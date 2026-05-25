"""User-created tool macro API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.schemas import User
from app.services.user_tools import (
    UserToolError,
    create_user_tool,
    delete_user_tool,
    execute_user_tool,
    get_user_tool,
    list_user_tools,
    owner_scope,
)

router = APIRouter(prefix="/api/user-tools", tags=["user-tools"])


class UserToolStep(BaseModel):
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    continue_on_error: bool = False


class UserToolCreate(BaseModel):
    tool_id: str
    display_name: str = ""
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    })
    steps: list[UserToolStep]


class UserToolRun(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    python_session_id: str = "default"


def _scope_for_user(user: User) -> str:
    return owner_scope(str(user.id))


@router.get("")
async def list_my_user_tools(user: User = Depends(get_current_user)):
    return {"tools": list_user_tools(_scope_for_user(user))}


@router.post("")
async def create_my_user_tool(req: UserToolCreate, user: User = Depends(get_current_user)):
    try:
        tool = create_user_tool(
            scope=_scope_for_user(user),
            tool_id=req.tool_id,
            display_name=req.display_name,
            description=req.description,
            input_schema=req.input_schema,
            steps=[step.model_dump() for step in req.steps],
        )
    except UserToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return tool


@router.get("/{tool_id}")
async def get_my_user_tool(tool_id: str, user: User = Depends(get_current_user)):
    try:
        tool = get_user_tool(_scope_for_user(user), tool_id)
    except UserToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not tool:
        raise HTTPException(status_code=404, detail="User tool not found")
    return tool


@router.delete("/{tool_id}")
async def delete_my_user_tool(tool_id: str, user: User = Depends(get_current_user)):
    try:
        deleted = delete_user_tool(_scope_for_user(user), tool_id)
    except UserToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": deleted}


@router.post("/{tool_id}/run")
async def run_my_user_tool(tool_id: str, req: UserToolRun, user: User = Depends(get_current_user)):
    result = await execute_user_tool(
        scope=_scope_for_user(user),
        tool_id=tool_id,
        arguments=req.arguments,
        user_id=str(user.id),
        python_session_id=req.python_session_id,
    )
    if result.get("error_class") == "user_tool_not_found":
        raise HTTPException(status_code=404, detail=result.get("error", "User tool not found"))
    return result
