# Video Frame Capture

A macOS desktop app for extracting still frames from video files.

## Four capture modes

**Single** — load a video, scrub to any frame, capture it  
**Batch** — add one or more timecodes and extract those frames from every video in a folder  
**Interval** — capture a frame every N seconds across a single video or an entire folder  
**Scene** — auto-detect hard cuts via FFmpeg and capture one frame per scene (single video or whole folder)

## Standalone Mac app

Build with the instructions in BUILDING.md, then double-click
`Video Frame Capture.app`. FFmpeg, the downloader, and the YouTube runtime
are included in the app; no separate installation is needed.

## Running from source

- Python 3.10+
- `python3 -m pip install -r requirements.txt`
- FFmpeg is included by the `imageio-ffmpeg` requirement (used automatically)
- Deno (recommended) or Node.js for full YouTube download support (`brew install deno`)

## Download videos from links

Click **Download Video from Link**, paste a YouTube, X / Twitter, Facebook,
or Instagram video / Reel link, choose a folder and quality, then click
**Download**. The download runs in the background with progress and a cancel
button. Completed videos are loaded automatically into frame capture.

If the video requires sign-in, select the browser where you can watch it under
**Use sign-in from**. This lets yt-dlp read that browser's cookies for the download;
the default is **None**, which does not access browser cookies. macOS may request
permission to read browser data. Access restrictions and site changes can prevent
downloads. Use individual video or post links rather than profile or channel URLs.

The included FFmpeg merges downloaded video and audio. MKV downloads are
automatically converted to MP4 before loading into frame capture. The app first
tries a fast conversion without re-encoding; if the codecs cannot go into MP4,
it converts to H.264 video and AAC audio, which takes longer. Original MKVs are
kept, existing MP4 files are never replaced, and a failed conversion leaves the
original available for frame capture. Cancelling removes the temporary conversion
file and keeps the original. 1080p and 720p limits are also available.
Posts containing multiple videos may download multiple files; the first is selected
automatically and the others appear in the video list. Cancelled downloads keep
partial files for retrying. Existing completed files are not overwritten.

Downloads use [yt-dlp](https://github.com/yt-dlp/yt-dlp). If a site stops working,
update it in the app's environment with:

```bash
.venv/bin/python -m pip install --upgrade 'yt-dlp[default]'
```

## Running

```bash
python3 src/video_frame_capture.py
```

## Project structure

```
src/
  video_frame_capture.py    Main application
assets/
  Frame_Capture.icns        App icon
  Frame_Capture.png         App icon source
PROJECT.md                  Planned improvements and feature tracking
```

## License

MIT
