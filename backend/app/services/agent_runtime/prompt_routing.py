"""Deterministic prompt-analysis routing for cosmology / research workflows,
inline-statistics extraction, and the cosmology anchor-comparison gate.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

import re
import uuid
from typing import Any, Literal, TypedDict


TaskKind = Literal[
    "deterministic_source_check",
    "research_exploration",
    "full_research",
    "general",
]


class RoutingDecision(TypedDict):
    task_kind: TaskKind
    confidence: float
    matched_signals: list[str]
    negated_signals: list[str]
    source_references: list[dict[str, str]]
    requested_operation: str | None
    missing_inputs: list[str]
    heavy_route_allowed: bool
    direct_tool_call: dict[str, Any] | None


_HEAVY_INTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("likelihood", re.compile(r"\b(?:likelihood|似然)\b", re.I)),
    ("fit", re.compile(r"\b(?:fit|fitting|拟合)\b", re.I)),
    ("sampling", re.compile(r"\b(?:sample|sampling|sampler|mcmc|nested\s+sampl)|采样", re.I)),
    ("posterior", re.compile(r"\bposterior\b|后验", re.I)),
    ("model_likelihood_comparison", re.compile(r"(?:compare|comparison|比较).{0,40}(?:model|模型).{0,40}(?:likelihood|似然)", re.I)),
    # Codex review P1 (PR #46): an explicit chain-execution request phrased
    # without likelihood/fit/sampler vocabulary ("Run the executable cosmology
    # chain...") must count as heavy intent. Bare "chain" stays excluded on
    # purpose (chain rule, chain of reasoning); the noun must be qualified or
    # the sampler named explicitly.
    ("chain_execution", re.compile(
        r"\bcobaya\b|"
        r"\b(?:run|rerun|re-run|execute|launch)\b[^.;。；]{0,60}"
        r"\b(?:cosmology|likelihood|mcmc|posterior|parameter)\s+chains?\b|"
        r"(?:跑|执行|运行)[^.;。；]{0,30}(?:宇宙学|似然|参数)链",
        re.I,
    )),
)
_NEGATION_TOKEN = re.compile(
    r"(?:\b(?:do\s+not|don't|without|avoid|exclude|excluding|not|never)\b|"
    r"不要|不需要|无需|别|避免|排除|不是|并非)",
    re.I,
)


def _normalized_task_text(text: str) -> str:
    normalized = str(text or "")
    normalized = normalized.replace("−", "-").replace("–", "-").replace("—", "-")
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"\\(?:mathrm|text|operatorname)\s*\{([^{}]+)\}", r"\1", normalized)
    normalized = normalized.replace("{", "").replace("}", "")
    normalized = re.sub(r"D\s*[_\s]?M\s*/\s*D\s*[_\s]?H", "D_M/D_H", normalized, flags=re.I)
    normalized = re.sub(r"(?<![A-Za-z])DM\s*/\s*DH(?![A-Za-z])", "D_M/D_H", normalized, flags=re.I)
    normalized = re.sub(r"D\s*[_\s]?M\s*/\s*r\s*[_\s]?d", "D_M/r_d", normalized, flags=re.I)
    normalized = re.sub(r"D\s*[_\s]?H\s*/\s*r\s*[_\s]?d", "D_H/r_d", normalized, flags=re.I)
    return re.sub(r"\s+", " ", normalized).strip()


_VERB_NEGATOR = re.compile(
    r"\b(?:do\s+not|don't|does\s+not|cannot|can't|must\s+not|never|not)\b|"
    r"不要|不得|不能|别",
    re.I,
)
_NOUN_NEGATOR = re.compile(
    r"\b(?:without|avoid|exclude|excluding)\b|无需|不需要|避免|排除|不是|并非",
    re.I,
)
_EXECUTION_VERB = re.compile(
    r"\b(?:run|rerun|re-run|running|execute|executing|launch|perform|compute)\b|"
    r"跑|执行|运行",
    re.I,
)
_CLAUSE_BOUNDARY = re.compile(r"[,:，：]")


def _prefix_negates(prefix: str) -> bool:
    """Whether the sentence prefix negates a heavy match right after it.

    Codex review P1 (PR #46, round 3): a negation anywhere in the sentence
    used to negate every later heavy signal, so "Don't explain it, execute
    the cosmology chain" lost its tools. Scope rules:

    - The LAST negator in the prefix governs.
    - No clause boundary between negator and match: negated (covers
      "Do not run a likelihood." and "without running a fit").
    - Boundary present, verb negator: negated only when an execution verb
      occurs before the first boundary — a negated imperative whose object
      list runs across commas ("Do not run a likelihood, fit, sampler").
    - Boundary present, noun negator: not negated — its scope ends with its
      clause ("Without approximations, run a Planck likelihood fit").

    Known limitation, deliberately accepted: a parenthetical between the
    negator and its verb ("Do not, under any circumstances, run the chain")
    reads as active; the paraphrase-variant suite probes this class.
    """
    last_negator: re.Match[str] | None = None
    negator_is_verb = False
    for candidate in _VERB_NEGATOR.finditer(prefix):
        if last_negator is None or candidate.start() >= last_negator.start():
            last_negator = candidate
            negator_is_verb = True
    for candidate in _NOUN_NEGATOR.finditer(prefix):
        if last_negator is None or candidate.start() > last_negator.start():
            last_negator = candidate
            negator_is_verb = False
    if last_negator is None:
        return False
    tail = prefix[last_negator.end() :]
    boundary = _CLAUSE_BOUNDARY.search(tail)
    if boundary is None:
        return True
    if not negator_is_verb:
        return False
    return bool(_EXECUTION_VERB.search(tail[: boundary.start()]))


def _active_and_negated_heavy_signals(text: str) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    negated: list[str] = []
    for name, pattern in _HEAVY_INTENT_PATTERNS:
        active = False
        was_negated = False
        for match in pattern.finditer(text):
            sentence_start = max(
                text.rfind(separator, 0, match.start())
                for separator in (".", ";", "。", "；", "\n")
            )
            prefix = text[sentence_start + 1 : match.start()]
            if _prefix_negates(prefix):
                was_negated = True
            else:
                active = True
        if active:
            matched.append(name)
        elif was_negated:
            negated.append(name)
    return matched, negated


def _source_references_from_prompt(text: str) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    patterns = (
        ("arxiv", re.compile(r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)),
        ("doi", re.compile(r"(?:doi:\s*|doi\.org/)(10\.\d{4,9}/[^\s,;]+)", re.I)),
        ("zenodo", re.compile(r"(?:zenodo\.org/(?:records?|record)/|10\.5281/zenodo\.)(\d+)", re.I)),
        ("url", re.compile(r"https://[^\s)\]>]+", re.I)),
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            identifier = match.group(1) if match.lastindex else match.group(0)
            if kind == "url" and any(
                host in identifier for host in ("arxiv.org/", "doi.org/", "zenodo.org/")
            ):
                continue
            key = (kind, identifier.rstrip(".,;"))
            if key in seen:
                continue
            seen.add(key)
            references.append({"kind": kind, "identifier": key[1]})
    return references


def _requested_scalar_operation(text: str) -> str | None:
    # Codex review P1 (PR #46, round 4): leading-space tokens missed
    # operation words at the very start of a prompt ("Product of A and B"),
    # dropping a complete verification request to general. Use token
    # boundaries instead.
    lowered = text.lower()
    if "d_m/d_h" in lowered or re.search(r"\bratios?\b|比值|相除", lowered):
        return "ratio"
    if re.search(r"\bweighted\s+(?:mean|average)\b|加权平均", lowered):
        return "weighted_mean"
    if re.search(r"\bdifferences?\b|差值|相减", lowered):
        return "difference"
    if re.search(r"\bproducts?\b|\bmultipl(?:y|ied|ication)\b|乘积|相乘", lowered):
        return "product"
    return None


_SCALAR_LABEL = r"(?:D_M/r_d|D_H/r_d|D_M|D_H|H0|H_0|H₀|n_s|ns|S8|Ωm|omega_m|rho|ρ|[A-Za-z][A-Za-z0-9_]*)"
_SCALAR_VALUE = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_H0_UNIT = r"km\s*(?:(?:/\s*s)|s\s*\^?\s*-?1|s-1)\s*(?:/\s*)?Mpc(?:\s*\^?\s*-?1)?"


def _scalar_quantities_from_prompt(text: str) -> list[dict[str, Any]]:
    quantities: list[dict[str, Any]] = []
    pattern = re.compile(
        rf"(?P<label>{_SCALAR_LABEL})\s*=\s*(?P<value>{_SCALAR_VALUE})"
        rf"\s*(?:±|\+/-|\+-)\s*(?P<uncertainty>{_SCALAR_VALUE})"
        rf"\s*(?P<unit>{_H0_UNIT}|Mpc|Gpc|kpc|pc)?",
        re.I,
    )
    seen: set[str] = set()
    for match in pattern.finditer(text):
        label = match.group("label")
        if label.lower() in {"rho", "ρ"}:
            continue
        canonical_id = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
        if canonical_id.lower() in seen:
            continue
        seen.add(canonical_id.lower())
        unit = (match.group("unit") or "dimensionless").strip()
        quantities.append(
            {
                "id": canonical_id,
                "label": label,
                "value": float(match.group("value")),
                "standard_uncertainty": float(match.group("uncertainty")),
                "unit": unit,
            }
        )
    return quantities


def _uncertainty_model_from_prompt(text: str, quantity_count: int) -> dict[str, Any] | None:
    # Connector accepts natural phrasings ("a correlation of -0.404", "the
    # correlation is -0.404") in addition to spec-style "rho=-0.404"; the
    # 2026-08-06 natural matrix showed the equals-only form suppressed every
    # legitimate V02_01 answer downstream.
    rho_match = re.search(
        rf"(?:rho|ρ|correlation(?:\s+coefficient)?|相关系数)\s*(?:\([^)]*\))?\s*"
        rf"(?:=|:|\bof\b|\bis\b|为|是)\s*({_SCALAR_VALUE})",
        text,
        re.I,
    )
    if rho_match and quantity_count == 2:
        rho = float(rho_match.group(1))
        return {
            "kind": "correlation_matrix",
            "matrix": [[1.0, rho], [rho, 1.0]],
        }
    if re.search(r"\b(?:independent|uncorrelated)\b|相互独立|假设独立|忽略相关", text, re.I):
        return {"kind": "independent"}
    return None


_INDEPENDENCE_STATED_RE = re.compile(
    r"\b(?:independent|uncorrelated)\b|相互独立|假设独立|忽略相关", re.I
)


def _prompt_numbers(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(_SCALAR_VALUE, text.replace("−", "-")):
        try:
            values.append(float(match.group()))
        except ValueError:
            continue
    return values


def scalar_call_echo_violation(
    call_input: dict[str, Any], prompt_text: str
) -> str | None:
    """Echo-validate a model-authored verify_scalar_derivation call.

    Fabrication guard for the incomplete-packet fallback: the model may
    complete the parse the deterministic parser missed, but every numeric
    input must appear in the user's own prompt, and an independence
    assumption must have been stated by the user. Returns a violation
    description, or None when the call is fully echoed.
    """
    numbers = _prompt_numbers(prompt_text)

    def echoed(value: float) -> bool:
        return any(
            abs(value - candidate) <= 1e-9 * max(1.0, abs(candidate))
            for candidate in numbers
        )

    for quantity in call_input.get("quantities") or []:
        for field in ("value", "standard_uncertainty"):
            raw = quantity.get(field)
            if not isinstance(raw, (int, float)) or not echoed(float(raw)):
                label = quantity.get("label") or quantity.get("id") or "quantity"
                return (
                    f"{label} {field} {raw!r} does not appear in the user prompt"
                )
    model = call_input.get("uncertainty_model") or {}
    kind = model.get("kind")
    if kind == "correlation_matrix":
        for i, row in enumerate(model.get("matrix") or []):
            for j, cell in enumerate(row or []):
                if i == j:
                    continue
                if not isinstance(cell, (int, float)) or not echoed(float(cell)):
                    return (
                        f"correlation coefficient {cell!r} does not appear in "
                        "the user prompt"
                    )
    elif kind == "independent":
        if not _INDEPENDENCE_STATED_RE.search(prompt_text):
            return "independence was not stated by the user"
    else:
        return (
            f"uncertainty_model kind {kind!r} is not allowed for the "
            "incomplete-packet fallback"
        )
    return None


def _source_locator_from_prompt(text: str) -> str:
    table_match = re.search(r"(?:Table|表)\s*(\d+)(?:[^.;。；\n]{0,32})?", text, re.I)
    row_match = re.search(r"\b(LRG\s*\d+|ELG|QSO|BGS)\b", text, re.I)
    parts = []
    if table_match:
        parts.append(f"Table {table_match.group(1)}")
    if row_match:
        parts.append(re.sub(r"\s+", "", row_match.group(1)).upper())
    equation_match = re.search(r"(?:Equation|Eq\.?|公式)\s*(\d+)", text, re.I)
    page_match = re.search(r"(?:Page|p\.?|页)\s*(\d+)", text, re.I)
    if equation_match:
        parts.append(f"Equation {equation_match.group(1)}")
    if page_match:
        parts.append(f"Page {page_match.group(1)}")
    return ", ".join(parts)


def _is_fixed_comparator(label: str) -> bool:
    return bool(
        re.search(
            r"(?:reference|fixed|baseline|target|comparison|参照|固定|基准|对照)",
            label,
            re.I,
        )
    )


def _deterministic_tool_call_from_prompt(
    text: str,
    operation: str | None,
    references: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, list[str]]:
    quantities = _scalar_quantities_from_prompt(text)
    missing: list[str] = []
    if operation is None:
        missing.append("operation")
    required_count = 2
    if len(quantities) < required_count:
        missing.append("quantities")
    uncertainty_model = _uncertainty_model_from_prompt(text, len(quantities))
    if uncertainty_model is None:
        missing.append("uncertainty_model")
    if missing:
        return None, missing

    locator = _source_locator_from_prompt(text)
    sources: list[dict[str, str]] = []
    # The compact prompt parser binds one primary source packet to all parsed
    # measurements. Prefer the normalized first reference (arXiv before URL in
    # extraction order); model-authored structured tool calls may still supply
    # multiple independently mapped sources.
    for index, reference in enumerate(references[:1]):
        sources.append(
            {
                "id": f"source-{index + 1}",
                "kind": reference["kind"],
                "identifier": reference["identifier"],
                "locator": locator,
            }
        )
    if not sources:
        sources.append(
            {
                "id": "user-supplied",
                "kind": "user_supplied",
                "identifier": "values in current user prompt",
                "locator": locator or "current prompt",
            }
        )
    source_id = sources[0]["id"]
    needs_fixed_source = any(
        _is_fixed_comparator(str(quantity.get("label") or ""))
        for quantity in quantities
    )
    if needs_fixed_source:
        sources.append(
            {
                "id": "user-supplied-fixed",
                "kind": "user_supplied",
                "identifier": "fixed comparator in current prompt",
                "locator": "current prompt",
            }
        )
    for quantity in quantities:
        if _is_fixed_comparator(str(quantity.get("label") or "")):
            quantity["source_ref"] = "user-supplied-fixed"
            quantity["source_locator"] = "current prompt"
        else:
            quantity["source_ref"] = source_id
            quantity["source_locator"] = locator or sources[0]["locator"]
    if uncertainty_model is not None:
        uncertainty_model["source_ref"] = source_id
    assert operation is not None and uncertainty_model is not None
    return {
        "id": f"auto_scalar_verify_{uuid.uuid4().hex}",
        "name": "verify_scalar_derivation",
        "input": {
            "operation": operation,
            "quantities": quantities,
            "uncertainty_model": uncertainty_model,
            "sources": sources,
            "boundary_statement": (
                "This is a source-table consistency calculation, not a likelihood "
                "fit, sampler run, posterior reconstruction, or dark-energy inference."
            ),
        },
    }, []


def classify_task_kind(text: str) -> RoutingDecision:
    """Classify task shape once, before domain keywords select any workflow."""
    normalized = _normalized_task_text(text)
    active_heavy, negated_heavy = _active_and_negated_heavy_signals(normalized)
    references = _source_references_from_prompt(normalized)
    operation = _requested_scalar_operation(normalized)
    # Noun set includes log/export: the 2026-08-06 natural matrix phrased the
    # fake transcript as a "pasted log", the detector never fired, and 15
    # correct model refusals were mislabeled disposition=full.
    untrusted_evidence_request = bool(
        re.search(
            r"\b(?:pasted|paste|fake|fabricated|external)\b.{0,80}"
            r"\b(?:tool\s+transcript|transcript|tool\s+result|evidence|log|export)\b|"
            r"粘贴|伪造|外部.{0,40}(?:工具记录|工具结果|证据|日志|导出)",
            normalized,
            re.I,
        )
        and re.search(
            r"\b(?:treat|present|claim|hide|conceal|forge)\b.{0,80}"
            r"\b(?:verified|current|paper[- ]ready|provenance|fact)\b|"
            r"(?:当作|视为|包装成|隐瞒|隐藏|伪装).{0,40}(?:已验证|当前|论文|来源|事实)",
            normalized,
            re.I,
        )
    )
    direct_tool_call, missing_inputs = _deterministic_tool_call_from_prompt(
        normalized, operation, references
    )
    scalar_shape = bool(operation) and bool(
        _scalar_quantities_from_prompt(normalized)
        or re.search(r"\b(?:table|row|source|paper)\b|表格|论文|来源", normalized, re.I)
    )

    if active_heavy:
        task_kind: TaskKind = "full_research"
        confidence = 0.98
        matched_signals = [f"positive_heavy_intent:{name}" for name in active_heavy]
        missing_inputs = []
        direct_tool_call = None
    elif scalar_shape:
        task_kind = "deterministic_source_check"
        confidence = 0.99 if direct_tool_call else 0.90
        matched_signals = [f"scalar_operation:{operation}"]
        if references:
            matched_signals.append("source_reference")
    elif re.search(
        r"\b(?:research|study|explore|investigate|hypothesis|method|approach|interpret)\b|"
        r"研究|探索|假设|方法|思路|解释",
        normalized,
        re.I,
    ):
        task_kind = "research_exploration"
        confidence = 0.82
        matched_signals = ["open_research_intent"]
        missing_inputs = []
        direct_tool_call = None
    else:
        task_kind = "general"
        confidence = 0.72
        matched_signals = ["no_execution_intent"]
        missing_inputs = []
        direct_tool_call = None

    if untrusted_evidence_request:
        matched_signals.append("untrusted_evidence_request")
        confidence = max(confidence, 0.99)

    return {
        "task_kind": task_kind,
        "confidence": confidence,
        "matched_signals": matched_signals,
        "negated_signals": [f"negated_heavy_intent:{name}" for name in negated_heavy],
        "source_references": references,
        "requested_operation": operation,
        "missing_inputs": missing_inputs,
        "heavy_route_allowed": task_kind == "full_research",
        "direct_tool_call": direct_tool_call,
    }


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

_DATASET_MENTION_SUFFIX_PATTERN = (
    r"(?:\s*\+|\s*[-_+]?\s*(?:legacy|dr\s*[0-9]+[a-z]?|"
    r"pr\s*[0-9]+[a-z]?|y\s*[0-9]+[a-z]?|(?:19|20)[0-9]{2}|"
    r"[0-9]{1,4}[a-z]?|cosmic[-\s]+shear|weak[-\s]+lensing|"
    r"lensing|bao|compressed)){0,5}"
)

_DATASET_FAMILY_KEYS: dict[str, frozenset[str]] = {
    "act": frozenset({"act_dr6_lensing"}),
    "des": frozenset({"des_y3_3x2pt"}),
    "desi": frozenset({"desi_dr1_bao", "desi_dr2_bao"}),
    "hsc": frozenset({"hsc_y1_cosmic_shear"}),
    "kids": frozenset({"kids1000_wl"}),
    "pantheon": frozenset({"pantheon_plus"}),
    "planck": frozenset({"planck2018_compressed"}),
    "shoes": frozenset({"shoes_h0_riess22"}),
    "spt": frozenset({"spt3g_cmb"}),
}

_GENERIC_PROBE_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "bao": ("bao", "baryon acoustic"),
    "sn": ("sn", "sn ia", "supernova"),
    "cmb": ("cmb",),
    "wl": ("weak lensing", "weak-lensing", "cosmic shear", "galaxy lensing"),
    "h0": (
        "h0 prior", "h0 priors", "h₀ prior", "h₀ priors",
        "distance ladder", "anchors",
    ),
    "hz": ("chronometer", "h(z)", "cosmic chronometer"),
}


def _prompt_with_normalized_dataset_mentions(text: str) -> str:
    """Replace concrete survey-family mentions with a stable identity token."""
    prompt = str(text or "").lower()
    matches: list[tuple[int, int]] = []
    aliases = sorted(
        {
            alias
            for family_aliases in COSMOLOGY_DATASET_FAMILY_ALIASES.values()
            for alias in family_aliases
        },
        key=len,
        reverse=True,
    )
    for alias in aliases:
        alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
        matches.extend(
            (match.start(), match.end())
            for match in re.finditer(
                rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                prompt,
            )
        )

    selected: list[tuple[int, int]] = []
    for start, end in sorted(matches, key=lambda span: (span[0], -(span[1] - span[0]))):
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end))
    if not selected:
        return prompt

    parts: list[str] = []
    cursor = 0
    for start, end in selected:
        parts.extend((prompt[cursor:start], " __dataset__ "))
        cursor = end
    parts.append(prompt[cursor:])
    normalized = "".join(parts)
    return re.sub(
        rf"__dataset__{_DATASET_MENTION_SUFFIX_PATTERN}",
        "__dataset__",
        normalized,
    )


def _is_pure_dataset_identity_question(text: str) -> bool:
    """Return True when the requested result is dataset identity, not a fit."""
    prompt = str(text or "").lower()
    normalized = _prompt_with_normalized_dataset_mentions(prompt)
    identity_noun = r"(?:datasets?|data\s+sets?|data|releases?|products?)"
    identity_term = (
        r"(?:same|identical|different|distinct|equivalent|separate|"
        r"interchangeable|independent|disjoint|overlapping)"
    )
    referential_identity = bool(re.search(
        rf"\b(?:are|is)\s+(?:these|those|they)\s+(?:the\s+)?"
        rf"{identity_term}\s+{identity_noun}\b"
        rf"|\b(?:these|those|they)\s+(?:are|is)\s+(?:the\s+)?"
        rf"{identity_term}\s+{identity_noun}\b",
        normalized,
    ))
    dataset_count = normalized.count("__dataset__")
    dataset_subject_relation = dataset_count >= 2 and bool(re.search(
        rf"\b(?:is|are)\s+(?:these\s+|those\s+|the\s+two\s+)?"
        rf"__dataset__(?:\s+(?:and|or|versus|vs)\s+__dataset__)?\s+"
        rf"(?:(?:the|statistically)\s+)?{identity_term}\b"
        rf"|\b(?:do|does)\s+__dataset__"
        rf"(?:\s+(?:and|or)\s+__dataset__)?\s+differ\b"
        rf"|__dataset__\s+(?:and|or|versus|vs)\s+__dataset__\s+"
        rf"(?:(?:{identity_noun})\s+)?(?:(?:is|are)\s+)?"
        rf"(?:(?:the|statistically)\s+)?(?:{identity_term}|differ(?:s|ed)?)\b"
        r"|__dataset__\s+(?:refers?\s+to\s+)?(?:the\s+)?same\s+"
        rf"{identity_noun}\s+(?:as\s+)?__dataset__"
        r"|__dataset__\s+(?:is|are)\s+(?:just\s+)?another\s+name\s+for\s+"
        r"__dataset__"
        rf"|\b(?:do|does)\s+__dataset__\s+(?:and|or)\s+__dataset__\s+"
        rf"refer\s+to\s+(?:the\s+)?same\s+{identity_noun}\b"
        rf"|__dataset__\s+(?:and|or)\s+__dataset__\s+refer\s+to\s+"
        rf"(?:the\s+)?same\s+{identity_noun}\b"
        r"|\b(?:is|are)\s+__dataset__\s+(?:just\s+)?another\s+name\s+for\s+"
        r"__dataset__"
        r"|\b(?:are|is)\s+__dataset__\s+(?:and|or)\s+__dataset__\s+"
        r"(?:(?:two\s+)?versions?\s+of\s+(?:the\s+same|one)\s+dataset|"
        r"based\s+on\s+(?:the\s+)?same\s+(?:data|data\s+product))\b"
        r"|__dataset__\s+(?:and|or)\s+__dataset__\s+(?:are\s+|is\s+)?"
        r"(?:(?:two\s+)?versions?\s+of\s+(?:the\s+same|one)\s+dataset|"
        r"based\s+on\s+(?:the\s+)?same\s+(?:data|data\s+product)|"
        r"use\s+(?:the\s+)?same\s+(?:data|data\s+product)|"
        r"share\s+(?:the\s+)?same\s+(?:underlying\s+)?data|"
        r"(?:are\s+)?derived\s+from\s+(?:the\s+)?same\s+observations?)\b",
        normalized,
    ))
    explicit_identity_complement = dataset_count >= 2 and bool(re.search(
        rf"\b(?:same|identical|equivalent)\s+{identity_noun}\s+(?:as|to)\b"
        rf"|\b(?:different|distinct|separate)\s+{identity_noun}\s+from\b",
        normalized,
    ))
    chinese_identity = dataset_count >= 2 and bool(re.search(
        r"(?:同一个|同一|相同|等价|不同的?|互不相同|是不是同|是否同|"
        r"相互独立|是否独立|是否重叠|数据重叠)"
        r"(?:数据集|数据|发布|产品|名字)?",
        normalized,
    ))
    identity_relation = (
        referential_identity
        or dataset_subject_relation
        or explicit_identity_complement
        or chinese_identity
    )
    if not identity_relation:
        return False

    # Identity language can be a qualifier inside a real analysis request:
    # "fit two different datasets" or "do they differ in S8?" must execute.
    scientific_quantity = (
        r"(?:s8|sigma\s*8|σ8|omega[_\s]*m|ωm|Ωm|h0|w0|n[_\s]*s|"
        r"posterior|likelihood|"
        r"constraints?|results?|measurements?|parameters?|tension|robustness|"
        r"chi(?:-?square|2)|参数|约束|后验|张力)"
    )
    scientific_target = bool(re.search(
        rf"\b(?:compare|evaluate|test|assess|constrain)\b[^.;\n]{{0,64}}"
        rf"(?:{scientific_quantity})\b"
        rf"|\b(?:differ(?:s|ed)?|different|same|agree|disagree)\b"
        rf"[^.;\n]{{0,16}}\b(?:in|on|with\s+respect\s+to)\b"
        rf"[^.;\n]{{0,40}}(?:{scientific_quantity})\b"
        rf"|(?:{scientific_quantity})[^。；\n]{{0,24}}"
        r"(?:是否|有何|是不是)?(?:相同|不同|一致|不一致)",
        prompt,
    ))
    scientific_target = scientific_target or bool(re.search(
        r"(?:s8|h0|Ωm|ωm|σ8|w0|n[_\s]*s)[^。；\n]{0,24}"
        r"(?:是否|有何|是不是)?(?:相同|不同|一致|不一致)",
        prompt,
        re.IGNORECASE,
    ))
    explicit_execution = bool(re.search(
        r"\b(?:run(?:ning)?|execut(?:e|ing)|fit(?:ting)?|"
        r"analy[sz](?:e|ing))\b[^.;\n]{0,96}"
        r"(?:__dataset__|both\b|all\b|two\s+(?:different\s+)?datasets?\b)"
        r"|\b(?:use|using|select|include|add|combine)\b"
        r"(?:(?!\b(?:explain|describe|discuss|mention|clarify|determine|"
        r"check|verify)\b)[^.;\n]){0,64}"
        r"(?:__dataset__|different\s+datasets?\b)"
        r"|\b(?:then\s+)?(?:run|execute|fit|use)\s+(?:both|all|them)\b",
        normalized,
    ))
    return not scientific_target and not explicit_execution


def _is_dataset_combination_advice_question(text: str) -> bool:
    """Keep methodological combination questions from starting real chains."""
    normalized = _prompt_with_normalized_dataset_mentions(text)
    if normalized.count("__dataset__") < 2:
        return False
    if re.search(
        r"\b(?:can|could|would)\s+you\b[^?;.\n]{0,96}"
        r"\b(?:run|fit|combine|use)\b"
        r"|(?:[;?]|\bthen\b|\bplease\b)[^;\n]{0,64}"
        r"\b(?:run|execute|fit|use)\b"
        r"(?:[^;.\n]{0,48}\b(?:both|all|them|it|the\s+"
        r"(?:joint\s+fit|chain))\b)?",
        normalized,
    ):
        return False
    return bool(re.search(
        r"\b(?:should|would|could|can|may)\b[^?;.\n]{0,96}"
        r"\b(?:combine|combined|use\s+together)\b"
        r"|\b(?:may|should)\s+i\b[^?;.\n]{0,96}"
        r"\b(?:combine|use|fit|run)\b[^?;.\n]{0,48}"
        r"\b(?:jointly|together)\b"
        r"|\b(?:is|would)\s+it\b[^?;.\n]{0,64}"
        r"\b(?:okay|ok|safe|valid|appropriate|advisable|sound)\b[^?;.\n]{0,48}"
        r"\b(?:combine|use)\b"
        r"|\b(?:is|are)\b[^?;.\n]{0,64}"
        r"\b(?:okay|ok|safe|valid|appropriate|advisable|sound)\b"
        r"[^?;.\n]{0,32}\b(?:combine|use\s+together)\b"
        r"|\bwould\b[^?;.\n]{0,96}\bjoint\b[^?;.\n]{0,64}"
        r"\b(?:double[-\s]?count|overlap|duplicate)\b"
        r"|\b(?:discuss|explain|assess)\s+whether\b[^?;.\n]{0,96}"
        r"\b(?:appropriate|valid|advisable|sound)\b",
        normalized,
    ))


def _is_dataset_metadata_only_question(text: str) -> bool:
    """Return True for registry/identity lookups that should not start a fit."""
    prompt = str(text or "").lower()
    normalized = _prompt_with_normalized_dataset_mentions(prompt)
    if "__dataset__" not in normalized:
        return False
    explicit_execution = bool(re.search(
        r"\b(?:run|execute|fit|analy[sz]e|select|include|add|combine)\b"
        r"[^.;\n]{0,96}(?:__dataset__|both|all|them)"
        r"|\b(?:use|using)\b"
        r"(?:(?!\b(?:metadata|documentation|glossary|registry)\b)"
        r"[^.;\n]){0,64}(?:__dataset__|both|all|them)"
        r"|(?:运行|拟合|分析|选择|加入|添加|联合)[^。；\n]{0,96}"
        r"__dataset__",
        normalized,
    ))
    if explicit_execution:
        return False
    scientific_quantity = bool(re.search(
        r"\b(?:s8|sigma\s*8|omega[_\s]*m|h0|posterior|likelihood|"
        r"constraints?|parameters?|tension|pull|outliers?|residuals?|"
        r"bin[-\s]?level|chi(?:-?square|2))\b"
        r"|(?:σ8|Ωm|ωm|后验|似然|约束|参数|张力)",
        prompt,
        re.IGNORECASE,
    ))
    if scientific_quantity:
        return False
    return bool(re.search(
        r"\b(?:what|which)\b[^?;.\n]{0,72}"
        r"\b(?:releases?|versions?|metadata|registry\s+entry|data\s+product)\b"
        r"|\b(?:list|show|describe|check|verify)\b[^?;.\n]{0,72}"
        r"\b(?:metadata|releases?|versions?|registry|registered|available)\b"
        r"|\b(?:is|are)\b[^?;.\n]{0,72}__dataset__[^?;.\n]{0,48}"
        r"\b(?:available|registered)\b"
        r"|\b(?:difference|distinction)\s+between\s+__dataset__\s+"
        r"(?:and|or)\s+__dataset__(?:\s+(?:datasets?|releases?|products?))?\b"
        r"|__dataset__[^。；\n]{0,48}(?:是否)?(?:可用|已注册|注册过|"
        r"什么版本|哪个版本|哪个发布|元数据)",
        normalized,
    ))


def _dataset_keys_for_family_mention(
    family: str,
    prompt: str,
    start: int,
    end: int,
) -> frozenset[str]:
    keys = _DATASET_FAMILY_KEYS.get(family, frozenset())
    if family != "desi":
        return keys
    release_tail = prompt[end : end + 24].replace("_", " ").replace("-", " ")
    release_match = re.match(r"^\s*(?:bao\s+)?dr\s*([12])\b", release_tail)
    if release_match and release_match.group(1) == "2":
        return frozenset({"desi_dr2_bao"})
    if release_match and release_match.group(1) == "1":
        return frozenset({"desi_dr1_bao"})
    return keys


def _explicit_dataset_key_intents(text: str) -> dict[str, tuple[int, bool]]:
    """Return ``(position, execute)`` for the last non-neutral named intent."""
    prompt = str(text or "").lower()
    events: dict[tuple[int, str], tuple[int, bool | None]] = {}
    for family, aliases in COSMOLOGY_DATASET_FAMILY_ALIASES.items():
        if family not in _DATASET_FAMILY_KEYS:
            continue
        for alias in aliases:
            alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
            for match in re.finditer(
                rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                prompt,
            ):
                for key in _dataset_keys_for_family_mention(
                    family,
                    prompt,
                    match.start(),
                    match.end(),
                ):
                    event_key = (match.start(), key)
                    prior = events.get(event_key)
                    if prior and prior[0] >= match.end():
                        continue
                    events[event_key] = (
                        match.end(),
                        _dataset_mention_execution_intent(
                            prompt,
                            match.start(),
                            match.end(),
                            comparison_is_execution=True,
                            report_default=True,
                        ),
                    )

    intents: dict[str, tuple[int, bool]] = {}
    last_positions: dict[str, int] = {}
    for (position, key), (_end, execute) in sorted(events.items()):
        if execute is None:
            continue
        if execute is _DEFAULT_EXECUTE:
            # A verb-less mention ("KiDS is a lensing survey") explains an
            # earlier explicit exclusion; it must not cancel it.
            prior = intents.get(key)
            if prior is not None and prior[1] is False:
                continue
            execute = True
        intents[key] = (position, execute)
        last_positions[key] = position

    anaphoric_intents: list[tuple[int, bool]] = []
    anaphoric_intents.extend(
        (match.start(), True)
        for match in re.finditer(
            r"\b(?:run|use|execute|fit|analy[sz]e|include|add|combine)\s+"
            + _ANAPHORIC_EXECUTION_OBJECT,
            prompt,
        )
        if not re.search(
            r"\b(?:do\s+not|don't|never|not)\s*$",
            prompt[max(0, match.start() - 24) : match.start()],
        )
    )
    anaphoric_intents.extend(
        (match.start(), False)
        for match in re.finditer(
            r"\b(?:exclude|omit|avoid)\s+(?:both|all|them|these|those)\b"
            r"|\b(?:do\s+not|don't|never)\s+"
            r"(?:run|use|execute|fit|analy[sz]e|include|add)\s+"
            r"(?:both|all|them|these|those|either)\b"
            r"|\b(?:use|include)\s+neither\b",
            prompt,
        )
    )
    for position, execute in sorted(anaphoric_intents):
        for key in list(intents):
            if position > last_positions.get(key, -1):
                intents[key] = (position, execute)
                last_positions[key] = position
    return intents


# Sentinel for a mention judged executable only by the concrete-mention
# default, with no explicit execution signal of its own. Intent folds must
# not let such a mention override an explicit exclusion.
_DEFAULT_EXECUTE = object()

# Anaphoric objects of an execution verb ("run them", "fit both cosmic-shear
# releases"). ``both``/``all`` followed by concrete dataset names ("run both
# DESI DR1 and DR2") are determiners scoped to those names — they must not
# cancel a different dataset's exclusion — so those arms require either a
# bare pronoun reading or a generic collective noun.
_ANAPHORIC_EXECUTION_OBJECT = (
    r"(?:them|these|those|the\s+two"
    r"|(?:both|all)"
    r"(?:(?:\s+[a-z0-9][a-z0-9-]*){0,3}\s+"
    r"(?:datasets?|releases?|surveys?|probes?|samples?|chains?|runs?|"
    r"likelihoods?|analyses)"
    r"|(?:\s+of\s+them)?"
    r"(?:\s+(?:separately|independently|together|jointly|again))?"
    r"(?!\s+[a-z0-9])"
    r"))\b"
)


def _dataset_mention_execution_intent(
    prompt: str,
    start: int,
    end: int,
    *,
    comparison_is_execution: bool = False,
    report_default: bool = False,
) -> bool | None | object:
    """Classify one mention as execute, exclude, or neutral/explanatory.

    With ``report_default=True`` the concrete-mention fallback returns
    ``_DEFAULT_EXECUTE`` instead of ``True`` so callers can tell an explicit
    execution request apart from a verb-less mention.
    """
    before = str(prompt or "")[max(0, start - 96) : start].lower()
    after = str(prompt or "")[end : end + 96].lower()
    scope_before = re.split(r"[.;\n]", before)[-1]
    clause_before = re.split(r"[.;\n]|,\s*(?:and\s+)?", before)[-1]
    clause_after = re.split(r"[.;\n]", after)[0]
    execution_pattern = re.compile(
        r"\b(?:run(?:ning)?|us(?:e|ing)|execut(?:e|ing)|select(?:ing)?|"
        r"fit(?:ting)?|analy[sz](?:e|ing)|includ(?:e|ing)|add(?:ing)?|"
        r"combin(?:e|ing))\b"
    )
    execution_matches = list(execution_pattern.finditer(clause_before))
    scope_execution_matches = list(execution_pattern.finditer(scope_before))

    # A later explicit anaphoric action overrides an earlier exclusion:
    # "do not run A and B separately; then combine them".
    if re.search(
        r"\b(?:then\s+|by\s+)?(?:run(?:ning)?|us(?:e|ing)|execut(?:e|ing)|"
        r"select(?:ing)?|fit(?:ting)?|analy[sz](?:e|ing)|combin(?:e|ing))\s+"
        r"(?:it\b|" + _ANAPHORIC_EXECUTION_OBJECT + r")",
        after,
    ):
        return True

    if re.search(
        r"\bwith\s+(?:and|/)\s*without\s+"
        r"(?:(?:an?|the|any|both|all|registered|available)\s+)*$",
        clause_before,
    ):
        return True
    # Every dataset in a coordinated with/without variant list is positive.
    # In ``with/without both KiDS and Pantheon`` the literal ``without`` is a
    # comparison-arm delimiter, not an exclusion governing the later names.
    # Keep a later explicit exclusion authoritative (for example, ``..., but
    # exclude Pantheon``).
    with_without_matches = list(re.finditer(
        r"\bwith\s*(?:and|/)\s*without\b",
        scope_before,
    ))
    if with_without_matches:
        variant_tail = scope_before[with_without_matches[-1].end() :]
        if not re.search(
            r"\b(?:exclude|omit|avoid|except|not|never)\b"
            r"|\b(?:do|does|did)\s+not\b|don't\b",
            variant_tail,
        ):
            return True
    if re.search(
        r"\b(?:do\s+not|don't|never|without|no|exclude)\b"
        r"[^.;\n]{0,80}\b(?:except(?:\s+for)?|other\s+than)\s*$",
        clause_before,
    ):
        return True

    negated_execution_matches = list(re.finditer(
        r"\b(?:do\s+not|don't|never|not)(?:\s+be)?\s+"
        r"(?:run(?:ning)?|us(?:e|ing)|execut(?:e|ing)|select(?:ing)?|"
        r"fit(?:ting)?|analy[sz](?:e|ing)|includ(?:e|ing)|add(?:ing)?)\b",
        scope_before,
    ))
    if negated_execution_matches:
        last_negated_execution = negated_execution_matches[-1]
        current_mention_is_near = (
            len(scope_before) - last_negated_execution.end() <= 96
        )
        later_positive_execution = any(
            match.start() >= last_negated_execution.end()
            for match in scope_execution_matches
        )
        if current_mention_is_near and not later_positive_execution:
            return False
    chinese_negated_actions = list(re.finditer(
        r"(?:不(?:要|应|能)?|不能|不得|(?<!分)别)\s*"
        r"(?:运行|使用|用|加入|添加|选择)",
        scope_before,
    ))
    if chinese_negated_actions:
        last_chinese_negation = chinese_negated_actions[-1]
        later_chinese_action = re.search(
            r"(?:运行|使用|(?<!不)用|加入|添加|选择|拟合|分析)",
            scope_before[last_chinese_negation.end() :],
        )
        if not later_chinese_action:
            return False
    if re.search(r"\b(?:and\s+|but\s+)?not\s*$", clause_before):
        return False
    if re.search(
        r"\bno\s+(?:(?:any|the|registered|available)\s+)*$",
        clause_before,
    ):
        return False
    # A negation after the mention applies to that mention only in passive
    # forms ("Planck should not be used").  Active forms such as
    # "KiDS without using Planck" target the later dataset instead.
    mention_subject = rf"^\s*{_DATASET_MENTION_SUFFIX_PATTERN}\s*,?\s*"
    if re.match(
        mention_subject
        + r"(?:(?:(?:must|should|is|are|was|were|will|would|can|could)\s+)?"
        r"(?:not|never)\s+(?:(?:to|be)\s+){0,2}"
        r"(?:run|used|executed|selected|fit|analy[sz]ed|included|added)\b"
        r"|without\s+being\s+"
        r"(?:run|used|executed|selected|fit|analy[sz]ed|included|added)\b"
        r"|(?:must|should|is|are|was|were|will|would|can|could)\s+"
        r"(?:be\s+)?(?:excluded|omitted|avoided)\b)",
        clause_after,
    ):
        return False
    if re.match(
        mention_subject
        + r"(?:不应|不能|不得|不要|未)(?:被)?"
        r"(?:使用|运行|加入|添加|选择)",
        clause_after,
    ):
        return False

    exclusion_matches = list(re.finditer(
        r"\b(?:without|exclud(?:e|ing)|except(?:\s+for)?|avoid(?:ing)?|"
        r"rather\s+than|other\s+than|instead\s+of)\b",
        clause_before,
    ))
    if exclusion_matches:
        exclusion = exclusion_matches[-1]
        exclusion_text = exclusion.group(0)
        if (
            re.fullmatch(r"(?:except(?:\s+for)?|other\s+than)", exclusion_text)
            and re.search(
                r"\b(?:do\s+not|don't|never|without|no|exclude)\b"
                r"[^.;\n]{0,80}$",
                clause_before[: exclusion.start()],
            )
        ):
            return True
        executions_after_exclusion = [
            match
            for match in execution_matches
            if match.start() > exclusion.end()
        ]
        exclusion_tail = clause_before[exclusion.end() :].lstrip()
        directly_excluded_execution = re.match(
            r"(?:to\s+)?(?:run(?:ning)?|us(?:e|ing)|execut(?:e|ing)|"
            r"select(?:ing)?|fit(?:ting)?|analy[sz](?:e|ing)|"
            r"includ(?:e|ing)|add(?:ing)?)\b",
            exclusion_tail,
        )
        if directly_excluded_execution:
            excluded_action_end = (
                exclusion.end() + len(clause_before[exclusion.end():])
                - len(exclusion_tail) + directly_excluded_execution.end()
            )
            later_positive_execution = any(
                match.start() >= excluded_action_end
                for match in executions_after_exclusion
            )
            if not later_positive_execution:
                return False
        elif not executions_after_exclusion:
            return False

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
        return None
    if explain_matches and execution_matches:
        last_explanation = explain_matches[-1]
        last_execution = execution_matches[-1]
        if last_explanation.start() > last_execution.end():
            between = clause_before[
                last_execution.end() : last_explanation.start()
            ]
            # Coordinated mixed intent ("run and explain KiDS") still runs
            # the dataset.  Otherwise the later explanatory verb governs the
            # current mention ("use a glossary to discuss Planck") and wins.
            coordination_tokens = between.strip().split()
            if coordination_tokens[:2] == ["and", "then"]:
                coordination_tokens = coordination_tokens[2:]
                coordinated = True
            elif coordination_tokens and coordination_tokens[0] in {
                "&", "and", "then",
            }:
                coordination_tokens = coordination_tokens[1:]
                coordinated = True
            else:
                coordinated = False
            coordinated = coordinated and all(
                token in {"also", "just", "very", "later", "first"}
                or (
                    token.isascii()
                    and token.isalpha()
                    and token.endswith("ly")
                )
                for token in coordination_tokens
            )
            if not coordinated:
                return None
    comparison_context = clause_before + " " + clause_after
    if (
        not comparison_is_execution
        and not execution_matches
        and re.search(r"\b(?:same|different|differs?|equivalent)\b", comparison_context)
    ):
        return None
    if (
        not comparison_is_execution
        and not execution_matches
        and re.search(r"\b(?:versus|vs)\b", comparison_context)
    ):
        return None
    scientific_execution = bool(re.search(
        r"\b(?:compare|evaluate|test|assess|constrain|agree|disagree|differ)\b"
        r"[^.;\n]{0,80}\b(?:s8|sigma\s*8|σ8|omega[_\s]*m|Ωm|h0|"
        r"posterior|constraints?|likelihood|models?)\b"
        r"|(?:比较|评估|检验|约束|拟合|分析)[^。；\n]{0,80}"
        r"(?:lcdm|wcdm|w0wa|s8|h0|Ωm|σ8|模型|约束)",
        str(prompt or ""),
        re.IGNORECASE,
    ))
    chinese_execution = bool(re.search(
        r"(?:运行|使用|(?<!不)用|加入|添加|选择|拟合|分析)",
        scope_before,
    ))
    if execution_matches or scientific_execution or chinese_execution:
        return True
    # A concrete dataset mention is executable by default.  Prompt-level
    # identity/advice guards above this parser suppress metadata-only requests,
    # while keeping ordinary scientific comparisons and provenance checks from
    # silently dropping a requested dataset.
    if report_default:
        return _DEFAULT_EXECUTE
    return True


def _dataset_mention_is_non_execution(
    prompt: str,
    start: int,
    end: int,
    *,
    comparison_is_execution: bool = False,
) -> bool:
    """Ignore mentions that are explicitly excluded or merely explanatory."""
    return _dataset_mention_execution_intent(
        prompt,
        start,
        end,
        comparison_is_execution=comparison_is_execution,
    ) is not True


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
    the anchor parameter is NOT in a curated, citation-bearing cosmology
    subtree returned by a tool (±1%, signed). "Planck18
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
    if (
        _is_pure_dataset_identity_question(prompt)
        or _is_dataset_combination_advice_question(prompt)
        or _is_dataset_metadata_only_question(prompt)
    ):
        return False
    if (
        _prompt_with_normalized_dataset_mentions(prompt).count("__dataset__")
        and re.search(r"\b(?:explain|describe|discuss|mention|clarify)\b", prompt)
        and not any(
            execute
            for _position, execute in _explicit_dataset_key_intents(prompt).values()
        )
    ):
        return False
    dataset_tokens = (
        "bao", "baryon acoustic", "desi", "sn ia", "supernova", "pantheon",
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
        "fit", "analysis",
        "expansion-history", "expansion history", "h(z)",
        "chronometer", "chronometers",
    )
    planning_tokens = (
        "available", "可用", "dataset", "数据集", "prior", "引用",
        "compare", "比较", "不同", "相同", "一致", "差异", "是否",
        "constraint", "约束", "model", "模型",
        "chain", "配置", "cobaya", "cosmosis", "workflow", "run", "use",
        "using", "execute", "fit", "include", "add", "combine",
        "differ", "difference", "different", "agree", "disagree", "same",
        "运行", "使用", "拟合",
        "posterior", "run", "executable", "product", "products",
        "config-only", "config only", "research", "study", "analysis",
        "analyze", "analyse", "evaluate", "assess",
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
            # A new affirmative action ends the exclusion scope: in
            # "do not use Planck and run KiDS", KiDS must not inherit the
            # Planck negation merely because both names fit in this window.
            post_window = re.split(
                r"(?:,|\b(?:and|but|then)\b)\s*"
                r"(?:(?:instead|please|subsequently|also|carefully|then|later|"
                r"now|[a-z]+ly)\s+){0,3}"
                r"(?:run(?:ning)?|us(?:e|ing)|execut(?:e|ing)|"
                r"select(?:ing)?|fit(?:ting)?|analy[sz](?:e|ing)|"
                r"includ(?:e|ing)|add(?:ing)?|combin(?:e|ing))\b",
                post_window,
                maxsplit=1,
            )[0]
            target = re.sub(
                r"^\s*(?:(?:any|all|the|registered|public|available|"
                r"compressed|generic)\s+)*",
                "",
                post_window,
            )
            for alias in aliases:
                alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
                alias_match = re.search(
                    rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                    target,
                )
                if not alias_match:
                    continue
                prefix = target[: alias_match.start()]
                prefix = re.sub(
                    r"\b(?:bao|baryon\s+acoustic|sn(?:\s+ia)?|supernova|cmb|"
                    r"weak[-\s]+lensing|cosmic\s+shear|galaxy\s+lensing|"
                    r"h0\s+prior|h₀\s+prior|distance\s+ladder|anchors?|"
                    r"chronometers?|h\(z\)|calibration|information|data|"
                    r"datasets?|priors?|products?|compressed|available|"
                    r"registered|any|all|the|or|and|nor)\b|[,/、，]|或|和",
                    "",
                    prefix,
                )
                if not prefix.strip():
                    return True
            start = index + len(negator)
    return False


def _generic_probe_family_intents(text: str) -> dict[str, tuple[int, bool]]:
    prompt = str(text or "").lower()
    mentions: dict[str, list[tuple[int, bool | object]]] = {}
    for family, aliases in _GENERIC_PROBE_FAMILY_ALIASES.items():
        for alias in aliases:
            alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
            for match in re.finditer(
                rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                prompt,
            ):
                intent = _dataset_mention_execution_intent(
                    prompt,
                    match.start(),
                    match.end(),
                    comparison_is_execution=True,
                    report_default=True,
                )
                if intent is None:
                    continue
                mentions.setdefault(family, []).append((match.start(), intent))
    intents: dict[str, tuple[int, bool]] = {}
    for family, events in mentions.items():
        current: tuple[int, bool] | None = None
        for position, intent in sorted(events, key=lambda event: event[0]):
            if intent is _DEFAULT_EXECUTE:
                # A verb-less mention ("weak lensing would be double
                # counting") explains an earlier explicit exclusion; it must
                # not cancel it.
                if current is not None and current[1] is False:
                    continue
                intent = True
            current = (position, bool(intent))
        if current is not None:
            intents[family] = current
    return intents


def _cosmology_forbidden_probe_families(text: str) -> set[str]:
    return {
        family
        for family, (_position, execute) in _generic_probe_family_intents(text).items()
        if not execute
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


def _dataset_group_mode_cues(text: str) -> list[tuple[int, str]]:
    prompt = _prompt_with_normalized_dataset_mentions(text)
    cues: list[tuple[int, str]] = []
    separate_patterns = (
        r"\b(?:run|fit|analy[sz]e|use|compare)\b[^.;,\n]{0,72}"
        r"(?:__dataset__|both|them|datasets?)[^.;,\n]{0,24}"
        r"\b(?:separately|independently)\b",
        r"\b(?:separately|independently)\s+(?:run|fit|analy[sz]e|use|compare)\b",
        r"\bwithout\s+combining\s+(?:them|the\s+datasets?)\b",
        r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|shouldn't|"
        r"cannot|can't)\s+(?:ever\s+)?(?:be\s+)?combined?\b",
        r"\bas\s+separate\s+(?:fits?|runs?|analyses)\b",
        r"(?:分别|各自|单独)(?:运行|拟合|分析|使用)|(?:不要|不应|不能|不得|别)组合",
    )
    joint_patterns = (
        r"\b(?:run|fit|analy[sz]e|use|compare)\b[^.;,\n]{0,72}"
        r"(?:__dataset__|both|them|datasets?)[^.;,\n]{0,24}"
        r"\b(?:together|jointly)\b",
        r"\bjoint\s+(?:fit|run|analysis)\b",
        r"\bnot\s+separately\b",
        r"(?:联合|一起)(?:运行|拟合|分析)",
    )
    for pattern in separate_patterns:
        cues.extend((match.start(), "separate") for match in re.finditer(pattern, prompt))
    for pattern in joint_patterns:
        cues.extend((match.start(), "joint") for match in re.finditer(pattern, prompt))
    for match in re.finditer(
        r"\bcombine\s+(?:both|them|the\s+datasets?)\b",
        prompt,
    ):
        prefix = prompt[max(0, match.start() - 40) : match.start()]
        if not re.search(
            r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|shouldn't|"
            r"cannot|can't)(?:\s+ever)?\s*$",
            prefix,
        ):
            cues.append((match.start(), "joint"))
    return sorted(cues)


def _last_dataset_group_mode(text: str) -> str | None:
    cues = _dataset_group_mode_cues(text)
    return cues[-1][1] if cues else None


def _cosmology_dataset_keys_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    if _cosmology_requires_dedicated_spectra_likelihood(prompt):
        return []
    if (
        _is_pure_dataset_identity_question(prompt)
        or _is_dataset_combination_advice_question(prompt)
        or _is_dataset_metadata_only_question(prompt)
    ):
        return []
    probe_family_intents = _generic_probe_family_intents(prompt)
    dataset_key_intents = _explicit_dataset_key_intents(prompt)
    if "desi" in prompt and re.search(
        r"\b(?:run|use|using|compare|fit|execute|select|include)\b",
        prompt,
    ):
        normalized_release_prompt = prompt.replace("_", " ").replace("-", " ")
        for match in re.finditer(
            r"\b(?:desi\s+(?:bao\s+)?)?dr\s*([12])\b",
            normalized_release_prompt,
        ):
            # Fully qualified DESI mentions were already classified by the
            # scoped intent parser above.  This pass exists only so a later
            # shorthand ("DESI DR1 and DR2") inherits the DESI family.
            if match.group(0).lstrip().startswith("desi"):
                continue
            before = normalized_release_prompt[
                max(0, match.start() - 48) : match.start()
            ]
            clause_before = re.split(r"[.;\n,]", before)[-1]
            with_without = bool(re.search(
                r"\bwith\s*(?:and|/)\s*without\s*$",
                clause_before,
            ))
            negated = not with_without and bool(re.search(
                r"\b(?:not|without|rather\s+than|instead\s+of)\s*$",
                clause_before,
            ))
            dataset_key_intents[f"desi_dr{match.group(1)}_bao"] = (
                match.start(),
                not negated,
            )
    explicitly_excluded_keys = {
        key
        for key, (_position, execute) in dataset_key_intents.items()
        if not execute
    }
    keys: list[str] = []
    h0_anchor_context = any(tok in prompt for tok in (
        "h0 prior", "h₀ prior", "h0-prior", "h₀-prior",
        "h0 priors", "h₀ priors", "h0-priors", "h₀-priors",
        "h0 constraint", "h₀ constraint", "late-universe h0",
        "late universe h0", "distance ladder", "anchors",
    ))
    pre_desi_bao = any(tok in prompt for tok in (
        "pre-desi", "pre desi", "non-desi", "non desi",
        "pre-desi bao", "before desi",
    )) or bool(re.search(
        r"\b(?:not|rather\s+than)\s+desi\b(?![\s_-]*(?:bao\s+)?dr\s*[12]\b)",
        prompt,
    ))
    desi_or_pre_desi = any(tok in prompt for tok in (
        "desi or pre-desi",
        "desi or pre desi",
        "desi/pre-desi",
        "desi/pre desi",
        "desi and pre-desi",
        "desi and pre desi",
    ))
    # A bare DESI mention keeps routing to DR1.  DR1/DR2 are mutually
    # incompatible in one likelihood, but explicit separate or with/without
    # comparisons retain both as distinct groups.
    normalized_release_prompt = prompt.replace("_", " ").replace("-", " ")
    dr1_named = "desi" in prompt and bool(re.search(
        r"\bdr\s*1\b",
        normalized_release_prompt,
    ))
    dr2_named = "desi" in prompt and bool(re.search(
        r"\bdr\s*2\b",
        normalized_release_prompt,
    ))
    desi_candidates: list[str] = []
    if dr1_named and "desi_dr1_bao" not in explicitly_excluded_keys:
        desi_candidates.append("desi_dr1_bao")
    if dr2_named and "desi_dr2_bao" not in explicitly_excluded_keys:
        desi_candidates.append("desi_dr2_bao")
    if (
        not dr1_named
        and not dr2_named
        and "desi" in prompt
        and "desi_dr1_bao" not in explicitly_excluded_keys
    ):
        desi_candidates.append("desi_dr1_bao")
    release_positions: dict[str, int] = {}
    for match in re.finditer(
        r"\b(?:desi\s+(?:bao\s+)?)?dr\s*([12])\b",
        normalized_release_prompt,
    ):
        release_positions[f"desi_dr{match.group(1)}_bao"] = match.start()
    retains_release_alternatives = bool(re.search(
        r"\bwith\s*(?:and|/)\s*without\b",
        prompt,
    )) or _last_dataset_group_mode(prompt) == "separate"
    if len(desi_candidates) > 1 and retains_release_alternatives:
        selected_desi_keys = sorted(
            desi_candidates,
            key=lambda key: release_positions.get(key, -1),
        )
    elif desi_candidates:
        selected_desi_keys = [max(
            desi_candidates,
            key=lambda key: release_positions.get(key, -1),
        )]
    else:
        selected_desi_keys = []
    if desi_or_pre_desi:
        keys.extend(selected_desi_keys)
        keys.append("sdss_6df_bao")
    elif selected_desi_keys and not pre_desi_bao:
        keys.extend(selected_desi_keys)
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
    planck_requested = _cosmology_prompt_mentions_dataset_family(
        prompt, "planck"
    )
    generic_cmb_requested = _cosmology_prompt_has_executable_pattern(
        prompt, r"\bcmb\b"
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
    broad_generic_wl_requested = bool(re.search(
        r"\b(?:(?:all|multiple)\s+(?:registered\s+|available\s+)?"
        r"(?:weak[ -]?lensing|cosmic[ -]?shear|galaxy\s+lensing)"
        r"(?:\s+datasets?)?"
        r"|(?:weak[ -]?lensing|cosmic[ -]?shear|galaxy\s+lensing)\s+datasets?)\b",
        prompt,
    ))
    if generic_wl_requested and (
        not specific_wl_requested or broad_generic_wl_requested
    ):
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
    for key, (position, execute) in dataset_key_intents.items():
        if execute and re.match(
            r"(?:run|use|execute|fit|analy[sz]e|include|add|combine)\b",
            prompt[position:],
        ):
            keys.append(key)
    if not keys and _cosmology_likelihood_executable_only_prompt(prompt):
        keys = ["planck2018_compressed", "act_dr6_lensing", "kids1000_wl"]

    def key_is_enabled(key: str) -> bool:
        named_intent = dataset_key_intents.get(key)
        family_intent = probe_family_intents.get(
            _cosmology_probe_family_for_dataset(key)
        )
        latest = named_intent
        if family_intent and (latest is None or family_intent[0] > latest[0]):
            latest = family_intent
        return latest is None or latest[1]

    return [
        key
        for key in dict.fromkeys(keys)
        if key_is_enabled(key)
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


def _prompt_term_last_intent(text: str, pattern: str) -> bool | None:
    prompt = str(text or "").lower()
    intent: bool | None = None
    for match in re.finditer(pattern, prompt, re.IGNORECASE):
        before = prompt[max(0, match.start() - 48) : match.start()]
        clause_before = re.split(r"[.;\n,]", before)[-1]
        after = prompt[match.end() : match.end() + 48]
        negated = bool(re.search(
            r"\b(?:not|without|rather\s+than|instead\s+of|"
            r"do\s+not\s+(?:use|run)|don't\s+(?:use|run)|exclude)\s*$",
            clause_before,
        )) or bool(re.match(
            r"^\s*(?:(?:is|should\s+be|must\s+be)\s+)?"
            r"(?:excluded|not\s+used|not\s+run)\b",
            after,
        ))
        intent = not negated
    return intent


def _cosmology_models_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    models: list[str] = []
    lcdm_intent = _prompt_term_last_intent(
        prompt,
        r"(?<![a-z0-9])(?:lcdm|λcdm)(?![a-z0-9])",
    )
    wcdm_intent = _prompt_term_last_intent(
        prompt,
        r"(?<![a-z0-9])wcdm(?![a-z0-9])",
    )
    w0wa_intent = _prompt_term_last_intent(
        prompt,
        r"(?<![a-z0-9])(?:w0wa(?:[\s_-]*cdm)?|cpl)(?![a-z0-9])",
    )
    if lcdm_intent:
        models.append("lcdm")
    if wcdm_intent:
        models.append("wcdm")
    if w0wa_intent:
        models.append("w0wa_cdm")
    wants_curvature = bool(_prompt_term_last_intent(
        prompt,
        r"\b(?:curvature|curved|non[-\s]?flat|omega_?k|omegak)\b|Ωk|曲率|非平坦",
    ))
    wants_neutrino_mass = bool(_prompt_term_last_intent(
        prompt,
        r"\b(?:neutrino|mnu|m_ν|mν|sum\s+m|nu\s+mass)\b|Σm|Σmν|中微子",
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


def _cosmology_bao_dataset_key_from_prompt(text: str) -> str:
    """Choose a BAO release only when the user names it explicitly.

    DR1 remains the compatibility default. A bare "DESI" must not silently
    opt an existing workflow into DR2.
    """

    prompt = str(text or "").lower()
    if re.search(
        r"(?<![a-z0-9])desi[\s_-]*(?:bao[\s_-]*)?dr[\s_-]*2(?![a-z0-9])",
        prompt,
    ):
        return "desi_dr2_bao"
    return "desi_dr1_bao"


def _should_run_dark_energy_evidence_matrix(text: str) -> bool:
    prompt = str(text or "").lower()
    if _cosmology_bao_dataset_key_from_prompt(prompt) != "desi_dr2_bao":
        return False
    if len(_cosmology_supernova_sets_from_prompt(prompt)) < 2:
        return False
    matrix_tokens = (
        "evidence matrix",
        "robustness matrix",
        "tension lab",
        "chain matrix",
        "证据矩阵",
        "鲁棒性矩阵",
        "张力实验室",
    )
    comparison_tokens = ("compare", "comparison", "比较", "对比")
    return any(token in prompt for token in matrix_tokens) or (
        any(token in prompt for token in comparison_tokens)
        and any(
            token in prompt
            for token in ("w0wa", "cpl", "dark energy", "dark-energy", "暗能量")
        )
    )


def _dataset_keys_named_after(
    text: str,
    start: int,
    allowed_keys: list[str],
) -> list[str]:
    prompt = str(text or "").lower()
    allowed = set(allowed_keys)
    found: set[str] = set()
    limit = min(len(prompt), start + 96)
    segment = prompt[start:limit]
    segment = re.split(
        r"[;\n]|(?:,|\b(?:and|but|then)\b)\s*"
        r"(?:(?:instead|please|subsequently|also|later|now|[a-z]+ly)\s+){0,3}"
        r"(?:run|use|execute|fit|analy[sz]e|include|add|combine)\b",
        segment,
        maxsplit=1,
    )[0]
    limit = start + len(segment)
    for family, aliases in COSMOLOGY_DATASET_FAMILY_ALIASES.items():
        for alias in aliases:
            alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
            matches = re.finditer(
                rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                prompt[start:limit],
            )
            for match in matches:
                absolute_start = start + match.start()
                absolute_end = start + match.end()
                found.update(
                    _dataset_keys_for_family_mention(
                        family,
                        prompt,
                        absolute_start,
                        absolute_end,
                    )
                    & allowed
                )
    for family, pattern in {
        "bao": r"\b(?:bao|baryon\s+acoustic)\b",
        "sn": r"\b(?:sn(?:\s+ia)?|supernova)\b",
        "cmb": r"\bcmb\b",
        "wl": r"\b(?:weak[-\s]+lensing|cosmic\s+shear|galaxy\s+lensing)\b",
        "h0": r"\b(?:an?\s+)?(?:h0|h₀)[-\s]+priors?\b",
        "hz": r"\b(?:cosmic\s+)?chronometers?\b|\bh\(z\)",
    }.items():
        if re.search(pattern, segment):
            found.update(
                key
                for key in allowed
                if _cosmology_probe_family_for_dataset(key) == family
            )
    return [key for key in allowed_keys if key in found]


def _dataset_keys_named_in_span(
    text: str,
    start: int,
    end: int,
    allowed_keys: list[str],
) -> list[str]:
    """Return concrete dataset keys in textual order inside one prompt span."""
    prompt = str(text or "").lower()
    allowed = set(allowed_keys)
    candidates: list[tuple[int, int, str]] = []
    for family, aliases in COSMOLOGY_DATASET_FAMILY_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
            for match in re.finditer(
                rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                prompt[start:end],
            ):
                absolute_start = start + match.start()
                absolute_end = start + match.end()
                for key in _dataset_keys_for_family_mention(
                    family,
                    prompt,
                    absolute_start,
                    absolute_end,
                ) & allowed:
                    candidates.append((absolute_start, absolute_end, key))

    allowed_order = {key: index for index, key in enumerate(allowed_keys)}
    ordered: list[str] = []
    seen: set[str] = set()
    for _position, _mention_end, key in sorted(
        set(candidates),
        key=lambda item: (item[0], allowed_order.get(item[2], len(allowed_order))),
    ):
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _with_without_global_keys(
    prompt: str,
    allowed_keys: list[str],
) -> list[str]:
    """Find datasets explicitly required in both comparison arms."""
    found: list[str] = []
    pattern = re.compile(
        r"\b(?:add|include|combine|plus)\b[^.;\n]{0,96}?"
        r"(?:\bto\s+both\b|\bin\s+both\b|"
        r"\b(?:in|to)\s+(?:every|each|all)\s+"
        r"(?:fits?|runs?|analyses|cases?|arms?)\b)"
    )
    for match in pattern.finditer(prompt):
        for key in _dataset_keys_named_in_span(
            prompt,
            match.start(),
            match.end(),
            allowed_keys,
        ):
            if key not in found:
                found.append(key)
    return found


def _with_without_dataset_groups(
    prompt: str,
    keys: list[str],
) -> list[list[str]] | None:
    """Build safe baseline/variant arms for one with/without clause."""
    with_without = re.search(r"\bwith\s*(?:and|/)\s*without\b", prompt)
    if not with_without:
        return None
    variant_keys = _dataset_keys_named_after(
        prompt,
        with_without.end(),
        keys,
    )
    named_variant_order = _dataset_keys_named_in_span(
        prompt,
        with_without.end(),
        len(prompt),
        variant_keys,
    )
    variant_keys = [
        *named_variant_order,
        *(key for key in variant_keys if key not in named_variant_order),
    ]
    global_keys = _with_without_global_keys(prompt, keys)
    variant_keys = [key for key in variant_keys if key not in global_keys]
    baseline = [key for key in keys if key not in variant_keys]
    if not variant_keys or not baseline:
        return None

    joint = [*baseline, *variant_keys]
    for action in re.finditer(
        r"\b(?:add|include|combine)\b[^.;\n]{0,96}",
        prompt,
    ):
        clause = action.group(0)
        action_keys = _dataset_keys_named_after(
            prompt,
            action.start(),
            keys,
        )
        if re.search(
            r"(?:only\s+to|to\s+only|only\s+in)\s+(?:the\s+)?"
            r"(?:joint|with[-\s]?(?:dataset|case|arm)?)\b"
            r"|\b(?:joint|with[-\s]?(?:dataset|case|arm)?)\s+"
            r"(?:case\s+)?only\b",
            clause,
        ):
            baseline = [key for key in baseline if key not in action_keys]
        if re.search(
            r"(?:only\s+to|to\s+only|only\s+in)\s+(?:the\s+)?baseline\b"
            r"|\bbaseline\s+(?:case\s+)?only\b",
            clause,
        ):
            joint = [key for key in joint if key not in action_keys]

    # DR1 and DR2 are alternative releases, never members of one likelihood.
    # Expand the comparison across release alternatives while retaining the
    # ordinary with/without arm for each release.
    desi_releases = [
        key for key in ("desi_dr1_bao", "desi_dr2_bao") if key in keys
    ]
    if len(desi_releases) >= 2:
        baseline_releases = [key for key in desi_releases if key in baseline]
        variant_releases = [key for key in desi_releases if key in variant_keys]
        baseline_common = [key for key in baseline if key not in desi_releases]
        variant_common = [key for key in variant_keys if key not in desi_releases]
        if baseline_releases:
            groups: list[list[str]] = []
            for release in baseline_releases:
                release_baseline = [*baseline_common, release]
                groups.append(release_baseline)
                if variant_common:
                    groups.append([*release_baseline, *variant_common])
            for release in variant_releases:
                alternative = [*baseline_common, release]
                if alternative not in groups:
                    groups.append(alternative)
            return groups
        groups = [baseline_common]
        groups.extend(
            [*baseline_common, release, *variant_common]
            for release in variant_releases
        )
        return [group for group in groups if group]
    return [baseline, joint]


def _independent_dataset_execution_clauses(
    prompt: str,
    keys: list[str],
) -> list[tuple[str, list[str]]]:
    """Return independently executed semicolon/newline clauses, if explicit."""
    clauses: list[tuple[str, list[str]]] = []
    for match in re.finditer(r"(?:^|[;\n])([^;\n]+)", prompt):
        clause = match.group(1).strip()
        if not re.search(
            r"\b(?:run|execute|fit|analy[sz]e|compare|use)\b",
            clause,
        ):
            continue
        start = match.start(1)
        end = match.end(1)
        clause_keys = _dataset_keys_named_in_span(prompt, start, end, keys)
        if clause_keys:
            clauses.append((clause, clause_keys))
    if len(clauses) < 2:
        return []
    # Generic-family expansions and anaphora need the existing whole-prompt
    # parser.  Only split when every selected key is concretely named in an
    # independent execution clause.
    if set().union(*(set(clause_keys) for _clause, clause_keys in clauses)) != set(keys):
        return []
    return clauses


def _overlapping_separate_fit_groups(
    prompt: str,
    keys: list[str],
) -> list[list[str]]:
    """Preserve repeated baselines in explicit ``A+B and A+C`` fits."""
    separate_fits = re.search(
        r"\bas\s+(?:(?:two|three|four|\d+)\s+)?separate\s+"
        r"(?:fits?|runs?|analyses)\b",
        prompt,
    )
    if not separate_fits:
        return []
    head = prompt[: separate_fits.start()]
    groups: list[list[str]] = []
    spans: list[tuple[int, int]] = []
    part_start = 0
    for conjunction in re.finditer(r"\band\b", head):
        spans.append((part_start, conjunction.start()))
        part_start = conjunction.end()
    spans.append((part_start, len(head)))
    for raw_start, raw_end in spans:
        raw_part = head[raw_start:raw_end]
        leading_space = len(raw_part) - len(raw_part.lstrip())
        trailing_space = len(raw_part) - len(raw_part.rstrip())
        part_start = raw_start + leading_space
        part_end = raw_end - trailing_space
        if part_start >= part_end:
            continue
        group = _dataset_keys_named_in_span(
            prompt,
            part_start,
            part_end,
            keys,
        )
        if len(group) >= 2:
            groups.append(group)
    if len(groups) >= 2 and set().union(*(set(group) for group in groups)) == set(keys):
        return groups
    return []


def _cosmology_dataset_groups_from_prompt(
    text: str,
    dataset_keys: list[str],
) -> list[list[str]]:
    """Preserve explicit baseline/joint and separate-dataset run structure."""
    keys = list(dict.fromkeys(dataset_keys))
    if len(keys) < 2:
        return [keys]
    prompt = str(text or "").lower()

    independent_clauses = _independent_dataset_execution_clauses(prompt, keys)
    if independent_clauses:
        groups: list[list[str]] = []
        for clause, clause_keys in independent_clauses:
            clause_with_without = _with_without_dataset_groups(clause, clause_keys)
            if clause_with_without:
                groups.extend(clause_with_without)
            elif _last_dataset_group_mode(clause) == "separate":
                groups.extend([[key] for key in clause_keys])
            else:
                groups.append(clause_keys)
        return groups

    overlapping_groups = _overlapping_separate_fit_groups(prompt, keys)
    if overlapping_groups:
        return overlapping_groups

    with_without_groups = _with_without_dataset_groups(prompt, keys)
    if with_without_groups:
        return with_without_groups

    scoped_separate = re.search(
        r"\b(?:separately|independently)\s+"
        r"(?:run|fit|analy[sz]e|use|compare)\b",
        prompt,
    )
    if scoped_separate:
        separate_keys = _dataset_keys_named_after(
            prompt,
            scoped_separate.end(),
            keys,
        )
        joint_keys = [key for key in keys if key not in separate_keys]
        if separate_keys and joint_keys:
            return [joint_keys, *[[key] for key in separate_keys]]

    trailing_separate = re.search(
        r"\b(?:run|fit|analy[sz]e|use|compare)\b"
        r"[^;,\n]{0,72}\b(?:separately|independently)\b",
        prompt,
    )
    if trailing_separate:
        separate_keys = _dataset_keys_named_after(
            prompt,
            trailing_separate.start(),
            keys,
        )
        joint_keys = [key for key in keys if key not in separate_keys]
        if separate_keys and joint_keys:
            return [joint_keys, *[[key] for key in separate_keys]]

    if _last_dataset_group_mode(prompt) == "separate":
        return [[key] for key in keys]
    return [keys]


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
                    "bao_dataset_key": _cosmology_bao_dataset_key_from_prompt(text),
                },
            }
            for model in models
        ]
    dataset_groups = _cosmology_dataset_groups_from_prompt(text, dataset_keys)
    return [
        {
            "id": f"auto_cosmo_config_{uuid.uuid4().hex}",
            "name": "build_cosmology_likelihood",
            "input": {
                "model": model,
                "dataset_keys": group,
                "output_format": "both",
            },
        }
        for model in models
        for group in dataset_groups
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

    if _should_run_dark_energy_evidence_matrix(raw):
        explicit_models = []
        if _prompt_term_last_intent(
            t,
            r"(?<![a-z0-9])(?:w0wa(?:[\s_-]*cdm)?|cpl)(?![a-z0-9])",
        ):
            explicit_models.append("w0wa_cdm")
        if _prompt_term_last_intent(
            t,
            r"(?<![a-z0-9])wcdm(?![a-z0-9])",
        ):
            explicit_models.append("wcdm")
        if _prompt_term_last_intent(
            t,
            r"(?<![a-z0-9])(?:lcdm|λcdm)(?![a-z0-9])",
        ):
            explicit_models.append("lcdm")
        # The registered headline DR2 matrix is base_w_wa. Defaulting this
        # dedicated route to LCDM would silently select a known capability gap.
        model = explicit_models[0] if explicit_models else "w0wa_cdm"
        wants_dr1_reference = (
            bool(re.search(r"(?<![a-z0-9])dr[\s_-]*1(?![a-z0-9])", t))
            and any(token in t for token in ("reference", "compare", "comparison", "参考", "对比"))
        )
        return [
            {
                "id": f"direct_route_{uuid.uuid4().hex}",
                "name": "run_dark_energy_evidence_matrix",
                "input": {
                    "model": model,
                    "supernova_sets": _cosmology_supernova_sets_from_prompt(raw),
                    "include_desi_dr1_reference": wants_dr1_reference,
                },
            }
        ]

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
    explicit_planck_shoes_anchor = (
        "planck" in t
        and "sh0es" in t
        and any(token in t for token in ("compare", "anchor", "tension"))
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
    if (
        any(k in t for k in hubble_triggers) or explicit_planck_shoes_anchor
    ) and not matrix_or_extended_context:
        return [{
            "id": f"direct_route_{uuid.uuid4().hex}",
            "name": "compare_luminosity_distances",
            "input": {
                "target_cosmology": "riess22_shoes",
                "comparison_mode": "h0_anchors",
            },
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
    if "spt3g_cmb" in dataset_keys:
        # SPT-3G damping-tail likelihoods are not yet executable in the
        # registry.  Do not substitute Planck/ACT compressed posteriors for an
        # SPT workflow; the assistant should report config/registry status.
        return []
    model_prompt = str(text or "").lower()
    explicit_model_count = sum((
        bool(_prompt_term_last_intent(
            model_prompt,
            r"(?<![a-z0-9])(?:lcdm|λcdm)(?![a-z0-9])",
        )),
        bool(_prompt_term_last_intent(
            model_prompt,
            r"(?<![a-z0-9])wcdm(?![a-z0-9])",
        )),
        bool(_prompt_term_last_intent(
            model_prompt,
            r"(?<![a-z0-9])(?:w0wa(?:[\s_-]*cdm)?|cpl)(?![a-z0-9])",
        )),
        bool(_prompt_term_last_intent(
            model_prompt,
            r"\b(?:curvature|curved|non[-\s]?flat|omega_?k|omegak)\b|Ωk|曲率|非平坦",
        )),
        bool(_prompt_term_last_intent(
            model_prompt,
            r"\b(?:neutrino|mnu|m_ν|mν|sum\s+m|nu\s+mass)\b|Σm|Σmν|中微子",
        )),
    ))
    explicit_multi_model_run = explicit_model_count >= 2 and bool(re.search(
        r"\b(?:run|fit|execute)\b[^.;\n]{0,160}"
        r"(?:lcdm|λcdm|wcdm|w0wa|cpl|curved|curvature|neutrino|mnu)"
        r"|(?:运行|拟合|执行)[^。；\n]{0,160}"
        r"(?:lcdm|λcdm|wcdm|w0wa|cpl|曲率|中微子)",
        model_prompt,
        re.IGNORECASE,
    ))
    run_models = (
        models
        if explicit_multi_model_run
        else ["lcdm"] if "lcdm" in models else [models[0]]
    )
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
                    "bao_dataset_key": _cosmology_bao_dataset_key_from_prompt(text),
                },
            }
            for model in run_models
        ]
    dataset_groups = _cosmology_dataset_groups_from_prompt(text, dataset_keys)
    return [
        {
            "id": f"auto_cosmo_run_{uuid.uuid4().hex}",
            "name": "run_cosmology_likelihood_chain",
            "input": {
                "model": model,
                "dataset_keys": group,
            },
        }
        for model in run_models
        for group in dataset_groups
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
    def has_nonnegated_term(terms: tuple[str, ...]) -> bool:
        for term in terms:
            start = 0
            while True:
                index = prompt.find(term, start)
                if index < 0:
                    break
                before = prompt[max(0, index - 64) : index]
                clause_before = re.split(r"[.;\n,]", before)[-1]
                if not re.search(
                    r"\b(?:without|exclude|excluding|avoid|avoiding|"
                    r"do\s+not|don't|never|not)\b[^.;\n]{0,40}$",
                    clause_before,
                ):
                    return True
                start = index + len(term)
        return False

    has_birefringence = has_nonnegated_term((
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
        ))
    has_feature_template = has_nonnegated_term((
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
        ))
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
