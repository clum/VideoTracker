# OpenCV Object Tracking Implementation Guide

## Goal

When asked to implement the typical OpenCV tracking workflow in this folder, build a small runnable prototype that tracks one user-selected object in a single user-supplied MP4 clip. The first version is a manual, single-object tracker; it does not need example images, a training set, or an automatic object detector.

## Expected workflow

1. Accept the path to one MP4 file as input. Do not hard-code a clip name or assume the input is one of the clips currently in this folder. Also accept an optional path to a one-row ROI CSV; when supplied, use it instead of prompting for a manual selection.
2. Open the video with `cv2.VideoCapture`, check that it opened, and read the first usable frame.
3. If an ROI CSV was supplied, load its `x`, `y`, `width`, and `height` values. Validate the file, its single data row, finite whole-pixel coordinates, and that the region fits within the first frame. Otherwise, let the user select a bounding box in that frame, for example with `cv2.selectROI`. If the selection is canceled or has zero area, stop with a clear message.
4. Initialize one OpenCV tracker from the selected frame and bounding box. Prefer CSRT for a useful general-purpose starting point when that API is available. Check the installed OpenCV build for the tracker API; CSRT may require the contrib package, and Python API names can vary by OpenCV version. If it is unavailable, report the needed dependency or offer a documented supported alternative rather than failing with an obscure attribute error.
5. Process subsequent frames in order, call the tracker update for each frame, and show or write the resulting bounding box when tracking succeeds. Treat a failed update as a lost target: record the failure, do not invent coordinates, and explain that the user may need to restart or reselect the target.
6. Release the video capture and writer and close any OpenCV windows on normal completion and on handled errors.

## Inputs and outputs

- Input: one MP4 path supplied by the user, such as a command-line argument. An optional `--roi-csv` argument supplies a previously saved ROI and bypasses interactive selection. Validate both paths and report clear errors for malformed ROI data or unreadable files.
- Outputs: place generated files in a separate output folder next to the input video (for example, `tracked_output/`), never overwrite or modify the source clip.
- ROI image: save an unannotated crop of the initially selected region from the first frame as `<stem>_roi.png` in the output folder. This is an additional output alongside the tracking CSV and preview.
- ROI coordinates: save the initial selection's `x`, `y`, `width`, `height`, `center_x`, and `center_y` as one row in `<stem>_roi.csv`. Coordinates are in pixels from the frame's top-left origin: `x` increases rightward and `y` increases downward. `x` and `y` identify the ROI's top-left corner; center coordinates identify its center.
- CSV: produce one row for each processed frame, with frame index, timestamp in seconds, tracking status, bounding-box `x`, `y`, `width`, `height`, and center `center_x`, `center_y`. Use blank coordinate fields for frames where the tracker reports failure. Derive timestamps from the video's FPS when it is valid; otherwise leave timestamps blank or use a documented fallback.
- Preview: write an annotated MP4 showing the frame index and current box on successful frames, and a visible lost-target indicator on failed frames. Initialize the writer using the source frame dimensions and a valid source FPS, with a clear error if a preview cannot be created.
- Use output names based on the input file stem, such as `<stem>_tracked.csv` and `<stem>_tracked.mp4`, to avoid collisions with the source.

## Implementation guidance

- Keep the first implementation focused on one target and one video. Avoid adding person/ball models, model downloads, dataset preparation, multi-object identity assignment, or a large UI unless the user asks for them.
- Keep video reading, ROI selection, tracking, and output writing easy to follow and maintain. Make resource cleanup reliable even when an exception or early exit occurs.
- Include concise run instructions and the required OpenCV package/version guidance with the implementation. Explain that GUI-based ROI selection requires a desktop-capable OpenCV build; using `--roi-csv` should not open an ROI selection window.
- Tracking is not guaranteed to remain correct through occlusion, rapid motion, appearance changes, or similar-looking objects. Make the preview and CSV useful for reviewing drift and lost frames; do not silently claim a failed track is valid.
- Automatic detection can be added later: a detector searches frames for known classes or trained objects, while this prototype follows the target selected in its first frame. Example images are relevant only if a later detector needs custom training or fine-tuning.

## Completion criteria

- A user can run the prototype with a single supplied MP4 path and select one valid initial ROI.
- The program saves the selected first-frame ROI crop and its coordinate CSV, writes a CSV record for every processed frame, and writes an annotated preview in the separate output folder.
- Invalid/unreadable input, malformed/out-of-bounds ROI CSV, canceled/invalid manual ROI, unavailable tracker, invalid FPS/output setup, and tracking loss are handled with understandable feedback and without corrupting the source video.
- The run instructions state dependencies and how to choose another input clip.
