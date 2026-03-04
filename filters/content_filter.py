"""Example pre-filter for the `content` column.

Input:
- columns: list of CSV column names
- row: current row dict
Output:
- dict[label_key, value]:
  - value == []: this label requires manual input
  - value != []: this label is auto-filled with value
  - omitted labels are filled with `filter_reject_value` by app
"""

from typing import Dict, List


def filter_row(columns: List[str], row: Dict[str, str]) -> Dict[str, object]:
    """Return per-label manual/auto plan for one row based on `content` text."""
    _ = columns  # Columns are available for advanced logic; unused in this example.
    content = str(row.get("content", "")).strip()
    if not content or len(content) < 5:
        # Empty dict => all labels omitted => all labels become filter_reject_value.
        return {}

    # Ad-like content: auto-fill is_ad and require manual notes only.
    if "#广告" in content:
        return {
            "is_ad": "yes",
            "notes": [],
        }

    # Normal content: all labels require manual input.
    return {
        "sentiment": [],
        "topics": [],
        "is_ad": [],
        "notes": [],
    }
