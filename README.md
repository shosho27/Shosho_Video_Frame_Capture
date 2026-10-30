# Video Frame Capture

A macOS desktop studio for **downloading video from social media, video editing, timed markup annotations, scene/chapter splitting, voiceover dubbing, offline AI captions, and still frame extraction**.

---

## Key Features

### 1. Interactive Timeline, J/K/L Shuttle & Direct Numeric Timing
- **Visual Timeline Map Bar**: Displays live visual spans for:
  - **Green `[` / `]` Lines**: Trim In & Trim Out bounds (drag either green line directly on the timeline—including all the way back out to the end—or double-click a green line to reset it).
  - **Orange Spans**: Timed markup annotations (drag to move or resize start/end handles, click to select, Delete to remove).
  - **Cyan Blocks**: Frozen-frame Hold inserts (see below) — click to select, drag to reposition, Delete to remove. Multiple holds stack, each one visibly extending the timeline.
  - **Gold Ticks**: Scene cut / chapter markers.
  - Dragging the playhead works during live playback too — it pauses for the duration of the drag and resumes from wherever you drop it.
- **Direct Numeric Entry Strip**: Type exact values (in seconds) directly above the scrubber:
  - **`Playhead:`** Type a timestamp and press Enter (or click **`Go`**) to jump straight to that moment.
  - **`Clip:`** Type exact **`Trim In`** and **`Trim Out`** seconds, or click **`Full`** to reset to the full video.
  - **`🖍 Markup:`** Select any drawn arrow/box/circle/line/text and type exact start/end seconds (e.g., extend an arrow from `3.00s` to `10.00s`), or click **`[ In`** / **`Out ]`** to stamp the current playhead time.
  - **`❄️ Hold:`** Click **`Hold`** at the playhead to open a duration popover; **Add** drops a frozen-frame segment there (see "Hold Frame" below).
- **Transport & Keyboard Shortcuts**:
  - **`Space`**: Toggle Play / Pause (always controls video transport, never toggles focused buttons).
  - **`J` / `K` / `L` Shuttle**:
    - **`L`**: Forward shuttle (`1x → 2x → 4x → 8x → 16x`).
    - **`J`**: Reverse shuttle (`-1x → -2x → -4x → -8x → -16x`).
    - **`K`**: Pause immediately.
  - **`I` / `O`**: Set Trim In (`[`) / Trim Out (`]`) at the playhead.
  - **`Left` / `Right`**: Step 1 frame backward / forward (`Shift+Left` / `Shift+Right` for ±1 second).
  - **`Cmd+S`**: Capture current still frame.
  - **`Delete` / `Backspace`**: Remove whatever's currently selected — a markup or a Hold block.
- **Live audio sync**: during playback, the displayed frame is slaved to the dub/narration audio's actual clock rather than a fixed per-tick counter, so lipsync can't drift over a long clip.

---

### 2. Timed On-Video Markup & Annotations
- Draw **Boxes, Circles, Arrows, and Text** directly on the video canvas.
- Choose custom **Width** and **Color**.
- Every annotation is **time-ranged** (`start_sec → end_sec`, defaulting to a 3-second window from when it is drawn) so it appears and disappears automatically during playback and in exported MP4s / GIFs.
- Switch to **`Pointer`** tool to click any annotation (on the canvas or its orange bar on the timeline): a dashed selection box with draggable corner/edge handles appears (two endpoint handles for arrows), so you can resize or move it. **Delete**/**Backspace** or the **🗑 Delete** button removes whichever markup is currently selected.

---

### 3. Hold Frame (stacking freeze-frame inserts), Trim, Fade, MP4 Quality, MP3 & GIF Export
Located in **Tab 1 (`🎬 Edit, Export (MP4 / MP3 / GIF), Chapters, Dub & Captions`)**:
- **Hold Frame (`❄️ Hold`)**: Click it at the playhead to open a Duration slider + Add/Cancel popover. Add inserts a real frozen-frame segment at that point — the timeline immediately gets longer. Add more than one and they stack, each extending the total runtime further. During playback, the playhead visibly travels across an inserted hold over its real duration rather than just freezing in place. Holds are selectable/draggable/deletable on the timeline, same as markups.
- **MP4 Export (`💾 Save MP4`)**: Exports the `[Trim In → Trim Out]` range with burned-in timed markups, captions (if burn-in is on), fade-in/out, every Hold insert, and mixed voiceover audio.
- **MP4 Quality Selector**:
  - **High Quality (CRF 16)** — Near-lossless H.264 output
  - **Balanced (CRF 20)** — Default crisp quality
  - **Compact / Web (CRF 24)** — Smaller file size for sharing
  - **Small File (CRF 28)** — Maximum compression
- **1-Click Audio Export (`🎵 Save MP3`)**: Extracts the trimmed audio range directly to `.mp3` at **320 kbps**, **192 kbps**, or **128 kbps**.
- **Animated GIF Export (`🎞 Save GIF`)**: Exports a high-quality palette-optimized `.gif` of the trimmed region (with annotations and captions included).

---

### 4. Scene Detection, Separate Clips & QuickTime MP4 Chapters
- **Auto-Detect Cuts (`🔍 Detect`)**: Uses FFmpeg scene score detection (`Sens` `0.05–0.95`) within your `[Trim In → Trim Out]` bounds to populate cut/chapter markers on the timeline.
- **Manual Markers**: Click **`+ Cut`** to add a cut/chapter at the playhead, **`- Cut`** to remove the nearest marker, or **`Clear`** to remove all markers.
- **Create Separate Clips (`✂️ Split Clips`)**: Splits the video at every marker within `[Trim In → Trim Out]` into `<output_folder>/<video_stem>_segments/`.
- **QuickTime-Compatible MP4 Chapters (`📑 MP4 + Chapters`)**: Exports an `.mp4` trimmed to `[Trim In → Trim Out]` with embedded `mov_text` chapter tracks tagged with ISO-639-2 `'eng'` (`0x15c7`) so **macOS QuickTime Player** and VLC display the interactive chapter menu out of the box.

---

### 5. Voiceover Dub Recording & Offline AI Captions
- **Live Voiceover Recording (`🎙 Dub Record`)**:
  - Click **`🎙 Dub Record`** to record from your selected macOS microphone while watching the video preview play.
  - Click again to stop, **`Play`** to audition your recording, **`Load`** to attach an existing audio file instead, or the **`×`** to clear it.
  - Choose **`Duck Video 20%`**, **`Replace Audio`**, or **`Mix 100%`** to control how your dub blends with the original audio.
  - macOS will prompt for microphone access the first time — required or recordings come out silent.
- **Offline Whisper AI Captions (`💬 Captions AI`)**:
  - Transcribes speech into timed captions completely offline using the bundled `faster-whisper` (`tiny`) model — no internet connection or API key required (it does **not** identify different speakers; that needs a separate diarization model, not currently included).
  - **`Burn into video/preview`** toggle: on, captions are drawn into the live preview and exported MP4/GIF; off, they stay as timed data only — useful if you just want the transcript/SRT without anything rendered on screen.
  - **`Bottom` / `Top`** position selector for burned-in captions, so they don't cover a lower third.
  - **`Cap Text`** opens a non-modal drawer listing every caption — it stays open and live-highlights/auto-scrolls to the active line while you keep playing the video.
  - **`Cap Export`** loads or saves captions as `.srt` (for Premiere's Captions panel / File > Import), plain `.txt` transcript, or `.csv`.
  - **Caption an audio-only file**: drag a bare `.wav`, `.mp3`, `.m4a`, `.aac`, or `.flac` onto the main window (no video needed). It's wrapped in a throwaway black-frame video behind the scenes so the normal player, `Captions AI`, and `Cap Export` all work on it unchanged — there's nothing to look at, just the transcript.

---

### 6. Still Frame Capture Modes & Organized Subfolders
Located in **Tab 2 (`📸 Still Frame Capture (Single, Batch, Interval & Scene)`)**:
- **Single (`Capture This Frame`)**: Captures the exact visible frame (including active markups and captions).
- **Batch**: Extract frames at a list of timecodes across every video in a folder.
- **Interval**: Extract a frame every `N` seconds within `[Trim In → Trim Out]` (for the current video or all videos in a folder).
- **Scene**: Auto-detect scene cuts within `[Trim In → Trim Out]` and capture the midpoint frame of each scene.
- **Organized `<video_stem>_frames/` Output**: All still captures are automatically saved inside `<output_folder>/<video_stem>_frames/` (matching `<output_folder>/<video_stem>_segments/` for split video clips), and **Contact Sheet PDF** generation reads from your output folder and `*_frames/` subfolders automatically.
- **Contact Sheet frame order**: frames are sorted naturally by their timestamp (`…_12.0s` before `…_102.0s`), not as plain text, so a folder of captured frames lays out in the order they actually occur.
- **Contact Sheet picture boxes**: each cell's border hugs the actual picture (no blank band above/below a wide video frame sitting in a near-square cell), and the grid itself is scored against the pictures' own aspect ratio, not forced square.
- **Contact Sheet page guide**: the preview panel behind the page is a dark neutral color, so the white page's actual edge is visible instead of blending into a white-on-white background.

---

### 7. Video Import & Link Downloader
- **Import Local Videos**: Click **`📂 Import Video(s)`**, **`📁 Open Folder`**, or drag and drop video files/folders directly onto the window.
- **Download Video from Link**: Paste a link from **YouTube, Vimeo, X/Twitter, Facebook, Instagram, TikTok, Reddit, Twitch, LinkedIn, Dailymotion, or Loom** (including shortened `lnkd.in` LinkedIn links — resolved automatically), select quality (`Best`, `1080p`, `720p`) or `MP3` mode, and download directly into your workspace with automatic H.264/MP4 conversion.
- **Built-In Engine Updater (`🔄 Update Engine`)**: Updates the standalone `yt-dlp` engine with SHA-256 verification.

---

## Standalone macOS App

Build the self-contained macOS `.app` bundle (`dist/Video Frame Capture.app`):

```bash
.venv/bin/python build_app.py
```

FFmpeg, the offline `faster-whisper` (`tiny.en`) model, `yt-dlp`, and Deno are bundled inside `Video Frame Capture.app`—no external dependencies required.

Every build stamps the git commit/date it was built from into `src/_build_stamp.py`, shown in the window title (e.g. `built 2026-10-28 14:00`) — always check this before reporting a bug, since a window left open from a previous build looks identical otherwise. The status area has a scrolling log with a **Copy Log** button for grabbing diagnostics quickly.

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
