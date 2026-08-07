# Standard Astro Quick Start Guide

Welcome to Standard Astro, an AI-native observational-cosmology research platform. The primary surface is the **AI Assistant** (Chat). This guide walks you through common tasks in under 20 minutes.

## 1. Use the AI Assistant (5 min)

1. Open **AI Assistant** from the navigation bar (or press `Cmd+K` then select "AI Assistant")
2. Try these example prompts:

| What you type | What the AI does |
|---------------|-----------------|
| "List the cosmology datasets available for likelihood building" | Runs `list_cosmology_datasets` over the dataset registry |
| "Build a DESI DR2 BAO + BBN likelihood and report the constraints" | Runs `build_cosmology_likelihood` then `fit_cosmology_mcmc` |
| "Compute the Planck 2018 theory CMB TT power spectrum" | Runs `compute_theory_cmb_spectrum` (in-process CAMB) |
| "Search for the 10 brightest quasars with z > 2" | Queries SIMBAD with appropriate ADQL filters |
| "Plot an HR diagram of stars within 50 pc" | Searches Gaia, plots color-magnitude diagram |
| "Find recent papers about Type Ia supernovae" | Searches NASA ADS — **requires `ADS_API_KEY`**, see note below |
| "Fit the [CII] luminosity vs FWHM relation from these cited tables" | Runs `extract_literature_tables` then `fit_line_lfr` |
| "From DESI DR2 Table 4, compute `(17.351±0.177)/(19.455±0.330)` with `ρ=-0.404`" | When the v0.2 flag is enabled, routes to the controlled scalar verifier and returns a source/uncertainty receipt |

The cosmology examples come first because that is the platform's focus, and they run on a fresh deployment with no extra setup beyond a model-provider key: the datasets are vendored and pinned in the repo, and CAMB runs in-process.

> **ADS dependency note:** the literature-search example needs the `ADS_API_KEY` environment variable on the backend. It is **not** declared in `render.yaml`, so a stock Render deploy does not have it — set it in the Render dashboard (or your backend environment). Without it the tool fails closed: you get an explicit "ADS_API_KEY is not configured" error, not fabricated results.

> Spectrum-analysis tools (`analyze_spectrum`) also exist, but they take a FITS file path on the backend filesystem — a fresh deployment has no FITS files, so that is not a first-run example.

*(Example verification status, as of 2026-08-07: every tool named above was checked to be registered and visible under cosmology focus via `build_allowed_tools("cosmology")`. The exact prompts still need to be re-run end-to-end after the latest source-matching hardening; actual routing also depends on the model provider you configure.)*

The AI has access to a global tool catalog of **81 tools** (live import,
2026-08-07) covering search, literature, statistics, observational-cosmology
likelihoods, and scalar verification. The active research module
(`ASTRO_RESEARCH_FOCUS`, which fails closed to `cosmology`) has **61 tools** in
its manifest allowlist. Because v0.2 is off by default, the wire-visible surface
is normally **60 tools**; enabling the flag exposes
`verify_scalar_derivation` as the 61st. The AI selects from that filtered set
based on your request.

When a tool result includes provenance, the chat card shows a **Data Sources** panel with `archive_version`, bibcodes, and source authority. The **Copy Acknowledgement** button assembles acknowledgement text from the conversation's provenance. If the AI tries a gated source such as SDSS or Chandra, the card appears as **Maintenance** rather than a generic error and suggests the active alternatives.

### Optional: v0.2 scalar verification

This path is off by default. For a local evaluation session, set
`LIGHTWEIGHT_VERIFICATION_ENABLED=1` before starting the backend. A qualifying
paper-table question can then produce a Scalar Verification Receipt that keeps
two claims separate:

- `calculation_status` says whether the controlled arithmetic succeeded;
- `source_status` says whether the cited values matched the requested source
  and locator exactly.

The receipt also records the formula, covariance assumption, source evidence,
boundary statement, response disposition, and a canonical SHA-256 hash. A
correct calculation does not by itself prove that the paper reported the
inputs. Missing cross-covariance normally yields a useful `limited` answer,
not a fabricated full verification. This path does not run arbitrary formulas,
likelihoods, samplers, or posterior reconstructions.

Literature-only searches support context and citations, not measurement claims. For relation fits such as `[CII]` luminosity versus FWHM, the assistant must extract cited literature tables and run the dedicated line-relation fit before it can report slope, intercept, scatter, or correlation values. If the current tools do not return usable measurement rows, the assistant should say that directly instead of filling gaps from memory.

**After each analysis**, the AI suggests 2-3 next steps. You can also use the **Next Steps panel** below the chat for quick actions: generate a paper draft, export a notebook, or run sensitivity analysis.

## 2. Export and Publish (2 min)

After completing an analysis in the AI Assistant, you have several export options:

**From the AI Assistant:**
- Ask: "Export this session as a Jupyter notebook"
- Ask: "Generate a paper draft in AASTeX format"
- Use the **Next Steps panel** buttons

A research-report export bundles, where applicable:
- Jupyter Notebook (reproducible code)
- CSV data tables
- VOTable (VO-standard format)
- Provenance record (data lineage)
- Pinned requirements.txt (for reproducibility)

> **Note:** The standalone Data Browser, Pipeline Studio, and Workspace pages were removed in the M3 trim (2026-05-18). The pipeline DAG engine still runs backend-side, but the current product surface is the AI Assistant (Chat). Data search, FITS handling, pipeline runs, and exports are all driven from the chat by asking the AI.

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl + K` | Open Command Palette |
| `Escape` | Close dialogs |

## Getting Help

- Click the **(?)** icon in the navigation bar for the built-in Help system
- The Help panel includes tutorials, a glossary of astronomy terms, and FAQ
- First-time users see an interactive onboarding tour

## Setting Up API Keys

For full functionality, configure these in **Account** settings:
- **Anthropic/OpenAI/DeepSeek API key** -- powers the AI assistant
- Keys are encrypted and stored securely (Fernet encryption)

## Need More?

- **API Documentation**: Visit `{your-backend-url}/docs` for the interactive Swagger UI
- **Architecture**: See [ARCHITECTURE.md](../ARCHITECTURE.md) for system design details
- **Deployment**: See [DEPLOYMENT.md](../DEPLOYMENT.md) for self-hosting instructions
