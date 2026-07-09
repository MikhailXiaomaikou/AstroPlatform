# Observational Cosmology Beta Test Call

**Standard Astro** is looking for feedback from people actively working in observational cosmology.

Repository: https://github.com/MikhailXiaomaikou/Standard-Astro

To try it, clone the repository and run it locally — see the "Run it" section
of the README (you will need your own model-provider API key — DeepSeek,
Anthropic, or OpenAI — for the chat itself).

If you read only one document, read
[docs/HONESTY_EVIDENCE.md](./HONESTY_EVIDENCE.md) (~5 minutes) — the verified
anti-fabrication record, including its failures and open gaps.

## Why this beta exists

Standard Astro is an AI-native astronomy research platform for archive discovery, provenance tracking, research planning, evidence graphs, statistical inference helpers, cosmology likelihood workflows, and reproducible paper/report export.

The current cosmology focus is not to claim production-grade full-likelihood inference. The goal is to test whether the platform’s research-mode planning, compressed-likelihood workflows, provenance model, and fact-checking guardrails are scientifically useful and honest enough for real exploratory work.

The alpha-test contract is deliberately narrow: registered datasets and
controlled runners may support exploratory compressed-likelihood results;
config-only entries, paper abstracts, old chat context, and user assumptions
may support background or scope notes only. A useful failure is one that says
which dataset, covariance, runner, or citation is missing.

Note on likelihood fidelity: the heavy full-covariance SN likelihoods
(Pantheon+, DES-SN5YR, Pantheon 2018) ship vendored but are switched off by
default for performance; every result labels its covariance fidelity
(`cov_fidelity`), so a compressed run is never presented as a full one.

## Who we hope will try it

We especially want feedback from researchers, postdocs, PhD students, and research software developers working on or near:

- DESI / BAO likelihoods and dark-energy interpretation;
- Pantheon+, SH0ES, Union-style, or other supernova cosmology samples;
- Planck / ACT compressed CMB constraints;
- weak-lensing and S8 comparisons, including KiDS, DES, HSC, ACT lensing;
- H0 tension workflows;
- Cobaya, CosmoSIS, MontePython, or related cosmological inference tooling;
- provenance, reproducibility, and scientific claim validation.

## Suggested things to test

Please try one or more of these workflows:

1. Ask Standard Astro to plan a BAO + SN + CMB observational cosmology analysis.
2. Ask it to distinguish compressed preliminary constraints from full-likelihood / publication-ready inference.
3. Ask it to build or inspect an evidence graph for a numerical cosmology claim.
4. Ask it to compare DESI / Pantheon+ / Planck / weak-lensing evidence without overclaiming unsupported results.
5. Ask it to draft a short research summary and check whether unsupported claims are clearly marked.
6. Ask it to produce a research plan, executed matrix, evidence graph, fact-check report, and local diagnostic bundle for a blind-test prompt.
7. Read the [full-CMB DESI 2024 VI w0waCDM reproduction record](../backend/scripts/cobaya/README_full_cmb_reproduction.md) — a dedicated offline Cobaya+CAMB run that lands all four parameters within 0.3σ of the published DESI values (with honest caveats stated in the record) — then ask the platform to plan the same analysis and check that it clearly distinguishes what the autonomous compressed-likelihood path can and cannot claim.

## Feedback we need most

Critical feedback is more valuable than praise. Please tell us:

- Which workflow you tested.
- Which scientific claim or output looked useful.
- Which claim looked unsupported, overstated, or scientifically unsafe.
- Which dataset, likelihood, covariance, citation, prior, or assumption was missing.
- Whether the evidence/provenance shown was enough to support the answer.
- What the platform should refuse to say without stronger evidence.
- What would make this useful for a real research group or graduate-student workflow.

## How to send feedback

Preferred: open a GitHub issue using the **Quick Feedback** template — three fields (who you are / what you tried / where it got stuck or impressed you), done in a minute.

For a detailed scientific report, use the full **Observational Cosmology Beta Feedback** template instead — it has structured fields for claims, evidence, missing datasets, and refusal boundaries.

You can also send informal notes, but GitHub issues are easiest to track and turn into concrete fixes.

## Discussion post draft

Title:

```text
Looking for observational cosmology beta testers / scientific feedback
```

Body:

```markdown
Hi everyone,

I’m looking for feedback from observational cosmology researchers on **Standard Astro**, an AI-native astronomy research platform.

Repo: https://github.com/MikhailXiaomaikou/Standard-Astro
(Clone and run locally — setup is in the README's "Run it" section; the chat
needs your own model-provider API key.)

The project focuses on archive discovery, provenance tracking, research planning, evidence graphs, statistical inference helpers, cosmology likelihood configs, compressed posterior experiments, and reproducible paper/report export.

The current beta focus is observational cosmology: DESI/BAO, Pantheon+ or other SN samples, Planck/ACT compressed constraints, weak-lensing S8 comparisons, H0 tension workflows, and scientific claim verification.

I’m especially looking for critical feedback on:

1. whether the cosmology workflows match real research practice;
2. which datasets, likelihoods, citations, priors, or covariances are missing;
3. where the platform overclaims beyond evidence;
4. whether the evidence graph / provenance / fact-checking model is too weak or too strict;
5. what would make it useful for students or research groups.

The goal is not endorsement — rough notes, issue comments, or a list of scientific concerns would be extremely helpful.

If you try it, please open an issue using the Quick Feedback template (three fields, one minute) or the full Observational Cosmology Beta Feedback template, or leave notes in this discussion.

Thanks!
```
