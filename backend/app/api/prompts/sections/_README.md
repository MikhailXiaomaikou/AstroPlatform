# SYSTEM_PROMPT sections

Each `.md` file here is one logical group of rules in the full
SYSTEM_PROMPT. Files are loaded in lexicographic order by
`backend/app/api/prompts/__init__.py` `assemble_system_prompt()`.

Naming convention: `NN_topic.md` where NN is a 2-digit ordering prefix.

Files starting with `_` (like this README) are treated as drafts and
skipped by the loader.

## Migration plan from monolithic chat.py SYSTEM_PROMPT

The 1848-line monolith in `chat.py` will be migrated section by section
into the planned files below. Each file is one git commit; each commit
keeps every keyword-asserting test green.

| File | Source sections (chat.py SYSTEM_PROMPT `## ...`) |
|---|---|
| `01_safety_contracts.md` | USER-PROMPT INJECTION DEFENSE / ANTI-INSTRUCTION-REFLECTION |
| `02_data_release_pins.md` | DATA RELEASE PINS / COSMOLOGY PRESETS |
| `03_zero_fabrication.md` | ZERO-FABRICATION CONTRACT / Data integrity rules |
| `04_provenance_and_synthetic.md` | Data provenance reporting / SYNTHETIC data workflow / Catalog-only reporting / data_source HARD RULE |
| `05_abstention_and_retry.md` | TOOL RETRY BUDGET / STRUCTURED ABSTENTION |
| `06_role_and_decision_tree.md` | Your role / Decision tree |
| `07_adql_idioms.md` | ADQL aggregate-function semantics / ADQL Usage Rules / Cluster idioms |
| `08_gaia_dr3_workflows.md` | Gaia DR3 completeness / specialized tables / GSP-Phot warnings |
| `09_cluster_workflows.md` | Cluster / CMD age / Open / Globular / BSS / cluster legacy |
| `10_distance_and_extinction.md` | Distance hierarchy / Extinction options / Low-EBV target routing |
| `11_variable_stars.md` | Variable star workflow |
| `12_specialized_xray_galaxy.md` | X-ray spectral / Galaxy SFR / RV orbit / Rotation / Sersic / IMF |
| `13_specialized_stellar_atm.md` | Stellar atmosphere models / White dwarf cooling / Brown dwarf / Pulsar |
| `14_specialized_extragalactic.md` | Galaxy cluster virial / IFU 2D / AGN SED / Streams / Solar system |
| `15_simbad_columns.md` | SIMBAD basic table |
| `16_actions_and_pipeline.md` | Available actions / Pipeline DAG / Examples |
| `17_response_rules.md` | English-only / Clustering failure / Python execution |
| `18_research_mode.md` | Proposal Generation / Transient Temporal / Parameter Sensitivity / Research Mode |

When a section is migrated:

1. Cut its full body (everything between two `## ...` headers) from
   chat.py SYSTEM_PROMPT into the corresponding file here.
2. Run the keyword-asserting tests:
   ```
   cd backend && pytest tests/test_admin_literature.py \
     tests/test_h_regression.py tests/test_system_prompt_helpers.py \
     tests/test_m6_methodology_validator.py \
     tests/test_research_focus_gating.py -q --no-cov
   ```
3. Update `chat.py` SYSTEM_PROMPT either by:
   - calling `assemble_system_prompt(archive_manifest=...)` if all
     sections have been migrated, OR
   - leaving the monolith intact during partial migration (the
     keyword tests still hit the unmoved sections).

The full swap-over only happens after the LAST section is migrated.
Until then the loader produces a partial preview; production keeps
using chat.py's literal string.

## Why files starting with `_` are skipped

Working drafts (`_draft_xxx.md`) and this README don't belong in the
production prompt. Skipping them prevents accidental inclusion.
