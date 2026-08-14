"""Cross-platform camera discovery and OpenCV capture helpers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union

import cv2


CameraSource = Union[int, str]
CaptureFactory = Callable[[CameraSource, int], object]


@dataclass(frozen=True)
class CameraDevice:
    """A camera source and the user-facing name shown by the GUI."""

    source: CameraSource
    name: str

    @property
    def label(self) -> str:
        if isinstance(self.source, int):
            return f"{self.source}: {self.name}"
        source = str(self.source)
        if self.name and self.name != source:
            return f"{source}: {self.name}"
        return source


def normalize_camera_source(value: CameraSource) -> CameraSource:
    """Convert an editable camera-index value to the type OpenCV expects."""

    if isinstance(value, int):
        return value

    text = value.strip()
    source_text, separator, _ = text.partition(":")
    source_text = source_text.strip()
    if source_text.isdigit():
        return int(source_text)
    if separator and source_text.startswith("/dev/video"):
        return source_text
    return text


def capture_backend_candidates(platform: str | None = None) -> list[tuple[int, str]]:
    """Return OpenCV capture backends in preferred order for the platform."""

    platform = (platform or sys.platform).lower()
    if platform == "win32":
        preferred_backends = [
            ("CAP_DSHOW", "DirectShow"),
            ("CAP_MSMF", "Media Foundation"),
        ]
    elif platform.startswith("linux"):
        preferred_backends = [
            ("CAP_V4L2", "V4L2"),
        ]
    elif platform == "darwin":
        preferred_backends = [
            ("CAP_AVFOUNDATION", "AVFoundation"),
        ]
    else:
        preferred_backends = []

    candidates: list[tuple[int, str]] = []
    for attribute, name in preferred_backends:
        backend = getattr(cv2, attribute, None)
        if backend is not None and backend != cv2.CAP_ANY:
            candidates.append((backend, name))
    candidates.append((cv2.CAP_ANY, "OpenCV 自动"))

    # Some OpenCV builds alias an unavailable platform backend to CAP_ANY.
    unique_candidates: list[tuple[int, str]] = []
    seen: set[int] = set()
    for backend, name in candidates:
        if backend not in seen:
            unique_candidates.append((backend, name))
            seen.add(backend)
    return unique_candidates


def default_camera_source(platform: str | None = None) -> CameraSource:
    """Return the editable camera source used when discovery finds nothing."""

    platform = (platform or sys.platform).lower()
    if platform.startswith("linux"):
        return "/dev/video0"
    return 0


def camera_access_hint(platform: str | None = None) -> str:
    """Return an actionable camera-open hint for the current operating system."""

    platform = (platform or sys.platform).lower()
    if platform == "win32":
        return "请关闭占用相机的其他程序，并检查 Windows 相机隐私权限。"
    if platform.startswith("linux"):
        return (
            "请关闭占用相机的其他程序，并检查 /dev/video* 访问权限"
            "（必要时将当前用户加入 video 组）。"
        )
    return "请关闭占用相机的其他程序，并检查系统相机权限。"


def _windows_friendly_names() -> list[str]:
    """Read DirectShow device names without opening and locking the cameras."""

    try:
        from pygrabber.dshow_graph import FilterGraph

        return list(FilterGraph().get_input_devices())
    except Exception:  # noqa: BLE001 - device enumeration must never break the GUI
        return []


def _release_capture(capture: object) -> None:
    """Release an OpenCV capture without interrupting fallback handling."""

    try:
        capture.release()
    except Exception:  # noqa: BLE001 - cleanup must not hide the next fallback
        pass


def _probe_camera_indices(
    platform: str,
    max_index: int,
    capture_factory: CaptureFactory,
) -> list[CameraDevice]:
    devices: list[CameraDevice] = []
    for index in range(max_index):
        capture, _, _ = open_camera_capture(index, platform, capture_factory)
        if capture is not None:
            devices.append(CameraDevice(index, f"相机 {index}"))
            _release_capture(capture)
    return devices


def _linux_video_device_paths(device_root: Path) -> list[Path]:
    """Return Linux V4L2 device paths in numeric index order."""

    return sorted(
        device_root.glob("video*"),
        key=lambda path: (
            int(path.name.removeprefix("video"))
            if path.name.removeprefix("video").isdigit()
            else sys.maxsize,
            path.name,
        ),
    )


def _linux_friendly_name(device_path: Path, sysfs_root: Path) -> str:
    """Read a Linux V4L2 device name from sysfs when it is available."""

    try:
        name = (sysfs_root / device_path.name / "name").read_text(
            encoding="utf-8"
        ).strip()
    except (OSError, UnicodeError):
        return str(device_path)
    return name or str(device_path)


def list_camera_devices(
    platform: str | None = None,
    max_index: int = 10,
    capture_factory: CaptureFactory = cv2.VideoCapture,
    device_root: Path | str = Path("/dev"),
    sysfs_root: Path | str = Path("/sys/class/video4linux"),
) -> list[CameraDevice]:
    """Discover cameras using native device enumeration where available."""

    platform = (platform or sys.platform).lower()
    if platform == "win32":
        names = _windows_friendly_names()
        if names:
            return [CameraDevice(index, name) for index, name in enumerate(names)]
        return _probe_camera_indices(platform, max_index, capture_factory)

    if platform.startswith("linux"):
        paths = _linux_video_device_paths(Path(device_root))
        sysfs_root = Path(sysfs_root)
        return [
            CameraDevice(str(path), _linux_friendly_name(path, sysfs_root))
            for path in paths
        ]

    return _probe_camera_indices(platform, max_index, capture_factory)


def open_camera_capture(
    source: CameraSource,
    platform: str | None = None,
    capture_factory: CaptureFactory = cv2.VideoCapture,
) -> tuple[object | None, str, list[str]]:
    """Open a camera using platform backends with deterministic fallbacks."""

    normalized_source = normalize_camera_source(source)
    attempted: list[str] = []
    for backend, backend_name in capture_backend_candidates(platform):
        attempted.append(backend_name)
        try:
            capture = capture_factory(normalized_source, backend)
        except Exception:  # noqa: BLE001 - try the next available OpenCV backend
            continue
        try:
            opened = capture.isOpened()
        except Exception:  # noqa: BLE001 - try the next available OpenCV backend
            _release_capture(capture)
            continue
        if opened:
            return capture, backend_name, attempted
        _release_capture(capture)
    return None, "", attempted


def fourcc_name(value: float) -> str:
    """Convert OpenCV's numeric FOURCC property into a readable code."""

    try:
        code = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if code <= 0:
        return ""
    name = "".join(chr((code >> (8 * offset)) & 0xFF) for offset in range(4))
    return "".join(character for character in name if character.isprintable()).strip()
