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
        assert len(TOOLS) == 15

    def test_tool_names(self):
        from app.services.ai_tools import TOOLS
        names = {t["name"] for t in TOOLS}
        assert "search_objects" in names
        assert "run_python" in names
        assert "run_pipeline" in names
        assert "read_arxiv_paper" in names

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
