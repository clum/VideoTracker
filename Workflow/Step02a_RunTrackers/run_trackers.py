"""Run one OpenCV single-object tracker and save ROI and trajectory CSV outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox


ALGORITHMS = (
    "csrt",
    "kcf",
    "mil",
    "boosting",
    "medianflow",
    "mosse",
    "tld",
    "dasiamrpn",
    "nano",
    "vit",
)
ROI_CSV_FIELDS = ("x", "y", "width", "height", "center_x", "center_y")
TRACK_CSV_FIELDS = (
    "frame_index",
    "timestamp_seconds",
    "tracking_status",
    "x",
    "y",
    "width",
    "height",
    "center_x",
    "center_y",
)
TRACKER_CLASSES = {
    "csrt": ("TrackerCSRT",),
    "kcf": ("TrackerKCF",),
    "mil": ("TrackerMIL",),
    "boosting": ("TrackerBoosting",),
    "medianflow": ("TrackerMedianFlow",),
    "mosse": ("TrackerMOSSE",),
    "tld": ("TrackerTLD",),
    "dasiamrpn": ("TrackerDaSiamRPN",),
    "nano": ("TrackerNano",),
    "vit": ("TrackerVit",),
}


class WorkflowError(Exception):
    """An expected input or processing error suitable for a user message."""


@dataclass(frozen=True)
class VideoInfo:
    frame_count: int
    fps: float
    width: int
    height: int


@dataclass(frozen=True)
class Outputs:
    roi_image: Path
    roi_csv: Path
    tracked_csv: Path


class VideoReader:
    """Random access is not needed, so keep one capture and read sequentially."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.capture = cv2.VideoCapture(str(path))
        if not self.capture.isOpened():
            self.capture.release()
            raise WorkflowError(f"OpenCV could not open the source video: {path}")

        fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        raw_count = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0:
            self.capture.release()
            raise WorkflowError(f"The source video has an invalid frame rate: {fps!r}")
        if (
            not math.isfinite(raw_count)
            or raw_count < 1
            or not raw_count.is_integer()
        ):
            self.capture.release()
            raise WorkflowError("OpenCV could not determine a positive integer frame count.")

        ok, first_frame = self.capture.read()
        if not ok or first_frame is None or first_frame.size == 0:
            self.capture.release()
            raise WorkflowError("The source video contains no readable first frame.")
        height, width = first_frame.shape[:2]
        if width <= 0 or height <= 0:
            self.capture.release()
            raise WorkflowError("OpenCV could not determine valid source frame dimensions.")

        self.info = VideoInfo(int(raw_count), fps, width, height)
        self.first_frame = first_frame

    def read_next(self) -> Any | None:
        ok, frame = self.capture.read()
        if not ok or frame is None or frame.size == 0:
            return None
        return frame

    def close(self) -> None:
        self.capture.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select one object in a video and track it with OpenCV."
    )
    parser.add_argument("--settings", type=Path, help="settings JSON containing ResultsDirectory and ScenarioName")
    parser.add_argument("--video", type=Path, help="source video to track")
    parser.add_argument("--algorithm", choices=ALGORITHMS, required=True, help="OpenCV tracker algorithm")
    parser.add_argument("--siam-rpn-model", type=Path, help="DaSiamRPN model file")
    parser.add_argument("--kernel-cls1-model", type=Path, help="DaSiamRPN classification kernel model file")
    parser.add_argument("--kernel-r1-model", type=Path, help="DaSiamRPN regression kernel model file")
    parser.add_argument("--nano-backbone", type=Path, help="Nano tracker backbone model file")
    parser.add_argument("--nano-neckhead", type=Path, help="Nano tracker neckhead model file")
    parser.add_argument("--vit-model", type=Path, help="ViT tracker model file")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    required_models = {
        "dasiamrpn": (
            ("siam_rpn_model", "--siam-rpn-model"),
            ("kernel_cls1_model", "--kernel-cls1-model"),
            ("kernel_r1_model", "--kernel-r1-model"),
        ),
        "nano": (
            ("nano_backbone", "--nano-backbone"),
            ("nano_neckhead", "--nano-neckhead"),
        ),
        "vit": (("vit_model", "--vit-model"),),
    }
    missing = [
        option
        for attribute, option in required_models.get(args.algorithm, ())
        if getattr(args, attribute) is None
    ]
    if missing:
        parser.error(f"--algorithm {args.algorithm} requires " + ", ".join(missing))
    return args


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
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Could not read settings JSON {settings_path}: {exc}") from exc

    if not isinstance(settings, dict):
        raise WorkflowError("Settings JSON must contain an object at its root.")
    raw_results = settings.get("ResultsDirectory")
    if not isinstance(raw_results, str) or not raw_results.strip():
        raise WorkflowError("Settings JSON must contain a non-empty 'ResultsDirectory' value.")
    scenario_name = settings.get("ScenarioName")
    if not isinstance(scenario_name, str) or not scenario_name.strip():
        raise WorkflowError("Settings JSON must contain a non-empty 'ScenarioName' value.")
    scenario_name = scenario_name.strip()
    if (
        scenario_name in {".", ".."}
        or any(char in '<>:"/\\|?*' for char in scenario_name)
        or scenario_name.endswith((" ", "."))
    ):
        raise WorkflowError("Settings JSON 'ScenarioName' must be a valid filename component.")

    results_directory = Path(raw_results).expanduser()
    if not results_directory.is_absolute():
        results_directory = settings_path.parent / results_directory
    return results_directory.resolve(), scenario_name


def resolve_model_paths(args: argparse.Namespace) -> dict[str, Path]:
    attributes = {
        "dasiamrpn": ("siam_rpn_model", "kernel_cls1_model", "kernel_r1_model"),
        "nano": ("nano_backbone", "nano_neckhead"),
        "vit": ("vit_model",),
    }.get(args.algorithm, ())
    paths: dict[str, Path] = {}
    for attribute in attributes:
        model_path = getattr(args, attribute)
        resolved = model_path.expanduser().resolve()
        if not resolved.is_file():
            raise WorkflowError(f"Required {args.algorithm} model file does not exist: {resolved}")
        try:
            with resolved.open("rb") as model_file:
                model_file.read(1)
        except OSError as exc:
            raise WorkflowError(f"Cannot read model file {resolved}: {exc}") from exc
        paths[attribute] = resolved
    return paths


def select_roi(frame: Any) -> tuple[int, int, int, int]:
    try:
        box = cv2.selectROI(
            "Select object (Enter/Space to accept, C to cancel)",
            frame,
            showCrosshair=True,
            fromCenter=False,
        )
    except cv2.error as exc:
        raise WorkflowError(
            "OpenCV could not open the ROI selector. Run Step02a in a desktop session "
            "with a GUI-enabled OpenCV build."
        ) from exc
    finally:
        close_windows()

    x, y, width, height = (int(value) for value in box)
    if width <= 0 or height <= 0:
        raise WorkflowError("ROI selection was canceled or had zero area.")
    return x, y, width, height


def validate_roi(
    box: tuple[int, int, int, int],
    width: int,
    height: int,
) -> None:
    x, y, roi_width, roi_height = box
    if x < 0 or y < 0 or roi_width <= 0 or roi_height <= 0:
        raise WorkflowError("The selected ROI has invalid coordinates or dimensions.")
    if x + roi_width > width or y + roi_height > height:
        raise WorkflowError("The selected ROI extends outside the first video frame.")


def _tracker_factory(algorithm: str) -> Any:
    class_names = TRACKER_CLASSES[algorithm]
    namespaces = [cv2]
    legacy = getattr(cv2, "legacy", None)
    if legacy is not None:
        namespaces.append(legacy)
    for namespace in namespaces:
        for class_name in class_names:
            tracker_class = getattr(namespace, class_name, None)
            if tracker_class is not None and hasattr(tracker_class, "create"):
                return tracker_class.create
            legacy_factory = getattr(namespace, f"{class_name}_create", None)
            if legacy_factory is not None:
                return legacy_factory
    raise WorkflowError(
        f"The active OpenCV build does not expose the {algorithm} tracker. "
        "Check the installed OpenCV package and version."
    )


def create_tracker(algorithm: str, model_paths: dict[str, Path]) -> Any:
    factory = _tracker_factory(algorithm)
    try:
        if algorithm == "dasiamrpn":
            params = cv2.TrackerDaSiamRPN_Params()
            params.model = str(model_paths["siam_rpn_model"])
            params.kernel_cls1 = str(model_paths["kernel_cls1_model"])
            params.kernel_r1 = str(model_paths["kernel_r1_model"])
            return factory(params)
        if algorithm == "nano":
            params = cv2.TrackerNano_Params()
            params.backbone = str(model_paths["nano_backbone"])
            params.neckhead = str(model_paths["nano_neckhead"])
            return factory(params)
        if algorithm == "vit":
            params = cv2.TrackerVit_Params()
            params.net = str(model_paths["vit_model"])
            return factory(params)
        return factory()
    except (AttributeError, TypeError, cv2.error, OSError) as exc:
        raise WorkflowError(f"Could not create the {algorithm} tracker: {exc}") from exc


def close_windows() -> None:
    try:
        cv2.destroyAllWindows()
    except cv2.error:
        pass


def _format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _box_from_tracker(value: Any) -> tuple[float, float, float, float] | None:
    try:
        x, y, width, height = (float(component) for component in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(component) for component in (x, y, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _write_roi_csv(path: Path, box: tuple[int, int, int, int]) -> None:
    x, y, width, height = box
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(ROI_CSV_FIELDS)
        writer.writerow(
            (
                x,
                y,
                width,
                height,
                _format_number(x + width / 2),
                _format_number(y + height / 2),
            )
        )


def _write_track_row(
    writer: csv.DictWriter,
    frame_index: int,
    fps: float,
    box: tuple[float, float, float, float] | None,
) -> None:
    if box is None:
        writer.writerow(
            {
                "frame_index": frame_index,
                "timestamp_seconds": f"{frame_index / fps:.6f}",
                "tracking_status": "lost",
                "x": "",
                "y": "",
                "width": "",
                "height": "",
                "center_x": "",
                "center_y": "",
            }
        )
        return
    x, y, width, height = box
    writer.writerow(
        {
            "frame_index": frame_index,
            "timestamp_seconds": f"{frame_index / fps:.6f}",
            "tracking_status": "tracked",
            "x": _format_number(x),
            "y": _format_number(y),
            "width": _format_number(width),
            "height": _format_number(height),
            "center_x": _format_number(x + width / 2),
            "center_y": _format_number(y + height / 2),
        }
    )


def output_paths(results_directory: Path, scenario_name: str, algorithm: str) -> Outputs:
    output_directory = results_directory / "Step02a_RunTrackers"
    stem = f"{scenario_name}_{algorithm}"
    return Outputs(
        output_directory / f"{stem}_roi.png",
        output_directory / f"{stem}_roi.csv",
        output_directory / f"{stem}_tracked.csv",
    )


def run(
    settings_path: Path,
    video_path: Path,
    algorithm: str,
    model_paths: dict[str, Path] | None = None,
) -> Outputs:
    results_directory, scenario_name = load_settings(settings_path)
    reader = VideoReader(video_path)
    outputs = output_paths(results_directory, scenario_name, algorithm)
    input_paths = (settings_path.resolve(), video_path.resolve(), *(model_paths or {}).values())
    output_resolved = {path.resolve() for path in (outputs.roi_image, outputs.roi_csv, outputs.tracked_csv)}
    if output_resolved.intersection(path.resolve() for path in input_paths):
        reader.close()
        raise WorkflowError("An output path would overwrite an input file.")

    try:
        roi = select_roi(reader.first_frame)
        validate_roi(roi, reader.info.width, reader.info.height)
        tracker = create_tracker(algorithm, model_paths or {})
        try:
            initialized = tracker.init(reader.first_frame, roi)
        except (cv2.error, TypeError, ValueError) as exc:
            raise WorkflowError(f"Could not initialize the {algorithm} tracker: {exc}") from exc
        if initialized is False:
            raise WorkflowError(f"The {algorithm} tracker rejected the selected ROI.")

        outputs.roi_image.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".step02a_", dir=outputs.roi_image.parent
        ) as temporary_directory:
            staging = Path(temporary_directory)
            staged_roi_image = staging / "roi.png"
            staged_roi_csv = staging / "roi.csv"
            staged_tracked_csv = staging / "tracked.csv"

            x, y, width, height = roi
            crop = reader.first_frame[y : y + height, x : x + width]
            if crop.size == 0 or not cv2.imwrite(str(staged_roi_image), crop):
                raise WorkflowError(f"Could not save selected ROI image: {outputs.roi_image}")
            _write_roi_csv(staged_roi_csv, roi)

            try:
                with staged_tracked_csv.open("w", newline="", encoding="utf-8") as csv_file:
                    writer = csv.DictWriter(csv_file, fieldnames=TRACK_CSV_FIELDS)
                    writer.writeheader()
                    _write_track_row(writer, 0, reader.info.fps, tuple(float(v) for v in roi))
                    for frame_index in range(1, reader.info.frame_count):
                        frame = reader.read_next()
                        if frame is None:
                            raise WorkflowError(
                                f"Source video ended or failed while reading frame {frame_index}."
                            )
                        if frame.shape[1] != reader.info.width or frame.shape[0] != reader.info.height:
                            raise WorkflowError(
                                f"Source frame {frame_index} has inconsistent dimensions."
                            )
                        try:
                            tracking_ok, updated = tracker.update(frame)
                        except (cv2.error, TypeError, ValueError) as exc:
                            raise WorkflowError(
                                f"The {algorithm} tracker failed on frame {frame_index}: {exc}"
                            ) from exc
                        box = _box_from_tracker(updated) if tracking_ok else None
                        _write_track_row(writer, frame_index, reader.info.fps, box)
            except (OSError, csv.Error) as exc:
                raise WorkflowError(f"Could not write tracking CSV: {exc}") from exc

            for staged, destination in (
                (staged_roi_image, outputs.roi_image),
                (staged_roi_csv, outputs.roi_csv),
                (staged_tracked_csv, outputs.tracked_csv),
            ):
                os.replace(staged, destination)
    except cv2.error as exc:
        raise WorkflowError(f"OpenCV failed during tracking: {exc}") from exc
    except OSError as exc:
        raise WorkflowError(f"Could not save Step02a outputs: {exc}") from exc
    finally:
        reader.close()
        close_windows()
    return outputs


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Step02a Run Trackers")
    try:
        model_paths = resolve_model_paths(args)
        settings_path = resolve_input_path(
            args.settings,
            "Select settings JSON",
            "Settings files (*.json);;All files (*.*)",
        )
        if settings_path is None:
            return 0
        video_path = resolve_input_path(
            args.video,
            "Select source video",
            "Video files (*.mp4 *.mov *.mkv *.avi *.wmv);;All files (*.*)",
        )
        if video_path is None:
            return 0
        outputs = run(settings_path, video_path, args.algorithm, model_paths)
    except (WorkflowError, OSError, cv2.error) as exc:
        QMessageBox.critical(None, "Step02a could not complete", str(exc))
        print(f"Step02a failed: {exc}", file=sys.stderr)
        return 1

    print(f"ROI image: {outputs.roi_image}")
    print(f"ROI coordinates: {outputs.roi_csv}")
    print(f"Tracking results: {outputs.tracked_csv}")
    QMessageBox.information(
        None,
        "Tracking complete",
        f"Saved outputs to:\n{outputs.roi_image.parent}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
