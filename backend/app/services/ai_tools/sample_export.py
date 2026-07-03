"""Sample-level cosmology bookkeeping: demagnify, distance comparison, export.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: compare_luminosity_distances, export_sample_table, demagnify_sample.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import math
from typing import Any

from app.services.ai_tools import _session_cache_key
from app.services.ai_tools.literature_tables import _literature_table_cache_payload
from app.services.ai_tools.spectral_measurements import _finite_float

TOOL_SCHEMAS = [
    {
        "name": "compare_luminosity_distances",
        "description": (
            "Compare luminosity distance + Δlog L for two cosmology choices "
            "across the cached literature sample. Use BEFORE citing a non-"
            "Planck H0/Om0 (e.g. Riess+11 H0=73.8, Suzuki+12 Om=0.295) on a "
            "sample whose source_cosmology is something else. Returns per-"
            "source ΔDL%% + Δlog L, plus median/max summary; use the result "
            "to either recompute log_luminosity or quote the shift as a "
            "cosmology-systematic uncertainty in the slope error budget."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Source cache key. Default: latest_literature_tables.",
                },
                "target_cosmology": {
                    "type": "string",
                    "description": (
                        "Cosmology name. Prefer a curated PART AA preset "
                        "('planck18' | 'planck18_bao' | 'freedman21_trgb' | "
                        "'riess22_shoes') — each carries a peer-reviewed "
                        "ADS bibcode that the citation validator anchors "
                        "against. Legacy names also accepted (Planck15 / "
                        "WMAP9 / WMAP7 / WMAP5 — no curated bibcode), as "
                        "is the FlatLambdaCDM_H<H0>_Om<Om> spec for "
                        "older measurements (e.g. FlatLambdaCDM_H73p8_Om0p295 "
                        "for Riess+11 / Suzuki+12)."
                    ),
                },
            },
            "required": ["target_cosmology"],
        },
    },
    {
        "name": "export_sample_table",
        "description": (
            "Export the cached literature sample as a machine-readable "
            "table (csv | votable | latex | ascii). Use as the final "
            "deliverable so the user can verify the 74-source sample "
            "directly. The content is returned inline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Source cache key. Default: latest_literature_tables.",
                },
                "format": {
                    "type": "string",
                    "enum": ["csv", "votable", "latex", "ascii"],
                    "description": "Output format. Default: csv.",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column subset. Default: all standard fields.",
                },
            },
        },
    },
    {
        "name": "demagnify_sample",
        "description": (
            "Apply gravitational-lensing demagnification to a literature "
            "sample. Reads cached line_measurements, subtracts log10(μ) "
            "from log_luminosity for every source listed in mu_map, and "
            "writes the corrected rows to a NEW cache key (default suffix "
            "'__demag') so the original is preserved. Use this BEFORE "
            "fit_line_lfr when any sample sources are gravitationally "
            "lensed; then call fit_line_lfr(cache_key=<new>) on the "
            "demagnified cache."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Source cache key. Default: latest_literature_tables.",
                },
                "mu_map": {
                    "type": "object",
                    "description": (
                        "Per-source magnification factors. Two equivalent "
                        "forms: '\"SRC-A\": 5.0' (just μ) or "
                        "'\"SRC-B\": {\"mu\": 3.0, \"reference\": \"Foo+24\"}' "
                        "(μ + cited source for the μ value). Reference "
                        "is recorded in provenance."
                    ),
                },
                "output_cache_key": {
                    "type": "string",
                    "description": "Override the default <input>__demag suffix.",
                },
            },
            "required": ["mu_map"],
        },
    },
]


def _exec_demagnify_sample(inp: dict, python_session_id: str = "default") -> dict:
    """Apply gravitational-lensing demagnification to a literature sample.

    Reads ``cache_key`` (default: ``latest_literature_tables``), copies
    every row, and for sources listed in ``mu_map`` subtracts log10(μ)
    from ``log_luminosity`` while flagging ``is_lensed=True``,
    ``mu_lens=μ`` and ``_demagnified=True``.  Writes the modified
    sample back to a NEW cache key (``<orig>__demag`` by default) so
    the original is preserved — that lets the lensing-systematic error
    budget be derived later by comparing fits run on the two cache
    keys.

    mu_map accepts two forms per source:
        "SRC-A": 5.0
        "SRC-B": {"mu": 3.0, "reference": "Foo+24"}

    The result dict lists every row's before / after log_luminosity,
    sources skipped because they were not in the map, and the new
    cache key.  After this call the AI is expected to invoke
    fit_line_lfr(cache_key=<new>) to get the demagnified-sample fit.
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _resolve_literature_measurement_cache, store_search_results
    cache_key = str(
        inp.get("cache_key") or "latest_literature_tables"
    ).strip() or "latest_literature_tables"
    mu_map_raw = inp.get("mu_map")
    if not isinstance(mu_map_raw, dict) or not mu_map_raw:
        return {
            "success": False,
            "error": "demagnify_sample requires a non-empty mu_map dict.",
            "error_class": "missing_mu_map",
            "__tool_status__": "FAILED",
        }
    out_cache_key = str(inp.get("output_cache_key") or f"{cache_key}__demag").strip()

    rows, resolved_cache_key = _resolve_literature_measurement_cache(
        cache_key, python_session_id,
    )
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
        }

    # Normalize mu_map to {name: {mu: float, reference: str}}.
    normalized_mu_map: dict[str, dict[str, Any]] = {}
    for src, value in mu_map_raw.items():
        if isinstance(value, dict):
            mu_val = _finite_float(value.get("mu"))
            ref = str(value.get("reference") or "").strip()
        else:
            mu_val = _finite_float(value)
            ref = ""
        if mu_val is None or mu_val <= 0:
            continue
        normalized_mu_map[str(src).strip()] = {"mu": mu_val, "reference": ref}

    if not normalized_mu_map:
        return {
            "success": False,
            "error": "mu_map contained no entries with a positive numeric μ.",
            "error_class": "invalid_mu_map",
            "__tool_status__": "FAILED",
        }

    new_rows: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        name = str(new_row.get("source_name") or "").strip()
        entry = normalized_mu_map.get(name)
        if entry is None:
            skipped.append({
                "source_name": name,
                "reason": "not_in_mu_map",
                "is_lensed": new_row.get("is_lensed"),
            })
        else:
            mu = float(entry["mu"])
            log_l_before = _finite_float(new_row.get("log_luminosity"))
            if log_l_before is None:
                skipped.append({
                    "source_name": name,
                    "reason": "row_missing_log_luminosity",
                })
            else:
                delta = math.log10(mu)
                new_row["log_luminosity"] = log_l_before - delta
                new_row["mu_lens"] = mu
                new_row["is_lensed"] = True
                new_row["_demagnified"] = True
                new_row["_demagnify_reference"] = entry["reference"]
                new_row["_log_luminosity_before_demag"] = log_l_before
                applied.append({
                    "source_name": name,
                    "mu": mu,
                    "log_luminosity_before": round(log_l_before, 6),
                    "log_luminosity_after": round(new_row["log_luminosity"], 6),
                    "delta_log_l": round(-delta, 6),
                    "reference": entry["reference"],
                })
        new_rows.append(new_row)

    payload = _literature_table_cache_payload(
        {"line_measurements": new_rows, "tables": []},
        out_cache_key,
    )
    payload["derived_from"] = resolved_cache_key
    payload["demagnify_summary"] = {
        "n_input_rows": len(rows),
        "n_demagnified": len(applied),
        "n_skipped": len(skipped),
    }
    store_search_results(out_cache_key, payload)
    session_key = _session_cache_key(out_cache_key, python_session_id)
    if session_key:
        store_search_results(session_key, payload)

    return {
        "success": True,
        "tool": "demagnify_sample",
        "__tool_status__": "PARTIAL" if applied and skipped else None,
        "input_cache_key": resolved_cache_key,
        "output_cache_key": out_cache_key,
        "n_input_rows": len(rows),
        "n_demagnified": len(applied),
        "n_skipped": len(skipped),
        "applied": applied,
        "skipped_summary": skipped[:50],
        "__message_to_model__": (
            f"Demagnified {len(applied)}/{len(rows)} rows. The corrected "
            f"sample is cached at '{out_cache_key}' — pass it to fit_line_lfr "
            f"as cache_key={out_cache_key!r} to fit on the demagnified rows. "
            "The original cache is preserved so the lensing-systematic error "
            "budget can be derived by comparing fits on both keys."
        ),
    }


def _cosmology_manifest_for(name: str) -> dict[str, Any]:
    """Build a manifest dict for an arbitrary supported cosmology name.

    PART AA: prefer the curated preset metadata (with bibcode/DOI) when
    the requested name is a PART AA preset OR its legacy astropy alias.
    Fall back to the raw astropy object for legacy names like WMAP9 /
    FlatLambdaCDM_HxxOmxx so the existing comparison flow still works.
    """
    from app.services.cosmology import (
        PRESETS,
        cosmology_manifest as _preset_manifest,
        get_cosmology,
    )

    # PART AA preset name OR legacy "Planck18" alias for the planck18
    # preset → return the preset manifest with bibcode + DOI.
    normalised = "planck18" if name == "Planck18" else name
    if normalised in PRESETS:
        return _preset_manifest(normalised)

    # Legacy astropy / FlatLambdaCDM_... path: compute from astropy obj,
    # bibcode is null because we don't claim attribution for these.
    cosmo = get_cosmology(name)
    return {
        "name": name,
        "H0_km_s_Mpc": float(cosmo.H0.value),
        "Om0": float(cosmo.Om0),
        "Ob0": float(getattr(cosmo, "Ob0", 0.0) or 0.0),
        "bibcode": None,
        "doi": None,
        "reference": "Astropy legacy alias (no curated preset metadata)",
    }


def _exec_compare_luminosity_distances(
    inp: dict, python_session_id: str = "default",
) -> dict:
    """Compare luminosity distance + Δlog L for two cosmology choices.

    Use this BEFORE citing a non-Planck H0/Om0 (e.g. Riess 2011 H0=73.8
    or Suzuki 2012 Om=0.295) on a sample whose source_cosmology is
    something else.  The tool reports per-source ΔDL and Δlog L' so the
    AI can decide whether the cosmology-systematic shift is large
    enough to recompute or merely cite as a < few % systematic.
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _resolve_literature_measurement_cache
    cache_key = str(
        inp.get("cache_key") or "latest_literature_tables"
    ).strip() or "latest_literature_tables"
    target_name = str(inp.get("target_cosmology") or "").strip()
    if not target_name:
        return {
            "success": False,
            "error": "target_cosmology is required (e.g. 'Planck18', 'WMAP9', or 'FlatLambdaCDM_H73p8_Om0p295').",
            "error_class": "missing_target_cosmology",
            "__tool_status__": "FAILED",
        }
    rows, resolved_cache_key = _resolve_literature_measurement_cache(
        cache_key, python_session_id,
    )
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
        }
    from app.services.cosmology import (
        cosmology_manifest as _current_manifest,
        get_cosmology as _get,
    )

    current_manifest = _current_manifest()
    target_manifest = _cosmology_manifest_for(target_name)
    current_cosmo = _get(None)
    target_cosmo = _get(target_name)

    per_source: list[dict[str, Any]] = []
    deltas_pct: list[float] = []
    deltas_log_l: list[float] = []
    for row in rows:
        z = _finite_float(row.get("redshift"))
        if z is None or z <= 0:
            continue
        try:
            dl_a = float(current_cosmo.luminosity_distance(z).to("Mpc").value)
            dl_b = float(target_cosmo.luminosity_distance(z).to("Mpc").value)
        except Exception:
            continue
        if dl_a <= 0:
            continue
        delta_pct = (dl_b - dl_a) / dl_a * 100.0
        # log L ∝ 2 · log DL ⇒ Δlog L = 2 · log10(DL_b / DL_a)
        delta_log_l = 2.0 * (math.log10(dl_b) - math.log10(dl_a))
        per_source.append({
            "source_name": row.get("source_name"),
            "redshift": z,
            "DL_current_Mpc": round(dl_a, 3),
            "DL_target_Mpc": round(dl_b, 3),
            "delta_pct": round(delta_pct, 4),
            "delta_log_luminosity": round(delta_log_l, 6),
        })
        deltas_pct.append(delta_pct)
        deltas_log_l.append(delta_log_l)

    if not per_source:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": "No rows with usable redshift were available for comparison.",
            "error_class": "no_redshift_rows",
        }
    import numpy as _np
    summary = {
        "n_used": len(per_source),
        "max_abs_delta_pct": round(float(_np.max(_np.abs(deltas_pct))), 4),
        "median_abs_delta_pct": round(float(_np.median(_np.abs(deltas_pct))), 4),
        "max_abs_delta_log_luminosity": round(
            float(_np.max(_np.abs(deltas_log_l))), 6,
        ),
        "median_abs_delta_log_luminosity": round(
            float(_np.median(_np.abs(deltas_log_l))), 6,
        ),
    }
    return {
        "success": True,
        "tool": "compare_luminosity_distances",
        "cache_key": resolved_cache_key,
        "current_cosmology": current_manifest,
        "target_cosmology": target_manifest,
        "summary": summary,
        "per_source": per_source[:200],
        "n_source_total": len(per_source),
        "__message_to_model__": (
            f"Cosmology cross-check vs {target_name!r}: median |ΔDL|"
            f" = {summary['median_abs_delta_pct']:.2f}%, max"
            f" {summary['max_abs_delta_pct']:.2f}%."
            "  If max |Δlog L| > 0.05 dex, recompute log_luminosity"
            " before fitting; otherwise quote the shift as a"
            " cosmology-systematic uncertainty."
        ),
    }


def _exec_export_sample_table(
    inp: dict, python_session_id: str = "default",
) -> dict:
    """Export the cached literature sample as a machine-readable table.

    Formats supported:
      - csv       (default; comma-separated, header row)
      - votable   (IVOA VOTable XML via astropy)
      - latex     (AAS deluxetable string)
      - ascii     (fixed-width, astropy ASCII)

    The table is returned inline in the result dict (`content`); the
    caller can write it to disk or include it in a PDF/paper draft.
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _resolve_literature_measurement_cache
    cache_key = str(
        inp.get("cache_key") or "latest_literature_tables"
    ).strip() or "latest_literature_tables"
    fmt = str(inp.get("format") or "csv").strip().lower()
    if fmt not in ("csv", "votable", "latex", "ascii"):
        return {
            "success": False,
            "error": f"Unsupported format {fmt!r}. Use csv | votable | latex | ascii.",
            "error_class": "invalid_format",
            "__tool_status__": "FAILED",
        }
    rows, resolved_cache_key = _resolve_literature_measurement_cache(
        cache_key, python_session_id,
    )
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
        }
    cols_default = [
        "source_name", "redshift", "line_id",
        "log_luminosity", "log_luminosity_err",
        "fwhm_km_s", "fwhm_err_km_s",
        "mu_lens", "is_lensed",
        "bibcode", "arxiv_id", "table_label",
    ]
    cols = inp.get("columns") if isinstance(inp.get("columns"), list) else cols_default
    cols = [str(c) for c in cols if c]

    def _row_value(row: dict, col: str) -> Any:
        v = row.get(col)
        if isinstance(v, dict):
            return v.get("bibcode") or v.get("arxiv_id") or ""
        return v if v is not None else ""

    if fmt == "csv":
        import csv as _csv
        import io as _io
        buf = _io.StringIO()
        writer = _csv.writer(buf)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([_row_value(row, c) for c in cols])
        content = buf.getvalue()
        media_type = "text/csv"
        ext = "csv"
    elif fmt == "ascii":
        try:
            import io as _io
            from astropy.table import Table
            from astropy.io import ascii as _ascii
            data = {c: [_row_value(row, c) for row in rows] for c in cols}
            tab = Table(data)
            buf = _io.StringIO()
            _ascii.write(tab, buf, format="fixed_width")
            content = buf.getvalue()
            media_type = "text/plain"
            ext = "txt"
        except Exception as exc:
            return {
                "success": False,
                "error": f"astropy ASCII export failed: {exc}",
                "error_class": "astropy_failure",
                "__tool_status__": "FAILED",
            }
    elif fmt == "votable":
        try:
            from astropy.table import Table
            from astropy.io.votable import from_table, writeto
            import io as _io
            data = {c: [_row_value(row, c) for row in rows] for c in cols}
            tab = Table(data)
            tab.meta["cache_key"] = resolved_cache_key
            buf = _io.BytesIO()
            writeto(from_table(tab), buf)
            content = buf.getvalue().decode("utf-8")
            media_type = "application/x-votable+xml"
            ext = "xml"
        except Exception as exc:
            return {
                "success": False,
                "error": f"astropy VOTable export failed: {exc}",
                "error_class": "astropy_failure",
                "__tool_status__": "FAILED",
            }
    else:  # latex
        # Plain AAS deluxetable.  No formal LaTeX rendering — the AI is
        # expected to paste this into a paper draft.
        header = " & ".join(cols) + r" \\"
        lines = [
            r"\begin{deluxetable}{" + "c" * len(cols) + r"}",
            r"\tablecaption{Literature line-measurement sample (cache_key=" +
            resolved_cache_key + r")}",
            r"\tablehead{" + header + r"}",
            r"\startdata",
        ]
        for row in rows:
            lines.append(
                " & ".join(str(_row_value(row, c)) for c in cols) + r" \\"
            )
        lines.extend([r"\enddata", r"\end{deluxetable}"])
        content = "\n".join(lines)
        media_type = "application/x-latex"
        ext = "tex"
    return {
        "success": True,
        "tool": "export_sample_table",
        "cache_key": resolved_cache_key,
        "format": fmt,
        "filename": f"sample_{resolved_cache_key}.{ext}",
        "media_type": media_type,
        "content": content,
        "n_rows": len(rows),
        "columns": cols,
        "__message_to_model__": (
            f"Wrote a {fmt} table of {len(rows)} rows from "
            f"{resolved_cache_key!r}.  The full content is in the "
            "'content' field — include it as the final sample table in "
            "your reply (or write it to disk via run_python)."
        ),
    }
