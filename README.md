# Video Frame Capture

A macOS desktop app for extracting still frames from video files.

## Four capture modes

**Single** — load a video, scrub to any frame, capture it  
**Batch** — add one or more timecodes and extract those frames from every video in a folder  
**Interval** — capture a frame every N seconds across a single video or an entire folder  
**Scene** — auto-detect hard cuts via FFmpeg and capture one frame per scene (single video or whole folder)

## Requirements

- Python 3.10+
- `pip install PyQt5 opencv-python`
- FFmpeg in PATH — required for Scene Capture (`brew install ffmpeg`)

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
