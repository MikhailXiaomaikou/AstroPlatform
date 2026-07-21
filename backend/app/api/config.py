"""Public runtime config endpoint for the frontend.

Exposes a small, unauthenticated read-only view of backend configuration
that the frontend needs to render correctly (e.g. which research focus
the backend is gating tools to). Nothing sensitive — no API keys, no
DB URLs, no secrets.

Action 6 (Cosmology Focus) — added 2026-05-08.  Frontend uses this to
hide non-cosmology nav items when ASTRO_RESEARCH_FOCUS=cosmology.
"""

from fastapi import APIRouter

from app.api.chat import _ASTRO_RESEARCH_FOCUS
from app.config import settings

router = APIRouter(tags=["config"])


@router.get("/api/config")
async def get_config() -> dict:
    """Return public runtime config.

    Currently exposes only `focus` (which research-focus mode the backend
    is running in: "cosmology" or "all").  Frontend uses it to gate the
    top nav so users don't see entry points for tools the backend has
    physically removed.
    """
    return {
        "focus": _ASTRO_RESEARCH_FOCUS,
        "signup_mode": settings.signup_mode,
        "claim_audit_enabled": settings.claim_audit_enabled,
        # Public execution topology lets the frontend route users away from
        # the legacy free-text form when hosted science accepts only
        # server-registered Workspace candidates.
        "claim_audit_execution_mode": settings.claim_audit_execution_mode,
        "research_workspace_enabled": settings.research_workspace_enabled,
        "arxiv_reader_enabled": settings.arxiv_reader_enabled,
        "union3_reproduction_enabled": settings.union3_reproduction_enabled,
        "evidence_pack_v2_enabled": settings.evidence_pack_v2_enabled,
        "local_science_worker_enabled": settings.local_science_worker_enabled,
        "workflow_registry_v2_enabled": settings.workflow_registry_v2_enabled,
        "foundry_gap_tracking_enabled": settings.foundry_gap_tracking_enabled,
        "foundry_ai_drafting_enabled": settings.foundry_ai_drafting_enabled,
        "foundry_auto_demo_enabled": settings.foundry_auto_demo_enabled,
        "foundry_candidate_catalog_enabled": (
            settings.foundry_candidate_catalog_enabled
        ),
        "foundry_registration_enabled": settings.foundry_registration_enabled,
        "analytics_requires_consent": True,
        # These values are deliberately public: they identify who operates
        # the hosted service and where users can exercise privacy rights.
        "privacy_notice": {
            "operator_name": settings.privacy_operator_name,
            "contact": settings.privacy_contact,
            "jurisdiction": settings.privacy_jurisdiction,
            "notice_url": "/privacy",
        },
    }
