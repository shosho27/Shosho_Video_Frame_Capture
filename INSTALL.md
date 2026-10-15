# Installing Video Frame Capture

## Requirements
- Mac with Apple Silicon (M1, M2, M3, or M4)
- macOS 12 or later
- FFmpeg, the downloader, and the YouTube runtime are included in the standalone app.

---

## Installation

Simply **double-click** `Video Frame Capture.app` to launch it. If macOS asks "Are you sure?" on first run, click **Open**. That's it.

---

## Using the app

### Download from YouTube, X, Facebook, or Instagram
1. Click **Download Video from Link**.
2. Paste an individual video, post, or Reel link.
3. Choose a download folder and quality, then click **Download**.
4. The completed video opens in frame capture automatically.

MKV downloads automatically convert to MP4 first. The original MKV is kept.
If conversion fails, the app loads the original and shows a message in the
download window. Conversion can be cancelled without losing the original.

For videos that require sign-in, choose the browser where you can watch the video
under **Use sign-in from**, then retry. This reads that browser's cookies only when
you start the download. Public downloads use **None** by default. Some videos can
remain unavailable because of site restrictions or changes.

The standalone app includes all download and conversion dependencies. For source
installations, install `requirements.txt` (which includes FFmpeg) and Deno or
Node.js. See README.md for downloader updates.

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
