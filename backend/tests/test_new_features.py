"""Tests for new features: analysis toolkit, code executor, AI tools, pipeline batch."""

from pathlib import Path

import pytest
import numpy as np


@pytest.mark.asyncio
async def test_chat_stream_accepts_anonymous_local_backend(app_client, monkeypatch):
    """Local browser chat can run with server-side local backend before sign-in."""

    async def fake_build_runtime(req, user, db):
        assert user is None
        return {
            "agent_names": ["orchestrator"],
            "toolset": [],
            "system": "test system",
        }

    async def fake_run_orchestrated_chat(**kwargs):
        return {"reply": "anonymous stream ok", "actions": []}

    monkeypatch.setattr("app.api.chat._build_runtime", fake_build_runtime)
    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", fake_run_orchestrated_chat)

    resp = await app_client.post(
        "/api/chat/message/stream",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "api_provider": "local",
                "model_profile": "local:openai-cli",
                "python_session_id": "anonymous-local-test",
                "current_session_id": None,
            },
        },
    )

    assert resp.status_code == 200
    assert "anonymous stream ok" in resp.text
    assert '"type": "done"' in resp.text


class TestAstroAnalysis:
    """Test the astronomy analysis toolkit."""

    def test_bpt_classify(self):
        from app.services.astro_analysis import bpt_classify
        classes = bpt_classify(
            np.array([-1.0, -0.3, 0.3]),
            np.array([-0.5, 0.5, 1.0]),
        )
        assert "SF" in classes
        assert "AGN" in classes

    def test_compute_luminosity_distance(self):
        from app.services.astro_analysis import compute_luminosity_distance
        d = compute_luminosity_distance(np.array([0.1]))
        assert d[0] > 400  # ~460 Mpc

    def test_compute_absolute_magnitude(self):
        from app.services.astro_analysis import compute_absolute_magnitude
        M = compute_absolute_magnitude(np.array([15.0]), parallax_mas=np.array([10.0]))
        assert M[0] == pytest.approx(10.0, abs=0.1)

    def test_compute_absolute_magnitude_zero_distance(self):
        from app.services.astro_analysis import compute_absolute_magnitude
        M = compute_absolute_magnitude(np.array([15.0]), distance_mpc=np.array([0.0]))
        assert np.isnan(M[0])

    def test_compute_absolute_magnitude_distance_pc(self):
        from app.services.astro_analysis import compute_absolute_magnitude
        M = compute_absolute_magnitude(np.array([15.0]), distance_pc=np.array([1e6]))
        assert M[0] == pytest.approx(-10.0, abs=0.1)

    def test_compute_absolute_magnitude_apparent_mag_alias(self):
        from app.services.astro_analysis import compute_absolute_magnitude
        M = compute_absolute_magnitude(apparent_mag=np.array([10.0]), distance_pc=np.array([100.0]))
        assert M[0] == pytest.approx(5.0, abs=0.1)

    def test_available_functions_docs(self):
        from app.services.astro_analysis import available_functions
        info = available_functions()
        assert "compute_absolute_magnitude" in info
        assert "distance_pc" in info["compute_absolute_magnitude"]["signature"]
        assert info["compute_luminosity_distance"]["summary"]
        assert "full_reduction" in info
        assert "solve_astrometry" in info

    def test_continuum_normalize(self):
        from app.services.astro_analysis import continuum_normalize
        wave = np.linspace(4000, 7000, 200)
        flux = np.ones(200) * 100
        norm, cont = continuum_normalize(wave, flux)
        assert np.allclose(norm, 1.0, atol=0.01)

    def test_continuum_normalize_flat(self):
        """Flat spectrum should not crash."""
        from app.services.astro_analysis import continuum_normalize
        wave = np.arange(50, dtype=float)
        flux = np.ones(50) * 42.0
        norm, cont = continuum_normalize(wave, flux)
        assert len(norm) == 50

    def test_multi_gaussian_fit(self):
        from app.services.astro_analysis import multi_gaussian_fit
        wave = np.linspace(4800, 5100, 200)
        flux = 100 + 30 * np.exp(-0.5 * ((wave - 5007) / 3) ** 2)
        result = multi_gaussian_fit(wave, flux, n_components=1, initial_centers=[5007])
        assert result["success"]
        assert abs(result["components"][0]["center"]["value"] - 5007) < 20

    def test_multi_gaussian_fit_too_few_points(self):
        from app.services.astro_analysis import multi_gaussian_fit
        result = multi_gaussian_fit([1.0, 2.0], [10.0, 20.0], n_components=2)
        assert not result["success"]

    def test_batch_equivalent_width(self):
        from app.services.astro_analysis import batch_equivalent_width
        wave = np.linspace(6400, 6700, 200)
        flux = np.ones(200) * 10.0 - 3.0 * np.exp(-0.5 * ((wave - 6563) / 3) ** 2)
        ews = batch_equivalent_width(wave, flux, [6563.0])
        assert len(ews) == 1
        assert ews[0]["ew"] is not None
        assert ews[0]["type"] == "absorption"

    def test_k_correction(self):
        from app.services.astro_analysis import k_correction
        kc = k_correction(np.array([0.0, 0.1]), band="r")
        assert kc[0] == pytest.approx(0.0, abs=0.01)

    def test_spectral_stacking(self):
        from app.services.astro_analysis import spectral_stacking
        w1 = np.linspace(4000, 7000, 100)
        f1 = np.ones(100) * 50 + np.random.normal(0, 2, 100)
        f2 = np.ones(100) * 50 + np.random.normal(0, 2, 100)
        sw, sf = spectral_stacking([w1, w1], [f1, f2])
        assert len(sw) == 100
        assert abs(np.mean(sf) - 50) < 5


class TestCodeExecutor:
    """Test the Python code execution sandbox."""

    def test_basic_print(self):
        from app.services.code_executor import execute_python
        r = execute_python('print("hello")')
        assert r.success
        assert "hello" in r.stdout

    def test_numpy(self):
        from app.services.code_executor import execute_python
        r = execute_python('import numpy as np; print(np.mean([1,2,3]))')
        assert r.success
        assert "2.0" in r.stdout

    def test_blocked_os(self):
        from app.services.code_executor import execute_python
        r = execute_python('import os')
        assert not r.success
        assert "not allowed" in r.error or "blocked" in r.error

    def test_blocked_os_path(self):
        from app.services.code_executor import execute_python
        r = execute_python('import os.path')
        assert not r.success

    def test_error_handling(self):
        from app.services.code_executor import execute_python
        r = execute_python('1/0')
        assert not r.success
        assert "ZeroDivision" in r.error

    def test_matplotlib_figure(self):
        from app.services.code_executor import execute_python
        r = execute_python('''
import matplotlib.pyplot as plt
plt.figure()
plt.plot([1,2,3])
print("ok")
''')
        assert r.success
        assert len(r.figures) >= 1

    def test_astro_toolkit_available(self):
        from app.services.code_executor import execute_python
        r = execute_python('print(type(plot_hr_diagram))')
        assert r.success
        assert "function" in r.stdout

    def test_import_astro_alias_is_allowed(self):
        from app.services.code_executor import execute_python
        r = execute_python("import astro\nprint(callable(astro.compute_luminosity_distance))")
        assert r.success
        assert "True" in r.stdout

    def test_legacy_astro_analysis_import_is_rewritten(self):
        from app.services.code_executor import execute_python

        r = execute_python(
            "from app.services import astro_analysis\n"
            "print(callable(astro_analysis.compute_luminosity_distance))"
        )
        assert r.success
        assert "True" in r.stdout

    def test_data_accessor(self):
        from app.services.code_executor import execute_python
        from app.services.ai_tools import store_search_results
        store_search_results("latest", [{"name": "test"}])
        r = execute_python('r = get_search_results(); print(len(r))')
        assert r.success
        assert "1" in r.stdout

    def test_adql_accessor_prefers_session_specific_cache(self):
        from app.services.code_executor import execute_python
        from app.services.ai_tools import store_search_results, store_session_results

        store_search_results("latest_adql", [{"value": "global"}])
        store_session_results("latest_adql", "session-1", [{"value": "session"}])

        r = execute_python("rows = get_adql_results(); print(rows[0]['value'])", session_id="session-1")
        assert r.success
        assert "session" in r.stdout

    def test_generic_cached_results_accessor_is_exposed(self):
        from app.services.code_executor import execute_python
        from app.services.ai_tools import store_session_results

        store_session_results("latest_adql", "session-cache", [{"value": "session"}])

        r = execute_python(
            "rows = get_cached_results('latest_adql')\n"
            "print(rows[0]['value'])",
            session_id="session-cache",
        )
        assert r.success
        assert "session" in r.stdout

    def test_adql_result_sets_accessor_returns_history(self):
        from app.services.ai_tools import replace_adql_result_sets
        from app.services.code_executor import execute_python

        replace_adql_result_sets(
            "session-history",
            [
                {
                    "service": "gaia",
                    "query": "SELECT * FROM a",
                    "row_count": 2,
                    "columns": ["cluster", "bp_rp"],
                    "rows": [{"cluster": "A", "bp_rp": 0.1}, {"cluster": "A", "bp_rp": 0.2}],
                },
                {
                    "service": "gaia",
                    "query": "SELECT * FROM b",
                    "row_count": 1,
                    "columns": ["cluster", "bp_rp"],
                    "rows": [{"cluster": "B", "bp_rp": 0.3}],
                },
            ],
        )

        r = execute_python(
            "sets = get_adql_result_sets()\n"
            "print(len(sets))\n"
            "print(sets[0]['query'])\n"
            "print(get_adql_results()[0]['cluster'])",
            session_id="session-history",
        )
        assert r.success

    def test_subprocess_cache_context_filters_and_aliases_session_keys(self, monkeypatch):
        import time

        from app.services import ai_tools
        from app.services import code_executor

        monkeypatch.setattr(ai_tools, "_search_result_cache", {}, raising=False)
        monkeypatch.setattr(ai_tools, "_search_result_cache_owners", {}, raising=False)
        ai_tools.store_search_results("latest", [{"name": "global"}])
        ai_tools.store_session_results(
            "latest_adql", "dispatch-session", [{"value": "session"}]
        )
        ai_tools._search_result_cache["bad:dispatch-session"] = (lambda value: value, time.time())
        ai_tools._search_result_cache_owners["bad:dispatch-session"] = "dispatch-session"

        context = code_executor._collect_subprocess_cache_context("dispatch-session")

        assert "latest" not in context
        assert context["latest_adql:dispatch-session"] == [{"value": "session"}]
        assert context["latest_adql"] == [{"value": "session"}]
        assert "bad:dispatch-session" not in context

    def test_dispatch_subprocess_maps_raw_result(self, monkeypatch):
        from app.services import code_executor
        from app.services.sandbox import subprocess_backend
        from app.services.sandbox.base import SandboxResult

        captured = {}

        class _FakeBackend:
            def execute(self, code, *, timeout, memory_bytes, cache_context):
                captured.update({
                    "code": code,
                    "timeout": timeout,
                    "memory_bytes": memory_bytes,
                    "cache_context": cache_context,
                })
                return SandboxResult(
                    success=True,
                    stdout="ok",
                    stderr="warn",
                    error=None,
                    figures=["figure"],
                    variables={"answer": "42"},
                    variable_types={"answer": "int"},
                    backend="subprocess",
                    duration_ms=1,
                    exit_code=0,
                )

        monkeypatch.setattr(subprocess_backend, "SubprocessBackend", _FakeBackend)
        monkeypatch.setattr(
            code_executor,
            "_collect_subprocess_cache_context",
            lambda session_id: {"session_id": session_id},
        )

        result = code_executor._dispatch_subprocess("print(42)", timeout_seconds=3, session_id="dispatch-session")

        assert result is not None
        assert result.success is True
        assert result.stdout == "ok"
        assert result.stderr == "warn"
        assert result.figures == ["figure"]
        assert result.variables == {"answer": "42"}
        assert result.variable_types == {"answer": "int"}
        assert captured["code"] == "print(42)"
        assert captured["timeout"] == 3
        assert captured["cache_context"] == {"session_id": "dispatch-session"}

    def test_dispatch_subprocess_backend_exception_falls_back(self, monkeypatch):
        from app.services import code_executor
        from app.services.sandbox import subprocess_backend

        class _FailingBackend:
            def execute(self, *args, **kwargs):
                raise RuntimeError("backend unavailable")

        monkeypatch.setattr(subprocess_backend, "SubprocessBackend", _FailingBackend)

        assert code_executor._dispatch_subprocess("print(1)", timeout_seconds=1) is None


class TestInferenceRouting:
    def test_chat_provider_keys_accept_context_api_keys(self):
        from app.api.chat import _provider_api_keys

        keys = _provider_api_keys(
            {
                "api_keys": {
                    "openai": "sk-openai-test",
                    "anthropic": "sk-ant-test",
                }
            },
            None,
        )

        assert keys["openai"] == "sk-openai-test"
        assert keys["anthropic"] == "sk-ant-test"

    def test_chat_provider_keys_infer_openai_for_legacy_generic_key(self):
        from app.api.chat import _provider_api_keys

        keys = _provider_api_keys({"api_key": "sk-openai-test"}, None)

        assert keys["openai"] == "sk-openai-test"

    def test_preferred_backend_maps_supported_provider(self):
        from app.api.chat import _preferred_backend

        assert _preferred_backend({"api_provider": "openai"}) == "openai"
        assert _preferred_backend({"api_provider": "anthropic"}) == "claude"
        assert _preferred_backend({"api_provider": "google"}) is None

    def test_preferred_model_profile_validates_provider(self):
        from app.api.chat import _preferred_model_profile

        openai_profile = _preferred_model_profile({"api_provider": "openai", "model_profile": "openai:gpt-5.5"})
        assert openai_profile is not None
        assert openai_profile.id == "openai:gpt-5.5"
        assert openai_profile.resolved_model_id == "gpt-5.4"

        deepseek_profile = _preferred_model_profile({"api_provider": "deepseek", "model_profile": "openai:gpt-5.5"})
        assert deepseek_profile is not None
        assert deepseek_profile.id == "deepseek:v4-pro"

    def test_gpt55_alias_uses_admin_override(self, monkeypatch):
        from app.ai.model_profiles import resolve_model_profile

        monkeypatch.setenv("OPENAI_GPT55_MODEL", "gpt-5.5-real")
        profile = resolve_model_profile("openai", "openai:gpt-5.5")

        assert profile.api_ready is True
        assert profile.resolved_model_id == "gpt-5.5-real"

    def test_local_openai_cli_profile_uses_tool_bridge(self, monkeypatch):
        from app.ai.model_profiles import resolve_model_profile

        monkeypatch.setenv("OPENAI_CLI_MODEL", "gpt-5.4")
        profile = resolve_model_profile("local", "local:openai-cli")

        assert profile.id == "local:openai-cli"
        assert profile.provider == "local"
        assert profile.resolved_model_id == "gpt-5.4"
        assert profile.supports_tools is True

    def test_local_claude_cli_profile_uses_tool_bridge(self, monkeypatch):
        from app.ai.model_profiles import resolve_model_profile

        monkeypatch.setenv("CLAUDE_CLI_MODEL", "claude-sonnet-5")
        profile = resolve_model_profile("local", "claude-cli")

        assert profile.id == "local:claude-cli"
        assert profile.provider == "local"
        assert profile.resolved_model_id == "claude-sonnet-5"
        assert profile.supports_tools is True

    def test_local_kimi_cli_profile_uses_tool_bridge(self, monkeypatch):
        from app.ai.model_profiles import resolve_model_profile

        monkeypatch.setenv("KIMI_CLI_MODEL", "kimi-code/k3")
        profile = resolve_model_profile("local", "kimi-k3")

        assert profile.id == "local:kimi-cli"
        assert profile.provider == "local"
        assert profile.resolved_model_id == "kimi-code/k3"
        assert profile.supports_tools is True

    def test_subscription_cli_is_disabled_for_prod_alias(self, monkeypatch):
        from app.ai.inference_router import _local_cli_enabled

        monkeypatch.setenv("ENV", "prod")
        monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
        assert _local_cli_enabled("CLAUDE_CLI_ENABLED") is False

    @pytest.mark.asyncio
    async def test_local_backend_can_call_claude_cli(self, monkeypatch):
        from app.ai.inference_router import LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:claude-cli")
        captured: dict = {}

        class FakeProc:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            async def communicate(self, stdin):
                captured["stdin"] = stdin.decode("utf-8")
                return (
                    b'{"is_error":false,"result":"{\\"tool_calls\\":[{\\"name\\":\\"search_objects\\",\\"input\\":{\\"query\\":\\"M31\\"}}]}"}',
                    b"",
                )

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            captured["git_head"] = (Path(kwargs["cwd"]) / ".git" / "HEAD").read_text(
                encoding="utf-8"
            )
            return FakeProc(list(cmd))

        monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
        monkeypatch.setenv("CLAUDE_CLI_COMMAND", "claude")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
        monkeypatch.setenv("JWT_SECRET", "must-not-leak")
        monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "must-not-leak")
        monkeypatch.setattr("app.ai.inference_router.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr("app.ai.inference_router.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

        result = await backend.complete(
            [{"role": "user", "content": "hello"}],
            system="You are local.",
            tools=[{"name": "search_objects", "input_schema": {"type": "object"}}],
            model_profile=profile,
        )

        assert result["content"] == ""
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "search_objects"
        assert result["model_profile"] == "local:claude-cli"
        # Isolation contract: pure completion endpoint, no CLI tools, no
        # settings, no persisted session, empty temporary Git sandbox.
        cmd = captured["cmd"]
        assert "--print" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert cmd[cmd.index("--tools") + 1] == ""
        assert cmd[cmd.index("--setting-sources") + 1] == ""
        assert "--no-session-persistence" in cmd
        assert captured["kwargs"]["cwd"]
        assert captured["git_head"] == "ref: refs/heads/main\n"
        # Subscription auth contract: API-key variables never reach the CLI.
        child_env = captured["kwargs"]["env"]
        assert "ANTHROPIC_API_KEY" not in child_env
        assert "JWT_SECRET" not in child_env
        assert "S3_SECRET_ACCESS_KEY" not in child_env
        assert "JSON bridge" in captured["stdin"]
        assert "search_objects" in captured["stdin"]

    @pytest.mark.asyncio
    async def test_local_backend_rejects_claude_cli_error_envelope(self, monkeypatch):
        from app.ai.inference_router import InferenceError, LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        class FakeProc:
            returncode = 0

            async def communicate(self, stdin):
                return b'{"is_error":true,"result":"model unavailable"}', b""

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
        monkeypatch.setattr("app.ai.inference_router.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(
            "app.ai.inference_router.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        with pytest.raises(InferenceError, match="model unavailable"):
            await LocalBackend().complete(
                [{"role": "user", "content": "hello"}],
                model_profile=resolve_model_profile("local", "local:claude-cli"),
            )

    @pytest.mark.asyncio
    async def test_local_backend_can_call_kimi_cli(self, monkeypatch):
        from app.ai.inference_router import LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:kimi-cli")
        captured: dict = {}

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return (
                    b'{"role":"assistant","content":"{\\"tool_calls\\":[{\\"name\\":\\"search_objects\\",\\"input\\":{\\"query\\":\\"M31\\"}}]}"}\n'
                    b'{"role":"meta","type":"session.resume_hint","content":"ignored"}\n',
                    b"",
                )

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            return FakeProc()

        monkeypatch.setenv("KIMI_CLI_ENABLED", "1")
        monkeypatch.setenv("KIMI_CLI_COMMAND", "kimi")
        monkeypatch.setenv("MOONSHOT_API_KEY", "must-not-leak")
        monkeypatch.setenv("JWT_SECRET", "must-not-leak")
        monkeypatch.setattr(
            "app.ai.inference_router.shutil.which", lambda name: "/usr/bin/kimi"
        )
        monkeypatch.setattr(
            "app.ai.inference_router.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        result = await backend.complete(
            [{"role": "user", "content": "hello"}],
            system="You are local.",
            tools=[{"name": "search_objects", "input_schema": {"type": "object"}}],
            model_profile=profile,
        )

        assert result["content"] == ""
        assert result["tool_calls"][0]["name"] == "search_objects"
        assert result["tool_calls"][0]["input"] == {"query": "M31"}
        assert result["model_profile"] == "local:kimi-cli"
        cmd = captured["cmd"]
        assert cmd[cmd.index("--model") + 1] == "kimi-code/k3"
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--auto" not in cmd
        assert "--yolo" not in cmd
        prompt = cmd[cmd.index("--prompt") + 1]
        assert "Do not invoke Kimi Code built-in tools" in prompt
        assert "JSON bridge" in prompt
        assert "search_objects" in prompt
        skills_dir = Path(cmd[cmd.index("--skills-dir") + 1])
        assert skills_dir.parent == Path(captured["kwargs"]["cwd"])
        child_env = captured["kwargs"]["env"]
        assert child_env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
        assert "MOONSHOT_API_KEY" not in child_env
        assert "JWT_SECRET" not in child_env

    def test_kimi_cli_stream_surfaces_errors(self):
        from app.ai.inference_router import InferenceError, LocalBackend

        with pytest.raises(InferenceError, match="model unavailable"):
            LocalBackend._parse_kimi_cli_stream(
                '{"role":"error","content":"model unavailable"}'
            )

    @pytest.mark.asyncio
    async def test_local_claude_cli_disabled_flag_falls_back_to_local_model_error(self, monkeypatch):
        from app.ai.inference_router import InferenceError, LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:claude-cli")
        monkeypatch.delenv("CLAUDE_CLI_ENABLED", raising=False)
        monkeypatch.delenv("LOCAL_MODEL_ENABLED", raising=False)

        with pytest.raises(InferenceError):
            await backend.complete(
                [{"role": "user", "content": "hello"}],
                model_profile=profile,
            )

    def test_local_openai_cli_prompt_prioritizes_paper_workflow_tools(self):
        from app.ai.inference_router import _cli_tool_specs_for_prompt, _format_cli_prompt

        tools = [
            {"name": "generic_last", "input_schema": {"type": "object"}},
            {"name": "run_adql", "input_schema": {"type": "object"}},
            {"name": "fit_line_lfr", "input_schema": {"type": "object"}},
            {"name": "search_literature", "input_schema": {"type": "object"}},
            {"name": "extract_literature_tables", "input_schema": {"type": "object"}},
            {"name": "compare_luminosity_distances", "input_schema": {"type": "object"}},
            {"name": "demagnify_sample", "input_schema": {"type": "object"}},
        ]

        specs = _cli_tool_specs_for_prompt(tools)
        names = [spec["name"] for spec in specs]

        assert names[:5] == [
            "search_literature",
            "extract_literature_tables",
            "fit_line_lfr",
            "compare_luminosity_distances",
            "demagnify_sample",
        ]

        prompt = _format_cli_prompt(
            [{"role": "user", "content": "compile a [CII] LFR sample"}],
            tools=tools,
        )

        assert "Available tool names include: search_literature, extract_literature_tables" in prompt
        assert "already includes the paper/table/cosmology/LFR workflow tools" in prompt
        assert "Do not tell the user to enable these listed tools" in prompt

    def test_local_openai_cli_detects_backend_tool_list_self_block(self):
        from app.ai.inference_router import _cli_bridge_self_blocked

        content = (
            "The required literature-compilation, table-extraction, "
            "cosmology-comparison, demagnification, and Bayesian LFR-fitting "
            "tools are not available in this backend tool list. "
            "Suggested next step: Enable search_literature, "
            "extract_literature_tables, compare_luminosity_distances, "
            "demagnify_sample, and fit_line_lfr."
        )

        assert _cli_bridge_self_blocked(content)

    @pytest.mark.asyncio
    async def test_inference_router_skips_unavailable_local_backend(self, monkeypatch):
        from app.ai.inference_router import InferenceRouter

        router = InferenceRouter()
        router.agent_routing["orchestrator"] = "openai"

        calls: list[str] = []

        async def fake_openai_complete(*args, **kwargs):
            calls.append("openai")
            return {"content": "ok", "tool_calls": [], "usage": {}}

        async def fail_if_called(*args, **kwargs):
            raise AssertionError("Unavailable backend should not be called")

        async def no_op_log(*args, **kwargs):
            return None

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("PLATFORM_DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("SHARED_DEEPSEEK_API_KEY_ENABLED", raising=False)
        monkeypatch.delenv("LOCAL_MODEL_ENABLED", raising=False)
        monkeypatch.setattr(router.backends["openai"], "complete", fake_openai_complete)
        monkeypatch.setattr(router.backends["local"], "complete", fail_if_called)
        monkeypatch.setattr(router.backends["claude"], "complete", fail_if_called)
        monkeypatch.setattr(router.backends["deepseek"], "complete", fail_if_called)
        monkeypatch.setattr(router, "log_inference", no_op_log)

        result = await router.route(
            "orchestrator",
            [{"role": "user", "content": "hello"}],
            provider_api_keys={"openai": "sk-openai-test"},
        )

        assert result["content"] == "ok"
        assert calls == ["openai"]

    @pytest.mark.asyncio
    async def test_explicit_request_keys_cannot_fall_back_to_server_env(self, monkeypatch):
        from app.ai.inference_router import InferenceError, InferenceRouter

        router = InferenceRouter()
        calls: list[str] = []

        async def fail_openai(*args, **kwargs):
            calls.append("openai")
            raise RuntimeError("invalid user key")

        async def fail_if_platform_called(*args, **kwargs):
            raise AssertionError("server-funded fallback must stay unavailable")

        async def no_op_log(*args, **kwargs):
            return None

        monkeypatch.setenv("OPENAI_API_KEY", "sk-server-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-server-anthropic")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-server-deepseek")
        monkeypatch.delenv("LOCAL_MODEL_ENABLED", raising=False)
        monkeypatch.delenv("OPENAI_CLI_ENABLED", raising=False)
        monkeypatch.delenv("CLAUDE_CLI_ENABLED", raising=False)
        monkeypatch.delenv("KIMI_CLI_ENABLED", raising=False)
        monkeypatch.setattr(router.backends["openai"], "complete", fail_openai)
        monkeypatch.setattr(router.backends["claude"], "complete", fail_if_platform_called)
        monkeypatch.setattr(router.backends["deepseek"], "complete", fail_if_platform_called)
        monkeypatch.setattr(router.backends["local"], "complete", fail_if_platform_called)
        monkeypatch.setattr(router, "log_inference", no_op_log)

        with pytest.raises(InferenceError, match="All configured AI backends failed"):
            await router.route(
                "orchestrator",
                [{"role": "user", "content": "hello"}],
                provider_api_keys={"openai": "sk-invalid-user-key"},
                preferred_backend="openai",
            )

        assert calls == ["openai"]

    @pytest.mark.asyncio
    async def test_local_backend_can_call_openai_cli(self, monkeypatch):
        from app.ai.inference_router import LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:openai-cli")
        captured: dict = {}

        class FakeProc:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            async def communicate(self, stdin):
                captured["stdin"] = stdin.decode("utf-8")
                output_path = self.cmd[self.cmd.index("--output-last-message") + 1]
                Path(output_path).write_text(
                    '{"tool_calls":[{"name":"search_objects","input":{"query":"M31"}}]}',
                    encoding="utf-8",
                )
                return b"ignored stdout", b""

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            return FakeProc(list(cmd))

        monkeypatch.setenv("OPENAI_CLI_ENABLED", "1")
        monkeypatch.setenv("OPENAI_CLI_COMMAND", "codex")
        monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
        monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-leak")
        monkeypatch.setattr("app.ai.inference_router.shutil.which", lambda name: "/usr/bin/codex")
        monkeypatch.setattr("app.ai.inference_router.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

        result = await backend.complete(
            [{"role": "user", "content": "hello"}],
            system="You are local.",
            tools=[{"name": "search_objects", "input_schema": {"type": "object"}}],
            model_profile=profile,
        )

        assert result["content"] == ""
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "search_objects"
        assert result["tool_calls"][0]["input"] == {"query": "M31"}
        assert result["model_profile"] == "local:openai-cli"
        assert "--ephemeral" in captured["cmd"]
        assert "--sandbox" in captured["cmd"]
        assert "read-only" in captured["cmd"]
        assert captured["cmd"].count("--disable") == 2
        assert "shell_tool" in captured["cmd"]
        assert "unified_exec" in captured["cmd"]
        assert "--ignore-user-config" in captured["cmd"]
        assert "--ignore-rules" in captured["cmd"]
        assert "--skip-git-repo-check" in captured["cmd"]
        assert "--ask-for-approval" not in captured["cmd"]
        assert captured["kwargs"]["cwd"]
        assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]
        assert "DATABASE_URL" not in captured["kwargs"]["env"]
        assert "JSON bridge" in captured["stdin"]
        assert "ADQL/database access" in captured["stdin"]
        assert "literature/network search" in captured["stdin"]
        assert "search_objects" in captured["stdin"]

    @pytest.mark.asyncio
    async def test_local_cli_rejects_unadvertised_tool_call(self, monkeypatch):
        from app.ai.inference_router import InferenceError, LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:claude-cli")

        class FakeProc:
            returncode = 0

            async def communicate(self, stdin):
                return (
                    b'{"is_error":false,"result":"{\\"tool_calls\\":[{\\"name\\":\\"run_python\\",\\"input\\":{}}]}"}',
                    b"",
                )

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
        monkeypatch.setattr(
            "app.ai.inference_router.shutil.which", lambda name: "/usr/bin/claude"
        )
        monkeypatch.setattr(
            "app.ai.inference_router.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        with pytest.raises(InferenceError, match="unavailable tool.*run_python"):
            await backend.complete(
                [{"role": "user", "content": "read a host file"}],
                tools=[{"name": "search_objects", "input_schema": {"type": "object"}}],
                model_profile=profile,
            )

    @pytest.mark.asyncio
    async def test_local_openai_cli_bridge_accepts_database_tool_aliases(self, monkeypatch):
        from app.ai.inference_router import LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:openai-cli")

        class FakeProc:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            async def communicate(self, stdin):
                output_path = self.cmd[self.cmd.index("--output-last-message") + 1]
                Path(output_path).write_text(
                    '{"tool_calls":[{"tool":"run_adql","arguments":{"service":"gaia","query":"SELECT TOP 1 * FROM gaiadr3.gaia_source"}}]}',
                    encoding="utf-8",
                )
                return b"", b""

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return FakeProc(list(cmd))

        monkeypatch.setenv("OPENAI_CLI_ENABLED", "1")
        monkeypatch.setenv("OPENAI_CLI_MAX_TOOL_CALLS", "12")
        monkeypatch.setattr("app.ai.inference_router.shutil.which", lambda name: "/usr/bin/codex")
        monkeypatch.setattr("app.ai.inference_router.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

        result = await backend.complete(
            [{"role": "user", "content": "query Gaia"}],
            tools=[
                {"name": "run_adql", "input_schema": {"type": "object"}},
                {"name": "search_literature", "input_schema": {"type": "object"}},
            ],
            model_profile=profile,
        )

        assert result["content"] == ""
        assert result["stop_reason"] == "tool_calls"
        assert result["tool_calls"][0]["name"] == "run_adql"
        assert result["tool_calls"][0]["input"]["service"] == "gaia"

    @pytest.mark.asyncio
    async def test_local_openai_cli_retries_self_blocked_tool_refusal(self, monkeypatch):
        from app.ai.inference_router import LocalBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = LocalBackend()
        profile = resolve_model_profile("local", "local:openai-cli")
        attempts = {"count": 0, "prompts": []}

        class FakeProc:
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            async def communicate(self, stdin):
                attempts["count"] += 1
                attempts["prompts"].append(stdin.decode("utf-8"))
                output_path = self.cmd[self.cmd.index("--output-last-message") + 1]
                if attempts["count"] == 1:
                    Path(output_path).write_text(
                        "I cannot use these tools from this chat environment.",
                        encoding="utf-8",
                    )
                else:
                    Path(output_path).write_text(
                        '{"tool_calls":[{"name":"search_literature","input":{"query":"[CII] LFR sample"}}]}',
                        encoding="utf-8",
                    )
                return b"", b""

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return FakeProc(list(cmd))

        monkeypatch.setenv("OPENAI_CLI_ENABLED", "1")
        monkeypatch.setattr("app.ai.inference_router.shutil.which", lambda name: "/usr/bin/codex")
        monkeypatch.setattr("app.ai.inference_router.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

        result = await backend.complete(
            [{"role": "user", "content": "compile [CII] sample"}],
            tools=[{"name": "search_literature", "input_schema": {"type": "object"}}],
            model_profile=profile,
        )

        assert attempts["count"] == 2
        assert "protocol_correction" in attempts["prompts"][1]
        assert result["content"] == ""
        assert result["stop_reason"] == "tool_calls"
        assert result["tool_calls"][0]["name"] == "search_literature"
        assert result["tool_calls"][0]["input"]["query"] == "[CII] LFR sample"

    @pytest.mark.asyncio
    async def test_openai_backend_extracts_content_array(self, monkeypatch):
        from app.ai.inference_router import OpenAIBackend

        backend = OpenAIBackend()

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": [
                                    {"type": "output_text", "text": "Hello from OpenAI"},
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

        result = await backend.complete(
            [{"role": "user", "content": "hello"}],
            provider_api_keys={"openai": "sk-openai-test"},
        )

        assert result["content"] == "Hello from OpenAI"
        assert result["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_openai_backend_supports_legacy_function_call(self, monkeypatch):
        from app.ai.inference_router import OpenAIBackend

        backend = OpenAIBackend()

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "finish_reason": "function_call",
                            "message": {
                                "content": None,
                                "function_call": {
                                    "name": "search_objects",
                                    "arguments": "{\"query\": \"M31\"}",
                                },
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

        result = await backend.complete(
            [{"role": "user", "content": "find M31"}],
            tools=[{"name": "search_objects", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}],
            provider_api_keys={"openai": "sk-openai-test"},
        )

        assert result["tool_calls"][0]["name"] == "search_objects"
        assert result["tool_calls"][0]["input"]["query"] == "M31"

    @pytest.mark.asyncio
    async def test_openai_gpt55_profile_uses_responses_api(self, monkeypatch):
        from app.ai.inference_router import OpenAIBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = OpenAIBackend()
        captured: dict = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Hello from GPT-5.5 alias"}],
                        }
                    ],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                    "status": "completed",
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, *, json=None, headers=None):
                captured["url"] = url
                captured["payload"] = json
                return FakeResponse()

        monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

        profile = resolve_model_profile("openai", "openai:gpt-5.5")
        result = await backend.complete(
            [{"role": "user", "content": "hello"}],
            provider_api_keys={"openai": "sk-openai-test"},
            model_profile=profile,
        )

        assert captured["url"].endswith("/responses")
        assert captured["payload"]["model"] == "gpt-5.4"
        assert result["content"] == "Hello from GPT-5.5 alias"
        assert result["model_profile"] == "openai:gpt-5.5"

    @pytest.mark.asyncio
    async def test_deepseek_v4_profiles_set_model_payload(self, monkeypatch):
        from app.ai.inference_router import DeepSeekBackend
        from app.ai.model_profiles import resolve_model_profile

        backend = DeepSeekBackend()
        captured: list[dict] = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *args, **kwargs):
                captured.append(kwargs["json"])
                return FakeResponse()

        monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

        pro = resolve_model_profile("deepseek", "deepseek:v4-pro")
        await backend.complete(
            [{"role": "user", "content": "hello"}],
            provider_api_keys={"deepseek": "sk-deepseek-test"},
            model_profile=pro,
        )
        assert captured[-1]["model"] == "deepseek-v4-pro"
        assert captured[-1]["reasoning_effort"] == "high"
        assert captured[-1]["thinking"] == {"type": "enabled"}

        flash = resolve_model_profile("deepseek", "deepseek:v4-flash")
        await backend.complete(
            [{"role": "user", "content": "hello"}],
            provider_api_keys={"deepseek": "sk-deepseek-test"},
            model_profile=flash,
        )
        assert captured[-1]["model"] == "deepseek-v4-flash"
        assert "reasoning_effort" not in captured[-1]

    def test_adql_results_unwraps_single_resultset_wrapper(self):
        from app.services.ai_tools import store_session_results
        from app.services.code_executor import execute_python

        store_session_results(
            "latest_adql",
            "wrapped",
            [
                {
                    "service": "gaia",
                    "query": "SELECT * FROM wrapped",
                    "columns": ["cluster"],
                    "rows": [{"cluster": "Pleiades"}],
                }
            ],
        )

        r = execute_python("rows = get_adql_results(); print(rows[0]['cluster'])", session_id="wrapped")
        assert r.success
        assert "Pleiades" in r.stdout

    def test_build_adql_result_set_derives_color_and_absolute_magnitude(self):
        from app.services.ai_tools import build_adql_result_set

        result_set = build_adql_result_set(
            service="gaia",
            query="SELECT phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, parallax FROM gaiadr3.gaia_source",
            columns=["phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag", "parallax"],
            data={
                "phot_g_mean_mag": [10.0],
                "phot_bp_mean_mag": [11.5],
                "phot_rp_mean_mag": [10.0],
                "parallax": [10.0],
            },
            row_count=1,
        )

        row = result_set["rows"][0]
        assert row["bp_rp"] == 1.5
        assert "abs_g_mag" in row
        assert "bp_rp" in result_set["columns"]
        assert "abs_g_mag" in result_set["columns"]

    def test_replay_session_history_restores_variables(self):
        from app.services.code_executor import clear_session_vars, execute_python, replay_session_history

        clear_session_vars("replay-test")
        replay_session_history(
            "replay-test",
            [
                "import numpy as np\nvalues = np.array([1, 2, 3])",
                "mean_value = float(values.mean())",
            ],
        )

        r = execute_python("print(mean_value)", session_id="replay-test")
        assert r.success
        assert "2.0" in r.stdout

    def test_plot_hr_diagram_accepts_existing_axes(self):
        from app.services.astro_analysis import plot_hr_diagram, pub_figure

        fig, ax = pub_figure()
        out_fig, out_ax = plot_hr_diagram(
            [0.5, 1.0, 1.5],
            [10.0, 11.0, 12.0],
            ax=ax,
            title="Existing Axes",
        )
        assert out_fig is fig
        assert out_ax is ax

    def test_complex_objects_persist_between_runs(self):
        from app.services.code_executor import clear_session_vars, execute_python

        clear_session_vars("persist-test")
        r1 = execute_python(
            "flat_lcdm = FlatLambdaCDM(H0=70, Om0=0.3)\nfrom scipy.optimize import curve_fit",
            session_id="persist-test",
        )
        assert r1.success

        r2 = execute_python(
            'print(round(flat_lcdm.H0.value))\nprint(callable(curve_fit))',
            session_id="persist-test",
        )
        assert r2.success
        assert "70" in r2.stdout
        assert "True" in r2.stdout

    def test_available_functions_helper(self):
        from app.services.code_executor import execute_python

        r = execute_python(
            'info = available_functions()\n'
            'print(info["compute_absolute_magnitude"]["signature"])\n'
            'print(info["compute_luminosity_distance"]["summary"])'
        )
        assert r.success
        assert "distance_pc" in r.stdout
        assert "luminosity distance" in r.stdout.lower()

    def test_variable_types_are_reported(self):
        from app.services.code_executor import execute_python

        r = execute_python("values = [1, 2, 3]\nanswer = 42")
        assert r.success
        assert r.variable_types["values"] == "list"
        assert r.variable_types["answer"] == "int"


class TestSearchErrorHelpers:
    def test_ned_timeout_budget_is_extended(self):
        from app.api.data import _search_timeout_for_source

        assert _search_timeout_for_source("ned") == 75.0
        assert _search_timeout_for_source("simbad") == 20.0

    def test_ned_timeout_message_mentions_slow_service(self):
        from app.api.data import _build_source_error_name

        msg = _build_source_error_name("ned", "timeout", TimeoutError())
        assert "responding slowly" in msg
        assert "narrow the search" in msg
        assert ": ." not in msg

    def test_sdss_timeout_message_mentions_skyserver(self):
        from app.api.data import _build_source_error_name

        msg = _build_source_error_name("sdss", "timeout", TimeoutError())
        assert "SkyServer" in msg
        assert "narrow the search radius" in msg


class TestAITools:
    """Test the AI tool definitions and execution."""

    def test_tool_count(self):
        from app.services.ai_tools import TOOLS
        assert len(TOOLS) >= 28

    def test_tool_names(self):
        from app.services.ai_tools import TOOLS
        names = {t["name"] for t in TOOLS}
        assert "search_objects" in names
        assert "run_python" in names
        assert "run_pipeline" in names
        assert "read_arxiv_paper" in names
        assert "validate_analysis" in names
        assert "generate_paper_draft" in names
        assert "reduce_ccd_image" in names
        assert "solve_astrometry" in names
        assert "extract_photometry" in names
        assert "fit_cosmology_mcmc" in names
        assert "run_cobaya_cosmology" in names
        assert "get_cosmology_run_status" in names
        assert "run_cosmology_likelihood_chain" in names
        assert "run_cosmology_robustness_matrix" in names

    @pytest.mark.asyncio
    async def test_generate_pipeline(self):
        from app.services.ai_tools import execute_tool
        r = await execute_tool("generate_pipeline", {
            "name": "Test",
            "nodes": [{"id": "n1", "type": "LoadData"}],
            "edges": [],
        })
        assert r["status"] == "created"

    @pytest.mark.asyncio
    async def test_get_cached_results_empty(self):
        from app.services.ai_tools import execute_tool
        r = await execute_tool("get_last_search_results", {})
        assert "results" in r

    @pytest.mark.asyncio
    async def test_run_python_uses_explicit_session_id(self):
        from app.services.ai_tools import execute_tool
        from app.services.code_executor import clear_session_vars

        clear_session_vars("sess-a")
        clear_session_vars("sess-b")

        r1 = await execute_tool("run_python", {"code": "x = 42"}, python_session_id="sess-a")
        r2 = await execute_tool("run_python", {"code": "print('x' in globals())"}, python_session_id="sess-b")
        r3 = await execute_tool("run_python", {"code": "print(x)"}, python_session_id="sess-a")

        assert r1["success"] is True
        assert r2["stdout"].strip() == "False"
        assert r3["stdout"].strip() == "42"

    @pytest.mark.asyncio
    async def test_run_python_auto_fixes_float_integer_formatting(self):
        from app.services.ai_tools import execute_tool

        result = await execute_tool(
            "run_python",
            {"code": 'value = 3.8\nprint(f"{value:d}")'},
            python_session_id="format-fix",
        )

        assert result["success"] is True
        assert result.get("auto_fix_note")
        assert result["stdout"].strip() == "4"


class TestExportHelpers:
    @pytest.mark.asyncio
    async def test_chat_notebook_extracts_python_from_params(self):
        from app.api.export import ChatToNotebookRequest, export_chat_as_notebook
        import json

        response = await export_chat_as_notebook(ChatToNotebookRequest(
            messages=[
                {
                    "role": "assistant",
                    "content": "I ran code",
                    "actions": [{"action": "run_python", "params": {"code": "print(123)"}}],
                }
            ]
        ))

        body = ""
        async for chunk in response.body_iterator:
            body += chunk
        notebook = json.loads(body)
        code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        assert any("print(123)" in "".join(cell["source"]) for cell in code_cells)


class TestPaperDraftHelpers:
    @pytest.mark.asyncio
    async def test_generate_paper_draft_and_validation(self, db_session):
        import uuid

        from app.auth import hash_password
        from app.models.schemas import ChatSession, User
        from app.services.analysis_validator import validate_analysis
        from app.services.paper_generator import generate_paper_draft

        user = User(
            id=uuid.uuid4(),
            username="paperuser",
            email="paperuser@example.com",
            password_hash=hash_password("securepassword123"),
        )
        db_session.add(user)
        await db_session.commit()

        session = ChatSession(
            id=uuid.uuid4(),
            user_id=user.id,
            title="Paper Session",
            messages=[
                {"role": "user", "content": "Analyze M31."},
                {
                    "role": "assistant",
                    "content": "Here is the result.",
                    "actions": [
                        {
                            "action": "search",
                            "query": "M31",
                            "sources": ["simbad"],
                            "tool_result": [{"name": "M31", "ra": 10.684, "dec": 41.269}],
                        }
                    ],
                },
            ],
        )
        db_session.add(session)
        await db_session.commit()

        validation = await validate_analysis(
            str(session.id), db_session, owner_id=str(user.id)
        )
        assert validation["overall_status"] in {"PASS", "WARN", "FAIL"}

        generated = await generate_paper_draft(str(session.id), "aastex", db_session)
        assert "paper_json" in generated
        assert "\\documentclass" in generated["latex_source"]
        assert "Reproducibility Appendix" in generated["latex_source"]


class TestPipelineBatch:
    """Test batch pipeline execution."""

    def test_batch_run_request_model(self):
        from app.api.pipeline import BatchRunRequest
        req = BatchRunRequest(
            dag={"nodes": [], "edges": []},
            input_data_ids=["test.fits"],
        )
        assert len(req.input_data_ids) == 1


class TestRedshiftVote:
    """Test the multi-method redshift voting."""

    def test_vote_method(self):
        from app.pipeline.nodes.redshift import redshift_estimate
        wave = np.linspace(4000, 7000, 300)
        flux = np.ones(300) * 50 + 20 * np.exp(-0.5 * ((wave - 6563) / 5) ** 2)
        r = redshift_estimate(
            {"data": {"wavelength": wave.tolist(), "flux": flux.tolist()}},
            {"method": "vote"},
        )
        assert "vote" in r["redshift_result"]["method"]

    def test_unknown_method(self):
        from app.pipeline.nodes.redshift import redshift_estimate
        with pytest.raises(ValueError, match="Unknown method"):
            redshift_estimate(
                {"data": {"wavelength": [1] * 20, "flux": [1] * 20}},
                {"method": "bogus"},
            )


class TestDedup:
    """Test search result deduplication."""

    def test_dedup_merges_nearby(self):
        from app.api.data import _dedup_by_position, SearchResult
        r1 = SearchResult(source="sdss", object_id="1", name="a", ra=10.0, dec=20.0)
        r2 = SearchResult(source="gaia", object_id="2", name="b", ra=10.0001, dec=20.0001, redshift=0.5)
        r3 = SearchResult(source="simbad", object_id="3", name="c", ra=50.0, dec=60.0)
        deduped = _dedup_by_position([r1, r2, r3])
        assert len(deduped) == 2
        assert deduped[0].redshift == 0.5  # best kept

    def test_dedup_no_merge_far(self):
        from app.api.data import _dedup_by_position, SearchResult
        r1 = SearchResult(source="a", object_id="1", name="x", ra=10.0, dec=20.0)
        r2 = SearchResult(source="b", object_id="2", name="y", ra=11.0, dec=21.0)
        deduped = _dedup_by_position([r1, r2])
        assert len(deduped) == 2


class TestCosmologyDirectRouting:
    """Regression tests for deterministic cosmology tool routing."""

    def test_simple_hubble_tension_still_routes_to_preset_comparison(self):
        from app.api.chat import _cosmology_direct_route_from_prompt

        calls = _cosmology_direct_route_from_prompt(
            "Compare Planck and SH0ES for the Hubble tension."
        )

        assert calls
        assert calls[0]["name"] == "compare_luminosity_distances"
        assert calls[0]["input"]["target_cosmology"] == "riess22_shoes"
        assert calls[0]["input"]["baseline_cosmology"] == "planck18"
        assert calls[0]["input"]["comparison_mode"] == "h0_anchors"

        registered_anchor_calls = _cosmology_direct_route_from_prompt(
            "Quote and compare the registered Planck 2018 CMB-only and "
            "Riess et al. 2022 SH0ES H0 anchors. Do not run a likelihood."
        )
        assert registered_anchor_calls
        assert registered_anchor_calls[0]["name"] == "compare_luminosity_distances"
        assert registered_anchor_calls[0]["input"]["comparison_mode"] == "h0_anchors"

    def test_extended_fisher_hubble_tension_request_does_not_force_dl_tool(self):
        from app.api.chat import _cosmology_direct_route_from_prompt

        calls = _cosmology_direct_route_from_prompt(
            "I want to analyze the Hubble tension geometrically in an extended "
            "constant-w dark-energy model, separating parameter shifts from "
            "constraint-direction curvature. Please identify whether Fisher/covariance "
            "information from CMB, BAO, and H0-prior data is available."
        )

        assert calls is None
