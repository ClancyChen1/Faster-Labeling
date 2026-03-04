"""Data models used by the labeling tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


VALID_LABEL_TYPES = {"single_choice", "multi_choice", "text"}


@dataclass
class LabelDefinition:
    """Definition of a single label and how user should input it."""

    key: str
    name: str
    input_type: str
    options: List[str] = field(default_factory=list)
    allow_custom: bool = False
    required: bool = True

    def validate(self) -> None:
        """Validate this label definition and raise ValueError for invalid config."""
        if not self.key:
            raise ValueError("Label key cannot be empty.")
        if not self.name:
            raise ValueError(f"Label '{self.key}' name cannot be empty.")
        if self.input_type not in VALID_LABEL_TYPES:
            raise ValueError(
                f"Label '{self.key}' has invalid type '{self.input_type}'. "
                f"Expected one of: {sorted(VALID_LABEL_TYPES)}"
            )
        if self.input_type in {"single_choice", "multi_choice"} and not self.options:
            raise ValueError(
                f"Label '{self.key}' requires non-empty options for type '{self.input_type}'."
            )


@dataclass
class AppConfig:
    """Strongly-typed application configuration parsed from JSON."""

    input_csv: str
    output_csv: str
    log_file: str
    state_file: str
    sampling_rate: float
    random_seed: int
    display_columns: List[str]
    labels: List[LabelDefinition]
    column_notes: Dict[str, str]
    label_notes: Dict[str, str]
    pre_filters: Dict[str, str]
    filter_reject_value: str
    log_filter_actions: bool = False
    ui_theme: str = "Dark"
    ui_color_theme: str = "dark-blue"
    ui_geometry: str = "1380x860"
    ui_icon: str = ""

    @property
    def label_keys(self) -> List[str]:
        """Return label keys in config order."""
        return [item.key for item in self.labels]

    @property
    def label_name_by_key(self) -> Dict[str, str]:
        """Map internal label key -> output CSV column name."""
        return {item.key: item.name for item in self.labels}

    @property
    def label_by_key(self) -> Dict[str, LabelDefinition]:
        """Map internal label key -> label definition object."""
        return {item.key: item for item in self.labels}
