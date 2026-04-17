"""arXiv paper table extraction — scrape data tables from arXiv papers."""

import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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


def _clean_arxiv_id(raw: str) -> str:
    """Extract arXiv ID from URL or plain ID."""
    raw = raw.strip()
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
            caption = re.sub(r'<[^>]+>', '', caption_match.group(1)).strip()

        # Extract header row
        thead_match = re.search(r'<thead[^>]*>(.*?)</thead>', table_html, re.DOTALL | re.IGNORECASE)
        header_html = thead_match.group(1) if thead_match else ""

        # Extract header cells
        headers = []
        for th in re.finditer(r'<th[^>]*>(.*?)</th>', header_html, re.DOTALL | re.IGNORECASE):
            headers.append(re.sub(r'<[^>]+>', '', th.group(1)).strip())

        # If no thead, try first row
        if not headers:
            first_row = re.search(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
            if first_row:
                for cell in re.finditer(r'<t[hd][^>]*>(.*?)</t[hd]>', first_row.group(1), re.DOTALL | re.IGNORECASE):
                    headers.append(re.sub(r'<[^>]+>', '', cell.group(1)).strip())

        # Extract data rows
        rows: list[list[str]] = []
        tbody_match = re.search(r'<tbody[^>]*>(.*?)</tbody>', table_html, re.DOTALL | re.IGNORECASE)
        body_html = tbody_match.group(1) if tbody_match else table_html

        for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', body_html, re.DOTALL | re.IGNORECASE):
            cells = []
            for td in re.finditer(r'<td[^>]*>(.*?)</td>', tr.group(1), re.DOTALL | re.IGNORECASE):
                text = re.sub(r'<[^>]+>', '', td.group(1)).strip()
                text = re.sub(r'\s+', ' ', text)
                cells.append(text)
            if cells and len(cells) > 1:  # Skip single-cell rows (likely captions)
                rows.append(cells)

        if headers and rows:
            tables.append({
                "name": caption or f"Table {idx + 1}",
                "columns": headers,
                "rows": rows[:200],  # Limit rows
                "row_count": len(rows),
            })

    return tables


def _parse_latex_tables(source: str) -> list[dict]:
    """Parse LaTeX tabular environments from arXiv source."""
    tables = []

    # Find \begin{table}...\end{table} or \begin{tabular}...\end{tabular}
    table_envs = re.finditer(
        r'\\begin\{(?:table\*?|deluxetable\*?)\}(.*?)\\end\{(?:table\*?|deluxetable\*?)\}',
        source, re.DOTALL
    )

    for idx, env_match in enumerate(table_envs):
        block = env_match.group(1)

        # Extract caption
        caption_match = re.search(r'\\caption\{([^}]+)\}', block)
        caption = caption_match.group(1) if caption_match else f"Table {idx + 1}"
        caption = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', caption)  # strip latex commands

        # Find tabular content
        tabular = re.search(r'\\begin\{tabular[*]?\}\{[^}]*\}(.*?)\\end\{tabular[*]?\}', block, re.DOTALL)
        if not tabular:
            # Try deluxetable format
            tabular_content = block
        else:
            tabular_content = tabular.group(1)

        # Split by \\ (row separator)
        raw_rows = re.split(r'\\\\', tabular_content)
        parsed_rows: list[list[str]] = []

        for row in raw_rows:
            row = row.strip()
            if not row or row.startswith('\\hline') or row.startswith('\\toprule') or row.startswith('\\midrule') or row.startswith('\\bottomrule'):
                continue
            row = re.sub(r'\\hline|\\toprule|\\midrule|\\bottomrule|\\endhead|\\tableline', '', row)
            cells = [c.strip() for c in row.split('&')]
            cells = [re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', c) for c in cells]  # strip latex
            cells = [re.sub(r'[{}$]', '', c).strip() for c in cells]
            if any(c for c in cells):
                parsed_rows.append(cells)

        if len(parsed_rows) >= 2:
            headers = parsed_rows[0]
            data_rows = parsed_rows[1:]
            tables.append({
                "name": caption,
                "columns": headers,
                "rows": data_rows[:200],
                "row_count": len(data_rows),
            })

    return tables


@router.post("/extract-tables", response_model=ArxivTableResponse)
async def extract_arxiv_tables(
    req: ArxivTableRequest,
    _user: User = Depends(get_current_user),
):
    """Extract data tables from an arXiv paper.

    M19: gated with get_current_user so this endpoint cannot be used as a
    DoS amplifier against ar5iv / arxiv.org by unauthenticated clients.
    """
    arxiv_id = _clean_arxiv_id(req.arxiv_id)
    if not arxiv_id:
        raise HTTPException(status_code=400, detail="Invalid arXiv ID")

    if not re.match(r'^[\d]{4}\.[\d]{4,5}$', arxiv_id) and not re.match(r'^[a-z-]+/[\d]{7}$', arxiv_id):
        raise HTTPException(status_code=400, detail="Invalid arXiv ID format")

    title = ""
    all_tables: list[dict] = []

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Try HTML version first (ar5iv)
        try:
            html_url = f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
            resp = await client.get(html_url)
            if resp.status_code == 200:
                html = resp.text
                # Extract title
                title_match = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                if title_match:
                    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                    title = title.replace(" - ar5iv", "").strip()
                all_tables = _parse_html_tables(html)
        except Exception as e:
            logger.warning("ar5iv fetch failed for %s: %s", arxiv_id, e)

        # If no tables from HTML, try LaTeX source
        if not all_tables:
            try:
                src_url = f"https://arxiv.org/e-print/{arxiv_id}"
                resp = await client.get(src_url, headers={"Accept": "text/plain"})
                if resp.status_code == 200:
                    content = resp.text
                    # Check if it's LaTeX
                    if "\\begin{" in content:
                        all_tables = _parse_latex_tables(content)
                        if not title:
                            t_match = re.search(r'\\title\{([^}]+)\}', content)
                            if t_match:
                                title = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', t_match.group(1))
                                title = re.sub(r'[{}$]', '', title).strip()
            except Exception as e:
                logger.warning("arXiv source fetch failed for %s: %s", arxiv_id, e)

        # Get title from API if still missing
        if not title:
            try:
                api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    t_match = re.search(r'<title[^>]*>(.*?)</title>', resp.text, re.DOTALL)
                    if t_match:
                        title = t_match.group(1).strip()
            except Exception:
                pass

    if not all_tables:
        raise HTTPException(status_code=404, detail=f"No tables found in arXiv:{arxiv_id}")

    return ArxivTableResponse(arxiv_id=arxiv_id, title=title or arxiv_id, tables=all_tables)
