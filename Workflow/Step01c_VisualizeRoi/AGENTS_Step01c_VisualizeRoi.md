# Step01c Visualize ROI Implementation Guide

## Goal

Implement a tool that reads the per-frame ROI data from Step01b, draws the ROI on the corresponding source-video frame, and writes an annotated MP4. Preserve source audio when present.

## Project layout and dependencies

- Store Step01c-specific code and run instructions in `Workflow/Step01c_VisualizeRoi/`.
- Use the repository-root `environment.yml` for shared dependencies. It must include OpenCV and FFmpeg; OpenCV decodes and annotates frames, and FFmpeg muxes the source audio into the rendered video.
- Place helpers in `Utilities/` only when they are independently reusable by other workflow steps.

## Inputs and settings

- Accept optional `--settings <path>`, `--keyframes <path>`, and `--video <path>` command-line options. Prompt with file dialogs for any omitted path, consistent with Step01b.
- Read `ResultsDirectory` and `ScenarioName` from settings JSON. Require non-empty strings, resolve a relative `ResultsDirectory` against the settings file's parent folder, and require `ScenarioName` to be a valid filename component.
- Open the original video and use its frame count, FPS, and decoded frame dimensions as the output timing and geometry source. Report a clear error if the video cannot be opened, has no readable frames, or reports invalid metadata.
- Read the Step01b CSV with exactly these columns in order: `frame_index,timestamp_seconds,width,height,center_x,center_y`. Require one unique row for every source frame index from zero through `frame_count - 1`, finite values, positive ROI dimensions, and ROIs wholly within the frame. Reject incomplete, duplicate, out-of-range, or malformed data before rendering.

## Rendering and audio

- Match each CSV row to the video frame with the same zero-based `frame_index`; draw a green, 2-pixel rectangle centered at `(center_x, center_y)` with the row's `width` and `height`.
- Render exactly one output frame per input frame, preserving frame order, source dimensions, and FPS. Do not resize, interpolate, skip, or add frames.
- Display a terminal progress meter during rendering with completed frames, total frames, and percentage. Update at percentage changes rather than printing for every frame; print percentage updates on separate lines when stdout is redirected. Print a clear status message while FFmpeg muxes audio.
- Write the annotated video to a temporary silent MP4 in the Step01c output folder, then use FFmpeg to mux the original audio stream into `<ResultsDirectory>/Step01c_VisualizeRoi/<ScenarioName>_visualize_roi.mp4`.
- If the source has no audio stream, keep the annotated video silent. If it has audio, require the final output to contain that audio; fail clearly if FFmpeg or muxing fails instead of silently dropping the audio. Clean up temporary files after success and on handled errors.
- Create the output folder if needed. Do not modify the source video, input CSV, or settings JSON. Report the final output path on success.

## Completion criteria

- Users can provide all three paths by command line or choose any omitted input through file dialogs.
- Every output frame has the ROI from its corresponding CSV row and matches source frame count, frame order, dimensions, and FPS.
- The terminal shows rendering progress through completion and identifies the audio-mux phase when the source has audio.
- The output filename is `<ScenarioName>_visualize_roi.mp4` under `<ResultsDirectory>/Step01c_VisualizeRoi/`.
- Source audio is present in the output when the input has audio; an input without audio produces a silent annotated MP4.
- Invalid settings, video, frame metadata, CSV schema or rows, out-of-bounds ROI data, output write failures, and FFmpeg errors produce understandable feedback without changing inputs.
