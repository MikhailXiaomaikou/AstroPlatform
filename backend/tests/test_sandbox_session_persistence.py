"""S1 (PART S) regression: session state persists across cells under the subprocess sandbox.

Round 9 R9-NEW-3 observed that AI variables (`sdss_galaxies`, `Planck18`, `SkyCoord`,
`v_total`) raised NameError in the next run_python cell. Cause: SANDBOX_BACKEND=subprocess
forks fresh each time; _session_vars only works for the in-process backend. Fix:
prepend prior-cell code as a replay prefix before subprocess dispatch so state is rebuilt
in a single exec.

Companion R9-NEW-1 detector regression: previous cell called get_adql_results, next cell
uses the rows variable directly — the current cell does not reference the helper name in
the AST, so the contract validator rejects it.
Fix: detector also scans session history for helper calls.
"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="subprocess sandbox is only tested on POSIX systems"
)


@pytest.fixture(autouse=True)
def _reset_session_state():
    """Clear session state before each test to avoid cross-test contamination."""
    from app.services import code_executor as ce
    # clear any leftover test sessions
    for sid in list(ce._session_code_history.keys()):
        ce.clear_session_vars(sid)
    yield
    for sid in list(ce._session_code_history.keys()):
        ce.clear_session_vars(sid)


def test_subprocess_replay_prefix_persists_variables():
    """Core invariant: under subprocess, the same session_id must be able to access variables from the previous cell across cells."""
    from app.services.code_executor import execute_python

    session_id = "test_s1_persist"

    # Cell 1: define variables
    r1 = execute_python("x = 42\nmsg = 'hello'", session_id=session_id)
    assert r1.success, f"cell 1 failed: {r1.error} / {r1.stderr}"

    # Cell 2: use variables from the previous cell
    r2 = execute_python("print(f'x={x}, msg={msg}')", session_id=session_id)
    assert r2.success, f"cell 2 should see prior vars, got: {r2.error} / {r2.stderr}"
    assert "x=42" in r2.stdout
    assert "msg=hello" in r2.stdout


def test_failed_cell_not_added_to_history():
    """Failed cells must not be added to the replay history, otherwise subsequent subprocess runs will re-crash."""
    from app.services.code_executor import execute_python, get_session_code_history

    session_id = "test_s1_failed"

    # Cell 1: success
    execute_python("y = 100", session_id=session_id)
    history_after_ok = get_session_code_history(session_id)
    assert len(history_after_ok) >= 1

    # Cell 2: failure (NameError)
    bad = execute_python("undefined_var + 1", session_id=session_id)
    assert not bad.success

    # history must not contain the failed cell
    history_after_fail = get_session_code_history(session_id)
    assert not any("undefined_var" in block for block in history_after_fail)


def test_failed_cell_records_completed_prefix_for_replay(monkeypatch):
    """R14-NEW-2: Variables defined in the completed prefix of a failed cell must be visible to the next cell."""
    from app.config import settings
    from app.services.code_executor import execute_python, get_session_code_history

    monkeypatch.setattr(settings, "sandbox_backend", "subprocess")
    session_id = "test_r14_partial_prefix"

    r1 = execute_python(
        "time_clean = [1, 2, 3]\n"
        "print('prepared partial state')\n"
        "missing_name + 1",
        session_id=session_id,
    )
    assert not r1.success
    assert "prepared partial state" in r1.stdout
    assert "time_clean" in r1.variables

    history = get_session_code_history(session_id)
    assert history, "the completed prefix before the failure should enter the replay history"
    assert "time_clean = [1, 2, 3]" in history[-1]
    assert "missing_name" not in history[-1]

    r2 = execute_python("print(sum(time_clean))", session_id=session_id)
    assert r2.success, f"next cell should see partial prefix vars: {r2.error} / {r2.stderr}"
    assert "6" in r2.stdout


def test_default_session_does_not_accumulate_history():
    """session_id='default' does not enable persistence, to avoid interference between tests and anonymous users."""
    from app.services.code_executor import execute_python, get_session_code_history

    execute_python("z = 1", session_id="default")
    execute_python("w = 2", session_id="default")
    assert get_session_code_history("default") == []


def test_session_history_capped():
    """History is capped at MAX_SESSION_CODE_BLOCKS; oldest entries are discarded."""
    from app.services.code_executor import (
        MAX_SESSION_CODE_BLOCKS,
        append_session_code_block,
        get_session_code_history,
    )

    sid = "test_cap"
    for i in range(MAX_SESSION_CODE_BLOCKS + 5):
        append_session_code_block(sid, f"v{i} = {i}")

    history = get_session_code_history(sid)
    assert len(history) == MAX_SESSION_CODE_BLOCKS
    # the oldest 5 (v0..v4) should be discarded
    assert "v0 =" not in "\n".join(history)
    assert f"v{MAX_SESSION_CODE_BLOCKS + 4} =" in "\n".join(history)


def test_get_session_helper_calls_picks_up_function_names():
    """R9-NEW-1 detector relaxation: helper names called in session history should be recognised."""
    from app.services.code_executor import (
        append_session_code_block,
        get_session_helper_calls,
    )

    sid = "test_helpers"
    append_session_code_block(sid, "rows = get_adql_results()")
    append_session_code_block(sid, "df = pd.DataFrame(rows)")

    called = get_session_helper_calls(sid)
    assert "get_adql_results" in called
    assert "pd" in called
    assert "DataFrame" in called
    assert "rows" in called


def test_get_session_defined_names_reports_history_assignments():
    """R22: NameError hints should be able to tell the AI which variables already exist in this session."""
    from app.services.code_executor import (
        append_session_code_block,
        clear_session_vars,
        get_session_defined_names,
    )

    sid = "test_r22_defined_names"
    clear_session_vars(sid)
    append_session_code_block(sid, "time_clean = [1, 2]\nflux_clean = [0.9, 1.0]")
    names = get_session_defined_names(sid)
    assert "time_clean" in names
    assert "flux_clean" in names


def test_clear_session_also_clears_code_history():
    """clear_session_vars must also clear _session_code_history."""
    from app.services.code_executor import (
        append_session_code_block,
        clear_session_vars,
        get_session_code_history,
    )

    sid = "test_clear"
    append_session_code_block(sid, "x = 1")
    assert get_session_code_history(sid)

    clear_session_vars(sid)
    assert get_session_code_history(sid) == []
