"""Authenticated HTTP boundary for local automation workflows."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.models.schemas import User
from app.services.automation_triage import triage_cosmology_prompt


router = APIRouter(prefix="/api/automation", tags=["automation"])

_AUTOMATION_PROMPT_MAX_LEN = 50_000


class CosmologyTriageRequest(BaseModel):
    prompt: str = Field(..., max_length=_AUTOMATION_PROMPT_MAX_LEN)


class CosmologyTriageRunCall(BaseModel):
    tool: str | None
    dataset_keys: list[str]
    model: str | None
    supernova_sets: list[str] | None


class CosmologyTriageResponse(BaseModel):
    is_cosmo_workflow: bool
    is_research_detour: bool
    direct_route: bool
    run_calls: list[CosmologyTriageRunCall]
    non_executable_keys: list[str]
    verdict: Literal["EXECUTE", "DETOUR", "NO_RUN", "MISS"]


@router.post("/cosmology/triage", response_model=CosmologyTriageResponse)
async def triage_cosmology_for_automation(
    req: CosmologyTriageRequest,
    _user: User = Depends(get_current_user),
) -> CosmologyTriageResponse:
    """Return the platform's deterministic preflight routing decision.

    This endpoint performs no model inference and executes no analysis tools.
    Authentication follows the same JWT/local-development rules as the rest of
    the protected platform API.
    """

    return CosmologyTriageResponse.model_validate(
        triage_cosmology_prompt(req.prompt)
    )
