#!/usr/bin/env python3
"""
Video Frame Capture
===================
Extract still frames from video files.

- Single Mode: Preview a video, scrub to the right frame, capture it.
- Batch Mode: Set one or more timecodes, extract from every video in a folder.
- Contact Sheet: Batch timecode frames, or a folder of stills, on one PDF.

Keyboard shortcuts:
  Space           Play / Pause
  Left / Right    Previous / Next frame
  Shift+Left/Right  -1s / +1s
  Cmd+S           Capture current frame

Requirements:
    pip install PyQt5 opencv-python
"""

import math
import sys
import os
import re
import shutil
import subprocess
import tempfile
import time
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QFrame,
    QSizePolicy, QDoubleSpinBox, QComboBox, QShortcut, QSpinBox, QDialog,
    QTabWidget, QLineEdit, QCheckBox, QPlainTextEdit,
)
from PyQt5.QtGui import (
    QImage, QPixmap, QFont, QKeySequence, QColor, QPalette,
    QPainter, QPdfWriter, QPageSize, QPen, QFontMetrics,
)
from PyQt5.QtCore import Qt, QTimer, QSizeF, QRectF, QMarginsF, QProcess, QUrl, pyqtSignal
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from video_downloader import DownloadDialog
from video_edit_tools import (
    ANNOTATION_COLORS,
    AnnotatedPreviewLabel,
    CaptionCue,
    TimelineMapBar,
    WhisperTranscribeWorker,
    build_preview_audio,
    export_audio_clip,
    export_mp4_with_chapters,
    export_video_or_gif,
    list_avfoundation_microphones,
    parse_srt,
    render_annotations_on_bgr,
    render_annotations_on_qimage,
    split_video_by_cuts,
    write_captions_csv,
    write_captions_txt,
    write_srt,
)

APP_NAME = "Video Frame Capture"


def _build_label():
    """Identify which build is running: baked-in stamp when frozen, live git state from source."""
    try:
        from _build_stamp import BUILD_LABEL
        return BUILD_LABEL
    except ImportError:
        pass
    if getattr(sys, 'frozen', False):
        return "packaged build, unstamped"
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        commit = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'], cwd=here,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ['git', 'status', '--porcelain'], cwd=here,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip())
        if commit:
            return f"source {commit}{'+dirty' if dirty else ''}"
    except Exception:
        pass
    return "source, unknown commit"


def _short_build_label(full):
    """Trim the commit hash / +dirty tag off BUILD_LABEL for display in the title bar —
    keeps the human-readable date/time so builds are still distinguishable at a glance,
    without the cryptic hash. Full BUILD_LABEL stays available for diagnostics."""
    return re.sub(r"\s*(?:·\s*)?[0-9a-f]{6,12}(?:\+dirty)?\s*$", "", full).rstrip()


BUILD_LABEL = _build_label()
BUILD_LABEL_SHORT = _short_build_label(BUILD_LABEL)

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv',
                    '.webm', '.m4v', '.mpg', '.mpeg')
CONTACT_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff'}
AUDIO_EXTENSIONS = ('.wav', '.mp3', '.m4a', '.aac', '.flac')
# Width, height in PDF points (1/72 inch), portrait.
PAPER_SIZES_PT = {
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
    "Tabloid": (792.0, 1224.0),
    "A4": (595.28, 841.89),
    "A3": (841.89, 1190.55),
}


def page_points(name, landscape):
    """Return (width, height) in points for a named paper size."""
    width, height = PAPER_SIZES_PT[name]
    if landscape:
        return height, width
    return width, height


def contact_grid(per_page, page_w, page_h, target_aspect=1.0):
    """Choose columns and rows for `per_page` cells on a page of this size.

    The grid is scored against `target_aspect` (the pictures' own width /
    height), so cells come out shaped like the pictures instead of always
    square — square cells leave blank space above and below a wide video
    frame. A perfect rectangle (no leftover empty cells) is still preferred.
    """
    per_page = max(1, int(per_page))
    target_aspect = target_aspect if target_aspect and target_aspect > 0 else 1.0
    best = None
    for cols in range(1, per_page + 1):
        rows = math.ceil(per_page / cols)
        aspect = (page_w / cols) / (page_h / rows)
        waste = cols * rows - per_page
        score = (abs(aspect - target_aspect) + waste * 0.15, cols)
        if best is None or score < best[0]:
            best = (score, cols, rows)
    return best[1], best[2]


def _natural_sort_key(name):
    """Split a file name into text/number chunks so '12' sorts before '102'."""
    return [
        int(chunk) if chunk.isdigit() else chunk.casefold()
        for chunk in re.split(r"(\d+)", name)
    ]


def list_contact_images(folder):
    """Image files in one folder (or its *_frames subfolders), sorted by file name (natural order)."""
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    sub_found = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in CONTACT_IMAGE_EXTENSIONS:
            found.append(path)
        elif os.path.isdir(path) and name.endswith("_frames"):
            for sub_name in os.listdir(path):
                if os.path.splitext(sub_name)[1].lower() in CONTACT_IMAGE_EXTENSIONS:
                    sub_path = os.path.join(path, sub_name)
                    if os.path.isfile(sub_path):
                        sub_found.append(sub_path)
    result = found if found else sub_found
    result.sort(key=lambda path: _natural_sort_key(os.path.basename(path)))
    return result


def contact_page_slices(paths, per_page):
    per_page = max(1, int(per_page))
    return [paths[i:i + per_page] for i in range(0, len(paths), per_page)]


# Print resolution for the preview and the PDF. 72 dpi made a full-page cell
# only a few hundred pixels wide, so the pictures looked soft.
CONTACT_SHEET_DPI = 300


def contact_seek_frame(seconds, fps, frame_count):
    """Frame index for a batch timecode.

    Returns -1 when the time is past a known end, so a short movie is skipped
    instead of showing its last frame. Returns None when the duration is
    unknown and the caller should seek by time.
    """
    if not fps or fps <= 0 or not frame_count or frame_count <= 0:
        return None
    last = int(frame_count) - 1
    index = int(float(seconds) * float(fps))
    if index > last:
        return -1
    return max(0, index)


class ContactFrame:
    """One cell: a picture plus the file name and folder printed under it."""

    def __init__(self, name, directory, path=None, image=None):
        self.name = name
        self.directory = directory
        self.path = path
        self.image = image

    def picture(self):
        if self.image is not None and not self.image.isNull():
            return self.image
        if self.path:
            return QImage(self.path)
        return QImage()


def page_device_pixels(page_w, page_h, dpi=CONTACT_SHEET_DPI):
    """Page size in pixels at `dpi`. `page_w` and `page_h` are in points."""
    scale = dpi / 72.0
    return page_w * scale, page_h * scale


def _set_image_dpi(image, dpi):
    dots_per_meter = int(round(dpi / 0.0254))
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)


def numpy_frame_to_qimage(frame, max_edge=None):
    """BGR or BGRA frame from OpenCV. Kept full size unless `max_edge` is set."""
    if frame is None or getattr(frame, "size", 0) == 0:
        return QImage()
    if frame.ndim == 2:
        rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    elif frame.shape[2] == 4:
        blue, green, red, alpha = cv2.split(frame)
        cover = alpha.astype(np.float32) / 255.0

        def blend(channel):
            mixed = channel.astype(np.float32) * cover + 255.0 * (1.0 - cover)
            return mixed.clip(0, 255).astype(np.uint8)

        rgb = cv2.merge([blend(red), blend(green), blend(blue)])
    else:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    longest = max(height, width)
    if max_edge and longest > max_edge:
        scale = max_edge / float(longest)
        rgb = cv2.resize(
            rgb,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    rgb = np.ascontiguousarray(rgb)
    height, width = rgb.shape[:2]
    image = QImage(rgb.data, width, height, int(rgb.strides[0]), QImage.Format_RGB888)
    return image.copy()


def load_contact_still(path, max_edge=None):
    """Remember one saved picture. The original file is read when the page is drawn."""
    del max_edge
    return ContactFrame(os.path.basename(path), os.path.dirname(path), path=path)


def _contact_picture(item):
    """Return (image, file name, directory) for a path or a ContactFrame."""
    if isinstance(item, ContactFrame):
        return item.picture(), item.name, item.directory
    return QImage(item), os.path.basename(item), os.path.dirname(item)


def _contact_extra(skipped, errors):
    parts = []
    if skipped:
        parts.append(f"{len(skipped)} skipped, shorter than the batch time")
    if errors:
        parts.append(f"{len(errors)} could not be read")
    return " · ".join(parts)


def _contact_detail(skipped, errors):
    lines = list(skipped) + list(errors)
    if not lines:
        return ""
    shown = "\n".join(lines[:8])
    if len(lines) > 8:
        shown += f"\n… and {len(lines) - 8} more"
    return shown


def _empty_frame_folder_message(folder):
    message = "That folder has no PNG, JPEG, or TIFF files."
    try:
        names = os.listdir(folder)
    except OSError:
        return message
    if any(os.path.isdir(os.path.join(folder, name)) for name in names):
        message += (
            "\n\nPictures inside subfolders are not included. "
            "Open the folder that holds the files."
        )
    return message


def _contain_size(src_w, src_h, box_w, box_h):
    """Size of `src` fitted inside `box`, keeping the aspect ratio."""
    if src_w <= 0 or src_h <= 0 or box_w <= 0 or box_h <= 0:
        return max(1.0, box_w), max(1.0, box_h)
    scale = min(box_w / float(src_w), box_h / float(src_h))
    return src_w * scale, src_h * scale


def _elide(font, text, width, painter=None):
    device = painter.device() if painter is not None else None
    metrics = QFontMetrics(font, device) if device is not None else QFontMetrics(font)
    return metrics.elidedText(text, Qt.ElideMiddle, max(1, int(width)))


def paint_contact_page(painter, page_w, page_h, paths, per_page, page_label="", dpi=CONTACT_SHEET_DPI):
    """Draw one contact page. `per_page` sets the grid even when this page is short.

    `page_w` and `page_h` are device pixels. `dpi` turns the point layout
    (margins, type) into that pixel grid so the pictures stay sharp.
    """
    scale = dpi / 72.0
    painter.fillRect(QRectF(0, 0, page_w, page_h), QColor("white"))
    margin = 24.0 * scale
    footer = 16.0 * scale
    gap = 8.0 * scale
    inner_w = page_w - margin * 2
    inner_h = page_h - margin - (margin + footer)
    sample_image = _contact_picture(paths[0])[0] if paths else QImage()
    target_aspect = (
        sample_image.width() / sample_image.height()
        if not sample_image.isNull() and sample_image.height() > 0
        else 1.0
    )
    cols, rows = contact_grid(per_page, inner_w, inner_h, target_aspect)
    cell_w = (inner_w - gap * (cols - 1)) / cols
    cell_h = (inner_h - gap * (rows - 1)) / rows
    caption_h = min(30.0 * scale, max(14.0 * scale, cell_h * 0.30))
    name_font = QFont()
    name_font.setPointSizeF(max(5.0, min(8.5, (cell_w / scale) / 26)))
    name_font.setBold(True)
    dir_font = QFont()
    dir_font.setPointSizeF(max(4.5, name_font.pointSizeF() - 1.0))

    for index, path in enumerate(paths):
        col = index % cols
        row = index // cols
        x = margin + col * (cell_w + gap)
        y = margin + row * (cell_h + gap)
        image_rect = QRectF(x, y, cell_w, max(1.0, cell_h - caption_h))
        image, name, directory = _contact_picture(path)
        painter.setPen(QPen(QColor("#d0d0d0"), max(1.0, scale)))
        if image.isNull():
            painter.drawRect(image_rect)
            painter.setPen(QColor("#888"))
            painter.drawText(image_rect, Qt.AlignCenter, "Unreadable")
        else:
            # Draw the original into the cell. QPdfWriter stores that image
            # and scales it on the page, so the PDF keeps the source pixels
            # instead of a copy shrunk to the cell.
            fitted_w, fitted_h = _contain_size(
                image.width(), image.height(), image_rect.width(), image_rect.height())
            dx = image_rect.x() + (image_rect.width() - fitted_w) / 2.0
            dy = image_rect.y() + (image_rect.height() - fitted_h) / 2.0
            fitted_rect = QRectF(dx, dy, fitted_w, fitted_h)
            # The border hugs the picture itself, not the full cell — a wide
            # video frame in a near-square cell otherwise sits inside a box
            # with a visibly empty white band above and below it.
            painter.drawRect(fitted_rect)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(fitted_rect, image)
        name_rect = QRectF(x, y + cell_h - caption_h + 1, cell_w, caption_h / 2)
        dir_rect = QRectF(x, name_rect.bottom(), cell_w, caption_h / 2)
        painter.setFont(name_font)
        painter.setPen(QColor("#222"))
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, _elide(name_font, name, cell_w, painter))
        painter.setFont(dir_font)
        painter.setPen(QColor("#555"))
        painter.drawText(dir_rect, Qt.AlignLeft | Qt.AlignVCenter, _elide(dir_font, directory, cell_w, painter))

    if page_label:
        painter.setFont(dir_font)
        painter.setPen(QColor("#666"))
        painter.drawText(
            QRectF(margin, page_h - margin - footer + 2, inner_w, footer),
            Qt.AlignRight | Qt.AlignVCenter,
            page_label,
        )


def write_contact_pdf(path, images, per_page, paper, landscape):
    """Write every contact page into one PDF. Returns the number of pages."""
    page_w, page_h = page_points(paper, landscape)
    px_w, px_h = page_device_pixels(page_w, page_h)
    pages = contact_page_slices(images, per_page)
    writer = QPdfWriter(path)
    writer.setResolution(CONTACT_SHEET_DPI)
    writer.setPageSize(QPageSize(QSizeF(page_w, page_h), QPageSize.Point))
    try:
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageSize.Point)
    except TypeError:
        writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"Could not create {path}")
    try:
        for index, group in enumerate(pages):
            if index:
                writer.newPage()
            paint_contact_page(
                painter, px_w, px_h, group, per_page,
                f"{index + 1} / {len(pages)}",
            )
    finally:
        painter.end()
    return len(pages)


def reveal_in_finder(parent, path):
    """Select a file or folder in Finder."""
    if not path:
        return
    try:
        subprocess.run(["/usr/bin/open", "-R", path], check=True,
                       capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        QMessageBox.warning(parent, APP_NAME, f"Could not show this in Finder.\n\n{error}")


class ContactSheetDialog(QDialog):
    """Preview captured frames as a contact sheet and print them to one PDF."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Contact Sheet")
        self.folder = None
        self.images = []
        self.page_index = 0
        self.source = "batch"
        self._extra = ""
        self._busy = False

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Per page:"))
        self.per_page = QSpinBox()
        self.per_page.setRange(1, 48)
        self.per_page.setValue(6)
        self.per_page.setToolTip("How many frames to place on each page")
        self.per_page.valueChanged.connect(self._settings_changed)
        controls.addWidget(self.per_page)
        controls.addWidget(QLabel("Paper:"))
        self.paper = QComboBox()
        self.paper.addItems(list(PAPER_SIZES_PT))
        self.paper.currentTextChanged.connect(self._settings_changed)
        controls.addWidget(self.paper)
        self.orientation = QComboBox()
        self.orientation.addItems(["Landscape", "Portrait"])
        self.orientation.currentTextChanged.connect(self._settings_changed)
        controls.addWidget(self.orientation)
        controls.addStretch()
        self.prev_page = QPushButton("‹")
        self.prev_page.setFixedWidth(36)
        self.prev_page.clicked.connect(lambda: self._turn(-1))
        controls.addWidget(self.prev_page)
        self.page_label = QLabel("Page 0 of 0")
        controls.addWidget(self.page_label)
        self.next_page = QPushButton("›")
        self.next_page.setFixedWidth(36)
        self.next_page.clicked.connect(lambda: self._turn(1))
        controls.addWidget(self.next_page)
        layout.addLayout(controls)

        source_row = QHBoxLayout()
        self.this_video_btn = QPushButton("This Video")
        self.this_video_btn.setToolTip("Grab the batch timecode from the highlighted video")
        self.this_video_btn.clicked.connect(self.use_this_video)
        source_row.addWidget(self.this_video_btn)
        self.all_videos_btn = QPushButton("All Videos")
        self.all_videos_btn.setToolTip("Grab the batch timecode from every video in the folder")
        self.all_videos_btn.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        self.all_videos_btn.clicked.connect(self.use_all_videos)
        source_row.addWidget(self.all_videos_btn)
        source_row.addStretch()
        self.folder_btn = QPushButton("Frame Folder…")
        self.folder_btn.setToolTip("Open a folder of pictures that are already saved")
        self.folder_btn.clicked.connect(self.choose_frame_folder)
        source_row.addWidget(self.folder_btn)
        layout.addLayout(source_row)

        self.summary = QLabel("No frames yet")
        self.summary.setStyleSheet("color: #8888aa; font-size: 11px;")
        layout.addWidget(self.summary)

        self.preview = QLabel("Choose This Video, All Videos, or a folder of frames.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(420)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet(
            "background: #3a3a46; color: #999; border: 1px solid #555570; border-radius: 4px;")
        layout.addWidget(self.preview, stretch=1)

        actions = QHBoxLayout()
        self.print_btn = QPushButton("Print to PDF")
        self.print_btn.setMinimumHeight(36)
        self.print_btn.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        self.print_btn.clicked.connect(self.print_pdf)
        actions.addWidget(self.print_btn)
        actions.addStretch()
        layout.addLayout(actions)
        self._page_image = None
        self.resize(880, 700)

    def set_folder(self, folder):
        self.source = "folder"
        self.folder = folder
        self.page_index = 0
        self._extra = ""
        paths = list_contact_images(folder)
        frames = []
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            if total:
                self.summary.setText(f"Loading {index} of {total}")
                QApplication.processEvents()
            frames.append(load_contact_still(path))
        self.images = frames
        self._render()

    def reload(self):
        if self.source == "folder" and self.folder:
            self.set_folder(self.folder)

    def prepare_batch(self, prompt):
        self.source = "batch"
        self.folder = None
        self.images = []
        self.page_index = 0
        self._extra = prompt
        self._render()

    def set_frames(self, frames, extra=""):
        self.source = "batch"
        self.images = list(frames)
        self.page_index = 0
        self._extra = extra
        self._render()

    def set_busy(self, busy):
        self._busy = busy
        for button in (self.this_video_btn, self.all_videos_btn, self.folder_btn):
            button.setEnabled(not busy)
        self.print_btn.setEnabled((not busy) and bool(self.images))

    def use_this_video(self):
        parent = self.parent()
        if parent is not None:
            parent.fill_contact_sheet(selected_only=True)

    def use_all_videos(self):
        parent = self.parent()
        if parent is not None:
            parent.fill_contact_sheet(selected_only=False)

    def choose_frame_folder(self):
        parent = self.parent()
        start = self.folder or ""
        if parent is not None:
            start = parent.video_folder or parent.output_folder or start
        folder = QFileDialog.getExistingDirectory(self, "Choose Folder of Frames", start)
        if not folder:
            return
        self.set_folder(folder)
        if not self.images:
            QMessageBox.information(self, "Contact Sheet", _empty_frame_folder_message(folder))

    def _settings_changed(self, _value=None):
        self.page_index = 0
        self._render()

    def _turn(self, step):
        pages = contact_page_slices(self.images, self.per_page.value())
        if not pages:
            return
        self.page_index = min(max(0, self.page_index + step), len(pages) - 1)
        self._render()

    def _page_setup(self):
        landscape = self.orientation.currentText() == "Landscape"
        return page_points(self.paper.currentText(), landscape)

    def _render(self):
        per_page = self.per_page.value()
        pages = contact_page_slices(self.images, per_page)
        count = len(self.images)
        page_count = len(pages)
        self.print_btn.setEnabled((not self._busy) and count > 0)
        self.prev_page.setEnabled(self.page_index > 0)
        self.next_page.setEnabled(self.page_index < page_count - 1)
        if page_count == 0:
            self.page_label.setText("Page 0 of 0")
            self._page_image = None
            self.preview.setPixmap(QPixmap())
            if self.source == "batch":
                self.summary.setText(self._extra or "Click This Video or All Videos.")
                self.preview.setText("Click This Video or All Videos.")
            else:
                self.summary.setText(
                    "No pictures in this folder. Frame Folder opens a folder of PNG, JPEG, or TIFF files.")
                self.preview.setText("Nothing to preview")
            return
        self.page_label.setText(f"Page {self.page_index + 1} of {page_count}")
        summary = (
            f"{count} frame{'s' if count != 1 else ''}  ·  {per_page} per page  ·  "
            f"file name and folder under each frame")
        if self._extra:
            summary += "  ·  " + self._extra
        self.summary.setText(summary)
        page_w, page_h = self._page_setup()
        px_w, px_h = page_device_pixels(page_w, page_h)
        image = QImage(max(1, int(round(px_w))), max(1, int(round(px_h))), QImage.Format_RGB32)
        _set_image_dpi(image, CONTACT_SHEET_DPI)
        image.fill(Qt.white)
        painter = QPainter(image)
        paint_contact_page(
            painter, px_w, px_h, pages[self.page_index], per_page,
            f"{self.page_index + 1} / {page_count}",
        )
        painter.end()
        self._page_image = image
        self.preview.setText("")
        self._fit_preview()

    def _fit_preview(self):
        if self._page_image is None:
            return
        self.preview.setPixmap(QPixmap.fromImage(self._page_image).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "preview", None) is not None:
            self._fit_preview()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_preview()

    def print_pdf(self):
        if not self.images or self._busy:
            return
        save_dir = self.folder
        parent = self.parent()
        if not save_dir and parent is not None:
            save_dir = parent.output_folder or parent.video_folder
        default = os.path.join(save_dir or os.path.expanduser("~"), "Contact_Sheet.pdf")
        path, _selected = QFileDialog.getSaveFileName(
            self, "Print Contact Sheet to PDF", default, "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            pages = write_contact_pdf(
                path, self.images, self.per_page.value(),
                self.paper.currentText(),
                self.orientation.currentText() == "Landscape",
            )
        except OSError as error:
            QMessageBox.warning(self, APP_NAME, f"Could not write the PDF.\n\n{error}")
            return
        count = len(self.images)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Contact Sheet")
        box.setText(
            f"Saved {count} frame{'s' if count != 1 else ''} "
            f"on {pages} page{'s' if pages != 1 else ''}."
        )
        box.setInformativeText(path)
        finder = box.addButton("Show in Finder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec_()
        if box.clickedButton() is finder:
            reveal_in_finder(self, path)

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
    padding: 3px 8px;
}
QPushButton:hover  { background-color: #4a4a64; }
QPushButton:pressed { background-color: #252535; }
QPushButton:checked { background-color: #0e7c86; color: #ffffff; border-color: #00e5ff; font-weight: bold; }
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
QTabWidget::pane {
    border: 1px solid #555570;
    border-radius: 5px;
    background-color: #23232e;
    top: -1px;
}
QTabBar::tab {
    background-color: #2e2e40;
    color: #aaaaee;
    border: 1px solid #555570;
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 5px 10px;
    margin-right: 4px;
    font-weight: bold;
}
QTabBar::tab:selected {
    background-color: #2a6ada;
    color: #ffffff;
    border-color: #4a8aff;
}
QTabBar::tab:hover:!selected {
    background-color: #3d3d56;
    color: #ffffff;
}
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
QDoubleSpinBox, QSpinBox, QComboBox {
    background-color: #1e1e2e;
    color: #dcdcf0;
    border: 1px solid #555570;
    border-radius: 4px;
    padding: 2px 5px;
    min-height: 22px;
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


def _read_frames(cap, frame_numbers):
    """Yield (frame_number, ok, frame) for frame numbers in ascending order.

    Jumping with CAP_PROP_POS_FRAMES makes the decoder start again from the
    previous keyframe, so on long-GOP camera files (HEVC/H.264) a 1s interval
    paid for most of a GOP on every frame. Reading forward is cheaper when the
    next frame is close. Which is cheaper depends on the file, so both costs
    are measured as we go and each step takes the cheaper one. The frames are
    identical either way. Measured on a 126s DJI HEVC clip: 0.5s interval
    107s -> 24s, 1s 46s -> 23s; at 5s it still seeks, as before.
    """
    seek_cost = grab_cost = None
    pos = None                       # the frame the next read() returns
    for n in frame_numbers:
        gap = None if pos is None else n - pos
        if (gap is not None and gap >= 0
                and (seek_cost is None or grab_cost is None
                     or gap * grab_cost < seek_cost)):
            started = time.perf_counter()
            ok = True
            for _ in range(gap):
                if not cap.grab():
                    ok = False
                    break
            if gap and ok:
                per = (time.perf_counter() - started) / gap
                grab_cost = per if grab_cost is None else 0.8 * grab_cost + 0.2 * per
            ok, frame = cap.read() if ok else (False, None)
        else:
            started = time.perf_counter()
            cap.set(cv2.CAP_PROP_POS_FRAMES, n)
            ok, frame = cap.read()
            took = time.perf_counter() - started
            seek_cost = took if seek_cost is None else 0.8 * seek_cost + 0.2 * took
        pos = n + 1
        yield n, ok, frame


def _find_ffmpeg():
    # Bundled static binary via imageio-ffmpeg (works frozen and unfrozen)
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    # PATH and common Homebrew locations
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.exists(candidate):
            return candidate
    return None


def _detect_scene_cuts(video_path, threshold=0.4, start_sec=0.0, end_sec=None):
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return None
    cmd = [
        ffmpeg, "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    timestamps = set()
    s_bound = max(0.0, float(start_sec or 0.0))
    e_bound = float(end_sec) if (end_sec is not None and end_sec > s_bound) else None
    for line in result.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            if t <= s_bound + 0.05:
                continue
            if e_bound is not None and t >= e_bound - 0.05:
                continue
            timestamps.add(t)
    return sorted(timestamps)


class StatusLogLabel(QLabel):
    """A QLabel whose setText also appends to a connected scrolling log widget,
    so every existing self.status_label.setText(...) call throughout this file
    doubles as a log entry automatically — no call site needs to change."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._log_widget = None

    def attach_log(self, log_widget):
        self._log_widget = log_widget

    def setText(self, text):
        super().setText(text)
        if self._log_widget is not None and text:
            self._log_widget.appendPlainText(text)


class HoldDurationPopover(QWidget):
    """Small popup shown near the Hold button: pick a duration, Add inserts a
    frozen-frame hold at the current playhead. Closes on outside click (Qt.Popup)
    or Cancel; Add emits `accepted(duration_seconds)`."""
    accepted = pyqtSignal(float)

    def __init__(self, parent=None, initial=3.0):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet(
            "QWidget { background: #2b2b2b; color: #eee; }"
            "QPushButton { background: #3a3a3a; border: 1px solid #555; border-radius: 4px; padding: 4px 10px; }"
            "QPushButton:default { background: #2a6ada; border-color: #2a6ada; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Duration:"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(5, 150)  # 0.5s .. 15.0s, in 0.1s steps
        self.slider.setFixedWidth(220)
        self.slider.setValue(int(round(initial * 10)))
        lay.addWidget(self.slider)
        self.val_label = QLabel(f"{initial:.2f}s")
        self.val_label.setFixedWidth(50)
        lay.addWidget(self.val_label)
        self.slider.valueChanged.connect(lambda v: self.val_label.setText(f"{v / 10.0:.2f}s"))
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.close)
        lay.addWidget(btn_cancel)
        btn_add = QPushButton("Add")
        btn_add.setDefault(True)
        btn_add.clicked.connect(self._on_add)
        lay.addWidget(btn_add)

    def _on_add(self):
        self.accepted.emit(self.slider.value() / 10.0)
        self.close()


def _remove_quarantine_attr():
    if sys.platform != "darwin":
        return
    try:
        app_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run(["xattr", "-d", "com.apple.quarantine", app_path],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class VideoFrameCapture(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  —  {BUILD_LABEL_SHORT}")
        self.setMinimumSize(1120, 740)
        self.setAcceptDrops(True)

        self.video_folder   = None
        self.video_files    = []
        self.video_info     = {}   # filename → {duration, width, height, fps}
        self.current_cap    = None
        self.current_video_path = None
        self.total_frames   = 0
        self.fps            = 30
        self.output_folder  = None
        self.contact_dialog = None
        self._contact_busy = False
        self._playing       = False
        self._has_alpha     = False
        self.download_dialog = None
        self.trim_in_sec    = 0.0
        self.trim_out_sec   = None
        self.holds          = []   # [{"id": int, "at": source_sec, "duration": sec}, ...], stacking freeze-frame inserts
        self._hold_id_counter = 0
        self._hold_wait     = None  # {"hold": dict, "start": monotonic_ts} while playback is paused on a hold
        self.selected_hold_id = None
        self.detected_cuts  = []
        self.chapter_titles = {}
        self.caption_cues   = []
        self._caption_dialog_list = None
        self._caption_drawer_dialog = None
        self._last_caption_highlight_idx = -2
        self.narration_path = None
        self._mic_process   = None
        self._preview_audio = QMediaPlayer(self)
        self._preview_audio_path = None
        self._preview_audio_sig = None
        self._dub_player_proc = None
        self._whisper_worker = None
        self._playback_rate = 1.0  # JKL shuttle rate (-16..-1, 0, 1..16)
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

        import_btn = QPushButton("🎬  Import Video File…")
        import_btn.setMinimumHeight(34)
        import_btn.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; "
            "border-radius: 4px; border: 1px solid #2ecc71;"
        )
        import_btn.setToolTip("Open one or more video files directly (Cmd+O)")
        import_btn.clicked.connect(self.import_video_files)
        left_panel.addWidget(import_btn)

        folder_btn = QPushButton("📁  Select Video Folder")
        folder_btn.setMinimumHeight(34)
        folder_btn.clicked.connect(self.select_folder)
        left_panel.addWidget(folder_btn)

        download_btn = QPushButton("↓  Download Video from Link")
        download_btn.setMinimumHeight(34)
        download_btn.setToolTip(
            "YouTube, Vimeo, X / Twitter, Facebook, Instagram, TikTok, "
            "Reddit, Twitch, LinkedIn, Dailymotion, and Loom"
        )
        download_btn.clicked.connect(self.open_downloader)
        left_panel.addWidget(download_btn)

        output_row = QHBoxLayout()
        output_row.setSpacing(4)
        self.output_btn = QPushButton("💾 Output Folder")
        self.output_btn.setMinimumHeight(34)
        self.output_btn.setToolTip("Choose where captured frames, clips, MP3s, and segments are saved")
        self.output_btn.clicked.connect(self.choose_output_folder)
        output_row.addWidget(self.output_btn, stretch=1)
        self.reveal_output_btn = QPushButton("Finder")
        self.reveal_output_btn.setMinimumHeight(34)
        self.reveal_output_btn.setToolTip("Reveal the output folder in Finder")
        self.reveal_output_btn.setEnabled(False)
        self.reveal_output_btn.clicked.connect(self.reveal_output_folder)
        output_row.addWidget(self.reveal_output_btn)
        left_panel.addLayout(output_row)

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
        left_widget.setFixedWidth(270)

        # ── RIGHT PANEL ───────────────────────────────────────────────────────
        right_panel = QVBoxLayout()
        right_panel.setSpacing(4)

        # Preview
        self.preview_label = AnnotatedPreviewLabel(
            "Select a folder or import a video to begin\n\nYou can also drag and drop a folder or video file here"
        )
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(210)
        self.preview_label.setStyleSheet(
            "background-color: #1a1a2e; color: #555570; border-radius: 8px; "
            "font-size: 13px; border: 2px solid #2a2a40;")
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_panel.addWidget(self.preview_label, stretch=1)

        # Compact Markup & Quick Capture toolbar right under preview
        markup_row = QHBoxLayout()
        markup_row.setSpacing(5)
        markup_row.addWidget(QLabel("Markup:"))
        self.tool_combo = QComboBox()
        for label, tool_id in [
            ("Pointer", "none"),
            ("↗ Arrow", "arrow"),
            ("◯ Circle", "circle"),
            ("▭ Box", "rect"),
            ("T Title / Text", "text"),
        ]:
            self.tool_combo.addItem(label, tool_id)
        self.tool_combo.currentIndexChanged.connect(
            lambda: setattr(self.preview_label, "tool", self.tool_combo.currentData())
        )
        markup_row.addWidget(self.tool_combo)

        self.color_combo = QComboBox()
        for cname, chex in ANNOTATION_COLORS:
            self.color_combo.addItem(cname, chex)
        self.color_combo.currentIndexChanged.connect(self._on_markup_color_changed)
        markup_row.addWidget(self.color_combo)

        markup_row.addWidget(QLabel("Width:"))
        self.thickness_spin = QSpinBox()
        self.thickness_spin.setRange(2, 40)
        self.thickness_spin.setValue(4)
        self.thickness_spin.setFixedWidth(52)
        self.thickness_spin.valueChanged.connect(self._on_markup_thickness_changed)
        markup_row.addWidget(self.thickness_spin)

        markup_row.addWidget(QLabel("Font:"))
        self.markup_font_size_spin = QSpinBox()
        self.markup_font_size_spin.setRange(8, 200)
        self.markup_font_size_spin.setValue(26)
        self.markup_font_size_spin.setFixedWidth(52)
        self.markup_font_size_spin.setToolTip("Font size of the selected text markup (click a title/callout to select it)")
        self.markup_font_size_spin.valueChanged.connect(self._on_markup_font_size_changed)
        markup_row.addWidget(self.markup_font_size_spin)

        markup_row.addWidget(QLabel("Duration:"))
        self.markup_dur_spin = QDoubleSpinBox()
        self.markup_dur_spin.setRange(0.0, 120.0)
        self.markup_dur_spin.setSingleStep(0.5)
        self.markup_dur_spin.setValue(3.0)
        self.markup_dur_spin.setSuffix("s")
        self.markup_dur_spin.setSpecialValueText("All Clip")
        self.markup_dur_spin.setToolTip(
            "How long newly drawn markup stays on screen (or change Start/End in the timing strip below)"
        )
        self.markup_dur_spin.setFixedWidth(78)
        self.markup_dur_spin.valueChanged.connect(self._on_markup_duration_changed)
        markup_row.addWidget(self.markup_dur_spin)

        btn_markup_in = QPushButton("[In")
        btn_markup_in.setToolTip("Set latest markup's start time to current playhead")
        btn_markup_in.clicked.connect(self.set_last_markup_in)
        markup_row.addWidget(btn_markup_in)

        btn_markup_out = QPushButton("Out]")
        btn_markup_out.setToolTip("Set latest markup's end time to current playhead")
        btn_markup_out.clicked.connect(self.set_last_markup_out)
        markup_row.addWidget(btn_markup_out)

        btn_delete_ann = QPushButton("🗑 Delete")
        btn_delete_ann.setToolTip("Delete the selected markup — click it in the preview with the Pointer tool first")
        btn_delete_ann.clicked.connect(self._delete_selected_markup)
        markup_row.addWidget(btn_delete_ann)

        btn_undo_ann = QPushButton("Undo")
        btn_undo_ann.clicked.connect(self.preview_label.undo_annotation)
        markup_row.addWidget(btn_undo_ann)

        btn_clear_ann = QPushButton("Clear")
        btn_clear_ann.clicked.connect(self.preview_label.clear_annotations)
        markup_row.addWidget(btn_clear_ann)

        btn_copy_clip = QPushButton("📋 Copy")
        btn_copy_clip.setToolTip("Copy current frame (with markup) to clipboard (Cmd+C)")
        btn_copy_clip.clicked.connect(self.copy_frame_to_clipboard)
        markup_row.addWidget(btn_copy_clip)

        markup_row.addStretch()

        quick_cap_btn = QPushButton("📸 Capture Still")
        quick_cap_btn.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; "
            "border-radius: 4px; border: 1px solid #2ecc71; padding: 3px 10px;"
        )
        quick_cap_btn.setToolTip("Save current frame into this video's still frames folder (Cmd+S)")
        quick_cap_btn.clicked.connect(self.capture_single)
        markup_row.addWidget(quick_cap_btn)
        right_panel.addLayout(markup_row)
        self.preview_label.annotations_changed.connect(self._update_timeline_feedback)
        self.preview_label.selection_changed.connect(self._on_markup_selection_changed)

        # Video info bar + live status summary banner
        info_banner_row = QHBoxLayout()
        info_banner_row.setSpacing(8)
        self.info_bar = QLabel("")
        self.info_bar.setStyleSheet("color: #8888aa; font-size: 10px; padding: 1px 2px;")
        info_banner_row.addWidget(self.info_bar)
        info_banner_row.addStretch()
        self.feedback_banner = QLabel("Timeline: Full clip  ·  Markup: None  ·  Hold: Off  ·  Transport: J/K/L (up to 16x)")
        self.feedback_banner.setStyleSheet("color: #8ac4ff; font-size: 10px; font-weight: bold;")
        self.feedback_banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.feedback_banner.setCursor(Qt.IBeamCursor)
        info_banner_row.addWidget(self.feedback_banner)
        right_panel.addLayout(info_banner_row)

        # ── INTERACTIVE TIMING STRIP (Type values directly to jump, trim, extend markup, or hold frame!) ──
        timing_frame = QFrame()
        timing_frame.setStyleSheet(
            "QFrame { background-color: #1e2238; border: 1px solid #354068; border-radius: 5px; }"
            "QLabel { border: none; background: transparent; color: #a8d8ff; font-size: 11px; font-weight: bold; }"
        )
        timing_row = QHBoxLayout(timing_frame)
        timing_row.setContentsMargins(6, 3, 6, 3)
        timing_row.setSpacing(4)

        # 1. Playhead / Jump to second
        timing_row.addWidget(QLabel("Playhead:"))
        self.jump_spin = QDoubleSpinBox()
        self.jump_spin.setDecimals(2)
        self.jump_spin.setRange(0.0, 99999.0)
        self.jump_spin.setSingleStep(0.5)
        self.jump_spin.setSuffix("s")
        self.jump_spin.setFixedWidth(80)
        self.jump_spin.setKeyboardTracking(False)
        self.jump_spin.setToolTip("Type any second and press Enter (or click Go) to jump directly to that part of the video")
        self.jump_spin.editingFinished.connect(self.jump_to_second)
        timing_row.addWidget(self.jump_spin)
        jump_btn = QPushButton("Go")
        jump_btn.setFixedWidth(40)
        jump_btn.clicked.connect(self.jump_to_second)
        timing_row.addWidget(jump_btn)

        timing_row.addSpacing(4)

        # 2. Clip [In → Out]
        timing_row.addWidget(QLabel("Clip:"))
        btn_set_in = QPushButton("[ In")
        btn_set_in.setToolTip("Set Clip In to current playhead (I) — or drag the green '[' line on the timeline")
        btn_set_in.clicked.connect(self.set_trim_in)
        timing_row.addWidget(btn_set_in)

        self.in_spin = QDoubleSpinBox()
        self.in_spin.setRange(0.0, 99999.0)
        self.in_spin.setDecimals(2)
        self.in_spin.setSingleStep(0.25)
        self.in_spin.setSuffix("s")
        self.in_spin.setFixedWidth(78)
        self.in_spin.setKeyboardTracking(False)
        self.in_spin.setToolTip("Clip In time (seconds) — type a value or drag the green '[' line")
        self.in_spin.valueChanged.connect(self._on_trim_spin_changed)
        timing_row.addWidget(self.in_spin)

        timing_row.addWidget(QLabel("→"))
        self.out_spin = QDoubleSpinBox()
        self.out_spin.setRange(0.0, 99999.0)
        self.out_spin.setDecimals(2)
        self.out_spin.setSingleStep(0.25)
        self.out_spin.setSuffix("s")
        self.out_spin.setFixedWidth(78)
        self.out_spin.setKeyboardTracking(False)
        self.out_spin.setToolTip("Clip Out time (seconds) — type a value or drag the green ']' line")
        self.out_spin.valueChanged.connect(self._on_trim_spin_changed)
        timing_row.addWidget(self.out_spin)

        btn_set_out = QPushButton("Out ]")
        btn_set_out.setToolTip("Set Clip Out to current playhead (O) — or drag the green ']' line on the timeline")
        btn_set_out.clicked.connect(self.set_trim_out)
        timing_row.addWidget(btn_set_out)

        btn_reset_trim = QPushButton("Full")
        btn_reset_trim.setToolTip("Reset In/Out to full video duration")
        btn_reset_trim.clicked.connect(self.reset_trim_range)
        timing_row.addWidget(btn_reset_trim)

        timing_row.addSpacing(4)

        # 3. Markup [Start → End] editable spinboxes
        timing_row.addWidget(QLabel("🖍 Markup:"))
        self.markup_start_spin = QDoubleSpinBox()
        self.markup_start_spin.setRange(0.0, 99999.0)
        self.markup_start_spin.setDecimals(2)
        self.markup_start_spin.setSingleStep(0.5)
        self.markup_start_spin.setSuffix("s")
        self.markup_start_spin.setFixedWidth(76)
        self.markup_start_spin.setKeyboardTracking(False)
        self.markup_start_spin.setToolTip("Start time of current/latest markup (type to change when it appears)")
        self.markup_start_spin.valueChanged.connect(self._on_markup_spin_edited)
        timing_row.addWidget(self.markup_start_spin)

        timing_row.addWidget(QLabel("→"))
        self.markup_end_spin = QDoubleSpinBox()
        self.markup_end_spin.setRange(0.0, 99999.0)
        self.markup_end_spin.setDecimals(2)
        self.markup_end_spin.setSingleStep(0.5)
        self.markup_end_spin.setSuffix("s")
        self.markup_end_spin.setFixedWidth(76)
        self.markup_end_spin.setKeyboardTracking(False)
        self.markup_end_spin.setToolTip("End time of current/latest markup (type a larger second to keep arrow/markup on screen longer)")
        self.markup_end_spin.valueChanged.connect(self._on_markup_spin_edited)
        timing_row.addWidget(self.markup_end_spin)

        timing_row.addSpacing(4)

        # 4. Hold — click to drop a frozen-frame insert at the playhead; each one
        # stacks onto the timeline (it gets longer every time you add one).
        self.btn_freeze_here = QPushButton("❄️ Hold")
        self.btn_freeze_here.setToolTip("Insert a frozen-frame hold at the playhead (opens a duration picker)")
        self.btn_freeze_here.clicked.connect(self._open_hold_popover)
        timing_row.addWidget(self.btn_freeze_here)

        timing_row.addStretch()
        right_panel.addWidget(timing_frame)

        # Scrubber + Interactive Timeline Map Bar (drag green In/Out lines, Markup spans, or Hold Frame!)
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self.on_slider_moved)
        right_panel.addWidget(self.frame_slider)

        self.timeline_bar = TimelineMapBar(self)
        self.timeline_bar.trim_in_changed.connect(self._on_timeline_drag_in)
        self.timeline_bar.trim_out_changed.connect(self._on_timeline_drag_out)
        self.timeline_bar.playhead_scrubbed.connect(self._on_timeline_scrub)
        self.timeline_bar.playhead_drag_started.connect(self._on_playhead_drag_started)
        self.timeline_bar.playhead_drag_finished.connect(self._on_playhead_drag_finished)
        self.timeline_bar.markup_span_changed.connect(self._on_timeline_markup_span)
        self.timeline_bar.markup_selected.connect(self._on_timeline_markup_selected)
        self.timeline_bar.hold_selected.connect(self._on_timeline_hold_selected)
        self.timeline_bar.hold_moved.connect(self._on_timeline_hold_moved)
        right_panel.addWidget(self.timeline_bar)

        # Combined Timecode + Transport + Nav + Format row (compact single line!)
        tc_nav_row = QHBoxLayout()
        tc_nav_row.setSpacing(4)
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setStyleSheet('font-family: "SF Mono", Menlo, monospace; font-size: 12px;')
        self.time_label.setFixedWidth(185)
        tc_nav_row.addWidget(self.time_label)

        self.btn_play = QPushButton("▶  Play")
        self.btn_play.setFixedWidth(86)
        self.btn_play.setEnabled(False)
        self.btn_play.setToolTip("Play / Pause (Space)  ·  Shuttle: J (Rev up to -16x) / K (Pause) / L (Fwd up to 16x)")
        self.btn_play.clicked.connect(self.toggle_play)
        tc_nav_row.addWidget(self.btn_play)

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
            tc_nav_row.addWidget(b)

        tc_nav_row.addStretch()
        self.frame_label = QLabel("Frame: 0 / 0")
        self.frame_label.setStyleSheet('font-family: "SF Mono", Menlo, monospace; font-size: 12px;')
        self.frame_label.setFixedWidth(200)
        tc_nav_row.addWidget(self.frame_label)

        tc_nav_row.addSpacing(8)
        tc_nav_row.addWidget(QLabel("Still Format:"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["PNG", "JPEG", "TIFF"])
        self.fmt_combo.setFixedWidth(70)
        self.fmt_combo.currentTextChanged.connect(self._on_format_changed)
        tc_nav_row.addWidget(self.fmt_combo)
        self.quality_label = QLabel("Q:")
        self.quality_label.setVisible(False)
        tc_nav_row.addWidget(self.quality_label)
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setMinimum(1)
        self.quality_slider.setMaximum(100)
        self.quality_slider.setValue(92)
        self.quality_slider.setFixedWidth(80)
        self.quality_slider.setVisible(False)
        self.quality_slider.valueChanged.connect(
            lambda v: self.quality_val_label.setText(str(v)))
        tc_nav_row.addWidget(self.quality_slider)
        self.quality_val_label = QLabel("92")
        self.quality_val_label.setFixedWidth(24)
        self.quality_val_label.setVisible(False)
        tc_nav_row.addWidget(self.quality_val_label)

        sheet_btn = QPushButton("Contact Sheet…")
        sheet_btn.setToolTip(
            "Preview batch timecode frames, or a folder of saved pictures, and print one PDF")
        sheet_btn.clicked.connect(self.open_contact_sheet)
        tc_nav_row.addWidget(sheet_btn)
        right_panel.addLayout(tc_nav_row)

        # ── MODE TABS (Tab 0: Edit / Export / Chapters / Dub VO / Captions, Tab 1: Still Frame Capture) ──
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setMaximumHeight(225)

        # TAB 0: Edit, Export (MP4 Quality / MP3 / GIF), QuickTime Chapters, Dub VO & Auto-Captions
        edit_tab = QWidget()
        edit_tab_lay = QHBoxLayout(edit_tab)
        edit_tab_lay.setContentsMargins(6, 4, 6, 4)
        edit_tab_lay.setSpacing(6)

        # Column 1: Export MP4 (Quality) / MP3 (Bitrate) / GIF & Fades
        trim_grp = QGroupBox("Export MP4 / MP3 / GIF && Fades")
        trim_lay = QVBoxLayout(trim_grp)
        trim_lay.setSpacing(4)
        trim_lay.setContentsMargins(6, 8, 6, 6)

        qual_row = QHBoxLayout()
        qual_row.setSpacing(4)
        qual_row.addWidget(QLabel("MP4 Quality:"))
        self.mp4_quality_combo = QComboBox()
        self.mp4_quality_combo.addItem("Original", "original")
        self.mp4_quality_combo.addItem("2160p (4K)", "2160p")
        self.mp4_quality_combo.addItem("1440p", "1440p")
        self.mp4_quality_combo.addItem("1080p", "1080p")
        self.mp4_quality_combo.addItem("720p", "720p")
        self.mp4_quality_combo.addItem("540p", "540p")
        self.mp4_quality_combo.addItem("360p", "360p")
        self.mp4_quality_combo.addItem("240p", "240p")
        self.mp4_quality_combo.setToolTip(
            "Original keeps the source's native resolution and bitrate. Every other "
            "option caps the output at that height (never upscales) when saving MP4 "
            "clips or split segments."
        )
        qual_row.addWidget(self.mp4_quality_combo, stretch=1)
        trim_lay.addLayout(qual_row)

        aud_fade_row = QHBoxLayout()
        aud_fade_row.setSpacing(4)
        aud_fade_row.addWidget(QLabel("MP3:"))
        self.mp3_bitrate_combo = QComboBox()
        self.mp3_bitrate_combo.addItem("320 kbps", "320k")
        self.mp3_bitrate_combo.addItem("192 kbps", "192k")
        self.mp3_bitrate_combo.addItem("128 kbps", "128k")
        self.mp3_bitrate_combo.setFixedWidth(96)
        self.mp3_bitrate_combo.setToolTip("Audio format/bitrate for '🎵 Save MP3'")
        aud_fade_row.addWidget(self.mp3_bitrate_combo)

        aud_fade_row.addWidget(QLabel("Fade:"))
        self.fade_in_spin = QDoubleSpinBox()
        self.fade_in_spin.setRange(0.0, 10.0)
        self.fade_in_spin.setSingleStep(0.5)
        self.fade_in_spin.setValue(0.0)
        self.fade_in_spin.setSuffix("s")
        self.fade_in_spin.setFixedWidth(74)
        self.fade_in_spin.setToolTip("Fade In duration (seconds)")
        aud_fade_row.addWidget(self.fade_in_spin)

        aud_fade_row.addWidget(QLabel("/"))
        self.fade_out_spin = QDoubleSpinBox()
        self.fade_out_spin.setRange(0.0, 10.0)
        self.fade_out_spin.setSingleStep(0.5)
        self.fade_out_spin.setValue(0.0)
        self.fade_out_spin.setSuffix("s")
        self.fade_out_spin.setFixedWidth(74)
        self.fade_out_spin.setToolTip("Fade Out duration (seconds)")
        aud_fade_row.addWidget(self.fade_out_spin)
        aud_fade_row.addStretch()
        trim_lay.addLayout(aud_fade_row)

        gif_row = QHBoxLayout()
        gif_row.setSpacing(4)
        gif_row.addWidget(QLabel("GIF FPS:"))
        self.gif_fps_combo = QComboBox()
        for fps_val in (10, 12, 15, 24):
            self.gif_fps_combo.addItem(f"{fps_val} fps", fps_val)
        self.gif_fps_combo.setCurrentIndex(1)
        gif_row.addWidget(self.gif_fps_combo)
        gif_row.addWidget(QLabel("GIF Size:"))
        self.gif_width_combo = QComboBox()
        for label, w_val in [("480 px", 480), ("640 px", 640), ("720 px", 720), ("Original", 0)]:
            self.gif_width_combo.addItem(label, w_val)
        gif_row.addWidget(self.gif_width_combo)
        gif_row.addStretch()
        trim_lay.addLayout(gif_row)

        exp_row = QHBoxLayout()
        exp_row.setSpacing(4)
        self.btn_export_mp4 = QPushButton("🎬 Save MP4")
        self.btn_export_mp4.setMinimumHeight(32)
        self.btn_export_mp4.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; border-radius: 5px; border: none; padding: 3px 6px;"
        )
        self.btn_export_mp4.setToolTip("Export [In → Out] clip with Markup, Hold Frame, Dub VO, Captions & selected MP4 Quality")
        self.btn_export_mp4.clicked.connect(lambda: self.export_edited_clip(as_gif=False))
        exp_row.addWidget(self.btn_export_mp4)

        self.btn_export_audio = QPushButton("🎵 Save MP3")
        self.btn_export_audio.setMinimumHeight(32)
        self.btn_export_audio.setStyleSheet(
            "background-color: #6c3483; color: white; font-weight: bold; border-radius: 5px; border: none; padding: 3px 6px;"
        )
        self.btn_export_audio.setToolTip("1-Click save audio only (MP3/WAV) for [In → Out] directly to Output Folder")
        self.btn_export_audio.clicked.connect(self.export_trimmed_audio)
        exp_row.addWidget(self.btn_export_audio)

        self.btn_export_gif = QPushButton("🎞 Save GIF")
        self.btn_export_gif.setMinimumHeight(32)
        self.btn_export_gif.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; border-radius: 5px; border: none; padding: 3px 6px;"
        )
        self.btn_export_gif.clicked.connect(lambda: self.export_edited_clip(as_gif=True))
        exp_row.addWidget(self.btn_export_gif)
        trim_lay.addLayout(exp_row)
        edit_tab_lay.addWidget(trim_grp, stretch=11)

        # Column 2: Scene Cuts, QuickTime MP4 Chapters & Splitter (respects [In → Out]!)
        cuts_grp = QGroupBox("Scene Cuts, Chapters && Splitter")
        cuts_lay = QVBoxLayout(cuts_grp)
        cuts_lay.setSpacing(3)
        cuts_lay.setContentsMargins(6, 8, 6, 6)

        cuts_top = QHBoxLayout()
        cuts_top.setSpacing(4)
        btn_find_cuts = QPushButton("🔍 Detect Cuts")
        btn_find_cuts.setToolTip("Auto-detect scene cuts strictly within [Clip In → Out]")
        btn_find_cuts.clicked.connect(self.detect_cuts_for_current)
        cuts_top.addWidget(btn_find_cuts)
        btn_prev_cut = QPushButton("◀ Cut")
        btn_prev_cut.clicked.connect(lambda: self.jump_cut(-1))
        cuts_top.addWidget(btn_prev_cut)
        btn_next_cut = QPushButton("Cut ▶")
        btn_next_cut.clicked.connect(lambda: self.jump_cut(1))
        cuts_top.addWidget(btn_next_cut)
        cuts_lay.addLayout(cuts_top)

        self.cuts_list = QListWidget()
        self.cuts_list.setMaximumHeight(50)
        self.cuts_list.setToolTip(
            "Click a cut/chapter to preview; double-click or click '✏️ Name' to title the chapter"
        )
        self.cuts_list.currentRowChanged.connect(self._on_cut_selected)
        self.cuts_list.itemDoubleClicked.connect(lambda _item: self.rename_selected_chapter())
        cuts_lay.addWidget(self.cuts_list)

        cuts_adj = QHBoxLayout()
        cuts_adj.setSpacing(4)
        btn_add_cut = QPushButton("+ Add Cut")
        btn_add_cut.clicked.connect(self.add_cut_at_current)
        cuts_adj.addWidget(btn_add_cut)
        btn_nudge_cut = QPushButton("↔ Move")
        btn_nudge_cut.setToolTip("Move selected cut/chapter point to current scrubber frame")
        btn_nudge_cut.clicked.connect(self.move_selected_cut_to_current)
        cuts_adj.addWidget(btn_nudge_cut)
        btn_name_cut = QPushButton("✏️ Name")
        btn_name_cut.setToolTip("Set a custom chapter title for the selected cut point")
        btn_name_cut.clicked.connect(self.rename_selected_chapter)
        cuts_adj.addWidget(btn_name_cut)
        btn_del_cut = QPushButton("Del")
        btn_del_cut.clicked.connect(self.remove_selected_cut)
        cuts_adj.addWidget(btn_del_cut)
        cuts_lay.addLayout(cuts_adj)

        cuts_export_row = QHBoxLayout()
        cuts_export_row.setSpacing(4)
        btn_save_chapters = QPushButton("📑 MP4 + Chapters")
        btn_save_chapters.setMinimumHeight(32)
        btn_save_chapters.setToolTip(
            "Save [In → Out] MP4 with QuickTime Player, VLC & YouTube chapter markers"
        )
        btn_save_chapters.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; border-radius: 5px; border: none; padding: 3px 6px;"
        )
        btn_save_chapters.clicked.connect(self.export_current_video_with_chapters)
        cuts_export_row.addWidget(btn_save_chapters)

        btn_split_segs = QPushButton("✂️ Split into Clips")
        btn_split_segs.setMinimumHeight(32)
        btn_split_segs.setToolTip(
            "Split [In → Out] at each scene cut into separate MP4 clips in <video>_segments/"
        )
        btn_split_segs.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; border-radius: 5px; border: none; padding: 3px 6px;"
        )
        btn_split_segs.clicked.connect(self.split_current_video_by_cuts)
        cuts_export_row.addWidget(btn_split_segs)
        cuts_lay.addLayout(cuts_export_row)
        edit_tab_lay.addWidget(cuts_grp, stretch=10)

        # Column 3: Dub / Voiceover Recording & Offline Whisper Auto-Captions
        narr_grp = QGroupBox("🎙 Dub Recording && 💬 Auto-Captions")
        narr_lay = QVBoxLayout(narr_grp)
        narr_lay.setSpacing(3)
        narr_lay.setContentsMargins(6, 8, 6, 6)

        narr_top_row = QHBoxLayout()
        narr_top_row.setSpacing(4)
        self.mic_device_combo = QComboBox()
        self.mic_device_combo.setToolTip("Select microphone or audio input device for Dub / Voiceover recording")
        self._populate_mic_devices()
        narr_top_row.addWidget(self.mic_device_combo, stretch=1)

        self.narr_mode_combo = QComboBox()
        self.narr_mode_combo.addItem("Duck Video 20%", "duck")
        self.narr_mode_combo.addItem("Replace Audio", "replace")
        self.narr_mode_combo.addItem("Mix 100%", "mix")
        narr_top_row.addWidget(self.narr_mode_combo)
        narr_lay.addLayout(narr_top_row)

        narr_btn_row = QHBoxLayout()
        narr_btn_row.setSpacing(4)
        self.btn_rec_mic = QPushButton("🎙 Dub Record")
        self.btn_rec_mic.setCheckable(True)
        self.btn_rec_mic.setStyleSheet(
            "QPushButton { background-color: #7b241c; color: white; font-weight: bold; border-radius: 4px; padding: 3px 6px; }"
            "QPushButton:checked { background-color: #e74c3c; color: white; border: 1px solid #ffffff; }"
        )
        self.btn_rec_mic.setToolTip("Record live dub / voiceover while watching the video play")
        self.btn_rec_mic.toggled.connect(self.toggle_mic_recording)
        narr_btn_row.addWidget(self.btn_rec_mic)

        self.btn_play_dub = QPushButton("🔊 Play")
        self.btn_play_dub.setToolTip("Preview recorded or loaded dub audio")
        self.btn_play_dub.clicked.connect(self.preview_dub_audio)
        narr_btn_row.addWidget(self.btn_play_dub)

        btn_load_narr = QPushButton("📂 Load")
        btn_load_narr.setToolTip("Load an external audio file (.wav, .mp3, .m4a) as dub track")
        btn_load_narr.clicked.connect(self.choose_narration_file)
        narr_btn_row.addWidget(btn_load_narr)

        btn_clear_narr = QPushButton("✕")
        btn_clear_narr.setFixedWidth(26)
        btn_clear_narr.setToolTip("Clear dub / narration audio")
        btn_clear_narr.clicked.connect(self.clear_narration)
        narr_btn_row.addWidget(btn_clear_narr)
        narr_lay.addLayout(narr_btn_row)

        self.narr_label = QLabel("Dub: None  ·  Subtitles below:")
        self.narr_label.setStyleSheet("color: #8888aa; font-size: 10px;")
        narr_lay.addWidget(self.narr_label)

        cap_btn_row = QHBoxLayout()
        cap_btn_row.setSpacing(4)
        self.btn_auto_cap = QPushButton("💬 Captions AI")
        self.btn_auto_cap.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; border-radius: 4px; padding: 3px 6px;"
        )
        self.btn_auto_cap.setToolTip("Generate timed subtitles offline using bundled Whisper AI")
        self.btn_auto_cap.clicked.connect(self.auto_transcribe_captions)
        cap_btn_row.addWidget(self.btn_auto_cap)

        self.auto_cap_on_load_chk = QCheckBox("Auto")
        self.auto_cap_on_load_chk.setToolTip("Automatically transcribe subtitles when a video is opened")
        cap_btn_row.addWidget(self.auto_cap_on_load_chk)

        btn_add_cap = QPushButton("+ Sub")
        btn_add_cap.setToolTip("Add a manual subtitle at current playhead")
        btn_add_cap.clicked.connect(self.add_manual_caption)
        cap_btn_row.addWidget(btn_add_cap)

        btn_srt = QPushButton("Cap Export")
        btn_srt.setToolTip("Load an .srt, or save captions as .srt / .txt / .csv")
        btn_srt.clicked.connect(self.manage_srt_menu)
        cap_btn_row.addWidget(btn_srt)

        btn_clear_cap = QPushButton("Clear")
        btn_clear_cap.clicked.connect(self.clear_captions)
        cap_btn_row.addWidget(btn_clear_cap)

        btn_expand_cap = QPushButton("Cap Text")
        btn_expand_cap.setToolTip("Open a larger window to read/edit every caption")
        btn_expand_cap.clicked.connect(self._open_caption_list_dialog)
        cap_btn_row.addWidget(btn_expand_cap)
        narr_lay.addLayout(cap_btn_row)

        burn_row = QHBoxLayout()
        self.burn_captions_cb = QCheckBox("Burn into video/preview")
        self.burn_captions_cb.setChecked(True)
        self.burn_captions_cb.setToolTip(
            "On: captions are drawn onto the preview and the exported MP4/GIF.\n"
            "Off: captions stay as timed data only (Cap Text / SRT export) — nothing is drawn on screen."
        )
        self.burn_captions_cb.toggled.connect(self._on_burn_captions_toggled)
        burn_row.addWidget(self.burn_captions_cb)

        self.caption_position_combo = QComboBox()
        self.caption_position_combo.addItem("Bottom", "bottom")
        self.caption_position_combo.addItem("Top", "top")
        self.caption_position_combo.setToolTip("Where burned-in captions sit — move to Top if they'd block a lower third")
        self.caption_position_combo.currentIndexChanged.connect(self._on_caption_position_changed)
        burn_row.addWidget(self.caption_position_combo)
        burn_row.addStretch()
        narr_lay.addLayout(burn_row)

        # Caption list itself is kept (not shown) as the backing widget every
        # caption-selection/edit/highlight method already targets — it only
        # appears via the "Cap Text" drawer now, so the main panel stays clean.
        self.caption_list = QListWidget()
        self.caption_list.setMaximumHeight(90)
        self.caption_list.setToolTip("Click a caption to jump to it; double-click to edit its text")
        self.caption_list.currentRowChanged.connect(self._on_caption_selected)
        self.caption_list.itemDoubleClicked.connect(lambda _item: self.edit_selected_caption())
        self.caption_list.setVisible(False)
        narr_lay.addWidget(self.caption_list)

        edit_tab_lay.addWidget(narr_grp, stretch=11)
        self.mode_tabs.addTab(edit_tab, "🎬 Edit, Export (MP4 / MP3 / GIF), Chapters, Dub && Captions   ")

        # TAB 1: Still Frame Capture (Single horizontal row of 4 cards — zero squishing!)
        capture_tab = QWidget()
        capture_row = QHBoxLayout(capture_tab)
        capture_row.setContentsMargins(6, 4, 6, 4)
        capture_row.setSpacing(8)

        # Card 1: Single Capture
        single_grp = QGroupBox("Single Frame")
        single_lay = QVBoxLayout(single_grp)
        single_lay.setSpacing(4)
        self.btn_capture = QPushButton("📸  Capture This Frame")
        self.btn_capture.setMinimumHeight(38)
        self.btn_capture.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; "
            "font-size: 13px; border-radius: 5px; border: none;")
        self.btn_capture.setToolTip("Capture current frame into <video>_frames/ subfolder (Cmd+S)")
        self.btn_capture.clicked.connect(self.capture_single)
        single_lay.addWidget(self.btn_capture)
        capture_row.addWidget(single_grp, stretch=9)

        # Card 2: Batch Capture
        batch_grp = QGroupBox("Batch Timecodes (All Videos)")
        batch_lay = QVBoxLayout(batch_grp)
        batch_lay.setSpacing(3)
        tc_list_row = QHBoxLayout()
        self.timecode_list = QListWidget()
        self.timecode_list.setMaximumHeight(48)
        self.timecode_list.setToolTip("Timecodes to extract from each video")
        tc_list_row.addWidget(self.timecode_list, stretch=1)

        tc_btn_col = QVBoxLayout()
        tc_btn_col.setSpacing(2)
        self.batch_spin = QDoubleSpinBox()
        self.batch_spin.setDecimals(2)
        self.batch_spin.setRange(0, 99999)
        self.batch_spin.setSingleStep(0.5)
        self.batch_spin.setFixedWidth(82)
        tc_btn_col.addWidget(self.batch_spin)
        tc_add_del = QHBoxLayout()
        add_tc_btn = QPushButton("+ Add")
        add_tc_btn.clicked.connect(self._add_timecode)
        tc_add_del.addWidget(add_tc_btn)
        del_tc_btn = QPushButton("Del")
        del_tc_btn.clicked.connect(self._remove_timecode)
        tc_add_del.addWidget(del_tc_btn)
        tc_btn_col.addLayout(tc_add_del)
        tc_list_row.addLayout(tc_btn_col)
        batch_lay.addLayout(tc_list_row)

        batch_bot_row = QHBoxLayout()
        use_tc_btn = QPushButton("← Playhead")
        use_tc_btn.setToolTip("Set timecode input to current scrubber position")
        use_tc_btn.clicked.connect(self.use_current_timecode)
        self.btn_use_timecode = use_tc_btn
        batch_bot_row.addWidget(use_tc_btn)

        self.btn_batch = QPushButton("⚡ Batch All")
        self.btn_batch.setMinimumHeight(30)
        self.btn_batch.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        self.btn_batch.clicked.connect(self.capture_batch)
        batch_bot_row.addWidget(self.btn_batch)
        batch_lay.addLayout(batch_bot_row)
        capture_row.addWidget(batch_grp, stretch=12)

        # Card 3: Interval Capture
        interval_grp = QGroupBox("Interval Capture (In→Out)")
        interval_lay = QVBoxLayout(interval_grp)
        interval_lay.setSpacing(4)
        ivl_top = QHBoxLayout()
        ivl_top.addWidget(QLabel("Every:"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setDecimals(1)
        self.interval_spin.setRange(0.1, 3600)
        self.interval_spin.setValue(3.0)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setSuffix(" sec")
        self.interval_spin.setFixedWidth(92)
        ivl_top.addWidget(self.interval_spin)
        ivl_top.addStretch()
        interval_lay.addLayout(ivl_top)
        ivl_btns = QHBoxLayout()
        btn_ivl_single = QPushButton("⏱ This Video")
        btn_ivl_single.setMinimumHeight(32)
        btn_ivl_single.clicked.connect(self.capture_interval_single)
        ivl_btns.addWidget(btn_ivl_single)
        btn_ivl_batch = QPushButton("⏱ All Videos")
        btn_ivl_batch.setMinimumHeight(32)
        btn_ivl_batch.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        btn_ivl_batch.clicked.connect(self.capture_interval_batch)
        ivl_btns.addWidget(btn_ivl_batch)
        interval_lay.addLayout(ivl_btns)
        capture_row.addWidget(interval_grp, stretch=10)

        # Card 4: Scene Capture
        scene_grp = QGroupBox("Scene Capture (In→Out)")
        scene_lay = QVBoxLayout(scene_grp)
        scene_lay.setSpacing(4)
        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel("Sensitivity:"))
        self.scene_threshold_slider = QSlider(Qt.Horizontal)
        self.scene_threshold_slider.setMinimum(10)
        self.scene_threshold_slider.setMaximum(90)
        self.scene_threshold_slider.setValue(40)
        self.scene_threshold_slider.valueChanged.connect(
            lambda v: self.scene_thresh_label.setText(f"{v / 100:.2f}"))
        thresh_row.addWidget(self.scene_threshold_slider)
        self.scene_thresh_label = QLabel("0.40")
        self.scene_thresh_label.setFixedWidth(34)
        thresh_row.addWidget(self.scene_thresh_label)
        scene_lay.addLayout(thresh_row)
        scene_btns = QHBoxLayout()
        btn_scene_single = QPushButton("🎬 This Video")
        btn_scene_single.setMinimumHeight(32)
        btn_scene_single.clicked.connect(self.capture_scenes_single)
        scene_btns.addWidget(btn_scene_single)
        btn_scene_batch = QPushButton("🎬 All Videos")
        btn_scene_batch.setMinimumHeight(32)
        btn_scene_batch.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        btn_scene_batch.clicked.connect(self.capture_scenes_batch)
        scene_btns.addWidget(btn_scene_batch)
        scene_lay.addLayout(scene_btns)
        capture_row.addWidget(scene_grp, stretch=10)

        self.mode_tabs.addTab(capture_tab, "📸  Still Frame Capture (Single, Batch, Interval && Scene)")
        right_panel.addWidget(self.mode_tabs)

        # Progress + status
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        right_panel.addWidget(self.progress)

        status_row = QHBoxLayout()
        self.status_label = StatusLogLabel("Ready  ·  Import a video or drag a folder here to begin")
        self.status_label.setStyleSheet("color: #8888aa; padding: 2px 4px; font-size: 11px;")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setCursor(Qt.IBeamCursor)
        status_row.addWidget(self.status_label, stretch=1)
        btn_copy_log = QPushButton("Copy Log")
        btn_copy_log.setToolTip("Copy every status/warning message from this session to the clipboard")
        btn_copy_log.clicked.connect(
            lambda: QApplication.clipboard().setText(self.status_log.toPlainText())
        )
        status_row.addWidget(btn_copy_log)
        right_panel.addLayout(status_row)

        self.status_log = QPlainTextEdit()
        self.status_log.setReadOnly(True)
        self.status_log.setMaximumHeight(70)
        self.status_log.setStyleSheet(
            "background: #15151f; color: #9999bb; font-size: 10px; border: 1px solid #333344;"
        )
        self.status_log.setToolTip("Scrolling history of every status/warning message — select any of it and Cmd+C, or use Copy Log")
        right_panel.addWidget(self.status_log)
        self.status_label.attach_log(self.status_log)

        right_widget = QWidget()
        right_widget.setLayout(right_panel)

        main_layout.addWidget(left_widget)
        main_layout.addWidget(right_widget, stretch=1)

        # Ensure pressing Spacebar always toggles video Play/Pause instead of clicking focused buttons
        for btn in self.findChildren(QPushButton):
            btn.setFocusPolicy(Qt.NoFocus)

    def open_downloader(self):
        if self.download_dialog is None:
            self.download_dialog = DownloadDialog(self, ffmpeg=_find_ffmpeg())
            self.download_dialog.video_ready.connect(self._load_download)
        self.download_dialog.show()
        self.download_dialog.raise_()
        self.download_dialog.activateWindow()

    def _load_download(self, path):
        self._load_folder(os.path.dirname(path))
        name = os.path.basename(path)
        if name in self.video_files:
            self.file_list.setCurrentRow(self.video_files.index(name))
            self.mode_tabs.setCurrentIndex(0)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence("Ctrl+O"), self, self.import_video_files)
        QShortcut(QKeySequence("Ctrl+S"), self, self.capture_single)
        QShortcut(QKeySequence("Ctrl+C"), self, self.copy_frame_to_clipboard)
        QShortcut(QKeySequence("I"), self, self.set_trim_in)
        QShortcut(QKeySequence("O"), self, self.set_trim_out)
        QShortcut(QKeySequence("J"), self, self.shuttle_reverse)
        QShortcut(QKeySequence("K"), self, self.shuttle_pause)
        QShortcut(QKeySequence("L"), self, self.shuttle_forward)
        QShortcut(QKeySequence(Qt.Key_Left),  self, lambda: self.seek_frames(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.seek_frames(1))
        QShortcut(QKeySequence("Shift+Left"),  self, lambda: self.seek_relative(-1))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek_relative(1))
        QShortcut(QKeySequence(Qt.Key_Delete), self, self._delete_selected_markup)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, self._delete_selected_markup)

    # ── Folder / file management ──────────────────────────────────────────────

    def import_video_files(self):
        exts = " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)
        start_dir = self.video_folder or os.path.expanduser("~")
        paths, _flt = QFileDialog.getOpenFileNames(
            self,
            "Import Video File(s)",
            start_dir,
            f"Video Files ({exts});;All Files (*)",
        )
        if not paths:
            return
        first_path = paths[0]
        folder = os.path.dirname(first_path)
        names = [os.path.basename(p) for p in paths if os.path.dirname(p) == folder]
        self._load_folder(folder, only_files=names)
        if self.video_files:
            self.file_list.setCurrentRow(0)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Video Folder")
        if folder:
            self._load_folder(folder)

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if folder:
            self._set_output_folder(folder)

    def _set_output_folder(self, folder):
        self.output_folder = folder
        self.output_label.setText(f"Output: {folder}")
        self.reveal_output_btn.setEnabled(bool(folder))
        self._refresh_contact_sheet()

    def _ask_contact_source(self):
        box = QMessageBox(self)
        box.setWindowTitle("Contact Sheet")
        box.setText("Where should the frames come from?")
        box.setInformativeText(
            "Batch Frames uses the batch timecode on the highlighted video, "
            "or on every video in the folder.\n\n"
            "Frame Folder opens a folder of pictures you already have. "
            "Select Video Folder only lists movies, so a folder of stills belongs here."
        )
        batch = box.addButton("Batch Frames", QMessageBox.AcceptRole)
        folder = box.addButton("Frame Folder…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is batch:
            return "batch"
        if clicked is folder:
            return "folder"
        return None

    def _batch_contact_prompt(self):
        times = ", ".join(f"{tc:.2f}s" for tc in self._get_timecodes())
        return f"Batch time {times}. Click This Video or All Videos."

    def open_contact_sheet(self):
        choice = self._ask_contact_source()
        if choice is None:
            return
        if self.contact_dialog is None:
            self.contact_dialog = ContactSheetDialog(self)
        if choice == "batch":
            if not self.video_files:
                QMessageBox.information(
                    self, APP_NAME,
                    "Select a video folder first. Batch Frames reads the movies in that list.",
                )
                return
            self.contact_dialog.prepare_batch(self._batch_contact_prompt())
        else:
            start = self.video_folder or self.output_folder or os.path.expanduser("~")
            folder = QFileDialog.getExistingDirectory(
                self, "Choose Folder of Frames", start)
            if not folder:
                return
            self.contact_dialog.set_folder(folder)
            if not self.contact_dialog.images:
                QMessageBox.information(
                    self, "Contact Sheet", _empty_frame_folder_message(folder))
        self.contact_dialog.show()
        self.contact_dialog.raise_()
        self.contact_dialog.activateWindow()

    def _refresh_contact_sheet(self):
        dialog = self.contact_dialog
        if dialog is None or not dialog.isVisible():
            return
        if dialog.source == "folder" and dialog.folder and dialog.folder == self.output_folder:
            dialog.set_folder(self.output_folder)

    def fill_contact_sheet(self, selected_only):
        if self._contact_busy:
            return
        if selected_only:
            if not self.current_video_path:
                QMessageBox.information(self, APP_NAME, "Select a video in the list first.")
                return
            paths = [self.current_video_path]
        else:
            if not self.video_folder or not self.video_files:
                QMessageBox.information(self, APP_NAME, "Select a video folder first.")
                return
            paths = [os.path.join(self.video_folder, name) for name in self.video_files]
        dialog = self.contact_dialog
        if dialog is None:
            dialog = ContactSheetDialog(self)
            self.contact_dialog = dialog
            dialog.show()
        self._contact_busy = True
        dialog.set_busy(True)
        try:
            cells, skipped, errors = self._grab_contact_frames(paths)
        finally:
            self._contact_busy = False
            if dialog is not None:
                dialog.set_busy(False)
        if dialog is None or not dialog.isVisible():
            return
        dialog.set_frames(cells, _contact_extra(skipped, errors))
        if not cells:
            QMessageBox.information(
                self, "Contact Sheet",
                _contact_detail(skipped, errors) or "No frames were read.",
            )

    def _grab_contact_frames(self, paths):
        timecodes = self._get_timecodes()
        cells = []
        skipped = []
        errors = []
        total = max(1, len(paths) * len(timecodes))
        self.progress.setVisible(True)
        self.progress.setMaximum(total)
        self.progress.setValue(0)
        done = 0
        dialog = self.contact_dialog
        try:
            for path in paths:
                if dialog is not None and not dialog.isVisible():
                    break
                filename = os.path.basename(path)
                directory = os.path.dirname(path)
                cap = cv2.VideoCapture(path)
                try:
                    if not cap.isOpened():
                        errors.append(f"{filename}: could not open")
                        done += len(timecodes)
                        self._report_contact_grab(done, total, filename)
                        continue
                    fps = cap.get(cv2.CAP_PROP_FPS) or 0
                    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    for tc in timecodes:
                        if dialog is not None and not dialog.isVisible():
                            break
                        done += 1
                        target = contact_seek_frame(tc, fps, count)
                        if target == -1:
                            duration = (count - 1) / fps if fps else 0
                            skipped.append(
                                f"{filename}: {float(tc):.2f}s is past the end ({duration:.2f}s)")
                        else:
                            if target is None:
                                cap.set(cv2.CAP_PROP_POS_MSEC, float(tc) * 1000.0)
                            else:
                                cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                            ok, frame = cap.read()
                            image = numpy_frame_to_qimage(frame) if ok else QImage()
                            if image.isNull():
                                errors.append(
                                    f"{filename} @ {float(tc):.2f}s: could not read frame")
                            else:
                                cells.append(ContactFrame(
                                    f"{filename}  {float(tc):.2f}s", directory, image=image))
                        self._report_contact_grab(done, total, filename)
                finally:
                    cap.release()
        finally:
            self.progress.setVisible(False)
        return cells, skipped, errors

    def _report_contact_grab(self, done, total, filename):
        self.progress.setValue(min(done, total))
        dialog = self.contact_dialog
        if dialog is not None:
            dialog.summary.setText(f"Reading {min(done, total)} of {total}  ·  {filename}")
        QApplication.processEvents()

    def reveal_output_folder(self):
        folder = self.output_folder
        if not folder:
            return
        os.makedirs(folder, exist_ok=True)
        reveal_in_finder(self, folder)

    def _load_folder(self, folder, only_files=None):
        self.video_folder = folder
        self.folder_label.setText(folder)

        if not self.output_folder:
            self._set_output_folder(os.path.join(folder, "captured_frames"))
        os.makedirs(self.output_folder, exist_ok=True)

        if only_files:
            self.video_files = [
                f for f in only_files
                if os.path.isfile(os.path.join(folder, f))
            ]
        else:
            self.video_files = sorted(
                f for f in os.listdir(folder)
                if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS)

        self.video_info = {}
        self.file_list.clear()
        for f in self.video_files:
            info = self._probe_video(os.path.join(folder, f))
            self.video_info[f] = info
            dur   = f"{info['duration']:.1f}s" if info['duration'] else "?"
            res   = f"{info['width']}×{info['height']}" if info['width'] else ""
            alpha = "  α" if info.get("has_alpha") else ""
            label = f"{f}\n  {res}  {dur}  {info['fps']:.0f}fps{alpha}" if res else f
            item  = QListWidgetItem(label)
            item.setToolTip(
                f"{f}\n{res}  {dur}  {info['fps']:.1f}fps"
                + ("\n⚠ Has alpha channel" if info.get("has_alpha") else ""))
            self.file_list.addItem(item)

        self.status_label.setText(
            f"{len(self.video_files)} video(s) found  ·  output → {self.output_folder}")

    def _load_audio_as_video(self, audio_path):
        """Wrap a dropped audio-only file in a throwaway black-frame video so the
        existing video pipeline (player, Auto Caption, Cap Export) can caption it
        without needing its own audio-only code path."""
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(
                self, APP_NAME,
                "FFmpeg was not found, so this audio file can't be prepared for captioning.")
            return

        stem = os.path.splitext(os.path.basename(audio_path))[0]
        work_dir = tempfile.mkdtemp(prefix="vfc_audio_")
        synthetic_path = os.path.join(work_dir, f"{stem}.mp4")

        self.status_label.setText(f"Preparing “{os.path.basename(audio_path)}” for captioning…")
        QApplication.processEvents()
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=5",
            "-i", audio_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            synthetic_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.isfile(synthetic_path):
            detail = (result.stderr or "")[-600:]
            QMessageBox.warning(
                self, APP_NAME,
                f"Could not prepare “{os.path.basename(audio_path)}” for captioning.\n\n{detail}")
            return

        self.video_folder = os.path.dirname(audio_path)
        self.folder_label.setText(self.video_folder)
        self.video_files = []
        self.video_info = {}
        self.file_list.clear()
        item = QListWidgetItem(f"🎙 {os.path.basename(audio_path)}  (audio — captioning only)")
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self.file_list.addItem(item)

        if not self.output_folder:
            self._set_output_folder(os.path.join(self.video_folder, "captured_frames"))
        os.makedirs(self.output_folder, exist_ok=True)

        self.load_video(synthetic_path)
        self.status_label.setText(
            f"🎙 Loaded “{os.path.basename(audio_path)}” for captioning — "
            "there's no picture, just the audio. Use Auto Caption, then Cap Export to save the .srt."
        )

    def _probe_video(self, path):
        cap = cv2.VideoCapture(path)
        w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        fc  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = fc / fps if fps else 0
        # Read one frame to check actual channel count — the only reliable way
        has_alpha = False
        ret, frame = cap.read()
        if ret and frame is not None and frame.ndim == 3:
            has_alpha = frame.shape[2] == 4
        cap.release()
        return {"width": w, "height": h, "fps": fps, "duration": dur,
                "frames": fc, "has_alpha": has_alpha}

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

        dur = self.total_frames / self.fps
        self.frame_slider.setMaximum(max(0, self.total_frames - 1))
        self.frame_slider.setValue(0)
        self.jump_spin.blockSignals(True)
        self.jump_spin.setMaximum(max(0.01, dur))
        self.jump_spin.setValue(0.0)
        self.jump_spin.blockSignals(False)
        self.btn_play.setEnabled(True)

        self.trim_in_sec = 0.0
        self.trim_out_sec = dur
        self.in_spin.blockSignals(True)
        self.out_spin.blockSignals(True)
        self.in_spin.setMaximum(max(0.01, dur))
        self.out_spin.setMaximum(max(0.01, dur))
        self.in_spin.setValue(0.0)
        self.out_spin.setValue(round(dur, 2))
        self.in_spin.blockSignals(False)
        self.out_spin.blockSignals(False)

        self.markup_start_spin.blockSignals(True)
        self.markup_end_spin.blockSignals(True)
        self.markup_start_spin.setMaximum(max(0.01, dur))
        self.markup_end_spin.setMaximum(max(0.01, dur))
        self.markup_start_spin.setValue(0.0)
        self.markup_end_spin.setValue(min(round(dur, 2), 3.0))
        self.markup_start_spin.blockSignals(False)
        self.markup_end_spin.blockSignals(False)

        self.holds = []
        self.selected_hold_id = None
        self._hold_wait = None
        self.preview_label.video_duration = dur
        self.preview_label.active_hold = None
        self.detected_cuts = []
        self.chapter_titles = {}
        self.cuts_list.clear()
        self.caption_cues = []
        self.caption_list.clear()

        # Detect alpha by reading first frame
        ret, test = self.current_cap.read()
        self._has_alpha = bool(ret and test is not None
                               and test.ndim == 3 and test.shape[2] == 4)
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        alpha_tag = "  ·  α ALPHA" if self._has_alpha else ""
        self.info_bar.setText(
            f"{os.path.basename(path)}  ·  {w}×{h}  ·  "
            f"{self.fps:.2f} fps  ·  {self.total_frames} frames  ·  {dur:.2f}s{alpha_tag}")

        self.show_frame(0)
        self._update_timeline_feedback()
        self.status_label.setText(f"Loaded: {os.path.basename(path)}")
        if self.auto_cap_on_load_chk.isChecked():
            QTimer.singleShot(150, self.auto_transcribe_captions)

    def _active_caption_at(self, sec):
        if not self.burn_captions_cb.isChecked():
            return ""
        for cue in self.caption_cues:
            if cue.start <= sec <= cue.end:
                return cue.text
        return ""

    def _on_burn_captions_toggled(self, _checked):
        if self.current_cap:
            self.show_frame(self.frame_slider.value())

    def _on_caption_position_changed(self, _idx):
        self.preview_label.caption_position = self.caption_position_combo.currentData() or "bottom"
        if self.current_cap:
            self.show_frame(self.frame_slider.value())

    def _sync_caption_highlight(self, sec):
        """Highlight + auto-scroll to whichever caption is active right now, in both
        the small sidebar list and the pull-out drawer (if open) — purely visual,
        never calls setCurrentRow so it can't itself trigger a seek while playing."""
        active_idx = -1
        for i, cue in enumerate(self.caption_cues):
            if cue.start <= sec <= cue.end:
                active_idx = i
                break
        if active_idx == self._last_caption_highlight_idx:
            return
        self._last_caption_highlight_idx = active_idx
        for lw in (self.caption_list, self._caption_dialog_list):
            if lw is None:
                continue
            for i in range(lw.count()):
                item = lw.item(i)
                if i == active_idx:
                    item.setBackground(QColor("#2a6ada"))
                    item.setForeground(QColor("#ffffff"))
                else:
                    item.setBackground(QColor(0, 0, 0, 0))
                    item.setForeground(QColor("#cccccc"))
            if active_idx >= 0:
                lw.scrollToItem(lw.item(active_idx))

    def show_frame(self, frame_num):
        if not self.current_cap:
            return
        frame_num = max(0, min(frame_num, self.total_frames - 1))
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.current_cap.read()
        if not ret:
            return

        h, w = frame.shape[:2]
        if frame.ndim == 3 and frame.shape[2] == 4:
            # BGRA — preserve alpha in preview (shown over dark background)
            rgba   = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
            qt_img = QImage(rgba.data, w, h, int(rgba.strides[0]), QImage.Format_RGBA8888).copy()
        else:
            rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            qt_img = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format_RGB888).copy()

        cur_sec   = frame_num / self.fps
        total_sec = self.total_frames / self.fps
        self.preview_label.set_base_qimage(
            qt_img,
            caption_text=self._active_caption_at(cur_sec),
            current_sec=cur_sec,
        )
        self._sync_caption_highlight(cur_sec)
        if not self.jump_spin.hasFocus():
            self.jump_spin.blockSignals(True)
            self.jump_spin.setValue(round(cur_sec, 2))
            self.jump_spin.blockSignals(False)
        self.time_label.setText(
            f"{self.fmt_time(cur_sec)} / {self.fmt_time(total_sec)}")
        self.frame_label.setText(f"Frame: {frame_num} / {self.total_frames - 1}")
        self._update_timeline_feedback()

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
        frame = int(round(self.jump_spin.value() * self.fps))
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
        elif event.key() == Qt.Key_J:
            self.shuttle_reverse()
        elif event.key() == Qt.Key_K:
            self.shuttle_pause()
        elif event.key() == Qt.Key_L:
            self.shuttle_forward()
        else:
            super().keyPressEvent(event)

    # ── Playback & JKL Shuttle (-16x to +16x) ────────────────────────────────

    def toggle_play(self):
        if self._playing:
            self._stop_playback()
        else:
            self._playback_rate = 1.0
            self._start_playback()

    def shuttle_reverse(self):
        """J key: play in reverse or double reverse shuttle speed (-1x, -2x, -4x, -8x, -16x)."""
        if not self.current_cap:
            return
        if not self._playing or self._playback_rate >= 0:
            self._playback_rate = -1.0
        elif self._playback_rate > -2.0:
            self._playback_rate = -2.0
        elif self._playback_rate > -4.0:
            self._playback_rate = -4.0
        elif self._playback_rate > -8.0:
            self._playback_rate = -8.0
        else:
            self._playback_rate = -16.0
        self._start_playback()
        self.status_label.setText(f"⏪  J Shuttle Reverse ({self._playback_rate:g}x)  ·  Press K/Space to pause, L for forward")

    def shuttle_pause(self):
        """K key: pause playback immediately."""
        self._stop_playback()
        self.status_label.setText("⏸  K Paused  ·  Press J for reverse (up to -16x), L for forward (up to 16x)")

    def shuttle_forward(self):
        """L key: play forward or double forward shuttle speed (1x, 2x, 4x, 8x, 16x)."""
        if not self.current_cap:
            return
        if not self._playing or self._playback_rate <= 0:
            self._playback_rate = 1.0
        elif self._playback_rate < 2.0:
            self._playback_rate = 2.0
        elif self._playback_rate < 4.0:
            self._playback_rate = 4.0
        elif self._playback_rate < 8.0:
            self._playback_rate = 8.0
        else:
            self._playback_rate = 16.0
        self._start_playback()
        self.status_label.setText(f"⏩  L Shuttle Forward ({self._playback_rate:g}x)  ·  Press K/Space to pause, J for reverse")

    def _start_playback(self):
        if not self.current_cap:
            return
        self._playing = True
        rate = self._playback_rate or 1.0
        if rate < 0:
            self.btn_play.setText(f"⏪ {rate:g}x")
        elif rate > 1.0:
            self.btn_play.setText(f"⏩ {rate:g}x")
        else:
            self.btn_play.setText("⏸  Pause")
        self._play_timer.start(max(1, int(1000 / self.fps)))
        self._sync_preview_audio()
        self._update_timeline_feedback()

    def _stop_playback(self):
        self._playing = False
        self._playback_rate = 1.0
        self._hold_wait = None
        self.preview_label.active_hold = None
        self.timeline_bar.set_active_hold_progress(None, 0.0)
        self._play_timer.stop()
        self._preview_audio.pause()
        self.btn_play.setText("▶  Play")
        self._update_timeline_feedback()

    def _sync_preview_audio(self):
        """Keep the live audio monitor (original + dub, mixed the same way Export
        would) in step with the video scrubber. Only engages at normal 1x forward
        speed and only within the current [In, Out] trim range — J/K/L shuttle and
        anything outside the clip bounds just plays silently, same as before.
        """
        if self._playback_rate != 1.0:
            self._preview_audio.pause()
            return
        t_in = self.in_spin.value()
        t_out = self.out_spin.value()
        cur_sec = (self.frame_slider.value() / self.fps) if self.fps else 0.0
        if cur_sec < t_in or cur_sec >= t_out:
            self._preview_audio.pause()
            return

        sig = (
            self.current_video_path, round(t_in, 2), round(t_out, 2),
            self.narration_path, self.narr_mode_combo.currentData(),
            tuple((h["id"], h["at"], h["duration"]) for h in self.holds if t_in <= h["at"] <= t_out),
        )
        if sig != self._preview_audio_sig:
            self._rebuild_preview_audio(sig, t_in, t_out)
        if self._preview_audio_path is None:
            return
        self._preview_audio.setPosition(max(0, int(round((cur_sec - t_in) * 1000))))
        self._preview_audio.play()

    def _rebuild_preview_audio(self, sig, t_in, t_out):
        ffmpeg = _find_ffmpeg()
        self._preview_audio_sig = sig
        if not ffmpeg or not self.current_video_path:
            self._preview_audio_path = None
            return
        out_path = os.path.join(tempfile.gettempdir(), f"vfc_preview_audio_{os.getpid()}.m4a")
        try:
            result = build_preview_audio(
                ffmpeg, self.current_video_path, out_path, t_in, t_out,
                narration_path=self.narration_path,
                narration_mode=self.narr_mode_combo.currentData(),
                holds=self.holds,
            )
        except Exception:
            result = None
        self._preview_audio_path = result
        if result:
            self._preview_audio.setMedia(QMediaContent(QUrl.fromLocalFile(result)))

    def _advance_frame(self):
        cur = self.frame_slider.value()
        step = int(round(self._playback_rate)) or 1

        if self._hold_wait is not None:
            h = self._hold_wait["hold"]
            dur = max(0.01, h["duration"])
            elapsed = time.monotonic() - self._hold_wait["start"]
            if elapsed < dur:
                self.timeline_bar.set_active_hold_progress(h["id"], elapsed / dur)
                self.preview_label.active_hold = (h, max(0.0, dur - elapsed))
                self.preview_label.refresh_display()
                return
            self.timeline_bar.set_active_hold_progress(None, 0.0)
            self.preview_label.active_hold = None
            self.preview_label.refresh_display()
            self._hold_wait = None
        else:
            for h in sorted(self.holds, key=lambda x: x["at"]):
                freeze_frame = int(round(h["at"] * self.fps))
                if min(cur, cur + step) <= freeze_frame <= max(cur, cur + step):
                    if cur != freeze_frame:
                        self.frame_slider.blockSignals(True)
                        self.frame_slider.setValue(freeze_frame)
                        self.frame_slider.blockSignals(False)
                        self.show_frame(freeze_frame)
                    self._hold_wait = {"hold": h, "start": time.monotonic()}
                    self.timeline_bar.set_active_hold_progress(h["id"], 0.0)
                    self.preview_label.active_hold = (h, h["duration"])
                    self.preview_label.refresh_display()
                    self.status_label.setText(
                        f"❄️ Holding at {h['at']:.2f}s for {h['duration']:.1f}s — place markups now"
                    )
                    return
            cur = self.frame_slider.value()

        # When the dub/narration audio monitor is actually playing, it's the
        # only hardware-accurate clock in the room — the QTimer driving this
        # method is just a software tick count and drifts from real audio
        # output over time (decode/repaint jitter never gets corrected). Slave
        # the displayed frame to the audio's live position instead of blindly
        # stepping by 1 each tick, so lipsync can't accumulate drift.
        nxt = cur + step
        if (
            self._playback_rate == 1.0
            and self._preview_audio_path is not None
            and self._preview_audio.state() == QMediaPlayer.PlayingState
        ):
            audio_sec = self.in_spin.value() + self._preview_audio.position() / 1000.0
            audio_frame = int(round(audio_sec * self.fps))
            if audio_frame == cur:
                return  # audio hasn't advanced far enough yet for a new frame
            nxt = audio_frame

        if nxt >= self.total_frames - 1:
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(self.total_frames - 1)
            self.frame_slider.blockSignals(False)
            self.show_frame(self.total_frames - 1)
            self._stop_playback()
            return
        if nxt <= 0:
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            self.show_frame(0)
            self._stop_playback()
            return
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(nxt)
        self.frame_slider.blockSignals(False)
        self.show_frame(nxt)

    # ── Output format & Per-Video Still Subfolder ─────────────────────────────

    def _frames_dir_for(self, video_path):
        """Return and create `<output_folder>/<video_stem>_frames/` so still images stay organized."""
        base_out = self.output_folder or os.path.join(os.path.dirname(video_path), "captured_frames")
        stem = os.path.splitext(os.path.basename(video_path))[0]
        frames_dir = os.path.join(base_out, f"{stem}_frames")
        os.makedirs(frames_dir, exist_ok=True)
        return frames_dir

    def _on_format_changed(self, fmt):
        is_jpeg = fmt == "JPEG"
        self.quality_label.setVisible(is_jpeg)
        self.quality_slider.setVisible(is_jpeg)
        self.quality_val_label.setVisible(is_jpeg)

    def _save_frame(self, frame, out_path_no_ext):
        fmt       = self.fmt_combo.currentText()
        has_alpha = frame.ndim == 3 and frame.shape[2] == 4

        if fmt == "PNG":
            # PNG supports alpha — save BGRA directly
            path = out_path_no_ext + ".png"
            cv2.imwrite(path, frame)
        elif fmt == "TIFF":
            # TIFF supports alpha — save BGRA directly
            path = out_path_no_ext + ".tif"
            cv2.imwrite(path, frame)
        elif fmt == "JPEG":
            # JPEG has no alpha — composite onto white before saving
            quality = self.quality_slider.value()
            path    = out_path_no_ext + ".jpg"
            save_frame = self._composite_on_white(frame) if has_alpha else frame
            cv2.imwrite(path, save_frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return path

    @staticmethod
    def _composite_on_white(bgra):
        """Flatten BGRA onto a white background — used for JPEG export."""
        b, g, r, a = cv2.split(bgra)
        alpha = a.astype(np.float32) / 255.0
        def blend(ch):
            return (ch * alpha + 255 * (1.0 - alpha)).clip(0, 255).astype(np.uint8)
        return cv2.merge([blend(b), blend(g), blend(r)])

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

    # ── Capture (Organized into <output_folder>/<stem>_frames/) ───────────────

    def capture_single(self):
        if not self.current_cap or not self.current_video_path:
            return
        frame_num = self.frame_slider.value()
        self.current_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self.current_cap.read()
        if not ret:
            self.status_label.setText("Error: could not read frame")
            return

        frames_dir = self._frames_dir_for(self.current_video_path)
        stem = os.path.splitext(os.path.basename(self.current_video_path))[0]
        sec  = frame_num / self.fps
        visible_anns = self.preview_label.visible_annotations_at(sec)
        if visible_anns or self._active_caption_at(sec):
            frame = render_annotations_on_bgr(
                frame,
                visible_anns,
                caption_text=self._active_caption_at(sec),
                caption_position=self.preview_label.caption_position,
            )
        base = os.path.join(frames_dir, f"{stem}_frame{frame_num}_{sec:.2f}s")
        path = self._save_frame(frame, base)

        self.status_label.setText(f"✅  Saved: {os.path.basename(frames_dir)}/{os.path.basename(path)}")
        self._flash_capture()
        self._refresh_contact_sheet()

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
            f"Output → {self.output_folder}/<video>_frames/",
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
            frames_dir = self._frames_dir_for(filepath)

            for tc in timecodes:
                tgt = max(0, min(int(tc * fps), total - 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, tgt)
                ret, frame = cap.read()
                op += 1
                if not ret:
                    errors.append(f"{filename} @ {tc:.2f}s: could not read frame")
                else:
                    base = os.path.join(frames_dir, f"{stem}_{tc:.2f}s")
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
        self._refresh_contact_sheet()
        QMessageBox.information(self, "Batch Complete", msg)

    # ── Interval capture (respects [In → Out] for current video & saves to <stem>_frames/) ──

    def capture_interval_single(self):
        if not self.current_cap or not self.current_video_path:
            self.status_label.setText("No video loaded.")
            return
        self._run_interval_capture([self.current_video_path])

    def capture_interval_batch(self):
        if not self.video_folder or not self.video_files:
            QMessageBox.warning(self, "No Videos",
                                "Please select a folder with videos first.")
            return
        self._run_interval_capture(
            [os.path.join(self.video_folder, f) for f in self.video_files])

    def _run_interval_capture(self, paths):
        interval = self.interval_spin.value()
        os.makedirs(self.output_folder, exist_ok=True)
        single_active = (len(paths) == 1 and paths[0] == self.current_video_path)

        # Fast metadata pass to size the progress bar
        total_ops = 0
        for path in paths:
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            dur = total_f / fps if fps else 0
            s_sec = self.in_spin.value() if single_active else 0.0
            e_sec = min(dur, self.out_spin.value()) if (single_active and self.out_spin.value() > s_sec) else dur
            total_ops += int(max(0.0, e_sec - s_sec) / interval) + 1
            cap.release()

        self.progress.setVisible(True)
        self.progress.setMaximum(max(1, total_ops))
        self.progress.setValue(0)

        success, errors, op = 0, [], 0
        for path in paths:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                errors.append(f"{os.path.basename(path)}: could not open")
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            dur = total_f / fps
            stem = os.path.splitext(os.path.basename(path))[0]
            frames_dir = self._frames_dir_for(path)
            s_sec = self.in_spin.value() if single_active else 0.0
            e_sec = min(dur, self.out_spin.value()) if (single_active and self.out_spin.value() > s_sec) else dur
            times, t = [], s_sec
            while t <= e_sec + 0.001:
                times.append(t)
                t += interval
            frame_nums = [min(int(round(t * fps)), total_f - 1) for t in times]
            for t, (_n, ret, frame) in zip(times, _read_frames(cap, frame_nums)):
                op += 1
                if ret:
                    base = os.path.join(frames_dir, f"{stem}_{t:.1f}s")
                    self._save_frame(frame, base)
                    success += 1
                else:
                    errors.append(f"{os.path.basename(path)} @ {t:.1f}s")
                self.progress.setValue(op)
                QApplication.processEvents()
            cap.release()

        self.progress.setVisible(False)
        msg = f"✅  Captured {success} frames from {len(paths)} video(s)"
        if errors:
            msg += f"\n\n⚠️ Errors ({len(errors)}):\n" + "\n".join(errors[:10])
        self.status_label.setText(msg.split("\n")[0])
        self._refresh_contact_sheet()
        if len(paths) > 1 or errors:
            QMessageBox.information(self, "Interval Capture Complete", msg)

    # ── Scene capture (respects [In → Out] for current video & saves to <stem>_frames/) ──

    def capture_scenes_single(self):
        if not self.current_cap or not self.current_video_path:
            self.status_label.setText("No video loaded.")
            return
        self._run_scene_capture([self.current_video_path])

    def capture_scenes_batch(self):
        if not self.video_folder or not self.video_files:
            QMessageBox.warning(self, "No Videos",
                                "Please select a folder with videos first.")
            return
        self._run_scene_capture(
            [os.path.join(self.video_folder, f) for f in self.video_files])

    def _run_scene_capture(self, paths):
        if not _find_ffmpeg():
            QMessageBox.warning(self, "FFmpeg Not Found",
                                "Scene detection requires FFmpeg.\n"
                                "Install via: brew install ffmpeg")
            return
        threshold = self.scene_threshold_slider.value() / 100.0
        os.makedirs(self.output_folder, exist_ok=True)
        single_active = (len(paths) == 1 and paths[0] == self.current_video_path)

        self.progress.setVisible(True)
        self.progress.setMaximum(len(paths))
        self.progress.setValue(0)

        success, errors = 0, []
        for i, path in enumerate(paths):
            stem = os.path.splitext(os.path.basename(path))[0]
            frames_dir = self._frames_dir_for(path)
            self.status_label.setText(f"Scanning scenes: {os.path.basename(path)}…")
            QApplication.processEvents()

            s_sec = self.in_spin.value() if single_active else 0.0
            e_sec = self.out_spin.value() if (single_active and self.out_spin.value() > s_sec) else None
            cuts = _detect_scene_cuts(path, threshold, start_sec=s_sec, end_sec=e_sec)
            if cuts is None:
                errors.append(f"{os.path.basename(path)}: ffmpeg error")
                self.progress.setValue(i + 1)
                continue

            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                errors.append(f"{os.path.basename(path)}: could not open")
                self.progress.setValue(i + 1)
                continue

            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            dur = total_f / fps
            end_bound = min(dur, e_sec) if e_sec is not None else dur

            boundaries = sorted({s_sec} | set(cuts) | {end_bound})
            scenes = [
                (boundaries[j], boundaries[j + 1])
                for j in range(len(boundaries) - 1)
                if boundaries[j + 1] - boundaries[j] > 0.1
            ]

            frame_nums = [min(int(round((start + (end - start) / 2) * fps)), total_f - 1)
                          for start, end in scenes]
            for idx, ((start, end), (_n, ret, frame)) in enumerate(
                    zip(scenes, _read_frames(cap, frame_nums))):
                if ret:
                    base = os.path.join(
                        frames_dir,
                        f"{stem}_scene{idx + 1:03d}_{start:.2f}s")
                    self._save_frame(frame, base)
                    success += 1
                else:
                    errors.append(f"{os.path.basename(path)} scene {idx + 1}")

            cap.release()
            self.progress.setValue(i + 1)
            QApplication.processEvents()

        self.progress.setVisible(False)
        msg = f"✅  Captured {success} scene frame(s) from {len(paths)} video(s)"
        if errors:
            msg += f"\n\n⚠️ Errors ({len(errors)}):\n" + "\n".join(errors[:10])
        self.status_label.setText(msg.split("\n")[0])
        self._refresh_contact_sheet()
        QMessageBox.information(self, "Scene Capture Complete", msg)

    # ── Edit, Markup, Freeze-Frame, Trim, GIF, Cut Splitter, Narration & Captions ──

    def copy_frame_to_clipboard(self):
        if self.preview_label._base_qimage.isNull():
            return
        sec = self.frame_slider.value() / self.fps if self.fps else 0.0
        img = render_annotations_on_qimage(
            self.preview_label._base_qimage,
            self.preview_label.visible_annotations_at(sec),
            caption_text=self._active_caption_at(sec),
            caption_position=self.preview_label.caption_position,
        )
        QApplication.clipboard().setImage(img)
        self.status_label.setText("📋  Copied current frame (with markup/caption) to clipboard")
        self._flash_capture()

    def _on_markup_duration_changed(self, value):
        self.preview_label.default_duration = float(value)
        if self.preview_label.annotations:
            last = self.preview_label.annotations[-1]
            if value <= 0:
                last.start_sec = 0.0
                last.end_sec = 999999.0
            else:
                dur = self.total_frames / self.fps if self.fps else 999999.0
                last.end_sec = round(min(dur, last.start_sec + float(value)), 2)
            self.preview_label.refresh_display()
        self._update_timeline_feedback()

    def _on_markup_spin_edited(self, _value=None):
        """User typed directly into the Markup Start / End spinboxes in the Timing Strip."""
        if not self.preview_label.annotations:
            return
        last = self.preview_label.annotations[-1]
        s_val = round(self.markup_start_spin.value(), 2)
        e_val = round(self.markup_end_spin.value(), 2)
        if e_val <= s_val:
            e_val = round(s_val + 0.5, 2)
            self.markup_end_spin.blockSignals(True)
            self.markup_end_spin.setValue(e_val)
            self.markup_end_spin.blockSignals(False)
        last.start_sec = s_val
        last.end_sec = e_val
        self.preview_label.refresh_display()
        self._update_timeline_feedback()
        self.status_label.setText(f"🖍  Markup timing updated: {s_val:.2f}s → {e_val:.2f}s")

    def _open_hold_popover(self):
        if not self.current_cap:
            return
        pop = HoldDurationPopover(self, initial=3.0)
        at_sec = round(self.frame_slider.value() / self.fps, 2) if self.fps else 0.0
        pop.accepted.connect(lambda dur, sec=at_sec: self._add_hold(sec, dur))
        pop.move(self.btn_freeze_here.mapToGlobal(self.btn_freeze_here.rect().bottomLeft()))
        pop.show()

    def _add_hold(self, at_sec, duration):
        self._hold_id_counter += 1
        self.holds.append({"id": self._hold_id_counter, "at": round(float(at_sec), 2), "duration": round(float(duration), 2)})
        self._update_timeline_feedback()
        total = (self.total_frames / self.fps if self.fps else 0.0) + sum(h["duration"] for h in self.holds)
        self.status_label.setText(
            f"❄️ Hold added: +{duration:.2f}s at {at_sec:.2f}s — timeline is now {total:.2f}s long"
        )

    # ── Interactive Timeline Map Bar drag handlers ───────────────────────────

    def _on_timeline_drag_in(self, sec):
        if not self.current_cap:
            return
        self.in_spin.setValue(round(float(sec), 2))
        self.status_label.setText(f"[ Trim In dragged to {self.in_spin.value():.2f}s → Out {self.out_spin.value():.2f}s")

    def _on_timeline_drag_out(self, sec):
        if not self.current_cap:
            return
        self.out_spin.setValue(round(float(sec), 2))
        self.status_label.setText(f"Trim Out ] dragged: {self.in_spin.value():.2f}s → {self.out_spin.value():.2f}s")

    def _on_timeline_scrub(self, sec):
        if not self.current_cap:
            return
        frame = max(0, min(int(round(float(sec) * self.fps)), self.total_frames - 1))
        self.frame_slider.setValue(frame)

    def _on_playhead_drag_started(self):
        # Playback's own timer was fighting the drag — every tick it would
        # re-snap the frame (and, since the lipsync fix, to the audio clock
        # too), so a manual drag never visibly took hold while playing. Pause
        # for the duration of the drag and resume from wherever it's dropped.
        self._was_playing_before_scrub = self._playing
        if self._playing:
            self._stop_playback()

    def _on_playhead_drag_finished(self):
        if getattr(self, "_was_playing_before_scrub", False):
            self._start_playback()
        self._was_playing_before_scrub = False

    def _on_timeline_markup_span(self, idx, start_sec, end_sec):
        anns = self.preview_label.annotations
        if not (0 <= idx < len(anns)):
            return
        ann = anns[idx]
        ann.start_sec = round(float(start_sec), 2)
        ann.end_sec = round(float(end_sec), 2)
        self.preview_label.refresh_display()
        self._update_timeline_feedback()

    def _on_timeline_markup_selected(self, idx):
        anns = self.preview_label.annotations
        if not (0 <= idx < len(anns)):
            return
        self.selected_hold_id = None
        self.preview_label.selected_idx = idx
        self.preview_label.selection_changed.emit()
        self.preview_label.refresh_display()

    def _on_timeline_hold_selected(self, hold_id):
        self.selected_hold_id = hold_id
        if self.preview_label.selected_idx != -1:
            self.preview_label.selected_idx = -1
            self.preview_label.selection_changed.emit()
            self.preview_label.refresh_display()
        h = next((x for x in self.holds if x["id"] == hold_id), None)
        if h:
            self.status_label.setText(
                f"Selected hold: {h['at']:.2f}s +{h['duration']:.1f}s — drag to move, Delete to remove"
            )

    def _on_timeline_hold_moved(self, hold_id, new_at):
        if not self.current_cap:
            return
        for h in self.holds:
            if h["id"] == hold_id:
                h["at"] = round(float(new_at), 2)
                break
        self._update_timeline_feedback()

    def _on_markup_thickness_changed(self, v):
        self.preview_label.thickness = v
        ann = self.preview_label.selected_annotation()
        if ann is not None:
            ann.thickness = v
            self.preview_label.refresh_display()

    def _on_markup_color_changed(self):
        hexv = self.color_combo.currentData()
        self.preview_label.color = hexv
        ann = self.preview_label.selected_annotation()
        if ann is not None:
            ann.color = hexv
            self.preview_label.refresh_display()

    def _on_markup_font_size_changed(self, v):
        ann = self.preview_label.selected_annotation()
        if ann is not None and ann.kind == "text":
            ann.font_size = v
            self.preview_label.refresh_display()

    def _on_markup_selection_changed(self):
        ann = self.preview_label.selected_annotation()
        if ann is None:
            return
        self.selected_hold_id = None
        self.thickness_spin.blockSignals(True)
        self.thickness_spin.setValue(ann.thickness)
        self.thickness_spin.blockSignals(False)
        idx = self.color_combo.findData(ann.color)
        if idx >= 0:
            self.color_combo.blockSignals(True)
            self.color_combo.setCurrentIndex(idx)
            self.color_combo.blockSignals(False)
        if ann.kind == "text":
            self.markup_font_size_spin.blockSignals(True)
            self.markup_font_size_spin.setValue(ann.font_size)
            self.markup_font_size_spin.blockSignals(False)
        self.status_label.setText(
            f"Selected markup: {ann.label()}  ·  drag its handles to resize/move, or Delete to remove it"
        )

    def _delete_selected_markup(self):
        if self.selected_hold_id is not None:
            self.holds = [h for h in self.holds if h["id"] != self.selected_hold_id]
            self.selected_hold_id = None
            self.timeline_bar.set_active_hold_progress(None, 0.0)
            self._update_timeline_feedback()
            self.status_label.setText("🗑  Hold deleted")
            return
        if self.preview_label.delete_selected():
            self.status_label.setText("🗑  Markup deleted")
            self._update_timeline_feedback()
        else:
            self.status_label.setText("Click a markup or hold in the preview/timeline first, then Delete")

    def set_last_markup_in(self):
        if not self.current_cap or not self.preview_label.annotations:
            self.status_label.setText("Draw an arrow, circle, box, or title first, then set its In/Out times.")
            return
        sec = round(self.frame_slider.value() / self.fps, 2)
        last = self.preview_label.annotations[-1]
        last.start_sec = sec
        if last.end_sec <= sec:
            dur = self.total_frames / self.fps if self.fps else sec + 3.0
            span = max(0.5, self.markup_dur_spin.value() or 3.0)
            last.end_sec = round(min(dur, sec + span), 2)
        self.preview_label.refresh_display()
        self._update_timeline_feedback()
        self.status_label.setText(
            f"🖍  Markup Start set: {last.start_sec:.2f}s → {min(self.total_frames / self.fps, last.end_sec):.2f}s"
        )

    def set_last_markup_out(self):
        if not self.current_cap or not self.preview_label.annotations:
            self.status_label.setText("Draw an arrow, circle, box, or title first, then set its In/Out times.")
            return
        sec = round(self.frame_slider.value() / self.fps, 2)
        last = self.preview_label.annotations[-1]
        if sec <= last.start_sec:
            last.start_sec = max(0.0, round(sec - 0.5, 2))
        last.end_sec = sec
        self.preview_label.refresh_display()
        self._update_timeline_feedback()
        self.status_label.setText(
            f"🖍  Markup End set: {last.start_sec:.2f}s → {last.end_sec:.2f}s"
        )

    def _on_trim_spin_changed(self, _value=None):
        self.trim_in_sec = self.in_spin.value()
        self.trim_out_sec = self.out_spin.value()
        self._update_timeline_feedback()

    def _update_timeline_feedback(self):
        if not hasattr(self, "timeline_bar") or not hasattr(self, "feedback_banner"):
            return
        dur = (self.total_frames / self.fps) if (self.current_cap and self.fps) else 0.0
        cur_sec = (self.frame_slider.value() / self.fps) if (self.current_cap and self.fps) else 0.0
        t_in = self.in_spin.value() if hasattr(self, "in_spin") else 0.0
        t_out = self.out_spin.value() if hasattr(self, "out_spin") else dur
        self.timeline_bar.update_state(
            duration=dur,
            current_sec=cur_sec,
            trim_in=t_in,
            trim_out=t_out,
            annotations=self.preview_label.annotations,
            holds=self.holds,
            cuts=self.detected_cuts,
        )
        parts = [f"Clip [{t_in:.2f}s → {t_out:.2f}s]"]
        if self.preview_label.annotations:
            last = self.preview_label.annotations[-1]
            vis_count = len(self.preview_label.visible_annotations_at(cur_sec))
            end_disp = min(dur, last.end_sec) if dur > 0 else last.end_sec
            if hasattr(self, "markup_start_spin") and not self.markup_start_spin.hasFocus():
                self.markup_start_spin.blockSignals(True)
                self.markup_start_spin.setValue(round(last.start_sec, 2))
                self.markup_start_spin.blockSignals(False)
            if hasattr(self, "markup_end_spin") and not self.markup_end_spin.hasFocus():
                self.markup_end_spin.blockSignals(True)
                self.markup_end_spin.setValue(round(end_disp, 2))
                self.markup_end_spin.blockSignals(False)
            if last.end_sec >= 99999.0 and last.start_sec <= 0.01:
                parts.append(f"🖍 Markup ({len(self.preview_label.annotations)}): All Clip [0.00s → {dur:.2f}s]")
            else:
                state = "ON SCREEN" if last.is_visible_at(cur_sec) else "scheduled"
                parts.append(
                    f"🖍 Markup ({vis_count}/{len(self.preview_label.annotations)} {state}): "
                    f"[{last.start_sec:.2f}s → {end_disp:.2f}s]"
                )
        else:
            parts.append("🖍 Markup: None")

        if self.holds:
            total_extra = sum(h["duration"] for h in self.holds)
            parts.append(f"❄️ Holds ({len(self.holds)}): +{total_extra:.1f}s total — timeline is {dur + total_extra:.2f}s")
        else:
            parts.append("❄️ Hold: None")

        if self._playing:
            rate = self._playback_rate or 1.0
            dir_lbl = "⏪ Rev" if rate < 0 else "⏩ Fwd"
            parts.append(f"{dir_lbl} {rate:g}x (J/K/L)")
        else:
            parts.append("J/K/L (up to 16x)")
        self.feedback_banner.setText("   ·   ".join(parts))

    def set_trim_in(self):
        if not self.current_cap:
            return
        sec = round(self.frame_slider.value() / self.fps, 2)
        self.in_spin.setValue(sec)
        if self.out_spin.value() <= sec:
            self.out_spin.setValue(round(self.total_frames / self.fps, 2))
        self._update_timeline_feedback()
        self.status_label.setText(f"[ Trim In set to {sec:.2f}s → Out {self.out_spin.value():.2f}s")

    def set_trim_out(self):
        if not self.current_cap:
            return
        sec = round(self.frame_slider.value() / self.fps, 2)
        if sec <= self.in_spin.value():
            self.in_spin.setValue(0.0)
        self.out_spin.setValue(sec)
        self._update_timeline_feedback()
        self.status_label.setText(f"Trim Out ] set: {self.in_spin.value():.2f}s → {sec:.2f}s")

    def reset_trim_range(self):
        if not self.current_cap:
            return
        dur = round(self.total_frames / self.fps, 2)
        self.in_spin.setValue(0.0)
        self.out_spin.setValue(dur)
        self._update_timeline_feedback()
        self.status_label.setText(f"Trim reset to full video (0.00s – {dur:.2f}s)")

    def export_edited_clip(self, as_gif=False):
        if not self.current_cap or not self.current_video_path:
            QMessageBox.information(self, APP_NAME, "Load a video first.")
            return
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, APP_NAME, "FFmpeg is required for clip export.")
            return
        os.makedirs(self.output_folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.current_video_path))[0]
        s_sec = self.in_spin.value()
        e_sec = self.out_spin.value()
        mp4_q = self.mp4_quality_combo.currentData() or "original"
        q_suffix = "" if (as_gif or mp4_q == "original") else f"_{mp4_q}"
        ext = ".gif" if as_gif else ".mp4"
        out_path = os.path.join(
            self.output_folder, f"{stem}_edit_{s_sec:.2f}s-{e_sec:.2f}s{q_suffix}{ext}"
        )
        self.status_label.setText(f"Rendering {'GIF' if as_gif else f'MP4 ({mp4_q})'} → {os.path.basename(out_path)}…")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        QApplication.processEvents()
        try:
            export_video_or_gif(
                ffmpeg=ffmpeg,
                video_path=self.current_video_path,
                out_path=out_path,
                start_sec=s_sec,
                end_sec=e_sec,
                fade_in=self.fade_in_spin.value(),
                fade_out=self.fade_out_spin.value(),
                annotations=self.preview_label.annotations,
                holds=self.holds,
                captions=self.caption_cues if self.burn_captions_cb.isChecked() else [],
                caption_position=self.preview_label.caption_position,
                narration_path=self.narration_path,
                narration_mode=self.narr_mode_combo.currentData(),
                as_gif=as_gif,
                gif_fps=self.gif_fps_combo.currentData(),
                gif_width=self.gif_width_combo.currentData(),
                mp4_quality=mp4_q,
            )
            if not as_gif and self.detected_cuts:
                relevant_holds = [h for h in self.holds if s_sec <= h["at"] <= e_sec]
                shifted_cuts = []
                shifted_titles = {0.0: self.chapter_titles.get(0.0, "Chapter 1")}
                for idx, c in enumerate(self.detected_cuts, 1):
                    if s_sec + 0.05 < c < e_sec - 0.05:
                        extra = sum(h["duration"] for h in relevant_holds if h["at"] < c)
                        rel_c = round(c - s_sec + extra, 2)
                        shifted_cuts.append(rel_c)
                        shifted_titles[rel_c] = self._default_chapter_title_for(c, idx + 1)
                if shifted_cuts:
                    tmp_chap = out_path + ".chap.mp4"
                    export_mp4_with_chapters(ffmpeg, out_path, tmp_chap, shifted_cuts, chapter_titles=shifted_titles)
                    os.replace(tmp_chap, out_path)
        except Exception as exc:
            self.progress.setVisible(False)
            self.progress.setRange(0, 100)
            QMessageBox.warning(self, APP_NAME, f"Export failed:\n\n{exc}")
            return
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.status_label.setText(f"✅  Saved: {os.path.basename(out_path)}")

    def export_trimmed_audio(self):
        if not self.current_cap or not self.current_video_path:
            QMessageBox.information(self, APP_NAME, "Load a video first.")
            return
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, APP_NAME, "FFmpeg is required for audio export.")
            return
        os.makedirs(self.output_folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.current_video_path))[0]
        s_sec = self.in_spin.value()
        e_sec = self.out_spin.value()
        bitrate = self.mp3_bitrate_combo.currentData() if hasattr(self, "mp3_bitrate_combo") else "192k"
        out_path = os.path.join(self.output_folder, f"{stem}_{s_sec:.2f}s-{e_sec:.2f}s.mp3")
        try:
            export_audio_clip(
                ffmpeg, self.current_video_path, out_path,
                start_sec=s_sec, end_sec=e_sec,
                fade_in=self.fade_in_spin.value(),
                fade_out=self.fade_out_spin.value(),
                fmt="mp3",
                bitrate=bitrate,
            )
            self.status_label.setText(f"✅  Saved audio ({bitrate}): {os.path.basename(out_path)}")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Audio export failed:\n\n{exc}")

    # ── Adjustable Edit / Scene Cut Markers, MP4 Chapters & Segment Splitting ──

    def _default_chapter_title_for(self, sec, chapter_num):
        custom = self.chapter_titles.get(round(float(sec), 2))
        if custom and custom.strip():
            return custom.strip()
        # If captions exist near this cut point, use the first words as a smart default label
        for cue in self.caption_cues:
            if cue.start - 0.5 <= sec <= cue.end + 0.5 and cue.text.strip():
                words = cue.text.strip().split()
                snippet = " ".join(words[:5])
                return snippet
        return f"Chapter {chapter_num}"

    def _refresh_cuts_list(self):
        self.cuts_list.blockSignals(True)
        self.cuts_list.clear()
        for idx, sec in enumerate(self.detected_cuts, 1):
            chap_num = idx + 1  # Start of clip is Chapter 1, first cut starts Chapter 2
            title = self._default_chapter_title_for(sec, chap_num)
            item = QListWidgetItem(
                f"Ch {chap_num:02d} @ {sec:.2f}s — {title}  (Frame {int(round(sec * self.fps))})"
            )
            item.setData(Qt.UserRole, sec)
            self.cuts_list.addItem(item)
        self.cuts_list.blockSignals(False)
        self._update_timeline_feedback()

    def detect_cuts_for_current(self):
        if not self.current_cap or not self.current_video_path:
            QMessageBox.information(self, APP_NAME, "Load a video first.")
            return
        threshold = self.scene_threshold_slider.value() / 100.0
        s_sec = self.in_spin.value()
        e_sec = self.out_spin.value()
        self.status_label.setText(f"Detecting scene cuts / chapters within [{s_sec:.2f}s → {e_sec:.2f}s]…")
        QApplication.processEvents()
        cuts = _detect_scene_cuts(self.current_video_path, threshold, start_sec=s_sec, end_sec=e_sec)
        if cuts is None:
            QMessageBox.warning(self, APP_NAME, "Scene detection failed.")
            return
        self.detected_cuts = sorted(round(c, 2) for c in cuts)
        self._refresh_cuts_list()
        self.status_label.setText(
            f"Found {len(self.detected_cuts)} cut/chapter point(s) within [{s_sec:.2f}s → {e_sec:.2f}s] "
            f"({len(self.detected_cuts) + 1} chapters). Click '📑 Save MP4 w/ Chapters' or '✂️ Split into Clips'."
        )

    def _on_cut_selected(self, row):
        if 0 <= row < len(self.detected_cuts):
            sec = self.detected_cuts[row]
            frame = max(0, min(int(round(sec * self.fps)), self.total_frames - 1))
            self.frame_slider.setValue(frame)

    def jump_cut(self, direction):
        if not self.detected_cuts:
            self.detect_cuts_for_current()
            if not self.detected_cuts:
                return
        cur_sec = self.frame_slider.value() / self.fps
        if direction > 0:
            nxt = next((i for i, c in enumerate(self.detected_cuts) if c > cur_sec + 0.05), 0)
        else:
            prevs = [i for i, c in enumerate(self.detected_cuts) if c < cur_sec - 0.05]
            nxt = prevs[-1] if prevs else len(self.detected_cuts) - 1
        self.cuts_list.setCurrentRow(nxt)

    def add_cut_at_current(self):
        if not self.current_cap:
            return
        sec = round(self.frame_slider.value() / self.fps, 2)
        if all(abs(c - sec) > 0.02 for c in self.detected_cuts):
            self.detected_cuts.append(sec)
            self.detected_cuts.sort()
            self._refresh_cuts_list()
            self.cuts_list.setCurrentRow(self.detected_cuts.index(sec))
            self.status_label.setText(f"Added cut / chapter point at {sec:.2f}s")

    def move_selected_cut_to_current(self):
        row = self.cuts_list.currentRow()
        if row < 0 or row >= len(self.detected_cuts) or not self.current_cap:
            return
        old_sec = round(self.detected_cuts[row], 2)
        sec = round(self.frame_slider.value() / self.fps, 2)
        if old_sec in self.chapter_titles:
            self.chapter_titles[sec] = self.chapter_titles.pop(old_sec)
        self.detected_cuts[row] = sec
        self.detected_cuts.sort()
        self._refresh_cuts_list()
        self.cuts_list.setCurrentRow(self.detected_cuts.index(sec))
        self.status_label.setText(f"Adjusted cut / chapter point to {sec:.2f}s")

    def rename_selected_chapter(self):
        row = self.cuts_list.currentRow()
        if row < 0 or row >= len(self.detected_cuts):
            self.status_label.setText("Select a cut/chapter in the list first to name it.")
            return
        from PyQt5.QtWidgets import QInputDialog
        sec = round(self.detected_cuts[row], 2)
        chap_num = row + 2
        cur_title = self._default_chapter_title_for(sec, chap_num)
        text, ok = QInputDialog.getText(
            self,
            "Rename MP4 Chapter",
            f"Chapter {chap_num:02d} title starting at {sec:.2f}s:",
            text=cur_title,
        )
        if ok and text.strip():
            self.chapter_titles[sec] = text.strip()
            self._refresh_cuts_list()
            self.cuts_list.setCurrentRow(row)
            self.status_label.setText(f"Named Chapter {chap_num:02d} ({sec:.2f}s): \"{text.strip()}\"")

    def remove_selected_cut(self):
        row = self.cuts_list.currentRow()
        if 0 <= row < len(self.detected_cuts):
            removed = round(self.detected_cuts.pop(row), 2)
            self.chapter_titles.pop(removed, None)
            self._refresh_cuts_list()
            self.status_label.setText(f"Removed cut / chapter point at {removed:.2f}s")

    def export_current_video_with_chapters(self):
        if not self.current_cap or not self.current_video_path:
            QMessageBox.information(self, APP_NAME, "Load a video first.")
            return
        if not self.detected_cuts:
            self.detect_cuts_for_current()
        if not self.detected_cuts:
            QMessageBox.information(
                self, APP_NAME,
                "No scene cuts or chapter points found.\n"
                "Scrub to where you want chapters and click '+ Add Here', or lower the Scene Sensitivity slider.",
            )
            return
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            QMessageBox.warning(self, APP_NAME, "FFmpeg is required to embed MP4 chapters.")
            return
        os.makedirs(self.output_folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.current_video_path))[0]
        s_sec = self.in_spin.value()
        e_sec = self.out_spin.value()
        out_path = os.path.join(self.output_folder, f"{stem}_chapters.mp4")

        # Build chapter title map including any caption-derived defaults
        titles_map = {
            0.0: self.chapter_titles.get(0.0, "Chapter 1"),
            round(s_sec, 2): self.chapter_titles.get(round(s_sec, 2), "Chapter 1"),
        }
        for idx, sec in enumerate(self.detected_cuts, 1):
            titles_map[round(sec, 2)] = self._default_chapter_title_for(sec, idx + 1)

        self.status_label.setText(f"Embedding chapters into {os.path.basename(out_path)}…")
        QApplication.processEvents()
        try:
            saved_path, chapters = export_mp4_with_chapters(
                ffmpeg,
                self.current_video_path,
                out_path,
                self.detected_cuts,
                chapter_titles=titles_map,
                start_sec=s_sec,
                end_sec=e_sec,
            )
            summary = "\n".join(
                f"  • {idx:02d}. [{s:.2f}s – {e:.2f}s]  {t}"
                for idx, (s, e, t) in enumerate(chapters, 1)
            )
            self.status_label.setText(
                f"✅  Saved MP4 with {len(chapters)} QuickTime-ready chapters: {os.path.basename(saved_path)}"
            )
            QMessageBox.information(
                self,
                "MP4 Chapters Embedded",
                f"Saved MP4 with {len(chapters)} QuickTime / VLC / YouTube chapter markers:\n{saved_path}\n\nChapters:\n{summary}",
            )
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Failed to embed MP4 chapters:\n\n{exc}")

    def split_current_video_by_cuts(self):
        if not self.current_cap or not self.current_video_path:
            QMessageBox.information(self, APP_NAME, "Load a video first.")
            return
        if not self.detected_cuts:
            self.detect_cuts_for_current()
        if not self.detected_cuts:
            QMessageBox.information(self, APP_NAME, "No edit cuts detected or added to split on.")
            return
        ffmpeg = _find_ffmpeg()
        stem = os.path.splitext(os.path.basename(self.current_video_path))[0]
        seg_dir = os.path.join(self.output_folder, f"{stem}_segments")
        s_sec = self.in_spin.value()
        e_sec = self.out_spin.value()
        mp4_q = self.mp4_quality_combo.currentData() or "original"
        self.progress.setVisible(True)

        def on_prog(done, total, msg):
            self.progress.setMaximum(max(1, total))
            self.progress.setValue(done)
            self.status_label.setText(msg)
            QApplication.processEvents()

        try:
            files = split_video_by_cuts(
                ffmpeg,
                self.current_video_path,
                seg_dir,
                self.detected_cuts,
                fade_in=self.fade_in_spin.value(),
                fade_out=self.fade_out_spin.value(),
                progress_cb=on_prog,
                start_sec=s_sec,
                end_sec=e_sec,
                mp4_quality=mp4_q,
            )
            self.progress.setVisible(False)
            self.status_label.setText(f"✅  Split [{s_sec:.2f}s → {e_sec:.2f}s] into {len(files)} clips in {seg_dir}")
            QMessageBox.information(
                self, "Video Split Complete",
                f"Created {len(files)} segment MP4 clip(s) within [{s_sec:.2f}s → {e_sec:.2f}s] in:\n{seg_dir}",
            )
        except Exception as exc:
            self.progress.setVisible(False)
            QMessageBox.warning(self, APP_NAME, f"Failed to split video:\n\n{exc}")

    # ── Dub / Voiceover Recording & Captions (Offline Whisper + Manual + SRT) ──

    def _populate_mic_devices(self):
        self.mic_device_combo.clear()
        devices = list_avfoundation_microphones(_find_ffmpeg())
        if not devices:
            self.mic_device_combo.addItem("Default Mic", "0")
            return
        preferred_idx = 0
        for i, (dev_id, name) in enumerate(devices):
            self.mic_device_combo.addItem(name, dev_id)
            if "microphone" in name.lower():
                preferred_idx = i
        self.mic_device_combo.setCurrentIndex(preferred_idx)

    def toggle_mic_recording(self, recording):
        ffmpeg = _find_ffmpeg()
        if not recording:
            self._stop_playback()
            if self._mic_process is not None and self._mic_process.state() != QProcess.NotRunning:
                self._mic_process.write(b"q\n")
                self._mic_process.closeWriteChannel()
                self._mic_process.waitForFinished(3000)
            self.btn_rec_mic.setText("🎙 Dub Record")
            if self.narration_path and os.path.isfile(self.narration_path):
                self.narr_label.setText(f"Dub Track: {os.path.basename(self.narration_path)}  (Ready for MP4 export or '🔊 Play Dub')")
                self.status_label.setText(f"🎙 Saved dub voiceover: {os.path.basename(self.narration_path)}")
            return

        mic_idx = self.mic_device_combo.currentData() or "0"
        os.makedirs(self.output_folder or tempfile.gettempdir(), exist_ok=True)
        stem = os.path.splitext(os.path.basename(self.current_video_path or "clip"))[0]
        out_wav = os.path.join(
            self.output_folder or tempfile.gettempdir(),
            f"{stem}_dub_{int(time.time())}.wav",
        )
        self.narration_path = out_wav
        self._mic_process = QProcess(self)
        self._mic_process.start(
            ffmpeg,
            ["-y", "-hide_banner", "-f", "avfoundation", "-i", f":{mic_idx}", "-ac", "1", "-ar", "44100", out_wav],
        )
        self.btn_rec_mic.setText("⏹ Stop Dub")
        if self.current_cap and not self._playing:
            self._start_playback()
        self.status_label.setText("🎙 Recording live dub voiceover to picture… Click '⏹ Stop Dub' when finished.")

    def preview_dub_audio(self):
        """Play or stop the recorded/loaded dub audio track using macOS afplay."""
        if self._dub_player_proc is not None and self._dub_player_proc.state() != QProcess.NotRunning:
            self._dub_player_proc.kill()
            self._dub_player_proc.waitForFinished(500)
            self.btn_play_dub.setText("🔊 Play Dub")
            self.status_label.setText("Stopped dub preview")
            return
        if not self.narration_path or not os.path.isfile(self.narration_path):
            self.status_label.setText("Record a dub with '🎙 Dub Record' or load an audio file first.")
            return
        afplay = shutil.which("afplay") or "/usr/bin/afplay"
        if os.path.exists(afplay):
            self._dub_player_proc = QProcess(self)
            self._dub_player_proc.finished.connect(lambda: self.btn_play_dub.setText("🔊 Play Dub"))
            self._dub_player_proc.start(afplay, [self.narration_path])
            self.btn_play_dub.setText("⏹ Stop Dub")
            self.status_label.setText(f"🔊 Playing dub track: {os.path.basename(self.narration_path)}")

    def choose_narration_file(self):
        path, _flt = QFileDialog.getOpenFileName(
            self, "Choose Dub / Narration Audio", self.output_folder or "",
            "Audio Files (*.wav *.mp3 *.m4a *.aac *.flac)",
        )
        if path:
            self.narration_path = path
            self.narr_label.setText(f"Dub Track: {os.path.basename(path)}  (Ready for MP4 export or '🔊 Play Dub')")
            self.status_label.setText(f"Loaded dub audio: {os.path.basename(path)}")

    def clear_narration(self):
        if self._dub_player_proc is not None and self._dub_player_proc.state() != QProcess.NotRunning:
            self._dub_player_proc.kill()
        self.narration_path = None
        self.narr_label.setText("Dub Track: None  (Click '🎙 Dub Record' to record voiceover synced to video)")
        self.status_label.setText("Cleared dub audio")

    def _refresh_caption_list(self):
        self.caption_list.blockSignals(True)
        self.caption_list.clear()
        for cue in self.caption_cues:
            self.caption_list.addItem(f"[{cue.start:.1f}s–{cue.end:.1f}s] {cue.text}")
        self.caption_list.blockSignals(False)
        if self._caption_dialog_list is not None:
            self._caption_dialog_list.blockSignals(True)
            self._caption_dialog_list.clear()
            for cue in self.caption_cues:
                self._caption_dialog_list.addItem(f"[{cue.start:.1f}s–{cue.end:.1f}s] {cue.text}")
            self._caption_dialog_list.blockSignals(False)
        self._last_caption_highlight_idx = -2  # force re-applying highlight to the freshly rebuilt items
        if self.current_cap:
            self.show_frame(self.frame_slider.value())

    def _open_caption_list_dialog(self):
        # Non-modal "drawer" — stays open and live-updates while you keep using
        # (and playing back) the main window. A previous QDialog here used
        # .exec_(), which blocks the whole app's event loop including playback;
        # that was the bug: you genuinely could not press Play while it was open.
        existing = getattr(self, "_caption_drawer_dialog", None)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return

        dlg = QDialog(self, Qt.Tool)
        dlg.setWindowTitle("Captions / Subtitles")
        dlg.resize(420, self.height())
        lay = QVBoxLayout(dlg)
        hint = QLabel("Follows playback — the current line highlights as the video plays.")
        hint.setStyleSheet("color: #8888aa; font-size: 10px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        big_list = QListWidget()
        big_list.setToolTip("Click a caption to jump to it; double-click to edit its text")
        for cue in self.caption_cues:
            big_list.addItem(f"[{cue.start:.1f}s–{cue.end:.1f}s] {cue.text}")

        def on_row_changed(row):
            self.caption_list.setCurrentRow(row)

        def on_double_clicked(_item):
            self.caption_list.setCurrentRow(big_list.currentRow())
            self.edit_selected_caption()

        big_list.currentRowChanged.connect(on_row_changed)
        big_list.itemDoubleClicked.connect(on_double_clicked)
        lay.addWidget(big_list)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.close)
        lay.addWidget(btn_close)

        self._caption_dialog_list = big_list
        self._caption_drawer_dialog = dlg
        self._last_caption_highlight_idx = -2

        def on_finished(_r):
            self._caption_dialog_list = None
            self._caption_drawer_dialog = None

        dlg.finished.connect(on_finished)
        dlg.move(self.geometry().right() + 8, self.geometry().top())
        dlg.show()
        if self.current_cap and self.fps:
            self._sync_caption_highlight(self.frame_slider.value() / self.fps)

    def _on_caption_selected(self, row):
        if 0 <= row < len(self.caption_cues) and self.current_cap:
            cue = self.caption_cues[row]
            frame = max(0, min(int(round(cue.start * self.fps)), self.total_frames - 1))
            self.frame_slider.setValue(frame)

    def edit_selected_caption(self):
        row = self.caption_list.currentRow()
        if row < 0 or row >= len(self.caption_cues):
            return
        from PyQt5.QtWidgets import QInputDialog
        cue = self.caption_cues[row]
        text, ok = QInputDialog.getText(
            self,
            "Edit Subtitle / Caption",
            f"Subtitle [{cue.start:.2f}s – {cue.end:.2f}s]:",
            text=cue.text,
        )
        if ok and text.strip():
            cue.text = text.strip()
            self._refresh_caption_list()
            self.caption_list.setCurrentRow(row)
            self.status_label.setText(f"Updated caption [{cue.start:.2f}s–{cue.end:.2f}s]")

    def auto_transcribe_captions(self):
        if not self.current_video_path:
            QMessageBox.information(self, APP_NAME, "Load a video first.")
            return
        if self._whisper_worker is not None and self._whisper_worker.isRunning():
            return
        self.btn_auto_cap.setEnabled(False)
        self.status_label.setText("Transcribing audio with offline Whisper AI…")
        self._whisper_worker = WhisperTranscribeWorker(
            self.current_video_path,
            start_sec=self.in_spin.value(),
            end_sec=self.out_spin.value() if self.out_spin.value() > self.in_spin.value() else None,
            model_size="tiny",
            parent=self,
        )
        self._whisper_worker.progress_text.connect(self.status_label.setText)
        self._whisper_worker.finished_cues.connect(self._on_whisper_done)
        self._whisper_worker.start()

    def _on_whisper_done(self, cues, error):
        self.btn_auto_cap.setEnabled(True)
        if error:
            self.status_label.setText(f"Auto-caption note: {error}")
            return
        self.caption_cues = list(cues)
        self._refresh_caption_list()
        if self.current_cap:
            self.show_frame(self.frame_slider.value())
        self.status_label.setText(
            f"💬 Generated {len(cues)} timed caption(s) — showing in the preview at the matching time"
        )

    def add_manual_caption(self):
        if not self.current_cap:
            return
        from PyQt5.QtWidgets import QInputDialog
        cur_sec = round(self.frame_slider.value() / self.fps, 2)
        text, ok = QInputDialog.getText(
            self, "Add Timed Caption",
            f"Caption text starting at {cur_sec:.2f}s (3s duration):",
        )
        if ok and text.strip():
            dur = self.total_frames / self.fps
            self.caption_cues.append(
                CaptionCue(start=cur_sec, end=min(dur, cur_sec + 3.0), text=text.strip())
            )
            self.caption_cues.sort(key=lambda c: c.start)
            self._refresh_caption_list()
            self.show_frame(self.frame_slider.value())

    def manage_srt_menu(self):
        box = QMessageBox(self)
        box.setWindowTitle("Export / Load Captions")
        box.setText(
            "Load an existing .srt subtitle file, or save the current captions as "
            ".srt (for Premiere — File > Import, or the Captions panel's Import), "
            "plain .txt transcript, or .csv."
        )
        load_btn = box.addButton("Load .srt…", QMessageBox.ActionRole)
        save_srt_btn = box.addButton("Save .srt…", QMessageBox.AcceptRole)
        save_txt_btn = box.addButton("Save .txt…", QMessageBox.ActionRole)
        save_csv_btn = box.addButton("Save .csv…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is load_btn:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open SRT File", self.output_folder or "", "SubRip Subtitles (*.srt)"
            )
            if path:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    self.caption_cues = parse_srt(fh.read())
                self._refresh_caption_list()
                if self.current_cap:
                    self.show_frame(self.frame_slider.value())
                self.status_label.setText(f"Loaded {len(self.caption_cues)} caption(s) from {os.path.basename(path)}")
            return

        if clicked not in (save_srt_btn, save_txt_btn, save_csv_btn):
            return
        if not self.caption_cues:
            QMessageBox.information(self, APP_NAME, "No captions to save yet.")
            return
        stem = os.path.splitext(os.path.basename(self.current_video_path or "captions"))[0]
        if clicked is save_srt_btn:
            ext, flt, content = "srt", "SubRip Subtitles (*.srt)", write_srt(self.caption_cues)
        elif clicked is save_txt_btn:
            ext, flt, content = "txt", "Text Transcript (*.txt)", write_captions_txt(self.caption_cues)
        else:
            ext, flt, content = "csv", "CSV (*.csv)", write_captions_csv(self.caption_cues)
        default = os.path.join(self.output_folder or "", f"{stem}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, f"Save {ext.upper()} File", default, flt)
        if path:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            self.status_label.setText(f"Saved captions to {os.path.basename(path)}")

    def clear_captions(self):
        self.caption_cues = []
        self._refresh_caption_list()
        if self.current_cap:
            self.show_frame(self.frame_slider.value())
        self.status_label.setText("Cleared captions")

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
        elif os.path.isfile(path) and \
                os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
            self._load_audio_as_video(path)

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
        if self.download_dialog is not None and self.download_dialog.is_busy():
            self.download_dialog.show()
            self.download_dialog.raise_()
            self.download_dialog.status.setText('Cancel the download before closing the app.')
            event.ignore()
            return
        self._stop_playback()
        if self.current_cap:
            self.current_cap.release()
        event.accept()


if __name__ == "__main__":
    _remove_quarantine_attr()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = VideoFrameCapture()
    window.show()
    sys.exit(app.exec_())
