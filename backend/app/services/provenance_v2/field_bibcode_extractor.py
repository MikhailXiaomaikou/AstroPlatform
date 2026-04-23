"""Column-name based field-level bibcode extraction."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.provenance_v2.field_level_schema import (
    FieldBibcodes,
    FieldLevelCoverage,
)

logger = logging.getLogger(__name__)

FIELD_BIBCODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(\w+)_bibcode$", re.I), "inferred"),
    (re.compile(r"^Reference$", re.I), "value"),
    (re.compile(r"^ref(_\w+)?$", re.I), "inferred"),
    (re.compile(r"^citation$", re.I), "value"),
    (re.compile(r"^bib(code)?$", re.I), "value"),
]

SIMBAD_EXPLICIT_MAP: dict[str, str | list[str]] = {
    "plx": "plx_value",
    "coo": ["ra", "dec"],
    "rv": "rv_value",
    "rvz": "rvz_radvel",
    "pm": ["pmra", "pmdec"],
    "sp": "sp_type",
    "morph": "morph_type",
    "galdim": ["galdim_majaxis", "galdim_minaxis", "galdim_angle"],
    "vlsr": "vlsr",
    "flux": "flux_value",
}


class FieldBibcodeExtractor:
    """Extract bibcode-bearing columns without scanning arbitrary values."""

    def extract(
        self,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> tuple[FieldBibcodes, FieldLevelCoverage]:
        if not columns or not rows:
            return FieldBibcodes(), FieldLevelCoverage()

        bibcode_columns: list[str] = []
        mapping: dict[str, str | list[str]] = {}
        for column in columns:
            kind = self._match_column(column)
            if kind is None:
                continue
            bibcode_columns.append(column)
            mapping[column] = self._infer_measurement_column(column, columns, kind)

        if not bibcode_columns:
            logger.debug("No field-level bibcode columns detected")
            return FieldBibcodes(), FieldLevelCoverage()

        by_column: dict[str, list[str]] = {column: [] for column in bibcode_columns}
        for row in rows:
            lowered = {str(key).lower(): value for key, value in row.items()}
            for column in bibcode_columns:
                value = row.get(column)
                if value is None:
                    value = lowered.get(column.lower())
                normalized = _normalize_bibcode_value(value)
                if normalized:
                    by_column[column].append(normalized)

        field_bibcodes = FieldBibcodes(
            columns=by_column,
            mapping=mapping,
            source_column_pattern=", ".join(pattern.pattern for pattern, _ in FIELD_BIBCODE_PATTERNS),
        )
        unique = field_bibcodes.all_bibcodes()
        coverage = FieldLevelCoverage(
            available=bool(unique),
            bibcode_columns_found=len(bibcode_columns),
            unique_bibcodes=len(unique),
        )
        return field_bibcodes, coverage

    def _match_column(self, column: str) -> str | None:
        for pattern, kind in FIELD_BIBCODE_PATTERNS:
            if pattern.match(str(column)):
                return kind
        return None

    def _infer_measurement_column(
        self,
        bibcode_column: str,
        all_columns: list[str],
        kind: str,
    ) -> str | list[str]:
        if kind == "value":
            return bibcode_column
        match = re.match(r"^(\w+)_bibcode$", bibcode_column, re.I)
        if match:
            prefix = match.group(1).lower()
            if prefix in SIMBAD_EXPLICIT_MAP:
                return SIMBAD_EXPLICIT_MAP[prefix]
            candidate = f"{prefix}_value"
            column_lookup = {str(column).lower(): str(column) for column in all_columns}
            if candidate in column_lookup:
                return column_lookup[candidate]
            if prefix in column_lookup:
                return column_lookup[prefix]
        return "unknown"


def _normalize_bibcode_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass
    text = str(value).strip()
    if not text or text == "--" or text.lower() in {"none", "null", "nan"}:
        return None
    return text
