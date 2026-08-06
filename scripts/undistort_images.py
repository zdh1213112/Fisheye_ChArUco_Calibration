"""Batch-undistort images with the same optimized projection used by the GUI."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from calibration import (  # noqa: E402
    create_balance_crop_roi,
    create_undistort_maps,
    create_undistort_valid_mask,
    fisheye_focal_scale_for_balance,
    load_calibration,
)


DEFAULT_RESOLUTION = "640x480"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "calibration" / "images" / DEFAULT_RESOLUTION
DEFAULT_CALIBRATION_PATH = (
    PROJECT_ROOT
    / "data"
    / "calibration"
    / "camera_intrinsics"
    / DEFAULT_RESOLUTION
    / "fisheye_calibration.json"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "undistorted_images"
    / DEFAULT_RESOLUTION
    / "natural_optimized"
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用与 GUI 相同的自然优化投影批量矫正鱼眼图片。"
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--balance",
        type=float,
        default=0.0,
        help="0 为参考项目风格的自然视图，1 为更宽视场（默认：0）。",
    )
    parser.add_argument(
        "--edge-compression",
        type=float,
        default=0.0,
        help="外围投影压缩强度，范围 0～1（默认：0，标准透视）。",
    )
    parser.add_argument(
        "--keep-crop-size",
        action="store_true",
        help="保留安全 ROI 的原始尺寸；默认缩放回输入分辨率。",
    )
    parser.add_argument(
        "--no-comparisons",
        action="store_true",
        help="不保存原图与矫正图的并排对比。",
    )
    return parser.parse_args()


def comparison_panel(image: np.ndarray, label: str) -> np.ndarray:
    panel = image.copy()
    font_scale = max(0.55, panel.shape[1] / 1200.0)
    thickness = max(1, int(round(panel.shape[1] / 600.0)))
    (text_width, text_height), baseline = cv2.getTextSize(
        label,
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        thickness,
    )
    cv2.rectangle(
        panel,
        (0, 0),
        (min(panel.shape[1], text_width + 20), text_height + baseline + 16),
        (0, 0, 0),
        -1,
    )
    cv2.putText(
        panel,
        label,
        (10, text_height + 7),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    return panel


def main() -> int:
    args = parse_args()
    if not 0.0 <= args.balance <= 1.0:
        raise ValueError("--balance 必须在 0～1 之间。")
    if not 0.0 <= args.edge_compression <= 1.0:
        raise ValueError("--edge-compression 必须在 0～1 之间。")
    if not args.input_dir.is_dir():
        raise FileNotFoundError(f"输入目录不存在：{args.input_dir}")
    if not args.calibration.is_file():
        raise FileNotFoundError(f"标定参数不存在：{args.calibration}")

    image_paths = sorted(
        path
        for path in args.input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not image_paths:
        raise RuntimeError(f"输入目录没有可处理图片：{args.input_dir}")

    calibration = load_calibration(args.calibration)
    model = str(calibration.get("model", "fisheye"))
    projection_alpha = 1.0 - 0.5 * args.edge_compression
    focal_scale = (
        fisheye_focal_scale_for_balance(args.balance)
        if model == "fisheye"
        else None
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir = args.output_dir / "comparisons"
    if not args.no_comparisons:
        comparison_dir.mkdir(parents=True, exist_ok=True)

    map_cache: Dict[
        Tuple[int, int],
        Tuple[np.ndarray, np.ndarray, np.ndarray, Tuple[int, int, int, int]],
    ] = {}
    processed = []
    skipped = []
    quality = [cv2.IMWRITE_JPEG_QUALITY, 95]

    for image_path in image_paths:
        image = cv2.imread(str(image_path))
        if image is None:
            skipped.append(f"{image_path.name}（无法读取）")
            continue

        height, width = image.shape[:2]
        frame_size = (width, height)
        if frame_size not in map_cache:
            map_x, map_y, _ = create_undistort_maps(
                calibration,
                frame_size,
                args.balance,
                projection_alpha=projection_alpha,
            )
            valid_mask = create_undistort_valid_mask(
                calibration,
                map_x,
                map_y,
                frame_size,
            )
            crop_roi = create_balance_crop_roi(
                calibration,
                map_x,
                map_y,
                frame_size,
                args.balance,
            )
            map_cache[frame_size] = (map_x, map_y, valid_mask, crop_roi)

        map_x, map_y, valid_mask, crop_roi = map_cache[frame_size]
        corrected_full = cv2.remap(
            image,
            map_x,
            map_y,
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
        )
        corrected_full[~valid_mask] = 0
        left, top, crop_width, crop_height = crop_roi
        corrected = corrected_full[
            top : top + crop_height,
            left : left + crop_width,
        ].copy()
        if not args.keep_crop_size and corrected.shape[:2] != image.shape[:2]:
            corrected = cv2.resize(
                corrected,
                frame_size,
                interpolation=cv2.INTER_CUBIC,
            )

        output_path = args.output_dir / image_path.name
        if not cv2.imwrite(str(output_path), corrected, quality):
            skipped.append(f"{image_path.name}（矫正图写入失败）")
            continue

        comparison_name = ""
        if not args.no_comparisons:
            display_corrected = corrected
            if display_corrected.shape[:2] != image.shape[:2]:
                display_corrected = cv2.resize(
                    display_corrected,
                    frame_size,
                    interpolation=cv2.INTER_CUBIC,
                )
            pair = np.hstack(
                (
                    comparison_panel(image, "Original"),
                    comparison_panel(
                        display_corrected,
                        f"Natural optimized balance={args.balance:.2f}",
                    ),
                )
            )
            comparison_path = comparison_dir / image_path.name
            if not cv2.imwrite(str(comparison_path), pair, quality):
                skipped.append(f"{image_path.name}（对比图写入失败）")
            else:
                comparison_name = str(comparison_path.relative_to(args.output_dir))

        processed.append(
            {
                "input": image_path.name,
                "output": output_path.name,
                "comparison": comparison_name,
                "resolution": [width, height],
                "crop_roi": list(crop_roi),
            }
        )

    metadata = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input_dir": str(args.input_dir),
        "calibration_path": str(args.calibration),
        "output_dir": str(args.output_dir),
        "model": model,
        "balance": args.balance,
        "focal_scale": focal_scale,
        "edge_compression": args.edge_compression,
        "projection_alpha": projection_alpha,
        "preserve_output_resolution": not args.keep_crop_size,
        "processed": processed,
        "skipped": skipped,
    }
    metadata_path = args.output_dir / "metadata.json"
    with metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)

    if not processed:
        raise RuntimeError("没有成功生成任何矫正图片。")
    print(
        f"矫正完成：{len(processed)} 张；balance={args.balance:.2f}；"
        f"focal_scale={focal_scale if focal_scale is not None else 'auto'}；"
        f"输出目录：{args.output_dir}"
    )
    if skipped:
        print(f"跳过或部分失败：{len(skipped)} 项，详情见 {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
