"""Load user-defined pre-filter functions from Python files."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple


class FilterLoadError(Exception):
    """Raised when pre-filter python file cannot be loaded or validated."""


FilterFunc = Callable[[List[str], Dict[str, str]], Dict[str, Any]]


@dataclass
class FilterResult:
    """Per-row filter result consumed by data preparation."""

    manual_label_keys: List[str]
    preset_label_values: Dict[str, str]
    explicit_auto_values: Dict[str, str]
    rejected_label_keys: List[str]

    @property
    def auto_filled_keys(self) -> List[str]:
        """Return all label keys that do not require manual input."""
        return list(self.explicit_auto_values.keys()) + self.rejected_label_keys


def _resolve_filter_path(base_dir: str, path_value: str) -> str:
    """Resolve absolute/relative filter file path based on config directory."""
    if os.path.isabs(path_value):
        return path_value
    return os.path.abspath(os.path.join(base_dir, path_value))


def _load_filter_function(file_path: str) -> FilterFunc:
    """Import module from file path and return mandatory `filter_row` function."""
    module_name = f"pre_filter_{abs(hash(file_path))}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise FilterLoadError(f"Unable to import filter module: {file_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        raise FilterLoadError(f"Error while loading filter file '{file_path}': {exc}") from exc

    filter_fn = getattr(module, "filter_row", None)
    if filter_fn is None or not callable(filter_fn):
        raise FilterLoadError(
            f"Filter file '{file_path}' must define callable function: filter_row(columns, row)."
        )
    return filter_fn


def load_pre_filters(config_dir: str, pre_filters: Dict[str, str]) -> Dict[str, FilterFunc]:
    """Load filter file for each configured column.

    Args:
        config_dir: Directory where config file lives.
        pre_filters: Mapping {column_name: python_file_path}.

    Returns:
        Mapping {column_name: callable filter function}.

    Raises:
        FilterLoadError: If path missing, unreadable, or function invalid.
    """
    loaded: Dict[str, FilterFunc] = {}
    for column_name, path_value in pre_filters.items():
        resolved = _resolve_filter_path(config_dir, path_value)
        if not os.path.exists(resolved):
            raise FilterLoadError(f"Filter file does not exist: {resolved}")
        if not os.path.isfile(resolved):
            raise FilterLoadError(f"Filter path is not a file: {resolved}")
        loaded[column_name] = _load_filter_function(resolved)
    return loaded


def run_filters(
    row: Dict[str, str],
    columns: List[str],
    label_keys: List[str],
    filter_map: Dict[str, FilterFunc],
    reject_value: str,
) -> FilterResult:
    """Run configured filters and return per-label manual/auto-fill plan for one row.

    Filter return contract:
    - return dict[label_key, value]
    - value == []: label requires manual input
    - value != []: label is auto-filled by this value
    - omitted label keys: auto-filled with `reject_value`
    """
    if not filter_map:
        return FilterResult(
            manual_label_keys=list(label_keys),
            preset_label_values={key: "" for key in label_keys},
            explicit_auto_values={},
            rejected_label_keys=[],
        )

    label_key_set = set(label_keys)
    combined_plan: Dict[str, Tuple[str, str]] = {}
    any_filter_executed = False

    for column_name, filter_fn in filter_map.items():
        if column_name not in row:
            continue

        any_filter_executed = True
        try:
            result = filter_fn(columns, row)
        except Exception as exc:  # noqa: BLE001
            raise FilterLoadError(f"Filter for column '{column_name}' raised error: {exc}") from exc

        if not isinstance(result, dict):
            raise FilterLoadError(
                f"Filter for column '{column_name}' must return dict[str, Any], got: {type(result)}"
            )

        for key, raw_value in result.items():
            if not isinstance(key, str):
                raise FilterLoadError(
                    f"Filter for column '{column_name}' returned non-string label key: {type(key)}"
                )
            if key not in label_key_set:
                raise FilterLoadError(
                    f"Filter for column '{column_name}' returned unknown label key '{key}'."
                )

            if isinstance(raw_value, list) and len(raw_value) == 0:
                normalized: Tuple[str, str] = ("manual", "")
            else:
                normalized = ("auto", _normalize_auto_value(raw_value))

            if key in combined_plan and combined_plan[key] != normalized:
                raise FilterLoadError(
                    f"Conflicting filter outputs for label key '{key}'. Existing={combined_plan[key]}, New={normalized}"
                )
            combined_plan[key] = normalized

    if not any_filter_executed:
        return FilterResult(
            manual_label_keys=list(label_keys),
            preset_label_values={key: "" for key in label_keys},
            explicit_auto_values={},
            rejected_label_keys=[],
        )

    manual_label_keys: List[str] = []
    preset_label_values: Dict[str, str] = {}
    explicit_auto_values: Dict[str, str] = {}
    rejected_label_keys: List[str] = []

    for key in label_keys:
        if key not in combined_plan:
            preset_label_values[key] = reject_value
            rejected_label_keys.append(key)
            continue

        mode, value = combined_plan[key]
        if mode == "manual":
            manual_label_keys.append(key)
            preset_label_values[key] = ""
        else:
            preset_label_values[key] = value
            explicit_auto_values[key] = value

    return FilterResult(
        manual_label_keys=manual_label_keys,
        preset_label_values=preset_label_values,
        explicit_auto_values=explicit_auto_values,
        rejected_label_keys=rejected_label_keys,
    )


def _normalize_auto_value(raw_value: Any) -> str:
    """Normalize user filter return value into persisted string form."""
    if isinstance(raw_value, list):
        items = [str(item).strip() for item in raw_value if str(item).strip()]
        return "; ".join(items)
    if raw_value is None:
        return ""
    return str(raw_value).strip()
