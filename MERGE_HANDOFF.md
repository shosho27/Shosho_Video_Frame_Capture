# Video Frame Capture: merge handoff

## Combined on the work computer — October 15, 2026

The work-computer baseline is `work-computer-contact-sheet` (`ad8d04a`): the
speed-audit frame reads, the Video Frame Capture rename, Show in Finder, and
the contact sheet. The home download work is `origin/codex/video-frame-capture-downloads`
(`ff21f1c`). They are combined on `codex/video-frame-capture-combined`.

Kept from the work computer: single, batch, interval, and scene capture; the
contact sheet (batch timecodes or a folder of stills, page preview, one PDF at
the original picture size); Show in Finder for the output folder and the saved
PDF. Kept from home: YouTube, X, Facebook, Instagram, and TikTok downloads;
quality, destination, optional browser cookies, progress, cancel, MKV-to-MP4
with the original kept, and the downloader update. Settings stay
`QSettings('Shosho', 'VideoFrameCapture')` and
`~/Library/Application Support/Shosho/VideoFrameCapture/`. The internal worker
flag stays `--shosho-download`.

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v`
passed 28 tests on October 15. The packaged app still needs a rebuild with
`build_app.py` before the download button is in the app you launch.

Saved October 14, 2026 (America/Chicago). The sections below are the original
instructions from the home computer.

## Start here on the work computer — October 15, 2026

The user wants to combine the work created in this session with the earlier
version created on their **work computer**. That work-computer version has not
been inspected in this session, and its commit, branch, and location are unknown.
It may still be uncommitted or exist only on that computer. Do not replace that
checkout with this branch or assume an older GitHub branch contains that work.

This session's implementation is safely pushed to:

- Repository: https://github.com/shosho27/Shosho_Video_Frame_Capture
- Branch: `codex/video-frame-capture-downloads`
- Implementation snapshot: `ab5aebde6bda345e25711302fa2431d980d30073`
- Source verification: all **19 tests passed** on October 14.

First, find the repository on the work computer and inspect its current state:

```bash
git remote -v
git status --short
git branch -avv
git log -10 --oneline --decorate
git worktree list
```

Confirm the remote is the repository above. If it is a different repository,
compare its contents and history before choosing an integration strategy.
Read local README, project notes, and any AGENTS.md instructions. Locate the
newer work-computer implementation, including any rename to Video Frame Capture.

Before switching branches, pulling, merging, or rebuilding, preserve all relevant
work-computer changes on a backup branch. Inspect untracked files and include
source and required assets; exclude credentials, environments, downloads, and
generated builds. Do not use reset, clean, force-push, or overwrite the work.
Keep any existing working app available until the combined app is verified.

Then fetch the saved implementation and read the latest handoff directly:

```bash
git fetch origin
git show origin/codex/video-frame-capture-downloads:MERGE_HANDOFF.md
```

Create a new integration branch from the preserved **work-computer version**,
for example `codex/video-frame-capture-combined`, then merge the download branch:

```bash
git merge --no-commit --no-ff origin/codex/video-frame-capture-downloads
```

Resolve conflicts by combining the functionality, rather than accepting either
side wholesale. If the work-computer version has renamed or reorganized files,
port the downloader, conversion, updater, and packaging into that structure.
If histories are unrelated, compare and port the changes deliberately rather
than forcing an unrelated-history merge. Review the October 8 autosync changes
separately; do not assume they substitute for the user's work-computer version.

If the earlier work cannot be located, keep both backups and report exactly what
was found. Ask the user to identify the earlier chat or folder before claiming
the two versions have been merged.

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

## Acceptance checks for the combined version

- Preserve the work computer's features, layout, and fixes; first list what that
  version contains and compare it with this branch.
- Use **Video Frame Capture** in the window title, app bundle, build script,
  launchers, and docs. Keep compatibility with the existing `Shosho` QSettings
  namespace and `~/Library/Application Support/Shosho/VideoFrameCapture/`
  downloader location, or explicitly migrate them without deleting user data.
- Keep all four link sources: YouTube, X/Twitter, Facebook, and Instagram.
- Keep quality selection, destination selection, optional browser cookies,
  progress, cancellation, and automatic loading into frame capture.
- Keep original MKVs, protect existing MP4s, and try remux before H.264/AAC
  conversion. Check conversion failure and cancellation as well as success.
- Preserve the Update Downloader button, verified replacement, and fallback
  behavior. These source changes were added after the first packaged app build.
- Preserve the existing single, batch, interval, and scene capture modes.
- Install requirements into a local environment if needed, and install
  PyInstaller plus Deno on the build machine. The finished app must include its
  runtime, downloader, Deno, and FFmpeg so end users need no separate installation.
- Run the full current test suite; 19 passing tests is this snapshot's baseline,
  not a fixed test-count requirement. Add checks for actual merge conflicts or
  behavior changes. Launch the rebuilt app and check the download-to-capture flow.
- Review the final diff, commit the combined source, and push the integration
  branch. When merging into main is authorized, merge and push main, then verify
  GitHub's remote commit. Report the exact branch, commit, and packaged-app path.
- Update this handoff with the work-computer baseline, final merge commit,
  verification results, and any remaining work. Do not call the merge complete
  while either version's required features are still missing.

## Build state

The standalone app is generated under `dist/` and is intentionally excluded from
Git. It can be rebuilt from `build_app.py` and `requirements.txt`; Python's
PyInstaller package and Deno are required on the build machine. The previously
built app predates the later downloader-update source changes in this checkout;
rebuild to include those changes. Third-party notices are in the repository.

No changes from this snapshot have been merged into main.
