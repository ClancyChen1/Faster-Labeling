"""Persistence helpers for output CSV and resume state JSON."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd


class StorageError(Exception):
    """Raised when output/state persistence fails."""


def ensure_parent_dir(file_path: str) -> None:
    """Ensure parent directory exists before writing file."""
    parent = os.path.dirname(os.path.abspath(file_path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def read_state(state_file: str) -> Optional[Dict[str, Any]]:
    """Read resume state JSON if available; return None when absent."""
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to read state file '{state_file}': {exc}") from exc


def write_state(state_file: str, payload: Dict[str, Any]) -> None:
    """Write state JSON atomically by replacing file content."""
    ensure_parent_dir(state_file)
    try:
        with open(state_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to write state file '{state_file}': {exc}") from exc


def read_output_rows(output_csv: str) -> List[Dict[str, str]]:
    """Read existing output rows with pandas primary and csv fallback."""
    if not os.path.exists(output_csv):
        return []
    try:
        dataframe = pd.read_csv(output_csv, dtype=str, keep_default_na=False)
        rows = dataframe.fillna("").to_dict(orient="records")
        return [{str(key): str(value) for key, value in row.items()} for row in rows]
    except Exception:
        try:
            with open(output_csv, "r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                return [
                    {str(key): str(value) for key, value in row.items()} for row in reader
                ]
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Failed to read output CSV '{output_csv}': {exc}") from exc


def write_output_rows(output_csv: str, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    """Persist all labeling rows into output CSV.

    Uses pandas first (handles UTF-8 and columns safely), and falls back to
    `csv.DictWriter` if pandas write fails.
    """
    ensure_parent_dir(output_csv)
    try:
        dataframe = pd.DataFrame(rows)
        if columns:
            dataframe = dataframe.reindex(columns=columns)
        dataframe.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return
    except Exception:
        pass

    try:
        with open(output_csv, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to write output CSV '{output_csv}': {exc}") from exc


def clear_output_file(output_csv: str) -> None:
    """Remove old output CSV so new session starts from clean result set."""
    if os.path.exists(output_csv):
        try:
            os.remove(output_csv)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Cannot remove old output CSV '{output_csv}': {exc}") from exc


def make_non_overwrite_path(original_path: str) -> str:
    """Generate new output path by appending timestamp suffix."""
    root, ext = os.path.splitext(original_path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = ext or ".csv"
    return f"{root}_{stamp}{ext}"
