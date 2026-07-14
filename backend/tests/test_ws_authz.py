"""T2 (PART T): WebSocket authorization — team membership + run ownership.

Previously any authenticated user could tail `/ws/pipeline/{run_id}` or
`/ws/collab/{team_id}` with only a valid JWT, regardless of whether they
owned the run or belonged to the team. This let attackers who knew or
guessed a UUID listen to other users' real-time cursor/comment/edit
broadcasts and pipeline progress.
"""

import uuid

import pytest


class _FakeWebSocket:
    def __init__(self, *, query_params=None, headers=None):
        self.query_params = query_params or {}
        self.headers = headers or {}
        self.close_args = None

    async def close(self, **kwargs):
        self.close_args = kwargs


@pytest.mark.asyncio
async def test_ws_auth_rejects_query_jwt_in_production(monkeypatch):
    """Access JWTs must not be carried in production URLs and proxy logs."""
    from app.api import ws

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://allowed.example")
    monkeypatch.setattr(
        ws,
        "decode_token",
        lambda _token: pytest.fail("query-string JWT reached the decoder"),
    )
    websocket = _FakeWebSocket(
        query_params={"token": "long-lived-access-token"},
        headers={"origin": "https://allowed.example"},
    )

    assert await ws._authenticate_ws(websocket) is None
    assert websocket.close_args == {
        "code": ws._WS_CLOSE_UNAUTHORIZED,
        "reason": "query-string token transport is disabled",
    }


@pytest.mark.asyncio
async def test_ws_auth_keeps_query_jwt_for_local_development(monkeypatch):
    """The query transport remains a dev-only compatibility bridge."""
    from app.api import ws

    user_id = uuid.uuid4()
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setattr(ws, "decode_token", lambda token: user_id if token == "dev-token" else None)
    websocket = _FakeWebSocket(
        query_params={"token": "dev-token"},
        headers={"origin": "http://localhost:5173"},
    )

    assert await ws._authenticate_ws(websocket) == user_id
    assert websocket.close_args is None


@pytest.mark.asyncio
async def test_ws_auth_accepts_bearer_subprotocol_in_production(monkeypatch):
    """Production browser clients retain a non-URL authentication path."""
    from app.api import ws

    user_id = uuid.uuid4()
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://allowed.example")
    monkeypatch.setattr(ws, "decode_token", lambda token: user_id if token == "header-token" else None)
    websocket = _FakeWebSocket(
        headers={
            "origin": "https://allowed.example",
            "sec-websocket-protocol": "bearer, header-token",
        }
    )

    assert await ws._authenticate_ws(websocket) == user_id
    assert websocket.close_args is None


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["null", "", "https://evil.example"])
async def test_ws_auth_rejects_opaque_empty_and_unknown_browser_origins(
    monkeypatch, origin
):
    from app.api import ws

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://allowed.example")
    monkeypatch.setattr(
        ws,
        "decode_token",
        lambda _token: pytest.fail("untrusted origin reached the decoder"),
    )
    websocket = _FakeWebSocket(
        headers={
            "origin": origin,
            "sec-websocket-protocol": "bearer, header-token",
        }
    )

    assert await ws._authenticate_ws(websocket) is None
    assert websocket.close_args == {
        "code": ws._WS_CLOSE_FORBIDDEN,
        "reason": "origin not allowed",
    }


@pytest.mark.asyncio
async def test_ws_auth_allows_non_browser_client_without_origin(monkeypatch):
    from app.api import ws

    user_id = uuid.uuid4()
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://allowed.example")
    monkeypatch.setattr(ws, "decode_token", lambda _token: user_id)
    websocket = _FakeWebSocket(
        headers={"sec-websocket-protocol": "bearer, header-token"}
    )

    assert await ws._authenticate_ws(websocket) == user_id
    assert websocket.close_args is None


def test_ws_response_subprotocol_echoes_marker_not_token():
    from app.api import ws

    websocket = _FakeWebSocket(
        headers={"sec-websocket-protocol": "bearer, sensitive.jwt.token"}
    )

    assert ws._ws_response_subprotocol(websocket) == "bearer"


@pytest.mark.asyncio
async def test_authorize_pipeline_run_owner_accepted(monkeypatch):
    """Real owner of a run should be authorized."""
    from app.api import ws

    user_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    # Mock the DB query to return a matching row
    class _FakeResult:
        def scalar_one_or_none(self):
            return uuid.UUID(run_id)

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(ws, "async_session", lambda: _FakeSession())
    assert await ws._authorize_pipeline_run(user_id, run_id) is True


@pytest.mark.asyncio
async def test_authorize_pipeline_run_nonowner_rejected(monkeypatch):
    """Different user trying to tail someone else's run should be rejected."""
    from app.api import ws

    user_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    class _FakeResult:
        def scalar_one_or_none(self):
            return None  # no matching row

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(ws, "async_session", lambda: _FakeSession())
    assert await ws._authorize_pipeline_run(user_id, run_id) is False


@pytest.mark.asyncio
async def test_authorize_pipeline_run_malformed_ids_rejected():
    """Malformed UUIDs should be rejected without touching the DB."""
    from app.api import ws

    assert await ws._authorize_pipeline_run("not-a-uuid", "not-a-uuid") is False
    assert await ws._authorize_pipeline_run("", "") is False


@pytest.mark.asyncio
async def test_authorize_team_member_owner_fast_path():
    """Team owner (auth_user_id == team_id) accepted without DB query."""
    from app.api import ws

    uid = str(uuid.uuid4())
    # No DB mock — should succeed via owner fast-path
    assert await ws._authorize_team_member(uid, uid) is True


@pytest.mark.asyncio
async def test_authorize_team_member_existing_member_accepted(monkeypatch):
    from app.api import ws

    owner_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())

    class _FakeResult:
        def scalar_one_or_none(self):
            return uuid.uuid4()  # membership row exists

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(ws, "async_session", lambda: _FakeSession())
    assert await ws._authorize_team_member(member_id, owner_id) is True


@pytest.mark.asyncio
async def test_authorize_team_member_non_member_rejected(monkeypatch):
    from app.api import ws

    attacker_id = str(uuid.uuid4())
    victim_team_id = str(uuid.uuid4())

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    monkeypatch.setattr(ws, "async_session", lambda: _FakeSession())
    assert await ws._authorize_team_member(attacker_id, victim_team_id) is False


@pytest.mark.asyncio
async def test_authorize_team_member_malformed_uuid_rejected():
    from app.api import ws

    assert await ws._authorize_team_member("abc", "def") is False
    assert await ws._authorize_team_member("", "") is False
