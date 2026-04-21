"""T2 (PART T): WebSocket authorization — team membership + run ownership.

Previously any authenticated user could tail `/ws/pipeline/{run_id}` or
`/ws/collab/{team_id}` with only a valid JWT, regardless of whether they
owned the run or belonged to the team. This let attackers who knew or
guessed a UUID listen to other users' real-time cursor/comment/edit
broadcasts and pipeline progress.
"""

import uuid
import pytest


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
