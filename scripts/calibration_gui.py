"""Launch the PySide6 camera calibration desktop application."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from calibration.gui_app import main
except ModuleNotFoundError as error:
    if error.name == "PySide6":
        raise SystemExit(
            "缺少 PySide6。请先激活虚拟环境并执行：\n"
            "python -m pip install PySide6-Essentials==6.7.2"
        ) from error
    raise


if __name__ == "__main__":
    raise SystemExit(main(PROJECT_ROOT))
