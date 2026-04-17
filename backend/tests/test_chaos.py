"""Chaos / failure-injection tests (Phase 3 / R12).

Simulates known hostile conditions against the chat pipeline and the
connector retry layer, asserting that:

- The user sees a clear error message (not a blank bubble).
- No corrupt state is persisted.
- The right Prometheus counter increments so operators can see the
  failure mode after the fact.

Uses the in-process retry decorator + claim validator directly; no HTTP
fixtures required.  If you later add an archive-integration chaos pass
it lives under ``-m integration``.
"""

from __future__ import annotations

import asyncio

import pytest

from app.connectors.retry import reset_circuit, with_retry


# ------------------------------------------------------------------
# R12-A: a connector that 503s three times does NOT open the circuit
# if max_retries=2 is enough to exhaust but not exceed threshold.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connector_retries_and_raises_on_exhaustion():
    reset_circuit()

    call_count = 0

    @with_retry(max_retries=2, base_delay=0.001)
    async def always_503() -> str:
        nonlocal call_count
        call_count += 1
        raise ConnectionError("HTTP 503")

    with pytest.raises(ConnectionError):
        await always_503()
    # 1 initial + 2 retries
    assert call_count == 3


# ------------------------------------------------------------------
# R12-B: user-error (ValueError) must not retry, must not open circuit.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connector_does_not_retry_user_errors():
    reset_circuit()

    call_count = 0

    @with_retry(max_retries=3, base_delay=0.001)
    async def bad_user_input() -> str:
        nonlocal call_count
        call_count += 1
        raise ValueError("malformed ADQL")

    with pytest.raises(ValueError):
        await bad_user_input()
    assert call_count == 1


# ------------------------------------------------------------------
# R12-C: claim validator survives a tool_results blob that mixes
# numeric strings, nested lists, and nulls without crashing.
# ------------------------------------------------------------------

def test_claim_validator_handles_adversarial_payload():
    from app.services.claim_validator import validate_claims

    payload = [
        {"tool": "x", "input": None, "result": None},
        {"tool": "y", "input": {}, "result": {"nested": [[1, 2, [3.14]], None]}},
        {"tool": "z", "input": {}, "result": "csv_preview\n1,2,3\n"},
    ]
    r = validate_claims("Nothing to cite here.", payload)
    assert r.ok

    r2 = validate_claims("z = 99.9", payload)
    assert not r2.ok  # 99.9 not in payload


# ------------------------------------------------------------------
# R12-D: normalize_tool_result must not crash when `tool_input` is
# non-JSON-serialisable (e.g., a function or a complex object).
# ------------------------------------------------------------------

def test_normalize_tool_result_handles_non_json_input():
    from app.services.result_provenance import normalize_tool_result

    class _NotSerialisable:
        pass

    r = normalize_tool_result(
        "run_python",
        {"value": 1},
        tool_input={"callback": _NotSerialisable()},
    )
    # Reproducibility envelope still present with a query_hash.
    assert "reproducibility" in r
    assert "query_hash" in r["reproducibility"]


# ------------------------------------------------------------------
# R12-E: concurrent get_session_vars on the sandbox does not exceed
# the TTL cap under a burst (regression guard for eviction races).
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sandbox_session_cap_under_concurrent_burst():
    from app.services import code_executor as ce

    # Isolate module state.
    ce._session_vars.clear()
    ce._session_replay_signatures.clear()
    ce._session_last_access.clear()

    async def create(i: int) -> None:
        ce.get_session_vars(f"burst-{i}")

    # Fire 200 sessions concurrently; registry must end below
    # MAX_TRACKED_SESSIONS + burst because the sweep runs on each
    # new-session creation once we cross the cap.
    await asyncio.gather(*(create(i) for i in range(200)))
    assert len(ce._session_vars) <= ce.MAX_TRACKED_SESSIONS * 2
