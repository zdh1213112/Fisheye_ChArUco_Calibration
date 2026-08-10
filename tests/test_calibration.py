import unittest

import cv2
import numpy as np

from calibration.workflow import (
    BoardConfig,
    create_chessboard_object_points,
    create_charuco_board,
    create_undistort_maps,
    detect_calibration_board,
    detect_charuco,
    detect_chessboard,
    fisheye_focal_scale_for_balance,
)


class OpenCV412CompatibilityTests(unittest.TestCase):
    def test_required_opencv_aruco_api_is_available(self):
        version = tuple(int(part) for part in cv2.__version__.split(".")[:2])

        self.assertGreaterEqual(version, (4, 12))
        self.assertTrue(hasattr(cv2, "aruco"))
        self.assertTrue(hasattr(cv2.aruco, "CharucoBoard"))
        self.assertTrue(hasattr(cv2.aruco, "DetectorParameters"))
        procedural_api = all(
            hasattr(cv2.aruco, name)
            for name in (
                "detectMarkers",
                "interpolateCornersCharuco",
                "calibrateCameraCharuco",
            )
        )
        detector_api = all(
            hasattr(cv2.aruco, name)
            for name in ("ArucoDetector", "CharucoDetector")
        )
        self.assertTrue(procedural_api or detector_api)

    def test_generated_board_can_be_detected(self):
        config = BoardConfig()
        board = create_charuco_board(config)
        image = board.generateImage((700, 450), marginSize=20)

        detection = detect_charuco(image, config)

        self.assertGreater(detection.marker_count, 0)
        self.assertGreater(detection.corner_count, 0)

    def test_generated_traditional_chessboard_can_be_detected(self):
        inner_horizontal, inner_vertical = 13, 8
        square_pixels = 42
        board = np.full(
            (
                (inner_vertical + 1) * square_pixels,
                (inner_horizontal + 1) * square_pixels,
            ),
            255,
            dtype=np.uint8,
        )
        for row in range(inner_vertical + 1):
            for column in range(inner_horizontal + 1):
                if (row + column) % 2 == 0:
                    cv2.rectangle(
                        board,
                        (column * square_pixels, row * square_pixels),
                        ((column + 1) * square_pixels, (row + 1) * square_pixels),
                        0,
                        -1,
                    )
        image = cv2.copyMakeBorder(board, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        config = BoardConfig(
            pattern_type="chessboard",
            dictionary_name="NOT_USED_FOR_CHESSBOARD",
            squares_horizontal=inner_horizontal,
            squares_vertical=inner_vertical,
            square_length=0.020,
            marker_length=100.0,
        )

        detection = detect_chessboard(image, config)
        dispatched = detect_calibration_board(image, config)

        self.assertEqual(detection.marker_count, 0)
        self.assertEqual(detection.corner_count, inner_horizontal * inner_vertical)
        self.assertEqual(dispatched.corner_count, detection.corner_count)
        np.testing.assert_array_equal(
            detection.ids.reshape(-1),
            np.arange(inner_horizontal * inner_vertical),
        )

    def test_chessboard_object_points_use_inner_corner_count_and_square_size(self):
        config = BoardConfig(
            pattern_type="chessboard",
            squares_horizontal=4,
            squares_vertical=3,
            square_length=0.025,
        )

        points = create_chessboard_object_points(config)

        self.assertEqual(points.shape, (12, 3))
        np.testing.assert_allclose(points[0], [0.0, 0.0, 0.0])
        np.testing.assert_allclose(points[3], [0.075, 0.0, 0.0])
        np.testing.assert_allclose(points[4], [0.0, 0.025, 0.0])

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
        self.assertAlmostEqual(new_camera_matrix[0, 0], 320.0)
        self.assertAlmostEqual(new_camera_matrix[1, 1], 320.0)

        _, _, wide_camera_matrix = create_undistort_maps(
            calibration,
            (640, 480),
            balance=1.0,
        )
        self.assertAlmostEqual(wide_camera_matrix[0, 0], 224.0)
        self.assertAlmostEqual(wide_camera_matrix[1, 1], 224.0)

    def test_balance_blends_from_natural_to_wide_projection(self):
        self.assertAlmostEqual(fisheye_focal_scale_for_balance(0.0), 1.0)
        self.assertAlmostEqual(fisheye_focal_scale_for_balance(0.5), 0.85)
        self.assertAlmostEqual(fisheye_focal_scale_for_balance(1.0), 0.70)
        self.assertAlmostEqual(fisheye_focal_scale_for_balance(-1.0), 1.0)
        self.assertAlmostEqual(fisheye_focal_scale_for_balance(2.0), 0.70)


if __name__ == "__main__":
    unittest.main()
