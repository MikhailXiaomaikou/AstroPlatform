"""Client-IP trust chain tests for app/rate_limit.py get_client_ip.

Vulnerability being pinned (regression): get_client_ip used to honor
CF-Connecting-IP / X-Real-IP unconditionally and take the *leftmost*
X-Forwarded-For entry — all fully attacker-controlled on a direct
connection — so per-IP rate limits could be rotated every request and
comment audit logs poisoned with arbitrary IPs.

Trust model under test (TRUSTED_PROXY_MODE, wired via app/config.py):
- "none" (default in dev): trust ONLY the transport peer
  (request.client.host); every forwarded header is ignored.
- integer N >= 1: N trusted reverse proxies in front; the real client is
  the Nth-from-the-right X-Forwarded-For entry.
  CF-Connecting-IP / X-Real-IP stay ignored.
- "render": trust the first X-Forwarded-For entry supplied by Render's edge.
- "cloudflare": Cloudflare in front; trust CF-Connecting-IP only.
- Production also defaults to "none"; proxy trust requires explicit opt-in.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.config import settings
from app.rate_limit import get_client_ip, get_rate_limit_key

_BACKEND_DIR = Path(__file__).resolve().parent.parent

SOCKET_PEER = "203.0.113.7"
SPOOFED = "6.6.6.6"
REAL_CLIENT = "198.51.100.42"


def _req(headers: dict | None = None, host: str | None = SOCKET_PEER):
    client = SimpleNamespace(host=host) if host is not None else None
    return SimpleNamespace(headers=headers or {}, client=client)


def _set_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_mode", mode, raising=False)


# ── default mode: trust nothing but the socket peer ──


def test_default_ignores_spoofed_cf_connecting_ip(monkeypatch):
    """THE repro test: a spoofed CF-Connecting-IP must not change the derived IP."""
    _set_mode(monkeypatch, "none")
    request = _req({"CF-Connecting-IP": SPOOFED})
    assert get_client_ip(request) == SOCKET_PEER


def test_default_ignores_spoofed_x_real_ip(monkeypatch):
    _set_mode(monkeypatch, "none")
    request = _req({"X-Real-IP": SPOOFED})
    assert get_client_ip(request) == SOCKET_PEER


def test_default_ignores_spoofed_x_forwarded_for(monkeypatch):
    _set_mode(monkeypatch, "none")
    request = _req({"X-Forwarded-For": f"{SPOOFED}, {SPOOFED}"})
    assert get_client_ip(request) == SOCKET_PEER


def test_default_uses_socket_peer_and_none_without_client(monkeypatch):
    _set_mode(monkeypatch, "none")
    assert get_client_ip(_req()) == SOCKET_PEER
    assert get_client_ip(_req(host=None)) is None


def test_dev_default_setting_is_none():
    """Without ENV=production the shipped default must trust no headers."""
    assert settings.trusted_proxy_mode == "none"


# ── explicit Render mode (Render puts the real client first) ──


def test_explicit_render_mode_uses_first_xff_hop(monkeypatch):
    _set_mode(monkeypatch, "render")
    request = _req({"X-Forwarded-For": f"{REAL_CLIENT}, {SPOOFED}"})
    assert get_client_ip(request) == REAL_CLIENT


def test_explicit_render_mode_no_xff_falls_back_to_socket_peer(monkeypatch):
    _set_mode(monkeypatch, "render")
    assert get_client_ip(_req()) == SOCKET_PEER


# ── generic proxy mode N=1 ──


def test_one_hop_mode_uses_rightmost_xff_hop(monkeypatch):
    _set_mode(monkeypatch, "1")
    request = _req({"X-Forwarded-For": f"{SPOOFED}, {REAL_CLIENT}"})
    assert get_client_ip(request) == REAL_CLIENT


def test_one_hop_mode_single_xff_entry(monkeypatch):
    _set_mode(monkeypatch, "1")
    request = _req({"X-Forwarded-For": REAL_CLIENT})
    assert get_client_ip(request) == REAL_CLIENT


def test_one_hop_mode_still_ignores_cf_connecting_ip(monkeypatch):
    _set_mode(monkeypatch, "1")
    request = _req({
        "CF-Connecting-IP": SPOOFED,
        "X-Forwarded-For": REAL_CLIENT,
    })
    assert get_client_ip(request) == REAL_CLIENT
    # CF header alone, no XFF: fall back to the socket peer, never the header.
    assert get_client_ip(_req({"CF-Connecting-IP": SPOOFED})) == SOCKET_PEER


def test_one_hop_mode_no_xff_falls_back_to_socket_peer(monkeypatch):
    _set_mode(monkeypatch, "1")
    assert get_client_ip(_req()) == SOCKET_PEER


# ── proxy mode N=2 ──


def test_two_hops_takes_second_from_right(monkeypatch):
    _set_mode(monkeypatch, "2")
    request = _req({"X-Forwarded-For": f"{SPOOFED}, {REAL_CLIENT}, 10.0.0.5"})
    assert get_client_ip(request) == REAL_CLIENT


def test_chain_shorter_than_trusted_hops_falls_back_to_socket_peer(monkeypatch):
    _set_mode(monkeypatch, "2")
    request = _req({"X-Forwarded-For": SPOOFED})
    assert get_client_ip(request) == SOCKET_PEER


# ── cloudflare mode ──


def test_cloudflare_mode_honors_cf_connecting_ip(monkeypatch):
    _set_mode(monkeypatch, "cloudflare")
    request = _req({"CF-Connecting-IP": REAL_CLIENT})
    assert get_client_ip(request) == REAL_CLIENT


def test_cloudflare_mode_ignores_x_real_ip_and_xff(monkeypatch):
    _set_mode(monkeypatch, "cloudflare")
    request = _req({"X-Real-IP": SPOOFED, "X-Forwarded-For": SPOOFED})
    assert get_client_ip(request) == SOCKET_PEER


# ── fail-closed parsing ──


def test_invalid_mode_fails_closed_to_socket_peer(monkeypatch):
    _set_mode(monkeypatch, "banana")
    request = _req({
        "CF-Connecting-IP": SPOOFED,
        "X-Real-IP": SPOOFED,
        "X-Forwarded-For": SPOOFED,
    })
    assert get_client_ip(request) == SOCKET_PEER


def test_invalid_mode_log_does_not_echo_configuration_value(monkeypatch, caplog):
    secret_like_value = "sk-sensitive-looking-config-value"
    _set_mode(monkeypatch, secret_like_value)

    assert get_client_ip(_req()) == SOCKET_PEER
    assert "Invalid TRUSTED_PROXY_MODE" in caplog.text
    assert secret_like_value not in caplog.text


def test_negative_hop_count_fails_closed(monkeypatch):
    _set_mode(monkeypatch, "-1")
    request = _req({"X-Forwarded-For": SPOOFED})
    assert get_client_ip(request) == SOCKET_PEER


# ── rate-limit key uses the same derivation ──


def test_rate_limit_key_default_ignores_spoofed_headers(monkeypatch):
    _set_mode(monkeypatch, "none")
    request = _req({
        "CF-Connecting-IP": SPOOFED,
        "X-Real-IP": SPOOFED,
        "X-Forwarded-For": f"{SPOOFED}, {SPOOFED}",
    })
    assert get_rate_limit_key(request) == f"ip:{SOCKET_PEER}"


# ── production remains fail-closed until proxy trust is explicit ──


def test_production_default_trusts_no_forwarded_headers():
    """ENV=production must not infer a proxy topology from the environment."""
    code = (
        "import os;"
        "os.environ['ENV'] = 'production';"
        "os.environ['JWT_SECRET'] = 'test-secret';"
        "os.environ['FERNET_KEY'] = 'test-fernet';"
        "os.environ['EVIDENCE_SIGNING_KEY'] = 'test-evidence-signing-key-32-bytes';"
        "os.environ['EVIDENCE_SIGNING_KEY_ID'] = 'test-v1';"
        "from app.config import Settings;"
        "print(Settings().trusted_proxy_mode)"
    )
    env = {**os.environ, "PYTHONPATH": str(_BACKEND_DIR)}
    env.pop("ENV", None)  # subprocess sets its own
    # conftest exports SANDBOX_BACKEND=inprocess for run_python tests; a
    # production-config subprocess inheriting it would trip the production
    # sandbox guard before reaching the trusted-proxy assertion under test.
    env.pop("SANDBOX_BACKEND", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=_BACKEND_DIR,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "none"


# ── audit log (comments) records the same derived IP ──


async def test_comment_audit_ip_default_ignores_spoofed_headers(
    app_client, db_session, monkeypatch
):
    """Stored client_ip must be the socket peer, not a spoofed header value."""
    _set_mode(monkeypatch, "none")
    r = await app_client.post(
        "/api/comments",
        json={"author_name": "Mallory", "content": "audit trust chain check"},
        headers={"CF-Connecting-IP": SPOOFED, "X-Forwarded-For": SPOOFED},
    )
    assert r.status_code == 201, r.text

    from app.models.schemas import Comment

    row = (
        await db_session.execute(
            select(Comment).where(Comment.id == uuid.UUID(r.json()["id"]))
        )
    ).scalar_one()
    # httpx ASGITransport presents the connection peer as 127.0.0.1
    assert row.client_ip == "127.0.0.1"
    assert row.client_ip != SPOOFED


async def test_comment_audit_ip_one_hop_mode_uses_rightmost_xff(
    app_client, db_session, monkeypatch
):
    _set_mode(monkeypatch, "1")
    r = await app_client.post(
        "/api/comments",
        json={"author_name": "Trent", "content": "audit trust chain check 2"},
        headers={
            "CF-Connecting-IP": SPOOFED,
            "X-Forwarded-For": f"{SPOOFED}, {REAL_CLIENT}",
        },
    )
    assert r.status_code == 201, r.text

    from app.models.schemas import Comment

    row = (
        await db_session.execute(
            select(Comment).where(Comment.id == uuid.UUID(r.json()["id"]))
        )
    ).scalar_one()
    assert row.client_ip == REAL_CLIENT
