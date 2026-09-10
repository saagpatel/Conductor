# Consumer: the F22 validator read live on one document, 2026-09-07

Release 0.82.0. The first `stage: fix` lane on a document that landed through conductor's own
gates instead of by the lead's hand. F10's anti-slop consumer (0.59.0) had to run its edit as a
`build` lane because a fix lane on prose was refused for having no test surface; F22 (0.81.0)
gave a deliverable a validator, and this run is the receipt that the validator's verdict carries
a fix lane end to end. Mission `20260907T204604Z-f22-validator-live-sandbox-doc`, $2.31, 8
minutes wall clock.

## Shape

| lane | model | mode | cap | what |
|---|---|---|---|---|
| edit | Sonnet 5, hard | write, `stage: fix`, `untrusted_output: true`, deliverable = the document with `validator: python3 scripts/prose_gate.py {path}` | $4.00 | the F10 anti-slop rules on one document |
| review-gemini | Gemini 3.7 Flash | read, `base: edit`, tainted by the edit's diff | $1.00 | the F10 review prompt |
| review-opus | Opus 5, hard | read, `base: edit`, tainted the same way | $3.00 | same prompt |

The document: `docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md`, 302 lines, chosen
because it failed the prose gate at the base with 10 findings (eight em dashes and two filler words),
so a correct edit would make the validator's before run fail and its after run pass. The
mission's `test` is the same prose gate. No Grok: taint on Cursor is refused. Nothing else in the
mission differs from the F10 shape except the stage and the validator line.

## The run

| lane | outcome | cost | wall |
|---|---|---|---|
| edit | green: 1 commit, 1 file, 2075 words to 2068, 58 tool calls | $1.40 | 344 s |
| review-gemini | NO_FINDINGS, 6 tool calls | $0.07 | 29 s |
| review-opus | 7 findings, confidence 3 to 8, every one cited | $0.84 | 144 s |

The edit lane's receipt, the object of the exercise:

```
deliverable.validator:
  command: python3 scripts/prose_gate.py docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md
  before:  exit_code 1, timed_out false, 10 tail lines naming validator-before.md
  after:   exit_code 0, timed_out false, tail "(no output)"
  verdict: reproduced
reproduce:
  ran false, worktree "", patch_bytes 0, verdict: validator
test_surface: touched false, clean_gate {ran: false, reason: "test surface unchanged"}
```

The before run's tail names the run directory's `validator-before.md` copy rather than the repo
path, which is the F22 design (the base bytes never enter the worktree). `conductor land --lane
edit` merged the branch, ran the prose gate on the merged tree, ran golden, and verified the
attestation chain: four steps, all ok. Both tainted reviewers ran with `taint_shell: denied`, 30
denied tools on Antigravity and 5 on Claude, and neither hit a denial.

## What the reviews found

Gemini read the diff and the original, checked every rule, and wrote NO_FINDINGS at $0.07.
Opus found seven meaning drifts in 62 changed lines, and the first is the finding of the day:

1. Confidence 8. A fenced block introduced as "Tool result, verbatim" quoted the CLI's denial
   message, which contains two em dashes. The prose gate scans inside code fences, so the editor
   rewrote the quote to pass the gate, and the document now labelled as verbatim a string the CLI
   never emitted. The editor's own answer named this as its least certain edit and its reasoning
   was exactly right: the gate required it. The gate was wrong.
2. Confidence 6. A parenthetical's second sentence, "Not a property of `--restricted`", was
   folded into the first so the disclaimer attached to "sensitive path" instead of to the denial.
3. Confidence 5. A converted parenthetical left "including on Haiku 4.5" dangling off the choice
   list rather than off "is accepted".
4. Confidence 4. `system`/`init` was lifted out of an enumeration labelled "Event kinds seen".
5. Confidence 4. A split sentence moved the antecedent of "so": the empty denial list now followed
   from the model's answer rather than from its not attempting a tool.
6. Confidence 4. "the run behaves" became "the run completes", a weaker claim.
7. Confidence 3. "exactly" survived on a line the pass did not touch.

The lead applied all seven by hand. For the first, `scripts/prose_gate.py` now skips fenced
code blocks (a quoted tool result or log line is a receipt; rewriting it to satisfy a style rule
falsifies it), with four tests in `tests/test_prose_gate.py`, and the verbatim block got its
dashes back. The three documents the gate already runs on kept their counts (README 79, AGENTS
2, RESET 5), so no fence in them had been carrying a hit.

## Reading

The validator did what F22 promised: a document fix lane reproduced on its own validator,
landed through `conductor land`, and cost $1.40 for the edit. The salvage path the F10 consumer
needed (edit as a build lane, reviews applied by hand, no `dispositions.json`) is no longer the
only route for prose.

The same run also showed the limit F22's README paragraph states. The validator checks what a
machine can check, and the machine here demanded that a receipt be falsified. The editor obeyed
the gate over the fact, flagged it, and the cold reviewer at $0.84 caught it at confidence 8.
Reviewer as product, editor as the thing reviewed, once more: Gemini's NO_FINDINGS was wrong on
the one item that mattered and right on the rest, and Opus found it. The fix belongs in the
validator, not in the prompt, and it is in the validator now.

One more thing the run bought. F21 slice 2's fix lane (0.79.0) was refused by the reproduce gate
with "the check does not reproduce the finding" when in fact every change sat under the test
surface and the stricter tests passed on the base: correct work, wrong stage, and the lead
salvaged it by hand. That case is now a named refusal, shipped with this release: `reproduce gate
passed on the base: every change is under the test surface and the stricter tests pass there
too; run this as a build lane`, `test_only: true` on the reproduce block, kind `reproduce`
unchanged, and an AGENTS.md rule that test-only hardening is a build-stage lane.
