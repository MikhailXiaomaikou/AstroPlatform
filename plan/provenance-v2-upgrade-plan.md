# Provenance v2 Upgrade — Codex-Adapted Execution Plan

**Status**: Implemented in this repository (M0-M6 complete)
**Target**: Raised citation tracking and closed the main `archive_version` / UI transparency gaps for the 6 active provenance-v2 sources
**Execution mode**: Historical milestone plan. Keep it as the audit trail and re-enable template for future connector upgrades.

This is a repo-adapted version of the original plan. The core technical intent is unchanged:
- Same seven milestones
- Same locked decisions
- Same non-scope boundaries
- Same acceptance discipline
- Same commit-message intent

Only the execution surface has been normalized to this repository so Codex can run it directly. As of the current code, the resulting implementation lives under `backend/app/services/provenance_v2/`, `backend/app/services/result_provenance.py`, `backend/app/services/claim_validator.py`, `backend/app/api/chat.py`, `frontend/src/components/chat/`, `frontend/src/hooks/`, and `frontend/src/pages/Chat/ChatPage.tsx`. ALMA has since been re-enabled as a v2 source for Science Archive observation metadata only.

## 0. How to use this plan in this repo

### Canonical context file

This repository currently registers the roadmap in `CLAUDE.md`:

```md
Primary roadmap: ./plan/provenance-v2-upgrade-plan.md. Work one milestone per session.
```

### Per-session rules

1. **One milestone per session.** Do not batch `M1 + M2`.
2. **Read the whole milestone before touching any file.** Every milestone keeps the original "Non-changes" discipline.
3. **Pre-flight with `rg -n`, not hardcoded line numbers.** In this repo:
   - `_BIBCODE_RE` is in `backend/app/services/paper_generator.py`
   - `attach_provenance` is in `backend/app/services/result_provenance.py`
   - `claim_validator` is in `backend/app/services/claim_validator.py`
   - `SYSTEM_PROMPT` is in `backend/app/api/chat.py`, not in a standalone Markdown file
4. **Run the milestone acceptance commands before offering a commit.**
5. **Show the diff to the user before any `git commit`.**
6. **Do not add new dependencies without approval.** This matters most for M6: this repo already has `pytest` and `vitest`, but not Playwright.

### Blockers that must stop the session

Stop and report instead of improvising if:
- A file path listed in this plan is missing
- A milestone would require violating a "Non-change" rule
- The connector registry or prompt structure materially differs from the assumptions below
- The current code makes the locked decisions internally inconsistent

### Checkpoint protocol

At the end of each session, report:

```text
Milestone: M<N>
Status: complete | blocked | partial
Files changed: [list]
Tests run: [list with pass/fail]
Commit SHA: <hash or "not yet committed">
Outstanding questions: [list, or "none"]
```

## 1. Repo normalization

These are the only intentional deviations from the original plan text.

| Original plan assumption | Current repo adaptation |
|---|---|
| `backend/app/provenance/...` package | Create new modules under `backend/app/services/provenance_v2/` |
| `backend/app/validators/claim_validator.py` | Use `backend/app/services/claim_validator.py` |
| `backend/app/tools/paper_generator.py` | Use `backend/app/services/paper_generator.py` |
| `backend/app/prompts/SYSTEM_PROMPT.md` | Append inside inline `SYSTEM_PROMPT` in `backend/app/api/chat.py` |
| Nested pytest directories like `backend/tests/provenance/` | Keep new tests flat under `backend/tests/` |
| Frontend files under `frontend/components/` | Use `frontend/src/components/...`, `frontend/src/hooks/...`, and `frontend/src/pages/Chat/ChatPage.tsx` |
| Playwright `e2e/*.spec.ts` | Use existing `vitest` frontend tests plus backend `pytest` e2e/regression tests unless the user explicitly approves adding browser e2e tooling |

### Provenance payload shape in this codebase

The current repo stamps top-level keys such as:
- `reproducibility`
- `data_origin`
- `analysis_status`
- `source_urls`
- `archive_ids`
- `warnings`

To stay backward-compatible, v2 should **add** a nested `provenance` object rather than replacing the current top-level envelope.

Default nested shape for this repo:

```python
result["provenance"] = {
    "reproducibility": result.get("reproducibility", {}),
    "datasets": [...],
    "field_bibcodes": {...} | None,
    "coverage": {...},
}
```

Do not remove or rename the existing top-level keys.

### Connector-key normalization

User-facing source names stay the same, but the actual connector keys in this repo are:
- `vizier`
- `gaia`  (display name remains `Gaia DR3`)
- `simbad`
- `ned`
- `2mass` (implemented in `backend/app/connectors/twomass.py`)

Earlier drafts used a stale gated-connector count. In this repo, `backend/app/connectors/registry.py` currently exposes **24** connector keys, so `M0` should gate **all non-whitelisted keys**, which is currently **19** keys:

`sdss`, `sdss_spec`, `mast`, `chandra`, `allwise`, `alma`, `eso`, `irsa`, `jwst`, `lamost`, `desi`, `panstarrs`, `xmm`, `nvss`, `first`, `jpl`, `atnf_pulsar`, `sparc`, `frbstats`

## 2. Existing infrastructure to reuse

Reuse these exactly as they exist in this repo:

| Component | Current location | Use |
|---|---|---|
| Reproducibility envelope | `backend/app/services/result_provenance.py` | Preserve and extend |
| `attach_provenance` decorator/helper | `backend/app/services/result_provenance.py` | Extend payload schema without breaking existing callers |
| `_BIBCODE_RE` | `backend/app/services/paper_generator.py` | Move to shared regex module in M4 |
| ADS BibTeX sync (`_get_bibtex_sync`) | `app.api.citations`, used by `paper_generator.py` | Keep as-is |
| `claim_validator` numeric regex set | `backend/app/services/claim_validator.py` | Extend; do not rewrite |
| Current banner contract | `backend/app/services/result_provenance.py` | `__tool_status__`, `__do_not_claim__`, `__message_to_model__` remain intact |
| Metrics registry | `backend/app/observability/metrics.py` | Add counters only |
| `SYSTEM_PROMPT` | `backend/app/api/chat.py` | Append only |
| `_ACKNOWLEDGMENTS` fallback dict | `backend/app/services/paper_generator.py` | Keep as fallback |

## 3. Locked decisions

Unchanged from the original plan:

1. **FieldBibcodeExtractor recognition**: pattern matching on column names only
2. **Field vs table citation priority**: dual-layer, field-level in prose and table-level in acknowledgements
3. **Registry role**: core fallback for SIMBAD / Gaia / NED, plus universal `credits_page_url` and `acknowledgement_template`
4. **Field-level scan performance**: column-name only, no value-content scan beyond reading matched bibcode columns

Deferred decisions stay deferred:
- Citation validator originally defaulted to warning mode; the current runtime
  hard-block default superseded this historical milestone decision.
- Registry remains in-repo

## 4. Not in scope

Unchanged from the original plan, plus one repo-specific note:
- No migration from `astroquery` to `pyvo`
- No provenance upgrade for non-initial connectors beyond the dispatch gate
- No UI revamp beyond provenance surfacing and acknowledgement copy support
- No rewrite of the `data_origin` state machine
- No modifications to the 30+ existing numeric regex patterns in `claim_validator`
- No new frontend test framework or browser-e2e dependency unless explicitly approved

## 5. Milestones

### M0 — Gate non-v2 data sources as under-maintenance

**Goal**: Route every non-v2 connector through an `UNAVAILABLE` banner before connector import or execution.

**Files touched**:

```text
backend/app/connectors/
├── availability.py                  [NEW]
└── registry.py                      [MODIFY]

backend/app/services/
└── ai_tools.py                      [MODIFY if call-site handling is needed]

backend/tests/
└── test_connector_availability_gate.py   [NEW]
```

**Repo-specific implementation note**:
- In this repo, the safest place to prevent import-time connector execution is `backend/app/connectors/registry.py`.
- Whitelist the 6 v2 connectors by key: `{"vizier", "gaia", "simbad", "ned", "2mass", "alma"}`.
- Do not hardcode a stale gated-connector count; gate every key in `CONNECTORS_KEYS` that is not whitelisted.
- Direct tools that bypass `CONNECTORS_KEYS` are not implicit exceptions. If a direct source such as `run_sdss_sql` does not emit independent provenance with `archive_version`, return the same `UNAVAILABLE` maintenance banner until it is upgraded.

**Tasks**:
- Add `availability.py` with `is_available()` and `build_unavailable_response()`
- Ensure gated connectors are not imported or instantiated
- Return the existing banner shape:
  - `__tool_status__ = "UNAVAILABLE"`
  - `__do_not_claim__`
  - `__message_to_model__`
  - `data_origin = "UNAVAILABLE"` / `analysis_status = "FAILED"` compatibility with current result contract
- Emit:
  - `INFO` log `connector_gated connector=<name>`
  - counter `connector_gated_total{connector_name=...}`
- Skip or gate existing live tests that assume the now-disabled connectors remain queryable

**Non-changes**:
- Do not modify the 18 gated connector modules themselves
- Do not change the tool surface
- Do not touch the 6 v2 connectors except for scoped provenance upgrades

**Acceptance**:
- From `backend/`: `python3 -m pytest tests/test_connector_availability_gate.py -q`
- From `backend/`: `python3 -m pytest tests/test_connectors.py tests/test_result_provenance.py -q`
- Manual smoke: a gated connector such as `chandra` returns the maintenance banner and the model is instructed to suggest the 6 available alternatives

**Commit message**:

```text
feat(connectors): gate non-v2 data sources as under-maintenance

- Feature-gate unsupported sources at dispatch level
- 6 v2 connector keys (`vizier`, `gaia`, `simbad`, `ned`, `2mass`, `alma`) remain active
- Gated sources return __tool_status__=UNAVAILABLE banner with
  __do_not_claim__ + __message_to_model__ instructions for the AI
- Protects users from un-provenanced data during the v2 rollout
- Re-enable path: add connector to V2_AVAILABLE_CONNECTORS after
  completing an M3-style provenance upgrade for that connector
```

### M1 — Registry + schema extension

**Goal**: Add the new provenance data layer with no user-visible behavior change.

**Files touched**:

```text
backend/app/services/provenance_v2/
├── __init__.py                      [NEW]
├── fallback_registry.yaml           [NEW]
├── registry_loader.py               [NEW]
└── field_level_schema.py            [NEW]

backend/app/services/
└── result_provenance.py             [EXTEND]

backend/tests/
├── test_provenance_registry_loader.py   [NEW]
└── test_field_level_schema.py           [NEW]
```

**Repo-specific implementation note**:
- `result_provenance.py` is currently function-based, not dataclass-based.
- Do not refactor the existing envelope system into new dataclasses.
- Instead, add new schema helpers in `services/provenance_v2/` and make `result_provenance.py` serialize them into the new nested `result["provenance"]` object.

**Tasks**:
- Add `fallback_registry.yaml` with the original 5 services plus ALMA after its v2 metadata upgrade
- Implement `load_registry()`, `resolve_service()`, `check_freshness()`
- Add `FieldBibcodes`, `FieldLevelCoverage`, and `PrimaryCitationSource`
- Extend `attach_provenance()` / `normalize_tool_result()` so payloads can optionally carry:
  - `provenance.datasets`
  - `provenance.field_bibcodes`
  - `provenance.coverage`
  - `provenance.reproducibility`

**Non-changes**:
- Do not change banner semantics
- Do not modify connectors yet
- Do not touch `claim_validator`

**Acceptance**:
- From `backend/`: `python3 -m pytest tests/test_provenance_registry_loader.py tests/test_field_level_schema.py tests/test_result_provenance.py -q`
- From `backend/`: `python3 -m pytest tests/test_provenance_versioning.py -q`
- From `backend/`: `python3 -c "from app.services.provenance_v2.registry_loader import load_registry; print(len(load_registry()['services']))"`

**Commit message**:

```text
feat(provenance): add fallback registry and extend schema for field bibcodes

- New fallback_registry.yaml with active service keys (`vizier`, `gaia`,
  `simbad`, `ned`, `2mass`, `alma`)
- New registry_loader with freshness check
- New field-level schema helpers
- Extended tool-result provenance payload with field_bibcodes,
  field-level coverage, and primary citation source
- No behavioral change; downstream consumers land in M2-M6
```

### M2 — FieldBibcodeExtractor

**Goal**: Detect bibcode-bearing result columns and populate field-level provenance.

**Files touched**:

```text
backend/app/services/provenance_v2/
└── field_bibcode_extractor.py       [NEW]

backend/app/services/
├── result_provenance.py             [MODIFY]
└── ai_tools.py                      [MODIFY if this is where row payloads are finalized]

backend/tests/
└── test_field_bibcode_extractor.py  [NEW]
```

**Tasks**:
- Implement `FieldBibcodeExtractor` with the original pattern set
- Wire extraction into the path that has both column names and row data before the final tool result is emitted
- Populate:
  - `result["provenance"]["field_bibcodes"]`
  - `result["provenance"]["coverage"]["field_level"]`
  - `result["provenance"]["coverage"]["primary_citation_source"] = "field_level"` when available

**Non-changes**:
- Do not modify connector query syntax
- Do not change `data_origin`
- Do not touch `claim_validator` yet

**Acceptance**:
- From `backend/`: `python3 -m pytest tests/test_field_bibcode_extractor.py tests/test_result_provenance.py -q`
- From `backend/`: `python3 -m pytest tests/test_connectors.py -k "simbad" -q`
- Confirm a SIMBAD result exposes `field_bibcodes` in the emitted tool result

**Commit message**:

```text
feat(provenance): add FieldBibcodeExtractor for per-value bibcode extraction

- Pattern-based column name matching (decision #1 locked)
- Extracts SIMBAD-style (*_bibcode) and NED-style (Reference) columns
- Populates field-level provenance + coverage metadata
- No value-level scanning beyond matched bibcode columns
```

### M3 — Fill `archive_version` + wire registry for active sources

**Goal**: Populate provenance metadata for the active sources in this repo.

**Files touched**:

```text
backend/app/connectors/
├── vizier.py                        [MODIFY]
├── gaia.py                          [MODIFY]
├── simbad.py                        [MODIFY]
├── ned.py                           [MODIFY]
├── twomass.py                       [MODIFY]
└── alma.py                          [POST-M6 UPGRADE — observation metadata only]

backend/app/services/provenance_v2/
├── ivoa_dataorigin_resolver.py      [NEW]
├── param_scanner_resolver.py        [NEW]
└── non_standard_info_resolver.py    [NEW]
```

**Repo-specific implementation note**:
- Connector file is `twomass.py`, connector key is `2mass`, display name remains `2MASS`.
- Gaia connector key is `gaia`; user-facing provenance text still says `Gaia DR3`.

**Tasks**:
- VizieR: Path A via `astropy.io.votable.dataorigin.extract_data_origin()`
- Gaia: Path B via VOTable `PARAM` scan plus registry supplement
- SIMBAD: Path D via registry fallback
- NED: Path C via non-standard `INFO` scan plus registry supplement
- 2MASS: same path as VizieR, with registry fallback only
- ALMA: registry-backed ObsCore/TAP observation metadata; do not treat ALMA archive rows as derived line-luminosity or FWHM measurements
- Set `archive_version`, `source_urls`, `archive_ids`, `source_authority`, and nested `provenance.datasets[*]`

**Non-changes**:
- Do not touch any still-gated connector
- Do not alter query shapes
- Do not change cache semantics

**Acceptance**:
- From `backend/`: `python3 -m pytest tests/test_connectors.py -k "vizier or gaia or simbad or ned or twomass" -q`
- Add and run targeted provenance tests for the active connectors
- Confirm resolver failures log warnings and do not crash the connector

**Commit message**:

```text
feat(connectors): fill archive_version + provenance for active sources

- VizieR: Path A via astropy extract_data_origin
- Gaia DR3: Path B via PARAM scanner + registry supplement
- SIMBAD: Path D via registry (no DataOrigin emission)
- NED: Path C via PROVIDER INFO + registry supplement
- 2MASS: Path A via VizieR (II/246)
- ALMA: registry-backed ObsCore observation metadata (post-M6 upgrade)

Closes archive_version 0% fill rate for focus sources.
```

### M4 — Citation validator extension

**Goal**: Block or warn on citation fabrications using a tool-sourced bibcode pool.

**Files touched**:

```text
backend/app/common/
└── regex.py                         [NEW]

backend/app/services/
├── claim_validator.py               [EXTEND]
└── paper_generator.py               [MODIFY import only]

backend/tests/
├── test_citation_validation.py      [NEW]
└── test_b7_regression.py            [NEW]
```

**Tasks**:
- Move `_BIBCODE_RE` into `backend/app/common/regex.py`
- Add `AUTHOR_YEAR_RE` and `IVOID_RE`
- Extend `claim_validator.py` with:
  - `_build_valid_bibcode_pool(...)`
  - `provenance_citation_violations(...)`
  - env/config flag `PROVENANCE_VALIDATOR_HARDBLOCK` defaulting to false
- Pool sources:
  - `tool_result.provenance.datasets[*].article`
  - `tool_result.provenance.field_bibcodes`
  - `tool_result.bibcode`
- Emit counters:
  - `fabrication_blocked_total{reason="invalid_bibcode"}`
  - `fabrication_blocked_total{reason="suspicious_author_year"}`

**Non-changes**:
- Do not delete existing numeric regex patterns
- Do not alter literature-prior hard blocks
- Do not touch the CJK guard behavior

**Acceptance**:
- From `backend/`: `python3 -m pytest tests/test_claim_validator.py tests/test_citation_validation.py tests/test_b7_regression.py -q`
- From `backend/`: `python3 -m pytest tests/test_observability.py -q`
- Confirm `paper_generator.py` still imports and behaves normally

**Commit message**:

```text
feat(validator): add citation validation against tool-sourced bibcode pool

- Shared regex module for cross-module reuse
- provenance_citation_violations() scans reply for bibcodes / author-year
- Valid pool: datasets.article + field_bibcodes + search_literature.bibcode
- Warning mode by default; hardblock gated by PROVENANCE_VALIDATOR_HARDBLOCK
- Closes B7 Fernie-1995 fabrication class
```

### M5 — `SYSTEM_PROMPT` update + `paper_generator` provenance preservation

**Goal**: Teach the model the new citation hierarchy and preserve provenance in generated papers.

**Files touched**:

```text
backend/app/api/
└── chat.py                          [APPEND inside inline SYSTEM_PROMPT]

backend/app/services/
└── paper_generator.py               [MODIFY]

backend/tests/
├── test_paper_acknowledgement.py    [NEW]
└── test_system_prompt_helpers.py    [UPDATE/EXTEND]
```

**Repo-specific implementation note**:
- There is no standalone prompt file here. Append the new section inside `backend/app/api/chat.py`.
- Insert the new provenance section immediately before `## ZERO-FABRICATION CONTRACT`, or cross-reference it explicitly if the string structure makes insertion there safer.

**Tasks**:
- Add the mandatory provenance reporting section from the original plan to the inline `SYSTEM_PROMPT`
- In `paper_generator.py`, preserve and surface:
  - `run_id`
  - `query_hash`
  - `archive_version`
  - `tool_version`
  - `source_urls`
- Generate acknowledgements dynamically from dataset provenance, with `_ACKNOWLEDGMENTS` as fallback

**Non-changes**:
- Do not remove `_ACKNOWLEDGMENTS`
- Do not change BibTeX fetch logic
- Do not restructure the rest of `SYSTEM_PROMPT`

**Acceptance**:
- From `backend/`: `python3 -m pytest tests/test_paper_acknowledgement.py tests/test_system_prompt_helpers.py tests/test_synthetic_fallback_regression.py -q`
- Generate a paper draft manually and confirm provenance fields appear in output

**Commit message**:

```text
feat(ai): enforce citation priority + dynamic acknowledgement generation

- SYSTEM_PROMPT now mandates 3-tier priority (field > table > registry)
- paper_generator preserves run_id / query_hash / archive_version in output
- Acknowledgement section dynamically composed from registry templates
- Fallback to _ACKNOWLEDGMENTS dict preserved for compat
```

### M6 — UI surfacing + B7 end-to-end regression

**Goal**: Expose provenance to users and verify the full B7 flow in the existing test stack.

**Files touched**:

```text
frontend/src/components/chat/
├── DataSourcesPanel.tsx             [NEW]
└── AckButton.tsx                    [NEW]

frontend/src/hooks/
└── useConversationProvenance.ts     [NEW]

frontend/src/pages/Chat/
└── ChatPage.tsx                     [MODIFY]

frontend/src/__tests__/
├── ChatPage.test.tsx                [EXTEND]
└── useConversationProvenance.test.ts [NEW]

backend/tests/
└── test_b7_regression.py            [EXTEND if full-flow coverage is still backend-driven]
```

**Repo-specific implementation note**:
- This repo does not currently include Playwright. Do not add it as part of this plan.
- Use `vitest` + React Testing Library for UI behavior, and keep full regression logic in backend `pytest` where needed.
- There is no `ToolResult.tsx`; wire the new panel into the existing tool-result rendering path inside `ChatPage.tsx`.

**Tasks**:
- Add a collapsible `DataSourcesPanel`
- Add `AckButton` wired to clipboard copy
- Aggregate conversation-level provenance in `useConversationProvenance()`
- Surface:
  - service name
  - `archive_version`
  - `ivoid`
  - article / bibcode
  - field-bibcode counts
  - authority-state visual cues
- Add regression coverage proving the B7 scenario is blocked or clearly warning-flagged

**Non-changes**:
- Do not redesign existing SYNTHETIC / FAILED / EMPTY chips
- Do not rewrite the Chat page layout

**Acceptance**:
- From `frontend/`: `npm test`
- From `frontend/`: `npm run build`
- From `backend/`: `python3 -m pytest tests/test_b7_regression.py tests/test_e2e_full.py -q`
- Manual smoke:
  - SIMBAD result shows field-level references
  - 2MASS result shows `archive_version`
  - "Copy Acknowledgement" produces the expected template

**Commit message**:

```text
feat(ui): surface provenance, archive_version, and acknowledgement generator

- DataSourcesPanel: collapsible per-tool-result provenance details
- AckButton: one-click dual-layer citation template
- B7 end-to-end regression: fabrication blocked, user sees correct citation
- Closes UI transparency gap
```

## 6. Definition of done

The original definition of done remains, translated to this repo's surface:

- All existing backend tests still pass
- All new tests pass
- All non-v2 connector keys return `UNAVAILABLE` without executing legacy query code
- The 6 active sources populate `archive_version`
- Citation fabrications are warning-flagged by default and blockable via config
- The UI surfaces provenance and acknowledgement text without regressing current chat behavior
- Both new metrics families appear in `/metrics`:
  - `connector_gated_total{connector_name=...}`
  - `fabrication_blocked_total{reason=...}`
- Registry freshness warnings block application startup; stale fallback provenance must be fixed before serving traffic

## 7. Notes for the implementer

1. **Do not let the original path names leak into edits.** In this repo, the correct targets are `services/`, `api/chat.py`, flat `backend/tests/`, and `frontend/src/...`.
2. **Treat the new nested `provenance` object as additive.** The current top-level result contract is already depended on by tests and UI.
3. **Do not trust line numbers from the original draft.** Use `rg -n`.
4. **M0 must use connector keys from `CONNECTORS_KEYS`, not human-readable source names.**
5. **M6 should stay within the existing test stack.** No new browser runner unless the user explicitly asks for it.
6. **Commit boundaries still matter.** Even in this adapted version, each milestone should remain atomic and revertible.
