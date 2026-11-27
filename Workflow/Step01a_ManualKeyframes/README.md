# Step01a Manual Keyframes

This desktop app lets you create manual ROI keyframes for one video. It uses OpenCV for video decoding and PySide6 for the GUI. It records authored keyframes only; it does not track the object or interpolate values between keypoints.

## Environment

From the repository root, create the shared Conda environment (or update an existing one):

```powershell
conda env create -f environment.yml
conda activate video-tracking
```

If `video-tracking` already exists, apply changes in `environment.yml` with:

```powershell
conda env update -n video-tracking -f environment.yml
```

## Run

From the repository root, pass both paths:

```powershell
python .\Workflow\Step01a_ManualKeyframes\manual_keyframes.py `
  --video "D:\path\to\clip.mp4" `
  --settings "D:\path\to\settings.json"
```

Either option may be omitted; the app prompts for the missing path. The settings JSON must have a non-empty `ResultsDirectory` entry. Relative result paths are resolved from the settings file's directory.

## Define keyframes

- Scrub to a frame. Drag a box to create a keypoint with its center and dimensions.
- The first keypoint must be created by dragging a box. A click on a later frame creates a keypoint at that center using the dimensions of the nearest preceding keypoint.
- Clicking an existing keypoint changes its center and keeps its current size. A new box drag replaces that frame's keypoint with the new dimensions and center.
- Select a keypoint in the list to seek to it. Numeric controls edit the selected frame's ROI. ROI values use source-video pixels, with `(0, 0)` at the top-left, x increasing right and y increasing down.
- Use the **Playback speed** menu to choose 0.25×, 0.5×, 0.75×, 1×, 1.5×, or 2× playback. The default is 1×. Changing speed during playback takes effect immediately; changing it while paused leaves playback paused. This changes only how quickly frames are displayed and does not change frame indices, timestamps, keyframes, or CSV output.
- The overlay appears only on frames with stored keypoints; intermediate frames are not interpolated.

Click **Save Keyframes CSV** to write `<video-stem>_manual_keyframes.csv` under `<ResultsDirectory>\Step01a_ManualKeyframes`. The CSV columns are `frame_index,timestamp_seconds,width,height,center_x,center_y`.
