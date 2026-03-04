"""Configuration loading, validation, and resume signature helpers."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict

import yaml

from labeling_tool.models import AppConfig, LabelDefinition


class ConfigError(Exception):
    """Raised when configuration file is missing or invalid."""


def _normalize_path(path_value: str) -> str:
    """Normalize path for stable cross-session signature comparison."""
    return os.path.abspath(os.path.expanduser(path_value))


def _require_key(data: Dict[str, Any], key: str, parent: str) -> Any:
    """Fetch required key from dict and raise descriptive config error if absent."""
    if key not in data:
        raise ConfigError(f"Missing key '{key}' in section '{parent}'.")
    return data[key]


def load_config(config_path: str) -> AppConfig:
    """Load YAML config from disk and validate all required fields.

    Args:
        config_path: Path to YAML config file.

    Returns:
        AppConfig: Parsed and validated configuration object.

    Raises:
        ConfigError: If file cannot be read or config data is invalid.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            payload = yaml.safe_load(file)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except PermissionError as exc:
        raise ConfigError(f"Permission denied when reading config: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config YAML parse error: {exc}") from exc

    if payload is None:
        raise ConfigError("Config file is empty.")
    if not isinstance(payload, dict):
        raise ConfigError("Config top-level structure must be a mapping/object.")

    paths = _require_key(payload, "paths", "root")
    labels_payload = _require_key(payload, "labels", "root")

    input_csv = _normalize_path(str(_require_key(paths, "input_csv", "paths")))
    output_csv = _normalize_path(str(_require_key(paths, "output_csv", "paths")))
    log_file = _normalize_path(str(_require_key(paths, "log_file", "paths")))
    state_file = _normalize_path(str(_require_key(paths, "state_file", "paths")))

    sampling_rate = float(payload.get("sampling_rate", 1.0))
    if not 0 <= sampling_rate <= 1:
        raise ConfigError("sampling_rate must be between 0 and 1.")

    random_seed = int(payload.get("random_seed", 42))

    display_columns = payload.get("display_columns", [])
    if not isinstance(display_columns, list):
        raise ConfigError("display_columns must be a list.")
    display_columns = [str(item) for item in display_columns]

    labels = []
    if not isinstance(labels_payload, list) or not labels_payload:
        raise ConfigError("labels must be a non-empty list.")
    for item in labels_payload:
        try:
            definition = LabelDefinition(
                key=str(_require_key(item, "key", "labels[]")),
                name=str(item.get("name", item.get("key", ""))),
                input_type=str(_require_key(item, "type", "labels[]")),
                options=[str(option) for option in item.get("options", [])],
                allow_custom=bool(item.get("allow_custom", False)),
                required=bool(item.get("required", True)),
            )
            definition.validate()
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc
        labels.append(definition)

    ui = payload.get("ui", {})
    ui_icon = str(ui.get("icon", "")).strip()

    config = AppConfig(
        input_csv=input_csv,
        output_csv=output_csv,
        log_file=log_file,
        state_file=state_file,
        sampling_rate=sampling_rate,
        random_seed=random_seed,
        display_columns=display_columns,
        labels=labels,
        column_notes={str(k): str(v) for k, v in payload.get("column_notes", {}).items()},
        label_notes={str(k): str(v) for k, v in payload.get("label_notes", {}).items()},
        pre_filters={str(k): str(v) for k, v in payload.get("pre_filters", {}).items()},
        filter_reject_value=str(payload.get("filter_reject_value", "__FILTERED_OUT__")),
        log_filter_actions=bool(payload.get("log_filter_actions", False)),
        ui_theme=str(ui.get("theme", "Dark")),
        ui_color_theme=str(ui.get("color_theme", "dark-blue")),
        ui_geometry=str(ui.get("geometry", "1380x860")),
        ui_icon=ui_icon,
    )

    return config


def build_path_signature(config: AppConfig) -> str:
    """Hash path-related config values to detect path change during resume."""
    payload = {
        "input_csv": config.input_csv,
        "output_csv": config.output_csv,
        "state_file": config.state_file,
        "log_file": config.log_file,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_core_signature(config: AppConfig) -> str:
    """Hash runtime-affecting config values, excluding notes as requested."""
    payload = {
        "sampling_rate": config.sampling_rate,
        "random_seed": config.random_seed,
        "display_columns": config.display_columns,
        "labels": [
            {
                "key": item.key,
                "name": item.name,
                "type": item.input_type,
                "options": item.options,
                "allow_custom": item.allow_custom,
                "required": item.required,
            }
            for item in config.labels
        ],
        "pre_filters": config.pre_filters,
        "filter_reject_value": config.filter_reject_value,
        "log_filter_actions": config.log_filter_actions,
        "ui_icon": config.ui_icon,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def config_for_logging(config: AppConfig) -> Dict[str, Any]:
    """Return readable config snapshot that can be serialized into session logs."""
    return {
        "input_csv": config.input_csv,
        "output_csv": config.output_csv,
        "log_file": config.log_file,
        "state_file": config.state_file,
        "sampling_rate": config.sampling_rate,
        "random_seed": config.random_seed,
        "display_columns": config.display_columns,
        "labels": [
            {
                "key": item.key,
                "name": item.name,
                "type": item.input_type,
                "options": item.options,
                "allow_custom": item.allow_custom,
                "required": item.required,
            }
            for item in config.labels
        ],
        "pre_filters": config.pre_filters,
        "filter_reject_value": config.filter_reject_value,
        "log_filter_actions": config.log_filter_actions,
        "ui_icon": config.ui_icon,
    }
