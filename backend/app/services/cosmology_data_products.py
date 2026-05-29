"""Controlled loader for registered cosmology data products.

The paper-mining loop repeatedly identified "data vector + covariance
ingestion" as the top missing/partial infrastructure need.  This loader is the
first narrow implementation step: it only loads products already declared in the
cosmology registry, verifies hashes when available, parses simple ASCII
tables/matrices, and reports covariance sanity checks.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import httpx
import numpy as np

from app.services.cosmology_likelihoods import DataProductSpec, get_cosmology_dataset


def _z_coverage_fields(entry: Any) -> dict[str, Any]:
    """Surface the dataset's declared redshift coverage as deterministic top-level
    fields. ``z_coverage_max`` / ``z_coverage_min`` are scalars so the blind-test
    runner's ``tool_result_status`` check (and the LLM) can anchor on a backend
    fact: a quantity reported outside [z_min, z_max] is a ΛCDM extrapolation, not
    a data constraint. ``None`` when the probe has no discrete-z coverage interval
    (H0 priors at z≈0, CMB at z*, cosmic-shear tomographic kernels)."""
    cov = getattr(entry, "z_coverage", None)
    if not cov:
        return {"z_coverage": None, "z_coverage_min": None, "z_coverage_max": None}
    z_min, z_max = float(cov[0]), float(cov[1])
    return {
        "z_coverage": [z_min, z_max],
        "z_coverage_min": z_min,
        "z_coverage_max": z_max,
        "coverage_note": (
            f"{entry.display_name} measurements span z ∈ [{z_min:g}, {z_max:g}]. "
            "A quantity reported outside this range is a ΛCDM model extrapolation, "
            "not a data constraint from this dataset."
        ),
    }


async def load_cosmology_data_product(
    *,
    dataset_key: str,
    role: str | None = None,
    product_type: str | None = None,
    allow_network: bool = True,
    content_override: str | bytes | None = None,
    max_preview_rows: int = 8,
) -> dict[str, Any]:
    """Load and validate a registered cosmology data product.

    ``content_override`` is for tests/local diagnostics.  Production use should
    leave it unset so data comes from the registry URL.
    """
    entry = get_cosmology_dataset(str(dataset_key))
    product = _select_product(entry.data_products, role=role, product_type=product_type)
    if product is None:
        compressed = _compressed_likelihood_data_product(
            entry,
            role=role,
            product_type=product_type,
            max_preview_rows=max_preview_rows,
        )
        if compressed is not None:
            return compressed
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": "No matching registered data product.",
            "dataset_key": entry.key,
            "requested_role": role,
            "requested_product_type": product_type,
            "__do_not_claim__": True,
        }

    raw_bytes: bytes
    source = "content_override"
    if content_override is not None:
        raw_bytes = content_override.encode("utf-8") if isinstance(content_override, str) else bytes(content_override)
    elif not allow_network:
        return {
            "success": False,
            "__tool_status__": "UNAVAILABLE",
            "analysis_status": "COSMOLOGY_DATA_PRODUCT_UNAVAILABLE",
            "publication_ready": False,
            "dataset_key": entry.key,
            "product": product.to_dict(),
            "error": "Network fetch disabled and no content_override was provided.",
            "__do_not_claim__": True,
        }
    else:
        source = product.url
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(product.url)
            response.raise_for_status()
            raw_bytes = response.content

    digest = hashlib.sha256(raw_bytes).hexdigest()
    hash_verified = product.sha256 is not None and digest == product.sha256
    warnings: list[str] = []
    if product.sha256 and not hash_verified:
        warnings.append("Downloaded data product hash does not match registry sha256.")
    if not product.sha256:
        warnings.append("Registry entry has no sha256; content integrity is not pinned.")

    text = raw_bytes.decode("utf-8", errors="replace")
    parsed = _parse_product_text(text, product=product, max_preview_rows=max_preview_rows)
    # positive_definite defaults True for non-matrix products (tables don't
    # set it); only covariance matrices are gated on actual PD-ness.
    publication_ready = (
        bool(parsed.get("parse_success"))
        and parsed.get("positive_definite", True)
        and (hash_verified or content_override is not None or not product.sha256)
    )
    status = "COMPLETED" if parsed.get("parse_success") else "PARTIAL"
    return {
        "success": bool(parsed.get("parse_success")),
        "__tool_status__": status,
        "analysis_status": "COSMOLOGY_DATA_PRODUCT_READY" if parsed.get("parse_success") else "COSMOLOGY_DATA_PRODUCT_PARTIAL",
        "publication_ready": publication_ready,
        "dataset_key": entry.key,
        "dataset_display_name": entry.display_name,
        "dataset_version": entry.version,
        **_z_coverage_fields(entry),
        "product": product.to_dict(),
        "source": source,
        "sha256": digest,
        "hash_verified": hash_verified,
        "parse": parsed,
        "warnings": warnings + list(parsed.get("warnings", [])),
        "citations": [citation.to_dict() for citation in entry.citations],
        "claim_scope": "registered_cosmology_data_product",
        "__message_to_model__": (
            "This tool loads/validates a registered data product. It can support "
            "claims about data-product availability, shape, columns, and hash status, "
            "but not posterior constraints unless a likelihood runner consumes it."
        ),
    }


def _select_product(
    products: tuple[DataProductSpec, ...],
    *,
    role: str | None,
    product_type: str | None,
) -> DataProductSpec | None:
    if role:
        for product in products:
            if product.role == role:
                return product
    if product_type:
        for product in products:
            if product.product_type == product_type:
                return product
    return products[0] if products else None


def _compressed_likelihood_data_product(
    entry: Any,
    *,
    role: str | None,
    product_type: str | None,
    max_preview_rows: int,
) -> dict[str, Any] | None:
    spec = getattr(entry, "compressed_likelihood", None)
    if spec is None:
        return None
    requested = {str(role or "").strip(), str(product_type or "").strip()}
    allowed = {
        "",
        "compressed_likelihood",
        "measurement_vector",
        "covariance",
        "compressed_gaussian_likelihood",
    }
    if not requested <= allowed:
        return None
    cov = np.asarray(spec.covariance, dtype=float)
    symmetric = bool(cov.ndim == 2 and cov.shape[0] == cov.shape[1] and np.allclose(cov, cov.T, rtol=1e-6, atol=1e-12))
    positive_diagonal = bool(symmetric and np.all(np.diag(cov) > 0))
    positive_definite = _is_positive_definite(cov)
    finite = bool(np.all(np.isfinite(cov)) and np.all(np.isfinite(np.asarray(spec.mean, dtype=float))))
    publication_ready = bool(finite and symmetric and positive_definite)
    warnings = []
    if not symmetric:
        warnings.append("Registered compressed covariance is not symmetric.")
    if symmetric and not positive_diagonal:
        warnings.append("Registered compressed covariance has non-positive diagonal entries.")
    if symmetric and positive_diagonal and not positive_definite:
        warnings.append("Registered compressed covariance is not positive-definite.")
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "COSMOLOGY_COMPRESSED_DATA_PRODUCT_READY" if publication_ready else "COSMOLOGY_DATA_PRODUCT_PARTIAL",
        "publication_ready": publication_ready,
        "dataset_key": entry.key,
        "dataset_display_name": entry.display_name,
        "dataset_version": entry.version,
        **_z_coverage_fields(entry),
        "product": {
            "product_type": "compressed_gaussian_likelihood",
            "role": "compressed_likelihood",
            "url": entry.source_url,
            "format": "registry mean/covariance",
            "description": spec.approximation,
            "columns": list(spec.parameters),
            "rows": len(spec.parameters),
            "sha256": None,
        },
        "source": "dataset_registry.compressed_likelihood",
        "hash_verified": False,
        "parse": {
            "parse_success": True,
            "kind": "compressed_gaussian_likelihood",
            "parameters": list(spec.parameters),
            "mean": list(spec.mean),
            "units": dict(spec.units),
            "covariance_shape": list(cov.shape),
            "covariance_symmetric": symmetric,
            "positive_diagonal": positive_diagonal,
            "positive_definite": positive_definite,
            "source_locator": spec.source_locator,
            "approximation": spec.approximation,
            "preview": {
                "mean": list(spec.mean)[:max_preview_rows],
                "covariance": _finite_preview(cov, max_preview_rows=max_preview_rows),
            },
            "warnings": warnings,
        },
        "warnings": [
            "Registry compressed likelihood vector; use as data-product provenance, not a posterior by itself.",
            *warnings,
        ],
        "citations": [citation.to_dict() for citation in entry.citations],
        "claim_scope": "registered_compressed_likelihood_data_product",
        "__message_to_model__": (
            "This exposes a registered compressed Gaussian mean/covariance summary. "
            "It can support claims about available compressed data vectors, but "
            "posterior/tension claims require run_cosmology_likelihood_chain."
        ),
    }


def _parse_product_text(text: str, *, product: DataProductSpec, max_preview_rows: int) -> dict[str, Any]:
    rows = _token_rows(text)
    if not rows:
        return {"parse_success": False, "warnings": ["No parseable non-comment rows found."]}
    numeric_matrix = _numeric_matrix(rows)
    if product.role == "covariance" or product.product_type.endswith("covariance_matrix"):
        matrix_format = "plain_square_matrix"
        flat_covariance = _dimension_prefixed_flat_covariance(rows)
        if flat_covariance is not None:
            numeric_matrix = flat_covariance
            matrix_format = "dimension_prefixed_flat_covariance"
        return _parse_matrix(numeric_matrix, max_preview_rows=max_preview_rows, matrix_format=matrix_format)
    return _parse_table(rows, numeric_matrix=numeric_matrix, product=product, max_preview_rows=max_preview_rows)


def _token_rows(text: str) -> list[list[str]]:
    result: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.replace(",", " ").split()
        if parts:
            result.append(parts)
    return result


def _numeric_matrix(rows: list[list[str]]) -> np.ndarray | None:
    numeric_rows: list[list[float]] = []
    width: int | None = None
    for row in rows:
        values: list[float] = []
        for value in row:
            try:
                values.append(float(value))
            except ValueError:
                pass
        if not values:
            continue
        width = width or len(values)
        if len(values) == width:
            numeric_rows.append(values)
    if not numeric_rows:
        return None
    return np.asarray(numeric_rows, dtype=float)


def _dimension_prefixed_flat_covariance(rows: list[list[str]]) -> np.ndarray | None:
    """Parse SN-style covariance files: ``N`` followed by ``N*N`` values."""
    values: list[float] = []
    for row in rows:
        for token in row:
            try:
                values.append(float(token))
            except ValueError:
                return None
    if len(values) < 2:
        return None
    n = int(round(values[0]))
    if not math.isfinite(values[0]) or n <= 0 or abs(values[0] - n) > 1e-9:
        return None
    remaining = values[1:]
    if len(remaining) != n * n:
        return None
    return np.asarray(remaining, dtype=float).reshape((n, n))


def _is_positive_definite(matrix: np.ndarray) -> bool:
    """True only for a finite, symmetric, positive-definite matrix.

    A positive diagonal is necessary but not sufficient for a valid
    covariance — strong off-diagonal terms can still make it indefinite.
    Cholesky succeeds iff the matrix is symmetric positive-definite.
    """
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        return False
    if not np.all(np.isfinite(matrix)):
        return False
    if not np.allclose(matrix, matrix.T, rtol=1e-6, atol=1e-12):
        return False
    try:
        np.linalg.cholesky(matrix)
        return True
    except np.linalg.LinAlgError:
        return False


def _parse_matrix(
    matrix: np.ndarray | None,
    *,
    max_preview_rows: int,
    matrix_format: str,
) -> dict[str, Any]:
    if matrix is None:
        return {"parse_success": False, "warnings": ["Covariance product contained no numeric matrix."]}
    square = matrix.ndim == 2 and matrix.shape[0] == matrix.shape[1]
    symmetric = bool(square and np.allclose(matrix, matrix.T, rtol=1e-6, atol=1e-12))
    positive_diagonal = bool(square and np.all(np.diag(matrix) > 0))
    positive_definite = _is_positive_definite(matrix)
    finite = bool(np.all(np.isfinite(matrix)))
    warnings: list[str] = []
    if not square:
        warnings.append("Covariance matrix is not square.")
    if square and not symmetric:
        warnings.append("Covariance matrix is not symmetric within tolerance.")
    if square and not positive_diagonal:
        warnings.append("Covariance matrix has non-positive diagonal entries.")
    if square and positive_diagonal and not positive_definite:
        warnings.append("Covariance matrix is not positive-definite.")
    return {
        "parse_success": finite and square,
        "kind": "matrix",
        "format_detected": matrix_format,
        "shape": list(matrix.shape),
        "finite": finite,
        "square": square,
        "symmetric": symmetric,
        "positive_diagonal": positive_diagonal,
        "positive_definite": positive_definite,
        "preview": _finite_preview(matrix, max_preview_rows=max_preview_rows),
        "warnings": warnings,
    }


def _parse_table(
    rows: list[list[str]],
    *,
    numeric_matrix: np.ndarray | None,
    product: DataProductSpec,
    max_preview_rows: int,
) -> dict[str, Any]:
    columns = list(product.columns)
    preview: list[dict[str, Any]] = []
    for row in rows[:max_preview_rows]:
        if columns and len(row) >= len(columns):
            preview.append({columns[i]: _coerce_token(row[i]) for i in range(len(columns))})
        else:
            preview.append({f"col_{i + 1}": _coerce_token(value) for i, value in enumerate(row)})
    return {
        "parse_success": True,
        "kind": "table",
        "row_count": len(rows),
        "declared_columns": columns,
        "numeric_shape": list(numeric_matrix.shape) if numeric_matrix is not None else None,
        "preview": preview,
        "warnings": [],
    }


def _finite_preview(matrix: np.ndarray, *, max_preview_rows: int) -> list[list[float | None]]:
    clipped = matrix[:max_preview_rows, :max_preview_rows]
    result: list[list[float | None]] = []
    for row in clipped:
        result.append([float(x) if math.isfinite(float(x)) else None for x in row])
    return result


def _coerce_token(value: str) -> str | float:
    try:
        return float(value)
    except ValueError:
        return value
