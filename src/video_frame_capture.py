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
import time
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QFrame,
    QSizePolicy, QDoubleSpinBox, QComboBox, QShortcut, QSpinBox, QDialog,
    QTabWidget, QLineEdit, QCheckBox,
)
from PyQt5.QtGui import (
    QImage, QPixmap, QFont, QKeySequence, QColor, QPalette,
    QPainter, QPdfWriter, QPageSize, QPen, QFontMetrics,
)
from PyQt5.QtCore import Qt, QTimer, QSizeF, QRectF, QMarginsF, QProcess
from video_downloader import DownloadDialog
from video_edit_tools import (
    ANNOTATION_COLORS,
    AnnotatedPreviewLabel,
    CaptionCue,
    TimelineMapBar,
    WhisperTranscribeWorker,
    export_audio_clip,
    export_mp4_with_chapters,
    export_video_or_gif,
    list_avfoundation_microphones,
    parse_srt,
    render_annotations_on_bgr,
    render_annotations_on_qimage,
    split_video_by_cuts,
    write_srt,
)

APP_NAME = "Video Frame Capture"

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv',
                    '.webm', '.m4v', '.mpg', '.mpeg')
CONTACT_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff'}
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


def contact_grid(per_page, page_w, page_h):
    """Choose columns and rows for `per_page` cells on a page of this size.

    A perfect rectangle is preferred, and the cell stays close to square so
    the picture and its two caption lines both fit.
    """
    per_page = max(1, int(per_page))
    best = None
    for cols in range(1, per_page + 1):
        rows = math.ceil(per_page / cols)
        aspect = (page_w / cols) / (page_h / rows)
        waste = cols * rows - per_page
        score = (abs(aspect - 1.0) + waste * 0.15, cols)
        if best is None or score < best[0]:
            best = (score, cols, rows)
    return best[1], best[2]


def list_contact_images(folder):
    """Image files in one folder, sorted by file name. Subfolders are skipped."""
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    for name in os.listdir(folder):
        if os.path.splitext(name)[1].lower() not in CONTACT_IMAGE_EXTENSIONS:
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            found.append(path)
    found.sort(key=lambda path: os.path.basename(path).casefold())
    return found


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
    cols, rows = contact_grid(per_page, inner_w, inner_h)
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
        painter.setPen(QPen(QColor("#d0d0d0"), max(1.0, scale)))
        painter.drawRect(image_rect)
        image, name, directory = _contact_picture(path)
        if image.isNull():
            painter.setPen(QColor("#888"))
            painter.drawText(image_rect, Qt.AlignCenter, "Unreadable")
        else:
            # Draw the original into the cell. QPdfWriter stores that image
            # and scales it on the page, so the PDF keeps the source pixels
            # instead of a copy shrunk to the cell.
            fitted_w, fitted_h = _contain_size(
                image.width(), image.height(), image_rect.width(), image_rect.height())
            dx = image_rect.x() + (image_rect.width() - fitted_w) / 2.0
            dy = image_rect.bottom() - fitted_h
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(dx, dy, fitted_w, fitted_h), image)
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
            "background: white; color: #666; border: 1px solid #555570; border-radius: 4px;")
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


def _detect_scene_cuts(video_path, threshold=0.4):
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
    for line in result.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            timestamps.add(float(m.group(1)))
    return sorted(timestamps)


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
        self.setWindowTitle(APP_NAME)
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
        self.contact_dialog = None
        self._contact_busy = False
        self._playing       = False
        self._has_alpha     = False
        self.download_dialog = None
        self.trim_in_sec    = 0.0
        self.trim_out_sec   = None
        self.freeze_at_sec  = None
        self.detected_cuts  = []
        self.chapter_titles = {}
        self.caption_cues   = []
        self.narration_path = None
        self._mic_process   = None
        self._whisper_worker = None
        self._playback_rate = 1.0  # JKL shuttle rate (-4, -2, -1, 0, 1, 2, 4)
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
        import_btn.setMinimumHeight(36)
        import_btn.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; "
            "border-radius: 4px; border: 1px solid #2ecc71;"
        )
        import_btn.setToolTip("Open one or more video files directly (Cmd+O)")
        import_btn.clicked.connect(self.import_video_files)
        left_panel.addWidget(import_btn)

        folder_btn = QPushButton("📁  Select Video Folder")
        folder_btn.setMinimumHeight(36)
        folder_btn.clicked.connect(self.select_folder)
        left_panel.addWidget(folder_btn)

        download_btn = QPushButton("↓  Download Video from Link")
        download_btn.setMinimumHeight(36)
        download_btn.setToolTip(
            "YouTube, Vimeo, X / Twitter, Facebook, Instagram, TikTok, "
            "Reddit, Twitch, LinkedIn, Dailymotion, and Loom"
        )
        download_btn.clicked.connect(self.open_downloader)
        left_panel.addWidget(download_btn)

        output_row = QHBoxLayout()
        output_row.setSpacing(6)
        self.output_btn = QPushButton("💾  Output Folder")
        self.output_btn.setMinimumHeight(36)
        self.output_btn.setToolTip("Choose where captured frames are saved")
        self.output_btn.clicked.connect(self.choose_output_folder)
        output_row.addWidget(self.output_btn, stretch=1)
        self.reveal_output_btn = QPushButton("Show in Finder")
        self.reveal_output_btn.setMinimumHeight(36)
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
        left_widget.setMaximumWidth(300)

        # ── RIGHT PANEL ───────────────────────────────────────────────────────
        right_panel = QVBoxLayout()
        right_panel.setSpacing(5)

        # Preview
        self.preview_label = AnnotatedPreviewLabel(
            "Select a folder and video to begin\n\nYou can also drag and drop a folder or video file here"
        )
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(300)
        self.preview_label.setStyleSheet(
            "background-color: #1a1a2e; color: #555570; border-radius: 8px; "
            "font-size: 13px; border: 2px solid #2a2a40;")
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_panel.addWidget(self.preview_label)

        # Markup & Freeze toolbar right under preview
        markup_row = QHBoxLayout()
        markup_row.setSpacing(4)
        markup_row.addWidget(QLabel("Markup:"))
        self.tool_combo = QComboBox()
        for label, tool_id in [
            ("Pointer (None)", "none"),
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
        self.color_combo.currentIndexChanged.connect(
            lambda: setattr(self.preview_label, "color", self.color_combo.currentData())
        )
        markup_row.addWidget(self.color_combo)

        markup_row.addWidget(QLabel("Size:"))
        self.thickness_spin = QSpinBox()
        self.thickness_spin.setRange(2, 14)
        self.thickness_spin.setValue(4)
        self.thickness_spin.setFixedWidth(48)
        self.thickness_spin.valueChanged.connect(
            lambda v: setattr(self.preview_label, "thickness", v)
        )
        markup_row.addWidget(self.thickness_spin)

        markup_row.addWidget(QLabel("Show for:"))
        self.markup_dur_spin = QDoubleSpinBox()
        self.markup_dur_spin.setRange(0.0, 60.0)
        self.markup_dur_spin.setSingleStep(0.5)
        self.markup_dur_spin.setValue(3.0)
        self.markup_dur_spin.setSuffix("s")
        self.markup_dur_spin.setSpecialValueText("All Clip")
        self.markup_dur_spin.setToolTip(
            "How long newly drawn markup stays on screen from the current timecode (0s = entire video)"
        )
        self.markup_dur_spin.setFixedWidth(68)
        self.markup_dur_spin.valueChanged.connect(self._on_markup_duration_changed)
        markup_row.addWidget(self.markup_dur_spin)

        btn_markup_in = QPushButton("[ Markup In")
        btn_markup_in.setToolTip("Set latest markup's start time to current playhead")
        btn_markup_in.clicked.connect(self.set_last_markup_in)
        markup_row.addWidget(btn_markup_in)

        btn_markup_out = QPushButton("Markup Out ]")
        btn_markup_out.setToolTip("Set latest markup's end time to current playhead")
        btn_markup_out.clicked.connect(self.set_last_markup_out)
        markup_row.addWidget(btn_markup_out)

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
        self.btn_freeze_here = QPushButton("❄️ Hold Frame:")
        self.btn_freeze_here.setCheckable(True)
        self.btn_freeze_here.setToolTip("Freeze on the current frame during exported clip/GIF to emphasize markup")
        self.btn_freeze_here.toggled.connect(self._toggle_freeze_point)
        markup_row.addWidget(self.btn_freeze_here)

        self.freeze_dur_spin = QDoubleSpinBox()
        self.freeze_dur_spin.setRange(0.5, 15.0)
        self.freeze_dur_spin.setSingleStep(0.5)
        self.freeze_dur_spin.setValue(3.0)
        self.freeze_dur_spin.setSuffix("s")
        self.freeze_dur_spin.setFixedWidth(64)
        self.freeze_dur_spin.valueChanged.connect(self._update_timeline_feedback)
        markup_row.addWidget(self.freeze_dur_spin)
        right_panel.addLayout(markup_row)
        self.preview_label.annotations_changed.connect(self._update_timeline_feedback)

        # Video info bar + live Markup / Hold feedback badge
        self.info_bar = QLabel("")
        self.info_bar.setStyleSheet("color: #8888aa; font-size: 10px; padding: 1px 2px;")
        right_panel.addWidget(self.info_bar)

        self.feedback_banner = QLabel("Timeline: Full clip  ·  Markup: None  ·  Hold: Off  ·  Transport: J (Rev) / K (Pause) / L (Fwd)")
        self.feedback_banner.setStyleSheet(
            "background-color: #1e2238; color: #a8d8ff; font-size: 11px; "
            "font-weight: bold; padding: 4px 8px; border-radius: 4px; border: 1px solid #354068;"
        )
        right_panel.addWidget(self.feedback_banner)

        # Scrubber + visual Timeline Map Bar (shows Trim [In..Out], Markup spans, Freeze Hold, Cuts)
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self.on_slider_moved)
        right_panel.addWidget(self.frame_slider)

        self.timeline_bar = TimelineMapBar(self)
        right_panel.addWidget(self.timeline_bar)

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
        sheet_btn = QPushButton("Contact Sheet…")
        sheet_btn.setToolTip(
            "Preview batch timecode frames, or a folder of saved pictures, and print one PDF")
        sheet_btn.clicked.connect(self.open_contact_sheet)
        fmt_row.addWidget(sheet_btn)
        right_panel.addLayout(fmt_row)

        # ── MODE TABS (Frame Capture vs. Edit / Trim / GIF / Captions Studio) ──
        self.mode_tabs = QTabWidget()

        # Tab 1: Frame Capture
        capture_tab = QWidget()
        capture_tab_lay = QVBoxLayout(capture_tab)
        capture_tab_lay.setContentsMargins(6, 6, 6, 6)
        capture_tab_lay.setSpacing(6)

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

        capture_tab_lay.addLayout(capture_row)

        # ── SECOND CAPTURE ROW: Interval + Scene ──────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        capture_tab_lay.addWidget(sep2)

        capture_row2 = QHBoxLayout()

        # Interval Capture
        interval_grp = QGroupBox("Interval Capture")
        interval_lay = QVBoxLayout()
        interval_lay.setSpacing(4)
        ivl_top = QHBoxLayout()
        ivl_top.addWidget(QLabel("Every:"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setDecimals(1)
        self.interval_spin.setMinimum(0.1)
        self.interval_spin.setMaximum(3600)
        self.interval_spin.setValue(3.0)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setSuffix(" sec")
        self.interval_spin.setFixedWidth(90)
        ivl_top.addWidget(self.interval_spin)
        ivl_top.addStretch()
        interval_lay.addLayout(ivl_top)
        btn_ivl_single = QPushButton("⏱  This Video")
        btn_ivl_single.setMinimumHeight(34)
        btn_ivl_single.clicked.connect(self.capture_interval_single)
        interval_lay.addWidget(btn_ivl_single)
        btn_ivl_batch = QPushButton("⏱  All Videos")
        btn_ivl_batch.setMinimumHeight(34)
        btn_ivl_batch.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        btn_ivl_batch.clicked.connect(self.capture_interval_batch)
        interval_lay.addWidget(btn_ivl_batch)
        interval_grp.setLayout(interval_lay)
        capture_row2.addWidget(interval_grp)

        # Scene Capture
        scene_grp = QGroupBox("Scene Capture")
        scene_lay = QVBoxLayout()
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
        btn_scene_single = QPushButton("🎬  This Video")
        btn_scene_single.setMinimumHeight(34)
        btn_scene_single.clicked.connect(self.capture_scenes_single)
        scene_lay.addWidget(btn_scene_single)
        btn_scene_batch = QPushButton("🎬  All Videos")
        btn_scene_batch.setMinimumHeight(34)
        btn_scene_batch.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; "
            "border-radius: 5px; border: none;")
        btn_scene_batch.clicked.connect(self.capture_scenes_batch)
        scene_lay.addWidget(btn_scene_batch)
        scene_grp.setLayout(scene_lay)
        capture_row2.addWidget(scene_grp)

        capture_tab_lay.addLayout(capture_row2)
        self.mode_tabs.addTab(capture_tab, "📸  Frame Capture")

        # Tab 2: Edit, Trim, GIF, Cut Splitter, Narration & Captions
        edit_tab = QWidget()
        edit_tab_lay = QHBoxLayout(edit_tab)
        edit_tab_lay.setContentsMargins(6, 6, 6, 6)
        edit_tab_lay.setSpacing(8)

        # Left column of Edit Studio: Trim, Fades, Export MP4 / GIF / Audio
        trim_grp = QGroupBox("Trim, Fade & Export (MP4 / GIF / Audio)")
        trim_lay = QVBoxLayout(trim_grp)
        trim_lay.setSpacing(5)

        in_out_row = QHBoxLayout()
        btn_set_in = QPushButton("[ Set In")
        btn_set_in.setToolTip("Set clip start to current playhead (I)")
        btn_set_in.clicked.connect(self.set_trim_in)
        in_out_row.addWidget(btn_set_in)
        self.in_spin = QDoubleSpinBox()
        self.in_spin.setRange(0, 99999)
        self.in_spin.setDecimals(2)
        self.in_spin.setSuffix("s")
        self.in_spin.setFixedWidth(78)
        self.in_spin.valueChanged.connect(self._on_trim_spin_changed)
        in_out_row.addWidget(self.in_spin)

        btn_set_out = QPushButton("Set Out ]")
        btn_set_out.setToolTip("Set clip end to current playhead (O)")
        btn_set_out.clicked.connect(self.set_trim_out)
        in_out_row.addWidget(btn_set_out)
        self.out_spin = QDoubleSpinBox()
        self.out_spin.setRange(0, 99999)
        self.out_spin.setDecimals(2)
        self.out_spin.setSuffix("s")
        self.out_spin.setFixedWidth(78)
        self.out_spin.valueChanged.connect(self._on_trim_spin_changed)
        in_out_row.addWidget(self.out_spin)

        btn_reset_trim = QPushButton("Full Clip")
        btn_reset_trim.clicked.connect(self.reset_trim_range)
        in_out_row.addWidget(btn_reset_trim)
        trim_lay.addLayout(in_out_row)

        fade_row = QHBoxLayout()
        fade_row.addWidget(QLabel("Fade In:"))
        self.fade_in_spin = QDoubleSpinBox()
        self.fade_in_spin.setRange(0.0, 10.0)
        self.fade_in_spin.setSingleStep(0.5)
        self.fade_in_spin.setValue(0.0)
        self.fade_in_spin.setSuffix("s")
        self.fade_in_spin.setFixedWidth(64)
        fade_row.addWidget(self.fade_in_spin)
        fade_row.addWidget(QLabel("Fade Out:"))
        self.fade_out_spin = QDoubleSpinBox()
        self.fade_out_spin.setRange(0.0, 10.0)
        self.fade_out_spin.setSingleStep(0.5)
        self.fade_out_spin.setValue(0.0)
        self.fade_out_spin.setSuffix("s")
        self.fade_out_spin.setFixedWidth(64)
        fade_row.addWidget(self.fade_out_spin)
        fade_row.addStretch()
        trim_lay.addLayout(fade_row)

        gif_row = QHBoxLayout()
        gif_row.addWidget(QLabel("GIF FPS:"))
        self.gif_fps_combo = QComboBox()
        for fps_val in (10, 12, 15, 24):
            self.gif_fps_combo.addItem(f"{fps_val} fps", fps_val)
        self.gif_fps_combo.setCurrentIndex(1)
        gif_row.addWidget(self.gif_fps_combo)
        gif_row.addWidget(QLabel("Width:"))
        self.gif_width_combo = QComboBox()
        for label, w_val in [("480 px", 480), ("640 px", 640), ("720 px", 720), ("Original", 0)]:
            self.gif_width_combo.addItem(label, w_val)
        gif_row.addWidget(self.gif_width_combo)
        gif_row.addStretch()
        trim_lay.addLayout(gif_row)

        exp_row = QHBoxLayout()
        self.btn_export_mp4 = QPushButton("🎬 Export Trim (MP4)")
        self.btn_export_mp4.setMinimumHeight(34)
        self.btn_export_mp4.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; border-radius: 5px; border: none;"
        )
        self.btn_export_mp4.clicked.connect(lambda: self.export_edited_clip(as_gif=False))
        exp_row.addWidget(self.btn_export_mp4)

        self.btn_export_gif = QPushButton("🎞 Export GIF")
        self.btn_export_gif.setMinimumHeight(34)
        self.btn_export_gif.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; border-radius: 5px; border: none;"
        )
        self.btn_export_gif.clicked.connect(lambda: self.export_edited_clip(as_gif=True))
        exp_row.addWidget(self.btn_export_gif)

        self.btn_export_audio = QPushButton("🎵 Export Audio")
        self.btn_export_audio.setMinimumHeight(34)
        self.btn_export_audio.clicked.connect(self.export_trimmed_audio)
        exp_row.addWidget(self.btn_export_audio)
        trim_lay.addLayout(exp_row)
        edit_tab_lay.addWidget(trim_grp, stretch=1)

        # Middle column: Adjustable Edit / Scene Cut Markers, MP4 Chapters & Video Splitter
        cuts_grp = QGroupBox("Scene Cuts, MP4 Chapters & Splitter")
        cuts_lay = QVBoxLayout(cuts_grp)
        cuts_lay.setSpacing(4)

        cuts_top = QHBoxLayout()
        btn_find_cuts = QPushButton("🔍 Detect Cuts / Chapters")
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
        self.cuts_list.setMaximumHeight(68)
        self.cuts_list.setToolTip(
            "Click a cut/chapter to preview; double-click or click '✏️ Name' to title the chapter; "
            "scrub and click '↔ Move Cut' to adjust"
        )
        self.cuts_list.currentRowChanged.connect(self._on_cut_selected)
        self.cuts_list.itemDoubleClicked.connect(lambda _item: self.rename_selected_chapter())
        cuts_lay.addWidget(self.cuts_list)

        cuts_adj = QHBoxLayout()
        btn_add_cut = QPushButton("+ Add Here")
        btn_add_cut.clicked.connect(self.add_cut_at_current)
        cuts_adj.addWidget(btn_add_cut)
        btn_nudge_cut = QPushButton("↔ Move Here")
        btn_nudge_cut.setToolTip("Move selected cut/chapter point to current scrubber frame")
        btn_nudge_cut.clicked.connect(self.move_selected_cut_to_current)
        cuts_adj.addWidget(btn_nudge_cut)
        btn_name_cut = QPushButton("✏️ Name")
        btn_name_cut.setToolTip("Set a custom chapter title for the selected cut point")
        btn_name_cut.clicked.connect(self.rename_selected_chapter)
        cuts_adj.addWidget(btn_name_cut)
        btn_del_cut = QPushButton("Remove")
        btn_del_cut.clicked.connect(self.remove_selected_cut)
        cuts_adj.addWidget(btn_del_cut)
        cuts_lay.addLayout(cuts_adj)

        cuts_export_row = QHBoxLayout()
        btn_save_chapters = QPushButton("📑 Save MP4 w/ Chapters")
        btn_save_chapters.setMinimumHeight(34)
        btn_save_chapters.setToolTip(
            "Embed detected/adjusted cuts as native MP4 chapter markers (QuickTime, VLC, Premiere, YouTube)"
        )
        btn_save_chapters.setStyleSheet(
            "background-color: #1e5c3a; color: white; font-weight: bold; border-radius: 5px; border: none;"
        )
        btn_save_chapters.clicked.connect(self.export_current_video_with_chapters)
        cuts_export_row.addWidget(btn_save_chapters)

        btn_split_segs = QPushButton("✂️ Split into Clips")
        btn_split_segs.setMinimumHeight(34)
        btn_split_segs.setToolTip("Split video at each cut into separate numbered MP4 clips")
        btn_split_segs.setStyleSheet(
            "background-color: #1a3d6e; color: white; font-weight: bold; border-radius: 5px; border: none;"
        )
        btn_split_segs.clicked.connect(self.split_current_video_by_cuts)
        cuts_export_row.addWidget(btn_split_segs)
        cuts_lay.addLayout(cuts_export_row)
        edit_tab_lay.addWidget(cuts_grp, stretch=1)

        # Right column: Narration & Captions (Offline Whisper + Manual/SRT)
        narr_grp = QGroupBox("Narration & Captions")
        narr_lay = QVBoxLayout(narr_grp)
        narr_lay.setSpacing(4)

        narr_row = QHBoxLayout()
        self.mic_device_combo = QComboBox()
        self.mic_device_combo.setToolTip("Select microphone or internal audio input device")
        self._populate_mic_devices()
        narr_row.addWidget(self.mic_device_combo)

        self.btn_rec_mic = QPushButton("🎙 Record VO")
        self.btn_rec_mic.setCheckable(True)
        self.btn_rec_mic.setToolTip("Record voiceover (auto-plays video while recording so you can narrate to picture)")
        self.btn_rec_mic.toggled.connect(self.toggle_mic_recording)
        narr_row.addWidget(self.btn_rec_mic)

        btn_load_narr = QPushButton("File…")
        btn_load_narr.clicked.connect(self.choose_narration_file)
        narr_row.addWidget(btn_load_narr)

        self.narr_mode_combo = QComboBox()
        self.narr_mode_combo.addItem("Duck Video", "duck")
        self.narr_mode_combo.addItem("Replace Audio", "replace")
        narr_row.addWidget(self.narr_mode_combo)

        btn_clear_narr = QPushButton("✕")
        btn_clear_narr.setFixedWidth(26)
        btn_clear_narr.setToolTip("Clear narration track")
        btn_clear_narr.clicked.connect(self.clear_narration)
        narr_row.addWidget(btn_clear_narr)
        narr_lay.addLayout(narr_row)

        self.narr_label = QLabel("Narration: None")
        self.narr_label.setStyleSheet("color: #8888aa; font-size: 10px;")
        narr_lay.addWidget(self.narr_label)

        cap_btn_row = QHBoxLayout()
        self.btn_auto_cap = QPushButton("💬 Auto-Caption Video")
        self.btn_auto_cap.setToolTip("Offline AI speech-to-text captions (watch with sound off)")
        self.btn_auto_cap.clicked.connect(self.auto_transcribe_captions)
        cap_btn_row.addWidget(self.btn_auto_cap)

        self.auto_cap_on_load_chk = QCheckBox("Auto on Load")
        self.auto_cap_on_load_chk.setToolTip("Automatically generate subtitles when a video is downloaded or opened")
        cap_btn_row.addWidget(self.auto_cap_on_load_chk)

        btn_add_cap = QPushButton("+ Add Caption")
        btn_add_cap.clicked.connect(self.add_manual_caption)
        cap_btn_row.addWidget(btn_add_cap)

        btn_srt = QPushButton("SRT…")
        btn_srt.setToolTip("Load or save .srt subtitles")
        btn_srt.clicked.connect(self.manage_srt_menu)
        cap_btn_row.addWidget(btn_srt)

        btn_clear_cap = QPushButton("Clear")
        btn_clear_cap.clicked.connect(self.clear_captions)
        cap_btn_row.addWidget(btn_clear_cap)
        narr_lay.addLayout(cap_btn_row)

        self.caption_list = QListWidget()
        self.caption_list.setMaximumHeight(55)
        self.caption_list.currentRowChanged.connect(self._on_caption_selected)
        narr_lay.addWidget(self.caption_list)

        edit_tab_lay.addWidget(narr_grp, stretch=1)

        self.mode_tabs.addTab(edit_tab, "✂️  Edit, Trim, GIF & Captions")
        right_panel.addWidget(self.mode_tabs)

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
            self.mode_tabs.setCurrentIndex(1)

    def _setup_shortcuts(self):
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

        self.frame_slider.setMaximum(max(0, self.total_frames - 1))
        self.frame_slider.setValue(0)
        self.jump_spin.setMaximum(self.total_frames / self.fps)
        self.btn_play.setEnabled(True)

        dur = self.total_frames / self.fps
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
        self.freeze_at_sec = None
        self.btn_freeze_here.blockSignals(True)
        self.btn_freeze_here.setChecked(False)
        self.btn_freeze_here.setText("❄️ Hold Frame:")
        self.btn_freeze_here.blockSignals(False)
        self.preview_label.video_duration = dur
        self.preview_label.freeze_at = None
        self.preview_label.freeze_duration = 0.0
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
        for cue in self.caption_cues:
            if cue.start <= sec <= cue.end:
                return cue.text
        return ""

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
        elif event.key() == Qt.Key_J:
            self.shuttle_reverse()
        elif event.key() == Qt.Key_K:
            self.shuttle_pause()
        elif event.key() == Qt.Key_L:
            self.shuttle_forward()
        else:
            super().keyPressEvent(event)

    # ── Playback & JKL Shuttle ───────────────────────────────────────────────

    def toggle_play(self):
        if self._playing:
            self._stop_playback()
        else:
            self._playback_rate = 1.0
            self._start_playback()

    def shuttle_reverse(self):
        """J key: play in reverse or increase reverse shuttle speed (-1x, -2x, -4x)."""
        if not self.current_cap:
            return
        if not self._playing or self._playback_rate >= 0:
            self._playback_rate = -1.0
        elif self._playback_rate > -2.0:
            self._playback_rate = -2.0
        else:
            self._playback_rate = -4.0
        self._start_playback()
        self.status_label.setText(f"⏪  J Shuttle Reverse ({self._playback_rate:g}x)  ·  Press K to pause, L for forward")

    def shuttle_pause(self):
        """K key: pause playback immediately."""
        self._stop_playback()
        self.status_label.setText("⏸  K Paused  ·  Press J for reverse, L for forward")

    def shuttle_forward(self):
        """L key: play forward or increase forward shuttle speed (1x, 2x, 4x)."""
        if not self.current_cap:
            return
        if not self._playing or self._playback_rate <= 0:
            self._playback_rate = 1.0
        elif self._playback_rate < 2.0:
            self._playback_rate = 2.0
        else:
            self._playback_rate = 4.0
        self._start_playback()
        self.status_label.setText(f"⏩  L Shuttle Forward ({self._playback_rate:g}x)  ·  Press K to pause, J for reverse")

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
        self._update_timeline_feedback()

    def _stop_playback(self):
        self._playing = False
        self._playback_rate = 1.0
        self._play_timer.stop()
        self.btn_play.setText("▶  Play")
        self._update_timeline_feedback()

    def _advance_frame(self):
        cur = self.frame_slider.value()
        step = int(round(self._playback_rate)) or 1
        nxt = cur + step
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

    # ── Output format ─────────────────────────────────────────────────────────

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
        import numpy as np
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
        visible_anns = self.preview_label.visible_annotations_at(sec)
        if visible_anns or self._active_caption_at(sec):
            frame = render_annotations_on_bgr(
                frame,
                visible_anns,
                caption_text=self._active_caption_at(sec),
            )
        base = os.path.join(self.output_folder, f"{stem}_frame{frame_num}_{sec:.2f}s")
        path = self._save_frame(frame, base)

        self.status_label.setText(f"✅  Saved: {os.path.basename(path)}")
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
        self._refresh_contact_sheet()
        QMessageBox.information(self, "Batch Complete", msg)

    # ── Interval capture ──────────────────────────────────────────────────────

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

        # Fast metadata pass to size the progress bar
        total_ops = 0
        for path in paths:
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            dur = total_f / fps if fps else 0
            total_ops += int(dur / interval) + 1
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
            times, t = [], 0.0
            while t <= dur + 0.001:
                times.append(t)
                t += interval
            frame_nums = [min(int(round(t * fps)), total_f - 1) for t in times]
            for t, (_n, ret, frame) in zip(times, _read_frames(cap, frame_nums)):
                op += 1
                if ret:
                    base = os.path.join(self.output_folder, f"{stem}_{t:.1f}s")
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

    # ── Scene capture ─────────────────────────────────────────────────────────

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

        self.progress.setVisible(True)
        self.progress.setMaximum(len(paths))
        self.progress.setValue(0)

        success, errors = 0, []
        for i, path in enumerate(paths):
            stem = os.path.splitext(os.path.basename(path))[0]
            self.status_label.setText(f"Scanning scenes: {os.path.basename(path)}…")
            QApplication.processEvents()

            cuts = _detect_scene_cuts(path, threshold)
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

            boundaries = sorted({0.0} | set(cuts) | {dur})
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
                        self.output_folder,
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

    def _toggle_freeze_point(self, checked):
        if not self.current_cap or not checked:
            self.freeze_at_sec = None
            self.preview_label.freeze_at = None
            self.preview_label.freeze_duration = 0.0
            self.btn_freeze_here.setText("❄️ Hold Frame:")
            self.preview_label.refresh_display()
            self._update_timeline_feedback()
            self.status_label.setText("Freeze-frame hold cleared")
            return
        sec = round(self.frame_slider.value() / self.fps, 2)
        hold_dur = self.freeze_dur_spin.value()
        self.freeze_at_sec = sec
        self.preview_label.freeze_at = sec
        self.preview_label.freeze_duration = hold_dur
        self.btn_freeze_here.setText(f"❄️ Hold @{sec:.2f}s → +{hold_dur:.1f}s")
        self.preview_label.refresh_display()
        self._update_timeline_feedback()
        self.status_label.setText(
            f"❄️ Hold Frame set: pauses at {sec:.2f}s for {hold_dur:.1f}s (Hold window {sec:.2f}s → {sec + hold_dur:.2f}s)"
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
        hold_on = self.btn_freeze_here.isChecked() and self.freeze_at_sec is not None
        hold_at = self.freeze_at_sec if hold_on else None
        hold_dur = self.freeze_dur_spin.value() if hold_on else 0.0
        self.preview_label.freeze_at = hold_at
        self.preview_label.freeze_duration = hold_dur
        if hold_on:
            self.btn_freeze_here.setText(f"❄️ Hold @{hold_at:.2f}s → +{hold_dur:.1f}s")
        self.timeline_bar.update_state(
            duration=dur,
            current_sec=cur_sec,
            trim_in=t_in,
            trim_out=t_out,
            annotations=self.preview_label.annotations,
            freeze_at=hold_at,
            freeze_duration=hold_dur,
            cuts=self.detected_cuts,
        )
        parts = [f"Clip [{t_in:.2f}s → {t_out:.2f}s]"]
        if self.preview_label.annotations:
            last = self.preview_label.annotations[-1]
            vis_count = len(self.preview_label.visible_annotations_at(cur_sec))
            end_disp = min(dur, last.end_sec) if dur > 0 else last.end_sec
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

        if hold_on:
            parts.append(
                f"❄️ Hold Frame: [{hold_at:.2f}s → +{hold_dur:.1f}s (ends {hold_at + hold_dur:.2f}s)]"
            )
        else:
            parts.append("❄️ Hold: Off")

        if self._playing:
            rate = self._playback_rate or 1.0
            dir_lbl = "⏪ Rev" if rate < 0 else "⏩ Fwd"
            parts.append(f"{dir_lbl} {rate:g}x (J/K/L)")
        else:
            parts.append("J/K/L Shuttle Ready")
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
        ext = ".gif" if as_gif else ".mp4"
        out_path = os.path.join(
            self.output_folder, f"{stem}_edit_{s_sec:.2f}s-{e_sec:.2f}s{ext}"
        )
        self.status_label.setText(f"Rendering {'GIF' if as_gif else 'MP4'} → {os.path.basename(out_path)}…")
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
                freeze_at=self.freeze_at_sec if self.btn_freeze_here.isChecked() else None,
                freeze_duration=self.freeze_dur_spin.value() if self.btn_freeze_here.isChecked() else 0.0,
                captions=self.caption_cues,
                narration_path=self.narration_path,
                narration_mode=self.narr_mode_combo.currentData(),
                as_gif=as_gif,
                gif_fps=self.gif_fps_combo.currentData(),
                gif_width=self.gif_width_combo.currentData(),
            )
            if not as_gif and self.detected_cuts:
                freeze_on = self.btn_freeze_here.isChecked() and self.freeze_at_sec is not None
                f_at = self.freeze_at_sec if freeze_on else None
                f_dur = self.freeze_dur_spin.value() if freeze_on else 0.0
                shifted_cuts = []
                shifted_titles = {0.0: self.chapter_titles.get(0.0, "Chapter 1")}
                for idx, c in enumerate(self.detected_cuts, 1):
                    if s_sec + 0.05 < c < e_sec - 0.05:
                        rel_c = round(c - s_sec + (f_dur if (f_at is not None and c > f_at) else 0.0), 2)
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
        default = os.path.join(self.output_folder, f"{stem}_{s_sec:.2f}s-{e_sec:.2f}s.mp3")
        out_path, _flt = QFileDialog.getSaveFileName(
            self, "Export Audio", default, "MP3 Audio (*.mp3);;WAV Audio (*.wav)"
        )
        if not out_path:
            return
        fmt = "wav" if out_path.lower().endswith(".wav") else "mp3"
        try:
            export_audio_clip(
                ffmpeg, self.current_video_path, out_path,
                start_sec=s_sec, end_sec=e_sec,
                fade_in=self.fade_in_spin.value(),
                fade_out=self.fade_out_spin.value(),
                fmt=fmt,
            )
            self.status_label.setText(f"✅  Exported audio: {os.path.basename(out_path)}")
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
            chap_num = idx + 1  # 0.00s is Chapter 1, first cut starts Chapter 2
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
        self.status_label.setText("Detecting scene cuts / chapter points…")
        QApplication.processEvents()
        cuts = _detect_scene_cuts(self.current_video_path, threshold)
        if cuts is None:
            QMessageBox.warning(self, APP_NAME, "Scene detection failed.")
            return
        self.detected_cuts = sorted(round(c, 2) for c in cuts)
        self._refresh_cuts_list()
        self.status_label.setText(
            f"Found {len(self.detected_cuts)} cut/chapter point(s) ({len(self.detected_cuts) + 1} chapters). "
            f"Click '📑 Save MP4 w/ Chapters' or '✂️ Split into Clips'."
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
        out_path = os.path.join(self.output_folder, f"{stem}_chapters.mp4")

        # Build chapter title map including any caption-derived defaults
        titles_map = {0.0: self.chapter_titles.get(0.0, "Chapter 1")}
        for idx, sec in enumerate(self.detected_cuts, 1):
            titles_map[round(sec, 2)] = self._default_chapter_title_for(sec, idx + 1)

        self.status_label.setText(f"Embedding {len(self.detected_cuts) + 1} chapters into {os.path.basename(out_path)}…")
        QApplication.processEvents()
        try:
            saved_path, chapters = export_mp4_with_chapters(
                ffmpeg,
                self.current_video_path,
                out_path,
                self.detected_cuts,
                chapter_titles=titles_map,
            )
            summary = "\n".join(
                f"  • {idx:02d}. [{s:.2f}s – {e:.2f}s]  {t}"
                for idx, (s, e, t) in enumerate(chapters, 1)
            )
            self.status_label.setText(
                f"✅  Saved MP4 with {len(chapters)} chapters: {os.path.basename(saved_path)}"
            )
            QMessageBox.information(
                self,
                "MP4 Chapters Embedded",
                f"Saved MP4 with {len(chapters)} embedded chapter markers:\n{saved_path}\n\nChapters:\n{summary}",
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
            )
            self.progress.setVisible(False)
            self.status_label.setText(f"✅  Split into {len(files)} segment clips in {seg_dir}")
            QMessageBox.information(
                self, "Video Split Complete",
                f"Created {len(files)} segment MP4 clip(s) in:\n{seg_dir}",
            )
        except Exception as exc:
            self.progress.setVisible(False)
            QMessageBox.warning(self, APP_NAME, f"Failed to split video:\n\n{exc}")

    # ── Narration & Captions (Offline Whisper + Manual + SRT) ─────────────────

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
            self.btn_rec_mic.setText("🎙 Record VO")
            if self.narration_path and os.path.isfile(self.narration_path):
                self.narr_label.setText(f"Narration: {os.path.basename(self.narration_path)}")
                self.status_label.setText(f"🎙 Saved voiceover: {os.path.basename(self.narration_path)}")
            return

        mic_idx = self.mic_device_combo.currentData() or "0"
        os.makedirs(self.output_folder or tempfile.gettempdir(), exist_ok=True)
        out_wav = os.path.join(
            self.output_folder or tempfile.gettempdir(),
            f"narration_{int(time.time())}.wav",
        )
        self.narration_path = out_wav
        self._mic_process = QProcess(self)
        self._mic_process.start(
            ffmpeg,
            ["-y", "-hide_banner", "-f", "avfoundation", "-i", f":{mic_idx}", "-ac", "1", "-ar", "44100", out_wav],
        )
        self.btn_rec_mic.setText("⏹ Stop VO")
        if self.current_cap and not self._playing:
            self._start_playback()
        self.status_label.setText("🎙 Recording voiceover to picture… Click '⏹ Stop VO' when finished.")

    def choose_narration_file(self):
        path, _flt = QFileDialog.getOpenFileName(
            self, "Choose Narration Audio", self.output_folder or "",
            "Audio Files (*.wav *.mp3 *.m4a *.aac *.flac)",
        )
        if path:
            self.narration_path = path
            self.narr_label.setText(f"Narration: {os.path.basename(path)}")
            self.status_label.setText(f"Loaded narration audio: {os.path.basename(path)}")

    def clear_narration(self):
        self.narration_path = None
        self.narr_label.setText("Narration: None")
        self.status_label.setText("Cleared narration audio")

    def _refresh_caption_list(self):
        self.caption_list.blockSignals(True)
        self.caption_list.clear()
        for cue in self.caption_cues:
            self.caption_list.addItem(f"[{cue.start:.1f}s–{cue.end:.1f}s] {cue.text}")
        self.caption_list.blockSignals(False)
        if self.current_cap:
            self.show_frame(self.frame_slider.value())

    def _on_caption_selected(self, row):
        if 0 <= row < len(self.caption_cues) and self.current_cap:
            cue = self.caption_cues[row]
            frame = max(0, min(int(round(cue.start * self.fps)), self.total_frames - 1))
            self.frame_slider.setValue(frame)

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
        self.status_label.setText(
            f"💬 Generated {len(cues)} timed caption(s) — play or scrub with sound off to read along!"
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

    def manage_srt_menu(self):
        box = QMessageBox(self)
        box.setWindowTitle("SRT Subtitles")
        box.setText("Load an existing .srt subtitle file or save current captions to .srt?")
        load_btn = box.addButton("Load .srt…", QMessageBox.ActionRole)
        save_btn = box.addButton("Save .srt…", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec_()
        if box.clickedButton() is load_btn:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open SRT File", self.output_folder or "", "SubRip Subtitles (*.srt)"
            )
            if path:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    self.caption_cues = parse_srt(fh.read())
                self._refresh_caption_list()
                self.status_label.setText(f"Loaded {len(self.caption_cues)} caption(s) from {os.path.basename(path)}")
        elif box.clickedButton() is save_btn:
            if not self.caption_cues:
                QMessageBox.information(self, APP_NAME, "No captions to save yet.")
                return
            stem = os.path.splitext(os.path.basename(self.current_video_path or "captions"))[0]
            default = os.path.join(self.output_folder or "", f"{stem}.srt")
            path, _ = QFileDialog.getSaveFileName(
                self, "Save SRT File", default, "SubRip Subtitles (*.srt)"
            )
            if path:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(write_srt(self.caption_cues))
                self.status_label.setText(f"Saved subtitles to {os.path.basename(path)}")

    def clear_captions(self):
        self.caption_cues = []
        self._refresh_caption_list()
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
