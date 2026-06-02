# Building and Distributing Shosho Video Frame Capture

## Build the .app

```bash
pip install pyinstaller
cd /path/to/Shosho_Video_Frame_Capture

pyinstaller "Shosho Video Frame Capture.spec" --noconfirm
# Output: dist/Shosho Video Frame Capture.app
```

---

## Sign the app

### Ad-hoc signing (free, no Apple Developer account)

```bash
codesign --deep --force --sign - "dist/Shosho Video Frame Capture.app"
```

Recipients need to strip quarantine once after copying:
```bash
xattr -cr "/path/to/Shosho Video Frame Capture.app"
```

### Full signing + notarization (Apple Developer Program — $99/year)

Enroll at **developer.apple.com/programs/enroll**

```bash
codesign --deep --force \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  --options runtime \
  "dist/Shosho Video Frame Capture.app"

xcrun notarytool submit "dist/Shosho Video Frame Capture.app" \
  --apple-id you@email.com \
  --team-id TEAMID \
  --password APP_SPECIFIC_PASSWORD \
  --wait

xcrun stapler staple "dist/Shosho Video Frame Capture.app"
```

---

## Distribution notes

- Current build is **arm64 (Apple Silicon) only**
- The `dist/` folder is excluded from git — do not commit built apps
- Always re-sign after every rebuild
