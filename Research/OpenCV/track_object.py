"""Track one manually selected object in an MP4 video using OpenCV CSRT."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Any

import cv2


CSV_FIELDS = (
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
ROI_CSV_FIELDS = ("x", "y", "width", "height", "center_x", "center_y")


class TrackingError(Exception):
    """An expected error that should be presented clearly to the user."""


def close_windows() -> None:
    """Close OpenCV windows without masking the original error on headless builds."""
    try:
        cv2.destroyAllWindows()
    except cv2.error:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select an object in the first frame of an MP4, track it with "
            "CSRT, and save the selected ROI, a CSV, and an annotated MP4."
        )
    )
    parser.add_argument("video", type=Path, help="path to the input MP4 video")
    parser.add_argument(
        "--roi-csv",
        type=Path,
        help=(
            "path to a one-row ROI CSV containing x, y, width, height; "
            "when provided, skip manual ROI selection"
        ),
    )
    return parser.parse_args()


def make_csrt_tracker() -> Any:
    """Create CSRT while accounting for OpenCV's Python API variants."""
    candidates = (
        (cv2, "TrackerCSRT_create"),
        (cv2, "TrackerCSRT"),
        (getattr(cv2, "legacy", None), "TrackerCSRT_create"),
        (getattr(cv2, "legacy", None), "TrackerCSRT"),
    )
    for namespace, name in candidates:
        if namespace is None or not hasattr(namespace, name):
            continue
        factory = getattr(namespace, name)
        return factory() if name.endswith("_create") else factory.create()

    raise TrackingError(
        "This OpenCV installation does not expose the CSRT tracker. "
        "Install the conda-forge OpenCV package from the repository-root "
        "environment.yml, then "
        "verify the installation with the commands in README.md."
    )


def select_roi(frame: Any) -> tuple[int, int, int, int]:
    try:
        roi = cv2.selectROI(
            "Select object (Enter/Space to accept, C to cancel)",
            frame,
            showCrosshair=True,
            fromCenter=False,
        )
    except cv2.error as exc:
        raise TrackingError(
            "OpenCV could not open the ROI selection window. Run this script "
            "in a desktop session with a GUI-enabled OpenCV build."
        ) from exc
    finally:
        close_windows()

    x, y, width, height = (int(value) for value in roi)
    if width <= 0 or height <= 0:
        raise TrackingError("ROI selection was canceled or had zero area.")
    return x, y, width, height


def load_roi_csv(csv_path: Path) -> tuple[int, int, int, int]:
    """Load one pixel-aligned ROI from a CSV produced by this workflow."""
    csv_path = csv_path.expanduser().resolve()
    if not csv_path.is_file():
        raise TrackingError(f"ROI CSV does not exist or is not a file: {csv_path}")

    try:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            required = {"x", "y", "width", "height"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                required_names = ", ".join(sorted(required))
                raise TrackingError(
                    f"ROI CSV must have these column headers: {required_names}."
                )

            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise TrackingError(f"Could not read ROI CSV {csv_path}: {exc}") from exc

    if len(rows) != 1:
        raise TrackingError(
            f"ROI CSV must contain exactly one data row; found {len(rows)}."
        )

    values: list[int] = []
    for field in ("x", "y", "width", "height"):
        raw_value = rows[0].get(field)
        try:
            value = float(raw_value) if raw_value is not None else math.nan
        except ValueError as exc:
            raise TrackingError(f"ROI CSV field '{field}' must be a pixel number.") from exc
        if not math.isfinite(value) or not math.isclose(value, round(value), abs_tol=1e-6):
            raise TrackingError(f"ROI CSV field '{field}' must be a finite whole-pixel value.")
        values.append(int(round(value)))

    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise TrackingError("ROI CSV coordinates must be nonnegative and dimensions must be positive.")
    return x, y, width, height


def box_values(box: Any) -> tuple[float, float, float, float] | None:
    try:
        x, y, width, height = (float(value) for value in box)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def write_annotated_frame(
    frame: Any,
    writer: Any,
    frame_index: int,
    box: tuple[float, float, float, float] | None,
) -> None:
    annotated = frame.copy()
    if box is None:
        cv2.putText(
            annotated,
            "TARGET LOST",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    else:
        x, y, width, height = box
        top_left = (round(x), round(y))
        bottom_right = (round(x + width), round(y + height))
        cv2.rectangle(annotated, top_left, bottom_right, (0, 255, 0), 2)
        center = (round(x + width / 2), round(y + height / 2))
        cv2.circle(annotated, center, 3, (0, 255, 255), -1)

    cv2.putText(
        annotated,
        f"Frame: {frame_index}",
        (20, annotated.shape[0] - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    writer.write(annotated)


def write_csv_row(
    csv_writer: Any,
    frame_index: int,
    fps: float,
    box: tuple[float, float, float, float] | None,
) -> None:
    if box is None:
        csv_writer.writerow(
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
    csv_writer.writerow(
        {
            "frame_index": frame_index,
            "timestamp_seconds": f"{frame_index / fps:.6f}",
            "tracking_status": "tracked",
            "x": f"{x:.3f}",
            "y": f"{y:.3f}",
            "width": f"{width:.3f}",
            "height": f"{height:.3f}",
            "center_x": f"{x + width / 2:.3f}",
            "center_y": f"{y + height / 2:.3f}",
        }
    )


def run(video_path: Path, roi_csv_path: Path | None = None) -> tuple[Path, Path, Path, Path]:
    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise TrackingError(f"Input video does not exist or is not a file: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise TrackingError(f"Expected an MP4 input file, got: {video_path.name}")

    capture = cv2.VideoCapture(str(video_path))
    writer = None
    try:
        if not capture.isOpened():
            raise TrackingError(f"OpenCV could not open the video: {video_path}")

        ok, first_frame = capture.read()
        if not ok or first_frame is None or first_frame.size == 0:
            raise TrackingError(f"The video has no readable frames: {video_path}")

        fps = float(capture.get(cv2.CAP_PROP_FPS))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not math.isfinite(fps) or fps <= 0:
            raise TrackingError(
                f"The video reports an invalid FPS ({fps!r}); cannot create a correctly timed preview."
            )
        if width <= 0 or height <= 0:
            raise TrackingError("The video reports invalid frame dimensions.")
        if first_frame.shape[1] != width or first_frame.shape[0] != height:
            height, width = first_frame.shape[:2]

        tracker = make_csrt_tracker()
        roi = load_roi_csv(roi_csv_path) if roi_csv_path is not None else select_roi(first_frame)
        x, y, roi_width, roi_height = roi
        frame_height, frame_width = first_frame.shape[:2]
        if (
            x < 0
            or y < 0
            or x + roi_width > frame_width
            or y + roi_height > frame_height
        ):
            raise TrackingError("The selected ROI extends outside the first video frame.")
        try:
            tracker.init(first_frame, roi)
        except cv2.error as exc:
            raise TrackingError(f"Could not initialize the CSRT tracker: {exc}") from exc

        output_dir = video_path.parent / "tracked_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_roi = output_dir / f"{video_path.stem}_roi.png"
        output_roi_csv = output_dir / f"{video_path.stem}_roi.csv"
        output_csv = output_dir / f"{video_path.stem}_tracked.csv"
        output_video = output_dir / f"{video_path.stem}_tracked.mp4"

        roi_crop = first_frame[y : y + roi_height, x : x + roi_width]
        if roi_crop.size == 0 or not cv2.imwrite(str(output_roi), roi_crop):
            raise TrackingError(f"Could not save the selected ROI image at {output_roi}.")
        center_x = x + roi_width / 2
        center_y = y + roi_height / 2
        try:
            with output_roi_csv.open("w", newline="", encoding="utf-8") as roi_csv_file:
                roi_csv_writer = csv.DictWriter(roi_csv_file, fieldnames=ROI_CSV_FIELDS)
                roi_csv_writer.writeheader()
                roi_csv_writer.writerow(
                    {
                        "x": x,
                        "y": y,
                        "width": roi_width,
                        "height": roi_height,
                        "center_x": f"{center_x:.3f}",
                        "center_y": f"{center_y:.3f}",
                    }
                )
        except OSError as exc:
            raise TrackingError(f"Could not save the selected ROI coordinates at {output_roi_csv}: {exc}") from exc

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        try:
            writer = cv2.VideoWriter(str(output_video), fourcc, fps, (width, height))
        except cv2.error as exc:
            raise TrackingError(
                f"OpenCV could not create the MP4 preview at {output_video}: {exc}"
            ) from exc
        if not writer.isOpened():
            raise TrackingError(
                f"Could not create the MP4 preview at {output_video}. "
                "Check that this OpenCV build supports the mp4v codec."
            )

        lost_frames = 0
        frame_index = 0
        current_frame = first_frame
        current_box: tuple[float, float, float, float] | None = tuple(
            float(value) for value in roi
        )
        with output_csv.open("w", newline="", encoding="utf-8") as csv_file:
            csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            csv_writer.writeheader()

            while True:
                if frame_index > 0:
                    try:
                        tracking_ok, updated_box = tracker.update(current_frame)
                        current_box = box_values(updated_box) if tracking_ok else None
                    except cv2.error as exc:
                        if lost_frames == 0:
                            print(f"Tracker update failed; marking affected frames lost: {exc}", file=sys.stderr)
                        current_box = None

                if current_box is None:
                    lost_frames += 1

                write_csv_row(csv_writer, frame_index, fps, current_box)
                write_annotated_frame(current_frame, writer, frame_index, current_box)

                ok, next_frame = capture.read()
                if not ok:
                    break
                current_frame = next_frame
                frame_index += 1

        print(f"Processed {frame_index + 1} frames.")
        print(f"Selected ROI: {output_roi}")
        print(f"ROI coordinates: {output_roi_csv}")
        print(f"CSV: {output_csv}")
        print(f"Preview: {output_video}")
        if lost_frames:
            print(
                f"The tracker reported a lost/invalid target on {lost_frames} frame(s). "
                "Review the preview and reselect the target if needed.",
                file=sys.stderr,
            )
        return output_roi, output_roi_csv, output_csv, output_video
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        close_windows()


def main() -> int:
    args = parse_args()
    try:
        run(args.video, args.roi_csv)
    except TrackingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"File or output error: {exc}", file=sys.stderr)
        return 1
    except cv2.error as exc:
        print(f"OpenCV error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
