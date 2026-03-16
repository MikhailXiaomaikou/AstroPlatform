"""Export endpoints for pipeline run results (CSV, VOTable, PDF)."""

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import PipelineRun, RunResult, User

router = APIRouter(prefix="/api/export", tags=["export"])


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


# ── CSV Export ──


@router.get("/run/{run_id}/csv")
async def export_run_csv(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export pipeline run results as a CSV file download."""
    run, results = await _get_run_and_results(run_id, user, db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["run_id", "status", "created_at", "completed_at"])
    writer.writerow([
        str(run.id),
        run.status,
        run.created_at.isoformat() if run.created_at else "",
        run.completed_at.isoformat() if run.completed_at else "",
    ])
    writer.writerow([])
    writer.writerow(["node_id", "output_path", "logs"])
    for r in results:
        writer.writerow([r.node_id, r.output_path or "", r.logs or ""])

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

    from astropy.table import Table
    from astropy.io.votable import from_table, writeto

    rows = []
    for r in results:
        rows.append({
            "node_id": r.node_id,
            "output_path": r.output_path or "",
            "logs": r.logs or "",
        })

    if rows:
        astro_table = Table(
            {
                "node_id": [row["node_id"] for row in rows],
                "output_path": [row["output_path"] for row in rows],
                "logs": [row["logs"] for row in rows],
            }
        )
    else:
        astro_table = Table(
            names=["node_id", "output_path", "logs"],
            dtype=["U256", "U1024", "U4096"],
        )

    # Add run metadata as table params
    astro_table.meta["run_id"] = str(run.id)
    astro_table.meta["status"] = run.status
    astro_table.meta["created_at"] = (
        run.created_at.isoformat() if run.created_at else ""
    )
    astro_table.meta["completed_at"] = (
        run.completed_at.isoformat() if run.completed_at else ""
    )

    votable = from_table(astro_table)
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


# ── PDF Export ──


def _build_report_html(run: PipelineRun, results: list[RunResult]) -> str:
    """Build an HTML string summarizing the pipeline run."""
    created = run.created_at.isoformat() if run.created_at else "N/A"
    completed = run.completed_at.isoformat() if run.completed_at else "N/A"

    rows_html = ""
    for r in results:
        logs_escaped = (r.logs or "").replace("&", "&amp;").replace("<", "&lt;")
        rows_html += (
            f"<tr>"
            f"<td>{r.node_id}</td>"
            f"<td>{r.output_path or ''}</td>"
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
