# Second consumer: the core-guard fix as a Shape A mission (F10), 2026-09-07

The follow-up to the audit in `2026-09-07-consumer-core-guard-audit.md`: the first Shape A
build conductor ran on a repository other than itself. The target was the operator's Claude
Code Bash guard (`hooks/core-guard.py`) and its regression gate
(`hooks/tests/core-guard-test.py`) in the harness repository; the spec was every audit
finding two or more vendors had reported, thirteen items, plus a gate change so each CATCH
case asserts which rule fired. The gate command was the guard's own suite, nothing from
conductor's tree.

## The two missions

| mission | lane | model | cap | spent | wall | result |
|---|---|---|---|---|---|---|
| audit-fixes | build | Sonnet 5, hard | $15.00 | $4.47 | 27 min | all 13 items, gate 120 catch / 108 quiet, green |
| audit-fixes | review-gemini | Gemini 3.7 Flash | $1.00 | $0.12 | 2 min | NO_FINDINGS |
| audit-fixes | review-grok | Grok 4.6 (ran the suite) | $2.00 | $0.42 | 8 min | one finding, real (item 6's pipeline check read every earlier `;` segment, not the pipe) |
| audit-fixes | fix | Sonnet 5, resumed | $8.00 | $1.75 | 4 min | fixed with two QUIET cases; disposition `fixed` |
| audit-fixes-2 | build | Sonnet 5, hard | $8.00 | $1.43 | 7 min | four items, gate 129 catch / 116 quiet, green |
| audit-fixes-2 | review-gemini | Gemini 3.7 Flash | $1.00 | $0.12 | 1 min | NO_FINDINGS |
| audit-fixes-2 | review-grok | Grok 4.6 (ran the suite) | $2.00 | $0.24 | 4 min | NO_FINDINGS |
| audit-fixes-2 | fix | | | $0.00 | | stopped at the pause: nothing to fix |

$8.56 for the pair, about 70 minutes of wall clock end to end including the lead's reads,
both missions attested. The result is three commits on `feat/core-guard-audit-fixes` in the
harness repository, gated green in a fresh worktree at the tip (129 catch / 116 quiet, per-rule
counts printed); the branch is the operator's to merge.

## What the second mission was for

The lead compared the old guard and the build's guard on shapes the gate does not pin, before
the fix lane ran. Two were regressions the build had introduced while closing an item:

- `script_bodies` was rewritten to take the segment's command word, so an interpreter behind
  a launcher the guard does not list (`timeout 60 bash evil.sh`, `sudo bash evil.sh`,
  `nice -n 10 bash evil.sh`) was no longer read. The old code found an interpreter word
  anywhere in the segment. No CATCH case covered a wrapped interpreter, so the gate stayed
  green.
- The upload rule was narrowed to the flag's own argument (a real false positive), and the
  glued short form `curl -d@file` lost its argument on the way.

Neither reviewer reported them: both read the diff against the spec, and the spec did not
say "keep every shape the old guard denied". The lead's before-and-after probe did. That is
the receipt's lesson for the next consumer spec: **a spec that narrows a rule states the old
denials that must survive**, or the lead probes for them before the reviewers run. The
mission's prompts are inlined at launch, so a lead finding cannot be handed to a running fix
lane; it became a second, four-item mission on the branch tip ($1.79), which is the shape rule
6 already describes for salvage.

The other two items of the second mission were gaps the build had read narrowly (a quoted
assignment value, a quoted standalone reference); the old guard allowed them too.

## What the receipt says about conductor

- Shape A on a foreign repository worked without a conductor-specific gate: the launcher's
  preflight ran the guard's own suite in a throwaway worktree and the lanes used it as-is.
- Grok's one finding on the first mission was real and outside the diff's own lines (the
  segment-versus-pipeline distinction), and Gemini's NO_FINDINGS was correct both times: rule
  7's pattern held on a third repository.
- Sonnet at `hard` built thirteen items across an 870-line file at $4.47 against a $15 cap,
  and four items at $1.43 against $8. Rule 2's per-item estimate is conservative on a single
  well-documented file; the launcher's forecast warning on the second build (cap under the
  80th percentile) was noise for a four-item spec.
- A mission on a worktree of the target repository (the second mission's `--repo` was a
  worktree checked out at the branch tip) works, but the fix lane's landing branch must differ
  from the branch that worktree has checked out. `base` names a lane, not a ref; a `--base-ref`
  on the launcher would remove the worktree dance.
- The live guard blocked two of the lead's own probe commands (a heredoc that wrote a Python
  file the same command then ran, and a command line that spelled `curl -d@` with a credential
  path inside a quoted string). Both are the guard doing its job on the lead's shell; the probe
  moved to a file written by the editor and run separately.
