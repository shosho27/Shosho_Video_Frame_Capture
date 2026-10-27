import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import QApplication
from video_downloader import DownloadDialog, download_command, validate_url
from yt_dlp import parse_options
from yt_dlp.extractor import gen_extractors


class DownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        from video_frame_capture import _find_ffmpeg
        self.dialog = DownloadDialog(ffmpeg=_find_ffmpeg())
        self.dialog.folder.setText(self.temp.name)
        self.dialog.url.setText('https://youtu.be/BaW_jenozKc')
        self.saved_settings = self.dialog.settings.value('download_folder')

    def tearDown(self):
        if self.dialog.is_busy():
            self.dialog.process.kill()
            self.dialog.process.waitForFinished(5000)
        if self.saved_settings is None:
            self.dialog.settings.remove('download_folder')
        else:
            self.dialog.settings.setValue('download_folder', self.saved_settings)
        self.dialog.close()
        self.temp.cleanup()

    def wait_for_finish(self, timeout=8):
        deadline = time.monotonic() + timeout
        while not self.dialog.download.isEnabled() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(self.dialog.download.isEnabled(), 'Download did not finish')

    def test_platform_links_match_real_extractors(self):
        examples = [
            ('https://youtu.be/BaW_jenozKc', 'youtube'),
            ('https://x.com/jack/status/20', 'twitter'),
            ('https://www.facebook.com/watch/?v=10153231379946729', 'facebook'),
            ('https://www.instagram.com/reel/Cx123456789/', 'instagram'),
            ('https://www.tiktok.com/@example/video/7000000000000000000', 'tiktok'),
            ('https://vm.tiktok.com/ZMexample/', 'vm.tiktok'),
            ('https://vt.tiktok.com/ZMexample/', 'vm.tiktok'),
            ('https://www.tiktok.com/t/ZMexample/', 'vm.tiktok'),
            ('https://vimeo.com/123456789', 'vimeo'),
            ('https://player.vimeo.com/video/123456789', 'vimeo'),
            ('https://www.reddit.com/r/videos/comments/abc123/test/', 'reddit'),
            ('https://www.twitch.tv/videos/123456789', 'twitch:vod'),
            ('https://clips.twitch.tv/ExampleClip', 'twitch:clips'),
            ('https://www.linkedin.com/posts/example_activity-123456789-abcd', 'linkedin'),
            ('https://www.dailymotion.com/video/x7tgad0', 'dailymotion'),
            ('https://dai.ly/x7tgad0', 'dailymotion'),
            ('https://www.loom.com/share/abcdef1234567890abcdef1234567890', 'loom'),
        ]
        extractors = gen_extractors()
        for url, name in examples:
            self.assertEqual(validate_url(url), url)
            self.assertTrue(any(ie.IE_NAME.lower() == name and ie.suitable(url)
                                for ie in extractors), url)

    def test_reject_invalid_urls(self):
        for url in ('file:///etc/passwd', 'https://youtube.com.evil.test/video',
                    '--exec rm', 'https://youtube.com@evil.test/video',
                    'https://tiktok.com.evil.test/video', ''):
            with self.assertRaises(ValueError):
                validate_url(url)

    def test_real_cli_options_and_no_implicit_cookies(self):
        args = download_command(self.dialog.url.text(), self.temp.name, 720,
                                ffmpeg=self.dialog.ffmpeg)
        options = parse_options(args[3:])
        self.assertIn('[height<=720]', options.ydl_opts['format'])
        self.assertNotIn('--cookies-from-browser', args)
        self.assertEqual(options.urls, [self.dialog.url.text()])
        signed_in = download_command(self.dialog.url.text(), self.temp.name,
                                     browser='firefox')
        self.assertEqual(signed_in[signed_in.index('--cookies-from-browser') + 1], 'firefox')

    def test_process_success_emits_only_existing_completed_file(self):
        path = Path(self.temp.name) / 'Clip ü [123].mp4'
        path.write_bytes(b'fixture')
        script = ('import time; '
                  'print("SHOSHO_PROGRESS:50.0%", flush=True); '
                  f'print({("SHOSHO_FILE:" + json.dumps(str(path)))!r}, flush=True)')
        ready = []
        self.dialog.video_ready.connect(ready.append)
        with patch('video_downloader.download_command', return_value=[sys.executable, '-c', script]):
            self.dialog.start_download()
        self.assertFalse(self.dialog.download.isEnabled())
        self.wait_for_finish()
        self.assertEqual(ready, [str(path)])
        self.assertEqual(self.dialog.progress.value(), 100)

    def test_error_does_not_emit_video(self):
        ready = []
        self.dialog.video_ready.connect(ready.append)
        command = [sys.executable, '-c', 'import sys; print("ERROR: Login required"); sys.exit(1)']
        with patch('video_downloader.download_command', return_value=command):
            self.dialog.start_download()
        self.wait_for_finish()
        self.assertEqual(ready, [])
        self.assertIn('failed', self.dialog.status.text())
        self.assertIn('Login required', self.dialog.log.toPlainText())

    def test_cancel_and_close_protection(self):
        command = [sys.executable, '-c', 'import time; time.sleep(30)']
        with patch('video_downloader.download_command', return_value=command):
            self.dialog.start_download()
        self.assertTrue(self.dialog.process.waitForStarted(3000))
        self.dialog.show()
        self.dialog.reject()
        self.assertTrue(self.dialog.isVisible())
        self.dialog.cancel_download()
        self.wait_for_finish()
        self.assertIn('Cancelled', self.dialog.status.text())
        self.assertEqual(self.dialog.process.state(), QProcess.NotRunning)

    def test_real_download_loads_into_frame_capture(self):
        import cv2
        import numpy as np
        from video_frame_capture import VideoFrameCapture

        source = Path(self.temp.name) / 'source'
        source.mkdir()
        clip = source / 'test.mp4'
        writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
        self.assertTrue(writer.isOpened())
        for _ in range(10):
            writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
        writer.release()
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(
            SimpleHTTPRequestHandler, directory=str(source)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        window = VideoFrameCapture()
        self.dialog.video_ready.connect(window._load_download)
        command = download_command(self.dialog.url.text(), self.temp.name)
        command[-1] = f'http://127.0.0.1:{server.server_port}/test.mp4'
        try:
            with patch('video_downloader.download_command', return_value=command):
                self.dialog.start_download()
            self.wait_for_finish(timeout=15)
            self.assertEqual(self.dialog.progress.value(), 100, self.dialog.log.toPlainText())
            self.assertTrue(window.current_cap.isOpened())
            self.assertEqual(window.total_frames, 10)
            self.assertEqual(window.file_list.currentRow(), 0)
            self.assertEqual(Path(window.current_video_path).parent, Path(self.temp.name))
        finally:
            window.close()
            server.shutdown()
            server.server_close()

    def _make_mkv(self, codec='libx264'):
        ffmpeg = self.dialog.ffmpeg
        if not ffmpeg:
            self.skipTest('FFmpeg is required for conversion tests')
        self.dialog.ffmpeg = ffmpeg
        path = Path(self.temp.name) / 'Clip ü [123].mkv'
        subprocess.run([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
            '-i', 'color=c=blue:s=64x64:r=10:d=1', '-f', 'lavfi',
            '-i', 'sine=frequency=440:duration=1', '-c:v', codec, '-c:a', 'pcm_s16le',
            '-shortest', str(path),
        ], check=True, capture_output=True)
        return path

    def _report_download(self, path):
        script = f'print({("SHOSHO_FILE:" + json.dumps(str(path)))!r}, flush=True)'
        with patch('video_downloader.download_command', return_value=[sys.executable, '-c', script]):
            self.dialog.start_download()

    def test_mkv_converts_deletes_original_and_preserves_existing_mp4(self):
        import cv2
        for codec in ('libx264', 'huffyuv'):
            with self.subTest(codec=codec):
                path = self._make_mkv(codec)
                existing = path.with_suffix('.mp4')
                existing.write_bytes(b'do not replace')
                ready = []
                self.dialog.video_ready.connect(ready.append)
                self._report_download(path)
                self.wait_for_finish(timeout=15)
                self.assertEqual(len(ready), 1, self.dialog.log.toPlainText())
                converted = Path(ready[0])
                self.assertEqual(converted.suffix, '.mp4')
                self.assertNotEqual(converted, existing)
                self.assertEqual(existing.read_bytes(), b'do not replace')
                self.assertFalse(path.exists(), 'MKV original should be deleted')
                cap = cv2.VideoCapture(str(converted))
                self.assertTrue(cap.read()[0])
                cap.release()
                probe = shutil.which('ffprobe')
                if probe:
                    metadata = json.loads(subprocess.check_output([
                        probe, '-v', 'error', '-show_streams', '-of', 'json', str(converted)]))
                    streams = metadata['streams']
                    self.assertTrue(any(s['codec_type'] == 'audio' for s in streams))
                    self.assertEqual(next(s['codec_name'] for s in streams
                                          if s['codec_type'] == 'video'), 'h264')
                if codec == 'huffyuv':
                    self.assertEqual(self.dialog._phase, 'encode')
                self.assertEqual(list(Path(self.temp.name).glob('.shosho-converting-*')), [])
                self.dialog.video_ready.disconnect(ready.append)

    def test_mp3_mode_command_and_h264_preference(self):
        args = download_command(self.dialog.url.text(), self.temp.name, mp3_bitrate=256)
        options = parse_options(args[3:])
        self.assertTrue(options.ydl_opts['postprocessors'])
        self.assertIn('256', json.dumps(options.ydl_opts['postprocessors']))
        video = parse_options(download_command(self.dialog.url.text(), self.temp.name)[3:])
        self.assertIn('h264', json.dumps(video.ydl_opts['format_sort']))

    def test_vp9_mp4_is_reencoded_in_place_for_adobe(self):
        import cv2
        ffmpeg = self.dialog.ffmpeg
        path = Path(self.temp.name) / 'vp9 clip.mp4'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', 'color=c=red:s=64x64:r=10:d=1', '-c:v', 'libvpx-vp9', str(path)],
                       check=True, capture_output=True)
        from video_downloader import probe_codecs
        self.assertEqual(probe_codecs(ffmpeg, str(path))[0], 'vp9')
        ready = []
        self.dialog.video_ready.connect(ready.append)
        self._report_download(path)
        self.wait_for_finish(timeout=20)
        self.assertEqual(ready, [str(path)], self.dialog.log.toPlainText())
        self.assertEqual(probe_codecs(ffmpeg, str(path))[:2], ('h264', 'yuv420p'))

    def test_video_plus_mp3_creates_both_with_bitrate(self):
        path = self._make_mkv()
        self.dialog.mode.setCurrentIndex(2)
        self.dialog.bitrate.setCurrentIndex(3)
        ready = []
        self.dialog.video_ready.connect(ready.append)
        self._report_download(path)
        self.wait_for_finish(timeout=20)
        mp3 = path.with_suffix('.mp3')
        self.assertTrue(mp3.exists(), self.dialog.log.toPlainText())
        self.assertTrue(path.with_suffix('.mp4').exists())
        self.assertFalse(path.exists())
        out = subprocess.run([self.dialog.ffmpeg, '-i', str(mp3)], capture_output=True, text=True).stderr
        self.assertIn('320 kb/s', out)

    def test_bundled_ffmpeg_works_without_system_installation(self):
        import imageio_ffmpeg
        from video_frame_capture import _find_ffmpeg
        with patch.dict(os.environ, {'PATH': '', 'IMAGEIO_FFMPEG_EXE': ''}):
            ffmpeg = _find_ffmpeg()
            self.assertTrue(Path(ffmpeg).is_relative_to(Path(imageio_ffmpeg.__file__).parent))
            version = subprocess.check_output([ffmpeg, '-version'], text=True)
            self.assertIn('ffmpeg version', version)

    def test_frozen_download_uses_bundled_worker_and_runtime(self):
        runtime = Path(self.temp.name) / 'bin/deno'
        runtime.parent.mkdir()
        runtime.write_bytes(b'fixture')
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, '_MEIPASS', self.temp.name, create=True), \
                patch('video_downloader.shutil.which', return_value=None):
            command = download_command(self.dialog.url.text(), self.temp.name)
        self.assertEqual(command[:2], [sys.executable, '--shosho-download'])
        self.assertEqual(command[command.index('--js-runtimes') + 1], f'deno:{runtime}')

    def test_conversion_failure_keeps_and_loads_mkv(self):
        path = Path(self.temp.name) / 'broken.mkv'
        path.write_bytes(b'invalid video fixture')
        ready = []
        self.dialog.video_ready.connect(ready.append)
        self._report_download(path)
        self.wait_for_finish()
        self.assertEqual(ready, [str(path)])
        self.assertEqual(path.read_bytes(), b'invalid video fixture')
        self.assertIn('conversions failed', self.dialog.status.text())
        self.assertEqual(list(Path(self.temp.name).glob('.shosho-converting-*')), [])

    def test_cancel_conversion_keeps_original_and_removes_temporary_file(self):
        path = Path(self.temp.name) / 'original.mkv'
        path.write_bytes(b'original')
        sleeper = Path(self.temp.name) / 'slow-converter'
        sleeper.write_text(f'#!{sys.executable}\nimport sys, time\nif "-c:v" in sys.argv: time.sleep(30)\nsys.exit(1)\n')
        sleeper.chmod(0o755)
        self.dialog.ffmpeg = str(sleeper)
        ready = []
        self.dialog.video_ready.connect(ready.append)
        self._report_download(path)
        deadline = time.monotonic() + 5
        while self.dialog._phase == 'download' and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIn(self.dialog._phase, ('remux', 'encode'))
        self.assertTrue(self.dialog.process.waitForStarted(3000))
        self.dialog.cancel_download()
        self.wait_for_finish()
        self.assertEqual(ready, [])
        self.assertTrue(path.exists())
        self.assertEqual(list(Path(self.temp.name).glob('.shosho-converting-*')), [])


if __name__ == '__main__':
    unittest.main()
