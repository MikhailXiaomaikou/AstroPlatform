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
    publication_ready = bool(parsed.get("parse_success")) and (hash_verified or content_override is not None or not product.sha256)
    status = "COMPLETED" if parsed.get("parse_success") else "PARTIAL"
    return {
        "success": bool(parsed.get("parse_success")),
        "__tool_status__": status,
        "analysis_status": "COSMOLOGY_DATA_PRODUCT_READY" if parsed.get("parse_success") else "COSMOLOGY_DATA_PRODUCT_PARTIAL",
        "publication_ready": publication_ready,
        "dataset_key": entry.key,
        "dataset_display_name": entry.display_name,
        "dataset_version": entry.version,
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


def _parse_product_text(text: str, *, product: DataProductSpec, max_preview_rows: int) -> dict[str, Any]:
    rows = _token_rows(text)
    if not rows:
        return {"parse_success": False, "warnings": ["No parseable non-comment rows found."]}
    numeric_matrix = _numeric_matrix(rows)
    if product.role == "covariance" or product.product_type.endswith("covariance_matrix"):
        return _parse_matrix(numeric_matrix, max_preview_rows=max_preview_rows)
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


def _parse_matrix(matrix: np.ndarray | None, *, max_preview_rows: int) -> dict[str, Any]:
    if matrix is None:
        return {"parse_success": False, "warnings": ["Covariance product contained no numeric matrix."]}
    square = matrix.ndim == 2 and matrix.shape[0] == matrix.shape[1]
    symmetric = bool(square and np.allclose(matrix, matrix.T, rtol=1e-6, atol=1e-12))
    positive_diagonal = bool(square and np.all(np.diag(matrix) > 0))
    finite = bool(np.all(np.isfinite(matrix)))
    warnings: list[str] = []
    if not square:
        warnings.append("Covariance matrix is not square.")
    if square and not symmetric:
        warnings.append("Covariance matrix is not symmetric within tolerance.")
    if square and not positive_diagonal:
        warnings.append("Covariance matrix has non-positive diagonal entries.")
    return {
        "parse_success": finite and square,
        "kind": "matrix",
        "shape": list(matrix.shape),
        "finite": finite,
        "square": square,
        "symmetric": symmetric,
        "positive_diagonal": positive_diagonal,
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
