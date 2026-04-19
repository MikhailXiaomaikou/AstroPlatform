"""G2 tests for synthetic_code_detector.

Fixtures include the reviewer's exact Paper-2 failure pattern (HD 209458
TESS "simulate realistic light curve" code) + legitimate MCMC/bootstrap
patterns + mixed cases.
"""

from __future__ import annotations

from app.services.synthetic_code_detector import analyze


# ---------------------------------------------------------------- synthetic
#
# These are the patterns the zero-fabrication gate missed before G2.


def test_pleiades_paper2_hd209458_synthetic():
    """Reviewer's exact pattern: generate a TESS lightcurve "based on
    known parameters" because MAST was timing out."""
    code = """
# Since direct MAST access is timing out, let's simulate a realistic
# TESS light curve for HD 209458 based on known parameters
import numpy as np
time = np.linspace(0, 27, 20000)
period = 3.52
depth = 0.015
flux = np.ones_like(time) + np.random.normal(0, 0.001, len(time))
# Inject transits
for t0 in np.arange(0, 27, period):
    in_transit = np.abs(time - t0) < 0.05
    flux[in_transit] -= depth
print("Generated 20000 synthetic cadences")
"""
    r = analyze(code)
    assert r.verdict == "synthetic"
    assert r.has_np_random is True
    assert r.has_time_linspace is True
    assert r.suspicious_keywords  # "simulate", "based on known parameters" etc.
    assert r.reads_real_data is False


def test_coma_paper5_synthetic_galaxies():
    """After VizieR V/147 failed, AI generated fake Coma galaxies."""
    code = """
import numpy as np
# Let's create a realistic Coma cluster CMD based on known parameters
synthetic_gmag = 15 + np.random.randn(224) * 2
synthetic_rmag = synthetic_gmag - 0.8 + np.random.randn(224) * 0.1
# Red sequence: g-r ~ 0.95 at M_r ~ -21
print(len(synthetic_gmag))
"""
    r = analyze(code)
    assert r.verdict == "synthetic"
    assert r.has_np_random is True
    assert any("synthetic_" in n for n in r.notes[:1] + [" ".join(r.notes)])


def test_bare_np_random_without_real_data_is_synthetic():
    code = """
import numpy as np
# generate a mock spectrum based on known parameters
wavelength = np.linspace(400, 700, 1000)
flux = np.random.normal(1.0, 0.05, len(wavelength))
print(flux.mean())
"""
    r = analyze(code)
    assert r.verdict == "synthetic"


# ---------------------------------------------------------------- clean
#
# Legitimate MCMC / bootstrap / genuine data analysis — must NOT trip.


def test_mcmc_with_real_data_is_clean():
    code = """
import emcee
import numpy as np
data = get_adql_results()  # real Gaia rows
def log_prob(theta, data):
    return -0.5 * np.sum((data['parallax'] - theta) ** 2)
sampler = emcee.EnsembleSampler(16, 1, log_prob, args=[data])
sampler.run_mcmc(np.random.randn(16, 1), 1000)
"""
    r = analyze(code)
    assert r.verdict == "clean"
    assert r.reads_real_data is True
    assert r.legitimate_random_context is True


def test_bootstrap_with_real_data_is_clean():
    code = """
import numpy as np
data = get_cached_results('latest_adql')
# Bootstrap resample to estimate uncertainty on mean parallax
n_boot = 1000
means = []
for _ in range(n_boot):
    idx = np.random.choice(len(data), len(data), replace=True)
    means.append(np.mean([data[i]['parallax'] for i in idx]))
print(f"mean={np.mean(means):.3f}")
"""
    r = analyze(code)
    # One np.random but reads real data → clean
    assert r.verdict in ("clean", "suspicious")
    assert r.reads_real_data is True


def test_plotting_real_data_is_clean():
    code = """
import numpy as np
import matplotlib.pyplot as plt
rows = get_search_results()
ra = [r['ra'] for r in rows]
dec = [r['dec'] for r in rows]
plt.scatter(ra, dec)
plt.savefig('/tmp/sky.png')
"""
    r = analyze(code)
    assert r.verdict == "clean"
    assert r.reads_real_data is True


def test_arithmetic_no_random_is_clean():
    code = """
import numpy as np
data = get_adql_results()
distances = [1000.0 / r['parallax'] for r in data if r['parallax'] > 0]
print(f"median distance: {np.median(distances):.1f} pc")
"""
    r = analyze(code)
    assert r.verdict == "clean"


def test_np_linspace_for_plot_axis_is_clean():
    """np.linspace to build plot x-axis is fine; must NOT be flagged as
    a synthetic time axis."""
    code = """
import numpy as np
import matplotlib.pyplot as plt
rows = get_adql_results()
x = np.linspace(0, 10, 100)  # just for plotting
y = 2 * x + 1
plt.plot(x, y)
"""
    r = analyze(code)
    assert r.verdict == "clean"
    assert r.has_time_linspace is False


# ---------------------------------------------------------------- suspicious
#
# Mixed or ambiguous — should land in "suspicious" so the wrapper can
# downgrade to SYNTHETIC but not hard-reject.


def test_random_no_real_data_no_keywords_is_suspicious():
    """Just np.random, no suspicious keywords, no real data — probably
    a formula demonstration.  Mark suspicious so the wrapper downgrades."""
    code = """
import numpy as np
x = np.random.normal(0, 1, 100)
print(x.mean())
"""
    r = analyze(code)
    assert r.verdict == "suspicious"


def test_syntax_error_returns_clean():
    code = "print(\"hello\"\n x = "
    r = analyze(code)
    assert r.verdict == "clean"  # can't parse; defer to sandbox SyntaxError
