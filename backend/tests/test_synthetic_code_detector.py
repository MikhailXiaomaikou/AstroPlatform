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


def test_constant_redshift_sequence_without_real_reader_is_synthetic():
    code = """
import pandas as pd
n = 100
df = pd.DataFrame({
    "z": [0.05] * n,
    "petroMag_r": [17.2] * n,
})
print(df["z"].mean())
"""
    r = analyze(code)
    assert r.verdict == "synthetic"
    assert r.has_constant_redshift_sequence is True
    assert r.reads_real_data is False


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
    assert r.verdict == "clean"
    assert r.reads_real_data is True
    assert r.actual_mcmc_usage is True


def test_real_data_reader_does_not_authenticate_unrelated_random_number():
    code = """
import numpy as np
rows = get_adql_results()
h0 = np.random.uniform(60, 80)
print(f"H0 = {h0}")
"""
    r = analyze(code)
    assert r.reads_real_data is True
    assert r.has_np_random is True
    assert r.actual_mcmc_usage is False
    assert r.verdict == "suspicious"


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


def test_download_and_clean_lightcurve_counts_as_real_reader():
    code = """
import astro
lc = astro.download_and_clean_lightcurve("HD 189733", mission="tess", sector=54)
time = lc["time"]
flux = lc["flux"]
print(len(time), len(flux))
"""
    r = analyze(code)
    assert r.verdict == "clean"
    assert r.reads_real_data is True


def test_real_data_with_model_grid_and_synthetic_comment_is_clean():
    code = """
import numpy as np
rows = get_search_results()
# Plot a synthetic/model comparison curve against the real observations.
x_model = np.linspace(0, 1, 1000)
y_model = 1 - 0.01 * np.exp(-((x_model - 0.5) ** 2) / 0.01)
print(len(rows), y_model.min())
"""
    r = analyze(code)
    assert r.verdict == "clean"
    assert r.reads_real_data is True


def test_catalog_summary_schematic_phase_curve_is_suspicious():
    """R0: Gaia vari_cepheid.pf + amplitude can support a catalog summary,
    but not a real phase-folded light curve.  A linspace phase axis plus an
    analytic magnitude curve without a time-series reader must be downgraded.
    """
    code = """
import numpy as np
import matplotlib.pyplot as plt
rows = get_adql_results()
period = rows[0]["pf"]
amp = rows[0]["peak_to_peak_g"]
mean_mag = rows[0]["int_average_g"]
phase = np.linspace(0, 2, 400)
mag = np.interp(phase % 1, [0.0, 0.5, 1.0], [mean_mag - amp/2, mean_mag + amp/2, mean_mag - amp/2])
plt.plot(phase, mag)
print(period)
"""
    r = analyze(code)
    assert r.reads_real_data is True
    assert r.has_schematic_phase_curve is True
    assert r.verdict == "suspicious"


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


# ── R5 O3: inert verdict ─────────────────────────────────────────────

def test_inert_simple_print_literal():
    """R5 O3: `print("hello world")` is a pure literal, not synthetic data."""
    r = analyze('print("hello world")')
    assert r.verdict == "inert"


def test_inert_print_arithmetic_literal():
    """R5 O3: `print(2+2)` literal arithmetic BinOp also counts as inert."""
    r = analyze('print(2+2)')
    assert r.verdict == "inert"


def test_inert_multiple_literal_prints():
    """R5 O3: smoke test typical form — two literal print lines."""
    code = 'print("hello world")\nprint(2+2)'
    r = analyze(code)
    assert r.verdict == "inert"


def test_not_inert_with_variable_read():
    """R5 O3: reading a variable (Name node) is not inert — already has side effects."""
    code = 'x = 5\nprint(x)'
    r = analyze(code)
    assert r.verdict != "inert"


def test_not_inert_with_import():
    """R5 O3: code with an import is not inert (could do harm in the future)."""
    code = 'import astropy\nprint("hi")'
    r = analyze(code)
    assert r.verdict != "inert"


def test_not_inert_with_function_call_other_than_print():
    """R5 O3: calling any function other than print is not inert."""
    code = 'len("hi")\nprint("done")'
    r = analyze(code)
    assert r.verdict != "inert"


def test_inert_empty_code_not_classified_as_inert():
    """R5 O3: empty code is not inert (no diagnostic value); preserves original clean behaviour."""
    r = analyze('')
    assert r.verdict != "inert"


def test_still_catches_np_random_after_inert_check():
    """R5 O3 regression: inert early-return must not suppress np.random synthetic detection."""
    code = 'import numpy as np\nx = np.random.normal(0, 1, 100)'
    r = analyze(code)
    assert r.verdict in ("synthetic", "suspicious")


# ── PART AD: RNG-evasion + date_range time axis ──────────────────────


def test_torch_randn_without_real_data_is_flagged():
    """PART AD: torch RNG must be caught like np.random (was an evasion gap)."""
    code = """
import torch
# fabricate a mock spectrum based on known parameters
flux = torch.randn(1000) * 0.05 + 1.0
print(float(flux.mean()))
"""
    r = analyze(code)
    assert r.has_np_random is True
    assert r.verdict in ("synthetic", "suspicious")


def test_getattr_dynamic_rng_is_flagged():
    """PART AD: getattr(np, 'random') dynamic dodge must be caught."""
    code = """
import numpy as np
# generate fake data based on known parameters
rng = getattr(np, "random")
x = rng.normal(0, 1, 500)
print(x.mean())
"""
    r = analyze(code)
    assert r.has_np_random is True
    assert r.verdict in ("synthetic", "suspicious")


def test_pd_date_range_time_axis_is_flagged():
    """PART AD: pandas date_range building a fabricated time axis was missed
    entirely by the linspace/arange-only detector."""
    code = """
import numpy as np
import pandas as pd
# simulate a realistic light curve based on known parameters
t = pd.date_range("2020-01-01", periods=5000, freq="min")
flux = np.random.normal(1.0, 0.01, len(t))
print(len(t))
"""
    r = analyze(code)
    assert r.has_time_linspace is True
    assert r.has_np_random is True
    assert r.verdict == "synthetic"


def test_torch_rng_with_real_data_not_hard_synthetic():
    """PART AD: torch RNG alongside a real reader must not be hard-synthetic
    (avoid误伤 a genuine bootstrap on real data)."""
    code = """
import torch
data = get_adql_results()
idx = torch.randint(0, len(data), (len(data),))
vals = [data[int(i)]['parallax'] for i in idx]
print(sum(vals) / len(vals))
"""
    r = analyze(code)
    assert r.reads_real_data is True
    assert r.verdict in ("clean", "suspicious")


def test_real_csv_with_emcee_fit_is_clean():
    """PART AD / 改动5: a user CSV read via pd.read_csv + a genuine emcee fit
    is the real-fit-mislabelled-as-synthetic case — must stay clean."""
    code = """
import pandas as pd
import numpy as np
import emcee
df = pd.read_csv("my_measurements.csv")
y = df["flux"].to_numpy()
def log_prob(theta):
    return -0.5 * np.sum((y - theta[0]) ** 2)
sampler = emcee.EnsembleSampler(16, 1, log_prob)
sampler.run_mcmc(np.random.randn(16, 1), 500)
print(sampler.get_chain().mean())
"""
    r = analyze(code)
    assert r.reads_real_data is True
    assert r.actual_mcmc_usage is True
    assert r.verdict == "clean"


def test_real_csv_then_np_random_fabrication_is_flagged():
    """PART AD / 改动5: reading a real CSV does NOT license fabricating extra
    rows with np.random — keyword + random + no sampler must be flagged."""
    code = """
import pandas as pd
import numpy as np
df = pd.read_csv("my_measurements.csv")
# generate synthetic data based on known parameters to fill the gaps
extra = np.random.normal(1.0, 0.1, 5000)
print(extra.mean())
"""
    r = analyze(code)
    assert r.reads_real_data is True
    assert r.verdict in ("suspicious", "synthetic")
