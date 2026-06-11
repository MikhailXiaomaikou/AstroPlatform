"""fit_line_lfr user-supplied CSV path (3B, 2026-06-11).

Researchers' own measurement tables can now be fit directly: upload a CSV
(general-upload endpoint → 'uploads/...' path) and pass user_file=. Locks:

1. A friendly-header CSV fits end-to-end with honest labeling:
   input_data_origin="user_uploaded", claim_scope="user_data",
   source_authority="user_provided", publication_ready=True (the citation
   requirement is replaced by the user-data origin, mirroring
   cosmology_mcmc.CLAIMABLE_INPUT_ORIGINS).
2. The result injects NOTHING into claim_validator's bibcode pool —
   user data must never mint literature citations.
3. Hostile headers + column_mapping (3A reuse) rescue the fit.
4. Path safety: traversal, absolute paths, non-uploads/ prefixes, and
   missing files all fail loud with error_class, never fabricate.
5. The literature cache path is untouched (no user_file → behavior as
   before; existing fit tests stay green separately).
"""
from __future__ import annotations

import asyncio

import app.services.ai_tools as ai_tools
import app.storage as storage
from app.services.claim_validator import _build_valid_bibcode_pool

_FRIENDLY_CSV = (
    "source_name,redshift,log_luminosity,log_luminosity_err,fwhm_km_s,fwhm_err_km_s\n"
    "MYGAL-1,4.50,8.91,0.05,310,20\n"
    "MYGAL-2,4.62,9.42,0.04,525,25\n"
    "MYGAL-3,4.71,9.05,0.06,402,18\n"
    "MYGAL-4,5.10,9.61,0.05,610,30\n"
    "MYGAL-5,5.31,8.77,0.07,288,15\n"
)

_HOSTILE_CSV = (
    "Obj,Spec. velocity ref,Brightness (dex),Width\n"
    "GAL-1,4.55,8.91,310\n"
    "GAL-2,5.10,9.42,525\n"
    "GAL-3,4.80,9.10,400\n"
    "GAL-4,5.00,9.30,500\n"
    "GAL-5,4.60,8.80,300\n"
)


def _put_csv(tmp_path, monkeypatch, rel_path: str, content: str) -> str:
    monkeypatch.setattr(storage, "_storage_root", tmp_path)
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return rel_path


def _fit(inp: dict) -> dict:
    return asyncio.run(ai_tools._exec_fit_line_lfr_async(inp, "user-csv-test", ""))


def test_friendly_csv_fits_with_honest_user_labels(tmp_path, monkeypatch):
    path = _put_csv(tmp_path, monkeypatch, "uploads/u1/abc_mine.csv", _FRIENDLY_CSV)
    out = _fit({"user_file": path})
    assert out["success"] is True
    assert out["n_used"] == 5
    assert out["input_data_origin"] == "user_uploaded"
    assert out["claim_scope"] == "user_data"
    assert out["publication_ready"] is True
    dataset = out["provenance"]["datasets"][0]
    assert dataset["service_key"] == "user_uploaded_csv_fit"
    assert dataset["source_authority"] == "user_provided"
    assert dataset["article"] == ""
    assert any(
        (w.get("code") if isinstance(w, dict) else "") == "user_uploaded_inputs"
        for w in out.get("warnings") or []
    )
    assert "USER'S OWN" in out["__message_to_model__"]


def test_user_csv_injects_no_row_level_citations(tmp_path, monkeypatch):
    path = _put_csv(tmp_path, monkeypatch, "uploads/u1/abc_mine.csv", _FRIENDLY_CSV)
    out = _fit({"user_file": path})
    assert out["citation_summary"]["citation_count"] == 0
    assert out["provenance"]["datasets"][0]["article"] == ""
    pool = _build_valid_bibcode_pool([
        {"tool": "fit_line_lfr", "input": {"user_file": path}, "result": out},
    ])
    # The pool may legitimately contain the ASSUMED-COSMOLOGY references the
    # fit result itself declares (cosmology_manifest: Planck18 preset + Tcmb
    # method paper) — citing the assumed cosmology is provenance-correct.
    # What must NEVER appear is a citation minted from the user's rows.
    manifest = out.get("cosmology_manifest") or {}
    allowed = {
        str(manifest.get("bibcode") or ""),
        str(manifest.get("tcmb_bibcode") or ""),
    } - {""}
    assert pool <= allowed, pool


def test_hostile_headers_rescued_by_column_mapping(tmp_path, monkeypatch):
    path = _put_csv(tmp_path, monkeypatch, "uploads/u1/abc_hostile.csv", _HOSTILE_CSV)
    out_plain = _fit({"user_file": path})
    assert out_plain["success"] is False
    assert out_plain["error_class"] == "user_csv_unreadable"
    assert "column_mapping" in out_plain["error"]

    out_mapped = _fit({
        "user_file": path,
        "column_mapping": {
            "source_name": "Obj",
            "redshift": 1,
            "log_luminosity": "Brightness (dex)",
            "fwhm_km_s": "Width",
        },
    })
    assert out_mapped["success"] is True
    assert out_mapped["n_used"] == 5
    assert out_mapped["input_data_origin"] == "user_uploaded"


def test_path_safety_fails_loud(tmp_path, monkeypatch):
    _put_csv(tmp_path, monkeypatch, "uploads/u1/abc_mine.csv", _FRIENDLY_CSV)
    for bad in (
        "../secrets.csv",
        "/etc/passwd",
        "data/astro.db",                  # not under uploads/
        "uploads/../../../etc/passwd.csv",
        "uploads/u1/nonexistent.csv",
        "uploads/u1/abc_mine.txt",        # wrong extension
    ):
        out = _fit({"user_file": bad})
        assert out["success"] is False, bad
        assert out["error_class"] == "user_csv_unreadable", bad
        assert out.get("__do_not_claim__") is True, bad


def test_traversal_guard_blocks_even_when_target_exists(tmp_path, monkeypatch):
    # The plain traversal cases above can't distinguish guard-block from
    # file-not-found (the target doesn't exist either way). Plant a sentinel
    # CSV OUTSIDE the storage root: only the resolve()+relative_to guard can
    # stop this one, so a guard regression turns this test red.
    monkeypatch.setattr(storage, "_storage_root", tmp_path / "root")
    (tmp_path / "root" / "uploads").mkdir(parents=True)
    (tmp_path / "secrets.csv").write_text(_FRIENDLY_CSV, encoding="utf-8")
    out = _fit({"user_file": "uploads/../../secrets.csv"})
    assert out["success"] is False
    assert out["error_class"] == "user_csv_unreadable"
    assert "traversal" in out["error"].lower()


def test_no_user_file_keeps_literature_path(monkeypatch):
    # Without user_file the entry falls through to the cache path exactly as
    # before — empty cache → the familiar missing_measurement_cache envelope.
    out = _fit({"cache_key": "definitely_not_cached_anywhere"})
    assert out["success"] is False
    assert out["error_class"] == "missing_measurement_cache"
