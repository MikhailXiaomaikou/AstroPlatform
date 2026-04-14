"""Provenance API endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/provenance", tags=["provenance"])


@router.get("/{entity_id}/lineage")
async def get_lineage(entity_id: str):
    """Get full lineage graph for an entity."""
    from app.services.provenance import get_lineage as _get_lineage
    return _get_lineage(entity_id)


@router.get("/{entity_id}/export/ivoa")
async def export_ivoa(entity_id: str):
    """Export provenance as IVOA ProvDM XML."""
    from app.services.provenance import export_provenance_ivoa
    xml = export_provenance_ivoa(entity_id)
    return Response(content=xml, media_type="application/xml")


@router.get("/{entity_id}/doi-metadata")
async def doi_metadata(entity_id: str, title: str = ""):
    """Generate DataCite-compatible DOI metadata."""
    from app.services.provenance import generate_doi_metadata
    return generate_doi_metadata(entity_id, title=title)


@router.get("/{entity_id}/reproduce")
async def reproduce(entity_id: str):
    """Get reproducibility package."""
    from app.services.provenance import get_reproducibility_package
    return get_reproducibility_package(entity_id)
