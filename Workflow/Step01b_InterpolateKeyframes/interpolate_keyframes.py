"""Interpolate Step01a ROI keyframes into one record per video frame."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox


CSV_FIELDS = (
    "frame_index",
    "timestamp_seconds",
    "width",
    "height",
    "center_x",
    "center_y",
)
ROI_FIELDS = ("width", "height", "center_x", "center_y")


class WorkflowError(Exception):
    """An expected input or processing error suitable for a user message."""


@dataclass(frozen=True)
class Keyframe:
    frame_index: int
    timestamp_seconds: float
    width: float
    height: float
    center_x: float
    center_y: float


@dataclass(frozen=True)
class VideoInfo:
    frame_count: int
    fps: float
    width: int
    height: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interpolate Step01a ROI keyframes across every video frame."
    )
    parser.add_argument("--settings", type=Path, help="settings JSON containing ResultsDirectory")
    parser.add_argument("--keyframes", type=Path, help="Step01a keyframe CSV")
    parser.add_argument("--video", type=Path, help="source video used to determine frame count and FPS")
    return parser.parse_args(argv)


def resolve_input_path(
    supplied_path: Path | None,
    title: str,
    file_filter: str,
) -> Path | None:
    if supplied_path is not None:
        path = supplied_path.expanduser().resolve()
        if not path.is_file():
            raise WorkflowError(f"Input file does not exist or is not a file: {path}")
        return path

    selected, _ = QFileDialog.getOpenFileName(None, title, "", file_filter)
    if not selected:
        return None
    return Path(selected).expanduser().resolve()


def load_settings(settings_path: Path) -> tuple[Path, str]:
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
    scenario_name = settings.get("ScenarioName")
    if not isinstance(scenario_name, str) or not scenario_name.strip():
        raise WorkflowError("Settings JSON must contain a non-empty 'ScenarioName' value.")
    scenario_name = scenario_name.strip()
    invalid_filename_chars = '<>:"/\\|?*'
    if (
        scenario_name in {".", ".."}
        or any(char in invalid_filename_chars for char in scenario_name)
        or scenario_name.endswith((" ", "."))
    ):
        raise WorkflowError(
            "Settings JSON 'ScenarioName' must be a valid filename component."
        )

    results_directory = Path(raw_directory).expanduser()
    if not results_directory.is_absolute():
        results_directory = settings_path.parent / results_directory
    return results_directory.resolve(), scenario_name


def resolve_results_directory(settings_path: Path) -> Path:
    """Return ResultsDirectory after validating the complete settings file."""
    results_directory, _scenario_name = load_settings(settings_path)
    return results_directory


def get_video_info(video_path: Path) -> VideoInfo:
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise WorkflowError(f"OpenCV could not open the video: {video_path}")

        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count_raw = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0:
            raise WorkflowError(f"The video has an invalid frame rate: {fps!r}")
        if (
            not math.isfinite(frame_count_raw)
            or frame_count_raw < 1
            or not frame_count_raw.is_integer()
        ):
            raise WorkflowError("OpenCV could not determine a positive video frame count.")
        frame_count = int(frame_count_raw)
        ok, first_frame = capture.read()
        if not ok or first_frame is None or first_frame.size == 0:
            raise WorkflowError("The video contains no readable frames.")
        height, width = first_frame.shape[:2]
        if width <= 0 or height <= 0:
            raise WorkflowError("OpenCV could not determine valid video dimensions.")
        return VideoInfo(frame_count, fps, width, height)
    finally:
        capture.release()


def _parse_finite_number(value: str | None, field: str, row_number: int) -> float:
    try:
        parsed = float(value) if value is not None else math.nan
    except ValueError as exc:
        raise WorkflowError(f"Keyframe CSV row {row_number}: '{field}' must be numeric.") from exc
    if not math.isfinite(parsed):
        raise WorkflowError(f"Keyframe CSV row {row_number}: '{field}' must be finite.")
    return parsed


def load_keyframes(csv_path: Path, video: VideoInfo) -> list[Keyframe]:
    try:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames != list(CSV_FIELDS):
                raise WorkflowError(
                    "Keyframe CSV must have these columns in order: " + ",".join(CSV_FIELDS)
                )
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise WorkflowError(f"Could not read keyframe CSV {csv_path}: {exc}") from exc

    if not rows:
        raise WorkflowError("Keyframe CSV has no keyframe rows.")

    keyframes: list[Keyframe] = []
    seen_frames: set[int] = set()
    for row_number, row in enumerate(rows, start=2):
        if None in row:
            raise WorkflowError(f"Keyframe CSV row {row_number} has extra fields.")
        frame_value = _parse_finite_number(row.get("frame_index"), "frame_index", row_number)
        if not frame_value.is_integer():
            raise WorkflowError(f"Keyframe CSV row {row_number}: frame_index must be an integer.")
        frame_index = int(frame_value)
        if not 0 <= frame_index < video.frame_count:
            raise WorkflowError(
                f"Keyframe CSV row {row_number}: frame_index {frame_index} is outside "
                f"the video range 0..{video.frame_count - 1}."
            )
        if frame_index in seen_frames:
            raise WorkflowError(f"Keyframe CSV contains duplicate frame_index {frame_index}.")
        seen_frames.add(frame_index)

        timestamp = _parse_finite_number(
            row.get("timestamp_seconds"), "timestamp_seconds", row_number
        )
        values = {
            field: _parse_finite_number(row.get(field), field, row_number)
            for field in ROI_FIELDS
        }
        if values["width"] <= 0 or values["height"] <= 0:
            raise WorkflowError(
                f"Keyframe CSV row {row_number}: width and height must be positive."
            )
        if (
            values["center_x"] - values["width"] / 2 < 0
            or values["center_x"] + values["width"] / 2 > video.width
            or values["center_y"] - values["height"] / 2 < 0
            or values["center_y"] + values["height"] / 2 > video.height
        ):
            raise WorkflowError(
                f"Keyframe CSV row {row_number}: ROI extends outside the video frame."
            )

        keyframes.append(
            Keyframe(
                frame_index=frame_index,
                timestamp_seconds=timestamp,
                **values,
            )
        )

    return sorted(keyframes, key=lambda keyframe: keyframe.frame_index)


def interpolate_roi(
    frame_index: int,
    keyframes: list[Keyframe],
    keyframe_indices: list[int] | None = None,
) -> tuple[float, float, float, float]:
    """Return width, height, center_x, center_y for a frame."""
    if frame_index <= keyframes[0].frame_index:
        keyframe = keyframes[0]
        return keyframe.width, keyframe.height, keyframe.center_x, keyframe.center_y
    if frame_index >= keyframes[-1].frame_index:
        keyframe = keyframes[-1]
        return keyframe.width, keyframe.height, keyframe.center_x, keyframe.center_y

    indices = keyframe_indices or [keyframe.frame_index for keyframe in keyframes]
    right_index = bisect_right(indices, frame_index)
    left = keyframes[right_index - 1]
    right = keyframes[right_index]
    if frame_index == left.frame_index:
        return left.width, left.height, left.center_x, left.center_y
    fraction = (frame_index - left.frame_index) / (right.frame_index - left.frame_index)
    return tuple(
        getattr(left, field)
        + fraction * (getattr(right, field) - getattr(left, field))
        for field in ROI_FIELDS
    )  # type: ignore[return-value]


def _format_number(value: float) -> str:
    return repr(value)


def write_results_csv(
    output_path: Path,
    video: VideoInfo,
    keyframes: list[Keyframe],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    keyframe_indices = [keyframe.frame_index for keyframe in keyframes]
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(CSV_FIELDS)
        for frame_index in range(video.frame_count):
            width, height, center_x, center_y = interpolate_roi(
                frame_index, keyframes, keyframe_indices
            )
            writer.writerow(
                (
                    frame_index,
                    f"{frame_index / video.fps:.6f}",
                    _format_number(width),
                    _format_number(height),
                    _format_number(center_x),
                    _format_number(center_y),
                )
            )


def run(settings_path: Path, keyframes_path: Path, video_path: Path) -> Path:
    results_directory, scenario_name = load_settings(settings_path)
    video = get_video_info(video_path)
    keyframes = load_keyframes(keyframes_path, video)
    output_path = (
        results_directory
        / "Step01b_InterpolateKeyframes"
        / f"{scenario_name}_interpolated_keyframes.csv"
    )
    output_path_resolved = output_path.resolve()
    for input_path in (settings_path, keyframes_path, video_path):
        if output_path_resolved == input_path.resolve():
            raise WorkflowError(f"Output path would overwrite an input file: {input_path}")
    try:
        write_results_csv(output_path, video, keyframes)
    except (OSError, csv.Error) as exc:
        raise WorkflowError(f"Could not write interpolation results to {output_path}: {exc}") from exc
    return output_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Step01b Interpolate Keyframes")

    try:
        settings_path = resolve_input_path(
            args.settings,
            "Select settings JSON",
            "Settings files (*.json);;All files (*.*)",
        )
        if settings_path is None:
            return 0
        keyframes_path = resolve_input_path(
            args.keyframes,
            "Select Step01a keyframe CSV",
            "CSV files (*.csv);;All files (*.*)",
        )
        if keyframes_path is None:
            return 0
        video_path = resolve_input_path(
            args.video,
            "Select source video",
            "Video files (*.mp4 *.mov *.mkv *.avi *.wmv);;All files (*.*)",
        )
        if video_path is None:
            return 0

        output_path = run(settings_path, keyframes_path, video_path)
    except (WorkflowError, OSError, cv2.error) as exc:
        QMessageBox.critical(None, "Step01b could not complete", str(exc))
        return 1

    print(f"Interpolated results saved to: {output_path}")
    QMessageBox.information(None, "Interpolation complete", f"Saved results to:\n{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
