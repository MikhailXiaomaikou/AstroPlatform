"""arXiv paper table extraction -- scrape data tables from arXiv papers."""

import gzip
import html
import io
import logging
import re
import tarfile
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.models.schemas import User

router = APIRouter(prefix="/api/arxiv", tags=["arxiv"])
logger = logging.getLogger(__name__)


class ArxivTableRequest(BaseModel):
    arxiv_id: str  # e.g. "2301.12345" or full URL


class ArxivTableResponse(BaseModel):
    arxiv_id: str
    title: str
    tables: list[dict]  # [{name, columns, rows}]
    line_measurements: list[dict] = Field(default_factory=list)
    cache_key: str | None = None


_MAX_TABLE_ROWS = 200
_MAX_SOURCE_BYTES = 8_000_000
_MAX_TEX_FILES = 40
_MAX_TEX_CHARS = 3_000_000
_ARXIV_ID_RE = re.compile(r"^[\d]{4}\.[\d]{4,5}$|^[a-z-]+/[\d]{7}$", re.I)


def _clean_arxiv_id(raw: str) -> str:
    """Extract arXiv ID from URL or plain ID."""
    raw = raw.strip()
    raw = re.sub(r"^arxiv:\s*", "", raw, flags=re.I)
    # Handle full URLs
    for prefix in ["https://arxiv.org/abs/", "http://arxiv.org/abs/",
                    "https://arxiv.org/pdf/", "http://arxiv.org/pdf/"]:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    # Remove version suffix
    raw = re.sub(r'v\d+$', '', raw)
    # Remove .pdf suffix
    raw = raw.replace('.pdf', '')
    return raw.strip('/')


def _valid_arxiv_id(arxiv_id: str) -> bool:
    return bool(_ARXIV_ID_RE.match(arxiv_id))


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _strip_html(value: str) -> str:
    return _normalize_ws(re.sub(r"<[^>]+>", " ", value or ""))


def _strip_latex(value: str) -> str:
    value = value or ""
    value = re.sub(r"(?<!\\)%.*", "", value)
    value = re.sub(r"\\(?:mathrm|textrm|text|textbf|emph|mathbf|mathit)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\(?:colhead|tablehead)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\multicolumn\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\pm", "+/-", value)
    value = re.sub(r"\\times", "x", value)
    value = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", value)
    value = value.replace("\\", " ")
    value = re.sub(r"[{}$]", "", value)
    return _normalize_ws(value)


def _table_id(prefix: str, idx: int) -> str:
    return f"{prefix}_{idx + 1}"


def _row_citations(citation_base: dict[str, Any], table: dict[str, Any], row_count: int) -> list[dict[str, Any]]:
    return [
        {
            **citation_base,
            "table_id": table.get("table_id"),
            "table_label": table.get("label") or table.get("name"),
            "caption": table.get("caption") or table.get("name") or "",
            "row_index": idx,
        }
        for idx in range(row_count)
    ]


def _attach_row_citations(tables: list[dict[str, Any]], citation_base: dict[str, Any]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for table in tables:
        cloned = dict(table)
        rows = cloned.get("rows") if isinstance(cloned.get("rows"), list) else []
        cloned["row_citations"] = _row_citations(citation_base, cloned, len(rows))
        enriched.append(cloned)
    return enriched


def _parse_html_tables(html: str) -> list[dict]:
    """Parse HTML tables from arXiv abstract page or HTML paper."""
    tables = []

    # Find all <table> blocks
    table_pattern = re.compile(r'<table[^>]*>(.*?)</table>', re.DOTALL | re.IGNORECASE)
    for idx, match in enumerate(table_pattern.finditer(html)):
        table_html = match.group(1)

        # Extract caption if present
        caption_match = re.search(r'<caption[^>]*>(.*?)</caption>', table_html, re.DOTALL | re.IGNORECASE)
        caption = ""
        if caption_match:
            caption = _strip_html(caption_match.group(1))

        # Extract header row
        thead_match = re.search(r'<thead[^>]*>(.*?)</thead>', table_html, re.DOTALL | re.IGNORECASE)
        header_html = thead_match.group(1) if thead_match else ""

        # Extract header cells
        headers = []
        for th in re.finditer(r'<th[^>]*>(.*?)</th>', header_html, re.DOTALL | re.IGNORECASE):
            headers.append(_strip_html(th.group(1)))

        # If no thead, try first row
        if not headers:
            first_row = re.search(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
            if first_row:
                for cell in re.finditer(r'<t[hd][^>]*>(.*?)</t[hd]>', first_row.group(1), re.DOTALL | re.IGNORECASE):
                    headers.append(_strip_html(cell.group(1)))

        # Extract data rows
        rows: list[list[str]] = []
        tbody_match = re.search(r'<tbody[^>]*>(.*?)</tbody>', table_html, re.DOTALL | re.IGNORECASE)
        body_html = tbody_match.group(1) if tbody_match else table_html

        for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', body_html, re.DOTALL | re.IGNORECASE):
            cells = []
            for td in re.finditer(r'<td[^>]*>(.*?)</td>', tr.group(1), re.DOTALL | re.IGNORECASE):
                text = _strip_html(td.group(1))
                cells.append(text)
            if cells and len(cells) > 1:  # Skip single-cell rows (likely captions)
                rows.append(cells)

        if headers and rows:
            tables.append({
                "table_id": _table_id("html", idx),
                "label": f"Table {idx + 1}",
                "name": caption or f"Table {idx + 1}",
                "caption": caption,
                "columns": headers,
                "rows": rows[:_MAX_TABLE_ROWS],
                "row_count": len(rows),
                "extraction_method": "ar5iv_html",
                "extraction_confidence": 0.78,
                "warnings": [],
            })

    return tables


def _extract_braced(block: str, command: str) -> str:
    marker = "\\" + command
    pos = block.find(marker)
    if pos < 0:
        return ""
    brace = block.find("{", pos + len(marker))
    if brace < 0:
        return ""
    depth = 0
    for idx in range(brace, len(block)):
        char = block[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return block[brace + 1:idx]
    return ""


def _parse_latex_rows(tabular_content: str) -> list[list[str]]:
    tabular_content = re.sub(r"(?<!\\)%.*", "", tabular_content)
    tabular_content = re.sub(
        r"\\(?:hline|toprule|midrule|bottomrule|endhead|tableline|startdata|enddata)\b",
        "",
        tabular_content,
    )
    raw_rows = re.split(r"\\\\", tabular_content)
    parsed_rows: list[list[str]] = []
    for row in raw_rows:
        row = row.strip()
        if not row:
            continue
        cells = [_strip_latex(cell) for cell in row.split("&")]
        cells = [cell for cell in cells if cell != ""]
        if len(cells) > 1:
            parsed_rows.append(cells)
    return parsed_rows


def _split_headers_rows(parsed_rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    if len(parsed_rows) < 2:
        return [], []
    headers = parsed_rows[0]
    rows = parsed_rows[1:]
    width = len(headers)
    normalized_rows: list[list[str]] = []
    for row in rows:
        if len(row) < 2:
            continue
        if len(row) < width:
            row = row + [""] * (width - len(row))
        elif len(row) > width:
            row = row[:width]
        normalized_rows.append(row)
    return headers, normalized_rows


def _parse_latex_tables(source: str) -> list[dict]:
    """Parse LaTeX tabular environments from arXiv source."""
    tables = []

    env_names = r"(?:table\*?|sidewaystable\*?|deluxetable\*?|longtable)"
    table_envs = re.finditer(
        rf"\\begin\{{{env_names}\}}(?:\{{[^}}]*\}})?(.*?)\\end\{{{env_names}\}}",
        source,
        re.DOTALL,
    )

    for idx, env_match in enumerate(table_envs):
        block = env_match.group(1)

        caption = _strip_latex(_extract_braced(block, "caption")) or f"Table {idx + 1}"
        label = _strip_latex(_extract_braced(block, "label")) or f"Table {idx + 1}"

        tabular = re.search(
            r"\\begin\{(?:tabular\*?|longtable)\}(?:\{[^}]*\})?(.*?)\\end\{(?:tabular\*?|longtable)\}",
            block,
            re.DOTALL,
        )
        if tabular:
            tabular_content = tabular.group(1)
        else:
            head = _extract_braced(block, "tablehead")
            data_match = re.search(r"\\startdata(.*?)\\enddata", block, re.DOTALL)
            if head and data_match:
                tabular_content = head + r"\\" + data_match.group(1)
            else:
                tabular_content = block

        headers, data_rows = _split_headers_rows(_parse_latex_rows(tabular_content))
        if headers and data_rows:
            tables.append({
                "table_id": _table_id("latex", idx),
                "label": label,
                "name": caption,
                "caption": caption,
                "columns": headers,
                "rows": data_rows[:_MAX_TABLE_ROWS],
                "row_count": len(data_rows),
                "extraction_method": "arxiv_latex_source",
                "extraction_confidence": 0.72,
                "warnings": [],
            })

    if not tables:
        for idx, match in enumerate(re.finditer(
            r"\\begin\{(?:tabular\*?|longtable)\}(?:\{[^}]*\})?(.*?)\\end\{(?:tabular\*?|longtable)\}",
            source,
            re.DOTALL,
        )):
            headers, data_rows = _split_headers_rows(_parse_latex_rows(match.group(1)))
            if headers and data_rows:
                tables.append({
                    "table_id": _table_id("latex", idx),
                    "label": f"Table {idx + 1}",
                    "name": f"Table {idx + 1}",
                    "caption": "",
                    "columns": headers,
                    "rows": data_rows[:_MAX_TABLE_ROWS],
                    "row_count": len(data_rows),
                    "extraction_method": "arxiv_latex_source",
                    "extraction_confidence": 0.55,
                    "warnings": ["Parsed a bare tabular environment without a table caption."],
                })

    return tables


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _source_texts_from_eprint(raw: bytes) -> list[str]:
    if len(raw) > _MAX_SOURCE_BYTES:
        raw = raw[:_MAX_SOURCE_BYTES]

    texts: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            for member in archive.getmembers():
                if len(texts) >= _MAX_TEX_FILES:
                    break
                if not member.isfile():
                    continue
                lower = member.name.lower()
                if not lower.endswith((".tex", ".ltx", ".txt")):
                    continue
                if member.size > 1_500_000:
                    continue
                extracted = archive.extractfile(member)
                if not extracted:
                    continue
                texts.append(_decode_bytes(extracted.read()))
            return texts
    except tarfile.TarError:
        pass

    try:
        decompressed = gzip.decompress(raw)
        return [_decode_bytes(decompressed)]
    except OSError:
        return [_decode_bytes(raw)]


async def _fetch_arxiv_metadata(client: httpx.AsyncClient, arxiv_id: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "arxiv_id": arxiv_id,
        "bibcode": f"arXiv:{arxiv_id}",
        "source_url": f"https://arxiv.org/abs/{arxiv_id}",
    }
    try:
        resp = await client.get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
        if resp.status_code != 200:
            return metadata
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return metadata
        title = entry.findtext("atom:title", default="", namespaces=ns)
        summary = entry.findtext("atom:summary", default="", namespaces=ns)
        published = entry.findtext("atom:published", default="", namespaces=ns)
        doi = entry.findtext("arxiv:doi", default="", namespaces=ns)
        authors = [
            (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
            for author in entry.findall("atom:author", ns)
        ]
        metadata.update({
            "title": _normalize_ws(title),
            "abstract": _normalize_ws(summary),
            "authors": [a for a in authors if a],
            "year": (published or "")[:4],
            "doi": doi or None,
        })
    except Exception as exc:
        logger.warning("arXiv metadata fetch failed for %s: %s", arxiv_id, exc)
    return metadata


def _column_key(column: str) -> str:
    text = _strip_latex(column).lower()
    text = text.replace("[", "").replace("]", "")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _parse_number(value: Any) -> float | None:
    text = _strip_latex(str(value or ""))
    if not text:
        return None
    text = text.replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_uncertainty(value: Any) -> float | None:
    text = _strip_latex(str(value or "")).replace(",", "")
    match = re.search(r"(?:\+/-|±)\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _find_column(columns: list[str], patterns: list[str]) -> int | None:
    keys = [_column_key(column) for column in columns]
    for pattern in patterns:
        regex = re.compile(pattern)
        for idx, key in enumerate(keys):
            if regex.search(key):
                return idx
    return None


def _normalize_line_measurements(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    measurements: list[dict[str, Any]] = []
    for table in tables:
        columns = [str(c) for c in table.get("columns") or []]
        rows = table.get("rows") if isinstance(table.get("rows"), list) else []
        row_citations = table.get("row_citations") if isinstance(table.get("row_citations"), list) else []
        source_idx = _find_column(columns, [
            r"^(source|object|name|id|galaxy)$",
            r"(source|object|galaxy)(name|id)",
            r"^(sourceid|objectid|galaxyid)$",
        ])
        redshift_idx = _find_column(columns, [r"^(z|redshift)$", r"zspec"])
        line_idx = _find_column(columns, [r"^line$", r"transition"])
        luminosity_idx = _find_column(columns, [
            r"log.*l.*cii", r"lcii", r"lc", r"lineluminos", r"luminos",
        ])
        fwhm_idx = _find_column(columns, [r"fwhm", r"linewidth", r"dv", r"velocitywidth"])
        luminosity_err_idx = _find_column(columns, [r"(err|sigma|unc).*l.*cii", r"l.*cii(err|sigma|unc)"])
        fwhm_err_idx = _find_column(columns, [r"(err|sigma|unc).*fwhm", r"fwhm(err|sigma|unc)"])
        flux_idx = _find_column(columns, [r"flux", r"integrated"])
        # Gravitational lensing magnification column.  Common names in
        # ALPINE/REBELS/cluster-lensed papers: "mu", "μ", "magnification".
        # μ ≳ 1.05 is the conventional "lensed" threshold; anything <=1
        # or missing means the source is either unlensed or we don't know.
        mu_idx = _find_column(columns, [r"^mu$", r"magnif(ication)?", r"mu_?lens", r"μ"])
        if source_idx is None or luminosity_idx is None or fwhm_idx is None:
            continue

        luminosity_header = columns[luminosity_idx]
        luminosity_is_log = "log" in luminosity_header.lower()
        luminosity_unit = _normalize_ws(luminosity_header)
        inferred_line = "[CII] 158um" if re.search(r"c\s*ii|cii|158", " ".join(columns + [str(table.get("caption") or "")]), re.I) else None

        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                continue
            def cell(idx: int | None) -> str:
                if idx is None or idx >= len(row):
                    return ""
                return str(row[idx])

            luminosity_value = _parse_number(cell(luminosity_idx))
            fwhm_value = _parse_number(cell(fwhm_idx))
            source_name = _normalize_ws(cell(source_idx))
            if not source_name or luminosity_value is None or fwhm_value is None:
                continue
            citation = row_citations[row_index] if row_index < len(row_citations) and isinstance(row_citations[row_index], dict) else {}
            quality_flags = []
            raw_luminosity = cell(luminosity_idx)
            raw_fwhm = cell(fwhm_idx)
            luminosity_err = _parse_number(cell(luminosity_err_idx)) or _parse_uncertainty(raw_luminosity)
            fwhm_err = _parse_number(cell(fwhm_err_idx)) or _parse_uncertainty(raw_fwhm)
            if any(token in raw_luminosity for token in ("<", ">")):
                quality_flags.append("luminosity_limit")
            if any(token in raw_fwhm for token in ("<", ">")):
                quality_flags.append("fwhm_limit")
            mu_value = _parse_number(cell(mu_idx)) if mu_idx is not None else None
            if mu_value is not None and mu_value > 1.05:
                is_lensed_flag: bool | None = True
            elif mu_value is not None:
                is_lensed_flag = False
            else:
                is_lensed_flag = None
            measurements.append({
                "source_name": source_name,
                "redshift": _parse_number(cell(redshift_idx)),
                "line_id": _normalize_ws(cell(line_idx)) or inferred_line,
                "log_luminosity": luminosity_value if luminosity_is_log else None,
                "log_luminosity_err": luminosity_err if luminosity_is_log else None,
                "luminosity": None if luminosity_is_log else luminosity_value,
                "luminosity_err": None if luminosity_is_log else luminosity_err,
                "luminosity_unit": luminosity_unit,
                "fwhm_km_s": fwhm_value,
                "fwhm_err_km_s": fwhm_err,
                "flux": _parse_number(cell(flux_idx)),
                # Schema v2 fields: lensing + paper-level cosmology.
                # mu_lens / is_lensed come from the table when the paper
                # reported them; source_cosmology is paper-level and stays
                # None here — populated by the caller if parsed from the
                # paper abstract / front matter.
                "mu_lens": mu_value,
                "is_lensed": is_lensed_flag,
                "source_cosmology": None,
                "raw_values": {
                    "luminosity": raw_luminosity,
                    "fwhm": raw_fwhm,
                    "redshift": cell(redshift_idx),
                    "mu": cell(mu_idx) if mu_idx is not None else "",
                },
                "quality_flags": quality_flags,
                "citation": citation,
                "bibcode": citation.get("bibcode"),
                "arxiv_id": citation.get("arxiv_id"),
                "table_id": table.get("table_id"),
                "table_label": table.get("label") or table.get("name"),
                "row_index": row_index,
            })
    return measurements


async def extract_arxiv_tables_payload(arxiv_id_raw: str) -> dict[str, Any]:
    arxiv_id = _clean_arxiv_id(arxiv_id_raw)
    if not arxiv_id:
        raise HTTPException(status_code=400, detail="Invalid arXiv ID")
    if not _valid_arxiv_id(arxiv_id):
        raise HTTPException(status_code=400, detail="Invalid arXiv ID format")

    all_tables: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {"arxiv_id": arxiv_id, "bibcode": f"arXiv:{arxiv_id}"}

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        metadata = await _fetch_arxiv_metadata(client, arxiv_id)

        try:
            html_url = f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
            resp = await client.get(html_url)
            if resp.status_code == 200:
                html_text = resp.text
                all_tables = _parse_html_tables(html_text)
        except Exception as exc:
            logger.warning("ar5iv fetch failed for %s: %s", arxiv_id, exc)

        if not all_tables:
            try:
                src_url = f"https://arxiv.org/e-print/{arxiv_id}"
                resp = await client.get(src_url)
                if resp.status_code == 200:
                    for text in _source_texts_from_eprint(resp.content):
                        if sum(len(t.get("rows") or []) for t in all_tables) > _MAX_TABLE_ROWS * 10:
                            break
                        all_tables.extend(_parse_latex_tables(text[:_MAX_TEX_CHARS]))
            except Exception as exc:
                logger.warning("arXiv source fetch failed for %s: %s", arxiv_id, exc)

    if not all_tables:
        raise HTTPException(status_code=404, detail=f"No tables found in arXiv:{arxiv_id}")

    citation_base = {
        "bibcode": metadata.get("bibcode") or f"arXiv:{arxiv_id}",
        "arxiv_id": arxiv_id,
        "doi": metadata.get("doi"),
        "title": metadata.get("title") or arxiv_id,
        "authors": metadata.get("authors") or [],
        "year": metadata.get("year"),
        "source_url": metadata.get("source_url") or f"https://arxiv.org/abs/{arxiv_id}",
    }
    tables = _attach_row_citations(all_tables, citation_base)
    line_measurements = _normalize_line_measurements(tables)
    return {
        "arxiv_id": arxiv_id,
        "title": metadata.get("title") or arxiv_id,
        "authors": metadata.get("authors") or [],
        "year": metadata.get("year"),
        "bibcode": citation_base["bibcode"],
        "doi": metadata.get("doi"),
        "source_url": citation_base["source_url"],
        "tables": tables,
        "line_measurements": line_measurements,
        "result_granularity": "paper_table",
        "supports_measurement_claims": bool(line_measurements),
    }


@router.post("/extract-tables", response_model=ArxivTableResponse)
async def extract_arxiv_tables(
    req: ArxivTableRequest,
    _user: User = Depends(get_current_user),
):
    """Extract data tables from an arXiv paper.

    M19: gated with get_current_user so this endpoint cannot be used as a
    DoS amplifier against ar5iv / arxiv.org by unauthenticated clients.
    """
    payload = await extract_arxiv_tables_payload(req.arxiv_id)
    return ArxivTableResponse(**payload)
