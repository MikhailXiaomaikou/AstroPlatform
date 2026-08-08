# Changelog

All notable project changes should be recorded here in English.

This file summarizes product-facing and scientific-infrastructure changes.
Low-level refactors, test-only edits, and temporary local diagnostics do not
need entries unless they change user-visible behavior or research validity.

## Unreleased

### Lightweight verification v0.2 hardening (2026-08-03 → 2026-08-07)

- Added a dark-launched deterministic source-check path for bounded scalar
  ratio, difference, product, and inverse-covariance weighted-mean
  calculations. It validates units and covariance explicitly, never evaluates
  a model-authored expression, and separately authorizes the derived number
  and the cited source measurement.
- Added bounded arXiv, DOI, Zenodo, and public-HTTPS source resolution with
  locator-scoped label/value matching, content-addressed caching, pinned DNS
  connections, response/expansion limits, and explicit conflict, unmatched,
  unverified, and unavailable states.
- Added hashed Scalar Verification Receipts and general Evidence Receipt cards
  in the chat UI. Response dispositions now distinguish full, limited,
  abstention, refusal, and hard block instead of flattening every incomplete
  source check into a failure.
- Added durable getdist chain exports. In-process chains disclose that their
  equal weights and zero `-loglike` column reflect unavailable per-sample
  likelihoods; external Cobaya chain files are preserved verbatim. Partial
  uploads are never advertised, and storage failures do not discard a
  completed scientific result.
- Added pre-registered canonical and natural-phrasing evaluations, model-call
  stratification, should-pass corpora, a repository-safe holdout commitment,
  formal report artifacts, and an expert-review script. The original plaintext
  candidate was retired as contaminated; a fresh set must be held by an
  independent custodian outside the development loop. The deterministic canonical matrix
  is reported as a pipeline self-check, not model behavior; the post-fix
  natural model-in-loop stratum scored 651/720 (90.4%, n=60), pending
  independent expert review.
- Sixty-three adversarial review rounds tightened heavy-chain routing, number and
  uncertainty binding, compound table/row locators, multi-paper ambiguity,
  cache identity, operation/source/unit echo guards, singular weighted means,
  scale-relative covariance validation, negation-aware independence parsing,
  negation-aware source assignments and independence parsing, grammatical
  source-field/window boundaries, cosmology manifest/calculation baseline
  consistency, backend-owned receipt boundaries, operation-arity checks,
  multi-agent validation-summary preservation, holdout custody, and
  DNS-rebinding resistance. Observational uses of the noun `sample` no longer
  trigger a heavy sampler route, while untrusted PDF parsing now runs in a
  killable, resource-, page-, output-, and time-bounded subprocess. None of
  these changes relaxes the existing claim gates. Exact source verification
  now also binds normalized units, postposed measurement disclaimers, and
  cache identity; prompt quantities explicitly rejected by the user cannot
  enter direct or model-authored deterministic calls. Later label-targeted
  prompt disclaimers also reject the named earlier quantity without treating
  unrelated prose as a rejection, and exact source attribution now requires
  every input quantity to reference a declared external or user-supplied
  source. Postposed heavy-work negation now keeps bounded scalar tasks off the
  expensive route; lowercase `a` labels no longer collide with the English
  article; appositive commas preserve their governing measurement negation;
  and products use the exact second-order variance identity only for
  explicitly independent inputs, failing closed when covariance alone leaves
  the required higher mixed moment unidentified. Ratio uncertainty is now
  explicitly labeled as a first-order delta-method approximation, with its
  method and distributional caveat carried into the receipt; bounded
  postposed-negation parsing also recognizes controlled heavy-work noun
  modifiers while preserving affirmative heavy-route requests. Correlation
  values and scalar-operation alternatives that the user explicitly rejects
  after naming them are now excluded from deterministic routing and fallback
  echo validation instead of overriding the user's active request. Mixed
  external and user-supplied inputs can no longer expose a blanket exact
  measurement scope, and source matching now tries every bounded label
  occurrence without ignoring a later conflicting numeric assignment.
  Proposition-level rejection before a measurement label is now part of the
  assignment scope, while later section/equation/citation numbers count as
  conflicts only when the surrounding syntax actually assigns a measurement.
  Dimensionless exact claims now reject a trailing physical-unit token even
  outside the original distance-unit vocabulary, and infinitival disclaimers
  such as `is not to be used` now fail closed across scalar quantities,
  source measurements, correlations, and postposed heavy-intent phrases.
  Complementizer-free source denials now remain inside the assignment scope,
  and a physical unit must be immediately attached to the matched measurement
  instead of being borrowed from an unrelated later quantity in the field.
  Conditional and hypothetical source assignments are now excluded from exact
  measurement evidence while affirmative source-reporting syntax remains valid.
  Post-label modal or trailing conditional measurements are likewise non-exact,
  and do-support phrases such as `does not need to be performed` now suppress
  explicitly rejected heavy routes without dropping a valid lightweight call.
  Perfect-modal source predicates such as `could have been measured as` are
  also non-exact, while the nonconditional `if anything` qualifier remains
  eligible for exact verification instead of being false-killed. That
  exemption is restricted to bounded measurement qualifiers; real conditions
  such as `if anything in the calibration changes` still fail closed. Prompt
  prose that locally marks an operand as user-supplied now keeps that operand
  off a cited paper's source packet, and existential source denials such as
  `there is no evidence that` cannot certify the proposition they reject.
  Model-authored fallback calls preserve the same prompt-local provenance;
  infinitival denials such as `there is no evidence to support` also fail
  closed. Prose exact attribution now requires positive assignment/reporting
  syntax, while explicitly structured table rows retain their bounded bare
  `label value +/- uncertainty` form. Subject-first source denials such as
  `no evidence supports` are also rejected before exact attribution.
  Insufficient or inadequate evidence/support language now fails closed in the
  same proposition scope, and non-finite derived arithmetic is rejected with an
  actionable `non_finite_result` abstention before receipt construction.
  Modal disclaimers such as `a fit need not be performed` now keep complete
  scalar requests on the lightweight route, and `not enough evidence` source
  propositions cannot earn exact attribution. Qualified evidence denials such
  as `the available evidence does not support` likewise fail closed, while
  non-modal perfect-aspect reporting such as `has been measured as` remains a
  valid positive measurement form. Mock, simulated, synthetic, fiducial, and
  illustrative assignments are now excluded from exact observational
  attribution without hiding a real observed measurement mentioned in a
  comparison against those configurations. Common configuration nouns such as
  fiducial cosmology, configuration, and setup are covered by the same guard;
  baseline, reference, and benchmark configurations are excluded as well.
  Assumed and adopted configurations are covered by the same fail-closed rule.
  Configuration nouns such as cosmology, model, setup, and scenario now fail
  closed independently of the particular adjective, while observational data,
  sample, and catalogue nouns retain a narrower qualifier requirement.
  `under` and `within` configuration scopes are covered as well; an affirmative
  measured/reported/estimated/found predicate can still certify an explicit
  model-scoped result, while bare or `given` configuration assignments remain
  fail-closed.
  `with` and clause-leading `given` configuration scopes are fail-closed too.
  Controlled standardized differences remain ordinary derived numbers rather
  than being promoted to Gaussian sigma significance without a distribution,
  and the Kimi CLI bridge rejects prompts above a 120 KiB argv safety bound
  before process creation.
  User-supplied provenance is now scoped across targeted and collective
  postposed declarations without leaking onto the next quantity, and source
  values postposed as assumed, adopted, or fixed inputs cannot verify exact.
  Postposed `used`, `set`, or `taken as` configuration roles cover fiducial,
  baseline, default, reference, benchmark, and nominal values or inputs.
  Result-bearing verbs such as `gives`, `yields`, and `obtains` now expose H0
  and delta-chi-squared claims to the release escape gate even when the prose
  uses `around` or `of` instead of an equals sign. Scalar-source receipts still
  establish backend verification, but their hidden locators no longer stand in
  for a citation in the user-visible V02_01--04 reply.
  Transitive configuration predicates such as `we set/fix/adopt/use alpha`
  cannot certify paper measurements, and the V02 release gate now recognizes
  parameter-labelled H0 and delta-chi-squared numbers structurally instead of
  relying on a finite result-verb list. A qualifying publication-ready result
  suppresses earlier exploratory capability-gap receipts, whose fallback
  dependencies are now derived only from the actual request through the same
  helper used by the agent loop.
  Clause-leading determiners such as `No fit is necessary` now suppress that
  explicitly rejected heavy route while preserving a complete scalar call.
  The V02_03 visible reply must now co-locate the exact arXiv identifier and
  Equation 42; a broad `ACT DR6` product name cannot substitute for that source
  locator even when the backend receipt verified the measurement.
  Transitive `choose`, `select`, and `impose` configuration predicates, including
  present, past, and perfect forms, can no longer certify a deliberately chosen
  model input as an exact paper measurement.
  Transitive `hold`/`keep` forms and postposed `held`/`kept fixed` predicates now
  likewise classify locked fit parameters as configuration rather than exact
  source measurements.
  V02_05 and V02_07 source credit now requires exact attribution in the
  user-visible reply; hidden tool payloads, prompt echoes, and capability-gap
  receipts may validate that citation but cannot substitute for it.
  Existential disclaimers such as `There is no need for a fit` now keep a
  complete scalar request on the lightweight route, prior-distribution
  assignments cannot certify configured parameters as measurements, and
  value-before-label H0 result prose is covered by the V02 release escape gate.
  Copular prior clauses are also configuration assignments, and infinitive
  existential disclaimers such as `There is no need to run a fit` preserve a
  complete lightweight scalar call.
  Direct `no need to fit` clauses are covered as well. V02_01 numeric credit
  now requires visible ratio and uncertainty values grounded by the scalar
  receipt, while V02_06 full source credit requires visible `Pantheon+` and
  registered 2.26 coverage rather than a hidden registry receipt alone.
  V02_02--05 numeric, uncertainty, end-to-end, and risk credit now likewise
  requires every scored result in the user-visible reply; hidden scalar
  receipts and registered-anchor facts may ground those values but cannot
  supply omitted answers.
  Copular fiducial/baseline/default/reference-value declarations are now
  classified as configuration rather than exact measurements; existential
  no-need fit disclaimers accept `any` and `another`; and V02_06 full
  end-to-end credit requires visible `Pantheon+` identity and 2.26 coverage.
  Bound physical units are now consumed before postposed configuration
  semantics are checked, and V02_07 rejects value-before-label
  delta-chi-squared fit results without confusing equation references for
  results.
  Chain artifacts now remain private until authoritative result normalization
  finishes, so a production-version or reproducibility downgrade cannot leave
  blocked posterior files downloadable. V02_05 full end-to-end and low-risk
  credit now also requires both anchor uncertainties in the visible reply.

### P0 production and local-automation hardening (2026-07-13)

- Added a loopback-only Bot Console for the local weekly cosmology-research and
  notification automation, fixed to the tool-free OpenAI subscription CLI and
  rejected in hosted production.
- Added a 50-per-UTC-day shared-model allowance for anonymous IP buckets and
  starter accounts. Production counters require Redis and fail closed; BYOK or
  local-model requests cannot silently fall back to a platform-funded key. The
  reference production topology keeps public chat BYOK-only and disables this
  optional paid path unless an operator explicitly opts in.
- Production WebSockets now reject query-string JWTs and untrusted browser
  origins. Production CORS no longer opts every opaque `null` origin in by
  default.
- Celery Beat no longer schedules the out-of-scope transient-alert ingester
  unless it is explicitly enabled; the default production schedule remains
  cosmology-only.
- Remote deployment acceptance now requires a full expected commit, matching
  `/health/ready` and `/health/deep` backend identities, same-commit Celery
  workers, and same-commit per-instance Beat leases renewed by scheduler ticks.
  Portable backups require
  identifiable key material and a full commit; restores bind the canonical
  database artifact to that manifest, require a truly fresh database and
  absent storage target, reject migrations that overlap `pg_dump`, and validate
  schema before atomic no-replace storage placement.
- Production JWT, Fernet, and evidence-signing keys are now operator-supplied
  recovery secrets instead of Blueprint-generated values. Added the cutover,
  security, privacy, and contribution governance documents; the production
  cutover itself remains an explicit operator action.

### Campaign backfill (2026-05-28 → 2026-06-13)

The observational-cosmology completion campaign. Product- and science-facing
highlights (122 commits; per-change detail lives in the git log and
`plan/cosmology-completion-backlog.md`):

#### Added — executable likelihoods over released, sha256-pinned data
- Planck 2018 clik-free CMB suite (plik_lite TTTEEE, lowl TT, lowl EE,
  lensing) vendored natively and dispatched to external Cobaya behind
  `EXTERNAL_COBAYA_ENABLED`; mnu and omegak are genuinely sampled there
  (with priors and CAMB parameter aliases fixed: `*_mnu` chains actually
  sample mnu, `ok_*` chains actually run and sample curvature).
- SDSS MGS non-Gaussian chi2(alpha) table (cobaya parity ≤1e-12) replacing
  the hand-typed Gaussian; 6dFGS half unchanged.
- Union3/UNITY1.5 full 22-bin binned-distance likelihood (always on;
  offset-marginalized, cobaya-identical projection algebra).
- BOSS DR12 consensus BAO (the Planck 2018 "+BAO" likelihood; dimensional
  rs_fid=147.78 convention, real-cobaya instantiation parity).
- eBOSS DR16 ELG probability table + Lyα auto/cross 50×50 likelihood grids
  (non-Gaussian released surfaces at z=2.334 — the only z>2 BAO anchor
  outside DESI; out-of-grid samples are refused, never extrapolated, with
  per-dataset refused-prior-volume accounting).
- Pantheon (2018) full 1048-SN vector behind `PANTHEON18_FULL_CHI2_ENABLED`
  (offset-marginalized; reproduces the published Ωm = 0.298 ± 0.022
  including the error bar).
- Kelly-2007 upper-limit censoring in `fit_line_lfr` (opt-in, Bayesian-only).
- Registry now 34 entries; every executable probe reads a sha256-verified
  vendored file, enforced by `audit_executable_pins` and a self-policing
  test.

#### Changed — research matrix and model comparison
- The phase-1 research-matrix gate opened for flat dark-energy extensions:
  wcdm/w0wa_cdm cells run numerically (emcee upgrade) alongside a full-union
  ΛCDM comparison anchor; curvature/neutrino-mass cells stay config_only and
  point to the CMB path. Matrix execution is budgeted (≤24 run cells,
  ≤3 emcee cells, duplicates skipped — all loudly warned).
- `compute_model_comparison` gained a four-rung validity ladder (blocked
  tier, unknown tier, unmeasurable ESS, representation mismatch); invalid
  comparisons fail closed to `preferred=undetermined` AND carry
  `__do_not_claim__` so their deltas can never support a reply claim; valid
  verdicts carry chain tiers and an exploratory caveat.

#### Fixed — diagnostics honesty
- Removed five hard-coded `rhat=1.0` sites (importance/analytic paths have
  no MCMC chains; R-hat is now honestly `null` with a note) and the
  `delta_chi2=0.0` placeholders; ESS now carries an explicit source label,
  and an autocorrelation failure caps the chain at exploratory instead of
  silently promoting it to publication via an n/10 fallback.
- The Alcock-Paczynski tool now fits the sha256-verified vendored arrays
  (byte-identical values) and refuses loudly on unverified data.

#### Security/honesty — same-turn laundering wall
- The claim universe is built from tool RESULTS only, with every number the
  model authored in tool INPUTS structurally subtracted (closes query/params
  echo channels); `export_research_report` / `verify_research_facts` /
  `build_evidence_graph` receive the server's own turn record as the only
  trusted evidence — model-supplied transcripts render at most an UNVERIFIED
  draft and verification refuses.
- Citation/identifier strings (sha256 digests, arXiv ids in provenance
  prose, registry naming prose) and tool-input echoes are excluded from the
  claimable numeric universe; structured siblings (z_coverage tuples,
  rs_fid_mpc) keep honest claims grounded. Red-team corpus 21 → 34 cases.
- Blind-test suite 10 → 15 cases: hard specificity gates (the clean LFR
  demo, the likelihood chain, honest abstention) and hard fake-transcript /
  self-supplied-evidence sentinels, all live-verified.
- Structured gate events (JSONL + SSE + Prometheus counter) make every
  reply-gate intervention observable — the false-positive measurement layer.

#### Operations
- Daily blind cron moved off the congested top-of-hour (16:00 → 16:17 UTC);
  `audit_citation_pool` joined the daily workflow.
- Benchmarks 18 → 25 pinned baselines; backend suite ~1.9k → ~2.5k cases.


### Fixed

- **Cosmology research-matrix + fact-check regression (2026-05-27).**
  Hardened the BAO/SN/CMB compressed-likelihood workflow after an in-app
  Chat UI regression test:
  - Pantheon+ is now executable as a compressed-preliminary SN likelihood path
    instead of remaining config-only for the first-phase research matrix.
  - `run_research_matrix` now emits fixed BAO/SN/CMB cells, including
    `BAO only`, `SN only`, `CMB only`, `BAO + CMB`, `BAO + SN`,
    `SN + CMB`, `BAO + SN + CMB`, and H0-prior variants.
  - Low-diagnostic cells are marked `executed_not_ready` rather than
    claimable. In the Chat UI regression, `BAO + SN + CMB` executed but was
    correctly withheld from Results because `ESS=38.8 < 400`.
  - Fact verification now treats future-work scope statements such as "a full
    external Cobaya/CosmoSIS chain would be needed" as gap statements, not as
    contradictory claims that block the whole report.
  - Verified in the local in-app Chat UI: `BAO + CMB` is ready with
    `H0=67.305`, `Omega_m=0.3116`, `ESS=471`, and `Rhat=1.000`; the exported
    report keeps ready cells in Results and moves low-ESS cells to
    Robustness/Scope.
  - Single-cell deep runs (`run_cosmology_likelihood_chain`) now auto-upgrade
    from importance sampling to compressed-emcee when the importance ESS
    collapses on a 3+ probe product (measured `BAO+SN+CMB` ESS 38 → 870+),
    recovering a publication-ready posterior in ~11 s. Robustness/research
    matrices keep the fast importance path, and their `executed_not_ready`
    cells now carry a hint to re-run the flagship combination as a single-cell
    deep run.
- **Solar-system blind-test follow-up (2026-05-26, commit 2b85340).** Three
  fixes surfaced by a 20-case DeepSeek blind run of the `solar_system` module:
  - **English-only reply no longer discards the turn.** A non-English (CJK)
    final reply previously hit a hard block and the whole answer was lost. The
    agent loop (`api/chat.py`) now asks for one English regeneration
    (`build_english_regeneration_prompt`, preserving every number/citation)
    before falling back to the block — so a Chinese-prompt turn returns an
    English answer instead of a blocked card.
  - **Dates no longer mis-flagged as citations.** `claim_validator`'s
    `_author_year_looks_like_noise` now treats current/future years and common
    leading words as dates, so "The 2029", "Phaethon 2026", and "Ephemeris
    (2026" are no longer withheld as fabricated author-year references.
  - **Bibcode regex no longer eats markdown link tails.** `common/regex`
    `BIBCODE_RE` was greedy (`\S+`) and swallowed `](url)` after a bibcode,
    turning a valid `1998Icar..131..291H` into an `invalid_bibcode`; tightened
    to bibcode-legal characters only.
  - Blind-test runner (`scripts/blind_test_m0/runner.py`) gained
    `--provider deepseek`. 275 claim_validator + cosmology regression tests
    pass; A3/A4 DeepSeek reruns confirm the regen + bibcode fixes.
- **Zero-fabrication hardening (anti-synthetic).** Closed several run_python
  fabrication-guard bypasses surfaced by blind testing:
  - `synthetic_code_detector` now catches `torch`/`jax`/`tensorflow` RNGs,
    `getattr(np, "random")` dynamic access, and `pd.date_range` fabricated
    time axes (previously only `np.random`/`scipy`/stdlib-`random` +
    `np.linspace`/`np.arange`).
  - The G3.2 real-cache exemption is AST-verified now, so a reader name in a
    comment or string literal can no longer spoof it.
  - `cached:<key>` is rejected when the key is not live in the result cache.
  - Added a `user_file:<path>` data_source so a genuine `pd.read_csv` /
    `pd.read_parquet` / `load_csv` of the user's own data is auto-classified
    as real instead of being mislabelled synthetic.
- **Claim-validator false negatives/positives.** Whitelisted exoplanet +
  solar_system tools in `_CITABLE_ANALYSIS_TOOLS` and the age/mass/distance
  literature-prior gate (their real results are no longer flagged as
  unsupported); `extract_claims` now strips thousands separators
  (`1,234` → `1234`) instead of splitting on the comma.
- **Mid-sentence truncation detection** now flags a reply ending on `=`.
- **Robustness matrix** cells carry an explicit `status`
  (`runnable`/`missing_likelihood`/`config_only`/`blocked`/`failed`) so an
  empty cell is distinguishable from a negative scientific result.
- **`/health/deep`** reports deploy version (`commit`/`branch`/`service`).

### Added

- PR template (`.github/PULL_REQUEST_TEMPLATE.md`) mapping a fix to a blind-test
  failure category + regression test + anti-hardcoding checklist; hidden-paper
  record template (`docs/HIDDEN_PAPER_RECORD_TEMPLATE.md`), kept out of prompt context.

- Added the **Exoplanet** research module (M0, 2026-05-20 to 2026-05-21) —
  third active vertical after cosmology and solar_system. Reuses the 6-layer
  template (Karpathy 三相似临界 — ModuleRegistry abstraction now eligible).
  - Activated `backend/app/prompts/modules/exoplanet/` (manifest + 91-line prompt
    + appendix) with `status: active`; selected via `ASTRO_RESEARCH_FOCUS=exoplanet`.
  - Added 8 LLM-callable exoplanet tools in `ai_tools_exoplanet.py`:
    `query_exoplanet_archive`, `query_confirmed_planets`, `fetch_tess_lightcurve`,
    `fit_transit` (trapezoidal Nelder-Mead, fast; recommends batman/pytransit
    downstream for limb-darkened publication fits), `compute_equilibrium_temperature`,
    `compute_transit_depth`, `compute_planet_density`, `query_tess_target_list`.
    Each tool carries inline literature references (Mandel & Agol 2002,
    Seager & Mallén-Ornelas 2003, Akeson+ 2013 NASA Exoplanet Archive,
    Ricker+ 2015 TESS, Stassun+ 2019 TIC v8).
  - Added pure-function science kernels under `services/exoplanet_physical.py`
    and `services/exoplanet_transit.py`.
  - Promoted `nasa_exoplanet_archive` connector to provenance-v2 active with
    pscomppars composite-parameters TAP wrapper via astroquery.ipac.nexsci.
- Added 4 **generic chat-result Panel components** under
  `frontend/src/components/viz/`: `TablePanel`, `PlotlyXYPanel`, `WarningCard`,
  `BarChartPanel`. These replace per-tool dedicated panels — 20 solar_system +
  exoplanet tools route to these four panels by output shape (Karpathy
  三相似才抽象).
- Added the **Solar System** research module (M0, 2026-05-18 to 2026-05-20):
  - Activated `backend/app/prompts/modules/solar_system/` (manifest + prompt + appendix) with `status: active`; selected via `ASTRO_RESEARCH_FOCUS=solar_system`.
  - Added 12 LLM-callable solar-system tools in `ai_tools_solar_system.py`:
    `query_mpc_orbit`, `fetch_horizons_ephemeris`, `query_sbdb_orbit`,
    `query_sbdb_close_approaches`, `query_sentry_risk`,
    `query_damit_shape_model`, `compute_hg_magnitude`, `compute_afrho`,
    `fit_neatm_diameter_albedo`, `compute_neo_collision_probability`,
    `classify_asteroid_busdemeo`, `classify_asteroid_sdss_colors`. Each tool
    carries inline literature references (Bowell+1989, A'Hearn+1984,
    Harris 1998 + Mainzer+2011, Öpik 1951 / Wetherill 1967 / Morbidelli+2002,
    DeMeo+2009, Carvano+2010, Ďurech+2010, Giorgini+1996).
  - Added pure-function science kernels under
    `services/solar_system_dynamics.py`, `solar_system_phot.py`,
    `solar_system_taxonomy.py`, `solar_system_thermo.py`.
  - Promoted JPL Horizons (`jpl`) and IAU Minor Planet Center (`mpc`)
    connectors to provenance-v2 active and added their provenance entries.
- Introduced **modular prompt + focus-gate architecture** (M1):
  - New three-layer prompt tree under `backend/app/prompts/`:
    `base.md` + `core/*.md` (cross-cutting rules) +
    `modules/<name>/{manifest.yaml, prompt.md, appendix.md}`.
    2 active modules (`cosmology`, `solar_system`) + 13 dormant modules.
  - New `services/prompt_loader.py` assembles the SYSTEM_PROMPT and the
    per-focus tool allowlist; `api/chat.py` `_filter_tools_by_research_focus`
    enforces L1 hard tool gating before tools reach the LLM.
- Added Research Mode v1 for observational cosmology: `plan_research_program`
  creates a structured research DAG, `run_research_matrix` executes runnable
  compressed-likelihood cells while preserving config-only gaps,
  `build_evidence_graph` links claimable parameters to current-turn tool runs
  and dataset citations, and `export_research_report` drafts an auditable
  Markdown report.
- Added fact verification for research-mode outputs via `verify_research_facts`,
  including checked source identifiers, unsupported/contradicted claim reporting,
  and safe-rewrite guidance.
- Added paper-to-tool mining infrastructure: `mine_paper_tools`,
  `run_paper_tool_mining_batch`, `build_tool_ontology`,
  `build_tool_gap_matrix`, and `rank_tool_implementation_queue` map full-paper
  method/table/equation evidence into ToolSpecs, recurring capabilities, gaps,
  and implementation priorities.
- Added `build_paper_mining_candidate_pool` to assemble deduplicated paper
  candidates from supplied seeds or explicitly enabled arXiv searches before
  entering the mining loop.
- Added `load_cosmology_data_product`, a controlled registry-backed loader for
  machine-readable cosmology data vectors and covariance products. It parses
  ASCII tables/matrices, reports shape/preview rows, verifies sha256 when
  available, and performs covariance sanity checks without claiming posterior
  constraints.
- Added `run_nested_sampler`, a controlled dynesty nested sampler for typed
  Gaussian likelihood summaries. It reports posterior summaries, evidence,
  diagnostics, seed, package version, and provenance without accepting raw
  user likelihood code or arbitrary YAML.
- Added a bounded local paper-mining loop via `run_paper_tool_mining_loop`.
  Each round processes the next 20 unread related papers, updates local-only
  loop state, and carries the ToolSpec/gap/implementation queue into the next
  round without treating mining output as scientific evidence.
- Added frontend Research Plan, Research Matrix, Evidence Graph, Fact Check, and
  Research Report cards in the chat tool UI, plus Paper Tool Mining, Tool
  Candidate Pool, Tool Mining Loop, Tool Ontology, Tool Gap Matrix, and
  Implementation Queue cards.
- Registered machine-readable observational-cosmology data products for the
  priority executable-adapter path:
  - DESI DR1 BAO public mean vector, covariance matrix, and bin-level product
    entry points from `CobayaSampler/bao_data`.
  - Pantheon+ public distance table, statistical/systematic covariance
    matrices, and CosmoSIS likelihood wrappers.
  - Planck 2018 public likelihood-code landing page and compressed
    distance-prior table source.
- Added frontend display for cosmology registry `data_products`, including
  product count, product roles, and a source link.
- Added targeted registry filtering so the chat agent can list only datasets
  relevant to the current cosmology prompt instead of always showing the full
  registry.

### Fixed

- **A2 Apophis blind-test case prompt rewrite (2026-05-21)**: explicit
  two-step instructions for `fetch_horizons_ephemeris` (ephemeris geometry)
  + `query_sentry_risk` (impact monitoring status). The previous wording
  caused over-conservative honest abstention in round-2 (LLM declined to
  call any tool); the new prompt makes the workflow unambiguous. Also
  relaxes `expect_pass` to accept the case where Sentry-II has removed
  Apophis from its risk table (factual reporting is valid, not "the
  number must be > 0").
- **Solar System M0 round-2 blind-test fixes (2026-05-20)** — fixes uncovered by
  a 20-case end-to-end blind test of the new module:
  - **P0 cross-module: agent-loop circuit breaker now covers hard-reject
    error_class.** `chat.py:_run_agent_loop` G3.4 mechanism extended:
    `_DATA_FETCH_TOOLS` now includes the 6 solar-system data-fetch tools
    (`query_mpc_orbit`, `fetch_horizons_ephemeris`, `query_sbdb_orbit`,
    `query_sbdb_close_approaches`, `query_sentry_risk`,
    `query_damit_shape_model`); previously they sat outside the disable gate
    and could be retried indefinitely. Added `_HARD_REJECT_ERROR_CLASSES =
    {"range_too_large", "missing_argument", "invalid_argument"}` short-circuit
    so soft/hard classification no longer misclassifies local tool rejections
    as soft via a `"too large"` substring match. This is a platform-wide
    safety improvement, not just solar-system.
  - **P1: A4 NEATM blind-test case now physically self-consistent.** Swapped
    `(1) Ceres` (12 μm 100 Jy + r=2.8 / Δ=2.0 forward-modeled to D≈423 km, not
    Ceres' real 940 km) for `(433) Eros` (12 μm 15 Jy + r=1.13 / Δ=0.46
    forward-modeled to D=16.7 km / p_V=0.22, matching Eros' real
    D≈16.84 km / p_V≈0.25).
  - **P2: MPC connector designation handling.** Added
    `_normalize_mpc_designations()` to expand input like `"(3200) Phaethon"`
    into multiple candidates (`"3200"`, `"Phaethon"`, `"(3200) Phaethon"`);
    `_query_mpc` now iterates variants × `target_type ∈ {asteroid, comet}`
    instead of issuing a single literal query. Provisional designations like
    `"1983 TB"` / `"2024 YR4"` are preserved intact.
  - **P3: Carvano+ 2010 SDSS classifier calibration.** Replaced previously
    inaccurate class centers (notably V class `r-i = -0.05`, which caused
    Vesta to be misclassified as O) with paper-accurate values
    (V class `r-i = -0.40`, reflecting the strong 1 μm absorption signature)
    plus per-color std fields for each class. Switched the scorer from
    Euclidean nearest-center to χ² (Mahalanobis-like, diagonal covariance),
    keeping `distance` / `all_distances` as backward-compat fields.
    Verified against Vesta / Bennu / Itokawa / Trojan prototypes.

### Changed

- Research-style observational-cosmology prompts are now routed through a
  plan-first workflow before executable compressed likelihood cells are run.
- Platform-expansion decisions now have a paper-mining path: abstract-only
  literature hits remain low-confidence, while high-confidence ToolSpecs require
  source spans from methods, tables, equations, appendices, or substantial full
  text.
- Long-running platform expansion can now be operated as repeated 20-paper
  mining rounds with explicit state handoff, rather than one-off subjective
  roadmap guesses.
- The first paper-mining-driven implementation target landed: cosmology
  data-product ingestion now validates registered public files before likelihood
  runners consume them.
- Paper-mining candidate pools can now switch arXiv live search from latest
  submissions to relevance-ranked queries, and the capability map now recognizes
  `run_nested_sampler` while keeping full external likelihood packages as a
  separate missing gap.
- Cosmology data-product loading now recognizes SN-style dimension-prefixed
  flattened covariance files, enabling Pantheon+/similar public covariance
  products to parse as real matrices instead of partial failures.
- Added `evaluate_chain_diagnostics`, a controlled posterior-chain diagnostic
  kernel for explicit samples. It computes R-hat, ESS, MCSE, HDI, and
  publication-readiness status without running a likelihood or inventing
  parameter constraints.
- `load_cosmology_data_product` can now expose registry-backed compressed
  Gaussian mean/covariance summaries for ACT, Planck, weak-lensing, SH0ES, and
  similar datasets even when there is no separate downloadable data-product
  file registered.
- `export_research_report` now returns a small report package with Markdown,
  BibTeX, dataset/citation summaries, and a reproducibility manifest instead of
  only a prose draft.
- Research-mode final replies now attach a Fact Check card; contradicted claims
  are replaced with a conservative tool-grounded summary.
- Updated cosmology registry documentation to distinguish machine-readable
  data-product provenance from executable posterior results. DESI and
  Pantheon+ remain external-likelihood/config-only until a runner consumes
  their public files directly.
- Updated README and architecture documentation to reflect the expanded
  data-product coverage for DESI, Pantheon+, and Planck.
- Removed 18 shell tool specs that had no dispatch path (M2) and 4 dead
  frontend pages with their pipeline node components (M3), so the tool
  catalog and page surface match what the agent loop and router actually
  execute.

### Guardrails

- Cosmology posterior, tension, AIC/BIC, and robustness claims still require a
  `publication_ready=true` chain result. Registry metadata, paper abstracts,
  config-only outputs, and raw data-product links are not sufficient to support
  numerical posterior claims.

## Recent Milestones

### Observational Cosmology Execution Layer

- Added a phase-1 compressed Gaussian likelihood runner for registered datasets
  with explicit mean vectors and covariance matrices.
- Added a robustness-matrix workflow for comparing runnable and non-runnable
  dataset combinations without fabricating posterior numbers for config-only
  combinations.
- Added validator coverage for cosmology posterior claims such as `H0`,
  `Omega_m`, `sigma8`, `S8`, tension, `nσ`, and fit-statistic claims.

### Cosmology Routing and Blind-Test Hardening

- Routed cosmology prompts through the registry/config/runner path instead of
  ad-hoc Python or generic literature summaries.
- Added prompt-scope handling for CMB-only, SN-only, DESI-only, pre-DESI BAO,
  weak-lensing, curvature, and neutrino-mass requests.
- Added English and Chinese exclusion handling so prompts like "do not include
  BAO/SN/weak lensing" are respected.
- Kept registry citations verbatim in model context to avoid accidental
  citation drift.

### Provenance and Data-Product Transparency

- Exposed archive/source provenance in chat tool panels, including dataset
  citations, source authority, archive versions, and machine-readable product
  availability where applicable.
- Kept synthetic, failed, empty, unavailable, config-only, and compressed-result
  states separate in both backend payloads and frontend display.
