# Installing Video Frame Capture

## Requirements
- Mac with Apple Silicon (M1, M2, M3, or M4)
- macOS 12 or later
- The day-to-day app is the spec build in `~/Apps/Video Frame Capture.app`. YouTube downloads on this Mac use Homebrew Deno. The `build_app.py` bundle is the one that carries FFmpeg, yt-dlp, and Deno inside the app.

---

## Installation

Simply **double-click** `Video Frame Capture.app` to launch it. If macOS asks "Are you sure?" on first run, click **Open**. That's it.

---

## Using the app

### Download from YouTube, X, Facebook, Instagram, TikTok, or LinkedIn
1. Click **Download Video from Link**.
2. Paste an individual video or post link.
3. Choose a download folder and quality, then click **Download**.
4. The completed video opens in frame capture automatically.

An X video and a Facebook video both downloaded on October 15, 2026. YouTube,
Instagram, and TikTok were not retried that day. YouTube needs Deno or Node
(`brew install deno`). The spec-built app finds Homebrew Deno even when Finder
does not put it on PATH.

MKV downloads automatically convert to MP4 first. The original MKV is kept.
If conversion fails, the app loads the original and shows a message in the
download window. Conversion can be cancelled without losing the original.

For videos that require sign-in, choose the browser where you can watch the video
under **Use sign-in from**, then retry. This reads that browser's cookies only when
you start the download. Public downloads use **None** by default. Some videos can
remain unavailable because of site restrictions or changes.

The spec-built app uses Homebrew Deno for YouTube. `build_app.py` is the bundle
that carries the downloader, Deno, and FFmpeg inside the app. For source
installations, install `requirements.txt` (which includes FFmpeg) and Deno or
Node.js. See README.md for downloader updates.

### Contact sheet
1. Set the batch timecode the same way as Batch Capture, or skip that if you already have stills.
2. Click **Contact Sheet…**.
3. Choose **Batch Frames**, then **This Video** or **All Videos**. Or choose **Frame Folder…** and open a folder of PNG, JPEG, or TIFF files.
4. Set **Per page**, paper size, and **Landscape** or **Portrait**. The preview is the printed page. One frame is centered. Portrait is a tall sheet.
5. Click **Print to PDF**. One PDF holds every page. Show in Finder selects the saved file.

Shane confirmed this layout on October 15, 2026. A black opening frame looks ahead up to 3 seconds. The caption shows the time that was used. Reprint an older PDF to pick up a layout change. An already-open Contact Sheet stays on the build that created it until you quit and relaunch.

### Single capture
1. Click **Select Video Folder** (or drag a folder onto the window)
2. Click a video in the list to preview it
3. Scrub to the frame you want — use the slider, arrow keys, or nav buttons
4. Click **Capture This Frame** (or press **Cmd+S**)

### Batch capture (fixed timecodes)
1. Enter a timecode in seconds and click **+ Add** (repeat for multiple timecodes)
2. Click **Batch Capture All Videos** to extract those frames from every video in the folder

### Interval capture (every N seconds)
1. Set the **Every** spinner to your desired interval (e.g. 3.0 sec)
2. Click **This Video** to sweep the current video, or **All Videos** to sweep the whole folder
3. One frame per interval is exported, named `videoname_3.0s.png`, `videoname_6.0s.png`, etc.

### Scene capture (auto-detect cuts)
1. Adjust the **Sensitivity** slider — lower captures more cuts, higher only catches hard slams (default 0.40 is a good starting point)
2. Click **This Video** or **All Videos**
3. FFmpeg scans for scene changes; the midpoint frame of each scene is exported, named `videoname_scene001_0.00s.png`, etc.
> **Requires FFmpeg.** If not installed: `brew install ffmpeg`

Captured frames are saved to the **Output Folder** shown at the top left (defaults to `captured_frames/` inside your video folder).

---

## Troubleshooting

**"App can't be opened because it is from an unidentified developer"**
Click **Open** in the dialog. This only appears once on first launch.

**App won't open at all**
Confirm your Mac has Apple Silicon (Apple menu → About This Mac → look for "Chip: Apple M…").
This build does not run on Intel Macs.

**Scene Capture shows "FFmpeg Not Found" warning**
The standalone app includes FFmpeg. Reinstall the app if its bundled files are
missing. For a source installation, reinstall `requirements.txt`.
