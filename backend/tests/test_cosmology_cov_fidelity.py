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
