# Blind Research Testing Log

This document records the testing protocol that emerged from the recent
research-oriented Chat UI test rounds.

It intentionally does **not** preserve earlier per-round scientific claims,
paper-specific results, API keys, deployment details, or transient bug notes.
Those details were explicitly cleared from working context. What remains here
is the reusable test method.

## Purpose

Standard Astro should be tested as a research platform, not as a prompt-only
paper-reproduction chatbot.

The core question for each test is:

> Given only a normal research request, can the platform choose appropriate
> data, methods, tools, evidence, and limitations without being handed the
> paper title, conclusion, or answer?

The test is therefore a **paper-derived blind test**:

1. The tester reads a real paper outside the platform.
2. The tester extracts the research direction and method, but hides the paper
   title, arXiv ID, DOI, conclusion, and final numbers from the Chat UI.
3. The Chat UI receives only a normal research-style prompt.
4. The platform response is compared offline against the hidden paper record.

## What Is Preserved From Recent Test Rounds

The following process-level findings are now part of the standard protocol:

- Do **not** tell the platform that the task is a reproduction audit.
- Do **not** paste the paper title, arXiv ID, DOI, abstract, or final result
  into the Chat UI unless the specific test is about literature lookup.
- Do **not** leak key numerical results, significances, posterior constraints,
  fitted slopes, or tension values in the prompt.
- Do **not** reuse chat history between papers.
- Do **not** manually correct the assistant mid-turn.
- Do record the full tool chain, evidence/fact-check state, final answer,
  warnings, silent failures, and unsupported claims.
- Do compare the platform output to the paper outside the platform.
- Do classify failures by capability gap, routing error, unsupported claim,
  hallucination, or UI/process failure.

This method is meant to evaluate whether the system can perform research-like
reasoning from available tools, not whether it can recognize a paper from
metadata.

## Per-Paper Hidden Record

For each paper, the tester keeps a hidden record that is never pasted into the
Chat UI during the blind run.

Recommended fields:

| Field | Meaning |
|---|---|
| `paper_id` | Internal identifier for the test record |
| `title` | Paper title |
| `arxiv_id` | arXiv identifier, if available |
| `doi` | DOI, if available |
| `year` | Publication or preprint year |
| `authors` | First author or author list |
| `anomaly_type` | Example: H0 tension, S8 tension, early dark energy, modified gravity |
| `research_question` | What the paper tries to test |
| `datasets` | Data products, surveys, likelihoods, tables, or covariances used |
| `model_space` | LCDM, wCDM, w0waCDM, modified gravity, EDE, etc. |
| `method` | Likelihood, estimator, sampler, robustness matrix, model comparison, etc. |
| `required_outputs` | Posterior constraints, tension, Bayes factor, fitted relation, plots, tables |
| `paper_conclusion` | Hidden conclusion used only for offline comparison |
| `key_numbers` | Hidden numerical results used only for offline comparison |
| `public_data_status` | Whether data, code, chains, covariance, or likelihood are public |

## Blind Prompt Rules

The Chat UI prompt should look like a normal research request.

Allowed:

- Scientific topic.
- Data families the researcher wants to use.
- Broad method goal.
- Desired output type.
- Constraints that a real researcher would specify before running the study.

Forbidden:

- Paper title.
- arXiv ID.
- DOI.
- Exact paper conclusion.
- Exact final numbers.
- Phrases such as "reproduce this paper", "audit this paper", or "test whether
  the platform can match this paper".
- Diagnostic instructions that reveal the hidden answer.

Example style:

```text
I want to examine whether current BAO, supernova, and compressed CMB data
support a deviation from LCDM in a time-varying dark-energy model. Please build
an auditable analysis plan, run any available controlled likelihood workflows,
and clearly separate runnable results from missing likelihoods or data gaps.
```

## Fresh Chat Requirement

Each paper must start from an isolated chat.

Minimum checks:

- No previous paper messages in the visible conversation.
- No old tool cards reused in the current answer.
- No old numerical values carried into the new answer.
- If the UI supports session IDs, confirm a new or empty session before sending
  the prompt.

If isolation fails, the run is invalid and must be repeated.

## What To Capture From Chat UI

For every run, record:

| Field | Meaning |
|---|---|
| `prompt_summary` | Short description of the blind prompt |
| `tool_runs` | Ordered list of tools/cards the platform executed |
| `data_choice` | Datasets or archives selected by the platform |
| `method_choice` | Statistical or scientific method chosen |
| `result_status` | Complete result, partial result, scope gap, or failure |
| `final_answer_summary` | What the assistant ultimately claimed |
| `warnings` | UI warnings, fact-check warnings, provenance warnings |
| `evidence_status` | Whether strong claims had evidence graph support |
| `fact_check_status` | Verified, partial, unsupported, contradicted, or absent |
| `silent_failure` | Whether the assistant stopped without a useful answer |
| `withheld` | Whether the answer was blocked by a guardrail |
| `internal_leak` | Whether internal tool names, XML tags, or debugging markers leaked |

## Offline Comparison

After the Chat UI run, compare the platform output to the hidden paper record.

The comparison should answer:

- Did the platform choose the right kind of data?
- Did it choose a method compatible with the paper's method?
- Did it run actual tools, or only produce a plan/config?
- Did it distinguish executable results from scope gaps?
- Did its result direction agree with the paper?
- Were numerical claims supported by a current-turn tool result?
- Did it overstate compressed or preliminary results as full likelihood results?
- Did it invent citations, datasets, methods, or numbers?
- Did it fail honestly and usefully when the required capability was missing?

## Scoring Categories

Use the following categories consistently.

### A: Research-Grade Pass

- Correct data family.
- Correct or scientifically close method.
- Tool-supported result.
- Claims linked to evidence/fact-check/provenance.
- Result direction agrees with the hidden paper record.
- Differences are explainable by data coverage, approximation level, or model
  scope.

### B: Partial Pass

- Research direction is correct.
- Platform runs a relevant approximation or subset.
- Limitations are stated clearly.
- No unsupported numerical claims.
- The answer is useful but not paper-level.

### C: Honest Failure

- Platform identifies missing data, covariance, likelihood, runner, sampler, or
  table extraction capability.
- It does not invent final numbers.
- It gives a clear next experiment or missing-tool explanation.

### D: Tool Route Error

- Platform chooses the wrong dataset, model, or method.
- It may still avoid hallucination, but the scientific route is not the one the
  paper requires.

### E: Severe Failure

Any of these:

- Unsupported numerical results in the final answer.
- Old chat context contaminates the answer.
- Paper metadata or abstract is treated as measurement data.
- Citation, DOI, arXiv, dataset, formula, or constant is fabricated.
- Silent termination.
- Internal markers or tool protocol leak into the user-facing answer.
- A config-only result is presented as a posterior or publication-ready result.

## Failure Categories

Use one or more labels per failed run:

- `data_unavailable`
- `likelihood_missing`
- `covariance_missing`
- `runner_missing`
- `sampler_missing`
- `wrong_dataset_routing`
- `wrong_model_space`
- `unsupported_numeric_claim`
- `source_mismatch`
- `formula_or_constant_mismatch`
- `config_only_overclaimed`
- `compressed_result_overclaimed`
- `citation_mismatch`
- `old_context_contamination`
- `silent_termination`
- `internal_marker_leak`
- `ui_process_failure`
- `honest_scope_gap`

## Batch Testing Cycle

The standard cycle is:

1. Select papers in batches.
2. Read each paper outside the platform.
3. Create hidden paper records.
4. Generate blind prompts.
5. Run each prompt in a fresh Chat UI session.
6. Save tool and answer records.
7. Compare offline against hidden paper records.
8. Assign scores and failure labels.
9. Write a failure report.
10. Fix platform issues.
11. Re-run affected papers.
12. Commit only non-hardcoded general fixes.

## Observed Problem Catalog

This catalog records the classes of problems exposed by recent Chat UI blind
testing and local research-workflow tests. It is intentionally written as a
general engineering checklist, not as a list of hidden paper answers.

### 1. Test Protocol Problems

| Problem | Impact | Required control |
|---|---|---|
| The platform was sometimes told that a run was a reproduction, audit, or test. | The model optimizes for matching a known paper instead of doing research. | Convert each paper into a normal research prompt before Chat UI execution. |
| Some prompts leaked too much paper identity or method-specific framing. | The platform may retrieve or infer the target paper instead of independently choosing tools. | Keep title, arXiv ID, DOI, final numbers, and conclusion outside the prompt. |
| Some tests were not fully run through the frontend Chat UI. | Backend-only success did not prove user-visible behavior. | For product validation, run the prompt in Chat UI and capture visible cards, warnings, and final answer. |
| Some tests mixed process debugging with scientific blind testing. | Results became hard to interpret. | Separate platform diagnosis runs from clean blind research runs. |

### 2. Chat Session And Context Isolation Problems

| Problem | Impact | Required control |
|---|---|---|
| New-chat isolation was not always reliable. | Old messages, old tool cards, or old numbers can contaminate a new paper run. | Verify fresh session state before every blind prompt. |
| Old numerical results could leak into a later answer. | False positive: the platform appears to solve a task by reusing earlier context. | Strong claims must be tied to current-turn tool-run IDs only. |
| Local storage or session IDs could preserve stale chat state. | Automated tests may think they started fresh when they did not. | Add a testable fresh-chat-ready state and clear session IDs on forced fresh chat. |

### 3. Silent Or Incomplete Answer Problems

| Problem | Impact | Required control |
|---|---|---|
| Fully blank final answer after tool use. | User sees tool cards but no explanation or conclusion. | End-of-turn fallback must summarize what ran, what failed, and what remains unsupported. |
| Mid-sentence termination. | The answer looks broken and may omit the actual limitation. | Final-answer sanity check should catch dangling colons, incomplete bullets, and empty assistant messages. |
| Withheld answer with too little process explanation. | The guardrail may be correct, but user cannot understand what happened. | Honest failure cards should include executed tools, usable data, missing data, and next step. |

### 4. Internal Protocol Leakage

| Problem | Impact | Required control |
|---|---|---|
| Internal XML-like markers or tool protocol tags leaked into final prose. | User sees implementation details instead of a clean scientific explanation. | Strip internal tags before rendering final answers. |
| Internal tool names appeared in user-facing guardrail text. | The message becomes less understandable and exposes implementation internals. | Rewrite guardrail output in user-facing language while keeping raw detail in diagnostics. |
| Tool-count banners did not always match visible tool cards. | Users cannot reconcile what the system says with what they can inspect. | Count only user-visible cards, or show a tooltip explaining hidden/internal events. |

### 5. Synthetic Fallback Problems

| Problem | Impact | Required control |
|---|---|---|
| The model attempted synthetic data when real measurement data were missing. | Risk of invented samples, slopes, p-values, or posterior claims. | Synthetic outputs must remain unciteable and must not support final scientific claims. |
| Synthetic data were sometimes used after a failed extraction or failed fit. | The system may produce plausible but false research results. | If the requested measurement table or likelihood is absent, return scope gap instead of synthetic result. |
| Synthetic demo mode was not always clearly separated from real analysis. | User may mistake demonstration code for observation-backed inference. | Demo outputs require an explicit demo label and should not be mixed with Results. |

### 6. Literature Search Problems

| Problem | Impact | Required control |
|---|---|---|
| Literature search sometimes returned weakly related or unrelated papers. | The assistant may route to irrelevant datasets or methods. | Use stricter topical relevance thresholds and show query/relevance diagnostics. |
| Repeated literature searches could drift in topic. | Later searches may dilute or contradict earlier relevant results. | Deduplicate queries by hash and preserve the highest-relevance evidence set. |
| Abstract-level results were sometimes treated as sufficient context for measurement claims. | Abstracts can support background but not fitted values or posterior constraints. | Measurement claims require extracted tables, registered datasets, or executable likelihoods. |

### 7. Literature Table Extraction Problems

| Problem | Impact | Required control |
|---|---|---|
| Table extraction sometimes returned no rows, truncated rows, or raw tables without usable column mapping. | The platform cannot compile the needed sample even when the paper is relevant. | Expose raw table status, mapping confidence, blocked reason, and required manual mapping. |
| Extracted table fields could be incomplete or inconsistent across runs. | Same prompt can produce different downstream capability. | Stable cache keys, extraction version hashes, and provenance timestamps are required. |
| Some columns were abbreviated or visually elided in UI. | Important parameters may be hidden or misread. | Preserve exact column names in raw payload and show full names on hover/copy/export. |
| Raw paper tables were sometimes confused with normalized measurement rows. | A fit may run on invalid or unmapped strings. | Only typed, normalized rows can feed fit tools. Raw-only tables need a "needs mapping" state. |

### 8. Line-Relation And Measurement-Fit Problems

| Problem | Impact | Required control |
|---|---|---|
| A dedicated fit tool could fail to consume measurement rows produced by table extraction. | Real extracted data exist, but the fit step reports zero usable rows. | Align cache keys and schemas between extraction and fit tools. |
| Python/MCMC analyses could produce real fits but be rejected by guardrails that only trusted a specific tool. | False negative: valid analysis is blocked. | Publication readiness should depend on evidence chain, diagnostics, seed, data hash, and citation, not only tool name. |
| Different fitting paths could report different uncertainty models. | Users cannot tell whether OLS, Bayesian XY-error, linmix, emcee, or another method was used. | Every fit card must show method, likelihood, priors, error model, seed, diagnostics, and citation. |
| Relation direction or units could be wrong. | A line in log L versus log FWHM can be confused with FWHM versus log L. | Fit tools must expose x/y definitions, units, pivoting, and formula convention. |

### 9. Cosmology Parameter And Constant Problems

| Problem | Impact | Required control |
|---|---|---|
| User-specified constants or cosmology presets could be parsed inconsistently. | Distance, luminosity, posterior, or comparison results become irreproducible. | Parse constants into structured assumptions and show them before calculation. |
| A user assumption could be written as if it were measured by the platform. | The source of a number becomes misleading. | Evidence graph must mark `user_assumption` separately from `tool_run`. |
| Formula or unit conventions could be underspecified. | Results may differ from a paper for avoidable reasons. | Cards must display formula, unit convention, rest frequency, distance definition, and conversion assumptions. |

### 10. Cosmology Likelihood And Research Runner Problems

| Problem | Impact | Required control |
|---|---|---|
| Config-only likelihood builders can look like real inference. | Users may overread generated configs as posterior results. | Config-only cards must state "no posterior run yet" and cannot support parameter claims. |
| Compressed-likelihood results can be overstated as full likelihood results. | Scientific claims become too strong. | All compressed outputs must be labeled "compressed-likelihood preliminary". |
| Missing covariance or likelihood metadata blocks publication-ready status. | The system may have a dataset name but not enough information to infer. | Dataset registry entries need version, citation, covariance, units, model applicability, and download source. |
| Robustness matrices can contain non-runnable cells. | Empty cells may be mistaken for negative scientific results. | Matrix UI must separate runnable, config-only, missing-likelihood, and failed cells. |

### 11. Evidence Graph And Fact Verification Problems

| Problem | Impact | Required control |
|---|---|---|
| Strong numerical claims were not always tied to current-turn evidence. | Unsupported results may enter final prose. | Claim -> result -> tool-run -> dataset/table -> citation must be required for strong claims. |
| Paper metadata or abstracts could support background but not measurement values. | The platform may appear cited while still unsupported. | Fact verifier must distinguish paper metadata, extracted table, dataset registry, and tool-run support levels. |
| Guardrails could be too strict. | Valid tool-backed results may be withheld. | Verify evidence completeness before blocking; allow safe summaries and limitations even when headline results are blocked. |
| Guardrails could be too loose. | Unsupported ranges, author-year claims, or relation statistics may pass. | Expand claim detection for ranges, correlations, relation coefficients, p-values, and unsupported author-year citations. |

### 12. Scientific Scope Gap Problems

| Problem | Impact | Required control |
|---|---|---|
| Some requested workflows require data or likelihoods not yet registered. | The correct answer is an honest scope gap, not a fabricated result. | Research plan must classify each requirement as runnable, partial, config-only, or unavailable. |
| Some paper-class tasks require full external likelihoods, not compressed approximations. | A compressed runner may be useful but insufficient. | Report approximation tier and recommend full Cobaya/CosmoSIS follow-up when needed. |
| Some research questions need table extraction plus domain-specific normalization. | Generic literature search is not enough. | Paper-to-tool mining should drive new table mappers, likelihoods, and diagnostics. |

### 13. Frontend Visibility Problems

| Problem | Impact | Required control |
|---|---|---|
| Users could not always see the full process before failure. | Failure looks arbitrary or unhelpful. | Display research plan, executed tools, missing cells, evidence, and fact-check results even on failure. |
| Warning badges were sometimes vague. | Users cannot tell which claim is unsafe. | Hover details should show original claim, missing evidence link, safe rewrite, and suggested tool/data fix. |
| Tool cards could contain useful data that final prose ignored. | User may not know a usable table or plot exists. | Final answer should summarize usable artifacts and where to inspect/export them. |

### 14. Operational And Deployment Problems

| Problem | Impact | Required control |
|---|---|---|
| A passing local state can differ from committed CI state if files are hidden or locally skipped. | Local tests pass but GitHub fails. | CI fixes must be committed from normal tracked files; avoid relying on hidden local-only changes. |
| Server-side model keys can be configured on the wrong service or environment group. | The frontend may show a provider, but backend reports no configured backend. | Backend health/status should explicitly report server-side provider availability without exposing secrets. |
| Render deployment state can lag GitHub CI state. | Dashboard failure may refer to an old commit or old env. | Record commit SHA, CI run ID, Render service, and health endpoint for each deploy check. |
| Public frontend/backend URLs can be confused. | A 404 on the wrong hostname looks like a deployment failure. | Keep canonical URLs documented and verify health endpoints directly. |

### 15. Documentation And Reporting Problems

| Problem | Impact | Required control |
|---|---|---|
| Test reports can mix hidden paper answers with platform-visible prompts. | Future runs may leak answers into prompts. | Store hidden answers separately from public protocol docs. |
| Reports can overstate platform readiness. | Researchers may expect full paper reproduction. | State supported data, runnable methods, preliminary tiers, and scope gaps plainly. |
| Fix plans can become detached from observed failure categories. | Work may drift into feature piling. | Every fix should map to a failure category and a regression test. |

## Anti-Hardcoding Rule

Fixes must not encode a paper's answer.

Allowed fixes:

- Add a general dataset registry entry with citation, version, units, covariance
  metadata, and download source.
- Add a general likelihood runner.
- Improve routing from research question to tool family.
- Improve evidence graph or fact-check validation.
- Improve UI visibility for scope gaps and warnings.
- Improve table extraction, provenance, citation matching, or formula parsing.

Forbidden fixes:

- Hardcode a paper's conclusion.
- Hardcode final posterior values, fitted slopes, significances, or tensions.
- Special-case a test prompt.
- Add hidden answer leakage to the prompt or tool layer.

## Aggregate Report Template

After a batch, summarize:

| Metric | Description |
|---|---|
| `papers_tested` | Number of completed blind runs |
| `A_count` | Research-grade passes |
| `B_count` | Partial passes |
| `C_count` | Honest failures |
| `D_count` | Tool-route errors |
| `E_count` | Severe failures |
| `most_common_missing_capabilities` | Top missing datasets, likelihoods, runners, covariances |
| `most_common_routing_errors` | Recurring wrong routes |
| `unsupported_claim_rate` | Fraction of runs with unsupported final claims |
| `honest_failure_rate` | Fraction of impossible tasks that failed safely |
| `highest_priority_fixes` | General fixes likely to improve many papers |

## Current Status

The retained protocol and the observed problem catalog are ready to use for
future observational-cosmology blind tests.

Specific hidden paper answers are not recorded in this document. Future rounds
should append concrete batch summaries below this line, with hidden paper
answers stored separately outside public prompt context.

## Future Batch Summaries

Add new summaries here using the aggregate report template above.

## Batch 2026-05-25 — Strict Visible Chat UI Run, 50 Paper-Derived Tests

### Execution Notes

This entry replaces the earlier mixed execution note for this batch. The
earlier endpoint-stream batch remains useful as a backend reference, but it is
**not** treated here as product validation. This stricter pass completed all
50 paper-derived prompts through the visible Chat UI.

- Candidate papers: 50 arXiv records collected under the local diagnostic
  directory `.local/blind-research-tests/round-2026-05-25-50/`.
- Hidden paper records: stored locally only, not copied into this document.
- Frontend route used: `http://127.0.0.1:5173/chat`.
- Backend/model shown in the Chat UI: local server-side backend using
  DeepSeek V4 Pro.
- Fresh-chat discipline: each prompt was sent in its own visible Chat UI
  session; the left sidebar still retained old session titles, but each saved
  current run contained a two-message current conversation.
- True UI artifacts:
  - `.local/blind-research-tests/round-2026-05-25-50/chat-ui-runs/`
- Backend-stream artifacts from earlier work:
  - `.local/blind-research-tests/round-2026-05-25-50/chat-runs/`
  - These are reference artifacts only and must not be reported as Chat UI
    results.

Important limitation: these `score_hint` values are frontend/capability
heuristics, not a final scientific agreement score against hidden paper
conclusions. The hidden conclusion comparison still has to be done offline
paper-by-paper before claiming a true reproduction or anomaly-agreement rate.

### Aggregate UI Outcome

| Metric | Count |
|---|---:|
| Visible Chat UI runs completed | 50 |
| Missing UI artifacts | 0 |
| Heuristic B: partial pass | 34 |
| Heuristic C: honest failure | 13 |
| Heuristic D: tool/process route failure | 3 |
| Guardrail-withheld final answers | 0 |
| Internal marker/XML leaks detected | 0 |
| Provider disconnect / silent process failures | 3 |
| Runs with Research Plan panel | 47 |
| Runs with Research Matrix panel | 47 |
| Runs with Fact Check panel | 22 |
| Runs with Research Report panel | 23 |
| Runs with scope-gap language | 47 |
| Runs with compressed-preliminary language | 34 |
| Runs saved while `Stop` was still visible | 25 |

Interpretation:

- The platform generally behaved better than a hallucinating chatbot: it mostly
  planned, ran registered matrix cells, and disclosed missing likelihoods.
- The dominant scientific blocker remains execution coverage, not unsupported
  final prose. In this UI batch, no internal XML/tool protocol leaked into the
  final answer and no guardrail-withheld result was observed.
- The dominant product blocker is completion robustness. Half the runs were
  saved while the UI still showed `Stop`, and only 22/50 reached a visible
  Fact Check panel.
- The current platform is useful for compressed LCDM/BAO/CMB baseline
  exploration, but it is not yet an anomaly-paper reproduction engine.

### Per-Run UI Status

| Run | Heuristic status | Visible outcome |
|---|---:|---|
| P01 | B | Research plan + matrix + evidence graph; compressed LCDM baseline only; saved with `Stop` still visible. |
| P02 | C | Research plan + matrix; honest scope gap; saved with `Stop` still visible. |
| P03 | B | Research plan + matrix; compressed preliminary subset; saved with `Stop` still visible. |
| P04 | C | Research plan + matrix; honest scope gap for missing CMB-rotation/birefringence likelihood; saved with `Stop` still visible. |
| P05 | B | Research plan + matrix + fact/report path; compressed preliminary baseline with unsupported target branches. |
| P06 | B | Research plan + matrix + fact/report path; compressed preliminary baseline with unsupported target branches. |
| P07 | B | Research plan + matrix + fact/report path; BAO+CMB-style compressed baseline available; target branch incomplete. |
| P08 | C | Honest birefringence/parity scope gap; no claimable rotation likelihood result. |
| P09 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P10 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P11 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P12 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P13 | D | Provider connection interrupted; no research plan/matrix reached. |
| P14 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P15 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P16 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P17 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P18 | B | Compressed preliminary baseline; EDE/full-likelihood branch missing. |
| P19 | C | Honest CMB-rotation/birefringence scope gap. |
| P20 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P21 | C | Honest scale-dependent birefringence scope gap. |
| P22 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P23 | D | Provider connection interrupted; no research plan/matrix reached. |
| P24 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P25 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P26 | D | Provider connection interrupted; no research plan/matrix reached. |
| P27 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P28 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P29 | B | Compressed preliminary baseline; target anomaly likelihood missing. |
| P30 | B | Research report visible; compressed preliminary baseline; saved with `Stop` still visible. |
| P31 | B/D | Correct matrix path started, but workflow also misrouted a tension check into object/luminosity tools; saved with `Stop` still visible. |
| P32 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P33 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P34 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P35 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P36 | C | Honest ACT/polarization-rotation likelihood scope gap; saved with `Stop` still visible. |
| P37 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P38 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P39 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P40 | C | Honest BICEP/Keck or parity-likelihood scope gap; saved with `Stop` still visible. |
| P41 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P42 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P43 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P44 | C | Honest parity/composite-field spectra scope gap; saved with `Stop` still visible. |
| P45 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P46 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P47 | B | Compressed preliminary baseline; saved with `Stop` still visible. |
| P48 | C | Honest simulation-based birefringence-inference scope gap; saved with `Stop` still visible. |
| P49 | C | Honest axion/birefringence cross-correlation scope gap; saved with `Stop` still visible. |
| P50 | B | Compressed preliminary baseline; saved with `Stop` still visible. |

### Pattern Counts From The Visible UI Artifacts

| Pattern | Count | Runs |
|---|---:|---|
| Provider disconnect text | 3 | P13, P23, P26 |
| `Stop` visible at saved snapshot | 25 | P01, P02, P03, P04, P30-P50 |
| Wrong astronomy object routing | 1 | P31 |
| Wrong luminosity-distance tool route | 1 | P31 |
| Early-dark-energy / pre-recombination gap language | 32 | P09-P12, P14-P18, P20, P22, P24-P25, P27-P35, P37-P39, P41-P43, P45-P47, P50 |
| CMB polarization-rotation gap language | 20 | P04-P23 subset |
| Pantheon+ config/gap language | 37 | P01-P03, P05-P06, P09-P18, P20, P22, P24-P25, P27-P35, P37-P39, P41-P43, P45-P47, P50 |
| BAO + CMB runnable/baseline language | 38 | P01-P03, P05-P07, P09-P18, P20, P22, P24-P25, P27-P35, P37-P39, P41-P43, P45-P47, P50 |
| Fact Check panel visible | 22 | P05-P12, P14-P22, P24-P25, P27-P29 |
| Research Report panel visible | 23 | P05-P12, P14-P22, P24-P25, P27-P30 |

### Problems Exposed By The Strict UI Batch

#### 1. Completion Robustness Is Not Good Enough

Twenty-five runs were saved while the UI still showed `Stop`. Some had already
produced useful matrix/evidence content, but a research-grade system cannot
leave the user guessing whether the run finished.

Required fix:

- Add an explicit terminal run state: `completed`, `completed_with_scope_gap`,
  `provider_interrupted`, `tool_budget_exhausted`, or `needs_user_action`.
- If the tool budget expires, force a final summary card that explains what
  ran and what did not.
- Do not leave `Stop` visible after the backend stream has effectively stopped
  producing new content.

#### 2. Provider Interruptions Are Product Failures

P13, P23, and P26 ended with the user-facing message that the AI provider
connection was interrupted. These are not honest scientific failures; they are
infrastructure/process failures.

Required fix:

- Add resumable turn state so completed tool outputs can still be summarized
  after a provider disconnect.
- Add automatic short retry for the final prose step when tools have already
  completed.
- Store provider disconnects as a distinct failure category in diagnostics.

#### 3. Tool Routing Can Fall Through To The Wrong Domain

P31 started a valid research-matrix path but then tried to resolve a
cosmological tension phrase as an object:

- `get_object_dossier` failed on a text phrase like "Tension check: CMB-only
  Planck18 H0 vs BAO+CMB H0 vs SH0ES H0".
- `compare_luminosity_distances` also appeared as an empty/no-data route.

Required fix:

- Add a domain lock once `plan_research_program` classifies a turn as
  observational cosmology.
- Disable object-dossier and stellar/galactic distance tools for abstract
  cosmology-matrix claim labels.
- Route tension labels only to evidence/fact-check/cosmology-chain tools.

#### 4. Fact Check And Report Panels Do Not Always Appear

Only 22/50 runs reached a visible Fact Check panel and 23/50 reached a Research
Report panel. That means many runs ended at plan/matrix/evidence state without
the final verification layer the product promises.

Required fix:

- Always run `verify_research_facts` on any turn that emits a matrix or
  evidence graph, even if the final answer is only a scope-gap summary.
- Always render a minimal report card for honest failures:
  "what ran", "what is missing", "what can be claimed", "what cannot be
  claimed".

#### 5. Specialized Likelihood Coverage Remains The Scientific Blocker

The platform repeatedly fell back to LCDM compressed preliminary baselines.
Those are useful, but they do not answer most hidden anomaly-paper methods.

High-impact missing capabilities observed again:

- Pantheon+ SN runner with covariance and nuisance treatment.
- Executable wCDM/CPL/w0wa matrix cells, not just config-only branches.
- EDE / dark-radiation / pre-recombination model runner through controlled
  Boltzmann/Cobaya infrastructure.
- CMB birefringence EB/TB likelihoods with instrument-angle calibration.
- BICEP/Keck and ACT DR6 polarization-rotation products.
- Scale-dependent / field-level birefringence estimators.
- BAO transverse/angular comparison runner where the hidden prompt requires
  BAOtr-style tests.

#### 6. Compressed Preliminary Output Is Valuable But Too Dominant

Thirty-four runs contained compressed-preliminary language, and 38 runs
contained BAO+CMB baseline language. This is good for safety and baseline
science, but it can over-dominate the response if the requested anomaly model
is not actually tested.

Required fix:

- Add a "method coverage" badge for each matrix:
  `target method executed`, `baseline only`, `config only`, or `not available`.
- In final prose, lead with the coverage grade before any baseline numbers.

#### 7. UI Diagnostics Are Noisy

Browser-side runs repeatedly logged Statsig rate-limit warnings. The footer
also still displayed stale global copy:

```text
claude-opus-4-7 · Planck18 · Gaia DR3 · PARSEC 3.9
```

while the Chat status area showed the active DeepSeek backend. The active model
should not conflict with stale footer text.

Required fix:

- Disable or debounce Statsig initialization in local test mode.
- Remove hardcoded model names from global footer copy.
- Keep active model reporting inside the Chat status area and backend-status
  panel.

#### 8. Assessment Artifacts Need A Stable Schema

The current assessments capture useful booleans and heuristic scores, but they
do not yet contain a strict per-run scientific comparison against the hidden
paper conclusion.

Required fix:

- Define one `BlindRunAssessment` schema with:
  - `paper_id`
  - `prompt_id`
  - `fresh_chat_confirmed`
  - `ui_completed_state`
  - `tool_cards`
  - `evidence_graph_status`
  - `fact_check_status`
  - `scope_gap_categories`
  - `scientific_agreement_offline`
  - `failure_labels`
- Validate every assessment before including it in aggregate statistics.

### Consolidated Issue Register From This Test

This table is the actionable issue register for the strict 50-run Chat UI
batch. It intentionally includes both product bugs and scientific capability
gaps. "Observed evidence" refers to the saved visible UI artifacts in
`.local/blind-research-tests/round-2026-05-25-50/chat-ui-runs/`.

| ID | Severity | Issue | Observed evidence | Impact | Required fix |
|---|---|---|---|---|---|
| UI-01 | P0 | Terminal state is unreliable. | 25/50 snapshots still showed `Stop` at save time: P01-P04 and P30-P50. | Users cannot tell whether a research turn finished, timed out, or is still running. | Add explicit terminal states and force a final "what ran / what did not run" summary when the stream stops or budget expires. |
| UI-02 | P0 | Provider disconnects are not recoverable. | P13, P23, P26 ended with "AI provider connection interrupted" and no research plan/matrix. | Completed partial work, if any, is lost to the user; failure category is infrastructure, not science. | Persist turn state, retry final prose once, and render a provider-interrupted summary card. |
| UI-03 | P1 | Fact Check is not mandatory for research turns. | Only 22/50 visible runs reached a Fact Check panel. | Strong or scope-sensitive claims may appear without the promised verification layer. | Always run and render a minimal Fact Check for any turn that emits a research matrix or evidence graph. |
| UI-04 | P1 | Research Report is not mandatory for scope-gap endings. | Only 23/50 visible runs reached a Research Report panel. | Honest failures are less useful because the user gets no consolidated "what is supported" report. | Always render a minimal report for matrix turns, including honest failures and provider-interrupted turns. |
| UI-05 | P1 | Cosmology prompts can fall through to non-cosmology tools. | P31 called `get_object_dossier` and `compare_luminosity_distances` on a cosmological tension phrase. | Produces irrelevant tool failures and weakens trust in domain routing. | Add observational-cosmology domain lock after `plan_research_program`; disable object/distance tools for abstract matrix labels. |
| UI-06 | P2 | Local browser diagnostics are noisy. | Statsig repeatedly logged initialization rate-limit warnings during sends. | Makes test logs hard to read and obscures real UI/backend failures. | Debounce or disable Statsig initialization in local/dev blind-test mode. |
| UI-07 | P2 | Footer model copy is stale and conflicts with active backend. | Chat status showed DeepSeek, while footer still displayed `claude-opus-4-7 · Planck18 · Gaia DR3 · PARSEC 3.9`. | Users may think the wrong model or stale scientific stack is active. | Remove hardcoded model names from footer; show active model only in the Chat/backend-status area. |
| UI-08 | P2 | Old chat titles remain visually noisy during fresh-run testing. | Sidebar retained many old two-message session titles while current run was fresh. | Not direct contamination, but makes manual validation and screenshots hard to interpret. | Add a test-mode clean sidebar or clearer current-session marker. |
| SCI-01 | P0 | Pantheon+ SN is not executable enough for research-grade BAO+SN work. | Pantheon+ gap/config language appeared in 37/50 runs. | BAO+SN and BAO+SN+CMB anomaly prompts cannot become paper-level analyses. | Implement Pantheon+ SN runner with covariance, nuisance treatment, citation, and publication-ready diagnostics. |
| SCI-02 | P0 | wCDM/CPL/w0wa execution is missing or too often config-only. | Dark-energy anomaly prompts usually fell back to LCDM compressed baselines. | Platform cannot properly test time-varying dark-energy claims. | Add executable wCDM/CPL/w0wa compressed likelihood runner and mark unsupported cells explicitly. |
| SCI-03 | P0 | EDE / dark-radiation / pre-recombination model support is missing. | EDE/pre-recombination gap language appeared in 32 runs. | H0-tension anomaly papers cannot be tested beyond baseline LCDM. | Add controlled Cobaya/Boltzmann path for registered EDE/dark-radiation models, or keep them hard unavailable. |
| SCI-04 | P0 | CMB birefringence / parity likelihood support is missing. | CMB polarization-rotation gap language appeared in 20 runs. | Parity/birefringence papers mostly become honest failures. | Register EB/TB datasets, calibration priors, covariance, and a dedicated polarization-rotation likelihood runner. |
| SCI-05 | P1 | BAO transverse/angular comparison support is incomplete. | Low-redshift/angular BAO prompts became baseline BAO/CMB or config gaps. | BAOtr-style anomaly papers cannot be directly tested. | Add BAO transverse/angular dataset entries, covariance, model predictions, and comparison runner. |
| SCI-06 | P1 | Compressed LCDM baseline is over-represented. | 34/50 runs contained compressed-preliminary language; 38/50 mentioned BAO+CMB runnable/baseline paths. | Baseline results can look more relevant than they are to the target anomaly method. | Add method-coverage labels: target executed, baseline only, config only, not available. |
| SCI-07 | P1 | Missing full external likelihood tier. | Many hidden-paper methods require full CMB/SN/WL likelihoods, not registered compressed approximations. | Platform cannot move from exploratory baseline to paper-grade inference for many anomaly classes. | Add controlled full Cobaya/CosmoSIS jobs for selected registered likelihoods; do not allow raw user YAML/code. |
| SCI-08 | P2 | Weak-lensing / S8 anomaly coverage was under-tested and likely under-supported. | Candidate pool was biased away from S8/growth/modified-gravity classes. | Current batch cannot establish S8/growth readiness. | Enforce per-class paper quotas and add KiDS/DES/HSC/ACT compressed S8 runner tests. |
| EVID-01 | P0 | Fact-check coverage is too sparse to claim unsupported-claim rate. | 28/50 runs did not show Fact Check. | Absence of visible hallucination is not proof that all claims are verified. | Treat unsupported-claim rate as unknown unless Fact Check ran or offline claim audit was done. |
| EVID-02 | P1 | Evidence Graph does not always reach final user-facing interpretation. | Many runs stopped at plan/matrix/evidence without fact/report. | Evidence exists in cards but is not translated into a clear final conclusion. | Require final summary to reference matrix/evidence status for every research turn. |
| EVID-03 | P1 | Hidden-paper scientific agreement was not yet computed. | Current `score_hint` is heuristic; no per-paper hidden conclusion comparison was recorded in the UI assessments. | Cannot claim reproduction rate, anomaly-agreement rate, or numerical correctness. | Add offline comparison fields and complete paper-by-paper hidden-record scoring. |
| EVID-04 | P2 | Assessment schema is not stable enough. | Current artifacts have booleans such as `has_matrix` and `score_hint`, but not strict scientific agreement fields. | Aggregates are useful for product behavior, not enough for scientific evaluation. | Define and validate a single `BlindRunAssessment` schema. |
| TEST-01 | P1 | Candidate pool is class-biased. | This 50-paper pool over-sampled EDE/H0 and birefringence/parity; modified gravity, primordial features, and S8/growth were not balanced. | Aggregate results overstate EDE/H0 behavior and under-test other anomaly classes. | Build deterministic candidate-pool generator with per-class quotas and stop if quotas are not met. |
| TEST-02 | P1 | Bulk Chat UI testing is still too ad hoc. | Tests were completed through visible UI automation and saved snapshots, but there is no first-class product harness. | Repeating 50-paper UI tests remains brittle and slow. | Add an internal blind-test harness that drives real Chat UI sessions and exports structured artifacts. |
| TEST-03 | P2 | Fresh-session validation is too manual. | Current run relied on visible two-message current conversations and local artifact checks. | Future runs can accidentally mix context unless checks are automated. | Add `fresh_chat_confirmed` state and fail the test if old messages/tool cards are visible in the current thread. |
| TEST-04 | P2 | UI snapshots include sidebar noise. | Saved snapshots contain many old session titles before the active run content. | Downstream text pattern matching can over-count or misread current-run content. | Save current-turn transcript separately from full-page text; keep full screenshot as auxiliary evidence. |

### What This Batch Did **Not** Prove

The following conclusions are explicitly not supported by the current batch:

- It did **not** prove that Standard Astro can reproduce 50 anomaly papers.
- It did **not** prove that the platform's numerical outputs agree with the
  hidden paper conclusions.
- It did **not** prove an unsupported-claim rate of zero, because Fact Check
  was visible in only 22/50 runs.
- It did **not** prove readiness for broad professional alpha testing.
- It did **not** prove robust coverage for S8/growth, modified gravity, or
  primordial-feature papers, because the candidate pool was not balanced.

What it did prove:

- The visible Chat UI can execute all 50 paper-derived prompts and save
  artifacts.
- The research-plan and research-matrix path is active in most runs.
- The platform usually chooses honest scope-gap language instead of fabricating
  anomaly conclusions.
- The largest remaining blockers are terminal-state robustness, mandatory fact
  verification, report completion, domain routing, and specialized likelihood
  coverage.

### Highest-Priority Engineering Queue From This UI Batch

1. **Terminal run-state and final fallback summary**
   - Fix `Stop`-forever and half-finished UI runs.
2. **Provider-disconnect recovery**
   - Preserve completed tool outputs and retry/summarize final prose.
3. **Cosmology domain lock**
   - Prevent cosmology matrix labels from routing into object/distance tools.
4. **Always-on Fact Check for research matrix turns**
   - Even honest failures need a visible Fact Check / Claim Safety panel.
5. **Pantheon+ SN runner v1**
   - Biggest scientific unlock for BAO+SN and BAO+SN+CMB matrix cells.
6. **Executable CPL/w0wa runner**
   - Required before dark-energy anomaly prompts can move beyond baseline LCDM.
7. **CMB birefringence likelihood registry and runner**
   - Required for the parity/birefringence subset.
8. **Controlled external Cobaya/Boltzmann path**
   - Required for EDE, dark radiation, and other pre-recombination anomaly
     papers.
9. **Local Chat UI blind-test harness**
   - Must drive the real UI, save visible cards, and export validated
     assessment artifacts without relying on fragile ad hoc browser typing.
