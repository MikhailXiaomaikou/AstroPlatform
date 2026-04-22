"""Tests for workflow_checkpoint session store."""

import pytest

from app.services import workflow_checkpoint as wc


@pytest.fixture(autouse=True)
def _reset():
    wc.reset()
    yield
    wc.reset()


class TestCheckpoint:
    def test_record_and_retrieve(self):
        wc.record_step("s1", "search_objects", "h1", "completed", ["latest"])
        cp = wc.get_checkpoint("s1")
        assert cp is not None
        assert len(cp.steps) == 1
        assert cp.steps[0].tool_name == "search_objects"
        assert cp.steps[0].status == "completed"

    def test_step_idx_is_sequential(self):
        for i in range(5):
            wc.record_step("s2", f"tool{i}", f"h{i}", "completed")
        cp = wc.get_checkpoint("s2")
        assert [s.step_idx for s in cp.steps] == [0, 1, 2, 3, 4]

    def test_last_successful_step_ignores_failed(self):
        wc.record_step("s3", "a", "h1", "completed")
        wc.record_step("s3", "b", "h2", "completed")
        wc.record_step("s3", "c", "h3", "failed", error="boom")
        last = wc.get_last_successful_step("s3")
        assert last is not None
        assert last.tool_name == "b"
        assert last.step_idx == 1

    def test_summary_shape(self):
        wc.record_step(
            "s4", "x", "h", "completed", ["latest"],
            tool_call_id="toolu_1", summary="1 row cached",
        )
        wc.record_step("s4", "y", "h", "failed", error="oops")
        summary = wc.summarize("s4")
        assert summary["has_checkpoint"] is True
        assert summary["n_steps"] == 2
        assert summary["last_successful_step_idx"] == 0
        assert summary["steps"][0]["tool_call_id"] == "toolu_1"
        assert summary["steps"][0]["summary"] == "1 row cached"
        assert summary["steps"][1]["error"] == "oops"

    def test_missing_session_returns_empty_summary(self):
        summary = wc.summarize("ghost")
        assert summary["has_checkpoint"] is False
        assert summary["steps"] == []

    def test_clear_session(self):
        wc.record_step("s5", "x", "h", "completed")
        wc.clear_session("s5")
        assert wc.get_checkpoint("s5") is None

    def test_bounded_step_count(self):
        for i in range(wc.MAX_STEPS_PER_SESSION + 10):
            wc.record_step("s6", f"tool{i}", "h", "completed")
        cp = wc.get_checkpoint("s6")
        assert len(cp.steps) == wc.MAX_STEPS_PER_SESSION
        # Step indices renumbered after truncation
        assert cp.steps[0].step_idx == 0
        assert cp.steps[-1].step_idx == wc.MAX_STEPS_PER_SESSION - 1
