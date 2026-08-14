"""PySide6 desktop application for ChArUco and chessboard calibration."""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .camera_devices import (
    CameraSource,
    camera_access_hint,
    default_camera_source,
    fourcc_name,
    list_camera_devices,
    normalize_camera_source,
    open_camera_capture,
)
from .workflow import (
    BoardConfig,
    MIN_CORNERS_PER_IMAGE,
    archive_calibration_images,
    calibrate_from_directory,
    create_balance_crop_roi,
    create_undistort_maps,
    create_undistort_valid_mask,
    detect_calibration_board,
    draw_detection,
    fisheye_focal_scale_for_balance,
    format_calibration_result,
    list_calibration_images,
    load_calibration,
)


class CameraThread(QThread):
    frame_ready = Signal(object)
    camera_opened = Signal(str)
    camera_error = Signal(str)
    camera_closed = Signal()

    def __init__(
        self,
        device: CameraSource,
        device_label: str,
        width: int,
        height: int,
        fps: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.device_label = device_label
        self.width = width
        self.height = height
        self.fps = fps
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        capture, backend_name, attempted = open_camera_capture(self.device)
        if capture is None:
            backends = "、".join(attempted)
            self.camera_error.emit(
                f"无法打开相机：{self.device_label}（已尝试 {backends}）。"
                f"{camera_access_hint()}"
            )
            return

        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        actual_fourcc = fourcc_name(capture.get(cv2.CAP_PROP_FOURCC)) or "未知"
        self.camera_opened.emit(
            f"已打开 {self.device_label}（{backend_name}），实际格式："
            f"{actual_width}×{actual_height} @ {actual_fps:.1f} FPS，{actual_fourcc}"
        )

        self._running = True
        consecutive_failures = 0
        last_emit = 0.0
        preview_interval = 1.0 / 30.0

        while self._running:
            ok, frame = capture.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 20:
                    self.camera_error.emit("连续读取相机画面失败，请重新插拔相机。")
                    break
                self.msleep(10)
                continue

            consecutive_failures = 0
            now = time.monotonic()
            if now - last_emit >= preview_interval:
                self.frame_ready.emit(frame)
                last_emit = now

        capture.release()
        self.camera_closed.emit()


class CalibrationThread(QThread):
    progress = Signal(str)
    succeeded = Signal(object, str)
    failed = Signal(str)

    def __init__(
        self,
        images_dir: Path,
        config: BoardConfig,
        model: str,
        output_path: Path,
        detected_images_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.images_dir = images_dir
        self.config = config
        self.model = model
        self.output_path = output_path
        self.detected_images_dir = detected_images_dir

    def run(self) -> None:
        try:
            result = calibrate_from_directory(
                self.images_dir,
                self.config,
                self.model,
                self.output_path,
                progress=self.progress.emit,
                detected_images_dir=self.detected_images_dir,
            )
            self.succeeded.emit(result, str(self.output_path))
        except Exception as error:  # noqa: BLE001 - show errors in the GUI
            self.failed.emit(str(error))


class VideoLabel(QLabel):
    def __init__(self, placeholder: str, parent=None) -> None:
        super().__init__(parent)
        self.placeholder = placeholder
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "QLabel { background: #111827; color: #94a3b8; "
            "border: 1px solid #334155; border-radius: 6px; }"
        )
        self.setText(placeholder)

    def clear_frame(self, message: Optional[str] = None) -> None:
        self.clear()
        self.setText(message or self.placeholder)

    def show_bgr_frame(self, frame: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(
            rgb.data,
            width,
            height,
            channels * width,
            QImage.Format.Format_RGB888,
        ).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pixmap)


class CalibrationWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = Path(project_root)
        self.images_root = self.project_root / "data" / "calibration" / "images"
        self.intrinsics_root = (
            self.project_root / "data" / "calibration" / "camera_intrinsics"
        )
        self.images_archive_root = (
            self.project_root / "data" / "calibration" / "image_archives"
        )
        self.detected_images_root = (
            self.project_root / "data" / "calibration" / "detected_images"
        )
        self.realtime_captures_root = (
            self.project_root / "data" / "realtime_captures"
        )
        self.images_root.mkdir(parents=True, exist_ok=True)
        self.intrinsics_root.mkdir(parents=True, exist_ok=True)
        self.images_archive_root.mkdir(parents=True, exist_ok=True)
        self.detected_images_root.mkdir(parents=True, exist_ok=True)
        self.realtime_captures_root.mkdir(parents=True, exist_ok=True)

        self.camera_thread: Optional[CameraThread] = None
        self.calibration_thread: Optional[CalibrationThread] = None
        self.current_frame: Optional[np.ndarray] = None
        self.current_frame_size: Optional[Tuple[int, int]] = None
        self.calibration: Optional[Dict[str, object]] = None
        self.calibration_path: Optional[Path] = None
        self.correction_enabled = False
        self.map_x: Optional[np.ndarray] = None
        self.map_y: Optional[np.ndarray] = None
        self.map_key: Optional[Tuple[object, ...]] = None
        self.crop_roi: Optional[Tuple[int, int, int, int]] = None

        self.setWindowTitle("鱼眼相机标定与实时矫正")
        self.resize(1500, 920)
        self._build_ui()
        self._apply_style()
        self.migrate_legacy_data()
        self.refresh_devices()
        self.refresh_image_count()

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.capture_image)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        root_layout.addWidget(self._build_settings_group())
        root_layout.addLayout(self._build_action_bar())

        video_splitter = QSplitter(Qt.Orientation.Horizontal)
        raw_container = self._build_video_panel("原始实时画面", "等待打开相机……")
        self.raw_video = raw_container[1]
        corrected_container = self._build_video_panel(
            "实时矫正画面",
            "完成标定并点击“开始实时矫正”",
        )
        self.corrected_video = corrected_container[1]
        video_splitter.addWidget(raw_container[0])
        video_splitter.addWidget(corrected_container[0])
        video_splitter.setSizes([750, 750])
        root_layout.addWidget(video_splitter, 3)

        details_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.parameters_text = QPlainTextEdit()
        self.parameters_text.setReadOnly(True)
        self.parameters_text.setPlaceholderText("标定完成后在此显示内参、畸变系数和误差……")
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("运行日志……")

        details_splitter.addWidget(
            self._wrap_widget("标定参数", self.parameters_text)
        )
        details_splitter.addWidget(self._wrap_widget("运行日志", self.log_text))
        details_splitter.setSizes([850, 650])
        root_layout.addWidget(details_splitter, 2)

        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("statusLabel")
        root_layout.addWidget(self.status_label)
        self.setCentralWidget(central)

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("相机与标定板设置")
        layout = QGridLayout(group)

        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.refresh_devices_button = QPushButton("刷新设备")
        self.refresh_devices_button.clicked.connect(self.refresh_devices)

        self.resolution_combo = QComboBox()
        for text, size in (
            ("1920 × 1080", (1920, 1080)),
            ("1280 × 960", (1280, 960)),
            ("1280 × 720", (1280, 720)),
            ("1024 × 768", (1024, 768)),
            ("800 × 600", (800, 600)),
            ("640 × 480", (640, 480)),
        ):
            self.resolution_combo.addItem(text, size)
        self.resolution_combo.setCurrentIndex(
            self.resolution_combo.findData((640, 480))
        )

        self.fps_combo = QComboBox()
        for fps in (60, 30, 20, 10, 5):
            self.fps_combo.addItem(str(fps), fps)
        self.fps_combo.setCurrentIndex(self.fps_combo.findData(30))

        self.model_combo = QComboBox()
        self.model_combo.addItem("鱼眼模型（推荐）", "fisheye")
        self.model_combo.addItem("针孔模型", "pinhole")
        self.model_combo.currentIndexChanged.connect(self.model_changed)

        self.pattern_combo = QComboBox()
        self.pattern_combo.addItem("ChArUco（推荐）", "charuco")
        self.pattern_combo.addItem("传统黑白棋盘格", "chessboard")
        self.pattern_combo.currentIndexChanged.connect(self.pattern_changed)

        self.dictionary_combo = QComboBox()
        for name in (
            "DICT_5X5_100",
            "DICT_5X5_250",
            "DICT_5X5_1000",
            "DICT_4X4_100",
            "DICT_6X6_250",
        ):
            if hasattr(cv2.aruco, name):
                self.dictionary_combo.addItem(name, name)

        self.vertical_spin = QSpinBox()
        self.vertical_spin.setRange(2, 100)
        self.vertical_spin.setValue(9)
        self.horizontal_spin = QSpinBox()
        self.horizontal_spin.setRange(2, 100)
        self.horizontal_spin.setValue(14)

        self.square_length_spin = QDoubleSpinBox()
        self.square_length_spin.setRange(0.01, 10000.0)
        self.square_length_spin.setDecimals(3)
        self.square_length_spin.setValue(20.0)
        self.square_length_spin.setSuffix(" mm")

        self.marker_length_spin = QDoubleSpinBox()
        self.marker_length_spin.setRange(0.01, 10000.0)
        self.marker_length_spin.setDecimals(3)
        self.marker_length_spin.setValue(15.0)
        self.marker_length_spin.setSuffix(" mm")

        self.balance_spin = QDoubleSpinBox()
        self.balance_spin.setRange(0.0, 1.0)
        self.balance_spin.setSingleStep(0.05)
        self.balance_spin.setDecimals(2)
        self.balance_spin.setValue(0.0)
        self.balance_spin.setToolTip(
            "0（推荐）：参考项目风格，使用原标定焦距，画面自然且无黑边；"
            "1：逐渐扩展到原项目的宽视场，外围拉伸和黑边可能增加"
        )
        self.balance_spin.valueChanged.connect(self.invalidate_maps)

        self.edge_compression_spin = QDoubleSpinBox()
        self.edge_compression_spin.setRange(0.0, 1.0)
        self.edge_compression_spin.setSingleStep(0.05)
        self.edge_compression_spin.setDecimals(2)
        self.edge_compression_spin.setValue(0.0)
        self.edge_compression_spin.setToolTip(
            "0：标准透视，直线最好但边缘拉伸最大；"
            "1：立体投影，边缘压缩最强；恢复标准效果请设为 0"
        )
        self.edge_compression_spin.valueChanged.connect(self.invalidate_maps)

        self.legacy_pattern_checkbox = QCheckBox("旧版布局兼容")
        self.legacy_pattern_checkbox.setChecked(False)
        self.legacy_pattern_checkbox.setToolTip(
            "仅适用于 OpenCV 4.6 之前生成的偶数行 ChArUco 标定板"
        )
        self.image_count_label = QLabel("标定照片：0 张")

        device_layout = QHBoxLayout()
        device_layout.addWidget(self.device_combo, 1)
        device_layout.addWidget(self.refresh_devices_button)

        layout.addWidget(QLabel("相机设备"), 0, 0)
        layout.addLayout(device_layout, 0, 1, 1, 3)
        layout.addWidget(QLabel("采集分辨率"), 0, 4)
        layout.addWidget(self.resolution_combo, 0, 5)
        layout.addWidget(QLabel("采集帧率"), 0, 6)
        layout.addWidget(self.fps_combo, 0, 7)

        layout.addWidget(QLabel("标定模型"), 1, 0)
        layout.addWidget(self.model_combo, 1, 1)
        layout.addWidget(QLabel("标定板类型"), 1, 2)
        layout.addWidget(self.pattern_combo, 1, 3)
        self.dictionary_label = QLabel("ArUco 字典")
        layout.addWidget(self.dictionary_label, 1, 4)
        layout.addWidget(self.dictionary_combo, 1, 5)
        layout.addWidget(self.legacy_pattern_checkbox, 1, 6)

        self.vertical_label = QLabel("纵向方格")
        layout.addWidget(self.vertical_label, 2, 0)
        layout.addWidget(self.vertical_spin, 2, 1)
        self.horizontal_label = QLabel("横向方格")
        layout.addWidget(self.horizontal_label, 2, 2)
        layout.addWidget(self.horizontal_spin, 2, 3)
        layout.addWidget(QLabel("方格边长"), 2, 4)
        layout.addWidget(self.square_length_spin, 2, 5)
        self.marker_length_label = QLabel("标记边长")
        layout.addWidget(self.marker_length_label, 2, 6)
        layout.addWidget(self.marker_length_spin, 2, 7)

        layout.addWidget(QLabel("矫正视场（0 自然 / 1 最宽）"), 3, 0)
        layout.addWidget(self.balance_spin, 3, 1)
        layout.addWidget(QLabel("边缘压缩"), 3, 2)
        layout.addWidget(self.edge_compression_spin, 3, 3)
        layout.addWidget(self.image_count_label, 3, 6, 1, 2)
        self.resolution_combo.currentIndexChanged.connect(self.resolution_changed)
        self.pattern_changed()
        return group

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        self.open_camera_button = QPushButton("打开相机")
        self.close_camera_button = QPushButton("关闭相机")
        self.capture_button = QPushButton("拍摄标定照片（空格）")
        self.new_batch_button = QPushButton("归档并新建批次")
        self.calibrate_button = QPushButton("开始计算标定参数")
        self.load_button = QPushButton("加载已有参数")
        self.correction_button = QPushButton("开始实时矫正")
        self.save_comparison_button = QPushButton("保存矫正对比")

        self.close_camera_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self.save_comparison_button.setEnabled(False)

        self.open_camera_button.clicked.connect(self.open_camera)
        self.close_camera_button.clicked.connect(self.close_camera)
        self.capture_button.clicked.connect(self.capture_image)
        self.new_batch_button.clicked.connect(self.start_new_calibration_batch)
        self.calibrate_button.clicked.connect(self.start_calibration)
        self.load_button.clicked.connect(self.load_existing_calibration)
        self.correction_button.clicked.connect(self.toggle_correction)
        self.save_comparison_button.clicked.connect(self.save_correction_comparison)

        for button in (
            self.open_camera_button,
            self.close_camera_button,
            self.capture_button,
            self.new_batch_button,
            self.calibrate_button,
            self.load_button,
            self.correction_button,
            self.save_comparison_button,
        ):
            layout.addWidget(button)
        layout.addStretch(1)
        return layout

    def _build_video_panel(self, title: str, placeholder: str):
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        video = VideoLabel(placeholder)
        layout.addWidget(video)
        return group, video

    def _wrap_widget(self, title: str, widget: QWidget) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(widget)
        return group

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f1f5f9; color: #0f172a; }
            QGroupBox {
                background: #ffffff; border: 1px solid #cbd5e1;
                border-radius: 7px; margin-top: 9px; font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton {
                background: #2563eb; color: white; border: none;
                border-radius: 5px; padding: 8px 14px; font-weight: 600;
            }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:disabled { background: #94a3b8; color: #e2e8f0; }
            QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
                background: white; border: 1px solid #cbd5e1;
                border-radius: 4px; padding: 4px;
            }
            QLabel#statusLabel {
                background: #e2e8f0; border-radius: 4px; padding: 7px;
            }
            """
        )

    def board_config(self) -> BoardConfig:
        config = BoardConfig(
            pattern_type=self.selected_pattern(),
            dictionary_name=str(self.dictionary_combo.currentData()),
            squares_vertical=self.vertical_spin.value(),
            squares_horizontal=self.horizontal_spin.value(),
            square_length=self.square_length_spin.value() / 1000.0,
            marker_length=self.marker_length_spin.value() / 1000.0,
            legacy_pattern=self.legacy_pattern_checkbox.isChecked(),
        )
        config.validate()
        return config

    def selected_model(self) -> str:
        return str(self.model_combo.currentData())

    def selected_pattern(self) -> str:
        return str(self.pattern_combo.currentData())

    def active_frame_size(self) -> Tuple[int, int]:
        if self.current_frame_size is not None:
            return self.current_frame_size
        width, height = self.resolution_combo.currentData()
        return int(width), int(height)

    def resolution_key(self, frame_size: Optional[Tuple[int, int]] = None) -> str:
        width, height = frame_size or self.active_frame_size()
        return f"{int(width)}x{int(height)}"

    def calibration_images_dir(
        self,
        frame_size: Optional[Tuple[int, int]] = None,
    ) -> Path:
        directory = self.images_root / self.resolution_key(frame_size)
        if self.selected_pattern() == "chessboard":
            directory /= "chessboard"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def calibration_intrinsics_dir(
        self,
        frame_size: Optional[Tuple[int, int]] = None,
    ) -> Path:
        directory = self.intrinsics_root / self.resolution_key(frame_size)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def calibration_images_archive_dir(
        self,
        frame_size: Optional[Tuple[int, int]] = None,
    ) -> Path:
        directory = (
            self.images_archive_root
            / self.resolution_key(frame_size)
            / self.selected_pattern()
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def calibration_detected_images_dir(
        self,
        model: str,
        frame_size: Optional[Tuple[int, int]] = None,
    ) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        directory = (
            self.detected_images_root
            / self.resolution_key(frame_size)
            / f"{timestamp}_{self.selected_pattern()}_{model}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def default_calibration_path(
        self,
        model: Optional[str] = None,
        frame_size: Optional[Tuple[int, int]] = None,
    ) -> Path:
        selected = model or self.selected_model()
        suffix = "" if self.selected_pattern() == "charuco" else "_chessboard"
        return self.calibration_intrinsics_dir(frame_size) / f"{selected}{suffix}_calibration.json"

    def migrate_legacy_data(self) -> None:
        moved_images = 0
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
        for image_path in list(self.images_root.iterdir()):
            if not image_path.is_file() or image_path.suffix.lower() not in image_extensions:
                continue
            image = cv2.imread(str(image_path))
            if image is None:
                continue
            height, width = image.shape[:2]
            target_dir = self.images_root / f"{width}x{height}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / image_path.name
            if target_path.exists():
                target_path = target_dir / f"{image_path.stem}_migrated{image_path.suffix}"
            image_path.replace(target_path)
            moved_images += 1

        copied_parameters = 0
        for parameter_path in self.intrinsics_root.glob("*.json"):
            try:
                calibration = load_calibration(parameter_path)
                image_size = calibration.get("image_size")
                if not image_size or len(image_size) != 2:
                    continue
                target_dir = self.intrinsics_root / f"{int(image_size[0])}x{int(image_size[1])}"
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = target_dir / parameter_path.name
                if not target_path.exists():
                    shutil.copy2(parameter_path, target_path)
                    copied_parameters += 1
            except Exception:  # noqa: BLE001 - leave unreadable legacy files untouched
                continue

        if moved_images:
            self.append_log(f"已按分辨率整理旧标定照片：{moved_images} 张")
        if copied_parameters:
            self.append_log(f"已按分辨率归档旧标定参数：{copied_parameters} 个")

    @Slot()
    def refresh_devices(self) -> None:
        current_text = self.device_combo.currentText().strip()
        current_source = normalize_camera_source(current_text) if current_text else None
        devices = list_camera_devices()

        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in devices:
            self.device_combo.addItem(device.label, device.source)

        selected_index = next(
            (
                index
                for index, device in enumerate(devices)
                if device.source == current_source
            ),
            0 if devices else -1,
        )
        self.device_combo.setCurrentIndex(selected_index)
        if not devices:
            fallback = default_camera_source()
            self.device_combo.setEditText(current_text or str(fallback))
        self.device_combo.blockSignals(False)
        if devices:
            self.append_log(
                "已刷新视频设备：" + ", ".join(device.label for device in devices)
            )
        else:
            self.append_log("未自动检测到视频设备；可手动输入相机索引或设备路径。")

    def refresh_image_count(self) -> None:
        image_dir = self.calibration_images_dir()
        count = len(list_calibration_images(image_dir))
        self.image_count_label.setText(
            f"标定照片（{self.resolution_key()}）：{count} 张"
        )

    @Slot()
    def start_new_calibration_batch(self) -> None:
        if self.calibration_thread and self.calibration_thread.isRunning():
            QMessageBox.information(self, "标定进行中", "请等待当前标定计算完成。")
            return

        images_dir = self.calibration_images_dir()
        image_paths = list_calibration_images(images_dir)
        if not image_paths:
            message = "当前标定批次没有照片，可以直接开始拍摄新数据。"
            self.append_log(message)
            self.set_status(message)
            QMessageBox.information(self, "新标定批次", message)
            return

        reply = QMessageBox.question(
            self,
            "归档并新建标定批次",
            f"当前共有 {len(image_paths)} 张标定照片。\n\n"
            "这些照片不会被删除，而是移动到带时间戳的归档目录；"
            "当前目录随后会变为空目录。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            archive_dir, archived_names = archive_calibration_images(
                images_dir,
                self.calibration_images_archive_dir(),
            )
            if archive_dir is None:
                return

            parameter_archive_dir = archive_dir / "camera_intrinsics"
            for model in ("fisheye", "pinhole"):
                parameter_path = self.default_calibration_path(model)
                if parameter_path.is_file():
                    parameter_archive_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(parameter_path, parameter_archive_dir / parameter_path.name)

            self.calibration = None
            self.calibration_path = None
            self.correction_enabled = False
            self.correction_button.setText("开始实时矫正")
            self.parameters_text.clear()
            self.corrected_video.clear_frame("新标定批次尚未生成矫正参数。")
            self.save_comparison_button.setEnabled(False)
            self.invalidate_maps()
            self.refresh_image_count()

            message = (
                f"已归档 {len(archived_names)} 张照片并新建空批次：{archive_dir}"
            )
            self.append_log(message)
            self.set_status(message)
            QMessageBox.information(self, "新批次已创建", message)
        except Exception as error:  # noqa: BLE001 - report archive failures
            QMessageBox.critical(self, "新建批次失败", str(error))

    @Slot()
    def resolution_changed(self) -> None:
        if not (self.camera_thread and self.camera_thread.isRunning()):
            self.current_frame_size = None
        if self.calibration:
            calibration_size = self.calibration.get("image_size")
            if calibration_size and tuple(calibration_size) != self.active_frame_size():
                self.calibration = None
                self.calibration_path = None
                self.correction_enabled = False
                self.correction_button.setText("开始实时矫正")
                self.parameters_text.clear()
                self.corrected_video.clear_frame()
                self.invalidate_maps()
        self.refresh_image_count()

    def set_camera_settings_enabled(self, enabled: bool) -> None:
        self.device_combo.setEnabled(enabled)
        self.refresh_devices_button.setEnabled(enabled)
        self.resolution_combo.setEnabled(enabled)
        self.fps_combo.setEnabled(enabled)

    @Slot()
    def open_camera(self) -> None:
        if self.camera_thread and self.camera_thread.isRunning():
            return

        device_label = self.device_combo.currentText().strip()
        selected_index = self.device_combo.currentIndex()
        if (
            selected_index >= 0
            and device_label == self.device_combo.itemText(selected_index)
        ):
            device = self.device_combo.itemData(selected_index)
        else:
            device = normalize_camera_source(device_label)
        width, height = self.resolution_combo.currentData()
        fps = int(self.fps_combo.currentData())
        self.camera_thread = CameraThread(
            device,
            device_label or str(device),
            width,
            height,
            fps,
            self,
        )
        self.camera_thread.frame_ready.connect(self.on_frame)
        self.camera_thread.camera_opened.connect(self.on_camera_opened)
        self.camera_thread.camera_error.connect(self.on_camera_error)
        self.camera_thread.camera_closed.connect(self.on_camera_closed)
        self.camera_thread.start()

        self.open_camera_button.setEnabled(False)
        self.close_camera_button.setEnabled(True)
        self.capture_button.setEnabled(True)
        self.set_camera_settings_enabled(False)
        self.set_status(f"正在打开相机 {device_label or device}……")

    @Slot()
    def close_camera(self) -> None:
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.stop()
            self.camera_thread.wait(2000)
        self.camera_thread = None
        self.current_frame = None
        self.current_frame_size = None
        self.raw_video.clear_frame()
        self.corrected_video.clear_frame()
        self.correction_enabled = False
        self.correction_button.setText("开始实时矫正")
        self.save_comparison_button.setEnabled(False)
        self.on_camera_closed()
        self.refresh_image_count()

    @Slot(str)
    def on_camera_opened(self, message: str) -> None:
        self.append_log(message)
        self.set_status(message)
        width, height = self.resolution_combo.currentData()
        if width < 1280 or height < 720:
            self.append_log(
                "提示：当前分辨率较低，放大显示和去畸变后会偏软；"
                "正式标定建议使用 1920×1080。"
            )

    @Slot(str)
    def on_camera_error(self, message: str) -> None:
        self.append_log("错误：" + message)
        self.set_status(message)
        QMessageBox.critical(self, "相机错误", message)
        self.on_camera_closed()

    @Slot()
    def on_camera_closed(self) -> None:
        self.open_camera_button.setEnabled(True)
        self.close_camera_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self.save_comparison_button.setEnabled(False)
        self.set_camera_settings_enabled(True)

    @Slot(object)
    def on_frame(self, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        frame_size = (width, height)
        if self.current_frame_size != frame_size:
            self.current_frame_size = frame_size
            if self.calibration:
                calibration_size = self.calibration.get("image_size")
                if calibration_size and tuple(calibration_size) != frame_size:
                    self.calibration = None
                    self.calibration_path = None
                    self.correction_enabled = False
                    self.correction_button.setText("开始实时矫正")
                    self.parameters_text.clear()
                    self.corrected_video.clear_frame(
                        "当前分辨率已变化，请加载对应的标定参数。"
                    )
                    self.invalidate_maps()
            self.refresh_image_count()
        self.current_frame = frame
        self.raw_video.show_bgr_frame(frame)
        self.save_comparison_button.setEnabled(self.calibration is not None)

        if not self.correction_enabled or self.calibration is None:
            return

        try:
            balance = self.balance_spin.value()
            edge_compression = self.edge_compression_spin.value()
            projection_alpha = 1.0 - 0.5 * edge_compression
            model = str(self.calibration.get("model", "fisheye"))
            key = (
                model,
                width,
                height,
                round(balance, 4),
                round(projection_alpha, 4),
                id(self.calibration),
            )
            if self.map_key != key or self.map_x is None or self.map_y is None:
                self.map_x, self.map_y, _ = create_undistort_maps(
                    self.calibration,
                    (width, height),
                    balance,
                    projection_alpha=projection_alpha,
                )
                self.crop_roi = create_balance_crop_roi(
                    self.calibration,
                    self.map_x,
                    self.map_y,
                    (width, height),
                    balance,
                )
                self.map_key = key

            corrected = cv2.remap(
                frame,
                self.map_x,
                self.map_y,
                interpolation=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
            )
            if self.crop_roi is not None:
                left, top, crop_width, crop_height = self.crop_roi
                corrected = corrected[
                    top : top + crop_height,
                    left : left + crop_width,
                ]
            self.corrected_video.show_bgr_frame(corrected)
        except Exception as error:  # noqa: BLE001 - report to GUI
            self.correction_enabled = False
            self.correction_button.setText("开始实时矫正")
            self.append_log(f"实时矫正失败：{error}")
            self.corrected_video.clear_frame("实时矫正失败，请检查参数文件。")

    @Slot()
    def capture_image(self) -> None:
        if self.current_frame is None:
            QMessageBox.information(self, "尚无画面", "请先打开相机。")
            return

        try:
            config = self.board_config()
            frame = self.current_frame.copy()
            detection = detect_calibration_board(frame, config)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "检测失败", str(error))
            return

        if detection.corner_count < MIN_CORNERS_PER_IMAGE:
            if config.pattern_type == "chessboard":
                message = (
                    "当前画面未检测到完整的传统棋盘格内角点。\n\n"
                    f"请确认参数填写的是内角点数：横向 {config.squares_horizontal}、"
                    f"纵向 {config.squares_vertical}。例如 14×9 个方格应填写 13×8 个内角点。\n"
                    "传统棋盘格需要完整可见，并应保持清晰、无强反光。"
                )
            elif detection.marker_count >= 10 and detection.corner_count == 0:
                message = (
                    f"当前画面检测到 {detection.marker_count} 个 ArUco 标记，但没有"
                    "生成 ChArUco 角点。\n\n"
                    "这通常表示横纵方格或布局设置不匹配。这块实体板在 OpenCV "
                    "坐标中应设置为横向 14、纵向 9；竖着摆放时也不要交换。"
                )
            else:
                message = (
                    f"当前画面检测到 {detection.marker_count} 个 ArUco 标记、"
                    f"{detection.corner_count} 个 ChArUco 角点，未达到保存要求。\n\n"
                    "请减小反光、保持画面清晰，并让标定板在画面中占更大面积。"
                )
            self.append_log(message.replace("\n", " "))
            QMessageBox.warning(self, "未保存照片", message)
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        output_dir = self.calibration_images_dir(
            (int(frame.shape[1]), int(frame.shape[0]))
        )
        prefix = "chessboard" if config.pattern_type == "chessboard" else "charuco"
        output_path = output_dir / f"{prefix}_{timestamp}.jpg"
        if not cv2.imwrite(str(output_path), frame):
            QMessageBox.critical(self, "保存失败", f"无法保存图片：{output_path}")
            return

        annotated = draw_detection(frame, detection)
        self.raw_video.show_bgr_frame(annotated)
        self.refresh_image_count()
        if config.pattern_type == "chessboard":
            message = f"已保存 {output_path.name}；检测到 {detection.corner_count} 个内角点。"
        else:
            message = (
                f"已保存 {output_path.name}；检测到 {detection.marker_count} 个标记、"
                f"{detection.corner_count} 个角点。"
            )
        self.append_log(message)
        self.set_status(message)

    @Slot()
    def start_calibration(self) -> None:
        if self.calibration_thread and self.calibration_thread.isRunning():
            return

        try:
            config = self.board_config()
        except ValueError as error:
            QMessageBox.warning(self, "参数错误", str(error))
            return

        model = self.selected_model()
        frame_size = self.active_frame_size()
        images_dir = self.calibration_images_dir(frame_size)
        output_path = self.default_calibration_path(model, frame_size)
        detected_images_dir = self.calibration_detected_images_dir(
            model,
            frame_size,
        )
        self.calibration_thread = CalibrationThread(
            images_dir,
            config,
            model,
            output_path,
            detected_images_dir,
            self,
        )
        self.calibration_thread.progress.connect(self.on_calibration_progress)
        self.calibration_thread.succeeded.connect(self.on_calibration_succeeded)
        self.calibration_thread.failed.connect(self.on_calibration_failed)
        self.calibration_thread.finished.connect(self.on_calibration_finished)
        self.calibration_thread.start()

        self.calibrate_button.setEnabled(False)
        self.load_button.setEnabled(False)
        self.new_batch_button.setEnabled(False)
        self.set_status("正在计算标定参数，请稍候……")

    @Slot(str)
    def on_calibration_progress(self, message: str) -> None:
        self.append_log(message)
        self.set_status(message)

    @Slot(object, str)
    def on_calibration_succeeded(
        self,
        result: Dict[str, object],
        output_path: str,
    ) -> None:
        self.calibration = result
        self.calibration_path = Path(output_path)
        self.invalidate_maps()
        self.parameters_text.setPlainText(format_calibration_result(result))
        self.save_comparison_button.setEnabled(self.current_frame is not None)
        detected_images_dir = str(result.get("detected_images_dir", ""))
        detected_count = len(result.get("detected_images", []))
        outlier_count = len(result.get("outlier_images", []))
        message = f"标定完成，参数已保存：{output_path}"
        if outlier_count:
            message += f"\n已自动剔除 {outlier_count} 张误差离群图并重新标定。"
        if detected_images_dir:
            message += (
                f"\n检测识别图像：{detected_images_dir}"
                f"（共 {detected_count} 张）"
            )
        self.append_log(message)
        self.set_status(message)
        QMessageBox.information(self, "标定完成", message)

    @Slot(str)
    def on_calibration_failed(self, message: str) -> None:
        self.append_log("标定失败：" + message)
        self.set_status("标定失败")
        QMessageBox.critical(self, "标定失败", message)

    @Slot()
    def on_calibration_finished(self) -> None:
        self.calibrate_button.setEnabled(True)
        self.load_button.setEnabled(True)
        self.new_batch_button.setEnabled(True)

    @Slot()
    def load_existing_calibration(self) -> bool:
        default_path = self.default_calibration_path()
        selected_path = default_path
        if not default_path.exists():
            filename, _ = QFileDialog.getOpenFileName(
                self,
                "选择标定参数文件",
                str(self.calibration_intrinsics_dir()),
                "JSON 参数文件 (*.json)",
            )
            if not filename:
                return False
            selected_path = Path(filename)

        try:
            result = load_calibration(selected_path)
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "加载失败", str(error))
            return False

        calibration_size = result.get("image_size")
        if calibration_size and tuple(calibration_size) != self.active_frame_size():
            QMessageBox.warning(
                self,
                "分辨率不匹配",
                f"当前相机分辨率为 {self.resolution_key()}，但参数文件属于 "
                f"{int(calibration_size[0])}x{int(calibration_size[1])}。\n"
                "请加载当前分辨率对应的参数文件。",
            )
            return False

        self.calibration = result
        self.calibration_path = selected_path
        self.invalidate_maps()
        self.parameters_text.setPlainText(format_calibration_result(result))
        self.save_comparison_button.setEnabled(self.current_frame is not None)
        message = f"已加载标定参数：{selected_path}"
        self.append_log(message)
        self.set_status(message)
        return True

    @staticmethod
    def _comparison_panel(image: np.ndarray, label: str) -> np.ndarray:
        panel = image.copy()
        font_scale = max(0.65, panel.shape[1] / 1500.0)
        thickness = max(2, int(round(panel.shape[1] / 900.0)))
        (text_width, text_height), _ = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            thickness,
        )
        cv2.rectangle(
            panel,
            (0, 0),
            (min(panel.shape[1], text_width + 24), text_height + 22),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            panel,
            label,
            (12, text_height + 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )
        return panel

    @Slot()
    def save_correction_comparison(self) -> None:
        if self.current_frame is None:
            QMessageBox.information(self, "尚无画面", "请先打开相机。")
            return
        if self.calibration is None and not self.load_existing_calibration():
            return

        frame = self.current_frame.copy()
        height, width = frame.shape[:2]
        frame_size = (width, height)
        current_balance = float(self.balance_spin.value())
        edge_compression = float(self.edge_compression_spin.value())
        projection_alpha = 1.0 - 0.5 * edge_compression

        try:
            selected_balances = tuple(
                dict.fromkeys((0.0, 0.5, 1.0, current_balance))
            )
            full_by_balance: Dict[float, np.ndarray] = {}
            corrected_by_balance: Dict[float, np.ndarray] = {}
            roi_by_balance: Dict[float, Tuple[int, int, int, int]] = {}
            focal_scale_by_balance: Dict[float, Optional[float]] = {}
            model = str(self.calibration.get("model", "fisheye"))

            for balance in selected_balances:
                map_x, map_y, _ = create_undistort_maps(
                    self.calibration,
                    frame_size,
                    balance,
                    projection_alpha=projection_alpha,
                )
                full_corrected = cv2.remap(
                    frame,
                    map_x,
                    map_y,
                    interpolation=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_CONSTANT,
                )
                valid_mask = create_undistort_valid_mask(
                    self.calibration,
                    map_x,
                    map_y,
                    frame_size,
                )
                full_corrected[~valid_mask] = 0
                roi = create_balance_crop_roi(
                    self.calibration,
                    map_x,
                    map_y,
                    frame_size,
                    balance,
                )
                full_by_balance[balance] = full_corrected
                roi_by_balance[balance] = roi
                left, top, crop_width, crop_height = roi
                corrected_by_balance[balance] = full_corrected[
                    top : top + crop_height,
                    left : left + crop_width,
                ].copy()
                focal_scale_by_balance[balance] = (
                    fisheye_focal_scale_for_balance(balance)
                    if model == "fisheye"
                    else None
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            output_dir = (
                self.realtime_captures_root
                / self.resolution_key(frame_size)
                / timestamp
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            current_corrected = corrected_by_balance[current_balance]
            diagnostic_full = full_by_balance[current_balance]
            current_diagnostic_roi = roi_by_balance[current_balance]
            original_path = output_dir / "original.jpg"
            corrected_path = output_dir / f"corrected_balance_{current_balance:.2f}.jpg"
            corrected_full_path = output_dir / "corrected_full.jpg"
            corrected_full_roi_path = (
                output_dir
                / f"corrected_full_with_roi_balance_{current_balance:.2f}.jpg"
            )
            pair_path = output_dir / f"original_vs_corrected_{current_balance:.2f}.jpg"
            grid_path = output_dir / "balance_comparison.jpg"
            cropped_grid_path = output_dir / "cropped_balance_comparison.jpg"

            corrected_full_with_roi = diagnostic_full.copy()
            left, top, crop_width, crop_height = current_diagnostic_roi
            right = min(width - 1, left + crop_width - 1)
            bottom = min(height - 1, top + crop_height - 1)
            roi_thickness = max(3, int(round(width / 320.0)))
            cv2.rectangle(
                corrected_full_with_roi,
                (left, top),
                (right, bottom),
                (40, 255, 40),
                roi_thickness,
                cv2.LINE_AA,
            )

            original_panel = self._comparison_panel(frame, "Original")
            current_panel = self._comparison_panel(
                corrected_full_with_roi,
                f"Corrected full + ROI balance={current_balance:.2f}",
            )
            pair = np.hstack((original_panel, current_panel))

            grid_panels = [original_panel]
            cropped_grid_panels = [original_panel]
            for balance in (0.0, 0.5, 1.0):
                roi_panel = full_by_balance[balance].copy()
                left, top, crop_width, crop_height = (
                    roi_by_balance[balance]
                )
                right = min(width - 1, left + crop_width - 1)
                bottom = min(height - 1, top + crop_height - 1)
                cv2.rectangle(
                    roi_panel,
                    (left, top),
                    (right, bottom),
                    (40, 255, 40),
                    roi_thickness,
                    cv2.LINE_AA,
                )
                grid_panels.append(
                    self._comparison_panel(
                        roi_panel,
                        "Corrected full + ROI "
                        f"balance={balance:.1f} "
                        f"scale={focal_scale_by_balance[balance] or 1.0:.2f}",
                    )
                )

                cropped_panel = cv2.resize(
                    corrected_by_balance[balance],
                    (width, height),
                    interpolation=cv2.INTER_CUBIC,
                )
                cropped_grid_panels.append(
                    self._comparison_panel(
                        cropped_panel,
                        f"Cropped output balance={balance:.1f}",
                    )
                )
            grid = np.vstack(
                (
                    np.hstack((grid_panels[0], grid_panels[1])),
                    np.hstack((grid_panels[2], grid_panels[3])),
                )
            )
            cropped_grid = np.vstack(
                (
                    np.hstack((cropped_grid_panels[0], cropped_grid_panels[1])),
                    np.hstack((cropped_grid_panels[2], cropped_grid_panels[3])),
                )
            )

            quality = [cv2.IMWRITE_JPEG_QUALITY, 95]
            writes = (
                cv2.imwrite(str(original_path), frame, quality),
                cv2.imwrite(str(corrected_path), current_corrected, quality),
                cv2.imwrite(str(corrected_full_path), diagnostic_full, quality),
                cv2.imwrite(
                    str(corrected_full_roi_path),
                    corrected_full_with_roi,
                    quality,
                ),
                cv2.imwrite(str(pair_path), pair, quality),
                cv2.imwrite(str(grid_path), grid, quality),
                cv2.imwrite(str(cropped_grid_path), cropped_grid, quality),
            )
            if not all(writes):
                raise RuntimeError("部分对比图片写入失败。")

            metadata = {
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "resolution": [width, height],
                "model": self.calibration.get("model", "fisheye"),
                "current_balance": current_balance,
                "edge_compression": edge_compression,
                "projection_alpha": projection_alpha,
                "focal_scale": focal_scale_by_balance[current_balance],
                "focal_scales": {
                    f"{balance:.2f}": focal_scale_by_balance[balance]
                    for balance in selected_balances
                },
                "saved_balances": [0.0, 0.5, 1.0],
                "calibration_path": str(self.calibration_path or ""),
                "files": {
                    "original": original_path.name,
                    "corrected": corrected_path.name,
                    "corrected_full": corrected_full_path.name,
                    "corrected_full_with_roi": corrected_full_roi_path.name,
                    "side_by_side": pair_path.name,
                    "balance_grid": grid_path.name,
                    "cropped_balance_grid": cropped_grid_path.name,
                },
            }
            with (output_dir / "metadata.json").open("w", encoding="utf-8") as file:
                json.dump(metadata, file, ensure_ascii=False, indent=2)

            message = f"实时矫正对比图片已保存：{output_dir}"
            self.append_log(message)
            self.set_status(message)
            QMessageBox.information(self, "保存完成", message)
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(error))

    @Slot()
    def toggle_correction(self) -> None:
        if self.correction_enabled:
            self.correction_enabled = False
            self.correction_button.setText("开始实时矫正")
            self.corrected_video.clear_frame("实时矫正已停止")
            self.set_status("已停止实时矫正")
            return

        if self.current_frame is None:
            QMessageBox.information(self, "尚无画面", "请先打开相机。")
            return
        if self.calibration is None and not self.load_existing_calibration():
            return

        self.correction_enabled = True
        self.correction_button.setText("停止实时矫正")
        self.invalidate_maps()
        self.set_status("实时矫正已开启")

    @Slot()
    def model_changed(self) -> None:
        if self.calibration and self.calibration.get("model") != self.selected_model():
            self.calibration = None
            self.calibration_path = None
            self.correction_enabled = False
            self.correction_button.setText("开始实时矫正")
            self.parameters_text.clear()
            self.corrected_video.clear_frame()
            self.save_comparison_button.setEnabled(False)
            self.invalidate_maps()

    @Slot()
    def pattern_changed(self) -> None:
        chessboard = self.selected_pattern() == "chessboard"
        self.dictionary_label.setEnabled(not chessboard)
        self.dictionary_combo.setEnabled(not chessboard)
        self.marker_length_label.setEnabled(not chessboard)
        self.marker_length_spin.setEnabled(not chessboard)
        self.legacy_pattern_checkbox.setEnabled(not chessboard)
        self.vertical_label.setText("纵向内角点" if chessboard else "纵向方格")
        self.horizontal_label.setText("横向内角点" if chessboard else "横向方格")
        tip = (
            "传统棋盘格填写内角点数量；例如 14×9 个方格应填写横向 13、纵向 8。"
            if chessboard
            else "ChArUco 填写方格数量。"
        )
        self.vertical_spin.setToolTip(tip)
        self.horizontal_spin.setToolTip(tip)
        if self.calibration:
            board = self.calibration.get("board") or {}
            loaded_pattern = str(board.get("pattern_type", "charuco"))
            if loaded_pattern != self.selected_pattern():
                self.calibration = None
                self.calibration_path = None
                self.correction_enabled = False
                self.correction_button.setText("开始实时矫正")
                self.parameters_text.clear()
                self.corrected_video.clear_frame()
                self.save_comparison_button.setEnabled(False)
                self.invalidate_maps()
        if hasattr(self, "image_count_label"):
            self.refresh_image_count()

    @Slot()
    def invalidate_maps(self) -> None:
        self.map_x = None
        self.map_y = None
        self.map_key = None
        self.crop_roi = None

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{timestamp}] {message}")

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self.calibration_thread and self.calibration_thread.isRunning():
            QMessageBox.information(self, "标定进行中", "请等待标定计算完成后再关闭。")
            event.ignore()
            return
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.stop()
            self.camera_thread.wait(2000)
        event.accept()


def main(project_root: Optional[Path] = None) -> int:
    root = project_root or Path(__file__).resolve().parents[2]
    application = QApplication.instance() or QApplication(sys.argv)
    window = CalibrationWindow(root)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
