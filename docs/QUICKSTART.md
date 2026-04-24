# Standard Astro Quick Start Guide

Welcome to Standard Astro, an AI-native astronomy research platform. This guide walks you through 5 common tasks in under 20 minutes.

## 1. Search for an Astronomical Object (2 min)

1. Open **Data Browser** from the navigation bar
2. Type an object name in the search box: `M31`, `NGC 1068`, `Sirius`, or `Crab Nebula`
3. Select which active databases to query. During the provenance-v2 rollout the active sources are SIMBAD, Gaia DR3, VizieR, NED, and 2MASS; other source chips are shown as under maintenance until their `archive_version` provenance is upgraded.
4. Click **Search**
5. Results appear in a merged table with coordinates, magnitudes, redshifts, and object types

**Quick actions** appear above the results table:
- **Quick Plot** -- instantly visualize the results (HR diagram, sky distribution)
- **Dossier** -- get a comprehensive report on the top result
- **Cross-match** -- match with another catalog

**Batch mode**: Toggle "Batch Mode" to search multiple targets at once (one per line).

## 2. Use the AI Assistant (5 min)

1. Open **AI Assistant** from the navigation bar (or press `Cmd+K` then select "AI Assistant")
2. Try these example prompts:

| What you type | What the AI does |
|---------------|-----------------|
| "Search for the 10 brightest quasars with z > 2" | Queries SIMBAD with appropriate ADQL filters |
| "Plot an HR diagram of stars within 50 pc" | Searches Gaia, plots color-magnitude diagram |
| "Analyze the spectrum of Vega" | Identifies spectral lines, measures equivalent widths |
| "Find recent papers about Type Ia supernovae" | Searches NASA ADS, returns abstracts and citations |
| "Estimate the photo-z for this galaxy: g=22.1, r=21.5, i=20.8" | Runs 30-template SED fitting with dust and IGM |
| "What transients were discovered this week?" | Queries TNS/ZTF for recent alerts |

The AI has access to **57 tools** covering search, spectroscopy, photometry, time-domain analysis, image processing, statistics, literature, and more. It automatically selects the right tool based on your request.

When a tool result includes provenance, the chat card shows a **Data Sources** panel with `archive_version`, bibcodes, and source authority. The **Copy Acknowledgement** button assembles acknowledgement text from the conversation's provenance. If the AI tries a gated source such as SDSS or Chandra, the card appears as **Maintenance** rather than a generic error and suggests the active alternatives.

**After each analysis**, the AI suggests 2-3 next steps. You can also use the **Next Steps panel** below the chat for quick actions: generate a paper draft, export a notebook, or run sensitivity analysis.

## 3. Build a Pipeline (5 min)

1. Open **Pipeline Studio** from the navigation bar
2. Choose a **Quick Template** from the dropdown:

| Template | Workflow |
|----------|----------|
| Spectrum Analysis | LoadData -> Denoise -> SpectralFit -> RedshiftEstimate -> Plot |
| CCD Photometry | BiasSubtract -> DarkCorrect -> FlatField -> CosmicRayReject -> SourceExtract -> PSFPhotometry -> Plot |
| Transient Triage | LoadData -> Denoise -> TimeSeriesAnalysis -> Plot |
| Photo-z Estimation | QueryData -> CrossMatch -> PhotoZPro -> Plot |
| Transit Search | LoadData -> GPDetrend -> TransitFit -> Plot |

3. Or build from scratch: drag nodes from the **Node Palette** on the left
4. Connect nodes by dragging from output (right) to input (left) handles
5. Click each node to configure parameters
6. Click **Run** to execute the pipeline
7. Results appear in specialized viewers (spectrum viewer, light curve viewer, MCMC diagnostics, etc.)

The platform has **35 node types** covering data I/O, CCD reduction, spectroscopy, photometry, time-domain, image processing, statistical inference, and visualization.

## 4. Upload and Analyze FITS Files (3 min)

1. Open **Workspace** from the navigation bar
2. Open the **FITS Manager** tab
3. Drag and drop `.fits` files into the upload area (or click to browse)
4. The platform **automatically detects** the file type:
   - **Image** -> suggests CCD reduction + photometry
   - **Spectrum** -> suggests spectral analysis + line fitting
   - **Light curve** -> suggests time-domain analysis
   - **Catalog/Table** -> suggests cross-matching
5. Click **Auto-analyze** to send the file to the AI assistant for instant analysis
6. Or use the file as input to a Pipeline node

Supported: FITS images, binary tables, multi-extension files, IFU data cubes.

## 5. Export and Publish (2 min)

After completing an analysis, you have several export options:

**From the AI Assistant:**
- Ask: "Export this session as a Jupyter notebook"
- Ask: "Generate a paper draft in AASTeX format"
- Use the **Next Steps panel** buttons

**From Pipeline Studio:**
- After a run completes, click **Publication Package** for a one-click download containing:
  - Jupyter Notebook (reproducible code)
  - CSV data tables
  - VOTable (VO-standard format)
  - FITS output files
  - Provenance record (data lineage)
  - Pinned requirements.txt (for reproducibility)

**From Data Browser:**
- Export search results as CSV or VOTable

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl + K` | Open Command Palette |
| `Cmd/Ctrl + Z` | Undo (Pipeline) |
| `Cmd/Ctrl + Shift + Z` | Redo (Pipeline) |
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
- **Deployment**: See [DEPLOY_OPENCLAW.md](../DEPLOY_OPENCLAW.md) for self-hosting instructions
