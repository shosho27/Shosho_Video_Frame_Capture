# Known Issues and Limitations

## Batch runs on the main thread

Batch capture calls `QApplication.processEvents()` each iteration to keep the
UI responsive, but it still blocks the main thread. On large folders this can
make the window sluggish. A proper fix would move batch processing to a QThread.

## HAP video not supported

OpenCV cannot decode HAP or HAP-Alpha encoded `.mov` files. These will fail
silently — `cap.isOpened()` returns True but `cap.read()` returns no frame.
Use ffmpeg-based extraction for HAP content.

## Frame seek accuracy

`cv2.CAP_PROP_POS_FRAMES` seek is not always frame-accurate on variable
frame rate (VFR) video or certain compressed formats. The extracted frame
may be slightly before or after the requested position.

## Output folder is fixed

Captured frames always go to a `captured_frames/` subfolder inside the
selected video folder. There is no option to choose a different output location.
This is a planned improvement (see PROJECT.md).

## Apple Silicon only

The distributed build is arm64. Will not run on Intel Macs.
