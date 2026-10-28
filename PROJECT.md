# Project — Video Frame Capture

---

## Completed Features

### Interactive Timeline, J/K/L Shuttle & Numeric Timing Strip
**Status:** Done
- Visual `TimelineMapBar` showing draggable green `[` / `]` Trim In & Out lines (double-click to reset), draggable orange Markup spans, cyan Hold Frame pause window, and gold Scene/Chapter ticks.
- Live numeric timing spinboxes directly above the scrubber (`Playhead` jump-to-time + `Go`, `Clip` Trim In/Out, `🖍 Markup` start/end times, `❄️ Hold` timestamp + duration).
- `J` / `K` / `L` shuttle transport supporting `-16x` to `+16x` (`1x, 2x, 4x, 8x, 16x`).
- `Spacebar` dedicated to play/pause with `Qt.NoFocus` on buttons so Spacebar never toggles focused controls.

### Timed On-Video Markup, Select/Resize/Delete & Stacking Hold Frame
**Status:** Done
- Draw Boxes, Circles, Arrows, and Text with customizable color and line width.
- Every markup item has editable `start_sec` and `end_sec` (defaulting to a 3-second range).
- Click-to-select with the Pointer tool: dashed bounding box + draggable resize handles (8 for shapes, 2 endpoints for arrows), drag-to-move, Delete/Backspace or the 🗑 button to remove the selected one.
- Hold Frame (`❄️ Hold`) is a real timeline insert, not a playback pause: click at the playhead, pick a duration, Add drops a frozen-frame segment that extends the total timeline. Multiple holds stack. Holds are click-select/drag-move/Delete on the timeline, same pattern as markups, and render into exported MP4s correctly in sequence.
- Live playback slaves the displayed frame to the dub/narration audio's actual clock (not a fixed tick counter), so lipsync holds over long clips. The playhead can also be dragged during live playback — it pauses for the drag and resumes from the drop point.

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
- Live microphone dub recording (`🎙 Dub Record`, `Play`, `Load`) synced with video preview playback; Duck/Replace/Mix modes for blending with original audio. Requires `NSMicrophoneUsageDescription` in the packaged app's Info.plist or macOS silently denies mic access (fixed 2026-10-28 — was missing, producing silent recordings with no error).
- Offline Whisper AI speech-to-text transcription (`💬 Captions AI`) using bundled `faster-whisper` (`tiny`). Transcription-only, no speaker diarization.
- `Burn into video/preview` toggle (captions as data-only vs. rendered), Bottom/Top burn position, non-modal live-highlighting `Cap Text` drawer, `Cap Export` for `.srt` / `.txt` / `.csv`.
- `faster-whisper` needs `av==16.0.0` pinned in `requirements.txt` — newer `av` removed a kwarg faster-whisper's audio decoder calls, breaking every transcription with a `TypeError`.

### Still Frame Capture Modes & Organized Subfolders
**Status:** Done
- Single, Batch, Interval, and Scene capture modes laid out in a clean 4-card horizontal tab.
- Saves captured still images into an organized `<output_folder>/<video_stem>_frames/` subfolder (matching `<video_stem>_segments/`).
- Contact Sheet PDF generator automatically discovers images in the output folder and `*_frames/` subfolders.

### Video Import, Link Downloader & Self-Contained macOS App
**Status:** Done
- `📂 Import Video(s)`, `📁 Open Folder`, and drag-and-drop support.
- Download Video from Link (`yt-dlp` + bundled FFmpeg H.264/MP4 conversion + MP3 extraction) across 11 platforms: YouTube, Vimeo, X/Twitter, Facebook, Instagram, TikTok, Reddit, Twitch, LinkedIn (including shortened `lnkd.in` links), Dailymotion, Loom. Plus `🔄 Update Engine`.
- Standalone `dist/Video Frame Capture.app` bundle built with `build_app.py`.
