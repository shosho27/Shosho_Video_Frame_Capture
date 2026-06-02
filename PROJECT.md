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
