#!/usr/bin/env python3
"""
Video Frame Capture Tool
========================
A simple GUI app to extract still frames from videos.

- Single Mode: Preview a video, scrub to the right frame, click Capture.
- Batch Mode: Set a timecode and extract that frame from every video in a folder.

Requirements:
    pip install PyQt5 opencv-python

Usage:
    python video_frame_capture.py
"""

import sys
import os
import glob
import cv2
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QSpinBox, QGroupBox,
    QListWidget, QMessageBox, QProgressBar, QSplitter, QFrame,
    QStyle, QSizePolicy, QDoubleSpinBox
)
from PyQt5.QtGui import QImage, QPixmap, QFont, QIcon
from PyQt5.QtCore import Qt, QTimer

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg')


class VideoFrameCapture(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Frame Capture")
        self.setMinimumSize(1000, 700)

        self.video_folder = None
        self.video_files = []
        self.current_cap = None
        self.current_video_path = None
        self.total_frames = 0
        self.fps = 30
        self.output_folder = None

        self.init_ui()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # ---- LEFT PANEL: File list ----
        left_panel = QVBoxLayout()

        folder_btn = QPushButton("📁  Select Video Folder")
        folder_btn.setMinimumHeight(40)
        folder_btn.clicked.connect(self.select_folder)
        left_panel.addWidget(folder_btn)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: #888; padding: 4px;")
        left_panel.addWidget(self.folder_label)

        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self.on_video_selected)
        left_panel.addWidget(self.file_list)

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(280)

        # ---- RIGHT PANEL: Preview + Controls ----
        right_panel = QVBoxLayout()

        # Video preview
        self.preview_label = QLabel("Select a folder and video to begin")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(350)
        self.preview_label.setStyleSheet("background-color: #1a1a2e; color: #666; border-radius: 8px; font-size: 14px;")
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_panel.addWidget(self.preview_label)

        # Scrubber slider
        scrubber_layout = QHBoxLayout()
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self.on_slider_moved)
        scrubber_layout.addWidget(self.frame_slider)
        right_panel.addLayout(scrubber_layout)

        # Time / frame info
        info_layout = QHBoxLayout()
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setStyleSheet("font-family: monospace; font-size: 13px;")
        info_layout.addWidget(self.time_label)
        info_layout.addStretch()
        self.frame_label = QLabel("Frame: 0 / 0")
        self.frame_label.setStyleSheet("font-family: monospace; font-size: 13px;")
        info_layout.addWidget(self.frame_label)
        right_panel.addLayout(info_layout)

        # Navigation buttons
        nav_layout = QHBoxLayout()

        self.btn_back10 = QPushButton("⏪ -10s")
        self.btn_back10.clicked.connect(lambda: self.seek_relative(-10))
        nav_layout.addWidget(self.btn_back10)

        self.btn_back1 = QPushButton("◀ -1s")
        self.btn_back1.clicked.connect(lambda: self.seek_relative(-1))
        nav_layout.addWidget(self.btn_back1)

        self.btn_prev_frame = QPushButton("‹ Prev Frame")
        self.btn_prev_frame.clicked.connect(lambda: self.seek_frames(-1))
        nav_layout.addWidget(self.btn_prev_frame)

        self.btn_next_frame = QPushButton("Next Frame ›")
        self.btn_next_frame.clicked.connect(lambda: self.seek_frames(1))
        nav_layout.addWidget(self.btn_next_frame)

        self.btn_fwd1 = QPushButton("+1s ▶")
        self.btn_fwd1.clicked.connect(lambda: self.seek_relative(1))
        nav_layout.addWidget(self.btn_fwd1)

        self.btn_fwd10 = QPushButton("+10s ⏩")
        self.btn_fwd10.clicked.connect(lambda: self.seek_relative(10))
        nav_layout.addWidget(self.btn_fwd10)

        right_panel.addLayout(nav_layout)

        # Jump to specific second
        jump_layout = QHBoxLayout()
        jump_layout.addWidget(QLabel("Jump to second:"))
        self.jump_spin = QDoubleSpinBox()
        self.jump_spin.setDecimals(2)
        self.jump_spin.setMinimum(0)
        self.jump_spin.setMaximum(99999)
        self.jump_spin.setSingleStep(0.5)
        jump_layout.addWidget(self.jump_spin)
        jump_btn = QPushButton("Go")
        jump_btn.clicked.connect(self.jump_to_second)
        jump_layout.addWidget(jump_btn)
        jump_layout.addStretch()
        right_panel.addLayout(jump_layout)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #444;")
        right_panel.addWidget(line)

        # ---- CAPTURE CONTROLS ----
        capture_layout = QHBoxLayout()

        # Single capture
        single_group = QGroupBox("Single Capture")
        single_layout = QVBoxLayout()
        self.btn_capture = QPushButton("📸  Capture This Frame")
        self.btn_capture.setMinimumHeight(45)
        self.btn_capture.setStyleSheet("background-color: #2d6a4f; color: white; font-weight: bold; font-size: 14px; border-radius: 6px;")
        self.btn_capture.clicked.connect(self.capture_single)
        single_layout.addWidget(self.btn_capture)
        single_group.setLayout(single_layout)
        capture_layout.addWidget(single_group)

        # Batch capture
        batch_group = QGroupBox("Batch Capture (All Videos in Folder)")
        batch_layout = QVBoxLayout()

        batch_time_layout = QHBoxLayout()
        batch_time_layout.addWidget(QLabel("Capture at second:"))
        self.batch_spin = QDoubleSpinBox()
        self.batch_spin.setDecimals(2)
        self.batch_spin.setMinimum(0)
        self.batch_spin.setMaximum(99999)
        self.batch_spin.setSingleStep(0.5)
        self.batch_spin.setValue(0)
        batch_time_layout.addWidget(self.batch_spin)
        self.btn_use_timecode = QPushButton("← Use Current Frame")
        self.btn_use_timecode.setToolTip("Set batch timecode to the frame currently shown in the preview")
        self.btn_use_timecode.setEnabled(False)
        self.btn_use_timecode.clicked.connect(self.use_current_timecode)
        batch_time_layout.addWidget(self.btn_use_timecode)
        batch_layout.addLayout(batch_time_layout)

        self.btn_batch = QPushButton("⚡  Batch Capture All Videos")
        self.btn_batch.setMinimumHeight(45)
        self.btn_batch.setStyleSheet("background-color: #1a5276; color: white; font-weight: bold; font-size: 14px; border-radius: 6px;")
        self.btn_batch.clicked.connect(self.capture_batch)
        batch_layout.addWidget(self.btn_batch)

        batch_group.setLayout(batch_layout)
        capture_layout.addWidget(batch_group)

        right_panel.addLayout(capture_layout)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        right_panel.addWidget(self.progress)

        # Status
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #888; padding: 4px;")
        right_panel.addWidget(self.status_label)

        # Combine panels
        right_widget = QWidget()
        right_widget.setLayout(right_panel)

        main_layout.addWidget(left_widget)
        main_layout.addWidget(right_widget, stretch=1)

    # ---- FOLDER / FILE MANAGEMENT ----

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Video Folder")
        if not folder:
            return

        self.video_folder = folder
        self.folder_label.setText(folder)

        # Create output subfolder
        self.output_folder = os.path.join(folder, "captured_frames")
        os.makedirs(self.output_folder, exist_ok=True)

        # Scan for video files
        self.video_files = []
        for f in sorted(os.listdir(folder)):
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                self.video_files.append(f)

        self.file_list.clear()
        for f in self.video_files:
            self.file_list.addItem(f)

        self.status_label.setText(f"Found {len(self.video_files)} video(s) in folder. Output → captured_frames/")

    def on_video_selected(self, row):
        if row < 0 or row >= len(self.video_files):
            return
        video_path = os.path.join(self.video_folder, self.video_files[row])
        self.load_video(video_path)

    # ---- VIDEO LOADING / DISPLAY ----

    def load_video(self, path):
        if self.current_cap:
            self.current_cap.release()

        self.current_cap = cv2.VideoCapture(path)
        if not self.current_cap.isOpened():
            self.status_label.setText(f"Error: Could not open {os.path.basename(path)}")
            return

        self.current_video_path = path
        self.total_frames = int(self.current_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.current_cap.get(cv2.CAP_PROP_FPS) or 30

        self.frame_slider.setMaximum(max(0, self.total_frames - 1))
        self.frame_slider.setValue(0)
        self.jump_spin.setMaximum(self.total_frames / self.fps)
        self.btn_use_timecode.setEnabled(True)

        self.show_frame(0)
        self.status_label.setText(f"Loaded: {os.path.basename(path)} | {self.total_frames} frames | {self.fps:.1f} fps | {self.total_frames/self.fps:.1f}s")

    def show_frame(self, frame_num):
        if not self.current_cap:
            return

        frame_num = max(0, min(frame_num, self.total_frames - 1))
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.current_cap.read()
        if not ret:
            return

        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        qt_image = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)

        # Scale to fit preview
        pixmap = QPixmap.fromImage(qt_image)
        scaled = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

        # Update labels
        current_sec = frame_num / self.fps
        total_sec = self.total_frames / self.fps
        self.time_label.setText(f"{self.format_time(current_sec)} / {self.format_time(total_sec)}")
        self.frame_label.setText(f"Frame: {frame_num} / {self.total_frames - 1}")

    def on_slider_moved(self, value):
        self.show_frame(value)

    # ---- NAVIGATION ----

    def seek_relative(self, seconds):
        if not self.current_cap:
            return
        current_frame = self.frame_slider.value()
        target = current_frame + int(seconds * self.fps)
        target = max(0, min(target, self.total_frames - 1))
        self.frame_slider.setValue(target)

    def seek_frames(self, delta):
        if not self.current_cap:
            return
        current_frame = self.frame_slider.value()
        target = max(0, min(current_frame + delta, self.total_frames - 1))
        self.frame_slider.setValue(target)

    def jump_to_second(self):
        if not self.current_cap:
            return
        sec = self.jump_spin.value()
        frame = int(sec * self.fps)
        frame = max(0, min(frame, self.total_frames - 1))
        self.frame_slider.setValue(frame)

    def use_current_timecode(self):
        if not self.current_cap:
            return
        sec = self.frame_slider.value() / self.fps
        self.batch_spin.setValue(round(sec, 2))
        self.status_label.setText(f"Batch timecode set to {sec:.2f}s")

    # ---- CAPTURE ----

    def capture_single(self):
        if not self.current_cap or not self.current_video_path:
            QMessageBox.warning(self, "No Video", "Please select a video first.")
            return

        frame_num = self.frame_slider.value()
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.current_cap.read()
        if not ret:
            QMessageBox.warning(self, "Error", "Could not read frame.")
            return

        base_name = os.path.splitext(os.path.basename(self.current_video_path))[0]
        sec = frame_num / self.fps
        out_name = f"{base_name}_frame{frame_num}_{sec:.2f}s.png"
        out_path = os.path.join(self.output_folder, out_name)

        cv2.imwrite(out_path, frame)
        self.status_label.setText(f"✅ Saved: {out_name}")

    def capture_batch(self):
        if not self.video_folder or not self.video_files:
            QMessageBox.warning(self, "No Videos", "Please select a folder with videos first.")
            return

        target_sec = self.batch_spin.value()

        reply = QMessageBox.question(
            self, "Batch Capture",
            f"Extract frame at {target_sec:.2f}s from {len(self.video_files)} video(s)?\n\nOutput → captured_frames/",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.progress.setVisible(True)
        self.progress.setMaximum(len(self.video_files))
        self.progress.setValue(0)

        success = 0
        errors = []

        for i, filename in enumerate(self.video_files):
            filepath = os.path.join(self.video_folder, filename)
            cap = cv2.VideoCapture(filepath)

            if not cap.isOpened():
                errors.append(f"{filename}: could not open")
                continue

            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            target_frame = int(target_sec * fps)

            # Clamp to valid range
            target_frame = max(0, min(target_frame, total - 1))

            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ret, frame = cap.read()
            cap.release()

            if not ret:
                errors.append(f"{filename}: could not read frame")
                continue

            base_name = os.path.splitext(filename)[0]
            out_name = f"{base_name}_{target_sec:.2f}s.png"
            out_path = os.path.join(self.output_folder, out_name)
            cv2.imwrite(out_path, frame)
            success += 1

            self.progress.setValue(i + 1)
            QApplication.processEvents()

        self.progress.setVisible(False)

        msg = f"✅ Captured {success} of {len(self.video_files)} videos at {target_sec:.2f}s"
        if errors:
            msg += f"\n\n⚠️ Errors ({len(errors)}):\n" + "\n".join(errors[:10])

        self.status_label.setText(msg.split("\n")[0])
        QMessageBox.information(self, "Batch Complete", msg)

    # ---- HELPERS ----

    @staticmethod
    def format_time(seconds):
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins:02d}:{secs:06.3f}"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-render current frame on resize
        if self.current_cap:
            self.show_frame(self.frame_slider.value())

    def closeEvent(self, event):
        if self.current_cap:
            self.current_cap.release()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark-ish theme
    from PyQt5.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(40, 40, 50))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(30, 30, 40))
    palette.setColor(QPalette.AlternateBase, QColor(45, 45, 55))
    palette.setColor(QPalette.ToolTipBase, QColor(220, 220, 220))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(55, 55, 65))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    window = VideoFrameCapture()
    window.show()
    sys.exit(app.exec_())
