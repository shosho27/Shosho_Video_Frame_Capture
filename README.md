# Video Frame Capture

A macOS desktop studio for **video editing, timed markup annotations, scene/chapter splitting, voiceover dubbing, offline AI captions, and still frame extraction**.

---

## Key Features

### 1. Interactive Timeline, J/K/L Shuttle & Direct Numeric Timing
- **Visual Timeline Map Bar**: Displays live visual spans for:
  - **Green `[` / `]` Lines**: Trim In & Trim Out bounds (drag either green line directly on the timeline—including all the way back out to the end—or double-click a green line to reset it).
  - **Orange Spans**: Timed markup annotations (drag to move or resize start/end handles).
  - **Cyan Bar**: Hold / Freeze frame pause window (rendered on top of the trim range; setting or moving Trim Out preserves your Hold Frame).
  - **Gold Ticks**: Scene cut / chapter markers.
- **Direct Numeric Entry Strip**: Type exact values (in seconds) directly above the scrubber:
  - **`Playhead:`** Type a timestamp and press Enter (or click **`Go`**) to jump straight to that moment.
  - **`Clip:`** Type exact **`Trim In`** and **`Trim Out`** seconds, or click **`Full`** to reset to the full video.
  - **`🖍 Markup:`** Select any drawn arrow/box/circle/line/text and type exact start/end seconds (e.g., extend an arrow from `3.00s` to `10.00s`), or click **`[ In`** / **`Out ]`** to stamp the current playhead time.
  - **`❄️ Hold:`** Toggle **`Hold`** and type the exact freeze timestamp (`@`) and hold duration (`+`).
- **Transport & Keyboard Shortcuts**:
  - **`Space`**: Toggle Play / Pause (always controls video transport, never toggles focused buttons).
  - **`J` / `K` / `L` Shuttle**:
    - **`L`**: Forward shuttle (`1x → 2x → 4x → 8x → 16x`).
    - **`J`**: Reverse shuttle (`-1x → -2x → -4x → -8x → -16x`).
    - **`K`**: Pause immediately.
  - **`I` / `O`**: Set Trim In (`[`) / Trim Out (`]`) at the playhead.
  - **`Left` / `Right`**: Step 1 frame backward / forward (`Shift+Left` / `Shift+Right` for ±1 second).
  - **`Cmd+S`**: Capture current still frame.

---

### 2. Timed On-Video Markup & Annotations
- Draw **Boxes, Circles, Arrows, Lines, and Text** directly on the video canvas.
- Choose custom **Pen width** (`1–24 px`) and **Color** (`Color` picker).
- Every annotation is **time-ranged** (`start_sec → end_sec`, defaulting to a 3-second window from when it is drawn) so it appears and disappears automatically during playback and in exported MP4s / GIFs.
- Switch to **`Pointer`** tool to click any annotation on the canvas (or click its orange bar on the timeline) to move, resize, or adjust its start/end times.

---

### 3. Trim, Fade, Hold Frame, MP4 Quality, MP3 & GIF Export
Located in **Tab 1 (`🎬 Edit, Export (MP4 / MP3 / GIF), Chapters, Dub & Captions`)**:
- **MP4 Export (`💾 Export MP4`)**: Exports the `[Trim In → Trim Out]` range with burned-in timed markups, captions, fade-in/out (`Fade In` / `Fade Out`), hold/freeze frame, and mixed voiceover audio.
- **MP4 Quality Selector (`Qual`)**:
  - **High Quality (CRF 16)** — Near-lossless H.264 output
  - **Balanced (CRF 20)** — Default crisp quality
  - **Compact / Web (CRF 24)** — Smaller file size for sharing
  - **Small File (CRF 28)** — Maximum compression
- **1-Click Audio Export (`🎵 MP3`)**: Extracts the trimmed audio range directly to `.mp3` at **320 kbps**, **192 kbps**, or **128 kbps** (or `.wav` / `.m4a` via the audio dialog).
- **Animated GIF Export (`🎞 GIF`)**: Exports a high-quality palette-optimized `.gif` of the trimmed region (with annotations and captions included).

---

### 4. Scene Detection, Separate Clips & QuickTime MP4 Chapters
- **Auto-Detect Cuts (`🔍 Detect`)**: Uses FFmpeg scene score detection (`Sens` `0.05–0.95`) within your `[Trim In → Trim Out]` bounds to populate cut/chapter markers on the timeline.
- **Manual Markers**: Click **`+ Cut`** to add a cut/chapter at the playhead, **`- Cut`** to remove the nearest marker, or **`Clear`** to remove all markers.
- **Create Separate Clips (`✂️ Split Clips`)**: Splits the video at every marker within `[Trim In → Trim Out]` into `<output_folder>/<video_stem>_segments/`.
- **QuickTime-Compatible MP4 Chapters (`📑 MP4 + Chapters`)**: Exports an `.mp4` trimmed to `[Trim In → Trim Out]` with embedded `mov_text` chapter tracks tagged with ISO-639-2 `'eng'` (`0x15c7`) so **macOS QuickTime Player** and VLC display the interactive chapter menu out of the box.

---

### 5. Voiceover Dub Recording & Offline AI Captions
- **Live Voiceover Recording (`🎙 Dub Record`)**:
  - Click **`🎙 Dub Record`** to record from your macOS microphone while watching the video preview play from Trim In (or current playhead).
  - Click **`⏹ Stop Dub`** to finish, **`🔊 Play`** to audition your recording, or **`📂 Dub File`** to attach an existing audio file.
  - Adjust **`Mix`** (`0%–200%`) to control original video audio volume relative to your dub (`0%` replaces original audio completely; `100%` mixes both at full volume).
- **Offline Whisper AI Captions (`💬 Captions AI`)**:
  - Opens the **Captions & Subtitles** manager.
  - Click **`✨ Auto-Transcribe (Offline AI)`** to transcribe speech into timed captions completely offline using the bundled `faster-whisper` (`tiny.en`) model—no internet connection or API key required.
  - Add, edit, or delete caption rows manually, customize font size, import/export `.srt` files, and burn captions into exported MP4s, GIFs, and still captures.

---

### 6. Still Frame Capture Modes & Organized Subfolders
Located in **Tab 2 (`📸 Still Frame Capture (Single, Batch, Interval & Scene)`)**:
- **Single (`Capture This Frame`)**: Captures the exact visible frame (including active markups and captions).
- **Batch**: Extract frames at a list of timecodes across every video in a folder.
- **Interval**: Extract a frame every `N` seconds within `[Trim In → Trim Out]` (for the current video or all videos in a folder).
- **Scene**: Auto-detect scene cuts within `[Trim In → Trim Out]` and capture the midpoint frame of each scene.
- **Organized `<video_stem>_frames/` Output**: All still captures are automatically saved inside `<output_folder>/<video_stem>_frames/` (matching `<output_folder>/<video_stem>_segments/` for split video clips), and **Contact Sheet PDF** generation reads from your output folder and `*_frames/` subfolders automatically.

---

### 7. Video Import & Link Downloader
- **Import Local Videos**: Click **`📂 Import Video(s)`**, **`📁 Open Folder`**, or drag and drop video files/folders directly onto the window.
- **Download Video from Link (`🌐 Download Link`)**: Paste a YouTube, X / Twitter, Facebook, or Instagram link, select quality (`Best`, `1080p`, `720p`) or `MP3` mode, and download directly into your workspace with automatic H.264/MP4 conversion.
- **Built-In Engine Updater (`🔄 Update Engine`)**: Updates the standalone `yt-dlp` engine with SHA-256 verification.

---

## Standalone macOS App

Build the self-contained macOS `.app` bundle (`dist/Video Frame Capture.app`):

```bash
.venv/bin/python build_app.py
```

FFmpeg, the offline `faster-whisper` (`tiny.en`) model, `yt-dlp`, and Deno are bundled inside `Video Frame Capture.app`—no external dependencies required.

## Running from Source

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/video_frame_capture.py
```

## Running Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Project Structure

```
src/
  app_main.py               macOS app entrypoint
  video_frame_capture.py    Main application UI, transport, capture & export workflows
  video_edit_tools.py       Timeline map bar, timed markups, FFmpeg export, chapters, dub & AI captions
  contact_sheet.py          Contact sheet PDF generator & preview dialog
  video_downloader.py       Link downloader (yt-dlp + FFmpeg H.264/MP3 pipeline)
  downloader_update.py      Verified yt-dlp release updater
tests/
  test_edit_tools.py        Unit tests for markups, J/K/L shuttle, chapters, scene splits, dub & AI captions
  test_contact_sheet.py     Unit tests for contact sheet generation
  test_video_downloader.py  Unit tests for downloader & codec conversion
  test_downloader_update.py Unit tests for engine updater
```

## License

MIT
