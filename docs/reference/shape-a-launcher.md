# The Shape A launcher

conductor shape a writes the measured default mission from a spec, with cap
arithmetic printed in full.


Shape A is the measured default (AGENTS.md): Sonnet 5 builds at `hard`, Gemini 3.7 Flash
and Grok 4.6 review cold and in parallel, Sonnet fixes on the build's resumed thread, and
the lead judges on bytes. Every mission on record was hand-written and hand-sized, and
mis-sized caps cost money five times. `conductor shape a` writes that mission from the
template in `shape.py`:

```
conductor shape a --spec specs/widget.md --repo ~/Projects/widget \
  --test 'make check' --items 5 --modules 4 --scheduler
```

It prints every term of the cap arithmetic, not just the sum, because the one recorded
$2 shortfall was a module undercount that one printed number would hide:

```
shape a-2026-09-06: 4 lanes, mission 'widget'
gate preflight: passed
ceiling: per_hour none, per_day none
build cap: $5.00 5 spec items + $2.00 scheduler tax + $2.00 2 modules past the second + $1.00 Claude summary = $10.00
review-gemini cap: $1.00 (reads only; rule 7)
review-grok cap: $1.50 (reads only; rule 7)
fix cap: $2.00 fix base + $4.00 4 findings + $2.00 scheduler tax + $1.00 Claude summary = $9.00
grace: $0.25 per claude lane and the grok read lane (E24/F5, on top of its own cap; 3 lanes, $0.75 in the mission budget)
mission budget: lanes $21.50 + $0.75 grace + $1.50 slack = $23.75
```

`--items` and `--modules` are hand counts. Conductor has no notion of a spec item or a
module touched and the launcher is not a forecaster; it is rules 2 and 10 as a function.
The fix lane lands on `--branch` (default `feat/<spec stem>`), `--build-commit` and
`--fix-commit` default to conventional messages scoped to the repo name, `--ports`
claims ports on the build lane, `--test-policy` defaults to `allow` (rule 3), and
`--grok-runs-suite` switches Grok to the prompt that lets it run the gate at the $2.00
cap. `--cap-grace-usd` (default $0.25, ceiling $0.50) sets the grace band (E24/F5) on
every Claude lane the shape declares -- build and fix always, plus `adversarial`
under `--adversarial` and `review-opus` under `--opus-review` -- whose cap is
native, and on the review-grok lane, whose cap is post-hoc; `--cap-grace-usd 0`
disables it. The cap arithmetic prints the band as
its own line, and it never changes the build, fix, or grok cap themselves --
but the mission budget does carry it, once per graced lane. The band is
spend against the same ledger (Grok's is post-hoc, so the dollars are
already gone when it is granted), and leaving it out let five graced lanes
legally spend $2.50 over the summed caps against $1.50 of slack, so the
mission ran out before the fix lane after a green build and clean reviews.

The lanes the shape emits and the arithmetic it sizes them against are two
separate arguments, and `max_cost_usd` comes from the arithmetic alone.
`shape_a` refuses when they disagree -- `adversarial=True` against caps
built without it, or the reverse -- rather than writing a mission whose
budget cannot carry a lane it declares.
`--adversarial` (E16) adds the adversarial lane beside the two reviewers, moves the
fix lane's `base` onto it (its `resume` stays
`build`), adds `adversarial` to the vendor policy and the cap arithmetic, and gives
`FIX_PROMPT` an `<adversarial>` block carrying that lane's answer and diff. The file
is written beside the spec (or at `--out`), never overwritten without `--force`, and
`--dry-run` runs `conductor mission --dry-run` on it.

Every Shape A build lane declares an evidence map (Phase H item 6, operator decision
2026-09-07): `evidence.json` at the repository root, a `commit: false` deliverable against
`evidence.schema.json` written beside the mission file, one entry per spec item with its
status (`built`, `partial`, `not_built`), the files changed for it, the tests that exercise
it, the check the builder ran, and a note. The three reviewers get it in an `<evidence>`
block after the diff, with the instruction to read it as a claim: an item whose files or
tests are not in the change, a check that was not run, or a spec item the map does not name
is reportable like any other finding. The outside review asked for this before any paid
spec-fidelity stage; the three F15 items Shape C caught as passed-but-not-built had no
artifact naming them at all. The map never lands in the repository: the harness keeps it
out of the commit and removes it from the isolated worktree after capture (below), so the
build's tip stays clean for the reviewers to build on.

`--opus-review` is Shape C (F9) as an option on the same launcher: a `review-opus` lane,
Opus 5 at `hard` reading cold beside Gemini and Grok with the same no-quota tail, at a
$4.00 cap ($3.00 for the read plus the rule 10 summary dollar; the three F9 lanes finished
at $2.40 to $2.76). The build is Sonnet, so the mission file carries `self_judging: allow`
and the review policy admits `anthropic`; the fix lane needs all three reviews, its prompt
gains a `<review_opus>` block after the Grok one (before any `<adversarial>` block), and
the dispositions contract names the third lane. Concurrency rises to three so the reviewers
still run side by side. On the F9 receipt Opus reported nothing false, found six defects
the pair missed, and caught three spec items the pair had passed as built, at about three
times the pair's cost; use it on lifecycle, security, and spec-risk work, not by default
(the outside review's reading, `docs/review/2026-09-07-astra-notes.md`, table 10 item 2).

Four more terms round out the arithmetic (F6, all in `shape.py` and `cli.py`):

`--deliverable PATH` (F23) runs a document or data spec through the same shape. The
build lane declares the file (repo-relative, no `..`) as its E1 deliverable in place of
the evidence map, with `--deliverable-validator CMD` as its F22 validator (`{path}`
substituted; conductor runs it on the base bytes and the worktree bytes and refuses a
file that fails it), and its prompt names the file and the command instead of asking for
`evidence.json`. The reviewers read a note in place of the evidence block: what a machine
can check is checked, whether the file still says what the spec asked is their question.
The review-applying lane keeps its name, its `dispositions.json` receipt, and its place in
`pause.before`, but runs as `stage: build`: a file has no test to reproduce, and the
validator already passes on the build's tip, so under `stage: fix` it would be refused
every time with `validator passed on the base too` (the data consumer's drill 2). The
policy then names `build` and `review` only, and `mission.py` reads dispositions from any
lane that declares `dispositions.json`, not only a fix lane. `--adversarial` is refused
with it. The cap arithmetic is unchanged and the forecast reads the same Anthropic build
history as a code mission, so pass `--no-forecast-cap` when the rule 2 figure fits a
one-file spec.

- **`--tests-items N`** (default 0): spec items, already counted once in `--items`, that
  are tests -- rule 11 sizes a spec whose tests are a fifth of the items as if they were
  half, so each one earns a second dollar on the build cap, printed as its own term
  (`$2.00 tests counted twice (2 items)`).
- **`--findings N`** (default 4, the median Grok finding count on this repository): the
  fix cap is `$2.00 fix base + $1.00 per expected finding + $1.00 Claude summary`, term by
  term like the build cap. The old flat $3 default was raised to $7 by hand on seven of the
  last nine missions; `--findings 4` is now that $7 without the hand edit.
- **`--ceiling none|default|H,D`** sets the mission's E9 rolling-spend ceiling
  (`ceiling.py`). `none` (the default) writes explicit null bounds: an attended launch is
  watched, so the ceiling follows `--unattended` at run time, not this launcher (operator
  decision 2026-09-07). `default` copies `ceiling.py`'s own `USD_PER_HOUR`/`USD_PER_DAY`
  constants, read live so the launcher never re-types the numbers. `H,D` sets both bounds
  explicitly. `shape_a_followon` takes the same keyword, and `conductor salvage --emit`
  carries the identical `--ceiling` flag through to the follow-on mission it writes.
- **Gate preflight**: before writing the mission file -- on a plain run and before
  `--dry-run`'s own check -- the launcher runs `--test`'s command once, for real, in a
  throwaway git worktree of `--repo` at HEAD (a directory under `$TMPDIR`, removed on
  every path, never under the repo itself), under `runner.GATE_TIMEOUT`. Exit 127, a shell
  "not found", or any other non-zero exit refuses the launch (`ShapeInvalid`, exit 3) and
  names the exit code and the gate's last ten lines: F1 and F3 were launched with
  `.venv/bin/pytest` and no `PYTHONPATH=src`, and their gates tested the main checkout's
  source from inside a worktree instead of the tree they actually ran in. `--skip-preflight`
  disables the check and says so in the printed output. The preflight is a lead-side check
  only; nothing inside a mission ever runs it.

The launcher writes each lane's prompt text to `prompts/<lane>.md` beside
the mission file and references it with `prompt_file`, so the prompts a
mission ran with sit under version control next to the spec and an edit to
one is a diff. `--inline` keeps every prompt inside the mission file. The
`prefix` stays inline either way: it is a cache key, and `prefix_file`
already exists for the operator.

## Verdict markers and fix dispositions

`REVIEW_TAIL` (the shared close of both review prompts) asks the reviewer
to number every item it reports and end the reply with exactly one final
line: `NO_FINDINGS` when there is nothing to report, or `FINDINGS: N`
naming how many items it numbered. `verdicts.review_verdict` reads that
line, not the whole reply, so a reviewer's narration ahead of its verdict
(Grok routinely writes some) no longer reads as a finding the way
`answer.strip() == "NO_FINDINGS"` did. `FIX_PROMPT` in turn asks for one entry per item either reviewer numbered
in a `dispositions.json` deliverable at the repository root -- a
`{"dispositions": [...]}` object whose entries are
`{"lane": "review-gemini" or "review-grok", "index": <item number>,
"disposition": "fixed" | "refused" | "already" | "wording", "reason": <why>}`
-- `fixed` for one it changed code for, `refused` (with the reason it is
wrong) for one it rejected, `already` for one already true before the fix,
`wording` for one that only asked for a comment or message change. The
prompt asks for the file even on `NO_CHANGES`; an empty array is correct
only when both reviews said `NO_FINDINGS`. `mission.py` reads the kept
deliverable copy first, and falls back to `verdicts.fix_dispositions`,
which parses `DISPOSITION: <lane> <index> <disposition>: <reason>` lines
anywhere in the reply, only when no deliverable copy is present; a line
that opens with `DISPOSITION:` but does not match the shape is skipped and
counted, never raised. Both
parsers feed the ledger report's reviewer finding rate and reviewer
precision tables (see "Ledger report" below). Editing either prompt moves
its `prompt_versions()` id by construction (E17); no fixture needs
touching for that alone (see "What a fixture holds").

## Cost forecast

Before anything dispatches, `conductor shape a` (and `run_mission` itself,
on a launch, a resume, and a dry run alike) compares each lane's cap
against what the same vendor and pipeline stage has actually cost on this
machine: `forecast.history` groups priced, non-dry-run receipts under
`$CONDUCTOR_HOME/runs` by `(vendor, stage)`, excluding known auxiliary
collate, judge-order, and resolver dispatches. Standalone dispatches remain
eligible. `forecast.forecast` reads the declared primary attempt's vendor
and cap, skipping a prepended cheap cascade, against that history's 80th
percentile (nearest-rank method), with a floor of three runs. With three or
four priced samples, p80 is the maximum: the three-run floor is a sample threshold, not outlier
protection. Fewer than three priced samples yields no percentile or numeric
cap warning. A lane warns when it has a cap,
at least three runs of history, and that cap sits under the 80th
percentile:

```
lane 'build': cap $5.00 is under the $7.42 80th percentile of 12 anthropic build runs
```

This is a warning, never a refusal: the mission dispatches exactly as
written either way. `conductor shape a` prints one line per lane after the
cap arithmetic, and the mission's own result (`result.json`, and the
`--dry-run` JSON summary) carries the same figures under `forecast` --
`{"lanes": [...], "warnings": [...]}` -- with every warning also folded
into the result's `notes`. Human and script lanes carry no cost history
worth comparing and are skipped. A golden replay runs in a home with no
run history at all, so its forecast is always empty.

Each lane row also counts `unpriced_runs`, `estimated_runs`, and
`zero_cost_runs`. Missing prices produce a separate warning even with no
priced samples, so an all-unpriced history differs from no history. Estimates
and valid zero-dollar receipts remain in the percentile sample. These counts
make data quality visible; they cannot detect a confidently wrong recorded
price. Use `conductor reprice` to repair known parser drift first.
Timezone-naive `since` values in the Python API mean UTC, as in spend reporting.

The warning used to be where it stopped, and on three launches running the
lead answered it the same way by hand: raise the build lane's `cap_usd` to
the p80 and the mission's `max_cost_usd` by the same difference before
launching. `conductor shape a` now does that itself (F17). Every lane the
forecast warns about, including review lanes, gets its cap
raised to the p80 rounded up to the next whole dollar, and the mission
budget rises by the same amount. Each raise prints its own line:

```
cap raised: build $5.00 -> $9.00 (forecast p80 $8.04, 12 runs)
```

The mission file records the arithmetic under a top-level `caps` block, one
entry per lane that has a cap, raised or not: `rule_2_usd` (the launcher's
own figure), `forecast_p80_usd` and `forecast_runs` (the history it was
read against, null and 0 when there is none), the `cap_usd` written, and
`basis`, either `rule 2` or `forecast p80`. The block is a receipt; the
scheduler never reads it, and the lane's own `cap_usd` stays the live
figure. `--no-forecast-cap` declines the raise: the caps stay at their rule
2 figures, the warning prints as before, and the `caps` block still carries
the p80 that was turned down.

The Python `apply_caps` API also matches generated lane names and inherited
mission caps. Reapplying the same forecast preserves the original arithmetic
and does not increase the budget a second time.
