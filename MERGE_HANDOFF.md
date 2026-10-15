# Video Frame Capture: merge handoff

Saved October 14, 2026 (America/Chicago).

## Product name

The user confirmed the product should be called **Video Frame Capture**.
The current source and packaged app still contain the old **Shosho Video Frame
Capture** name. Apply the rename when combining versions; preserve access to
existing download settings and any installed downloader updates.

## Versions to compare

- `main`, `cbc7efa`: July 31 baseline used for this local checkout.
- `autosync/y4gjvvrph6`, `72b978c`: October 8 performance work, not merged
  into main. Changes sequential frame reads, QImage row strides, and related notes.
- `codex/video-frame-capture-downloads`: snapshot of the current local work.
  Adds YouTube, X, Facebook, and Instagram link downloads; progress and cancel;
  optional browser cookies; automatic MKV-to-MP4 remux with H.264/AAC fallback;
  original preservation; standalone macOS packaging with FFmpeg, yt-dlp, and Deno;
  plus the downloader-update implementation present in this shared checkout.
- The user reports another version pushed October 14. It has not been located.
  At the time of checking, this GitHub repository showed its last push October 9,
  and had no pull requests or releases. Do not assume the autosync branch is the
  version the user means. Locate the other repository, branch, chat, or checkout.

## Merge workflow

1. Fetch GitHub and locate the user's other version. Preserve it on its own branch
   before editing or overwriting anything. Check for any local changes made after
   this snapshot, including changes by another chat sharing this folder.
2. Combine the versions on a new integration branch. Review the autosync
   performance changes and retain the applicable improvements as well.
3. Rename the app and build output to Video Frame Capture, updating docs and
   launchers consistently. Retain existing user preferences when changing names.
4. Run `.venv/bin/python -m unittest discover -s tests -v`, then rebuild using
   `.venv/bin/python build_app.py` and test the actual packaged app.
5. Check downloads, downloader updates, MP4 conversion, frame preview, and capture.
   Verify bundled FFmpeg and the downloader work without Homebrew in PATH.
6. Review the combined changes and merge the integration branch into main.

## Build state

The standalone app is generated under `dist/` and is intentionally excluded from
Git. It can be rebuilt from `build_app.py` and `requirements.txt`; Python's
PyInstaller package and Deno are required on the build machine. The previously
built app predates the later downloader-update source changes in this checkout;
rebuild to include those changes. Third-party notices are in the repository.

No changes from this snapshot have been merged into main.
