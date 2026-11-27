# Step02a Run Trackers Implementation Guide

## Goal

Implement a tool that tracks one user-selected object in a video with an OpenCV tracker. This step records the initially selected region and the tracker's per-frame results as image/CSV outputs. It does not write an annotated movie.

## Project layout and dependencies

- Store Step02a-specific code and run instructions in `Workflow/Step02a_RunTrackers/`.
- Use the repository-root `environment.yml`, which provides Python, OpenCV, PySide6, and FFmpeg for the overall workflow. Step02a requires OpenCV; use PySide6 file dialogs when paths are omitted.
- Put helpers in `Utilities/` only when they are independently reusable by other workflow steps.

## Inputs and settings

- Accept optional `--settings <path>` and `--video <path>` command-line options. Prompt with file dialogs for either omitted path, consistent with Steps 01b and 01c. Accept optional `--roi-csv <path>` to initialize from a saved Step02a ROI CSV rather than prompting for an ROI selection.
- Require `--algorithm` with one of these lowercase choices: `csrt`, `kcf`, `mil`, `boosting`, `medianflow`, `mosse`, `tld`, `dasiamrpn`, `nano`, or `vit`. Use the normalized algorithm name in output filenames. These options match the tracker classes exposed by the project's OpenCV 5.1 environment.
- Check tracker availability at runtime and report a clear error when the selected tracker is unavailable in the active OpenCV build. Support API variations in both `cv2` and `cv2.legacy` for classic trackers. Never silently substitute another algorithm.
- DaSiamRPN, Nano, and ViT require model files. Accept these conditional path arguments: `--siam-rpn-model`, `--kernel-cls1-model`, and `--kernel-r1-model` for DaSiamRPN; `--nano-backbone` and `--nano-neckhead` for Nano; and `--vit-model` for ViT. Require and validate only the model paths for the selected DNN tracker. Do not download model files automatically; report missing or unreadable files clearly.
- Read `ResultsDirectory` and `ScenarioName` from the settings JSON. Require both to be non-empty strings. Use an absolute `ResultsDirectory` directly; resolve a relative one from the settings file's parent directory. Require `ScenarioName` to be a valid single filename component. Report invalid JSON, missing values, and inaccessible paths clearly.
- Open the selected source video with OpenCV. Require a readable first frame, a finite positive frame rate, and a positive frame count. Use source dimensions and FPS throughout; do not resize or change frame order.

## ROI initialization and tracking

- If `--roi-csv` is supplied, load its one-row Step02a ROI CSV and skip the interactive ROI selector. Require exactly these columns in order: `x,y,width,height,center_x,center_y`; require one data row, finite numeric values, whole-pixel `x`, `y`, `width`, and `height`, and a box inside the first frame. Use the box coordinates to initialize tracking and recompute its center. If `--roi-csv` is omitted, let the user select one ROI interactively on the first video frame with OpenCV's ROI selector. Treat cancellation or a zero-area selection as a clear error. Do not read Step01a keyframes in this step.
- When all input paths and `--algorithm` are provided on the command line along with `--roi-csv`, run without showing an ROI-selection window or a success dialog. Keep the existing settings/video file dialogs when those two paths are omitted.
- Validate that the selected ROI lies within the first frame. Use integer pixel `x`, `y`, `width`, and `height`; define `x,y` as the box's top-left coordinate and calculate `center_x = x + width / 2`, `center_y = y + height / 2`.
- Initialize the selected tracker using the first frame and ROI. For model-based trackers, load the selected model files using that tracker's OpenCV API before initialization. If the requested tracker or required model files cannot be loaded, report a clear error and do not silently substitute another tracker.
- Record the initial ROI as a successful tracked result at frame index 0. For each later frame, update the tracker once and record its result. If an update fails, mark that frame `lost`, leave all coordinate fields blank, and continue processing later frames so the tracker can attempt recovery.
- Display a terminal progress bar while processing, counting the initial frame and each subsequent frame written to the tracking CSV. Show completed frames, total frames, and percentage; finish the bar on its own line, including for a one-frame video. If processing fails after progress has started, end the progress line before reporting the error.
- Derive timestamps as `frame_index / fps`. Use zero-based frame indices and pixel coordinates with the frame's top-left origin, x increasing rightward and y increasing downward.

## Outputs

- Create `<ResultsDirectory>/Step02a_RunTrackers/` when needed. Use the settings `ScenarioName` and lowercase algorithm name to produce:
  - `<ScenarioName>_<algorithm>_roi.png`: unannotated crop of the selected ROI from the first frame.
  - `<ScenarioName>_<algorithm>_roi.csv`: one row with exactly these columns, in order: `x,y,width,height,center_x,center_y`.
  - `<ScenarioName>_<algorithm>_tracked.csv`: one row for every decoded video frame, with exactly these columns, in order: `frame_index,timestamp_seconds,tracking_status,x,y,width,height,center_x,center_y`.
- Use `tracked` and `lost` for `tracking_status`. For `lost` rows, write blank values for all coordinate and size columns. For the initial and subsequent successful rows, write the tracker's box and its center.
- Do not create a movie file. Do not modify the source video or settings JSON. Existing files with these output names may be replaced; report all output paths on success.
- Load and validate an ROI CSV before staging outputs. Permit it to be the same path as the generated `<ScenarioName>_<algorithm>_roi.csv` (the supplied ROI has already been loaded before the output is replaced). Reject collisions with the ROI image or tracked CSV outputs.

## Reliability and completion criteria

- Release the video capture and close OpenCV windows on normal completion and handled errors.
- Report understandable errors for missing or unreadable inputs, invalid settings, bad video metadata, canceled or invalid ROI selection, unavailable tracker APIs, and output-write failures. Do not leave partial CSV data presented as a successful result.
- The user can run Step02a with a video, settings JSON, and any supported explicit algorithm choice. The tool writes the ROI PNG, ROI CSV, and full per-frame tracking CSV in the settings-derived output folder, without producing a movie.
- While processing frames, the terminal shows progress through the video as a percentage and completed/total frame count.
- A valid `--roi-csv` bypasses manual ROI selection; a missing option preserves the interactive selection flow. If all paths are supplied, the ROI-CSV workflow completes without further prompts.
- All tracker choices use the same output schemas and coordinate conventions. Every frame has exactly one tracking CSV row; lost frames have blank coordinates and do not stop later frame processing.

## Verification scenarios

- Check valid startup using file dialogs and command-line paths, settings path resolution, and rejection of an invalid algorithm name.
- Confirm that a valid `--roi-csv` skips ROI selection and that omitting it preserves manual selection. Test exact schema, one-row, numeric, whole-pixel, and frame-bound validation, including using the generated ROI CSV as both input and output.
- For each available classic tracker, confirm the initial ROI is saved, frame 0 is represented in the tracking CSV, and output names use the correct lowercase algorithm suffix.
- For DaSiamRPN, Nano, and ViT, check required model arguments, successful model loading with valid files, and clear failure for missing or unreadable model files.
- Confirm CSV headers and ordering, one tracking row per source frame, timestamps, top-left-origin ROI values, and blank coordinates on lost frames.
- Confirm tracking continues after a failed update and no annotated movie is created.
- Exercise canceled/zero-area ROI selection, unreadable video, invalid FPS/frame count, malformed settings, unavailable tracker factory, and output-write failures; each should fail clearly and clean up resources.
