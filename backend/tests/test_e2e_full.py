"""End-to-end AI integration tests.

Every test calls execute_tool() — the exact same entry point the AI assistant uses.
Tests that need network access are marked @pytest.mark.network.
All tests are marked @pytest.mark.integration (skipped in default CI).

Run: python -m pytest tests/test_e2e_full.py -v -m integration --tb=short
"""

import math
import numpy as np
import pytest

pytestmark = pytest.mark.integration


# ── Helper ──

async def call_tool(name: str, inp: dict, **kw) -> dict:
    """Call execute_tool and return result. Raises on error."""
    from app.services.ai_tools import execute_tool
    result = await execute_tool(name, inp, **kw)
    return result


def assert_no_error(result: dict, tool_name: str = ""):
    """Assert the tool result does not contain an error."""
    err = result.get("error")
    if err:
        pytest.fail(f"{tool_name} returned error: {err}")


async def create_active_user() -> str:
    """Create a real ACTIVE user row and return its id as a string.

    execute_tool() checks account_runtime_is_active() before every run:
    a user_id without an ACTIVE users row (or one that is not a UUID at
    all) is treated as deletion-requested and the tool call is refused.
    Synthetic ids like "test-user" therefore cannot exercise user-scoped
    tools; tests must own a real account like production callers do.
    """
    import uuid
    from app.models.database import engine
    from app.models.schemas import Base, User
    from sqlalchemy.ext.asyncio import AsyncSession as AS
    from sqlalchemy.orm import sessionmaker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session_factory = sessionmaker(engine, class_=AS, expire_on_commit=False)
    uid = uuid.uuid4()
    async with async_session_factory() as db:
        db.add(User(
            id=uid,
            username=f"e2e-user-{uid.hex[:12]}",
            email=f"e2e-{uid.hex[:12]}@example.com",
            password_hash="e2e-not-a-real-hash",
            account_status="ACTIVE",
        ))
        await db.commit()
    return str(uid)


# ═══════════════════════════════════════
# 1. Data Query Tools
# ═══════════════════════════════════════

class TestDataQueryTools:

    @pytest.mark.network
    async def test_search_objects_simbad(self):
        r = await call_tool("search_objects", {"query": "Sirius", "sources": ["simbad"]})
        assert_no_error(r, "search_objects")
        results = r.get("results", [])
        assert len(results) > 0, "No results for Sirius"

    @pytest.mark.network
    async def test_run_adql_gaia(self):
        r = await call_tool("run_adql", {
            "query": "SELECT TOP 5 source_id, ra, dec, phot_g_mean_mag, bp_rp, parallax FROM gaiadr3.gaia_source WHERE source_id = 5853498713190525696",
            "service": "gaia",
        })
        assert_no_error(r, "run_adql gaia")
        assert r.get("row_count", 0) > 0, "ADQL returned 0 rows"
        assert "source_id" in r.get("columns", []), f"Missing source_id in columns: {r.get('columns')}"

    @pytest.mark.network
    async def test_run_adql_simbad(self):
        r = await call_tool("run_adql", {
            "query": "SELECT TOP 5 main_id, ra, dec, otype FROM basic WHERE main_id = 'M31'",
            "service": "simbad",
        })
        assert_no_error(r, "run_adql simbad")
        assert r.get("row_count", 0) > 0

    @pytest.mark.network
    async def test_get_object_info(self):
        r = await call_tool("get_object_info", {"name": "M31"})
        assert_no_error(r, "get_object_info")
        assert r.get("ra") is not None or r.get("coordinates") is not None

    @pytest.mark.network
    async def test_batch_search(self):
        r = await call_tool("batch_object_search", {"targets": ["M31", "M42"], "sources": ["simbad"]})
        assert_no_error(r, "batch_object_search")

    @pytest.mark.network
    async def test_search_literature(self):
        r = await call_tool("search_literature", {"query": "NGC 1647 open cluster"})
        assert_no_error(r, "search_literature")

    @pytest.mark.network
    async def test_get_dossier(self):
        r = await call_tool("get_object_dossier", {"ra": 10.6847, "dec": 41.269})
        assert_no_error(r, "get_object_dossier")

    async def test_get_cached_results(self):
        r = await call_tool("get_last_search_results", {})
        # May be empty if no prior search, but should not error
        assert isinstance(r, dict)


# ═══════════════════════════════════════
# 2. Scientific Analysis Tools
# ═══════════════════════════════════════

class TestScienceTools:

    @pytest.mark.network
    @pytest.mark.slow
    @pytest.mark.timeout(300)
    async def test_fit_isochrone_direct(self):
        """Fit isochrone with synthetic CMD data (needs PARSEC API, can be slow)."""
        np.random.seed(42)
        n = 50
        bp_rp = np.linspace(0.3, 2.5, n).tolist()
        abs_mag = [0.5 + 3.0 * c + np.random.normal(0, 0.1) for c in bp_rp]
        r = await call_tool("fit_isochrone", {"bp_rp": bp_rp, "abs_mag": abs_mag, "method": "grid"})
        assert_no_error(r, "fit_isochrone direct")
        assert "best_log_age" in r, f"Missing best_log_age in {list(r.keys())}"

    async def test_estimate_photo_z(self):
        # Demo 7-template path: test_estimate_photo_z_pro below covers
        # method="enhanced_template" (the default). Asking for the demo path
        # here exercises a distinct code branch and finishes in <5 s instead
        # of hitting the prod-side 60 s wait_for that wraps enhanced.
        r = await call_tool("estimate_photo_z", {
            "magnitudes": {"g": 22.1, "r": 21.5, "i": 20.8, "z": 20.3},
            "mag_errors": {"g": 0.05, "r": 0.04, "i": 0.04, "z": 0.05},
            "method": "template",
            "allow_demo": True,
        })
        assert_no_error(r, "estimate_photo_z")

    @pytest.mark.timeout(120)
    async def test_estimate_photo_z_pro(self):
        r = await call_tool("estimate_photo_z_pro", {
            "magnitudes": {"u": 23.0, "g": 22.1, "r": 21.5, "i": 20.8, "z": 20.3},
            "mag_errors": {"u": 0.1, "g": 0.05, "r": 0.04, "i": 0.04, "z": 0.05},
        })
        assert_no_error(r, "estimate_photo_z_pro")

    async def test_classify_transient(self):
        np.random.seed(0)
        t = np.linspace(0, 60, 30).tolist()
        mag = [18.0 - 2.5 * math.exp(-((ti - 15) ** 2) / 50) + np.random.normal(0, 0.05) for ti in t]
        r = await call_tool("classify_transient", {"times": t, "magnitudes": mag, "band": "r"})
        assert_no_error(r, "classify_transient")

    async def test_classify_transient_spectrum(self):
        wave = np.linspace(3500, 9000, 200).tolist()
        flux = [1.0 + 0.3 * math.exp(-0.5 * ((w - 6150) / 80) ** 2) for w in wave]
        r = await call_tool("classify_transient_spectrum", {"wavelength": wave, "flux": flux})
        assert_no_error(r, "classify_transient_spectrum")

    async def test_gp_detrend(self):
        np.random.seed(1)
        t = np.linspace(0, 10, 100).tolist()
        flux = [1.0 + 0.01 * ti + np.random.normal(0, 0.005) for ti in t]
        flux_err = [0.005] * len(t)
        r = await call_tool("gp_detrend_lightcurve", {"time": t, "flux": flux, "flux_err": flux_err})
        assert_no_error(r, "gp_detrend")

    async def test_detect_flares(self):
        np.random.seed(2)
        t = np.linspace(0, 5, 200).tolist()
        flux = [1.0 + np.random.normal(0, 0.002) for _ in t]
        flux[100] = 1.05  # inject flare
        flux[101] = 1.08
        flux[102] = 1.04
        r = await call_tool("detect_stellar_flares", {"time": t, "flux": flux, "nsigma": 3.0})
        assert_no_error(r, "detect_flares")

    async def test_transit_bls(self):
        np.random.seed(3)
        t = np.linspace(0, 30, 500).tolist()
        period = 3.5
        flux = []
        for ti in t:
            phase = (ti % period) / period
            f = 1.0 - (0.01 if 0.48 < phase < 0.52 else 0.0) + np.random.normal(0, 0.002)
            flux.append(f)
        r = await call_tool("transit_search_bls", {"time": t, "flux": flux, "period_min": 1.0, "period_max": 10.0})
        assert_no_error(r, "transit_bls")

    async def test_fit_transit(self):
        np.random.seed(4)
        t = np.linspace(0, 5, 200).tolist()
        flux = [1.0 + np.random.normal(0, 0.001) for _ in t]
        for i, ti in enumerate(t):
            if abs(ti - 2.5) < 0.15:
                flux[i] -= 0.01
        r = await call_tool("fit_transit_model", {"time": t, "flux": flux, "period": 5.0, "t0": 2.5, "rp_rs": 0.1})
        assert_no_error(r, "fit_transit")

    async def test_radio_analysis(self):
        r = await call_tool("radio_analysis", {
            "operation": "spectral_index",
            "flux_1_mJy": 100.0, "freq_1_MHz": 1400,
            "flux_2_mJy": 45.0, "freq_2_MHz": 4850,
        })
        assert_no_error(r, "radio_analysis")

    @pytest.mark.network
    async def test_crossmatch_catalogs(self):
        r = await call_tool("crossmatch_catalogs", {
            "table1_query": "SELECT TOP 10 source_id, ra, dec FROM gaiadr3.gaia_source WHERE source_id BETWEEN 5853498713190525696 AND 5853498713190525700",
            "table2_query": "SELECT TOP 10 source_id, ra, dec FROM gaiadr3.gaia_source WHERE source_id BETWEEN 5853498713190525696 AND 5853498713190525700",
            "radius_arcsec": 1.0,
        })
        assert_no_error(r, "crossmatch_catalogs")


# ═══════════════════════════════════════
# 3. Python Sandbox
# ═══════════════════════════════════════

class TestPythonSandbox:

    async def test_basic_print(self):
        r = await call_tool("run_python", {"code": "print(1 + 1)"})
        assert r.get("success") is True, f"run_python failed: {r}"
        assert "2" in r.get("stdout", "")

    async def test_numpy_import(self):
        r = await call_tool("run_python", {"code": "import numpy as np; print(np.mean([1,2,3]))"})
        assert r.get("success") is True, f"numpy import failed: {r}"

    async def test_sklearn_import(self):
        r = await call_tool("run_python", {"code": "from sklearn.cluster import DBSCAN; print('sklearn ok')"})
        assert r.get("success") is True, f"sklearn import failed: {r}"

    async def test_astropy_import(self):
        r = await call_tool("run_python", {"code": "from astropy.coordinates import SkyCoord; print('astropy ok')"})
        assert r.get("success") is True, f"astropy import failed: {r}"

    async def test_blocked_os(self):
        r = await call_tool("run_python", {"code": "import os; os.listdir('/')"})
        assert r.get("success") is not True, "os import should be blocked"


# ═══════════════════════════════════════
# 4. Pipeline & Workflow
# ═══════════════════════════════════════

class TestPipelineTools:

    async def test_generate_pipeline(self):
        r = await call_tool("generate_pipeline", {
            "name": "test_pipeline",
            "description": "Test",
            "dag": {
                "nodes": [
                    {"id": "n1", "type": "LoadData", "params": {}},
                    {"id": "n2", "type": "Plot", "params": {"chart_type": "scatter"}},
                ],
                "edges": [{"source": "n1", "target": "n2"}],
            },
        })
        assert_no_error(r, "generate_pipeline")

    async def test_research_workflow(self):
        r = await call_tool("research_workflow", {
            "hypothesis": "NGC 1647 age is approximately 200 Myr",
            "steps": ["Query Gaia data", "Select members", "Fit isochrone"],
        })
        assert_no_error(r, "research_workflow")

    async def test_generate_proposal(self):
        r = await call_tool("generate_proposal", {
            "target": "NGC 1647",
            "instrument": "VLT/FLAMES",
            "science_goal": "Measure lithium abundances",
        })
        assert_no_error(r, "generate_proposal")

    async def test_sensitivity_analysis(self):
        r = await call_tool("sensitivity_analysis", {
            "parameters": {"distance": 450, "age_myr": 200, "extinction_av": 1.0},
            "ranges": {"distance": [400, 500], "age_myr": [150, 250]},
        })
        assert_no_error(r, "sensitivity_analysis")


# ═══════════════════════════════════════
# 5. Literature & Export
# ═══════════════════════════════════════

class TestLiteratureExport:

    @pytest.mark.network
    async def test_read_arxiv(self):
        r = await call_tool("read_arxiv_paper", {"arxiv_id": "2301.12345"})
        # May fail for invalid arxiv_id, but should not crash
        assert isinstance(r, dict)

    @pytest.mark.network
    async def test_literature_review(self):
        r = await call_tool("literature_review", {"topic": "open cluster ages"})
        assert_no_error(r, "literature_review")

    async def test_export_results(self):
        r = await call_tool("export_results", {"run_id": "nonexistent", "format": "csv"})
        # May return error for missing run, but should not crash
        assert isinstance(r, dict)

    async def test_workspace_export(self):
        r = await call_tool("workspace_export", {"format": "csv"})
        assert isinstance(r, dict)

    async def test_get_provenance(self):
        r = await call_tool("get_provenance", {"entity_id": "test-entity"})
        assert isinstance(r, dict)


# ═══════════════════════════════════════
# 6. Session & Paper
# ═══════════════════════════════════════

class TestSessionPaper:

    async def _create_session(self) -> tuple[str, str]:
        """Create a real ChatSession in DB; return (session_id, user_id).

        The session-paper tools require an authenticated owner — they call
        `_require_tool_session_owner` which short-circuits with a sign-in error
        when `user_id` is missing. Tests must thread the owner's id through.
        """
        import uuid
        from app.models.database import engine
        from app.models.schemas import Base, ChatSession
        from sqlalchemy.ext.asyncio import AsyncSession as AS
        from sqlalchemy.orm import sessionmaker
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async_session_factory = sessionmaker(engine, class_=AS, expire_on_commit=False)
        sid = uuid.uuid4()
        uid = uuid.UUID(await create_active_user())
        async with async_session_factory() as db:
            session = ChatSession(
                id=sid,
                user_id=uid,
                title="E2E Test Session",
                messages=[
                    {"role": "user", "content": "Analyze NGC 1647"},
                    {"role": "assistant", "content": "I'll query Gaia DR3 for NGC 1647 members."},
                ],
            )
            db.add(session)
            await db.commit()
        return str(sid), str(uid)

    async def test_validate_analysis(self):
        sid, uid = await self._create_session()
        r = await call_tool("validate_analysis", {"session_id": sid}, user_id=uid)
        assert_no_error(r, "validate_analysis")

    async def test_generate_paper_draft(self):
        sid, uid = await self._create_session()
        r = await call_tool("generate_paper_draft", {}, chat_session_id=sid, user_id=uid)
        assert_no_error(r, "generate_paper_draft")

    async def test_full_research_report(self):
        sid, uid = await self._create_session()
        r = await call_tool("full_research_report", {"session_id": sid}, user_id=uid)
        # May have partial errors but should not crash
        assert isinstance(r, dict)


# ═══════════════════════════════════════
# 7. Spectrum / Image / VO
# ═══════════════════════════════════════

class TestSpectrumImageVO:

    async def test_analyze_spectrum_pro(self):
        """analyze_spectrum_pro requires a FITS file — verify error handling for missing file."""
        r = await call_tool("analyze_spectrum_pro", {"fits_path": "nonexistent.fits"})
        # Should return a clear error, not crash
        assert isinstance(r, dict)
        assert "error" in r
        assert "not found" in r["error"].lower() or "required" in r["error"].lower()

    @pytest.mark.network
    @pytest.mark.timeout(60)
    async def test_query_vo_discover(self):
        try:
            r = await call_tool("query_vo_service", {"protocol": "discover", "keyword": "galaxy", "service_type": "tap"})
            assert_no_error(r, "query_vo_discover")
        except Exception as e:
            if "timeout" in str(e).lower() or "connection" in str(e).lower():
                pytest.skip(f"VO registry unavailable: {e}")
            raise

    @pytest.mark.network
    async def test_cross_wavelength(self):
        r = await call_tool("analyze_cross_wavelength", {"ra": 10.68, "dec": 41.27, "radius": 0.01})
        assert_no_error(r, "cross_wavelength")

    async def test_followup_recommendation(self):
        r = await call_tool("get_followup_recommendation", {
            "ra": 180.0, "dec": 45.0, "magnitude": 17.5, "classification": "SN Ia",
        })
        assert_no_error(r, "followup_rec")


# ═══════════════════════════════════════
# 8. Collaboration
# ═══════════════════════════════════════

class TestCollaboration:

    async def test_share_with_team(self):
        uid = await create_active_user()
        r = await call_tool("share_with_team", {
            "resource_type": "pipeline",
            "resource_id": "test-id",
            "email": "test@example.com",
        }, user_id=uid)
        assert_no_error(r, "share_with_team")
        assert r.get("action") == "share"

    async def test_invite_member(self):
        uid = await create_active_user()
        r = await call_tool("invite_team_member", {
            "email": "new@example.com",
            "role": "editor",
        }, user_id=uid)
        assert_no_error(r, "invite_team_member")
        assert r.get("action") == "invite"
