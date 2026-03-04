"""PyQt6 GUI for configurable CSV labeling with autosave/resume/edit flows."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from labeling_tool.config_manager import build_core_signature, build_path_signature, config_for_logging
from labeling_tool.data_manager import (
    DataError,
    PreparedRow,
    build_sample_indexes,
    load_csv_rows,
    prepare_row,
    validate_display_columns,
)
from labeling_tool.filter_loader import FilterLoadError, load_pre_filters
from labeling_tool.models import AppConfig, LabelDefinition
from labeling_tool.session_logger import SessionLogger
from labeling_tool.storage import (
    StorageError,
    clear_output_file,
    make_non_overwrite_path,
    read_output_rows,
    read_state,
    write_output_rows,
    write_state,
)


class LabelingApp(QMainWindow):
    """Main PyQt6 app that orchestrates data prep, labeling input, and persistence."""

    COLORS = {
        "primary": "#3B82F6",
        "accent": "#67E8F9",
        "warning": "#F97316",
        "text": "#1E293B",
        "muted": "#94A3B8",
        "bg": "#F8FAFC",
        "panel": "#FFFFFF",
        "input": "#F1F5F9",
        "border": "#E2E8F0",
        "divider": "#E2E8F0",
        "secondary": "#E2E8F0",
        "secondary_text": "#334155",
    }

    DARK_COLORS = {
        "primary": "#3B82F6",
        "accent": "#67E8F9",
        "warning": "#F97316",
        "text": "#E2E8F0",
        "muted": "#94A3B8",
        "bg": "#0F172A",
        "panel": "#1E293B",
        "input": "#334155",
        "border": "#334155",
        "divider": "#334155",
        "secondary": "#334155",
        "secondary_text": "#E2E8F0",
    }

    BASE_VIEW_WIDTH = 1400
    BASE_VIEW_HEIGHT = 900
    BASE_FONT_PX = {
        "base": 13,
        "title": 16,
        "module": 14,
        "field": 13,
        "body": 13,
        "hint": 11,
        "button": 13,
        "header": 13,
        "validation": 10,
    }

    def __init__(self, config: AppConfig, config_path: str) -> None:
        super().__init__()

        self.config_obj = config
        self.config_path = os.path.abspath(config_path)
        self.config_dir = os.path.dirname(self.config_path)
        self.path_signature = build_path_signature(self.config_obj)
        self.core_signature = build_core_signature(self.config_obj)
        self._loaded_window_icon = QIcon()

        self.session_logger = SessionLogger(self.config_obj.log_file)
        self.session_logger.attach_callback(self._append_log_line)
        self._apply_window_icon()

        self.raw_rows: List[Dict[str, str]] = []
        self.prepared_rows: List[Optional[PreparedRow]] = []
        self.sample_indexes: List[int] = []
        self.label_values: List[Dict[str, str]] = []

        self.current_progress_index = 0
        self.active_index = 0
        self.editing_index: Optional[int] = None
        self.active_row_start_time = time.time()

        self.display_columns_runtime: List[str] = []

        self.single_combos: Dict[str, QComboBox] = {}
        self.single_custom_entries: Dict[str, QLineEdit] = {}
        self.multi_checkboxes: Dict[str, Dict[str, QCheckBox]] = {}
        self.multi_custom_entries: Dict[str, QLineEdit] = {}
        self.text_edits: Dict[str, QTextEdit] = {}
        self.validation_labels: Dict[str, QLabel] = {}
        self.validation_widgets: Dict[str, List[QWidget]] = {}

        self.current_theme_mode = "Dark"
        self.active_colors = dict(self.DARK_COLORS)
        self.font_scale = 1.0
        self._last_applied_font_scale = -1.0

        self.resume_state = self._safe_read_state()
        self.continue_history = self._apply_resume_strategy()

        try:
            self.filter_map = load_pre_filters(self.config_dir, self.config_obj.pre_filters)
        except FilterLoadError as exc:
            self._error_and_close("筛选器加载失败", str(exc))
            raise

        try:
            self._prepare_data_context()
        except (DataError, StorageError) as exc:
            self._error_and_close("数据初始化失败", str(exc))
            raise

        self._build_ui()
        self._bind_shortcuts()
        self._refresh_recent_table()
        self._show_progress_row()

        ui_theme = self.config_obj.ui_theme if self.config_obj.ui_theme in {"Light", "Dark"} else "Dark"
        self.theme_combo.setCurrentText(ui_theme)
        self._apply_theme(ui_theme)

        self.session_logger.log_session_start(config_for_logging(self.config_obj))
        self.session_logger.info(
            f"Ready. rows_total={len(self.raw_rows)}, sample_size={len(self.prepared_rows)}, output={self.config_obj.output_csv}"
        )

    def _exec_message_box(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        """Show top-most app-modal message box to avoid being hidden behind windows."""
        # Use a top-level window (no parent) so Windows can show it in taskbar.
        box = QMessageBox(None)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default_button != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(default_button)
        window_icon = self._loaded_window_icon
        if window_icon.isNull():
            window_icon = self.windowIcon()
        if window_icon.isNull():
            app = QApplication.instance()
            if app is not None:
                window_icon = app.windowIcon()
        if not window_icon.isNull():
            box.setWindowIcon(window_icon)

        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.Dialog, False)
        box.setWindowFlag(Qt.WindowType.Tool, False)
        box.setWindowFlag(Qt.WindowType.Window, True)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        return QMessageBox.StandardButton(box.exec())

    def _warn(self, title: str, text: str) -> None:
        """Show warning dialog in top-most app-modal mode."""
        self._exec_message_box(QMessageBox.Icon.Warning, title, text)

    def _info(self, title: str, text: str) -> None:
        """Show information dialog in top-most app-modal mode."""
        self._exec_message_box(QMessageBox.Icon.Information, title, text)

    def _error(self, title: str, text: str) -> None:
        """Show error dialog in top-most app-modal mode."""
        self._exec_message_box(QMessageBox.Icon.Critical, title, text)

    def _ask_yes_no(
        self,
        title: str,
        text: str,
        default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.No,
    ) -> QMessageBox.StandardButton:
        """Show Yes/No confirm dialog in top-most app-modal mode."""
        return self._exec_message_box(
            icon=QMessageBox.Icon.Question,
            title=title,
            text=text,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button=default_button,
        )

    def _safe_read_state(self) -> Dict[str, Any]:
        """Read state file with robust fallback so UI can still start."""
        try:
            return read_state(self.config_obj.state_file) or {}
        except StorageError as exc:
            self._warn("状态文件读取失败", f"将以新会话启动。\n{exc}")
            return {}

    def _detect_resume_mode(self) -> str:
        """Detect whether config is unchanged / changed / path changed for resume behavior."""
        if not self.resume_state:
            return "new"

        old_path_sig = self.resume_state.get("path_signature")
        old_core_sig = self.resume_state.get("core_signature")

        if old_path_sig != self.path_signature:
            return "path_changed"
        if old_core_sig == self.core_signature:
            return "same"
        return "same_path_diff_core"

    def _apply_resume_strategy(self) -> bool:
        """Apply resume decision flow and return whether history should be loaded."""
        mode = self._detect_resume_mode()
        if mode == "new":
            return False

        if mode == "same":
            reply = self._ask_yes_no(
                "断点续打",
                """检测到历史打标数据，是否继续？\n（路径和标签设置未变更）\n如果选择"Yes"，则在历史数据上继续打标；\n如果选择"No"，则删除历史数据并从头开始。""",
                default_button=QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                return True
            try:
                clear_output_file(self.config_obj.output_csv)
            except StorageError as exc:
                self._warn("清理旧结果失败", str(exc))
            return False

        if mode == "same_path_diff_core":
            reply = self._ask_yes_no(
                "配置变更",
                "路径未变化，但标签/展示配置有变更。\n选择“是”覆盖历史结果；选择“否”将自动新建输出CSV。",
                default_button=QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    clear_output_file(self.config_obj.output_csv)
                except StorageError as exc:
                    self._warn("清理旧结果失败", str(exc))
                return False

            self.config_obj.output_csv = make_non_overwrite_path(self.config_obj.output_csv)
            self.path_signature = build_path_signature(self.config_obj)
            self._info("已新建输出路径", f"将使用新输出文件继续：\n{self.config_obj.output_csv}")
            return False

        self._info(
            "路径变更",
            "检测到路径配置变化，将重新加载新数据并在新路径写入结果；旧CSV内容会保留。",
        )
        return False

    def _prepare_data_context(self) -> None:
        """Load CSV/sample context; filter each row lazily when user reaches it."""
        self.raw_rows = load_csv_rows(self.config_obj.input_csv)
        if not self.raw_rows:
            raise DataError("输入CSV为空，无法打标。")

        if self.continue_history:
            saved_indexes = self.resume_state.get("sample_indexes")
            if isinstance(saved_indexes, list) and all(isinstance(x, int) for x in saved_indexes):
                self.sample_indexes = saved_indexes
            else:
                self.sample_indexes = build_sample_indexes(
                    total_rows=len(self.raw_rows),
                    sampling_rate=self.config_obj.sampling_rate,
                    seed=self.config_obj.random_seed,
                )
        else:
            self.sample_indexes = build_sample_indexes(
                total_rows=len(self.raw_rows),
                sampling_rate=self.config_obj.sampling_rate,
                seed=self.config_obj.random_seed,
            )

        if not self.sample_indexes:
            raise DataError("抽样后无可用数据，请调整 sampling_rate（当前可能为0或样本过小）。")

        # Single-loop flow: rows are prepared lazily when user reaches them.
        self.prepared_rows = [None for _ in self.sample_indexes]

        self.display_columns_runtime = validate_display_columns(
            rows=self.raw_rows,
            display_columns=self.config_obj.display_columns,
        )
        if not self.display_columns_runtime:
            self.display_columns_runtime = list(self.raw_rows[0].keys())

        self._init_label_values()

        start_index = int(self.resume_state.get("current_index", 0)) if self.continue_history else 0
        start_index = max(0, min(start_index, max(0, len(self.prepared_rows) - 1)))
        # Keep resume cursor stable for continued sessions; new sessions start from first unfinished row.
        self.current_progress_index = start_index if self.continue_history else self._find_next_unfinished(start_index)
        self.active_index = self.current_progress_index

        self._persist_all()

    def _init_label_values(self) -> None:
        """Initialize in-memory label value table and merge existing output values."""
        self.label_values = [{label_key: "" for label_key in self.config_obj.label_keys} for _ in self.sample_indexes]

        if not self.continue_history:
            return

        existing_rows = read_output_rows(self.config_obj.output_csv)
        if not existing_rows:
            return

        if len(existing_rows) != len(self.prepared_rows):
            self.session_logger.error("历史输出行数与当前样本数不一致，将忽略历史标签并按新会话处理。")
            return

        key_to_name = self.config_obj.label_name_by_key
        for idx, existing in enumerate(existing_rows):
            for key, column_name in key_to_name.items():
                existing_value = str(existing.get(column_name, "")).strip()
                if existing_value:
                    self.label_values[idx][key] = existing_value

    def _ensure_prepared_row(self, row_idx: int) -> Optional[PreparedRow]:
        """Prepare one row lazily on demand, then merge default/preset values."""
        if row_idx < 0 or row_idx >= len(self.prepared_rows):
            return None

        cached = self.prepared_rows[row_idx]
        if cached is not None:
            return cached

        callback = self.session_logger.info if self.config_obj.log_filter_actions else None
        try:
            prepared = prepare_row(
                all_rows=self.raw_rows,
                source_index=self.sample_indexes[row_idx],
                label_keys=self.config_obj.label_keys,
                filter_map=self.filter_map,
                filter_reject_value=self.config_obj.filter_reject_value,
                sample_pos=row_idx + 1,
                filter_log_callback=callback,
            )
        except DataError as exc:
            self._error_and_close("筛选执行失败", str(exc))
            return None
        self.prepared_rows[row_idx] = prepared

        # Fill only empty slots so resumed history values are preserved.
        for key, value in prepared.preset_label_values.items():
            if not self.label_values[row_idx].get(key, "").strip():
                self.label_values[row_idx][key] = value

        return prepared

    def _build_ui(self) -> None:
        """Build fixed 60px top bar + strict 25/50/25 three-column main layout."""
        self.setWindowTitle("打标签辅助工具")
        self._apply_geometry(self.config_obj.ui_geometry)
        self.setMinimumSize(1320, 780)

        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 对应示意图：顶部通栏模块（固定高度60px）
        self.top_bar = QWidget()
        self.top_bar.setObjectName("TopBar")
        self.top_bar.setFixedHeight(60)
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)
        top_layout.setSpacing(10)

        title = QLabel("打标签辅助工具")
        title.setObjectName("AppTitle")
        top_layout.addWidget(title)
        top_layout.addStretch(1)

        theme_label = QLabel("主题")
        theme_label.setObjectName("BodyText")
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("ThemeCombo")
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        top_layout.addWidget(theme_label)
        top_layout.addWidget(self.theme_combo)
        root_layout.addWidget(self.top_bar, 0)

        # 对应示意图：下方主体区（3列固定占比 25% | 50% | 25%）
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(20)
        root_layout.addWidget(body, 1)

        self.left_column = self._build_left_panel()
        self.middle_column = self._build_middle_panel()
        self.right_column = self._build_right_panel()

        # 修改点：锁定三列拉伸比为 1:3:1（左:中:右）
        body_layout.addWidget(self.left_column, 1)
        body_layout.addWidget(self.middle_column, 3)
        body_layout.addWidget(self.right_column, 1)

        # 修改点：锁定左右列宽度范围，防止自动撑宽侵占中间核心区
        self.left_column.setMinimumWidth(260)
        self.left_column.setMaximumWidth(300)
        self.right_column.setMinimumWidth(280)
        self.right_column.setMaximumWidth(320)

        self._add_column_shadow(self.left_column)
        self._add_column_shadow(self.middle_column)
        self._add_column_shadow(self.right_column)

    def _build_left_panel(self) -> QWidget:
        """Build left column with fixed module order: config -> display columns -> recent output rows."""
        # 对应示意图：左列卡片容器（25%）
        panel = QFrame()
        panel.setObjectName("ColumnCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)

        # 对应示意图：左列第1块 展示配置模块
        config_section = QWidget()
        config_layout = QVBoxLayout(config_section)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(8)
        config_layout.addWidget(self._build_module_title("展示配置模块"))

        # 修改点：路径文本仅显示文件名 + tooltip 展示完整绝对路径，防止左列撑宽
        input_label = self._build_path_label("输入数据路径", self.config_obj.input_csv)
        output_label = self._build_path_label("输出文件路径", self.config_obj.output_csv)
        state_label = self._build_path_label("状态文件路径", self.config_obj.state_file)
        filter_summary = ", ".join(self.config_obj.pre_filters.keys()) if self.config_obj.pre_filters else "(无)"
        filter_label = self._build_left_hint_label("筛选函数配置", filter_summary, tooltip=filter_summary)
        sample_text = f"抽样率配置: {self.config_obj.sampling_rate:.2f} | 随机种子: {self.config_obj.random_seed}"
        sample_label = self._build_left_hint_label("抽样参数", sample_text, tooltip=sample_text)
        for item in [input_label, output_label, state_label, filter_label, sample_label]:
            config_layout.addWidget(item)
        config_layout.addStretch(1)
        layout.addWidget(config_section, 1)

        self._add_left_separator(layout)

        # 对应示意图：左列第2块 展示列选择模块
        columns_section = QWidget()
        columns_layout = QVBoxLayout(columns_section)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        columns_layout.setSpacing(8)
        columns_layout.addWidget(self._build_module_title("展示列选择模块"))

        self.columns_scroll = QScrollArea()
        self.columns_scroll.setObjectName("FlatScroll")
        self.columns_scroll.setWidgetResizable(True)
        self.columns_host = QWidget()
        self.columns_host_layout = QVBoxLayout(self.columns_host)
        self.columns_host_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_host_layout.setSpacing(12)
        self.columns_host_layout.addStretch(1)
        self.columns_scroll.setWidget(self.columns_host)
        columns_layout.addWidget(self.columns_scroll, 1)
        layout.addWidget(columns_section, 1)

        self._add_left_separator(layout)

        # 对应示意图：左列第3块 最近标注数据模块
        recent_section = QWidget()
        recent_layout = QVBoxLayout(recent_section)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_layout.setSpacing(8)
        recent_layout.addWidget(self._build_module_title("最近标注数据模块（output.csv）"))

        self.recent_table = QTableWidget(0, 4)
        self.recent_table.setHorizontalHeaderLabels(["样本", "原始行", "状态", "标签摘要"])
        self.recent_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.recent_table.itemSelectionChanged.connect(self._on_recent_select)
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent_table.horizontalHeader().setStretchLastSection(True)
        recent_layout.addWidget(self.recent_table, 1)
        layout.addWidget(recent_section, 1)

        self._render_display_column_checkboxes()
        return panel

    def _build_middle_panel(self) -> QWidget:
        """Build middle column with fixed module order: progress -> data -> logs."""
        # 对应示意图：中间列卡片容器（50%）
        panel = QFrame()
        panel.setObjectName("ColumnCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(20)

        # 对应示意图：中间列第1块 进度展示模块
        progress_section = QWidget()
        progress_layout = QVBoxLayout(progress_section)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(8)
        progress_layout.addWidget(self._build_module_title("进度展示模块"))
        self.progress_label = QLabel("-")
        self.progress_label.setObjectName("BodyText")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        layout.addWidget(progress_section, 1)

        # 对应示意图：中间列第2块 展示数据模块（核心，约70%）
        data_section = QWidget()
        data_layout = QVBoxLayout(data_section)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(8)
        data_layout.addWidget(self._build_module_title("展示数据模块"))
        self.data_scroll = QScrollArea()
        self.data_scroll.setObjectName("FlatScroll")
        self.data_scroll.setWidgetResizable(True)
        self.data_host = QWidget()
        self.data_host_layout = QVBoxLayout(self.data_host)
        self.data_host_layout.setContentsMargins(0, 0, 0, 0)
        self.data_host_layout.setSpacing(10)
        self.data_host_layout.addStretch(1)
        self.data_scroll.setWidget(self.data_host)
        data_layout.addWidget(self.data_scroll, 1)
        layout.addWidget(data_section, 7)

        # 对应示意图：中间列第3块 日志展示模块
        log_section = QWidget()
        log_layout = QVBoxLayout(log_section)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(8)
        log_layout.addWidget(self._build_module_title("日志展示模块"))
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        log_buttons = QHBoxLayout()
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.setObjectName("SecondaryButton")
        self.clear_log_button.clicked.connect(self.log_text.clear)
        self.save_log_button = QPushButton("保存日志")
        self.save_log_button.setObjectName("SecondaryButton")
        self.save_log_button.clicked.connect(self._save_log_snapshot)
        log_buttons.addWidget(self.clear_log_button)
        log_buttons.addWidget(self.save_log_button)
        log_buttons.addStretch(1)
        log_layout.addWidget(self.log_text, 1)
        log_layout.addLayout(log_buttons)
        layout.addWidget(log_section, 2)

        return panel

    def _build_right_panel(self) -> QWidget:
        """Build right column with fixed module order: label inputs -> action buttons."""
        # 对应示意图：右列卡片容器（25%）
        panel = QFrame()
        panel.setObjectName("ColumnCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(20)

        # 对应示意图：右列第1块 标签输入模块（约80%）
        labels_section = QWidget()
        labels_layout = QVBoxLayout(labels_section)
        labels_layout.setContentsMargins(0, 0, 0, 0)
        labels_layout.setSpacing(8)
        labels_layout.addWidget(self._build_module_title("标签输入模块"))
        self.labels_scroll = QScrollArea()
        self.labels_scroll.setObjectName("FlatScroll")
        self.labels_scroll.setWidgetResizable(True)
        self.labels_host = QWidget()
        self.labels_host_layout = QVBoxLayout(self.labels_host)
        self.labels_host_layout.setContentsMargins(0, 0, 0, 0)
        self.labels_host_layout.setSpacing(10)
        self.labels_host_layout.addStretch(1)
        self.labels_scroll.setWidget(self.labels_host)
        labels_layout.addWidget(self.labels_scroll, 1)
        layout.addWidget(labels_section, 8)

        # 对应示意图：右列第2块 功能按钮模块
        action_section = QWidget()
        action_layout = QVBoxLayout(action_section)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addWidget(self._build_module_title("功能按钮模块"))

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.btn_next = QPushButton("保存并下一条 (Enter)")
        self.btn_next.setProperty("full_text", "保存并下一条 (Enter)")
        self.btn_next.setObjectName("PrimaryButton")
        self.btn_next.clicked.connect(self._on_save_and_next)
        grid.addWidget(self.btn_next, 0, 0, 1, 2)

        self.btn_edit_history = QPushButton("修改历史")
        self.btn_edit_history.setProperty("full_text", "修改历史")
        self.btn_edit_history.setObjectName("SecondaryButton")
        self.btn_edit_history.clicked.connect(self._focus_recent_table)
        grid.addWidget(self.btn_edit_history, 1, 0)

        self.btn_manual_save = QPushButton("手动保存 (Ctrl+S)")
        self.btn_manual_save.setProperty("full_text", "手动保存 (Ctrl+S)")
        self.btn_manual_save.setObjectName("SecondaryButton")
        self.btn_manual_save.clicked.connect(self._on_manual_save)
        grid.addWidget(self.btn_manual_save, 1, 1)

        self.btn_clear = QPushButton("清空输入 (Esc)")
        self.btn_clear.setProperty("full_text", "清空输入 (Esc)")
        self.btn_clear.setObjectName("SecondaryButton")
        self.btn_clear.clicked.connect(self._clear_active_inputs)
        grid.addWidget(self.btn_clear, 2, 0)

        self.btn_exit = QPushButton("退出")
        self.btn_exit.setProperty("full_text", "退出")
        self.btn_exit.setObjectName("SecondaryButton")
        self.btn_exit.clicked.connect(self.close)
        grid.addWidget(self.btn_exit, 2, 1)
        action_layout.addLayout(grid)

        self.mode_label = QLabel("当前模式: 进度打标")
        self.mode_label.setObjectName("HintLabel")
        self.feedback_label = QLabel("")
        self.feedback_label.setObjectName("HintLabel")
        action_layout.addWidget(self.mode_label)
        action_layout.addWidget(self.feedback_label)
        action_layout.addStretch(1)

        layout.addWidget(action_section, 2)
        return panel

    def _build_module_title(self, text: str) -> QLabel:
        """Create module title label with unified style id."""
        label = QLabel(text)
        label.setObjectName("ModuleTitle")
        return label

    def _build_path_label(self, prefix: str, path_value: str) -> QLabel:
        """Build compact path label: show filename only and keep full path in tooltip."""
        full_path = os.path.abspath(path_value)
        file_name = os.path.basename(full_path) or full_path
        text = f"{prefix}: {file_name}"
        return self._build_left_hint_label(prefix, text, tooltip=full_path)

    def _build_left_hint_label(self, _prefix: str, text: str, tooltip: str = "") -> QLabel:
        """Build single-line compact hint label used by narrow left column."""
        label = QLabel(self._elide_text(text, 38))
        label.setObjectName("HintLabel")
        label.setWordWrap(False)
        if tooltip:
            label.setToolTip(tooltip)
        else:
            label.setToolTip(text)
        return label

    def _elide_text(self, text: str, max_chars: int = 38) -> str:
        """Simple text elide helper for narrow columns."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    def _add_left_separator(self, layout: QVBoxLayout) -> None:
        """Add 1px divider + 20px spacing for left column section separation."""
        divider = QFrame()
        divider.setObjectName("SectionDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(20)

    def _add_column_shadow(self, widget: QWidget) -> None:
        """Apply subtle shadow only to three top-level column cards."""
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(20)
        effect.setOffset(0, 4)
        effect.setColor(QColor(15, 23, 42, 45))
        widget.setGraphicsEffect(effect)

    def _render_display_column_checkboxes(self) -> None:
        """Render selectable column checkbox list in left panel."""
        while self.columns_host_layout.count() > 1:
            item = self.columns_host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.column_checkboxes: Dict[str, QCheckBox] = {}
        if not self.raw_rows:
            return

        all_columns = list(self.raw_rows[0].keys())
        selected = set(self.display_columns_runtime)

        for col in all_columns:
            cb = QCheckBox(col)
            cb.setChecked(col in selected)
            cb.stateChanged.connect(self._on_display_columns_changed)
            self.column_checkboxes[col] = cb
            self.columns_host_layout.insertWidget(self.columns_host_layout.count() - 1, cb)

    def _apply_geometry(self, geometry: str) -> None:
        """Apply WxH geometry string with robust fallback."""
        try:
            width_str, height_str = geometry.lower().split("x", 1)
            self.resize(int(width_str), int(height_str))
        except Exception:
            self.resize(1400, 900)

    def _apply_window_icon(self) -> None:
        """Apply app/window icon from config or fallback file if available."""
        icon_candidates: List[str] = []
        if self.config_obj.ui_icon:
            configured = self.config_obj.ui_icon
            resolved = configured if os.path.isabs(configured) else os.path.abspath(os.path.join(self.config_dir, configured))
            icon_candidates.append(resolved)
            # Windows taskbar/titlebar rendering is more stable with .ico if sibling exists.
            base, ext = os.path.splitext(resolved)
            if ext.lower() != ".ico":
                sibling_ico = f"{base}.ico"
                if os.path.isfile(sibling_ico):
                    icon_candidates.insert(0, sibling_ico)

        fallback = os.path.join(self.config_dir, "image.png")
        if not icon_candidates and os.path.isfile(fallback):
            icon_candidates.append(fallback)

        for path_value in icon_candidates:
            if not path_value or not os.path.isfile(path_value):
                continue
            icon = self._build_icon_from_file(path_value)
            if icon.isNull():
                continue
            self._loaded_window_icon = icon
            self.setWindowIcon(icon)
            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(icon)
            self.session_logger.info(f"Window icon loaded: {path_value}")
            return

    def _build_icon_from_file(self, path_value: str) -> QIcon:
        """Build robust multi-size QIcon from file for stable Windows title/taskbar rendering."""
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

    def _bind_shortcuts(self) -> None:
        """Bind high-frequency operations shortcuts on main window scope."""
        save_next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        save_next_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_next_shortcut.activated.connect(self._on_save_and_next)

        esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc_shortcut.activated.connect(self._clear_focused_or_active)

        manual_save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        manual_save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        manual_save_shortcut.activated.connect(self._on_manual_save)

        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def _apply_theme(self, mode: str) -> None:
        """Apply Light/Dark global QSS and refresh widgets."""
        self.current_theme_mode = mode if mode in {"Light", "Dark"} else "Dark"
        colors = self.DARK_COLORS if self.current_theme_mode == "Dark" else self.COLORS
        self.active_colors = dict(colors)

        self._refresh_dynamic_typography(force=True)
        self._refresh_recent_table()
        self._render_data_cards()

    def _asset_qss_url(self, file_name: str) -> str:
        """Return escaped absolute path used by QSS `url(...)`."""
        raw = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", file_name))
        return raw.replace("\\", "/")

    def _build_global_qss(self, colors: Dict[str, str]) -> str:
        """Build centralized global stylesheet string for all visual rules."""
        base_font = "Microsoft YaHei UI" if os.name == "nt" else "Arial"
        base_size = self._font_px("base")
        title_size = self._font_px("title")
        module_size = self._font_px("module")
        field_size = self._font_px("field")
        body_size = self._font_px("body")
        hint_size = self._font_px("hint")
        button_size = self._font_px("button")
        header_size = self._font_px("header")
        is_dark = self.current_theme_mode == "Dark"

        checkbox_text = "#F1F5F9" if is_dark else "#1E293B"
        checkbox_border = "#475569" if is_dark else "#94A3B8"
        checkbox_bg = "#1E293B" if is_dark else "#FFFFFF"
        checkbox_hover_bg = "#223246" if is_dark else "#F8FAFC"

        combo_arrow_icon = self._asset_qss_url("combo_arrow_light.svg" if is_dark else "combo_arrow_dark.svg")
        checkbox_check_icon = self._asset_qss_url("checkbox_check_white.svg")

        theme_combo_bg = "#1E293B" if is_dark else "#FFFFFF"
        theme_combo_border = "#334155" if is_dark else "#E2E8F0"
        theme_combo_text = "#F1F5F9" if is_dark else "#1E293B"
        theme_combo_hover_bg = "#243447" if is_dark else "#F8FAFC"
        theme_menu_bg = "#1E293B" if is_dark else "#FFFFFF"
        theme_menu_border = "#334155" if is_dark else "#E2E8F0"
        return f"""
        QMainWindow, QWidget {{
            background: {colors['bg']};
            color: {colors['text']};
            font-family: '{base_font}';
            font-size: {base_size}px;
        }}

        QWidget#TopBar {{
            background: {colors['bg']};
            border: none;
            border-bottom: 1px solid {colors['divider']};
        }}

        QFrame#ColumnCard {{
            background: {colors['panel']};
            border: none;
            border-radius: 12px;
        }}

        QFrame#SectionDivider {{
            background: {colors['divider']};
            border: none;
        }}

        QLabel#AppTitle {{
            color: {colors['primary']};
            font-size: {title_size}px;
            font-weight: 700;
        }}

        QLabel#ModuleTitle {{
            color: {colors['text']};
            font-size: {module_size}px;
            font-weight: 700;
        }}

        QLabel#FieldTitle {{
            color: {colors['primary']};
            font-size: {field_size}px;
            font-weight: 700;
        }}

        QLabel#BodyText {{
            color: {colors['text']};
            font-size: {body_size}px;
            font-weight: 400;
        }}

        QLabel#HintLabel {{
            color: {colors['muted']};
            font-size: {hint_size}px;
            font-weight: 400;
        }}

        QLineEdit, QTextEdit, QPlainTextEdit {{
            background: {colors['input']};
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 6px;
            selection-background-color: {colors['accent']};
        }}

        QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{
            border: 1px solid {colors['border']};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border: 1px solid {colors['primary']};
        }}

        QComboBox {{
            background: {colors['input']};
            color: {colors['text']};
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 6px 28px 6px 8px;
            selection-background-color: {colors['accent']};
        }}
        QComboBox:hover {{
            border: 1px solid {colors['primary']};
        }}
        QComboBox:focus {{
            border: 1px solid {colors['primary']};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 24px;
            border: none;
            background: transparent;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
        }}
        QComboBox::down-arrow {{
            image: url("{combo_arrow_icon}");
            width: 12px;
            height: 12px;
        }}
        QComboBox QAbstractItemView {{
            background: {colors['panel']};
            color: {colors['text']};
            border: 1px solid {colors['border']};
            border-radius: 8px;
            outline: 0;
            selection-background-color: {colors['primary']};
            selection-color: #FFFFFF;
            padding: 4px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {colors['primary']};
            color: #FFFFFF;
        }}

        QComboBox#ThemeCombo {{
            background: {theme_combo_bg};
            border: 1px solid {theme_combo_border};
            color: {theme_combo_text};
            border-radius: 8px;
            padding: 6px 30px 6px 10px;
        }}
        QComboBox#ThemeCombo:hover {{
            border: 1px solid {colors['primary']};
            color: {colors['primary']};
            background: {theme_combo_hover_bg};
        }}
        QComboBox#ThemeCombo::drop-down {{
            width: 26px;
        }}
        QComboBox#ThemeCombo::down-arrow {{
            image: url("{combo_arrow_icon}");
            width: 12px;
            height: 12px;
        }}
        QComboBox#ThemeCombo QAbstractItemView {{
            background: {theme_menu_bg};
            color: {theme_combo_text};
            border: 1px solid {theme_menu_border};
            border-radius: 8px;
            selection-background-color: {colors['primary']};
            selection-color: #FFFFFF;
            padding: 4px;
            outline: 0;
        }}
        QComboBox#ThemeCombo QAbstractItemView::item:hover {{
            background: {colors['primary']};
            color: #FFFFFF;
        }}

        QCheckBox {{
            color: {checkbox_text};
            font-size: {body_size}px;
            spacing: 8px;
            padding-top: 2px;
            padding-bottom: 2px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid {checkbox_border};
            background: {checkbox_bg};
        }}
        QCheckBox::indicator:unchecked:hover {{
            border: 1px solid {colors['primary']};
            background: {checkbox_hover_bg};
        }}
        QCheckBox::indicator:checked {{
            border: 1px solid {colors['primary']};
            background: {colors['primary']};
            image: url("{checkbox_check_icon}");
        }}
        QCheckBox::indicator:checked:hover {{
            border: 1px solid {colors['primary']};
            background: #2563EB;
            image: url("{checkbox_check_icon}");
        }}

        QScrollArea#FlatScroll {{
            border: none;
            background: transparent;
        }}
        QScrollArea#FlatScroll > QWidget > QWidget {{
            background: transparent;
            border: none;
        }}

        QPushButton {{
            border-radius: 8px;
            padding: 8px 10px;
            font-size: {button_size}px;
            font-weight: 600;
            border: none;
        }}

        QPushButton#PrimaryButton {{
            background: {colors['primary']};
            color: #FFFFFF;
        }}
        QPushButton#PrimaryButton:hover {{
            background: #2563EB;
        }}

        QPushButton#SecondaryButton {{
            background: {colors['secondary']};
            color: {colors['secondary_text']};
        }}
        QPushButton#SecondaryButton:hover {{
            background: {colors['border']};
        }}

        QTableWidget {{
            background: {colors['input']};
            border: none;
            border-radius: 8px;
            gridline-color: transparent;
            alternate-background-color: {colors['panel']};
        }}
        QHeaderView::section {{
            background: {colors['primary']};
            color: #FFFFFF;
            font-size: {header_size}px;
            font-weight: 700;
            border: none;
            padding: 6px;
        }}

        QProgressBar {{
            background: {colors['input']};
            border: none;
            border-radius: 8px;
            text-align: center;
            min-height: 18px;
        }}
        QProgressBar::chunk {{
            background: {colors['primary']};
            border-radius: 8px;
        }}
        """

    def _build_scrollbar_qss(self, is_dark: bool) -> str:
        """Load standalone scrollbar QSS and fill mode-specific colors."""
        qss_path = os.path.join(os.path.dirname(__file__), "scrollbar.qss")
        try:
            with open(qss_path, "r", encoding="utf-8") as file:
                template = file.read()
        except OSError:
            template = ""

        track = "#1E293B" if is_dark else "#F1F5F9"
        handle = "#475569" if is_dark else "#94A3B8"
        return (
            template.replace("__TRACK__", track)
            .replace("__HANDLE__", handle)
            .replace("__HOVER__", "#3B82F6")
        )

    def _update_font_scale(self) -> None:
        """Update runtime font scale based on current window size."""
        width_ratio = self.width() / self.BASE_VIEW_WIDTH if self.width() > 0 else 1.0
        height_ratio = self.height() / self.BASE_VIEW_HEIGHT if self.height() > 0 else 1.0
        raw_scale = min(width_ratio, height_ratio)
        self.font_scale = max(0.85, min(1.35, raw_scale))

    def _refresh_dynamic_typography(self, force: bool = False) -> None:
        """Rebuild typography styles only when scale meaningfully changes."""
        self._update_font_scale()
        if not force and abs(self.font_scale - self._last_applied_font_scale) < 0.02:
            self._fit_action_button_texts()
            return

        self._apply_runtime_styles()
        self._fit_action_button_texts()
        self._last_applied_font_scale = self.font_scale

    def _font_px(self, key: str) -> int:
        """Resolve scaled font pixel size with safe lower bound."""
        base = self.BASE_FONT_PX.get(key, self.BASE_FONT_PX["base"])
        return max(9, int(round(base * self.font_scale)))

    def _apply_runtime_styles(self) -> None:
        """Apply stylesheet using current colors and current font scale."""
        colors = self.active_colors
        self.setStyleSheet(
            self._build_global_qss(colors)
            + "\n"
            + self._build_scrollbar_qss(is_dark=(self.current_theme_mode == "Dark"))
        )

        if hasattr(self, "recent_table"):
            self.recent_table.setAlternatingRowColors(True)
            self.recent_table.setShowGrid(False)
            self.recent_table.setStyleSheet(
                f"""
                QTableWidget::item:selected {{
                    background: {colors['accent']};
                    color: {colors['text']};
                }}
                """
            )

    def _set_primary_action_text(self, text: str) -> None:
        """Set full text for primary action button and refit for current width."""
        if not hasattr(self, "btn_next"):
            return
        self.btn_next.setProperty("full_text", text)
        self.btn_next.setText(text)
        self._fit_action_button_texts()

    def _fit_action_button_texts(self) -> None:
        """Fit action-button texts to current width via font shrink and elide."""
        for name in ["btn_next", "btn_edit_history", "btn_manual_save", "btn_clear", "btn_exit"]:
            button = getattr(self, name, None)
            if isinstance(button, QPushButton):
                self._fit_button_text(button)

    def _fit_button_text(self, button: QPushButton) -> None:
        """Fit one button text to available width."""
        full_text = str(button.property("full_text") or button.text())
        if not full_text:
            return

        target_px = self._font_px("button")
        min_px = max(9, target_px - 3)
        available = max(24, button.width() - 20)

        for size in range(target_px, min_px - 1, -1):
            font = QFont(button.font())
            font.setPixelSize(size)
            metrics = QFontMetrics(font)
            if metrics.horizontalAdvance(full_text) <= available:
                button.setStyleSheet(f"font-size: {size}px;")
                button.setText(full_text)
                return

        fallback = QFont(button.font())
        fallback.setPixelSize(min_px)
        metrics = QFontMetrics(fallback)
        button.setStyleSheet(f"font-size: {min_px}px;")
        button.setText(metrics.elidedText(full_text, Qt.TextElideMode.ElideRight, available))

    def _show_progress_row(self) -> None:
        """Switch to current progress row and leave edit mode."""
        self.editing_index = None
        self.active_index = self.current_progress_index
        self.mode_label.setText("当前模式: 进度打标")
        self._set_primary_action_text("保存并下一条 (Enter)")
        self.feedback_label.setText("")
        self._render_active_row()

    def _focus_recent_table(self) -> None:
        """Focus recent table so user can choose historical row to edit quickly."""
        self.recent_table.setFocus()
        if self.recent_table.rowCount() > 0 and self.recent_table.currentRow() < 0:
            self.recent_table.selectRow(self.recent_table.rowCount() - 1)

    def _render_active_row(self) -> None:
        """Render current active row data and label inputs."""
        if not self.prepared_rows:
            return
        if self._ensure_prepared_row(self.active_index) is None:
            return

        self._render_data_cards()
        self._build_label_inputs_for_row(self.active_index)
        self.active_row_start_time = time.time()
        self._update_progress_display()

    def _render_data_cards(self) -> None:
        """Render field cards to avoid large plain-text blocks."""
        while self.data_host_layout.count() > 1:
            item = self.data_host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self.prepared_rows:
            empty_label = QLabel("📄 暂无可展示数据")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.data_host_layout.insertWidget(0, empty_label)
            return

        prepared = self._ensure_prepared_row(self.active_index)
        if prepared is None:
            return

        meta = QLabel(f"样本序号: {self.active_index + 1} | 原始行号: {prepared.source_index + 1}")
        meta.setObjectName("HintLabel")
        self.data_host_layout.insertWidget(0, meta)

        insert_index = 1
        for column in self.display_columns_runtime:
            value = str(prepared.data.get(column, ""))
            note = self.config_obj.column_notes.get(column, "")

            card = QWidget()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(6)

            title = QLabel(f"{column}:")
            title.setObjectName("FieldTitle")
            card_layout.addWidget(title)

            is_long = column.lower() in {"content", "text", "body"} or len(value) > 120 or "\n" in value
            if is_long:
                widget = QPlainTextEdit()
                widget.setReadOnly(True)
                widget.setPlainText(value)
                widget.setMinimumHeight(190 if column.lower() in {"content", "text", "body"} else 96)
            else:
                widget = QLineEdit(value)
                widget.setReadOnly(True)

            card_layout.addWidget(widget)

            if note:
                note_label = QLabel(note)
                note_label.setObjectName("HintLabel")
                card_layout.addWidget(note_label)

            divider = QFrame()
            divider.setObjectName("SectionDivider")
            divider.setFixedHeight(1)
            card_layout.addWidget(divider)

            self.data_host_layout.insertWidget(insert_index, card)
            insert_index += 1

    def _build_label_inputs_for_row(self, row_idx: int) -> None:
        """Build label inputs grouped by type with note hints beside controls."""
        while self.labels_host_layout.count() > 1:
            item = self.labels_host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.single_combos.clear()
        self.single_custom_entries.clear()
        self.multi_checkboxes.clear()
        self.multi_custom_entries.clear()
        self.text_edits.clear()
        self.validation_labels.clear()
        self.validation_widgets.clear()

        prepared = self._ensure_prepared_row(row_idx)
        if prepared is None:
            return
        allowed = set(prepared.allowed_label_keys)

        grouped: Dict[str, List[LabelDefinition]] = {"single_choice": [], "multi_choice": [], "text": []}
        sorted_labels = sorted(
            self.config_obj.labels,
            key=lambda item: (0 if item.required else 1, item.input_type, item.name.lower()),
        )
        for label in sorted_labels:
            grouped.setdefault(label.input_type, []).append(label)

        group_titles = {
            "single_choice": "单选标签",
            "multi_choice": "多选标签",
            "text": "文本标签",
        }

        for input_type in ["single_choice", "multi_choice", "text"]:
            labels = grouped.get(input_type, [])
            if not labels:
                continue

            section = QWidget()
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(8)
            section_layout.addWidget(self._build_module_title(group_titles[input_type]))

            for label in labels:
                item = self._build_label_item(label=label, row_idx=row_idx, allowed=allowed)
                section_layout.addWidget(item)

            divider = QFrame()
            divider.setObjectName("SectionDivider")
            divider.setFixedHeight(1)
            section_layout.addWidget(divider)
            self.labels_host_layout.insertWidget(self.labels_host_layout.count() - 1, section)

    def _build_label_item(self, label: LabelDefinition, row_idx: int, allowed: set) -> QWidget:
        """Build one label item and return card widget."""
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(6)

        title_text = f"{label.name} ({label.input_type})"
        if label.required and label.key in allowed:
            title_text = f"* {title_text}"

        title = QLabel(title_text)
        title.setObjectName("FieldTitle")
        card_layout.addWidget(title)

        note = self.config_obj.label_notes.get(label.key, "")
        if note:
            note_label = QLabel(note)
            note_label.setObjectName("HintLabel")
            card_layout.addWidget(note_label)

        existing = self.label_values[row_idx].get(label.key, "")

        if label.key not in allowed:
            skip_label = QLabel(f"该标签由前置筛选自动赋值: {existing}")
            skip_label.setObjectName("HintLabel")
            card_layout.addWidget(skip_label)
            return card

        self.validation_widgets[label.key] = []

        if label.input_type == "single_choice":
            self._build_single_choice_widget(card_layout, label, existing)
        elif label.input_type == "multi_choice":
            self._build_multi_choice_widget(card_layout, label, existing)
        else:
            self._build_text_widget(card_layout, label, existing)

        err = QLabel("")
        err.setObjectName("HintLabel")
        card_layout.addWidget(err)
        self.validation_labels[label.key] = err
        return card

    def _build_single_choice_widget(self, layout: QVBoxLayout, label: LabelDefinition, existing_value: str) -> None:
        """Build single-choice input controls."""
        combo = QComboBox()
        combo.addItem("__请选择__")
        for option in label.options:
            combo.addItem(option)
        if label.allow_custom:
            combo.addItem("__自定义__")

        if existing_value in label.options:
            combo.setCurrentText(existing_value)
        elif existing_value and label.allow_custom:
            combo.setCurrentText("__自定义__")
        else:
            combo.setCurrentText("__请选择__")

        combo.currentTextChanged.connect(lambda _v, key=label.key: self._validate_label_inline(key))
        layout.addWidget(combo)
        self.single_combos[label.key] = combo
        self.validation_widgets[label.key].append(combo)

        if label.allow_custom:
            custom_entry = QLineEdit()
            custom_entry.setPlaceholderText("自定义输入")
            if existing_value and existing_value not in label.options:
                custom_entry.setText(existing_value)
            custom_entry.textChanged.connect(lambda _v, key=label.key: self._validate_label_inline(key))
            layout.addWidget(custom_entry)
            self.single_custom_entries[label.key] = custom_entry
            self.validation_widgets[label.key].append(custom_entry)

    def _build_multi_choice_widget(self, layout: QVBoxLayout, label: LabelDefinition, existing_value: str) -> None:
        """Build multi-choice checkboxes and optional custom field."""
        parsed = [item.strip() for item in existing_value.split(";") if item.strip()]
        parsed_set = set(parsed)

        option_map: Dict[str, QCheckBox] = {}
        for option in label.options:
            cb = QCheckBox(option)
            cb.setChecked(option in parsed_set)
            cb.stateChanged.connect(lambda _s, key=label.key: self._validate_label_inline(key))
            layout.addWidget(cb)
            option_map[option] = cb
        self.multi_checkboxes[label.key] = option_map

        if label.allow_custom:
            custom_entry = QLineEdit()
            custom_entry.setPlaceholderText("自定义输入（多个值用英文逗号分隔）")
            custom_items = [item for item in parsed if item not in label.options]
            if custom_items:
                custom_entry.setText(", ".join(custom_items))
            custom_entry.textChanged.connect(lambda _v, key=label.key: self._validate_label_inline(key))
            layout.addWidget(custom_entry)
            self.multi_custom_entries[label.key] = custom_entry
            self.validation_widgets[label.key].append(custom_entry)

    def _build_text_widget(self, layout: QVBoxLayout, label: LabelDefinition, existing_value: str) -> None:
        """Build text input control for free-form labels."""
        editor = QTextEdit()
        editor.setMinimumHeight(92 if label.required else 76)
        if existing_value:
            editor.setPlainText(existing_value)
        editor.textChanged.connect(lambda key=label.key: self._validate_label_inline(key))
        layout.addWidget(editor)
        self.text_edits[label.key] = editor
        self.validation_widgets[label.key].append(editor)

    def _peek_label_value(self, label: LabelDefinition) -> str:
        """Peek one label value from current UI widgets without raising validation error."""
        if label.input_type == "single_choice":
            combo = self.single_combos[label.key]
            selected = combo.currentText().strip()
            if selected == "__请选择__":
                return ""
            if selected == "__自定义__":
                custom = self.single_custom_entries.get(label.key)
                return custom.text().strip() if custom else ""
            return selected

        if label.input_type == "multi_choice":
            selected: List[str] = [
                option for option, cb in self.multi_checkboxes.get(label.key, {}).items() if cb.isChecked()
            ]
            if label.allow_custom and label.key in self.multi_custom_entries:
                raw = self.multi_custom_entries[label.key].text().strip()
                if raw:
                    selected.extend([item.strip() for item in raw.split(",") if item.strip()])
            return "; ".join(dict.fromkeys(selected))

        if label.key in self.text_edits:
            return self.text_edits[label.key].toPlainText().strip()
        return ""

    def _validate_label_inline(self, label_key: str) -> bool:
        """Validate one label value and refresh inline validation hint."""
        if not self.prepared_rows:
            return True

        prepared = self._ensure_prepared_row(self.active_index)
        if prepared is None:
            return True
        label = self.config_obj.label_by_key[label_key]
        allowed = set(prepared.allowed_label_keys)
        if label.key not in allowed:
            self._set_validation_state(label.key, is_valid=True, message="")
            return True

        value = self._peek_label_value(label)
        if label.required and not value:
            self._set_validation_state(label.key, is_valid=False, message="⚠ 必填项不能为空")
            return False

        self._set_validation_state(label.key, is_valid=True, message="")
        return True

    def _set_validation_state(self, label_key: str, is_valid: bool, message: str) -> None:
        """Update validation text and border style for one label."""
        if label_key in self.validation_labels:
            validation_size = self._font_px("validation")
            self.validation_labels[label_key].setText(message if not is_valid else "")
            self.validation_labels[label_key].setStyleSheet(
                f"color: {self.active_colors['warning']}; font-size: {validation_size}px;" if not is_valid else ""
            )

        for widget in self.validation_widgets.get(label_key, []):
            if not is_valid:
                widget.setStyleSheet(
                    f"background: {self.active_colors['input']}; border: 1px solid {self.active_colors['warning']}; border-radius: 8px; padding: 6px;"
                )
            else:
                widget.setStyleSheet("")

    def _collect_inputs_for_row(self, row_idx: int) -> Dict[str, str]:
        """Collect and validate all active input controls for row index."""
        prepared = self._ensure_prepared_row(row_idx)
        if prepared is None:
            raise ValueError("当前样本准备失败，无法保存。")
        allowed = set(prepared.allowed_label_keys)
        output: Dict[str, str] = {}
        missing: List[str] = []

        for label in self.config_obj.labels:
            if label.key not in allowed:
                output[label.key] = self.label_values[row_idx].get(label.key, self.config_obj.filter_reject_value)
                self._set_validation_state(label.key, is_valid=True, message="")
                continue

            value = self._peek_label_value(label)
            if label.required and not value:
                missing.append(label.name)
                self._set_validation_state(label.key, is_valid=False, message="⚠ 必填项不能为空")
            else:
                self._set_validation_state(label.key, is_valid=True, message="")
            output[label.key] = value

        if missing:
            raise ValueError(f"标签 '{'、'.join(missing)}' 为必填，请完成输入。")

        return output

    def _clear_focused_or_active(self) -> None:
        """Clear focused input widget first; otherwise clear all active input controls."""
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit):
            focus.clear()
            return
        if isinstance(focus, QTextEdit):
            focus.clear()
            return
        if isinstance(focus, QPlainTextEdit):
            focus.clear()
            return
        self._clear_active_inputs()

    def _clear_active_inputs(self) -> None:
        """Clear all editable inputs for current active row."""
        if not self.prepared_rows:
            return

        prepared = self._ensure_prepared_row(self.active_index)
        if prepared is None:
            return
        allowed = set(prepared.allowed_label_keys)
        for label in self.config_obj.labels:
            if label.key not in allowed:
                continue

            if label.input_type == "single_choice":
                if label.key in self.single_combos:
                    self.single_combos[label.key].setCurrentText("__请选择__")
                if label.allow_custom and label.key in self.single_custom_entries:
                    self.single_custom_entries[label.key].clear()
            elif label.input_type == "multi_choice":
                for cb in self.multi_checkboxes.get(label.key, {}).values():
                    cb.setChecked(False)
                if label.allow_custom and label.key in self.multi_custom_entries:
                    self.multi_custom_entries[label.key].clear()
            else:
                if label.key in self.text_edits:
                    self.text_edits[label.key].clear()

            self._set_validation_state(label.key, is_valid=True, message="")

        self.feedback_label.setText("已清空当前输入")

    def _on_save_and_next(self) -> None:
        """Save current row and move to next unfinished row."""
        self._save_active_row(move_next=True)

    def _save_active_row(self, move_next: bool) -> None:
        """Save current active row with optional navigation to next row."""
        if not self.prepared_rows:
            return

        target = self.active_index
        prepared = self._ensure_prepared_row(target)
        if prepared is None:
            self.feedback_label.setText("当前样本准备失败，无法保存")
            return
        try:
            values = self._collect_inputs_for_row(target)
        except ValueError as exc:
            self._warn("输入校验失败", str(exc))
            self.feedback_label.setText(str(exc))
            return

        self.label_values[target].update(values)

        duration = time.time() - self.active_row_start_time
        if target == self.current_progress_index and prepared.allowed_label_keys:
            self.session_logger.record_item_duration(duration)

        if not self._persist_all():
            self.feedback_label.setText("保存失败，请查看错误信息")
            return

        self.session_logger.info(f"Saved row sample={target + 1}, source_index={prepared.source_index}")
        self._refresh_recent_table()

        if self.editing_index is not None:
            self.feedback_label.setText("历史样本修改已保存 ✓")
            if move_next:
                self._show_progress_row()
            else:
                self._render_active_row()
            return

        self.feedback_label.setText("保存成功 ✓")

        if move_next:
            self.current_progress_index = self._find_next_unfinished(self.current_progress_index + 1)
            self.active_index = self.current_progress_index
            self._render_active_row()

    def _on_manual_save(self) -> None:
        """Manual save shortcut for current row without navigation."""
        self._save_active_row(move_next=False)

    def _persist_all(self) -> bool:
        """Persist full output CSV and resume state."""
        try:
            write_output_rows(
                output_csv=self.config_obj.output_csv,
                rows=self._build_output_rows(),
                columns=self._build_output_columns(),
            )
            write_state(
                self.config_obj.state_file,
                {
                    "path_signature": self.path_signature,
                    "core_signature": self.core_signature,
                    "sample_indexes": self.sample_indexes,
                    "current_index": int(self.current_progress_index),
                    "updated_at": datetime.now().isoformat(),
                },
            )
            return True
        except StorageError as exc:
            self.session_logger.error(str(exc))
            self._error("保存失败", str(exc))
            return False

    def _build_output_rows(self) -> List[Dict[str, Any]]:
        """Merge original row data and label values for final output CSV rows."""
        key_to_name = self.config_obj.label_name_by_key
        rows: List[Dict[str, Any]] = []
        for idx, source_index in enumerate(self.sample_indexes):
            base_row = self.raw_rows[source_index] if 0 <= source_index < len(self.raw_rows) else {}
            combined = dict(base_row)
            for key, value in self.label_values[idx].items():
                combined[key_to_name[key]] = value
            rows.append(combined)
        return rows

    def _build_output_columns(self) -> List[str]:
        """Output columns = original columns + label columns."""
        original_columns = list(self.raw_rows[0].keys()) if self.raw_rows else []
        label_columns = [label.name for label in self.config_obj.labels]
        return original_columns + label_columns

    def _row_complete(self, row_idx: int) -> bool:
        """Return True when all required allowed labels in row are filled."""
        prepared = self.prepared_rows[row_idx]
        if prepared is None:
            return False

        allowed = set(prepared.allowed_label_keys)
        if not allowed:
            return True

        by_key = self.config_obj.label_by_key
        values = self.label_values[row_idx]
        for key in allowed:
            if by_key[key].required and not values.get(key, "").strip():
                return False
        return True

    def _find_next_unfinished(self, start: int) -> int:
        """Find first unfinished row index from start; fallback to last row."""
        if not self.prepared_rows:
            return 0

        for idx in range(max(0, start), len(self.prepared_rows)):
            if not self._row_complete(idx):
                return idx

        self._info("完成", "当前样本已全部完成打标，可继续检查历史并修改。")
        return len(self.prepared_rows) - 1

    def _refresh_recent_table(self) -> None:
        """Refresh recent completed rows table and keep row-index metadata."""
        self.recent_table.setRowCount(0)

        completed = [idx for idx in range(len(self.prepared_rows)) if self._row_complete(idx)]
        recent = completed[-10:]

        for row_pos, idx in enumerate(recent):
            prepared = self.prepared_rows[idx]
            if prepared is None:
                continue
            status = "筛选跳过" if not prepared.allowed_label_keys else "已打标"
            summary = " | ".join(
                [
                    f"{label.name}={self.label_values[idx].get(label.key, '')}"
                    for label in self.config_obj.labels
                    if self.label_values[idx].get(label.key, "")
                ]
            )

            self.recent_table.insertRow(row_pos)
            sample_item = QTableWidgetItem(str(idx + 1))
            source_item = QTableWidgetItem(str(prepared.source_index + 1))
            status_item = QTableWidgetItem(status)
            summary_item = QTableWidgetItem(summary[:320])

            sample_item.setData(Qt.ItemDataRole.UserRole, idx)
            self.recent_table.setItem(row_pos, 0, sample_item)
            self.recent_table.setItem(row_pos, 1, source_item)
            self.recent_table.setItem(row_pos, 2, status_item)
            self.recent_table.setItem(row_pos, 3, summary_item)

        self._update_progress_display()

    def _on_recent_select(self) -> None:
        """Switch to history edit mode when user selects recent row."""
        selected = self.recent_table.selectedItems()
        if not selected:
            return

        first = self.recent_table.item(selected[0].row(), 0)
        if first is None:
            return

        row_idx = first.data(Qt.ItemDataRole.UserRole)
        if row_idx is None:
            return

        self.editing_index = int(row_idx)
        self.active_index = int(row_idx)
        self.mode_label.setText(f"当前模式: 历史编辑 (样本 {self.active_index + 1})")
        self._set_primary_action_text("保存并回到当前数据 (Enter)")
        self.feedback_label.setText("已进入历史编辑模式")
        self._render_active_row()

    def _update_progress_display(self) -> None:
        """Update progress text and progress bar in middle top area."""
        total = len(self.prepared_rows)
        completed = sum(1 for idx in range(total) if self._row_complete(idx))
        ratio = (completed / total) if total else 0.0

        self.progress_label.setText(
            f"第{self.active_index + 1}条/共{total}条（完成 {ratio * 100:.1f}%） | 已完成 {completed}/{total}"
        )
        self.progress_bar.setValue(int(ratio * 100))

    def _on_display_columns_changed(self) -> None:
        """Apply display-column check state immediately and re-render data cards."""
        selected = [
            col for col, cb in self.column_checkboxes.items() if cb.isChecked()
        ]

        if not selected:
            self.feedback_label.setText("至少保留一个展示列")
            sender = self.sender()
            if isinstance(sender, QCheckBox):
                sender.blockSignals(True)
                sender.setChecked(True)
                sender.blockSignals(False)
            return

        self.display_columns_runtime = validate_display_columns(self.raw_rows, selected)
        if not self.display_columns_runtime:
            self.display_columns_runtime = list(self.raw_rows[0].keys())

        self.session_logger.info(f"Display columns changed to: {self.display_columns_runtime}")
        self.feedback_label.setText("展示列已更新")
        self._render_data_cards()

    def _append_log_line(self, message: str) -> None:
        """Append one log line into on-screen QTextEdit."""
        if not hasattr(self, "log_text"):
            return
        self.log_text.appendPlainText(message)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _save_log_snapshot(self) -> None:
        """Save current on-screen log text to a user-selected txt file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存日志副本",
            os.path.join(os.path.dirname(self.config_obj.log_file), "log_snapshot.txt"),
            "Text Files (*.txt)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write(self.log_text.toPlainText())
            self.feedback_label.setText(f"日志已保存到: {path}")
        except OSError as exc:
            self._warn("保存失败", str(exc))

    def _error_and_close(self, title: str, message: str) -> None:
        """Show error message and close window for unrecoverable init failures."""
        self._error(title, message)
        self.close()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Re-apply dynamic typography on window resize."""
        super().resizeEvent(event)
        if hasattr(self, "recent_table"):
            self._refresh_dynamic_typography(force=False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Persist all state and flush session summary before exit."""
        try:
            self._persist_all()
            self.session_logger.log_summary()
        finally:
            event.accept()
