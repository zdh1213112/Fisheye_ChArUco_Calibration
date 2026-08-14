import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2

from calibration.camera_devices import (
    CameraDevice,
    camera_access_hint,
    capture_backend_candidates,
    default_camera_source,
    fourcc_name,
    list_camera_devices,
    normalize_camera_source,
    open_camera_capture,
)


class FakeCapture:
    def __init__(self, opened):
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def release(self):
        self.released = True


class CameraDeviceTests(unittest.TestCase):
    def test_normalize_camera_source_accepts_index_label_and_path(self):
        self.assertEqual(normalize_camera_source("2"), 2)
        self.assertEqual(normalize_camera_source("2: USB Camera"), 2)
        self.assertEqual(normalize_camera_source("/dev/video2"), "/dev/video2")
        self.assertEqual(
            normalize_camera_source("/dev/video2: USB Fisheye"),
            "/dev/video2",
        )
        self.assertEqual(
            normalize_camera_source("rtsp://127.0.0.1/camera"),
            "rtsp://127.0.0.1/camera",
        )

    def test_windows_devices_use_directshow_names_and_indices(self):
        with patch(
            "calibration.camera_devices._windows_friendly_names",
            return_value=["USB Fisheye", "Integrated Camera"],
        ):
            devices = list_camera_devices(platform="win32")

        self.assertEqual(
            devices,
            [CameraDevice(0, "USB Fisheye"), CameraDevice(1, "Integrated Camera")],
        )
        self.assertEqual(devices[0].label, "0: USB Fisheye")

    def test_windows_discovery_probes_indices_when_names_are_unavailable(self):
        captures = []

        def capture_factory(index, backend):
            opened = (index == 1 and backend == cv2.CAP_DSHOW) or (
                index == 3 and backend == cv2.CAP_MSMF
            )
            capture = FakeCapture(opened)
            captures.append(capture)
            return capture

        with patch(
            "calibration.camera_devices._windows_friendly_names", return_value=[]
        ):
            devices = list_camera_devices(
                platform="win32", max_index=4, capture_factory=capture_factory
            )

        self.assertEqual([device.source for device in devices], [1, 3])
        self.assertTrue(all(capture.released for capture in captures))

    def test_linux_devices_use_v4l2_paths_and_sysfs_names(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            device_root = root / "dev"
            sysfs_root = root / "sys" / "class" / "video4linux"
            device_root.mkdir(parents=True)
            (device_root / "video10").touch()
            (device_root / "video2").touch()
            (device_root / "not-a-camera").touch()
            (sysfs_root / "video2").mkdir(parents=True)
            (sysfs_root / "video2" / "name").write_text(
                "USB Fisheye\n",
                encoding="utf-8",
            )

            devices = list_camera_devices(
                platform="linux",
                device_root=device_root,
                sysfs_root=sysfs_root,
            )

        self.assertEqual(
            [Path(device.source).name for device in devices],
            ["video2", "video10"],
        )
        self.assertEqual(devices[0].name, "USB Fisheye")
        self.assertTrue(devices[0].label.endswith(": USB Fisheye"))
        self.assertEqual(devices[1].name, str(device_root / "video10"))

    def test_windows_capture_falls_back_from_directshow_to_media_foundation(self):
        captures = []

        def capture_factory(source, backend):
            self.assertEqual(source, 1)
            capture = FakeCapture(backend == cv2.CAP_MSMF)
            captures.append((backend, capture))
            return capture

        capture, backend_name, attempted = open_camera_capture(
            "1: USB Camera", platform="win32", capture_factory=capture_factory
        )

        self.assertIs(capture, captures[1][1])
        self.assertTrue(captures[0][1].released)
        self.assertFalse(captures[1][1].released)
        self.assertEqual(backend_name, "Media Foundation")
        self.assertEqual(attempted, ["DirectShow", "Media Foundation"])

    def test_linux_capture_falls_back_from_v4l2_to_opencv_auto(self):
        captures = []

        def capture_factory(source, backend):
            self.assertEqual(source, "/dev/video4")
            capture = FakeCapture(backend == cv2.CAP_ANY)
            captures.append((backend, capture))
            return capture

        capture, backend_name, attempted = open_camera_capture(
            "/dev/video4",
            platform="linux",
            capture_factory=capture_factory,
        )

        self.assertIs(capture, captures[1][1])
        self.assertTrue(captures[0][1].released)
        self.assertEqual(backend_name, "OpenCV 自动")
        self.assertEqual(attempted, ["V4L2", "OpenCV 自动"])

    def test_platform_backend_order(self):
        self.assertEqual(capture_backend_candidates("win32")[0][0], cv2.CAP_DSHOW)
        self.assertEqual(capture_backend_candidates("linux")[0][0], cv2.CAP_V4L2)

    def test_unavailable_backend_alias_is_not_mislabeled(self):
        with patch.object(cv2, "CAP_DSHOW", cv2.CAP_ANY):
            candidates = capture_backend_candidates("win32")

        self.assertNotIn((cv2.CAP_ANY, "DirectShow"), candidates)
        self.assertEqual(candidates[-1], (cv2.CAP_ANY, "OpenCV 自动"))

    def test_platform_defaults_and_access_hints(self):
        self.assertEqual(default_camera_source("win32"), 0)
        self.assertEqual(default_camera_source("linux"), "/dev/video0")
        self.assertIn("Windows", camera_access_hint("win32"))
        self.assertIn("/dev/video", camera_access_hint("linux"))

    def test_fourcc_name(self):
        code = cv2.VideoWriter_fourcc(*"MJPG")
        self.assertEqual(fourcc_name(code), "MJPG")
        self.assertEqual(fourcc_name(0), "")
        self.assertEqual(fourcc_name(float("nan")), "")


if __name__ == "__main__":
    unittest.main()
