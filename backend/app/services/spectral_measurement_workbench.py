"""Typed spectral-measurement workbench.

This module turns row-level literature measurements into a reusable,
domain-neutral inventory.  The goal is to keep line work such as [CII],
CO, Halpha, Lyalpha, [OIII], or X-ray/optical line tables on one shared
path instead of special-casing every paper workflow.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any


LINE_ALIASES: dict[str, str] = {
    "cii": "[CII] 158um",
    "c ii": "[CII] 158um",
    "[cii]": "[CII] 158um",
    "[cii]158": "[CII] 158um",
    "co10": "CO(1-0)",
    "co(1-0)": "CO(1-0)",
    "co1-0": "CO(1-0)",
    "halpha": "Halpha 6563A",
    "h alpha": "Halpha 6563A",
    "hα": "Halpha 6563A",
    "lyalpha": "Lyalpha 1216A",
    "lya": "Lyalpha 1216A",
    "lyα": "Lyalpha 1216A",
    "oiii5007": "[OIII] 5007A",
    "[oiii]5007": "[OIII] 5007A",
    "oii3727": "[OII] 3727A",
    "[oii]3727": "[OII] 3727A",
}

LINE_REST_FREQUENCY_GHZ: dict[str, float] = {
    "[CII] 158um": 1900.5369,
    "CO(1-0)": 115.2712018,
    "Halpha 6563A": 456802.0,
    "Lyalpha 1216A": 2466067.0,
    "[OIII] 5007A": 598755.0,
    "[OII] 3727A": 804750.0,
}


def canonical_line_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    compact = re.sub(r"[^a-z0-9α\[\]]+", "", text.lower())
    if compact in LINE_ALIASES:
        return LINE_ALIASES[compact]
    # Preserve already-canonical extractor labels.
    for canonical in LINE_REST_FREQUENCY_GHZ:
        if compact == re.sub(r"[^a-z0-9α\[\]]+", "", canonical.lower()):
            return canonical
    if "cii" in compact:
        return "[CII] 158um"
    if "co" in compact and ("10" in compact or "1-0" in text):
        return "CO(1-0)"
    if "halpha" in compact or "hα" in compact:
        return "Halpha 6563A"
    if "lya" in compact or "lyalpha" in compact or "lyα" in compact:
        return "Lyalpha 1216A"
    if "oiii" in compact and "5007" in compact:
        return "[OIII] 5007A"
    if "oii" in compact and "3727" in compact:
        return "[OII] 3727A"
    return text


def _finite_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _citation_key(row: dict[str, Any]) -> str:
    citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
    return str(
        row.get("bibcode")
        or row.get("arxiv_id")
        or citation.get("bibcode")
        or citation.get("arxiv_id")
        or citation.get("doi")
        or ""
    ).strip()


def _line_matches(row_line: str | None, wanted_line: str | None) -> bool:
    if not wanted_line:
        return True
    if not row_line:
        return False
    a = re.sub(r"[^a-z0-9]+", "", row_line.lower())
    b = re.sub(r"[^a-z0-9]+", "", wanted_line.lower())
    return a == b or a in b or b in a


def prepare_spectral_measurements(
    rows: list[dict[str, Any]],
    *,
    line_id: str | None = None,
    min_fit_rows: int = 5,
    preview_limit: int = 20,
) -> dict[str, Any]:
    """Validate and summarize row-level spectral measurements.

    Rows are intentionally generic: any extractor/connector can feed this
    function as long as it uses the common fields produced by
    extract_literature_tables (`source_name`, `line_id`, `redshift`,
    `log_luminosity`, `fwhm_km_s`, citation fields).
    """
    wanted_line = canonical_line_id(line_id) if line_id else None
    typed_rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    inventory: Counter[str] = Counter()
    citations: set[str] = set()
    by_citation: defaultdict[str, int] = defaultdict(int)

    for idx, row in enumerate(rows or []):
        if not isinstance(row, dict):
            rejected.append({"row_index": idx, "reason": "not_a_mapping"})
            continue
        row_line = canonical_line_id(row.get("line_id") or row.get("line") or row.get("transition"))
        if row_line:
            inventory[row_line] += 1
        if not _line_matches(row_line, wanted_line):
            rejected.append({"row_index": idx, "source_name": row.get("source_name"), "reason": "line_filter"})
            continue
        source_name = str(row.get("source_name") or row.get("object") or row.get("name") or "").strip()
        log_l = _finite_float(row.get("log_luminosity"))
        fwhm = _finite_float(row.get("fwhm_km_s"))
        z = _finite_float(row.get("redshift"))
        citation_key = _citation_key(row)
        missing = []
        if not source_name:
            missing.append("source_name")
        if log_l is None:
            missing.append("log_luminosity")
        if fwhm is None or fwhm <= 0:
            missing.append("fwhm_km_s")
        if not citation_key:
            missing.append("citation")
        if missing:
            rejected.append({
                "row_index": idx,
                "source_name": source_name or None,
                "reason": "missing_required_fields",
                "missing": missing,
            })
            continue
        citations.add(citation_key)
        by_citation[citation_key] += 1
        typed_rows.append({
            "source_name": source_name,
            "line_id": row_line or wanted_line or "unknown",
            "redshift": z,
            "log_luminosity": log_l,
            "log_luminosity_err": _finite_float(row.get("log_luminosity_err")),
            "fwhm_km_s": fwhm,
            "fwhm_err_km_s": _finite_float(row.get("fwhm_err_km_s")),
            "luminosity_kind": row.get("luminosity_kind") or "L_solar",
            "is_lensed": row.get("is_lensed"),
            "mu_lens": _finite_float(row.get("mu_lens")),
            "table_label": row.get("table_label"),
            "citation_key": citation_key,
            "row_index": row.get("row_index", idx),
        })

    n_fit_ready = len(typed_rows)
    fwhm_values = [r["fwhm_km_s"] for r in typed_rows]
    log_l_values = [r["log_luminosity"] for r in typed_rows]
    z_values = [r["redshift"] for r in typed_rows if r.get("redshift") is not None]
    fit_ready = n_fit_ready >= min_fit_rows
    return {
        "success": True,
        "tool": "prepare_spectral_measurements",
        "result_granularity": "spectral_measurement_workbench",
        "line_filter": wanted_line,
        "n_input_rows": len(rows or []),
        "n_fit_ready_rows": n_fit_ready,
        "n_rejected_rows": len(rejected),
        "fit_ready": fit_ready,
        "supports_measurement_claims": fit_ready,
        "required_fields": ["source_name", "log_luminosity", "fwhm_km_s", "citation"],
        "line_inventory": dict(sorted(inventory.items())),
        "citation_count": len(citations),
        "rows_by_citation": dict(sorted(by_citation.items())),
        "range_summary": {
            "redshift": _range(z_values),
            "log_luminosity": _range(log_l_values),
            "fwhm_km_s": _range(fwhm_values),
        },
        "fit_ready_cache_note": (
            "Use fit_line_lfr on the same cache for relation statistics; this "
            "workbench validates rows but does not fit a relation."
        ),
        "typed_rows_preview": typed_rows[:preview_limit],
        "rejected_preview": rejected[:preview_limit],
    }


def _range(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "max": None}
    return {
        "n": len(values),
        "min": round(float(min(values)), 6),
        "max": round(float(max(values)), 6),
    }
