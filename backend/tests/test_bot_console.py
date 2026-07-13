from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import bot_console
from app.services import local_bot_console as console


def _auth_headers(token: str, *, origin: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def _prepare_research_root(
    root: Path,
    *,
    state: dict | None = None,
    report_name: str | None = None,
    report_text: str = "# Weekly result\nEvidence.",
) -> tuple[str, Path | None]:
    week_id = console.current_week_id()
    weeks = root / ".pipeline_state" / "weeks"
    reports = root / "reports"
    weeks.mkdir(parents=True)
    reports.mkdir(parents=True)
    report: Path | None = None
    if report_name is not None:
        report = reports / report_name
        report.write_text(report_text, encoding="utf-8")
    if state is not None:
        (weeks / f"{week_id}.json").write_text(
            json.dumps(state),
            encoding="utf-8",
        )
    return week_id, report


TEST_MODEL_ID = "test-console-model"
TEST_RESEARCH_LABEL = "com.example.astro-research-weekly"
TEST_BOT_LABEL = "com.example.astro-notify-bot"


@pytest.fixture(autouse=True)
def local_console_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("BOT_CONSOLE_ENABLED", "1")
    monkeypatch.setenv("OPENAI_CLI_ENABLED", "1")
    monkeypatch.setenv("BOT_CONSOLE_MODEL_ID", TEST_MODEL_ID)
    monkeypatch.delenv("BOT_CONSOLE_MODEL_PROFILE", raising=False)
    monkeypatch.setenv("RESEARCH_LAUNCHD_LABEL", TEST_RESEARCH_LABEL)
    monkeypatch.setenv("BOT_LAUNCHD_LABEL", TEST_BOT_LABEL)
    # Self-hosters may run this console on non-macOS; that path is covered by
    # its own tests below. Every other test exercises the launchd-supported
    # path regardless of the OS actually running the suite.
    monkeypatch.setattr(console, "_launchd_supported", lambda: True)
    monkeypatch.delenv("LOCAL_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("LOCAL_CONTROL_ORIGINS", raising=False)


@pytest.mark.asyncio
async def test_all_console_endpoints_require_authentication(app_client) -> None:
    responses = [
        await app_client.post(
            "/api/automation/bot/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        ),
        await app_client.get("/api/automation/research/status"),
        await app_client.get("/api/automation/research/report"),
        await app_client.post("/api/automation/research/trigger"),
    ]
    assert [response.status_code for response in responses] == [401, 401, 401, 401]


@pytest.mark.asyncio
async def test_hosted_origin_cannot_reach_local_console(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, token = test_user

    async def fail_if_called(_messages):
        raise AssertionError("origin guard must run before SOL")

    monkeypatch.setattr(console, "chat_with_sol", fail_if_called)
    response = await app_client.post(
        "/api/automation/bot/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers=_auth_headers(token, origin="https://astroplatform.pages.dev"),
    )

    assert response.status_code == 403
    assert "telegram" not in response.text.lower()


@pytest.mark.asyncio
async def test_local_origin_can_reach_console(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, token = test_user

    async def fake_chat(_messages):
        return "local answer"

    monkeypatch.setattr(console, "chat_with_sol", fake_chat)
    response = await app_client.post(
        "/api/automation/bot/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers=_auth_headers(token, origin="http://127.0.0.1:5173"),
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "local answer", "model": TEST_MODEL_ID}


@pytest.mark.asyncio
async def test_non_loopback_peer_is_rejected() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api/automation/research/status",
            "raw_path": b"/api/automation/research/status",
            "query_string": b"",
            "headers": [],
            "client": ("192.0.2.25", 12345),
            "server": ("127.0.0.1", 8000),
        }
    )
    with pytest.raises(HTTPException) as raised:
        await bot_console.require_local_operator(
            request,
            user=SimpleNamespace(),  # type: ignore[arg-type]
        )
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_console_is_hidden_in_production(app_client, test_user, monkeypatch) -> None:
    _, token = test_user
    monkeypatch.setenv("ENV", "production")

    response = await app_client.get(
        "/api/automation/research/status",
        headers=_auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_production_probe_returns_404_not_401(
    app_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unauthenticated probe must not learn this route exists in production
    by seeing 401 (route exists, you're just not logged in) instead of 404."""

    monkeypatch.setenv("ENV", "production")

    response = await app_client.get("/api/automation/research/status")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_chat_validation_rejects_non_user_final_turn(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, token = test_user

    async def fail_if_called(_messages):
        raise AssertionError("invalid chat must not reach SOL")

    monkeypatch.setattr(console, "chat_with_sol", fail_if_called)
    response = await app_client.post(
        "/api/automation/bot/chat",
        json={"messages": [{"role": "assistant", "content": "hello"}]},
        headers=_auth_headers(token),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sol_service_forces_model_and_disables_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    monkeypatch.setenv("OPENAI_CLI_MODEL", "not-the-requested-model")

    async def fake_complete(self, messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {"content": "SOL answer", "tool_calls": []}

    monkeypatch.setattr(console.LocalBackend, "complete", fake_complete)
    answer = await console.chat_with_sol([{"role": "user", "content": "question"}])

    assert answer == "SOL answer"
    assert captured["tools"] is None
    assert captured["model_profile"].id == "local:openai-cli"
    assert captured["model_profile"].resolved_model_id == TEST_MODEL_ID
    normalized_system = " ".join(captured["system"].lower().split())
    assert "do not claim to have called a tool" in normalized_system


@pytest.mark.asyncio
async def test_sol_service_never_falls_through_to_generic_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CLI_ENABLED", "0")
    monkeypatch.setenv("LOCAL_MODEL_ENABLED", "1")

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("generic local backend must not be called")

    monkeypatch.setattr(console.LocalBackend, "complete", fail_if_called)
    with pytest.raises(console.SolUnavailable):
        await console.chat_with_sol([{"role": "user", "content": "question"}])


@pytest.mark.asyncio
async def test_sol_service_rejects_non_cli_profile_before_backend_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_CONSOLE_MODEL_PROFILE", "local:default")
    monkeypatch.setenv("LOCAL_MODEL_ENABLED", "1")

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("generic local backend must not be called")

    monkeypatch.setattr(console.LocalBackend, "complete", fail_if_called)
    with pytest.raises(console.SolUnavailable):
        await console.chat_with_sol([{"role": "user", "content": "question"}])


@pytest.mark.asyncio
async def test_sol_failure_response_is_redacted(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, token = test_user
    private_detail = "telegram-secret /Users/private/codex"

    async def fail(_messages):
        raise console.SolUnavailable(private_detail)

    monkeypatch.setattr(console, "chat_with_sol", fail)
    response = await app_client.post(
        "/api/automation/bot/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers=_auth_headers(token),
    )

    assert response.status_code == 503
    assert private_detail not in response.text
    assert "/Users/" not in response.text


@pytest.mark.asyncio
async def test_status_returns_structured_safe_fields_only(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    week_id = console.current_week_id()
    report_name = "2026-07-10.md"
    state = {
        "week_id": week_id,
        "status": "completed",
        "report": str(tmp_path / "reports" / report_name),
        "budget": {"llm_calls_used": 3, "llm_calls_limit": 10},
        "execution_quality": {"executed_success": 2, "needs_review": 1},
        "notification": {"status": "sent"},
        "error": "must-not-leak /Users/private/secret",
        "code_snapshot": {"private": "must-not-leak"},
    }
    _prepare_research_root(
        tmp_path,
        state=state,
        report_name=report_name,
    )
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))
    monkeypatch.setattr(console, "_run_bot_status_check", lambda: True)

    response = await app_client.get(
        "/api/automation/research/status",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "week_id": week_id,
        "status": "completed",
        "status_label": "Completed",
        "bot_online": True,
        "model": TEST_MODEL_ID,
        "schedule_primary": None,
        "schedule_recovery": None,
        "llm_calls_used": 3,
        "llm_calls_limit": 10,
        "executed_success": 2,
        "needs_review": 1,
        "report_available": True,
        "report_name": report_name,
        "notification_status": "sent",
        "can_trigger": False,
    }
    assert str(tmp_path) not in response.text
    assert "must-not-leak" not in response.text


@pytest.mark.asyncio
async def test_unknown_state_text_is_replaced_instead_of_leaked(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    private_detail = "secret /Users/private/state"
    _prepare_research_root(
        tmp_path,
        state={
            "status": private_detail,
            "notification": {"status": private_detail},
        },
    )
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))
    monkeypatch.setattr(console, "_run_bot_status_check", lambda: False)

    response = await app_client.get(
        "/api/automation/research/status",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["notification_status"] == "unknown"
    assert response.json()["bot_online"] is False
    assert private_detail not in response.text
    assert "/Users/" not in response.text


def test_bot_online_check_uses_only_fixed_launchctl_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = list(arguments)
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(arguments, 0, stdout="state = running\n")

    monkeypatch.setattr(console.subprocess, "run", fake_run)

    assert console._run_bot_status_check() is True
    domain = f"gui/{os.getuid()}/{TEST_BOT_LABEL}"
    assert captured["arguments"] == ["/bin/launchctl", "print", domain]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["stderr"] is subprocess.DEVNULL


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_kind", ["traversal", "symlink"])
async def test_report_rejects_paths_outside_reports_and_symlinks(
    unsafe_kind: str,
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    root = tmp_path / "cosmo"
    outside = tmp_path / "2026-07-10.md"
    outside.write_text("PRIVATE REPORT", encoding="utf-8")
    week_id, _ = _prepare_research_root(root)
    if unsafe_kind == "traversal":
        unsafe = root / "reports" / ".." / ".." / outside.name
    else:
        unsafe = root / "reports" / outside.name
        unsafe.symlink_to(outside)
    state_path = root / ".pipeline_state" / "weeks" / f"{week_id}.json"
    state_path.write_text(
        json.dumps({"status": "completed", "report": str(unsafe)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(root))

    response = await app_client.get(
        "/api/automation/research/report",
        headers=_auth_headers(token),
    )

    assert response.status_code == 404
    assert "PRIVATE REPORT" not in response.text
    assert str(tmp_path) not in response.text


@pytest.mark.asyncio
async def test_report_rejects_symlinked_reports_directory(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    root = tmp_path / "cosmo"
    weeks = root / ".pipeline_state" / "weeks"
    weeks.mkdir(parents=True)
    outside_reports = tmp_path / "private-reports"
    outside_reports.mkdir()
    report_name = "2026-07-10.md"
    (outside_reports / report_name).write_text("PRIVATE REPORT", encoding="utf-8")
    (root / "reports").symlink_to(outside_reports, target_is_directory=True)
    week_id = console.current_week_id()
    (weeks / f"{week_id}.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "report": str(root / "reports" / report_name),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(root))

    response = await app_client.get(
        "/api/automation/research/report",
        headers=_auth_headers(token),
    )

    assert response.status_code == 404
    assert "PRIVATE REPORT" not in response.text
    assert str(tmp_path) not in response.text


@pytest.mark.asyncio
async def test_report_returns_markdown_without_absolute_path(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    report_name = "2026-07-10.md"
    state = {
        "status": "completed",
        "report": str(tmp_path / "reports" / report_name),
    }
    _prepare_research_root(
        tmp_path,
        state=state,
        report_name=report_name,
        report_text="# Result\nSafe evidence.",
    )
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))

    response = await app_client.get(
        "/api/automation/research/report",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_name"] == report_name
    assert payload["markdown"] == "# Result\nSafe evidence."
    assert payload["truncated"] is False
    assert payload["updated_at"].endswith("+00:00")
    assert str(tmp_path) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("research_status", ["running", "completed"])
async def test_trigger_is_idempotent_for_running_and_completed(
    research_status: str,
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    notification = {"status": "sent"} if research_status == "completed" else None
    state = {"status": research_status, "notification": notification}
    _prepare_research_root(tmp_path, state=state)
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))

    def fail_if_called(_arguments):
        raise AssertionError("idempotent trigger must not call launchctl")

    monkeypatch.setattr(console, "_run_launchctl", fail_if_called)
    response = await app_client.post(
        "/api/automation/research/trigger",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["submitted"] is False
    assert response.json()["status"] == research_status


@pytest.mark.asyncio
async def test_trigger_uses_only_fixed_launchctl_argv_without_shell(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    _prepare_research_root(tmp_path, state={"status": "failed"})
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))
    calls: list[tuple[list[str], dict]] = []

    def fake_run(arguments, **kwargs):
        calls.append((list(arguments), dict(kwargs)))
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    response = await app_client.post(
        "/api/automation/research/trigger",
        headers=_auth_headers(token),
    )

    domain = f"gui/{os.getuid()}/{TEST_RESEARCH_LABEL}"
    assert response.status_code == 200
    assert response.json()["submitted"] is True
    assert [call[0] for call in calls] == [
        ["/bin/launchctl", "print", domain],
        ["/bin/launchctl", "kickstart", domain],
    ]
    for arguments, kwargs in calls:
        assert "-k" not in arguments
        assert "--force" not in arguments
        assert kwargs["shell"] is False
        assert kwargs["check"] is False
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL


@pytest.mark.asyncio
async def test_unloaded_launchd_job_returns_redacted_503(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    _prepare_research_root(tmp_path, state={"status": "failed"})
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))

    def not_loaded(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 113)

    monkeypatch.setattr(console.subprocess, "run", not_loaded)
    response = await app_client.post(
        "/api/automation/research/trigger",
        headers=_auth_headers(token),
    )

    assert response.status_code == 503
    assert str(tmp_path) not in response.text
    assert TEST_RESEARCH_LABEL not in response.text


@pytest.mark.asyncio
async def test_status_returns_503_when_research_root_is_not_configured(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hoster who has not set COSMO_SECOND_ORDER_ROOT gets a clear
    unavailable response, never a path guessed from someone else's machine."""

    _, token = test_user
    monkeypatch.delenv("COSMO_SECOND_ORDER_ROOT", raising=False)

    response = await app_client.get(
        "/api/automation/research/status",
        headers=_auth_headers(token),
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_status_reflects_configured_schedule_strings(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    _prepare_research_root(tmp_path, state={"status": "pending"})
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))
    monkeypatch.setenv("RESEARCH_SCHEDULE_PRIMARY", "Friday 18:30 UTC")
    monkeypatch.setenv("RESEARCH_SCHEDULE_RECOVERY", "Saturday 10:30 UTC")

    response = await app_client.get(
        "/api/automation/research/status",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schedule_primary"] == "Friday 18:30 UTC"
    assert payload["schedule_recovery"] == "Saturday 10:30 UTC"


def test_bot_online_check_returns_false_on_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console, "_launchd_supported", lambda: False)

    def fail_if_called(arguments, **_kwargs):
        raise AssertionError("launchctl must not run on an unsupported platform")

    monkeypatch.setattr(console.subprocess, "run", fail_if_called)
    assert console._run_bot_status_check() is False


@pytest.mark.asyncio
async def test_trigger_is_unavailable_on_unsupported_platform(
    app_client,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, token = test_user
    _prepare_research_root(tmp_path, state={"status": "failed"})
    monkeypatch.setenv("COSMO_SECOND_ORDER_ROOT", str(tmp_path))
    monkeypatch.setattr(console, "_launchd_supported", lambda: False)

    def fail_if_called(arguments, **_kwargs):
        raise AssertionError("launchctl must not run on an unsupported platform")

    monkeypatch.setattr(console.subprocess, "run", fail_if_called)
    response = await app_client.post(
        "/api/automation/research/trigger",
        headers=_auth_headers(token),
    )

    assert response.status_code == 503
