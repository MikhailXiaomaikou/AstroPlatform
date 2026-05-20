"""Stage 6.3 spike (2026-05-20): LLM 抽论文表格 + ±1% 反查 prototype.

设计哲学:
  现有 `_normalize_line_measurements` (ai_tools.py) 是 monolithic regex
  matcher, 一把钥匙开一把锁, 跨 paper schema 立即掉链子. 但纯让 LLM
  读数字又跟平台防造假主线冲突 (LLM 可能编数字).

  本 module 走 hybrid 路:
    1. LLM 读 paper 给数字, 但要求附 (table_idx, row_idx, cell_provenance)
    2. backend 拿这 3 个坐标回到原 HTML, 用 regex 在 cell 文字里找数字
    3. LLM 给的数字跟 cell 找到的数字 ±1% 不一致 → 拒, 不存 cache

  这样 LLM 自由发挥 schema 推断, backend 守 anti-fabrication 底线.

  独立 spike module, 不接 ai_tools dispatch / claim_validator / chat.py
  pipeline. 跑通后再决定升级.

依赖:
  - httpx (项目已用)
  - beautifulsoup4 (项目已用)
  - anthropic SDK (项目已用)
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

# Stage 6.3 spike v2 (2026-05-20): codex 复测发现 ALPINE 2002.00962 默认
# excerpt 把 budget 浪费在 formula/metadata 表上, 真 measurement table
# (table 25) 被截掉. 改造 build_html_excerpt 按 measurement-keyword
# score 排序, 强相关表先送 LLM.
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
# LaTeX equation markers — 表里大量出现表明是 formula table 不是 data table.
_LATEX_EQ_MARKERS = (
    "\\frac", "\\sum", "\\int", "\\partial",
    "\\mathrm", "\\Pi", "\\Sigma", "\\equiv",
    "\\nabla", "\\dot", "\\bar",
)


@dataclass
class ExtractedMeasurement:
    """LLM 抽出 + backend ±1% 反查后的一条 measurement."""

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

    ar5iv 是 LaTeX→HTML 转换服务, 比直接抓 LaTeX 源码可靠 (大部分 cosmology
    论文都在). 失败时 caller 决定 fallback (e.g. arxiv.org HTML / LaTeX
    源码), 本 spike 不做.
    """
    cleaned = arxiv_id.strip().replace("arXiv:", "").replace("arxiv:", "")
    url = _AR5IV_URL_TEMPLATE.format(arxiv_id=cleaned)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def parse_html_tables(html: str) -> list[list[list[str]]]:
    """Return list of tables; each table is list of rows; each row is list of cell strings.

    跟 ai_tools._parse_html_tables 同思路但本 module 自包含, 不依赖 arxiv.py.
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
    """Stage 6.3 spike v2: 给 table 算 measurement-table 相关度分数.

    看第 1 行 (header) 是否含已知 measurement column 关键词. 多行 sample
    表加 bonus. 0 分表示完全不像 measurement table.
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
    """Stage 6.3 spike v2: 判 table 是否 formula / metadata / caption-only.

    - 行数 < 2: 不是真 data table
    - 第 1 行只 1 个 cell: caption / metadata
    - 大量 LaTeX equation markers (\\frac / \\sum / etc.): formula table
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

    Stage 6.3 spike v2 (2026-05-20): 不再按 table 顺序截断. 改成:
      1. 过滤 low_value (formula / metadata / caption-only) 表
      2. 给剩下表打 measurement-relevance 分数
      3. 按 score 降序送进 budget — 高分表优先, score=0 表跳过

    保留原始 `table_idx` (不重编号), 因为 verify_record 要用 table_idx 反查
    原 HTML cell, 必须跟 LLM 看到的 table_idx 一致.

    Codex 复测发现 ALPINE 2002.00962 表 25 才是真 measurement, 但默认实现
    把 budget 在前 24 个 equation/metadata 表里耗光. v2 把 table 25 排到
    最前送进去.
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

    Stage 6.3 spike v2 (2026-05-20): tables 在 excerpt 里已经按 measurement-
    relevance 排过, 但 idx 是原 HTML idx (不连续). prompt 强调"用 dump 里
    给的 Table N 标签的 N 作为 table_idx", 否则 verify_record 会反查错位.
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
    # 找第一个 [ ... ] block
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

    反查规则:
      - (table_idx, row_idx) 必须在 parsed tables 范围内 (row_idx +1 跳 header)
      - 每个 non-null 字段必须有 cell_provenance 入口
      - 对应 cell_provenance 文字里必须 ±1% 找到 extracted value
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

    provenance_raw = record.get("cell_provenance")
    provenance: dict[str, str] = {}
    if isinstance(provenance_raw, dict):
        provenance = {str(k): str(v) for k, v in provenance_raw.items()}

    # Stage 6.3 spike v3 (2026-05-20): codex 复测发现 LLM 在 row_idx convention
    # 上不稳定 — 大部分行用 "data 0-indexed 不含 header", 偶尔切换到 "dump
    # Row N (含 header)". 死定 +1 让最后行越界.
    # 修法: try 两个 candidate (+1 和 +0), 选 cell_provenance 文字命中更多
    # 的那行作 truth. provenance 全没命中时 (or LLM 编了 provenance) 取
    # +1 作为 default (兼容旧 convention).
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
    # 用 cell_provenance 文字 disambiguation
    best_offset = candidate_rows[0][0]
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
                best_offset = offset
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
        cell_text = provenance.get(field_name) or row_text
        ok, note = verify_value_against_text(value, cell_text)
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
    Empty list if HTML has no tables. 不抛异常 (除 LLM 调用 / HTML 抓取
    本身 fail).
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
