"""Code-review remediation: cov_fidelity stamp aggregation across ALL probes,
the publication gate on unverified data, robust loader degradation, and the
data-product local-read fix.
"""
from __future__ import annotations

import asyncio

import app.services.cosmology_likelihoods as cl
from app.services.cosmology_likelihoods import run_likelihood_chain


# ── #1/#10: CC-only / RSD-only chains now carry the verified fidelity stamp ──

def test_cc_only_chain_certifies_diagonal_fidelity():
    r = run_likelihood_chain(model="lcdm", dataset_keys=["cosmic_chronometers"], n_samples=2000, random_seed=42)
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "diagonal"
    assert prov["artifact_sha256"]["cosmic_chronometers"]


def test_rsd_only_chain_certifies_diagonal_fidelity():
    r = run_likelihood_chain(model="lcdm", dataset_keys=["eboss_dr16_rsd"], n_samples=2000, random_seed=42)
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "diagonal"
    assert prov["artifact_sha256"]["eboss_dr16_rsd"]


# ── #2: a mixed BAO(full)+CC(diagonal) chain reports the WEAKEST, never 'full' ──

def test_mixed_bao_cc_reports_weakest_fidelity_not_full():
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["desi_dr1_bao", "cosmic_chronometers"],
        n_samples=2000, random_seed=42,
    )
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "diagonal"  # NOT "full" — CC drags it down
    assert set(prov["artifact_sha256"]) == {"desi_dr1_bao", "cosmic_chronometers"}


def test_desi_only_still_reports_full():
    r = run_likelihood_chain(model="lcdm", dataset_keys=["desi_dr1_bao"], n_samples=2000, random_seed=42)
    assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] == "full"


# ── #3: an 'unverified' probe blocks publication and warns ──

def test_unverified_probe_blocks_publication(monkeypatch):
    monkeypatch.setattr(
        cl, "load_verified_cc_data",
        lambda key: {
            "hz_vector": cl.COSMIC_CHRONOMETER_HZ, "sha256": None,
            "hash_verified": False, "cov_fidelity": "unverified",
        },
    )
    r = run_likelihood_chain(model="lcdm", dataset_keys=["cosmic_chronometers"], n_samples=2000, random_seed=42)
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "unverified"
    assert r["publication_ready"] is False
    assert any("sha256 verification" in w for w in r["warnings"])


# ── #5/#6/#7: the shared loader degrades safely, never crashes import ──

def _point_loader_at(tmp_path, monkeypatch, contents: str | None):
    if contents is not None:
        d = tmp_path / "cosmic_chronometers"
        d.mkdir(parents=True, exist_ok=True)
        (d / "hz.txt").write_text(contents)
    monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", tmp_path)
    return cl._load_verified_diagonal_vector("cosmic_chronometers", "hz.txt", "hz_measurement_vector")


def test_missing_but_pinned_file_is_unverified(tmp_path, monkeypatch):
    out = _point_loader_at(tmp_path, monkeypatch, None)  # registry pins it, file absent
    assert out["vector"] is None and out["cov_fidelity"] == "unverified"


def test_corrupt_file_digest_mismatch_is_unverified(tmp_path, monkeypatch):
    out = _point_loader_at(tmp_path, monkeypatch, "0.07 999.0 19.6\n")
    assert out["cov_fidelity"] == "unverified"


def test_malformed_two_column_file_degrades_not_crashes(tmp_path, monkeypatch):
    out = _point_loader_at(tmp_path, monkeypatch, "0.07 69.0\n")  # 2 columns
    assert out["vector"] is None and out["cov_fidelity"] == "unverified"


def test_header_only_empty_vector_degrades(tmp_path, monkeypatch):
    out = _point_loader_at(tmp_path, monkeypatch, "# header only, no rows\n")
    assert out["vector"] is None and out["cov_fidelity"] == "unverified"


# ── #4: load_cosmology_data_product reads the vendored local file (no spurious fail) ──

def test_load_cosmology_data_product_reads_local_for_cc():
    from app.services.cosmology_data_products import load_cosmology_data_product

    out = asyncio.run(load_cosmology_data_product(dataset_key="cosmic_chronometers"))
    assert out["hash_verified"] is True
    assert out["source"].startswith("local:")
    assert out["publication_ready"] is True


def test_load_cosmology_data_product_reads_local_for_rsd():
    from app.services.cosmology_data_products import load_cosmology_data_product

    out = asyncio.run(load_cosmology_data_product(dataset_key="eboss_dr16_rsd"))
    assert out["hash_verified"] is True and out["source"].startswith("local:")


def test_npz_binary_product_refused_not_parsed_as_text():
    """Code-review fix: selecting the Pantheon+ binary npz product by product_type
    must NOT utf-8-decode it into a ~556k-row garbage table flagged
    publication_ready. The LAST-ordering only guarded the default no-role call; an
    explicit product_type defeated it. The loader now short-circuits binary
    formats to UNAVAILABLE + __do_not_claim__ (sha256 match does not rescue it)."""
    from app.services.cosmology_data_products import load_cosmology_data_product

    out = asyncio.run(load_cosmology_data_product(
        dataset_key="pantheon_plus",
        product_type="sn_full_data_npz",
        allow_network=False,
    ))
    assert out["success"] is False
    assert out["publication_ready"] is False
    assert out["__tool_status__"] == "UNAVAILABLE"
    assert out["__do_not_claim__"] is True
    assert "parse" not in out  # no fabricated parsed table


# ── T1-U6c: executed compressed-Gaussian summaries certify 'literature_typed' ──
# A hand-typed Gaussian summary is honestly 'literature_typed' (no released file),
# never None.  This generalizes to ALL compressed probes (SN + CMB), which is what
# keeps the U6b None-gate from blocking a legitimate compressed chain.

def test_sn_compressed_only_chain_is_literature_typed():
    r = run_likelihood_chain(model="lcdm", dataset_keys=["pantheon_plus"], n_samples=2000, random_seed=42)
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "literature_typed"
    assert r["publication_ready"] is True


def test_des_and_union3_compressed_are_literature_typed():
    for ds in ("des_sn5yr", "union3"):
        r = run_likelihood_chain(model="lcdm", dataset_keys=[ds], n_samples=2000, random_seed=42)
        prov = r["provenance"]["cosmology_likelihood"]
        assert prov["cov_fidelity"] == "literature_typed", ds
        assert r["chain_tier"] == "publication", ds


def test_cmb_compressed_is_literature_typed_not_none():
    # NOT SN: proves the rule is "any executed hand-typed Gaussian", so the U6b
    # None-gate cannot block a legitimate compressed-CMB chain.
    r = run_likelihood_chain(model="lcdm", dataset_keys=["planck2018_compressed"], n_samples=2000, random_seed=42)
    assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] == "literature_typed"


def test_compressed_summary_never_labeled_full_or_diagonal():
    for ds in ("pantheon_plus", "des_sn5yr", "union3", "planck2018_compressed"):
        r = run_likelihood_chain(model="lcdm", dataset_keys=[ds], n_samples=2000, random_seed=42)
        assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] not in ("full", "diagonal"), ds


def test_full_sn_path_entry_verifies_as_full(monkeypatch):
    # When the full 1701-SN path is enabled, the SN entry verifies as 'full' via
    # the sha256-pinned npz — entry-level check, no 208s fit needed.
    monkeypatch.setattr(cl, "PANTHEON_PLUS_EXECUTABLE_KEYS", {"pantheon_plus"})
    entry = cl.get_cosmology_dataset("pantheon_plus")
    fidelity, sha = cl._entry_verification(entry)
    assert fidelity == "full" and sha


# ── T1-U6b: an unstamped (None) fidelity can no longer be publication-ready ──
# Defense in depth: if any executed probe ever slips through unstamped (None),
# the chain must be blocked exactly like an 'unverified' one — on BOTH runners.

def test_none_cov_fidelity_blocks_publication_sampling_path(monkeypatch):
    monkeypatch.setattr(cl, "_aggregate_cov_fidelity", lambda entries: (None, {}))
    r = run_likelihood_chain(model="lcdm", dataset_keys=["desi_dr1_bao"], n_samples=2000, random_seed=42)
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] is None
    assert r["publication_ready"] is False
    assert r["chain_tier"] != "publication"
    assert any("sha256 verification" in w for w in r["warnings"])


def test_none_cov_fidelity_blocks_publication_inline_path(monkeypatch):
    monkeypatch.setattr(cl, "_aggregate_cov_fidelity", lambda entries: (None, {}))
    r = run_likelihood_chain(model="lcdm", dataset_keys=["pantheon_plus"], n_samples=2000, random_seed=42)
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] is None
    assert r["publication_ready"] is False
    assert r["chain_tier"] != "publication"


# ── Moresco 2020 CC full covariance (2026-06-05): a NEW 'full' hz entry, the
# GA2018 diagonal entry untouched ──

import numpy as np  # noqa: E402


def test_moresco20_entry_certifies_full_fidelity():
    entry = cl.get_cosmology_dataset("cosmic_chronometers_moresco20")
    fidelity, sha = cl._entry_verification(entry)
    assert fidelity == "full"
    assert sha  # the cov.txt sha256


def test_moresco20_only_chain_is_full_and_publication_ready():
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers_moresco20"],
        n_samples=2000, random_seed=42,
    )
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "full"
    assert prov["artifact_sha256"]["cosmic_chronometers_moresco20"]
    assert r["publication_ready"] is True
    assert r["chain_tier"] == "publication"


def test_moresco20_is_distinct_from_ga2018_diagonal():
    # The two CC entries are independent: different fidelity grade and different
    # point counts. Adding the full-cov entry must not change the GA2018 entry.
    full = cl._entry_verification(cl.get_cosmology_dataset("cosmic_chronometers_moresco20"))[0]
    diag = cl._entry_verification(cl.get_cosmology_dataset("cosmic_chronometers"))[0]
    assert full == "full" and diag == "diagonal"
    assert len(cl.COSMIC_CHRONOMETER_MORESCO20_HZ) == 15
    assert len(cl.COSMIC_CHRONOMETER_HZ) == 31


def test_moresco20_mixed_with_diagonal_reports_weakest():
    # A chain combining the full-cov entry with a diagonal probe reports the
    # WEAKEST fidelity ('diagonal'), never 'full'.
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers_moresco20", "eboss_dr16_rsd"],
        n_samples=2000, random_seed=42,
    )
    assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] == "diagonal"


def test_moresco20_covariance_is_real_full_matrix():
    # The committed cov.txt is a genuine full covariance: 15x15, symmetric,
    # positive-definite, with non-zero off-diagonal (systematic) terms.
    raw = cl.load_verified_cc_full_cov_data("cosmic_chronometers_moresco20")
    cov = raw["covariance"]
    assert cov.shape == (15, 15)
    assert np.allclose(cov, cov.T)
    assert np.linalg.eigvalsh(cov).min() > 0
    off = cov - np.diag(np.diag(cov))
    assert np.any(np.abs(off) > 0)  # correlated systematics -> not diagonal


def test_moresco20_missing_file_degrades_unverified(tmp_path, monkeypatch):
    # Loader robustness: registry pins the files; if they are absent the loader
    # degrades to 'unverified' (blocks publication), never crashes.
    cl.load_verified_cc_full_cov_data.cache_clear()
    try:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", tmp_path)
        out = cl.load_verified_cc_full_cov_data("cosmic_chronometers_moresco20")
        assert out["covariance"] is None
        assert out["cov_fidelity"] == "unverified"
    finally:
        cl.load_verified_cc_full_cov_data.cache_clear()


def test_moresco20_only_bic_uses_15_points_not_31():
    # Regression fix: n_constraints must reflect the real per-entry vector length
    # (15 for moresco20), not the hard-coded GA2018 31.
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers_moresco20"],
        n_samples=2000, random_seed=42,
    )
    assert r["fit_statistics"]["n_constraints"] == 15


def test_co_selecting_both_cc_entries_warns_overlap():
    # Regression fix: co-adding the GA2018 compilation and its Moresco-2020 subset
    # double-counts; the runner must emit an explicit overlap warning.
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers", "cosmic_chronometers_moresco20"],
        n_samples=2000, random_seed=42,
    )
    assert any("overlap" in w and "co-added" in w for w in r["warnings"])


def test_co_selecting_both_cc_entries_blocks_publication():
    # Hard guard: a do_not_combine_with violation double-counts shared points, so
    # the chain must NOT be publication-ready and must be 'blocked' (not exploratory).
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers", "cosmic_chronometers_moresco20"],
        n_samples=2000, random_seed=42,
    )
    assert r["publication_ready"] is False
    assert r["chain_tier"] == "blocked"


# ── eBOSS DR16 FSBAO joint (D_M/r_s, D_H/r_s, fσ8) full covariance (2026-06-05) ──

def test_fsbao_entries_certify_full_fidelity():
    for key in ("eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao"):
        fidelity, sha = cl._entry_verification(cl.get_cosmology_dataset(key))
        assert fidelity == "full", key
        assert sha, key


def test_fsbao_only_chain_is_full_fidelity():
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao"],
        n_samples=4000, random_seed=42,
    )
    prov = r["provenance"]["cosmology_likelihood"]
    assert prov["cov_fidelity"] == "full"
    assert set(prov["artifact_sha256"]) == {"eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao"}
    # 9 LRG + 3 QSO joint (D_M/r_s, D_H/r_s, fσ8) points
    assert r["fit_statistics"]["n_constraints"] == 12


def test_fsbao_covariance_is_real_full_matrix():
    for key, n in (("eboss_dr16_lrg_fsbao", 9), ("eboss_dr16_qso_fsbao", 3)):
        raw = cl.load_verified_fsbao_data(key)
        cov = raw["covariance"]
        assert cov.shape == (n, n), key
        assert np.allclose(cov, cov.T), key
        assert np.linalg.eigvalsh(cov).min() > 0, key
        # quantities present: distance ratios AND growth
        quantities = {row[2] for row in raw["mean_vector"]}
        assert "f_sigma8" in quantities and "DM_over_rs" in quantities and "DH_over_rs" in quantities, key


def test_fsbao_co_selected_with_diagonal_rsd_blocks_publication():
    # The FSBAO entries share tracers with the fσ8-only eboss_dr16_rsd entry;
    # co-adding double-counts, so publication must be blocked.
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["eboss_dr16_lrg_fsbao", "eboss_dr16_rsd"],
        n_samples=2000, random_seed=42,
    )
    assert r["publication_ready"] is False
    assert r["chain_tier"] == "blocked"
    assert any("overlap" in w and "co-added" in w for w in r["warnings"])


def test_fsbao_missing_file_degrades_unverified(tmp_path, monkeypatch):
    cl.load_verified_fsbao_data.cache_clear()
    try:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", tmp_path)
        out = cl.load_verified_fsbao_data("eboss_dr16_lrg_fsbao")
        assert out["covariance"] is None and out["cov_fidelity"] == "unverified"
    finally:
        cl.load_verified_fsbao_data.cache_clear()


def test_unverified_data_is_blocked_not_exploratory(monkeypatch):
    # Gate hardening: a sha256-failed (unverified) probe must be BLOCKED, never
    # surfaced even as exploratory — unverified numbers are not discussable.
    monkeypatch.setattr(
        cl, "load_verified_cc_data",
        lambda key: {
            "hz_vector": cl.COSMIC_CHRONOMETER_HZ, "sha256": None,
            "hash_verified": False, "cov_fidelity": "unverified",
        },
    )
    r = run_likelihood_chain(model="lcdm", dataset_keys=["cosmic_chronometers"], n_samples=2000, random_seed=42)
    assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] == "unverified"
    assert r["publication_ready"] is False
    assert r["chain_tier"] == "blocked"


# ── DES-SN5YR full distance-modulus likelihood (2026-06-05) ──────────────────

def test_des_sn5yr_default_is_compressed_literature_typed():
    # Flag off (default): des_sn5yr stays the compressed Ωm summary, unchanged.
    assert cl.DES_SN5YR_EXECUTABLE_KEYS == set()
    assert cl._entry_verification(cl.get_cosmology_dataset("des_sn5yr")) == ("literature_typed", None)


def test_des_sn5yr_full_path_entry_verifies_as_full(monkeypatch):
    # With the full χ² enabled, the npz-backed entry verifies as 'full' via the
    # sha256-pinned bundle — entry-level check, no 40s fit needed.
    monkeypatch.setattr(cl, "DES_SN5YR_EXECUTABLE_KEYS", {"des_sn5yr"})
    fidelity, sha = cl._entry_verification(cl.get_cosmology_dataset("des_sn5yr"))
    assert fidelity == "full" and sha


def test_des_sn5yr_npz_is_real_full_covariance():
    data = cl.load_verified_des_sn5yr_data("des_sn5yr")
    assert data["cov_fidelity"] == "full" and data["hash_verified"] is True
    cov = data["cov"]
    assert cov.shape == (1829, 1829)
    assert np.allclose(cov, cov.T)
    assert np.linalg.eigvalsh(cov).min() > 0
    # STAT+SYS: the diagonal includes the stat term, so diag(cov) >= MUERR² everywhere
    assert np.all(np.diag(cov) >= data["mu_err_diag"] ** 2 - 1e-9)


def test_des_sn5yr_chi2_recovers_published_omegam():
    # The offset-marginalized χ² over an Ωm grid (no MCMC) must minimize near the
    # DES-SN5YR published Ωm=0.352 with χ²/dof≈0.9.
    grid = np.linspace(0.20, 0.50, 61)
    samples = grid.reshape(-1, 1)
    chi2 = cl._des_sn5yr_chi2_samples(samples, ["omegam"])
    best_om = float(grid[int(np.argmin(chi2))])
    assert 0.33 <= best_om <= 0.37, best_om
    assert 0.7 <= chi2.min() / (1829 - 1) <= 1.1


def test_des_sn5yr_chi2_invariant_to_h0_fiducial():
    # The analytic offset marginalization makes the χ² independent of any constant
    # μ shift, i.e. of the fiducial H0 — a core property of this likelihood.
    samples = np.array([[0.35]])
    base = cl._des_sn5yr_chi2_samples(samples, ["omegam"])[0]
    # shift every μ_obs by a constant (≡ changing H0/M offset) → χ² unchanged
    data = cl._load_des_sn5yr_data()
    orig = data["mu"].copy()
    try:
        data["mu"] = orig + 0.137  # arbitrary constant offset
        shifted = cl._des_sn5yr_chi2_samples(samples, ["omegam"])[0]
        assert abs(base - shifted) < 1e-6
    finally:
        data["mu"] = orig


def test_des_sn5yr_missing_file_degrades_unverified(tmp_path, monkeypatch):
    cl.load_verified_des_sn5yr_data.cache_clear()
    try:
        monkeypatch.setattr(cl, "_DES_SN5YR_DATA_DIR", tmp_path)
        out = cl.load_verified_des_sn5yr_data("des_sn5yr")
        assert out["cov"] is None and out["cov_fidelity"] == "unverified"
    finally:
        cl.load_verified_des_sn5yr_data.cache_clear()


def test_des_sn5yr_co_selected_with_union3_blocks_publication():
    # DES-SN and Union3 are overlapping SN compilations — co-adding double-counts,
    # so do_not_combine_with must block publication.
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["des_sn5yr", "union3"], n_samples=2000, random_seed=42,
    )
    assert r["publication_ready"] is False
    assert r["chain_tier"] == "blocked"
    assert any("overlap" in w and "co-added" in w for w in r["warnings"])


def test_des_sn5yr_full_path_labels_emcee_sampler(monkeypatch):
    # The full SN χ² takes the emcee bypass, so the runner provenance must say emcee
    # (not the stale 'importance' label).
    monkeypatch.setattr(cl, "DES_SN5YR_EXECUTABLE_KEYS", {"des_sn5yr"})
    r = run_likelihood_chain(model="lcdm", dataset_keys=["des_sn5yr"], n_samples=2000, random_seed=42)
    assert r["sampler"] == "sn_emcee"
    assert r["chain_diagnostics"]["overall_status"] == "emcee_sampled"
    assert r["provenance"]["cosmology_likelihood"]["runner"] == "sn_emcee"


# ── CCHP JWST+HST TRGB H0 prior (tier-3 cheap prior, 2026-06-05) ─────────────

def test_cchp_h0_freedman24_prior_value_and_uncertainty():
    e = cl.get_cosmology_dataset("cchp_h0_freedman24")
    spec = e.compressed_likelihood
    assert spec.parameters == ("H0",)
    assert spec.mean == (70.39,)
    # σ = stat 1.22 ⊕ sys 1.33 ⊕ σ_SN 0.70 in quadrature
    assert abs(spec.covariance[0][0] - (1.22 ** 2 + 1.33 ** 2 + 0.70 ** 2)) < 1e-9
    assert any(c.arxiv == "2408.06153" for c in e.citations)


def test_cchp_h0_not_combined_with_2019_trgb():
    # Same CCHP TRGB program/sample — co-adding double-counts, must block.
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["cchp_h0_freedman24", "trgb_h0_freedman19"],
        n_samples=2000, random_seed=42,
    )
    assert r["publication_ready"] is False
    assert r["chain_tier"] == "blocked"
    assert any("overlap" in w and "co-added" in w for w in r["warnings"])
