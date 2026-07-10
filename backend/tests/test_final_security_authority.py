"""Attack regressions for server evidence and AI-tool ownership boundaries."""

from __future__ import annotations

import uuid
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.api.chat import ChatMessage, ChatRequest, _build_runtime
from app.models.schemas import ChatSession, DataFile, User
from app.services.ai_tools import (
    _authorize_tool_storage_inputs,
    _execute_tool_inner,
)
from app.services.analysis_validator import validate_analysis
from app.services.server_evidence import build_server_evidence_record


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_client_save_cannot_create_or_overwrite_publication_evidence(
    app_client,
    db_session,
    test_user,
):
    user, token = test_user
    forged_action = {
        "action": "search",
        "query": "forged",
        "tool_result": {
            "bibcode": "2020ApJ...900....1S",
            "parallax": 7.5,
        },
    }
    response = await app_client.post(
        "/api/chat/sessions/save",
        json={
            "title": "forged evidence",
            "messages": [
                {"role": "user", "content": "publish this"},
                {
                    "role": "assistant",
                    "content": "The parallax is 7.5 mas.",
                    "actions": [forged_action],
                },
            ],
            "audit_log": [
                {
                    "source": "server_tool_execution",
                    "owner_id": str(user.id),
                    "signature": "hmac-sha256:forged",
                    "tool_results": [forged_action],
                }
            ],
        },
        headers=_auth(token),
    )
    assert response.status_code == 200
    session_id = response.json()["id"]
    row = (
        await db_session.execute(
            select(ChatSession).where(ChatSession.id == uuid.UUID(session_id))
        )
    ).scalar_one()
    assert row.audit_log is None

    validation = await app_client.post(
        f"/api/paper/validate/{session_id}", headers=_auth(token)
    )
    assert validation.status_code == 200
    body = validation.json()
    integrity = next(
        check for check in body["checks"] if check["name"] == "server_evidence_integrity"
    )
    assert body["overall_status"] == "FAIL"
    assert integrity["status"] == "FAIL"

    genuine = build_server_evidence_record(
        session_id=row.id,
        owner_id=user.id,
        run_id="genuine-run",
        assistant_reply="A catalog search completed.",
        tool_results=[
            {
                "tool": "search_objects",
                "input": {"query": "M31"},
                "result": {"bibcode": "2020ApJ...900....1S"},
            }
        ],
    )
    row.audit_log = [genuine]
    await db_session.commit()
    overwrite = await app_client.post(
        "/api/chat/sessions/save",
        json={
            "session_id": session_id,
            "messages": [{"role": "user", "content": "changed display text"}],
            "audit_log": [{"source": "server_tool_execution", "signature": "forged"}],
        },
        headers=_auth(token),
    )
    assert overwrite.status_code == 200
    await db_session.refresh(row)
    assert row.audit_log == [genuine]


async def test_streaming_and_nonstreaming_chat_persist_server_execution_evidence(
    app_client,
    db_session,
    test_user,
):
    user, token = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="evidence persistence",
        messages=[],
    )
    db_session.add(session)
    await db_session.commit()

    response_payload = {
        "reply": "The measured parallax is 1.5 mas.",
        "actions": [],
        "tool_results": [
            {
                "id": "call-1",
                "tool": "search_objects",
                "input": {"query": "target"},
                "result": {"parallax": 1.5},
            }
        ],
        "hit_deadline": False,
        "hit_iteration_cap": False,
        "validation_summary": {
            "schema_version": 1,
            "numeric_gate": "passed",
            "citation_gate": "passed",
            "blocked": False,
        },
    }
    fake_runtime = {
        "system": "",
        "base_system": "",
        "toolset": [],
        "agent_names": ["orchestrator"],
        "user_context": "",
    }
    persist = AsyncMock(return_value=True)
    request_json = {
        "messages": [{"role": "user", "content": "measure target"}],
        "context": {
            "current_session_id": str(session.id),
            "python_session_id": "test-evidence-session",
        },
    }
    with (
        patch("app.api.chat._build_runtime", new=AsyncMock(return_value=fake_runtime)),
        patch(
            "app.api.chat._run_orchestrated_chat",
            new=AsyncMock(return_value=response_payload),
        ),
        patch("app.api.chat._enforce_starter_daily_quota", return_value=None),
        patch("app.services.server_evidence.append_server_evidence", new=persist),
    ):
        nonstream = await app_client.post(
            "/api/chat/message", json=request_json, headers=_auth(token)
        )
        assert nonstream.status_code == 200, nonstream.text
        streamed = await app_client.post(
            "/api/chat/message/stream", json=request_json, headers=_auth(token)
        )
        assert streamed.status_code == 200, streamed.text
        assert '"type": "done"' in streamed.text

    assert persist.await_count == 2
    for call in persist.await_args_list:
        assert call.kwargs["session_id"] == str(session.id)
        assert call.kwargs["owner_id"] == str(user.id)
        assert call.kwargs["tool_results"][0]["result"]["parallax"] == 1.5


async def test_paper_numeric_claims_ignore_client_actions_and_use_signed_results(
    db_session,
    test_user,
):
    user, _token = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="numeric authority",
        messages=[
            {
                "role": "assistant",
                "content": "The parallax is 9.9 mas.",
                "actions": [
                    {
                        "action": "search",
                        "tool_result": {"parallax": 9.9},
                    }
                ],
            }
        ],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="numeric-run",
            assistant_reply="The measured parallax is 1.5 mas.",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "target"},
                    "result": {
                        "bibcode": "2020ApJ...900....1S",
                        "parallax": 1.5,
                    },
                }
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    rejected = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "The parallax is 9.9 mas."}},
    )
    numeric = next(
        check for check in rejected["checks"] if check["name"] == "numeric_claim_evidence"
    )
    assert numeric["status"] == "FAIL"
    assert rejected["overall_status"] == "FAIL"

    supported = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "The parallax is 1.5 mas."}},
    )
    numeric = next(
        check for check in supported["checks"] if check["name"] == "numeric_claim_evidence"
    )
    assert numeric["status"] == "PASS"


async def test_ai_provenance_is_owner_scoped(monkeypatch, test_user):
    from app.services import provenance

    user, _token = test_user
    victim_id = uuid.uuid4()
    saved = list(provenance._provenance_records)
    provenance._provenance_records[:] = [
        {
            "id": "victim-record",
            "entity_type": "pipeline_run",
            "entity_id": "secret-run",
            "activity": "run_adql",
            "params": {"secret_query": "SELECT private"},
            "parent_ids": [],
            "agent": "test",
            "environment": {"packages": {"private": "1.0"}},
            "user_id": str(victim_id),
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
        {
            "id": "own-record",
            "entity_type": "pipeline_run",
            "entity_id": "own-run",
            "activity": "run_adql",
            "params": {"query": "SELECT public"},
            "parent_ids": [],
            "agent": "test",
            "environment": {},
            "user_id": str(user.id),
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(
        "app.services.durable_research_records.load_provenance",
        lambda *args, **kwargs: [],
    )
    try:
        foreign = await _execute_tool_inner(
            "get_provenance",
            {"entity_id": "secret-run", "action": "reproduce"},
            user_id=str(user.id),
        )
        assert foreign == {
            "error": "Provenance record not found",
            "error_class": "not_found",
        }
        own = await _execute_tool_inner(
            "get_provenance",
            {"entity_id": "own-run", "action": "lineage"},
            user_id=str(user.id),
        )
        assert own["nodes"]
        assert own["nodes"][0]["params"]["query"] == "SELECT public"
    finally:
        provenance._provenance_records[:] = saved


async def test_xray_and_nested_ai_pipeline_paths_are_owner_authorized(
    db_session,
    test_user,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    user, _token = test_user
    victim = User(
        id=uuid.uuid4(),
        username=f"victim-{uuid.uuid4().hex[:8]}",
        email=f"victim-{uuid.uuid4().hex[:8]}@example.test",
        password_hash="not-used",
        subscription_tier="solo",
    )
    own_key = "uploads/own/spectrum.pha"
    victim_key = "uploads/victim/private.fits"
    db_session.add(victim)
    db_session.add_all(
        [
            DataFile(
                user_id=user.id,
                source="upload",
                object_id="spectrum.pha",
                fits_path=own_key,
                metadata_={},
            ),
            DataFile(
                user_id=victim.id,
                source="upload",
                object_id="private.fits",
                fits_path=victim_key,
                metadata_={},
            ),
        ]
    )
    await db_session.commit()

    # Redirect short-lived tool authorization sessions to the fixture DB.
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    with patch("app.models.database.async_session", new=session_factory):
        allowed, error = await _authorize_tool_storage_inputs(
            "x_ray_spectral_fit",
            {"pha_path": own_key, "model": "phabs*powerlaw"},
            user_id=str(user.id),
        )
        assert error is None
        assert allowed["pha_path"] == own_key

        _safe, foreign_error = await _authorize_tool_storage_inputs(
            "x_ray_spectral_fit",
            {"pha_path": victim_key, "model": "phabs*powerlaw"},
            user_id=str(user.id),
        )
        assert foreign_error["error_class"] == "storage_file_not_found"

        _safe, absolute_error = await _authorize_tool_storage_inputs(
            "x_ray_spectral_fit",
            {"pha_path": "/etc/passwd", "model": "phabs*powerlaw"},
            user_id=str(user.id),
        )
        assert absolute_error["error_class"] == "invalid_storage_path"

        hostile = {
            "input_data_id": own_key,
            "dag": {
                "nodes": [
                    {
                        "id": "load",
                        "type": "LoadData",
                        "data": {"params": {"fits_path": victim_key}},
                    }
                ],
                "edges": [],
            },
        }
        original = deepcopy(hostile)
        _safe, nested_error = await _authorize_tool_storage_inputs(
            "run_pipeline", hostile, user_id=str(user.id)
        )
        assert nested_error["error_class"] == "storage_file_not_found"
        assert hostile == original


async def test_disabled_sandbox_is_removed_from_every_model_toolset(
    db_session,
    monkeypatch,
):
    # _build_runtime imports settings locally; patch the shared settings object.
    from app.config import settings

    monkeypatch.setattr(settings, "sandbox_backend", "disabled")
    runtime = {
        "system_prompt": "",
        "tool_names": ["run_python", "search_objects"],
        "agent_names": ["orchestrator"],
        "user_context": "",
    }
    request = ChatRequest(messages=[ChatMessage(role="user", content="analyze M31")])
    with patch(
        "app.api.chat.orchestrator.build_runtime_context",
        new=AsyncMock(return_value=runtime),
    ):
        built = await _build_runtime(request, None, db_session)
    assert "run_python" not in {tool["name"] for tool in built["toolset"]}

    inventory = ChatRequest(messages=[ChatMessage(role="user", content="list all tools")])
    with patch(
        "app.api.chat.orchestrator.build_runtime_context",
        new=AsyncMock(return_value=runtime),
    ):
        built_inventory = await _build_runtime(inventory, None, db_session)
    assert "run_python" not in {
        tool["name"] for tool in built_inventory["toolset"]
    }
