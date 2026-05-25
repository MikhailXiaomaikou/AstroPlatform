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

Verdict = Literal["clean", "suspicious", "synthetic", "inert"]


@dataclass
class DetectionResult:
    verdict: Verdict
    has_np_random: bool = False
    has_time_linspace: bool = False
    has_schematic_phase_curve: bool = False
    has_constant_redshift_sequence: bool = False
    has_large_literal_array: bool = False
    suspicious_keywords: list[str] = field(default_factory=list)
    reads_real_data: bool = False
    reads_time_series_data: bool = False
    legitimate_random_context: bool = False
    actual_mcmc_usage: bool = False  # PART Y Batch 3
    notes: list[str] = field(default_factory=list)


# Functions / modules whose presence legitimises np.random usage — MCMC,
# bootstrap, dynesty, etc. all need genuine random numbers.
#
# PART Y Batch 3: split into "module name" (just imported / referenced) vs
# "actual usage" (an MCMC sampler or bootstrap routine is genuinely called).
# Bare `import emcee` should NOT exempt np.random.normal — the audit found
# AI agents writing `import emcee` then fabricating data with np.random and
# never calling any sampler.
_LEGIT_RANDOM_MODULE_NAMES = {
    "emcee", "dynesty", "ultranest", "arviz", "pymc", "pymc3",
    "mcmc", "MCMC",
}
_LEGIT_RANDOM_USAGE_NAMES = {
    "EnsembleSampler", "NestedSampler", "sample_prior",
    "bootstrap", "resample", "permute", "jackknife",
    "run_mcmc", "sample",
}
_LEGIT_RANDOM_IDENTIFIERS = _LEGIT_RANDOM_MODULE_NAMES | _LEGIT_RANDOM_USAGE_NAMES

# Real-data reader helpers — their presence indicates the code IS working
# on actual observational data, even if it also generates some randomness.
_REAL_DATA_READERS = {
    "get_search_results", "get_adql_results", "get_adql_result_sets",
    "get_cached_results", "load_fits", "load_votable", "load_csv",
    "fits.open", "Table.read", "pd.read_csv", "pd.read_parquet",
    "lightkurve.search_lightcurve", "astroquery",
    "search_lightcurve", "astro.search_lightcurve",
    "download_and_clean_lightcurve", "astro.download_and_clean_lightcurve",
    "transit_search", "astro.transit_search",
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
        self.time_axis_builder_calls: list[str] = []  # pd.date_range etc.
        self.real_data_reader_calls: list[str] = []
        self.time_series_reader_calls: list[str] = []
        self.legit_random_refs: list[str] = []
        self.legit_random_actual_usages: list[str] = []  # PART Y Batch 3
        self.suspicious_var_names: list[str] = []
        self.constant_redshift_sequences: list[str] = []
        self.phase_axis_generators: list[str] = []
        self.schematic_curve_assignments: list[str] = []
        self.large_literal_arrays: list[str] = []  # PART Y Batch 3

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

    def _getattr_targets_rng(self, node: ast.Call) -> bool:
        """True when getattr(base, name) resolves to a random-number API."""
        if len(node.args) < 2:
            return False
        base = self._attribute_chain(node.args[0])
        if not base and isinstance(node.args[0], ast.Name):
            base = node.args[0].id
        name_arg = node.args[1]
        name_val = (
            name_arg.value
            if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)
            else ""
        )
        rng_bases = {
            "np", "numpy", "np.random", "numpy.random", "random",
            "torch", "jax", "jax.random", "tf", "tensorflow", "scipy.stats",
        }
        rng_names = {
            "random", "randn", "rand", "normal", "uniform", "gauss",
            "choice", "poisson", "randint", "standard_normal", "rvs",
            "normalvariate", "multivariate_normal", "lognormal",
        }
        if base in {"np.random", "numpy.random", "jax.random"}:
            return True
        if base in rng_bases and (name_val in rng_names or "random" in name_val):
            return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        chain = self._attribute_chain(node.func)
        if not chain and isinstance(node.func, ast.Name):
            chain = node.func.id
        # np.random.* / numpy.random.*
        if "np.random" in chain or "numpy.random" in chain:
            self.np_random_calls.append(chain)
        # PART Y Batch 3: scipy.stats.*.rvs / scipy.stats.norm.rvs etc. ARE
        # random number generation just like np.random. Audit found AI
        # agents writing `from scipy.stats import norm; norm.rvs(size=100)`
        # to bypass the np.random detector — count it the same way.
        if "scipy.stats" in chain or chain.endswith(".rvs"):
            self.np_random_calls.append(chain)
        # PART Y Batch 3: stdlib `random` module — random.gauss, random.uniform,
        # random.choice, etc. Same fabrication risk as np.random.
        if chain.startswith("random."):
            tail = chain.split(".", 1)[1]
            if tail in {
                "random", "uniform", "gauss", "normalvariate", "choice",
                "sample", "shuffle", "randint", "betavariate",
                "lognormvariate", "expovariate", "vonmisesvariate",
                "weibullvariate", "triangular", "paretovariate",
                "gammavariate",
            }:
                self.np_random_calls.append(chain)
        # PART AD: GPU / array-framework RNGs — torch / jax / tensorflow ship
        # their own generators that the np.random / scipy / stdlib matchers miss.
        if (
            (chain.startswith("torch.") and chain.rsplit(".", 1)[-1] in {
                "rand", "randn", "randint", "randperm", "normal", "bernoulli",
                "multinomial", "poisson", "rand_like", "randn_like",
            })
            or "jax.random" in chain
            or "tf.random" in chain
            or "tensorflow.random" in chain
        ):
            self.np_random_calls.append(chain)
        # PART AD: dynamic getattr access used to dodge the static matcher,
        # e.g. getattr(np, "random")(...) / getattr(np.random, "normal").
        if chain == "getattr" and self._getattr_targets_rng(node):
            self.np_random_calls.append("getattr<rng>")
        # np.linspace / np.arange — only flagged if used to build a "time"
        # axis (detected by surrounding usage, checked in run()).
        if chain in {"np.linspace", "numpy.linspace", "np.arange", "numpy.arange"}:
            self.linspace_calls.append(chain)
        # PART AD: pandas date_range is almost always a fabricated time axis.
        if chain in {"pd.date_range", "pandas.date_range"}:
            self.time_axis_builder_calls.append(chain)
        # Real-data readers
        for reader in _REAL_DATA_READERS:
            if reader in chain:
                self.real_data_reader_calls.append(chain)
                break
        if any(
            token in chain
            for token in (
                "search_lightcurve", "download_and_clean_lightcurve",
                "transit_search", "phase_fold", "plot_phase_folded",
                "lomb_scargle_period",
            )
        ):
            self.time_series_reader_calls.append(chain)
        if chain in {"get_cached_results", "astro.get_cached_results"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and str(first.value) == "latest_lightcurve":
                self.time_series_reader_calls.append("get_cached_results(latest_lightcurve)")
        # PART Y Batch 3: actual MCMC usage — distinguishing import vs call.
        # `EnsembleSampler(...)` / `bootstrap(...)` count; `import emcee` alone
        # does NOT (handled in visit_Import / visit_ImportFrom).
        last_attr = chain.rsplit(".", 1)[-1] if chain else ""
        if last_attr in _LEGIT_RANDOM_USAGE_NAMES:
            self.legit_random_actual_usages.append(chain or last_attr)
        # PART Y Batch 3: large literal arrays via np.array([many constants])
        # are fabricated data. Audit case: `np.array([0.5, 0.51, ..., 50 vals])`.
        if chain in {"np.array", "numpy.array", "np.asarray", "numpy.asarray"} and node.args:
            first = node.args[0]
            if isinstance(first, (ast.List, ast.Tuple)) and len(first.elts) >= 8:
                if all(
                    isinstance(el, ast.Constant) and isinstance(el.value, (int, float))
                    for el in first.elts
                ):
                    self.large_literal_arrays.append(f"{chain}([{len(first.elts)} const])")
        self.generic_visit(node)

    def _target_names(self, target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Tuple):
            out: list[str] = []
            for elt in target.elts:
                out.extend(self._target_names(elt))
            return out
        if isinstance(target, ast.Subscript):
            # df["z"] = ...
            sl = target.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                return [sl.value]
        return []

    def _is_redshift_name(self, name: str) -> bool:
        lowered = name.lower()
        return lowered in {"z", "redshift", "zsp", "z_spec", "specz"} or "redshift" in lowered

    def _is_constant_numeric_sequence(self, node: ast.AST) -> bool:
        # [0.05] * n or np.full(n, 0.05) — a “constant-column” fake sample.
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Mult):
                left = node.left
                if (
                    isinstance(left, ast.List)
                    and len(left.elts) == 1
                    and isinstance(left.elts[0], ast.Constant)
                    and isinstance(left.elts[0].value, (int, float))
                ):
                    return True
            # PART Y Batch 3: `np.zeros(N) + 0.5` / `np.ones(N) * 2.0` —
            # using zeros/ones as a constant column with an offset is still synthetic data.
            for child in (node.left, node.right):
                if isinstance(child, ast.Call):
                    inner_chain = self._attribute_chain(child.func)
                    if inner_chain in {
                        "np.zeros", "numpy.zeros",
                        "np.ones", "numpy.ones",
                        "np.full", "numpy.full",
                    }:
                        return True
        if isinstance(node, ast.Call):
            chain = self._attribute_chain(node.func)
            if chain in {"np.full", "numpy.full"} and len(node.args) >= 2:
                fill = node.args[1]
                return isinstance(fill, ast.Constant) and isinstance(fill.value, (int, float))
            # PART Y Batch 3: np.zeros(N) / np.ones(N) used as a constant
            # data column (audit: `np.zeros(100) + 0.01` style fabrication).
            if chain in {"np.zeros", "numpy.zeros", "np.ones", "numpy.ones"} and node.args:
                return True
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        target_names: list[str] = []
        for target in node.targets:
            target_names.extend(self._target_names(target))

        value_chain = self._call_chain_in_expr(node.value)
        for name in target_names:
            lowered = name.lower()
            if (
                ("phase" in lowered)
                and value_chain in {"np.linspace", "numpy.linspace", "np.arange", "numpy.arange"}
            ):
                self.phase_axis_generators.append(name)
            if (
                any(tok in lowered for tok in ("mag", "flux", "lightcurve", "curve"))
                and self._looks_like_analytic_curve(node.value)
            ):
                self.schematic_curve_assignments.append(name)

        if self._is_constant_numeric_sequence(node.value):
            for name in target_names:
                if self._is_redshift_name(name):
                    self.constant_redshift_sequences.append(name)
        self.generic_visit(node)

    def _call_chain_in_expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Call):
            return self._attribute_chain(node.func)
        return ""

    def _looks_like_analytic_curve(self, node: ast.AST) -> bool:
        """Heuristic for a plotted light/phase curve generated from formulas.

        This catches the R0 failure mode: code reads a real catalog row
        (period/amplitude) but then fabricates phase/magnitude arrays with
        np.linspace + np.interp/piecewise/where/sin/cos and presents that
        as a phase-folded observation.  Real time-series analysis normally
        calls phase_fold / lomb_scargle / download helpers and is exempt.
        """
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                chain = self._attribute_chain(child.func)
                if chain in {
                    "np.interp", "numpy.interp", "np.piecewise", "numpy.piecewise",
                    "np.where", "numpy.where", "np.sin", "numpy.sin",
                    "np.cos", "numpy.cos",
                }:
                    return True
            if isinstance(child, ast.Name) and child.id.lower() in {"phase", "phases", "phase_grid"}:
                return True
        return False

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, val in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and self._is_redshift_name(key.value)
                and self._is_constant_numeric_sequence(val)
            ):
                self.constant_redshift_sequences.append(key.value)
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


def _is_inert_expr(node: ast.expr) -> bool:
    """True iff an expression is a pure literal computation — no name
    reads, no function calls.  Covers: Constant, BinOp over constants,
    UnaryOp over constants, nested tuples/lists of constants.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.BinOp):
        return _is_inert_expr(node.left) and _is_inert_expr(node.right)
    if isinstance(node, ast.UnaryOp):
        return _is_inert_expr(node.operand)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_inert_expr(el) for el in node.elts)
    return False


def _is_inert_stmt(node: ast.stmt) -> bool:
    """True iff a top-level statement is `print(<literal>...)` or a
    standalone literal expression.  Nothing else qualifies — no imports,
    assignments, loops, conditions, defs, or variable reads."""
    if not isinstance(node, ast.Expr):
        return False
    value = node.value
    if _is_inert_expr(value):
        return True
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "print"
        and all(_is_inert_expr(arg) for arg in value.args)
        and not value.keywords  # reject print(..., file=sys.stderr) forms
    ):
        return True
    return False


def _is_inert_code(tree: ast.Module) -> bool:
    """True iff EVERY top-level statement is inert (see above).  Empty
    code doesn't count — has nothing to diagnose.
    """
    if not tree.body:
        return False
    return all(_is_inert_stmt(stmt) for stmt in tree.body)


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

    # R5 O3: code consisting only of literal print / arithmetic statements
    # (diagnostic / smoke test) returns early with verdict='inert'. Even if
    # the AI declares data_source='none...', this kind of code is not data
    # synthesis — subprocess environment self-checks should not be flagged
    # with a SYNTHETIC banner.
    if _is_inert_code(tree):
        result.verdict = "inert"
        result.notes.append("code contains only literal output (inert/diagnostic)")
        return result

    visitor = _CodeVisitor()
    visitor.visit(tree)

    result.has_np_random = bool(visitor.np_random_calls)
    result.reads_real_data = bool(visitor.real_data_reader_calls)
    result.reads_time_series_data = bool(visitor.time_series_reader_calls)
    result.legitimate_random_context = bool(visitor.legit_random_refs)
    result.actual_mcmc_usage = bool(visitor.legit_random_actual_usages)
    result.has_constant_redshift_sequence = bool(visitor.constant_redshift_sequences)
    result.has_large_literal_array = bool(visitor.large_literal_arrays)
    if visitor.large_literal_arrays:
        result.notes.append(f"large_literal_arrays: {visitor.large_literal_arrays}")

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

    # PART AD: pandas date_range is a time-axis builder on its own — no
    # variable-name heuristic needed (date_range exists to make time series).
    if visitor.time_axis_builder_calls:
        result.has_time_linspace = True

    # Keyword / phrase scan across comments and string literals
    for pat in _SUSPICIOUS_KEYWORDS:
        m = pat.search(code)
        if m:
            result.suspicious_keywords.append(m.group(0))

    # Suspicious variable names
    if visitor.suspicious_var_names:
        result.notes.append(f"variables: {visitor.suspicious_var_names}")
    if visitor.constant_redshift_sequences:
        result.notes.append(f"constant_redshift_sequences: {visitor.constant_redshift_sequences}")
    if visitor.phase_axis_generators and visitor.schematic_curve_assignments and not result.reads_time_series_data:
        result.has_schematic_phase_curve = True
        result.notes.append(
            "schematic_phase_curve: phase axis plus analytic mag/flux curve without time-series reader"
        )

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
    if result.has_constant_redshift_sequence:
        hard_signals += 1
    if result.has_schematic_phase_curve:
        hard_signals += 1
    if result.has_large_literal_array:
        hard_signals += 1

    if hard_signals == 0:
        result.verdict = "clean"
    elif result.reads_real_data:
        # When real data is read, np.linspace + "synthetic/model" comments often
        # appear for plotting a comparison model curve and should not downgrade
        # a genuine analysis on keywords alone. The real danger is random/fake
        # generation mixed into real claims, or a catalog summary assembled into
        # a schematic phase curve that masquerades as a real folded light curve.
        if result.has_schematic_phase_curve:
            result.verdict = "suspicious"
        elif result.has_np_random and not result.actual_mcmc_usage:
            result.verdict = "suspicious" if hard_signals >= 2 else "clean"
        elif result.has_large_literal_array:
            # A real reader alongside a large literal array is rare: typically
            # the reader fetches a catalog while the literal array is a fabricated
            # comparison; mark as suspicious.
            result.verdict = "suspicious"
        else:
            result.verdict = "clean"
    elif result.actual_mcmc_usage:
        # PART Y Batch 3: an MCMC sampler / bootstrap was actually invoked (not
        # just imported). Tolerate 1 hard signal (usually np.random itself);
        # multiple signals still result in suspicious.
        result.verdict = "clean" if hard_signals <= 1 else "suspicious"
    else:
        # PART Y Batch 3: a bare `import emcee` / `import arviz` is no longer
        # exempt. Falls through to the else branch the same as having no legit context at all.
        if result.has_constant_redshift_sequence:
            result.verdict = "synthetic"
            return result
        if result.has_large_literal_array and result.has_np_random:
            # large literal array + random generation + no real data = synthetic
            result.verdict = "synthetic"
            return result
        # Random without any real-data anchor ⇒ synthetic
        # ≥2 hard signals → synthetic
        # 1 hard signal (just np.random, no keywords, no time axis) →
        # suspicious (could be legitimate randomness in a formula demo)
        result.verdict = "synthetic" if hard_signals >= 2 else "suspicious"

    return result
