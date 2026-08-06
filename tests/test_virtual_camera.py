import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from virtual_camera.virtual_camera import VirtualCamera


class VirtualCameraOpenCVCompatibilityTests(unittest.TestCase):
    def test_circular_mask_uses_opencv_412(self):
        mask = VirtualCamera.circular_mask((10, 10), 5, 20, 20)

        self.assertEqual(mask.shape, (20, 20))
        self.assertEqual(mask.dtype, np.uint8)
        self.assertEqual(int(mask[10, 10]), 255)
        self.assertEqual(int(mask[0, 0]), 0)

    def test_saved_image_can_be_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.png"
            expected = np.full((12, 16, 3), 127, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(image_path), expected))

            actual = VirtualCamera.load_image(str(image_path))

        np.testing.assert_array_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
