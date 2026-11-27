# Step01b Interpolate Keyframes

This tool converts sparse Step01a ROI keyframes into one CSV row per source-video frame. It interpolates ROI size and center linearly between keyframes, holding the nearest keyframe's values before the first and after the last keyframe.

## Environment

From the repository root, create or update the shared environment and activate it:

```powershell
conda env create -f environment.yml
conda activate video-tracking
```

If the environment already exists, use `conda env update -n video-tracking -f environment.yml` instead.

## Run

From the repository root, pass paths for settings, Step01a keyframes, and source video:

```powershell
python .\Workflow\Step01b_InterpolateKeyframes\interpolate_keyframes.py `
  --settings "D:\path\to\settings.json" `
  --keyframes "D:\path\to\Step01a_manual_keyframes.csv" `
  --video "D:\path\to\source_video.mp4"
```

Any omitted option opens a file dialog for that input. Settings JSON must contain non-empty `ResultsDirectory` and `ScenarioName` values; relative result paths are resolved from the settings file's directory.

## Output

The tool writes `<ScenarioName>_interpolated_keyframes.csv` to `<ResultsDirectory>\Step01b_InterpolateKeyframes`. For the example scenario `26_11_15_RootsGame3`, the output filename is `26_11_15_RootsGame3_interpolated_keyframes.csv`. Its columns are `frame_index,timestamp_seconds,width,height,center_x,center_y`. Frame indices are zero-based, timestamps are calculated from source FPS, and ROI coordinates are pixels from the top-left origin.

Each frame receives exactly one row. Frames between keyframes use linear interpolation for width, height, center_x, and center_y. Frames before or after the keyed range use the nearest endpoint values. A single keyframe is held for every frame.
