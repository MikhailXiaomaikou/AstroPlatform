"""S1 (PART S) 回归: subprocess sandbox 下 session state 跨 cell 持久.

Round 9 R9-NEW-3 观察到 AI 的变量 (`sdss_galaxies`, `Planck18`, `SkyCoord`,
`v_total`) 在下一个 run_python cell 里 NameError. 原因: SANDBOX_BACKEND=subprocess
每次 fresh fork, _session_vars 只对 in-process backend 生效. 修复方案:
subprocess dispatch 前拼历史 code 作为 prefix, 一次 exec 重建 state.

配套的 R9-NEW-1 detector regression: 前 cell 调过 get_adql_results, 后 cell
直接用 rows 变量 — 当前 cell 没在 AST 里引用 helper 名, 被 contract validator 拒.
修复: detector 也扫 session 历史中 helper 调用.
"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="subprocess sandbox 仅在 POSIX 系统测试"
)


@pytest.fixture(autouse=True)
def _reset_session_state():
    """每条测试开始前清 session 状态, 避免相互污染."""
    from app.services import code_executor as ce
    # 清掉可能残留的 test session
    for sid in list(ce._session_code_history.keys()):
        ce.clear_session_vars(sid)
    yield
    for sid in list(ce._session_code_history.keys()):
        ce.clear_session_vars(sid)


def test_subprocess_replay_prefix_persists_variables():
    """核心: subprocess 下同一 session_id 跨 cell 应能访问前 cell 变量."""
    from app.services.code_executor import execute_python

    session_id = "test_s1_persist"

    # Cell 1: 定义变量
    r1 = execute_python("x = 42\nmsg = 'hello'", session_id=session_id)
    assert r1.success, f"cell 1 failed: {r1.error} / {r1.stderr}"

    # Cell 2: 使用前 cell 变量
    r2 = execute_python("print(f'x={x}, msg={msg}')", session_id=session_id)
    assert r2.success, f"cell 2 should see prior vars, got: {r2.error} / {r2.stderr}"
    assert "x=42" in r2.stdout
    assert "msg=hello" in r2.stdout


def test_failed_cell_not_added_to_history():
    """失败的 cell 不该进 replay 历史, 否则后续 subprocess 每次 re-crash."""
    from app.services.code_executor import execute_python, get_session_code_history

    session_id = "test_s1_failed"

    # Cell 1: 成功
    execute_python("y = 100", session_id=session_id)
    history_after_ok = get_session_code_history(session_id)
    assert len(history_after_ok) >= 1

    # Cell 2: 失败 (NameError)
    bad = execute_python("undefined_var + 1", session_id=session_id)
    assert not bad.success

    # 历史不应包含失败的 cell
    history_after_fail = get_session_code_history(session_id)
    assert not any("undefined_var" in block for block in history_after_fail)


def test_failed_cell_records_completed_prefix_for_replay(monkeypatch):
    """R14-NEW-2: 失败 cell 前半段已定义的变量要能被下一 cell 看到。"""
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
    assert history, "失败前已完成的前缀应进入 replay history"
    assert "time_clean = [1, 2, 3]" in history[-1]
    assert "missing_name" not in history[-1]

    r2 = execute_python("print(sum(time_clean))", session_id=session_id)
    assert r2.success, f"next cell should see partial prefix vars: {r2.error} / {r2.stderr}"
    assert "6" in r2.stdout


def test_default_session_does_not_accumulate_history():
    """session_id='default' 不启用持久化, 避免测试 / 匿名用户互相干扰."""
    from app.services.code_executor import execute_python, get_session_code_history

    execute_python("z = 1", session_id="default")
    execute_python("w = 2", session_id="default")
    assert get_session_code_history("default") == []


def test_session_history_capped():
    """MAX_SESSION_CODE_BLOCKS 上限, 老的被丢弃."""
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
    # 最老的 5 个 (v0..v4) 应被丢弃
    assert "v0 =" not in "\n".join(history)
    assert f"v{MAX_SESSION_CODE_BLOCKS + 4} =" in "\n".join(history)


def test_get_session_helper_calls_picks_up_function_names():
    """R9-NEW-1 detector 放宽: session 历史里 call 过的 helper 名应能被识别."""
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
    """R22: NameError 提示应能告诉 AI 这个 session 已有哪些变量。"""
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
    """clear_session_vars 要连带清 _session_code_history."""
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
