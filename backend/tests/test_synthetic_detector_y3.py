"""PART Y Batch 3: synthetic_code_detector 加固 — 验证 6 个新覆盖盲区.

Audit (Agent 5A) 列出的可绕过 pattern:
1. `from scipy.stats import norm; norm.rvs(size=100)` — scipy 不在白名单
2. `import random; random.gauss(0, 1)` — stdlib random 不在白名单
3. `np.array([0.5, 0.51, ..., many literals])` — 大字面量数组
4. `np.zeros(100) + 0.01` — np.zeros / np.ones 当作 constant 数据列
5. 单 `import emcee` 不真用 sampler + np.random — emcee 豁免太宽

Plus 一条向后兼容测试: 真用 EnsembleSampler 仍判 clean.
"""

from __future__ import annotations

from app.services.synthetic_code_detector import analyze


def test_scipy_stats_rvs_is_treated_as_synthetic_random() -> None:
    """scipy.stats.norm.rvs(size=N) is fabricated data with no legit context."""
    code = """
from scipy.stats import norm
x = norm.rvs(size=100)
print(x.mean())
"""
    result = analyze(code)
    assert result.has_np_random is True, (
        "scipy.stats.*.rvs should count as random number generation"
    )
    # No real data, no actual MCMC usage → synthetic or suspicious
    assert result.verdict in {"synthetic", "suspicious"}


def test_stdlib_random_gauss_is_treated_as_synthetic_random() -> None:
    """import random; random.gauss / random.uniform / etc. is also fabrication."""
    code = """
import random
data = [random.gauss(0, 1) for _ in range(50)]
print(sum(data))
"""
    result = analyze(code)
    assert result.has_np_random is True, (
        "stdlib random.gauss / random.uniform should count as random generation"
    )
    assert result.verdict in {"synthetic", "suspicious"}


def test_random_uniform_no_legit_context_is_flagged() -> None:
    """random.uniform without MCMC / real data should not pass clean."""
    code = """
import random
flux = [random.uniform(0.99, 1.01) for _ in range(100)]
"""
    result = analyze(code)
    assert result.verdict != "clean"


def test_large_literal_array_via_np_array_is_suspicious() -> None:
    """np.array([many constants]) is fabricated data."""
    code = """
import numpy as np
flux = np.array([0.5, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57, 0.58, 0.59])
print(flux.mean())
"""
    result = analyze(code)
    assert result.has_large_literal_array is True
    assert result.verdict in {"synthetic", "suspicious"}, (
        f"large literal array should not pass clean (got {result.verdict})"
    )


def test_small_literal_array_via_np_array_passes_clean() -> None:
    """np.array([1, 2, 3]) is normal initialisation, not fabrication."""
    code = """
import numpy as np
ref_pixel = np.array([1024, 512, 256])
print(ref_pixel)
"""
    result = analyze(code)
    assert result.has_large_literal_array is False
    # Small literal arrays should not flip the verdict
    assert result.verdict == "clean"


def test_np_zeros_constant_redshift_is_synthetic() -> None:
    """np.zeros(N) used as a constant column for redshift = synthetic."""
    code = """
import numpy as np
import pandas as pd
z = np.zeros(50) + 0.5  # fabricated redshift column
df = pd.DataFrame({'z': z})
"""
    result = analyze(code)
    # z = np.zeros(50) + 0.5 should be flagged as constant-redshift sequence
    # via the new np.zeros/ones support in _is_constant_numeric_sequence.
    assert result.has_constant_redshift_sequence is True
    assert result.verdict == "synthetic"


def test_emcee_import_only_does_not_exempt_np_random() -> None:
    """PART Y Batch 3: bare `import emcee` no longer exempts np.random.

    Audit case: AI writes `import emcee` then `np.random.normal(size=N)` to
    fabricate data without ever calling a sampler. Old detector judged this
    clean (legit_random_context=True, hard_signals=1). Should now flag.
    """
    code = """
import emcee
import numpy as np
x = np.random.normal(0, 1, 100)
print(x.mean())
"""
    result = analyze(code)
    assert result.legitimate_random_context is True  # `emcee` imported
    assert result.actual_mcmc_usage is False, (
        "import emcee alone is NOT actual MCMC usage"
    )
    assert result.verdict != "clean", (
        "bare import emcee + np.random must not pass clean"
    )


def test_actual_mcmc_call_keeps_np_random_clean() -> None:
    """When an MCMC sampler is genuinely called, np.random is legit context."""
    code = """
import emcee
import numpy as np

def log_prob(theta):
    return -0.5 * np.sum(theta ** 2)

ndim, nwalkers = 2, 10
p0 = np.random.randn(nwalkers, ndim)
sampler = emcee.EnsembleSampler(nwalkers, ndim, log_prob)
sampler.run_mcmc(p0, 100)
"""
    result = analyze(code)
    assert result.actual_mcmc_usage is True, (
        "EnsembleSampler / run_mcmc usage should register actual MCMC"
    )
    assert result.verdict == "clean"


def test_bootstrap_call_keeps_np_random_clean() -> None:
    """bootstrap() / resample() actual usage is legit random context."""
    code = """
import numpy as np

def bootstrap(data, n=100):
    samples = []
    for _ in range(n):
        idx = np.random.choice(len(data), size=len(data), replace=True)
        samples.append(data[idx].mean())
    return samples

result = bootstrap(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
"""
    result = analyze(code)
    assert result.actual_mcmc_usage is True
    assert result.verdict == "clean"
