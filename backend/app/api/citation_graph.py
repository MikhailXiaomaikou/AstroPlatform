"""Citation graph API — build and return citation networks from NASA ADS."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.citation_graph import build_citation_graph

router = APIRouter(prefix="/api/literature", tags=["literature"])
logger = logging.getLogger(__name__)


class CitationGraphRequest(BaseModel):
    bibcodes: list[str] = Field(..., min_length=1, max_length=20)
    depth: int = Field(1, ge=1, le=3)


@router.post("/citation-graph")
async def citation_graph(req: CitationGraphRequest):
    """Build a citation graph for the given bibcodes."""
    graph = await build_citation_graph(req.bibcodes, depth=req.depth)
    return graph
