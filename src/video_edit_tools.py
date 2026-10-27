"""
Edit, Markup, Freeze-Frame, Trim, GIF, Audio, Narration & Captioning Studio
for Video Frame Capture.
"""

from dataclasses import dataclass, field
import math
import os
import re
import shutil
import subprocess
import tempfile

import cv2
import numpy as np
from PyQt5.QtCore import QPointF, QProcess, QRectF, Qt, QThread, pyqtSignal
from PyQt5.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF,
)
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)


ANNOTATION_COLORS = [
    ("Red", "#ff3b30"),
    ("Yellow", "#ffcc00"),
    ("Green", "#34c759"),
    ("Cyan", "#00c7be"),
    ("White", "#ffffff"),
    ("Orange", "#ff9500"),
]


@dataclass
class Annotation:
    """Single vector markup item stored in normalized (0..1) image coordinates.
    Optional `start_sec` and `end_sec` control when the markup appears and disappears.
    """
    kind: str  # 'arrow', 'circle', 'rect', 'text'
    x1: float
    y1: float
    x2: float = 0.0
    y2: float = 0.0
    color: str = "#ff3b30"
    thickness: int = 4
    text: str = ""
    start_sec: float = 0.0
    end_sec: float = 999999.0

    def is_visible_at(self, sec):
        return self.start_sec - 0.01 <= float(sec) <= self.end_sec + 0.01

    def label(self):
        kind_icon = {"arrow": "↗ Arrow", "circle": "◯ Circle", "rect": "▭ Box", "text": f'T "{self.text}"'}.get(
            self.kind, self.kind
        )
        if self.end_sec >= 99999.0 and self.start_sec <= 0.01:
            return f"[All Clip]  {kind_icon}"
        return f"[{self.start_sec:.1f}s – {self.end_sec:.1f}s]  {kind_icon}"


@dataclass
class CaptionCue:
    """Single timed caption segment in seconds."""
    start: float
    end: float
    text: str


def format_srt_timestamp(seconds):
    ms = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _parse_srt_time(text):
    text = text.strip().replace(".", ",")
    m = re.match(r"^(\d+):(\d{2}):(\d{2}),(\d{1,3})$", text)
    if not m:
        m2 = re.match(r"^(\d+):(\d{2}),(\d{1,3})$", text)
        if not m2:
            return float(text.replace(",", "."))
        mins, secs, ms = m2.groups()
        return int(mins) * 60 + int(secs) + int(ms.ljust(3, "0")) / 1000.0
    hrs, mins, secs, ms = m.groups()
    return int(hrs) * 3600 + int(mins) * 60 + int(secs) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(content):
    """Parse SRT or simple `start --> end` text into CaptionCue objects."""
    cues = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        arrow_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if arrow_idx is None:
            continue
        parts = lines[arrow_idx].split("-->", 1)
        try:
            start = _parse_srt_time(parts[0])
            end = _parse_srt_time(parts[1].split()[0])
        except ValueError:
            continue
        body = " ".join(lines[arrow_idx + 1:]).strip()
        if body and end > start:
            cues.append(CaptionCue(start=start, end=end, text=body))
    return cues


def write_srt(cues):
    """Serialize CaptionCue list to standard SRT format."""
    blocks = []
    for idx, cue in enumerate(cues, 1):
        blocks.append(
            f"{idx}\n"
            f"{format_srt_timestamp(cue.start)} --> {format_srt_timestamp(cue.end)}\n"
            f"{cue.text.strip()}\n"
        )
    return "\n".join(blocks)


def render_annotations_on_qimage(qimage, annotations, caption_text=None):
    """Return a copy of `qimage` with vector annotations and optional caption drawn at native resolution."""
    if qimage.isNull():
        return qimage
    out = qimage.convertToFormat(QImage.Format_ARGB32)
    w = out.width()
    h = out.height()
    if w <= 0 or h <= 0:
        return out
    scale = max(1.0, min(w, h) / 720.0)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    for ann in annotations:
        color = QColor(ann.color)
        stroke = max(2.0, ann.thickness * scale)
        pen = QPen(color, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        px1, py1 = ann.x1 * w, ann.y1 * h
        px2, py2 = ann.x2 * w, ann.y2 * h

        if ann.kind == "arrow":
            painter.drawLine(QPointF(px1, py1), QPointF(px2, py2))
            angle = math.atan2(py2 - py1, px2 - px1)
            head_len = max(14.0 * scale, stroke * 4.2)
            head_angle = math.pi / 6.0
            p1 = QPointF(
                px2 - head_len * math.cos(angle - head_angle),
                py2 - head_len * math.sin(angle - head_angle),
            )
            p2 = QPointF(
                px2 - head_len * math.cos(angle + head_angle),
                py2 - head_len * math.sin(angle + head_angle),
            )
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(px2, py2), p1, p2]))
        elif ann.kind == "circle":
            rect = QRectF(
                min(px1, px2), min(py1, py2),
                max(4.0, abs(px2 - px1)), max(4.0, abs(py2 - py1)),
            )
            painter.drawEllipse(rect)
        elif ann.kind == "rect":
            rect = QRectF(
                min(px1, px2), min(py1, py2),
                max(4.0, abs(px2 - px1)), max(4.0, abs(py2 - py1)),
            )
            painter.drawRect(rect)
        elif ann.kind == "text" and ann.text:
            font = QFont("Helvetica Neue", max(12, int(round((14 + ann.thickness * 3) * scale))))
            font.setBold(True)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            tw = metrics.horizontalAdvance(ann.text)
            th = metrics.height()
            pad_x = 10 * scale
            pad_y = 6 * scale
            box = QRectF(
                max(4.0, min(px1, w - tw - pad_x * 2 - 4.0)),
                max(4.0, min(py1 - th / 2.0, h - th - pad_y * 2 - 4.0)),
                tw + pad_x * 2,
                th + pad_y * 2,
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 185))
            painter.drawRoundedRect(box, 6 * scale, 6 * scale)
            painter.setPen(color)
            painter.drawText(box, Qt.AlignCenter, ann.text)

    if caption_text:
        font = QFont("Helvetica Neue", max(14, int(round(22 * scale))))
        font.setBold(True)
        painter.setFont(font)
        margin_x = w * 0.08
        max_w = w - margin_x * 2
        metrics = QFontMetrics(font)
        wrapped = metrics.boundingRect(
            0, 0, int(max_w), int(h * 0.3),
            Qt.AlignCenter | Qt.TextWordWrap, caption_text,
        )
        pad_x = 16 * scale
        pad_y = 10 * scale
        box_w = min(max_w, wrapped.width() + pad_x * 2)
        box_h = wrapped.height() + pad_y * 2
        box_x = (w - box_w) / 2.0
        box_y = h - box_h - max(18.0 * scale, h * 0.045)
        box = QRectF(box_x, box_y, box_w, box_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 195))
        painter.drawRoundedRect(box, 8 * scale, 8 * scale)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(box, Qt.AlignCenter | Qt.TextWordWrap, caption_text)

    painter.end()
    return out


def render_annotations_on_bgr(frame, annotations, caption_text=None):
    """Apply vector annotations and optional caption onto an OpenCV BGR or BGRA frame."""
    if frame is None or (not annotations and not caption_text):
        return frame
    has_alpha = frame.ndim == 3 and frame.shape[2] == 4
    h, w = frame.shape[:2]
    if has_alpha:
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        qimg = QImage(rgba.data, w, h, int(rgba.strides[0]), QImage.Format_RGBA8888).copy()
    else:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format_RGB888).copy()

    rendered = render_annotations_on_qimage(qimg, annotations, caption_text=caption_text)
    conv = rendered.convertToFormat(QImage.Format_RGBA8888)
    ptr = conv.bits()
    ptr.setsize(conv.byteCount())
    stride = conv.bytesPerLine()
    raw = np.frombuffer(ptr, np.uint8).reshape((h, stride))[:, : w * 4].reshape((h, w, 4)).copy()
    if has_alpha:
        return cv2.cvtColor(raw, cv2.COLOR_RGBA2BGRA)
    return cv2.cvtColor(raw, cv2.COLOR_RGBA2BGR)


def make_transparent_overlay_png(width, height, annotations, path):
    """Save a transparent PNG of `annotations` at `width`x`height` for FFmpeg video overlay."""
    image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    rendered = render_annotations_on_qimage(image, annotations)
    rendered.save(path, "PNG")
    return path


class AnnotatedPreviewLabel(QLabel):
    """Interactive video preview widget supporting click-drag arrows, circles, rectangles, and text."""
    annotations_changed = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.tool = "none"  # 'none', 'arrow', 'circle', 'rect', 'text'
        self.color = "#ff3b30"
        self.thickness = 4
        self.default_duration = 3.0  # seconds a new markup stays on screen (0 = entire video)
        self.current_sec = 0.0
        self.video_duration = 999999.0
        self.freeze_at = None
        self.freeze_duration = 0.0
        self.annotations = []
        self._drag_current = None
        self._base_qimage = QImage()
        self._caption_preview = ""

    def set_base_qimage(self, qimage, caption_text="", current_sec=None):
        self._base_qimage = qimage
        self._caption_preview = caption_text or ""
        if current_sec is not None:
            self.current_sec = float(current_sec)
        self.refresh_display()

    def visible_annotations_at(self, sec=None):
        t = self.current_sec if sec is None else float(sec)
        return [ann for ann in self.annotations if ann.is_visible_at(t)]

    def _new_time_window(self):
        if not self.default_duration or self.default_duration <= 0:
            return 0.0, 999999.0
        start = round(self.current_sec, 2)
        end = round(min(self.video_duration, start + float(self.default_duration)), 2)
        return start, max(start + 0.1, end)

    def clear_annotations(self):
        if self.annotations:
            self.annotations.clear()
            self._drag_current = None
            self.refresh_display()
            self.annotations_changed.emit()

    def undo_annotation(self):
        if self.annotations:
            self.annotations.pop()
            self.refresh_display()
            self.annotations_changed.emit()

    def _displayed_image_rect(self):
        if self._base_qimage.isNull():
            return QRectF()
        iw = self._base_qimage.width()
        ih = self._base_qimage.height()
        lw = self.width()
        lh = self.height()
        if iw <= 0 or ih <= 0 or lw <= 0 or lh <= 0:
            return QRectF()
        scale = min(lw / float(iw), lh / float(ih))
        dw = iw * scale
        dh = ih * scale
        dx = (lw - dw) / 2.0
        dy = (lh - dh) / 2.0
        return QRectF(dx, dy, dw, dh)

    def _pos_to_norm(self, pos):
        rect = self._displayed_image_rect()
        if rect.isEmpty():
            return None
        nx = (pos.x() - rect.left()) / rect.width()
        ny = (pos.y() - rect.top()) / rect.height()
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    def mousePressEvent(self, event):
        if self.tool == "none" or self._base_qimage.isNull() or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        norm = self._pos_to_norm(event.pos())
        if norm is None:
            return
        nx, ny = norm
        start_s, end_s = self._new_time_window()
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, "Add Title / Callout", "Enter text:")
            if ok and text.strip():
                self.annotations.append(
                    Annotation(
                        kind="text", x1=nx, y1=ny,
                        color=self.color, thickness=self.thickness,
                        text=text.strip(),
                        start_sec=start_s, end_sec=end_s,
                    )
                )
                self.refresh_display()
                self.annotations_changed.emit()
            return
        self._drag_current = Annotation(
            kind=self.tool, x1=nx, y1=ny, x2=nx, y2=ny,
            color=self.color, thickness=self.thickness,
            start_sec=start_s, end_sec=end_s,
        )
        self.refresh_display()

    def mouseMoveEvent(self, event):
        if self._drag_current is None:
            super().mouseMoveEvent(event)
            return
        norm = self._pos_to_norm(event.pos())
        if norm is None:
            return
        self._drag_current.x2, self._drag_current.y2 = norm
        self.refresh_display()

    def mouseReleaseEvent(self, event):
        if self._drag_current is None or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        norm = self._pos_to_norm(event.pos())
        if norm is not None:
            self._drag_current.x2, self._drag_current.y2 = norm
        dx = abs(self._drag_current.x2 - self._drag_current.x1)
        dy = abs(self._drag_current.y2 - self._drag_current.y1)
        if dx > 0.008 or dy > 0.008:
            self.annotations.append(self._drag_current)
            self.annotations_changed.emit()
        self._drag_current = None
        self.refresh_display()

    def refresh_display(self):
        if self._base_qimage.isNull():
            return
        active = self.visible_annotations_at(self.current_sec)
        if self._drag_current is not None:
            active.append(self._drag_current)
        composed = render_annotations_on_qimage(
            self._base_qimage, active, caption_text=self._caption_preview
        )
        # Draw non-exported preview HUD badges for active Markup & Freeze Hold
        hud_pills = []
        if active:
            last_a = active[-1]
            if last_a.end_sec >= 99999.0 and last_a.start_sec <= 0.01:
                hud_pills.append(("#ff9f0a", "🖍 Markup: All Clip"))
            else:
                hud_pills.append(
                    ("#ff9f0a", f"🖍 Markup ON: {last_a.start_sec:.2f}s → {min(self.video_duration, last_a.end_sec):.2f}s")
                )
        if self.freeze_at is not None and self.freeze_duration > 0:
            end_hold = self.freeze_at + self.freeze_duration
            if abs(self.current_sec - self.freeze_at) <= 0.08:
                hud_pills.append(
                    ("#00d2ff", f"❄️ HOLD HERE: {self.freeze_at:.2f}s → +{self.freeze_duration:.1f}s (Ends {end_hold:.2f}s)")
                )
            else:
                hud_pills.append(
                    ("#2a6ada", f"❄️ Hold Set @ {self.freeze_at:.2f}s (+{self.freeze_duration:.1f}s)")
                )
        if hud_pills:
            w, h = composed.width(), composed.height()
            scale = max(1.0, min(w, h) / 720.0)
            p = QPainter(composed)
            p.setRenderHint(QPainter.Antialiasing, True)
            font = QFont("Helvetica Neue", max(11, int(round(13 * scale))))
            font.setBold(True)
            p.setFont(font)
            fm = QFontMetrics(font)
            y_off = int(12 * scale)
            for bg_hex, label_txt in hud_pills:
                tw = fm.horizontalAdvance(label_txt)
                th = fm.height()
                pad_x = int(10 * scale)
                pad_y = int(5 * scale)
                rect = QRectF(12 * scale, y_off, tw + pad_x * 2, th + pad_y * 2)
                c = QColor(bg_hex)
                c.setAlpha(215)
                p.setPen(QPen(QColor("#ffffff"), max(1.0, 1.2 * scale)))
                p.setBrush(c)
                p.drawRoundedRect(rect, 6 * scale, 6 * scale)
                p.setPen(QColor("#0b0e17") if bg_hex in ("#00d2ff", "#ff9f0a") else QColor("#ffffff"))
                p.drawText(rect, Qt.AlignCenter, label_txt)
                y_off += int(th + pad_y * 2 + 6 * scale)
            p.end()
        pixmap = QPixmap.fromImage(composed)
        scaled = pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        super().setPixmap(scaled)


class TimelineMapBar(QWidget):
    """Interactive visual timeline bar below the scrubber showing Trim [In..Out], Markup spans,
    Freeze-Frame Hold, Edit Cuts, and current Playhead.
    Supports clicking & dragging the green In/Out handles, Markup bars, Hold Frame bar, or Playhead.
    """
    trim_in_changed = pyqtSignal(float)
    trim_out_changed = pyqtSignal(float)
    playhead_scrubbed = pyqtSignal(float)
    markup_span_changed = pyqtSignal(int, float, float)
    freeze_moved = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMouseTracking(True)
        self.setToolTip(
            "Drag the green [ In or Out ] lines to adjust trim (or double-click a green line to reset it).\n"
            "Drag colored Markup bars or the cyan Hold bar to move/resize them, or click anywhere to scrub."
        )
        self.duration = 0.0
        self.current_sec = 0.0
        self.trim_in = 0.0
        self.trim_out = 0.0
        self.annotations = []
        self.freeze_at = None
        self.freeze_duration = 0.0
        self.cuts = []
        self._drag_mode = None  # 'in', 'out', 'playhead', 'markup_move', 'markup_end', 'freeze'
        self._drag_ann_idx = -1
        self._drag_offset_sec = 0.0

    def update_state(
        self,
        duration,
        current_sec,
        trim_in,
        trim_out,
        annotations,
        freeze_at,
        freeze_duration,
        cuts,
    ):
        self.duration = max(0.01, float(duration or 0.01))
        self.current_sec = float(current_sec or 0.0)
        self.trim_in = float(trim_in or 0.0)
        self.trim_out = float(trim_out if trim_out is not None else self.duration)
        self.annotations = list(annotations or [])
        self.freeze_at = freeze_at
        self.freeze_duration = float(freeze_duration or 0.0)
        self.cuts = list(cuts or [])
        self.update()

    def _sec_to_x(self, sec):
        w = max(1, self.width() - 16)
        denom = max(0.01, float(self.duration or 0.01))
        ratio = max(0.0, min(1.0, float(sec or 0.0) / denom))
        return 8.0 + ratio * w

    def _x_to_sec(self, x):
        w = max(1, self.width() - 16)
        denom = max(0.01, float(self.duration or 0.01))
        ratio = max(0.0, min(1.0, (float(x) - 8.0) / w))
        return round(ratio * denom, 2)

    def _hit_test(self, pos):
        if self.duration <= 0.01:
            return None, -1
        px, py = pos.x(), pos.y()
        x_in = self._sec_to_x(self.trim_in)
        x_out = self._sec_to_x(self.trim_out)
        # Check Out handle first so if In and Out are close, Out is easy to grab and pull right
        if abs(px - x_out) <= 8:
            return "out", -1
        if abs(px - x_in) <= 8:
            return "in", -1
        if self.freeze_at is not None and self.freeze_duration > 0 and py >= self.height() // 2:
            fx1 = self._sec_to_x(self.freeze_at)
            fx2 = max(fx1 + 8.0, self._sec_to_x(min(self.duration, self.freeze_at + self.freeze_duration)))
            if fx1 - 4 <= px <= fx2 + 4:
                return "freeze", -1
        for idx in range(len(self.annotations) - 1, -1, -1):
            ann = self.annotations[idx]
            ax1 = self._sec_to_x(max(0.0, ann.start_sec))
            ax2 = max(ax1 + 6.0, self._sec_to_x(min(self.duration, ann.end_sec)))
            if py <= self.height() // 2 + 2:
                if abs(px - ax2) <= 6:
                    return "markup_end", idx
                if ax1 <= px <= ax2:
                    return "markup_move", idx
        return "playhead", -1

    def mousePressEvent(self, event):
        if self.duration <= 0.01 or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        mode, idx = self._hit_test(event.pos())
        self._drag_mode = mode
        self._drag_ann_idx = idx
        sec = self._x_to_sec(event.pos().x())
        if mode == "markup_move" and 0 <= idx < len(self.annotations):
            self._drag_offset_sec = sec - self.annotations[idx].start_sec
        elif mode == "freeze" and self.freeze_at is not None:
            self._drag_offset_sec = sec - self.freeze_at
        elif mode == "playhead":
            self.playhead_scrubbed.emit(sec)
        self.update()

    def mouseMoveEvent(self, event):
        if self.duration <= 0.01:
            super().mouseMoveEvent(event)
            return
        if self._drag_mode is None:
            mode, _ = self._hit_test(event.pos())
            if mode in ("in", "out", "markup_end"):
                self.setCursor(Qt.SplitHCursor)
            elif mode in ("markup_move", "freeze"):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.PointingHandCursor)
            return

        sec = self._x_to_sec(event.pos().x())
        if self._drag_mode == "in":
            new_in = max(0.0, min(sec, self.trim_out - 0.1))
            self.trim_in = new_in
            self.trim_in_changed.emit(new_in)
        elif self._drag_mode == "out":
            new_out = max(self.trim_in + 0.1, min(sec, self.duration))
            self.trim_out = new_out
            self.trim_out_changed.emit(new_out)
        elif self._drag_mode == "playhead":
            self.playhead_scrubbed.emit(sec)
        elif self._drag_mode == "markup_end" and 0 <= self._drag_ann_idx < len(self.annotations):
            ann = self.annotations[self._drag_ann_idx]
            new_end = max(ann.start_sec + 0.2, min(self.duration, sec))
            self.markup_span_changed.emit(self._drag_ann_idx, ann.start_sec, new_end)
        elif self._drag_mode == "markup_move" and 0 <= self._drag_ann_idx < len(self.annotations):
            ann = self.annotations[self._drag_ann_idx]
            span = max(0.2, min(self.duration, ann.end_sec) - ann.start_sec)
            new_start = max(0.0, min(self.duration - span, sec - self._drag_offset_sec))
            self.markup_span_changed.emit(self._drag_ann_idx, round(new_start, 2), round(new_start + span, 2))
        elif self._drag_mode == "freeze" and self.freeze_at is not None:
            new_f = max(0.0, min(self.duration, round(sec - self._drag_offset_sec, 2)))
            self.freeze_moved.emit(new_f)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None
        self._drag_ann_idx = -1
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.duration <= 0.01:
            return
        mode, _ = self._hit_test(event.pos())
        if mode == "out":
            self.trim_out = round(self.duration, 2)
            self.trim_out_changed.emit(self.trim_out)
        elif mode == "in":
            self.trim_in = 0.0
            self.trim_in_changed.emit(0.0)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            w = self.width()
            h = self.height()

            # Track background
            track = QRectF(8, 3, max(1, w - 16), h - 6)
            painter.setPen(QPen(QColor("#3a3a55"), 1))
            painter.setBrush(QColor("#1a1a28"))
            painter.drawRoundedRect(track, 4, 4)

            if self.duration <= 0.0:
                return

            # Trim In..Out region with draggable bracket handles
            x_in = self._sec_to_x(self.trim_in)
            x_out = self._sec_to_x(self.trim_out)
            if x_out >= x_in:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(46, 204, 113, 45))
                painter.drawRect(QRectF(x_in, 4, max(2.0, x_out - x_in), h - 8))
                pen_trim = QPen(QColor("#2ecc71"), 3)
                painter.setPen(pen_trim)
                # Left '[' handle
                painter.drawLine(QPointF(x_in, 2), QPointF(x_in, h - 2))
                painter.drawLine(QPointF(x_in, 2), QPointF(x_in + 5, 2))
                painter.drawLine(QPointF(x_in, h - 2), QPointF(x_in + 5, h - 2))
                # Right ']' handle
                painter.drawLine(QPointF(x_out, 2), QPointF(x_out, h - 2))
                painter.drawLine(QPointF(x_out - 5, 2), QPointF(x_out, 2))
                painter.drawLine(QPointF(x_out - 5, h - 2), QPointF(x_out, h - 2))

            # Scene cut ticks (drawn inside active trim range or subtly outside)
            for cut in self.cuts:
                cx = self._sec_to_x(cut)
                in_bounds = self.trim_in - 0.02 <= cut <= self.trim_out + 0.02
                painter.setPen(QPen(QColor("#ff9f0a" if in_bounds else "#665030"), 1.8 if in_bounds else 1.0))
                painter.drawLine(QPointF(cx, 4), QPointF(cx, h - 4))

            # Markup spans (top lane)
            for ann in self.annotations:
                s = max(0.0, ann.start_sec)
                e = min(self.duration, ann.end_sec)
                ax1 = self._sec_to_x(s)
                ax2 = max(ax1 + 5.0, self._sec_to_x(e))
                painter.setPen(QPen(QColor("#ffffff"), 1))
                c = QColor(ann.color)
                c.setAlpha(200)
                painter.setBrush(c)
                painter.drawRoundedRect(QRectF(ax1, 4, ax2 - ax1, 8), 2, 2)

            # Freeze hold marker & span (bottom lane — always drawn brightly on top!)
            if self.freeze_at is not None and self.freeze_duration > 0:
                fx1 = self._sec_to_x(self.freeze_at)
                fx2 = max(fx1 + 8.0, self._sec_to_x(min(self.duration, self.freeze_at + self.freeze_duration)))
                painter.setPen(QPen(QColor("#ffffff"), 1.2))
                painter.setBrush(QColor(0, 210, 255, 225))
                painter.drawRoundedRect(QRectF(fx1, h - 12, fx2 - fx1, 8), 2, 2)

            # Playhead line
            px = self._sec_to_x(self.current_sec)
            painter.setPen(QPen(QColor("#ffffff"), 2.2))
            painter.drawLine(QPointF(px, 1), QPointF(px, h - 1))
        finally:
            painter.end()


def list_avfoundation_microphones(ffmpeg):
    """Return list of (index_str, device_name) for macOS AVFoundation audio inputs."""
    if not ffmpeg or os.uname().sysname != "Darwin":
        return []
    try:
        res = subprocess.run(
            [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    devices = []
    in_audio = False
    for line in res.stderr.splitlines():
        if "AVFoundation audio devices:" in line:
            in_audio = True
            continue
        if in_audio:
            m = re.search(r"\[(\d+)\]\s+(.+)$", line)
            if m:
                idx, name = m.group(1), m.group(2).strip()
                devices.append((idx, name))
    return devices


def _bundled_whisper_model(model_size="tiny"):
    """Return local bundled path for faster-whisper model if present inside the app/repo."""
    candidates = [
        os.path.join(getattr(__import__("sys"), "_MEIPASS", ""), "assets", f"whisper-{model_size}"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", f"whisper-{model_size}"),
    ]
    for path in candidates:
        if path and os.path.isdir(path) and os.path.isfile(os.path.join(path, "model.bin")):
            return path
    return model_size


class WhisperTranscribeWorker(QThread):
    """Background worker that transcribes video/audio into CaptionCue items using faster-whisper."""
    progress_text = pyqtSignal(str)
    finished_cues = pyqtSignal(list, str)  # (cues, error_message)

    def __init__(self, media_path, start_sec=0.0, end_sec=None, model_size="tiny", parent=None):
        super().__init__(parent)
        self.media_path = media_path
        self.start_sec = float(start_sec or 0.0)
        self.end_sec = float(end_sec) if end_sec is not None else None
        self.model_size = model_size

    def run(self):
        try:
            from faster_whisper import WhisperModel
            model_ref = _bundled_whisper_model(self.model_size)
            self.progress_text.emit("Loading offline Whisper speech model…")
            model = WhisperModel(model_ref, device="cpu", compute_type="int8")
            self.progress_text.emit("Transcribing speech to timed captions…")
            segments, _info = model.transcribe(
                self.media_path, beam_size=3, vad_filter=True
            )
            cues = []
            for seg in segments:
                s = float(seg.start)
                e = float(seg.end)
                if self.end_sec is not None and s >= self.end_sec:
                    continue
                if e <= self.start_sec:
                    continue
                text = seg.text.strip()
                if text:
                    cues.append(CaptionCue(start=max(self.start_sec, s), end=e, text=text))
            self.finished_cues.emit(cues, "")
        except Exception as exc:
            self.finished_cues.emit([], str(exc))


def _has_audio_stream(ffmpeg, video_path):
    try:
        res = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", video_path],
            capture_output=True, text=True, timeout=15,
        )
        return " Audio: " in res.stderr
    except Exception:
        return False


def export_audio_clip(ffmpeg, video_path, out_path, start_sec=0.0, end_sec=None,
                      fade_in=0.0, fade_out=0.0, fmt="mp3", bitrate=192):
    """Extract audio (MP3 or WAV) from `[start_sec, end_sec]` with optional fade in/out."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostdin"]
    if start_sec and start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    if end_sec is not None and end_sec > start_sec:
        cmd += ["-to", f"{end_sec:.3f}"]
    cmd += ["-i", video_path, "-vn"]

    af_filters = []
    if fade_in and fade_in > 0:
        af_filters.append(f"afade=t=in:st=0:d={fade_in:.2f}")
    if fade_out and fade_out > 0 and end_sec is not None and end_sec > start_sec:
        clip_dur = max(0.1, end_sec - start_sec)
        st = max(0.0, clip_dur - fade_out)
        af_filters.append(f"afade=t=out:st={st:.2f}:d={fade_out:.2f}")
    if af_filters:
        cmd += ["-af", ",".join(af_filters)]

    if fmt.lower() == "wav":
        cmd += ["-c:a", "pcm_s16le", out_path]
    else:
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{int(bitrate)}k", out_path]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip().splitlines()[-1] if res.stderr else "FFmpeg audio export failed")
    return out_path


def export_video_or_gif(
    ffmpeg,
    video_path,
    out_path,
    start_sec=0.0,
    end_sec=None,
    fade_in=0.0,
    fade_out=0.0,
    annotations=None,
    freeze_at=None,
    freeze_duration=0.0,
    captions=None,
    narration_path=None,
    narration_mode="duck",  # 'duck', 'replace', or 'mix'
    as_gif=False,
    gif_fps=12,
    gif_width=480,
    mp4_quality="high",  # 'high', 'medium', 'small'
):
    """Render a trimmed MP4 or palette-optimized GIF with optional markup, freeze-frame hold,
    fade in/out, burned-in captions, and voiceover narration.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360)
    video_dur = total_frames / fps if fps > 0 else 0.0

    s_sec = max(0.0, float(start_sec or 0.0))
    e_sec = float(end_sec) if (end_sec is not None and end_sec > s_sec) else video_dur
    e_sec = min(video_dur, max(s_sec + 0.05, e_sec))

    annotations = list(annotations or [])
    captions = list(captions or [])
    has_freeze = (
        freeze_at is not None
        and freeze_duration
        and freeze_duration > 0
        and s_sec <= freeze_at <= e_sec
    )

    def active_caption(sec):
        for cue in captions:
            if cue.start <= sec <= cue.end:
                return cue.text
        return None

    crf_val = "18"
    max_h = 0
    if not as_gif:
        if mp4_quality == "medium":
            crf_val = "23"
            max_h = 720
        elif mp4_quality == "small":
            crf_val = "28"
            max_h = 480

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_video = os.path.join(tmpdir, "rendered_video.mp4")
        start_f = max(0, int(round(s_sec * fps)))
        end_f = min(max(start_f, total_frames - 1), int(round(e_sec * fps)))
        freeze_f = int(round(freeze_at * fps)) if has_freeze else -1
        freeze_hold_frames = int(round(freeze_duration * fps)) if has_freeze else 0

        target_w, target_h = width, height
        if max_h > 0 and height > max_h:
            scale_r = max_h / float(height)
            target_w = max(2, int(round(width * scale_r)))
            target_h = max_h
        out_w = target_w if target_w % 2 == 0 else target_w - 1
        out_h = target_h if target_h % 2 == 0 else target_h - 1
        total_out_frames = (end_f - start_f + 1) + (freeze_hold_frames if has_freeze else 0)
        fade_in_frames = int(round(fade_in * fps)) if fade_in > 0 else 0
        fade_out_frames = int(round(fade_out * fps)) if fade_out > 0 else 0

        encode_cmd = [
            ffmpeg, "-y", "-hide_banner", "-nostdin",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{out_w}x{out_h}", "-r", f"{fps:.4f}",
            "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val,
            "-pix_fmt", "yuv420p",
            raw_video,
        ]
        proc = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
        out_idx = 0
        frozen_done = False

        def apply_fade(bgr, idx):
            factor = 1.0
            if fade_in_frames > 0 and idx < fade_in_frames:
                factor = min(factor, idx / float(fade_in_frames))
            if fade_out_frames > 0 and idx >= total_out_frames - fade_out_frames:
                rem = max(0, total_out_frames - 1 - idx)
                factor = min(factor, rem / float(fade_out_frames))
            if factor < 0.999:
                return (bgr.astype(np.float32) * max(0.0, factor)).astype(np.uint8)
            return bgr

        try:
            for f_idx in range(start_f, end_f + 1):
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if frame.ndim == 3 and frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                if frame.shape[1] != out_w or frame.shape[0] != out_h:
                    frame = cv2.resize(frame, (out_w, out_h))

                cur_sec = f_idx / fps
                cap_text = active_caption(cur_sec)

                # Show any markup whose [start_sec, end_sec] window includes cur_sec
                visible_now = [a for a in annotations if a.is_visible_at(cur_sec)]
                composed = render_annotations_on_bgr(frame, visible_now, caption_text=cap_text)
                composed = apply_fade(composed, out_idx)
                proc.stdin.write(composed.tobytes())
                out_idx += 1

                if has_freeze and not frozen_done and f_idx >= freeze_f:
                    frozen_done = True
                    freeze_anns = [a for a in annotations if a.is_visible_at(freeze_at)] or annotations
                    freeze_frame = render_annotations_on_bgr(frame, freeze_anns, caption_text=cap_text)
                    for _ in range(freeze_hold_frames):
                        faded = apply_fade(freeze_frame, out_idx)
                        proc.stdin.write(faded.tobytes())
                        out_idx += 1
        finally:
            cap.release()
            if proc.stdin:
                proc.stdin.close()
            stderr_bytes = proc.stderr.read() if proc.stderr else b""
            if proc.stderr:
                proc.stderr.close()
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(stderr_bytes.decode("utf-8", "replace")[-400:])

        if as_gif:
            scale_expr = f"scale={int(gif_width)}:-1:flags=lanczos" if gif_width else "null"
            vf = (
                f"fps={int(gif_fps)},{scale_expr},"
                f"split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
                f"[s1][p]paletteuse=dither=bayer:bayer_scale=5"
            )
            gif_cmd = [
                ffmpeg, "-y", "-hide_banner", "-nostdin",
                "-i", raw_video, "-vf", vf, "-loop", "0", out_path,
            ]
            res = subprocess.run(gif_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(res.stderr.strip()[-400:])
            return out_path

        # Build audio track for MP4
        has_src_audio = _has_audio_stream(ffmpeg, video_path)
        has_narr = bool(narration_path and os.path.isfile(narration_path))
        total_clip_dur = out_idx / fps if fps > 0 else (e_sec - s_sec)

        if not has_src_audio and not has_narr:
            shutil.move(raw_video, out_path)
            return out_path

        mux_cmd = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-i", raw_video]
        filter_parts = []
        audio_out_label = None

        if has_src_audio and (not has_narr or narration_mode in ("duck", "mix")):
            if has_freeze:
                post_start = freeze_at
                mux_cmd += ["-i", video_path]
                filter_parts.append(
                    f"[1:a]atrim=start={s_sec:.3f}:end={freeze_at:.3f},asetpts=PTS-STARTPTS[a_pre];"
                    f"aevalsrc=0:d={freeze_duration:.3f}[a_sil];"
                    f"[1:a]atrim=start={post_start:.3f}:end={e_sec:.3f},asetpts=PTS-STARTPTS[a_post];"
                    f"[a_pre][a_sil][a_post]concat=n=3:v=0:a=1[src_a]"
                )
            else:
                mux_cmd += ["-ss", f"{s_sec:.3f}", "-to", f"{e_sec:.3f}", "-i", video_path]
                filter_parts.append("[1:a]asetpts=PTS-STARTPTS[src_a]")
            audio_out_label = "[src_a]"

        if has_narr:
            narr_idx = 2 if (has_src_audio and narration_mode in ("duck", "mix")) else 1
            mux_cmd += ["-i", narration_path]
            if audio_out_label and narration_mode in ("duck", "mix"):
                vol = "0.22" if narration_mode == "duck" else "1.0"
                filter_parts.append(
                    f"{audio_out_label}volume={vol}[ducked];"
                    f"[{narr_idx}:a]asetpts=PTS-STARTPTS[narr];"
                    f"[ducked][narr]amix=inputs=2:duration=first:dropout_transition=0[mixed_a]"
                )
                audio_out_label = "[mixed_a]"
            else:
                filter_parts.append(f"[{narr_idx}:a]asetpts=PTS-STARTPTS[narr_a]")
                audio_out_label = "[narr_a]"

        af_tail = []
        if fade_in and fade_in > 0:
            af_tail.append(f"afade=t=in:st=0:d={fade_in:.2f}")
        if fade_out and fade_out > 0:
            st = max(0.0, total_clip_dur - fade_out)
            af_tail.append(f"afade=t=out:st={st:.2f}:d={fade_out:.2f}")
        if af_tail:
            filter_parts.append(f"{audio_out_label}{','.join(af_tail)}[final_a]")
            audio_out_label = "[final_a]"

        mux_cmd += [
            "-filter_complex", ";".join(filter_parts),
            "-map", "0:v:0", "-map", audio_out_label,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", out_path,
        ]
        res = subprocess.run(mux_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback to video-only if audio filter fails on an unusual stream
            shutil.copy2(raw_video, out_path)
        return out_path


def split_video_by_cuts(
    ffmpeg,
    video_path,
    out_folder,
    cut_seconds,
    fade_in=0.0,
    fade_out=0.0,
    start_sec=0.0,
    end_sec=None,
    mp4_quality="high",
    progress_cb=None,
):
    """Split `video_path` at `cut_seconds` within `[start_sec, end_sec]` into numbered
    Adobe-ready MP4 segments in `out_folder`.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = total_f / fps if fps > 0 else 0.0
    cap.release()

    lo = max(0.0, float(start_sec or 0.0))
    hi = float(end_sec) if (end_sec is not None and float(end_sec) > lo + 0.05) else dur
    hi = min(dur, max(lo + 0.1, hi))

    boundaries = sorted(
        {round(lo, 2)}
        | {round(float(c), 2) for c in (cut_seconds or []) if lo + 0.05 < float(c) < hi - 0.05}
        | {round(hi, 2)}
    )
    segments = [
        (boundaries[i], boundaries[i + 1])
        for i in range(len(boundaries) - 1)
        if boundaries[i + 1] - boundaries[i] >= 0.15
    ]
    os.makedirs(out_folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    created = []
    crf_val = {"high": "18", "medium": "23", "small": "28"}.get(mp4_quality, "18")

    for idx, (s_sec, e_sec) in enumerate(segments, 1):
        if progress_cb:
            progress_cb(idx - 1, len(segments), f"Splitting segment {idx}/{len(segments)}…")
        out_path = os.path.join(
            out_folder, f"{stem}_segment_{idx:03d}_{s_sec:.2f}s-{e_sec:.2f}s.mp4"
        )
        if fade_in > 0 or fade_out > 0 or mp4_quality != "high":
            export_video_or_gif(
                ffmpeg, video_path, out_path,
                start_sec=s_sec, end_sec=e_sec,
                fade_in=fade_in, fade_out=fade_out,
                mp4_quality=mp4_quality,
            )
        else:
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-nostdin",
                "-ss", f"{s_sec:.3f}", "-to", f"{e_sec:.3f}",
                "-i", video_path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", out_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(res.stderr.strip()[-300:])
        created.append(out_path)

    if progress_cb:
        progress_cb(len(segments), len(segments), "Done")
    return created


def _escape_ffmetadata(text):
    """Escape special characters (`=`, `;`, `#`, `\\`, newline) for FFMETADATA1."""
    out = str(text or "").replace("\\", "\\\\")
    for ch in ("=", ";", "#", "\n", "\r"):
        out = out.replace(ch, f"\\{ch}")
    return out


def build_chapter_spans(cut_seconds, duration, chapter_titles=None):
    """Convert scene cut timestamps into a list of `(start_sec, end_sec, title)` chapters."""
    dur = max(0.2, float(duration or 0.2))
    titles = dict(chapter_titles or {})
    boundaries = sorted(
        {0.0}
        | {round(float(c), 2) for c in (cut_seconds or []) if 0.05 < float(c) < dur - 0.05}
        | {round(dur, 2)}
    )
    chapters = []
    for idx in range(len(boundaries) - 1):
        s_sec = boundaries[idx]
        e_sec = boundaries[idx + 1]
        if e_sec - s_sec < 0.05:
            continue
        custom = titles.get(round(s_sec, 2)) or titles.get(s_sec)
        title = custom.strip() if custom and str(custom).strip() else f"Chapter {len(chapters) + 1}"
        chapters.append((s_sec, e_sec, title))
    return chapters


def build_ffmetadata_chapters(cut_seconds, duration, chapter_titles=None):
    """Build an FFmpeg `FFMETADATA1` string containing `[CHAPTER]` markers."""
    chapters = build_chapter_spans(cut_seconds, duration, chapter_titles=chapter_titles)
    lines = [";FFMETADATA1"]
    for s_sec, e_sec, title in chapters:
        start_ms = max(0, int(round(s_sec * 1000)))
        end_ms = max(start_ms + 1, int(round(e_sec * 1000)))
        lines.extend(
            [
                "",
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={_escape_ffmetadata(title)}",
            ]
        )
    return "\n".join(lines) + "\n", chapters


def _fix_mp4_quicktime_chapter_locale(mp4_path):
    """Patch ISO-BMFF `mdhd` language codes inside the `moov` atom from `0x0000`/`0x55c4` (`und`)
    to `0x15c7` (`eng`) so macOS QuickTime Player (`AVURLAsset.chapterMetadataGroups(bestMatchingPreferredLanguages:)`)
    displays the chapter markers immediately.
    """
    try:
        with open(mp4_path, "rb") as fh:
            data = bytearray(fh.read())
    except OSError:
        return

    offset = 0
    n = len(data)
    moov_start, moov_end = 0, min(n, 4 * 1024 * 1024)
    while offset + 8 <= n:
        box_size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = bytes(data[offset + 4:offset + 8])
        if box_size == 1 and offset + 16 <= n:
            box_size = int.from_bytes(data[offset + 8:offset + 16], "big")
        elif box_size == 0:
            box_size = n - offset
        if box_size < 8 or offset + box_size > n:
            break
        if box_type == b"moov":
            moov_start = offset
            moov_end = offset + box_size
            break
        offset += box_size

    changed = False
    idx = moov_start
    while idx < moov_end:
        pos = data.find(b"mdhd", idx, moov_end)
        if pos == -1 or pos < 4:
            break
        box_len = int.from_bytes(data[pos - 4:pos], "big")
        version = data[pos + 4] if pos + 5 <= moov_end else 99
        if box_len in (32, 44) and version in (0, 1):
            lang_offset = pos + 4 + (32 if version == 1 else 20)
            if lang_offset + 2 <= moov_end:
                lang_val = int.from_bytes(data[lang_offset:lang_offset + 2], "big")
                if lang_val in (0x0000, 0x55C4, 0x7FFF):
                    data[lang_offset:lang_offset + 2] = (0x15C7).to_bytes(2, "big")
                    changed = True
        idx = pos + 4

    if changed:
        with open(mp4_path, "wb") as fh:
            fh.write(data)


def export_mp4_with_chapters(
    ffmpeg,
    video_path,
    out_path,
    cut_seconds,
    chapter_titles=None,
    start_sec=0.0,
    end_sec=None,
):
    """Embed scene-cut / custom chapter points into an MP4 file using FFmpeg `FFMETADATA1`
    and QuickTime-compatible `eng` `mdhd` language tags.
    Respects `[start_sec, end_sec]` trim boundaries if provided.
    Returns `(out_path, chapters)`.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = total_f / fps if fps > 0 else 0.0
    cap.release()

    lo = max(0.0, float(start_sec or 0.0))
    hi = float(end_sec) if (end_sec is not None and float(end_sec) > lo + 0.05) else dur
    hi = min(dur, max(lo + 0.1, hi))
    clip_dur = round(hi - lo, 2)
    is_trimmed = (lo > 0.05) or (hi < dur - 0.05)

    titles_in = dict(chapter_titles or {})
    rel_cuts = []
    rel_titles = {0.0: titles_in.get(round(lo, 2)) or titles_in.get(0.0) or "Chapter 1"}
    for c in sorted(cut_seconds or []):
        fc = float(c)
        if lo + 0.05 < fc < hi - 0.05:
            rc = round(fc - lo, 2)
            rel_cuts.append(rc)
            if round(fc, 2) in titles_in:
                rel_titles[rc] = titles_in[round(fc, 2)]
            elif rc in titles_in:
                rel_titles[rc] = titles_in[rc]

    meta_text, chapters = build_ffmetadata_chapters(
        rel_cuts, clip_dur, chapter_titles=rel_titles
    )
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        meta_path = os.path.join(tmpdir, "chapters.ffmeta")
        with open(meta_path, "w", encoding="utf-8") as fh:
            fh.write(meta_text)

        trim_args = ["-ss", f"{lo:.3f}", "-to", f"{hi:.3f}"] if is_trimmed else []
        copy_cmd = [
            ffmpeg, "-y", "-hide_banner", "-nostdin",
            *trim_args,
            "-i", video_path,
            "-f", "ffmetadata", "-i", meta_path,
            "-map", "0:v:0", "-map", "0:a?",
            "-map_metadata", "1",
            "-map_chapters", "1",
            "-c:v", "copy", "-c:a", "copy",
            "-metadata:s:v:0", "language=eng",
            "-metadata:s:a:0", "language=eng",
            "-movflags", "+faststart+disable_chpl",
            out_path,
        ]
        res = subprocess.run(copy_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            reencode_cmd = [
                ffmpeg, "-y", "-hide_banner", "-nostdin",
                *trim_args,
                "-i", video_path,
                "-f", "ffmetadata", "-i", meta_path,
                "-map", "0:v:0", "-map", "0:a?",
                "-map_metadata", "1",
                "-map_chapters", "1",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-metadata:s:v:0", "language=eng",
                "-metadata:s:a:0", "language=eng",
                "-movflags", "+faststart+disable_chpl",
                out_path,
            ]
            res2 = subprocess.run(reencode_cmd, capture_output=True, text=True)
            if res2.returncode != 0:
                raise RuntimeError(res2.stderr.strip()[-300:] if res2.stderr else "Failed to write MP4 chapters")

    _fix_mp4_quicktime_chapter_locale(out_path)
    return out_path, chapters
