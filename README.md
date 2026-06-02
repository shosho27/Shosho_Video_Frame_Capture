# Shosho Video Frame Capture

A macOS desktop app for extracting still frames from video files.

## Two modes

**Single** — load a video, scrub to any frame, capture it as PNG  
**Batch** — set a timecode and extract that frame from every video in a folder at once

## Requirements

- Python 3.10+
- `pip install PyQt5 opencv-python`

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
