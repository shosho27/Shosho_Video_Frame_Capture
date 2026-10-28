# Building and Distributing Video Frame Capture

Build on an Apple Silicon Mac. There are two builds.

### Day-to-day app

This is the app Shane launches. It does not bundle Deno. YouTube uses the Deno
or Node already installed on the Mac. X and Facebook downloads and the contact
sheet were confirmed with this build on October 15, 2026.

```bash
cd /Users/sashornock/Documents/Scripts/ScriptProduction/Shosho_Video_Frame_Capture
.venv/bin/pyinstaller "Video Frame Capture.spec" --noconfirm
codesign --deep --force --sign - "dist/Video Frame Capture.app"
codesign --verify --deep --strict "dist/Video Frame Capture.app"
ditto "dist/Video Frame Capture.app" "$HOME/Apps/Video Frame Capture.app"
```

Quit the running app before launching the new one. The Desktop alias points at
`dist/`. `~/Apps` is a copy.

### Full bundle

`build_app.py` includes Python, PyQt5, OpenCV, yt-dlp, its JavaScript solver,
Deno, and FFmpeg. That app does not need Homebrew. It has not been rebuilt for
the October 15 contact-sheet and download fixes. Use it when the app has to run
on a Mac that does not already have Deno and FFmpeg.

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
