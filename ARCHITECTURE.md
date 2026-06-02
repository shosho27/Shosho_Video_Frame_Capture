# Architecture — Shosho Video Frame Capture

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
already-open `cv2.VideoCapture`, writes PNG via `cv2.imwrite`.

**Batch capture:** opens a fresh `cv2.VideoCapture` per video, seeks to
`target_sec * fps`, reads one frame, writes PNG, releases. Runs synchronously
on the main thread with `QApplication.processEvents()` called each iteration
to keep the progress bar updating.

## Timecode sync (Use Current Frame)

`use_current_timecode()` converts `frame_slider.value() / self.fps` to seconds
and sets `batch_spin.setValue()`. No signal juggling needed — both widgets are
on the main thread.

## Output naming

- Single: `{video_stem}_frame{N}_{sec:.2f}s.png`
- Batch:  `{video_stem}_{target_sec:.2f}s.png`

All output goes to a `captured_frames/` subfolder inside the selected video folder,
created automatically on folder selection.
