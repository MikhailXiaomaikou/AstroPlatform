"""Stage 6.3 spike (2026-05-20): LLM paper table extraction + ±1% back-verification prototype.

Design philosophy:
  The existing `_normalize_line_measurements` (ai_tools.py) is a monolithic regex
  matcher — one key for one lock — and breaks immediately across different paper schemas.
  But letting the LLM read numbers freely conflicts with the platform's anti-fabrication
  principle (the LLM might invent numbers).

  This module takes a hybrid approach:
    1. The LLM reads the paper and provides numbers, but must attach
       (table_idx, row_idx, cell_provenance).
    2. The backend uses those 3 coordinates to locate the original HTML cell and
       applies a regex to find numbers in the cell text.
    3. If the LLM's number and the cell's number disagree by more than ±1%,
       the result is rejected and not cached.

  This lets the LLM handle schema inference freely while the backend enforces the
  anti-fabrication floor.

  Standalone spike module — not wired into the ai_tools dispatch / claim_validator /
  chat.py pipeline. Promotion decision deferred until the spike proves out.

Dependencies:
  - httpx (already used by the project)
  - beautifulsoup4 (already used by the project)
  - anthropic SDK (already used by the project)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_AR5IV_URL_TEMPLATE = "https://ar5iv.org/abs/{arxiv_id}"
_DEFAULT_MAX_HTML_EXCERPT_CHARS = 30000
_NUMBER_RE = re.compile(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?")

# Stage 6.3 spike v2 (2026-05-20): codex re-testing found that for ALPINE
# 2002.00962, the default excerpt wasted budget on formula/metadata tables and
# the actual measurement table (table 25) was truncated. build_html_excerpt was
# reworked to rank tables by measurement-keyword score so the most relevant
# tables are sent to the LLM first.
_MEASUREMENT_HEADER_STRONG = (
    "fwhm", "linewidth", "line width",
    "[cii]", "l[cii]", "l_cii", "lcii",
    "log l", "log10 l", "lsun", "l_sun",
    "i[cii]", "icii", "i_cii",
    "flux",
)
_MEASUREMENT_HEADER_MEDIUM = (
    "source", "name", "object", "galaxy", "id",
    "redshift", "z[cii]", "z_cii", "zcii", "z[",
)
# LaTeX equation markers — many occurrences indicate a formula table, not a data table.
_LATEX_EQ_MARKERS = (
    "\\frac", "\\sum", "\\int", "\\partial",
    "\\mathrm", "\\Pi", "\\Sigma", "\\equiv",
    "\\nabla", "\\dot", "\\bar",
)


@dataclass
class ExtractedMeasurement:
    """One measurement extracted by the LLM and back-verified by the backend at ±1%."""

    source_name: str
    fwhm_km_s: float | None
    log_luminosity: float | None
    z: float | None
    table_idx: int
    row_idx: int
    cell_provenance: dict[str, str]
    validation_status: str  # "passed" / "failed_mismatch" / "failed_no_cell"
    validation_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_paper_html(arxiv_id: str, timeout: float = 30.0) -> str:
    """Fetch ar5iv-converted HTML for an arxiv paper.

    ar5iv is a LaTeX-to-HTML conversion service, more reliable than scraping
    the raw LaTeX source (most cosmology papers are covered). On failure, the
    caller decides the fallback (e.g. arxiv.org HTML / LaTeX source); this
    spike does not implement a fallback.
    """
    cleaned = arxiv_id.strip().replace("arXiv:", "").replace("arxiv:", "")
    url = _AR5IV_URL_TEMPLATE.format(arxiv_id=cleaned)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def parse_html_tables(html: str) -> list[list[list[str]]]:
    """Return list of tables; each table is list of rows; each row is list of cell strings.

    Same approach as ai_tools._parse_html_tables but self-contained; does not depend on arxiv.py.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def score_table_relevance(table: list[list[str]]) -> int:
    """Stage 6.3 spike v2: score a table for measurement-table relevance.

    Checks whether the first row (header) contains known measurement-column
    keywords. Multi-row tables receive a bonus. A score of 0 means the table
    does not resemble a measurement table at all.
    """
    if not table:
        return 0
    header = " ".join(table[0]).lower()
    score = 0
    for kw in _MEASUREMENT_HEADER_STRONG:
        if kw in header:
            score += 5
    for kw in _MEASUREMENT_HEADER_MEDIUM:
        if kw in header:
            score += 2
    if len(table) >= 5:
        score += 2
    elif len(table) >= 3:
        score += 1
    return score


def is_low_value_table(table: list[list[str]]) -> bool:
    """Stage 6.3 spike v2: determine whether a table is formula / metadata / caption-only.

    - fewer than 2 rows: not a real data table
    - first row has only 1 cell: caption or metadata
    - many LaTeX equation markers (\\frac / \\sum / etc.): formula table
    """
    if not table or len(table) < 2:
        return True
    if len(table[0]) <= 1:
        return True
    flat = " ".join(cell for row in table for cell in row)
    eq_hits = sum(flat.count(marker) for marker in _LATEX_EQ_MARKERS)
    if eq_hits >= 3:
        return True
    return False


def build_html_excerpt(
    tables: list[list[list[str]]],
    max_chars: int = _DEFAULT_MAX_HTML_EXCERPT_CHARS,
    max_rows_per_table: int = 200,
) -> str:
    """Format tables as plain text for LLM prompt, bounded by max_chars.

    Stage 6.3 spike v2 (2026-05-20): no longer truncates by table order. New approach:
      1. Filter out low-value tables (formula / metadata / caption-only).
      2. Score the remaining tables by measurement relevance.
      3. Fill the budget in descending score order — highest-scoring tables first;
         tables with score=0 are skipped.

    The original `table_idx` is preserved (no renumbering) because verify_record
    uses table_idx to look up the original HTML cell and must match what the LLM saw.

    Codex re-testing found that for ALPINE 2002.00962, table 25 is the real
    measurement table but the default implementation exhausted the budget on the
    first 24 equation/metadata tables. v2 promotes table 25 to the front.
    """
    scored: list[tuple[int, int, list[list[str]]]] = []
    for idx, table in enumerate(tables):
        if is_low_value_table(table):
            continue
        score = score_table_relevance(table)
        if score == 0:
            continue
        scored.append((score, idx, table))
    scored.sort(key=lambda x: -x[0])

    parts: list[str] = []
    total = 0
    if not scored:
        parts.append(
            "(no measurement-like tables found after filtering equation/"
            "metadata/caption-only tables)"
        )
        return "\n".join(parts)

    for score, idx, table in scored:
        header_line = f"--- Table {idx} (score={score}, {len(table)} rows) ---"
        parts.append(header_line)
        total += len(header_line) + 1
        if total >= max_chars:
            parts.append(f"... (truncated at {max_chars} chars)")
            return "\n".join(parts)
        for j, row in enumerate(table[:max_rows_per_table]):
            line = f"Row {j}: {' | '.join(row)}"
            parts.append(line)
            total += len(line) + 1
            if total >= max_chars:
                parts.append(f"... (truncated at {max_chars} chars)")
                return "\n".join(parts)
        if len(table) > max_rows_per_table:
            parts.append(
                f"... ({len(table) - max_rows_per_table} more rows in this table)"
            )
    return "\n".join(parts)


def build_llm_prompt(html_excerpt: str, fields: list[str]) -> str:
    """Construct the extraction prompt with anti-fabrication framing.

    Stage 6.3 spike v2 (2026-05-20): tables in the excerpt are already sorted by
    measurement relevance, but their idx values are the original HTML indices
    (non-contiguous). The prompt emphasises "use the N from the Table N label in
    the dump as table_idx"; otherwise verify_record will look up the wrong cell.
    """
    fields_str = ", ".join(fields)
    return f"""You are extracting astronomical measurements from a paper's HTML tables.

REQUIRED FIELDS: {fields_str}

The tables below have been pre-filtered to remove equation / metadata /
caption-only tables, and ranked by header-keyword relevance to the requested
fields. Higher-score tables are listed first. Each table header looks like:

    --- Table N (score=K, M rows) ---

The integer N is the ORIGINAL table index in the paper's HTML (NOT a
re-numbered 0/1/2 sequence). You MUST use that N as `table_idx` in your
output — the backend reverse-lookup depends on it.

For EACH row in EACH table that has the required measurements, output a JSON
object with these keys:
  - source_name (string, e.g. galaxy/source identifier)
  - fwhm_km_s (float or null, line FWHM in km/s)
  - log_luminosity (float or null, log10 L[CII] in solar luminosities)
  - z (float or null, redshift)
  - table_idx (integer, the ORIGINAL HTML table index shown in the header above)
  - row_idx (integer, 0-based, NOT counting header row, which row in that table)
  - cell_provenance (object mapping each NON-NULL field to the EXACT cell
    text from the table — this is the literal characters in that cell)

CRITICAL ANTI-FABRICATION RULES (backend will enforce):
  1. cell_provenance values must be the literal text from the table cell.
  2. Backend will re-parse the same HTML, find the cell at (table_idx, row_idx),
     and verify your numeric value matches a number in the cell text within 1%.
  3. Any row where your number does not match the cell text within 1% will be
     REJECTED. Do not invent values. If a column is missing or unclear, set
     the field to null and omit it from cell_provenance.
  4. Only output rows where you can pinpoint exact (table_idx, row_idx).
  5. Prefer tables whose header matches FWHM / [CII] / L / z. Return [] if
     no table in the dump looks like a real measurement table.

Output a JSON array of measurement objects. No prose, no markdown fences.

PARSED TABLES:
{html_excerpt}
"""


def call_llm_anthropic(prompt: str, api_key: str, model: str = "claude-opus-4-7") -> str:
    """Single-shot LLM call via Anthropic SDK. Returns raw text content."""
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def parse_llm_json(raw: str) -> list[dict[str, Any]]:
    """Strip optional markdown fences and parse a JSON array."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    # Find the first [ ... ] block
    bracket_start = cleaned.find("[")
    bracket_end = cleaned.rfind("]")
    if bracket_start == -1 or bracket_end == -1 or bracket_end <= bracket_start:
        raise json.JSONDecodeError("no JSON array found in LLM output", cleaned, 0)
    return json.loads(cleaned[bracket_start : bracket_end + 1])


def verify_value_against_text(
    extracted: float | None,
    cell_text: str,
    tolerance: float = 0.01,
) -> tuple[bool, str]:
    """Check if `extracted` matches any number in `cell_text` within tolerance.

    Returns (ok, note). null extracted → vacuously OK.
    """
    if extracted is None:
        return True, "null value, no verification needed"
    nums = _NUMBER_RE.findall(cell_text or "")
    if not nums:
        return False, f"no numbers in cell text {cell_text!r}"
    closest_diff = float("inf")
    closest_value: float | None = None
    for n in nums:
        try:
            cell_value = float(n)
        except ValueError:
            continue
        denom = max(abs(cell_value), abs(extracted), 1e-12)
        diff = abs(extracted - cell_value) / denom
        if diff < closest_diff:
            closest_diff = diff
            closest_value = cell_value
        if diff <= tolerance:
            return True, f"matched cell value {cell_value}"
    return False, (
        f"no cell number within {tolerance*100:.1f}% of {extracted} "
        f"(closest {closest_value}, off by {closest_diff*100:.2f}%)"
    )


def verify_record(
    record: dict[str, Any],
    tables: list[list[list[str]]],
) -> ExtractedMeasurement:
    """Validate one LLM-extracted record against the parsed tables.

    Back-verification rules:
      - (table_idx, row_idx) must be within the bounds of the parsed tables
        (row_idx +1 skips the header row)
      - every non-null field must have a cell_provenance entry
      - the extracted value must be found within ±1% in the corresponding
        cell_provenance text
    """
    source_name = str(record.get("source_name") or "?")
    fwhm = _safe_float(record.get("fwhm_km_s"))
    log_lum = _safe_float(record.get("log_luminosity"))
    z_val = _safe_float(record.get("z"))
    try:
        table_idx = int(record.get("table_idx"))
        row_idx = int(record.get("row_idx"))
    except (TypeError, ValueError):
        return ExtractedMeasurement(
            source_name=source_name,
            fwhm_km_s=fwhm,
            log_luminosity=log_lum,
            z=z_val,
            table_idx=-1,
            row_idx=-1,
            cell_provenance={},
            validation_status="failed_no_cell",
            validation_notes=["LLM did not supply integer table_idx/row_idx"],
        )

    if not 0 <= table_idx < len(tables):
        return ExtractedMeasurement(
            source_name=source_name,
            fwhm_km_s=fwhm,
            log_luminosity=log_lum,
            z=z_val,
            table_idx=table_idx,
            row_idx=row_idx,
            cell_provenance={},
            validation_status="failed_no_cell",
            validation_notes=[
                f"table_idx {table_idx} out of range (paper has {len(tables)} tables)"
            ],
        )

    provenance_raw = record.get("cell_provenance")
    provenance: dict[str, str] = {}
    if isinstance(provenance_raw, dict):
        provenance = {str(k): str(v) for k, v in provenance_raw.items()}

    # Stage 6.3 spike v3 (2026-05-20): codex re-testing found that the LLM is
    # inconsistent about row_idx convention — most rows use "data 0-indexed
    # excluding the header", but it occasionally switches to "dump Row N
    # including the header". Hard-coding +1 causes out-of-bounds on the last row.
    # Fix: try two candidates (+1 and +0) and pick whichever row has more
    # cell_provenance text hits as the truth. When neither has any provenance
    # hits (or the LLM fabricated provenance), fall back to +1 (backward compat).
    candidate_rows: list[tuple[int, list[str]]] = []
    for offset in (1, 0):
        target_idx = row_idx + offset
        if 0 <= target_idx < len(tables[table_idx]):
            candidate_rows.append((offset, tables[table_idx][target_idx]))
    if not candidate_rows:
        return ExtractedMeasurement(
            source_name=source_name,
            fwhm_km_s=fwhm,
            log_luminosity=log_lum,
            z=z_val,
            table_idx=table_idx,
            row_idx=row_idx,
            cell_provenance=provenance,
            validation_status="failed_no_cell",
            validation_notes=[
                f"table {table_idx} row {row_idx} (tried HTML rows "
                f"{row_idx} and {row_idx + 1}) out of range"
            ],
        )
    # Disambiguate using cell_provenance text
    cells = candidate_rows[0][1]
    if len(candidate_rows) > 1 and provenance:
        best_match = -1
        for offset, candidate in candidate_rows:
            row_text_candidate = " | ".join(candidate).lower()
            match_count = sum(
                1
                for prov_text in provenance.values()
                if prov_text and str(prov_text).lower() in row_text_candidate
            )
            if match_count > best_match:
                best_match = match_count
                cells = candidate
    row_text = " | ".join(cells)

    notes: list[str] = []
    all_passed = True
    for field_name, value in (
        ("fwhm_km_s", fwhm),
        ("log_luminosity", log_lum),
        ("z", z_val),
    ):
        if value is None:
            continue
        # B8 anti-fabrication fix: verify the extracted value against the ACTUAL
        # re-parsed HTML row text, NEVER the LLM's self-reported cell_provenance
        # string. The old code matched against `provenance.get(field_name) or
        # row_text`, so a fabricated value paired with a fabricated provenance
        # string validated itself. cell_provenance is now used only to LOCATE the
        # row (above); missing provenance still falls back to the real row text,
        # where the value must genuinely appear to pass.
        ok, note = verify_value_against_text(value, row_text)
        if not ok:
            all_passed = False
            notes.append(f"{field_name}: {note}")
        else:
            notes.append(f"{field_name}: {note}")

    return ExtractedMeasurement(
        source_name=source_name,
        fwhm_km_s=fwhm,
        log_luminosity=log_lum,
        z=z_val,
        table_idx=table_idx,
        row_idx=row_idx,
        cell_provenance=provenance,
        validation_status="passed" if all_passed else "failed_mismatch",
        validation_notes=notes,
    )


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def extract_with_llm_and_verify(
    arxiv_id: str,
    fields: list[str],
    api_key: str,
    model: str = "claude-opus-4-7",
) -> list[ExtractedMeasurement]:
    """End-to-end: fetch paper → parse tables → LLM extract → ±1% verify.

    Returns list of ExtractedMeasurement; caller can filter by validation_status.
    Returns an empty list if the HTML has no tables. Does not raise exceptions
    (except for failures in the LLM call or HTML fetch itself).
    """
    html = fetch_paper_html(arxiv_id)
    tables = parse_html_tables(html)
    if not tables:
        logger.info("llm_paper_extractor: 0 tables parsed for %s", arxiv_id)
        return []

    excerpt = build_html_excerpt(tables)
    prompt = build_llm_prompt(excerpt, fields)
    raw = call_llm_anthropic(prompt, api_key, model=model)
    try:
        records = parse_llm_json(raw)
    except json.JSONDecodeError as exc:
        logger.warning("llm_paper_extractor: LLM JSON parse failed: %s", exc)
        return [ExtractedMeasurement(
            source_name="?",
            fwhm_km_s=None,
            log_luminosity=None,
            z=None,
            table_idx=-1,
            row_idx=-1,
            cell_provenance={},
            validation_status="failed_no_cell",
            validation_notes=[f"LLM did not return valid JSON: {exc}"],
        )]

    results: list[ExtractedMeasurement] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        results.append(verify_record(rec, tables))
    return results
