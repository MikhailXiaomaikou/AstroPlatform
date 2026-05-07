"""Local paper-to-tool mining loop runner.

The loop is intentionally local-first: it reads caller-supplied paper payloads,
processes them in fixed-size rounds, writes sanitized JSON bundles under
``.local/paper_tool_mining`` when requested, and tracks already-mined paper IDs.
It does not upload data or claim scientific conclusions.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.local_diagnostics import sanitize_for_diagnostic
from app.services.paper_tool_mining import run_paper_tool_mining_batch


DEFAULT_BATCH_SIZE = 20
DEFAULT_OUTPUT_DIR = ".local/paper_tool_mining"


def run_paper_tool_mining_loop_round(
    *,
    papers: list[dict[str, Any]],
    domain_tag: str = "observational_cosmology",
    state: dict[str, Any] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    output_dir: str | Path | None = None,
    write_local_bundle: bool = False,
) -> dict[str, Any]:
    """Run exactly one paper-mining round.

    A round selects the next ``batch_size`` unread papers, mines them, writes a
    local bundle if requested, and returns updated state.  The caller controls
    the paper list; this function does not perform external search itself.
    """
    clean_papers = [p for p in papers if isinstance(p, dict)]
    current_state = _normalize_state(state)
    read_ids = set(current_state.get("read_paper_ids", []))
    selected: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    for paper in clean_papers:
        paper_id = _paper_id(paper)
        if not paper_id or paper_id in read_ids:
            continue
        selected.append(paper)
        selected_ids.append(paper_id)
        if len(selected) >= max(1, int(batch_size or DEFAULT_BATCH_SIZE)):
            break

    round_index = int(current_state.get("round_index", 0)) + 1
    if not selected:
        result = {
            "success": True,
            "__tool_status__": "EMPTY",
            "analysis_status": "PAPER_TOOL_MINING_LOOP_EMPTY",
            "publication_ready": False,
            "__do_not_claim__": True,
            "round_index": round_index,
            "selected_paper_ids": [],
            "updated_state": current_state,
            "message": "No unread papers were available for this loop round.",
        }
        if write_local_bundle:
            result["bundle_path"] = str(_write_loop_bundle(result, output_dir))
        return result

    batch = run_paper_tool_mining_batch(
        papers=selected,
        domain_tag=domain_tag,
        max_papers=len(selected),
    )
    updated_state = {
        **current_state,
        "round_index": round_index,
        "domain_tag": domain_tag,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "read_paper_ids": sorted(read_ids | set(selected_ids)),
        "round_history": [
            *list(current_state.get("round_history", []))[-24:],
            {
                "round_index": round_index,
                "paper_ids": selected_ids,
                "tool_spec_count": batch.get("tool_spec_count", 0),
                "mined_paper_count": batch.get("mined_paper_count", 0),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
    }
    result = {
        "success": True,
        "__tool_status__": batch.get("__tool_status__", "COMPLETED"),
        "analysis_status": "PAPER_TOOL_MINING_LOOP_ROUND_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "round_index": round_index,
        "batch_size": len(selected),
        "selected_paper_ids": selected_ids,
        "remaining_unread": max(0, len(clean_papers) - len(updated_state["read_paper_ids"])),
        "batch_result": batch,
        "updated_state": updated_state,
        "warnings": [
            "Loop output is an infrastructure/tooling map, not evidence for scientific claims.",
            "Bundles are local-only when write_local_bundle=true.",
        ],
        "__message_to_model__": (
            "Summarize this as a paper-to-tool mining round. Do not report posterior, "
            "fit, or paper-conclusion numbers from it."
        ),
    }
    if write_local_bundle:
        result["bundle_path"] = str(_write_loop_bundle(result, output_dir))
    return result


def run_paper_tool_mining_loop(
    *,
    papers: list[dict[str, Any]],
    domain_tag: str = "observational_cosmology",
    state: dict[str, Any] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rounds: int = 1,
    output_dir: str | Path | None = None,
    write_local_bundle: bool = False,
) -> dict[str, Any]:
    """Run bounded consecutive mining rounds.

    ``max_rounds`` is required so a chat/tool call cannot accidentally start an
    unbounded loop.  Schedulers can call this repeatedly.
    """
    current_state = _normalize_state(state)
    rounds: list[dict[str, Any]] = []
    for _ in range(max(1, min(int(max_rounds or 1), 50))):
        round_result = run_paper_tool_mining_loop_round(
            papers=papers,
            domain_tag=domain_tag,
            state=current_state,
            batch_size=batch_size,
            output_dir=output_dir,
            write_local_bundle=write_local_bundle,
        )
        rounds.append(round_result)
        current_state = round_result.get("updated_state", current_state)
        if round_result.get("analysis_status") == "PAPER_TOOL_MINING_LOOP_EMPTY":
            break
    aggregate_queue: list[dict[str, Any]] = []
    for item in rounds:
        batch = item.get("batch_result") if isinstance(item.get("batch_result"), dict) else {}
        aggregate_queue.extend(batch.get("implementation_queue", []) if isinstance(batch, dict) else [])
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if rounds else "EMPTY",
        "analysis_status": "PAPER_TOOL_MINING_LOOP_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "rounds_run": len(rounds),
        "rounds": rounds,
        "updated_state": current_state,
        "aggregate_implementation_queue": _dedupe_queue(aggregate_queue)[:20],
        "__message_to_model__": "This is a bounded paper-mining loop result, not scientific evidence.",
    }


def read_loop_state(path: str | Path | None = None) -> dict[str, Any]:
    state_path = _state_path(path)
    if not state_path.exists():
        return _merge_state_with_bundles(_normalize_state(None), state_path.parent)
    try:
        return _merge_state_with_bundles(
            _normalize_state(json.loads(state_path.read_text(encoding="utf-8"))),
            state_path.parent,
        )
    except Exception:
        return _merge_state_with_bundles(_normalize_state(None), state_path.parent)


def write_loop_state(state: dict[str, Any], path: str | Path | None = None) -> Path:
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(sanitize_for_diagnostic(_normalize_state(state)), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return state_path


def _normalize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        state = {}
    read_ids = [str(x) for x in state.get("read_paper_ids", []) if str(x).strip()]
    history = state.get("round_history") if isinstance(state.get("round_history"), list) else []
    return {
        "schema_version": 1,
        "loop_id": str(state.get("loop_id") or uuid.uuid4()),
        "round_index": int(state.get("round_index") or 0),
        "domain_tag": str(state.get("domain_tag") or "observational_cosmology"),
        "read_paper_ids": sorted(set(read_ids)),
        "round_history": history,
        "updated_at": str(state.get("updated_at") or ""),
        "local_only": True,
    }


def _merge_state_with_bundles(state: dict[str, Any], bundle_dir: Path) -> dict[str, Any]:
    """Recover loop state from local round bundles.

    Local loops can be interrupted or run from stale state.  Round bundles are
    append-only diagnostics, so they are the safer source of already-read paper
    IDs.  This merge keeps future rounds from re-reading papers after a partial
    or interrupted run overwrote ``state.json``.
    """
    if not bundle_dir.exists():
        return state
    read_ids: set[str] = set(state.get("read_paper_ids", []))
    history_by_round: dict[int, dict[str, Any]] = {}
    for item in state.get("round_history", []):
        if isinstance(item, dict):
            try:
                history_by_round[int(item.get("round_index") or 0)] = item
            except Exception:
                continue
    max_round = int(state.get("round_index") or 0)
    for path in sorted(bundle_dir.glob("round_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        round_index = int(payload.get("round_index") or _round_index_from_path(path) or 0)
        selected_ids = [str(x) for x in payload.get("selected_paper_ids", []) if str(x).strip()]
        if not selected_ids:
            hist = payload.get("updated_state", {}).get("round_history", [])
            if isinstance(hist, list) and hist:
                selected_ids = [str(x) for x in hist[-1].get("paper_ids", []) if str(x).strip()]
        if selected_ids:
            read_ids.update(selected_ids)
            history_by_round[round_index] = {
                "round_index": round_index,
                "paper_ids": selected_ids,
                "tool_spec_count": (
                    payload.get("batch_result", {}).get("tool_spec_count", 0)
                    if isinstance(payload.get("batch_result"), dict)
                    else 0
                ),
                "mined_paper_count": (
                    payload.get("batch_result", {}).get("mined_paper_count", 0)
                    if isinstance(payload.get("batch_result"), dict)
                    else 0
                ),
                "created_at": payload.get("updated_state", {}).get("updated_at") or payload.get("created_at") or "",
            }
            max_round = max(max_round, round_index)
    return {
        **state,
        "round_index": max_round,
        "read_paper_ids": sorted(read_ids),
        "round_history": [history_by_round[k] for k in sorted(history_by_round)][-24:],
    }


def _round_index_from_path(path: Path) -> int:
    try:
        return int(path.stem.split("_")[-1])
    except Exception:
        return 0


def _paper_id(paper: dict[str, Any]) -> str:
    for key in ("arxiv_id", "doi", "bibcode", "paper_id", "title"):
        value = str(paper.get(key) or "").strip()
        if value:
            return value
    nested = paper.get("paper_metadata")
    if isinstance(nested, dict):
        return _paper_id(nested)
    return ""


def _state_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    return _repo_root() / DEFAULT_OUTPUT_DIR / "state.json"


def _write_loop_bundle(result: dict[str, Any], output_dir: str | Path | None) -> Path:
    root = Path(output_dir) if output_dir is not None else _repo_root() / DEFAULT_OUTPUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    round_index = int(result.get("round_index") or 0)
    path = root / f"round_{round_index:04d}.json"
    path.write_text(
        json.dumps(sanitize_for_diagnostic(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    state = result.get("updated_state")
    if isinstance(state, dict):
        write_loop_state(state, root / "state.json")
    return path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _dedupe_queue(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in queue:
        capability = str(item.get("capability") or "")
        if not capability or capability in seen:
            continue
        seen.add(capability)
        result.append(item)
    return result
