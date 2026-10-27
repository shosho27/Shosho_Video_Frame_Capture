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
    whisper_dir = ROOT / 'assets/whisper-tiny'
    if not (whisper_dir / 'model.bin').is_file():
        from faster_whisper.utils import download_model
        download_model('tiny', output_dir=str(whisper_dir))
    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--windowed', '--onedir', '--name', 'Video Frame Capture',
        '--osx-bundle-identifier', 'com.shosho.video-frame-capture',
        '--icon', str(ROOT / 'assets/Frame_Capture.icns'),
        '--collect-all', 'imageio_ffmpeg', '--collect-all', 'yt_dlp',
        '--collect-all', 'yt_dlp_ejs', '--collect-all', 'curl_cffi',
        '--collect-all', 'faster_whisper', '--collect-all', 'ctranslate2',
        '--collect-all', 'tokenizers', '--collect-all', 'onnxruntime',
        '--copy-metadata', 'imageio-ffmpeg',
        '--add-binary', f'{os.path.realpath(deno)}:bin',
        '--add-data', f'{whisper_dir}:assets/whisper-tiny',
        '--add-data', f'{ROOT / "THIRD_PARTY_NOTICES.md"}:.',
        str(ROOT / 'src/app_main.py'),
    ]
    for folder in (ROOT / 'dist', ROOT / 'build'):
        for _ in range(3):
            if folder.exists():
                subprocess.run(['/bin/rm', '-rf', str(folder)], check=False)
    subprocess.run(command, cwd=ROOT, check=True)
    app = ROOT / 'dist/Video Frame Capture.app'
    plist = app / 'Contents/Info.plist'
    import plistlib
    with plist.open('rb') as fh:
        info = plistlib.load(fh)
    info['NSMicrophoneUsageDescription'] = (
        'Video Frame Capture uses your microphone to record voiceover narration onto video clips.'
    )
    with plist.open('wb') as fh:
        plistlib.dump(info, fh)
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print(f'Built and verified: {app}')


if __name__ == '__main__':
    main()
