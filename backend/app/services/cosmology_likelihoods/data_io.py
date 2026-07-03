"""Vendored-data directory + sha256-pinned generic loaders.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import hashlib
import pathlib
from typing import Any

import numpy as np

from app.services.cosmology_likelihoods.core import (
    logger,
)

from app.services.cosmology_likelihoods.registry import (
    get_cosmology_dataset,
)


# Vendored, sha256-pinned cosmology data products. They live here so the array
# the chi² actually fits IS the array the registry checksum verifies — closing
# the "decorative provenance" hole where the checksum certified a file the fit
# never read (Step 1 provenance-binding, 2026-06-01).
# parents[3] == backend/ (this file is one level deeper than the pre-split
# cosmology_likelihoods.py, which used parents[2]).
_VENDORED_COSMO_DATA_DIR = pathlib.Path(__file__).resolve().parents[3] / "data" / "cosmology"


def _registry_product_sha256(dataset_key: str, role: str) -> str | None:
    for product in get_cosmology_dataset(dataset_key).data_products:
        if product.role == role:
            return product.sha256
    return None


def _load_verified_diagonal_vector(
    dataset_key: str, filename: str, role: str
) -> dict[str, Any]:
    """Shared robust loader for sha256-pinned 3-column (z, value, σ) diagonal data
    products (cosmic-chronometer H(z), eBOSS fσ8).  Returns
    {vector, sha256, hash_verified, cov_fidelity}; vector is None on any failure
    so the caller substitutes its hand-typed fallback.  Failure semantics:
    file present + digest matches -> 'diagonal'; present + digest mismatch ->
    'unverified'; expected (registry-pinned) file missing or unparseable ->
    'unverified' (never an import-time crash, never a silent wrong-shape/empty
    vector); no registry product at all -> 'literature_typed'.
    """
    pinned = _registry_product_sha256(dataset_key, role)
    path = _VENDORED_COSMO_DATA_DIR / dataset_key / filename
    if not path.exists():
        return {
            "vector": None, "sha256": None, "hash_verified": False,
            "cov_fidelity": "unverified" if pinned else "literature_typed",
        }
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        arr = np.atleast_2d(np.loadtxt(path, comments="#"))
        if arr.shape[0] == 0 or arr.shape[1] != 3:
            raise ValueError(f"expected a non-empty 3-column table, got shape {arr.shape}")
        vector = tuple((float(z), float(v), float(s)) for z, v, s in arr)
        verified = digest == pinned
        return {
            "vector": vector, "sha256": digest, "hash_verified": bool(verified),
            "cov_fidelity": "diagonal" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning(
            "cosmology data product %s/%s failed to load (%s); marking unverified",
            dataset_key, filename, exc,
        )
        return {"vector": None, "sha256": None, "hash_verified": False, "cov_fidelity": "unverified"}
