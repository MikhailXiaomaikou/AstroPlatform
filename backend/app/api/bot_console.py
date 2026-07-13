"""Authenticated, local-only HTTP boundary for the Bot Console UI."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth import get_current_user
from app.models.schemas import User
from app.rate_limit import limiter
from app.services import local_bot_console as console


router = APIRouter(prefix="/api/automation", tags=["bot-console"])

_MAX_CHAT_MESSAGES = 16
_MAX_CHAT_MESSAGE_CHARS = 8_000
_MAX_CHAT_TOTAL_CHARS = 40_000


class BotChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=_MAX_CHAT_MESSAGE_CHARS)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content cannot be blank")
        return value


class BotChatRequest(BaseModel):
    messages: list[BotChatMessage] = Field(
        ...,
        min_length=1,
        max_length=_MAX_CHAT_MESSAGES,
    )

    @model_validator(mode="after")
    def validate_conversation(self) -> "BotChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("the final message must be from the user")
        total = sum(len(message.content) for message in self.messages)
        if total > _MAX_CHAT_TOTAL_CHARS:
            raise ValueError("conversation is too large")
        return self


class BotChatResponse(BaseModel):
    reply: str
    model: str


class ResearchStatusResponse(BaseModel):
    week_id: str
    status: str
    status_label: str
    bot_online: bool
    model: str
    schedule_primary: str | None
    schedule_recovery: str | None
    llm_calls_used: int
    llm_calls_limit: int
    executed_success: int
    needs_review: int
    report_available: bool
    report_name: str | None
    notification_status: str | None
    can_trigger: bool


class ResearchReportResponse(BaseModel):
    week_id: str
    report_name: str
    markdown: str
    truncated: bool
    updated_at: str


class ResearchTriggerResponse(BaseModel):
    week_id: str
    submitted: bool
    status: str
    message: str


async def require_console_runtime_enabled() -> None:
    """Hide the existence of this machine-control surface in production.

    Declared ahead of ``get_current_user`` in ``require_local_operator`` so
    this 404 is raised before authentication is even checked — an
    unauthenticated production probe must not be able to tell the route
    exists by seeing 401 instead of 404.
    """

    if not console.local_runtime_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    # A deployment may accidentally set the CLI flags while binding publicly.
    # Refuse the local surface if an explicit production-style opt-out is set.
    if os.getenv("BOT_CONSOLE_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        raise HTTPException(status_code=404, detail="Not found")


async def require_local_operator(
    request: Request,
    _runtime_enabled: None = Depends(require_console_runtime_enabled),
    user: User = Depends(get_current_user),
) -> User:
    """Require an authenticated request from the local browser/runtime.

    This check intentionally does not trust X-Forwarded-For.  It remains active
    when LOCAL_DEV_NO_AUTH is enabled, because the normal local-dev identity is
    not enough to distinguish the user's local UI from an allowed hosted CORS
    origin attempting to reach localhost.
    """

    del _runtime_enabled

    peer = request.client.host if request.client is not None else None
    if not console.is_loopback_peer(peer):
        raise HTTPException(status_code=403, detail="Local operator access required")

    origin = request.headers.get("origin")
    if origin is not None:
        normalized_origin = origin.strip().rstrip("/")
        if normalized_origin not in console.allowed_local_origins():
            raise HTTPException(status_code=403, detail="Local operator access required")

    return user


@router.post("/bot/chat", response_model=BotChatResponse)
@limiter.limit("6/minute")
async def bot_chat(
    request: Request,
    req: BotChatRequest,
    _operator: User = Depends(require_local_operator),
) -> BotChatResponse:
    del request, _operator
    try:
        reply = await console.chat_with_sol(
            [message.model_dump() for message in req.messages]
        )
    except console.SolUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The linked local model is temporarily unavailable.",
        ) from None
    return BotChatResponse(reply=reply, model=console.sol_model())


@router.get("/research/status", response_model=ResearchStatusResponse)
async def get_research_status(
    _operator: User = Depends(require_local_operator),
) -> ResearchStatusResponse:
    del _operator
    try:
        payload = await console.research_status()
    except console.ResearchUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local research system is temporarily unavailable.",
        ) from None
    return ResearchStatusResponse.model_validate(payload)


@router.get("/research/report", response_model=ResearchReportResponse)
async def get_research_report(
    _operator: User = Depends(require_local_operator),
) -> ResearchReportResponse:
    del _operator
    try:
        payload = console.research_report()
    except console.ResearchUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local research system is temporarily unavailable.",
        ) from None
    if payload is None:
        raise HTTPException(status_code=404, detail="No readable weekly research report yet.")
    return ResearchReportResponse.model_validate(payload)


@router.post("/research/trigger", response_model=ResearchTriggerResponse)
@limiter.limit("3/minute")
async def trigger_research(
    request: Request,
    _operator: User = Depends(require_local_operator),
) -> ResearchTriggerResponse:
    del request, _operator
    try:
        payload = await console.trigger_research()
    except console.ResearchUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The research run is not configured or could not be started.",
        ) from None
    return ResearchTriggerResponse.model_validate(payload)
