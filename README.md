# Fisheye Camera ChArUco / Chessboard Calibration

[中文说明](README_zh-CN.md)

This project provides a cross-platform desktop workflow for calibrating USB/UVC cameras on Windows and Linux. It supports ChArUco and traditional chessboards, fisheye and pinhole models, live undistortion, recoverable calibration batches, and batch image correction.

![ChArUco detection example](docs/README_images/detected_markers.png)

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Windows and Linux camera support](#windows-and-linux-camera-support)
- [GUI calibration workflow](#gui-calibration-workflow)
- [Data and output layout](#data-and-output-layout)
- [Batch undistortion](#batch-undistortion)
- [Python API](#python-api)
- [Calibration quality](#calibration-quality)
- [Legacy data migration](#legacy-data-migration)
- [Troubleshooting](#troubleshooting)
- [Development and tests](#development-and-tests)

## Features

- Discovers DirectShow camera names and numeric indices on Windows.
- Enumerates `/dev/video*` and reads V4L2 names from sysfs on Linux.
- Falls back through DirectShow, Media Foundation, and OpenCV automatic capture on Windows.
- Falls back from V4L2 to OpenCV automatic capture on Linux.
- Supports ChArUco and traditional black-and-white chessboards.
- Supports OpenCV fisheye and pinhole calibration models.
- Validates detected corners before saving a calibration image.
- Separates images, parameters, and detection results by actual resolution.
- Saves annotated detections and automatically rejects strong reprojection-error outliers.
- Shows the original and undistorted camera streams side by side.
- Saves corrected images, valid ROIs, side-by-side comparisons, and multi-balance comparisons.
- Archives the active image set and existing calibration files before starting a clean batch.
- Batch-undistorts existing images from the command line.

## Requirements

| Item | Requirement |
| --- | --- |
| Operating system | Windows 10/11, or a Linux distribution with V4L2 |
| Python | 3.10, 3.11, or 3.12; Python 3.10 is recommended |
| Camera | A USB/UVC or system video device accessible to OpenCV |
| OpenCV | `opencv-contrib-python==4.12.0.88` |
| NumPy | `numpy==2.2.6` |
| GUI | `PySide6-Essentials==6.7.2` |

Run project commands from the repository root and make sure the current user can write to `data/`.

## Installation

### Option 1: Miniforge (recommended)

Miniforge provides the same Python and environment-management workflow on Windows and Linux. Current Miniforge installers provide both `conda` and `mamba`; the separate Mambaforge distribution is deprecated. Mamba is preferred because it performs parallel downloads and faster dependency solving.

1. Install the appropriate package from the [official Miniforge repository](https://github.com/conda-forge/miniforge).
2. Open Miniforge Prompt on Windows or a terminal on Linux.
3. Enter the project root and run:

```bash
mamba env create -f environment.yml
conda activate fisheye-charuco
```

If `mamba` is unavailable in the current terminal, use the compatible Conda command:

```bash
conda env create -f environment.yml
conda activate fisheye-charuco
```

The environment uses Python 3.10 and installs this repository in editable mode. Platform markers in `requirements.txt` are applied automatically:

- Windows installs `pygrabber` for friendly DirectShow names.
- Linux skips `pygrabber`.
- Windows skips POSIX-only packages such as `pexpect` and `ptyprocess`.

Mamba accelerates Conda repository access, package downloads, and dependency solving. This project then uses pip to install packages such as OpenCV and PySide6, so total setup time can still depend heavily on PyPI network performance.

Users in mainland China can temporarily select a pip mirror before creating the environment. Windows PowerShell:

```powershell
$env:PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
mamba env create -f environment.yml
```

Linux:

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
mamba env create -f environment.yml
```

Remove `PIP_INDEX_URL` and retry with the official PyPI service if the mirror is unavailable or does not contain the required version.

Launch without activating the environment:

```bash
conda run -n fisheye-charuco python scripts/calibration_gui.py
```

Update an existing environment:

```bash
mamba env update -n fisheye-charuco -f environment.yml --prune
```

Remove the environment:

```bash
conda env remove -n fisheye-charuco
```

### Option 2: Python venv

Windows PowerShell:

```powershell
py -3.10 -m venv myenv
myenv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

If PowerShell blocks activation scripts, use Command Prompt:

```bat
myenv\Scripts\activate.bat
```

Linux:

```bash
python3 -m venv myenv
source myenv/bin/activate
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

Install optional development dependencies:

```bash
python -m pip install -e ".[dev]"
```

### Verify the environment

```bash
python -c "import cv2, numpy, PySide6; print(cv2.__version__, numpy.__version__)"
```

## Quick start

Activate the environment and run from the repository root:

```bash
python scripts/calibration_gui.py
```

Required `data/calibration/` and `data/realtime_captures/` directories are created automatically.

Default GUI settings:

| Setting | Default |
| --- | --- |
| Resolution | `640 x 480` |
| Frame rate | `30 FPS` |
| Camera model | Fisheye |
| Board | ChArUco |
| ArUco dictionary | `DICT_5X5_100` |
| Board size | X=`14`, Y=`9` squares |
| Square length | `20 mm` |
| Marker length | `15 mm` |
| Balance | `0.00` |
| Edge compression | `0.00` |

## Windows and Linux camera support

| Platform | Discovery | Capture fallback order | Manual source |
| --- | --- | --- | --- |
| Windows | DirectShow names, then numeric-index probing | DirectShow → Media Foundation → OpenCV automatic | `0`, `1` |
| Linux | `/dev/video*` plus names from `/sys/class/video4linux` | V4L2 → OpenCV automatic | `/dev/video0` |

The camera selector remains editable. Windows typically displays `1: USB Camera`; Linux displays `/dev/video0: USB Camera` when a sysfs name is available.

After a camera opens, the log reports the selected backend, actual resolution, actual FPS, and FOURCC. Camera drivers may not accept every requested value, so use the reported format when diagnosing a mismatch.

## GUI calibration workflow

### 1. Select the camera format

1. Refresh the device list and select the target camera.
2. Select a resolution and frame rate.
3. Open the camera.
4. Confirm the actual format in the log.

Use the highest practical format that the camera can sustain. If `1920 x 1080 @ 60 FPS` fails, start with `640 x 480 @ 30 FPS`.

### 2. Configure the board

| Board | Meaning of horizontal/vertical values | Other values |
| --- | --- | --- |
| ChArUco | Number of squares | ArUco dictionary, square length, marker length |
| Traditional chessboard | Number of inner corners, not squares | Square length |

The default ChArUco board is X=`14`, Y=`9`, with 20 mm squares and 15 mm markers. Rotating the physical board does not swap X and Y.

For a traditional board containing `14 x 9` black-and-white squares, enter `13 x 8` inner corners.

### 3. Capture calibration images

1. Click the capture button or press Space.
2. Images with too few detected corners are not saved.
3. Capture 15–25 images covering the center, edges, and corners of the frame.
4. Include different distances, tilts, and rotations.

Images are separated by actual resolution and board type:

```text
data/calibration/images/<width>x<height>/                 # ChArUco
data/calibration/images/<width>x<height>/chessboard/      # chessboard
```

### 4. Calculate calibration parameters

Select the fisheye or pinhole model and start calibration. The calculation runs in a worker thread while the camera preview remains responsive.

The GUI displays the intrinsic matrix `K`, distortion coefficients `D`, RMS error, per-image errors, and COLMAP parameters. Output examples:

```text
data/calibration/camera_intrinsics/1920x1080/fisheye_calibration.json
data/calibration/camera_intrinsics/1920x1080/pinhole_calibration.json
data/calibration/camera_intrinsics/1920x1080/fisheye_chessboard_calibration.json
data/calibration/camera_intrinsics/1920x1080/pinhole_chessboard_calibration.json
```

Annotated detections are written to:

```text
data/calibration/detected_images/1920x1080/<timestamp>_<board>_<model>/
```

Each image is marked as `USED`, `OUTLIER`, or `SKIPPED`.

### 5. Live undistortion

Start live correction to display the original stream on the left and the corrected stream on the right.

- `balance=0.00` uses the calibrated focal length for a more natural view.
- `balance=1.00` expands the field of view but may increase stretching and black borders.
- `edge compression=0.00` uses the standard OpenCV projection.
- Higher edge compression reduces peripheral stretching but changes the projection and should be treated as experimental.

Start with both values at `0.00` before tuning the view.

### 6. Save correction comparisons

The save action creates:

```text
data/realtime_captures/<resolution>/<timestamp>/
├── original.jpg
├── corrected_balance_0.00.jpg
├── corrected_full.jpg
├── corrected_full_with_roi_balance_0.00.jpg
├── original_vs_corrected_0.00.jpg
├── balance_comparison.jpg
├── cropped_balance_comparison.jpg
└── metadata.json
```

### 7. Archive and start a new batch

Use the archive action before capturing a clean image set at the same resolution. Active images move to:

```text
data/calibration/image_archives/<width>x<height>/<board>/<timestamp>/
```

Existing fisheye and pinhole parameter files are copied into the archive's `camera_intrinsics/` directory. The active calibration state is cleared after a successful archive. Images are not deleted, and a failed move is rolled back when possible.

## Data and output layout

```text
Fisheye_ChArUco_Calibration/
├── environment.yml
├── requirements.txt
├── scripts/
│   ├── calibration_gui.py
│   ├── undistort_images.py
│   ├── run_calibration.py
│   └── generate_virtual_cameras.py
├── src/
│   ├── calibration/
│   └── virtual_camera/
├── tests/
└── data/
    ├── calibration/
    │   ├── images/
    │   ├── image_archives/
    │   ├── camera_intrinsics/
    │   └── detected_images/
    ├── realtime_captures/
    ├── raw_images/
    ├── undistorted_images/
    └── virtual_cameras/
```

A calibration file is valid only for its matching resolution and camera imaging mode. Recalibrate after changing resolution, crop mode, or a driver mode that changes the image geometry.

## Batch undistortion

Use `scripts/undistort_images.py` for batch processing:

```bash
python scripts/undistort_images.py --help
```

Run with the default 640x480 fisheye paths:

```bash
python scripts/undistort_images.py
```

Custom example:

```bash
python scripts/undistort_images.py \
  --input-dir data/raw_images/descent_1 \
  --calibration data/calibration/camera_intrinsics/1920x1080/fisheye_calibration.json \
  --output-dir data/undistorted_images/1920x1080 \
  --balance 0.2
```

In Windows PowerShell, replace each `\` continuation with a backtick or put the command on one line.

| Option | Description |
| --- | --- |
| `--input-dir` | Input image directory |
| `--calibration` | Calibration JSON file |
| `--output-dir` | Output directory |
| `--balance` | Field-of-view setting from `0` to `1` |
| `--edge-compression` | Peripheral compression from `0` to `1` |
| `--keep-crop-size` | Keep the safe ROI size instead of resizing to the input resolution |
| `--no-comparisons` | Do not create original/corrected comparison images |

The output includes corrected images, an optional `comparisons/` directory, and `metadata.json`.

`scripts/run_calibration.py` is a legacy API example. Its default path reads only files directly inside `data/calibration/images/` and does not recurse into resolution directories. Prefer the GUI. If the script is required, change its image path to a specific directory such as `data/calibration/images/1920x1080/`.

## Python API

The current workflow can be called directly:

```python
from pathlib import Path

from calibration import BoardConfig, calibrate_from_directory

config = BoardConfig(
    pattern_type="charuco",
    dictionary_name="DICT_5X5_100",
    squares_horizontal=14,
    squares_vertical=9,
    square_length=0.020,
    marker_length=0.015,
)

result = calibrate_from_directory(
    images_dir=Path("data/calibration/images/1920x1080"),
    config=config,
    model="fisheye",
    output_path=Path(
        "data/calibration/camera_intrinsics/1920x1080/fisheye_calibration.json"
    ),
)
```

The package also exports `CharucoCalibrator`, `FisheyeCalibrator`, and `PinholeCalibrator`. The `virtual_camera` package provides `RectangularCamera` and `ConcentricCamera` helpers.

## Calibration quality

- Use sharp, evenly exposed images without motion blur.
- Cover the frame center, edges, and corners instead of repeating one pose.
- Include different distances, rotations, and board tilts.
- Use the same resolution and imaging mode for calibration and correction.
- For fisheye lenses, place the board near the field-of-view boundary in several images.
- A traditional chessboard requires the complete inner-corner area; ChArUco tolerates partial occlusion better.

With at least 10 valid images, the workflow uses median/MAD statistics to identify strong per-image reprojection-error outliers and runs a second calibration pass. At most 20% of the images are rejected, and at least five valid images are retained. Original images are never modified.

## Legacy data migration

When the GUI starts:

- Images stored directly in `data/calibration/images/` are moved into resolution directories based on their actual dimensions.
- Legacy root-level calibration JSON files are copied into the matching resolution directory under `camera_intrinsics/`.
- Archive data is not deleted.

## Troubleshooting

### Windows camera is missing or cannot open

- Enable camera and desktop-app access under **Settings → Privacy & security → Camera**.
- Close browsers, meeting clients, and the Windows Camera application.
- Refresh devices after reconnecting the USB camera; its numeric index may change.
- `pygrabber` is used only for names. Numeric probing still works if friendly-name enumeration fails.
- Start with `640 x 480 @ 30 FPS` before requesting a higher format.

### Linux camera is missing or cannot open

```bash
ls -l /dev/video*
```

- Confirm that a `/dev/video*` node exists.
- Some distributions require the current user to belong to the `video` group; sign in again after changing group membership.
- If `v4l-utils` is installed, run `v4l2-ctl --list-devices` to inspect device mappings.
- Close other applications that may hold the V4L2 device.
- Enter `/dev/video0` manually when discovery is unavailable.

### Camera opens but no frames arrive

- Lower the resolution or FPS.
- Check the actual FOURCC in the application log; MJPG may not be supported.
- Try another USB port and avoid bandwidth-limited hubs.
- Ensure that another process is not using the device exclusively.

### Board is visible but no corners are detected

- Verify the dictionary, board dimensions, square length, and marker length.
- The default board is X=`14`, Y=`9`; do not swap the values based on how the board is physically rotated.
- For a traditional chessboard, enter inner-corner counts rather than square counts.
- Avoid cropped board edges, severe reflections, and motion blur.

### Calibration succeeds but correction looks wrong

- Match the calibration resolution to the actual camera resolution.
- Reset balance and edge compression to `0.00`.
- Inspect annotated detections for many `SKIPPED` images or incorrect corners.
- Capture a new batch with better edge coverage.

## Development and tests

Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

Linux:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

With development dependencies installed:

```bash
python -m pytest -q
```

Tests cover OpenCV 4.12 compatibility, ChArUco/chessboard detection, fisheye maps, batch archiving, Windows/Linux camera discovery and fallback behavior, and virtual-camera image output.

## License

This project is distributed under the [MIT License](licence.txt).
