# Project — Video Frame Capture

---

## Completed Features

### Interactive Timeline, J/K/L Shuttle & Numeric Timing Strip
**Status:** Done
- Visual `TimelineMapBar` showing draggable green `[` / `]` Trim In & Out lines (double-click to reset), draggable orange Markup spans, cyan Hold Frame pause window, and gold Scene/Chapter ticks.
- Live numeric timing spinboxes directly above the scrubber (`Playhead` jump-to-time + `Go`, `Clip` Trim In/Out, `🖍 Markup` start/end times, `❄️ Hold` timestamp + duration).
- `J` / `K` / `L` shuttle transport supporting `-16x` to `+16x` (`1x, 2x, 4x, 8x, 16x`).
- `Spacebar` dedicated to play/pause with `Qt.NoFocus` on buttons so Spacebar never toggles focused controls.

### Timed On-Video Markup & Hold Frame
**Status:** Done
- Draw Boxes, Circles, Arrows, Lines, and Text with customizable color and line width.
- Every markup item has editable `start_sec` and `end_sec` (defaulting to a 3-second range) and can be moved/resized on canvas or timeline.
- Hold / Freeze Frame (`❄️ Hold`) pauses playback for `N` seconds and renders into exported MP4s; preserved when adjusting Trim Out.

### Trim, Fade, MP4 Quality, 1-Click MP3 & GIF Export
**Status:** Done
- MP4 Quality selector (`CRF 16 High`, `CRF 20 Balanced`, `CRF 24 Compact`, `CRF 28 Small File`).
- 1-Click MP3 extraction (`320k`, `192k`, `128k`) and animated GIF export.
- Video & audio fade-in / fade-out (`Fade In`, `Fade Out`).

### Scene Detection, Split Clips & QuickTime MP4 Chapters
**Status:** Done
- Auto-detect scene cuts via FFmpeg (`🔍 Detect`) and add/remove manual cut markers (`+ Cut`, `- Cut`, `Clear`).
- Respects `[Trim In → Trim Out]` boundaries for scene detection, splitting into `<stem>_segments/`, and exporting chapters.
- QuickTime Player compatible MP4 chapters (`📑 MP4 + Chapters`) with ISO-639-2 `'eng'` (`0x15c7`) `mdhd` language tagging.

### Voiceover Dub Recording & Offline AI Captions
**Status:** Done
- Live microphone dub recording (`🎙 Dub Record`, `🔊 Play`, `📂 Dub File`) synced with video preview playback and adjustable original-audio mix (`0–200%`).
- Offline Whisper AI speech-to-text transcription (`💬 Captions AI` → `✨ Auto-Transcribe (Offline AI)`) using bundled `faster-whisper` (`tiny.en`), plus `.srt` import/export and subtitle burn-in.

### Still Frame Capture Modes & Organized Subfolders
**Status:** Done
- Single, Batch, Interval, and Scene capture modes laid out in a clean 4-card horizontal tab.
- Saves captured still images into an organized `<output_folder>/<video_stem>_frames/` subfolder (matching `<video_stem>_segments/`).
- Contact Sheet PDF generator automatically discovers images in the output folder and `*_frames/` subfolders.

### Video Import, Link Downloader & Self-Contained macOS App
**Status:** Done
- `📂 Import Video(s)`, `📁 Open Folder`, and drag-and-drop support.
- `🌐 Download Link` (`yt-dlp` + bundled FFmpeg H.264/MP4 conversion + MP3 extraction) and `🔄 Update Engine`.
- Standalone `dist/Video Frame Capture.app` bundle built with `build_app.py`.
