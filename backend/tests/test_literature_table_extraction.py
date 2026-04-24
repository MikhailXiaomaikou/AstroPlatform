from __future__ import annotations


def test_clean_arxiv_id_accepts_urls_and_prefixes():
    from app.api.arxiv import _clean_arxiv_id

    assert _clean_arxiv_id("arXiv:2211.04968v2") == "2211.04968"
    assert _clean_arxiv_id("https://arxiv.org/pdf/2211.04968.pdf") == "2211.04968"


def test_html_table_gets_row_citations_and_line_measurements():
    from app.api.arxiv import _attach_row_citations, _normalize_line_measurements, _parse_html_tables

    html = """
    <html><body>
      <table>
        <caption>Table 2. [CII] line measurements</caption>
        <thead><tr><th>Source</th><th>z</th><th>log L[CII]</th><th>FWHM (km/s)</th></tr></thead>
        <tbody><tr><td>MACS1149-JD1</td><td>9.11</td><td>8.15</td><td>245</td></tr></tbody>
      </table>
    </body></html>
    """
    tables = _parse_html_tables(html)
    citation = {
        "bibcode": "arXiv:2211.04968",
        "arxiv_id": "2211.04968",
        "title": "A test paper",
        "authors": ["Example, A."],
        "year": "2022",
        "source_url": "https://arxiv.org/abs/2211.04968",
    }
    tables = _attach_row_citations(tables, citation)
    measurements = _normalize_line_measurements(tables)

    assert tables[0]["row_citations"][0]["table_label"] == "Table 1"
    assert measurements[0]["source_name"] == "MACS1149-JD1"
    assert measurements[0]["log_luminosity"] == 8.15
    assert measurements[0]["fwhm_km_s"] == 245.0
    assert measurements[0]["citation"]["arxiv_id"] == "2211.04968"


def test_latex_deluxetable_can_normalize_cii_rows():
    from app.api.arxiv import _attach_row_citations, _normalize_line_measurements, _parse_latex_tables

    latex = r"""
    \begin{deluxetable}{lccc}
    \caption{CII sample}
    \label{tab:cii}
    \tablehead{\colhead{Object} & \colhead{Redshift} & \colhead{log L[CII]} & \colhead{FWHM}}
    \startdata
    A2744_YD4 & 8.38 & 8.20 & 280 \\
    \enddata
    \end{deluxetable}
    """
    tables = _attach_row_citations(_parse_latex_tables(latex), {
        "bibcode": "arXiv:2211.04968",
        "arxiv_id": "2211.04968",
        "authors": ["Example, A."],
        "year": "2022",
        "source_url": "https://arxiv.org/abs/2211.04968",
    })
    measurements = _normalize_line_measurements(tables)

    assert measurements[0]["source_name"] == "A2744_YD4"
    assert measurements[0]["redshift"] == 8.38
    assert measurements[0]["fwhm_km_s"] == 280.0


def test_raw_table_without_required_columns_is_not_measurement_ready():
    from app.api.arxiv import _attach_row_citations, _normalize_line_measurements

    tables = [{
        "table_id": "latex_1",
        "label": "Table 1",
        "caption": "Observation log",
        "columns": ["Target", "Band", "Time"],
        "rows": [["A2744_YD4", "6", "1200"]],
    }]
    tables = _attach_row_citations(tables, {"bibcode": "arXiv:2211.04968", "arxiv_id": "2211.04968"})

    assert _normalize_line_measurements(tables) == []
