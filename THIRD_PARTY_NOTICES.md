# Bundled components

The desktop build includes these third-party components:

- FFmpeg 7.1 from imageio-ffmpeg 0.6.0's Apple Silicon wheel, including the
  H.264 and AAC encoders used for conversion. The executable is a separate
  program invoked by this application. This build uses GPL-enabled FFmpeg.
  Project and source: https://ffmpeg.org/ and https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz
  Binary build project: https://github.com/imageio/imageio-binaries
  License information is available from the bundled executable with `-L`.
- imageio-ffmpeg (BSD-2-Clause): https://github.com/imageio/imageio-ffmpeg
- yt-dlp (Unlicense): https://github.com/yt-dlp/yt-dlp
- yt-dlp-ejs: https://github.com/yt-dlp/ejs
- Deno (MIT), including its dependencies: https://github.com/denoland/deno
- PyQt5 and Qt: https://www.riverbankcomputing.com/software/pyqt/
- OpenCV: https://opencv.org/

Package metadata and available package license files are included by the build
tools. Deno's dependency notices can be viewed with the bundled `deno license`.
