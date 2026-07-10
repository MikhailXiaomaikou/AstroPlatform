"""Cross-restart persistence and owner isolation for scientific evidence."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.database import Base


@pytest.fixture
def durable_database(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import durable_research_records as durable

    url = f"sqlite+aiosqlite:///{tmp_path / 'durable.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    durable.reset_engine()
    engine = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(engine)
    engine.dispose()
    yield durable
    durable.reset_engine()


def test_provenance_survives_hot_cache_clear_and_is_owner_scoped(durable_database):
    from app.services import provenance

    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    provenance._provenance_records.clear()
    record_id = provenance.record_activity(
        entity_type="chat_tool_result",
        entity_id="run-42",
        activity="fit_cosmology_mcmc",
        params={"random_seed": 7},
        parent_ids=["dataset-1"],
        user_id=owner,
        artifact_sha256="a" * 64,
    )
    assert record_id

    provenance._provenance_records.clear()  # simulate process restart
    lineage = provenance.get_lineage("run-42", owner_id=owner)
    assert [node["id"] for node in lineage["nodes"]] == ["run-42"]
    assert lineage["edges"] == [{"from": "dataset-1", "to": "run-42"}]
    assert provenance.get_lineage("run-42", owner_id=other)["nodes"] == []


def test_async_job_survives_kv_restart_and_rejects_other_owner(durable_database):
    from app.services import _kv_store, async_tool_runtime as runtime

    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    _kv_store.use_memory_backend_for_testing()
    runtime.set_dispatcher(lambda *_args, **_kwargs: None)
    runtime.reset_persister()
    try:
        banner = runtime.submit_async_job(
            "fit_cosmology_mcmc",
            {"n_walkers": 32, "n_steps": 800},
            user_id=owner,
            session_id=None,
        )
        runtime.write_result(banner["job_id"], {"success": True, "value": 1.0})

        _kv_store.use_memory_backend_for_testing()  # empty replacement backend
        restored = runtime.get_async_job(banner["job_id"], owner_id=owner)
        assert restored is not None
        assert restored["status"] == "completed"
        assert restored["result"] == {"success": True, "value": 1.0}
        assert runtime.get_async_job(banner["job_id"], owner_id=other) is None
    finally:
        runtime.reset_dispatcher()
        runtime.set_persister(lambda _job: None)


def test_large_job_arguments_are_hashed_not_replayed(durable_database, monkeypatch):
    from app.services import durable_research_records as durable

    monkeypatch.setattr(durable, "MAX_REPLAY_ARGS_BYTES", 64)
    owner = str(uuid.uuid4())
    durable.save_job({
        "job_id": "large-1",
        "user_id": owner,
        "tool_name": "transit_search_bls",
        "inputs_hash": "abc",
        "args": {"flux": list(range(100))},
        "status": "failed",
        "created_at": 1_700_000_000,
    })

    restored = durable.load_job("large-1", owner_id=owner)
    assert restored is not None
    assert restored["args_replayable"] is False
    assert restored["args"]["_omitted"].startswith("arguments exceed")
    assert len(restored["args"]["sha256"]) == 64


def test_large_job_result_is_integrity_checked_object_artifact(
    durable_database, monkeypatch, tmp_path
):
    from app import storage
    from app.services import durable_research_records as durable

    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 64)
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    owner = str(uuid.uuid4())
    expected = {"chain": list(range(100))}
    durable.save_job({
        "job_id": "large-result-1",
        "user_id": owner,
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "completed",
        "result": expected,
        "created_at": 1_700_000_000,
        "completed_at": 1_700_000_001,
    })

    raw = durable.load_job("large-result-1", owner_id=owner, hydrate=False)
    assert raw is not None
    assert raw["result"]["_artifact_ref"].endswith("/result.json.gz")
    assert len(raw["result"]["sha256"]) == 64
    restored = durable.load_job("large-result-1", owner_id=owner)
    assert restored is not None
    assert restored["result"] == expected


def test_job_persistence_retries_before_success(durable_database, monkeypatch):
    durable = durable_database
    attempts: list[str] = []

    def flaky(job, **_kwargs):
        attempts.append(str(job["job_id"]))
        if len(attempts) < 3:
            raise OSError("temporary database outage")

    monkeypatch.setattr(durable, "_save_job_once", flaky)
    monkeypatch.setattr(durable, "JOB_PERSIST_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(durable, "JOB_PERSIST_RETRY_BASE_SECONDS", 0)

    durable.save_job({
        "job_id": "retry-write-1",
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "queued",
        "created_at": 1_700_000_000,
    })

    assert attempts == ["retry-write-1"] * 3


def test_job_persistence_raises_after_bounded_retries(
    durable_database, monkeypatch
):
    durable = durable_database
    attempts = 0

    def always_fail(_job, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("database remains unavailable")

    monkeypatch.setattr(durable, "_save_job_once", always_fail)
    monkeypatch.setattr(durable, "JOB_PERSIST_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(durable, "JOB_PERSIST_RETRY_BASE_SECONDS", 0)

    with pytest.raises(durable.ResearchJobPersistenceError):
        durable.save_job({
            "job_id": "retry-write-2",
            "tool_name": "fit_cosmology_mcmc",
            "inputs_hash": "abc",
            "args": {},
            "status": "queued",
            "created_at": 1_700_000_000,
        })

    assert attempts == 2


def test_stale_job_reconciliation_is_durable_and_updates_hot_state(
    durable_database,
):
    from app.models.research_records import ResearchJob
    from app.services import _kv_store, async_tool_runtime as runtime

    durable = durable_database
    now = datetime.now(timezone.utc)
    durable.save_job({
        "job_id": "orphaned-running-1",
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "running",
        "created_at": now - timedelta(hours=3),
        "started_at": now - timedelta(hours=3),
    })
    with Session(durable._engine()) as db:
        row = db.get(ResearchJob, "orphaned-running-1")
        row.updated_at = now - timedelta(hours=2)
        db.commit()

    _kv_store.use_memory_backend_for_testing()
    runtime._JOBS_STORE.set(
        "orphaned-running-1",
        {"job_id": "orphaned-running-1", "status": "running", "ttl": 3600},
        ttl=3600,
    )

    assert durable.reconcile_stale_jobs(
        stale_after_seconds=3600,
        now=now,
    ) == 1
    restored = durable.load_job("orphaned-running-1")
    assert restored["status"] == "failed"
    assert restored["error_class"] == "stale_job_reconciled"
    hot = runtime._JOBS_STORE.get("orphaned-running-1")
    assert hot["status"] == "failed"
    assert hot["durability_status"] == "durable"
