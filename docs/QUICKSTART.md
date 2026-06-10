# Standard Astro Quick Start Guide

Welcome to Standard Astro, an AI-native observational-cosmology research platform. The primary surface is the **AI Assistant** (Chat). This guide walks you through common tasks in under 20 minutes.

## 1. Use the AI Assistant (5 min)

1. Open **AI Assistant** from the navigation bar (or press `Cmd+K` then select "AI Assistant")
2. Try these example prompts:

| What you type | What the AI does |
|---------------|-----------------|
| "Search for the 10 brightest quasars with z > 2" | Queries SIMBAD with appropriate ADQL filters |
| "Plot an HR diagram of stars within 50 pc" | Searches Gaia, plots color-magnitude diagram |
| "Analyze the spectrum of Vega" | Identifies spectral lines, measures equivalent widths |
| "Find recent papers about Type Ia supernovae" | Searches NASA ADS, returns abstracts and citations |
| "List the cosmology datasets available for likelihood building" | Runs `list_cosmology_datasets` over the dataset registry |
| "Build a DESI DR2 BAO + BBN likelihood and report the constraints" | Runs `build_cosmology_likelihood` then `fit_cosmology_mcmc` |
| "Fit the [CII] luminosity vs FWHM relation from these cited tables" | Runs `extract_literature_tables` then `fit_line_lfr` |

The AI has access to a global tool catalog of **77 tools** covering search, literature, statistics, and observational-cosmology likelihood building. The active research module (`ASTRO_RESEARCH_FOCUS`, which fails closed to `cosmology`) narrows the per-turn surface — currently **57 tools** are visible under cosmology focus, as declared in `backend/app/prompts/modules/cosmology/manifest.yaml`. The AI selects the right tool from the visible set based on your request.

When a tool result includes provenance, the chat card shows a **Data Sources** panel with `archive_version`, bibcodes, and source authority. The **Copy Acknowledgement** button assembles acknowledgement text from the conversation's provenance. If the AI tries a gated source such as SDSS or Chandra, the card appears as **Maintenance** rather than a generic error and suggests the active alternatives.

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
