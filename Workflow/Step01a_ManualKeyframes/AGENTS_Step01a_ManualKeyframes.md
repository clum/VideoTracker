# Step01a Manual Keyframes Implementation Guide

## Goal

Implement a desktop application that lets a user manually define ROI keyframes in one video and save those keyframes to CSV. This step records manually authored keypoints only; it does not track the object automatically or interpolate between keypoints.

## Project layout

- Store Step01a application code and run instructions in `Workflow/Step01a_ManualKeyframes/`. Keep shared Conda dependencies in the repository-root `environment.yml`.
- Put components that are clearly reusable by later workflow steps in `Utilities/`. Keep Step01a-specific behavior in its own folder.
- Use PySide6 for the desktop GUI and OpenCV for opening, decoding, and seeking within the video. Keep GUI and video operations responsive; use a worker or timer-based frame loading if needed rather than blocking the interface during playback or seeking.

## Inputs and startup

- Accept `--video <path>` and `--settings <path>` command-line options. Each option is optional: when omitted, prompt for that path using a GUI file dialog. Support either or both paths being supplied on the command line. Also accept optional `--keyframes <path>` to import a Step01a keyframe CSV at startup; when it is omitted, keyframes can be imported from the GUI.
- Open the selected video with OpenCV. Show a clear error for a missing/unreadable video or one with no readable frames. Determine frame timestamps from the video frame rate; report a clear error if frame timing cannot be determined reliably.
- Read the settings JSON and require a non-empty `ResultsDirectory` value. If it is an absolute path, use it directly. If it is relative, resolve it relative to the directory containing the settings JSON. Report invalid JSON, missing keys, and inaccessible paths clearly.
- Save keyframes under `<ResultsDirectory>/Step01a_ManualKeyframes/`. Create the directory if it does not exist. Use `<video-stem>_manual_keyframes.csv` as the default output filename.

## GUI and ROI interaction

- Display the current video frame with controls to play/pause, adjust playback speed, and scrub to a frame. Provide speed presets of 0.05×, 0.1×, 0.25×, 0.5×, 0.75×, 1×, 1.5×, 2×, 5×, 10×, and 20×, with 1× selected by default. Derive the timer interval from the source frame rate and selected multiplier; apply changes immediately during playback without starting playback when paused. Playback speed affects display cadence only: keep frame indices, source timestamps, keyframe data, and CSV output unchanged.
- Show the current frame index and timestamp.
- Bind window-wide keyboard shortcuts: `P` activates Play/Pause; Left/Right seeks by 5 frames and pauses at the destination; `E`/`D` increases/decreases the current ROI height by 1 pixel; `F`/`S` decreases/increases its width by 1 pixel. Suspend shortcuts while a numeric or text field is being edited. ROI size shortcuts modify only an existing keyframe at the current frame, use normal ROI validation, and leave the keyframe unchanged if the adjustment is invalid or no keyframe exists.
- Display these shortcuts in a compact **Keyboard shortcuts** text box in the GUI.
- Provide a zero-based frame-number control that seeks directly to the specified frame, bounded by the video's first and last frames.
- Provide a timeline zoom control with a fit-to-video view and zoomed views centered around the current frame. Keep the visible timeline window synchronized when seeking or playing beyond its current range; zoom must not change frame indices or timestamps.
- Provide a video canvas for ROI editing, numeric controls for `width`, `height`, `center_x`, and `center_y`, a running keypoint list, and a button to save the keypoints CSV.
- Provide **Modify Selected** and **Delete Selected** actions for the keypoint list. Disable both when no keypoint is selected. Modifying opens an editor for all four ROI parameters, validates the whole ROI against frame bounds, and updates the selected keyframe, overlay, list, and numeric controls. Deleting asks for confirmation, removes that frame's keypoint and overlay, and refreshes the list and controls. The deletion should be reflected in the next CSV save.
- Provide an **Import Keyframes CSV** button. Importing replaces the current keyframe set only after the entire CSV has parsed and validated successfully. On success, refresh the list and overlay and seek to the earliest imported keyframe; a header-only file replaces the set with no keyframes. On cancel or failure, preserve the current keyframes.
- Interpret all ROI parameters in source-video pixels. The coordinate origin is the frame's top-left corner; x increases to the right and y increases downward. `center_x` and `center_y` specify the ROI center. Map mouse coordinates through any display scaling or letterboxing back to source-frame pixels.
- Let the user click and drag on the video to define a bounding box. Convert the box to width, height, and center coordinates and store it as a keypoint at the current frame.
- Require the first keypoint to be created by dragging a box, since no earlier ROI dimensions exist. After that, a single click at a frame creates or updates that frame's keypoint center while inheriting width and height from the preceding keypoint in frame order.
- When the current frame already has a keypoint, editing it replaces that keypoint; never keep duplicate keypoints for one frame. Keep the displayed list ordered by frame index. Selecting a keypoint in the list seeks to its frame and loads its values into the controls.
- Let users edit all four values numerically for the current keypoint. Update the stored keypoint and ROI overlay when a value changes. Validate positive width and height and require the whole ROI to remain within frame bounds. Reject an invalid edit with a clear message and keep the last valid keypoint values; do not silently clamp or rewrite the user's input.
- Enable numeric controls only when a keypoint exists at the current frame. For a single click on an existing keypoint, change only its center and preserve that keypoint's current width and height. For a new frame, inherit dimensions from the nearest preceding keypoint in frame order; if none exists, require a box drag to establish the dimensions.
- Draw the ROI overlay on stored keypoint frames. Do not interpolate or generate ROI values for frames between keypoints in this step.

## CSV output

- The Save Keyframes button writes one row per stored keypoint to `<ResultsDirectory>/Step01a_ManualKeyframes/<video-stem>_manual_keyframes.csv`.
- Use exactly these columns, in this order: `frame_index,timestamp_seconds,width,height,center_x,center_y`.
- Import the same CSV schema. Validate required columns, numeric finite values, unique frame indices within the selected video's range, and ROI bounds for every row before applying any imported keyframes. Use frame indices and ROI pixel values from the file; derive timestamps from the selected video's FPS when displaying or saving.
- Use zero-based frame indices. Compute `timestamp_seconds` as `frame_index / fps`. Preserve pixel-coordinate values without converting to a bottom-left origin or normalized units.
- Sort rows by frame index and write a header even when there are no keypoints. Do not modify the input video or settings file. Show a success message with the saved CSV path and a clear error if writing fails.

## Completion criteria

- The user can launch with both paths on the command line, select either missing path through dialogs, scrub video, jump to a zero-based frame number, zoom the timeline, and play or pause it. All speed presets are available, default to 1×, and adjust playback cadence immediately while playing without changing frame indices, timestamps, keyframe data, or CSV output.
- The displayed keyboard shortcuts control playback, frame stepping, and current-frame ROI dimensions as specified; arrow seeks pause playback and clamp at the video boundaries.
- A first drag creates the initial keypoint. Later point clicks inherit dimensions from the preceding keypoint; later drags redefine both size and center. Re-editing a frame updates its existing entry.
- The ROI overlay, numeric controls, and keypoint list stay consistent, including when selecting an item from the list or scrubbing between keypoints.
- A selected keypoint can be modified through its four ROI values or deleted from the list; invalid edits preserve the last valid keypoint, and deletion is confirmed before removal.
- The user can import a keyframe CSV at startup with `--keyframes` or after launch with **Import Keyframes CSV**. A valid import replaces all current keyframes; an invalid or canceled GUI import leaves them unchanged.
- Saving creates the required folder and CSV schema at the settings-derived location, with frame-ordered keypoints and timestamps.
- Invalid inputs, out-of-frame ROI edits, missing initial keypoint dimensions, unreadable video, bad settings, and save errors produce understandable feedback without crashing or corrupting source files.
