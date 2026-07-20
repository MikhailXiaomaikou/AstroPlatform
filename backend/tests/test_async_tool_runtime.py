"""Tests for the generic async-tool runtime (submit / poll / cancel)."""

from __future__ import annotations

import pytest

from app.services import _kv_store, async_tool_runtime as atr


@pytest.fixture(autouse=True)
def _isolate_runtime():
    _kv_store.use_memory_backend_for_testing()
    dispatched: list[tuple[str, dict, str]] = []

    def _fake_dispatch(tool_name, args, job_id):
        dispatched.append((tool_name, args, job_id))

    atr.set_dispatcher(_fake_dispatch)
    # expose the captured list on the runtime module for the test to read
    atr._dispatched_for_test = dispatched  # type: ignore[attr-defined]
    yield
    atr.reset_dispatcher()


class TestSubmit:
    def test_submit_returns_partial_banner_and_dispatches(self):
        banner = atr.submit_async_job("fit_cosmology_mcmc", {"n_walkers": 32, "n_steps": 5000})
        assert banner["__tool_status__"] == "PARTIAL"
        assert banner["__do_not_claim__"] is True
        assert banner["tool_name"] == "fit_cosmology_mcmc"
        assert banner["status"] == "queued"
        assert banner["job_id"].startswith("fit_cosmology_mcmc-")
        # dispatcher fired exactly once
        assert len(atr._dispatched_for_test) == 1

    def test_https_worker_mode_rejects_generic_celery_science_before_publish(
        self, monkeypatch
    ):
        from app.config import settings

        monkeypatch.setattr(settings, "science_execution_backend", "https_worker")

        banner = atr.submit_async_job(
            "fit_cosmology_mcmc",
            {"n_walkers": 32, "n_steps": 5000},
        )

        assert banner["__tool_status__"] == "FAILED"
        assert banner["error_class"] == "science_workflow_not_registered"
        assert banner["background_backend"] == "https_worker"
        assert banner["publication_ready"] is False
        assert banner["__do_not_claim__"] is True
        assert "job_id" not in banner
        assert atr._dispatched_for_test == []
        assert atr._JOBS_STORE.scan_keys() == []

    def test_submit_dedupes_identical_running_jobs(self):
        b1 = atr.submit_async_job("transit_search_bls", {"target": "TOI-700"})
        b2 = atr.submit_async_job("transit_search_bls", {"target": "TOI-700"})
        assert b1["job_id"] == b2["job_id"]
        assert b2["deduplicated"] is True
        assert len(atr._dispatched_for_test) == 1

    def test_anonymous_scopes_do_not_cross_dedup_or_poll(self):
        from app.services.ai_tools.stellar_tools import _exec_get_async_job_status

        with atr.anonymous_owner_scope("anonymous:request-a"):
            a1 = atr.submit_async_job("transit_search_bls", {"target": "TOI-700"})
            a2 = atr.submit_async_job("transit_search_bls", {"target": "TOI-700"})
            assert a2["job_id"] == a1["job_id"]
            assert atr.get_async_job(a1["job_id"]) is not None
            assert _exec_get_async_job_status(
                {"job_id": a1["job_id"]}, user_id=None
            )["__tool_status__"] == "PARTIAL"

        with atr.anonymous_owner_scope("anonymous:request-b"):
            assert atr.get_async_job(a1["job_id"]) is None
            assert _exec_get_async_job_status(
                {"job_id": a1["job_id"]}, user_id=None
            )["error_class"] == "not_found"
            b = atr.submit_async_job("transit_search_bls", {"target": "TOI-700"})
            assert b["job_id"] != a1["job_id"]
            assert atr.get_async_job(b["job_id"]) is not None

        raw_a = atr._JOBS_STORE.get(a1["job_id"])
        assert raw_a["user_id"] is None
        assert raw_a["owner_scope"] == "anonymous:request-a"

    def test_submit_does_not_dedupe_different_args(self):
        atr.submit_async_job("transit_search_bls", {"target": "TOI-700"})
        atr.submit_async_job("transit_search_bls", {"target": "TOI-200"})
        assert len(atr._dispatched_for_test) == 2

    def test_dedup_off_always_dispatches(self):
        atr.submit_async_job("x", {"k": 1}, dedup=False)
        atr.submit_async_job("x", {"k": 1}, dedup=False)
        assert len(atr._dispatched_for_test) == 2

    def test_job_cap_never_evicts_queued_or_running_jobs(self, monkeypatch):
        monkeypatch.setattr(atr, "MAX_STORED_JOBS", 2)

        first = atr.submit_async_job("x", {"k": 1}, dedup=False)
        second = atr.submit_async_job("x", {"k": 2}, dedup=False)
        third = atr.submit_async_job("x", {"k": 3}, dedup=False)

        assert atr._JOBS_STORE.get(first["job_id"])["status"] == "queued"
        atr.update_progress(second["job_id"], status="running")
        assert atr._JOBS_STORE.get(second["job_id"])["status"] == "running"
        assert atr._JOBS_STORE.get(third["job_id"])["status"] == "queued"

    def test_job_cap_removes_only_same_owner_terminal_jobs(self, monkeypatch):
        monkeypatch.setattr(atr, "MAX_STORED_JOBS", 2)
        owner = "owner-a"
        other = "owner-b"

        old_terminal = atr.submit_async_job(
            "x", {"k": "old"}, dedup=False, user_id=owner
        )
        atr.write_result(old_terminal["job_id"], {"ok": True})
        other_terminal = atr.submit_async_job(
            "x", {"k": "other"}, dedup=False, user_id=other
        )
        atr.write_error(other_terminal["job_id"], "done")
        active_one = atr.submit_async_job(
            "x", {"k": "active-1"}, dedup=False, user_id=owner
        )
        active_two = atr.submit_async_job(
            "x", {"k": "active-2"}, dedup=False, user_id=owner
        )

        assert atr._JOBS_STORE.get(old_terminal["job_id"]) is None
        assert atr._JOBS_STORE.get(other_terminal["job_id"])["status"] == "failed"
        assert atr._JOBS_STORE.get(active_one["job_id"])["status"] == "queued"
        assert atr._JOBS_STORE.get(active_two["job_id"])["status"] == "queued"

    def test_celery_unavailable_returns_failed_banner(self):
        def boom(tool, args, job_id):
            raise RuntimeError("worker offline")

        atr.set_dispatcher(boom)
        banner = atr.submit_async_job("fit_transit", {"foo": "bar"})
        assert banner["__tool_status__"] == "FAILED"
        assert banner["error_class"] == "celery_unavailable"
        # The failed job is still queryable
        job = atr.get_async_job(banner["job_id"])
        assert job is not None
        assert job["status"] == "failed"

    def test_persistence_failure_is_visible_and_prevents_dispatch(self):
        from app.services.durable_research_records import (
            ResearchJobPersistenceError,
        )

        def fail_persistence(_job):
            raise ResearchJobPersistenceError("database unavailable")

        atr.set_persister(fail_persistence)
        banner = atr.submit_async_job("fit_cosmology_mcmc", {"n_steps": 1000})

        assert banner["__tool_status__"] == "FAILED"
        assert banner["error_class"] == "durable_persistence_failed"
        assert len(atr._dispatched_for_test) == 0
        hot = atr._JOBS_STORE.get(banner["job_id"])
        assert hot["status"] == "failed"
        assert hot["durability_status"] == "failed"


class TestPoll:
    def test_get_async_job_returns_none_for_unknown(self):
        assert atr.get_async_job("ghost") is None

    def test_get_async_job_returns_dict_for_existing(self):
        banner = atr.submit_async_job("t", {})
        job = atr.get_async_job(banner["job_id"])
        assert job is not None
        assert job["status"] == "queued"

    def test_format_status_unknown_job(self):
        out = atr.format_status_for_tool(None, requested_job_id="x")
        assert out["__tool_status__"] == "FAILED"
        assert out["error_class"] == "not_found"

    def test_format_status_queued(self):
        banner = atr.submit_async_job("t", {})
        job = atr.get_async_job(banner["job_id"])
        out = atr.format_status_for_tool(job, requested_job_id=banner["job_id"])
        assert out["__tool_status__"] == "PARTIAL"
        assert out["analysis_status"] == "QUEUED"
        assert out["__do_not_claim__"] is True

    def test_format_status_completed_unwraps_result_dict(self):
        banner = atr.submit_async_job("t", {})
        atr.write_result(banner["job_id"], {"answer": 42, "__tool_status__": "OK"})
        job = atr.get_async_job(banner["job_id"])
        out = atr.format_status_for_tool(job, requested_job_id=banner["job_id"])
        assert out["__tool_status__"] == "OK"
        assert out["answer"] == 42
        assert out["job_id"] == banner["job_id"]

    def test_format_status_failed(self):
        banner = atr.submit_async_job("t", {})
        atr.write_error(banner["job_id"], RuntimeError("boom"))
        job = atr.get_async_job(banner["job_id"])
        out = atr.format_status_for_tool(job, requested_job_id=banner["job_id"])
        assert out["__tool_status__"] == "FAILED"
        assert out["error"] == "boom"
        assert out["error_class"] == "RuntimeError"


class TestProgress:
    def test_update_progress_transitions(self):
        banner = atr.submit_async_job("t", {})
        atr.update_progress(banner["job_id"], status="running", progress=10.0, progress_message="warmup")
        job = atr.get_async_job(banner["job_id"])
        assert job["status"] == "running"
        assert job["progress"] == 10.0
        assert job["progress_message"] == "warmup"
        assert "started_at" in job

    def test_update_progress_completed_records_completed_at(self):
        banner = atr.submit_async_job("t", {})
        atr.update_progress(banner["job_id"], status="completed")
        job = atr.get_async_job(banner["job_id"])
        assert "completed_at" in job


class TestWorkerDeliveryGuards:
    def test_reconciled_terminal_job_is_never_resurrected(self):
        from app.tasks.ai_tools_tasks import run_long_tool

        atr._JOBS_STORE.set(
            "stale-terminal-1",
            {
                "job_id": "stale-terminal-1",
                "status": "failed",
                "error_class": "stale_job_reconciled",
            },
            ttl=3600,
        )

        result = run_long_tool.run("fit_cosmology_mcmc", {}, "stale-terminal-1")

        assert result == {"status": "failed", "job_id": "stale-terminal-1"}
        assert atr._JOBS_STORE.get("stale-terminal-1")["error_class"] == (
            "stale_job_reconciled"
        )


class TestCancel:
    def test_cancel_marks_status_cancelled(self):
        banner = atr.submit_async_job("t", {})
        out = atr.cancel_async_job(banner["job_id"])
        assert out["status"] == "cancelled"

    def test_is_cancelled_reflects_state(self):
        banner = atr.submit_async_job("t", {})
        assert not atr.is_cancelled(banner["job_id"])
        atr.cancel_async_job(banner["job_id"])
        assert atr.is_cancelled(banner["job_id"])

    def test_cancel_on_completed_is_idempotent(self):
        banner = atr.submit_async_job("t", {})
        atr.write_result(banner["job_id"], {"ok": True})
        out = atr.cancel_async_job(banner["job_id"])
        # Should not flip completed → cancelled
        assert out["status"] == "completed"

    def test_cancel_unknown_returns_not_found(self):
        out = atr.cancel_async_job("ghost")
        assert out["success"] is False
        assert out["error_class"] == "not_found"
