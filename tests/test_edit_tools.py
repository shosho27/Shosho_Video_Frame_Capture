import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2
import numpy as np
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication

from video_edit_tools import (
    Annotation,
    CaptionCue,
    _bundled_whisper_model,
    export_audio_clip,
    export_mp4_with_chapters,
    export_video_or_gif,
    parse_srt,
    render_annotations_on_bgr,
    render_annotations_on_qimage,
    split_video_by_cuts,
    write_srt,
)
from video_frame_capture import VideoFrameCapture, _find_ffmpeg


class EditToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.ffmpeg = _find_ffmpeg()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.video_path = os.path.join(self.temp.name, "sample.mp4")
        # Generate a 2-second test video with two distinct color scenes and a sine tone
        subprocess.run(
            [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=1",
                "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                self.video_path,
            ],
            check=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_annotations_and_captions_render_on_frame(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        anns = [
            Annotation(kind="arrow", x1=0.1, y1=0.1, x2=0.5, y2=0.5, color="#ff3b30", thickness=4),
            Annotation(kind="circle", x1=0.2, y1=0.2, x2=0.6, y2=0.6, color="#ffcc00", thickness=3),
            Annotation(kind="rect", x1=0.6, y1=0.1, x2=0.9, y2=0.4, color="#34c759", thickness=3),
            Annotation(kind="text", x1=0.3, y1=0.3, color="#ffffff", thickness=4, text="Watch Here"),
        ]
        rendered = render_annotations_on_bgr(frame, anns, caption_text="Subtitles work with sound off")
        self.assertEqual(rendered.shape, frame.shape)
        self.assertGreater(int(rendered.sum()), 0)

    def test_srt_roundtrip(self):
        cues = [
            CaptionCue(start=0.5, end=2.25, text="First line of speech"),
            CaptionCue(start=2.5, end=4.0, text="Second line here"),
        ]
        srt_text = write_srt(cues)
        parsed = parse_srt(srt_text)
        self.assertEqual(len(parsed), 2)
        self.assertAlmostEqual(parsed[0].start, 0.5, places=2)
        self.assertEqual(parsed[1].text, "Second line here")

    def test_trim_fade_freeze_and_narration_mp4_export(self):
        narr_wav = os.path.join(self.temp.name, "vo.wav")
        subprocess.run(
            [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
                narr_wav,
            ],
            check=True,
        )
        out_mp4 = os.path.join(self.temp.name, "trimmed_freeze.mp4")
        anns = [Annotation(kind="circle", x1=0.2, y1=0.2, x2=0.8, y2=0.8, color="#ff3b30", thickness=4)]
        cues = [CaptionCue(start=0.0, end=2.0, text="Hello caption")]
        export_video_or_gif(
            ffmpeg=self.ffmpeg,
            video_path=self.video_path,
            out_path=out_mp4,
            start_sec=0.2,
            end_sec=1.8,
            fade_in=0.3,
            fade_out=0.3,
            annotations=anns,
            freeze_at=1.0,
            freeze_duration=1.0,
            captions=cues,
            narration_path=narr_wav,
            narration_mode="duck",
            as_gif=False,
        )
        self.assertTrue(os.path.isfile(out_mp4))
        cap = cv2.VideoCapture(out_mp4)
        fps = cap.get(cv2.CAP_PROP_FPS) or 10
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        # 1.6s trim + 1.0s freeze hold = ~2.6s (~26 frames at 10fps)
        self.assertGreaterEqual(frames / fps, 2.2)

    def test_gif_and_audio_export(self):
        out_gif = os.path.join(self.temp.name, "clip.gif")
        export_video_or_gif(
            ffmpeg=self.ffmpeg,
            video_path=self.video_path,
            out_path=out_gif,
            start_sec=0.0,
            end_sec=1.0,
            as_gif=True,
            gif_fps=10,
            gif_width=120,
        )
        self.assertTrue(os.path.isfile(out_gif))
        self.assertGreater(os.path.getsize(out_gif), 500)

        out_mp3 = os.path.join(self.temp.name, "clip.mp3")
        export_audio_clip(
            self.ffmpeg, self.video_path, out_mp3,
            start_sec=0.2, end_sec=1.5, fade_in=0.2, fade_out=0.2, fmt="mp3",
        )
        self.assertTrue(os.path.isfile(out_mp3))
        self.assertGreater(os.path.getsize(out_mp3), 500)

    def test_adjustable_cuts_and_segment_splitter(self):
        window = VideoFrameCapture()
        window._load_folder(self.temp.name)
        window.file_list.setCurrentRow(0)
        window.detect_cuts_for_current()
        # Add/adjust a cut and split into segments
        window.frame_slider.setValue(8)
        window.add_cut_at_current()
        self.assertTrue(len(window.detected_cuts) >= 1)
        window.frame_slider.setValue(10)
        window.cuts_list.setCurrentRow(0)
        window.move_selected_cut_to_current()
        self.assertIn(1.0, window.detected_cuts)

        seg_dir = os.path.join(self.temp.name, "segments")
        files = split_video_by_cuts(self.ffmpeg, self.video_path, seg_dir, window.detected_cuts)
        self.assertEqual(len(files), 2)
        for f in files:
            self.assertTrue(os.path.isfile(f))

        # Verify embedding scene cuts as native MP4 chapter markers
        chap_mp4 = os.path.join(self.temp.name, "with_chapters.mp4")
        saved_path, chapters = export_mp4_with_chapters(
            self.ffmpeg,
            self.video_path,
            chap_mp4,
            window.detected_cuts,
            chapter_titles={0.0: "Intro Scene", 1.0: "Red Scene"},
        )
        self.assertTrue(os.path.isfile(saved_path))
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0][2], "Intro Scene")
        self.assertEqual(chapters[1][2], "Red Scene")

        # Probe MP4 with FFmpeg to verify Chapter #0:0 and Chapter #0:1 are embedded in container
        probe = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-i", saved_path],
            capture_output=True, text=True,
        )
        self.assertIn("Chapter #0:0", probe.stderr)
        self.assertIn("Intro Scene", probe.stderr)
        self.assertIn("Chapter #0:1", probe.stderr)
        self.assertIn("Red Scene", probe.stderr)
        window.close()

    def test_bundled_whisper_model_exists_offline(self):
        path = _bundled_whisper_model("tiny")
        self.assertTrue(os.path.isdir(path), f"Expected bundled whisper-tiny dir, got {path}")
        self.assertTrue(os.path.isfile(os.path.join(path, "model.bin")))

    def test_timed_markup_hold_feedback_and_jkl_shuttle(self):
        window = VideoFrameCapture()
        # Exercise paintEvent before any video is loaded (duration == 0.0)
        window.show()
        window.timeline_bar.repaint()
        self.app.processEvents()

        window._load_folder(self.temp.name)
        window.file_list.setCurrentRow(0)
        window.timeline_bar.repaint()
        self.app.processEvents()

        # Draw a timed annotation from 0.20s to 1.20s
        window.preview_label.annotations.append(
            Annotation(
                kind="arrow", x1=0.1, y1=0.1, x2=0.5, y2=0.5,
                start_sec=0.2, end_sec=1.2,
            )
        )
        window.frame_slider.setValue(5)  # 0.50s -> visible
        self.assertEqual(len(window.preview_label.visible_annotations_at(0.5)), 1)
        self.assertIn("0.20s → 1.20s", window.feedback_banner.text())

        window.frame_slider.setValue(18)  # 1.80s -> off-screen
        self.assertEqual(len(window.preview_label.visible_annotations_at(1.8)), 0)

        # Adjust Markup Out to current playhead (1.80s)
        window.set_last_markup_out()
        self.assertAlmostEqual(window.preview_label.annotations[-1].end_sec, 1.8, places=2)
        self.assertEqual(len(window.preview_label.visible_annotations_at(1.8)), 1)

        # Toggle Hold Frame at 1.00s and check feedback banner shows begin & end
        window.frame_slider.setValue(10)  # 1.00s
        window.freeze_dur_spin.setValue(2.5)
        window.btn_freeze_here.setChecked(True)
        self.assertIn("Hold Frame: [1.00s → +2.5s (ends 3.50s)]", window.feedback_banner.text())

        # JKL transport shuttle
        window.shuttle_forward()
        self.assertTrue(window._playing)
        self.assertEqual(window._playback_rate, 1.0)
        window.shuttle_forward()
        self.assertEqual(window._playback_rate, 2.0)
        window.shuttle_reverse()
        self.assertEqual(window._playback_rate, -1.0)
        window.shuttle_reverse()
        self.assertEqual(window._playback_rate, -2.0)
        window.shuttle_pause()
        self.assertFalse(window._playing)
        window.close()


if __name__ == "__main__":
    unittest.main()
