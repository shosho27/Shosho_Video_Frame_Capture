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
)
from PyQt5.QtGui import (
    QImage, QPixmap, QFont, QKeySequence, QColor, QPalette,
    QPainter, QPdfWriter, QPageSize, QPen, QFontMetrics,
)
from PyQt5.QtCore import Qt, QTimer, QSizeF, QRectF, QMarginsF
from video_downloader import DownloadDialog

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

        download_btn = QPushButton("↓  Download Video from Link")
        download_btn.setMinimumHeight(36)
        download_btn.setToolTip("YouTube, X / Twitter, Facebook, Instagram, and TikTok")
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
        sheet_btn = QPushButton("Contact Sheet…")
        sheet_btn.setToolTip(
            "Preview batch timecode frames, or a folder of saved pictures, and print one PDF")
        sheet_btn.clicked.connect(self.open_contact_sheet)
        fmt_row.addWidget(sheet_btn)
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

        # ── SECOND CAPTURE ROW: Interval + Scene ──────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        right_panel.addWidget(sep2)

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

        right_panel.addLayout(capture_row2)

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

    def _load_folder(self, folder):
        self.video_folder = folder
        self.folder_label.setText(folder)

        if not self.output_folder:
            self._set_output_folder(os.path.join(folder, "captured_frames"))
        os.makedirs(self.output_folder, exist_ok=True)

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
        self.status_label.setText(f"Loaded: {os.path.basename(path)}")

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
            qt_img = QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888)
        else:
            rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            qt_img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
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
