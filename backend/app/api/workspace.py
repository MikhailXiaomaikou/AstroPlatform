"""Workspace management API — batch operations, tags, notes, export."""

import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.connectors.registry import CONNECTORS_KEYS, get_connector
from app.models.database import get_db
from app.models.schemas import DataFile, DataNote, DataTag, User

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


# ── Batch Query ──

class BatchTarget(BaseModel):
    name: str
    ra: float | None = None
    dec: float | None = None

class BatchQueryRequest(BaseModel):
    targets: list[BatchTarget]
    sources: list[str] = ["sdss", "gaia", "simbad"]
    radius: float = 0.1

@router.post("/batch-search")
async def batch_search(req: BatchQueryRequest):
    """Search multiple targets across multiple sources."""
    import asyncio

    for s in req.sources:
        if s not in CONNECTORS_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown source: {s}")

    results = {}
    for target in req.targets[:50]:  # Limit to 50 targets
        tasks = [
            get_connector(s).search(target.name, ra=target.ra, dec=target.dec, radius=req.radius)
            for s in req.sources
        ]
        source_results = await asyncio.gather(*tasks, return_exceptions=True)
        target_results = []
        for source_name, result in zip(req.sources, source_results):
            if isinstance(result, Exception):
                continue
            for obj in result:
                target_results.append({
                    "source": obj.source,
                    "object_id": obj.object_id,
                    "name": obj.name,
                    "ra": obj.ra,
                    "dec": obj.dec,
                    "object_type": obj.object_type,
                    "magnitude": obj.magnitude,
                    "redshift": obj.redshift,
                })
        results[target.name] = target_results

    return {"results": results, "target_count": len(req.targets)}


# ── Tags ──

class TagRequest(BaseModel):
    tag: str

@router.post("/files/{file_id}/tags")
async def add_tag(
    file_id: str,
    req: TagRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fid = uuid.UUID(file_id)
    tag = DataTag(user_id=user.id, data_file_id=fid, tag=req.tag)
    db.add(tag)
    await db.commit()
    return {"id": str(tag.id), "tag": req.tag}


@router.get("/files/{file_id}/tags")
async def list_tags(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fid = uuid.UUID(file_id)
    result = await db.execute(
        select(DataTag).where(DataTag.data_file_id == fid, DataTag.user_id == user.id)
    )
    return [{"id": str(t.id), "tag": t.tag} for t in result.scalars().all()]


@router.delete("/files/{file_id}/tags/{tag_id}")
async def remove_tag(
    file_id: str,
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tid = uuid.UUID(tag_id)
    await db.execute(delete(DataTag).where(DataTag.id == tid, DataTag.user_id == user.id))
    await db.commit()
    return {"deleted": True}


# ── Notes ──

class NoteRequest(BaseModel):
    content: str

@router.post("/files/{file_id}/notes")
async def add_note(
    file_id: str,
    req: NoteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fid = uuid.UUID(file_id)
    note = DataNote(user_id=user.id, data_file_id=fid, content=req.content)
    db.add(note)
    await db.commit()
    return {"id": str(note.id), "content": req.content}


@router.get("/files/{file_id}/notes")
async def list_notes(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fid = uuid.UUID(file_id)
    result = await db.execute(
        select(DataNote)
        .where(DataNote.data_file_id == fid, DataNote.user_id == user.id)
        .order_by(DataNote.created_at.desc())
    )
    return [
        {"id": str(n.id), "content": n.content, "created_at": n.created_at.isoformat() if n.created_at else None}
        for n in result.scalars().all()
    ]


# ── Export ──

@router.get("/export/{file_id}")
async def export_data(
    file_id: str,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Export data file content as CSV, VOTable, or LaTeX."""
    from astropy.io import fits
    from astropy.table import Table
    from app.storage import download_fits

    fid = uuid.UUID(file_id)
    result = await db.execute(
        select(DataFile).where(DataFile.id == fid, DataFile.user_id == user.id)
    )
    data_file = result.scalar_one_or_none()
    if not data_file:
        raise HTTPException(status_code=404, detail="File not found")

    if not data_file.fits_path:
        raise HTTPException(status_code=400, detail="No FITS data available")

    raw = download_fits(data_file.fits_path)
    hdul = fits.open(io.BytesIO(raw))

    # Find first table HDU
    table = None
    for hdu in hdul:
        if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU)):
            table = Table.read(hdu)
            break

    hdul.close()

    if table is None:
        raise HTTPException(status_code=400, detail="No table data in FITS file")

    buf = io.StringIO()

    if format == "csv":
        table.write(buf, format="ascii.csv")
        media_type = "text/csv"
        filename = f"export-{file_id[:8]}.csv"
    elif format == "votable":
        buf_bytes = io.BytesIO()
        table.write(buf_bytes, format="votable")
        from fastapi.responses import Response
        return Response(
            content=buf_bytes.getvalue(),
            media_type="application/xml",
            headers={"Content-Disposition": f"attachment; filename=export-{file_id[:8]}.xml"},
        )
    elif format == "latex":
        table.write(buf, format="ascii.latex")
        media_type = "text/plain"
        filename = f"export-{file_id[:8]}.tex"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format}. Use csv, votable, or latex")

    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Batch Target Upload ──

@router.post("/batch-upload")
async def batch_upload_targets(file: UploadFile = File(...)):
    """Parse a CSV file of targets (name, ra, dec) for batch search."""
    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    targets = []
    for row in reader:
        name = row.get("name", row.get("Name", row.get("target", "")))
        ra = None
        dec = None
        try:
            ra = float(row.get("ra", row.get("RA", row.get("ra_deg", ""))))
        except (ValueError, TypeError):
            pass
        try:
            dec = float(row.get("dec", row.get("DEC", row.get("dec_deg", ""))))
        except (ValueError, TypeError):
            pass
        if name:
            targets.append({"name": name, "ra": ra, "dec": dec})

    return {"targets": targets, "count": len(targets)}
