"""Phase P: /api/admin/sandbox/health + /exec-test 诊断端点测试."""

from __future__ import annotations


async def test_sandbox_health_returns_ok_and_stderr_baseline(app_client, monkeypatch):
    """/health 用 subprocess.Popen 跑 `print("ok")` — 应该成功, stdout
    含 "ok", stderr 含 baseline 标识, exit_code=0.  这是整个 P0 诊断
    能力的主测试: 证明绕开 multiprocessing 后能抓 stderr."""
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
    """/exec-test 用真实 SubprocessBackend; 本地 CI 环境多数能跑通,
    但 production Render 上可能挂 — 重点是端点存在并返结构正确."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")

    r = await app_client.get("/api/admin/sandbox/exec-test")
    assert r.status_code == 200
    body = r.json()
    # ok 字段存在即可 — 值取决于 CI 是否能 spawn subprocess
    assert "ok" in body
    assert "note" in body
