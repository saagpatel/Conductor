# F15: the Shape C findings, fixed as two Shape A missions

Date: 2026-09-07. Release 0.58.0. Roadmap item F15 (`docs/ROADMAP-2026-11.md`). Source of the
findings: `docs/research/2026-09-07-f9-shape-b-c.md` (Opus 5 cold review of the F3, F1, and F2
build commits, verified on bytes).

Both missions ran through `conductor shape a` on this checkout: Sonnet 5 builds at `hard` under
the launcher's cap, Gemini 3.7 Flash and Grok 4.6 review cold, the fix lane on the build's
resumed thread behind a pause; the lead gated each build tip in a fresh worktree while the
reviewers read (rule 11), and `conductor land` merged, gated, ran `golden check`, and attested.
Every mission file carried `ceiling: {per_hour_usd: 20, per_day_usd: null}`.

## Mission 1: six verify, report, and wall-clock defects (`20260907T083702Z-f15-m1-verify-report-wall`)

Spec: the six defects Opus found still on the tree, each with the current file and line, the
required behaviour, and a test: a read lane whose worktree vanished read as ok (F3 #3); the
precision table admitted an unparsed verdict as zero findings (F1 #4); a disposition naming an
unknown lane vanished with no counter and `dispositions_malformed` was printed nowhere (F1 #5);
`gate_s` omitted the reproduce gate and setup/teardown (F2 #2); `idle_s` reset on resume beside
whole-life columns (F2 #3); `busy` exceeded 1.0 whenever lanes overlapped (F2 #6). Sized at
8 items (6 defects, tests counted twice), 4 modules, scheduler tax for the resume touch: build
cap $15, fix cap $9, budget $28.

| lane | model | outcome | cost |
|---|---|---|---|
| build | Sonnet 5 hard | green under cap, tip 784a900, 10 files, +442/-55 | $6.04 |
| review-gemini | Gemini 3.7 Flash | NO_FINDINGS | $0.19 |
| review-grok | Grok 4.6 | 1 finding: the unknown-lane counter test asserts the JSON field only, not the printed line | $1.22 |
| fix | Sonnet 5 | stopped by the lead at the pause | $0 |

Total $7.45. The lead's fresh-worktree gate on the tip: ruff clean, 1262 passed. The diff was
read item by item: `Verdict.vanished` beside `no_op` (every existing reader unchanged), the F3
skip requires it clear and `Result.failure()` names the vanished tree ahead of the gate check;
`review_lanes[...]["unparsed"]` keeps an unparsed verdict out of the findings count and its
dispositions out of the tally, with an `unparsed` column on the precision row; the report
counts `dispositions_unknown_lane` and sums `dispositions_malformed`, printed on one line under
the precision table, and `_review_fix_label` appends `N malformed`; `_gate_seconds` adds
`reproduce`, `lane_env.setup`, `lane_env.teardown` under the same incomplete rule; `idle_total`
seeds from the prior `result.json`'s `wall.idle_s`; the wall block records `concurrency` and
`busy` divides by it (None on older receipts), with the false `wall_s >= lanes_s` invariant
replaced by `wall_s * concurrency >= lanes_s`.

Grok's finding was real and test-only: a fix lane under the reproduce gate cannot add a test
that passes on the current tree, so the fix lane was stopped and the lead added the printed-line
assertion by hand (6d6e47a) after `conductor land` merged the build tip (91d74af: merge, gate,
golden, attest all ok). Fix-lane spend avoided: about $2.50 for one assertion.

## Mission 2: F1's `dispositions.json` deliverable, per-finding confidence, calibration (`20260907T090336Z-f15-m2-dispositions-deliverable`)

Spec: the three F1 items Opus found passed as built but not built, plus the one piece of
machinery they needed. (1) `REVIEW_TAIL` asks for one `FINDING: <n> <file>:<line> confidence
<1-10>` line per numbered item ahead of the marker line, and `review_verdict` parses them into
`review.items` with an `items_malformed` count, `verdict` and `findings` unchanged. (2) The fix
lane declares `dispositions.json` as a schema-checked E1 deliverable (the launcher writes
`dispositions.schema.json` beside the mission file, `--inline` included, and `salvage --emit`
writes it too); `FIX_PROMPT` asks for the file instead of `DISPOSITION:` lines; `settle()` reads
the kept deliverable copy, validating every entry, and falls back to the prose lines only when no
copy exists, so every receipt on disk still parses; `LaneResult.from_dict` refuses a malformed
entry with the same validator. (3) `deliverable.commit: false` keeps a deliverable out of the
harness commit: `verify.commit_work(..., exclude=())` stages everything and unstages the named
paths, and reports `nothing to commit beyond the excluded deliverable` when they were the only
change. (4) `conductor report`'s precision rows carry the confidences of refused and fixed
findings (joined on lane and index), a `calibration()` (means and matched count), and a
`corrected_rate()` (fixed over findings), printed as one line per vendor under the precision
table. Sized at 8 items (tests counted twice), 7 modules: build cap $16, fix cap $7, budget $27.

| lane | model | outcome | cost |
|---|---|---|---|
| build | Sonnet 5 hard | green under cap, tip 2a5c58f, 16 files, +839/-44 | $9.81 |
| review-gemini | Gemini 3.7 Flash | NO_FINDINGS | $0.20 |
| review-grok | Grok 4.6 | 1 finding, confidence 9: `Result.failure()` and `errors._commit_failed` exempt only the literal `nothing to commit`, so a NO_CHANGES fix lane whose only change is `dispositions.json` fails as `commit did not land` | $0.55 |
| fix | Sonnet 5, resumed thread | fixed: `verify.NO_OP_COMMIT_REASONS` shared by both callers, reproducing test first, tip 490ef0f | $4.17 |

Total $14.72; wall clock 58 minutes, of which the build took 45. The lead's fresh-worktree gate
on the build tip: ruff clean, 1282 passed. The lead had read the same gap in the diff before the
reviews came back (the spec's own "the run's `ok` is unaffected" sentence), which is the second
time this sitting that Grok's one finding matched the lead's own reading of the diff; Grok also
wrote its finding in the new `FINDING:` shape from the spec text alone, with the old review
prompt. `conductor land` on the fix tip: merge, gate, golden, attest all ok (87812c0).

Two receipts the new code wrote about itself: mission 2's `result.json` carries the F15 wall
block from mission 1's code (`wall_s 3456.1, paused_s 18.1, gate_s 119.9, lanes_s 3419.3,
idle_s 0.0, concurrency 2`), and a probe launch of the new launcher wrote
`dispositions.schema.json` beside the mission file with the fix lane declaring
`{"path": "dispositions.json", "schema": "dispositions.schema.json", "commit": false}`, and
`conductor mission --dry-run` loaded it. The first mission to run with the fix lane's deliverable
live will be the next Shape A on this repository.

## What is still open from the Shape C list

Not built here, on purpose: the `c5-review-fix` projection backfill F3 asked for (a hand edit to
a golden fixture, which the F8 sitting rejected in favour of fixing code and re-recording), and
the four latent items (`settle()` reading an answer without `errors="replace"`, a second fix lane
overwriting the first's dispositions in the report, and two shapes not in use). They stay on the
F15 roadmap line as record.

## Cost

$22.17 for the two missions ($7.45 and $14.72), against the roadmap's "about $6 each": the specs
were larger than the estimate (six defects across four modules with a resume touch; four items
across seven modules), and the launcher's own arithmetic priced them at $28 and $27 budgets, of
which $22 was spent. Fix-lane spend avoided by stopping mission 1's fix at the pause: about
$2.50.
