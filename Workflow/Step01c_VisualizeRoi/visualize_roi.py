"""Render a Step01b ROI overlay onto its source video."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


class WorkflowError(Exception):
    """An expected input or processing error suitable for a user message."""


@dataclass(frozen=True)
class VideoInfo:
    frame_count: int
    fps: float
    width: int
    height: int


@dataclass(frozen=True)
class Roi:
    width: float
    height: float
    center_x: float
    center_y: float


class TerminalProgress:
    """Show percentage changes as a bar or as readable lines when redirected."""

    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = total
        self.is_tty = sys.stdout.isatty()
        self.last_percent = -1
        self.finished = False
        print(f"{label}...", flush=True)

    def update(self, completed: int) -> None:
        percent = min(100, int(completed * 100 / self.total))
        if percent == self.last_percent and completed != self.total:
            return
        self.last_percent = percent
        if self.is_tty:
            width = 30
            filled = round(width * percent / 100)
            bar = "#" * filled + "-" * (width - filled)
            print(
                f"\r{self.label}: [{bar}] {percent:3d}% ({completed}/{self.total})",
                end="",
                flush=True,
            )
            if completed == self.total:
                print(flush=True)
        else:
            print(f"{self.label}: {percent}% ({completed}/{self.total})", flush=True)
        self.finished = completed == self.total

    def finish(self) -> None:
        if self.is_tty and not self.finished:
            print(flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw the Step01b ROI on every frame of the source video."
    )
    parser.add_argument("--settings", type=Path, help="settings JSON containing ResultsDirectory and ScenarioName")
    parser.add_argument("--keyframes", type=Path, help="Step01b per-frame ROI CSV")
    parser.add_argument("--video", type=Path, help="original source video")
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
        raise WorkflowError("Settings JSON 'ScenarioName' must be a valid filename component.")

    results_directory = Path(raw_directory).expanduser()
    if not results_directory.is_absolute():
        results_directory = settings_path.parent / results_directory
    return results_directory.resolve(), scenario_name


def _parse_finite_number(value: str | None, field: str, row_number: int) -> float:
    try:
        number = float(value) if value is not None else math.nan
    except ValueError as exc:
        raise WorkflowError(f"Step01b CSV row {row_number}: '{field}' must be numeric.") from exc
    if not math.isfinite(number):
        raise WorkflowError(f"Step01b CSV row {row_number}: '{field}' must be finite.")
    return number


def get_video_info(video_path: Path) -> VideoInfo:
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise WorkflowError(f"OpenCV could not open the source video: {video_path}")

        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count_raw = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0:
            raise WorkflowError(f"The source video has an invalid FPS: {fps!r}")
        if (
            not math.isfinite(frame_count_raw)
            or frame_count_raw < 1
            or not frame_count_raw.is_integer()
        ):
            raise WorkflowError("OpenCV could not determine a positive integer video frame count.")

        ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            raise WorkflowError("The source video contains no readable frames.")
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            raise WorkflowError("The source video has invalid frame dimensions.")
        return VideoInfo(int(frame_count_raw), fps, width, height)
    finally:
        capture.release()


def load_rois(csv_path: Path, video: VideoInfo) -> list[Roi]:
    try:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames != list(CSV_FIELDS):
                raise WorkflowError(
                    "Step01b CSV must have these columns in order: " + ",".join(CSV_FIELDS)
                )
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise WorkflowError(f"Could not read Step01b CSV {csv_path}: {exc}") from exc

    if len(rows) != video.frame_count:
        raise WorkflowError(
            f"Step01b CSV has {len(rows)} data rows, but the video has "
            f"{video.frame_count} frames."
        )

    by_frame: list[Roi | None] = [None] * video.frame_count
    for row_number, row in enumerate(rows, start=2):
        if None in row:
            raise WorkflowError(f"Step01b CSV row {row_number} has extra fields.")
        raw_index = _parse_finite_number(row.get("frame_index"), "frame_index", row_number)
        if not raw_index.is_integer():
            raise WorkflowError(f"Step01b CSV row {row_number}: frame_index must be an integer.")
        frame_index = int(raw_index)
        if not 0 <= frame_index < video.frame_count:
            raise WorkflowError(
                f"Step01b CSV row {row_number}: frame_index {frame_index} is outside "
                f"0..{video.frame_count - 1}."
            )
        if by_frame[frame_index] is not None:
            raise WorkflowError(f"Step01b CSV has duplicate frame_index {frame_index}.")

        _parse_finite_number(row.get("timestamp_seconds"), "timestamp_seconds", row_number)
        roi = Roi(
            width=_parse_finite_number(row.get("width"), "width", row_number),
            height=_parse_finite_number(row.get("height"), "height", row_number),
            center_x=_parse_finite_number(row.get("center_x"), "center_x", row_number),
            center_y=_parse_finite_number(row.get("center_y"), "center_y", row_number),
        )
        if roi.width <= 0 or roi.height <= 0:
            raise WorkflowError(f"Step01b CSV row {row_number}: width and height must be positive.")
        if (
            roi.center_x - roi.width / 2 < 0
            or roi.center_x + roi.width / 2 > video.width
            or roi.center_y - roi.height / 2 < 0
            or roi.center_y + roi.height / 2 > video.height
        ):
            raise WorkflowError(f"Step01b CSV row {row_number}: ROI extends outside the video frame.")
        by_frame[frame_index] = roi

    missing = [index for index, roi in enumerate(by_frame) if roi is None]
    if missing:
        preview = ", ".join(str(index) for index in missing[:8])
        suffix = "..." if len(missing) > 8 else ""
        raise WorkflowError(f"Step01b CSV is missing frame indices: {preview}{suffix}")
    return [roi for roi in by_frame if roi is not None]


def find_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise WorkflowError(
            f"Could not find {name}. Activate the project Conda environment, "
            "which includes FFmpeg."
        )
    return executable


def run_probe(ffprobe: str, arguments: list[str], input_path: Path) -> str:
    command = [ffprobe, "-v", "error", *arguments, str(input_path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise WorkflowError(f"Could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or f"ffprobe exited with code {result.returncode}."
        raise WorkflowError(f"Could not inspect {input_path}: {message}")
    return result.stdout.strip()


def has_audio_stream(ffprobe: str, video_path: Path) -> bool:
    streams = run_probe(
        ffprobe,
        ["-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0"],
        video_path,
    )
    return bool(streams)


def verify_video_frame_count(ffprobe: str, path: Path, expected_count: int) -> None:
    value = run_probe(
        ffprobe,
        [
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
        ],
        path,
    )
    try:
        count = int(value)
    except ValueError as exc:
        raise WorkflowError(f"Could not verify the rendered frame count for {path}.") from exc
    if count != expected_count:
        raise WorkflowError(
            f"Rendered video has {count} frames; expected {expected_count}."
        )


def has_audio_in_output(ffprobe: str, path: Path) -> bool:
    return has_audio_stream(ffprobe, path)


def draw_roi(frame: object, roi: Roi) -> None:
    left = round(roi.center_x - roi.width / 2)
    top = round(roi.center_y - roi.height / 2)
    right = round(roi.center_x + roi.width / 2)
    bottom = round(roi.center_y + roi.height / 2)
    cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)


def render_silent_video(
    video_path: Path,
    output_path: Path,
    video: VideoInfo,
    rois: list[Roi],
) -> None:
    capture = cv2.VideoCapture(str(video_path))
    writer = None
    progress: TerminalProgress | None = None
    try:
        if not capture.isOpened():
            raise WorkflowError(f"OpenCV could not reopen the source video: {video_path}")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(
            str(output_path), fourcc, video.fps, (video.width, video.height)
        )
        if not writer.isOpened():
            raise WorkflowError(f"Could not create the temporary annotated video: {output_path}")
        progress = TerminalProgress("Rendering ROI overlay", len(rois))

        for frame_index, roi in enumerate(rois):
            ok, frame = capture.read()
            if not ok or frame is None or frame.size == 0:
                raise WorkflowError(
                    f"Source video ended or failed while reading frame {frame_index}."
                )
            if frame.shape[1] != video.width or frame.shape[0] != video.height:
                raise WorkflowError(f"Source frame {frame_index} has inconsistent dimensions.")
            draw_roi(frame, roi)
            writer.write(frame)
            progress.update(frame_index + 1)
    except cv2.error as exc:
        raise WorkflowError(f"OpenCV failed while rendering the ROI overlay: {exc}") from exc
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if progress is not None:
            progress.finish()


def mux_audio(
    ffmpeg: str,
    rendered_video: Path,
    source_video: Path,
    muxed_output: Path,
    video: VideoInfo,
) -> None:
    print("Muxing source audio with FFmpeg...", flush=True)
    duration_seconds = video.frame_count / video.fps
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(rendered_video),
        "-i",
        str(source_video),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-t",
        f"{duration_seconds:.12f}",
        "-movflags",
        "+faststart",
        str(muxed_output),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise WorkflowError(f"Could not run FFmpeg to preserve source audio: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or f"FFmpeg exited with code {result.returncode}."
        raise WorkflowError(f"FFmpeg could not mux the source audio: {message}")


def render_video(
    video_path: Path,
    output_path: Path,
    video: VideoInfo,
    rois: list[Roi],
    ffmpeg: str,
    ffprobe: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_fd, render_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_", suffix=".silent.mp4", dir=output_path.parent
    )
    os.close(render_fd)
    silent_temp = Path(render_name)
    mux_temp: Path | None = None

    try:
        render_silent_video(video_path, silent_temp, video, rois)
        verify_video_frame_count(ffprobe, silent_temp, video.frame_count)

        source_has_audio = has_audio_stream(ffprobe, video_path)
        if source_has_audio:
            mux_fd, mux_name = tempfile.mkstemp(
                prefix=f".{output_path.stem}_", suffix=".muxed.mp4", dir=output_path.parent
            )
            os.close(mux_fd)
            mux_temp = Path(mux_name)
            mux_audio(ffmpeg, silent_temp, video_path, mux_temp, video)
            if not has_audio_in_output(ffprobe, mux_temp):
                raise WorkflowError("FFmpeg completed, but the output contains no audio stream.")
            verify_video_frame_count(ffprobe, mux_temp, video.frame_count)
            os.replace(mux_temp, output_path)
            mux_temp = None
        else:
            print("Source video has no audio; keeping the annotated video silent.", flush=True)
            os.replace(silent_temp, output_path)
    finally:
        silent_temp.unlink(missing_ok=True)
        if mux_temp is not None:
            mux_temp.unlink(missing_ok=True)


def run(settings_path: Path, keyframes_path: Path, video_path: Path) -> Path:
    results_directory, scenario_name = load_settings(settings_path)
    video = get_video_info(video_path)
    rois = load_rois(keyframes_path, video)
    output_path = (
        results_directory
        / "Step01c_VisualizeRoi"
        / f"{scenario_name}_visualize_roi.mp4"
    )

    output_resolved = output_path.resolve()
    for input_path in (settings_path, keyframes_path, video_path):
        if output_resolved == input_path.resolve():
            raise WorkflowError(f"Output path would overwrite an input file: {input_path}")

    ffmpeg = find_executable("ffmpeg")
    ffprobe = find_executable("ffprobe")
    try:
        render_video(video_path, output_path, video, rois, ffmpeg, ffprobe)
    except (OSError, csv.Error) as exc:
        raise WorkflowError(f"Could not write annotated video {output_path}: {exc}") from exc
    return output_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Step01c Visualize ROI")

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
            "Select Step01b ROI CSV",
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
        QMessageBox.critical(None, "Step01c could not complete", str(exc))
        return 1

    print(f"Annotated video saved to: {output_path}")
    QMessageBox.information(None, "Visualization complete", f"Saved video to:\n{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
