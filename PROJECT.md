# Project — Shosho Video Frame Capture

---

## High priority

### Use current frame position for batch
**Status:** Done
Button pushes current scrubber timecode into batch spin box.

### Play / pause button
**Status:** Done
QTimer at native FPS, Space bar toggles, stops at last frame.

### Output format options
**Status:** Done
PNG / JPEG (quality slider) / TIFF selector. Chooseable output folder button.

### Interval capture
**Status:** Done
"Every N seconds" spinner (0.1–3600 s, default 3 s). Two buttons: This Video
(sweeps the currently loaded video) and All Videos (batch across the whole folder).
Output named `{stem}_{t:.1f}s.{ext}`. Progress bar sized by fast metadata probe.

### Scene capture
**Status:** Done
FFmpeg `select='gt(scene,threshold)'` scene detection ported from Vanilla Splice.
Sensitivity slider (0.10–0.90, default 0.40 — lower = more cuts detected).
Two buttons: This Video and All Videos. Captures midpoint frame of each scene,
named `{stem}_scene{N:03d}_{start:.2f}s.{ext}`. Warns if FFmpeg not in PATH.

---

## Medium priority

### Keyboard shortcuts
**Status:** Done
- Space — play / pause
- Left / Right — previous / next frame
- Shift+Left / Shift+Right — ±1 second
- Cmd+S — capture current frame

### Video info display
**Status:** Done
Info bar shows codec resolution, FPS, frame count, duration for loaded video.
File list shows resolution, duration, FPS per file.

### Sync batch timecode from scrubber
**Status:** Done (← Use Current Frame button)

---

## Low priority

### Drag and drop
**Status:** Done
Accepts drop of video folder or individual video file onto window.

### Multiple timecode batch
**Status:** Done
Timecode list — add/remove timecodes, batch runs for all of them.

### Dark theme consistency
**Status:** Done
Full STYLESHEET applied via app.setStyleSheet() — covers all widget types.

### Capture confirmation feedback
**Status:** Done
Preview border flashes green for 500ms after each capture.

---

## Open / future

### Background threading
Interval and scene batch runs block the main thread (same as existing batch).
Moving to QThread would prevent UI sluggishness on large folders.

### Face-filter for scene capture
Vanilla Splice already has MediaPipe face detection at scene midpoints.
Could add a "Faces Only" toggle to skip scenes with no detected face.

### Scene preview list
Show detected scenes as thumbnails in the left panel before capturing,
so the user can deselect individual scenes.
