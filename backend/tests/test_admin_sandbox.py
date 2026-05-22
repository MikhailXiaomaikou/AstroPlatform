"""Phase P: /api/admin/sandbox/health + /exec-test diagnostic endpoint tests."""

from __future__ import annotations


async def test_sandbox_health_returns_ok_and_stderr_baseline(app_client, monkeypatch):
    """/health runs `print("ok")` via subprocess.Popen — should succeed, stdout
    contains "ok", stderr contains the baseline marker, exit_code=0.  This is the
    primary P0 diagnostic test: proves that stderr can be captured after bypassing
    multiprocessing."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")

    r = await app_client.get("/api/admin/sandbox/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["exit_code"] == 0
    assert "ok" in body["stdout"]
    assert "stderr-baseline" in body["stderr"]
    assert "python_executable" in body
    assert "python_version" in body


async def test_sandbox_health_requires_admin(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-xyz")

    r = await app_client.get("/api/admin/sandbox/health")
    assert r.status_code == 403

    r = await app_client.get(
        "/api/admin/sandbox/health",
        headers={"X-Admin-Secret": "test-xyz"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_sandbox_exec_test_available(app_client, monkeypatch):
    """/exec-test uses the real SubprocessBackend; most local CI environments pass,
    but it may hang on production Render — the key point is that the endpoint exists
    and returns the correct structure."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")

    r = await app_client.get("/api/admin/sandbox/exec-test")
    assert r.status_code == 200
    body = r.json()
    # the ok field just needs to be present — its value depends on whether CI can spawn a subprocess
    assert "ok" in body
    assert "note" in body
