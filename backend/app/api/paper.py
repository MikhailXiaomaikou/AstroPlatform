"""Paper draft generation and validation endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import ChatSession, PaperDraft, User
from app.services.analysis_validator import validate_analysis
from app.services.paper_generator import generate_paper_draft, generate_reproducibility_appendix, render_latex

router = APIRouter(prefix="/api/paper", tags=["paper"])


class PaperGenerateRequest(BaseModel):
    session_id: str
    journal_format: str = "aastex"
    override_validation: bool = False


class PaperUpdateRequest(BaseModel):
    paper_json: dict


@router.post("/generate")
async def generate_paper(
    req: PaperGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        session_uuid = uuid.UUID(req.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session ID") from exc

    session = (
        await db.execute(
            select(ChatSession).where(ChatSession.id == session_uuid, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    validation = await validate_analysis(str(session.id), db)
    if validation["overall_status"] == "FAIL" and not req.override_validation:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Validation failed. Override to continue with draft generation.",
                "validation": validation,
            },
        )

    generated = await generate_paper_draft(str(session.id), req.journal_format, db)
    draft = PaperDraft(
        user_id=user.id,
        session_id=session.id,
        journal_format=req.journal_format,
        paper_json=generated["paper_json"],
        latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        validation=validation,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    return {
        "id": str(draft.id),
        "paper_json": draft.paper_json,
        "latex_source": draft.latex_source,
        "bibtex": draft.bibtex,
        "validation": draft.validation,
    }


@router.post("/validate/{session_id}")
async def validate_session_for_paper(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session ID") from exc

    session = (
        await db.execute(
            select(ChatSession).where(ChatSession.id == session_uuid, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await validate_analysis(str(session.id), db)


@router.get("/{paper_id}/download")
async def download_paper_latex(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        paper_uuid = uuid.UUID(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid paper ID") from exc

    draft = (
        await db.execute(
            select(PaperDraft).where(PaperDraft.id == paper_uuid, PaperDraft.user_id == user.id)
        )
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Paper draft not found")

    return Response(
        content=draft.latex_source,
        media_type="application/x-tex",
        headers={"Content-Disposition": f'attachment; filename="paper_{paper_id}.tex"'},
    )


@router.get("/{paper_id}/bibtex")
async def download_paper_bibtex(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        paper_uuid = uuid.UUID(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid paper ID") from exc

    draft = (
        await db.execute(
            select(PaperDraft).where(PaperDraft.id == paper_uuid, PaperDraft.user_id == user.id)
        )
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Paper draft not found")

    return Response(
        content=draft.bibtex,
        media_type="application/x-bibtex",
        headers={"Content-Disposition": f'attachment; filename="paper_{paper_id}.bib"'},
    )


@router.put("/{paper_id}")
async def update_paper_draft(
    paper_id: str,
    req: PaperUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        paper_uuid = uuid.UUID(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid paper ID") from exc

    draft = (
        await db.execute(
            select(PaperDraft).where(PaperDraft.id == paper_uuid, PaperDraft.user_id == user.id)
        )
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Paper draft not found")

    appendix = await generate_reproducibility_appendix(str(draft.session_id), db)
    draft.paper_json = req.paper_json
    draft.latex_source = render_latex(req.paper_json, draft.journal_format) + "\n\n" + appendix
    await db.commit()
    await db.refresh(draft)

    return {
        "id": str(draft.id),
        "paper_json": draft.paper_json,
        "latex_source": draft.latex_source,
        "bibtex": draft.bibtex,
        "validation": draft.validation,
    }
