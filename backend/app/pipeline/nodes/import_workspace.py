"""ImportWorkspace node — load a file from workspace storage into pipeline data."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from astropy.io import fits
from astropy.io.votable import parse_single_table
from astropy.table import Table

from app.pipeline.nodes.load_data import load_data
from app.storage import download_fits


def _table_to_payload(table: Table) -> tuple[list[str], dict[str, list], list[dict]]:
    columns = list(table.colnames)
    data = {col: table[col].tolist() for col in columns}
    rows = [
        {col: data[col][idx] for col in columns}
        for idx in range(len(table))
    ]
    return columns, data, rows


def import_workspace(input_data: dict | None, params: dict) -> dict:
    """Import a workspace file into the pipeline.

    params:
        path: storage path from the Workspace page
    """
    path = str(
        params.get("path")
        or params.get("fits_path")
        or (input_data or {}).get("path")
        or (input_data or {}).get("fits_path")
        or ""
    ).strip()
    if not path:
        raise ValueError("ImportWorkspace requires a workspace file path")

    suffix = Path(path).suffix.lower()
    if suffix in {".fits", ".fit", ".fts"}:
        return load_data(input_data, {"fits_path": path})

    try:
        raw = download_fits(path)
    except FileNotFoundError as exc:
        raise ValueError(f"ImportWorkspace could not find '{path}'") from exc

    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return {"type": "table", "path": path, "columns": [], "rows": [], "data": {}}
        columns = list(rows[0].keys())
        data = {col: [row.get(col) for row in rows] for col in columns}
        return {"type": "table", "path": path, "columns": columns, "rows": rows, "data": data}

    if suffix in {".vot", ".xml", ".votable"}:
        table = parse_single_table(io.BytesIO(raw)).to_table(use_names_over_ids=True)
        columns, data, rows = _table_to_payload(table)
        return {"type": "table", "path": path, "columns": columns, "rows": rows, "data": data}

    if suffix == ".json":
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(payload, list):
            columns = sorted({key for row in payload if isinstance(row, dict) for key in row})
            data = {col: [row.get(col) if isinstance(row, dict) else None for row in payload] for col in columns}
            return {"type": "table", "path": path, "columns": columns, "rows": payload, "data": data}
        return {"type": "json", "path": path, "data": payload}

    if suffix in {".txt", ".md", ".tex", ".bib", ".ipynb"}:
        text = raw.decode("utf-8", errors="replace")
        return {
            "type": "text",
            "path": path,
            "content": text,
            "preview": text[:4000],
        }

    # Best effort: detect FITS content even if extension is unusual.
    try:
        with fits.open(io.BytesIO(raw)):
            return load_data(input_data, {"fits_path": path})
    except Exception:
        pass

    raise ValueError(f"ImportWorkspace does not yet support '{suffix or 'unknown'}' files")
