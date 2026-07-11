"""Deterministic prompt-analysis routing for cosmology / research workflows,
inline-statistics extraction, and the cosmology anchor-comparison gate.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

import re
import uuid
from typing import Any


# Shared canonical-family vocabulary for prompt routing and deterministic
# release-disclosure summaries.  Keep expanded survey names here so a router
# alias cannot silently bypass the downstream identity check.
COSMOLOGY_DATASET_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "act": ("act", "atacama cosmology telescope"),
    "des": ("des y3", "des-y3", "dark energy survey", "des"),
    "desi": ("desi", "dark energy spectroscopic instrument"),
    "hsc": ("hsc", "hyper suprime-cam", "hyper suprime cam", "hyper suprime"),
    "kids": (
        "kids",
        "kilo-degree survey",
        "kilo degree survey",
        "kilo-degree",
        "kilo degree",
    ),
    "pantheon": ("pantheon",),
    "planck": ("planck",),
    "shoes": ("sh0es", "shoes"),
    "spt": ("spt", "south pole telescope"),
}

# A bare infix plus normally joins datasets.  Only these canonical families
# currently use an attached plus as part of the registered release identity.
COSMOLOGY_PLUS_RELEASE_FAMILIES = frozenset({"pantheon"})


def _dataset_mention_is_non_execution(
    prompt: str,
    start: int,
    end: int,
    *,
    comparison_is_execution: bool = False,
) -> bool:
    """Ignore datasets explicitly excluded or mentioned only for explanation."""
    before = str(prompt or "")[max(0, start - 96) : start].lower()
    after = str(prompt or "")[end : end + 96].lower()
    clause_before = re.split(r"[.;\n]|,\s*(?:and\s+)?", before)[-1]
    clause_after = re.split(r"[.;\n]", after)[0]
    execution_pattern = re.compile(
        r"\b(?:run|use|execute|select|fit|analy[sz]e)\b"
    )
    execution_matches = list(execution_pattern.finditer(clause_before))

    if re.search(r"\bwith\s+(?:and|/)\s*without\s*$", clause_before):
        return False

    if re.search(
        r"\b(?:do\s+not|don't|never)\s+"
        r"(?:run|use|execute|select|fit|analy[sz]e)\b[^.;\n]{0,48}$",
        clause_before,
    ):
        return True
    if re.search(r"\b(?:and\s+|but\s+)?not\s*$", clause_before):
        return True
    if re.search(
        r"\b(?:without|do\s+not|don't|never|not\s+(?:be\s+)?)\b"
        r"[^.;\n]{0,32}\b"
        r"(?:run|use|execute|select|fit|analy[sz]e)(?:d|ing)?\b",
        clause_after,
    ):
        return True

    # Later anaphoric execution applies to datasets introduced earlier in an
    # explanation: "compare A and B by running both".
    if re.search(
        r"\b(?:then\s+|by\s+)?(?:run(?:ning)?|us(?:e|ing)|execut(?:e|ing)|"
        r"select(?:ing)?|fit(?:ting)?|analy[sz](?:e|ing))\s+"
        r"(?:both|all|them|these|those|it|the\s+two)\b",
        after,
    ):
        return False

    exclusion_matches = list(re.finditer(
        r"\b(?:without|excluding?|avoid(?:ing)?|rather\s+than|instead\s+of)\b",
        clause_before,
    ))
    if exclusion_matches:
        exclusion = exclusion_matches[-1]
        executions_after_exclusion = [
            match
            for match in execution_matches
            if match.start() > exclusion.end()
        ]
        exclusion_tail = clause_before[exclusion.end() :].lstrip()
        directly_excluded_execution = bool(re.match(
            r"(?:to\s+)?(?:run|use|execute|select|fit|analy[sz]e)\b",
            exclusion_tail,
        ))
        if not executions_after_exclusion or directly_excluded_execution:
            return True

    explanation_verbs = (
        r"explain|describe|discuss|mention|clarify"
        if comparison_is_execution
        else r"explain|describe|discuss|mention|clarify|compare|contrast|distinguish"
    )
    explain_matches = list(re.finditer(
        rf"\b(?:{explanation_verbs})\b",
        clause_before,
    ))
    if explain_matches and not execution_matches:
        return True
    if explain_matches and execution_matches:
        last_explanation = explain_matches[-1]
        last_execution = execution_matches[-1]
        if last_explanation.start() > last_execution.end():
            between = clause_before[
                last_execution.end() : last_explanation.start()
            ]
            for aliases in COSMOLOGY_DATASET_FAMILY_ALIASES.values():
                for alias in aliases:
                    alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
                    if re.search(
                        rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                        between,
                    ):
                        return True
    comparison_context = clause_before + " " + clause_after
    explicit_comparison_request = bool(re.search(
        r"\b(?:compare|contrast)\b",
        clause_before,
    ))
    if (
        not execution_matches
        and re.search(r"\b(?:same|different|differs?|equivalent)\b", comparison_context)
        and not (comparison_is_execution and explicit_comparison_request)
    ):
        return True
    if (
        not comparison_is_execution
        and not execution_matches
        and re.search(r"\b(?:versus|vs)\b", comparison_context)
    ):
        return True
    return False


def _cosmology_prompt_mentions_dataset_family(
    prompt: str,
    family: str,
    *,
    include_canonical_short_name: bool = True,
) -> bool:
    """Match a shared family alias without confusing DES with DESI."""
    for alias in COSMOLOGY_DATASET_FAMILY_ALIASES.get(family, (family,)):
        if not include_canonical_short_name and alias == family:
            continue
        pattern = re.escape(alias).replace(r"\ ", r"\s+")
        for match in re.finditer(
            rf"(?<![a-z0-9]){pattern}(?![a-z0-9])",
            prompt,
            re.IGNORECASE,
        ):
            if not _dataset_mention_is_non_execution(
                prompt,
                match.start(),
                match.end(),
                comparison_is_execution=True,
            ):
                return True
    return False


def _cosmology_prompt_has_executable_pattern(
    prompt: str,
    pattern: str,
) -> bool:
    return any(
        not _dataset_mention_is_non_execution(
            prompt,
            match.start(),
            match.end(),
            comparison_is_execution=True,
        )
        for match in re.finditer(pattern, prompt, re.IGNORECASE)
    )


def _cosmology_dataset_keys_present(tool_results: list[dict]) -> set[str]:
    keys: set[str] = set()

    def collect_from(value: Any) -> None:
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and entry.get("key"):
                    keys.add(str(entry["key"]))
                elif isinstance(entry, str):
                    keys.add(entry)

    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        for field in ("datasets", "datasets_used", "datasets_not_run", "dataset_keys", "candidate_dataset_keys"):
            collect_from(result.get(field))
        matrix = result.get("matrix")
        if isinstance(matrix, list):
            for cell in matrix:
                if isinstance(cell, dict):
                    collect_from(cell.get("dataset_keys"))
                    cell_result = cell.get("result")
                    if isinstance(cell_result, dict):
                        for field in ("datasets", "datasets_used", "datasets_not_run", "dataset_keys"):
                            collect_from(cell_result.get(field))
        provenance = result.get("provenance")
        if isinstance(provenance, dict):
            likelihood = provenance.get("cosmology_likelihood")
            if isinstance(likelihood, dict):
                for field in ("dataset_keys", "datasets_used", "datasets_not_run"):
                    collect_from(likelihood.get(field))
    return keys


_COSMOLOGY_ANCHOR_NUMERIC_PATTERNS: tuple[tuple[set[str], re.Pattern[str]], ...] = (
    (
        {"planck2018_compressed"},
        re.compile(
            r"\b(?:Planck|CMB)[^\n]{0,100}(?:H_?0|H₀|S_?8|sigma_?8|σ_?8)"
            r"[^\n]{0,60}\d",
            re.I,
        ),
    ),
    (
        {"shoes_h0_riess22"},
        re.compile(
            r"\b(?:SH0ES|Riess)[^\n]{0,100}(?:H_?0|H₀)[^\n]{0,60}\d",
            re.I,
        ),
    ),
    (
        {"h0licow_h0"},
        re.compile(r"\b(?:H0LiCOW|time[-\s]?delay)[^\n]{0,100}(?:H_?0|H₀)[^\n]{0,60}\d", re.I),
    ),
    (
        {"megamaser_h0_pesce20"},
        re.compile(r"\b(?:megamaser|maser)[^\n]{0,100}(?:H_?0|H₀)[^\n]{0,60}\d", re.I),
    ),
)


# Lookarounds keep the token from matching INSIDE a word — "SH0ES" and
# "H0LiCOW" both contain "H0", and a token hit there makes the
# number-after-token read the "0" of the next "H0" instead of the anchor value.
_ANCHOR_PARAM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:H_?0|H₀|S_?8|sigma_?8|σ_?8)(?![A-Za-z0-9])", re.I
)
_ANCHOR_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _unsupported_cosmology_anchor_numeric_comparison(
    reply: str,
    tool_results: list[dict],
) -> bool:
    """Catch H0/S8 comparison anchors that were not selected this turn.

    A tool may itself declare the anchor value — fit_line_lfr's
    cosmology_manifest carries the Planck18 preset (H0=67.36, Om=0.3153,
    sigma8=0.8111) the fit assumed, and the system prompt REQUIRES declaring
    the assumed cosmology. So a match only blocks when the number attached to
    the anchor parameter is NOT in a tool-declared cosmology subtree
    (cosmology_manifest / source_cosmology; ±1%, signed). "Planck18
    (H0 = 67.36)" over a fit result passes; "Planck measured H0 = 70" still
    blocks (matching the full tool universe instead would launder any anchor
    near a coincidental FWHM/flux value).
    """
    text = str(reply or "")
    if not text:
        return False
    from app.services.claim_validator import value_supported_by_cosmology_manifest

    dataset_keys = _cosmology_dataset_keys_present(tool_results)
    for required_keys, pattern in _COSMOLOGY_ANCHOR_NUMERIC_PATTERNS:
        if required_keys & dataset_keys:
            continue
        for match in pattern.finditer(text):
            # The pattern ends at a digit; widen the window so the trailing
            # number is complete, then read the FIRST number after the
            # parameter token (H0/S8/sigma8) — that is the anchor value.
            window = text[match.start(): match.end() + 24]
            token = _ANCHOR_PARAM_TOKEN_RE.search(window)
            if token is None:
                return True  # cannot locate the parameter — keep the conservative block
            number = _ANCHOR_NUMBER_RE.search(window, token.end())
            if number is None:
                # The pattern's trailing \d was satisfied by a digit inside a
                # name ("SH0ES", "H0LiCOW"), not by a value attached to the
                # parameter — qualitative tension prose, not a numeric claim.
                continue
            try:
                value = float(number.group(0))
            except ValueError:
                return True
            if not value_supported_by_cosmology_manifest(value, tool_results):
                return True
    return False


_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _parse_inline_numeric_array(text: str, name: str) -> list[float] | None:
    """Parse a small user-supplied array like x=[0,1,2] from chat text."""
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*[:=：＝]\s*[\[\(（]\s*([^\]\)）]+?)\s*[\]\)）]",
        re.I,
    )
    match = pattern.search(str(text or ""))
    if not match:
        return None
    values = [float(v) for v in re.findall(_NUMBER_RE, match.group(1))]
    return values or None


def _parse_inline_uniform_error(text: str, axis: str, n: int) -> list[float] | None:
    """Parse phrases like 'x error is 0.1' (or the Chinese equivalent) or 'x and y errors are 0.1'."""
    if n <= 0:
        return None
    lower = str(text or "").lower()
    both_axis = axis in {"x", "y"} and re.search(
        rf"(?:x\s*(?:和|and|/|,|，)\s*y|每个点.*?x.*?y|both\s+x\s+and\s+y)"
        rf".{{0,30}}(?:误差|error|uncertaint).*?(?:都是|均为|为|=|are|is)?\s*({_NUMBER_RE})",
        lower,
        re.I | re.S,
    )
    if both_axis:
        return [float(both_axis.group(1))] * n
    axis_match = re.search(
        rf"(?<![a-z0-9_]){re.escape(axis)}(?:[_\s-]*(?:err|error|uncertainty)|\s*误差)"
        rf".{{0,20}}(?:都是|均为|为|=|are|is)?\s*({_NUMBER_RE})",
        lower,
        re.I | re.S,
    )
    if axis_match:
        return [float(axis_match.group(1))] * n
    array = (
        _parse_inline_numeric_array(text, f"{axis}_err")
        or _parse_inline_numeric_array(text, f"{axis}err")
    )
    if array and len(array) == n:
        return array
    return None


def _inline_statistics_tool_call_from_prompt(text: str) -> dict[str, Any] | None:
    """Return a deterministic statistics tool call for explicit inline data."""
    prompt = str(text or "")
    x = _parse_inline_numeric_array(prompt, "x")
    y = _parse_inline_numeric_array(prompt, "y")
    if not x or not y or len(x) != len(y) or len(x) < 2:
        return None
    lowered = prompt.lower()
    regression_tokens = (
        "linear regression", "line fit", "fit a line", "regression",
        "线性回归", "拟合", "斜率", "截距",
    )
    if not any(tok in lowered for tok in regression_tokens):
        return None
    tool_input: dict[str, Any] = {
        "analysis_type": "linear_regression",
        "x": x,
        "y": y,
        "method": "auto",
    }
    x_err = _parse_inline_uniform_error(prompt, "x", len(x))
    y_err = _parse_inline_uniform_error(prompt, "y", len(y))
    if x_err:
        tool_input["x_err"] = x_err
    if y_err:
        tool_input["y_err"] = y_err
    return {
        "id": f"auto_stats_{uuid.uuid4().hex}",
        "name": "astro_statistics_toolbox",
        "input": tool_input,
    }


def _is_cosmology_likelihood_workflow(text: str) -> bool:
    prompt = str(text or "").lower()
    pure_dataset_identity_question = (
        bool(re.search(
            r"\b(?:same|equivalent|different)\s+"
            r"(?:datasets?|data\s+sets?|releases?|products?)\b",
            prompt,
        ))
        and not re.search(
            r"\b(?:run|use|execute|fit|analy[sz]e|compare|contrast|"
            r"constrain|evaluate|assess)\b",
            prompt,
        )
    )
    if pure_dataset_identity_question:
        return False
    dataset_tokens = (
        "bao", "baryon acoustic", "sn ia", "supernova", "pantheon",
        "des-sn", "union3", "cmb", "planck", "act dr6", "spt", "spt-3g", "sh0es",
        "cosmic chronometer", "weak lensing", "weak-lensing",
        "cosmic shear", "cosmic-shear", "kids",
        "des y3", "hsc", "galaxy lensing", "trgb", "freedman",
        "h0licow", "time-delay", "time delay", "strong-lens",
        "strong lens", "megamaser", "cosmic-chronometer", "h(z)",
        "expansion-history", "expansion history", "observational-cosmology",
        "observational cosmology", "cosmology probes",
    )
    model_tokens = (
        "dark energy", "dark-energy", "暗能量", "lcdm", "λcdm", "wcdm", "w0wa",
        "cpl", "omega_m", "ωm", "Ωm", "h0", "h₀", "posterior", "后验",
        "likelihood", "协方差", "covariance", "robustness",
        "pull", "outlier", "residual", "bin-level", "分红移",
        "s8", "sigma8", "σ8", "tension", "consistency",
        "cross-check", "cross check", "workflow",
        "constraint", "constraints", "compressed product",
        "compressed products", "chain", "chains",
        "expansion-history", "expansion history", "h(z)",
        "chronometer", "chronometers",
    )
    planning_tokens = (
        "available", "可用", "dataset", "数据集", "prior", "引用",
        "compare", "比较", "constraint", "约束", "model", "模型",
        "chain", "配置", "cobaya", "cosmosis", "workflow",
        "posterior", "run", "executable", "product", "products",
        "config-only", "config only", "research", "study", "analysis",
        "robustness", "matrix", "test", "consistent", "supported",
        "summary", "summaries", "pairwise", "approximation",
        "approximations", "conclusion", "conclusions", "availability",
        "available", "研究", "分析", "稳健",
    )
    return (
        any(tok in prompt for tok in dataset_tokens)
        and any(tok in prompt for tok in model_tokens)
        and any(tok in prompt for tok in planning_tokens)
    )


def _is_research_program_workflow(text: str) -> bool:
    prompt = str(text or "").lower()
    if _cosmology_requires_dedicated_spectra_likelihood(prompt) or _cosmology_has_dedicated_model_gap(prompt):
        return True
    research_tokens = (
        "research", "study", "analysis", "analyze", "assess", "evaluate",
        "compare", "test", "inspect", "examine", "constrain", "identify",
        "blind", "workflow", "robustness", "matrix",
        "研究", "分析", "评估", "比较", "检验", "盲测", "稳健",
        "张力", "新结论", "发现",
    )
    return _is_cosmology_likelihood_workflow(text) and any(
        token in prompt for token in research_tokens
    )


def _research_plan_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(tool_results):
        if item.get("tool") != "plan_research_program":
            continue
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("research_plan"), dict):
            return result["research_plan"]
    return None


def _research_evidence_graph_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(tool_results):
        if item.get("tool") != "build_evidence_graph":
            continue
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("evidence_graph"), dict):
            return result["evidence_graph"]
    return None


def _compact_tool_results_for_evidence(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in tool_results:
        result = item.get("result")
        if isinstance(result, dict):
            result = {
                key: value
                for key, value in result.items()
                if key in {
                    "analysis_status",
                    "publication_ready",
                    "claim_scope",
                    "dataset_keys",
                    "datasets",
                    "datasets_used",
                    "datasets_not_run",
                    "parameters",
                    "posterior_summary",
                    "derived_params",
                    "pairwise_tensions",
                    "provenance",
                    "research_plan",
                    "matrix",
                    "warnings",
                }
            }
        compact.append({
            "tool": item.get("tool"),
            "input": item.get("input"),
            "result": result,
        })
    return compact


def _cosmology_prompt_mentions_act(text: str) -> bool:
    prompt = str(text or "").lower()
    return bool(re.search(r"\bact\b", prompt)) or any(tok in prompt for tok in (
        "act dr6", "act lens", "act-era", "act era",
    ))


def _cosmology_prompt_mentions_spt(text: str) -> bool:
    prompt = str(text or "").lower()
    return bool(re.search(r"\bspt\b", prompt)) or "spt-3g" in prompt


def _cosmology_prompt_forbids_family(text: str, aliases: tuple[str, ...]) -> bool:
    """Return True when the prompt explicitly excludes a probe family.

    This is intentionally conservative and local: it only looks for probe
    aliases inside a short negation span, so ordinary phrases such as
    "config-only/no posterior" do not accidentally ban a dataset family.
    """
    prompt = str(text or "").lower()
    negators = (
        "不要加入", "不要使用", "不要引入", "不加入", "不使用", "不引入",
        "别加入", "别使用", "排除", "without", "exclude",
        "do not include", "do not use", "do not add",
        "don't include", "don't use", "don't add",
        "not include", "not use", "not add",
    )
    for negator in negators:
        start = 0
        while True:
            index = prompt.find(negator, start)
            if index < 0:
                break
            window = prompt[max(0, index - 16) : index + 96]
            if "with and without" in window or "with/without" in window:
                start = index + len(negator)
                continue
            post_window = prompt[index + len(negator) : index + len(negator) + 96]
            post_window = re.split(r"[.;\n]", post_window, maxsplit=1)[0]
            if any(alias in post_window for alias in aliases):
                return True
            start = index + len(negator)
    return False


def _cosmology_forbidden_probe_families(text: str) -> set[str]:
    return {
        family
        for family, aliases in {
            "bao": ("bao", "baryon acoustic", "desi", "sdss", "boss", "eboss", "6df"),
            "sn": ("sn", "sn ia", "supernova", "pantheon", "des-sn", "union3"),
            "cmb": ("cmb", "planck", "act dr6", "act lens"),
            "wl": ("weak lensing", "weak-lensing", "cosmic shear", "kids", "des y3", "hsc"),
            "h0": (
                "sh0es", "h0 prior", "h₀ prior", "trgb", "freedman",
                "h0licow", "time-delay", "time delay", "strong-lens",
                "strong lens", "megamaser",
            ),
            "hz": ("chronometer", "h(z)", "cosmic chronometer"),
        }.items()
        if _cosmology_prompt_forbids_family(text, aliases)
    }


def _cosmology_probe_family_for_dataset(key: str) -> str:
    if key in {"desi_dr1_bao", "desi_dr2_bao", "sdss_6df_bao"}:
        return "bao"
    if key in {"pantheon_plus", "des_sn5yr", "union3"}:
        return "sn"
    if key in {"planck2018_compressed", "act_dr6_lensing", "spt3g_cmb"}:
        return "cmb"
    if key in {"kids1000_wl", "des_y3_3x2pt", "hsc_y1_cosmic_shear"}:
        return "wl"
    if key in {
        "shoes_h0_riess22",
        "trgb_h0_freedman19",
        "h0licow_h0",
        "megamaser_h0_pesce20",
    }:
        return "h0"
    if key == "cosmic_chronometers":
        return "hz"
    return "other"


def _cosmology_prompt_mentions_bao(text: str) -> bool:
    prompt = str(text or "").lower()
    return any(tok in prompt for tok in ("bao", "baryon acoustic", "desi", "sdss", "6df", "6dfgs", "boss", "eboss"))


def _cosmology_prompt_mentions_weak_lensing(text: str) -> bool:
    prompt = str(text or "").lower()
    return any(tok in prompt for tok in (
        "weak lensing", "weak-lensing", "weak-lensing survey",
        "weak lensing survey", "galaxy lensing", "cosmic shear",
        "kids", "kilo-degree", "des y3", "des-y3", "hsc",
    ))


def _cosmology_dataset_keys_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    if _cosmology_requires_dedicated_spectra_likelihood(prompt):
        return []
    forbidden = _cosmology_forbidden_probe_families(prompt)
    keys: list[str] = []
    h0_anchor_context = any(tok in prompt for tok in (
        "h0 prior", "h₀ prior", "h0-prior", "h₀-prior",
        "h0 priors", "h₀ priors", "h0-priors", "h₀-priors",
        "h0 constraint", "h₀ constraint", "late-universe h0",
        "late universe h0", "distance ladder", "anchors",
    ))
    pre_desi_bao = any(tok in prompt for tok in (
        "pre-desi", "pre desi", "non-desi", "non desi",
        "pre-desi bao", "before desi", "rather than desi",
        "not desi",
    ))
    desi_or_pre_desi = any(tok in prompt for tok in (
        "desi or pre-desi",
        "desi or pre desi",
        "desi/pre-desi",
        "desi/pre desi",
        "desi and pre-desi",
        "desi and pre desi",
    ))
    # An explicit DR2 mention ("desi dr2", "desi-dr2", "desi_dr2_bao") selects
    # the DR2 likelihood; bare "desi" keeps routing to DR1 for backward
    # compatibility. Exactly one DESI release is ever selected — the registry
    # marks desi_dr1_bao and desi_dr2_bao mutually do_not_combine_with, and
    # DR2 supersedes DR1 when both releases are named.
    desi_key = "desi_dr1_bao"
    if "desi" in prompt and re.search(
        r"\bdr\s*2\b", prompt.replace("_", " ").replace("-", " ")
    ):
        desi_key = "desi_dr2_bao"
    if desi_or_pre_desi:
        keys.extend([desi_key, "sdss_6df_bao"])
    elif "desi" in prompt and not pre_desi_bao:
        keys.append(desi_key)
    elif any(tok in prompt for tok in ("bao", "baryon acoustic")):
        if pre_desi_bao or any(tok in prompt for tok in ("act dr6", "act lens", "sdss", "6df", "6dfgs", "eboss", "boss")):
            keys.append("sdss_6df_bao")
        else:
            keys.append("desi_dr1_bao")
    pantheon_requested = _cosmology_prompt_mentions_dataset_family(
        prompt, "pantheon"
    ) or _cosmology_prompt_has_executable_pattern(
        prompt, r"\b(?:supernova|sn\s+ia|sn)\b"
    )
    if pantheon_requested:
        keys.append("pantheon_plus")
    if _cosmology_prompt_has_executable_pattern(
        prompt, r"\b(?:des[ -]?sn|des[ -]?5yr|desy5)\b"
    ):
        keys.append("des_sn5yr")
    if _cosmology_prompt_has_executable_pattern(
        prompt, r"\b(?:union3|unity)\b"
    ):
        keys.append("union3")
    planck_named = bool(re.search(r"\bplanck\b", prompt, re.IGNORECASE))
    planck_requested = _cosmology_prompt_mentions_dataset_family(
        prompt, "planck"
    )
    generic_cmb_requested = (
        not planck_named
        and _cosmology_prompt_has_executable_pattern(prompt, r"\bcmb\b")
    )
    if planck_requested or generic_cmb_requested:
        keys.append("planck2018_compressed")
    if (
        not keys
        and _cosmology_has_dedicated_model_gap(prompt)
        and any(tok in prompt for tok in ("compressed cosmology", "public compressed", "cosmology data", "data-combination", "data combination"))
    ):
        keys.append("planck2018_compressed")
    if (
        _cosmology_prompt_mentions_dataset_family(prompt, "act")
        or _cosmology_prompt_has_executable_pattern(
            prompt, r"\bcmb[ -]lensing\b"
        )
    ):
        keys.append("act_dr6_lensing")
    if _cosmology_prompt_mentions_dataset_family(prompt, "spt"):
        keys.append("spt3g_cmb")
    kids_requested = _cosmology_prompt_mentions_dataset_family(prompt, "kids")
    # Bare "DES" also prefixes DES-SN, which is a distinct supernova product.
    # Keep the short alias for identity disclosure but require an explicit WL
    # release/full survey name at the routing layer.
    des_requested = _cosmology_prompt_mentions_dataset_family(
        prompt, "des", include_canonical_short_name=False
    )
    des_requested = des_requested or (
        _cosmology_prompt_has_executable_pattern(
            prompt,
            r"\bdes[\s_-]+y\s*[0-9]+[a-z]?\b",
        )
        or _cosmology_prompt_has_executable_pattern(
            prompt,
            r"\bdes\b(?![\s-]*sn\b)[^\n]{0,48}\b(?:weak[ -]?lensing|cosmic[ -]?shear|3x2pt)\b",
        )
    )
    hsc_requested = _cosmology_prompt_mentions_dataset_family(prompt, "hsc")
    specific_wl_requested = kids_requested or des_requested or hsc_requested
    galaxy_weak_lensing_requested = _cosmology_prompt_has_executable_pattern(
        prompt,
        r"\bgalaxy\s+weak[ -]?lensing\b",
    )
    if kids_requested:
        keys.append("kids1000_wl")
    if des_requested or (
        galaxy_weak_lensing_requested and not specific_wl_requested
    ):
        keys.append("des_y3_3x2pt")
    if hsc_requested:
        keys.append("hsc_y1_cosmic_shear")
    generic_wl_requested = _cosmology_prompt_has_executable_pattern(
        prompt,
        r"\b(?:weak[ -]?lensing(?:\s+survey)?|galaxy\s+lensing|cosmic[ -]?shear)\b",
    )
    if generic_wl_requested and not specific_wl_requested:
        for key in ("kids1000_wl", "des_y3_3x2pt", "hsc_y1_cosmic_shear"):
            keys.append(key)
    if "chronometer" in prompt or re.search(r"\bcc\b", prompt):
        keys.append("cosmic_chronometers")
    explicit_h0_prior_selected = False
    if "trgb" in prompt or "freedman" in prompt:
        keys.append("trgb_h0_freedman19")
        explicit_h0_prior_selected = True
    if any(tok in prompt for tok in ("h0licow", "time-delay", "time delay", "strong-lens", "strong lens")) or (
        h0_anchor_context
        and "lensing" in prompt
        and not _cosmology_prompt_mentions_weak_lensing(prompt)
    ):
        keys.append("h0licow_h0")
        explicit_h0_prior_selected = True
    if "megamaser" in prompt or "maser" in prompt:
        keys.append("megamaser_h0_pesce20")
        explicit_h0_prior_selected = True
    wants_shoes = "sh0es" in prompt or "riess" in prompt
    wants_shoes_alongside_specific_anchor = any(
        tok in prompt
        for tok in (
            "compare against sh0es", "compare with sh0es", "vs sh0es",
            "versus sh0es", "+ sh0es", "plus sh0es", "include sh0es",
            "combine with sh0es", "from sh0es", "sh0es,", "sh0es /",
            "sh0es and", "sh0es +", "sh0es/trgb", "sh0es, trgb",
        )
    )
    if wants_shoes and (not explicit_h0_prior_selected or wants_shoes_alongside_specific_anchor):
        keys.append("shoes_h0_riess22")
        explicit_h0_prior_selected = True
    elif (
        not explicit_h0_prior_selected
        and ("h0 prior" in prompt or "h₀ prior" in prompt)
    ):
        keys.append("shoes_h0_riess22")
    if not keys and _cosmology_likelihood_executable_only_prompt(prompt):
        keys = ["planck2018_compressed", "act_dr6_lensing", "kids1000_wl"]
    elif not keys and _is_cosmology_likelihood_workflow(text):
        keys = ["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"]
    return [
        key
        for key in dict.fromkeys(keys)
        if _cosmology_probe_family_for_dataset(key) not in forbidden
    ]


def _cosmology_supernova_sets_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    keys: list[str] = []
    if any(tok in prompt for tok in ("pantheon", "supernova", "sn ia")) or re.search(r"\bsn\b", prompt):
        keys.append("pantheon_plus")
    if any(tok in prompt for tok in ("des-sn", "des sn", "des-5yr", "des 5yr", "desy5")):
        keys.append("des_sn5yr")
    if "union3" in prompt or "unity" in prompt:
        keys.append("union3")
    return list(dict.fromkeys(keys))


def _cosmology_models_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    models: list[str] = []
    if "lcdm" in prompt or "λcdm" in prompt:
        models.append("lcdm")
    if "wcdm" in prompt:
        models.append("wcdm")
    if "w0wa" in prompt or "cpl" in prompt:
        models.append("w0wa_cdm")
    wants_curvature = any(tok in prompt for tok in (
        "curvature", "curved", "non-flat", "nonflat", "omega_k",
        "omegak", "Ωk", "曲率", "非平坦",
    ))
    wants_neutrino_mass = any(tok in prompt for tok in (
        "neutrino", "mnu", "m_ν", "mν", "sum m", "Σm", "Σmν",
        "nu mass", "中微子",
    ))
    if wants_curvature:
        if "w0wa_cdm" in models:
            models.append("ok_w0wa_cdm")
        elif "wcdm" in models:
            models.append("ok_wcdm")
        else:
            models.append("ok_lcdm")
    if wants_neutrino_mass:
        if "w0wa_cdm" in models:
            models.append("w0wa_cdm_mnu")
        else:
            models.append("lcdm_mnu")
    if not models and (
        _is_cosmology_likelihood_workflow(text)
        or _should_build_cosmology_robustness_matrix(text)
        or _cosmology_has_dedicated_model_gap(text)
    ):
        if any(tok in prompt for tok in (
            "dark energy", "dark-energy", "暗能量", "wcdm", "w0wa", "cpl",
            "模型比较", "model comparison", "compare model",
        )):
            models = ["lcdm", "wcdm", "w0wa_cdm"]
        else:
            models = ["lcdm"]
    return list(dict.fromkeys(models))


def _should_build_cosmology_robustness_matrix(text: str) -> bool:
    prompt = str(text or "").lower()
    sn_sets = _cosmology_supernova_sets_from_prompt(prompt)
    forbidden = _cosmology_forbidden_probe_families(prompt)
    if "bao" in forbidden:
        return False
    if not _cosmology_prompt_mentions_bao(prompt):
        return False
    robustness_tokens = (
        "robust", "鲁棒", "consistency", "一致", "compare", "比较",
        "discrepancy", "tension", "张力", "互相", "组合", "compilation",
        "des-5yr", "desy5", "union3",
    )
    return len(sn_sets) >= 2 and any(tok in prompt for tok in robustness_tokens)


def _cosmology_likelihood_build_calls_from_prompt(text: str) -> list[dict[str, Any]]:
    if _cosmology_requires_dedicated_spectra_likelihood(text):
        return []
    dataset_keys = _cosmology_dataset_keys_from_prompt(text)
    models = _cosmology_models_from_prompt(text)
    if not dataset_keys or not models:
        return []
    if _should_build_cosmology_robustness_matrix(text):
        sn_sets = _cosmology_supernova_sets_from_prompt(text)
        return [
            {
                "id": f"auto_cosmo_matrix_{uuid.uuid4().hex}",
                "name": "build_cosmology_robustness_matrix",
                "input": {
                    "model": model,
                    "supernova_sets": sn_sets,
                    "include_weak_lensing": _cosmology_prompt_mentions_weak_lensing(text),
                    "include_h0_prior": "sh0es" in str(text or "").lower()
                    or "h0 prior" in str(text or "").lower()
                    or "h₀ prior" in str(text or "").lower(),
                },
            }
            for model in models
        ]
    return [
        {
            "id": f"auto_cosmo_config_{uuid.uuid4().hex}",
            "name": "build_cosmology_likelihood",
            "input": {
                "model": model,
                "dataset_keys": dataset_keys,
                "output_format": "both",
            },
        }
        for model in models
    ]


def _cosmology_direct_route_from_prompt(text: str) -> list[dict[str, Any]] | None:
    """Detect explicit single-tool cosmology requests and return a forced
    first-iteration tool call list.

    Both DeepSeek and Anthropic's function-call rankers default to
    `plan_research_program` for any "research-flavored" user phrasing
    regardless of how specific the user is. Five rounds of prompt + schema
    work (2026-05-28 V1-V5) failed to steer them on "Hubble tension" and
    "Alcock-Paczynski" prompts. This deterministic gate fires BEFORE the
    first LLM call and pre-executes the right cosmology tool, then lets the
    LLM continue with the result already in hand — same pattern as
    `_inline_statistics_tool_call_from_prompt` and
    `_cosmology_likelihood_run_calls_from_prompt`.

    Returns ``None`` when no trigger phrase matches → normal agent loop.
    """
    raw = str(text or "")
    t = raw.lower()
    if not t:
        return None

    # Only phrases whose comparison target is unequivocally SH0ES. Vague
    # triggers ("compare cosmologies", "delta h0", "luminosity-distance
    # offset", "preset vs preset") were removed 2026-05-28: they hard-coded
    # target_cosmology=riess22_shoes and overrode the user's real intent
    # (e.g. "compare cosmologies planck18 vs wcdm"). Those fall through to
    # the LLM's own routing now.
    hubble_triggers = (
        "hubble tension",
        "compare planck and sh0es",
    )
    matrix_or_extended_context = (
        "matrix" in t
        or "fisher" in t
        or "covariance" in t
        or "constraint-direction" in t
        or "constraint direction" in t
        or "curvature" in t
        or "constant-w" in t
        or "extended" in t
        or "dark-energy model" in t
        or "dark energy model" in t
        or "build an auditable" in t
        or "available cmb/bao/sn/h0 information" in t
    )
    if any(k in t for k in hubble_triggers) and not matrix_or_extended_context:
        return [{
            "id": f"direct_route_{uuid.uuid4().hex}",
            "name": "compare_luminosity_distances",
            "input": {"target_cosmology": "riess22_shoes"},
        }]

    # "ap test" removed 2026-05-28: as a bare substring it matched "snap test"
    # (SNAP = a real dark-energy mission), "map test", "heatmap test" — all
    # plausible in a cosmology chat. "alcock-paczynski" alone is unambiguous.
    ap_triggers = (
        "alcock-paczynski",
        "alcock paczynski",
        "dm/dh ratio",
        "bao bin anomaly",
        "per-bin bao",
        "geometric omega_m from bao",
        "geometric ωm from bao",
    )
    if any(k in t for k in ap_triggers):
        return [{
            "id": f"direct_route_{uuid.uuid4().hex}",
            "name": "assess_bao_bin_anomaly",
            "input": {},
        }]

    return None


def _cosmology_likelihood_run_calls_from_prompt(text: str) -> list[dict[str, Any]]:
    if _cosmology_requires_dedicated_spectra_likelihood(text):
        return []
    dataset_keys = _cosmology_dataset_keys_from_prompt(text)
    models = _cosmology_models_from_prompt(text)
    if not dataset_keys or not models:
        return []
    if _cosmology_prompt_mentions_spt(text):
        # SPT-3G damping-tail likelihoods are not yet executable in the
        # registry.  Do not substitute Planck/ACT compressed posteriors for an
        # SPT workflow; the assistant should report config/registry status.
        return []
    run_models = ["lcdm"] if "lcdm" in models else [models[0]]
    if _should_build_cosmology_robustness_matrix(text):
        sn_sets = _cosmology_supernova_sets_from_prompt(text)
        return [
            {
                "id": f"auto_cosmo_run_matrix_{uuid.uuid4().hex}",
                "name": "run_cosmology_robustness_matrix",
                "input": {
                    "model": model,
                    "supernova_sets": sn_sets,
                    "include_weak_lensing": _cosmology_prompt_mentions_weak_lensing(text),
                    "include_h0_prior": "sh0es" in str(text or "").lower()
                    or "h0 prior" in str(text or "").lower()
                    or "h₀ prior" in str(text or "").lower(),
                },
            }
            for model in run_models
        ]
    return [
        {
            "id": f"auto_cosmo_run_{uuid.uuid4().hex}",
            "name": "run_cosmology_likelihood_chain",
            "input": {
                "model": model,
                "dataset_keys": dataset_keys,
            },
        }
        for model in run_models
    ]


def _cosmology_likelihood_executable_only_prompt(text: str) -> bool:
    prompt = str(text or "").lower()
    return (
        ("registry" in prompt or "registered" in prompt)
        and any(tok in prompt for tok in ("executable chain", "executable chains", "可执行"))
        and ("observational-cosmology" in prompt or "observational cosmology" in prompt or "probes" in prompt)
    )


def _cosmology_requires_dedicated_spectra_likelihood(text: str) -> bool:
    """Prompts whose observable class is spectra/parity/template level.

    Planck/ACT compressed distance or lensing summaries are useful background
    cosmology products, but they are the wrong evidence class for EB/TB
    birefringence or oscillatory primordial-feature searches.
    """
    prompt = str(text or "").lower()
    has_birefringence = any(
        tok in prompt
        for tok in (
            "birefringence",
            "polarization rotation",
            "polarization-rotation",
            "polarisation rotation",
            "polarisation-rotation",
            "rotation angle",
            "rotation-angle",
            "rotation field",
            "rotation-field",
            "eb/tb",
            "tb/eb",
            "eb tb",
            "tb eb",
            "instrument-angle",
            "instrument angle",
            "parity violation",
            "parity-violating",
            "parity violating",
            "偏振旋转",
            "旋转角",
        )
    )
    has_feature_template = any(
        tok in prompt
        for tok in (
            "primordial feature",
            "primordial-feature",
            "sharp-feature",
            "resonant-feature",
            "oscillatory feature",
            "oscillatory primordial",
            "primordial oscillation",
            "oscillatory residual",
            "feature template",
            "feature-search",
            "look-elsewhere",
            "look elsewhere",
            "frequency scan",
            "frequency/phase scan",
            "inflationary anomaly",
            "inflationary anomalies",
            "inflationary feature",
            "原初",
            "振荡",
        )
    )
    has_cmb_spectra_context = any(
        tok in prompt
        for tok in (
            "cmb",
            "planck",
            "act",
            "spt",
            "temperature",
            "polarization",
            "polarisation",
            "b-mode",
            "b mode",
            "tt",
            "te",
            "ee",
            "eb",
            "tb",
            "map",
            "maps",
            "bandpower",
            "bandpowers",
            "estimator",
            "spectra",
            "spectrum",
            "power spectrum",
            "power-spectrum",
        )
    )
    return has_cmb_spectra_context and (has_birefringence or has_feature_template)


def _cosmology_has_dedicated_model_gap(text: str) -> bool:
    prompt = str(text or "").lower()
    return any(
        tok in prompt
        for tok in (
            "early dark energy",
            "early-dark-energy",
            "early energy",
            "early-energy",
            "transient early-energy",
            "transient early energy",
            "pre-recombination",
            "before recombination",
            " ede",
            "ede ",
            "ede-vs",
            "ede vs",
            "ede claim",
            "ede model",
            "axion-like early",
            "axion like early",
            "modified gravity",
            "modified-gravity",
            "growth model",
            "growth-rate",
            "growth-index",
            "growth index",
            "growth likelihood",
            "growth than planck",
            "differs from gr",
            "thawing",
            "emergent",
            "mirage",
        )
    )
