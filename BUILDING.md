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
Deno, and FFmpeg. That app does not need Homebrew. Use it when the app has to
run on a Mac that does not already have Deno and FFmpeg. It is also the build
Shosho Hub uses (see below). On November 7, 2026 it was rebuilt from the
November 5 code, installed to `/Applications` and launched on the home Mac.

Use Python 3.14 for the `.venv` if Homebrew's default is 3.15: some of the
dependencies (PyAV, onnxruntime) had no 3.15 wheels then.

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

Before building, `build_app.py` writes `src/_build_stamp.py` (git-ignored) with
the build date and commit, so the window title reads `built 2026-11-07 07:37`.
A packaged app without it says "packaged build, unstamped".

### Through Shosho Hub

On a Mac where the project is a `~/Github` clone, Shosho Hub's Install/Update
button pulls the current branch, creates `.venv` the first time (Python 3.14
when present), installs `requirements.txt` and PyInstaller, runs
`build_app.py` and installs `dist/Video Frame Capture.app` into
`/Applications`. If the app is open, the Hub quits it and reopens the new copy.
The first install takes a few minutes; later updates about one.

`Video Frame Capture.spec` is git-ignored and machine-specific (it names a
Homebrew Deno version path), so the Hub does not use it.

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
