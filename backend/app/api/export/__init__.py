"""Export endpoints for pipeline run results (CSV, VOTable, FITS, PDF)."""

import csv
import io
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user
from app.models.database import get_db
from app.models.schemas import ChatSession, PipelineRun, RunResult, User

router = APIRouter(prefix="/api/export", tags=["export"])


# ---------- Chat-export shared helpers (markdown / notebook / html) ----------

# Per-cell stdout truncation threshold. The culprit behind 100+ page reports
# is a handful of run_python calls that print thousands of rows or large tables.
# Anything beyond this limit is tail-truncated, keeping head + tail with a
# total line count.
_STDOUT_HEAD_LINES = 30
_STDOUT_TAIL_LINES = 5


def _truncate_stdout(stdout: str) -> str:
    """Truncate long stdout (thousands of lines) to head + tail, with a single ellipsis line in between."""
    if not stdout:
        return ""
    lines = stdout.splitlines()
    if len(lines) <= _STDOUT_HEAD_LINES + _STDOUT_TAIL_LINES + 1:
        return stdout.rstrip()
    head = lines[:_STDOUT_HEAD_LINES]
    tail = lines[-_STDOUT_TAIL_LINES:]
    skipped = len(lines) - _STDOUT_HEAD_LINES - _STDOUT_TAIL_LINES
    return "\n".join(
        head + [f"... ({skipped} lines truncated) ..."] + tail
    )


def _action_summary_one_liner(action: dict) -> str | None:
    """One-liner summary for non-run_python actions, replacing the previous full params dump.

    Returns None if the action is not worth showing in an export (e.g. unknown
    or system-level metadata). Filtering these out can compress long-session
    exports to 1/10 of their original size."""
    name = str(action.get("action") or "").strip()
    if not name or name in {"agent_text", "honest_abstention", "tool_progress"}:
        return None

    result = action.get("tool_result")
    if isinstance(result, dict):
        # Prefer the result-level summary (the tool's own summary field is most reliable).
        summary = result.get("summary") or result.get("note")
        if isinstance(summary, str) and summary.strip():
            short = summary.strip().replace("\n", " ")
            return f"`{name}` — {short[:200]}"
        # For run_adql / search_objects style results, extract row_count + columns.
        if "row_count" in result:
            row_count = result.get("row_count")
            cols = result.get("columns")
            cols_preview = (
                ", ".join(str(c) for c in cols[:6]) + ("…" if isinstance(cols, list) and len(cols) > 6 else "")
                if isinstance(cols, list) else ""
            )
            tag = f"`{name}` → {row_count} rows"
            if cols_preview:
                tag += f" ({cols_preview})"
            return tag
        if "results" in result and isinstance(result["results"], list):
            return f"`{name}` → {len(result['results'])} matches"
        # Surface error status explicitly.
        err = result.get("error")
        if isinstance(err, str) and err.strip():
            return f"`{name}` → ⚠ {err.strip()[:200]}"
        status = result.get("__tool_status__")
        if isinstance(status, str) and status:
            return f"`{name}` → status={status}"
    return f"`{name}` (no summary)"


def _figures_already_present(actions: list[dict]) -> bool:
    """Return True if at least one run_python action in the submitted list
    already carries figure bytes, so we can skip the DB lookup.
    This is a fast-path short-circuit for the figures-offload fallback."""
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if action.get("action") != "run_python":
            continue
        result = action.get("tool_result")
        if isinstance(result, dict):
            figs = result.get("figures")
            if isinstance(figs, list) and any(isinstance(f, str) and f for f in figs):
                return True
    return False


def _actions_need_rehydrate(actions: list[dict]) -> bool:
    """Return True if the frontend has pruned figures from localStorage,
    leaving behind a __figures_offloaded__ or __offloaded__ marker (PART X).
    Detecting either marker means we need to pull the figures back from the DB."""
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        result = action.get("tool_result")
        if not isinstance(result, dict):
            continue
        if result.get("__offloaded__") is True:
            return True
        if isinstance(result.get("__figures_offloaded__"), int):
            return True
    return False


async def _rehydrate_messages_from_db(
    messages: list[dict],
    session_id: str | None,
    user: User | None,
    db: AsyncSession,
) -> list[dict]:
    """If figures have been offloaded from the frontend (localStorage 4 MB
    soft cap), use session_id + authenticated user to pull the full messages
    back from the chat_sessions DB.

    The server-side chat_sessions.messages JSON field is never pruned, so
    figures base64 is still there. Matching rule: align by message index +
    equal action counts on both sides + matching action names, then copy
    DB tool_result.figures onto the current actions (all other fields are kept
    from the frontend version since they may have been updated)."""
    if not session_id or user is None:
        return messages
    all_actions = [
        a for m in messages if isinstance(m, dict) for a in (m.get("actions") or [])
    ]
    if not _actions_need_rehydrate(all_actions) and _figures_already_present(
        all_actions
    ):
        return messages

    try:
        sid = uuid.UUID(str(session_id))
    except (ValueError, TypeError, AttributeError):
        return messages

    row = (
        await db.execute(
            select(ChatSession).where(
                ChatSession.id == sid,
                ChatSession.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.messages:
        return messages

    db_messages = row.messages
    out: list[dict] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        front_actions = msg.get("actions") or []
        if idx >= len(db_messages):
            out.append(msg)
            continue
        db_msg = db_messages[idx]
        if not isinstance(db_msg, dict):
            out.append(msg)
            continue
        db_actions = db_msg.get("actions") or []
        if len(db_actions) != len(front_actions):
            # If counts differ, do not force a match to avoid attaching the wrong figures to an unrelated cell.
            out.append(msg)
            continue
        rebuilt: list[dict] = []
        for f_act, d_act in zip(front_actions, db_actions, strict=False):
            if not isinstance(f_act, dict) or not isinstance(d_act, dict):
                rebuilt.append(f_act)
                continue
            if f_act.get("action") != d_act.get("action"):
                rebuilt.append(f_act)
                continue
            f_result = f_act.get("tool_result") if isinstance(f_act.get("tool_result"), dict) else None
            d_result = d_act.get("tool_result") if isinstance(d_act.get("tool_result"), dict) else None
            if not d_result:
                rebuilt.append(f_act)
                continue
            db_figures = d_result.get("figures")
            if not isinstance(db_figures, list) or not any(
                isinstance(f, str) and f for f in db_figures
            ):
                rebuilt.append(f_act)
                continue
            new_result = dict(f_result or {})
            # Only overwrite the figures field; all other fields defer to the frontend version.
            new_result["figures"] = db_figures
            new_result.pop("__figures_offloaded__", None)
            new_result.pop("__offloaded__", None)
            rebuilt.append({**f_act, "tool_result": new_result})
        out.append({**msg, "actions": rebuilt})
    return out


def _normalize_figure_to_data_uri(fig: str) -> str | None:
    """Normalize the two ways figures are stored: 'data:image/png;base64,XXX' or bare base64."""
    if not isinstance(fig, str) or not fig:
        return None
    if fig.startswith("data:image"):
        return fig
    return f"data:image/png;base64,{fig}"


def _normalize_figure_to_bare_base64(fig: str) -> str | None:
    """Jupyter display_data image/png must be bare base64 (no data-URI prefix)."""
    if not isinstance(fig, str) or not fig:
        return None
    if fig.startswith("data:image"):
        _, _, b64 = fig.partition(",")
        return b64 or None
    return fig


async def _get_run_and_results(
    run_id: str,
    user: User,
    db: AsyncSession,
) -> tuple[PipelineRun, list[RunResult]]:
    """Fetch a pipeline run and its results, verifying ownership."""
    run_uuid = uuid.UUID(run_id)
    run = (
        await db.execute(
            select(PipelineRun).where(
                PipelineRun.id == run_uuid,
                PipelineRun.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    results = (
        (
            await db.execute(
                select(RunResult)
                .where(RunResult.run_id == run_uuid)
                .order_by(RunResult.node_id)
            )
        )
        .scalars()
        .all()
    )
    return run, list(results)


def _node_type_map(run: PipelineRun) -> dict[str, str]:
    """Build {node_id: node_type} from the DAG definition."""
    dag = run.dag or {}
    return {n["id"]: n.get("type", "unknown") for n in dag.get("nodes", [])}


def _extract_tabular_nodes(
    results_json: dict | None,
) -> list[tuple[str, dict[str, list]]]:
    """Return [(node_id, {col_name: [values]})] for nodes with tabular data.

    A node result qualifies as tabular when it has a ``"data"`` key whose
    value is a dict where every value is a list (columns of equal length).
    """
    if not results_json:
        return []
    tabular: list[tuple[str, dict[str, list]]] = []
    for node_id, node_out in results_json.items():
        if not isinstance(node_out, dict):
            continue
        data = node_out.get("data")
        if not isinstance(data, dict) or not data:
            continue
        # Every value must be a list (column arrays)
        if all(isinstance(v, list) for v in data.values()):
            tabular.append((node_id, data))
    return tabular


# ── CSV Export ──


@router.get("/run/{run_id}/csv")
async def export_run_csv(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export pipeline run results as a CSV file download."""
    run, results = await _get_run_and_results(run_id, user, db)
    type_map = _node_type_map(run)

    buf = io.StringIO()
    writer = csv.writer(buf)

    # ── Run metadata header ──
    writer.writerow(["run_id", "status", "created_at", "completed_at"])
    writer.writerow([
        str(run.id),
        run.status,
        run.created_at.isoformat() if run.created_at else "",
        run.completed_at.isoformat() if run.completed_at else "",
    ])
    writer.writerow([])

    # ── Per-node metadata from RunResult rows ──
    writer.writerow(["node_id", "output_path", "logs"])
    for r in results:
        writer.writerow([r.node_id, r.output_path or "", r.logs or ""])

    # ── Per-node tabular data from PipelineRun.results ──
    tabular = _extract_tabular_nodes(run.results)
    for node_id, data in tabular:
        node_type = type_map.get(node_id, "unknown")
        writer.writerow([])
        writer.writerow([f"# Node: {node_id} ({node_type})"])
        columns = list(data.keys())
        writer.writerow(columns)
        n_rows = max(len(v) for v in data.values()) if data else 0
        for i in range(n_rows):
            writer.writerow([
                data[col][i] if i < len(data[col]) else ""
                for col in columns
            ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="run_{run_id}.csv"'
        },
    )


# ── VOTable Export ──


@router.get("/run/{run_id}/votable")
async def export_run_votable(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export pipeline run results as a VOTable XML file (astronomy standard)."""
    run, results = await _get_run_and_results(run_id, user, db)
    type_map = _node_type_map(run)

    from astropy.table import Table
    from astropy.io.votable import from_table, writeto
    from astropy.io.votable.tree import Resource

    # ── Metadata TABLE (existing behaviour) ──
    rows = []
    for r in results:
        rows.append({
            "node_id": r.node_id,
            "output_path": r.output_path or "",
            "logs": r.logs or "",
        })

    if rows:
        meta_table = Table(
            {
                "node_id": [row["node_id"] for row in rows],
                "output_path": [row["output_path"] for row in rows],
                "logs": [row["logs"] for row in rows],
            }
        )
    else:
        meta_table = Table(
            names=["node_id", "output_path", "logs"],
            dtype=["U256", "U1024", "U4096"],
        )

    meta_table.meta["run_id"] = str(run.id)
    meta_table.meta["status"] = run.status
    meta_table.meta["created_at"] = (
        run.created_at.isoformat() if run.created_at else ""
    )
    meta_table.meta["completed_at"] = (
        run.completed_at.isoformat() if run.completed_at else ""
    )

    # Build the VOTable with the metadata table first
    votable = from_table(meta_table)

    # ── Data TABLEs from PipelineRun.results ──
    tabular = _extract_tabular_nodes(run.results)
    if tabular:
        resource = votable.resources[0] if votable.resources else votable.resources.append(Resource())

        for node_id, data in tabular:
            node_type = type_map.get(node_id, "unknown")
            col_data = {}
            for col_name, values in data.items():
                arr = np.array(values)
                col_data[col_name] = arr
            data_table = Table(col_data)
            data_table.meta["node_id"] = node_id
            data_table.meta["node_type"] = node_type

            vot_from = from_table(data_table)
            # Move the TABLE element into our existing resource
            if vot_from.resources:
                for tbl in vot_from.resources[0].tables:
                    resource.tables.append(tbl)

    buf = io.BytesIO()
    writeto(votable, buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="run_{run_id}.votable.xml"'
        },
    )


# ── FITS Export ──


@router.get("/run/{run_id}/fits")
async def export_run_fits(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export pipeline run results as a FITS file with binary table extensions."""
    from astropy.io import fits
    from astropy.table import Table

    run, results = await _get_run_and_results(run_id, user, db)
    type_map = _node_type_map(run)

    hdu_list = [fits.PrimaryHDU()]
    # Store run metadata in the primary header
    hdu_list[0].header["RUN_ID"] = str(run.id)
    hdu_list[0].header["STATUS"] = run.status or ""
    hdu_list[0].header["CREATED"] = (
        run.created_at.isoformat() if run.created_at else ""
    )
    hdu_list[0].header["COMPLETE"] = (
        run.completed_at.isoformat() if run.completed_at else ""
    )

    tabular = _extract_tabular_nodes(run.results)
    for node_id, data in tabular:
        node_type = type_map.get(node_id, "unknown")
        col_data = {}
        for col_name, values in data.items():
            col_data[col_name] = np.array(values)
        astro_table = Table(col_data)
        bin_hdu = fits.BinTableHDU(astro_table, name=node_id[:68])
        bin_hdu.header["NODEID"] = node_id
        bin_hdu.header["NODETYPE"] = node_type
        hdu_list.append(bin_hdu)

    if len(hdu_list) == 1:
        # No tabular data — add an empty extension so the file is valid
        hdu_list.append(fits.BinTableHDU.from_columns([]))

    hdul = fits.HDUList(hdu_list)
    buf = io.BytesIO()
    hdul.writeto(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/fits",
        headers={
            "Content-Disposition": f'attachment; filename="run_{run_id}.fits"'
        },
    )


# ── PDF Export ──


def _build_report_html(run: PipelineRun, results: list[RunResult]) -> str:
    """Build an HTML string summarizing the pipeline run."""
    created = run.created_at.isoformat() if run.created_at else "N/A"
    completed = run.completed_at.isoformat() if run.completed_at else "N/A"

    rows_html = ""
    for r in results:
        def _esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        logs_escaped = _esc(r.logs or "")
        nid_escaped = _esc(r.node_id or "")
        path_escaped = _esc(r.output_path or "")
        rows_html += (
            f"<tr>"
            f"<td>{nid_escaped}</td>"
            f"<td>{path_escaped}</td>"
            f"<td><pre>{logs_escaped}</pre></td>"
            f"</tr>"
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Pipeline Run Report — {run.id}</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; margin: 40px; color: #222; }}
  h1 {{ font-size: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
  th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
  th {{ background: #f0f0f0; }}
  pre {{ margin: 0; white-space: pre-wrap; font-size: 12px; }}
  .meta {{ margin-bottom: 24px; }}
  .meta dt {{ font-weight: bold; float: left; width: 120px; }}
  .meta dd {{ margin-left: 130px; margin-bottom: 4px; }}
</style>
</head>
<body>
<h1>Pipeline Run Report</h1>
<dl class="meta">
  <dt>Run ID</dt><dd>{run.id}</dd>
  <dt>Status</dt><dd>{run.status}</dd>
  <dt>Created</dt><dd>{created}</dd>
  <dt>Completed</dt><dd>{completed}</dd>
</dl>
<h2>Node Results</h2>
<table>
  <thead><tr><th>Node ID</th><th>Output Path</th><th>Logs</th></tr></thead>
  <tbody>{rows_html if rows_html else '<tr><td colspan="3">No results</td></tr>'}</tbody>
</table>
<p style="margin-top:24px;font-size:11px;color:#888;">
  Generated by Astro Research Platform on {datetime.now(timezone.utc).isoformat()}
</p>
</body>
</html>"""


@router.get("/run/{run_id}/pdf")
async def export_run_pdf(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export pipeline run results as a PDF report.

    Uses reportlab if available; otherwise falls back to an HTML download.
    """
    run, results = await _get_run_and_results(run_id, user, db)

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table as RLTable,
            TableStyle,
            Paragraph,
            Spacer,
        )
        from reportlab.lib.styles import getSampleStyleSheet

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        styles = getSampleStyleSheet()
        elements: list = []

        elements.append(Paragraph("Pipeline Run Report", styles["Title"]))
        elements.append(Spacer(1, 12))

        created = run.created_at.isoformat() if run.created_at else "N/A"
        completed = run.completed_at.isoformat() if run.completed_at else "N/A"
        meta_lines = [
            f"<b>Run ID:</b> {run.id}",
            f"<b>Status:</b> {run.status}",
            f"<b>Created:</b> {created}",
            f"<b>Completed:</b> {completed}",
        ]
        for line in meta_lines:
            elements.append(Paragraph(line, styles["Normal"]))
        elements.append(Spacer(1, 16))

        elements.append(Paragraph("Node Results", styles["Heading2"]))
        elements.append(Spacer(1, 8))

        table_data = [["Node ID", "Output Path", "Logs"]]
        for r in results:
            table_data.append([
                r.node_id,
                r.output_path or "",
                (r.logs or "")[:200],
            ])
        if len(table_data) == 1:
            table_data.append(["No results", "", ""])

        t = RLTable(table_data, repeatRows=1)
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        elements.append(t)

        doc.build(elements)
        buf.seek(0)

        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="run_{run_id}.pdf"'
            },
        )

    except ImportError:
        # Fallback: return HTML report
        html = _build_report_html(run, results)
        return StreamingResponse(
            iter([html]),
            media_type="text/html",
            headers={
                "Content-Disposition": f'attachment; filename="run_{run_id}.html"'
            },
        )


# ── Analysis Report Export ──

from pydantic import BaseModel as _BaseModel  # noqa: E402


class ReportRequest(_BaseModel):
    analysis: dict  # SpectrumAnalysis result from /fits/analyze
    object_name: str = ""
    fits_path: str = ""


@router.post("/report/markdown")
async def export_analysis_markdown(req: ReportRequest):
    """Export an AI spectrum analysis as a Markdown report."""
    a = req.analysis
    lines = [
        "# Spectrum Analysis Report",
        "",
        f"**Object:** {req.object_name or 'Unknown'}",
        f"**FITS file:** `{req.fits_path}`",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    if a.get("ai_classification"):
        lines.append("## Classification")
        lines.append("")
        lines.append(f"**{a['ai_classification']}** (confidence: {a.get('ai_confidence', 'unknown')})")
        lines.append("")

    if a.get("ai_redshift") and a["ai_redshift"].get("value") is not None:
        z = a["ai_redshift"]
        lines.append(f"**Redshift:** z = {z['value']:.6f}" + (f" ± {z['uncertainty']:.6f}" if z.get("uncertainty") else ""))
        lines.append("")

    if a.get("ai_summary"):
        lines.append("## Summary")
        lines.append("")
        lines.append(a["ai_summary"])
        lines.append("")

    if a.get("ai_narrative"):
        lines.append("## Detailed Analysis")
        lines.append("")
        lines.append(a["ai_narrative"])
        lines.append("")

    ai_lines = a.get("ai_lines", [])
    if ai_lines:
        lines.append("## Identified Spectral Lines")
        lines.append("")
        lines.append("| Line | Rest (Å) | Observed (Å) | Type | Strength |")
        lines.append("|------|----------|-------------|------|----------|")
        for ln in ai_lines:
            rw = ln.get('rest_wavelength')
            ow = ln.get('observed_wavelength')
            lines.append(
                f"| {ln.get('name','')} | {f'{rw:.1f}' if isinstance(rw, (int,float)) else ''} | "
                f"{f'{ow:.1f}' if isinstance(ow, (int,float)) else ''} | {ln.get('type','')} | {ln.get('strength','')} |"
            )
        lines.append("")

    if a.get("ai_special_features"):
        lines.append("## Special Features")
        lines.append("")
        for feat in a["ai_special_features"]:
            lines.append(f"- {feat}")
        lines.append("")

    if a.get("ai_next_steps"):
        lines.append("## Suggested Next Steps")
        lines.append("")
        for i, step in enumerate(a["ai_next_steps"], 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Standard Astro AI Spectrum Analyzer*")

    md = "\n".join(lines)
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="analysis_report.md"'},
    )


class ChatMarkdownRequest(_BaseModel):
    """Export a chat session as a Markdown report."""
    messages: list[dict] = []
    title: str = "AI Research Chat"
    # Optional session_id: if provided and the user is logged in, figures that
    # have been offloaded from localStorage will be pulled back from the DB
    # (keyed by session_id + user.id) and embedded before export.
    session_id: str | None = None


@router.post("/report/from-chat")
async def export_chat_as_markdown(
    req: ChatMarkdownRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Export an AI chat session as a downloadable Markdown report.

    Output strategy (PART AI rewrite, addressing "100+ pages / no figures"):
      - No longer renders every action as a ### Action heading + full params dump.
      - run_python: code + embedded figures + stdout (first 30 + last 5 lines, truncated)
      - Other tools: one-liner summary (e.g. "run_adql -> 571 rows (ra, dec, plx ...)")
      - Long stdout is tail-truncated to prevent a single print from exploding the document.
      - If the frontend has offloaded figures, use session_id to pull them back from the DB.
    """
    messages = await _rehydrate_messages_from_db(
        list(req.messages or []), req.session_id, user, db,
    )

    lines: list[str] = [
        f"# {req.title}",
        "",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Turns:** {len(messages)}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = (msg.get("content") or "").rstrip()
        actions = msg.get("actions") or []

        if role == "user":
            lines.append("## 🧑 User")
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")
        elif role == "assistant":
            lines.append("## 🤖 AI Assistant")
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")

        # List all non-run_python tool calls for this turn as one-liners first —
        # the user can skim and see what happened without wading through a 100+ line dump.
        non_python_summaries: list[str] = []
        python_actions: list[dict] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            if action.get("action") == "run_python":
                python_actions.append(action)
            else:
                summary = _action_summary_one_liner(action)
                if summary:
                    non_python_summaries.append(summary)
        if non_python_summaries:
            lines.append("**Tools used this turn:**")
            lines.append("")
            for s in non_python_summaries:
                lines.append(f"- {s}")
            lines.append("")

        # run_python is still expanded in full — it is the source of figures.
        for py_idx, action in enumerate(python_actions, start=1):
            tool_input = action.get("tool_input") if isinstance(action.get("tool_input"), dict) else {}
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            code = (
                action.get("code", "")
                or (tool_input or {}).get("code", "")
                or (params or {}).get("code", "")
            )
            if code:
                lines.append(f"<details><summary>📝 Python cell #{py_idx}</summary>")
                lines.append("")
                lines.append("```python")
                lines.append(str(code).rstrip())
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")

            tool_result = action.get("tool_result")
            if isinstance(tool_result, dict):
                stdout = tool_result.get("stdout")
                if isinstance(stdout, str) and stdout.strip():
                    truncated = _truncate_stdout(stdout)
                    lines.append("```text")
                    lines.append(truncated)
                    lines.append("```")
                    lines.append("")

                figures = tool_result.get("figures")
                if isinstance(figures, list):
                    for fig_idx, fig in enumerate(figures, start=1):
                        data_uri = _normalize_figure_to_data_uri(fig)
                        if data_uri:
                            lines.append(
                                f"![Figure {py_idx}.{fig_idx}]({data_uri})"
                            )
                            lines.append("")

                err = tool_result.get("error")
                if isinstance(err, str) and err.strip():
                    lines.append(f"> **⚠ Error:** {err.strip()[:500]}")
                    lines.append("")

                offloaded = tool_result.get("__figures_offloaded__")
                if isinstance(offloaded, int) and offloaded > 0:
                    lines.append(
                        f"> *(📊 {offloaded} figure(s) were trimmed from "
                        f"localStorage cache and could not be re-fetched. "
                        f"Try re-running this cell to regenerate them.)*"
                    )
                    lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("*Generated by Standard Astro AI Research Assistant*")

    md = "\n".join(lines)
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="ai_research_chat.md"'},
    )


class WorkflowExportRequest(_BaseModel):
    """Export a chat session's AI tool calls as a reproducible Python script."""
    tool_calls: list[dict] = []  # [{tool, input, result}]
    title: str = "AI Research Workflow"


@router.post("/workflow/python")
async def export_workflow_as_python(req: WorkflowExportRequest):
    """Convert AI agent tool calls into a standalone reproducible Python script."""
    lines = [
        "#!/usr/bin/env python3",
        f'"""Reproducible research workflow: {req.title}',
        "Generated by Standard Astro AI Research Agent",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        '"""',
        "",
        "import numpy as np",
        "import matplotlib.pyplot as plt",
        "from astropy.table import Table",
        "from astropy.coordinates import SkyCoord",
        "import astropy.units as u",
        "",
        "# ── Workflow Steps ──",
        "",
    ]

    for i, tc in enumerate(req.tool_calls, 1):
        tool = tc.get("tool", "")
        inp = tc.get("input", {})

        lines.append(f"# Step {i}: {tool}")

        if tool == "search_objects":
            lines.append(f"# Search: {inp.get('query', '')}")
            lines.append(f"# Sources: {inp.get('sources', ['simbad'])}")
            lines.append("# (Use astroquery to reproduce this search)")
            lines.append("")

        elif tool == "run_adql":
            query = inp.get("query", "")
            service = inp.get("service", "gaia")
            lines.append("from astroquery.utils.tap.core import TapPlus")
            tap_urls = {
                "gaia": "https://gea.esac.esa.int/tap-server/tap",
                "simbad": "https://simbad.u-strasbg.fr/simbad/sim-tap",
                "vizier": "https://tapvizier.u-strasbg.fr/TAPVizieR/tap",
            }
            url = tap_urls.get(service, tap_urls["gaia"])
            lines.append(f'tap = TapPlus(url="{url}")')
            lines.append(f"query = {repr(query)}")
            lines.append("job = tap.launch_job(query)")
            lines.append(f"results_{i} = job.get_results()")
            lines.append(f"print(f'Step {i}: {{len(results_{i})}} rows')")
            lines.append("")

        elif tool == "run_python":
            code = inp.get("code", "")
            lines.append("# Python analysis code:")
            for code_line in code.split("\n"):
                lines.append(code_line)
            lines.append("")

        elif tool == "get_object_info":
            name = inp.get("name", "")
            lines.append("from astroquery.simbad import Simbad")
            lines.append(f"result_{i} = Simbad.query_object({repr(name)})")
            lines.append(f"print(result_{i})")
            lines.append("")

        elif tool == "analyze_spectrum":
            path = inp.get("fits_path", "")
            lines.append("from astropy.io import fits")
            lines.append(f"# hdul = fits.open({repr(path)})")
            lines.append("")

        else:
            lines.append(f"# Tool: {tool}, Input: {inp}")
            lines.append("")

    lines.append("")
    lines.append("print('Workflow complete.')")

    script = "\n".join(lines)
    return StreamingResponse(
        iter([script]),
        media_type="text/x-python",
        headers={"Content-Disposition": 'attachment; filename="astro_workflow.py"'},
    )


class LatexReportRequest(_BaseModel):
    """Generate a LaTeX document from chat/search results."""
    messages: list[dict] = []
    title: str = "Standard Astro Research Report"
    abstract: str = ""
    author: str = "Standard Astro User"


class BibTeXRequest(_BaseModel):
    """Extract ADS bibcodes from chat messages and return a .bib file."""
    messages: list[dict] = []


@router.post("/report/latex")
async def export_report_latex(req: LatexReportRequest):
    """Generate a LaTeX document structured as an astronomy paper draft."""
    import re as _re

    def _tex_escape(text: str) -> str:
        """Escape special LaTeX characters."""
        specials = {
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        for char, replacement in specials.items():
            text = text.replace(char, replacement)
        return text

    # Collect content from messages
    user_queries: list[str] = []
    assistant_sections: list[str] = []
    code_blocks: list[str] = []
    tables_data: list[dict] = []
    bibcodes: list[str] = []
    figure_refs: list[str] = []

    bibcode_pattern = _re.compile(r"\b(\d{4}[A-Za-z][A-Za-z&.]+\.+\S+)")

    for msg in req.messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        actions = msg.get("actions", [])

        if role == "user":
            user_queries.append(content)
        elif role == "assistant":
            assistant_sections.append(content)
            # Extract bibcodes from assistant text
            found = bibcode_pattern.findall(content)
            bibcodes.extend(found)

        for action in (actions or []):
            if not isinstance(action, dict):
                continue
            action_type = action.get("action", "")
            if action_type == "run_python":
                params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
                tool_input = action.get("tool_input", {}) if isinstance(action.get("tool_input"), dict) else {}
                code = (
                    action.get("code", "")
                    or params.get("code", "")
                    or tool_input.get("code", "")
                )
                if code:
                    code_blocks.append(code)
            elif action_type in ("search", "search_objects"):
                tool_result = action.get("tool_result")
                if isinstance(tool_result, list) and len(tool_result) > 0:
                    tables_data.append({
                        "caption": f"Search results for: {action.get('query', '')}",
                        "rows": tool_result[:20],
                    })
            elif action_type == "plot":
                figure_refs.append(action.get("chart_type", "figure"))

    # Build abstract
    abstract_text = req.abstract
    if not abstract_text and assistant_sections:
        # Use first assistant message as abstract (truncated)
        first = assistant_sections[0]
        abstract_text = first[:500] + ("..." if len(first) > 500 else "")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build LaTeX document
    lines: list[str] = []
    lines.append(r"% Generated by Standard Astro on " + date_str)
    lines.append(r"% Attempt aastex631; fall back to article if unavailable")
    lines.append(r"\newif\ifaastex")
    lines.append(r"\IfFileExists{aastex631.cls}{\aastextrue}{\aastexfalse}")
    lines.append(r"\ifaastex")
    lines.append(r"  \documentclass[twocolumn]{aastex631}")
    lines.append(r"\else")
    lines.append(r"  \documentclass[12pt]{article}")
    lines.append(r"  \usepackage{graphicx}")
    lines.append(r"  \usepackage{natbib}")
    lines.append(r"  \usepackage{amsmath}")
    lines.append(r"  \usepackage[margin=1in]{geometry}")
    lines.append(r"\fi")
    lines.append(r"\usepackage{hyperref}")
    lines.append(r"\usepackage{listings}")
    lines.append(r"")
    lines.append(r"\begin{document}")
    lines.append(r"")
    lines.append(r"\title{" + _tex_escape(req.title) + r"}")
    lines.append(r"\author{" + _tex_escape(req.author) + r"}")
    lines.append(r"\date{" + date_str + r"}")
    lines.append(r"")
    lines.append(r"\begin{abstract}")
    lines.append(_tex_escape(abstract_text) if abstract_text else "Abstract text here.")
    lines.append(r"\end{abstract}")
    lines.append(r"")
    lines.append(r"\maketitle")
    lines.append(r"")

    # Section 1: Introduction / Methodology
    lines.append(r"\section{Methodology}")
    lines.append(r"")
    if user_queries:
        lines.append(r"The following research queries were investigated using the Standard Astro AI research assistant:")
        lines.append(r"\begin{enumerate}")
        for q in user_queries[:10]:
            lines.append(r"  \item " + _tex_escape(q[:200]))
        lines.append(r"\end{enumerate}")
    else:
        lines.append(r"% Describe your methodology here.")
    lines.append(r"")

    # Section 2: Results
    lines.append(r"\section{Results}")
    lines.append(r"")
    if assistant_sections:
        for i, section in enumerate(assistant_sections[:5]):
            if i > 0:
                lines.append(r"")
                lines.append(r"\subsection{Result " + str(i + 1) + r"}")
                lines.append(r"")
            # Truncate very long sections
            text = section[:2000]
            lines.append(_tex_escape(text))
            lines.append(r"")
    else:
        lines.append(r"% Results will appear here.")
    lines.append(r"")

    # Tables
    for ti, tbl in enumerate(tables_data[:5]):
        rows = tbl.get("rows", [])
        caption = tbl.get("caption", f"Table {ti + 1}")
        if not rows:
            continue

        # Extract column keys from first row
        first_row = rows[0] if isinstance(rows[0], dict) else {}
        col_keys = [k for k in ["name", "ra", "dec", "object_type", "magnitude", "redshift"]
                     if k in first_row]
        if not col_keys:
            col_keys = list(first_row.keys())[:6]

        col_spec = "l" * len(col_keys)
        col_headers = " & ".join(_tex_escape(k) for k in col_keys)

        lines.append(r"\begin{table}[ht]")
        lines.append(r"\centering")
        lines.append(r"\caption{" + _tex_escape(caption) + r"}")
        lines.append(r"\begin{tabular}{" + col_spec + r"}")
        lines.append(r"\hline")
        lines.append(col_headers + r" \\")
        lines.append(r"\hline")
        for row in rows[:15]:
            if not isinstance(row, dict):
                continue
            vals = []
            for k in col_keys:
                v = row.get(k, "")
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(_tex_escape(str(v) if v is not None else ""))
            lines.append(" & ".join(vals) + r" \\")
        lines.append(r"\hline")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")
        lines.append(r"")

    # Figures
    for fi, fig_type in enumerate(figure_refs[:5]):
        lines.append(r"\begin{figure}[ht]")
        lines.append(r"  \centering")
        lines.append(r"  \includegraphics[width=\columnwidth]{figure_" + str(fi + 1) + r"}")
        lines.append(r"  \caption{" + _tex_escape(fig_type) + r" generated by Standard Astro.}")
        lines.append(r"  \label{fig:" + str(fi + 1) + r"}")
        lines.append(r"\end{figure}")
        lines.append(r"")

    # Code listings
    if code_blocks:
        lines.append(r"\section{Analysis Code}")
        lines.append(r"")
        for ci, code in enumerate(code_blocks[:5]):
            lines.append(r"\begin{lstlisting}[language=Python, caption={Analysis step " + str(ci + 1) + r"}]")
            lines.append(code)
            lines.append(r"\end{lstlisting}")
            lines.append(r"")

    # References
    lines.append(r"\section{References}")
    lines.append(r"")
    unique_bibcodes = list(dict.fromkeys(bibcodes))
    if unique_bibcodes:
        lines.append(r"The following references were cited in this analysis:")
        lines.append(r"\begin{itemize}")
        for bib in unique_bibcodes[:20]:
            lines.append(r"  \item \texttt{" + _tex_escape(bib) + r"}")
        lines.append(r"\end{itemize}")
        lines.append(r"")
        lines.append(r"% To use BibTeX, export the .bib file from Standard Astro")
        lines.append(r"% and uncomment the following:")
        lines.append(r"% \bibliographystyle{aasjournal}")
        lines.append(r"% \bibliography{references}")
    else:
        lines.append(r"% Add your references here or use BibTeX.")
        lines.append(r"% \bibliographystyle{aasjournal}")
        lines.append(r"% \bibliography{references}")
    lines.append(r"")
    lines.append(r"\end{document}")

    tex = "\n".join(lines)
    return StreamingResponse(
        iter([tex]),
        media_type="application/x-tex",
        headers={"Content-Disposition": 'attachment; filename="astro_report.tex"'},
    )


@router.post("/report/bibtex")
async def export_report_bibtex(req: BibTeXRequest):
    """Extract all ADS bibcodes from chat messages and return a .bib file."""
    import re as _re

    bibcode_pattern = _re.compile(r"\b(\d{4}[A-Za-z][A-Za-z&.]+\.+\S+)")
    bibcodes: list[str] = []

    for msg in req.messages:
        content = msg.get("content", "")
        found = bibcode_pattern.findall(content)
        bibcodes.extend(found)

        for action in (msg.get("actions") or []):
            if isinstance(action, dict):
                tool_result = action.get("tool_result")
                if isinstance(tool_result, dict):
                    bib = tool_result.get("bibcode", "")
                    if bib:
                        bibcodes.append(bib)
                elif isinstance(tool_result, list):
                    for item in tool_result:
                        if isinstance(item, dict) and item.get("bibcode"):
                            bibcodes.append(item["bibcode"])

    unique_bibcodes = list(dict.fromkeys(bibcodes))

    # Generate BibTeX entries
    bib_entries: list[str] = []
    bib_entries.append("% BibTeX entries extracted by Standard Astro")
    bib_entries.append(f"% Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    bib_entries.append(f"% Bibcodes found: {len(unique_bibcodes)}")
    bib_entries.append("")

    if not unique_bibcodes:
        bib_entries.append("% No ADS bibcodes were found in the chat messages.")
        bib_entries.append("% You can manually add entries here.")
    else:
        # D5.1 — populate full ADS records when possible so the exported
        # .bib file is directly usable with bibtex.  Falls back to a
        # stub only when ADS is unreachable / ADS_API_KEY is unset.
        import os as _os
        ads_key = _os.getenv("ADS_API_KEY", "").strip()
        ads_client = None
        if ads_key:
            import httpx as _httpx
            ads_client = _httpx.AsyncClient(timeout=10.0)
        try:
            for bib in unique_bibcodes:
                safe_key = bib.replace("&", "_").replace(".", "_")
                entry = None
                if ads_client is not None:
                    try:
                        resp = await ads_client.get(
                            f"https://api.adsabs.harvard.edu/v1/export/bibtex/{bib}",
                            headers={"Authorization": f"Bearer {ads_key}"},
                        )
                        if resp.status_code == 200:
                            body = resp.json().get("export") or ""
                            # ADS returns the @ARTICLE block already; strip
                            # surrounding newlines so our joined output stays
                            # clean.  Keep the original citekey from ADS (it
                            # follows the author+year convention that
                            # \\citet expects).
                            entry = body.strip()
                    except Exception:
                        entry = None
                if entry:
                    bib_entries.append(entry)
                    bib_entries.append("")
                    continue
                bib_entries.append(f"@ARTICLE{{{safe_key},")
                bib_entries.append("  author = {},")
                bib_entries.append("  title = {},")
                bib_entries.append("  journal = {},")
                bib_entries.append(f"  year = {{{bib[:4]}}},")
                bib_entries.append(f"  adsurl = {{https://ui.adsabs.harvard.edu/abs/{bib}}},")
                bib_entries.append(f"  note = {{ADS metadata unavailable; set ADS_API_KEY to auto-populate. Bibcode: {bib}}}")
                bib_entries.append("}")
                bib_entries.append("")
        finally:
            if ads_client is not None:
                await ads_client.aclose()

    bib_text = "\n".join(bib_entries)
    return StreamingResponse(
        iter([bib_text]),
        media_type="application/x-bibtex",
        headers={"Content-Disposition": 'attachment; filename="references.bib"'},
    )


# ── Jupyter Notebook Export (Pipeline Run) ──

# Maps pipeline node types to Python code snippets that reproduce the operation.
_NODE_CODE_TEMPLATES: dict[str, str] = {
    "LoadData": (
        "from astropy.io import fits\n"
        "# Load FITS data — update the path to your local file\n"
        "# hdul = fits.open('{fits_path}')\n"
        "# data = hdul[0].data\n"
        "# header = hdul[0].header\n"
        "print('LoadData: configure the FITS path above')"
    ),
    "QueryData": (
        "from astroquery.utils.tap.core import TapPlus\n"
        "# Query an astronomical database\n"
        "# tap = TapPlus(url='https://gea.esac.esa.int/tap-server/tap')\n"
        "# job = tap.launch_job('SELECT ...')\n"
        "# results = job.get_results()\n"
        "print('QueryData: configure the query above')"
    ),
    "Denoise": (
        "from astropy.stats import sigma_clip\n"
        "# Apply sigma-clipping to remove outliers\n"
        "# clipped = sigma_clip(data, sigma=3.0)\n"
        "# denoised = np.where(clipped.mask, np.nanmedian(data), data)\n"
        "print('Denoise: apply sigma clipping to your data')"
    ),
    "SpectralFit": (
        "from astropy.modeling import models, fitting\n"
        "# Fit spectral lines\n"
        "# gauss = models.Gaussian1D(amplitude=1, mean=6563, stddev=5)\n"
        "# fitter = fitting.LevMarLSQFitter()\n"
        "# fitted = fitter(gauss, wavelength, flux)\n"
        "print('SpectralFit: configure your line model above')"
    ),
    "CoordTransform": (
        "from astropy.coordinates import SkyCoord\n"
        "import astropy.units as u\n"
        "# Transform coordinates between frames\n"
        "# coord = SkyCoord(ra=10.0*u.deg, dec=20.0*u.deg, frame='icrs')\n"
        "# galactic = coord.galactic\n"
        "print('CoordTransform: configure coordinates above')"
    ),
    "RedshiftEstimate": (
        "# Estimate redshift from spectral lines\n"
        "# rest_wavelength = 6562.8  # H-alpha\n"
        "# observed_wavelength = ...  # from your data\n"
        "# z = (observed_wavelength - rest_wavelength) / rest_wavelength\n"
        "print('RedshiftEstimate: set observed wavelength above')"
    ),
    "CrossMatch": (
        "from astropy.coordinates import SkyCoord\n"
        "import astropy.units as u\n"
        "# Cross-match two catalogs\n"
        "# cat1 = SkyCoord(ra=ra1*u.deg, dec=dec1*u.deg)\n"
        "# cat2 = SkyCoord(ra=ra2*u.deg, dec=dec2*u.deg)\n"
        "# idx, sep, _ = cat1.match_to_catalog_sky(cat2)\n"
        "print('CrossMatch: load your catalogs above')"
    ),
    "Plot": (
        "import matplotlib.pyplot as plt\n"
        "# Generate a plot\n"
        "# fig, ax = plt.subplots(figsize=(10, 6))\n"
        "# ax.plot(x, y)\n"
        "# ax.set_xlabel('X')\n"
        "# ax.set_ylabel('Y')\n"
        "# plt.tight_layout()\n"
        "# plt.show()\n"
        "print('Plot: configure your plot above')"
    ),
    "InteractivePlot": (
        "# import plotly.express as px\n"
        "# fig = px.scatter(x=x, y=y)\n"
        "# fig.show()\n"
        "print('InteractivePlot: configure your interactive plot above')"
    ),
    "SEDFit": (
        "# Spectral Energy Distribution fitting\n"
        "# Provide photometric data points (bands + fluxes)\n"
        "# and fit an SED model.\n"
        "print('SEDFit: configure photometry data above')"
    ),
    "EquivalentWidth": (
        "# Measure equivalent width of spectral lines\n"
        "# from specutils import SpectralRegion\n"
        "# from specutils.analysis import equivalent_width\n"
        "# ew = equivalent_width(spectrum, regions=SpectralRegion(...))\n"
        "print('EquivalentWidth: configure spectral region above')"
    ),
    "PhotCalibrate": (
        "# Photometric calibration\n"
        "# Apply zero-point corrections to instrumental magnitudes\n"
        "# calibrated_mag = instrumental_mag + zero_point\n"
        "print('PhotCalibrate: set zero-point above')"
    ),
    "ImageStack": (
        "# Stack multiple images\n"
        "# from astropy.nddata import CCDData\n"
        "# from ccdproc import combine\n"
        "# combined = combine(image_list, method='median')\n"
        "print('ImageStack: provide image list above')"
    ),
    "BiasSubtract": (
        "# Subtract bias frame\n"
        "# from ccdproc import subtract_bias\n"
        "# corrected = subtract_bias(image, bias)\n"
        "print('BiasSubtract: provide bias frame above')"
    ),
    "DarkCorrect": (
        "# Dark current correction\n"
        "# from ccdproc import subtract_dark\n"
        "# corrected = subtract_dark(image, dark, exposure_time='exptime', exposure_unit=u.s)\n"
        "print('DarkCorrect: provide dark frame above')"
    ),
    "FlatField": (
        "# Flat-field correction\n"
        "# from ccdproc import flat_correct\n"
        "# corrected = flat_correct(image, flat)\n"
        "print('FlatField: provide flat frame above')"
    ),
    "CosmicRayReject": (
        "# Cosmic ray rejection\n"
        "# from astroscrappy import detect_cosmics\n"
        "# mask, clean = detect_cosmics(data)\n"
        "print('CosmicRayReject: apply to your image data')"
    ),
    "SourceExtract": (
        "# Source extraction\n"
        "# from photutils.detection import DAOStarFinder\n"
        "# daofind = DAOStarFinder(fwhm=3.0, threshold=5.*std)\n"
        "# sources = daofind(data - median)\n"
        "print('SourceExtract: configure detection parameters above')"
    ),
    "PSFPhotometry": (
        "# PSF photometry\n"
        "# from photutils.psf import PSFPhotometry\n"
        "# phot = PSFPhotometry(psf_model, fit_shape=(11,11))\n"
        "# result = phot(data, init_params=init_table)\n"
        "print('PSFPhotometry: configure PSF model above')"
    ),
    "AstrometricSolve": (
        "# Astrometric plate solving\n"
        "# Determine WCS solution for an image\n"
        "print('AstrometricSolve: provide image for plate solving')"
    ),
    "TimeSeriesAnalysis": (
        "# Time-series analysis\n"
        "# from astropy.timeseries import LombScargle\n"
        "# ls = LombScargle(times, flux)\n"
        "# frequency, power = ls.autopower()\n"
        "print('TimeSeriesAnalysis: provide time-series data above')"
    ),
    "CustomScript": (
        "# Custom user script\n"
        "# Insert your custom analysis code here.\n"
        "print('CustomScript: add your code')"
    ),
    "Condition": (
        "# Conditional branch\n"
        "# This node routes data based on a condition.\n"
        "print('Condition: define your branching logic')"
    ),
}


def _topological_sort_flat(dag: dict) -> list[str]:
    """Return node IDs in topological order (flattened from level-based sort).

    Uses Kahn's algorithm.  Falls back to the order in ``dag['nodes']``
    if edges are missing or the graph is trivial.
    """
    nodes_list = dag.get("nodes", [])
    if not nodes_list:
        return []

    node_ids = [n["id"] for n in nodes_list]
    edges = dag.get("edges", [])
    if not edges:
        return node_ids

    from collections import deque

    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    in_deg: dict[str, int] = {nid: 0 for nid in node_ids}
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in adj and tgt in in_deg:
            adj[src].append(tgt)
            in_deg[tgt] += 1

    queue = deque(nid for nid, d in in_deg.items() if d == 0)
    ordered: list[str] = []
    while queue:
        nid = queue.popleft()
        ordered.append(nid)
        for nb in adj.get(nid, []):
            in_deg[nb] -= 1
            if in_deg[nb] == 0:
                queue.append(nb)

    # If cycle detected, fall back to declaration order for remaining nodes
    if len(ordered) < len(node_ids):
        seen = set(ordered)
        for nid in node_ids:
            if nid not in seen:
                ordered.append(nid)

    return ordered


@router.get("/run/{run_id}/notebook")
async def export_run_notebook(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a pipeline run as a Jupyter notebook (.ipynb)."""
    import json as _json

    run, results = await _get_run_and_results(run_id, user, db)
    dag = run.dag or {"nodes": [], "edges": []}
    node_map = {n["id"]: n for n in dag.get("nodes", [])}
    sorted_ids = _topological_sort_flat(dag)

    # Build a quick lookup: node_id -> RunResult
    result_by_node: dict[str, RunResult] = {r.node_id: r for r in results}

    cells: list[dict] = []

    # ── Cell 1: Markdown header with run metadata ──
    created = run.created_at.isoformat() if run.created_at else "N/A"
    completed = run.completed_at.isoformat() if run.completed_at else "N/A"
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [
            "# Pipeline Run Report\n",
            "\n",
            f"- **Run ID:** `{run.id}`\n",
            f"- **Status:** {run.status}\n",
            f"- **Created:** {created}\n",
            f"- **Completed:** {completed}\n",
            "\n",
            f"Nodes: {len(dag.get('nodes', []))} | "
            f"Edges: {len(dag.get('edges', []))}\n",
        ],
    })

    # ── Cell 2: Code cell — install dependencies ──
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": [
            "# Install / verify dependencies (uncomment as needed)\n",
            "# !pip install numpy astropy matplotlib scipy specutils\n",
            "\n",
            "import numpy as np\n",
            "import matplotlib.pyplot as plt\n",
            "from astropy.table import Table\n",
            "from astropy.coordinates import SkyCoord\n",
            "import astropy.units as u\n",
        ],
        "execution_count": None,
        "outputs": [],
    })

    # ── Per-node cells (topologically sorted) ──
    results_json = run.results or {}

    for node_id in sorted_ids:
        node_def = node_map.get(node_id, {})
        node_type = node_def.get("type", "unknown")
        node_label = node_def.get("data", {}).get("label", node_id)
        node_params = node_def.get("data", {}).get("params", {})

        # Markdown cell: node name and type
        md_lines = [
            f"## Node: {node_label}\n",
            "\n",
            f"- **ID:** `{node_id}`\n",
            f"- **Type:** `{node_type}`\n",
        ]
        if node_params:
            md_lines.append("- **Parameters:**\n")
            for pk, pv in node_params.items():
                md_lines.append(f"  - `{pk}`: `{pv}`\n")

        rr = result_by_node.get(node_id)
        if rr:
            if rr.output_path:
                md_lines.append(f"- **Output path:** `{rr.output_path}`\n")
            if rr.execution_time_ms is not None:
                md_lines.append(f"- **Execution time:** {rr.execution_time_ms} ms\n")

        cells.append({
            "cell_type": "markdown",
            "metadata": {},
            "source": md_lines,
        })

        # Code cell: reproducible Python snippet
        code_template = _NODE_CODE_TEMPLATES.get(node_type)
        if node_type == "CustomScript":
            # Embed the user's actual script if available
            user_code = node_params.get("code", "")
            if user_code:
                code_template = f"# Custom script from pipeline\n{user_code}"

        if code_template:
            # Substitute known parameter values into the template
            code_text = code_template
            if node_type == "LoadData" and node_params.get("fits_path"):
                code_text = code_text.replace("{fits_path}", str(node_params["fits_path"]))
            code_lines = [line + "\n" for line in code_text.split("\n")]
        else:
            code_lines = [
                f"# Node type '{node_type}' — add your implementation here\n",
                f"print('Node {node_id} ({node_type}): not yet implemented')\n",
            ]

        cells.append({
            "cell_type": "code",
            "metadata": {},
            "source": code_lines,
            "execution_count": None,
            "outputs": [],
        })

        # Output cell: embed the node's result if available
        node_result_data = results_json.get(node_id)
        if node_result_data is not None:
            result_repr = _json.dumps(node_result_data, indent=2, default=str)
            # Truncate very large outputs to keep the notebook manageable
            if len(result_repr) > 5000:
                result_repr = result_repr[:5000] + "\n# ... (truncated)"

            cells.append({
                "cell_type": "code",
                "metadata": {},
                "source": [
                    f"# Output from node '{node_id}' (recorded during pipeline execution)\n",
                    f"node_output = {result_repr}\n",
                    "print(f'Keys: {list(node_output.keys()) if isinstance(node_output, dict) else type(node_output).__name__}')\n",
                ],
                "execution_count": None,
                "outputs": [],
            })

        # If the RunResult has logs, add them as a markdown cell
        if rr and rr.logs:
            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"**Logs for `{node_id}`:**\n",
                    "\n",
                    "```\n",
                    rr.logs + "\n",
                    "```\n",
                ],
            })

    # ── Last cell: summary ──
    node_count = len(dag.get("nodes", []))
    edge_count = len(dag.get("edges", []))
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [
            "---\n",
            "\n",
            "## Summary\n",
            "\n",
            f"Pipeline run `{run.id}` completed with status **{run.status}**.\n",
            "\n",
            f"- **Nodes executed:** {node_count}\n",
            f"- **Edges:** {edge_count}\n",
            f"- **Results recorded:** {len(results)}\n",
            "\n",
            "*Generated by Standard Astro Research Platform*\n",
        ],
    })

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": cells,
    }

    notebook_json = _json.dumps(notebook, indent=2, default=str)
    return StreamingResponse(
        iter([notebook_json]),
        media_type="application/x-ipynb+json",
        headers={
            "Content-Disposition": f'attachment; filename="run_{run_id}.ipynb"'
        },
    )


class ChatToNotebookRequest(_BaseModel):
    messages: list[dict] = []
    title: str = "AI Research Session"
    session_id: str | None = None


@router.post("/notebook/from-chat")
async def export_chat_as_notebook(
    req: ChatToNotebookRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Export an AI chat session as an executable Jupyter notebook."""
    import json as _json

    messages = await _rehydrate_messages_from_db(
        list(req.messages or []), req.session_id, user, db,
    )

    cells = []
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [f"# {req.title}\n", "\nGenerated by Standard Astro AI Research Assistant\n"],
    })

    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": [
            "# D6.1 — imports + inline plot magic for notebook-friendly runs\n",
            "%matplotlib inline\n",
            "import numpy as np\n",
            "import matplotlib.pyplot as plt\n",
            "from astropy.table import Table\n",
            "from astropy.coordinates import SkyCoord\n",
            "import astropy.units as u\n",
        ],
        "execution_count": None,
        "outputs": [],
    })

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        actions = msg.get("actions", [])

        if role == "user":
            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"## User\n\n{content}\n"],
            })
        elif role == "assistant":
            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"## AI Assistant\n\n{content}\n"],
            })

        for action in (actions or []):
            if isinstance(action, dict) and action.get("action") == "run_python":
                params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
                tool_input = action.get("tool_input", {}) if isinstance(action.get("tool_input"), dict) else {}
                code = (
                    action.get("code", "")
                    or params.get("code", "")
                    or tool_input.get("code", "")
                )
                if code:
                    # Write the real stdout / figures / error from the last
                    # run_python call as Jupyter cell outputs, so the .ipynb
                    # displays figures immediately in Jupyter / JupyterLab /
                    # VS Code / GitHub / nbviewer / Colab without re-running
                    # the cell (re-running would also fail because astro.*
                    # helpers are not available in a standard Jupyter kernel).
                    outputs: list[dict] = []
                    tool_result = action.get("tool_result")
                    if isinstance(tool_result, dict):
                        stdout = tool_result.get("stdout")
                        if isinstance(stdout, str) and stdout:
                            outputs.append({
                                "output_type": "stream",
                                "name": "stdout",
                                "text": stdout if stdout.endswith("\n") else stdout + "\n",
                            })
                        figures = tool_result.get("figures")
                        if isinstance(figures, list):
                            for fig in figures:
                                if not isinstance(fig, str) or not fig:
                                    continue
                                # Accept either "data:image/png;base64,XXX" or bare base64.
                                if fig.startswith("data:image"):
                                    _, _, b64 = fig.partition(",")
                                else:
                                    b64 = fig
                                if not b64:
                                    continue
                                outputs.append({
                                    "output_type": "display_data",
                                    "data": {"image/png": b64},
                                    "metadata": {},
                                })
                        err = tool_result.get("error")
                        if isinstance(err, str) and err.strip():
                            outputs.append({
                                "output_type": "stream",
                                "name": "stderr",
                                "text": err.rstrip() + "\n",
                            })
                    cells.append({
                        "cell_type": "code",
                        "metadata": {},
                        "source": [code],
                        "execution_count": 1 if outputs else None,
                        "outputs": outputs,
                    })

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": cells,
    }

    return StreamingResponse(
        iter([_json.dumps(notebook, indent=2)]),
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": 'attachment; filename="ai_research_session.ipynb"'},
    )


# ---------- HTML self-contained chat export (PART AI) ----------

class ChatHtmlRequest(_BaseModel):
    """Self-contained HTML report. Open by double-clicking in a browser / print to PDF with Ctrl+P."""
    messages: list[dict] = []
    title: str = "AI Research Session"
    session_id: str | None = None


def _html_escape(s: object) -> str:
    """Minimal HTML escaping to prevent <script> tags in user content from landing. No markupsafe dependency."""
    text = "" if s is None else str(s)
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
    )


def _render_user_content_as_html(content: str) -> str:
    """Render user/assistant text as HTML: preserve paragraphs + simple markdown
    bold / inline code; render everything else literally. No markdown library
    dependency to avoid bloat."""
    if not content:
        return ""
    safe = _html_escape(content)
    # Three most common patterns: **bold** / `inline code` / paragraphs (double newline)
    import re as _re
    safe = _re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", safe)
    safe = _re.sub(r"`([^`\n]+)`", r"<code>\1</code>", safe)
    # Paragraphs: split on blank lines.
    blocks = [b.strip() for b in safe.split("\n\n") if b.strip()]
    return "".join(
        f"<p>{block.replace(chr(10), '<br>')}</p>" for block in blocks
    )


_HTML_CSS = """
:root {
  --paper: #fbfaf5;
  --ink: #1a1a1a;
  --ink-muted: #555;
  --burgundy: #7b2d26;
  --rule: #d8d4c4;
  --code-bg: #f4f1e6;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--paper);
  color: var(--ink);
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 16px;
  line-height: 1.6;
  max-width: 860px;
  margin: 0 auto;
  padding: 32px 48px 64px;
}
h1, h2, h3 { font-family: Georgia, serif; color: var(--ink); margin-top: 1.4em; }
h1 { font-size: 2em; border-bottom: 2px solid var(--burgundy); padding-bottom: 8px; }
h2 { font-size: 1.4em; color: var(--burgundy); }
nav.toc {
  background: var(--code-bg);
  border-left: 3px solid var(--burgundy);
  padding: 12px 18px;
  margin: 16px 0 32px;
  font-size: 0.92em;
}
nav.toc a { color: var(--ink); text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
nav.toc ol { margin: 0; padding-left: 22px; }
.turn { border-bottom: 1px solid var(--rule); padding: 16px 0; }
.role-user { color: var(--burgundy); font-weight: 700; margin-bottom: 6px; }
.role-assistant { color: #2a5d7b; font-weight: 700; margin-bottom: 6px; }
.tools-summary {
  background: var(--code-bg);
  border-radius: 4px;
  padding: 8px 14px;
  margin: 8px 0;
  font-size: 0.88em;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.tools-summary ul { margin: 4px 0; padding-left: 20px; }
details.code-block {
  margin: 12px 0;
  border: 1px solid var(--rule);
  border-radius: 4px;
}
details.code-block > summary {
  cursor: pointer;
  padding: 6px 12px;
  background: var(--code-bg);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.88em;
  user-select: none;
}
pre.code, pre.stdout {
  margin: 0;
  padding: 12px 16px;
  background: var(--code-bg);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.84em;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
pre.stdout {
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 4px;
  margin: 12px 0;
}
figure.run-python-fig {
  margin: 16px 0;
  text-align: center;
}
figure.run-python-fig img {
  max-width: 100%;
  height: auto;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: white;
}
figure.run-python-fig figcaption {
  font-size: 0.82em;
  color: var(--ink-muted);
  margin-top: 6px;
}
.error-banner {
  background: #fff0f0;
  border-left: 3px solid #c62828;
  padding: 8px 14px;
  margin: 12px 0;
  font-size: 0.9em;
}
.offload-note {
  background: #fff8e1;
  border-left: 3px solid #f5a623;
  padding: 8px 14px;
  margin: 12px 0;
  font-size: 0.86em;
  color: var(--ink-muted);
}
footer {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--rule);
  font-size: 0.82em;
  color: var(--ink-muted);
  text-align: center;
}
@media print {
  body { padding: 0; max-width: none; }
  details.code-block[open] > summary { display: none; }
  details.code-block { border: none; }
}
"""


@router.post("/report/html-from-chat")
async def export_chat_as_html(
    req: ChatHtmlRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Export the AI chat as a self-contained HTML file.

    A single .html file that opens by double-clicking in a browser. All figures
    are embedded as base64. Ctrl+P prints directly to PDF. Better suited for
    sharing / printing / archiving than .md."""
    messages = await _rehydrate_messages_from_db(
        list(req.messages or []), req.session_id, user, db,
    )

    title = _html_escape(req.title or "AI Research Session")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append(f"<title>{title}</title>")
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<style>{_HTML_CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append(f"<h1>{title}</h1>")
    parts.append(f"<p style='color:var(--ink-muted)'>Generated {timestamp} · {len(messages)} turns</p>")

    # Table of contents — use the first 60 characters of each user message as the anchor label.
    toc_entries: list[tuple[str, str]] = []
    for idx, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            preview = (msg.get("content") or "").strip().splitlines()
            label = (preview[0][:60] + "…") if (preview and len(preview[0]) > 60) else (preview[0] if preview else f"Turn {idx + 1}")
            toc_entries.append((f"turn-{idx}", label))
    if toc_entries:
        parts.append('<nav class="toc">')
        parts.append("<strong>Contents</strong>")
        parts.append("<ol>")
        for anchor, label in toc_entries:
            parts.append(f'<li><a href="#{anchor}">{_html_escape(label)}</a></li>')
        parts.append("</ol>")
        parts.append("</nav>")

    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = msg.get("content") or ""
        actions = msg.get("actions") or []

        anchor_id = f"turn-{idx}" if role == "user" else None
        anchor_attr = f' id="{anchor_id}"' if anchor_id else ""
        parts.append(f'<section class="turn"{anchor_attr}>')

        if role == "user":
            parts.append('<div class="role-user">🧑 You</div>')
        elif role == "assistant":
            parts.append('<div class="role-assistant">🤖 AI Assistant</div>')

        if content:
            parts.append(_render_user_content_as_html(content))

        # One-liner summary of non-run_python actions.
        non_python = []
        python_actions = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            if action.get("action") == "run_python":
                python_actions.append(action)
            else:
                summary = _action_summary_one_liner(action)
                if summary:
                    non_python.append(summary)
        if non_python:
            parts.append('<div class="tools-summary"><strong>Tools used:</strong><ul>')
            for s in non_python:
                parts.append(f"<li>{_html_escape(s)}</li>")
            parts.append("</ul></div>")

        for py_idx, action in enumerate(python_actions, start=1):
            tool_input = action.get("tool_input") if isinstance(action.get("tool_input"), dict) else {}
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            code = (
                action.get("code", "")
                or (tool_input or {}).get("code", "")
                or (params or {}).get("code", "")
            )
            if code:
                parts.append('<details class="code-block">')
                parts.append(f"<summary>📝 Python cell #{py_idx}</summary>")
                parts.append(f'<pre class="code">{_html_escape(code)}</pre>')
                parts.append("</details>")

            tool_result = action.get("tool_result")
            if isinstance(tool_result, dict):
                stdout = tool_result.get("stdout")
                if isinstance(stdout, str) and stdout.strip():
                    truncated = _truncate_stdout(stdout)
                    parts.append(f'<pre class="stdout">{_html_escape(truncated)}</pre>')

                figures = tool_result.get("figures")
                if isinstance(figures, list):
                    for fig_idx, fig in enumerate(figures, start=1):
                        data_uri = _normalize_figure_to_data_uri(fig)
                        if data_uri:
                            parts.append('<figure class="run-python-fig">')
                            parts.append(
                                f'<img alt="Figure {py_idx}.{fig_idx}" '
                                f'src="{_html_escape(data_uri)}">'
                            )
                            parts.append(
                                f"<figcaption>Figure {py_idx}.{fig_idx}</figcaption>"
                            )
                            parts.append("</figure>")

                err = tool_result.get("error")
                if isinstance(err, str) and err.strip():
                    parts.append(
                        f'<div class="error-banner"><strong>⚠ Error:</strong> '
                        f"{_html_escape(err.strip()[:500])}</div>"
                    )

                offloaded = tool_result.get("__figures_offloaded__")
                if isinstance(offloaded, int) and offloaded > 0:
                    parts.append(
                        f'<div class="offload-note">📊 {offloaded} figure(s) '
                        f"were trimmed from local cache and could not be "
                        "rehydrated. Re-run the Python cell to regenerate.</div>"
                    )

        parts.append("</section>")

    parts.append("<footer>Generated by Standard Astro AI Research Assistant</footer>")
    parts.append("</body></html>")

    html = "\n".join(parts)
    return StreamingResponse(
        iter([html]),
        media_type="text/html",
        headers={"Content-Disposition": 'attachment; filename="ai_research_chat.html"'},
    )


class NotebookFromSearchRequest(_BaseModel):
    query: str = ""
    results: list[dict] = []  # SearchResult dicts


@router.post("/notebook/from-search")
async def export_search_as_notebook(req: NotebookFromSearchRequest):
    """Export search results as a ready-to-run Jupyter notebook with astropy code."""
    import json as _json

    cells = []

    # Title cell
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [
            "# Standard Astro Search Results\n",
            "\n",
            f"Query: **{req.query}**  \n",
            f"Results: {len(req.results)} objects\n",
        ],
    })

    # Setup cell
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": [
            "import numpy as np\n",
            "import matplotlib.pyplot as plt\n",
            "from astropy.table import Table\n",
            "from astropy.coordinates import SkyCoord\n",
            "import astropy.units as u\n",
        ],
        "execution_count": None,
        "outputs": [],
    })

    # Data cell — embed results as a Python dict
    sample = req.results[:200]
    data_lines = [
        "# Search results from Standard Astro\n",
        "results = " + _json.dumps(sample, indent=2, default=str) + "\n",
        "\n",
        "# Convert to astropy Table\n",
        "t = Table()\n",
        "t['name'] = [r.get('name','') for r in results]\n",
        "t['ra'] = [r.get('ra',0) for r in results]\n",
        "t['dec'] = [r.get('dec',0) for r in results]\n",
        "t['source'] = [r.get('source','') for r in results]\n",
        "t['type'] = [r.get('object_type','') for r in results]\n",
        "t['mag'] = [r.get('magnitude') for r in results]\n",
        "t['redshift'] = [r.get('redshift') for r in results]\n",
        "print(f'{len(t)} objects loaded')\n",
        "t.show_in_notebook()\n",
    ]
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": data_lines,
        "execution_count": None,
        "outputs": [],
    })

    # Sky plot cell
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": [
            "# Sky distribution (Aitoff projection)\n",
            "coords = SkyCoord(ra=t['ra']*u.deg, dec=t['dec']*u.deg)\n",
            "fig = plt.figure(figsize=(12, 6))\n",
            "ax = fig.add_subplot(111, projection='aitoff')\n",
            "ra_rad = coords.ra.wrap_at(180*u.deg).radian\n",
            "dec_rad = coords.dec.radian\n",
            "ax.scatter(ra_rad, dec_rad, s=10, alpha=0.6)\n",
            "ax.grid(True)\n",
            "ax.set_title('Sky Distribution')\n",
            "plt.tight_layout()\n",
            "plt.show()\n",
        ],
        "execution_count": None,
        "outputs": [],
    })

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": cells,
    }

    return StreamingResponse(
        iter([_json.dumps(notebook, indent=2)]),
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": f'attachment; filename="astro_search_{len(req.results)}_results.ipynb"'},
    )


@router.get("/run/{run_id}/publication-package")
async def publication_package(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One-click publication package with all export format URLs."""
    # Verify the run exists and belongs to the user
    run_uuid = uuid.UUID(run_id)
    run = (
        await db.execute(
            select(PipelineRun).where(
                PipelineRun.id == run_uuid,
                PipelineRun.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    return {
        "run_id": run_id,
        "exports": {
            "notebook": f"/api/export/run/{run_id}/notebook",
            "csv": f"/api/export/run/{run_id}/csv",
            "votable": f"/api/export/run/{run_id}/votable",
            "fits": f"/api/export/run/{run_id}/fits",
            "provenance": f"/api/provenance/{run_id}/lineage",
            "requirements": f"/api/provenance/{run_id}/requirements.txt",
        },
        "note": "Download each format from the URLs above.",
    }
