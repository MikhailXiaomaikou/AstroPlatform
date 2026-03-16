"""Visualization API — interactive chart generation and export."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.pipeline.nodes.plot_interactive import (
    CHART_TEMPLATES,
    _BUILDERS,
)

router = APIRouter(prefix="/api/viz", tags=["visualization"])


# ── Request / Response models ──

class GenerateRequest(BaseModel):
    chart_type: str
    data: dict
    params: dict = {}


class ExportRequest(BaseModel):
    plot_json: dict
    format: str = "png"
    width: int = 1200
    height: int = 800


# ── Endpoints ──

@router.get("/templates")
async def list_templates():
    """Return available chart templates with names and descriptions."""
    return CHART_TEMPLATES


@router.post("/generate")
async def generate_chart(req: GenerateRequest):
    """Generate a Plotly JSON figure from the given chart type and data."""
    builder = _BUILDERS.get(req.chart_type)
    if builder is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown chart type: {req.chart_type}. "
                   f"Available: {list(CHART_TEMPLATES.keys())}",
        )

    figure_dict = builder(req.data, req.params)
    return {"plot_json": figure_dict, "chart_type": req.chart_type}


@router.post("/export")
async def export_chart(req: ExportRequest):
    """Render a Plotly figure to PNG or PDF and return as file download."""
    fmt = req.format.lower()
    if fmt not in ("png", "pdf"):
        raise HTTPException(status_code=400, detail="format must be 'png' or 'pdf'")

    media_types = {
        "png": "image/png",
        "pdf": "application/pdf",
    }
    media_type = media_types[fmt]

    try:
        import plotly.io as pio
        img_bytes = pio.to_image(
            req.plot_json,
            format=fmt,
            width=req.width,
            height=req.height,
            engine="kaleido",
        )
        return Response(
            content=img_bytes,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename=plot.{fmt}"},
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="kaleido not installed for image export",
        )
