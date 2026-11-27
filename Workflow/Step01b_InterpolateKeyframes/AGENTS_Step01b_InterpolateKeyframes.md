# Step01b Interpolate Keyframes Implementation Guide

## Goal

Implement a tool that reads sparse manually authored ROI keyframes from Step01a and produces one ROI record for every frame in the source video. Interpolate ROI dimensions and position linearly between keyframes, and extend the nearest endpoint keyframe outside the keyed interval.

## Project layout and dependencies

- Store Step01b-specific code and run instructions in `Workflow/Step01b_InterpolateKeyframes/`.
- Use the existing repository-root `environment.yml`; OpenCV provides video metadata and PySide6 provides file dialogs when inputs are not supplied on the command line.
- Put a helper in `Utilities/` only if it is independently reusable by other workflow steps.

## Inputs and settings

- Accept optional `--settings <path>`, `--keyframes <path>`, and `--video <path>` command-line options. For any omitted path, prompt the user with an appropriate file dialog. Support any combination of command-line paths and dialogs.
- Read `ResultsDirectory` and `ScenarioName` from the settings JSON. Require both to be non-empty strings. Resolve absolute result paths as given and relative paths from the settings file's parent directory. Require `ScenarioName` to be a valid single filename component. Report invalid JSON, missing values, and inaccessible paths clearly.
- Open the source video with OpenCV and obtain its frame count and FPS. Require a positive frame count and finite positive FPS; report a clear error if metadata or the video is unreadable.
- Read the Step01a CSV with exactly the required columns `frame_index,timestamp_seconds,width,height,center_x,center_y`. Require at least one valid row, unique integer frame indices within `0..frame_count-1`, finite numeric ROI values, and positive width and height. Reject malformed or out-of-range input rather than silently changing it.
- The Step01a input timestamps are not used for output timing; derive output timestamps from the source video FPS so each row's time matches its frame index.

## Interpolation behavior

- Generate exactly one output row for every source frame, from frame index `0` through `frame_count - 1`.
- For each ROI property (`width`, `height`, `center_x`, and `center_y`), interpolate linearly by frame index between each pair of neighboring keyframes. Preserve the exact supplied keyframe values at their frame indices.
- For frames before the first keyframe, use the first keyframe's values. For frames after the last keyframe, use the last keyframe's values. If the input has one keyframe, hold its ROI values for the entire video.
- Keep ROI values in source-video pixels using the top-left origin (x increases rightward, y increases downward). Do not normalize coordinates or convert origins.

## Output

- Write to `<ResultsDirectory>/Step01b_InterpolateKeyframes/<ScenarioName>_interpolated_keyframes.csv`. Create the Step01b output directory if needed and do not modify the source video, settings JSON, or Step01a CSV.
- Use exactly these columns in this order: `frame_index,timestamp_seconds,width,height,center_x,center_y`.
- Sort rows by frame index. Use zero-based indices and set `timestamp_seconds = frame_index / fps`.
- Report successful output with the path and provide understandable feedback if directory creation or CSV writing fails.

## Completion criteria

- Users can supply all three paths on the command line or choose any missing input through dialogs.
- Output contains each source frame index once, has the same row count as the video frame count, and preserves source keyframes exactly.
- Intermediate parameter values follow linear interpolation; rows outside the keyed interval hold the nearest endpoint values.
- Invalid settings or filename component, unreadable video, invalid frame metadata, malformed/empty keyframe CSV, duplicate or out-of-range keyframes, invalid ROI values, and output write failures produce clear errors without changing input files.
