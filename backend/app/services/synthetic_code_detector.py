"""G2: detect synthetic / fabricated data generation in run_python code.

The Pleiades / HD 209458 b reviewer saw the AI bypass the zero-fabrication
gate by writing Python code that ITSELF generated the "data", then citing
the numbers from its own output.  The contract in G1 (`data_source`) puts
declaration responsibility on the AI — this module verifies the declaration
against the actual code body.

Three verdicts:
- ``clean``      : no red flags.  Legitimate MCMC / bootstrap use is fine.
- ``suspicious`` : some red flags, but the code also reads real data.
- ``synthetic``  : clearly fabricates data with no real-data inputs.

The LLM's declared ``data_source`` is checked against ``verdict``:
- declared real source AND verdict == "synthetic"  → contract violation
- declared real source AND verdict == "suspicious" → downgrade to SYNTHETIC
- declared ``none_not_analyzing_real_data``        → always SYNTHETIC (G1.3)

See tests/test_synthetic_code_detector.py for canonical fixtures.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["clean", "suspicious", "synthetic"]


@dataclass
class DetectionResult:
    verdict: Verdict
    has_np_random: bool = False
    has_time_linspace: bool = False
    suspicious_keywords: list[str] = field(default_factory=list)
    reads_real_data: bool = False
    legitimate_random_context: bool = False
    notes: list[str] = field(default_factory=list)


# Functions / modules whose presence legitimises np.random usage — MCMC,
# bootstrap, dynesty, etc. all need genuine random numbers.
_LEGIT_RANDOM_IDENTIFIERS = {
    "emcee", "dynesty", "ultranest", "arviz", "pymc", "pymc3",
    "bootstrap", "resample", "permute", "jackknife",
    "EnsembleSampler", "NestedSampler", "sample_prior",
    "mcmc", "MCMC",
}

# Real-data reader helpers — their presence indicates the code IS working
# on actual observational data, even if it also generates some randomness.
_REAL_DATA_READERS = {
    "get_search_results", "get_adql_results", "get_adql_result_sets",
    "get_cached_results", "load_fits", "load_votable", "load_csv",
    "fits.open", "Table.read", "pd.read_csv", "pd.read_parquet",
    "lightkurve.search_lightcurve", "astroquery",
}

# Keyword / phrase blacklist.  Matched in comments, docstrings, and string
# literals.  Each ~doubles suspicion.
_SUSPICIOUS_KEYWORDS = [
    re.compile(r"\b(?:let'?s\s+)?simulate\b", re.IGNORECASE),
    re.compile(r"\bsynthetic\s+(?:data|lightcurve|spectrum|catalog)", re.IGNORECASE),
    re.compile(r"\b(?:mock|fake)\s+(?:data|lightcurve|spectrum)", re.IGNORECASE),
    re.compile(r"\bgenerate\s+(?:a\s+)?(?:realistic|synthetic|fake|mock|example)\s+\w+", re.IGNORECASE),
    re.compile(r"\bbased\s+on\s+known\s+parameters\b", re.IGNORECASE),
    re.compile(r"\b(?:MAST|Gaia|SDSS|VizieR|TESS).{0,40}(?:timing\s+out|timeout|unavailable|failed)", re.IGNORECASE),
    re.compile(r"\bwe(?:'re|\s+are)\s+going\s+to\s+(?:create|build|simulate)\s+(?:a|an)?\s*\w+\s+light\s*curve", re.IGNORECASE),
    re.compile(r"\bsince\s+(?:direct\s+)?(?:MAST|Gaia|SDSS|VizieR|TESS)\b.{0,40}(?:is\s+)?(?:timing|failing|unavailable)", re.IGNORECASE),
]


class _CodeVisitor(ast.NodeVisitor):
    """Walk the AST once, collecting the features we care about."""

    def __init__(self) -> None:
        self.np_random_calls: list[str] = []  # np.random.normal etc.
        self.linspace_calls: list[str] = []   # np.linspace / np.arange
        self.real_data_reader_calls: list[str] = []
        self.legit_random_refs: list[str] = []
        self.suspicious_var_names: list[str] = []

    def _attribute_chain(self, node: ast.AST) -> str:
        """Return a dotted chain like 'np.random.normal' if possible."""
        parts: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    def visit_Call(self, node: ast.Call) -> None:
        chain = self._attribute_chain(node.func)
        if not chain and isinstance(node.func, ast.Name):
            chain = node.func.id
        # np.random.*
        if "np.random" in chain or "numpy.random" in chain:
            self.np_random_calls.append(chain)
        # np.linspace / np.arange — only flagged if used to build a "time"
        # axis (detected by surrounding usage, checked in run()).
        if chain in {"np.linspace", "numpy.linspace", "np.arange", "numpy.arange"}:
            self.linspace_calls.append(chain)
        # Real-data readers
        for reader in _REAL_DATA_READERS:
            if reader in chain:
                self.real_data_reader_calls.append(chain)
                break
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _LEGIT_RANDOM_IDENTIFIERS:
            self.legit_random_refs.append(node.id)
        if any(
            node.id.startswith(p) for p in
            ("synthetic_", "fake_", "mock_", "sim_", "example_", "dummy_")
        ):
            self.suspicious_var_names.append(node.id)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in _LEGIT_RANDOM_IDENTIFIERS:
                self.legit_random_refs.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module in _LEGIT_RANDOM_IDENTIFIERS:
            self.legit_random_refs.append(node.module)
        for alias in node.names:
            if alias.name in _LEGIT_RANDOM_IDENTIFIERS:
                self.legit_random_refs.append(alias.name)
        self.generic_visit(node)


def analyze(code: str) -> DetectionResult:
    """Classify a run_python code string.  Never raises — returns
    verdict='clean' if the code can't be parsed (the sandbox will give
    the real SyntaxError later).
    """
    result = DetectionResult(verdict="clean")

    # Parse
    try:
        tree = ast.parse(code)
    except SyntaxError:
        result.notes.append("code has SyntaxError; detector skipped")
        return result

    visitor = _CodeVisitor()
    visitor.visit(tree)

    result.has_np_random = bool(visitor.np_random_calls)
    result.reads_real_data = bool(visitor.real_data_reader_calls)
    result.legitimate_random_context = bool(visitor.legit_random_refs)

    # np.linspace / np.arange are only suspicious when the variable
    # assigned holds the word "time" / "t" / "bjd" / "mjd" — cheap lexical
    # heuristic.  Otherwise they're common array helpers.
    if visitor.linspace_calls:
        # Scan lines that contain linspace/arange
        for line in code.split("\n"):
            lower = line.lower()
            if ("linspace" in lower or "arange" in lower) and re.search(
                r"\b(t|time|bjd|mjd|jd|days?|epochs?)\s*=\s*(?:np\.)?(?:linspace|arange)",
                lower,
            ):
                result.has_time_linspace = True
                break

    # Keyword / phrase scan across comments and string literals
    for pat in _SUSPICIOUS_KEYWORDS:
        m = pat.search(code)
        if m:
            result.suspicious_keywords.append(m.group(0))

    # Suspicious variable names
    if visitor.suspicious_var_names:
        result.notes.append(f"variables: {visitor.suspicious_var_names}")

    # Classification
    hard_signals = 0
    if result.has_np_random:
        hard_signals += 1
    if result.has_time_linspace:
        hard_signals += 1
    if result.suspicious_keywords:
        hard_signals += 1
    if visitor.suspicious_var_names:
        hard_signals += 1

    if hard_signals == 0:
        result.verdict = "clean"
    elif result.reads_real_data or result.legitimate_random_context:
        # Random + real data ⇒ probably bootstrap / MCMC over real data
        result.verdict = "clean" if hard_signals == 1 else "suspicious"
    else:
        # Random without any real-data anchor ⇒ synthetic
        # ≥2 hard signals → synthetic
        # 1 hard signal (just np.random, no keywords, no time axis) →
        # suspicious (could be legitimate randomness in a formula demo)
        result.verdict = "synthetic" if hard_signals >= 2 else "suspicious"

    return result
