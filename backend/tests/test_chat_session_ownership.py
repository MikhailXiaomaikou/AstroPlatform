"""Account-bound chat session and streaming owner propagation tests."""

from __future__ import annotations

import uuid

import pytest

from app.auth import hash_password
from app.models.schemas import ChatSession, User
from app.utils.usernames import username_from_email


async def _add_user(db_session, email: str) -> User:
    user = User(
        id=uuid.uuid4(),
        username=username_from_email(email),
        email=email,
        password_hash=hash_password("securepassword123"),
        subscription_tier="solo",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_stream_passes_authenticated_owner_and_owned_session(
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    user, token = test_user
    session = ChatSession(user_id=user.id, title="owned", messages=[])
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    run_calls: list[dict] = []
    status_calls: list[dict] = []

    async def fake_build_runtime(req, current_user, db):
        assert current_user.id == user.id
        return {"agent_names": ["orchestrator"], "toolset": [], "system": "test"}

    async def fake_run_orchestrated_chat(**kwargs):
        run_calls.append(kwargs)
        return {"reply": "ok", "actions": []}

    async def fake_update(session_id, **kwargs):
        status_calls.append({"session_id": session_id, **kwargs})

    monkeypatch.setattr("app.api.chat._build_runtime", fake_build_runtime)
    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", fake_run_orchestrated_chat)
    monkeypatch.setattr("app.api.chat._update_chat_session_status", fake_update)

    response = await app_client.post(
        "/api/chat/message/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "api_provider": "local",
                "model_profile": "local:openai-cli",
                "python_session_id": "python-sandbox-id",
                "current_session_id": str(session.id),
            },
        },
    )

    assert response.status_code == 200
    assert run_calls[0]["user_id"] == str(user.id)
    assert run_calls[0]["chat_session_id"] == str(session.id)
    assert [call["status"] for call in status_calls] == ["running", "idle"]
    assert all(call["session_id"] == str(session.id) for call in status_calls)
    assert all(call["owner_id"] == str(user.id) for call in status_calls)


@pytest.mark.parametrize("endpoint", ["/api/chat/message", "/api/chat/message/stream"])
async def test_chat_rejects_another_accounts_current_session_at_entry(
    endpoint,
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    _owner, token = test_user
    other = await _add_user(db_session, f"other-{uuid.uuid4().hex}@example.com")
    other_session = ChatSession(user_id=other.id, title="private", messages=[])
    db_session.add(other_session)
    await db_session.commit()
    await db_session.refresh(other_session)

    async def must_not_run(**kwargs):
        raise AssertionError("orchestrator must not receive an unauthorized session")

    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", must_not_run)
    response = await app_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "api_provider": "local",
                "model_profile": "local:openai-cli",
                "current_session_id": str(other_session.id),
            },
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat session not found"


async def test_anonymous_stream_with_saved_session_fails_closed(
    app_client,
    monkeypatch,
):
    async def must_not_run(**kwargs):
        raise AssertionError("anonymous saved-session request reached orchestrator")

    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", must_not_run)
    response = await app_client.post(
        "/api/chat/message/stream",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "api_provider": "local",
                "model_profile": "local:openai-cli",
                "current_session_id": str(uuid.uuid4()),
            },
        },
    )

    assert response.status_code == 401
    assert "Authentication is required" in response.json()["detail"]


async def test_separate_anonymous_streams_cannot_dedup_or_poll_each_other(
    app_client,
    monkeypatch,
):
    from app.services import async_tool_runtime as runtime

    first_job_id: str | None = None
    observed_job_ids: list[str] = []

    async def fake_build_runtime(req, user, db):
        assert user is None
        return {"agent_names": ["orchestrator"], "toolset": [], "system": "test"}

    async def fake_run_orchestrated_chat(**kwargs):
        nonlocal first_job_id
        assert kwargs["user_id"] is None
        if first_job_id is not None:
            # owner_id=None is safe here because the stream-installed context
            # supplies this request's anonymous owner scope.
            assert runtime.get_async_job(first_job_id, owner_id=None) is None
        banner = runtime.submit_async_job(
            "transit_search_bls",
            {"target": "TOI-700"},
        )
        assert runtime.get_async_job(banner["job_id"], owner_id=None) is not None
        observed_job_ids.append(banner["job_id"])
        if first_job_id is None:
            first_job_id = banner["job_id"]
        return {"reply": "ok", "actions": []}

    monkeypatch.setattr("app.api.chat._build_runtime", fake_build_runtime)
    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", fake_run_orchestrated_chat)

    payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "context": {
            "api_provider": "local",
            "model_profile": "local:openai-cli",
            "current_session_id": None,
        },
    }
    first = await app_client.post("/api/chat/message/stream", json=payload)
    second = await app_client.post("/api/chat/message/stream", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(observed_job_ids) == 2
    assert observed_job_ids[0] != observed_job_ids[1]


async def test_session_state_service_requires_matching_owner(
    db_session,
    test_user,
    monkeypatch,
):
    from app.services import agent_session_state
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    owner, _token = test_user
    other = await _add_user(db_session, f"state-other-{uuid.uuid4().hex}@example.com")
    session = ChatSession(user_id=owner.id, title="owned", messages=[])
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    state_session_factory = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(
        agent_session_state,
        "AsyncSessionLocal",
        state_session_factory,
    )

    await agent_session_state.update_session_status(
        session.id,
        owner_id=other.id,
        status="running",
        current_run_id="forbidden",
    )
    assert await agent_session_state.get_session_status(
        session.id,
        owner_id=other.id,
    ) == {}

    await db_session.refresh(session)
    assert session.agent_status is None
    assert session.current_run_id is None

    await agent_session_state.update_session_status(
        session.id,
        owner_id=owner.id,
        status="running",
        current_run_id="allowed",
    )
    status = await agent_session_state.get_session_status(
        session.id,
        owner_id=owner.id,
    )
    assert status == {"agent_status": "running", "current_run_id": "allowed"}
