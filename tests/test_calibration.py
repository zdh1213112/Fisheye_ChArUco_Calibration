import unittest

import cv2
import numpy as np

from calibration.workflow import (
    BoardConfig,
    create_charuco_board,
    create_undistort_maps,
    detect_charuco,
)


class OpenCV412CompatibilityTests(unittest.TestCase):
    def test_required_opencv_aruco_api_is_available(self):
        version = tuple(int(part) for part in cv2.__version__.split(".")[:2])

        self.assertGreaterEqual(version, (4, 12))
        self.assertTrue(hasattr(cv2, "aruco"))
        self.assertTrue(hasattr(cv2.aruco, "CharucoBoard"))
        self.assertTrue(hasattr(cv2.aruco, "DetectorParameters"))
        self.assertTrue(hasattr(cv2.aruco, "detectMarkers"))
        self.assertTrue(hasattr(cv2.aruco, "interpolateCornersCharuco"))
        self.assertTrue(hasattr(cv2.aruco, "calibrateCameraCharuco"))

    def test_generated_board_can_be_detected(self):
        config = BoardConfig()
        board = create_charuco_board(config)
        image = board.generateImage((700, 450), marginSize=20)

        detection = detect_charuco(image, config)

        self.assertGreater(detection.marker_count, 0)
        self.assertGreater(detection.corner_count, 0)

    def test_fisheye_undistort_maps_are_created(self):
        calibration = {
            "model": "fisheye",
            "image_size": [640, 480],
            "K": [
                [320.0, 0.0, 320.0],
                [0.0, 320.0, 240.0],
                [0.0, 0.0, 1.0],
            ],
            "D": [[0.0], [0.0], [0.0], [0.0]],
        }

        map_x, map_y, new_camera_matrix = create_undistort_maps(
            calibration,
            (640, 480),
            balance=0.0,
        )

        self.assertEqual(map_x.shape, (480, 640))
        self.assertEqual(map_y.shape, (480, 640))
        self.assertEqual(new_camera_matrix.shape, (3, 3))
        self.assertEqual(map_x.dtype, np.float32)
        self.assertEqual(map_y.dtype, np.float32)
        self.assertTrue(np.isfinite(map_x).all())
        self.assertTrue(np.isfinite(map_y).all())


if __name__ == "__main__":
    unittest.main()
