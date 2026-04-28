"""Local semi-automatic maintenance inbox and report generation.

The maintenance system is deliberately conservative: it collects local
evidence, classifies likely issues, writes a report under a gitignored
directory, and exposes an agent lock.  It does not edit source files, push,
deploy, or create remote issues.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = 1
LOCK_STALE_SECONDS = 6 * 60 * 60


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_maintenance_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".local" / "maintenance"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(args: list[str], root: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}


def collect_git_context(root: Path | None = None) -> dict[str, Any]:
    """Collect local git state without mutating the worktree."""
    root = root or repo_root()
    status = _run_git(["status", "--short", "--branch"], root)
    head = _run_git(["rev-parse", "HEAD"], root)
    recent = _run_git(["log", "--oneline", "-5"], root)
    branch = _run_git(["branch", "--show-current"], root)
    dirty_lines = [
        line
        for line in status.get("stdout", "").splitlines()
        if line and not line.startswith("##")
    ]
    return {
        "branch": branch.get("stdout") or None,
        "head": head.get("stdout") or None,
        "status": status.get("stdout") or "",
        "dirty": bool(dirty_lines),
        "dirty_files": dirty_lines,
        "recent_commits": recent.get("stdout", "").splitlines(),
        "errors": [
            item.get("stderr")
            for item in (status, head, recent, branch)
            if not item.get("ok") and item.get("stderr")
        ],
    }


def collect_project_context(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    workflow_dir = root / ".github" / "workflows"
    workflows = sorted(
        str(path.relative_to(root))
        for path in workflow_dir.glob("*.yml")
    ) + sorted(
        str(path.relative_to(root))
        for path in workflow_dir.glob("*.yaml")
    )
    return {
        "render_yaml": (root / "render.yaml").exists(),
        "github_workflows": workflows,
        "pytest_ini": [
            str(path.relative_to(root))
            for path in (root / "pytest.ini", root / "backend" / "pytest.ini")
            if path.exists()
        ],
        "local_diagnostics_dir": str((root / ".local" / "diagnostics").relative_to(root)),
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_diagnostic_bundles(
    diagnostics_dir: str | Path | None = None,
    *,
    limit: int = 10,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Load newest local diagnostic bundles from a gitignored directory."""
    root = root or repo_root()
    directory = Path(diagnostics_dir) if diagnostics_dir is not None else root / ".local" / "diagnostics"
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    bundles: list[dict[str, Any]] = []
    for path in files[: max(0, limit)]:
        payload = _load_json(path)
        if payload is None:
            bundles.append({
                "path": str(path),
                "bundle_id": path.stem,
                "load_error": "invalid_json",
            })
            continue
        payload["_path"] = str(path)
        bundles.append(payload)
    return bundles


def replay_bundle_if_possible(bundle: dict[str, Any]) -> dict[str, Any] | None:
    path = bundle.get("_path")
    if not path:
        return None
    try:
        from app.services.local_diagnostics import replay_diagnostic_bundle

        return replay_diagnostic_bundle(path)
    except Exception as exc:
        return {"error": str(exc), "bundle_id": bundle.get("bundle_id")}


def summarize_diagnostic_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    tools = bundle.get("tool_timeline") or []
    if not isinstance(tools, list):
        tools = []
    statuses: dict[str, int] = {}
    tool_names: list[str] = []
    error_classes: dict[str, int] = {}
    line_measurement_count = 0
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("tool") or item.get("tool_name") or "unknown")
        tool_names.append(name)
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if isinstance(result, dict):
            status = str(result.get("__tool_status__") or result.get("analysis_status") or "").upper()
            if status:
                statuses[status] = statuses.get(status, 0) + 1
            err = str(result.get("error_class") or "").strip()
            if err:
                error_classes[err] = error_classes.get(err, 0) + 1
            count = result.get("line_measurement_count")
            if isinstance(count, int):
                line_measurement_count += max(0, count)
            summary = result.get("llm_summary")
            if isinstance(summary, dict) and isinstance(summary.get("line_measurement_count"), int):
                line_measurement_count += max(0, int(summary["line_measurement_count"]))
    reply = str(bundle.get("final_reply") or "")
    replay = replay_bundle_if_possible(bundle)
    return {
        "bundle_id": bundle.get("bundle_id"),
        "path": bundle.get("_path"),
        "created_at": bundle.get("created_at"),
        "prompt_preview": str(bundle.get("prompt") or "")[:240],
        "tool_count": len(tools),
        "tool_names": tool_names[:30],
        "statuses": statuses,
        "error_classes": error_classes,
        "line_measurement_count": line_measurement_count,
        "final_reply_empty": not bool(reply.strip()),
        "final_reply_trailing_colon": reply.rstrip().endswith(":"),
        "replay": replay,
    }


def _finding(
    *,
    title: str,
    severity: str,
    category: str,
    evidence: str,
    recommended_next_step: str,
    source: str | None = None,
) -> dict[str, str]:
    return {
        "id": f"MNT-{uuid.uuid4().hex[:8]}",
        "severity": severity,
        "category": category,
        "title": title,
        "evidence": evidence,
        "source": source or "",
        "recommended_next_step": recommended_next_step,
    }


def generate_findings(
    *,
    git_context: dict[str, Any],
    diagnostic_summaries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if git_context.get("dirty"):
        findings.append(_finding(
            title="Worktree has uncommitted changes",
            severity="medium",
            category="repo_hygiene",
            evidence="; ".join(git_context.get("dirty_files") or []),
            recommended_next_step=(
                "Review git diff before allowing any maintenance agent to edit files. "
                "Do not run automatic fixes until ownership is clear."
            ),
        ))

    if not diagnostic_summaries:
        findings.append(_finding(
            title="No local diagnostic bundles found",
            severity="info",
            category="diagnostics",
            evidence="No .local/diagnostics/*.json files were available.",
            recommended_next_step=(
                "Export a local diagnostic bundle after the next AI-assistant failure, "
                "then re-run maintenance report."
            ),
        ))

    for summary in diagnostic_summaries:
        source = str(summary.get("path") or summary.get("bundle_id") or "")
        statuses = summary.get("statuses") or {}
        errors = summary.get("error_classes") or {}
        replay = summary.get("replay") if isinstance(summary.get("replay"), dict) else {}
        if summary.get("final_reply_empty"):
            findings.append(_finding(
                title="Diagnostic ended with an empty assistant reply",
                severity="high",
                category="assistant_behavior",
                evidence=f"bundle={summary.get('bundle_id')} tool_count={summary.get('tool_count')}",
                source=source,
                recommended_next_step=(
                    "Replay the bundle locally and inspect chat loop termination / final-reply fallback."
                ),
            ))
        if summary.get("final_reply_trailing_colon"):
            findings.append(_finding(
                title="Assistant reply appears truncated mid-sentence",
                severity="high",
                category="assistant_behavior",
                evidence=f"bundle={summary.get('bundle_id')} final reply ends with ':'",
                source=source,
                recommended_next_step=(
                    "Check tool-quota exhaustion and end-of-response sanity fallback."
                ),
            ))
        if statuses.get("SYNTHETIC", 0) > 0:
            findings.append(_finding(
                title="Synthetic tool output was produced",
                severity="medium",
                category="provenance",
                evidence=f"SYNTHETIC count={statuses.get('SYNTHETIC')}",
                source=source,
                recommended_next_step=(
                    "Confirm the final reply did not use synthetic facts; if it did, patch the validator."
                ),
            ))
        failing_status_count = sum(int(statuses.get(key, 0)) for key in ("FAILED", "EMPTY", "UNAVAILABLE", "PARTIAL"))
        if failing_status_count:
            findings.append(_finding(
                title="Tool failures or incomplete results present",
                severity="medium",
                category="tooling",
                evidence=f"statuses={statuses} errors={errors}",
                source=source,
                recommended_next_step=(
                    "Classify failures into upstream, cache, sandbox, and validator issues before patching."
                ),
            ))
        if errors.get("missing_measurement_cache"):
            findings.append(_finding(
                title="Missing measurement cache",
                severity="high",
                category="data_cache",
                evidence=f"missing_measurement_cache count={errors['missing_measurement_cache']}",
                source=source,
                recommended_next_step=(
                    "Check literature-table cache persistence and fit_line_lfr cache-key handoff."
                ),
            ))
        if errors.get("sandbox_crash") or errors.get("subprocess_crash"):
            findings.append(_finding(
                title="Sandbox crash observed",
                severity="high",
                category="sandbox",
                evidence=f"errors={errors}",
                source=source,
                recommended_next_step=(
                    "Run sandbox health checks and inspect subprocess payload-completeness logs."
                ),
            ))
        if replay and replay.get("would_withhold"):
            findings.append(_finding(
                title="Local replay says final reply would be withheld",
                severity="high",
                category="validator",
                evidence=json.dumps({
                    "numeric_ok": replay.get("numeric_ok"),
                    "uncited_numeric_claims": len(replay.get("uncited_numeric_claims") or []),
                    "unsupported_narrative_violations": len(replay.get("unsupported_narrative_violations") or []),
                }, ensure_ascii=False),
                source=source,
                recommended_next_step=(
                    "Fix grounding or improve the user-facing withheld message before deployment."
                ),
            ))
        if summary.get("line_measurement_count", 0) > 0 and statuses.get("SYNTHETIC", 0) > 0:
            findings.append(_finding(
                title="Fit-ready measurements coexisted with synthetic fallback",
                severity="high",
                category="routing",
                evidence=(
                    f"line_measurement_count={summary.get('line_measurement_count')} "
                    f"synthetic_count={statuses.get('SYNTHETIC')}"
                ),
                source=source,
                recommended_next_step=(
                    "Ensure cached measurements route to fit_line_lfr and suppress synthetic run_python."
                ),
            ))
    return findings


def build_recommended_plan(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    if not findings:
        return [{
            "step": "No action",
            "status": "ready",
            "details": "No local findings were generated.",
        }]
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    ordered = sorted(findings, key=lambda item: severity_rank.get(item.get("severity", "info"), 5))
    return [
        {
            "step": f"Investigate {finding['title']}",
            "status": "pending",
            "details": finding["recommended_next_step"],
        }
        for finding in ordered[:8]
    ]


def build_maintenance_report(
    *,
    root: Path | None = None,
    diagnostics_dir: str | Path | None = None,
    diagnostic_limit: int = 10,
) -> dict[str, Any]:
    root = root or repo_root()
    git_context = collect_git_context(root)
    project_context = collect_project_context(root)
    bundles = load_diagnostic_bundles(diagnostics_dir, limit=diagnostic_limit, root=root)
    diagnostic_summaries = [summarize_diagnostic_bundle(bundle) for bundle in bundles]
    findings = generate_findings(
        git_context=git_context,
        diagnostic_summaries=diagnostic_summaries,
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "local_only": True,
        "automation_policy": {
            "edits_source_files": False,
            "pushes_to_remote": False,
            "deploys": False,
            "creates_remote_issues": False,
        },
        "guardrails": [
            "Acquire the local maintenance lock before allowing an agent to edit files.",
            "Show diff before commit.",
            "Run targeted tests and lint before push/deploy.",
            "Do not upload diagnostic bundles automatically.",
        ],
        "git": git_context,
        "project": project_context,
        "diagnostics": diagnostic_summaries,
        "findings": findings,
        "recommended_plan": build_recommended_plan(findings),
    }


def severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = str(finding.get("severity") or "unknown")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def render_markdown_report(report: dict[str, Any]) -> str:
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    diagnostics = report.get("diagnostics") if isinstance(report.get("diagnostics"), list) else []
    counts = severity_counts(findings)
    lines = [
        "# Maintenance Report",
        "",
        f"- Created: {report.get('created_at')}",
        f"- Local only: {report.get('local_only')}",
        f"- Branch: {report.get('git', {}).get('branch')}",
        f"- HEAD: {report.get('git', {}).get('head')}",
        f"- Dirty worktree: {report.get('git', {}).get('dirty')}",
        f"- Diagnostics loaded: {len(diagnostics)}",
        f"- Finding counts: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Guardrails",
    ]
    for item in report.get("guardrails") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Findings"])
    if not findings:
        lines.append("- No findings.")
    for finding in findings:
        source = f" `{finding.get('source')}`" if finding.get("source") else ""
        lines.append(
            f"- **{finding.get('severity', '').upper()} / {finding.get('category')}** "
            f"{finding.get('title')}{source}"
        )
        lines.append(f"  Evidence: {finding.get('evidence')}")
        lines.append(f"  Next: {finding.get('recommended_next_step')}")
    lines.extend(["", "## Recommended Plan"])
    for idx, step in enumerate(report.get("recommended_plan") or [], start=1):
        lines.append(f"{idx}. {step.get('step')} — {step.get('status')}")
        lines.append(f"   {step.get('details')}")
    lines.append("")
    return "\n".join(lines)


def write_maintenance_report(
    report: dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, str]:
    root = Path(output_dir) if output_dir is not None else default_maintenance_dir()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = root / f"maintenance-report-{stamp}.json"
    md_path = root / f"maintenance-report-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _lock_path(root: Path | None = None) -> Path:
    return default_maintenance_dir(root) / "agent.lock"


def read_lock(root: Path | None = None) -> dict[str, Any] | None:
    path = _lock_path(root)
    if not path.exists():
        return None
    payload = _load_json(path)
    if payload is None:
        return {"path": str(path), "invalid": True}
    payload["path"] = str(path)
    created = str(payload.get("created_at") or "")
    try:
        created_dt = datetime.fromisoformat(created)
        age = (datetime.now(timezone.utc) - created_dt).total_seconds()
    except Exception:
        age = None
    payload["age_seconds"] = age
    payload["stale"] = bool(age is not None and age > LOCK_STALE_SECONDS)
    return payload


def acquire_lock(
    *,
    owner: str,
    root: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_lock(root)
    if existing and not existing.get("stale") and not force:
        return {"acquired": False, "lock": existing}
    payload = {
        "owner": owner,
        "created_at": _utc_now(),
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "purpose": "maintenance_agent_write_lock",
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["path"] = str(path)
    return {"acquired": True, "lock": payload}


def release_lock(
    *,
    owner: str | None = None,
    root: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    path = _lock_path(root)
    existing = read_lock(root)
    if existing is None:
        return {"released": False, "reason": "no_lock"}
    if not force and owner and existing.get("owner") != owner:
        return {"released": False, "reason": "owner_mismatch", "lock": existing}
    path.unlink(missing_ok=True)
    return {"released": True, "previous_lock": existing}
