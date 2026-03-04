"""Dataset loading, sampling, and filter planning utilities."""

from __future__ import annotations

import csv
import os
import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

from labeling_tool.filter_loader import FilterFunc, FilterLoadError, FilterResult, run_filters


class DataError(Exception):
    """Raised for CSV read, sampling, and filter-related data preparation errors."""


@dataclass
class PreparedRow:
    """Prepared labeling row with source data and filter-driven constraints."""

    source_index: int
    data: Dict[str, str]
    allowed_label_keys: List[str]
    preset_label_values: Dict[str, str]


def load_csv_rows(path_value: str) -> List[Dict[str, str]]:
    """Load CSV rows with pandas primary path and csv module fallback.

    Args:
        path_value: Absolute CSV file path.

    Returns:
        List of row dicts.

    Raises:
        DataError: If file is missing, unreadable, or malformed.
    """
    if not os.path.exists(path_value):
        raise DataError(f"CSV file does not exist: {path_value}")
    if not os.path.isfile(path_value):
        raise DataError(f"CSV path is not a file: {path_value}")

    try:
        dataframe = pd.read_csv(path_value, dtype=str, keep_default_na=False)
        rows = dataframe.fillna("").to_dict(orient="records")
        return [{str(key): str(value) for key, value in row.items()} for row in rows]
    except Exception:
        # Fallback to built-in csv module if pandas parser fails.
        try:
            with open(path_value, "r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames is None:
                    raise DataError("CSV header is missing.")
                rows = []
                for item in reader:
                    rows.append({str(key): str(value) for key, value in item.items()})
                return rows
        except PermissionError as exc:
            raise DataError(f"Permission denied when reading CSV: {path_value}") from exc
        except UnicodeDecodeError as exc:
            raise DataError(f"CSV encoding error. Use UTF-8/UTF-8-SIG. File: {path_value}") from exc
        except Exception as exc:  # noqa: BLE001
            raise DataError(f"Failed to read CSV file '{path_value}': {exc}") from exc


def build_sample_indexes(total_rows: int, sampling_rate: float, seed: int) -> List[int]:
    """Build deterministic sample row indexes based on rate and random seed."""
    if total_rows <= 0:
        return []
    if sampling_rate < 0 or sampling_rate > 1:
        raise DataError("sampling_rate must be in [0, 1].")
    if sampling_rate == 1:
        return list(range(total_rows))
    sample_size = int(total_rows * sampling_rate)
    if sample_size == 0:
        return []
    rng = random.Random(seed)
    return sorted(rng.sample(range(total_rows), sample_size))


def prepare_rows(
    all_rows: List[Dict[str, str]],
    sample_indexes: List[int],
    label_keys: List[str],
    filter_map: Dict[str, FilterFunc],
    filter_reject_value: str,
    filter_log_callback: Optional[Callable[[str], None]] = None,
) -> List[PreparedRow]:
    """Construct prepared rows used by GUI and save layers.

    Each row stores:
    - source index in original CSV
    - row data values
    - allowed labels returned by pre-filter pipeline
    - pre-filled label values (auto fill + reject defaults)

    Raises:
        DataError: If sample index out of range or filter output is malformed.
    """
    prepared: List[PreparedRow] = []
    if not all_rows:
        return prepared

    for sample_pos, source_index in enumerate(sample_indexes, start=1):
        prepared.append(
            prepare_row(
                all_rows=all_rows,
                source_index=source_index,
                label_keys=label_keys,
                filter_map=filter_map,
                filter_reject_value=filter_reject_value,
                sample_pos=sample_pos,
                filter_log_callback=filter_log_callback,
            )
        )
    return prepared


def prepare_row(
    all_rows: List[Dict[str, str]],
    source_index: int,
    label_keys: List[str],
    filter_map: Dict[str, FilterFunc],
    filter_reject_value: str,
    sample_pos: Optional[int] = None,
    filter_log_callback: Optional[Callable[[str], None]] = None,
) -> PreparedRow:
    """Prepare one sampled row by running filter once and building fill plan."""
    if not all_rows:
        raise DataError("No rows available to prepare.")
    if source_index < 0 or source_index >= len(all_rows):
        raise DataError(f"Sample index out of range: {source_index}")

    columns = list(all_rows[0].keys())
    row = all_rows[source_index]
    try:
        filter_result: FilterResult = run_filters(
            row=row,
            columns=columns,
            label_keys=label_keys,
            filter_map=filter_map,
            reject_value=filter_reject_value,
        )
    except FilterLoadError as exc:
        raise DataError(str(exc)) from exc

    if filter_log_callback and filter_result.auto_filled_keys:
        auto_keys = ", ".join(filter_result.auto_filled_keys)
        manual_keys = ", ".join(filter_result.manual_label_keys) if filter_result.manual_label_keys else "(none)"
        sample_text = sample_pos if sample_pos is not None else "?"
        filter_log_callback(
            f"Filter applied | sample={sample_text} | source_index={source_index + 1} | "
            f"manual_keys=[{manual_keys}] | auto_filled_keys=[{auto_keys}]"
        )

    return PreparedRow(
        source_index=source_index,
        data=row,
        allowed_label_keys=filter_result.manual_label_keys,
        preset_label_values=dict(filter_result.preset_label_values),
    )


def validate_display_columns(rows: List[Dict[str, str]], display_columns: List[str]) -> List[str]:
    """Return only valid columns that exist in dataset, preserving order."""
    if not rows:
        return []
    available = set(rows[0].keys())
    return [column for column in display_columns if column in available]
