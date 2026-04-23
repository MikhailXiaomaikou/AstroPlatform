"""Small schema helpers for field-level citation provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PrimaryCitationSource = Literal["field_level", "table_level", "registry", "none"]


@dataclass
class FieldBibcodes:
    """Per-value bibcodes extracted from data rows."""

    columns: dict[str, list[str]] = field(default_factory=dict)
    mapping: dict[str, str | list[str]] = field(default_factory=dict)
    source_column_pattern: str = "*_bibcode"

    def all_bibcodes(self) -> set[str]:
        values: set[str] = set()
        for column_values in self.columns.values():
            values.update(str(v) for v in column_values if str(v).strip())
        return values

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "mapping": self.mapping,
            "source_column_pattern": self.source_column_pattern,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "FieldBibcodes":
        if not isinstance(payload, dict):
            return cls()
        columns_raw = payload.get("columns") or {}
        columns = {
            str(key): [str(item) for item in value if str(item).strip()]
            for key, value in columns_raw.items()
            if isinstance(value, list)
        }
        mapping_raw = payload.get("mapping") or {}
        mapping = {
            str(key): (
                [str(item) for item in value]
                if isinstance(value, list)
                else str(value)
            )
            for key, value in mapping_raw.items()
        }
        return cls(
            columns=columns,
            mapping=mapping,
            source_column_pattern=str(payload.get("source_column_pattern") or "*_bibcode"),
        )


@dataclass
class FieldLevelCoverage:
    available: bool = False
    bibcode_columns_found: int = 0
    unique_bibcodes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "bibcode_columns_found": self.bibcode_columns_found,
            "unique_bibcodes": self.unique_bibcodes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "FieldLevelCoverage":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            available=bool(payload.get("available", False)),
            bibcode_columns_found=int(payload.get("bibcode_columns_found") or 0),
            unique_bibcodes=int(payload.get("unique_bibcodes") or 0),
        )
