"""Adversarial isolation tests for AI-tool and run_python caches."""

from __future__ import annotations

import asyncio
import uuid

import pytest


@pytest.fixture(autouse=True)
def _clear_result_cache():
    from app.services import ai_tools

    ai_tools._search_result_cache.clear()
    ai_tools._search_result_cache_owners.clear()
    yield
    ai_tools._search_result_cache.clear()
    ai_tools._search_result_cache_owners.clear()


def test_trusted_scope_separates_two_users_reusing_same_client_session_id():
    from app.services import ai_tools

    client_session_id = "client-controlled-shared-id"
    owner_a = ai_tools.build_trusted_python_session_id(
        user_id="user-a",
        chat_session_id="chat-a",
        requested_session_id=client_session_id,
    )
    owner_b = ai_tools.build_trusted_python_session_id(
        user_id="user-b",
        chat_session_id="chat-b",
        requested_session_id=client_session_id,
    )
    owner_a_other_chat = ai_tools.build_trusted_python_session_id(
        user_id="user-a",
        chat_session_id="chat-a-2",
        requested_session_id=client_session_id,
    )

    assert len({owner_a, owner_b, owner_a_other_chat}) == 3
    ai_tools.store_session_results("latest", owner_a, [{"secret": "A"}])
    ai_tools.store_session_results("latest", owner_b, [{"secret": "B"}])
    assert ai_tools.get_session_cached_results("latest", owner_a) == [
        {"secret": "A"}
    ]
    assert ai_tools.get_session_cached_results("latest", owner_b) == [
        {"secret": "B"}
    ]


@pytest.mark.asyncio
async def test_execute_tool_applies_trusted_scope_before_cache_producer(
    monkeypatch,
):
    from app.services import account_deletion, ai_tools

    runtime_sessions: list[str] = []

    monkeypatch.setattr(account_deletion, "account_runtime_is_active", lambda _uid: True)
    monkeypatch.setattr(account_deletion, "register_result_artifacts", lambda **_kw: [])
    monkeypatch.setattr(account_deletion, "dispose_deleted_account_result", lambda **_kw: None)

    async def fake_inner(
        _tool_name,
        _tool_input,
        _api_key,
        _provider_api_keys,
        runtime_session_id,
        _user_id,
        _chat_session_id,
        _progress_callback,
    ):
        runtime_sessions.append(runtime_session_id)
        ai_tools.store_session_results(
            "latest", runtime_session_id, [{"runtime": runtime_session_id}]
        )
        return {"success": True}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    for user_id in (str(uuid.uuid4()), str(uuid.uuid4())):
        await ai_tools.execute_tool(
            "search_objects",
            {"query": "M31"},
            python_session_id="same-client-session",
            user_id=user_id,
            chat_session_id="same-client-chat-id",
        )

    assert len(set(runtime_sessions)) == 2
    assert all(session.startswith("trusted-v2-") for session in runtime_sessions)
    assert ai_tools.get_session_cached_results("latest", runtime_sessions[0]) != (
        ai_tools.get_session_cached_results("latest", runtime_sessions[1])
    )


@pytest.mark.asyncio
async def test_late_tool_cache_is_erased_when_account_deletes_mid_execution(
    monkeypatch,
):
    from app.services import account_deletion, ai_tools, code_executor

    user_id = str(uuid.uuid4())
    active_checks = iter((True, True, False))
    runtime_sessions: list[str] = []
    monkeypatch.setattr(
        account_deletion,
        "account_runtime_is_active",
        lambda _uid: next(active_checks),
    )
    monkeypatch.setattr(account_deletion, "register_result_artifacts", lambda **_kw: [])
    monkeypatch.setattr(account_deletion, "dispose_deleted_account_result", lambda **_kw: None)
    monkeypatch.setattr(code_executor, "register_user_session", lambda *_args: None)

    async def fake_inner(
        _tool_name,
        _tool_input,
        _api_key,
        _provider_api_keys,
        runtime_session_id,
        _user_id,
        _chat_session_id,
        _progress_callback,
    ):
        runtime_sessions.append(runtime_session_id)
        ai_tools.store_session_results(
            "latest", runtime_session_id, [{"secret": "late"}]
        )
        return {"success": True}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    result = await ai_tools.execute_tool(
        "search_objects",
        {"query": "M31"},
        python_session_id="race-session",
        user_id=user_id,
        chat_session_id=str(uuid.uuid4()),
    )

    assert result["error_class"] == "account_deletion_requested"
    assert ai_tools.get_session_cached_results(
        "latest", runtime_sessions[0]
    ) is None


@pytest.mark.asyncio
async def test_tool_ledger_commit_error_keeps_cleanup_discovery_without_eager_delete(
    monkeypatch,
):
    from app.services import account_deletion, ai_tools, code_executor

    user_id = str(uuid.uuid4())
    staged: list[str] = []
    disposed: list[str] = []
    monkeypatch.setattr(account_deletion, "account_runtime_is_active", lambda _uid: True)
    monkeypatch.setattr(code_executor, "register_user_session", lambda *_args: None)
    monkeypatch.setattr(
        account_deletion,
        "stage_result_artifacts_for_registration",
        lambda **_kw: staged.append("staged"),
    )
    monkeypatch.setattr(
        account_deletion,
        "register_result_artifacts",
        lambda **_kw: (_ for _ in ()).throw(OSError("commit acknowledgement lost")),
    )
    monkeypatch.setattr(
        account_deletion,
        "dispose_deleted_account_result",
        lambda **_kw: disposed.append("deleted"),
    )

    async def fake_inner(*_args, **_kwargs):
        return {"success": True, "output_path": "processed/uncertain.fits"}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    with pytest.raises(OSError, match="acknowledgement lost"):
        await ai_tools.execute_tool(
            "search_objects",
            {"query": "M31"},
            user_id=user_id,
            chat_session_id=str(uuid.uuid4()),
        )

    assert staged == ["staged"]
    assert disposed == []


def test_anonymous_requests_are_request_scoped_and_legacy_default_is_separate():
    from app.services import ai_tools

    anon_a = ai_tools.build_trusted_python_session_id(
        user_id=None,
        chat_session_id=None,
        requested_session_id="same-client-id",
        anonymous_scope="server-request-a",
    )
    anon_b = ai_tools.build_trusted_python_session_id(
        user_id=None,
        chat_session_id=None,
        requested_session_id="same-client-id",
        anonymous_scope="server-request-b",
    )
    assert anon_a != anon_b
    assert ai_tools.build_trusted_python_session_id(
        user_id=None,
        chat_session_id=None,
        requested_session_id="default",
    ) == "default"

    ai_tools.store_search_results("latest", [{"owner": "legacy-default"}])
    ai_tools.store_session_results("latest", anon_a, [{"owner": "anonymous-a"}])
    assert ai_tools.get_session_cached_results("latest", "default") == [
        {"owner": "legacy-default"}
    ]
    assert ai_tools.get_session_cached_results("latest", anon_b) is None


def test_run_python_accessors_and_subprocess_snapshot_only_see_exact_owner():
    from app.services import ai_tools
    from app.services.code_executor import (
        _collect_subprocess_cache_context,
        _make_data_accessor,
    )

    owner_a = "trusted-v2-owner-a"
    owner_b = "trusted-v2-owner-b"
    ai_tools.store_search_results("latest", [{"owner": "default"}])
    for owner, label in ((owner_a, "A"), (owner_b, "B")):
        ai_tools.store_session_results("latest", owner, [{"owner": label}])
        ai_tools.store_session_results(
            "latest_literature_tables",
            owner,
            {"line_measurements": [{"source_name": label}]},
        )
        ai_tools.store_session_results(
            "latest_crossmatch", owner, [{"match": label}]
        )
        ai_tools.store_session_results(
            "derived_sample", owner, {"owner": label}
        )

    accessor_a = _make_data_accessor(owner_a)
    assert accessor_a["get_search_results"]() == [{"owner": "A"}]
    assert accessor_a["get_cached_results"]("derived_sample") == {"owner": "A"}
    assert accessor_a["get_cached_results"]("latest_crossmatch") == [
        {"match": "A"}
    ]
    # Supplying another session's physical key is scoped again, not trusted.
    assert accessor_a["get_cached_results"](
        f"derived_sample:{owner_b}"
    ) is None

    snapshot_a = _collect_subprocess_cache_context(owner_a)
    assert snapshot_a["latest"] == [{"owner": "A"}]
    assert snapshot_a["latest_literature_tables"]["line_measurements"][0][
        "source_name"
    ] == "A"
    assert all("owner-b" not in key for key in snapshot_a)
    assert "B" not in repr(snapshot_a)

    default_snapshot = _collect_subprocess_cache_context("default")
    assert default_snapshot == {"latest": [{"owner": "default"}]}


def test_cached_data_source_guard_does_not_disclose_foreign_cache_keys():
    from app.services import ai_tools
    from app.services.ai_tools import _exec_run_python

    ai_tools.store_session_results(
        "private_observations", "owner-a", [{"secret_value": 42}]
    )
    result = asyncio.run(
        _exec_run_python(
            {
                "code": "print('never executed')",
                "data_source": "cached:private_observations",
            },
            python_session_id="owner-b",
        )
    )

    assert result["success"] is False
    assert result["error_class"] == "cached_key_not_found"
    assert "private_observations:owner-a" not in result["error"]
    assert "secret_value" not in result["error"]


def test_search_retrieval_and_literature_derivatives_do_not_fall_back():
    from app.services import ai_tools

    ai_tools.store_session_results("latest", "owner-a", [{"name": "A"}])
    assert ai_tools._exec_get_cached_results({}, "owner-a")["results"] == [
        {"name": "A"}
    ]
    assert ai_tools._exec_get_cached_results({}, "owner-b")["results"] == []

    ai_tools.store_session_results(
        "latest_literature_tables",
        "owner-a",
        {
            "line_measurements": [
                {
                    "source_name": "A",
                    "log_luminosity": 9.0,
                    "fwhm_km_s": 200.0,
                }
            ]
        },
    )
    rows_b, _ = ai_tools._resolve_literature_measurement_cache(
        "latest_literature_tables", "owner-b"
    )
    assert rows_b == []

    result = ai_tools._exec_demagnify_sample(
        {"mu_map": {"A": 2.0}}, python_session_id="owner-a"
    )
    assert result["success"] is True
    output_key = result["output_cache_key"]
    assert ai_tools.get_session_cached_results(output_key, "owner-a") is not None
    assert ai_tools.get_session_cached_results(output_key, "owner-b") is None
    assert ai_tools.get_cached_results(output_key) is None


def test_marking_session_deleted_clears_only_exact_owned_cache():
    from app.services import ai_tools, code_executor

    # Deliberately suffix-colliding ids prove deletion uses owner metadata, not
    # ``key.endswith(session_id)``.
    ai_tools.store_session_results("latest", "tenant:shared", [{"owner": "A"}])
    ai_tools.store_session_results("latest", "shared", [{"owner": "B"}])
    ai_tools.store_session_results("derived", "shared", {"owner": "B"})

    code_executor.mark_session_deleted("shared")

    assert ai_tools.get_session_cached_results("latest", "shared") is None
    assert ai_tools.get_session_cached_results("derived", "shared") is None
    assert ai_tools.get_session_cached_results("latest", "tenant:shared") == [
        {"owner": "A"}
    ]


def test_shared_deletion_marker_blocks_stale_cache_in_another_process():
    import time

    from app.services import ai_tools, code_executor

    session_id = ai_tools.build_trusted_python_session_id(
        user_id=str(uuid.uuid4()),
        chat_session_id=str(uuid.uuid4()),
        requested_session_id="cross-process-cache",
    )
    ai_tools.store_session_results("latest", session_id, [{"secret": "owned"}])
    code_executor.mark_session_deleted(session_id)

    # Simulate stale RAM in another web process: that process did not receive a
    # callback, so it still has the value and owner map. Its next read must
    # consult the shared marker, refuse the value, and erase its local copy.
    physical_key = ai_tools._resolved_session_cache_key("latest", session_id)
    ai_tools._search_result_cache[physical_key] = (
        [{"secret": "stale-other-process"}],
        time.time(),
    )
    ai_tools._search_result_cache_owners[physical_key] = session_id

    assert ai_tools.get_session_cached_results("latest", session_id) is None
    assert physical_key not in ai_tools._search_result_cache
    ai_tools.store_session_results("latest", session_id, [{"secret": "late-write"}])
    assert physical_key not in ai_tools._search_result_cache
