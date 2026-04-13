"""Rule-based scientific rigor checks for session-derived paper drafts."""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ChatSession
from app.services.event_collector import track_event
from app.services.paper_generator import _extract_actions


def _build_check(name: str, status: str, details: str, recommendation: str) -> dict:
    return {
        "name": name,
        "status": status,
        "details": details,
        "recommendation": recommendation,
    }


@track_event("analysis.function_called")
async def validate_analysis(session_id: str, db: AsyncSession) -> dict:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    artifacts = _extract_actions(session.messages or [])
    combined_text = "\n".join(
        artifacts.user_prompts
        + artifacts.assistant_text
        + [str(action.get("query", "")) for action in artifacts.adql_calls]
        + [str(action.get("code", "")) for action in artifacts.python_calls]
    ).lower()

    checks: list[dict] = []

    # Unit consistency
    unit_status = "PASS"
    unit_details = "No obvious unit mismatches detected in the recorded session."
    unit_reco = "Document all unit conversions explicitly in the final manuscript."
    if ("arcsec" in combined_text and "degree" in combined_text and "convert" not in combined_text):
        unit_status = "WARN"
        unit_details = "Angular quantities mention both arcsec and degrees without an explicit conversion step."
        unit_reco = "State the conversion between angular units before combining measurements."
    elif ("jy" in combined_text and "erg/s/cm" in combined_text and "convert" not in combined_text):
        unit_status = "WARN"
        unit_details = "Flux-like quantities appear in different systems without a documented conversion."
        unit_reco = "Convert all fluxes to a common system before drawing comparisons."
    checks.append(_build_check("unit_consistency", unit_status, unit_details, unit_reco))

    # Statistical method audit
    stat_status = "PASS"
    stat_details = "No immediately suspicious statistical pattern was detected."
    stat_reco = "Report assumptions, effect sizes, and confidence intervals together."
    p_value_mentions = len(re.findall(r"p\s*[<=>]\s*0\.\d+", combined_text))
    if "pearson" in combined_text and not any(token in combined_text for token in ("shapiro", "kolmogorov", "spearman")):
        stat_status = "WARN"
        stat_details = "Pearson correlation appears without evidence of a normality check."
        stat_reco = "Add a Shapiro-Wilk or KS test, or justify Pearson over Spearman."
    elif p_value_mentions > 3 and not any(token in combined_text for token in ("bonferroni", "benjamini", "fdr", "bh correction")):
        stat_status = "WARN"
        stat_details = "Multiple p-values were reported without a multiple-testing correction."
        stat_reco = "Apply a Bonferroni or Benjamini-Hochberg correction."
    checks.append(_build_check("statistical_method_audit", stat_status, stat_details, stat_reco))

    # Conclusion-data consistency
    conclusion_status = "PASS"
    conclusion_details = "No unsupported conclusion pattern was detected."
    conclusion_reco = "Ensure every major claim is paired with the supporting statistic or measurement."
    if "significant" in combined_text and "p < 0.05" not in combined_text and "p-value" not in combined_text:
        conclusion_status = "WARN"
        conclusion_details = "The session uses significance language without explicitly reporting the supporting p-value."
        conclusion_reco = "Add the test statistic, p-value, and uncertainty when claiming significance."
    if re.search(r"\bs/?n\s*[<:=]\s*([0-2](\.\d+)?)", combined_text):
        conclusion_status = "FAIL"
        conclusion_details = "A detection claim appears alongside an S/N below 3."
        conclusion_reco = "Recast the statement as a tentative signal or gather deeper data."
    checks.append(_build_check("conclusion_data_consistency", conclusion_status, conclusion_details, conclusion_reco))

    # Completeness
    completeness_status = "PASS"
    completeness_details = "The session includes basic analysis context."
    completeness_reco = "Add systematic uncertainties, extinction handling, and literature comparison where relevant."
    if any(token in combined_text for token in ("bp_rp", "phot_g_mean_mag", "optical", "magnitude")) and "extinction" not in combined_text:
        completeness_status = "WARN"
        completeness_details = "Optical photometry appears without an explicit extinction correction discussion."
        completeness_reco = "Consider applying or discussing extinction corrections (e.g. CCM89 or F99)."
    elif not artifacts.bibcodes:
        completeness_status = "WARN"
        completeness_details = "No literature references were recorded in the session."
        completeness_reco = "Run a literature search and compare the findings to prior work."
    checks.append(_build_check("completeness", completeness_status, completeness_details, completeness_reco))

    # Provenance
    provenance_status = "PASS"
    provenance_details = "Queries and data sources were captured in the session history."
    provenance_reco = "Retain archive names, data release identifiers, and query strings in the appendix."
    if not artifacts.search_calls and not artifacts.adql_calls:
        provenance_status = "FAIL"
        provenance_details = "No recorded search or ADQL actions were found for this session."
        provenance_reco = "Run the analysis from a saved session that includes the underlying data acquisition steps."
    elif any("gaia_source" in str(action.get("query", "")).lower() for action in artifacts.adql_calls) and "gaiadr3" not in combined_text:
        provenance_status = "WARN"
        provenance_details = "Gaia queries were present, but the data release was not consistently obvious."
        provenance_reco = "Explicitly state the Gaia release (e.g. DR3) in the final draft."
    checks.append(_build_check("data_provenance", provenance_status, provenance_details, provenance_reco))

    fail_count = sum(1 for check in checks if check["status"] == "FAIL")
    warn_count = sum(1 for check in checks if check["status"] == "WARN")
    score = max(0.0, min(1.0, 1.0 - 0.25 * fail_count - 0.08 * warn_count))
    overall_status = "FAIL" if fail_count else ("WARN" if warn_count else "PASS")

    return {
        "overall_status": overall_status,
        "score": round(score, 2),
        "checks": checks,
    }
