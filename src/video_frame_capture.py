#!/usr/bin/env python3
"""
Shosho Video Frame Capture
==========================
Extract still frames from video files.

- Single Mode: Preview a video, scrub to the right frame, capture it.
- Batch Mode: Set one or more timecodes, extract from every video in a folder.

Keyboard shortcuts:
  Space           Play / Pause
  Left / Right    Previous / Next frame
  Shift+Left/Right  -1s / +1s
  Cmd+S           Capture current frame

Requirements:
    pip install PyQt5 opencv-python
"""

import sys
import os
import cv2
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QFrame,
    QSizePolicy, QDoubleSpinBox, QComboBox, QShortcut,
)
from PyQt5.QtGui import QImage, QPixmap, QFont, QKeySequence, QColor, QPalette
from PyQt5.QtCore import Qt, QTimer

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv',
                    '.webm', '.m4v', '.mpg', '.mpeg')

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #282832;
    color: #dcdcf0;
}
QPushButton {
    background-color: #3a3a50;
    color: #dcdcf0;
    border: 1px solid #555570;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover  { background-color: #4a4a64; }
QPushButton:pressed { background-color: #252535; }
QPushButton:disabled { color: #666680; background-color: #2e2e3e; border-color: #44445a; }
QGroupBox {
    border: 1px solid #555570;
    border-radius: 5px;
    margin-top: 8px;
    padding-top: 6px;
    color: #aaaacc;
    font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QListWidget {
    background-color: #1e1e2e;
    border: 1px solid #555570;
    border-radius: 4px;
    color: #dcdcf0;
}
QListWidget::item:selected { background-color: #2a6ada; color: white; }
QListWidget::item:hover    { background-color: #3a3a50; }
QSlider::groove:horizontal {
    height: 6px;
    background: #3a3a50;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #2a6ada;
    width: 14px; height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal { background: #2a6ada; border-radius: 3px; }
QDoubleSpinBox, QComboBox {
    background-color: #1e1e2e;
    color: #dcdcf0;
    border: 1px solid #555570;
    border-radius: 4px;
    padding: 2px 6px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #1e1e2e;
    color: #dcdcf0;
    selection-background-color: #2a6ada;
}
QProgressBar {
    background-color: #1e1e2e;
    border: 1px solid #555570;
    border-radius: 4px;
    text-align: center;
    color: #dcdcf0;
}
QProgressBar::chunk { background-color: #2a6ada; border-radius: 3px; }
QLabel { color: #dcdcf0; }
QFrame[frameShape="4"] { color: #555570; }
"""


class VideoFrameCapture(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shosho Video Frame Capture")
        self.setMinimumSize(1100, 740)
        self.setAcceptDrops(True)

        self.video_folder   = None
        self.video_files    = []
        self.video_info     = {}   # filename → {duration, width, height, fps}
        self.current_cap    = None
        self.current_video_path = None
        self.total_frames   = 0
        self.fps            = 30
        self.output_folder  = None
        self._playing       = False
        self._flash_timer   = QTimer()
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._play_timer = QTimer()
        self._play_timer.timeout.connect(self._advance_frame)

        self.init_ui()
        self._setup_shortcuts()

    # ── UI construction ───────────────────────────────────────────────────────

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ── LEFT PANEL ────────────────────────────────────────────────────────
        left_panel = QVBoxLayout()
        left_panel.setSpacing(6)

        folder_btn = QPushButton("📁  Select Video Folder")
        folder_btn.setMinimumHeight(36)
        folder_btn.clicked.connect(self.select_folder)
        left_panel.addWidget(folder_btn)

        self.output_btn = QPushButton("💾  Output Folder")
        self.output_btn.setMinimumHeight(36)
        self.output_btn.setToolTip("Choose where captured frames are saved")
        self.output_btn.clicked.connect(self.choose_output_folder)
        left_panel.addWidget(self.output_btn)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: #8888aa; padding: 2px 4px; font-size: 11px;")
        left_panel.addWidget(self.folder_label)

        self.output_label = QLabel("Output: same folder / captured_frames")
        self.output_label.setWordWrap(True)
        self.output_label.setStyleSheet("color: #6666aa; padding: 2px 4px; font-size: 10px;")
        left_panel.addWidget(self.output_label)

        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self.on_video_selected)
        left_panel.addWidget(self.file_list)

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(300)

        # ── RIGHT PANEL ───────────────────────────────────────────────────────
        right_panel = QVBoxLayout()
        right_panel.setSpacing(5)

        # Preview
        self.preview_label = QLabel("Select a folder and video to begin\n\nYou can also drag and drop a folder or video file here")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(320)
        self.preview_label.setStyleSheet(
            "background-color: #1a1a2e; color: #555570; border-radius: 8px; "
            "font-size: 13px; border: 2px solid #2a2a40;")
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_panel.addWidget(self.preview_label)

        # Video info bar
        self.info_bar = QLabel("")
        self.info_bar.setStyleSheet("color: #8888aa; font-size: 10px; padding: 1px 2px;")
        right_panel.addWidget(self.info_bar)

        # Scrubber
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self.on_slider_moved)
        right_panel.addWidget(self.frame_slider)

        # Timecode row
        tc_row = QHBoxLayout()
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setStyleSheet("font-family: monospace; font-size: 12px;")
        tc_row.addWidget(self.time_label)
        tc_row.addStretch()
        self.btn_play = QPushButton("▶  Play")
        self.btn_play.setFixedWidth(90)
        self.btn_play.setEnabled(False)
        self.btn_play.setToolTip("Play / Pause  (Space)")
        self.btn_play.clicked.connect(self.toggle_play)
        tc_row.addWidget(self.btn_play)
        self.frame_label = QLabel("Frame: 0 / 0")
        self.frame_label.setStyleSheet("font-family: monospace; font-size: 12px;")
        tc_row.addWidget(self.frame_label)
        right_panel.addLayout(tc_row)

        # Nav buttons
        nav = QHBoxLayout()
        for label, fn in [
            ("⏪ -10s", lambda: self.seek_relative(-10)),
            ("◀ -1s",  lambda: self.seek_relative(-1)),
            ("‹ Prev",  lambda: self.seek_frames(-1)),
            ("Next ›",  lambda: self.seek_frames(1)),
            ("+1s ▶",  lambda: self.seek_relative(1)),
            ("+10s ⏩", lambda: self.seek_relative(10)),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            nav.addWidget(b)
        right_panel.addLayout(nav)

        # Jump row
        jump_row = QHBoxLayout()
        jump_row.addWidget(QLabel("Jump to second:"))
        self.jump_spin = QDoubleSpinBox()
        self.jump_spin.setDecimals(2)
        self.jump_spin.setMinimum(0)
        self.jump_spin.setMaximum(99999)
        self.jump_spin.setSingleStep(0.5)
        self.jump_spin.setFixedWidth(80)
        jump_row.addWidget(self.jump_spin)
        jump_btn = QPushButton("Go")
        jump_btn.setFixedWidth(44)
        jump_btn.clicked.connect(self.jump_to_second)
        jump_row.addWidget(jump_btn)
        jump_row.addStretch()
        right_panel.addLayout(jump_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        right_panel.addWidget(sep)

        # ── OUTPUT FORMAT ROW ─────────────────────────────────────────────────
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["PNG", "JPEG", "TIFF"])
        self.fmt_combo.setFixedWidth(80)
        self.fmt_combo.currentTextChanged.connect(self._on_format_changed)
        fmt_row.addWidget(self.fmt_combo)
        self.quality_label = QLabel("Quality:")
        self.quality_label.setVisible(False)
        fmt_row.addWidget(self.quality_label)
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setMinimum(1)
        self.quality_slider.setMaximum(100)
        self.quality_slider.setValue(92)
        self.quality_slider.setFixedWidth(120)
        self.quality_slider.setVisible(False)
        self.quality_slider.valueChanged.connect(
            lambda v: self.quality_val_label.setText(str(v)))
        fmt_row.addWidget(self.quality_slider)
        self.quality_val_label = QLabel("92")
        self.quality_val_label.setFixedWidth(28)
        self.quality_val_label.setVisible(False)
        fmt_row.addWidget(self.quality_val_label)
        fmt_row.addStretch()
        right_panel.addLayout(fmt_row)

        # ── CAPTURE CONTROLS ──────────────────────────────────────────────────
        capture_row = QHBoxLayout()

        # Single capture
        single_grp = QGroupBox("Single Capture")
        single_lay = QVBoxLayout()
        self.btn_capture = QPushButton("📸  Capture This Frame")
        self.btn_capture.setMinimumHeight(42)
        self.btn_capture.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; "
            "font-size: 13px; border-radius: 5px; border: none;")
        self.btn_capture.setToolTip("Capture current frame  (Cmd+S)")
        self.btn_capture.clicked.connect(self.capture_single)
        single_lay.addWidget(self.btn_capture)
        single_grp.setLayout(single_lay)
        capture_row.addWidget(single_grp)

        # Batch capture
        batch_grp = QGroupBox("Batch Capture (All Videos in Folder)")
        batch_lay = QVBoxLayout()
        batch_lay.setSpacing(4)

        # Timecode list
        tc_list_row = QHBoxLayout()
        self.timecode_list = QListWidget()
        self.timecode_list.setMaximumHeight(70)
        self.timecode_list.setToolTip("Timecodes to extract from each video")
        tc_list_row.addWidget(self.timecode_list)

        tc_btn_col = QVBoxLayout()
        self.batch_spin = QDoubleSpinBox()
        self.batch_spin.setDecimals(2)
        self.batch_spin.setMinimum(0)
        self.batch_spin.setMaximum(99999)
        self.batch_spin.setSingleStep(0.5)
        self.batch_spin.setFixedWidth(80)
        tc_btn_col.addWidget(self.batch_spin)
        add_tc_btn = QPushButton("+ Add")
        add_tc_btn.setFixedWidth(64)
        add_tc_btn.clicked.connect(self._add_timecode)
        tc_btn_col.addWidget(add_tc_btn)
        del_tc_btn = QPushButton("Remove")
        del_tc_btn.setFixedWidth(64)
        del_tc_btn.clicked.connect(self._remove_timecode)
        tc_btn_col.addWidget(del_tc_btn)
        tc_list_row.addLayout(tc_btn_col)
        batch_lay.addLayout(tc_list_row)

        use_tc_btn = QPushButton("← Use Current Frame")
        use_tc_btn.setToolTip("Set timecode input to current scrubber position")
        use_tc_btn.clicked.connect(self.use_current_timecode)
        self.btn_use_timecode = use_tc_btn
        batch_lay.addWidget(use_tc_btn)

        self.btn_batch = QPushButton("⚡  Batch Capture All Videos")
        self.btn_batch.setMinimumHeight(42)
        self.btn_batch.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "font-size: 13px; border-radius: 5px; border: none;")
        self.btn_batch.clicked.connect(self.capture_batch)
        batch_lay.addWidget(self.btn_batch)
        batch_grp.setLayout(batch_lay)
        capture_row.addWidget(batch_grp)

        right_panel.addLayout(capture_row)

        # Progress + status
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        right_panel.addWidget(self.progress)

        self.status_label = QLabel("Ready  ·  Drag a video folder here to begin")
        self.status_label.setStyleSheet("color: #8888aa; padding: 2px 4px; font-size: 11px;")
        right_panel.addWidget(self.status_label)

        right_widget = QWidget()
        right_widget.setLayout(right_panel)

        main_layout.addWidget(left_widget)
        main_layout.addWidget(right_widget, stretch=1)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+S"), self, self.capture_single)
        QShortcut(QKeySequence(Qt.Key_Left),  self, lambda: self.seek_frames(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.seek_frames(1))
        QShortcut(QKeySequence("Shift+Left"),  self, lambda: self.seek_relative(-1))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek_relative(1))

    # ── Folder / file management ──────────────────────────────────────────────

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Video Folder")
        if folder:
            self._load_folder(folder)

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if folder:
            self.output_folder = folder
            self.output_label.setText(f"Output: {folder}")

    def _load_folder(self, folder):
        self.video_folder = folder
        self.folder_label.setText(folder)

        if not self.output_folder:
            self.output_folder = os.path.join(folder, "captured_frames")
            self.output_label.setText(f"Output: {self.output_folder}")
        os.makedirs(self.output_folder, exist_ok=True)

        self.video_files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS)

        self.video_info = {}
        self.file_list.clear()
        for f in self.video_files:
            info = self._probe_video(os.path.join(folder, f))
            self.video_info[f] = info
            dur  = f"{info['duration']:.1f}s" if info['duration'] else "?"
            res  = f"{info['width']}×{info['height']}" if info['width'] else ""
            item = QListWidgetItem(f"{f}\n  {res}  {dur}  {info['fps']:.0f}fps" if res else f)
            item.setToolTip(f"{f}\n{res}  {dur}  {info['fps']:.1f}fps")
            self.file_list.addItem(item)

        self.status_label.setText(
            f"{len(self.video_files)} video(s) found  ·  output → {self.output_folder}")

    def _probe_video(self, path):
        cap = cv2.VideoCapture(path)
        w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        fc  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = fc / fps if fps else 0
        cap.release()
        return {"width": w, "height": h, "fps": fps, "duration": dur, "frames": fc}

    def on_video_selected(self, row):
        if row < 0 or row >= len(self.video_files):
            return
        self.load_video(os.path.join(self.video_folder, self.video_files[row]))

    # ── Video loading / display ───────────────────────────────────────────────

    def load_video(self, path):
        self._stop_playback()
        if self.current_cap:
            self.current_cap.release()

        self.current_cap = cv2.VideoCapture(path)
        if not self.current_cap.isOpened():
            self.status_label.setText(f"Error: could not open {os.path.basename(path)}")
            return

        self.current_video_path = path
        self.total_frames = int(self.current_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.current_cap.get(cv2.CAP_PROP_FPS) or 30
        w = int(self.current_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.current_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self.frame_slider.setMaximum(max(0, self.total_frames - 1))
        self.frame_slider.setValue(0)
        self.jump_spin.setMaximum(self.total_frames / self.fps)
        self.btn_play.setEnabled(True)

        dur = self.total_frames / self.fps
        self.info_bar.setText(
            f"{os.path.basename(path)}  ·  {w}×{h}  ·  "
            f"{self.fps:.2f} fps  ·  {self.total_frames} frames  ·  {dur:.2f}s")

        self.show_frame(0)
        self.status_label.setText(f"Loaded: {os.path.basename(path)}")

    def show_frame(self, frame_num):
        if not self.current_cap:
            return
        frame_num = max(0, min(frame_num, self.total_frames - 1))
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.current_cap.read()
        if not ret:
            return

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qt_img  = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap  = QPixmap.fromImage(qt_img)
        scaled  = pixmap.scaled(self.preview_label.size(),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

        cur_sec   = frame_num / self.fps
        total_sec = self.total_frames / self.fps
        self.time_label.setText(
            f"{self.fmt_time(cur_sec)} / {self.fmt_time(total_sec)}")
        self.frame_label.setText(f"Frame: {frame_num} / {self.total_frames - 1}")

    def on_slider_moved(self, value):
        self.show_frame(value)

    # ── Navigation ────────────────────────────────────────────────────────────

    def seek_relative(self, seconds):
        if not self.current_cap:
            return
        target = self.frame_slider.value() + int(seconds * self.fps)
        self.frame_slider.setValue(max(0, min(target, self.total_frames - 1)))

    def seek_frames(self, delta):
        if not self.current_cap:
            return
        target = self.frame_slider.value() + delta
        self.frame_slider.setValue(max(0, min(target, self.total_frames - 1)))

    def jump_to_second(self):
        if not self.current_cap:
            return
        frame = int(self.jump_spin.value() * self.fps)
        self.frame_slider.setValue(max(0, min(frame, self.total_frames - 1)))

    def use_current_timecode(self):
        if not self.current_cap:
            return
        sec = self.frame_slider.value() / self.fps
        self.batch_spin.setValue(round(sec, 2))
        self.status_label.setText(f"Batch timecode set to {sec:.2f}s")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
        else:
            super().keyPressEvent(event)

    # ── Playback ─────────────────────────────────────────────────────────────

    def toggle_play(self):
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if not self.current_cap:
            return
        self._playing = True
        self.btn_play.setText("⏸  Pause")
        self._play_timer.start(max(1, int(1000 / self.fps)))

    def _stop_playback(self):
        self._playing = False
        self._play_timer.stop()
        self.btn_play.setText("▶  Play")

    def _advance_frame(self):
        cur = self.frame_slider.value()
        if cur >= self.total_frames - 1:
            self._stop_playback()
            return
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(cur + 1)
        self.frame_slider.blockSignals(False)
        self.show_frame(cur + 1)

    # ── Output format ─────────────────────────────────────────────────────────

    def _on_format_changed(self, fmt):
        is_jpeg = fmt == "JPEG"
        self.quality_label.setVisible(is_jpeg)
        self.quality_slider.setVisible(is_jpeg)
        self.quality_val_label.setVisible(is_jpeg)

    def _save_frame(self, frame, out_path_no_ext):
        fmt = self.fmt_combo.currentText()
        if fmt == "PNG":
            path = out_path_no_ext + ".png"
            cv2.imwrite(path, frame)
        elif fmt == "JPEG":
            quality = self.quality_slider.value()
            path = out_path_no_ext + ".jpg"
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        elif fmt == "TIFF":
            path = out_path_no_ext + ".tif"
            cv2.imwrite(path, frame)
        return path

    # ── Timecode list (batch) ─────────────────────────────────────────────────

    def _add_timecode(self):
        sec = self.batch_spin.value()
        # Avoid duplicates
        existing = [self.timecode_list.item(i).data(Qt.UserRole)
                    for i in range(self.timecode_list.count())]
        if sec not in existing:
            item = QListWidgetItem(f"{sec:.2f}s")
            item.setData(Qt.UserRole, sec)
            self.timecode_list.addItem(item)
            self.timecode_list.sortItems()

    def _remove_timecode(self):
        row = self.timecode_list.currentRow()
        if row >= 0:
            self.timecode_list.takeItem(row)

    def _get_timecodes(self):
        if self.timecode_list.count() == 0:
            return [self.batch_spin.value()]
        return [self.timecode_list.item(i).data(Qt.UserRole)
                for i in range(self.timecode_list.count())]

    # ── Capture ───────────────────────────────────────────────────────────────

    def capture_single(self):
        if not self.current_cap or not self.current_video_path:
            return
        frame_num = self.frame_slider.value()
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.current_cap.read()
        if not ret:
            self.status_label.setText("Error: could not read frame")
            return

        os.makedirs(self.output_folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.current_video_path))[0]
        sec  = frame_num / self.fps
        base = os.path.join(self.output_folder, f"{stem}_frame{frame_num}_{sec:.2f}s")
        path = self._save_frame(frame, base)

        self.status_label.setText(f"✅  Saved: {os.path.basename(path)}")
        self._flash_capture()

    def _flash_capture(self):
        self.preview_label.setStyleSheet(
            "background-color: #1a1a2e; color: #666; border-radius: 8px; "
            "border: 2px solid #2ecc71;")
        self._flash_timer.start(500)

    def _clear_flash(self):
        self.preview_label.setStyleSheet(
            "background-color: #1a1a2e; color: #555570; border-radius: 8px; "
            "border: 2px solid #2a2a40;")

    def capture_batch(self):
        if not self.video_folder or not self.video_files:
            QMessageBox.warning(self, "No Videos",
                                "Please select a folder with videos first.")
            return

        timecodes = self._get_timecodes()
        tc_str = ", ".join(f"{t:.2f}s" for t in timecodes)
        reply = QMessageBox.question(
            self, "Batch Capture",
            f"Extract {len(timecodes)} timecode(s) ({tc_str})\n"
            f"from {len(self.video_files)} video(s)?\n\n"
            f"Output → {self.output_folder}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        os.makedirs(self.output_folder, exist_ok=True)
        total_ops = len(self.video_files) * len(timecodes)
        self.progress.setVisible(True)
        self.progress.setMaximum(total_ops)
        self.progress.setValue(0)

        success, errors, op = 0, [], 0
        for filename in self.video_files:
            filepath = os.path.join(self.video_folder, filename)
            cap = cv2.VideoCapture(filepath)
            if not cap.isOpened():
                errors.append(f"{filename}: could not open")
                op += len(timecodes)
                self.progress.setValue(op)
                continue

            fps   = cap.get(cv2.CAP_PROP_FPS) or 30
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            stem  = os.path.splitext(filename)[0]

            for tc in timecodes:
                tgt = max(0, min(int(tc * fps), total - 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, tgt)
                ret, frame = cap.read()
                op += 1
                if not ret:
                    errors.append(f"{filename} @ {tc:.2f}s: could not read frame")
                else:
                    base = os.path.join(self.output_folder, f"{stem}_{tc:.2f}s")
                    self._save_frame(frame, base)
                    success += 1
                self.progress.setValue(op)
                QApplication.processEvents()

            cap.release()

        self.progress.setVisible(False)
        msg = (f"✅  Captured {success} of {total_ops} "
               f"({len(self.video_files)} videos × {len(timecodes)} timecode(s))")
        if errors:
            msg += f"\n\n⚠️ Errors ({len(errors)}):\n" + "\n".join(errors[:10])
        self.status_label.setText(msg.split("\n")[0])
        QMessageBox.information(self, "Batch Complete", msg)

    # ── Drag and drop ─────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if os.path.isdir(path):
            self._load_folder(path)
        elif os.path.isfile(path) and \
                os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS:
            folder = os.path.dirname(path)
            self._load_folder(folder)
            # Select the dropped file
            name = os.path.basename(path)
            for i in range(self.file_list.count()):
                if self.file_list.item(i).text().startswith(name):
                    self.file_list.setCurrentRow(i)
                    break

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def fmt_time(seconds):
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m:02d}:{s:06.3f}"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_cap:
            self.show_frame(self.frame_slider.value())

    def closeEvent(self, event):
        self._stop_playback()
        if self.current_cap:
            self.current_cap.release()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = VideoFrameCapture()
    window.show()
    sys.exit(app.exec_())
