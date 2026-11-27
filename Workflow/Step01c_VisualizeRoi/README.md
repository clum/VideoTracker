# Step01c Visualize ROI

This tool draws the per-frame ROI from a Step01b CSV onto the matching source-video frame and saves an annotated MP4. It preserves source dimensions, FPS, frame order, and audio when the source has an audio stream.

## Environment

From the repository root, create or update the shared Conda environment and activate it:

```powershell
conda env create -f environment.yml
conda activate video-tracking
```

If the environment already exists, update it with `conda env update -n video-tracking -f environment.yml`.

## Run

From the repository root, pass the settings, Step01b CSV, and original video:

```powershell
python .\Workflow\Step01c_VisualizeRoi\visualize_roi.py `
  --settings "D:\path\to\settings.json" `
  --keyframes "D:\path\to\Step01b_interpolated_keyframes.csv" `
  --video "D:\path\to\source_video.mp4"
```

Any omitted option opens a file dialog. The settings JSON must contain non-empty `ResultsDirectory` and `ScenarioName` values; relative result paths are resolved against the settings file's directory.

## Output

The tool writes `<ScenarioName>_visualize_roi.mp4` under `<ResultsDirectory>\Step01c_VisualizeRoi`. The Step01b CSV must contain exactly one valid ROI row for each source-video frame, using columns `frame_index,timestamp_seconds,width,height,center_x,center_y`.

The ROI is a green 2-pixel rectangle in source-video pixel coordinates. Videos with audio keep their source audio via FFmpeg; videos without audio produce a silent annotated video. Inputs are not modified.
