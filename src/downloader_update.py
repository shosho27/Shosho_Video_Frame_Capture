"""Install verified official downloader releases outside the signed app bundle."""

import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

import certifi
from PyQt5.QtCore import QThread, pyqtSignal


RELEASE_API = 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest'
RELEASE_ROOT = 'https://github.com/yt-dlp/yt-dlp/releases/download/'
ASSET = 'yt-dlp_macos'


def installed_downloader():
    return Path.home() / 'Library/Application Support/Shosho/VideoFrameCapture/downloader' / ASSET


def updated_downloader():
    path = installed_downloader()
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def _fetch(url, destination=None, progress=lambda message: None):
    request = Request(url, headers={'User-Agent': 'Shosho-Video-Frame-Capture',
                                    'Accept': 'application/vnd.github+json' if url == RELEASE_API else '*/*'})
    digest = hashlib.sha256()
    deadline = time.monotonic() + 120
    data = bytearray()
    count = 0
    with urlopen(request, timeout=20, context=ssl.create_default_context(cafile=certifi.where())) as response:
        total = int(response.headers.get('Content-Length') or 0)
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('The update took too long. Please try again.')
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > (150 * 1024 * 1024 if destination else 5 * 1024 * 1024):
                raise ValueError('Update exceeded the expected size.')
            digest.update(chunk)
            if destination:
                destination.write(chunk)
                if total:
                    progress(f'Downloading update… {min(100, count * 100 // total)}%')
            else:
                data.extend(chunk)
    return digest.hexdigest() if destination else bytes(data)


def install_update(target=None, progress=lambda message: None):
    if sys.platform != 'darwin':
        raise ValueError('This updater supports the macOS app.')
    progress('Checking for downloader updates…')
    release = json.loads(_fetch(RELEASE_API))
    version = release.get('tag_name', '')
    if not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}(?:\.[\w.-]+)?', version):
        raise ValueError('The release did not contain a valid version.')
    root = RELEASE_ROOT + version + '/'
    checksums = _fetch(root + 'SHA2-256SUMS').decode('utf-8')
    expected = None
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == ASSET:
            expected = parts[0].lower()
    if not expected or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('The release is missing its verification checksum.')
    target = Path(target) if target is not None else installed_downloader()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.update-', dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            actual = _fetch(root + ASSET, destination=handle, progress=progress)
        if actual != expected:
            raise ValueError('Update verification failed. The current downloader was kept.')
        temporary.chmod(0o755)
        progress('Verifying downloader…')
        env = os.environ.copy()
        # A separate PyInstaller app must start with its own runtime environment.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        result = subprocess.run([str(temporary), '--version'], capture_output=True,
                                text=True, timeout=30, check=True, env=env)
        if result.stdout.strip() != version:
            raise ValueError('The updated downloader failed its version check.')
        os.replace(temporary, target)
        return version
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class DownloaderUpdate(QThread):
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.version = None
        self.error = None

    def run(self):
        try:
            self.version = install_update(progress=self.status.emit)
        except Exception as error:
            self.error = str(error)
