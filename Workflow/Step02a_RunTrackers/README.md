# Step02a Run Trackers

This step tracks one object selected manually in the first frame. It uses an OpenCV tracker and writes the initial ROI image and coordinates plus a CSV result for every video frame. It does not create an annotated movie.

## Environment

From the repository root, create or update the shared Conda environment and activate it:

```powershell
conda env create -f environment.yml
conda activate video-tracking
```

If the environment already exists, update it with `conda env update -n video-tracking -f environment.yml`.

## Run

Choose one of `csrt`, `kcf`, `mil`, `boosting`, `medianflow`, `mosse`, `tld`, `dasiamrpn`, `nano`, or `vit`:

```powershell
python .\Workflow\Step02a_RunTrackers\run_trackers.py `
  --settings "D:\path\to\settings.json" `
  --video "D:\path\to\source_video.mp4" `
  --algorithm csrt
```

The `--settings` and `--video` options can be omitted; the app opens a file dialog for each omitted path. The settings JSON must contain non-empty `ResultsDirectory` and `ScenarioName` entries. A relative `ResultsDirectory` is resolved against the settings file's directory.

For the model-based algorithms, pass their required model files. For example, DaSiamRPN requires all three:

```powershell
python .\Workflow\Step02a_RunTrackers\run_trackers.py `
  --settings "D:\path\to\settings.json" `
  --video "D:\path\to\source_video.mp4" `
  --algorithm dasiamrpn `
  --siam-rpn-model "D:\models\siamrpn.onnx" `
  --kernel-cls1-model "D:\models\kernel_cls1.onnx" `
  --kernel-r1-model "D:\models\kernel_r1.onnx"
```

Nano requires `--nano-backbone` and `--nano-neckhead`; ViT requires `--vit-model`. Model files are not downloaded by this tool. Each selected DNN tracker requires its model files; model options for other algorithms are not needed.

In the ROI window, drag a box around the object and press **Enter** or **Space** to accept it. Press **C** to cancel. The selector requires a desktop session and GUI-enabled OpenCV.

## Output

The tool writes to `<ResultsDirectory>\Step02a_RunTrackers`:

- `<ScenarioName>_<algorithm>_roi.png`: unannotated crop of the selected region in the first frame.
- `<ScenarioName>_<algorithm>_roi.csv`: one row with `x,y,width,height,center_x,center_y`.
- `<ScenarioName>_<algorithm>_tracked.csv`: one row per video frame with `frame_index,timestamp_seconds,tracking_status,x,y,width,height,center_x,center_y`.

Coordinates are in source-video pixels from the top-left origin, with x increasing to the right and y increasing downward. Frame indices start at zero and timestamps are computed as `frame_index / fps`. The first frame is recorded as `tracked` with the selected ROI. If a tracker update fails, its row is marked `lost` with blank coordinate fields; processing continues for later frames.

The selected tracker must be available in the active OpenCV build. The tool checks current and legacy OpenCV API locations and reports an error if the requested tracker is unavailable; it never silently changes algorithms. Existing outputs with the same scenario and algorithm names are replaced. The source video and settings file are not modified.
