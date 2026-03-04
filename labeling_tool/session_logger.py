"""Session logging and timing metrics for labeling operations."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Dict, List


class SessionLogger:
    """Wrapper around `logging` with in-memory callback support for GUI display."""

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file
        self.start_time = time.time()
        self.item_durations: List[float] = []
        self.callbacks: List[Callable[[str], None]] = []

        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)

        logger_name = f"LabelingSessionLogger:{log_file}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        handler = logging.FileHandler(log_file, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def attach_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback used by GUI for real-time log text updates."""
        self.callbacks.append(callback)

    def info(self, message: str) -> None:
        """Write info log to file and notify GUI callbacks."""
        self.logger.info(message)
        for callback in self.callbacks:
            callback(message)

    def error(self, message: str) -> None:
        """Write error log to file and notify GUI callbacks."""
        self.logger.error(message)
        for callback in self.callbacks:
            callback(f"[ERROR] {message}")

    def log_session_start(self, config_payload: Dict) -> None:
        """Record session start event with config snapshot in append mode."""
        self.info("Session started.")
        self.info(f"Config snapshot: {json.dumps(config_payload, ensure_ascii=False)}")

    def record_item_duration(self, seconds: float) -> None:
        """Record per-row labeling duration used for summary statistics."""
        self.item_durations.append(seconds)

    def log_summary(self) -> None:
        """Write final summary including total and average labeling time."""
        total = time.time() - self.start_time
        count = len(self.item_durations)
        average = (sum(self.item_durations) / count) if count else 0.0
        self.info(
            "Session summary | total_seconds=%.2f | labeled_count=%d | avg_per_item=%.2f"
            % (total, count, average)
        )
