"""Automatic paper draft generation from saved analysis sessions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.citations import _get_bibtex_sync
from app.models.schemas import ChatSession
from app.services.event_collector import track_event

_BIBCODE_RE = re.compile(r"\b(\d{4}[A-Za-z][A-Za-z&.]+\.+\S+)")

_ACKNOWLEDGMENTS = {
    "gaia": "This work has made use of data from the European Space Agency (ESA) mission Gaia.",
    "sdss": "Funding for the Sloan Digital Sky Survey (SDSS) has been provided by the Alfred P. Sloan Foundation and participating institutions.",
    "simbad": "This research has made use of the SIMBAD database, operated at CDS, Strasbourg, France.",
    "vizier": "This research has made use of the VizieR catalogue access tool, CDS, Strasbourg, France.",
    "mast": "This work made use of observations from the Mikulski Archive for Space Telescopes (MAST).",
    "jwst": "This work made use of archival observations from the James Webb Space Telescope via MAST.",
    "ned": "This research has made use of the NASA/IPAC Extragalactic Database (NED).",
    "chandra": "This work made use of data obtained from the Chandra Data Archive.",
}


@dataclass
class SessionArtifacts:
    session: ChatSession | None
    user_prompts: list[str]
    assistant_text: list[str]
    search_calls: list[dict]
    adql_calls: list[dict]
    python_calls: list[dict]
    pipeline_calls: list[dict]
    figure_refs: list[dict]
    bibcodes: list[str]


def _escape_latex(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def _format_table_cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if abs(value) >= 1_000 or (0 < abs(value) < 1e-3):
            return f"{value:.3e}"
        return f"{value:.5g}"
    return str(value)


def _extract_actions(messages: list[dict]) -> SessionArtifacts:
    user_prompts: list[str] = []
    assistant_text: list[str] = []
    search_calls: list[dict] = []
    adql_calls: list[dict] = []
    python_calls: list[dict] = []
    pipeline_calls: list[dict] = []
    figure_refs: list[dict] = []
    bibcodes: list[str] = []

    for message in messages:
        role = message.get("role", "")
        content = str(message.get("content", "") or "")
        if role == "user" and content:
            user_prompts.append(content)
        elif role == "assistant" and content:
            assistant_text.append(content)
            bibcodes.extend(_BIBCODE_RE.findall(content))

        for action in message.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("action", ""))
            if action_name in {"search", "search_objects"}:
                search_calls.append(action)
            elif action_name in {"adql", "run_adql"}:
                adql_calls.append(action)
            elif action_name == "run_python":
                python_calls.append(action)
            elif action_name in {"generate_pipeline", "run_pipeline", "modify_pipeline"}:
                pipeline_calls.append(action)
            elif action_name == "plot":
                figure_refs.append({
                    "figure_number": len(figure_refs) + 1,
                    "caption": f"{action.get('chart_type', 'Plot')} generated during the session.",
                    "session_figure_ref": f"plot_{len(figure_refs) + 1}",
                })

            tool_result = action.get("tool_result")
            if isinstance(tool_result, dict) and tool_result.get("bibcode"):
                bibcodes.append(str(tool_result["bibcode"]))
            elif isinstance(tool_result, list):
                for item in tool_result:
                    if isinstance(item, dict) and item.get("bibcode"):
                        bibcodes.append(str(item["bibcode"]))

    return SessionArtifacts(
        session=None,  # patched by caller
        user_prompts=user_prompts,
        assistant_text=assistant_text,
        search_calls=search_calls,
        adql_calls=adql_calls,
        python_calls=python_calls,
        pipeline_calls=pipeline_calls,
        figure_refs=figure_refs,
        bibcodes=list(dict.fromkeys(bibcodes)),
    )


def _summarize_data_sources(artifacts: SessionArtifacts) -> tuple[list[str], str]:
    used_sources: list[str] = []
    data_lines: list[str] = []
    for action in artifacts.search_calls:
        sources = action.get("sources") or []
        if isinstance(sources, list):
            used_sources.extend(str(source).lower() for source in sources)
        data_lines.append(
            f"Catalog search for {action.get('query', 'target')} using {', '.join(map(str, sources or ['simbad']))}."
        )
    for action in artifacts.adql_calls:
        service = str(action.get("service", "gaia")).lower()
        used_sources.append(service)
        query = str(action.get("query", "")).strip().replace("\n", " ")
        data_lines.append(f"ADQL query on {service}: {query[:180]}")

    deduped_sources = list(dict.fromkeys(source for source in used_sources if source))
    data_text = " ".join(data_lines) or "The session primarily consists of interactive analysis and interpretation."
    return deduped_sources, data_text


def _build_acknowledgments(sources: list[str]) -> str:
    lines = [_ACKNOWLEDGMENTS[source] for source in sources if source in _ACKNOWLEDGMENTS]
    if not lines:
        lines.append("This work made use of the Standard Astro research platform for interactive data analysis.")
    return " ".join(dict.fromkeys(lines))


def _build_results_tables(artifacts: SessionArtifacts) -> list[dict]:
    tables: list[dict] = []
    for action in artifacts.search_calls:
        tool_result = action.get("tool_result")
        if isinstance(tool_result, list) and tool_result:
            first_rows = tool_result[:5]
            if isinstance(first_rows[0], dict):
                headers = list(first_rows[0].keys())[:6]
                rows = [headers]
                for row in first_rows:
                    rows.append([row.get(header) for header in headers])
                tables.append({
                    "table_number": len(tables) + 1,
                    "caption": f"Representative catalog results for {action.get('query', 'the target')}.",
                    "data": rows,
                })
    return tables


def _build_default_paper_json(artifacts: SessionArtifacts, journal_format: str) -> dict:
    sources, data_description = _summarize_data_sources(artifacts)
    primary_question = artifacts.user_prompts[0] if artifacts.user_prompts else "Exploratory astronomical data analysis"
    discussion_citations = artifacts.bibcodes[:5]
    intro_citations = artifacts.bibcodes[:2]
    methods_citations = artifacts.bibcodes[2:3]
    results_text = artifacts.assistant_text[-1] if artifacts.assistant_text else "The session produced a set of exploratory findings that should be reviewed and refined before submission."

    return {
        "title": primary_question[:120],
        "abstract": (
            "This draft summarizes an interactive Standard Astro analysis session. "
            f"The investigation focused on {primary_question[:120]}. "
            f"Data were gathered from {', '.join(sources) if sources else 'multiple astronomical archives'} "
            "and inspected with a combination of catalog queries, AI-assisted reasoning, and reproducible analysis steps. "
            "The present draft should be treated as a structured starting point for a full manuscript, with explicit attention to "
            "sample definitions, uncertainties, and comparison to prior work."
        ),
        "introduction": {
            "text": (
                f"This session was motivated by the question: {primary_question}. "
                "The goal of this draft is to convert the interactive exploration into a manuscript-ready narrative."
            ),
            "citations": intro_citations,
        },
        "data_and_methods": {
            "data_sources": data_description,
            "analysis_methods": (
                f"The analysis used {len(artifacts.search_calls)} catalog search steps, "
                f"{len(artifacts.adql_calls)} ADQL queries, {len(artifacts.python_calls)} Python analysis steps, "
                f"and {len(artifacts.pipeline_calls)} pipeline-related operations."
            ),
            "citations": methods_citations,
        },
        "results": {
            "text": results_text,
            "figures": artifacts.figure_refs,
            "tables": _build_results_tables(artifacts),
        },
        "discussion": {
            "text": (
                "The current discussion should be expanded with quantitative uncertainty estimates, comparison to the literature, "
                "and explicit caveats tied to sample selection and data quality."
            ),
            "citations": discussion_citations,
        },
        "conclusions": (
            "The session establishes a reproducible foundation for a paper draft, but the final manuscript should confirm all "
            "numerical claims, uncertainties, and literature context before submission."
        ),
        "acknowledgments": _build_acknowledgments(sources),
        "journal_format": journal_format,
    }


def render_latex(paper_json: dict, format: str = "aastex") -> str:
    format_key = format.lower()
    if format_key == "mnras":
        documentclass = r"\documentclass[a4paper,fleqn,usenatbib]{mnras}"
    elif format_key in {"aa", "a&a"}:
        documentclass = r"\documentclass{aa}"
    else:
        documentclass = r"\documentclass[twocolumn]{aastex631}"

    lines = [
        documentclass,
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{longtable}",
        r"\usepackage{hyperref}",
        r"\begin{document}",
        rf"\title{{{_escape_latex(paper_json.get('title', 'Standard Astro Paper Draft'))}}}",
        r"\author{Standard Astro User}",
        r"\begin{abstract}",
        _escape_latex(paper_json.get("abstract", "")),
        r"\end{abstract}",
        r"\maketitle",
    ]

    def _section(title: str, body: str):
        lines.append(rf"\section{{{_escape_latex(title)}}}")
        lines.append(_escape_latex(body))
        lines.append("")

    intro = paper_json.get("introduction", {})
    methods = paper_json.get("data_and_methods", {})
    results = paper_json.get("results", {})
    discussion = paper_json.get("discussion", {})

    _section("Introduction", str(intro.get("text", "")))
    _section(
        "Data and Methods",
        f"{methods.get('data_sources', '')}\n\n{methods.get('analysis_methods', '')}",
    )
    _section("Results", str(results.get("text", "")))

    for figure in results.get("figures", [])[:5]:
        figure_ref = _escape_latex(str(figure.get("session_figure_ref", "session_figure")))
        lines.extend([
            r"\begin{figure}",
            r"\centering",
            rf"\fbox{{\parbox{{0.9\columnwidth}}{{Placeholder for {figure_ref}}}}}",
            rf"\caption{{{_escape_latex(str(figure.get('caption', 'Session figure')))}}}",
            r"\end{figure}",
        ])

    for table in results.get("tables", [])[:3]:
        rows = table.get("data", [])
        if not rows:
            continue
        column_count = len(rows[0])
        colspec = "l" * max(1, column_count)
        lines.extend([
            r"\begin{table}",
            r"\centering",
            rf"\caption{{{_escape_latex(str(table.get('caption', 'Session table')))}}}",
            rf"\begin{{tabular}}{{{colspec}}}",
            r"\toprule",
            " & ".join(_escape_latex(str(cell)) for cell in rows[0]) + r" \\",
            r"\midrule",
        ])
        for row in rows[1:]:
            lines.append(" & ".join(_escape_latex(_format_table_cell(cell)) for cell in row) + r" \\")
        lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])

    _section("Discussion", str(discussion.get("text", "")))
    _section("Conclusions", str(paper_json.get("conclusions", "")))
    _section("Acknowledgments", str(paper_json.get("acknowledgments", "")))
    lines.append(r"\end{document}")
    return "\n".join(lines)


async def generate_reproducibility_appendix(session_id: str, db: AsyncSession) -> str:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    artifacts = _extract_actions(session.messages or [])
    query_lines: list[str] = []
    for action in artifacts.search_calls:
        query_lines.append(f"- Search: {action.get('query', '')} | sources={action.get('sources', [])}")
    for action in artifacts.adql_calls:
        query_lines.append(f"- ADQL ({action.get('service', 'gaia')}): {str(action.get('query', ''))[:200]}")

    pipeline_lines = [
        f"- Pipeline action: {action.get('action', '')}"
        for action in artifacts.pipeline_calls
    ] or ["- No pipeline DAGs recorded in this session."]

    code_lines = [
        str(action.get("code") or (action.get("tool_input") or {}).get("code") or "")[:600]
        for action in artifacts.python_calls
    ] or ["No custom Python analysis was recorded."]

    versions = {"platform": "Standard Astro"}
    for package in ("numpy", "scipy", "astropy"):
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = "unknown"

    appendix = [
        r"\appendix",
        r"\section{Reproducibility Appendix}",
        r"\subsection{Queries Executed}",
        _escape_latex("\n".join(query_lines or ["No catalog queries were recorded."])),
        r"\subsection{Pipeline DAG Description}",
        _escape_latex("\n".join(pipeline_lines)),
        r"\subsection{Python Code}",
        _escape_latex("\n\n".join(code_lines)),
        r"\subsection{Software Versions}",
        _escape_latex(json.dumps(versions, indent=2)),
        r"\subsection{Random Seeds}",
        "No explicit stochastic seeds were captured in this session.",
    ]
    return "\n".join(appendix)


@track_event("export.paper_draft")
async def generate_paper_draft(session_id: str, journal_format: str, db: AsyncSession) -> dict:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    format_key = (journal_format or "aastex").strip().lower()
    if format_key not in {"aastex", "mnras", "aa", "a&a"}:
        raise HTTPException(status_code=400, detail="Unsupported journal format")

    artifacts = _extract_actions(session.messages or [])
    artifacts.session = session
    paper_json = _build_default_paper_json(artifacts, format_key)
    appendix = await generate_reproducibility_appendix(str(session.id), db)
    latex_source = render_latex(paper_json, format_key) + "\n\n" + appendix

    bibtex_entries: list[str] = []
    for bibcode in artifacts.bibcodes[:30]:
        try:
            bibtex_entries.append(_get_bibtex_sync(bibcode))
        except Exception:
            bibtex_entries.append(f"% BibTeX lookup failed for {bibcode}")
    bibtex = "\n\n".join(entry for entry in bibtex_entries if entry.strip()) or "% No citations were found in this session."

    return {
        "paper_json": paper_json,
        "latex_source": latex_source,
        "bibtex": bibtex,
        "session_history": {
            "message_count": len(session.messages or []),
            "queries": len(artifacts.search_calls) + len(artifacts.adql_calls),
            "pipelines": len(artifacts.pipeline_calls),
            "figures": len(artifacts.figure_refs),
            "citations": len(artifacts.bibcodes),
        },
    }
