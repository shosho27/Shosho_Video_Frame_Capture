# Project — Shosho Video Frame Capture

Planned improvements organized by priority.

---

## High priority

### Use current frame position for batch
**Status:** To do  
When you find a good frame in Single mode, clicking a "Use This Timecode" button
should push the current frame's timecode directly into the batch spin box.
Biggest friction point in the current workflow.

### Play / pause button
**Status:** To do  
Add a play button that advances frames in real time using a QTimer at the video's
native FPS. Spacebar shortcut to toggle. Lets you watch the video roll instead of
only scrubbing manually.

### Output format options
**Status:** To do  
Currently saves PNG only. Add a format selector: PNG, JPG (with quality slider),
TIFF. Add an "Output folder" button so captures don't have to go into
`captured_frames/` subfolder.

---

## Medium priority

### Keyboard shortcuts
**Status:** To do  
- Space — play / pause
- Left / Right arrow — previous / next frame
- Shift + Left / Right — ±1 second
- Cmd + S — capture current frame

### Video info display
**Status:** To do  
Show codec, resolution, frame rate, and duration for the loaded video.
File list should also show duration and resolution per file, not just filename.

### Sync batch timecode from scrubber
**Status:** To do  
Related to "Use current frame position" — also make the batch spin box
reflect the scrubber position in real time as an option.

### Output preview panel
**Status:** To do  
Small thumbnail strip showing already-captured frames from the current session.
Click a thumbnail to see the full image. Prevents duplicate captures.

---

## Low priority

### Drag and drop
**Status:** To do  
Accept drag-and-drop of video files or folders onto the window.
Currently requires clicking "Select Video Folder" every time.

### Multiple timecode batch
**Status:** To do  
Batch mode currently extracts one timecode per run. Add a list of timecodes
so you can capture frame at 0s, 30s, and 60s from every video in one pass.

### Dark theme consistency
**Status:** To do  
Dark palette is set but some Qt widgets ignore it (QGroupBox borders,
QDoubleSpinBox backgrounds). Apply consistent stylesheet across all widgets.

### Capture confirmation feedback
**Status:** To do  
After a single capture, briefly flash the preview border green and show
the output filename. Currently only the status bar updates, easy to miss.
