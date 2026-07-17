"""GET /api/config — public runtime config for the frontend.

Action 6 (Cosmology Focus, 2026-05-08): exposes the current
ASTRO_RESEARCH_FOCUS so the frontend can hide non-cosmology nav
items in cosmology mode.

Contracts:
1. Endpoint returns 200 with a JSON body containing `focus` key.
2. The value matches whatever the backend's _ASTRO_RESEARCH_FOCUS
   was resolved to at module load time (default "cosmology" since
   2026-05-08, but env can override).
3. No auth needed — this is public config (just the focus mode,
   no secrets).
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def reload_chat_and_config(monkeypatch):
    """Reload chat + config modules with a fresh focus env."""
    def _reload(focus_value: str | None):
        if focus_value is None:
            monkeypatch.delenv("ASTRO_RESEARCH_FOCUS", raising=False)
        else:
            monkeypatch.setenv("ASTRO_RESEARCH_FOCUS", focus_value)
        import app.api.chat as chat_mod
        import app.api.config as config_mod
        importlib.reload(chat_mod)
        importlib.reload(config_mod)
        return config_mod
    return _reload


def _client_for(config_mod):
    """Build a tiny FastAPI app exposing only the reloaded config router.

    Intentionally avoids importing app.main (which has heavy DB / Redis
    setup); the config endpoint depends only on chat module state.
    """
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(config_mod.router)
    return TestClient(app)


def test_config_endpoint_returns_focus_field(reload_chat_and_config):
    config_mod = reload_chat_and_config("cosmology")
    with _client_for(config_mod) as client:
        r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "focus" in body
    assert body["focus"] == "cosmology"


def test_config_endpoint_reflects_all_mode(reload_chat_and_config):
    config_mod = reload_chat_and_config("all")
    with _client_for(config_mod) as client:
        r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["focus"] == "all"


def test_config_endpoint_unset_env_defaults_to_cosmology(reload_chat_and_config):
    """Default since 2026-05-08 user decision."""
    config_mod = reload_chat_and_config(None)
    with _client_for(config_mod) as client:
        r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["focus"] == "cosmology"


def test_config_endpoint_no_auth_required(reload_chat_and_config):
    """Public config — no Authorization header sent."""
    config_mod = reload_chat_and_config("cosmology")
    with _client_for(config_mod) as client:
        r = client.get("/api/config", headers={})
    assert r.status_code == 200


def test_config_endpoint_publishes_hosted_privacy_identity(reload_chat_and_config, monkeypatch):
    config_mod = reload_chat_and_config("cosmology")
    monkeypatch.setattr(config_mod.settings, "privacy_operator_name", "Example Observatory")
    monkeypatch.setattr(config_mod.settings, "privacy_contact", "privacy@example.invalid")
    monkeypatch.setattr(config_mod.settings, "privacy_jurisdiction", "Example jurisdiction")
    with _client_for(config_mod) as client:
        body = client.get("/api/config").json()
    assert body["privacy_notice"] == {
        "operator_name": "Example Observatory",
        "contact": "privacy@example.invalid",
        "jurisdiction": "Example jurisdiction",
        "notice_url": "/privacy",
    }


@pytest.fixture(autouse=True)
def _restore_modules(monkeypatch):
    """Restore chat + config modules after each test."""
    yield
    monkeypatch.delenv("ASTRO_RESEARCH_FOCUS", raising=False)
    import app.api.chat as chat_mod
    import app.api.config as config_mod
    importlib.reload(chat_mod)
    importlib.reload(config_mod)
