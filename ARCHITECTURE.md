# Architecture — Video Frame Capture

## Overview

Single-file PyQt5 application. OpenCV handles all video I/O and frame extraction.
Qt handles the GUI, preview rendering, and event loop.

## Video loading and display

`cv2.VideoCapture` opens the video file. Frame display uses OpenCV's
`cv2.CAP_PROP_POS_FRAMES` to seek to a specific frame number, reads it with
`cap.read()`, converts BGR→RGB with `cv2.cvtColor`, wraps in a `QImage`, and
scales to fit the preview label with `Qt.KeepAspectRatio`.

The preview re-renders on every slider move and on window resize.

## Frame extraction

**Single capture:** reads the current slider frame number directly from the
already-open `cv2.VideoCapture`, writes to the chosen format via `_save_frame`.

**Batch capture:** opens a fresh `cv2.VideoCapture` per video, seeks to
`target_sec * fps`, reads one frame, writes to output, releases. Runs
synchronously on the main thread with `QApplication.processEvents()` called
each iteration to keep the progress bar updating.

**Interval capture:** for each path in the list, steps through `t = 0.0` to
`duration` in `interval`-second increments, seeking to `round(t * fps)` each
step. Does a fast metadata-only probe pass first to size the progress bar.

**Scene capture:** calls module-level `_detect_scene_cuts(path, threshold)`
which shells out to FFmpeg's `select='gt(scene,N)'` filter and parses
`pts_time:` values from stderr. The resulting cut timestamps are merged with
`{0.0, duration}` to form scene boundaries; slivers under 100 ms are dropped.
For each scene the midpoint frame is captured. Requires FFmpeg in PATH;
shows a warning dialog if not found.

## Scene detection helpers (module-level)

`_find_ffmpeg()` — resolves ffmpeg from PATH then common Homebrew locations;
returns `None` if not found.

`_detect_scene_cuts(path, threshold)` — shells out to FFmpeg, parses stderr
for `pts_time:` tokens, returns sorted list of cut timestamps. Returns `None`
if ffmpeg is unavailable.

## Output naming

- Single:   `{stem}_frame{N}_{sec:.2f}s.{ext}`
- Batch:    `{stem}_{target_sec:.2f}s.{ext}`
- Interval: `{stem}_{t:.1f}s.{ext}`
- Scene:    `{stem}_scene{N:03d}_{start:.2f}s.{ext}`

Output format is PNG / JPEG / TIFF, chosen via the Format combo. JPEG exposes
a quality slider. Alpha-channel video is composited onto white for JPEG, preserved
for PNG and TIFF.

## Timecode sync (Use Current Frame)

`use_current_timecode()` converts `frame_slider.value() / self.fps` to seconds
and sets `batch_spin.setValue()`. No signal juggling needed — both widgets are
on the main thread.

## Output folder

Defaults to `captured_frames/` inside the selected video folder, created
automatically. User can override via the Output Folder button at any time.
Show in Finder reveals that folder with `/usr/bin/open -R`.

## Contact sheet

`ContactSheetDialog` is non-modal. Batch Frames seeks the batch timecode with
`contact_seek_frame`. A blank frame looks ahead 1, 2, and 3 seconds
(`choose_contact_time`) and the caption shows the time kept. Frame Folder lists
PNG, JPEG, and TIFF in that folder only.

`paint_contact_page` is shared by the preview and `write_contact_pdf`.
`contact_grid` picks the largest contained cell for the frame aspect. The
picture block is centered with `origin_x` and `origin_y`. A near-black border
is trimmed in `_content_source_rect`, then the picture is contain-fit in the
slot. `drawImage` embeds the source pixels. `CONTACT_SHEET_DPI` is 300. The
media box stays in points. The preview label background is `#1e1e2e`, so a
portrait page reads as a tall white sheet.

## Link downloads

`src/video_downloader.py` builds a yt-dlp command for YouTube, X, Facebook,
Instagram, TikTok, and LinkedIn posts. A country host such as `pt.linkedin.com`
is rewritten to `www.linkedin.com`, which is what the extractor matches. LinkedIn
MP4s omit height, so the format selector falls back to the best combined file.
`javascript_runtime()` checks a bundled `bin` directory,
then PATH, then `/opt/homebrew/bin` and `/usr/local/bin`, Deno before Node.
The command gets `--js-runtimes` when a runtime is found. MKV results convert
to MP4 and the original is kept. `src/downloader_update.py` can replace the
engine with a verified `yt-dlp_macos` binary under Application Support.
