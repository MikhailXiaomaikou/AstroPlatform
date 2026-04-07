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

from pydantic import BaseModel as _BaseModel


class ReportRequest(_BaseModel):
    analysis: dict  # SpectrumAnalysis result from /fits/analyze
    object_name: str = ""
    fits_path: str = ""


@router.post("/report/markdown")
async def export_analysis_markdown(req: ReportRequest):
    """Export an AI spectrum analysis as a Markdown report."""
    a = req.analysis
    lines = [
        f"# Spectrum Analysis Report",
        f"",
        f"**Object:** {req.object_name or 'Unknown'}",
        f"**FITS file:** `{req.fits_path}`",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
    ]

    if a.get("ai_classification"):
        lines.append(f"## Classification")
        lines.append(f"")
        lines.append(f"**{a['ai_classification']}** (confidence: {a.get('ai_confidence', 'unknown')})")
        lines.append(f"")

    if a.get("ai_redshift") and a["ai_redshift"].get("value") is not None:
        z = a["ai_redshift"]
        lines.append(f"**Redshift:** z = {z['value']:.6f}" + (f" ± {z['uncertainty']:.6f}" if z.get("uncertainty") else ""))
        lines.append(f"")

    if a.get("ai_summary"):
        lines.append(f"## Summary")
        lines.append(f"")
        lines.append(a["ai_summary"])
        lines.append(f"")

    if a.get("ai_narrative"):
        lines.append(f"## Detailed Analysis")
        lines.append(f"")
        lines.append(a["ai_narrative"])
        lines.append(f"")

    ai_lines = a.get("ai_lines", [])
    if ai_lines:
        lines.append(f"## Identified Spectral Lines")
        lines.append(f"")
        lines.append(f"| Line | Rest (Å) | Observed (Å) | Type | Strength |")
        lines.append(f"|------|----------|-------------|------|----------|")
        for ln in ai_lines:
            rw = ln.get('rest_wavelength')
            ow = ln.get('observed_wavelength')
            lines.append(
                f"| {ln.get('name','')} | {f'{rw:.1f}' if isinstance(rw, (int,float)) else ''} | "
                f"{f'{ow:.1f}' if isinstance(ow, (int,float)) else ''} | {ln.get('type','')} | {ln.get('strength','')} |"
            )
        lines.append(f"")

    if a.get("ai_special_features"):
        lines.append(f"## Special Features")
        lines.append(f"")
        for feat in a["ai_special_features"]:
            lines.append(f"- {feat}")
        lines.append(f"")

    if a.get("ai_next_steps"):
        lines.append(f"## Suggested Next Steps")
        lines.append(f"")
        for i, step in enumerate(a["ai_next_steps"], 1):
            lines.append(f"{i}. {step}")
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"*Generated by Astro Platform AI Spectrum Analyzer*")

    md = "\n".join(lines)
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="analysis_report.md"'},
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
        f"Generated by Astro Platform AI Research Agent",
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
            lines.append(f"# (Use astroquery to reproduce this search)")
            lines.append("")

        elif tool == "run_adql":
            query = inp.get("query", "")
            service = inp.get("service", "gaia")
            lines.append(f"from astroquery.utils.tap.core import TapPlus")
            tap_urls = {
                "gaia": "https://gea.esac.esa.int/tap-server/tap",
                "simbad": "https://simbad.u-strasbg.fr/simbad/sim-tap",
                "vizier": "https://tapvizier.u-strasbg.fr/TAPVizieR/tap",
            }
            url = tap_urls.get(service, tap_urls["gaia"])
            lines.append(f'tap = TapPlus(url="{url}")')
            lines.append(f"query = {repr(query)}")
            lines.append(f"job = tap.launch_job(query)")
            lines.append(f"results_{i} = job.get_results()")
            lines.append(f"print(f'Step {i}: {{len(results_{i})}} rows')")
            lines.append("")

        elif tool == "run_python":
            code = inp.get("code", "")
            lines.append(f"# Python analysis code:")
            for code_line in code.split("\n"):
                lines.append(code_line)
            lines.append("")

        elif tool == "get_object_info":
            name = inp.get("name", "")
            lines.append(f"from astroquery.simbad import Simbad")
            lines.append(f"result_{i} = Simbad.query_object({repr(name)})")
            lines.append(f"print(result_{i})")
            lines.append("")

        elif tool == "analyze_spectrum":
            path = inp.get("fits_path", "")
            lines.append(f"from astropy.io import fits")
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
        headers={"Content-Disposition": f'attachment; filename="astro_workflow.py"'},
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
            f"# Astro Platform Search Results\n",
            f"\n",
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
        "# Search results from Astro Platform\n",
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
