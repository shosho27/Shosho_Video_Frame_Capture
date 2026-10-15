"""Build a macOS application including FFmpeg, yt-dlp, and Deno."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Build this macOS application on a Mac.')
    import imageio_ffmpeg
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    if not ffmpeg.is_relative_to(Path(imageio_ffmpeg.__file__).parent):
        raise SystemExit('Install the imageio-ffmpeg wheel; its bundled binary is required.')
    deno = shutil.which('deno')
    if not deno:
        raise SystemExit('Deno is needed on the build machine so it can be bundled.')
    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--windowed', '--onedir', '--name', 'Shosho Video Frame Capture',
        '--osx-bundle-identifier', 'com.shosho.videoframecapture',
        '--icon', str(ROOT / 'assets/Frame_Capture.icns'),
        '--collect-all', 'imageio_ffmpeg', '--collect-all', 'yt_dlp',
        '--collect-all', 'yt_dlp_ejs', '--copy-metadata', 'imageio-ffmpeg',
        '--add-binary', f'{os.path.realpath(deno)}:bin',
        '--add-data', f'{ROOT / "THIRD_PARTY_NOTICES.md"}:.',
        str(ROOT / 'src/app_main.py'),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    app = ROOT / 'dist/Shosho Video Frame Capture.app'
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print(f'Built and verified: {app}')


if __name__ == '__main__':
    main()
