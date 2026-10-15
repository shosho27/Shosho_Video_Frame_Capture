"""Desktop entry point and bundled downloader subprocess entry point."""

import sys


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--shosho-download':
        import yt_dlp
        yt_dlp.main(sys.argv[2:])
    else:
        from video_frame_capture import (
            APP_NAME, QApplication, VideoFrameCapture, STYLESHEET,
        )
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationDisplayName(APP_NAME)
        app.setStyle('Fusion')
        app.setStyleSheet(STYLESHEET)
        window = VideoFrameCapture()
        window.show()
        sys.exit(app.exec_())
