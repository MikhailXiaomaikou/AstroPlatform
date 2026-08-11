# Standard Astro v0.2 Evaluation-Honesty Campaign — Full Report (incl. the Aug 7 re-review update)

> English translation of the Chinese original
> [`STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.zh-CN.md`](./STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.zh-CN.md).
> Numbers, statuses, and framings are verbatim; if the two versions ever
> disagree, the Chinese original governs.

Date: 2026-08-06, updated 2026-08-07 | Branch: `feat/lightweight-verification-v02` (includes the round-66 hardening)
Companion evidence: the desktop package "Standard-Astro v0.2 Formal Report Package 2026-08-06" (formal report revision 1.2, with all scoring CSVs and adjudications)

---

## 1. Plain-language summary (three paragraphs)

**Starting point**: yesterday's 1.0 report led with "system 1440/1440 perfect score vs bare model 839/1440." A line-by-line audit today found that the perfect-score side was a deterministic code self-check — the tasks were phrased in the system router's "native tongue," 7 of 8 tasks answered in milliseconds, and **the AI model was never in the loop**. This is not fabrication (every number is real), but presenting it as a model-capability comparison would be seen through instantly by any serious expert.

**Process**: the same 8 tasks were rewritten into natural researcher phrasing and re-taken (with the model genuinely in the loop), exposing a true score of 77.0% and two product defects; both were fixed the same day and re-verified to 90.4%. A subsequent real-web-path walkthrough for the demo then uncovered an "intent-triage / merge layer" that no evaluation had ever covered, plus two defects in it, which were closed on the spot. Every discovery, fix, verification, and residual is written into the formal report without whitewashing.

**End point**: what we now hold is an evaluation snapshot an expert can interrogate line by line — the honest A/B numbers (bare model 46.6% vs in-platform model-in-loop 90.4%), three zero-escape test records with confidence bounds, an error-budget table whose untested rows say "untested," and a 20–30 minute demo script. On Aug 7 the code went through a further sixty-six review rounds with 151 zero-false-positive findings; the full backend regression on the latest `HEAD` passes, but the natural-phrasing matrix and the five real web questions have not been re-run yet. **So the current state is not "all that's left is booking people" — it is "first re-run on the current code, then book people."**

---

## 2. Key numbers, before vs after

| Metric | Old framing (rev 1.0) | Honest framing (rev 1.2) |
| --- | --- | --- |
| Headline comparison | System 100% vs bare model 58.3% | Bare model 46.6% vs **in-platform model-in-loop 90.4%** (n=60, post-fix) |
| True identity of the system's perfect score | Undisclosed | Deterministic pipeline self-check (model not in the loop, millisecond-scale; now labeled at every occurrence) |
| Per-model "system gain +XX pp" | Five models at +25–54 pp each | **Entire table retracted** (the same code replayed 15 times — not model behavior) |
| Fake-evidence attack (strictest framing) | Not measured under this framing | In-platform model repeated the fake number **0/15**; bare model **15/15** |
| Hard escapes | "Zero escapes" | 0 events across three framings, reported with rule-of-three confidence bounds (0/60 → <5%, 0/30 → <10%, 0/12 → <25%) |
| Should-pass wrongly refused (false kills) | Not measured | Pre-fix 15/60 → post-fix **1/60** (the 1 residual is filed and accumulating evidence) |
| Terminal-state labeling accuracy | Not measured | 70% → **93.3%** |
| Evaluation coverage boundary | Not declared | Explicitly disclosed: the matrix measures the main loop; the web-side triage/merge layer was first covered by this walkthrough (§8.3) |

Per-model in-loop scores (post-fix, n=12 each): Sol 95.8% | Fable 94.4% | Kimi 94.4% | Luna 84.0% | Terra 83.3%.

---

## 3. Timeline and the discovery-disposition ledger

### Stage 1: assessing the old report
- The timing column of the 240-row scoring CSV (system side 0.004–0.066 s vs bare model 10–138 s) established that the model was not in the loop under the system condition.
- Side finding: the 240 samples had been through several "repair → re-run → merge" cycles (the repair file chain is in the `.local` audit directory), so "frozen, then passed in one shot" does not hold.
- Numeric spot checks: the ratio and error propagation in tasks 1/2 and the ACT 4.5σ all recompute consistently — the data themselves contain nothing fake.

### Stage 2: the natural-phrasing matrix (an exam the model actually takes)
- A new pre-registered task bank was frozen: same 8 tasks, same reference answers, with every "router codeword" stripped from the phrasing; a stratified analysis plan was pre-registered (split by `llm_calls` into a deterministic-channel stratum and a model-in-loop stratum, never merged into one headline).
- The evaluation script gained a model-call counter (via the chat loop's own test hook — zero product-code changes).
- Result: in 90 of the 120 system samples the model genuinely participated; the in-loop stratum scored 77.0%; two defects were exposed →

**Defect A (parse false-kill)**: the correlation parser only accepted equals-sign forms like `rho=-0.404`; the natural phrasing "a correlation of -0.404" — one preposition — killed the entire deterministic path. The model then computed the right answer itself and was blocked by the anti-fabrication gate. Task 1 failed 15/15.
**Defect B (terminal-state mislabel)**: the fake-evidence detector's noun list lacked "log," so the "pasted log" phrasing never triggered the deterministic refusal path; the model's content-level refusals were all correct (0/15 repeated the fake number), but the terminal state was mislabeled `full`.
**Scorer lesson**: all 22 raw "escape" flags were re-read one by one: 15 = the bare model genuinely repeating the fake number (real baseline failures); 7 = scorer-rule substring false positives on negated contexts ("cannot present as paper-ready result" was hit by a substring); **real system-side laundering: 0**. The pre-registered endpoint wording ("both conditions must be 0") was a drafting error; per the frozen text it is reported honestly as "not met," with the lesson recorded — no post-hoc reinterpretation.

### Stage 3: fixes and two-sided verification
- Fix A: the parser accepts natural phrasings; when only the uncertainty statement is missing, the controlled tool is opened to the model, and model-authored calls pass **echo validation** (every input number must appear in the user's own words, independence must be stated by the user; invented inputs are rejected before execution).
- Fix B: the noun list gained log/export (and Chinese 日志/导出); the deterministic refusal path is restored.
- No fix touched any hard-gate threshold (project red line: fix false kills without loosening gates).
- Verification: regression tests red-then-green; the 120 system samples re-run (in-loop stratum 90.4%, false kills 1/60, system-side flags cleared, refusal×15 restored); a strict 20-case blind re-test confirmed **zero hard-gate failures** (the defense was not loosened by the fixes); two full regression runs 3956/3958 all green.
- 1 residual of a new class (after a successful tool run, quoting the user's own input value gets withheld): fixing it means touching the B1 inline-data defense, so per "record first, don't gamble the wall for 1/60" it is filed and accumulating evidence.

### Stage 4: demo pre-flight walkthrough (the real web path)
- **Major discovery**: the real HTTP chat route has an "intent-triage + multi-specialist merge" layer above the main loop — **never covered by any evaluation or blind test**. Two defects measured live: deterministic small tasks were fanned out to several specialists that re-ran literature tools redundantly (experiment 1 measured 170.7 s); the merge reply gate withheld correct receipts the main loop had already produced (experiment 2 withheld entirely).
- Closing fix: following the triage layer's existing "collapse fast-path" precedent, deterministic small tasks collapse to a single main-loop pass. Red-then-green tests; after the fix the 5 demo questions re-tested against the real endpoint all pass: **0.1 s / 0.0 s / 73.7 s / 0.0 s / 21.0 s**.
- Incidental cleanup: the runtime library gained the missing provenance_records migration (warning source eliminated).
- Report §8.3 discloses the measurement boundary honestly: "every matrix number in this package measures the main loop; the triage/merge layer is first covered by this walkthrough," with a matching row in the error-budget table.

### Stage 5: demo assets
- Demo script: `docs/research/STANDARD_ASTRO_V02_EXPERT_DEMO_SCRIPT_2026-08-06.zh-CN.md` — a numbers card (what may and may not be said), startup commands, a 10-minute pixel self-check list (the API layer is auto-walked; per-screen frontend checks stay manual), 22 minutes of per-task talking points, 6 on-the-spot risk responses, and a post-demo 24-hour action list.

---

## 4. Methodology assets that now persist (the four precision instruments)

| Asset | Location | Purpose |
| --- | --- | --- |
| Error-budget table v1 | Survey report §8.1 | One row per error source: measured ones carry a number + sample size, unmeasured ones say "untested"; external calibration (postdoc blind review) is listed separately as irreplaceable |
| Confidence-bound convention | Every "zero events" statement | Rule of three: 0/N → 95% upper bound 300/N%; spec-sheet voice replaces marketing voice |
| Should-pass false-kill corpus | `docs/research/...should_pass_corpus*.json` | 15 pre-fix / 1 post-fix; re-run paired with blind-test group B on every gate change, so both error directions stay visible |
| Holdout commitment (pending independent rebuild) | `...holdout_commitment.json` | The plaintext candidate set is treated as leaked (it entered PR history) and retired as burned; the new task set must be generated outside the dev/debug loop by an independent custodian, the repo pre-registers only a SHA-256, and unsealing happens exactly once with an unseal log |
| Paraphrase-variant set (frozen, not yet run) | `...paraphrase_variants.json` | 8 tasks × 4 phrasings (formal English / colloquial English / Chinese / telegraphic), to measure "response flatness" |
| Both pre-registrations + all scoring CSVs | Report package `evidence/` directory | 3×240 rows, recomputable per sample |

---

## 5. Code ledger (6 campaign commits + 39 follow-up hardening commits)

```
d3fbda9 docs: §8.3 live-path disclosure + demo script
49725cb fix:  triage-desk collapse (deterministic small tasks go straight to a single main loop)
d4ef745 docs: report revision 1.2 (fix-verification re-run)
b028f71 fix:  natural-phrasing parsing + fake-evidence detection + echo validation
bf0f1d6 docs: report revision 1.1 (retract the misleading framing)
522a45e feat: natural-phrasing matrix evaluation (llm_calls stratification)
```

The campaign-stage product changes were mainly `prompt_routing.py`, `loop.py`, and `orchestrator.py`. The subsequent sixty-six review rounds extended to `source_packet_resolver.py`, `scalar_derivation.py`, `scalar_verification.py`, `sample_export.py`, the scorer, `chain_export.py`, the inference bridges, the multi-agent merge layer, and the holdout custody flow; the old claim that "product changes touch only 3 files" no longer holds.

### Aug 7 sixty-six-round hardening summary (151 findings, zero false positives)

| Round | What was tightened |
|---:|---|
| 1 | Rounded substrings must not impersonate exact matches; a missing locator must not fall back to the top of the text; explicit chain requests take the heavy path |
| 2 | Weighted means reject zero uncertainties; compound locators must match in full |
| 3 | Exponent suffixes, dangling sources, unverifiable matrix attribution, and negation scope |
| 4 | Lossy six-digit rendering, per-claim locators, leading operation words, and chain-cleanup renewal fail-open |
| 5 | Label–value permutation, cache-key identity, and multi-paper ambiguity |
| 6 | Short-label word boundaries, uncertainty binding, and echo quantity pairing |
| 7 | Full echo binding of operation/units/sources/locators/correlation pairs, and pinned-DNS connections |
| 8 | Negation scope after repeated commands; table windows must not spill into the next table |
| 9 | Singular nonzero covariance, target-row binding, and row-name boundaries |
| 10 | Values must follow the target label; unrelated trailing digits must not become uncertainties; generic multi-word labels must match in full |
| 11 | Small-scale positive-definite covariance must not be false-killed by an absolute tolerance; no claim may borrow the next field's value |
| 12 | Small-scale non-PSD covariance must close; last-item fields, Equation boundaries, and table-caption label scope |
| 13 | Small-magnitude echo values and covariance diagonals must match at relative scale; the Planck–SH0ES anchor comparison pins the Planck baseline explicitly; arXiv version identity is preserved; the scorer intercepts natural-language and rounded forms of forbidden outputs |
| 14 | arXiv version identity carried through prompt routing, normalization, and evidence requests; multi-agent merge preserves validation schema v2, valid evidence receipts, limits, and hard blocks |
| 15 | Decimals inside full scalar assignments must not masquerade as bare arXiv IDs; the plaintext holdout candidate set is burned and retired, the repo keeps only the irreversible commitment and the independent-rebuild rules |
| 16 | "Independence must not be assumed" cannot be read as an independence statement, and the echo guard rejects it too; small-scale covariance symmetry is checked at the matrix's own scale |
| 17 | Postponed negations like "independent errors are not assumed" also close the independent-error path; source fields must not borrow later calibration values across comma-delimited contrast clauses |
| 18 | Explicit baselines in sample-cosmology comparisons constrain both manifest attribution and numeric distance computation; source claims must not borrow values across ordinary comma-separated subsequent measurement fields |
| 19 | Values explicitly negated by the source must not gain exact support via token co-occurrence; postposed `never` on independent errors closes both the direct and echo paths; comma boundaries distinguish new measurement fields from scientific parentheticals without false-killing legitimate appositives |
| 20 | Negation grammar completed with contracted and fused forms (`shouldn't`, `can't`, `cannot`), covering the independent-error direct/echo paths and source measurement assignments |
| 21 | Source bounds and inequalities must not impersonate exact assignments; receipt boundary statements become backend-generated only, echo smuggling is rejected; a binary operation seeing a third complete quantity is ruled ambiguous instead of constructing a must-fail call |
| 22 | The noun `sample` in observational contexts no longer mis-triggers the heavy sampling path; untrusted PDF parsing moves into a kill-able isolated process with CPU, memory, wall-clock, page, and output caps |
| 23 | Source units must normalize-match within the same measurement field and enter the cache identity; negation/exclusion after a value also blocks exact verification; prompt quantities the user explicitly discards must not enter direct or model echo calls |
| 24 | When a later sentence names and discards an earlier quantity, only the named target is rejected (article-based counter-cases prevent overkill); every input quantity must bind to a declared external or user source — empty/dangling refs must not inherit the others' `verified_exact` |
| 25 | Postposed negation on `fit/fitting` must not mis-trigger the heavy path; lowercase label `a` is fully distinguished from the English article; product gains an exact second-order variance term under explicit independence while correlated inputs close for lack of higher moments; source parenthetical commas must not erase a governing negation |
| 26 | Ratio uncertainty is explicitly downgraded to a first-order delta-method linearization — receipts carry method, limits, and assumptions instead of posing as exact deterministic results; postposed heavy negation admits controlled noun modifiers, with affirmative counter-assertions preventing overkill |
| 27 | A correlation coefficient explicitly discarded after the values must not keep overriding the independence model; operation alternatives under postposed negation must not manufacture fake ambiguity or squash a valid lightweight call |
| 28 | Mixing external source quantities with user-fixed comparators closes the blanket verification claim, preventing paper evidence from spilling onto the comparator; source labels are searched at every occurrence, with a "later same-label values must agree" reverse guard preserving conflict detection |
| 29 | Pre-label governing negations like "It is not true that / We cannot conclude that" enter measurement-assignment scope; later same-label digits count as conflicts only with real measurement-assignment syntax, so Section/Equation/citation numbers no longer false-kill |
| 30 | Dimensionless exact verification gains an adjacent physical-unit guard, so unknown out-of-vocabulary units (e.g. eV) can no longer slip through; infinitive negations like "is not to be used" cover quantities, source measurements, correlation coefficients, and postposed heavy intent |
| 31 | Pre-label negation may omit `that` — "The data do not support alpha = ..." no longer gains exact verification; source units must sit adjacent to the matched measurement pair and cannot borrow from a later calibration quantity in the same sentence |
| 32 | Conditional or hypothetical source statements ("If / Suppose / Assuming alpha = ...") cannot serve as measured evidence; affirmative source-reporting sentences are preserved by counter-assertions |
| 33 | "alpha could/would be ..." and post-pair `if` conditions must not impersonate measurements; do-support negations like "fit does not need to be performed" no longer mis-trigger heavy routing or drop valid lightweight calls |
| 34 | Perfect-modal source values ("could have been / might have been measured as") must not impersonate measurements; the non-conditional idiom "if anything" no longer false-kills legitimate exact evidence |
| 35 | The "if anything" exemption narrows to explicit measurement qualifiers; genuine conditionals like "if anything in the calibration changes" keep closing exact verification |
| 36 | User-supplied quantities declared locally in the prompt no longer inherit the same sentence's paper source; existential negations like "There is no evidence that / no support for ..." must not reverse-verify the negated proposition |
| 37 | Model fallback calls preserve prompt-local user-source declarations; infinitive existential negations like "There is no evidence to support ..." must not verify the negated proposition; ordinary prose needs explicit assignment/reporting syntax for exact verification while bounded bare fields in structured table rows stay usable |
| 38 | Subject-fronted evidence/support/basis/indication negations like "No evidence supports ..." join pre-label proposition scope and must not reverse-verify the negated measurement |
| 39 | Negations weaker than "no evidence" (`insufficient/inadequate evidence/support`) also close exact verification; derived operations on finite inputs that overflow to non-finite values close with an actionable `non_finite_result` abstention before a receipt is built |
| 40 | Modal negations like "A fit need not be performed" no longer mis-trigger the heavy path or squash complete lightweight calls; "not enough evidence/support/basis" also closes source exact verification |
| 41 | Qualified evidence/observation negations like "The available evidence does not support ..." keep closing exact verification; non-modal perfects "has/have/had been measured as" are preserved as genuine affirmative measurement syntax, with negation counter-assertions preventing gate loosening |
| 42 | mock/simulated/synthetic/fiducial/illustrative configuration or example assignments must not impersonate observed measurements; counter-cases ensure a real observed measurement explicitly reported alongside a mock comparison still verifies |
| 43 | Common configuration nouns in "For the fiducial cosmology/configuration/setup ..." join the same non-measurement-context guard and cannot gain observational attribution via a bare equation |
| 44 | Configuration qualifiers baseline/reference/benchmark likewise must not impersonate observed measurements; sentence-initial "No fit is necessary" is recognized as qualifier negation of heavy intent, preserving the complete scalar direct call, with scoped counter-cases like "No approximations, run a fit" preventing over-negation |
| 45 | Assumed/adopted configuration values ("assumed/adopted cosmology/model") join the non-measurement context and cannot gain observational exact verification via a bare equation |
| 46 | default/nominal/fixed/input cosmology configuration values close exact verification; configuration nouns cosmology/model/setup/scenario become a generic guard independent of an adjective whitelist, while data/sample/catalogue still require an explicit configuration qualifier, avoiding unbounded synonym chasing |
| 47 | `under/within` join configuration scope; only explicit affirmative measured/reported/estimated/found predicates may override a configuration prefix, preserving results like "In the best-fitting model, alpha has been measured as ..." while bare equations, copulas, and `given` assignments stay fail-closed |
| 48 | `with` and sentence-initial `given` configuration scope close bare-assignment verification; the scalar receipt's standardized difference stays an ordinary dimensionless derived value and is no longer minted into a Gaussian σ significance without distributional assumptions; Kimi 0.26 supports prompt passing via argv only, so the bridge enforces a 120 KiB UTF-8 argv cap before process creation and rejects oversized requests explicitly |
| 49 | User-source declarations support both a directed suffix on the current quantity and a `both/all values` collective scope after the last item, using the nearest preceding fragment so A's postposed declaration is not mis-assigned to B; source values with postposed "was assumed/adopted/fixed as input" close exact verification |
| 50 | Source values with postposed "used/set/taken as" plus configuration roles fiducial/baseline/default/reference/benchmark/nominal and nouns value/input/configuration/setup/parameter uniformly close exact verification |
| 51 | Result-verbs and ordinary connectives like `around/of` no longer bypass the H0/Δχ² forbidden-output detection; the hidden verification receipts of V02_01–04 only prove backend verification and cannot substitute for source attribution in the user-visible reply |
| 52 | Source-text preposed `set/fix/adopt/use` configuration predicates must not impersonate measurements; H0/Δχ² forbidden-output detection becomes label–value structural recognition; same-turn publication-ready results take precedence over earlier exploratory gaps; gap-receipt dependencies derive from the actual request instead of a hard-coded EDE-specific list |
| 53 | V02_03's visible reply must give both `arXiv:2503.14452` and `Equation 42`; the broad product name `ACT DR6` alone no longer impersonates a precise source locator |
| 54 | Source-text preposed `choose/select/impose` in present, past, and perfect forms must not pass a human-chosen model input off as a paper measurement |
| 55 | Source-text preposed `hold/keep` in all word forms, and postposed `held/kept fixed`, are treated as fixed configuration and must not pass a fit-locked parameter off as a paper measurement |
| 56 | V02_05's and V02_07's full source scores require precise source attribution to appear in the user-visible reply first; hidden tool payloads, prompt echoes, or capability-gap receipts only verify a visible citation and no longer substitute for it |
| 57 | Existential negations like "There is no need for a fit" preserve the complete scalar direct call; "Gaussian/normal prior for/on alpha = ..." is treated as prior configuration, not measurement; V02_07 gains a value-first, H0-label-later result-escape guard |
| 58 | Copular/equative prior sentences like "The Gaussian prior is alpha = ..." keep closing measured exact verification; infinitive negations like "There is no need to run/perform a fit" preserve the lightweight scalar direct call |
| 59 | V02_01's ratio and uncertainty must appear in the visible reply and land via the scalar receipt; V02_06's full source score requires visible `Pantheon+` and the 2.26 registry coverage; direct infinitives like "There is no need to fit the data" also preserve lightweight routing |
| 60 | V02_02–05's numeric, uncertainty, end-to-end, and risk scores must be triggered by complete results in the user-visible reply; hidden scalar receipts and registry anchor facts can only ground verification, not substitute for missing answers |
| 61 | Copular configuration declarations like "alpha = ... is the fiducial/default/reference value" must not impersonate measurements; "no need to perform any/run another fit" preserves lightweight routing; V02_06's end-to-end full score requires visible `Pantheon+` identity and 2.26 coverage |
| 62 | Postposed configuration semantics first step over an adjacent physical unit, so "H0 = ... km/s/Mpc was assumed/held/fiducial" cannot verify as measured; V02_07 gains the value-first, Δχ²-label-later result-escape guard, with an Equation-number counter-case preserved |
| 63 | Chain-file persistence moves after authoritative result normalization, so runs demoted to blocked by the publication or reproducibility gates no longer leave a downloadable posterior; V02_05's end-to-end and low-risk full scores require both anchor errors 0.54 and 1.04 to be visible |
| 64 | Evaluation samples keep a re-verifiable signed projection of the full scalar receipt, and the scorer recomputes the digest before granting source/numeric points, rejecting tampering; V02_03's end-to-end full score requires both the difference and the significance to be visible; chain formatting, database, and object uploads move out of the async request loop |
| 65 | Postposed configuration roles like "was chosen/selected as the fiducial/baseline value" must not verify as measured; label-first result predicates like "H0 peaks at ..." join the forbidden-output gate; postposed negations like "fit can be skipped/may be bypassed" preserve the complete lightweight scalar route |
| 66 | Postposed constant-parameter semantics like "was kept constant / remained constant" are treated as configuration input and must not verify as measured; natural result qualifiers like "H0 is near ..." join the forbidden-output gate |

Two chore commits used only to re-trigger GitHub Actions after infrastructure failures, and a CI wrap-up fix that releases the test suite's own async resources, are not counted among the sixty-six behavioral rounds. All of these fixes are pinned by targeted regression tests; they did not automatically rewrite the Aug 6 scoring CSVs, and they do not constitute fresh end-to-end demo evidence. After round 66 the full regression run is 4199 passed, 8 skipped, 59 deselected, 69.73% coverage, exiting normally after printing its stats.

---

## 6. Current state and next steps

**Ready**: the report package (revision 1.2, as the Aug 6 snapshot) | the demo script | targeted regression tests for all sixty-six hardening rounds | stratified scoring and the error budget.

**Must be redone before any demo**: re-run, on the current `HEAD`, the v0.2 focused tests, the natural-phrasing matrix, and the five real HTTP/frontend questions from the script; confirm numbers, `source_status`, terminal states, and receipt cards screen by screen. Until then, the old timings and pass rates may only be labeled as historical snapshots.

**Only you can do**:
1. First complete the current-code re-verification per §1 of the expert demo script and save the results.
2. Only after it passes, book people — cold-email arXiv authors / CosmoCoffee / department coffee hour / Bluesky.
3. On demo day still do the 15-minute preparation and per-screen pixel self-check; a historical re-verification cannot replace the same-day check.

**Queued (not blocking the demo)**: the paraphrase-variant batch | the postdoc blind-review pack (must use model-in-loop framing) | systematic matrix coverage of the triage/merge layer | drift control charts (data accumulating automatically) | an LLM second scorer (deliberately after the human blind review) | evidence accumulation for the residual false kill.

---

## 7. The campaign's one-sentence lesson

**What persuades is never the perfect score — it is "we know what we have not tested."** Every retracted framing, every disclosed defect, and every "untested" cell in the error budget in this report will win over a real observational cosmologist more surely than 1440/1440 ever could.
