# Lane stages, reviewer policy, and reproduce before fix

Build, review, and fix stages, plus the reproduce-before-fix gate, are declared
on the mission.


A lane may declare `"stage"`: `build`, `review`, `fix`, or `adversarial`. A
`review` lane must be read mode; a `build`, `fix`, or `adversarial` lane
must be write mode. Any other mode for a staged lane, or any stage string
outside those four, is refused at load with the lane named.

A mission may also set a top-level `"policy"` object naming which vendors
may run each stage:

```json
{
  "policy": {
    "review": {"vendors": ["anthropic", "google"]}
  }
}
```

Every attempt of a lane with that stage, fallbacks included, must be on an
allowed vendor; the first one that is not is refused by name: `lane
'<name>' (<attempt>) is on vendor '<v>'; policy allows <list> for stage
<stage>`. Reviewer direction is a policy rather than a free choice because
it is not a wash which vendor reviews which: in one controlled study,
Claude reviewing Codex lifted pass rate 71.6% → 89.7%, while Codex reviewing
Claude dropped it 91.4% → 82.8% ([arXiv 2607.21656](https://arxiv.org/abs/2607.21656);
see `docs/ROADMAP-2026-09.md` item A4). A `policy` entry naming an unknown
stage, an unknown vendor id, or a stage no lane declares is refused at load,
so a typo cannot silently allow everything.

A `stage: review` lane with a `base` is judge hygiene's business too (see
below): it is treated exactly like a verdict lane even with no `verdict`
checklist of its own, refused if it could share a vendor with its base, and
lifted the same way with `self_judging: allow`.

## Reproduce before fix

A `fix` lane's change is accepted only if its own check fails without it. The
check runs after the model has worked, on the transplanted test surface, so
this is evidence about the result, not a constraint on the editing order. When a
`stage: fix` write dispatch's fleet has changed the test surface, conductor
adds a detached worktree at the base commit — the mirror image of the clean
gate above, `include` pathspecs instead of `exclude` through the same
temporary index — transplants only the test-surface change into it, and
runs the gate there. That run must **fail**: a check that already fails
against the unfixed base is the reproduction, and only then does the fix's
own gate (or the clean gate, per `test_policy`) run and have to pass.

Three outcomes besides a normal reproduction:

- the fleet changed source but never touched the test surface: refused
  without attempting a base run, no commit, ordinary gate skipped —
  `fix without a reproducing check: no test-surface change`;
- the base run **passes**: the new or changed check reproduces nothing, no
  commit, ordinary gate skipped —
  `reproduce gate passed on the base: the check does not reproduce the finding`;
  when every change sits under the test surface, so the fleet hardened tests
  without fixing anything, the same refusal reads
  `reproduce gate passed on the base: every change is under the test surface
  and the stricter tests pass there too; run this as a build lane` and the
  receipt's `reproduce` block carries `test_only: true`. That is correct work
  at the wrong stage: rerun it as a `stage: build` lane (with
  `test_policy: allow`), whose clean gate is the check that fits it;
- the fleet changed nothing at all, or wrote only its declared deliverable
  (Shape A's fix lane writes `dispositions.json` even after three
  NO_FINDINGS reviews): this gate has nothing to do with it; the existing
  no-op handling applies unchanged, verdict `skipped` (F19).

Both refusals classify as kind `reproduce` (F19): conductor's own verdict on
the transplanted test surface, never a fleet's word.

The receipt gains a `reproduce` block: `ran`, `exit_code`, `timed_out`,
`tail`, `worktree`, `patch_bytes`, and `verdict` — `reproduced`,
`not-reproduced`, `no-check`, `inherited` (below), `validator` (a fix lane's
declared deliverable validator reproduced something instead; see
"Validators" above), or `skipped` (reason in `tail`) for every dispatch that
is not a `stage: fix` or `stage: adversarial` write, including a plain
dispatch with no stage at all. The reproduce worktree is always removed once
the gate ends, exactly like the clean gate's.

## Adversarial test lanes (E16)

A `stage: adversarial` lane's deliverable is a test, not a fix: it must
declare a `base` — the lane it attacks — refused at load otherwise naming
the lane, and every attempt must set `test_policy: allow`, refused
otherwise, because its whole diff *is* a test-surface change and
`test_policy: clean` would trip the clean gate on it by construction.

A clean write that moved bytes is judged in two steps. First, any change
outside the test surface fails the dispatch outright — an adversarial
lane's job is to write a failing test, never to fix or touch source —
`adversarial lane changed source: <files>` (sorted, at most five, then `,
and N more`), kind `adversarial`. Otherwise conductor runs the same
reproduce transplant as a fix lane's, against the dispatch's own base
commit, but reads the verdict the other way round: the gate **failing**
there means the lane found a defect (`reproduced`); the gate **passing**
means it found nothing (`not-reproduced`). Neither verdict fails the lane.
Only a `no-check` (no gate set, no base commit, or no test-surface change
at all) does, with the same "without a reproducing check" wording a fix
lane gets. The lane's own gate and the clean gate never run for this stage
(its test fails on its own tree by construction; the receipt notes them as
skipped) — the reproduce transplant is the only judge.

What lands depends on the verdict: `reproduced` commits normally, and the
lane is buildable, same as any other write lane. `not-reproduced` and
`no-check` both commit nothing — the harness discards the change back to
the base commit entirely (not merely soft-reset, the way a fix's failed
gate leaves work staged for review) so a later lane can still build
cleanly on the unchanged base — and the lane is `ok` for `not-reproduced`
(it did its job; it just found nothing) but not for `no-check`.

A `stage: fix` lane may name an adversarial lane as its `base` (its
`resume` rule is unchanged: `resume` must still be a need or the base). When
that adversarial lane's own final attempt actually reproduced something,
the fix dispatch inherits the check: its reproduce receipt reads `verdict:
inherited`, naming the lane, instead of demanding a fresh test-surface
change of its own — the failing test already sits at the fix's base commit.
The fix's ordinary gate (or the clean gate, per `test_policy`) still runs
and still has to pass; that is where the inherited test is actually
exercised. When the adversarial base was `not-reproduced`, the fix lane
behaves exactly as it would building on any other lane: no inheritance, its
own reproduce-before-fix rule applies in full.

`conductor shape a --adversarial` adds this lane to Shape A; see "The Shape
A launcher" below.

