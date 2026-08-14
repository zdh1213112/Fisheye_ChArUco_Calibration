import unittest
from unittest.mock import patch

import cv2

from calibration.camera_devices import (
    CameraDevice,
    capture_backend_candidates,
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
            self.assertEqual(backend, cv2.CAP_DSHOW)
            capture = FakeCapture(index in (1, 3))
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

    def test_platform_backend_order(self):
        self.assertEqual(capture_backend_candidates("win32")[0][0], cv2.CAP_DSHOW)
        self.assertEqual(capture_backend_candidates("linux")[0][0], cv2.CAP_V4L2)

    def test_fourcc_name(self):
        code = cv2.VideoWriter_fourcc(*"MJPG")
        self.assertEqual(fourcc_name(code), "MJPG")


if __name__ == "__main__":
    unittest.main()
