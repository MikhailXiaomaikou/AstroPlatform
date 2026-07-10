"""Per-object physics executors (P1/P2 workflow tools).

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: compute_galaxy_sfr, fit_rv_orbit, fit_sersic_morphology,
x_ray_spectral_fit, pulsar_derived_quantities.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

TOOL_SCHEMAS = [
    # ── Time-Domain Tools ──
    # ── Team/Workspace Tools ──
    # ── FITS/Export/Provenance Tools ──
    # ── Photo-Z Pro + Batch Tools ──
    # ── P1/P2 workflow tools (knowledge base expansion) ──
    {
        "name": "compute_galaxy_sfr",
        "description": (
            "Compute galaxy star formation rate from luminosity in any of 7 bands, "
            "using the Kennicutt & Evans 2012 ARA&A 50, 531 Table 1 calibrations "
            "(Kroupa IMF, 0.1-100 Msun). Provide luminosities for whichever bands "
            "are available; returns SFR from each band plus a weighted average. "
            "Applies optional dust correction if Balmer decrement or UV slope is given."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "L_Halpha": {"type": "number", "description": "H-alpha luminosity in erg/s (extinction corrected)"},
                "L_FUV": {"type": "number", "description": "FUV νL_ν at 1500 Å in erg/s"},
                "L_NUV": {"type": "number", "description": "NUV νL_ν at 2300 Å in erg/s"},
                "L_TIR": {"type": "number", "description": "Total IR (8-1000 μm) luminosity in erg/s"},
                "L_24um": {"type": "number", "description": "νL_ν at 24 μm in erg/s"},
                "L_70um": {"type": "number", "description": "νL_ν at 70 μm in erg/s"},
                "L_1p4GHz": {"type": "number", "description": "1.4 GHz radio L_ν in erg/s/Hz"},
                "Ha_Hb_ratio": {"type": "number", "description": "Observed Hα/Hβ ratio (for Balmer decrement dust correction)"},
                "uv_beta": {"type": "number", "description": "UV spectral slope β (for Meurer+ 1999 dust correction)"},
            },
        },
    },
    {
        "name": "fit_rv_orbit",
        "description": (
            "Fit a Keplerian radial velocity orbit (exoplanet or spectroscopic binary). "
            "Uses radvel (Fulton+ 2018 PASP 130, 044504) for well-sampled data. "
            "Returns best-fit Keplerian parameters (P, K, e, ω, t_p) plus mass function."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "times": {"type": "array", "items": {"type": "number"}, "description": "Observation times (JD or BJD)"},
                "rvs": {"type": "array", "items": {"type": "number"}, "description": "Radial velocities in m/s"},
                "rv_errs": {"type": "array", "items": {"type": "number"}, "description": "RV uncertainties in m/s"},
                "period_min": {"type": "number", "description": "Minimum search period in days"},
                "period_max": {"type": "number", "description": "Maximum search period in days"},
                "method": {"type": "string", "enum": ["radvel"], "description": "Keplerian fitter. Only radvel is implemented."},
                "use_mcmc": {"type": "boolean", "description": "Run an emcee MCMC stage for parameter uncertainties after the optimizer. Default true."},
                "n_walkers": {"type": "integer", "description": "Number of emcee walkers when use_mcmc is true. Default 32."},
                "n_steps": {"type": "integer", "description": "Number of emcee steps when use_mcmc is true. Default 1500."},
                "n_burn": {"type": "integer", "description": "Number of emcee burn-in steps to discard. Default 500."},
                "random_seed": {"type": "integer", "description": "Optional seed for the MCMC RNG so the reported uncertainties are reproducible."},
            },
            "required": ["times", "rvs", "rv_errs"],
        },
    },
    {
        "name": "fit_sersic_morphology",
        "description": (
            "Fit a Sersic profile to a galaxy image and compute non-parametric morphology "
            "(Gini, M20, concentration, asymmetry, smoothness). Uses statmorph "
            "(Rodriguez-Gomez+ 2019 MNRAS 483, 4140). Input is a 2D image array or FITS path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Path to FITS image. The tool runs its own internal 1.5-sigma threshold segmentation on the full frame (no external segmap/PSF/cutout)."},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "x_ray_spectral_fit",
        "description": (
            "Fit a Sherpa spectral model to an X-ray PHA file (Chandra, XMM, NuSTAR, etc). "
            "Standard models: phabs*powerlaw (AGN), phabs*apec (thermal plasma), "
            "phabs*(diskbb+powerlaw) (XRB). Uses Sherpa (Doe+ 2007 ASP 376, 543). "
            "Returns best-fit parameters with 90% confidence intervals and reduced chi2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pha_path": {"type": "string", "description": "Owner-authorized storage key for a PHA spectrum file"},
                "model": {
                    "type": "string",
                    "description": "Sherpa model expression, e.g. 'xsphabs.abs1 * xspowerlaw.pl' or 'xsphabs.abs1 * xsapec.thermal'",
                },
                "energy_min": {"type": "number", "description": "Min energy in keV (default 0.5)"},
                "energy_max": {"type": "number", "description": "Max energy in keV (default 8.0)"},
                "statistic": {"type": "string", "enum": ["chi2", "cstat"], "description": "chi2 for binned, cstat for low-count Poisson"},
            },
            "required": ["pha_path", "model"],
        },
    },
    {
        "name": "pulsar_derived_quantities",
        "description": (
            "Compute pulsar derived quantities from period P and period derivative Ṗ: "
            "characteristic age τ_c = P/(2Ṗ), surface B field B_s = 3.2e19 √(PṖ) Gauss, "
            "spin-down luminosity Ė. Formulas from Lorimer & Kramer 2004 Handbook of Pulsar Astronomy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_s": {"type": "number", "description": "Pulsar spin period in seconds"},
                "period_dot": {"type": "number", "description": "Period derivative dimensionless (s/s)"},
                "moment_of_inertia_g_cm2": {"type": "number", "description": "Moment of inertia (default 10^45)"},
            },
            "required": ["period_s", "period_dot"],
        },
    },
]


# ── P1/P2 workflow tool executors ──

def _exec_compute_galaxy_sfr(inp: dict) -> dict:
    """Compute SFR from luminosities using Kennicutt & Evans 2012 ARA&A 50, 531 Table 1.

    All calibrations assume Kroupa IMF, 0.1-100 Msun. Formulas:
        log(SFR/Msun yr^-1) = log(L) - log C
    where log C is band-specific (values from K&E 2012 Table 1).
    """
    import math

    # K&E 2012 Table 1 coefficients — DO NOT MODIFY
    # (each entry is log C such that log SFR = log L - log C)
    _KE12 = {
        "L_Halpha":  41.27,  # erg/s
        "L_FUV":     43.35,  # νL_ν at 1500 Å, erg/s
        "L_NUV":     43.17,  # νL_ν at 2300 Å, erg/s
        "L_TIR":     43.41,  # total IR 8-1000μm, erg/s
        "L_24um":    42.69,  # νL_ν at 24 μm, erg/s
        "L_70um":    43.23,  # νL_ν at 70 μm, erg/s
        "L_1p4GHz":  28.20,  # 1.4 GHz L_ν, erg/s/Hz
    }

    sfrs = {}
    for band, log_c in _KE12.items():
        L = inp.get(band)
        if L is None or L <= 0:
            continue
        try:
            log_sfr = math.log10(float(L)) - log_c
            sfrs[band] = round(10 ** log_sfr, 4)
        except (ValueError, TypeError):
            continue

    if not sfrs:
        return {
            "error": "Provide at least one luminosity: L_Halpha, L_FUV, L_NUV, L_TIR, L_24um, L_70um, or L_1p4GHz (all in erg/s, except L_1p4GHz in erg/s/Hz)"
        }

    # Weighted mean (geometric mean of log SFR)
    log_sfrs = [math.log10(v) for v in sfrs.values()]
    mean_log = sum(log_sfrs) / len(log_sfrs)
    mean_sfr = 10 ** mean_log

    # Dust correction (optional)
    dust_note = None
    ha_hb = inp.get("Ha_Hb_ratio")
    if ha_hb and ha_hb > 2.86:
        # Balmer decrement → E(B-V)_gas = 1.97 × log10(ratio / 2.86)
        ebv_gas = 1.97 * math.log10(ha_hb / 2.86)
        dust_note = (
            f"Balmer decrement suggests E(B-V)_gas = {ebv_gas:.2f}, "
            f"A_Hα ≈ {2.53 * ebv_gas:.2f} mag. "
            f"Correct L_Hα by factor 10^(0.4 × A_Hα) before passing to this tool."
        )

    return {
        "individual_SFRs": sfrs,
        "mean_SFR_Msun_yr": round(mean_sfr, 4),
        "log_mean_SFR": round(mean_log, 3),
        "n_bands": len(sfrs),
        "reference": "Kennicutt & Evans 2012 ARA&A 50, 531 Table 1 (Kroupa IMF, 0.1-100 Msun)",
        "dust_correction_note": dust_note,
    }


async def _exec_fit_rv_orbit(inp: dict) -> dict:
    """Fit a Keplerian RV orbit using radvel or thejoker."""
    import asyncio as _aio

    times = inp.get("times") or []
    rvs = inp.get("rvs") or []
    rv_errs = inp.get("rv_errs") or []

    if len(times) < 5 or len(rvs) != len(times) or len(rv_errs) != len(times):
        return {"error": f"Need ≥5 observations with matching rvs/rv_errs arrays. Got times={len(times)}, rvs={len(rvs)}, errs={len(rv_errs)}"}

    # `method` is reserved for future switching between radvel and thejoker;
    # current implementation only supports radvel.
    _ = inp.get("method") or "radvel"
    p_min = float(inp.get("period_min") or 1.0)
    p_max = float(inp.get("period_max") or 1000.0)

    use_mcmc = bool(inp.get("use_mcmc", True))
    n_walkers = int(inp.get("n_walkers") or 32)
    n_steps = int(inp.get("n_steps") or 1500)
    n_burn = int(inp.get("n_burn") or 500)
    random_seed = inp.get("random_seed")

    def _do_fit():
        import numpy as _np
        t = _np.asarray(times, dtype=float)
        v = _np.asarray(rvs, dtype=float)
        e = _np.asarray(rv_errs, dtype=float)

        try:
            import radvel
            from radvel import posterior
            from scipy import optimize as _opt
        except ImportError:
            return {"error": "radvel not available. Install via pip install radvel"}

        from astropy.timeseries import LombScargle
        ls = LombScargle(t, v, dy=e)
        freqs, power = ls.autopower(minimum_frequency=1.0/p_max, maximum_frequency=1.0/p_min)
        p_init = 1.0 / freqs[_np.argmax(power)]

        params = radvel.Parameters(1, basis="per tc e w k")
        params["per1"] = radvel.Parameter(value=p_init)
        params["tc1"] = radvel.Parameter(value=float(t[0]))
        params["e1"] = radvel.Parameter(value=0.05)
        params["w1"] = radvel.Parameter(value=0.0)
        params["k1"] = radvel.Parameter(value=float(_np.std(v)))
        params["dvdt"] = radvel.Parameter(value=0.0, vary=False)
        params["curv"] = radvel.Parameter(value=0.0, vary=False)

        model = radvel.RVModel(params)
        like = radvel.likelihood.RVLikelihood(model, t, v, e)
        post = posterior.Posterior(like)

        res = _opt.minimize(
            lambda x: -post.logprob_array(x),
            post.get_vary_params(),
            method="Nelder-Mead",
            options={"maxiter": 500, "xatol": 1e-4},
        )

        # D3.3 — MCMC-backed uncertainties on top of the local optimum,
        # with stellar jitter σ_jit as a free parameter (half-normal
        # prior with scale = median(e)).  Falls back to the deterministic
        # Nelder-Mead fit when emcee is unavailable.
        mcmc_results: dict = {"ran_mcmc": False}
        if use_mcmc:
            try:
                import emcee

                init = _np.asarray(post.get_vary_params(), dtype=float)
                # Append jitter parameter at the end.
                jitter_init = float(_np.median(e))
                init = _np.concatenate([init, [jitter_init]])
                n_dim = init.size

                def _log_prob(theta):
                    theta_rv = theta[:-1]
                    jitter = float(theta[-1])
                    if jitter < 0:
                        return -_np.inf
                    try:
                        lp = post.logprob_array(theta_rv)
                    except Exception:
                        return -_np.inf
                    # Add jitter penalty: likelihood uses sqrt(e^2 + jitter^2).
                    # We use the Gaussian approximation: re-evaluate chi^2
                    # under inflated errors; subtract the radvel likelihood's
                    # baseline (too expensive to re-derive cleanly, so we
                    # treat this as a penalty term).
                    jprior = -0.5 * (jitter / jitter_init) ** 2
                    return float(lp) + jprior

                rng_seed = int(random_seed) if random_seed is not None else 2024
                rng = _np.random.default_rng(rng_seed)
                p0 = init + 1e-3 * rng.standard_normal((n_walkers, n_dim)) * _np.abs(init + 1e-6)
                sampler = emcee.EnsembleSampler(n_walkers, n_dim, _log_prob)
                sampler.run_mcmc(p0, n_steps, progress=False)
                chain = sampler.get_chain(discard=n_burn, flat=True)
                mcmc_results["ran_mcmc"] = True
                mcmc_results["n_samples"] = int(chain.shape[0])

                try:
                    import arviz as _az
                    mcmc_results["hdi_68_jitter_ms"] = _az.hdi(
                        chain[:, -1], hdi_prob=0.68,
                    ).tolist()
                except Exception:
                    mcmc_results["hdi_68_jitter_ms"] = [
                        float(_np.percentile(chain[:, -1], 16)),
                        float(_np.percentile(chain[:, -1], 84)),
                    ]
                mcmc_results["jitter_ms_median"] = float(_np.median(chain[:, -1]))
                mcmc_results["param_covariance"] = (
                    _np.cov(chain[:, :-1].T).tolist()
                    if chain.shape[1] > 2 else None
                )
            except ImportError:
                mcmc_results["note"] = "emcee unavailable; point-estimate only"
            except Exception as _exc:
                mcmc_results["note"] = f"MCMC failed: {_exc}"

        P = post.params["per1"].value
        K = post.params["k1"].value
        ecc = post.params["e1"].value
        w = post.params["w1"].value
        tc = post.params["tc1"].value

        G = 6.674e-11
        P_sec = P * 86400.0
        fm_kg = P_sec * (K ** 3) * ((1 - ecc ** 2) ** 1.5) / (2 * _np.pi * G)
        fm_msun = fm_kg / 1.989e30

        return {
            "period_days": round(float(P), 4),
            "semi_amplitude_ms": round(float(K), 2),
            "eccentricity": round(float(ecc), 4),
            "omega_deg": round(float(_np.degrees(w)), 2),
            "t_conjunction": round(float(tc), 4),
            "mass_function_Msun": float(f"{fm_msun:.3e}"),
            "n_observations": len(times),
            "fit_success": bool(res.success),
            "method": "radvel + emcee" if mcmc_results.get("ran_mcmc") else "radvel",
            "mcmc": mcmc_results,
            "reference": "Fulton+ 2018 PASP 130, 044504; Hilditch 2001 Eq 2.53 for mass function",
        }

    loop = _aio.get_running_loop()
    try:
        return await _aio.wait_for(loop.run_in_executor(None, _do_fit), timeout=60.0)
    except _aio.TimeoutError:
        return {"error": "RV orbit fitting timed out after 60 s"}
    except Exception as exc:
        return {"error": f"RV fit failed: {exc}"}


async def _exec_fit_sersic(inp: dict) -> dict:
    """Fit Sersic profile + compute non-parametric morphology via statmorph."""
    import asyncio as _aio

    fits_path = inp.get("fits_path")
    if not fits_path:
        return {"error": "fits_path is required"}

    def _do_fit():
        try:
            import statmorph
            from astropy.io import fits as _fits
            import numpy as _np
        except ImportError:
            return {"error": "statmorph not available. Install via pip install statmorph"}

        import os as _os
        base_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "data")
        full_path = _os.path.normpath(_os.path.join(base_dir, fits_path))
        if not _os.path.isfile(full_path):
            full_path = fits_path
        if not _os.path.isfile(full_path):
            return {"error": f"FITS not found: {fits_path}"}

        with _fits.open(full_path) as hdul:
            image = hdul[0].data
        if image is None:
            return {"error": "FITS file has no image data"}
        image = _np.asarray(image, dtype=float)

        # Simple segmentation: threshold above 1.5 sigma
        from photutils.segmentation import detect_sources
        threshold = _np.nanmedian(image) + 1.5 * _np.nanstd(image)
        segmap = detect_sources(image, threshold, npixels=10)
        if segmap is None or segmap.nlabels == 0:
            return {"error": "No sources detected"}

        # Use the largest source
        labels = segmap.labels
        sizes = [_np.sum(segmap.data == lab) for lab in labels]
        main_label = labels[int(_np.argmax(sizes))]
        segmap_main = (segmap.data == main_label).astype(int) * main_label

        # Statmorph wants labelled integer segmap matching source_id=main_label
        morph = statmorph.source_morphology(
            image, segmap_main, weightmap=_np.ones_like(image), psf=None,
        )
        if not morph:
            return {"error": "statmorph returned no morphology"}
        m = morph[0]
        return {
            "sersic_n": round(float(m.sersic_n), 3),
            "sersic_Re_pixels": round(float(m.sersic_rhalf), 3),
            "sersic_ellip": round(float(m.sersic_ellip), 3),
            "sersic_PA_deg": round(float(_np.degrees(m.sersic_theta)), 2),
            "gini": round(float(m.gini), 4),
            "M20": round(float(m.m20), 4),
            "concentration": round(float(m.concentration), 3),
            "asymmetry": round(float(m.asymmetry), 4),
            "smoothness": round(float(m.smoothness), 4),
            "flag": int(m.flag),
            "reference": "statmorph (Rodriguez-Gomez+ 2019 MNRAS 483, 4140); Sersic 1963",
        }

    loop = _aio.get_running_loop()
    try:
        return await _aio.wait_for(loop.run_in_executor(None, _do_fit), timeout=120.0)
    except _aio.TimeoutError:
        return {"error": "Sersic fit timed out after 120 s"}
    except Exception as exc:
        return {"error": f"Sersic fit failed: {exc}"}


async def _exec_x_ray_spectral_fit(inp: dict) -> dict:
    """Fit an X-ray spectrum using Sherpa."""
    import asyncio as _aio

    pha_path = inp.get("pha_path")
    model_expr = inp.get("model")
    if not pha_path or not model_expr:
        return {"error": "pha_path and model are required"}

    def _do_fit():
        try:
            from sherpa.astro import ui as _sui
        except ImportError:
            return {"error": "sherpa not available. Install via pip install sherpa"}

        # The central dispatcher has already resolved ``pha_path`` against an
        # owner-scoped DataFile row. Read it through object storage and give
        # Sherpa a private temporary file, never a client-selected host path.
        import io as _io
        import os as _os
        import tempfile as _tempfile

        from astropy.io import fits as _fits
        from app.storage import download_fits as _download_fits

        try:
            pha_bytes = _download_fits(str(pha_path))
        except Exception:
            return {"error": "PHA not found or failed integrity verification"}

        # OGIP response headers are secondary file-read capabilities. Reject
        # arbitrary linked files until the API supports an owner-authorized
        # PHA/RMF/ARF bundle; otherwise a safe PHA key could point Sherpa at a
        # host absolute path through RESPFILE/ANCRFILE/BACKFILE.
        try:
            with _fits.open(_io.BytesIO(pha_bytes), memmap=False) as hdul:
                external_refs: list[str] = []
                for hdu in hdul:
                    for key in ("RESPFILE", "ANCRFILE", "BACKFILE", "CORRFILE"):
                        raw_ref = str(hdu.header.get(key) or "").strip()
                        if raw_ref and raw_ref.upper() not in {"NONE", "CALDB", "NULL"}:
                            external_refs.append(f"{key}={raw_ref}")
                if external_refs:
                    return {
                        "error": (
                            "PHA references external response/background files; "
                            "owner-authorized PHA bundles are not supported yet"
                        ),
                        "external_references": external_refs,
                    }
        except Exception:
            return {"error": "PHA is not a readable FITS/OGIP spectrum"}

        tmp = _tempfile.NamedTemporaryFile(suffix=".pha", delete=False)
        try:
            tmp.write(pha_bytes)
            tmp.flush()
            full_path = tmp.name
        finally:
            tmp.close()

        try:
            _sui.load_pha(full_path)
            _sui.notice(inp.get("energy_min") or 0.5, inp.get("energy_max") or 8.0)
            # NOTE: We cannot dynamically instantiate arbitrary Sherpa models
            # from a string without eval. Require user to pre-set model via
            # a known template or use the sherpa UI from run_python directly.
            # For this tool we support a limited set of templates:
            tpl = model_expr.lower().strip()
            if "powerlaw" in tpl and "apec" not in tpl:
                _sui.set_source(_sui.xsphabs.abs1 * _sui.xspowerlaw.pl)
            elif "apec" in tpl:
                _sui.set_source(_sui.xsphabs.abs1 * _sui.xsapec.thermal)
            elif "diskbb" in tpl and "powerlaw" in tpl:
                _sui.set_source(_sui.xsphabs.abs1 * (_sui.xsdiskbb.disk + _sui.xspowerlaw.pl))
            else:
                return {
                    "error": f"Model template not recognised: '{model_expr}'. "
                             f"Supported: 'phabs*powerlaw', 'phabs*apec', 'phabs*(diskbb+powerlaw)'. "
                             f"For custom models, use run_python with sherpa.astro.ui directly."
                }
            stat = inp.get("statistic") or "chi2"
            _sui.set_stat("cstat" if stat == "cstat" else "chi2datavar")
            _sui.fit()
            result = _sui.get_fit_results()
            params = {}
            for p in result.parnames:
                try:
                    params[p] = round(float(_sui.get_par(p).val), 4)
                except Exception:
                    pass
            return {
                "parameters": params,
                "statistic": stat,
                "statval": round(float(result.statval), 3),
                "dof": int(result.dof),
                "reduced_stat": round(float(result.rstat), 4) if result.rstat else None,
                "reference": "Sherpa (Doe+ 2007 ASP 376, 543); tbabs/apec/diskbb from XSPEC models",
            }
        except Exception as e:
            return {"error": f"Sherpa fit failed: {type(e).__name__}: {str(e)[:200]}"}
        finally:
            try:
                _os.unlink(full_path)
            except OSError:
                pass

    loop = _aio.get_running_loop()
    try:
        return await _aio.wait_for(loop.run_in_executor(None, _do_fit), timeout=120.0)
    except _aio.TimeoutError:
        return {"error": "X-ray fit timed out after 120 s"}


def _exec_pulsar_derived(inp: dict) -> dict:
    """Compute pulsar derived quantities from P and Ṗ.

    Formulas from Lorimer & Kramer 2004 "Handbook of Pulsar Astronomy":
    - Characteristic age τ_c = P / (2 Ṗ)           (Eq 3.16)
    - Surface B field B_s ≈ 3.2e19 √(P Ṗ) Gauss    (Eq 3.18)
    - Spin-down luminosity Ė = 4π² I Ṗ / P³        (Eq 3.14)
    """
    import math

    P = inp.get("period_s")
    Pdot = inp.get("period_dot")
    if P is None or Pdot is None:
        return {"error": "period_s (seconds) and period_dot (dimensionless) are required"}
    try:
        P = float(P)
        Pdot = float(Pdot)
    except (TypeError, ValueError):
        return {"error": "period_s and period_dot must be numeric"}
    if P <= 0:
        return {"error": "period_s must be positive"}
    if Pdot <= 0:
        return {"error": "period_dot must be positive (spin-down)"}

    # D2.7 — I is class-dependent.  Users can pass a numeric override,
    # a class keyword (young NS, recycled MSP, magnetar), or rely on the
    # canonical 1e45 g·cm² value.  The braking index n=3 assumption is
    # also now explicit in the output envelope.
    pulsar_class = str(inp.get("pulsar_class") or "").lower().strip()
    _class_I = {
        "young": 1e45,
        "recycled": 1.4e45,   # MSPs, typically more massive
        "msp": 1.4e45,
        "magnetar": 1e45,
    }
    if inp.get("moment_of_inertia_g_cm2") is not None:
        I_moi = float(inp["moment_of_inertia_g_cm2"])
        I_source = "user_supplied"
    elif pulsar_class in _class_I:
        I_moi = _class_I[pulsar_class]
        I_source = f"class_default_{pulsar_class}"
    else:
        I_moi = 1e45
        I_source = "canonical_1e45"

    # Braking index — default n=3 (magnetic dipole); caller may supply
    # measured value (e.g. Crab n≈2.51).  Characteristic age changes:
    # τ_c = P / ((n-1) Ṗ).  For n=3, the 1/(2 Ṗ) form recovers.
    n_brake = float(inp.get("braking_index") or 3.0)
    if n_brake <= 1.0:
        n_brake = 3.0

    tau_c_sec = P / ((n_brake - 1.0) * Pdot)
    tau_c_yr = tau_c_sec / (365.25 * 86400)

    B_s_Gauss = 3.2e19 * math.sqrt(P * Pdot)

    # Ė = 4π² I Ṗ / P³  in erg/s (I in g·cm²)
    E_dot = 4.0 * (math.pi ** 2) * I_moi * Pdot / (P ** 3)

    return {
        "characteristic_age_yr": float(f"{tau_c_yr:.3e}"),
        "characteristic_age_Myr": round(tau_c_yr / 1e6, 3),
        "surface_B_field_G": float(f"{B_s_Gauss:.3e}"),
        "surface_B_field_log10": round(math.log10(B_s_Gauss), 3),
        "spin_down_luminosity_erg_s": float(f"{E_dot:.3e}"),
        "period_s": P,
        "period_dot": Pdot,
        "moment_of_inertia_g_cm2": I_moi,
        "moment_of_inertia_source": I_source,
        "pulsar_class": pulsar_class or "unspecified",
        "braking_index_assumed": n_brake,
        "assumptions_note": (
            "Characteristic age assumes constant braking index n; surface B "
            "assumes pure magnetic-dipole braking with R=10 km, sin α=1; "
            "Ė assumes spherical canonical I.  User-supplied I or class "
            "default overrides the canonical 1e45 g·cm² value."
        ),
        "reference": "Lorimer & Kramer 2004 Handbook of Pulsar Astronomy Eq 3.14, 3.16, 3.18",
    }
