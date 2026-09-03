export const meta = {
  name: 'adversarial-review',
  description: 'Multi-lens adversarial review of a git range; every finding independently verified before it reaches the user',
  whenToUse: 'Before committing science-critical or anti-fabrication changes. args: {range: "HEAD~1..HEAD" | "origin/main..HEAD", repo?: "<absolute worktree path, default primary checkout>", focus?: "extra reviewer guidance", lenses?: [["key","description"], ...]}. Findings come back split into confirmed / uncertain / refuted-with-pinnable-reasons.',
  phases: [
    { title: 'Review', detail: 'independent finders, one lens each' },
    { title: 'Verify', detail: 'one skeptic per finding tries to refute it' },
  ],
}

// Override with args.repo when reviewing a worktree other than the primary checkout.
const REPO = (args && args.repo) || '/Users/chenkexuan/Projects/astro-platform'
const range = (args && args.range) || 'origin/main..HEAD'
const focus = (args && args.focus) || ''

// Lenses mirror the bug classes that real incidents produced (see CLAUDE.md
// "Verification Discipline"). Override via args.lenses for special reviews.
const DEFAULT_LENSES = [
  ['correctness',
   'logic errors, broken edge cases, wrong physics or units, regressions the existing tests would not catch'],
  ['data-fidelity',
   'silent data drops: per-probe dispatch loops without an else:raise fuse, fast-path opt-out guards missing a probe family, datasets listed in datasets_used without contributing chi2, provenance labels or hardcoded dataset/version strings that no longer match the data'],
  ['honesty-gates',
   'weakened claim_validator / result_provenance / synthetic_code_detector defenses, relaxed forbid strings, blacklists shrunk "while updating the matching test", gates that could now pass fabricated or self-supplied input, gates that now false-kill clean specificity cases'],
  ['tests-load-bearing',
   'deleted or weakened load-bearing tests, regression tests that would pass even without the fix, tests exercising a convenient path instead of the channel that actually triggered the original bug'],
  ['contracts',
   'changed fields, signatures, or return shapes where some call sites or parallel code paths were not updated; rg every changed symbol and check each occurrence'],
]
const lenses = (args && args.lenses) || DEFAULT_LENSES

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          summary: { type: 'string' },
          evidence: { type: 'string', description: 'concrete anchor: code excerpt, command output, or spec quote' },
        },
        required: ['file', 'severity', 'summary', 'evidence'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['CONFIRMED', 'REFUTED', 'UNCERTAIN'] },
    reasoning: { type: 'string' },
    refuting_evidence: {
      type: 'string',
      description: 'if REFUTED: the concrete evidence, phrased so it can be pinned in a code comment to prevent re-flagging',
    },
  },
  required: ['verdict', 'reasoning'],
}

phase('Review')
const rounds = await parallel(lenses.map(([key, desc]) => () =>
  agent(
    `You are reviewing ${REPO} (git range ${range}) through ONE lens: ${key} — ${desc}.
Read the diff (git diff ${range}) and the surrounding current code, not just the hunks. Project rules live in ${REPO}/CLAUDE.md — read the "Verification Discipline" and "Non-Negotiable Scientific Guardrails" sections first. ${focus}
Report only findings you can anchor to a file and concrete evidence. No style nits, no speculation without an anchor.`,
    { label: `review:${key}`, phase: 'Review', schema: FINDINGS },
  )))

const seen = new Set()
const all = rounds.filter(Boolean).flatMap(r => r.findings).filter(f => {
  const k = f.file + '|' + f.summary.slice(0, 60)
  if (seen.has(k)) return false
  seen.add(k)
  return true
})
log(`${all.length} deduped findings from ${lenses.length} lenses`)

phase('Verify')
const verified = await parallel(all.map(f => () =>
  agent(
    `Independently verify this review finding about ${REPO} (git range ${range}). Default to REFUTED unless the evidence survives a real reproduction attempt: read the actual code, run the test or a minimal check where possible. Findings are hypotheses, not conclusions.
Finding: [${f.severity}] ${f.file}:${f.line || '?'} — ${f.summary}
Claimed evidence: ${f.evidence}`,
    { label: `verify:${f.file}`, phase: 'Verify', schema: VERDICT },
  ).then(v => (v ? { ...f, ...v } : null))))

const ok = verified.filter(Boolean)
return {
  range,
  confirmed: ok.filter(v => v.verdict === 'CONFIRMED'),
  uncertain: ok.filter(v => v.verdict === 'UNCERTAIN'),
  refuted_with_pinnable_reasons: ok.filter(v => v.verdict === 'REFUTED'),
}
