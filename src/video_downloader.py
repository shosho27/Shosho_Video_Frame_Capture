"""Non-blocking link downloads using an isolated yt-dlp process."""

import json
import os
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

from PyQt5.QtCore import QProcess, QProcessEnvironment, QSettings, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)
from downloader_update import DownloaderUpdate, updated_downloader


SUPPORTED_HOSTS = ('youtube.com', 'youtu.be', 'x.com', 'twitter.com',
                   'facebook.com', 'fb.watch', 'instagram.com', 'tiktok.com', 'tiktokv.com')
FILE_MARKER = 'SHOSHO_FILE:'


def validate_url(value):
    value = value.strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme not in ('http', 'https') or parsed.username or
            not any(host == domain or host.endswith('.' + domain)
                    for domain in SUPPORTED_HOSTS)):
        raise ValueError('Paste a YouTube, X, Facebook, Instagram, or TikTok video link.')
    return value


def download_command(url, folder, height=None, browser=None, ffmpeg=None):
    """Build argv directly; never send a pasted URL through a shell."""
    url = validate_url(url)
    updated = updated_downloader()
    if updated:
        command = [updated]
    elif getattr(sys, 'frozen', False):
        command = [sys.executable, '--shosho-download']
    else:
        command = [sys.executable, '-m', 'yt_dlp']
    limit = f'[height<={int(height)}]' if height else ''
    command += [
        '--ignore-config', '--no-playlist', '--no-simulate', '--newline',
        '--progress', '--no-colors', '--no-overwrites', '--socket-timeout', '20',
        '--retries', '3', '--windows-filenames',
        '-f', f'bv*{limit}+ba/b{limit}',
        '--merge-output-format', 'mp4/mkv',
        '-P', folder, '-o', '%(title).140B [%(id)s].%(ext)s',
        '--print', f'after_move:{FILE_MARKER}%(filepath)j',
        '--progress-template', 'download:SHOSHO_PROGRESS:%(progress._percent_str)s',
    ]
    if ffmpeg:
        command += ['--ffmpeg-location', ffmpeg]
    for runtime in ('deno', 'node'):
        bundled = os.path.join(getattr(sys, '_MEIPASS', ''), 'bin', runtime)
        path = bundled if os.path.isfile(bundled) else shutil.which(runtime)
        if path:
            command += ['--js-runtimes', f'{runtime}:{path}']
            break
    if browser:
        command += ['--cookies-from-browser', browser]
    return command + ['--', url]


class DownloadDialog(QDialog):
    video_ready = pyqtSignal(str)

    def __init__(self, parent=None, ffmpeg=None):
        super().__init__(parent)
        self.setWindowTitle('Download Video')
        self.resize(620, 500)
        self.ffmpeg = ffmpeg
        self.settings = QSettings('Shosho', 'VideoFrameCapture')
        self.process = None
        self.updater = None
        self.cancelled = False
        self._buffer = b''
        self._files = []
        self._phase = 'download'
        self._conversion_temp = None
        self._conversion_queue = []
        self._conversion_failures = 0
        layout = QVBoxLayout(self)
        heading = QLabel('Download a video, then capture its frames')
        heading.setStyleSheet('font-size: 16px; font-weight: bold;')
        layout.addWidget(heading)
        layout.addWidget(QLabel('YouTube · X / Twitter · Facebook · Instagram · TikTok'))
        link_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText('Paste a video or Reel link…')
        link_row.addWidget(self.url)
        self.paste = QPushButton('Paste')
        self.paste.clicked.connect(lambda: self.url.setText(QApplication.clipboard().text().strip()))
        link_row.addWidget(self.paste)
        layout.addLayout(link_row)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(self.settings.value(
            'download_folder', os.path.expanduser('~/Movies/Shosho Downloads')))
        folder_row.addWidget(self.folder)
        self.browse = QPushButton('Save to…')
        self.browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.browse)
        layout.addLayout(folder_row)
        options = QHBoxLayout()
        options.addWidget(QLabel('Quality:'))
        self.quality = QComboBox()
        for label, height in [('Best available', None), ('Up to 1080p', 1080),
                              ('Up to 720p', 720)]:
            self.quality.addItem(label, height)
        options.addWidget(self.quality)
        options.addWidget(QLabel('Use sign-in from:'))
        self.browser = QComboBox()
        for label, browser in [('None (public video)', None), ('Chrome', 'chrome'),
                               ('Safari', 'safari'), ('Firefox', 'firefox'),
                               ('Edge', 'edge'), ('Brave', 'brave')]:
            self.browser.addItem(label, browser)
        options.addWidget(self.browser)
        layout.addLayout(options)
        note = QLabel('For sign-in, choose the browser where you can watch the video. '
                      'This reads that browser’s cookies when you click Download. '
                      'Some videos may still be unavailable.')
        note.setWordWrap(True)
        layout.addWidget(note)
        conversion_note = QLabel('MKV downloads are automatically converted to MP4. Originals are kept.')
        conversion_note.setWordWrap(True)
        layout.addWidget(conversion_note)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel('Ready')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setPlaceholderText('Download details appear here')
        layout.addWidget(self.log)
        update_row = QHBoxLayout()
        self.update_button = QPushButton('Update Downloader')
        self.update_button.setToolTip('Install the latest official yt-dlp compatibility fixes')
        self.update_button.clicked.connect(self.update_downloader)
        update_row.addWidget(self.update_button)
        update_row.addStretch()
        layout.addLayout(update_row)
        buttons = QHBoxLayout()
        self.reveal = QPushButton('Open Download Folder')
        self.reveal.clicked.connect(self.open_folder)
        buttons.addWidget(self.reveal)
        buttons.addStretch()
        self.cancel = QPushButton('Cancel Download')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_download)
        buttons.addWidget(self.cancel)
        self.download = QPushButton('Download')
        self.download.clicked.connect(self.start_download)
        buttons.addWidget(self.download)
        layout.addLayout(buttons)

    def is_busy(self):
        return ((self.updater is not None and self.updater.isRunning()) or
                (self.process is not None and self.process.state() != QProcess.NotRunning))

    def update_downloader(self):
        if self.is_busy():
            return
        if self.updater is not None:
            self.updater.deleteLater()
        self._set_busy(True)
        self.cancel.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText('Checking for downloader updates…')
        self.updater = DownloaderUpdate(self)
        self.updater.status.connect(self.status.setText)
        self.updater.finished.connect(self._update_finished)
        self.updater.start()

    def _update_finished(self):
        self._set_busy(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0 if self.updater.error else 100)
        if self.updater.error:
            self.status.setText('Update failed. Your current downloader is unchanged. See details below.')
            self.log.appendPlainText(self.updater.error)
        else:
            self.status.setText(f'Downloader {self.updater.version} installed. Ready for your next download.')

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Save downloaded videos', self.folder.text())
        if folder:
            self.folder.setText(folder)

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(os.path.expanduser(self.folder.text()))))

    def _set_busy(self, busy):
        for widget in (self.url, self.paste, self.folder, self.browse,
                       self.quality, self.browser, self.download, self.update_button):
            widget.setEnabled(not busy)
        self.cancel.setEnabled(busy)

    def start_download(self):
        if self.is_busy():
            return
        try:
            if not self.ffmpeg:
                raise ValueError('FFmpeg is required for downloads. Install it, then reopen this window.')
            if not self.folder.text().strip():
                raise ValueError('Choose a download folder.')
            folder = os.path.abspath(os.path.expanduser(self.folder.text().strip()))
            command = download_command(self.url.text(), folder, self.quality.currentData(),
                                       self.browser.currentData(), self.ffmpeg)
            os.makedirs(folder, exist_ok=True)
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.settings.setValue('download_folder', folder)
        self.folder.setText(folder)
        self.cancelled = False
        self._files = []
        self._phase = 'download'
        self._conversion_failures = 0
        self._buffer = b''
        self.log.clear()
        self.progress.setRange(0, 0)
        self.status.setText('Connecting…')
        self._set_busy(True)
        self._start_process(command)

    def _start_process(self, command):
        self._buffer = b''
        if self.process is not None:
            self.process.deleteLater()
        self.process = QProcess(self)
        if command[0] != sys.executable:
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert('PYINSTALLER_RESET_ENVIRONMENT', '1')
            self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.start(command[0], command[1:])

    def _read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self._buffer:
            line, self._buffer = self._buffer.split(b'\n', 1)
            self._handle_line(line.decode('utf-8', errors='replace').strip())

    def _handle_line(self, line):
        if self._phase != 'download':
            if line:
                self.log.appendPlainText(line)
            return
        if line.startswith(FILE_MARKER):
            try:
                path = json.loads(line[len(FILE_MARKER):])
                if isinstance(path, str) and os.path.isfile(path):
                    self._files.append(path)
            except (ValueError, TypeError):
                pass
        elif line.startswith('SHOSHO_PROGRESS:'):
            match = re.search(r'(\d+(?:\.\d+)?)%', line)
            if match:
                self.progress.setRange(0, 100)
                self.progress.setValue(min(100, int(float(match.group(1)))))
                self.status.setText(f'Downloading… {match.group(1)}%')
        elif line:
            self.log.appendPlainText(line)
            if line.startswith(('[Merger]', '[VideoRemuxer]', '[Fixup')):
                self.progress.setRange(0, 0)
                self.status.setText('Finishing video…')

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            if self._phase != 'download':
                self.log.appendPlainText('Could not start converter: ' + self.process.errorString())
                self._conversion_failed()
                return
            self._set_busy(False)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status.setText('Could not start downloader: ' + self.process.errorString())

    def _finished(self, code, exit_status):
        self._read_output()
        if self._buffer:
            self._handle_line(self._buffer.decode('utf-8', errors='replace').strip())
            self._buffer = b''
        if self.cancelled:
            self._cleanup_conversion()
            self._set_busy(False)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status.setText('Cancelled. Downloaded originals and partial downloads are kept.')
        elif self._phase != 'download':
            if (code == 0 and exit_status == QProcess.NormalExit and
                    self._conversion_temp and os.path.isfile(self._conversion_temp) and
                    os.path.getsize(self._conversion_temp) > 0):
                self._publish_conversion()
            elif self._phase == 'remux':
                self._phase = 'encode'
                self.status.setText('Converting MKV to MP4… This may take a few minutes.')
                self._start_process(self._conversion_command(copy=False))
            else:
                self._conversion_failed()
        elif code == 0 and exit_status == QProcess.NormalExit and self._files:
            self._conversion_queue = [i for i, path in enumerate(self._files)
                                      if os.path.splitext(path)[1].lower() == '.mkv']
            self._convert_next()
        else:
            self._set_busy(False)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status.setText('Download failed. See details below. If sign-in is required, '
                                'choose your browser and retry. Site changes may require a downloader update.')

    def _convert_next(self):
        if not self._conversion_queue:
            self._set_busy(False)
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            message = f'Downloaded {len(self._files)} video(s). Loaded into frame capture.'
            if self._conversion_failures:
                message += ' Some MP4 conversions failed; original MKVs were kept. See details below.'
            self.status.setText(message)
            self.video_ready.emit(self._files[0])
            return
        self._conversion_index = self._conversion_queue.pop(0)
        self._conversion_source = self._files[self._conversion_index]
        try:
            fd, self._conversion_temp = tempfile.mkstemp(
                prefix='.shosho-converting-', suffix='.mp4',
                dir=os.path.dirname(self._conversion_source))
            os.close(fd)
        except OSError as error:
            self.log.appendPlainText(str(error))
            self._conversion_failed()
            return
        self._phase = 'remux'
        self.progress.setRange(0, 0)
        self.status.setText('Converting MKV to MP4…')
        self._start_process(self._conversion_command(copy=True))

    def _conversion_command(self, copy):
        command = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                   '-i', self._conversion_source, '-map', '0:v:0', '-map', '0:a:0?']
        if copy:
            command += ['-c', 'copy']
        else:
            command += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                        '-pix_fmt', 'yuv420p', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
                        '-c:a', 'aac', '-b:a', '192k']
        return command + ['-movflags', '+faststart', self._conversion_temp]

    def _publish_conversion(self):
        stem = os.path.splitext(self._conversion_source)[0]
        counter = 0
        try:
            # Publish without ever replacing an existing MP4, even if another
            # app creates one while FFmpeg is running.
            while True:
                suffix = '' if counter == 0 else f' (converted {counter})'
                target = stem + suffix + '.mp4'
                try:
                    os.link(self._conversion_temp, target)
                    break
                except FileExistsError:
                    counter += 1
            self._files[self._conversion_index] = target
            self.log.appendPlainText('MP4 ready: ' + target)
            self._cleanup_conversion()
            self._convert_next()
        except OSError as error:
            self.log.appendPlainText(str(error))
            self._conversion_failed()

    def _cleanup_conversion(self):
        if self._conversion_temp:
            try:
                os.remove(self._conversion_temp)
            except OSError:
                pass
            self._conversion_temp = None

    def _conversion_failed(self):
        self.log.appendPlainText('MP4 conversion failed; kept original: ' + self._conversion_source)
        self._conversion_failures += 1
        self._cleanup_conversion()
        self._convert_next()

    def cancel_download(self):
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.cancelled = True
            self.cancel.setEnabled(False)
            self.status.setText('Cancelling…')
            process = self.process
            process.terminate()
            timer = QTimer(process)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: process.kill()
                                  if process.state() != QProcess.NotRunning else None)
            timer.start(3000)

    def reject(self):
        if self.is_busy():
            self.status.setText('Wait for the update or cancel the download before closing this window.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_busy():
            self.status.setText('Wait for the update or cancel the download before closing this window.')
            event.ignore()
        else:
            event.accept()
