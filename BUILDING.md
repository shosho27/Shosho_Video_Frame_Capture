# Building and Distributing Video Frame Capture

## Build the .app

```bash
pip install pyinstaller
cd /path/to/Shosho_Video_Frame_Capture

pyinstaller "Video Frame Capture.spec" --noconfirm
# Output: dist/Video Frame Capture.app
```

---

## Sign the app

### Ad-hoc signing (free, no Apple Developer account)

```bash
codesign --deep --force --sign - "dist/Video Frame Capture.app"
```

Recipients just double-click the app — it automatically removes the quarantine flag on first run, so no manual `xattr` step is needed.

### Full signing + notarization (Apple Developer Program — $99/year)

Enroll at **developer.apple.com/programs/enroll**

```bash
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

---

## Distribution notes

- Current build is **arm64 (Apple Silicon) only**
- The `dist/` folder is excluded from git — do not commit built apps
- Always re-sign after every rebuild
