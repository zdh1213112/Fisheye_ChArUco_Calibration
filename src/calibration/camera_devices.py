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
        return self.name


def normalize_camera_source(value: CameraSource) -> CameraSource:
    """Convert an editable camera-index value to the type OpenCV expects."""

    if isinstance(value, int):
        return value

    text = value.strip()
    index_text = text.split(":", 1)[0].strip()
    if index_text.isdigit():
        return int(index_text)
    return text


def capture_backend_candidates(platform: str | None = None) -> list[tuple[int, str]]:
    """Return OpenCV capture backends in preferred order for the platform."""

    platform = platform or sys.platform
    if platform == "win32":
        candidates = [
            (getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY), "DirectShow"),
            (getattr(cv2, "CAP_MSMF", cv2.CAP_ANY), "Media Foundation"),
            (cv2.CAP_ANY, "自动"),
        ]
    elif platform.startswith("linux"):
        candidates = [
            (getattr(cv2, "CAP_V4L2", cv2.CAP_ANY), "V4L2"),
            (cv2.CAP_ANY, "自动"),
        ]
    elif platform == "darwin":
        candidates = [
            (getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY), "AVFoundation"),
            (cv2.CAP_ANY, "自动"),
        ]
    else:
        candidates = [(cv2.CAP_ANY, "自动")]

    # Some OpenCV builds alias an unavailable platform backend to CAP_ANY.
    unique_candidates: list[tuple[int, str]] = []
    seen: set[int] = set()
    for backend, name in candidates:
        if backend not in seen:
            unique_candidates.append((backend, name))
            seen.add(backend)
    return unique_candidates


def _windows_friendly_names() -> list[str]:
    """Read DirectShow device names without opening and locking the cameras."""

    try:
        from pygrabber.dshow_graph import FilterGraph

        return list(FilterGraph().get_input_devices())
    except Exception:  # noqa: BLE001 - device enumeration must never break the GUI
        return []


def _probe_camera_indices(
    platform: str,
    max_index: int,
    capture_factory: CaptureFactory,
) -> list[CameraDevice]:
    backend = capture_backend_candidates(platform)[0][0]
    devices: list[CameraDevice] = []
    for index in range(max_index):
        try:
            capture = capture_factory(index, backend)
            opened = capture.isOpened()
        except Exception:  # noqa: BLE001 - skip indices rejected by a driver
            continue
        try:
            if opened:
                devices.append(CameraDevice(index, f"相机 {index}"))
        finally:
            capture.release()
    return devices


def list_camera_devices(
    platform: str | None = None,
    max_index: int = 10,
    capture_factory: CaptureFactory = cv2.VideoCapture,
) -> list[CameraDevice]:
    """Discover cameras using native device enumeration where available."""

    platform = platform or sys.platform
    if platform == "win32":
        names = _windows_friendly_names()
        if names:
            return [CameraDevice(index, name) for index, name in enumerate(names)]
        return _probe_camera_indices(platform, max_index, capture_factory)

    if platform.startswith("linux"):
        paths = sorted(
            Path("/dev").glob("video*"),
            key=lambda path: int(path.name.removeprefix("video"))
            if path.name.removeprefix("video").isdigit()
            else max_index,
        )
        return [CameraDevice(str(path), str(path)) for path in paths]

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
            capture.release()
            continue
        if opened:
            return capture, backend_name, attempted
        capture.release()
    return None, "", attempted


def fourcc_name(value: float) -> str:
    """Convert OpenCV's numeric FOURCC property into a readable code."""

    code = int(value)
    return "".join(chr((code >> (8 * offset)) & 0xFF) for offset in range(4)).strip("\x00")
