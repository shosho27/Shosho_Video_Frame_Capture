# Known Issues and Limitations

## All batch modes run on the main thread

Batch, Interval, and Scene capture all call `QApplication.processEvents()` each
iteration to keep the UI responsive, but they still block the main thread. On
large folders this can make the window sluggish. A proper fix would move
processing to a QThread.

## HAP video not supported

OpenCV cannot decode HAP or HAP-Alpha encoded `.mov` files. These will fail
silently — `cap.isOpened()` returns True but `cap.read()` returns no frame.
Use ffmpeg-based extraction for HAP content.

## Frame seek accuracy

`cv2.CAP_PROP_POS_FRAMES` seek is not always frame-accurate on variable
frame rate (VFR) video or certain compressed formats. The extracted frame
may be slightly before or after the requested position.

Since 2026-10-08 batch capture only seeks when that is cheaper than reading
forward (`_read_frames`); a frame reached by reading forward is always exact.
On the files tested (DJI HEVC, ProRes) both ways gave byte-identical frames,
but on a VFR file the forward-read frames may now differ from older runs —
by being correct. Interval capture on a 126s HEVC clip went 46s -> 23s at 1s.

## Scene detection requires FFmpeg

The Scene Capture mode shells out to FFmpeg for cut detection. The standalone app includes FFmpeg through imageio-ffmpeg. Source installations
get it from requirements.txt. A missing or damaged bundled binary can prevent
scene capture and conversion; reinstall the app or its source dependencies.

## Scene detection is threshold-sensitive

The FFmpeg `scene` filter compares frame histograms. A threshold of 0.40
(default) works well for most broadcast footage but may miss subtle cuts in
low-contrast video or over-trigger on flash frames. Adjust the Sensitivity
slider if results are unexpected.

## Apple Silicon only

The distributed build is arm64. Will not run on Intel Macs.

## The day-to-day app does not bundle Deno

`Video Frame Capture.spec` is the build copied to `~/Apps`. It finds Deno at
`/opt/homebrew/bin/deno` or `/usr/local/bin/deno` when Finder's PATH does not
include Homebrew. A Mac without Deno or Node can fail a YouTube download with
an n-challenge error. X and Facebook downloads succeeded on October 15, 2026
with this build. YouTube was not retried after that lookup was added.
`build_app.py` is the build that puts Deno inside the app, and that bundle has
not been rebuilt for this round.

## Audio-only captioning needs FFmpeg, and its working file is temporary

Dropping a bare audio file wraps it in a throwaway black-frame `.mp4` under
the system temp directory (`tempfile.mkdtemp`) so the normal video pipeline
can caption it. That wrapper file is not reloaded on relaunch — close and
re-drop the audio file in a new session rather than expecting it in the
recent-files list. Requires FFmpeg, same as Scene Detection.

## An open window keeps the previous build

Replacing `dist/` or `~/Apps` does not change an app that is already running.
Quit Video Frame Capture and open it again. An already-open Contact Sheet also
keeps the frames it already grabbed.
