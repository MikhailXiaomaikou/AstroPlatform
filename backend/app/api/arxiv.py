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


def _parse_html_tables(html_text: str) -> list[dict]:
    """Parse HTML tables from an arXiv/ar5iv HTML paper.

    PART Z: replaced the previous all-regex parser with BeautifulSoup so
    nested tags (KaTeX <span class="ltx_text">, math spans, inline
    footnotes), malformed / unclosed HTML, and HTML entities do not
    leak into extracted cells. Uses stdlib `html.parser` as the BS4
    backend so we don't take on an lxml binary dep.

    Only top-level <table> elements are extracted; tables nested inside
    another <table> (e.g. some math/figure layouts) are skipped to
    avoid producing phantom rows.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text or "", "html.parser")
    tables: list[dict] = []

    top_level_tables = [
        t for t in soup.find_all("table") if not t.find_parent("table")
    ]
    for idx, table_tag in enumerate(top_level_tables):
        caption_tag = table_tag.find("caption")
        caption = (
            _normalize_ws(caption_tag.get_text(" ", strip=True))
            if caption_tag else ""
        )

        # Headers: prefer the first <tr> inside <thead>; fall back to the
        # first <tr> of the table.
        headers: list[str] = []
        thead = table_tag.find("thead")
        if thead:
            head_tr = thead.find("tr")
            if head_tr:
                headers = [
                    _normalize_ws(c.get_text(" ", strip=True))
                    for c in head_tr.find_all(["th", "td"])
                ]
        if not headers:
            first_tr = table_tag.find("tr")
            if first_tr:
                headers = [
                    _normalize_ws(c.get_text(" ", strip=True))
                    for c in first_tr.find_all(["th", "td"])
                ]

        # Data rows: tbody if present, else all <tr> after the first when
        # there is no <thead> (the first <tr> is then the header row we
        # already harvested above).
        tbody = table_tag.find("tbody")
        if tbody:
            tr_iter = tbody.find_all("tr")
        else:
            all_tr = table_tag.find_all("tr")
            tr_iter = all_tr[1:] if (not thead and len(all_tr) > 1) else all_tr

        rows: list[list[str]] = []
        for tr in tr_iter:
            # Skip rows that are just a stray <td colspan="N"> caption /
            # section divider — keep only multi-cell rows.
            cells = [
                _normalize_ws(c.get_text(" ", strip=True))
                for c in tr.find_all(["td", "th"])
            ]
            if cells and len(cells) > 1:
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
                "extraction_confidence": 0.85,  # bs4 path is more stable
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


# PART Z: line-ID inference table. Each entry is (regex, canonical_label).
# The first match wins — order specific lines (e.g. [OIII] 5007) before
# generic ones (e.g. plain "OIII"). Wavelengths are the brightest line of
# the doublet/multiplet for [OII], [SII], [NII] when the header doesn't
# specify a single component.
_LINE_ID_PATTERNS: tuple[tuple[str, str], ...] = (
    # [CII] 158 micron — was the original hardcoded fallback
    (r"\[?\s*c\s*ii\s*\]?(?!i)|158\s*[uµμ]m", "[CII] 158um"),
    # Lyman alpha (1216 Å)
    (r"ly\s*[aα]|lyman\s*[aα]|1215|1216", "Lyalpha 1216A"),
    # Balmer
    (r"h\s*[aα](?!lf)|halpha|6563|6562", "Halpha 6563A"),
    (r"h\s*[βb](?![a-z])|hbeta|4861", "Hbeta 4861A"),
    (r"h\s*[γg](?![a-z])|hgamma|4340", "Hgamma 4340A"),
    # Forbidden / nebular
    (r"\[?\s*o\s*iii\s*\]?|5007|4959", "[OIII] 5007A"),
    (r"\[?\s*o\s*ii\s*\]?(?!i)|3727|3729", "[OII] 3727A"),
    (r"\[?\s*n\s*ii\s*\]?(?!i)|6583|6548", "[NII] 6583A"),
    (r"\[?\s*s\s*ii\s*\]?(?!i)|6716|6731", "[SII] 6716A"),
    (r"\[?\s*ne\s*iii\s*\]?|3869", "[NeIII] 3869A"),
    (r"\[?\s*ne\s*v\s*\]?|3426", "[NeV] 3426A"),
    # CO rotational ladder
    (r"co\s*\(?\s*[12]\s*-\s*[01]\s*\)?", "CO rotational"),
    # IR cooling
    (r"\[?\s*o\s*i\s*\]?\s*63|63\s*[uµμ]m", "[OI] 63um"),
    (r"\[?\s*n\s*ii\s*\]?\s*122|122\s*[uµμ]m", "[NII] 122um"),
)


def _infer_line_id(text: str) -> str | None:
    """Best-guess emission-line label from column headers + table caption.

    Returns None when no known line is detected; callers should fall back
    to whatever is in the per-row "line" column. Previously hardcoded to
    [CII] 158um for any header containing "cii" / "158", which silently
    corrupted Hα / Lyα papers.
    """
    if not text:
        return None
    for pattern, label in _LINE_ID_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
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
        # C-X4: ALPINE / REBELS / similar high-z survey tables put the
        # redshift in a line-specific column ("z_CII", "z_[CII]", "z_line",
        # "zCII", "z_phot", "z_sys", "z_CO", ...) that the previous strict
        # pattern `^(z|redshift)$|zspec` did not catch — the row was then
        # dropped from line_measurements with "missing redshift" even
        # though luminosity + FWHM were both present. Widen to match any
        # `z`-prefixed column key (after _column_key strips punctuation
        # and lowercases). A bare `^z<digit>` (e.g. `z1`, `z2`) catches
        # multi-component / multi-line redshift columns too.
        redshift_idx = _find_column(columns, [
            r"^(z|redshift)$",
            r"^zspec$", r"^zphot$", r"^zsys$",
            r"^zcii$", r"^zline$", r"^zco$", r"^zha$", r"^zlya$",
            r"^z\d+$",
            # last-resort: column key starts with "z" and is short (<= 6
            # chars). Catches z_CII style after stripping. Don't go too
            # generic to avoid false-matching "Z" (atomic number) or
            # "zenith".
            r"^z[a-z0-9]{1,5}$",
        ])
        line_idx = _find_column(columns, [r"^line$", r"transition"])
        # PART Z: widen luminosity-column matching beyond [CII]-only.
        # Real papers write "log L(Hα)", "L_Lya", "log L([OIII])", etc.
        # _column_key strips unicode to ASCII a-z0-9, so "log L(Hα)" →
        # "loglh" / "log L_[OIII]" → "loglloiii" / "L_Lya" → "llya".
        # Match these via a generic "^log_?l" prefix + line-name fallback.
        luminosity_idx = _find_column(columns, [
            # Specific line-luminosity patterns (legacy + common)
            r"log.*l.*cii", r"lcii",
            r"log.*l.*halpha", r"^lhalpha$", r"lha\b",
            r"log.*l.*hbeta", r"^lhbeta$",
            r"log.*l.*ly(a|alpha)", r"^lly", r"llya",
            r"log.*l.*oiii", r"^loiii$",
            r"log.*l.*oii\b", r"^loii$",
            r"log.*l.*nii", r"^lnii$",
            r"log.*l.*sii", r"^lsii$",
            # Generic "log L<line>" / luminosity headers
            r"^log[_]?l[a-z]*\d*$", r"loglum", r"lineluminos", r"luminos",
            # Last resort: solo "lc" — kept for backwards-compat with old
            # papers that just wrote "L_C" or "Lc"
            r"^lc$",
        ])
        fwhm_idx = _find_column(columns, [r"fwhm", r"linewidth", r"^dv$", r"velocitywidth"])
        luminosity_err_idx = _find_column(columns, [
            r"(err|sigma|unc).*l.*(cii|halpha|hbeta|ly|oiii|oii|nii|sii)",
            r"l.*(cii|halpha|hbeta|ly|oiii|oii|nii|sii).*(err|sigma|unc)",
            r"^(loglerr|llumerr|errlogl)$",
        ])
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
        # PART Z: infer line from headers + caption across multiple species.
        # Used to be hardcoded [CII] 158um, missing Hα / Lyα / [OIII] / etc.
        inferred_line = _infer_line_id(
            " ".join(columns + [str(table.get("caption") or "")])
        )

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
