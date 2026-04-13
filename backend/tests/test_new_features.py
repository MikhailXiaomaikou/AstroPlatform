"""Tests for new features: analysis toolkit, code executor, AI tools, pipeline batch."""

import pytest
import numpy as np


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
        assert "not allowed" in r.error

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
        from app.services.ai_tools import store_search_results

        store_search_results("latest_adql", [{"value": "global"}])
        store_search_results("latest_adql:session-1", [{"value": "session"}])

        r = execute_python("rows = get_adql_results(); print(rows[0]['value'])", session_id="session-1")
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

    def test_adql_results_unwraps_single_resultset_wrapper(self):
        from app.services.ai_tools import store_search_results
        from app.services.code_executor import execute_python

        store_search_results(
            "latest_adql:wrapped",
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
        assert len(TOOLS) == 28

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

        validation = await validate_analysis(str(session.id), db_session)
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
