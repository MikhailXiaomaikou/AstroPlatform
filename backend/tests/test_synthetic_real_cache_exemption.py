"""PART AC C3 — code-content reverse exemption for the G3.2 SYNTHETIC
tainter.

R2.4 M3 audit reproducer:

  AI ran 5 search_literature calls (1 returned 0 hits → empty_data_fetches
  picked up search_literature) + 2 extract_literature_tables (both ok)
  + 1 fit_line_lfr (PARTIAL) + 4 run_python (all reading the real cache
  via `get_cached_results("latest_literature_tables")`).

  Pre-AC the SYNTHETIC banner said
      "1 SYNTHETIC of 12 tools this turn ... Synthetic: run_python"
  even though those run_python calls were processing genuine ALPINE rows.
  The AI in prose actually argued back at the platform.

The fix is `_run_python_code_reads_real_cache`: G3.2 inspects the code
body and grants an exemption when it sees a real cache reader, instead
of relying on the input.data_source declaration only.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "code, expected, label",
    [
        # Real-cache readers — exemption granted.
        ("rows = get_cached_results('latest_literature_tables')", True, "literature cache"),
        ("rows = get_adql_results()", True, "adql results helper"),
        ("rows = get_adql_result_sets()", True, "adql result sets"),
        ("rows = get_search_results()", True, "search results helper"),
        ("from astropy.io import fits\nhdul = fits.open(path)", True, "fits.open"),
        ("from astropy.table import Table\nt = Table.read(path)", True, "Table.read"),
        ("import lightkurve as lk\nlc = lk.search_lightcurve('TIC...')", True, "lightkurve module"),
        ("import pandas as pd\ndf = pd.read_csv('alpine.csv')", True, "pd.read_csv"),
        ("# uses latest_literature_tables key\nrows = get_cached_results('foo')",
         True, "literal key reference"),
        ("from astroquery.simbad import Simbad", True, "astroquery import"),
        # Pure synthetic — no exemption.
        ("import numpy as np\nx = np.random.normal(0, 1, 100)", False, "np.random only"),
        ("t = np.linspace(0, 100, 50)", False, "linspace only"),
        ("flux = [0.99] * 100", False, "constant list"),
        ("print('hello world')", False, "literal print"),
        ("", False, "empty code"),
        # Edge — passes through helper signature.
        ("def helper():\n    return get_cached_results('x')", True,
         "helper definition that calls cache"),
        # PART AD: comment / string spoofing must NOT grant exemption — the
        # old substring scan returned True for these (the hole we closed).
        ("# rows = get_cached_results('latest_literature_tables')\n"
         "import numpy as np\nx = np.random.normal(0, 1, 100)",
         False, "reader only in a comment"),
        ('note = "get_adql_results()"\n'
         "import numpy as np\ny = np.random.normal(0, 1, 50)",
         False, "reader only in a string literal"),
    ],
)
def test_run_python_code_reads_real_cache(code: str, expected: bool, label: str) -> None:
    from app.api.chat import _run_python_code_reads_real_cache

    assert _run_python_code_reads_real_cache(code) is expected, (
        f"{label}: expected {expected} for code {code!r}"
    )


def test_run_python_code_reads_real_cache_handles_non_string_input() -> None:
    """Defensive: if `code` is missing / None / dict, must not crash."""
    from app.api.chat import _run_python_code_reads_real_cache

    assert _run_python_code_reads_real_cache(None) is False  # type: ignore[arg-type]
    assert _run_python_code_reads_real_cache({}) is False  # type: ignore[arg-type]
    assert _run_python_code_reads_real_cache([]) is False  # type: ignore[arg-type]
