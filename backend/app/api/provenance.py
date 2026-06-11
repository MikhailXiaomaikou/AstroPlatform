"""Provenance API endpoints."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.auth import get_current_user
from app.models.schemas import User

router = APIRouter(prefix="/api/provenance", tags=["provenance"])


@router.get("/{entity_id}/lineage")
async def get_lineage(entity_id: str, _user: User = Depends(get_current_user)):
    """Get full lineage graph for an entity."""
    from app.services.provenance import get_lineage as _get_lineage
    return _get_lineage(entity_id)


@router.get("/{entity_id}/export/ivoa")
async def export_ivoa(entity_id: str, _user: User = Depends(get_current_user)):
    """Export provenance as IVOA ProvDM XML."""
    from app.services.provenance import export_provenance_ivoa
    xml = export_provenance_ivoa(entity_id)
    return Response(content=xml, media_type="application/xml")


@router.get("/{entity_id}/doi-metadata")
async def doi_metadata(entity_id: str, title: str = "",
                       _user: User = Depends(get_current_user)):
    """Generate DataCite-compatible DOI metadata."""
    from app.services.provenance import generate_doi_metadata
    return generate_doi_metadata(entity_id, title=title)


@router.get("/{entity_id}/requirements.txt")
async def export_requirements(entity_id: str, _user: User = Depends(get_current_user)):
    """Export pinned requirements.txt for reproducibility."""
    from app.services.provenance import export_requirements_pinned
    content = export_requirements_pinned(entity_id)
    return Response(content=content, media_type="text/plain",
                   headers={"Content-Disposition": f"attachment; filename=requirements-{entity_id[:8]}.txt"})


@router.get("/{entity_id}/reproduce")
async def reproduce(entity_id: str, _user: User = Depends(get_current_user)):
    """Get reproducibility package."""
    from app.services.provenance import get_reproducibility_package
    return get_reproducibility_package(entity_id)
