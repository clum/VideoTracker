"""Manually author ROI keyframes for a video and save them as CSV."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QAbstractSpinBox,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


CSV_FIELDS = (
    "frame_index",
    "timestamp_seconds",
    "width",
    "height",
    "center_x",
    "center_y",
)


class WorkflowError(Exception):
    """An expected input or processing error suitable for a user message."""


@dataclass(frozen=True)
class RoiKeyframe:
    width: float
    height: float
    center_x: float
    center_y: float


class VideoReader:
    """Small random-access wrapper around OpenCV VideoCapture."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.capture = cv2.VideoCapture(str(path))
        if not self.capture.isOpened():
            self.capture.release()
            raise WorkflowError(f"OpenCV could not open the video: {path}")

        fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0:
            self.capture.release()
            raise WorkflowError(f"The video has an invalid frame rate: {fps!r}")
        if not math.isfinite(frame_count) or frame_count < 1:
            self.capture.release()
            raise WorkflowError("OpenCV could not determine the video's frame count.")

        self.fps = fps
        self.frame_count = int(frame_count)
        self.last_frame_index: int | None = None
        first_frame = self.read_frame(0)
        if first_frame is None:
            self.close()
            raise WorkflowError(f"The video contains no readable first frame: {path}")
        self.first_frame = first_frame
        self.frame_height, self.frame_width = first_frame.shape[:2]

    def read_frame(self, frame_index: int) -> Any | None:
        if not 0 <= frame_index < self.frame_count:
            return None
        if self.last_frame_index != frame_index - 1:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self.capture.read()
        if not ok or frame is None or frame.size == 0:
            self.last_frame_index = None
            return None
        self.last_frame_index = frame_index
        return frame

    def close(self) -> None:
        self.capture.release()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create manual ROI keyframes in a video and export them to CSV."
    )
    parser.add_argument("--video", type=Path, help="video file to annotate")
    parser.add_argument("--settings", type=Path, help="settings JSON containing ResultsDirectory")
    parser.add_argument("--keyframes", type=Path, help="optional CSV of keyframes to import at startup")
    return parser.parse_args(argv)


def get_startup_path(
    supplied_path: Path | None,
    title: str,
    file_filter: str,
) -> Path | None:
    if supplied_path is not None:
        path = supplied_path.expanduser().resolve()
        if not path.is_file():
            raise WorkflowError(f"Selected file does not exist or is not a file: {path}")
        return path

    selected, _ = QFileDialog.getOpenFileName(None, title, "", file_filter)
    if not selected:
        return None
    return Path(selected).expanduser().resolve()


def resolve_results_directory(settings_path: Path) -> Path:
    try:
        with settings_path.open("r", encoding="utf-8-sig") as settings_file:
            settings = json.load(settings_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Could not read settings JSON {settings_path}: {exc}") from exc

    if not isinstance(settings, dict):
        raise WorkflowError("Settings JSON must contain an object at its root.")
    raw_directory = settings.get("ResultsDirectory")
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        raise WorkflowError("Settings JSON must contain a non-empty 'ResultsDirectory' value.")

    results_directory = Path(raw_directory).expanduser()
    if not results_directory.is_absolute():
        results_directory = settings_path.parent / results_directory
    return results_directory.resolve()


def write_keyframes_csv(
    output_path: Path,
    fps: float,
    keyframes: dict[int, RoiKeyframe],
) -> None:
    """Write keyframes in the public CSV format, sorted by source frame."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for frame_index in sorted(keyframes):
            keyframe = keyframes[frame_index]
            writer.writerow(
                {
                    "frame_index": frame_index,
                    "timestamp_seconds": f"{frame_index / fps:.6f}",
                    "width": f"{keyframe.width:.6f}".rstrip("0").rstrip("."),
                    "height": f"{keyframe.height:.6f}".rstrip("0").rstrip("."),
                    "center_x": f"{keyframe.center_x:.6f}".rstrip("0").rstrip("."),
                    "center_y": f"{keyframe.center_y:.6f}".rstrip("0").rstrip("."),
                }
            )


def read_keyframes_csv(
    input_path: Path,
    frame_count: int,
    frame_width: int,
    frame_height: int,
) -> dict[int, RoiKeyframe]:
    """Read and validate a complete Step01a keyframe CSV before returning it."""
    try:
        with input_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            fields = reader.fieldnames
            if fields is None or len(fields) != len(set(fields)) or set(fields) != set(CSV_FIELDS):
                expected = ",".join(CSV_FIELDS)
                raise WorkflowError(f"Keyframe CSV must contain these columns: {expected}")

            keyframes: dict[int, RoiKeyframe] = {}
            for line_number, row in enumerate(reader, start=2):
                if None in row or any(row.get(field) is None or not row[field].strip() for field in CSV_FIELDS):
                    raise WorkflowError(f"Keyframe CSV row {line_number} is incomplete or has extra values.")
                try:
                    frame_index = int(row["frame_index"])
                    timestamp = float(row["timestamp_seconds"])
                    values = {
                        field: float(row[field])
                        for field in ("width", "height", "center_x", "center_y")
                    }
                except ValueError as exc:
                    raise WorkflowError(f"Keyframe CSV row {line_number} contains an invalid number.") from exc
                if not math.isfinite(timestamp) or any(not math.isfinite(value) for value in values.values()):
                    raise WorkflowError(f"Keyframe CSV row {line_number} contains a non-finite number.")
                if not 0 <= frame_index < frame_count:
                    raise WorkflowError(
                        f"Keyframe CSV row {line_number} has frame_index {frame_index}, "
                        f"outside this video's range 0–{frame_count - 1}."
                    )
                if frame_index in keyframes:
                    raise WorkflowError(f"Keyframe CSV contains duplicate frame_index {frame_index}.")

                keyframe = RoiKeyframe(**values)
                error = validate_roi(keyframe, frame_width, frame_height)
                if error:
                    raise WorkflowError(f"Keyframe CSV row {line_number}: {error}")
                keyframes[frame_index] = keyframe
            return keyframes
    except (OSError, csv.Error, UnicodeError) as exc:
        raise WorkflowError(f"Could not read keyframe CSV {input_path}: {exc}") from exc


def validate_roi(keyframe: RoiKeyframe, frame_width: int, frame_height: int) -> str | None:
    if keyframe.width <= 0 or keyframe.height <= 0:
        return "ROI width and height must be greater than zero."
    left = keyframe.center_x - keyframe.width / 2
    right = keyframe.center_x + keyframe.width / 2
    top = keyframe.center_y - keyframe.height / 2
    bottom = keyframe.center_y + keyframe.height / 2
    if left < 0 or top < 0 or right > frame_width or bottom > frame_height:
        return "The whole ROI must fit inside the video frame."
    return None


class VideoCanvas(QWidget):
    """Aspect-preserving video canvas that emits source-coordinate gestures."""

    pointClicked = Signal(float, float)
    boxDrawn = Signal(float, float, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)
        self.frame: Any | None = None
        self.qimage: QImage | None = None
        self.keyframe: RoiKeyframe | None = None
        self.drag_start_source: QPointF | None = None
        self.drag_start_widget: QPointF | None = None
        self.drag_current_widget: QPointF | None = None

    def set_frame(self, frame: Any, keyframe: RoiKeyframe | None) -> None:
        self.frame = frame
        self.keyframe = keyframe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb_frame.shape
        self.qimage = QImage(
            rgb_frame.data,
            width,
            height,
            channels * width,
            QImage.Format.Format_RGB888,
        ).copy()
        self.update()

    def image_rect(self) -> QRectF:
        if self.frame is None:
            return QRectF()
        frame_height, frame_width = self.frame.shape[:2]
        scaled_size = QSize(frame_width, frame_height).scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        x = (self.width() - scaled_size.width()) / 2
        y = (self.height() - scaled_size.height()) / 2
        return QRectF(x, y, scaled_size.width(), scaled_size.height())

    def source_point(self, position: QPointF) -> QPointF | None:
        image_rect = self.image_rect()
        if not image_rect.contains(position) or self.frame is None:
            return None
        frame_height, frame_width = self.frame.shape[:2]
        x = (position.x() - image_rect.x()) * frame_width / image_rect.width()
        y = (position.y() - image_rect.y()) * frame_height / image_rect.height()
        return QPointF(
            min(max(x, 0.0), float(frame_width - 1)),
            min(max(y, 0.0), float(frame_height - 1)),
        )

    def widget_point(self, source: QPointF) -> QPointF:
        image_rect = self.image_rect()
        frame_height, frame_width = self.frame.shape[:2]
        return QPointF(
            image_rect.x() + source.x() * image_rect.width() / frame_width,
            image_rect.y() + source.y() * image_rect.height() / frame_height,
        )

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt event name
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(28, 30, 34))
        if self.qimage is None or self.frame is None:
            painter.setPen(QColor(220, 220, 220))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open a video to begin")
            return

        image_rect = self.image_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(image_rect, self.qimage)

        if self.keyframe is not None:
            roi = self.keyframe
            top_left = self.widget_point(
                QPointF(roi.center_x - roi.width / 2, roi.center_y - roi.height / 2)
            )
            bottom_right = self.widget_point(
                QPointF(roi.center_x + roi.width / 2, roi.center_y + roi.height / 2)
            )
            painter.setPen(QPen(QColor(40, 230, 100), 2))
            painter.drawRect(QRectF(top_left, bottom_right))
            painter.setBrush(QColor(40, 230, 100))
            painter.drawEllipse(self.widget_point(QPointF(roi.center_x, roi.center_y)), 4, 4)

        if self.drag_start_widget is not None and self.drag_current_widget is not None:
            painter.setPen(QPen(QColor(255, 205, 45), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(self.drag_start_widget, self.drag_current_widget).normalized())

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt event name
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.source_point(event.position())
        if point is None:
            return
        self.drag_start_source = point
        self.drag_start_widget = event.position()
        self.drag_current_widget = event.position()
        self.update()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt event name
        if self.drag_start_source is None:
            return
        if self.source_point(event.position()) is None:
            return
        self.drag_current_widget = event.position()
        self.update()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt event name
        if event.button() != Qt.MouseButton.LeftButton or self.drag_start_source is None:
            return
        end_source = self.source_point(event.position())
        start_source = self.drag_start_source
        start_widget = self.drag_start_widget
        self.drag_start_source = None
        self.drag_start_widget = None
        self.drag_current_widget = None
        self.update()
        if end_source is None or start_widget is None:
            return

        if (event.position() - start_widget).manhattanLength() < 4:
            self.pointClicked.emit(end_source.x(), end_source.y())
            return
        self.boxDrawn.emit(
            start_source.x(),
            start_source.y(),
            end_source.x(),
            end_source.y(),
        )


class MainWindow(QMainWindow):
    def __init__(
        self,
        video_path: Path,
        settings_path: Path,
        results_directory: Path,
        video: VideoReader,
        initial_keyframes: dict[int, RoiKeyframe] | None = None,
    ) -> None:
        super().__init__()
        self.video_path = video_path
        self.settings_path = settings_path
        self.results_directory = results_directory
        self.video = video
        self.current_frame_index = 0
        self.current_frame = video.first_frame.copy()
        self.keyframes: dict[int, RoiKeyframe] = dict(initial_keyframes or {})
        self._startup_import_requested = initial_keyframes is not None
        self._updating_controls = False
        self._timeline_zoom = 1.0
        self._shortcuts: list[QShortcut] = []
        self.timer = QTimer(self)
        self.timer.setInterval(max(1, round(1000 / video.fps)))
        self.timer.timeout.connect(self.advance_frame)

        self.setWindowTitle(f"Step01a Manual Keyframes — {video_path.name}")
        self.resize(1200, 760)
        self._build_ui()
        self._connect_signals()
        if self._startup_import_requested:
            self._refresh_keypoint_list()
            if self.keyframes:
                self.seek_to_frame(min(self.keyframes))
            else:
                self.update_frame_view()
            self.statusBar().showMessage(f"Imported {len(self.keyframes)} keyframes.", 5000)
        else:
            self.update_frame_view()

    def _build_ui(self) -> None:
        root = QWidget(self)
        outer = QVBoxLayout(root)
        body = QHBoxLayout()
        self.canvas = VideoCanvas(root)
        body.addWidget(self.canvas, 1)

        side_panel = QWidget(root)
        side_layout = QVBoxLayout(side_panel)
        self.frame_label = QLabel()
        self.play_button = QPushButton("Play")
        side_layout.addWidget(self.frame_label)
        playback_controls = QHBoxLayout()
        playback_controls.addWidget(self.play_button)
        playback_controls.addWidget(QLabel("Playback speed"))
        self.speed_combo = QComboBox()
        for label, multiplier in (
            ("0.05×", 0.05),
            ("0.1×", 0.1),
            ("0.25×", 0.25),
            ("0.5×", 0.5),
            ("0.75×", 0.75),
            ("1×", 1.0),
            ("1.5×", 1.5),
            ("2×", 2.0),
            ("5×", 5.0),
            ("10×", 10.0),
            ("20×", 20.0),
        ):
            self.speed_combo.addItem(label, multiplier)
        self.speed_combo.setCurrentIndex(5)
        self.speed_combo.setToolTip("Change playback speed without changing video frame timing or saved keyframes.")
        playback_controls.addWidget(self.speed_combo)
        side_layout.addLayout(playback_controls)

        self.keypoint_list = QListWidget()
        side_layout.addWidget(QLabel("Keypoints"))
        side_layout.addWidget(self.keypoint_list, 1)
        self.import_button = QPushButton("Import Keyframes CSV")
        side_layout.addWidget(self.import_button)
        keypoint_actions = QHBoxLayout()
        self.modify_keypoint_button = QPushButton("Modify Selected")
        self.modify_keypoint_button.setEnabled(False)
        self.delete_keypoint_button = QPushButton("Delete Selected")
        self.delete_keypoint_button.setEnabled(False)
        keypoint_actions.addWidget(self.modify_keypoint_button)
        keypoint_actions.addWidget(self.delete_keypoint_button)
        side_layout.addLayout(keypoint_actions)

        self.roi_controls: dict[str, QDoubleSpinBox] = {}
        form = QFormLayout()
        for name, label in (
            ("width", "Width (px)"),
            ("height", "Height (px)"),
            ("center_x", "Center X (px)"),
            ("center_y", "Center Y (px)"),
        ):
            control = QDoubleSpinBox()
            control.setDecimals(2)
            control.setSingleStep(1.0)
            control.setRange(0.0, 1_000_000.0)
            control.setEnabled(False)
            self.roi_controls[name] = control
            form.addRow(label, control)
        side_layout.addLayout(form)

        shortcut_group = QGroupBox("Keyboard shortcuts")
        shortcut_layout = QVBoxLayout(shortcut_group)
        shortcut_text = QLabel(
            "P — Play / pause\n"
            "← / → — Back / forward 5 frames\n"
            "E / D — Increase / decrease ROI height by 1 px\n"
            "F / S — Increase / decrease ROI width by 1 px"
        )
        shortcut_text.setWordWrap(True)
        shortcut_layout.addWidget(shortcut_text)
        side_layout.addWidget(shortcut_group)

        self.save_button = QPushButton("Save Keyframes CSV")
        side_layout.addWidget(self.save_button)
        body.addWidget(side_panel, 0)
        outer.addLayout(body, 1)

        timeline = QHBoxLayout()
        self.timeline_label = QLabel("Frame 0")
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, self.video.frame_count - 1)
        self.frame_slider.setPageStep(max(1, round(self.video.fps)))
        timeline.addWidget(self.timeline_label)
        timeline.addWidget(self.frame_slider, 1)
        outer.addLayout(timeline)

        timeline_tools = QHBoxLayout()
        timeline_tools.addWidget(QLabel("Go to frame"))
        self.frame_jump_spin = QSpinBox()
        self.frame_jump_spin.setRange(0, self.video.frame_count - 1)
        self.frame_jump_spin.setValue(0)
        self.frame_jump_spin.setKeyboardTracking(False)
        self.frame_jump_spin.setToolTip("Enter a zero-based frame number and press Enter to seek.")
        timeline_tools.addWidget(self.frame_jump_spin)
        timeline_tools.addWidget(QLabel("Timeline zoom"))
        self.timeline_zoom_combo = QComboBox()
        for label, zoom in (
            ("Fit", 1.0),
            ("2×", 2.0),
            ("5×", 5.0),
            ("10×", 10.0),
            ("25×", 25.0),
            ("50×", 50.0),
            ("100×", 100.0),
        ):
            self.timeline_zoom_combo.addItem(label, zoom)
        self.timeline_zoom_combo.setToolTip("Zoom the timeline around the current frame.")
        timeline_tools.addWidget(self.timeline_zoom_combo)
        timeline_tools.addStretch(1)
        outer.addLayout(timeline_tools)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Drag a box to create the first keypoint.")

    def _connect_signals(self) -> None:
        self._register_keyboard_shortcuts()
        self.play_button.clicked.connect(self.toggle_playback)
        self.speed_combo.currentIndexChanged.connect(self.update_playback_speed)
        self.frame_slider.sliderPressed.connect(self.pause_playback)
        self.frame_slider.valueChanged.connect(self.seek_to_frame)
        self.frame_jump_spin.valueChanged.connect(self.seek_to_frame)
        self.timeline_zoom_combo.currentIndexChanged.connect(self.update_timeline_zoom)
        self.keypoint_list.currentItemChanged.connect(self.on_keypoint_selected)
        self.import_button.clicked.connect(self.import_keyframes_from_dialog)
        self.modify_keypoint_button.clicked.connect(self.modify_selected_keypoint)
        self.delete_keypoint_button.clicked.connect(self.delete_selected_keypoint)
        self.save_button.clicked.connect(self.save_keyframes)
        self.canvas.pointClicked.connect(self.add_point_keyframe)
        self.canvas.boxDrawn.connect(self.add_box_keyframe)
        for name, control in self.roi_controls.items():
            control.valueChanged.connect(lambda _value, field=name: self.update_current_keyframe(field))

    def _register_keyboard_shortcuts(self) -> None:
        bindings = (
            ("P", "play_pause"),
            (Qt.Key.Key_Left, "step_back"),
            (Qt.Key.Key_Right, "step_forward"),
            ("E", "height_up"),
            ("D", "height_down"),
            ("S", "width_down"),
            ("F", "width_up"),
        )
        for key, action in bindings:
            sequence = QKeySequence(key) if isinstance(key, str) else QKeySequence(key)
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(lambda name=action: self._dispatch_keyboard_shortcut(name))
            self._shortcuts.append(shortcut)

    def _dispatch_keyboard_shortcut(self, action: str) -> None:
        focus_widget = QApplication.focusWidget()
        if isinstance(focus_widget, (QAbstractSpinBox, QLineEdit)):
            return
        actions = {
            "play_pause": self.play_button.click,
            "step_back": lambda: self.step_frames(-5),
            "step_forward": lambda: self.step_frames(5),
            "height_up": lambda: self.adjust_current_roi("height", 1.0),
            "height_down": lambda: self.adjust_current_roi("height", -1.0),
            "width_down": lambda: self.adjust_current_roi("width", -1.0),
            "width_up": lambda: self.adjust_current_roi("width", 1.0),
        }
        actions[action]()

    def step_frames(self, offset: int) -> None:
        self.pause_playback()
        target_frame = max(0, min(self.current_frame_index + offset, self.video.frame_count - 1))
        self.seek_to_frame(target_frame)

    def adjust_current_roi(self, field: str, delta: float) -> None:
        keyframe = self.keyframes.get(self.current_frame_index)
        if keyframe is None:
            self.statusBar().showMessage(
                "There is no ROI keyframe at the current frame to resize.",
                3000,
            )
            return
        candidate_values = {
            "width": keyframe.width,
            "height": keyframe.height,
            "center_x": keyframe.center_x,
            "center_y": keyframe.center_y,
        }
        candidate_values[field] += delta
        self._store_keyframe(self.current_frame_index, RoiKeyframe(**candidate_values))

    def update_playback_speed(self, _index: int = -1) -> None:
        speed = float(self.speed_combo.currentData())
        self.timer.setInterval(max(1, round(1000 / (self.video.fps * speed))))

    def update_timeline_zoom(self, _index: int = -1) -> None:
        self._timeline_zoom = float(self.timeline_zoom_combo.currentData())
        self._set_timeline_window(self.current_frame_index)

    def _set_timeline_window(self, center_frame: int) -> None:
        last_frame = self.video.frame_count - 1
        if self._timeline_zoom <= 1.0:
            start, end = 0, last_frame
        else:
            visible_frames = max(2, math.ceil(self.video.frame_count / self._timeline_zoom))
            visible_frames = min(visible_frames, self.video.frame_count)
            start = max(0, min(center_frame - visible_frames // 2, self.video.frame_count - visible_frames))
            end = start + visible_frames - 1
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(start, end)
        self.frame_slider.setValue(center_frame)
        self.frame_slider.blockSignals(False)

    def _ensure_timeline_contains(self, frame_index: int) -> None:
        if not self.frame_slider.minimum() <= frame_index <= self.frame_slider.maximum():
            self._set_timeline_window(frame_index)

    def update_frame_view(self) -> None:
        if self.current_frame is None:
            return
        keyframe = self.keyframes.get(self.current_frame_index)
        self.canvas.set_frame(self.current_frame, keyframe)
        timestamp = self.current_frame_index / self.video.fps
        self.frame_label.setText(f"Frame {self.current_frame_index}  |  {timestamp:.3f} s")
        self.timeline_label.setText(f"Frame {self.current_frame_index}")
        self.frame_jump_spin.blockSignals(True)
        self.frame_jump_spin.setValue(self.current_frame_index)
        self.frame_jump_spin.blockSignals(False)
        self._sync_controls(keyframe)
        self._sync_keypoint_selection()

    def _sync_controls(self, keyframe: RoiKeyframe | None) -> None:
        self._updating_controls = True
        try:
            for control in self.roi_controls.values():
                control.setEnabled(keyframe is not None)
            if keyframe is not None:
                for field, control in self.roi_controls.items():
                    control.setValue(getattr(keyframe, field))
        finally:
            self._updating_controls = False

    def _sync_keypoint_selection(self) -> None:
        target_item = None
        for index in range(self.keypoint_list.count()):
            item = self.keypoint_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == self.current_frame_index:
                target_item = item
                break
        self.keypoint_list.blockSignals(True)
        self.keypoint_list.setCurrentItem(target_item)
        self.keypoint_list.blockSignals(False)
        has_selection = target_item is not None
        self.modify_keypoint_button.setEnabled(has_selection)
        self.delete_keypoint_button.setEnabled(has_selection)

    def seek_to_frame(self, frame_index: int) -> None:
        if frame_index == self.current_frame_index and self.current_frame is not None:
            self.update_frame_view()
            return
        frame = self.video.read_frame(frame_index)
        if frame is None:
            self.pause_playback()
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(self.current_frame_index)
            self.frame_slider.blockSignals(False)
            self.statusBar().showMessage(f"Could not read frame {frame_index}.", 5000)
            return
        self.current_frame_index = frame_index
        self.current_frame = frame
        self._ensure_timeline_contains(frame_index)
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(frame_index)
        self.frame_slider.blockSignals(False)
        self.update_frame_view()

    def advance_frame(self) -> None:
        next_frame = self.current_frame_index + 1
        if next_frame >= self.video.frame_count:
            self.pause_playback()
            return
        frame = self.video.read_frame(next_frame)
        if frame is None:
            self.pause_playback()
            self.statusBar().showMessage(f"Could not read frame {next_frame}.", 5000)
            return
        self.current_frame_index = next_frame
        self.current_frame = frame
        self._ensure_timeline_contains(next_frame)
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(next_frame)
        self.frame_slider.blockSignals(False)
        self.update_frame_view()

    def toggle_playback(self) -> None:
        if self.timer.isActive():
            self.pause_playback()
            return
        if self.current_frame_index >= self.video.frame_count - 1:
            self.seek_to_frame(0)
        self.play_button.setText("Pause")
        self.timer.start()

    def pause_playback(self) -> None:
        self.timer.stop()
        if hasattr(self, "play_button"):
            self.play_button.setText("Play")

    def on_keypoint_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        self.pause_playback()
        frame_index = int(current.data(Qt.ItemDataRole.UserRole))
        self.frame_slider.setValue(frame_index)

    def modify_selected_keypoint(self) -> None:
        item = self.keypoint_list.currentItem()
        if item is None:
            return
        frame_index = int(item.data(Qt.ItemDataRole.UserRole))
        keyframe = self.keyframes.get(frame_index)
        if keyframe is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Modify keypoint at frame {frame_index}")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        controls: dict[str, QDoubleSpinBox] = {}
        for field, label in (
            ("width", "Width (px)"),
            ("height", "Height (px)"),
            ("center_x", "Center X (px)"),
            ("center_y", "Center Y (px)"),
        ):
            control = QDoubleSpinBox(dialog)
            control.setDecimals(2)
            control.setSingleStep(1.0)
            control.setRange(0.0, 1_000_000.0)
            control.setValue(getattr(keyframe, field))
            controls[field] = control
            form.addRow(label, control)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        candidate = RoiKeyframe(**{field: control.value() for field, control in controls.items()})
        error = self._validate_keyframe(candidate)
        if error:
            QMessageBox.warning(self, "Invalid ROI", error)
            return
        self.keyframes[frame_index] = candidate
        self.seek_to_frame(frame_index)
        self._refresh_keypoint_list()
        self.update_frame_view()
        self.statusBar().showMessage(f"Keypoint at frame {frame_index} modified.", 3000)

    def delete_selected_keypoint(self) -> None:
        item = self.keypoint_list.currentItem()
        if item is None:
            return
        frame_index = int(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(
            self,
            "Delete keypoint",
            f"Delete the keypoint at frame {frame_index}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self.keyframes[frame_index]
        self._refresh_keypoint_list()
        self.update_frame_view()
        self.statusBar().showMessage(f"Keypoint at frame {frame_index} deleted.", 3000)

    def _validate_keyframe(self, keyframe: RoiKeyframe) -> str | None:
        return validate_roi(keyframe, self.video.frame_width, self.video.frame_height)

    def _store_keyframe(self, frame_index: int, keyframe: RoiKeyframe) -> bool:
        error = self._validate_keyframe(keyframe)
        if error:
            self.statusBar().showMessage(error, 5000)
            return False
        self.keyframes[frame_index] = keyframe
        self._refresh_keypoint_list()
        self.update_frame_view()
        self.statusBar().showMessage(f"Keypoint saved at frame {frame_index}.", 2500)
        return True

    def add_point_keyframe(self, center_x: float, center_y: float) -> None:
        existing = self.keyframes.get(self.current_frame_index)
        if existing is not None:
            width, height = existing.width, existing.height
        else:
            earlier = [index for index in self.keyframes if index < self.current_frame_index]
            if not earlier:
                self.statusBar().showMessage(
                    "No preceding keypoint has ROI dimensions. Drag a box to create this keypoint.",
                    5000,
                )
                return
            previous = self.keyframes[max(earlier)]
            width, height = previous.width, previous.height
        self._store_keyframe(
            self.current_frame_index,
            RoiKeyframe(width, height, center_x, center_y),
        )

    def add_box_keyframe(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        frame_width, frame_height = self.video.frame_width, self.video.frame_height
        left = min(max(math.floor(min(x0, x1)), 0), frame_width - 1)
        top = min(max(math.floor(min(y0, y1)), 0), frame_height - 1)
        right = min(max(math.floor(max(x0, x1)) + 1, left + 1), frame_width)
        bottom = min(max(math.floor(max(y0, y1)) + 1, top + 1), frame_height)
        width = float(right - left)
        height = float(bottom - top)
        self._store_keyframe(
            self.current_frame_index,
            RoiKeyframe(width, height, left + width / 2, top + height / 2),
        )

    def update_current_keyframe(self, _changed_field: str) -> None:
        if self._updating_controls:
            return
        existing = self.keyframes.get(self.current_frame_index)
        if existing is None:
            return
        candidate = RoiKeyframe(
            **{field: control.value() for field, control in self.roi_controls.items()}
        )
        error = self._validate_keyframe(candidate)
        if error:
            self._sync_controls(existing)
            self.statusBar().showMessage(error, 5000)
            return
        self.keyframes[self.current_frame_index] = candidate
        self._refresh_keypoint_list()
        self.update_frame_view()

    def _refresh_keypoint_list(self) -> None:
        self.keypoint_list.blockSignals(True)
        self.keypoint_list.clear()
        for frame_index in sorted(self.keyframes):
            keyframe = self.keyframes[frame_index]
            timestamp = frame_index / self.video.fps
            item = QListWidgetItem(
                f"Frame {frame_index} | {timestamp:.3f} s | "
                f"Center ({keyframe.center_x:.1f}, {keyframe.center_y:.1f}) | "
                f"Size {keyframe.width:.1f} × {keyframe.height:.1f} px"
            )
            item.setData(Qt.ItemDataRole.UserRole, frame_index)
            self.keypoint_list.addItem(item)
        self.keypoint_list.blockSignals(False)
        self._sync_keypoint_selection()

    def import_keyframes_from_dialog(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Import keyframes CSV",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )
        if not selected:
            return
        input_path = Path(selected).expanduser().resolve()
        try:
            imported = read_keyframes_csv(
                input_path,
                self.video.frame_count,
                self.video.frame_width,
                self.video.frame_height,
            )
        except WorkflowError as exc:
            QMessageBox.warning(self, "Could not import keyframes", str(exc))
            return
        self._replace_keyframes(imported)
        self.statusBar().showMessage(f"Imported {len(imported)} keyframes from {input_path.name}.", 5000)

    def _replace_keyframes(self, keyframes: dict[int, RoiKeyframe]) -> None:
        self.keyframes = dict(keyframes)
        self._refresh_keypoint_list()
        if self.keyframes:
            self.seek_to_frame(min(self.keyframes))
        else:
            self.update_frame_view()

    def save_keyframes(self) -> None:
        output_directory = self.results_directory / "Step01a_ManualKeyframes"
        output_path = output_directory / f"{self.video_path.stem}_manual_keyframes.csv"
        try:
            write_keyframes_csv(output_path, self.video.fps, self.keyframes)
        except (OSError, csv.Error) as exc:
            QMessageBox.critical(self, "Could not save keyframes", str(exc))
            return
        QMessageBox.information(self, "Keyframes saved", f"Saved CSV to:\n{output_path}")

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt event name
        self.pause_playback()
        self.video.close()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Step01a Manual Keyframes")

    try:
        video_path = get_startup_path(
            args.video,
            "Select video",
            "Video files (*.mp4 *.mov *.mkv *.avi *.wmv);;All files (*.*)",
        )
        if video_path is None:
            return 0
        settings_path = get_startup_path(
            args.settings,
            "Select settings JSON",
            "Settings files (*.json);;All files (*.*)",
        )
        if settings_path is None:
            return 0
        results_directory = resolve_results_directory(settings_path)
        video = VideoReader(video_path)
        initial_keyframes: dict[int, RoiKeyframe] | None = None
        if args.keyframes is not None:
            keyframes_path = get_startup_path(
                args.keyframes,
                "Select keyframes CSV",
                "CSV files (*.csv);;All files (*.*)",
            )
            if keyframes_path is None:
                return 0
            initial_keyframes = read_keyframes_csv(
                keyframes_path,
                video.frame_count,
                video.frame_width,
                video.frame_height,
            )
    except (WorkflowError, OSError, cv2.error) as exc:
        if "video" in locals():
            video.close()
        QMessageBox.critical(None, "Step01a could not start", str(exc))
        return 1

    window = MainWindow(video_path, settings_path, results_directory, video, initial_keyframes)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
