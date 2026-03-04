"""Program entry point for the manual labeling GUI tool."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from labeling_tool.config_manager import ConfigError, load_config
from labeling_tool.gui import LabelingApp


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for starting the labeling tool."""
    parser = argparse.ArgumentParser(description="General-purpose GUI labeling helper tool")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file. Default: config.yaml",
    )
    return parser.parse_args()


def _set_windows_app_user_model_id() -> None:
    """Set explicit AppUserModelID so Windows taskbar uses app icon reliably."""
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("manual_labeling_tool2.labeling")
    except Exception:
        pass


def _resolve_ui_icon_path(config_path: str, icon_value: str) -> str:
    """Resolve ui.icon to absolute path; return empty string if unavailable."""
    if not icon_value:
        return ""
    base_dir = os.path.dirname(os.path.abspath(config_path))
    resolved = icon_value if os.path.isabs(icon_value) else os.path.abspath(os.path.join(base_dir, icon_value))
    if os.path.isfile(resolved):
        return resolved
    return ""


def _build_icon_from_file(path_value: str) -> QIcon:
    """Build robust multi-size icon for better Windows title/taskbar compatibility."""
    icon = QIcon(path_value)
    pixmap = QPixmap(path_value)
    if pixmap.isNull():
        return icon

    multi_size_icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        scaled = pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        multi_size_icon.addPixmap(scaled)
    return multi_size_icon if not multi_size_icon.isNull() else icon


def main() -> int:
    """Load configuration, bootstrap GUI app, and start Qt event loop."""
    args = parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"[ConfigError] {exc}")
        return 1

    _set_windows_app_user_model_id()
    qt_app = QApplication(sys.argv)
    icon_path = _resolve_ui_icon_path(args.config, config.ui_icon)
    icon_candidates = []
    if icon_path:
        base, ext = os.path.splitext(icon_path)
        if ext.lower() != ".ico":
            sibling_ico = f"{base}.ico"
            if os.path.isfile(sibling_ico):
                icon_candidates.append(sibling_ico)
        icon_candidates.append(icon_path)

    for candidate in icon_candidates:
        app_icon = _build_icon_from_file(candidate)
        if app_icon.isNull():
            continue
        qt_app.setWindowIcon(app_icon)
        break
    window = LabelingApp(config=config, config_path=args.config)
    window.show()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
