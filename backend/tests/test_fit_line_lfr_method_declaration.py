"""M2 验收:fit_line_lfr 方法论声明 + cosmology mismatch warning.

M2 的核心契约:
1. fit_method / fit_method_requested / fit_method_downgrade_reason 三个
   字段永远在返回 dict 里(即便只跑 OLS),不漏报.
2. 显式请求 bayesian_xyerr + err 缺失 → __tool_status__=METHOD_DOWNGRADED
   + reason 提到缺哪根轴.
3. 显式请求 bayesian_xyerr + err 齐全 → 在 M2 仍然 downgrade,reason 提
   "bayesian backend not yet wired"(M3 接通后这条测试会改).
4. 样本里有 source_cosmology 跟当前 manifest 不一致 → warnings 里出现
   cosmology_mismatch,cosmology_mismatch=True.
5. 老 v1 cache(rows 不带 v2 字段)仍然能跑 OLS,不 KeyError.
6. residual_rms_dex 字段存在;scatter_dex alias 仍在(向后兼容).
7. provenance.method_provenance 节点带全三项声明字段.
"""

from unittest.mock import patch

from app.services.ai_tools import _exec_fit_line_lfr
from app.services import result_provenance as _rp


def _make_rows(n: int, *, with_err: bool = False, with_cosmo: str | None = None,
               with_mu: bool = False) -> list[dict]:
    """造 n 条合成 line_measurements,可选带 err / 宇宙学 / μ 字段."""
    rows = []
    for i in range(n):
        row = {
            "source_name": f"SRC-{i}",
            "redshift": 5.0 + 0.1 * i,
            "line_id": "[CII]",
            "log_luminosity": 9.0 + 0.05 * i,
            "fwhm_km_s": 200.0 + 10.0 * i,
            "quality_flags": [],
            "citation": {"bibcode": f"2024Paper.X{i:02d}"},
            "bibcode": f"2024Paper.X{i:02d}",
            "arxiv_id": f"2404.{i:05d}",
            # v2 字段默认 None
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": None,
            "is_lensed": None,
            "source_cosmology": None,
        }
        if with_err:
            row["log_luminosity_err"] = 0.1
            row["fwhm_err_km_s"] = 15.0
        if with_cosmo:
            row["source_cosmology"] = {"name": with_cosmo, "H0": 73.8, "Om0": 0.27}
        if with_mu:
            row["mu_lens"] = 2.0
            row["is_lensed"] = True
        rows.append(row)
    return rows


def _patch_cache(rows: list[dict]):
    """在 fit_line_lfr 内部的 cache 解析点 patch 返回固定 rows."""
    return patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "latest_literature_tables"),
    )


# ── Test 1: METHOD_DOWNGRADED status 被加到 _VALID_STATUS ──────────────

def test_method_downgraded_is_valid_status():
    assert "method_downgraded" in _rp._VALID_STATUS
    assert _rp.METHOD_DOWNGRADED == "method_downgraded"


def test_fit_line_lfr_is_in_stochastic_tools():
    assert "fit_line_lfr" in _rp._STOCHASTIC_TOOLS


# ── Test 2: fit_method 字段永远存在(即便默认 auto 路径) ───────────────

def test_default_auto_returns_ols_with_method_fields():
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables"})
    assert out["success"] is True
    assert out["fit_method"] == "ols"
    assert out["fit_method_requested"] == "auto"
    assert out["fit_method_downgrade_reason"] is None
    # auto 路径不算降级
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"


def test_explicit_ols_never_downgrades():
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "ols"})
    assert out["fit_method"] == "ols"
    assert out["fit_method_requested"] == "ols"
    assert out["fit_method_downgrade_reason"] is None


# ── Test 3: bayesian_xyerr 请求 + err 缺失 → 降级 + reason 提到缺哪根轴 ──

def test_bayesian_requested_but_errs_missing_triggers_downgrade():
    rows = _make_rows(6, with_err=False)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    assert out["__tool_status__"] == "METHOD_DOWNGRADED"
    assert out["fit_method"] == "ols"
    assert out["fit_method_requested"] == "bayesian_xyerr"
    reason = out["fit_method_downgrade_reason"]
    assert reason is not None
    assert "fwhm_err_km_s" in reason and "log_luminosity_err" in reason
    # 降级时 supports_measurement_claims 必须 False
    assert out["supports_measurement_claims"] is False
    assert out["__do_not_claim__"] is True
    # error_axes 诊断字段
    assert out["error_axes_available"]["x_err_rows"] == 0
    assert out["error_axes_available"]["y_err_rows"] == 0
    assert out["error_axes_available"]["both_axes_available"] is False


def test_bayesian_requested_errs_available_runs_bayesian_in_m3(monkeypatch):
    """M3 contract: bayesian_xyerr + err columns populated → real Bayesian
    fit (linmix), no downgrade.  We monkeypatch kelly07_linmix_fit so the
    test does not actually pay the MCMC cost; the integration test that
    runs the real sampler lives in test_bayesian_linmix_kelly07.py.
    """
    rows = _make_rows(6, with_err=True)

    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 8.5, "alpha_hdi_94": [8.3, 8.7],
        "beta_median": 1.2,  "beta_hdi_94": [1.0, 1.4],
        "intrinsic_scatter_dex": 0.18,
        "intrinsic_scatter_dex_hdi": [0.12, 0.25],
        "parameters": {
            "alpha": {"mean": 8.5, "median": 8.5, "std": 0.1, "hdi_low_94": 8.3, "hdi_high_94": 8.7, "ess": 800},
            "beta":  {"mean": 1.2, "median": 1.2, "std": 0.1, "hdi_low_94": 1.0, "hdi_high_94": 1.4, "ess": 800},
            "sigma_int": {"mean": 0.18, "median": 0.18, "std": 0.03, "hdi_low_94": 0.12, "hdi_high_94": 0.25, "ess": 800},
        },
        "n_draws_total": 16000, "n_chains": 4, "miniter": 4000, "maxiter": 20000, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored)", "reference": "Kelly 2007",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    # Bayesian path actually ran — no METHOD_DOWNGRADED status.
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"
    assert out["fit_method"] == "bayesian_xyerr_linmix"
    assert out["fit_method_downgrade_reason"] is None
    assert out["error_axes_available"]["both_axes_available"] is True
    # alpha / beta now come from Bayesian medians.
    assert out["alpha"] == 8.5
    assert out["beta"] == 1.2
    # M3 fields exposed.
    assert out["intrinsic_scatter_dex"] == 0.18
    assert out["intrinsic_scatter_dex_hdi"] == [0.12, 0.25]
    assert out["bayesian_summary"] is fake_bayes
    # method_provenance carries sampler bookkeeping.
    mp = out["provenance"]["method_provenance"]
    assert mp["intrinsic_scatter_dex"] == 0.18
    assert mp["bayesian_n_draws"] == 16000
    assert mp["bayesian_converged"] is True
    assert mp["bayesian_publication_ready"] is True


def test_bayesian_sampler_failure_falls_back_to_ols(monkeypatch):
    """M3 contract: when err columns are present but the sampler raises,
    we still get a sane result — OLS with METHOD_DOWNGRADED + concrete
    reason that mentions the exception class.
    """
    rows = _make_rows(6, with_err=True)
    import app.services.bayesian_inference as bi

    def _boom(**kw):
        raise RuntimeError("synthetic sampler failure")

    monkeypatch.setattr(bi, "kelly07_linmix_fit", _boom)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    assert out["__tool_status__"] == "METHOD_DOWNGRADED"
    assert out["fit_method"] == "ols"
    assert out["bayesian_error"] is not None
    assert "RuntimeError" in out["bayesian_error"]
    assert "synthetic sampler failure" in out["fit_method_downgrade_reason"]


def test_auto_with_errs_picks_bayesian(monkeypatch):
    """auto + err columns populated → Bayesian (not a downgrade)."""
    rows = _make_rows(6, with_err=True)
    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 9.0, "alpha_hdi_94": [8.8, 9.2],
        "beta_median": 1.1,  "beta_hdi_94": [0.9, 1.3],
        "intrinsic_scatter_dex": 0.20,
        "intrinsic_scatter_dex_hdi": [0.15, 0.27],
        "parameters": {
            "alpha": {"mean": 9.0, "median": 9.0, "std": 0.1, "hdi_low_94": 8.8, "hdi_high_94": 9.2, "ess": 800},
            "beta":  {"mean": 1.1, "median": 1.1, "std": 0.1, "hdi_low_94": 0.9, "hdi_high_94": 1.3, "ess": 800},
            "sigma_int": {"mean": 0.2, "median": 0.2, "std": 0.03, "hdi_low_94": 0.15, "hdi_high_94": 0.27, "ess": 800},
        },
        "n_draws_total": 12000, "n_chains": 4, "miniter": 4000, "maxiter": 20000, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored)", "reference": "Kelly 2007",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "auto"})
    assert out["fit_method"] == "bayesian_xyerr_linmix"
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"
    # auto carries no methodology promise → no downgrade label
    assert out["fit_method_downgrade_reason"] is None


def test_auto_without_errs_stays_ols_no_downgrade():
    """auto + err columns missing → silently stays OLS, NOT a downgrade."""
    rows = _make_rows(6, with_err=False)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "auto"})
    assert out["fit_method"] == "ols"
    assert out["fit_method_downgrade_reason"] is None
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"


# ── Test 4: cosmology mismatch warning ────────────────────────────────

def test_source_cosmology_mismatch_triggers_warning():
    # Sample declares Riess2011-style non-Planck cosmology
    rows = _make_rows(6, with_cosmo="Riess2011")
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert out["cosmology_mismatch"] is True
    assert "Riess2011" in out["sample_source_cosmologies"]
    warnings = out.get("warnings") or []
    assert any(w.get("code") == "cosmology_mismatch" for w in warnings)
    assert out["provenance"]["method_provenance"]["cosmology_mismatch"] is True


def test_matching_cosmology_no_warning():
    # Force current manifest to Planck18, sample also Planck18 → no warning
    rows = _make_rows(6, with_cosmo="Planck18")
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert out["cosmology_mismatch"] is False
    warnings = out.get("warnings") or []
    assert not any(w.get("code") == "cosmology_mismatch" for w in warnings)


# ── Test 5: backward compat with v1 rows(无 v2 字段) ─────────────────

def test_v1_rows_still_fit_without_keyerror():
    """Rows that never went through v2 normalization must still fit OK."""
    v1_rows = [
        {
            "source_name": f"S{i}", "redshift": 5.0, "line_id": "[CII]",
            "log_luminosity": 9.0 + 0.1 * i, "fwhm_km_s": 250.0 + 5.0 * i,
            "quality_flags": [], "citation": {"bibcode": f"2024X{i:02d}"},
            "bibcode": f"2024X{i:02d}",
            # 关键:没有 v2 字段
        }
        for i in range(6)
    ]
    with _patch_cache(v1_rows):
        out = _exec_fit_line_lfr({})
    assert out["success"] is True
    assert out["fit_method"] == "ols"
    # 没 err 字段 → error_axes 诊断显示 0/6
    assert out["error_axes_available"]["x_err_rows"] == 0
    assert out["error_axes_available"]["y_err_rows"] == 0
    # 没 mu_lens / is_lensed → 全部 unknown
    assert out["n_lensed"] == 0
    assert out["n_unlensed"] == 0
    assert out["n_lensed_unknown"] == 6


# ── Test 6: residual_rms_dex + scatter_dex alias ─────────────────────

def test_residual_rms_dex_field_and_alias():
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert "residual_rms_dex" in out
    assert "scatter_dex" in out  # deprecated alias
    assert out["residual_rms_dex"] == out["scatter_dex"]


# ── Test 7: lensing statistics counters ──────────────────────────────

def test_lensing_counters_when_some_rows_are_lensed():
    rows = _make_rows(3, with_mu=False) + _make_rows(3, with_mu=True)
    # tweak source_names to keep them unique
    for i, r in enumerate(rows):
        r["source_name"] = f"S{i}"
    # 前 3 条 is_lensed=None (未知),后 3 条 is_lensed=True
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert out["n_lensed"] == 3
    assert out["n_lensed_unknown"] == 3
    assert out["n_unlensed"] == 0
    # M2 里还没做 demagnify,所以 demagnified 必须是 0
    assert out["lensed_sources_demagnified"] == 0


# ── Test 8: method_provenance 节点完整 ───────────────────────────────

def test_method_provenance_node_populated():
    """When err columns are missing, the downgrade path is taken and
    method_provenance carries every diagnostic field."""
    rows = _make_rows(6, with_err=False, with_cosmo="Planck18")
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    mp = out["provenance"]["method_provenance"]
    assert mp["fit_method"] == "ols"
    assert mp["fit_method_requested"] == "bayesian_xyerr"
    assert mp["fit_method_downgrade_reason"] is not None
    assert "cosmology_used" in mp
    assert "cosmology_mismatch" in mp
    assert mp["lensed_sources_demagnified"] == 0
    # M3: Bayesian-specific keys are present even when the path didn't
    # run (all None in that case) so consumers can rely on shape.
    assert "intrinsic_scatter_dex" in mp
    assert "bayesian_n_draws" in mp
    assert mp["intrinsic_scatter_dex"] is None
    assert mp["bayesian_n_draws"] is None
