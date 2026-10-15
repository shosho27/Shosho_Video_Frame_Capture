# Building and Distributing Video Frame Capture

Build on an Apple Silicon Mac. The finished app includes Python, PyQt5, OpenCV,
yt-dlp, its JavaScript solver, Deno, and FFmpeg. Users do not need Python,
Homebrew, or a separate FFmpeg installation.

```bash
cd /Users/sashornock/Documents/Scripts/ScriptProduction/Shosho_Video_Frame_Capture
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller
brew install deno
.venv/bin/python build_app.py
```

The output is `dist/Video Frame Capture.app`. FFmpeg is shared by scene
detection, audio/video merging, and MKV-to-MP4 conversion. The downloader runs
as a subprocess of the bundled app.

The build script ad-hoc signs the app and verifies its signature. This is a
local build, not an Apple-notarized distribution.

### Ad-hoc signing (free, no Apple Developer account)

`build_app.py` already does this. To sign again:

```bash
cd /Users/sashornock/Documents/Scripts/ScriptProduction/Shosho_Video_Frame_Capture
codesign --deep --force --sign - "dist/Video Frame Capture.app"
```

The app removes its quarantine flag on first run.

### Full signing + notarization (Apple Developer Program)

```bash
cd /Users/sashornock/Documents/Scripts/ScriptProduction/Shosho_Video_Frame_Capture
codesign --deep --force \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  --options runtime \
  "dist/Video Frame Capture.app"

xcrun notarytool submit "dist/Video Frame Capture.app" \
  --apple-id you@email.com \
  --team-id TEAMID \
  --password APP_SPECIFIC_PASSWORD \
  --wait

xcrun stapler staple "dist/Video Frame Capture.app"
```

## Distribution notes

- Current build is **arm64 (Apple Silicon) only**
- Do not commit `dist/`, `build/`, or generated `.spec` files
- Keep `build_app.py` and `requirements.txt` in version control
- Third-party component information is in `THIRD_PARTY_NOTICES.md`
