"""SSE wire-payload shaping: tool-result slimming that preserves provenance.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

import json
from typing import Any


_SSE_FIELD_BIBCODES_PER_COLUMN = 50


def _bounded_provenance_for_sse(provenance: Any) -> Any:
    """Bound the provenance block for the SSE wire WITHOUT dropping lineage.

    Audit 2026-07-03: slimming used to drop the entire provenance block
    (datasets, field bibcodes, reproducibility envelope) from any tool result
    over the SSE size cap — exactly the large archive queries a cosmologist
    needs lineage for, and the loss was persisted via /sessions/save.  The
    only piece that scales with row count is `field_bibcodes.columns` (one
    entry per row), so dedupe each column's list (order-preserving) and cap
    it; everything else (datasets are already deduped, the reproducibility
    envelope and coverage are small fixed dicts) passes through unchanged.
    Never mutates the input — the same dict is shared with the actions list
    and session persistence.
    """
    if not isinstance(provenance, dict):
        return provenance
    slim_prov = dict(provenance)
    fb = slim_prov.get("field_bibcodes")
    if isinstance(fb, dict) and isinstance(fb.get("columns"), dict):
        fb = dict(fb)
        bounded_columns: dict[str, Any] = {}
        truncated: dict[str, int] = {}
        for column, values in fb["columns"].items():
            if not isinstance(values, list):
                bounded_columns[column] = values
                continue
            deduped = list(dict.fromkeys(str(v) for v in values))
            if len(deduped) > _SSE_FIELD_BIBCODES_PER_COLUMN:
                truncated[str(column)] = (
                    len(deduped) - _SSE_FIELD_BIBCODES_PER_COLUMN
                )
                deduped = deduped[:_SSE_FIELD_BIBCODES_PER_COLUMN]
            bounded_columns[column] = deduped
        fb["columns"] = bounded_columns
        if truncated:
            # Honest marker: N distinct bibcodes were cut from the wire copy
            # (the full list stays in the server-side tool result).
            fb["columns_truncated_for_stream"] = truncated
        slim_prov["field_bibcodes"] = fb
    return slim_prov


def _slim_tool_result_for_sse(result: Any, *, max_bytes: int = 8000) -> Any:
    """Shrink large tool results before putting them on the live SSE wire.

    The backend already gives the model a bounded tool-result block.  The
    browser stream has a different constraint: it only needs enough structure
    to render cards/panels and diagnostics.  Sending full rows/figures/stdout
    after final text can keep the response open for minutes, leaving the Chat
    UI stuck in "Stop" state even though the answer is visible.
    """
    try:
        raw = json.dumps(result, default=str)
    except (TypeError, ValueError):
        return result
    if len(raw) <= max_bytes or not isinstance(result, dict):
        return result

    src = dict(result)
    keep = {
        "success", "error", "error_class",
        "stderr", "stderr_note", "traceback",
        "exit_code", "backend", "duration_ms",
        "mode", "auto_escalated_mode", "note",
        "analysis_status", "data_origin",
        "__tool_status__", "__do_not_claim__",
        "__message_to_model__",
        "row_count", "columns", "meta",
        # Audit 2026-07-03: the reproducibility envelope (run_id / query_hash
        # / archive_version / seed) is small and load-bearing for the
        # provenance UI — it must survive slimming.
        "reproducibility",
    }
    slim = {k: src[k] for k in keep if k in src}
    # Audit 2026-07-03: preserve the data-lineage block (datasets, field
    # bibcodes, reproducibility, coverage); only its per-row bibcode lists
    # are bounded.  Dropping it blanked the Data Sources panel for exactly
    # the >8 KB archive results that most need lineage.
    if isinstance(src.get("provenance"), dict):
        slim["provenance"] = _bounded_provenance_for_sse(src["provenance"])
    status = str(src.get("analysis_status") or "")

    if status == "RESEARCH_PLAN_READY" and isinstance(src.get("research_plan"), dict):
        slim["research_plan"] = src["research_plan"]
        for key in ("plan_id", "dataset_count", "matrix_size", "publication_ready"):
            if key in src:
                slim[key] = src[key]

    if status.startswith("RESEARCH_MATRIX") and isinstance(src.get("matrix"), list):
        compact_cells: list[dict[str, Any]] = []
        for cell in src.get("matrix", [])[:10]:
            if not isinstance(cell, dict):
                continue
            result_obj = cell.get("result") if isinstance(cell.get("result"), dict) else {}
            params = (
                result_obj.get("parameters")
                if isinstance(result_obj, dict) and isinstance(result_obj.get("parameters"), dict)
                else {}
            )
            diagnostics = (
                result_obj.get("chain_diagnostics")
                if isinstance(result_obj, dict) and isinstance(result_obj.get("chain_diagnostics"), dict)
                else {}
            )
            compact_cells.append({
                "label": cell.get("label"),
                "model": cell.get("model"),
                "dataset_keys": cell.get("dataset_keys"),
                "publication_ready": cell.get("publication_ready"),
                "runnable": cell.get("runnable"),
                "execution_level": cell.get("execution_level"),
                "warnings": cell.get("warnings"),
                "result": {
                    "publication_ready": result_obj.get("publication_ready") if isinstance(result_obj, dict) else None,
                    "parameters": {
                        name: params.get(name)
                        for name in ("H0", "omegam", "sigma8", "S8", "rd", "H0_rd")
                        if isinstance(params, dict) and name in params
                    },
                    "chain_diagnostics": diagnostics,
                    "datasets_not_run": (
                        result_obj.get("datasets_not_run")
                        if isinstance(result_obj, dict)
                        else None
                    ),
                },
            })
        slim["matrix"] = compact_cells
        for key in ("matrix_size", "ready_cells", "publication_ready", "claim_scope"):
            if key in src:
                slim[key] = src[key]

    if status == "EVIDENCE_GRAPH_READY" and isinstance(src.get("evidence_graph"), dict):
        graph = src["evidence_graph"]
        slim["evidence_graph"] = {
            "claimable_parameters": graph.get("claimable_parameters"),
            "supported_claims": graph.get("supported_claims"),
            "unsupported_claims": graph.get("unsupported_claims"),
        }
        if "claimable_parameters" in src:
            slim["claimable_parameters"] = src["claimable_parameters"]

    for big_key in ("rows", "data", "figures", "variables", "variable_types", "stdout"):
        if big_key not in src:
            continue
        try:
            value = src[big_key]
            if isinstance(value, (list, tuple)):
                slim[f"{big_key}__preview__"] = {"n_items": len(value), "truncated": True}
            elif isinstance(value, dict):
                slim[f"{big_key}__preview__"] = {"n_keys": len(value), "truncated": True}
            elif isinstance(value, str) and len(value) > 2000:
                slim[big_key] = value[:2000] + "…[truncated]"
            else:
                slim[big_key] = value
        except Exception:
            pass

    slim["__preview__"] = True
    slim["__original_size__"] = len(raw)
    return slim


def _trim_large_tool_results(messages: list[dict]) -> list[dict]:
    """G6.2: shrink oversized tool_result / assistant content so the full
    message array stays under Anthropic's ~200 KB prompt cap.

    Rule: any single content block whose JSON serialization exceeds 30 KB
    is replaced with a summary stub {shape, preview, note}.  Preserves the
    structural role/content shape so the downstream LLM still sees a valid
    tool_result — just much smaller.
    """
    import json as _json

    PER_BLOCK_MAX = 30_000

    def _shrink_string(s: str) -> str:
        if len(s) <= PER_BLOCK_MAX:
            return s
        return (
            s[:8000]
            + f"\n...[TRIMMED by pre-flight: {len(s) - 8000} chars dropped; "
            + f"original was {len(s)} chars. This tool_result was from a "
            + "previous turn. If you need to re-read it, ask the user to "
            + "re-run the tool or start a new chat.]"
        )

    out: list[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            out.append({**m, "content": _shrink_string(content)})
            continue
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        raw = block.get("content")
                        if isinstance(raw, str) and len(raw) > PER_BLOCK_MAX:
                            new_blocks.append({**block, "content": _shrink_string(raw)})
                            continue
                    block_str = _json.dumps(block, default=str)
                    if len(block_str) > PER_BLOCK_MAX:
                        new_blocks.append({
                            "type": block.get("type", "text"),
                            "text": (
                                f"[TRIMMED content block, original size "
                                f"{len(block_str)} bytes; see session history for full data]"
                            ),
                        })
                        continue
                new_blocks.append(block)
            out.append({**m, "content": new_blocks})
            continue
        out.append(m)
    return out
