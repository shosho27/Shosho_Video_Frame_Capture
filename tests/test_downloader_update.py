import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from downloader_update import ASSET, install_update
from video_downloader import DownloadDialog, download_command


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.target = Path(self.folder.name) / ASSET
        self.target.write_bytes(b'previous downloader')
        self.version = '2026.08.19'
        self.content = b'new downloader fixture'

    def fetch(self, url, destination=None, progress=None):
        if url.endswith('/latest'):
            return json.dumps({'tag_name': self.version}).encode()
        if url.endswith('SHA2-256SUMS'):
            return f'{hashlib.sha256(self.content).hexdigest()}  {ASSET}\n'.encode()
        destination.write(self.content)
        return hashlib.sha256(self.content).hexdigest()

    def test_verified_release_replaces_previous_download_engine(self):
        with patch('downloader_update._fetch', side_effect=self.fetch), patch(
                'downloader_update.subprocess.run') as run:
            run.return_value.stdout = self.version + '\n'
            self.assertEqual(install_update(self.target), self.version)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertTrue(self.target.stat().st_mode & 0o100)
        self.assertEqual(list(self.target.parent.glob('.update-*')), [])

    def test_checksum_failure_keeps_previous_engine(self):
        def corrupted(url, destination=None, progress=None):
            result = self.fetch(url, destination, progress)
            return '0' * 64 if destination else result
        with patch('downloader_update._fetch', side_effect=corrupted), patch(
                'downloader_update.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                install_update(self.target)
            run.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'previous downloader')
        self.assertEqual(list(self.target.parent.glob('.update-*')), [])

    def test_failed_version_check_keeps_previous_engine(self):
        with patch('downloader_update._fetch', side_effect=self.fetch), patch(
                'downloader_update.subprocess.run') as run:
            run.return_value.stdout = 'unexpected version'
            with self.assertRaisesRegex(ValueError, 'version check'):
                install_update(self.target)
        self.assertEqual(self.target.read_bytes(), b'previous downloader')

    def test_network_failure_keeps_previous_engine(self):
        with patch('downloader_update._fetch', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                install_update(self.target)
        self.assertEqual(self.target.read_bytes(), b'previous downloader')

    def test_updated_engine_used_without_changing_download_options(self):
        with patch('video_downloader.updated_downloader', return_value=str(self.target)):
            command = download_command('https://youtu.be/test', '/tmp/video', ffmpeg='/app/ffmpeg')
        self.assertEqual(command[0], str(self.target))
        self.assertIn('--ffmpeg-location', command)
        self.assertIn('--no-playlist', command)

    def test_builtin_engine_is_default(self):
        with patch('video_downloader.updated_downloader', return_value=None):
            command = download_command('https://youtu.be/test', '/tmp/video')
        self.assertEqual(command[:3], [sys.executable, '-m', 'yt_dlp'])

    def test_update_button_restores_controls_after_success_or_failure(self):
        for error in (None, OSError('offline')):
            with self.subTest(error=error):
                dialog = DownloadDialog()
                try:
                    with patch('downloader_update.install_update', return_value=self.version,
                               side_effect=error):
                        dialog.update_button.click()
                        self.assertFalse(dialog.download.isEnabled())
                        self.assertFalse(dialog.cancel.isEnabled())
                        deadline = time.monotonic() + 5
                        while not dialog.download.isEnabled() and time.monotonic() < deadline:
                            self.app.processEvents()
                            time.sleep(.01)
                        self.assertTrue(dialog.download.isEnabled())
                        self.assertTrue(dialog.update_button.isEnabled())
                        self.assertFalse(dialog.is_busy())
                        self.assertIn('failed' if error else self.version, dialog.status.text())
                finally:
                    if dialog.updater:
                        dialog.updater.wait(5000)
                    dialog.close()
