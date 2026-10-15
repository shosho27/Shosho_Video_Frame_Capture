# Building Shosho Video Frame Capture

Build on an Apple Silicon Mac:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller
# Deno is required on the build machine; it is included in the finished app.
brew install deno
.venv/bin/python build_app.py
```

The output is `dist/Shosho Video Frame Capture.app`. Double-click to launch.
The build includes Python, PyQt5, OpenCV, yt-dlp, its JavaScript solver, Deno,
and FFmpeg from the imageio-ffmpeg wheel. Users do not need Python, Homebrew,
or a separate FFmpeg installation. FFmpeg is shared by scene detection,
audio/video merging, and MKV-to-MP4 conversion.

The downloader runs as a subprocess of the bundled app. Deno and its dynamic
library dependencies are collected into the app by PyInstaller. The build script
ad-hoc signs the app and verifies its signature. This is a local build, not an
Apple-notarized distribution; downloads of shared builds may require macOS
approval. Public distribution can use a Developer ID signature and notarization.

Do not commit `dist/`, `build/`, or generated `.spec` files. Keep the build script
and requirements in version control. Update dependencies and rebuild when a
video site changes. Third-party component information is included in
`THIRD_PARTY_NOTICES.md` inside the bundle.
