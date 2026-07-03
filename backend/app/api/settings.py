"""User settings API — manage API keys and preferences."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import User

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Supported AI providers
PROVIDERS = {
    "anthropic": {"name": "Anthropic (Claude)", "prefix": "sk-ant-"},
    "openai": {"name": "OpenAI (GPT)", "prefix": "sk-"},
    "google": {"name": "Google (Gemini)", "prefix": "AI"},
    "deepseek": {"name": "DeepSeek", "prefix": "sk-"},
    "custom": {"name": "Custom / Other", "prefix": ""},
}


class SaveKeyRequest(BaseModel):
    provider: str
    key: str


class DeleteKeyRequest(BaseModel):
    provider: str


class KeyInfo(BaseModel):
    provider: str
    name: str
    masked_key: str


class AllKeysResponse(BaseModel):
    keys: list[KeyInfo]
    providers: dict  # available providers metadata


def _mask_key(key: str) -> str:
    if len(key) <= 12:
        return key[:3] + "..." + key[-3:]
    return key[:8] + "..." + key[-4:]


def _get_keys(user: User) -> dict:
    """Get a COPY of the user's api_keys dict, migrating from legacy field if needed."""
    keys = dict(user.api_keys or {})  # must copy to avoid SQLAlchemy same-ref detection issue
    # Migrate legacy single-key field
    if not keys.get("anthropic") and user.anthropic_api_key:
        keys["anthropic"] = user.anthropic_api_key
    return keys


@router.get("/api-keys", response_model=AllKeysResponse)
async def get_api_keys(user: User = Depends(get_current_user)):
    """List all configured API keys (masked)."""
    keys = _get_keys(user)
    key_list = []
    for provider_id, raw_key in keys.items():
        if raw_key:
            meta = PROVIDERS.get(provider_id, {"name": provider_id, "prefix": ""})
            key_list.append(KeyInfo(
                provider=provider_id,
                name=meta["name"],
                masked_key=_mask_key(raw_key),
            ))
    return AllKeysResponse(keys=key_list, providers=PROVIDERS)


@router.put("/api-keys")
async def save_api_key(
    req: SaveKeyRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save or update an API key for a provider."""
    key = req.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    keys = _get_keys(user)
    keys[req.provider] = key
    user.api_keys = keys
    # Security: keys live ONLY in the Fernet-encrypted api_keys blob. The
    # legacy anthropic_api_key column is plain TEXT; _get_keys() above already
    # migrated any legacy value into `keys`, so null the cleartext copy here.
    user.anthropic_api_key = None
    await db.commit()
    return {"saved": True, "provider": req.provider, "masked_key": _mask_key(key)}


@router.delete("/api-keys")
async def delete_api_key(
    req: DeleteKeyRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove an API key for a provider."""
    keys = _get_keys(user)
    if req.provider in keys:
        del keys[req.provider]
    user.api_keys = keys if keys else None
    if req.provider == "anthropic":
        user.anthropic_api_key = None
    await db.commit()
    return {"deleted": True, "provider": req.provider}


# ── Legacy single-key endpoints (kept for backwards compat) ──

@router.get("/api-key")
async def get_api_key_legacy(user: User = Depends(get_current_user)):
    keys = _get_keys(user)
    anthropic_key = keys.get("anthropic")
    if anthropic_key:
        return {"has_key": True, "masked_key": _mask_key(anthropic_key)}
    return {"has_key": False, "masked_key": None}


@router.put("/api-key")
async def save_api_key_legacy(
    req: SaveKeyRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    key = req.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    keys = _get_keys(user)
    keys["anthropic"] = key
    user.api_keys = keys
    # Never write the cleartext legacy column (see save_api_key above).
    user.anthropic_api_key = None
    await db.commit()
    return {"saved": True, "masked_key": _mask_key(key)}


@router.delete("/api-key")
async def delete_api_key_legacy(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    keys = _get_keys(user)
    keys.pop("anthropic", None)
    user.api_keys = keys if keys else None
    user.anthropic_api_key = None
    await db.commit()
    return {"deleted": True}
