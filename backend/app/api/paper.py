"""Paper draft generation and validation endpoints."""

from __future__ import annotations

import uuid
import secrets
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import ChatSession, PaperDraft, User
from app.services.analysis_validator import (
    PUBLICATION_LANGUAGE_ATTESTATION_KEY,
    bind_paper_validation,
    build_publication_language_attestation,
    paper_validation_is_publishable,
    validate_analysis,
)
from app.services.paper_generator import (
    generate_paper_draft,
    generate_reproducibility_appendix,
    render_latex,
)

router = APIRouter(prefix="/api/paper", tags=["paper"])


class PaperGenerateRequest(BaseModel):
    session_id: str
    journal_format: str = "aastex"
    # Backward-compatible request parsing only. Generation always produces a
    # private draft, and publication independently requires a fresh PASS.
    override_validation: bool = False


class PaperUpdateRequest(BaseModel):
    paper_json: dict


class PaperLanguageReviewRequest(BaseModel):
    confirmed_english: bool


_UNVERIFIED_LATEX_MARKER = "% STANDARD_ASTRO_UNVERIFIED_DRAFT"
_UNVERIFIED_LATEX_BANNER = "\n".join(
    (
        _UNVERIFIED_LATEX_MARKER,
        r"\begin{center}",
        r"\fbox{\parbox{0.92\linewidth}{\centering\textbf{UNVERIFIED DRAFT --- NOT FOR PUBLICATION}}}",
        r"\end{center}",
    )
)


def _apply_unverified_watermark(latex_source: str) -> str:
    """Add a visible, idempotent warning to a private non-PASS draft."""

    if _UNVERIFIED_LATEX_MARKER in latex_source:
        return latex_source
    document_start = r"\begin{document}"
    if document_start in latex_source:
        return latex_source.replace(
            document_start,
            f"{document_start}\n{_UNVERIFIED_LATEX_BANNER}",
            1,
        )
    return f"{_UNVERIFIED_LATEX_BANNER}\n{latex_source}"


def _draft_has_publishable_validation(draft: PaperDraft) -> bool:
    return paper_validation_is_publishable(
        draft.validation,
        session_id=str(draft.session_id),
        owner_id=str(draft.user_id),
        paper_json=draft.paper_json,
        latex_source=draft.latex_source,
        bibtex=draft.bibtex,
        journal_format=draft.journal_format,
    )


async def _validate_and_bind_content(
    *,
    session_id: str,
    owner_id: str,
    paper_json: dict,
    clean_latex_source: str,
    bibtex: str,
    journal_format: str,
    db: AsyncSession,
) -> tuple[str, dict]:
    """Validate current authoring content and bind the result to stored bytes."""

    validation = await validate_analysis(
        session_id,
        db,
        owner_id=owner_id,
        paper_json=paper_json,
        latex_source=clean_latex_source,
        bibtex=bibtex,
    )
    stored_latex = clean_latex_source
    bound = bind_paper_validation(
        validation,
        session_id=session_id,
        owner_id=owner_id,
        paper_json=paper_json,
        latex_source=stored_latex,
        bibtex=bibtex,
        journal_format=journal_format,
    )
    if not bound.get("publishable"):
        stored_latex = _apply_unverified_watermark(clean_latex_source)
        bound = bind_paper_validation(
            validation,
            session_id=session_id,
            owner_id=owner_id,
            paper_json=paper_json,
            latex_source=stored_latex,
            bibtex=bibtex,
            journal_format=journal_format,
        )
    return stored_latex, bound


def _public_url(draft: PaperDraft) -> str | None:
    if (
        not draft.is_public
        or not draft.public_token
        or not _draft_has_publishable_validation(draft)
    ):
        return None
    return f"/papers/public/{draft.public_token}"


def _serialize_draft(
    draft: PaperDraft,
    *,
    include_evidence_snapshot: bool = False,
) -> dict:
    effectively_public = bool(
        draft.is_public
        and draft.public_token
        and _draft_has_publishable_validation(draft)
    )
    validation = deepcopy(draft.validation or {})
    if not include_evidence_snapshot and validation.pop("evidence_snapshot", None):
        validation["evidence_snapshot_redacted"] = True
    return {
        "id": str(draft.id),
        "session_id": str(draft.session_id),
        "journal_format": draft.journal_format,
        "paper_json": draft.paper_json,
        "latex_source": draft.latex_source,
        "bibtex": draft.bibtex,
        "validation": validation,
        "is_public": effectively_public,
        "public_token": draft.public_token if effectively_public else None,
        "public_url": _public_url(draft),
        "published_at": draft.published_at.isoformat()
        if effectively_public and draft.published_at
        else None,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


async def _require_owned_draft(
    paper_id: str, user: User, db: AsyncSession
) -> PaperDraft:
    try:
        paper_uuid = uuid.UUID(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid paper ID") from exc
    draft = (
        await db.execute(
            select(PaperDraft).where(
                PaperDraft.id == paper_uuid, PaperDraft.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Paper draft not found")
    return draft


async def _load_public_draft(token: str, db: AsyncSession) -> PaperDraft:
    draft = (
        await db.execute(
            select(PaperDraft).where(
                PaperDraft.public_token == token,
                PaperDraft.is_public.is_(True),
            )
        )
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Published paper draft not found")
    if not _draft_has_publishable_validation(draft):
        # Old public rows and content modified outside the API fail closed.
        # Their owner can upgrade them by publishing again, which revalidates.
        raise HTTPException(status_code=404, detail="Published paper draft not found")
    return draft


async def _new_public_token(db: AsyncSession) -> str:
    for _ in range(8):
        token = secrets.token_urlsafe(24)
        existing = (
            await db.execute(
                select(PaperDraft.id).where(PaperDraft.public_token == token)
            )
        ).scalar_one_or_none()
        if existing is None:
            return token
    raise HTTPException(
        status_code=500, detail="Could not allocate a public paper token"
    )


@router.get("")
async def list_paper_drafts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    drafts = (
        (
            await db.execute(
                select(PaperDraft)
                .where(PaperDraft.user_id == user.id)
                .order_by(PaperDraft.updated_at.desc(), PaperDraft.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [_serialize_draft(draft) for draft in drafts]


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
            select(ChatSession).where(
                ChatSession.id == session_uuid, ChatSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    generated = await generate_paper_draft(str(session.id), req.journal_format, db)
    latex_source, validation = await _validate_and_bind_content(
        session_id=str(session.id),
        owner_id=str(user.id),
        paper_json=generated["paper_json"],
        clean_latex_source=generated["latex_source"],
        bibtex=generated["bibtex"],
        journal_format=req.journal_format,
        db=db,
    )
    draft = PaperDraft(
        user_id=user.id,
        session_id=session.id,
        journal_format=req.journal_format,
        paper_json=generated["paper_json"],
        latex_source=latex_source,
        bibtex=generated["bibtex"],
        validation=validation,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    return _serialize_draft(draft)


@router.get("/public/{token}")
async def get_public_paper_draft(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_public_draft(token, db)
    # The public record exposes the evidence fingerprint and combined binding,
    # but not private session prompts or raw tool payloads in the snapshot.
    return _serialize_draft(draft, include_evidence_snapshot=False)


@router.get("/public/{token}/download")
async def download_public_paper_latex(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_public_draft(token, db)
    return Response(
        content=draft.latex_source,
        media_type="application/x-tex",
        headers={"Content-Disposition": f'attachment; filename="paper_{token}.tex"'},
    )


@router.get("/public/{token}/bibtex")
async def download_public_paper_bibtex(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_public_draft(token, db)
    return Response(
        content=draft.bibtex,
        media_type="application/x-bibtex",
        headers={"Content-Disposition": f'attachment; filename="paper_{token}.bib"'},
    )


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
            select(ChatSession).where(
                ChatSession.id == session_uuid, ChatSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await validate_analysis(
        str(session.id), db, owner_id=str(user.id)
    )


@router.get("/{paper_id}/download")
async def download_paper_latex(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    draft = await _require_owned_draft(paper_id, user, db)

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
    draft = await _require_owned_draft(paper_id, user, db)

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
    draft = await _require_owned_draft(paper_id, user, db)

    appendix = await generate_reproducibility_appendix(str(draft.session_id), db)
    clean_latex_source = (
        render_latex(req.paper_json, draft.journal_format) + "\n\n" + appendix
    )
    latex_source, validation = await _validate_and_bind_content(
        session_id=str(draft.session_id),
        owner_id=str(user.id),
        paper_json=req.paper_json,
        clean_latex_source=clean_latex_source,
        bibtex=draft.bibtex,
        journal_format=draft.journal_format,
        db=db,
    )
    draft.paper_json = req.paper_json
    draft.latex_source = latex_source
    draft.validation = validation
    # Editing creates a new artifact. A previously published validation and
    # public URL must never carry over to the changed content.
    draft.is_public = False
    draft.public_token = None
    draft.published_at = None
    await db.commit()
    await db.refresh(draft)

    return _serialize_draft(draft)


@router.post("/{paper_id}/language-review")
async def attest_paper_english_language(
    paper_id: str,
    req: PaperLanguageReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Record an explicit human English-language review of exact draft bytes."""

    if req.confirmed_english is not True:
        raise HTTPException(
            status_code=400,
            detail="English-language review must be explicitly confirmed.",
        )
    draft = await _require_owned_draft(paper_id, user, db)
    reviewed_json = deepcopy(draft.paper_json)
    reviewed_json.pop(PUBLICATION_LANGUAGE_ATTESTATION_KEY, None)
    attestation = build_publication_language_attestation(
        reviewed_json,
        source="human_review",
        reviewer_id=str(user.id),
    )
    if attestation is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "The draft contains text outside the supported English claim "
                "scope. Translate it before attesting."
            ),
        )
    reviewed_json[PUBLICATION_LANGUAGE_ATTESTATION_KEY] = attestation
    appendix = await generate_reproducibility_appendix(str(draft.session_id), db)
    clean_latex_source = (
        render_latex(reviewed_json, draft.journal_format) + "\n\n" + appendix
    )
    latex_source, validation = await _validate_and_bind_content(
        session_id=str(draft.session_id),
        owner_id=str(user.id),
        paper_json=reviewed_json,
        clean_latex_source=clean_latex_source,
        bibtex=draft.bibtex,
        journal_format=draft.journal_format,
        db=db,
    )
    draft.paper_json = reviewed_json
    draft.latex_source = latex_source
    draft.validation = validation
    draft.is_public = False
    draft.public_token = None
    draft.published_at = None
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft)


@router.get("/{paper_id}")
async def get_paper_draft(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    draft = await _require_owned_draft(paper_id, user, db)
    return _serialize_draft(draft)


@router.post("/{paper_id}/publish")
async def publish_paper_draft(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    draft = await _require_owned_draft(paper_id, user, db)
    appendix = await generate_reproducibility_appendix(str(draft.session_id), db)
    clean_latex_source = (
        render_latex(draft.paper_json, draft.journal_format) + "\n\n" + appendix
    )
    latex_source, validation = await _validate_and_bind_content(
        session_id=str(draft.session_id),
        owner_id=str(user.id),
        paper_json=draft.paper_json,
        clean_latex_source=clean_latex_source,
        bibtex=draft.bibtex,
        journal_format=draft.journal_format,
        db=db,
    )
    draft.latex_source = latex_source
    draft.validation = validation
    if not _draft_has_publishable_validation(draft):
        draft.is_public = False
        draft.public_token = None
        draft.published_at = None
        await db.commit()
        await db.refresh(draft)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Only a current PASS validation can be published. The draft was saved privately with an unverified watermark.",
                "validation": draft.validation,
            },
        )
    if not draft.public_token:
        draft.public_token = await _new_public_token(db)
    draft.is_public = True
    draft.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft)


@router.delete("/{paper_id}/publish")
async def unpublish_paper_draft(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    draft = await _require_owned_draft(paper_id, user, db)
    draft.is_public = False
    draft.public_token = None
    draft.published_at = None
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft)
