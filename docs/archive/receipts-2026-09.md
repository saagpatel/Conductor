# Receipts from `docs/RESET-2026-09.md`

Every `## Receipt` and `## Shape A receipt` section that previously followed the September reset note, in that file's order, unedited. `docs/RESET-2026-09.md` is the index; this file is the record. A fleet's word is never evidence — these bytes are.

<a id="r-0.13.0"></a>
## Shape A receipt: C3 liveness (2026-09-05, v0.13.0)

First roadmap item shipped through Shape A. Mission `c3-liveness`: Sonnet 5 build (Gemini 3.7 Flash
fallback), cold reviews on Gemini 3.7 Flash and Grok 4.6 with the no-quota template, fix on Sonnet.

| lane | result | tools | wall | cost |
|---|---|---|---|---|
| build, Sonnet 5 hard | gate green in its own run; **clean gate failed**: it gave `_wait` two required arguments and an existing test calls the old shape; commit undone | 48 | 902 s | $2.38 |
| build, Gemini 3.7 Flash hard (fallback) | pass, clean gate green, 7 new tests | 214 | 872 s | $1.64 |
| review, Gemini 3.7 Flash | one finding, confidence 10, real (heartbeat never written on a normal poll tick) | 66 | 122 s | $0.18 |
| review, Grok 4.6 | the same finding, independently; nothing else | 43 | 207 s | $0.52 |
| fix, Sonnet 5 standard | reproduced the finding with a fake process, fixed, gate green; cross-fleet so no thread reuse | 10 | 166 s | $0.33 |
| **mission** | ok | | 2730 s | **$5.05** |

Against the v0.12.0 receipt (C1 through Sol build, Opus review, Sol fix): $19.40 and 3870 s. C3 is a
smaller item than C1, so this is not a like-for-like ratio; the shape's mechanics are what the receipt
settles. What it showed:

- **The clean test policy did its job on a first-party lane.** Sonnet's build passed its own gate and
  was still rejected, because the original tests failed against the new source. The spec had said
  "any existing test you changed and why" without saying "keep existing call signatures working";
  Gemini kept the old signature working by making the new arguments optional. Spec lesson recorded.
- **Two cheap reviewers converged on one real defect at $0.70 total**, with no quota and no manufactured
  findings: Grok reported "nothing else" explicitly, Gemini reported one item. The fix lane reproduced
  it before touching code.
- **Lead review still found three tidy-ups the reviewers did not** (an unused duplicate constant, a
  swallowed write error, four copies of one guard). None affects behavior; they are the kind of thing
  a consequence threshold correctly omits. The lead pass stays in the shape.
- Gemini's 214 tool calls versus Sonnet's 48 for the same task matches the 3x observed on the probe.

Next receipt: A3/A4 through the same shape, with the signature rule in the spec.

<a id="r-0.14.0"></a>
## Shape A receipt: A3 judge hygiene (2026-09-05, v0.14.0)

Second item through Shape A, and the one that found the shape's two operating rules.

| stage | result | cost | wall |
|---|---|---|---|
| attempt 1, build (Sonnet 5 hard, then Gemini 3.7 Flash) | both cut off by a $3 per-lane cap sized for C3; Sonnet was one lint error from green | $6.02 | 33 min |
| attempt 2, interrupted for a session restart, resumed cleanly | build rerun from scratch | $0.73 | 6 min |
| attempt 2 resumed, build (Sonnet, then Gemini, $7 caps) | **both own gates green (377 tests) and both rejected by the clean test policy**: the spec makes same-vendor judging a load-time refusal and eleven existing tests build exactly that shape, so the original tests fail against the new source by design | $11.40 | 58 min |
| lead salvage | Sonnet's staged work gated (ruff clean, 377 tests), read in full, committed | $0 | 10 min |
| review, Gemini 3.7 Flash | NO_FINDINGS | $0.23 | 2 min |
| review, Grok 4.6 | two findings: a ranking collate's two priced order runs invisible to `conductor spend --by mission` (real, confidence 8); a README sentence claiming a dissent slot prevents unanimity (real, confidence 7) | $0.70 | 7 min |
| fix, Sonnet 5 standard, `test_policy: allow` | reproduced both, fixed both, one new test; gate green | $0.57 | 3 min |
| lead pass | ranking parser given the verdict parser's tolerance for an object wrapped in prose; one new test; 379 tests | $0 | 5 min |
| **total** | ok | **$19.65** | |

Against $19.40 for v0.12.0 on Sol/Opus, so no saving this time, and the whole gap is operating error,
not lane capability: the work that shipped cost $8.90 (the resumed build plus review, fix, lead).

- **Size the build cap to the spec.** A seven-item spec on Sonnet at high effort spends $6 to $7; a $3
  cap bought two partial builds. Rule: about a dollar per spec item for the build lane.
- **A spec that changes what existing missions are permitted to do needs `test_policy: allow` on its
  build lane.** The clean policy (A1) reruns the original tests against the new source; when the new
  source refuses shapes the old tests build, that failure is the feature working. Both builders were
  right and both were rejected. The lead reads every existing-test edit in that case, which is what
  happened here: each edit carried a written reason, none weakened an assertion.
- **The salvage path works.** A build lane's kept worktree with a green own gate is a deliverable; a
  review-and-fix mission runs on it with `cwd` set to the worktree and the diff pasted into the
  mission prompt. Cost of the remainder: $1.50.
- **Grok earned its lane again:** the spend-attribution bug is exactly the kind of cross-module
  consequence a builder misses and a cold reader catches. Gemini's NO_FINDINGS was also correct on
  the items it could see; the bug was outside the diff.

Next: A4 (reviewer direction and reproduce-before-fix) through the same shape with the cap and the
test policy set from the start.

<a id="r-0.15.0"></a>
## Shape A receipt: A4 reviewer direction (2026-09-05, v0.15.0)

Third item through Shape A, run with the cap and policy rules from the A3 receipt applied from the start.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard ($8 cap) | six items, own gate and clean gate green, 24 new tests, committed | $5.84 | 39 min |
| review, Gemini 3.7 Flash | NO_FINDINGS | $0.23 | 2 min |
| review, Grok 4.6 | three findings in the reproduce gate's error handling, all real (a transplant failure counted as a reproduction, a stop not marked interrupted, a fleet self-commit surviving a refusal); **lane not ok** because it ran pytest with a temp dir inside its read worktree and left untracked files | $0.95 | 6 min |
| fix, Sonnet 5 standard, run as its own mission on the build commit | reproduced and fixed all three; verified with throwaway scripts, no tests pinned | $1.01 | 8 min |
| lead pass | pinned the three fixes plus the case the fix missed (a timed-out reproduce gate also proves nothing); 407 tests | $0 | 15 min |
| **total** | ok | **$8.03** | 70 min |

Against $19.40 for v0.12.0 on Sol/Opus for a comparable item: 41 percent of the cost.

- **The cap rule held:** six items, $8 cap, $5.84 spent. No cut-offs.
- **A read reviewer that runs the suite can fail on bytes.** Grok's review was correct and useful, but
  pytest wrote its temp dir under the worktree and conductor refused the lane. The finding lane and
  the byte verdict are separate things; the operator read the findings and ran the fix anyway. Rule
  for review prompts: name a temp dir outside the tree, or say not to run the suite when the build
  lane's own gate is the receipt.
- **A fix lane told to reproduce first did so, but with scripts, not tests.** The prompt said "a
  failing test or a demonstrated wrong result"; it took the second. From A4 on, a `stage: fix` lane
  under the reproduce gate cannot do that: without a test-surface change the fix is refused. Shape A's
  fix lane should carry `stage: fix` now that it exists.
- Gemini's NO_FINDINGS was again correct on what a diff-only read can see; all three of Grok's
  findings needed reading the surrounding runner code.

Next: A5 signed lane receipts, or B5 early cancel and best-of-n, through the same shape with `stage`
and `policy` set on the mission itself.

<a id="r-0.16.0"></a>
## Shape A receipt: B5 early cancel and best-of-n (2026-09-05, v0.16.0)

Fourth item through Shape A, and the first mission that declared stages and a vendor policy on
itself, with the fix lane under the reproduce gate.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $7 cap, no Gemini fallback (the A3 rule: a Gemini fallback would let the Gemini reviewer judge its own vendor) | **cut off by the cap** one README assertion from green: ruff clean, 10 of 11 new tests passing, every spec item present | $7.03 | 25 min |
| lead salvage | one phrase aligned in the README, full gate green (418 tests), committed | $0 | 5 min |
| review, Gemini 3.7 Flash | NO_FINDINGS | $0.15 | 1 min |
| review, Grok 4.6 | three findings, all real: omitted lanes leaked back into a capped collate; a cancelled lane's receipt and its report line carried two different cancel strings; a lane cancelled mid-run rendered as an ordinary failure in the table | $0.72 | 6 min |
| fix, Sonnet 5 standard, `stage: fix` | wrote one failing test per finding, fixed each; **the reproduce gate ran live**: the three tests transplanted onto the base failed there, receipt `reproduced` | $1.59 | 18 min |
| lead pass | one keyword argument defaulted; 421 tests | $0 | 5 min |
| **total** | ok | **$9.49** | 60 min |

Against $19.40 for v0.12.0 on Sol/Opus: 49 percent.

- **The cap rule needs a weight for concurrency work.** Six items at a dollar each was $1 short: threading,
  cancel semantics, and resume interaction cost more per item than plumbing does. Rule: a dollar per item,
  plus two when an item touches the scheduler, the runner's wait loop, or resume.
- **The reproduce gate changed the fix lane's behavior by construction.** A4's fix lane verified with
  scripts; B5's, told the gate would refuse a fix without a failing test, wrote the test first every time.
- **Stages and policy cost nothing at run time and caught a real shape at load time:** the Gemini build
  fallback had to go, because a reviewer must not share a vendor with a build attempt.
- **Grok, four for four.** Every Shape A run so far, Grok found real defects outside the diff's own lines
  and Gemini reported NO_FINDINGS correctly on what the diff alone shows. The pair is complementary, not
  redundant; a single reviewer would have shipped every one of these.

Next: A5 signed lane receipts, or C2 the pause primitive, through the same shape.

<a id="r-0.17.0"></a>
## Shape A receipt: A5 signed lane receipts (2026-09-05, v0.17.0)

Fifth item through Shape A, run in parallel with C2 as a second mission against the same checkout
(separate branches, separate caps). Design calls made by the lead, not the fleet: HMAC-SHA256 over a
DSSE envelope with a conductor-owned key, because the runtime is standard library only and the
property wanted is "not editable after the fact without the key", which a shared secret gives.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $8 cap | green on its own gate and the clean gate: `attest.py`, an envelope per spawned dispatch, a signed link per settled lane, `conductor attest`, README, 24 tests | $5.46 | 28 min |
| review, Gemini 3.7 Flash | NO_FINDINGS after running the suite | $0.19 | 2 min |
| review, Grok 4.6 | two findings: `conductor attest` re-derived base and tip from the isolation block, so an honest non-isolated lane read as tampered (real); an attestation write failure sinks `ok` (spec-mandated, kept) | $0.68 | 6 min |
| fix, Sonnet 5 standard, `stage: fix` | one failing test for the first finding, then `base_commit` and `tip_commit` on the receipt itself so the verifier compares like with like; rejected the second with the right argument; reproduce gate `reproduced` | $2.60 | 6 min |
| lead pass | fresh-worktree gate green (446 tests); one widened exception handler so a key race cannot crash a finished dispatch | $0 | 10 min |
| **total** | ok | **$8.92** | 52 min |

Against $19.40 for v0.12.0 on Sol/Opus: 46 percent.

- **Two missions on one checkout worked.** Each lane ran in its own worktree; the only shared state was
  the ledger directory, and the two runs never touched each other's branches. Total for both: $17.16.
- **The cap rule held:** six items at a dollar each plus two for the runner and mission hooks, $8 cap,
  $5.46 spent. The build had room to write 24 tests instead of stopping at the spec's minimum.
- **A fix lane can say no.** Sonnet rejected Grok's second finding as spec-mandated and explained why,
  rather than patching it. That is the reproduce gate doing its job: no failing test, no edit.
- **The verifier bug was in the lead's spec, not the build.** The spec said to compare the statement with
  `result.json` on base and tip without saying where those live on the receipt; the build guessed the
  isolation block. Grok caught it cold. A spec that names a field should say which file carries it.

<a id="r-0.18.0"></a>
## Shape A receipt: C2 pause primitive (2026-09-05, v0.18.0)

Sixth item through Shape A, run in parallel with A5 against the same checkout and merged second.
Design call by the lead: the pause points are mission data (`pause.before` lane names and
`pause.spend_usd`), answered with `conductor mission --resume ID --answer continue|stop`, never a
request a fleet's output can make. A fleet's word is not evidence, and the operator's standing rule on
outward actions needed a mechanism, not a parser for prose.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $10 cap (scheduler and resume both touched) | green on its own gate and the clean gate: `pause` key, parking, `pause.json`, `--answer`, exit 4, README, 15 tests | $4.82 | 28 min |
| review, Gemini 3.7 Flash | NO_FINDINGS | $0.08 | 1 min |
| review, Grok 4.6 | two findings, both real: a SIGINT while parking wrote an unanswered pause and exited 4 instead of interrupting; `--answer stop` still rendered the "resume with" line and exited 4 | $0.82 | 8 min |
| fix, Sonnet 5 standard, `stage: fix` | one failing test per finding, then two guard conditions; reproduce gate `reproduced` | $2.52 | 5 min |
| lead pass | merged onto 0.17.0 (three both-added hunks, no semantic overlap), scheduler nesting flattened back to one level, gate green (463 tests) | $0 | 20 min |
| **total** | ok | **$8.24** | 53 min |

Against $19.40 for v0.12.0 on Sol/Opus: 42 percent.

- **The second of two parallel merges is the lead's work, and it was small:** every conflict was two
  features adding a field or a section at the same place. Worth the parallelism: both items shipped in
  under an hour of wall clock for $17.16.
- **Read the merged diff for shape, not just for conflicts.** The build wrapped the whole scheduler in an
  `else:` to short-circuit the operator's stop, a 366-line diff for a 245-line change. An empty pending
  list did the same job flat. The reviewers were right not to flag it (it was correct); the lead is the
  one who owns readability.
- **Grok, six for six.** Both findings were interactions the spec named in one clause each ("a stop
  while parking is handled as today"; "a `stop` answer is terminal") and the build got half right.

<a id="r-0.19.0"></a>
## Shape A receipt: B2 cache-friendly prompts (2026-09-05, v0.19.0)

Seventh item through Shape A, and the first of three missions run at once (B2, B3, C4 against one
checkout). Probe first: the installed Claude Code CLI (2.1.261) has both flags the roadmap names.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $6 cap, `test_policy: allow` (an existing test pins the exact Claude argv) | **cut off by the cap** one README phrase and one lint line from green: every spec item present, 474 of 476 passing | $6.03 | 25 min |
| lead salvage | wrapped one line, made the two README tests whitespace-tolerant, full gate green (476 tests), committed | $0 | 15 min |
| review, Gemini 3.7 Flash, on the salvaged commit | NO_FINDINGS after running the suite | $0.09 | 1 min |
| review, Grok 4.6 | NO_FINDINGS after running the suite; priced $0.0055 over its $1 cap when it ended, so the lane reads as failed (Cursor's cap is a verdict after the run) | $1.01 | 7 min |
| fix, Sonnet 5 | skipped: its need was not ok, and both reviews were empty anyway | $0 | 0 |
| lead pass | merged, gate green, release | $0 | 5 min |
| **total** | ok | **$7.13** | 55 min |

Against $19.40 for v0.12.0 on Sol/Opus: 37 percent.

- **The cap rule needs a weight for breadth, too.** Five items at a dollar each was $2 short because they
  spread over four modules (`fleets`, `mission`, `runner`, `spend`) and four existing test files. Rule
  amendment: a dollar per item, plus two for scheduler, wait-loop, or resume work, plus one for every
  module past the second.
- **A reviewer's cap is not a finding.** Grok's review was complete and correct; the half-cent overage
  cost the fix lane its start. Size a Grok review cap at $1.50 when the pasted diff is over 30 KB.
- **The salvage path is now routine:** gate the kept worktree, read it, commit it, review it as its own
  mission with the diff in the prompt. Twice in seven runs, both times cheaper than a rebuild.

<a id="r-0.20.0"></a>
## Shape A receipt: B3 cheap-first cascade (2026-09-05, v0.20.0)

Eighth item through Shape A, second of the three parallel missions. The cascade is a mission
option, never a default, as the roadmap's own risk sentence demands.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $7 cap | green on its own gate and the clean gate: `cascade` key, per-lane opt-out, snapshot round trip that keeps the cascade attempt and the real primary as siblings, `escalated` per lane, `escalation` block, README, 13 tests | $6.64 | 30 min |
| review, Gemini 3.7 Flash | one finding, confidence 10, real | $0.13 | 1 min |
| review, Grok 4.6 | the same finding, reproduced by hand; then $0.02 over its $1 cap at the end, so the lane reads as failed and the fix never started | $1.02 | 8 min |
| fix, Sonnet 5 | skipped: its need was not ok | $0 | 0 |
| lead fix | one failing test first, then the two-line fix: the escalation summary now reads `Lane.cascaded` instead of re-deriving the target set, so an opted-out lane's real primary is no longer counted as a cheap attempt; fresh-worktree gate green (476) | $0 | 10 min |
| lead pass | merged onto 0.19.0 (two both-added hunks), gate green (489 tests) | $0 | 10 min |
| **total** | ok | **$7.79** | 60 min |

Against $19.40 for v0.12.0 on Sol/Opus: 40 percent.

- **Both reviewers, same bug, independently.** The build's own docstring argued the opt-out "is not
  distinguishable from here" while the field that distinguishes it sat three screens up. A model
  rationalizing a shortcut in prose is the tell; both reviewers read past the prose to the field.
- **Grok's cap, second time today.** Rule: Grok review cap $1.50 when the diff is over 30 KB (now in
  AGENTS.md). A complete review that prices over its cap is a complete review; the mission still
  has to say the lane failed, because a cap is a cap.
- **The lead fixing a two-line bug with a pinned test cost less than a fix lane** ($0 and ten minutes
  against $2.50 and five), and the receipt says who did it.

<a id="r-0.21.0"></a>
## Shape A receipt: C4 per-lane setup, teardown, ports, and includes (2026-09-05, v0.21.0)

Ninth item through Shape A, third of the three parallel missions, and the one whose review stage
had to be run three times before a reviewer wrote anything down.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $8 cap | green on its own gate and the clean gate: `ports.py` with exclusive claim files, setup and teardown through `run_tests` with one env dict, includes kept untracked through a worktree-scoped excludes file, four attempt keys and four flags, `gc` for stale claims, README, 20 tests | $5.62 | 30 min |
| review, Gemini 3.7 Flash (first) | killed by the cap at 261 tool calls: it ran the suite as a background task and polled the log | $1.43 | 4 min |
| review, Grok 4.6 (first) | exit 0 with a final message that said its findings were "listed in the review reply" and listed none | $0.74 | 8 min |
| review, Gemini (second, $1.50 cap, 150-call ceiling, told to run the suite in the foreground) | killed by the tool ceiling at 196 calls, same polling loop, no answer | $0.43 | 4 min |
| review, Grok (second) | three findings, all real: the clean and reproduce gates ran without the lane env (a shadowed local variable); claim files were not released when the dispatch body raised; the per-worktree excludes file replaced the operator's global excludes | $0.66 | 8 min |
| fix, Sonnet 5 standard, `stage: fix`, as its own mission | one failing test per finding, all three fixed; global excludes are now seeded into the per-lane file and `extensions.worktreeConfig` stays on by design; reproduce gate `reproduced` | $3.00 | 26 min |
| lead pass | fresh-worktree gate green (484); merged onto 0.20.0 with no conflicts; gate green (510 tests) | $0 | 20 min |
| **total** | ok | **$11.88** | 100 min |

Against $19.40 for v0.12.0 on Sol/Opus: 61 percent, the most expensive Shape A run so far, and
$2.60 of it was review lanes that wrote nothing.

- **Gemini cannot run this suite.** `agy` backgrounds a long command and polls its log every few
  seconds; a two-minute pytest run is a hundred tool calls of polling. Twice killed, never an answer.
  Rule 5 now says Gemini reads the diff and the code and does not run the suite here.
- **"See the findings above" is an empty review.** Grok's first pass hit the plan-file trap in reviewer
  rule 6 from the other side: it referred to a list it never wrote. The rerun with the diff in the
  prompt instead of the working tree produced three real findings for $0.66.
- **The shadowed variable was the kind of bug only a reader catches:** the build threaded `env` through
  the gate helpers and then assigned a local `env` for `GIT_INDEX_FILE` that reached the subprocess
  instead. Every test passed because no test asked the clean gate for a port.
- **Three parallel missions, receipts in order:** B2 $7.13, B3 $7.79, C4 $11.88, $26.80 for three
  releases in about two and a half hours of wall clock. Every one needed the lead's hands (a salvage, a
  two-line fix, a rerun review); the fleet spend was the cheap part.

<a id="r-0.22.0"></a>
## Shape A receipt: C5 structured error kinds (2026-09-05, v0.22.0)

Tenth item through Shape A, and the first mission run on 0.21.0 with the new features engaged on
purpose: a `prefix`, a `cascade` (Sonnet at medium under $4 before Sonnet at hard under $9), `ports: 1`
on the build lane, `pause.before: ["fix"]`, Gemini told not to run the suite, Grok capped at $1.50.

| stage | result | cost | wall |
|---|---|---|---|
| build, cascade attempt, Sonnet 5 standard, $4 cap | cut off by the cap, gate red | $4.02 | 19 min |
| build, Sonnet 5 hard, $9 cap | **cut off by the cap $0.05 over, after its gate went green** (551 tests): Claude's own budget stop lands on the final summary | $9.05 | 41 min |
| lead salvage | fresh gate on the kept worktree green (551), diff read, committed | $0 | 15 min |
| review, Gemini 3.7 Flash, read-only | NO_FINDINGS | $0.08 | 1 min |
| review, Grok 4.6, $1.50 cap | two findings, both real: a breaker kill under a cap was classified `cap` because `budget.settle` marks every kill exceeded; `conductor runs` read `kind` off old receipts instead of computing it | $0.71 | 9 min |
| fix, Sonnet 5 standard, `stage: fix`, as its own mission | one failing test per finding; `_capped` now needs independent evidence; legacy receipts reclassified; reproduce gate `reproduced` | $0.93 | 8 min |
| lead pass | fresh-worktree gate green (554); merged with no conflicts; gate green | $0 | 15 min |
| **total** | ok | **$14.78** | 110 min |

Against $19.40 for v0.12.0 on Sol/Opus: 76 percent, the most expensive Shape A run, and $4 of it was
the cascade experiment.

- **Live receipts, all five features:** `conductor attest` verified the 4-link chain of the first
  run; the build receipt records port 59629 claimed and released; the cascade block reads `0 of 1
  passed, 1 escalated, $4.02 cheap, $9.05 after`; the cache block reads 98.7 percent hits; every
  prompt starts with the prefix. The pause did not fire because the mission never reached the fix
  lane; still owed a live receipt.
- **A cascade is for small items.** Sonnet at medium under $4 cannot build a five-item spec, and the
  escalation rate of 1.0 says so on the receipt. Use the cascade when the cheap attempt has a real
  chance: one or two items, or a fix lane. Rule 10 in AGENTS.md.
- **Add a dollar to every Claude build cap for the summary.** Claude stops itself at exactly the cap,
  and the final message costs; twice today a green build was lost for five cents. The cap estimate
  is the work; the cap is the estimate plus one.
- **Gemini read-only is the right shape here:** $0.08, one minute, and a correct empty answer, against
  two kills and no answer when it ran the suite.

<a id="r-0.23.0"></a>
## Shape A receipt: C7 golden-mission suite (2026-09-05, v0.23.0)

Eleventh item through Shape A, run on 0.22.0 with no cascade (five items across five modules), the
build cap sized by rule 2 plus rule 10 ($11), `pause.before: ["fix"]` again, Gemini read-only, Grok at
$1.50. The build landed green under its cap on the first attempt; what the lead judged on bytes was
the fixture set, and both reviewers found what the diff could not show.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $11 cap | green own gate (583 tests), 44 files, one commit | $8.90 | 35 min |
| review, Gemini 3.7 Flash, read-only | NO_FINDINGS on the pasted diff; its worktree gate then failed | $0.13 | 1 min |
| review, Grok 4.6, $1.50 cap | two findings, both real: the five `stdout.log` transcripts were never committed (the operator's global excludes carry `*.log`, and the fleet's worktree inherits them), and every attestation's DSSE payload still held real paths under the scrub; its worktree gate then failed | $1.00 | 6 min |
| fix lane | skipped: both reviews "not ok" on their gates, so the pause never fired | $0 | |
| fix, Sonnet 5 hard, as its own mission with `pause.before: ["fix"]` | **the pause fired live** (exit 4, `pause.json`, answered `continue`, resumed six seconds later); one failing test per finding; transcripts stored as `stdout.jsonl`, attestations dropped, `scrub_guard` decodes base64; fixtures re-recorded; own gate green (586), reproduce `reproduced`; **clean gate red**: the base tree's fixture test against the new source | $2.18 | 17 min |
| lead salvage | kept worktree verified on bytes (fully staged, nothing ignored, guard empty, `golden check` clean, transcripts hash identically to the first recording), committed, merged, fresh-worktree gate green (586) | $0 | 30 min |
| **total** | ok | **$12.21** | 90 min |

- **The fresh-checkout guard earned its place on day one.** The parametrized fixture test failed in
  both review worktrees and in the lead's fresh gate while the build's own gate was green against
  files still on disk. A fixture never carries a name an operator's global excludes can match; the
  recorder now writes `stdout.jsonl` and the test pins that no `.log` is ever recorded.
- **Rule 3 covers fix lanes that rewrite fixtures.** The fix re-recorded both fixtures, so the clean
  gate reran the base tree's fixture test against the new source and failed the way B5's did;
  `test_policy: allow` was owed on that lane. Salvaged from the kept worktree at no extra cost.
- **Review lanes carry the mission gate.** Both reviewers were right and both were "not ok" because
  the suite failed in their worktrees, which skipped the fix lane. When the build is expected to break
  the suite in a fresh checkout, the reviews need their own `test` or the fix runs as its own mission.
- **A plain-text scrub guard is blind to base64.** Grok decoded an attestation payload by hand; the
  guard now does the same for any run of 64 or more base64 characters.
- **Live receipts:** the pause primitive (owed since v0.18.0), `conductor attest` on both missions,
  and a deterministic recorder: the transcripts re-recorded by the fix lane carry the same sha256 as
  the build's recording. Fixtures: 1.0 MB and 924 KB after elision, from 12 MB of raw transcripts.

<a id="r-0.24.0"></a>
## Shape A receipt: D2 taint tracking (2026-09-06, v0.24.0)

Twelfth item through Shape A and the first of Phase D, run in parallel with D1 on 0.23.0. Build cap
$11 by rule 2 plus rule 10, `test_policy: allow` on build and fix, `pause.before: ["fix"]`, Gemini
read-only, Grok at $1.50. Every stage landed on the first attempt.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $11 cap | green own gate (612 tests), 7 files, one commit; fresh-worktree gate green | $8.91 | 28 min |
| review, Gemini 3.7 Flash, read-only | NO_FINDINGS | $0.25 | 2 min |
| review, Grok 4.6, $1.50 cap | two findings, both real: the collate's taint check at load looked at sinks only while the runtime check sees every candidate when `candidates` is 0, so an off-Claude collate could load and then abort at collate time as an uncaught refusal; the collate refusal did not name the lane | $1.16 | 5 min |
| pause before fix | fired live, answered `continue` five minutes later | $0 | 5 min |
| fix, Sonnet 5 standard, resumed thread, $3 cap | one failing test per finding; the load-time pool now mirrors `_collate_candidates`; the refusal names the lane; reproduce `reproduced`; own gate green (614) | $2.85 | 6 min |
| lead pass | fresh-worktree gate on the fix tip green (614); merged clean; merge gate green | $0 | 20 min |
| **total** | ok | **$13.17** | 70 min |

- **Taint is a Claude-only guarantee, on purpose.** `--disallowedTools` is the only headless tool
  deny list among the three fleets, so a tainted lane on antigravity or cursor is refused at load
  rather than run unguarded. A fleet earns taint support with a probe of its own deny mechanism.
- **Load-time checks must mirror runtime selection.** Grok's first finding was a mismatch between
  what validation looked at and what the collate actually receives; the fix makes the two read the
  same list. Worth a glance whenever a new lane-level property feeds a mission-level dispatch.
- **Golden fixtures survive schema growth through the replayer, not by editing fixtures.** D2 taught
  `_backfill_snapshot` to fill new lane fields and run on replay; D1, built in parallel, hand-edited
  the two fixtures instead. The lead kept D2's approach at merge time and dropped D1's fixture edits.
- **The pause primitive is now routine:** second and third live receipts today, both answered
  within five minutes, neither costing a cent.

<a id="r-0.25.0"></a>
## Shape A receipt: D1 conflict-aware collate (2026-09-06, v0.25.0)

Thirteenth item through Shape A, run in parallel with D2 on 0.23.0 and merged second. Build cap $10
by rule 2 plus rule 10, `test_policy: allow` on build and fix, `pause.before: ["fix"]`, Gemini
read-only, Grok at $1.50. The build landed first try; the fix lane was cut at its cap with the work
done and only its summary unpaid, and the lead salvaged it.

| stage | result | cost | wall |
|---|---|---|---|
| build, Sonnet 5 hard, $10 cap | green own gate (605 tests), 9 files, one commit; fresh-worktree gate green | $7.21 | 32 min |
| review, Gemini 3.7 Flash, read-only | NO_FINDINGS | $0.07 | 1 min |
| review, Grok 4.6, $1.50 cap | two findings, both real and both outside the spec's five items: a resume re-dispatched and re-paid the resolver because nothing kept a prior resolve; the resolver's tokens were missing from the mission totals | $0.82 | 6 min |
| pause before fix | fired live, answered `continue` three minutes later | $0 | 3 min |
| fix, Sonnet 5 standard, resumed thread, $3 cap | **cut off $0.07 over the cap after its own gate went green** (607): both tests written, both fixes in, the line-length fix applied, the summary never sent | $3.07 | 6 min |
| lead salvage | ruff clean and 607 green on the kept worktree; the resolver's cache tokens folded into the cache summary (a lead edit pinned by the fleet's own test); committed; merged over D2 with three both-added hunks; D1's hand-edited golden fixtures dropped for D2's replay backfill | $0 | 25 min |
| **total** | ok | **$11.17** | 75 min |

- **Rule 10 covers fix lanes too.** Claude stops itself at exactly the cap and the final summary
  costs; a $3 fix cap lost a green fix for seven cents. Every Claude cap, build or fix, is the
  estimate plus one dollar.
- **Grok reads across the lifecycle, not just the diff.** Neither finding was visible in the
  spec's five items: resume accounting and the token roll-up are mission-lifetime behavior the
  spec never mentioned. Seven runs, seven real reports from Grok outside the diff's own lines.
- **Parallel missions that both grow the mission snapshot collide in the fixtures.** D1 edited the
  two golden fixtures by hand to add its new keys; D2 taught the replayer to backfill. The
  replayer's way is the one that survives the next schema change without touching recorded bytes.
- **Live receipts:** `merge-tree` conflicts and overlap hotspots computed on real sink diffs will
  come with the first best-of-n mission on 0.25.0; the resolver's pause, resume, and receipt chain
  (`conductor attest`, six links across the two missions) are on record today.

<a id="r-0.26.0"></a>
## Shape A receipt: D3 inline agent definitions (2026-09-06, v0.26.0)

Fourteenth item through Shape A. Probed first (`docs/research/2026-09-06-live-probe-inline-agents.md`,
about $0.45): Claude's `--agents` plus `--agent` applies a persona in both modes and its `tools` list
is a real restriction, agy's `--agent` selects from disk and fails open on an unknown name, Cursor has
nothing. So the item was built for the Claude fleet and refused on the other two. Build cap $9, fix
cap $5 (rule 10 as amended), `pause.before: ["fix"]`.

| stage | result | cost | wall |
|---|---|---|---|
| probe, lead | six Claude runs and one agy run on scratch repos; the last-message trap found (a persona shapes the first message, the `result` envelope keeps only the last) | $0.45 | 20 min |
| build, Sonnet 5 hard, $9 cap | green own gate (667 tests), 9 files; two existing tests edited because `KINDS` grew; fresh-worktree gate green apart from one timing test that flaked under three fleets' load and passed three times alone | $6.12 | 32 min |
| review, Gemini 3.7 Flash, read-only | NO_FINDINGS | $0.11 | 1 min |
| review, Grok 4.6, $1.50 cap | two findings, both real: a review-stage lane with a non-list `tools` crashed load with a `TypeError` instead of a refusal naming the lane; the missing-init-event completeness test accepted an empty stream, recasting `no_answer` as `agent`; **complete review priced $1.96, lane failed on the cap, fix skipped** | $1.96 | 9 min |
| fix, Sonnet 5 hard, as its own mission with `pause.before: ["fix"]` | pause fired and answered within a second; one failing test per finding; validation now runs before the write-tools check; completeness requires a parsed result event; reproduce `reproduced`; own gate green (669) | $1.08 | 8 min |
| lead pass | merged clean; merge gate green (669) | $0 | 15 min |
| **total** | ok | **$9.72** | 85 min |

- **Probe before build paid for itself again.** Twenty minutes and under a dollar settled which
  fleets can do this at all, and found the trap the build had to design around: the persona check
  reads the stream's init event, never the answer.
- **Rule 7 amended:** Grok's cap is $2.00 when it runs the suite on this repo. The suite now takes
  two to five minutes under load, and a complete review was lost to the cap for the third time.
- **`tools` on an inline agent is an allow list**, the mirror of D2's deny list, and the init event
  proves which tools the session actually had. Together they give a lane a floor and a ceiling.

<a id="r-0.101.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 17: re-pricing the stored receipt corpus after wave 16 moved the token convention, shipped as v0.101.0 ($0.00, no fleet lanes)

Wave 16 corrected `usage_from_raw`: `cache_read` sits beside `input_tokens` for cursor and
antigravity, not inside it. That fixed the parser going forward and left the ledger reading a
**mix of two conventions**: every receipt written before v0.100.0 carries `input_tokens: 0` for
those two fleets, while every receipt written after carries the vendor's real input.
`conductor spend`, `conductor report`'s `usd_per_item`, the rolling ceiling, and the E14 forecast
all read that ledger, so the mix was not cosmetic: it understated the input component of every
cursor and antigravity run on record and, through the forecast, the caps sized from them.

This wave re-parsed the stored bytes with the current parser and rewrote what actually moved.

### What moved

860 receipts scanned under `~/.conductor/runs`. **254 moved**, none of them `cost_basis: reported`
(a reported figure is the vendor's own number and was never recomputed):

| fleet | receipts | input token delta |
|---|---|---|
| cursor | 141 | +28,022,226 |
| antigravity | 113 | +26,500,536 |

Cost over the moved receipts: **$307.84 to $502.71, +$194.87.** Per day, on the moved receipts
only: 09-03 +$1.96, 09-05 +$22.85, 09-06 +$39.31, 09-07 +$38.89, 09-08 +$91.86. Every wave cost
reported in this session's earlier receipts is understated on its input component by roughly that
share; the ledger is now the corrected figure and those older receipt headlines are not.

Two receipts were left alone because re-parsing their stdout returns no usage at all (both codex,
watcher-sourced figures with nothing in the stream to re-derive). 23 more carry no usage object.

### The trap, which is the reason step 1 was a dry run

The first dry run reported 275 moving receipts, not 254. The extra 21 were **codex receipts whose
token counters did not move at all** and whose cost still doubled: `$10.90 to $21.23` on the
largest. Their stored estimate is not wrong. `prices.py`'s `gpt-5.6-sol` entry has an `as_of` of
2026-09-08 and a promotional note; those runs are from 09-03 and 09-04 and predate the W6 `price`
block, so they carry no record of the table that priced them. Recomputing them would have applied
today's rate to last week's spend and added **$94.33** of fiction to the ledger ($101.98 to
$196.31 across the 23 codex estimates that differ from today's table).

The rule the script now encodes: **a receipt whose token counters did not move is not a re-pricing
wave's business.** Re-price what the parser changed; never let a price-table revision walk
backwards through stored history.

### Method and verification

One-off script, dry run first, nothing written until the summary was read. The corpus's 860
`result.json` files were archived to `~/.conductor/runs-receipts.backup-20260908.tgz` before the
first write (rewriting receipts is an irreversible action outside a repo). Only the five `usage`
token counters, `total_tokens`, and `cost_usd` plus `price` where `cost_basis == "estimated"` were
rewritten, at `json.dumps(..., indent=2)` with no trailing newline to match `runner.py`'s own
write. Verdicts, `ok`, commit state, reported costs, and every other field were left alone. The
dry run re-run afterwards reports **0 receipts moving**. Gate green on the merged tree: ruff clean,
2100 passed.

No source change: the parser was already right as of v0.100.0. This wave is the data catching up
to it, plus the two rules above.

### Open design call, not built

A stored receipt whose parser convention changed will happen again, and the one-off script that
did it lives in a scratchpad. `conductor reprice --dry-run` would own this properly: re-parse,
diff, refuse to touch a `reported` cost, refuse to move a receipt whose counters did not move,
back up before writing. Proposed, not built; it is the operator's call whether conductor should
carry a command that rewrites its own history.

<a id="r-0.100.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 16: outputs.py, and a token convention the streams falsify, shipped as v0.100.0 ($12.41, corpus mining then two cold reads then one Grok 4.6 write lane)

Wave 15 left two findings unshipped because both rested on unevidenced claims about what a vendor
CLI emits. This wave started by answering that question the cheap way, and the method is the
lesson: **this repository has 495 stdout logs on disk. Mine the corpus before proposing a parser
change, and before paying for a probe.**

## What the corpus settled, for free

Both wave 15 claims are dead. 0 of 145 cursor streams contain an error event of any kind; 0 of 124
antigravity streams have any event after the result event. Receipts in
`docs/research/2026-09-08-stream-shapes-from-the-receipt-corpus.md`, written so neither is filed a
fourth time.

The same method then refuted 11 of the 15 findings the Gemini read produced, every one at zero
occurrences across the corpus: claude error subtypes always carry `is_error: true` (0 of 176
counterexamples); no cursor event other than a result carries a `result` or `is_error` key; no agy
result payload omits its status; no agy `event: "error"` exists; no agy result carries a non-dict
`result`; no cursor run reports reasoning tokens; no stream anywhere carries two distinct session
ids; and the spoofed-envelope story has no supporting bytes, since the only 7 non-JSON stdout lines
in the corpus are the pretty-printed array transcripts. The Grok read reached the same conclusions
independently, mined the corpus itself, and filed exactly one finding -- explicitly declining to
claim it knew Cursor's billing identity. That restraint is why its one finding was trusted.

## The one finding, and it was in the ledger

`usage_from_raw` subtracted `cache_read` out of `input_tokens` for cursor and antigravity. The
module docstring stated the assumption and its own safety condition: "Cursor's `cacheReadTokens` is
assumed to follow the same inside-the-input convention; every live run so far reported zero, so the
assumption has not yet cost anything."

Both halves were false. **139 of 142 cursor result envelopes report `cacheReadTokens` greater than
`inputTokens`** -- typically 215,915 input beside 4,765,056 cache read -- and exactly 1 of 142
reports zero. **98 of 116 antigravity envelopes** are the same shape. A quantity nested inside
another cannot be twenty times larger than it, so the nesting the convention assumed is
arithmetically impossible for those streams, and `max(0, input - cache)` recorded **zero billed
input tokens** for essentially every cursor and antigravity run conductor has ever priced.

Two independent confirmations, both on bytes: **116 of 116 antigravity envelopes carrying a
`total_tokens` satisfy `total_tokens == input_tokens + output_tokens` exactly**, never including
cache -- the vendor's own arithmetic treats input as standalone. And the build lane found the
mirror case: Claude's `cache_read_input_tokens` exceeds `input_tokens` on 15,824 of 16,000 usage
objects (typical `input_tokens: 2` beside `cache_read_input_tokens: 27,539`), which is exactly why
Claude, which reports uncached input natively, was never subtracted -- and why subtracting would
have zeroed it the same way. `codex` is untouched: OpenAI documents `cached_input_tokens` as
genuinely nested, and cursor/agy evidence is not a reason to move it.

**The blast radius is every cursor and antigravity cost conductor has recorded.** All twelve
affected golden receipts read `input_tokens: 0` before this wave; eleven of them literally zero,
one at 6,853 where the vendor sent 237,253. `cache_hit_rate` was inflated by the same zero (one
fixture moved 0.539 to 0.454). Existing receipts under `~/.conductor/runs` are still written under
the old convention and are not rewritten by this change: the forecast and `conductor report` now
read a mix of two conventions until they are re-priced, which is the next wave's work.

## The paid answer Claude lanes were throwing away

`claude_said` de-duplicated assistant events by `message.id`, first occurrence winning, because
"Claude may repeat one assistant message in the stream". That is true of usage, which
`claude_stream_usage` handles correctly (last wins, since usage is a message-level meter restated
per event). It is not true of content: **Claude Code emits one assistant event per content block,
all sharing one message id.** Dumped from a real transcript, in stream order: a `thinking` block,
then the `text` block, then two `tool_use` blocks. The first event claimed the id, contributed no
text, and the event carrying the actual answer was skipped.

**122 of 176 claude runs lose text a block-join would keep. The worst returns 69 characters of a
34,102 character answer.** `claude_said` is what `_claude_cut_short` returns for every Claude
stream that ended without a result event -- a cap kill, a timeout, a breaker trip, a stop. AGENTS.md
says the salvage path is normal, not exceptional; this was the text the lead read off a killed lane.

## Two smaller ones

An empty or unusable `usage` object arrived as a zeroed `Usage` rather than as no usage, and
`prices.estimate` prices all-zero tokens at exactly `0.0` -- so a run with no usable counter was
recorded as costing $0.00 with `cost_basis: "estimated"` and `unpriced: False`, which is precisely
what `prices.estimate` returning `None` for an unknown model exists to prevent. Now a usage object
with no usable counter and no usable cost is no usage. The `script` fleet's deliberate free $0.00
path is untouched: it builds its `Usage` directly and never calls `usage_from_raw`.

And `_last_stream_id` could not read the JSON-array transcript shape that `claude_init_event` and
`_last_json_object` both handle; 7 such transcripts exist in the corpus. The success path never
noticed because `_parse_envelope` reads the session id off the envelope, but `_claude_cut_short`
uses `_last_stream_id`, so a cut-short array transcript lost its session id and a resume against it
would have failed.

## Verification

2100 tests green and ruff clean on the merged tree and on the main checkout after merge. 8 of this
wave's tests are red against 0.99.0's source, covering all four items. Three edits to existing
tests, all read: the two cache tests rewritten to the new convention (their old names claimed cache
was "split out of input", which is the assumption this wave disproves) with the corpus counts in
their docstrings, and one `output_tokens == 0` assertion strengthened to `usage is None`.

The lead re-recorded twelve golden run receipts' token counters from the corrected parser and
rewrote six fixtures' projections, then refreshed the digest manifests. **No verdict, cost, `ok`
flag, or commit state moved in any fixture** -- only token counts, the derived cache hit rate, and
a prompt_sha256 backfill. Each receipt's historical `cost_usd` was deliberately left alone: it is
what conductor actually reported at the time, and no consumer re-derives cost from tokens.

<a id="r-0.99.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 15: the prompts conductor authors, and the dispatch and watcher paths, shipped as v0.99.0 ($26.93, four cold reads then four Grok 4.6 write lanes)

Wave 14 found `_rank_contract` breaking five of AGENTS.md's own eight no-quota rules -- a prompt
inside conductor that had never been audited against the section of AGENTS.md that governs every
prompt conductor sends. This wave asked what else was never audited, and paired it with the last
large unread surface: `runner.py`'s dispatch and watcher paths.

**Shape.** Four cold reads, Grok 4.6 (cursor) and Gemini 3.8 Flash (antigravity) at `hard` on a
$3.00 cap, two on each scope: (1) every prompt conductor authors, judged as the text it actually
sends -- interpolations, wrapper functions, and JSON schema `description` fields included, not the
templates alone; (2) `dispatch`, `_wait`, the stop and kill-group helpers, `_priced_usage` and
`_parse_failure_result`. Reads: $7.39 for four, every lane inside its cap. Then four Grok 4.6
write lanes split by module (shape.py, verdicts.py, errors.py, runner.py) with disjoint test
files, caps above rule 2's estimate: $19.54 of $40.00, every lane inside. Four merges in series,
gated one at a time, no conflicts.

**28 claims, 18 shipped, 10 refuted or dropped.** Two of the refutations were filed at confidence
10, the same rate wave 14 saw. Gemini's `KeyboardInterrupt` story (zero receipts on SIGINT during
`_wait`) is not reachable through the CLI, which installs a handler that calls `request_stop()` so
`_wait` returns cooperatively -- but the structural half of it was real and was fixed: any
exception between the spawn and `post_wait = True` left the fleet's process group alive and wrote
no receipt at all. Its antigravity grace-band claim quoted `Spec._validate_cap_grace`'s refusal of
`cap_grace_usd` on every watcher-capped fleet and filed the finding anyway. Grok's reading of
`DEFAULT_COLLATE_INSTRUCTIONS` asked a comparison prompt to satisfy rules written for review
prompts, and its adversarial fix-prompt contradiction read "no further test change required" as a
prohibition. Two `outputs.py` stream-parsing claims (a cursor error event before a clean result
envelope; `_parse_antigravity` reading the last JSON object rather than scanning for the result
event) were left unshipped: both need a live probe of what those vendors actually emit, and a
guess about a stream shape is not evidence.

**The two worst prompt defects were prompts lying about conductor's own enforcement.**
`DELIVERABLE_REVIEW_NOTE` tells a reviewer "What a machine can check is checked" -- true when a
`--deliverable-validator` ran, and sent unchanged when none did, narrowing the reviewer away from
mechanical defects nobody had checked. `DELIVERABLE_VALIDATOR_SENTENCE` tells the deliverable fix
lane that "Conductor runs `<validator>` on the file before and after your change and refuses a file
that fails it" -- true on the build lane, which declares the validator, and false on the fix lane,
whose deliverable is unconditionally `dispositions.json` with no validator key at all. The lane
agreed the prompt was the wrong half: attaching the document's validator to that deliverable would
point conductor at `dispositions.json`, the wrong file.

**The mission prefix is written for a write lane and goes to every lane.** `shape_a` puts
`_prefix` on the mission's `prefix` field and `mission._with_prefix` prepends it to every lane
prompt; the recorded prompts under `tests/golden/f10-shape-a-*/runs/` are cursor and antigravity
*review* lanes opening with "Keep changes and tests to what the task asks for" and "if it is a plan
or a promise, do that work now", immediately above "CHANGE NOTHING".

**Three receipts that disagreed with the branch beside them.** A clean gate that could not run
(`infra_error`) never took its commit back: the code's own comment says "the commit is taken back
below (policy `clean` requires this gate; we do not land unverified work)", and `_gate_passed`
falls back to the lane's own passing gate when the clean gate's `ran` is False, so the block was
skipped and its `why = "clean gate could not run"` string was unreachable in the case it was
written for. A crash anywhere in the post-wait path after `commit_work` left the commit on the
branch and built a `_parse_failure_result` receipt carrying no commit at all. And an attestation
that could not be signed wrote its reason to `Result.error` -- the first thing `failure()` reads --
so a spawned, built, gated, committed run came back `ok: false` with `kind: unknown` because a
signing key could not be read.

**`kind` named the cap for four failures that had nothing to do with money.** `_own_check_kind`
was split out of `error_kind` on 2026-09-08 precisely so an unpriced run's own verdict is not
renamed `cap`; that fix was correct and incomplete. `verdict invalid:`, `resume failed:`,
`clean gate could not run:` and `test surface changed under policy forbid:` were all missing from
it, so on any cursor lane -- unpriced by construction -- they read `cap`, and on a priced lane they
fell through to `unknown`. Now `parse`, a new `resume` kind, `gate` and `gate_test_surface`.
Separately: a dispatch that never spawned reported `over_cap: true` beside its own `kind: refused`,
and a priced timeout whose final figure sat over its ceiling was classified `cap` -- against the
explicit claim in `Budget.settle`'s docstring that "a timeout is not a cap (`errors.capped`
excludes `timed_out`)", which only the unpriced branch honoured.

**Verification.** 2092 tests green and ruff clean on the merged tree, gated after each of the four
merges in series. 32 of this wave's tests are red against 0.98.0's source; every shipped item maps
to at least one. Every edit to an existing test was read: two in `test_errors.py` (a "cap kill that
also timed out" fixture that now includes `fleet_status="error_max_budget_usd"`, so it describes an
actual cap kill rather than the defect this wave fixed, and an `unknown` case whose error string was
swapped because the old one is now classified) and one in `test_shape_prompts.py` (an assertion
replaced by a strictly longer one containing it). No golden fixture moved: unlike `_rank_contract`,
which is rendered live at collate time, the `shape.py` constants are written into `mission.json`
when the mission is shaped, so a replayed fixture keeps the text it recorded.

**Left open, on purpose.** `DEFAULT_RESOLVE_INSTRUCTIONS` still carries no rule-6 sentence: the
lane was told not to change it (its mission-order fallback is a settled design call) and correctly
reported the gap instead. It is now pinned by tests, which it was not before -- it was the one
prompt in `prompt_versions()` with no assertion anywhere in the suite.

<a id="r-0.98.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 14: the report and the judgment surface shipped as v0.98.0 ($23.07, four cold reads then four Grok 4.6 write lanes)

`report.py` (1635 lines) with `spend.py` (470), and `verdicts.py` (512) with `mission.py`'s
collate/resolve/rank paths, were the last large surfaces never cold-read as a scope of their own.
Four read lanes -- Grok 4.6 and Gemini 3.8 Flash at $3.00 each on both scopes, $5.56 for the set
-- filed 28 findings; 19 survived verification on bytes and shipped, split across four Grok 4.6
write lanes by function region for $17.51 of a $35.00 ceiling. Gemini's read cap was raised to
$3.00 after w13, where a complete 16-finding review overran $2.00 by three cents and failed its
lane; both vendors finished inside it here. Five findings were refuted on bytes and are recorded
below, because two of them were filed at confidence 10.

**The report (`report.py` and `spend.py`, two lanes).** `spend._read_run` threw away an entire
priced run when a receipt carried `"cache_read_tokens": null`, `"cache_write_tokens": null`, or
`"breaker": {"tool_calls": null}`: `dict.get(key, 0)` returns the *stored* value when the key is
present, so the null defeated the default, failed `isinstance(..., int)`, and returned `None` for
the whole receipt -- while `total_tokens` twenty lines up handled an explicit null correctly. A
receipt *missing* the key was kept and counted 0; the same receipt with the key present and null
vanished from every aggregate in the project, cost included. One `_optional_count` now reads all
four counters; a wrong *type* is still a refusal, and the identity and `cost_basis` refusals are
untouched. `walk_lane` still walked `previous_attempts` before `attempts`, the exact
first-occurrence-wins shape `walk_collate` was corrected for on 2026-09-08, and `effects`'s
docstring still named the pre-fix order for both.

`--since` blanked the one figure AGENTS.md rule 2 exists to measure. `windowed` was
`since is not None or until is not None` -- one flag stamped on every mission row -- and
`MissionRow.usd_per_item` refuses to divide a windowed cost, so `conductor report --since
2020-01-01` over 2026 receipts cut nothing and still emitted no per-item figure on any row or on
the landed line. It is now per mission: the snapshot's own run count against the rows the window
kept, with an undeterminable count counting as windowed rather than as whole. The precision table
and the finding-rate table also scored different sittings under a window -- finding rate walked
in-window review runs, precision walked the whole snapshot's review lanes with no filter, and
`stopped_review_ids` was built only from in-window rows, so an interrupted review before the
boundary had its partial `answer.txt` scored as a completed sitting. `_review_sitting_stopped`'s
docstring says the two tables cannot disagree about whether a review happened; under a window
they could. They now join on `review_lanes[name]["run_id"]`, and a lane that cannot be joined is
counted as `review_sittings_unjoined` rather than scored.

`_matches_a_finding` returned True on an empty `items` list, for a documented reason -- a receipt
predating the field cannot answer the question. But `_scan_missions` writes `items: []` for a
modern lane that answered `NO_FINDINGS` too, so the two were byte-identical: a fixer's
disposition naming `finding 1` against a reviewer that reported nothing landed on that vendor's
row, incrementing `fixed` while `findings` stayed 0 and driving `corrected_rate` above 1.0 --
which the D20 comment three lines away states is the thing the check prevents. `findings` now
tells the two apart. `if not review_lanes: continue` short-circuited before the disposition loop,
so on a fix-only mission -- a salvage follow-on, a build-and-fix -- every disposition was the
unknown-lane case the counter exists for, and every one of them vanished silently. The rule 10
grace block sat outside the `else` that sets its neighbouring cohort to `n/a`, so a stage with no
Claude run at all printed `used_usd 0.00, runs 0` beside a cohort correctly saying the stage did
not run, against the README's own sentence. And `_wall_figures` accepted `NaN` and `Infinity`
where `_read_run` guards `math.isfinite`, so a poisoned wall block made `busy()` and `stretch()`
NaN and `json.dumps` wrote a bare `NaN`, which is not valid JSON.

**The judgment surface (`verdicts.py` and `mission.py`, two lanes).** `_rank_contract` broke five
of AGENTS.md's own eight prompt rules -- the file that governs every prompt conductor sends. It
asked "Which lane's result is strongest?" with a schema whose `strongest` enum held only the lane
names, so a judge that found no candidate usable had to name one anyway (rule 1, and rule 2's
presumption); "strongest" is an adjective with no consequence threshold (rule 3); `reason` asked
for no citation (rule 5); and the whole-answer-in-this-reply sentence was absent (rule 6). The
prose collate two hundred lines away got rule 1 right. `"none"` is now a representable and
invited answer that parses as valid rather than as a broken judge, a unanimous `none` sitting is
`agreement: "none"` and `ok: True` rather than invalid, and the contract carries the bar, the
citation, and the closing sentence.

`_rank_schema_for` dropped the schema flag for `antigravity` alone, so a `fleet: cursor` rank
judge got a schema path, hit `fleets.Spec._validate_schema`'s refusal, and failed the mission at
validation -- Grok 4.6, one of the two lanes this project actually reads with, could not be a
ranking judge at all, for a reason the function's own docstring already answers (the contract
embeds the schema as text and the parser extracts embedded JSON). A new
`fleets.supports_schema_flag` drives it, so `script` is covered too and the refusal itself is
unchanged. A ranking sitting with fewer than two candidates dispatched anyway: one candidate paid
2M judge calls to compare a lane against itself, zero wrote `"enum": []` and paid for answers that
could only fail as unknown lane names. Mean judge scores were `sum(values) / len(values)` over
whichever orders happened to score each candidate, so a candidate scored by one judge in one
order was printed beside one scored by two judges in two, as though comparable; each mean now
carries its `n`, the idiom `report.VendorStageRow` already uses. `_rendered_verdict` crashed with
an unhandled `TypeError` on a verdict dict it did not write, from inside collate prompt
generation, and `_collate_is_trusted` did the same on a resume when `judges` was a truthy
non-list.

In `verdicts.py`, `parse_verdict` accepted any non-empty string as evidence for an `ok: true`
criterion -- including `"no evidence"`, the exact literal the contract it had just sent reserves
for a *failing* criterion. It also computed `passed` from the criteria in both directions of
disagreement: a model reporting `"verdict": "fail"` while marking every criterion ok was overruled
into a pass, with a note in a summary no exit code reads, and the lane then ranked first on
`verdict_rank = 0`. The other direction was already right and is unchanged. A citation *shape*
rule was considered and rejected: a real citation is often prose, and a regex that rejects a
correct answer is worse than the gap it closes.

**The prose collate.** `DEFAULT_COLLATE_INSTRUCTIONS` told the model "the order the lanes are
listed in carries no meaning" -- which is rule 7's own counter-example, not a mitigation, and the
two-order rank sitting exists because that mitigation does not work. Dispatching the prose collate
twice would double its cost on every mission that uses one, which is a decision the operator has
not made; instead the single dispatch now asks for the judgment as listed and again in reverse and
calls a disagreement inconclusive. A test asserting the old sentence was inverted at merge.

**Refuted on bytes, not shipped.** A reviewer filed at confidence 10 that a paused mission with
`"answer": null` reads as finished: that is `pause.json`, and `result.json`'s `paused` block uses
key-absent as the parked signal, which is what `approvals.py` and `mission.py`'s own report
writer both read. Also at 10: that a mission with no `result.json` leaks runs into the finished
aggregates via `mission_row.unfinished = False` -- there is no mission row at all, because the
run/mission join is built from `result.json`. `rank_lanes` giving an absent verdict and a failed
verdict the same rank is what its docstring says it does; separating them is a design decision,
not a defect, and the lane was told to leave it. Forward/reverse leaving a middle candidate of
three never first or last is real and is the documented shape; full permutation is a cost
decision. And the landed line's `cost_usd` summing lower bounds is already qualified in the
printed sentence beside it.

**Method.** Every claim was read against the source before it entered a spec; nothing shipped on a
reviewer's word. Golden fixtures and `tests/test_prompt_versions.py` pin the `rank_contract` and
`collate_default` fingerprints, so all four lanes were fenced off from them and the lead resolved
them at merge -- the shared-file race that cost wave 12. All four lanes stayed in region and
finished inside cap. 2061 tests green, ruff clean, on the merged tree and on the main checkout
after merge. 44 of the wave's tests are red against 0.97.0's source and every shipped item maps to
at least one. The `e4-judge-sitting` golden fixture's four recorded rank prompts were re-rendered
and its file digests updated; its `prompt_versions` block was left alone, being a recording of
what that mission ran with rather than a live expectation.

<a id="r-0.97.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 13: the launcher and the operator surface shipped as v0.97.0 ($26.87, four cold reads then four Grok 4.6 write lanes)

`cli.py` (1526 lines) and `shape.py` (1289) were the last large surfaces never cold-read as a
scope of their own. Four read lanes -- Grok 4.6 at $3.00 and Gemini 3.8 Flash at $2.00 on each of
two scopes -- filed 28 findings; 25 survived verification on bytes and shipped, split across four
Grok 4.6 write lanes by function region. Opus was not used this wave (operator, 2026-09-08); the
two Opus lanes launched first were killed mid-spawn, one of them $1.70 in with a transport error
and nothing usable. Gemini earned the swap: it filed 12 on `shape.py` and 16 on `cli.py`, mostly
disjoint from Grok's, and found the wave's best defect.

**The launcher (`shape.py`, two lanes).** `--adversarial` never taught the fix lane about the
adversarial lane: on exactly the run that flag exists for -- both cold reviews clean, the
adversarial lane reproducing a real defect with a failing test -- `FIX_PROMPT` still told the
fixer to change nothing and reply `NO_CHANGES`, and `DISPOSITIONS_SCHEMA` gave it no legal `lane`
value for the disposition it did record. `_three_reviewer_wording` had solved the identical
problem one flag over for `--opus-review` and nobody did the same here; `_adversarial_wording`
now does, after the Opus rewrite so both flags compose. `--ports` reached only the build lane, so
on any project whose suite needs a TCP port the build passed and every other gate-running lane
failed with none claimed. `parse_ceiling` accepted `inf` and `nan` (`nan <= 0` is False), and so
did `cap_arithmetic`'s grace band. `followon_budget` was `mission_budget - build_cap`, which is
not the same as "without the build lane": it left the build lane's grace in the ceiling and, with
`adversarial` set, that whole lane's cap for a lane a follow-on never emits. A follow-on capped
its read-only Grok lane at the suite-running price. `_refuse_unsized_lanes` checked only the flags
a caller remembered to pass -- so the follow-on, which passes only `opus_review`, shipped the
over-budget it exists to prevent -- and raised `AttributeError` on a flag it did not model.
`ADVERSARIAL_PROMPT` was the one conductor-authored prompt with no version id, and stated no
consequence threshold and asked for no citation, against AGENTS.md rules 3 and 5. `build_prompt`
was the one builder that did not go through `with_gate`. And the arithmetic block printed the
rule-2 budget while the file carried the F17-raised one, leaving the operator to re-add the
raises by hand.

**The operator surface (`cli.py`, two lanes).** `conductor runs` indexed `data["exit_code"]`
behind a guard that never required it, so one older receipt took down the listing of every good
one; `round(data.get("cost_usd", 0), 4)` crashed on a stored null, which is exactly what an
unpriced run writes (`.get(key, default)` returns the stored `None`, not the default); and
`collisions["hotspots"]` did the same for a partial collision block. `_kind_from_legacy_receipt`
failed hardest on its own target case: `Result` has fourteen fields with no default, so a receipt
missing any one of them returned the `null` the helper was written to prevent. `status` came from
the heartbeat age alone, so a run whose process was gone read as `running` for `LIVENESS_STALE_S`
-- and forever if `at` was missing, since the `except ValueError: pass` left the age at `0.0`
-- while the command computed `pid_alive` two lines below and did not use it. `conductor missions`
answered "is this paused" off `pause.json` while `conductor report` answered it off `result.json`,
with two different predicates, and reported an unreadable pause file as not paused; it also
hardcoded `resumes` and `children` to zero for any mission without a receipt. `salvage --emit` ran
the full suite before validating the flags `--emit` needs. `cmd_land` interpolated an unvalidated
`--lane` into a path and read whatever JSON it found there, before `land()`'s own
`is_lane_name` check. `golden check` from a directory with no `tests/golden` compared nothing and
exited 0. `salvage` and `land` installed no stop handler, so Ctrl-C during either left no receipt
and an orphaned test process group -- `main` now keys off a `runs_in_band` marker rather than a
hardcoded tuple. A mistyped `--cwd` was a `FileNotFoundError` traceback, and a non-UTF-8
`--verdict-file` or `--agent-file` was one too, while the identical mistake on `--prompt-file`
had been a clean exit 2 since the day before.

**Three findings did not ship.** The `FINDING:` / `FINDINGS: N` markers in `REVIEW_TAIL` were
filed again as a quota; they are consumed by `verdicts.review_verdict` and were kept deliberately
in an earlier wave. `golden record`'s exit 1 on a missing mission is pinned by a test on purpose.
And both vendors filed `forecast.apply_caps` raising review-lane caps as violating F17 -- but
`apply_caps` has always said "every warned lane" in its own docstring, and a review cap warned by
history is a cap that will fail its lane. The code is right; AGENTS.md rule 2's prose was stale
and is corrected in this release.

**What the shape cost.** Reads $6.03 (including $1.70 on a killed Opus lane and a Gemini lane
that overran $2.00 by three cents, whose complete review was salvaged from the stream per rule 6);
builds $19.13 against $45.00 of caps, every lane inside. 2015 tests green, 64 of them red on
0.96.0's source. Two existing tests were rewritten by the lead rather than the lanes -- both
pinned behaviour a fix correctly replaced, and both were strengthened: `followon_budget`'s
asserted a subtraction, and the liveness fixture built its "silent" case from a dead pid, which
only exercised the stale-heartbeat path because `status` ignored the pid. One cross-lane seam the
lead closed by hand: lane C rewrote the fix prompt for `--adversarial` but `cmd_shape_a`, in
another lane's region, still wrote the schema without the flag.

<a id="r-0.96.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 12: the dispatch surface shipped as v0.96.0 ($32.90, four cold reads then three Grok 4.6 write lanes)

`runner.py` (3913 lines) and `fleets.py` (1392) are where one run actually happens, and where the
project's founding rule -- *a fleet's word is never evidence; verify on bytes* -- is either true or
not. Neither had been cold-read as a scope of its own. Split in two: how a run is launched, watched,
priced and ended; and what conductor judges on bytes. Grok 4.6 ($2.50) and Opus 5 ($4.00) read each
half, $11.12 for the four. 24 of the claims verified on bytes; three Grok 4.6 write lanes built the
fixes in parallel for $21.79 against $34.00 of cap. 1938 tests green (36 new), 49 of them red
against 0.95.0's source.

**The verification surface had real holes.** Three of them:

- **A hard link defeated the deliverable guard that symlinks no longer defeat.** W5 closed
  `plan.json -> ../secret`; `ln <outside> plan.json` reproduced the outcome exactly. `is_symlink()`
  is false, `resolve()` stays inside the worktree, and the `O_NOFOLLOW` in `copy_no_follow` and
  `_deliverable_sha256` opens the inode either way -- so another file's bytes were hashed and stored
  as the lane's product, and a read lane's `_read_deliverable_only` granted them its exemption. Now
  refused on `st_nlink > 1`, from the `fstat` the digest already needed, with the three cases it
  still does not cover named in the docstring.
- **The deliverable schema was re-read after the run.** It commonly resolves to an absolute path
  outside the worktree, next to the mission YAML, and a write lane runs with
  `--permission-mode bypassPermissions`. Rewriting it to `{"type": "object"}` produced
  `deliverable.ok: true` against a contract the spec never declared. The schema is now snapshotted
  before the spawn; deleting it mid-run can no longer drop the check or downgrade it to "unreadable".
- **A `stage: fix` lane could skip the reproduce gate by declaring a source file as its deliverable.**
  F19's exemption keyed only on "changed paths equal the deliverable path", and nothing constrained
  that path to be a document, so the rule a fix lane exists under -- show your check failing on the
  base before you may land -- was optional.

**Taint enforcement was thinner than `AGENTS.md` claims.** Preflight matched conductor's hook file
by filename suffix and took the first hit, so it could approve a lookalike while the paid process
loaded another file; it now compares absolute paths and fails closed on a source it cannot compare.
`_uncovered_agy_tools` was a substring net that failed open on any name it did not recognise
(`http_request`, `download`, `computer_use`) -- it is now an allowlist, so an unrecognised tool is
uncovered by construction. And `hooks_modified: []` read identically whether the digest check ran or
never ran at all; the receipt now says which, and a hook file conductor cannot hash after writing it
is a failed write rather than a file quietly dropped from the map.

**One run, two stories,** the class this project keeps finding:

- `Result.failure()` ranked a non-zero exit above a fleet error while the comment on the next line
  said the opposite, and `errors.error_kind` ranked them the other way. A live failure is usually
  both, so one receipt carried `kind: rate_limit` with `failure(): "exit code 1"`, and a fallback
  declared `on: ["exit"]` missed what `on: ["rate_limit"]` caught.
- A timed-out run was settled as a timeout *and* as an unenforced cap: `Budget.settle` carved out
  killed and interrupted but not timed out, and one unpriced dispatch makes a whole mission's budget
  unverifiable, so a Grok read lane hitting its wall clock could halt a mission.
- The over-budget undo took the commit back without restating the git verdict -- five lines already
  written twice in the same function -- so the receipt claimed a commit the branch no longer had,
  while the signed attestation carried the correct tip.
- A refused run receipted `permission_mode: null` beside an `argv.json` carrying `--restricted`.
- A clean gate that never ran because the transplant machinery broke was reported as "clean gate
  exited 1" and its commit taken back with reason "gate failed" -- the exact lie `verify.uncommit`'s
  `why` parameter was added to prevent.

**The quiet structural one.** The include/taint exclude file was unlinked *before*
`worktrees.release` judged the tree, and git silently ignores a `core.excludesFile` that does not
exist (a reviewer probed it: present, `git status --porcelain` is empty; removed, the same tree
prints `?? secret.txt`). So every `include` lane and every tainted Antigravity lane came back
`clean: false, kept: true`, which blocks any downstream lane naming it as a base, and in a
legitimately-kept worktree the operator's global excludes stopped applying too, so a salvage could
commit conductor's own deny hooks onto the lane branch.

Also landed: `Spec.timeout` refused at load like every neighbouring integer (`timeout: 0` spawned,
paid, killed instantly and receipted "timed out after 0s"); `cap_usd: true` refused instead of
silently becoming a $1 cap; `_parse_failure_result` carrying through a kill that already happened
rather than re-deriving the budget from nothing; the tainted-agy refusal keeping `prompt_versions`
its docstring promises "unconditionally"; `verify.run_tests` bounding the `proc.wait()` after a kill
the way the fleet path already does; a validator with no base blob answering `no-base` instead of
`accepted` and then refusing the lane for a base run that never happened; `deliverable.ok` no longer
staying true beside an error about the same file; a failed deliverable copy failing closed instead
of reading as "nothing to capture"; and the signed statement binding `taint_enforcement` and
`settings` -- conductor's two strongest own-eyes checks, previously unsigned and unverified, so a
`result.json` edited to flip either still verified.

**The merge taught the wave's own lesson.** All three lanes were green alone and 13 attest and
export tests failed on the merged tree: lane C had duplicated the gate-summary shape into
`attest.py` (it cannot import `runner`, which imports it) while lane B changed the rule in
`runner.py`, so every signed statement disagreed with its own `result.json`. One line to fix, and a
docstring on both halves saying they must keep mirroring each other. Two lanes changing one side
each of a deliberately duplicated rule is a merge hazard worth naming: the same failure class the
wave was hunting, produced by the wave itself.

Operational notes. Grok's read cap overran again -- $2.747 against $2.50 on the larger scope -- and
again wrote a complete review, so the answer was salvaged from the stream. Caps sized *above*
rule 2's estimate rather than at it: all three write lanes finished inside them ($21.79 of $34.00)
after wave 11 lost a commit to a $0.24 overrun. All three lanes again committed a stray
`evidence.json` at the repository root, dropped before the release for the third wave running.

<a id="r-0.95.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 11: the scheduler and the resume path shipped as v0.95.0 ($38.32, four cold reads then three Grok 4.6 write lanes)

`mission.py` is the largest module in the repository and had never been cold-read as a scope of
its own. Split in two -- the scheduler and the lane graph; resume, pause and the locks -- and read
cold by Grok 4.6 ($2.50 cap) and Opus 5 ($4.00 cap) on each half, $11.18 for the four. Every one of
the 16 claims that survived verification was checked on bytes before a build lane saw it. Three
Grok 4.6 write lanes then built the fixes in parallel, split by *function region* rather than by
module, since every defect lived in the same two files; each lane owned a disjoint set of test
files so the merges would not collide. `mission.py` auto-merged across all three tips; the one
conflict was an add/add in `attempts.py` where two lanes appended different helpers at the same
line, resolved by keeping both.

Cost: $11.18 reads, $20.73 builds ($27.00 of cap), $38.32 with the failed lane's own spend counted.
1902 tests green (24 new), 26 of them red against 0.94.0's source.

Two findings landed independently in all four reviews or in both reviewers of a scope:

- **`Ledger.add` never published, and `finish` ran before it.** `start` and `finish` were the only
  callers of `_publish()`, and `dispatch_one` cleared the outstanding cap six lines before it
  counted the spend. So after every completed dispatch the last thing written to `running.json`
  was: this cap no longer outstanding, and the spend not yet counted -- and for a mission's final
  dispatch there was no later publish at all, so the live lock's last word understated by that
  lane's whole cost. Worse than the stale document: `ledger.remaining()` is read on worker threads,
  so another lane's retry could fit its cap to dollars a dispatch that had *already ended* spent,
  outside the bound the `Ledger` docstring states. `add`, `seed` and `add_child` now publish, and
  the spend is counted before the cap clears.
- **Early cancel named a lane that had already finished `ok`.** `wait(FIRST_COMPLETED)` returns
  every completed future, and the drain popped them one at a time, so firing the cancel mid-batch
  listed a still-in-`running` sibling that had already succeeded. `early_cancel.cancelled` and
  report.md named it; `lanes[name]` in the same document said it passed its gate and was paid. The
  scheduler now drains every done future and fires the cancel once, after.

The most dangerous single item was quieter: a **`--dry-run --resume` rehearsal overwrote the real
parked mission's `result.json`**, erasing `paused`. Every producer of `pause_park` is disabled on
the dry-run path, and the write at the end of `_execute_mission` had no `dry_run` guard, so
wave 10's own `gc._mission_awaiting_resume` protection switched off and the next `gc --apply` would
delete the parked lanes' worktrees and branches -- the salvage source AGENTS.md rule 6 depends on.

The rest, by lane:

- **Scheduler and ledger.** One run now carries one cancel reason across the run receipt and the
  lane attempt (`dispatch()` is told the winner rather than corrected after the fact); an
  interrupted retry backoff rewrites the run receipt to the ending the error histogram already
  counted; a cancelled run on a post-hoc-priced fleet is `unknown_cost` -- neither `$0` nor
  `unpriced`, which would trip `blocker()` and make every later lane refuse.
- **Resume, pause and locks.** The pause answer is no longer consumed before the running lock is
  held, so an answer cannot be spent on a resume that never runs. A parked run keeps the prior
  collate and resolve records, whose run ids were previously dropped from the snapshot forever
  along with their dollars. Run-receipt recovery fires when a lane receipt is *stale*, not only
  when it is absent -- wave 9 closed "no receipt, so pay twice"; this is its dual, "a stale receipt
  drops a run that is on disk". A recovered lane's synthesized attempt now carries the cost and
  duration the run receipt already held, so the ledger, the lane row and `occupied_s` stop giving
  three answers about one run. `_release_lock` renames to a tombstone and re-reads before removing,
  and says in its own docstring what that still does not cover.
- **Invariants, wall clock and report.** The two E26 refusals that keyed on `attempts[0]` now
  consider every attempt's `cwd`, so a fallback into another repository is refused at load rather
  than failing after its dispatch is paid for. `critical_path_s` counts `previous_attempts`, which
  the wall block always claimed it did. The collate and resolve keep-rules authenticate against run
  receipts rather than path identity alone. And `conductor report` stops printing a parked or
  interrupted mission as a finished failed one, following `windowed`'s shape rather than inventing
  a second.

Six claims were refused on bytes or left unshipped; they are recorded in the session's resume file
so a later wave does not re-raise them.

Operational notes. Grok's read cap must stay at $2.50: scope 1 finished at $2.471, and wave 10's
$2.00 failed a complete review by nine tenths of a cent. The build lane sized at $8.00 finished its
work, gated itself green, and then failed at $8.2449 -- AGENTS.md rule 7's trap on a *write* lane
this time, where a cursor cap is a post-hoc verdict and a cursor write lane refuses `cap_grace_usd`.
Its worktree was kept, its own gate re-run by the lead, its diff read, and it was committed by hand:
rule 6's salvage path, and the cheapest part of the wave. Three parallel write lanes with no
`--test` on the dispatch again cost nothing (rule 12).

<a id="r-0.94.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 10: the operator-facing surface shipped as v0.94.0 ($19.57, four cold reads then three Grok 4.6 write lanes)

Four cold reads over two scopes -- Grok 4.6 and Opus 5 on each, $9.43 -- produced 24 claims.
Antigravity was skipped: wave 9's "Individual quota reached" was inside the reset window.
Grok's scope-2 lane finished a complete review at $2.009 against a $2.00 cap, so conductor
failed the lane on its post-hoc verdict (AGENTS.md rule 7's trap, nine tenths of a cent);
the review was intact in the stream and was used.

22 claims confirmed on bytes, 6 refused. Three Grok 4.6 write lanes built the fixes in
parallel ($10.14 against $30.00 of cap), each on its own module set, with no `--test` on the
dispatch (AGENTS.md rule 12: lane gates run one at a time) and the tips gated in series on the
merged tree. 1878 tests green, 29 new, 34 of them red on 0.93.0's source.

What landed:

- The export scrubber redacted any JSON field whose *name contains* "key", so `usage.price.key`
  and every verdict-checklist criterion id read `<redacted>` in every bundle ever exported --
  against the README's own promise that a bundle shows which price entry produced a cost.
  The rule now judges a field name as components (`api_key` yes, `key` and `keyboard` no).
- `conductor gc` raised an uncaught ValueError and printed no plan at all when any worktree or
  `conductor/` branch carried a regex-valid but impossible stamp (`20260230T000000Z-x`).
- A paused or interrupted mission writes `result.json`, so gc read it as finished and stopped
  protecting its runs' worktrees and branches.
- An empty or unreadable port claim was deleted without the liveness recheck, racing
  `ports.claim`'s create-then-write window. It is now kept unless `--older-than` proves it dead
  by mtime. A non-UTF-8 claim aborted the whole plan; it no longer does.
- `_vendor`'s no-match fallback returned the fleet name, which for `cursor` and `script` is a
  registered vendor: an unmatched Grok id shared Composer's row, cost and cap misses included.
  It now returns `unmatched:<fleet>`. Rule 10 selects the Claude *fleet*, not the anthropic
  vendor, so one receipt can no longer be a cap miss in one table and "no Claude run at all" in
  the next; a fleet-Claude run whose model id did not resolve is surfaced, not claimed.
- Mission top-level files were copied without the symlink guard the subdirectories and run files
  already had. `check` now cross-checks `attestations`, `scope` and each run's diff digest
  against the attestation payload it already carries. The manifest's own free-form text is
  scrubbed, so an unreadable receipt no longer reads as a leak and refuses the export. An
  unreadable file is an ExportError naming it, not a traceback. A truncated `mission.json`
  recovers its scrub paths from `result.json` and records the degradation in `scope`.
  `_OMITTED` now names the run-level sidecars, the mission-root prompt files, and the fact that
  `result.json`'s `tail` carries 20 lines of the log without `--logs`.
- Report: unknown tool-call counts are no longer averaged as zero, `skipped` is windowed like
  every other figure, an interrupted review is out of precision as well as the finding rate, an
  unreadable answer counts as unparsed rather than vanishing, the Landed aggregate emits the
  cost its own division rests on, `windowed` is emitted so a blank per-item figure is
  explicable, and `_land_merged` agrees with `land.py` about a receipt missing `dry_run`.

Refused, on the record: the retry-vs-lane denominator split (two documented figures, not a
disagreement); plaintext key-name redaction (golden.py records it as deliberately withdrawn);
email and hostname scrubbing (a scope expansion of what the scrubber promises, operator's call);
display rounding of a Decimal total at the print boundary; replaying the stored `kind` across
the 0.93.0 boundary (that is the design); the multi-build-lane evidence map (needs a decision
about what `usd_per_item` means across two build lanes).

Fifth lead correction in as many waves, and the first that was not a defect: lane B committed a
stray `evidence.json` at the repository root, dropped before the release commit.

<a id="r-0.93.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 9: the money surface shipped as v0.93.0 ($18.85, four cold reads plus two Opus replacements, then two Grok 4.6 write lanes)

The surface that decides what a lane may spend and what it is charged: `prices.py`, `ceiling.py`,
`forecast.py` in one scope; `spend.py` and the cap-enforcement path through `budget.py`,
`errors.py`, `runner.py` and `mission.py` in the other. Nothing had read it end to end.

**The wave cost more than it should have, for two reasons worth recording.** Antigravity returned
"Individual quota reached" on both Gemini lanes after they had already spent $1.26, so half the
cross-vendor pair died with no answer. Rather than wait out a 1h47m reset, the lead substituted two
Opus 5 cold reads at $6.00 -- the F9 Shape C reviewer, used here as a stand-in rather than a third
seat. It paid for itself: Opus and Grok found the same grace/cap defect independently from opposite
directions, which is the cross-vendor signal rule 7 exists for. Then the scope-2 *write* lane
finished its work and came in at $8.48 against a $7.00 cap, so conductor undid its commit. Cursor
prices post-hoc, so the cap is a verdict after the fact; the worktree was kept, the lead gated it
clean, read it, and committed it (AGENTS.md rule 6). **Both write lanes reported `tests: 0` -- neither
ran its own gate -- so the merged-tree gate was the only verification either one got.**

Twenty-five claims, nine confirmed and shipped, four refused. The two headline defects:

- **A price override silently drops the long-context tier.** `load_prices` rebuilt a well-formed
  override entry through `_std`, which has no way to carry `long_context_tokens` / `long_context`,
  and the override JSON had no key for them. The module docstring advertises that file as *the* way
  to track rate drift without a code change, so the expected operator action -- writing a new rate
  for `cursor-grok-4.6` when xAI moves it -- deleted the 200K doubling wave 6 had just added. A
  300K-prompt review is then estimated at half what it cost and judged against a cap it has already
  passed. The same rebuild also re-derived `cache_read` at 10% of input where the table says 25%,
  changing a rate the operator never mentioned. An override now keeps every field it does not name,
  and the tier is settable explicitly.
- **`capped()` ignored the grace band `settle` honours.** `Budget.settle` treats the real ceiling as
  `cap_usd + grace_usd`; `capped()` asked `observed > cap_usd`. Since `exceeded` is true for *any*
  kill, a breaker-killed lane whose spend sat inside its band was receipted `kind: cap` and
  `over_cap: true` while the same receipt recorded `grace_used` -- the band absorbing the same
  dollars the verdict blamed. The fallback the function exists for was never reached, so a fallback
  attempt declared `on: ["breaker"]` was skipped and `conductor report` folded loop kills into cap
  failures.

The rest, all the same failure in different clothes -- conductor disagreeing with itself about one
run: a cap tightened to the mission's last dollars still got its full grace stacked on top, so a
lane could legally spend past `remaining()` and past `max_cost_usd` while `outstanding_cap_usd`
understated it by the whole band; the post-spend crash receipt repriced from the watcher only, which
threw away a cursor lane's entire reported cost (no watcher exists for cursor) and downgraded the
watcher fleets from `reported` to `estimated`; that same path re-settled an already-settled budget
with `killed=False, fleet_status=None`, flipping `exceeded` back to false after the commit had
already been rolled back for exceeding; a lane receipt that was never written was skipped silently
on resume while the *unreadable* one two lines below raised `accounting_unknown`, so a hard-killed
mission could spend the same dollars twice; the forecast keyed a lane with no explicit `model` by
its fleet name instead of its vendor, so it saw `runs: 0` and never warned; `AS_OF` was five days
older than the table it stamped on every receipt; and three promotional rates expired in prose with
nothing comparing a date.

**Refused, and recorded.** Opus's cumulative-vs-per-request tier claim is real arithmetic if true --
the tier thresholds are per-request and the counts reaching `cost()` are a thread aggregate -- but
codex is paused, Opus could not verify cursor's aggregation, and pricing math does not get changed
on an unverified premise. Left unverified, not shipped, exactly as wave 7 left six. The forecast
pooling every model of a vendor into one bucket is pinned by a test as deliberate; F17 turning that
pooled number into money that leaves before launch is an operator decision, not a defect to fix
quietly. The rolling ceiling counting an unpriced run as $0.00 is the documented E9 contract and
both reviewers said so. And `composer-2.5`'s tier stays until there is vendor evidence.

**What the lead had to fix.** One of the wave's own new tests pinned nothing:
`test_in_flight_outstanding_is_the_graced_ceiling_not_the_bare_cap` called `ledger.start(2.25)` by
hand and asserted the ledger echoed 2.25, which pins `Ledger` rather than the dispatch path that now
passes cap plus grace to it. Its own docstring admitted as much. Removed; the end-to-end pair in
`test_cap_grace.py` covers the real change. Five waves, five lead corrections to a green lane's work.

Proof: twenty-four new tests, twenty-one red on 0.92.0's source. Of the three that pass, two are
deliberate regression guards (a spend past the whole ceiling is still `cap` even with a breaker; the
ordinary case keeps its band) and the third was the vacuous one, now gone. 1849 green on the merged
tree, ruff clean.

<a id="r-0.92.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 8: conductor's own prompts, read against conductor's own rules, shipped as v0.92.0 ($8.36, four cold-read lanes then two Grok 4.6 write lanes)

Every wave before this one read code. Wave 8 read the *text conductor pays models to obey*: the
Shape A prompt constants in `shape.py`, and the instruction defaults and verdict contracts in
`mission.py` and `verdicts.py`. The standard was AGENTS.md's own eight "Reviewer prompts: no
quotas, ever" rules, quoted verbatim into each lane prompt, plus the per-fleet guidance from the
model-notes section. Two scopes, both reviewers on each (rule 7), $2.10 for the four reads.

Twenty-five claims, twelve confirmed on bytes, seven refuted, and the rest folded into the twelve.
The two headline defects were both prompts that lied about conductor:

- **The gate nobody was given.** `GROK_REVIEW_PROMPT`, `ADVERSARIAL_PROMPT` and `FIX_PROMPT` all
  told their lane to "run the gate named in the spec". The spec a lane receives is
  `{{mission.prompt}}`, the operator's spec file, and `shape_a` never put the gate command into
  it. Only `BUILD_PROMPT` had a `<gate>` block. So a Grok reviewer, an adversarial lane, and a
  follow-on fix lane with no build thread to recall were each told to run a command they had never
  been shown -- the exact failure `BUILD_PROMPT`'s gate block was written to prevent, reintroduced
  three prompts over. All three now carry the block, filled from the mission's `test`, and drop
  the claim entirely when there is no gate to name.
- **A judge's coin flip.** `_answer_object` refuses an answer carrying two different verdict
  objects, on the stated ground that picking one "is a coin flip dressed as a rule". It decided
  verdict-shaped with `_VERDICT_KEYS`. `_parse_rank_answer` called the same function for a
  *ranking* judge, whose answers are `{"strongest", "reason"}` -- never verdict-shaped, so the
  refusal never fired and the last object won silently. A checklist judge was protected; the judge
  that actually picks a winner was not. `_answer_object` now takes the marker key set as a
  keyword, defaulting to the verdict shape.

The rest: the dispositions schema still named two reviewers after the Opus rewrite added a third;
a deliverable fix lane was told to reproduce a defect it has no gate for and to pass `--basetemp`
to a validator that is not pytest; a deliverable build lane still got call-signature and
test-gaming rules and said "do not commit" twice; the validator command reached the model with its
`{path}` placeholder unsubstituted; the salvage note told read-only reviewers to "review or fix";
the evidence note asked reviewers to cite a map entry that by definition does not exist; the
default collate demanded a winner with no way to say "equivalent" or "none usable" and never said
the whole answer goes in the reply (rules 1 and 6); the checklist contract let an `ok: true`
criterion pass with "no evidence" (rule 5); and `_self_judging_findings` skipped any review lane
with no `base` that still read another lane through a template, so rule 7's own enforcement had a
hole in it.

**Refuted, and recorded so they are not re-raised.** `FINDING:`/`FINDINGS: N` is not a rule 2
quota: it is a count of what was found, with `NO_FINDINGS` as the equally complete answer, and it
is the contract `report.py` parses. `BUILD_PROMPT`'s spec-last ordering *is* Anthropic's guidance,
not a violation of it. Opus reading beyond the diff is deliberate and receipted (F9). The three
"strict contract, lenient parser" items on `checklist_contract` are Postel, and the code comment
says so. The resolver's mission-order tiebreak is a write prompt and a deterministic tiebreak, not
position bias -- Grok refuted Gemini's claim here, correctly. Forcing a "tie/none" option into the
rank schema is a protocol decision, not a prompt fix: the two-order unanimity requirement is the
designed guard. And a tool-call budget on the Gemini review prompt has no evidence behind it on
this shape; its reviews finish at $0.38.

**What the lead had to fix.** Lane A added an `opus_review` keyword to `write_shape_schemas` and
never wired the two callers, so a `--opus-review` mission still wrote the two-reviewer schema to
disk -- the defect the item named, half-fixed and green. Four waves, four lead corrections to a
green lane's work. A passing gate is not a delivered item.

Proof: nineteen new tests, seventeen red on 0.91.0's source (the two that pass are deliberate
regression guards: a non-review lane that templates another is not a judge, and `self_judging:
allow` still lifts). 1826 green on the merged tree, ruff clean. Builds cost $6.26 against $14.00
of cap.

<a id="r-0.91.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 7: the orchestration layer shipped as v0.91.0 ($23.13, six cold-read lanes then five Grok 4.6 write lanes; lead verified every finding on bytes)

The last surfaces no cold review had ever seen: `mission.py` in two halves (the data model and the
numeric parsers, then the resume plan and the scheduler), `land.py`, and `shape.py`. Both reviewers
read all three scopes, per rule 7. Twenty-five claims, eighteen confirmed on bytes, one refuted, and
six left out as unverified rather than shipped on a fleet's word. The fixes then went to five Grok
4.6 write lanes on the operator's instruction that Grok do all the building.

Two findings were worth the whole wave, and neither reviewer found both.

`_build_resume_plan`'s fixed point does not terminate. `_keep_cancelled_lanes` pulls a cancelled
lane out of `rerun` when its cancel winner is kept; the dependency walk in the same `while changed:`
loop pushes it straight back when one of that lane's own `needs` is in `rerun`; the next round pulls
it again. Reproduced before anything was written: still changing after 200 rounds, one duplicate
note appended each time. A real resume hangs there holding the mission running lock while `notes`
grows without bound. The rule was right and its guard was missing: a cancelled lane whose own
upstream must rerun is not settled, so the winner-is-kept rule must not claim it.

`land` reports a green land of work whose gate never finished. `_perform` commits the merge and only
then runs gate, golden, and attest; `land()` writes the receipt only after `_land` returns. A death
between those two leaves the merge in HEAD and no receipt at all, and the already-merged shortcut
answered `ok=True` over it. `_ungated_merge` existed for exactly this defect class but only read
receipts that say `ok: false`, so the case with no receipt walked past it. It now treats a tip
already in HEAD with no completed land receipt as ungated too.

Beside them: `_resolve_is_trusted` read "the resolver never ran" as "nothing to redo", which is true
only for `no hotspots` and false for the budget-blocked `resolve not started`, so a resumed mission
kept an unexecuted resolver forever and reported success with its collisions unresolved. `land`'s
refusal receipt joined an unvalidated lane name into a path on precisely the branch where that name
had just been rejected. `_ungated_merge` globbed `{lane}-*.json`, so lane `fix` inherited `fix-2`'s
receipts. The `CONDUCTOR_LANE` refusal, which the module docstring calls outright, ran after the
mission reads and two git subprocesses. The adversarial lane is a write lane whose prompt says to
change nothing when it finds nothing, and it did not set `no_op_ok`, so a correct NO_FINDINGS failed
its lane and aborted the mission. `shape_a_followon` left the grace band out of its ceiling and had
none of the flag-versus-arithmetic guard `shape_a` was given on 2026-09-08. And a family of guards
this codebase already applies elsewhere was missing here: `pause.spend_usd: true` meant $1.00,
`notify.timeout: "inf"` never timed out, `cap_usd: true` became a real cap, and `LaneResult` would
rehydrate a NaN cost straight into `rank_lanes`' sort key.

One refuted, and it is the useful kind. Grok reported `_pollable_sleep(NaN)` as an infinite loop at
the same confidence as its real findings. It is not: `max(0.0, float("nan"))` returns `0.0`, because
`max` keeps its first argument when the comparison is False, so the guard fires immediately and the
function returns. Every NaN finding in this wave looked alike on the page; only running them told
them apart.

Proof discipline, unchanged and worth restating: all 36 new test cases were run against 0.90.0's
source with only the test files swapped in. Thirty-four are red there. The two that pass are
deliberate guards against over-tightening -- a zero cost is still admitted, a resolve whose tip is
genuinely gone is still refused -- and they are supposed to pass on both trees. Both headline fixes
were checked in isolation as well as in the batch, because a batch is not proof for a test that
touches module state.

One correction was the lead's. The land lane's fix made a tip already in HEAD with no receipt raise
"an earlier land of lane 'x' failed its checks", which is a sentence conductor cannot support: in
that case no land failed anything, it died or someone merged by hand. Split into `_ungated_reason`,
which quotes a failed receipt when there is one and says what is actually known when there is not.
Same refusal either way; only the claim changed.

1807 tests, golden clean.

<a id="r-0.90.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 6: the first wave the fleets *wrote* shipped as v0.90.0 ($13.91, five headless write lanes across Grok 4.6 and Gemini 3.8 Flash; lead scoped the lanes, merged them, and verified on bytes)

Waves 1 through 5 used the fleets as cold readers and the lead wrote every fix. Wave 6 inverted
that on operator instruction: the backlog was already verified on bytes, so no new reads were
bought, and the fourteen items went out as five parallel **write** lanes, grouped so no two touched
the same module. Two Grok 4.6 lanes took `runner.py` (taint enforcement, then lifecycle); three
Gemini 3.8 Flash lanes took `attest.py`, `golden.py` + `cli.py`, and `prices.py`. Every lane ran in
its own `--isolate` worktree off the same release commit.

The measured result: $13.91 for fourteen items, $0.99 apiece, which is AGENTS.md rule 2's estimate
to the cent. All fourteen were fixed. Twenty-six new tests came back, and all twenty-six go red
when they are run against 0.89.0's source with only the test files swapped in -- the check that
separates a pinning test from a decorative one, and the one that matters most when a fleet wrote
both the fix and its test. Not one lane deleted or edited an existing test; every diff was pure
addition. `--test-policy allow` was set on the two runner lanes under rule 3 and bought nothing,
which is itself the finding: on this backlog the clean gate would have held.

Three fixes were still the lead's, and they are the argument against trusting a green lane:

- The attest lane closed the path-jail finding by taking the jail root from `chain.json`'s own
  `mission_id` in preference to the caller's. That is backwards -- the file being verified does not
  get to say which directory it may be read from -- and it was written that way because the honest
  root breaks an existing test. Reversed to prefer the caller, which makes the refusal fire one
  step *earlier* than the signature check; `test_cli_attest_refuses_a_chain_signed_for_another_mission`
  now names both refusals.
- The lifecycle lane fixed the `tip_commit`-names-an-undone-commit item with an unconditional
  `after = GitState.capture()` at the end of the try. Correct, and it broke
  `test_dispatch_takes_only_two_content_manifests`, which pins a cost invariant: exactly two content
  manifests per dispatch. Moved to the three uncommit sites that actually invalidate `after`.
- The same lane's last-resort receipt for a `BaseException` after a paid run was unguarded, so a
  failure writing the receipt would have replaced the interrupt that caused it. Wrapped.

One operating lesson cost four gate failures and no money: a lane's `--test` cannot be this repo's
full suite. Every dispatched process carries `CONDUCTOR_LANE=1` so `land.py` can refuse to run
inside a lane, and seven tests in `test_land_failure_paths.py` assert exactly that refusal, so the
suite fails inside any lane that runs it. All four lanes that produced work failed their gate for
this and this alone; their commits were undone and their worktrees kept, which is the salvage path
working as designed (rule 6). The lane gate for this repository is `env -u CONDUCTOR_LANE` in front
of pytest. Conductor's own test suite is not a neutral gate command for conductor's own lanes.

Also load-related, and worth separating from the above: five `-n auto` suites at once on this
machine flaked `test_stop.py::test_a_stop_request_kills_the_fleet_and_releases_its_worktree` in all
five worktrees, including the lane that touched only `prices.py`. Re-run serially, four were clean
and the fifth was the real manifest-count failure above. Parallel lanes are free; parallel *gates*
are not.

1771 tests, golden clean.

<a id="r-0.89.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 5: runner, fleets, golden, cli, worktrees and attest shipped as v0.89.0 ($5.14, seven cold-review lanes across Grok 4.6 and Gemini 3.8 Flash, no subagents; lead verified every finding on bytes and wrote every fix)

Wave 4 read the primitives. This wave read the last surfaces no cold review had seen: `runner.py`
in two halves (the dispatch and spawn path, then the wait loop and the post-run gates), `fleets.py`
and `prices.py` against the AGENTS.md fleet contract, `golden.py`, `cli.py`, and -- once
Antigravity's quota reset -- `budget.py`/`spend.py`/`ceiling.py` and `worktrees.py`/`attest.py`.
Twenty-four findings; eight were wrong and are recorded below rather than fixed.

The through-line was the same as wave 4's, and it showed up three more times: a run whose receipt
says it failed while its bytes are on a branch. The commit gate stops conductor from committing a
failed run's work, but the self-commit adoption immediately after it ran unconditionally, so a
fleet that committed its own bytes and then timed out, exited non-zero, or cut its stream short
landed them anyway. The undo blocks below do not catch that: `_gate_passed` reads a gate that never
ran as nothing to fail, which is the correct fail-closed reading for the gate and exactly wrong
here. Separately, `budget.settle` is the cap verdict and it runs *after* the commit decision. The
wait loop re-checks the breaker with `final=True` for precisely this reason -- a runaway must not
evade the ceiling by exiting in the same poll tick -- but the watcher has no such re-check, and a
cursor lane has no in-run watcher at all, so a run that crossed its cap was committed and only then
receipted as over budget. `unpriced` is the same case. Both were reproduced on bytes before a line
changed, and both are undone rather than discarded, so a kept worktree still holds the work.

`attest.py` had the sharper version of the same idea. An attestation's signature proves conductor
wrote the statement, not that it wrote it about *this* run: `verify_run_attestation` never compared
the signed `run_id` against the run it was verifying, so a valid attestation copied from another
run's directory verified. The mission link's `attestation_sha256` catches that only when the link
carries one. Beside it, `evaluate_chain` treated "neither completeness figure was recorded" as the
only unrecorded case, so with one recorded and the other missing, the check nobody ran was skipped
and the chain still reported `verified` -- which `land` merges on.

The settings digest, added in the third fail-closed drill, rested on "a read lane runs in plan mode,
which cannot edit these files". F12 makes that false: an explicit `restricted: true` lane and every
read lane with a declared deliverable run on `--permission-mode acceptEdits`, which Edits and
Writes. Shape A's evidence lanes are all in that class, and one that named a settings file as its
deliverable would be exempt from the read-only byte check too, leaving nothing to catch it.
`fleets.claude_can_edit` is now the single source the argv builder and the digest both read.

Three counting bugs. A transient retry is a dispatch, so `dispatch_one` appends a summary for it,
and `len(attempts) > 1` read a cheap attempt that failed on a rate limit and succeeded on its own
retry as an escalation -- the rate AGENTS.md rule B3 quotes -- while charging that retry to
`escalated_usd`. `retry_of` is the only sound mark: the `c5-build-cascade-capped` fixture is a real
mission whose cascade and primary share a fleet/model label and differ only by effort, which
refuted the first fix. `ceiling.rolling_spend` had a lower bound only, so a receipt stamped in the
future sat inside both windows forever. And `prices.py` priced Grok 4.6 flat when xAI doubles it
above 200K prompt tokens and bills the whole request at the higher rate, so a long review was
estimated at half what it cost and judged against a band it had already left; `Price` now carries an
optional `long_context` tier, and cache reads and writes count toward the threshold.

Two quieter ones. `golden.py` listed `schema` in `_CONTRACT_KEYS` and `_requested_contract` always
set it, but no receipt carried the fact, so every fixture reported it uncomparable and turning
structured output on for a lane was never a difference; `dispatch` now records the argv flag as
`structured`, read off the real argv like `permission_mode` and `restricted` beside it.
`worktrees.release` took `rev-parse`'s stdout without its exit code -- an empty answer is never
equal to a `base_sha` that is always set there, so a clean no-op lane was reported as leaving
commits on its branch and its `tip_sha` reached the receipt as `""` -- and read `branch -D`'s exit
code the same way, receipting a branch still in the repository as deleted.

`cli.py` gave up one shape in several places: a value argparse accepted without a bound, or a file
read outside the try that was meant to turn a bad path into a refusal. `--limit` took a negative,
and `entries[:-1]` is a valid end-relative slice, so the listing became every entry except the
oldest under a heading that says recent N. `--older-than` took `nan` and `inf`, which pass gc's own
`< 0` check because NaN fails every comparison, and reach `timedelta`. Nothing refused a path-shaped
mission id, so `../other` reached a directory outside the missions folder through attest, salvage,
land, golden record and export alike. And `--prompt-file` was read bare while `--verdict-file` and
`--agent-file` beside it already returned a clean refusal.

Eight findings were refuted on bytes and not acted on, which is the point of verifying every one.
The `<cwd>`/`<cwd2>` placeholder collision a reviewer rated 9 does not exist: `<cwd>` is not a
substring of `<cwd2>`, because the closing `>` does not match `2`. The claim that redaction misses a
JSON mapping inside a plain-text artifact names a gap `golden.py` already documents as a decision
taken the same day, with the reason a name-based rule was written and withdrawn. A `spend.walk_lane`
finding rated 10 needs one receipt carrying `final` as a string *and* `attempts` as a list; the
summary shape and the lane-receipt shape are disjoint, and no fixture has both. A NaN cost defeating
the cap is blocked by `outputs._reported_cost`; grace without a cap is refused at `Spec.validate`;
a claude watcher enforcing its own cap cannot happen because `runner` passes `cap_usd` only when the
fleet's cap mode is `watcher`. Confidence is not evidence, and neither is agreement between models.

One trap worth recording for the mutation discipline itself: swapping two equal-size blocks within
the same second defeats CPython's `(mtime, size)` bytecode invalidation, so a mutation run can read
a stale `.pyc` and report a false result in either direction. Clear `__pycache__` when a mutation
does not change the file's size.

1745 tests, golden clean.

<a id="r-0.88.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 4: the primitives everything else trusts shipped as v0.88.0 ($4.70, six cold-review lanes across Grok 4.6 and Gemini 3.8 Flash, no subagents; lead verified every finding on bytes and wrote every fix)

Waves 2 and 3 read the modules a lead touches directly. This wave read the ones underneath them:
`verdicts.py` and `outputs.py` (where a model's words become conductor's facts), `prices.py`,
`spend.py` and `budget.py` (the money), `attempts.py` (what a resume may keep without paying
twice), `salvage.py`, `breakers.py` and `collisions.py`, and `verify.py` and `errors.py` (the git
and classification primitives every other verdict rests on). Twenty-nine findings; four were wrong
and are recorded below rather than fixed.

The two that mattered most were both cases of two parts of conductor disagreeing about the same
run. A truncated antigravity turn reports itself with `status: incomplete` rather than an `error`,
on purpose -- no fleet reports it, so it is conductor's own reading, and `Result.failure` treats it
as not ok. The commit gate tested only `output.error`, so a write lane that exited 0, moved bytes,
and passed its gate had its work committed under a receipt saying the run failed: the branch and
the verdict describing the same dispatch differently. And wave 2's own `_keep_cancelled_lanes` ran
once, before the resume's needs cascade, which then runs to a fixed point and can move a winner
into `rerun` afterwards. A cancelled loser is a competitor of its winner, not a dependent of it, so
its `needs` never name the winner and the cascade never revisited it: the resume kept a sink
settled against a winner it was about to pay for again, and if that rerun then failed, the
competitor never ran at all. The settle now runs inside the same fixed point and answers in both
directions.

The breakers took three. A limit went through `value or None` with no type or range check, and
`true` is legal in both JSON and TOML and compares equal to 1, so `stall_timeout: true` killed a
healthy run after one second and named it "no output for Trues"; a negative is behind every elapsed
time and trips on the first check; NaN is False against every comparison and turns the breaker off
without saying so. On a truncated or replaced log the read offset reset and the tally did not, so
every re-read call with a fresh identity counted twice while every identity already seen was
skipped. And `_entries` split with `splitlines()`, which also breaks on U+2028, U+2029, \x0b and
\x0c -- all legal inside a JSON string -- so a tool call whose arguments carried one was split into
fragments, failed to parse, and vanished from the count the breakers exist to keep.

`verify.py`, which everything else trusts, had three places where a git command that did not answer
read as an answer. `GitState.capture` built its manifest from a `git status` that may have failed,
and the manifest of an empty status is the manifest of a clean tree, so two failed statuses
compared equal and failed the lane for a no-op it never made. `commit_work` read HEAD without
checking the exit code, putting `committed: true` with an empty sha on the receipt. `diff_since`
swallowed a failed `git diff` and returned an empty diff -- which is what a judge reads as "the
lane changed nothing". `salvage` had the same unchecked `rev-parse`, and both `land` and `salvage`
took a lane name from the command line and built three paths out of it without checking it was a
lane name.

In `errors.py`, `capped` reads an unpriced run -- every cursor lane -- as a cap, because an unpriced
run cannot be shown to have stayed under one. It was asked before every kind conductor decides for
itself, so a cursor lane that failed its own setup, or was refused before it spawned, came back
`cap` and sent a `fallback: [{"on": ["cap"]}]` chasing a budget that was never the problem. Those
prefixes moved into `_own_check_kind` and are asked first. Beside it, the transport table carried
`ECONNREFUSED` but not the same failure in prose, and `_is_refusal` matches the substring "refus",
so "Connection refused" under a claude `error_*` status classified as a safety refusal, which no
retry policy covers.

Smaller, same shape: `git merge-tree` exiting 1 while naming no file was recorded as a clean merge;
`_parse_conflicts` left git's quoting on a path while `touched_files` unquotes it, so a conflict and
the hotspot naming the same file never matched; `usable_int` accepted negatives, and
`cache_read_tokens` is subtracted from input, so a negative became extra billed input in the
estimate caps are judged against; `_answer_object` kept the last JSON object in an answer, so a
metadata blob under the judgment was parsed as the judgment.

Four findings were refuted on bytes and not acted on. `same_repo` is not broken for a subdirectory:
`git rev-parse --git-common-dir` answers relative to cwd and `same_repo` resolves it, probed
directly. `run_tests` returning `ran=True` when the gate could not start is the fail-closed reading,
since `_gate_passed` treats a gate that did not run as nothing to fail. The reproduce gate runs
after the dispatch, so the pre-spawn `refused` catch-all never shadowed it. And `idle_s` is
exercised, in `test_liveness.py`, outside the scope that lane was given.

Left as it is, on the operator's call: the rolling spend ceiling counts unpriced receipts and then
leaves them out of its dollars, so its figure is a lower bound while the mission ledger refuses to
start on the same evidence. Making the ceiling fail closed would block launches on three unpriced
runs a day out of a hundred and fifty; the refusal message now says when its figure is a lower
bound instead.

1708 tests, golden clean.

<a id="r-0.87.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 3: land, shape, report, approvals, and five tests that pinned nothing shipped as v0.87.0 ($5.42, six cold-review lanes across Grok 4.6 and Gemini 3.8 Flash, no subagents; lead verified every finding on bytes and wrote every fix)

Wave 2 read gc, export, golden, and the resume path. This wave read what was left of the
post-baseline surface: `land.py`, `shape.py`, `report.py`, `approvals.py`, and `graph.py`, plus a
sixth lane over the README sections that describe them. Six lanes, one of them (graph.py) answering
NO_FINDINGS, which is a complete answer and was taken as one.

The defect that mattered most was in `land`. `_perform` resets the checkout after a red step only
when HEAD is still exactly the merge commit and the tree is otherwise clean; when it cannot -- a
dirty submodule checkout, a `reset --hard` that itself failed -- the ungated merge stays in the
branch. The already-merged shortcut asks only whether the pinned tip is an ancestor of HEAD, so the
next `land` answered `ok=True` over that merge without running gate, golden, or attest, and the
receipt said the lane had landed. `_ungated_merge` now reads the lane's own land receipts for a
failed land whose reset did not happen and whose merge is still reachable, and refuses the shortcut
over it; a lane that landed green still takes it. `git merge --abort`'s exit code is read too: an
abort that fails leaves MERGE_HEAD set, and every later `land` then refused with "checkout is
mid-merge" naming nothing about why.

`shape.py` wrote `cap_grace_usd` onto five lanes and left it out of `mission_budget` entirely, on
the reasoning that grace does not change the caps -- true of the caps, false of the ledger. The band
is spend against the same budget, and Grok's is post-hoc, so the dollars are gone by the time it is
granted: at the $0.50 ceiling five graced lanes can legally spend $2.50 over the summed caps against
$1.50 of slack, and the mission runs out before the fix lane after a green build and two clean
reviews. Beside it, `shape_a`'s `adversarial`/`opus_review` arguments and the `CapArithmetic` those
lanes are sized against were independent, while `max_cost_usd` comes from the arithmetic alone, so a
mismatch emitted a lane whose cap was never in the total; it is refused now, in both directions. And
the salvage follow-on gave its fix lane `FIX_PROMPT`, which opens by telling the model it wrote the
change itself earlier in the same thread -- true in `shape_a`, where the fix resumes the build's
session, false in a follow-on, which has no build lane and nothing to resume.

`report.py` had six places where a figure it could not compute was printed as one it had measured. A
missing, non-finite, or negative `duration_s` read as 0.0 and was averaged in; `json` round-trips
both NaN and inf and both survive an `int | float` check, so one receipt turned a whole vendor
group's mean and median into NaN. `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so one
truncated `answer.txt` or mission snapshot aborted the entire report instead of being skipped. An
interrupted or cancelled review that flushed a partial answer was scored as a completed sitting in
rule 7's own finding rate. `usage.input_tokens` written as a JSON float read as 0 and inflated the
cache hit rate. And `usd_per_item` divided a `cost_usd` that skips unpriced runs, or one the
report's own `--since`/`--until` window had cut, so a mission read as cheap rather than unknown; a
new `unpriced` column names those runs and the per-item figure is blank on both paths.

In `approvals.py`, E10's launch-time budget clamp was unreachable. D2 later made the launch rerun
`_plan_check_child` in full against the live ledger, and that check refuses a child whose cap is over
the parent's remaining -- so `min(child.max_cost_usd, parent_remaining)` could only ever be a no-op,
and a parent that spent while the pause waited failed a plan the operator had just approved instead
of launching it smaller. `clamp_budget` widens that one comparison at launch and nothing else; a
parent with nothing left is still a refusal.

Five tests would have survived the deletion of what they name. The approvals import-cycle guard
missed `from conductor import mission` -- the spelling closest to the eight lazy imports in that
file. Both D4 tests routed the resolver to claude, which may take tainted input, so neither ever
fired the refusal D4 exists for. `test_collate_taint_sources_carries_only_what_validate_reads` never
called the function. The reviewer finding-rate fixture was the bare token `NO_FINDINGS`, which the
pre-F1 whole-answer check accepted too, so the narration case F1 was written for went untested. And
the precision heading test asserted "fixer agreement" appears, which "fixer agreement is reviewer
accuracy" also satisfies.

Two fleet claims were refuted rather than fixed. Grok read `REVIEW_TAIL`'s closing `FINDINGS: N` as
a quota, which AGENTS.md forbids; it is the machine-readable verdict `verdicts.review_verdict` and
the whole reviewer finding-rate table parse, and the no-quota rule is about asking for a number of
findings, not about reporting the number found. Gemini reported that `child_policy` is not compared
at launch; every field in it is derived from bytes the sha256 digest already covers.

1667 tests, golden clean.

<a id="r-0.86.0"></a>
## Receipt 2026-09-08: post-baseline hardening wave 2: gc, the export bundle, the resume budget, and fourteen doc claims shipped as v0.86.0 ($5.4, seven cold-review lanes across Grok 4.6 and Gemini 3.8 Flash, one Sonnet subagent for the negative tests; lead verified every finding on bytes and wrote every fix)

The Astra peer review's baseline was `67a47f3`, so the roughly 13,000 lines added between 0.61.0
and 0.85.0 had never been cold-read. This wave read them, split by risk area, and fixed what
survived verification on bytes. Nothing was taken on a fleet's word: two reported findings were
refuted outright, one report's claim about directory symlinks was wrong while its claim about file
symlinks was right, and Grok's independent gc review found exactly the three defects the lead had
already fixed, which is corroboration and not new work.

The worst of them was silent and total: `scrub_guard` fired `bearer token` on the scrubber's own
`Bearer <redacted>`, because the pattern's `\S+` matches the marker every other rule exempts. Any
mission whose transcript so much as mentioned an Authorization header could not be exported at
all -- `export` deleted the bundle and raised. Beside it, Stripe `sk_live_`/`sk_test_`, Slack
`xox[abprs]-`, `npm_`, and AWS `ASIA` had no rule at all and shipped verbatim while the guard
called the bundle clean; `_find_key_material` searched lowercase hex and padded base64 only; a file
symlink planted in a mission subdirectory was inlined under an innocent name; `check()` raised
`AttributeError` on a malformed manifest entry where a problem list is promised, and never read
`manifest["chain"]`, so a manifest could claim `state: verified` over a chain the bundle does not
contain and still check clean.

`gc` was removing a clean worktree whose branch held the only copy of its commits, which is exactly
what AGENTS.md rule 6's salvage path reads from disk; leaving a superseded attempt named only by
`previous_attempts` unprotected while its mission was still live; and, for a bare repository,
falling back to `--show-toplevel` and planning it once per linked worktree -- which also ran
`_branch_disposition` with `HEAD` inside the linked worktree, so `conductor/<run>` was trivially its
own ancestor and was classified `delete-branch`. `export` paired only the mission's own `cwd` with a
placeholder, so a cross-repo mission's other repositories travelled as absolute paths in five
bundle files, and `scrub_guard`, which cannot recover a path it was not told about, passed them.

On the resume path, `Ledger.add`'s finite non-negative cost guard from the previous wave had never
reached `_run_receipt_spend` or `add_child`; `json` round-trips `NaN`, and `NaN >= max` is False, so
a resumed mission could start with a poisoned total and never block. The same reader counted a
cancelled unpriced dispatch as unpriced where the ledger drops it, so resuming an early-cancelled
mission could refuse to start anything as `budget unverifiable`. And the previous wave's own
`and prior_ok` on a cancelled lane was a regression: an interrupt forces `ok = False`, and
SIGINT-then-resume is the flow rule 8 documents and promises not to pay twice, so every cancelled
lane was re-dispatched on exactly the resume the rule exists to serve. What settles a cancelled lane
is whether the sink that beat it is kept, and the cancel reason names that winner, so
`_keep_cancelled_lanes` now decides in a second pass once the kept set is complete.

Nine fail-closed checks had tests only on the path where the check passes, so deleting the check
outright would have left the suite green. Each now has a negative test verified by deleting its own
check and watching it go red. Fourteen doc claims were corrected against the code, including a
cap-raise worked example printing $8.00 for a p80 of $8.04 where `math.ceil` gives $9.00, a
`FIX_PROMPT` still documented as asking for `DISPOSITION:` prose lines when it has moved to a
`dispositions.json` deliverable, and a chain-state list missing the `unrecorded` state the previous
wave added. Left as documented rather than changed, on the operator's call: `salvage --emit`
requires `--items` and `--modules`, which size `build_cap`, and a follow-on mission has no build
lane, so they change nothing it emits.

1634 tests, golden clean.

<a id="r-0.85.0"></a>
## Receipt 2026-09-07: F23 shape a --deliverable and --deliverable-validator: a document or data spec through the build, review, fix shape shipped as v0.85.0 ($0, lead build, dry-run launch on a research document)

The route the data consumer's drill 2 was missing: "correct work, wrong stage" now has a
launcher. `conductor shape a --deliverable PATH [--deliverable-validator CMD]` writes a Shape A
mission whose build lane declares the file as its deliverable with the F22 validator in place of
the evidence map, whose reviewers read a note in place of the evidence block, and whose
review-applying lane keeps its name, its `dispositions.json` receipt, and its pause point but runs
as `stage: build`, because a file has no test to reproduce and the validator already passes on the
build's tip. `mission.py` reads dispositions from any lane that declares `dispositions.json`, and
the policy names `build` and `review` only. `--adversarial` with a deliverable is refused, as is a
validator without a deliverable or a path outside the repo. Verified by a real launch: the CLI
wrote a five-lane mission on a research document with the prose gate as validator and
`--opus-review`, and `conductor mission --dry-run` loaded it. Seven tests. Not run paid; the first
paid run is the next document consumer. Design note: this reverses the 2026-09-07 deferral of a
prose launcher, on the strength of two prose consumers and one data consumer in a day.
1555 tests, golden clean.

<a id="r-0.84.0"></a>
## Receipt 2026-09-07: F22 on a data deliverable: accepted and rejected read live; array schemas; a failed deliverable check undoes the commit shipped as v0.84.0 ($0.30, four scratch-repo dispatches; lead fixes)

The second F22 consumer, on data rather than prose: a JSON catalog with a `type: array` schema and
a stdlib validator, four Sonnet fix dispatches on a scratch repository, $0.30
(`docs/research/2026-09-07-consumer-validator-data.md`). All three verdicts now have a live
receipt: `reproduced` landed with reproduce verdict `validator`; `accepted` on a valid base was
refused as `fix without a reproducing check: validator passed on the base too` with the edit left
in the tree; `rejected` sank the deliverable, failed the gate, and had its commit undone. The
first dispatch found two defects. `_schema_mismatch` demanded a top-level object before reading
the schema, so no list deliverable could ever pass a schema; it now checks a `type: array` schema
one element at a time against `items` and reports `item <n>: ...`. And a deliverable that failed
its E1 check (missing, empty, unparsable, schema) sank `ok` while conductor's commit stayed on the
branch, `ok: false` beside `committed: true` on one receipt; the check now counts as a gate and
the commit is undone with the work left staged, the failed-gate path. Two tests that fail
without the fixes, README paragraph updated. 1548 tests, golden clean.

<a id="r-0.83.0"></a>
## Receipt 2026-09-07: gc identifies a repository by its git common dir; a lane worktree named in a receipt is planned once shipped as v0.83.0 ($0, lead fix)

Found by the item 3 cleanup after 0.82.0: `conductor gc --apply` did everything it planned and
exited 1. A run that ran inside a lane worktree records that worktree as its `isolation.repo`,
and `build_plan` deduplicated candidates by `git rev-parse --show-toplevel`, which answers with
the linked worktree itself, so the conductor repository was planned once per lane worktree the
receipts named: 241 rows, every action four times over, and the second pass of the apply failing
on worktrees the first had already removed. `_main_worktree` now resolves a candidate through
`--git-common-dir` and plans on the main worktree; the live dry run reads 59 rows over 39
repositories, exit 0. One test: a receipt naming a lane worktree yields one plan for the
repository with that worktree as its only remove. README gc paragraph says so. 1546 tests,
golden clean.

<a id="r-0.82.0"></a>
## Receipt 2026-09-07: F22 read live on a document fix lane (validator reproduced, landed by conductor land); prose gate skips fences; the test-only fix lane is a named refusal shipped as v0.82.0 ($2.31, one fix-stage document mission with two tainted reviewers, landed by conductor land; lead fixes)

The first `stage: fix` lane on a document to land through conductor's own gates. Mission
`20260907T204604Z-f22-validator-live-sandbox-doc`: Sonnet 5 at `hard` edited one probe receipt
that failed the prose gate at the base with 10 findings, under the F10 anti-slop rules, as a fix
lane whose deliverable declared `validator: python3 scripts/prose_gate.py {path}`; Gemini 3.7
Flash and Opus 5 reviewed cold under taint. The edit lane's receipt reads `validator.before`
exit 1, `validator.after` exit 0, verdict `reproduced`, and `reproduce.verdict` `validator` with
the test surface untouched; `conductor land --lane edit` merged it, gated the merged tree on the
prose gate, ran golden, and verified the chain. $1.40 for the edit, $0.07 for Gemini
(NO_FINDINGS), $0.84 for Opus (seven findings, confidence 3 to 8), $2.31 and 8 minutes for the
mission. Opus's top finding, at confidence 8, was the gate's fault: the prose gate scanned inside
code fences, so the editor rewrote a tool result the document labelled verbatim, and said so in
its own answer. Fixed by the lead: the gate skips fenced code (four tests), the quote got its
dashes back, and the other six findings were applied by hand. Shipped with it: the reproduce gate
names the case F21 slice 2's fix lane hit, every change under the test surface and the stricter
tests passing on the base, as `reproduce gate passed on the base: every change is under the test
surface and the stricter tests pass there too; run this as a build lane`, `test_only: true` on
the block, kind `reproduce` unchanged, with an AGENTS.md rule that test-only hardening is a
build-stage lane. Receipt document: `docs/research/2026-09-07-consumer-validator-live.md`.
1545 tests, golden clean.

<a id="r-0.81.0"></a>
## Receipt 2026-09-07: F22 non-code artifact acceptance: a deliverable validator run on the before and after bytes, the reproduce gate reads its verdict shipped as v0.81.0 ($13.07, Shape A via the launcher with --opus-review, landed by conductor land)

Review item 5 (idea 8, rank 10), and the last of the five. A prose or data deliverable has no test
surface, so a fix lane on a document was refused by the reproduce gate and the lead applied
reviews by hand (F10, 0.59.0). The review asked for a change of abstraction rather than a pretend
code test, and this is it: `deliverable.validator` names a shell command (`{path}` substituted;
`conductor dispatch --deliverable-validator`) that the runner runs twice after the fleet exits and
before the gate, on the base commit's bytes (`git show`, raw bytes with the file's own suffix, into
the run directory) and on the worktree's bytes, under the gate's timeout, process group, and stop
check. The receipt block carries both runs and a verdict: `accepted` (after passed; before passed
or the file did not exist), `reproduced` (before failed, after passed), `rejected` (after failed
or timed out, which sinks the deliverable with `deliverable rejected by validator: ...`, kind
`deliverable`). A before run that timed out or could not start is no evidence either way, the same
reading `_reproduce_gate` gives its own infra errors. Policy: a `stage: fix` lane whose only change
is a validated deliverable with verdict `reproduced` gets reproduce verdict `validator` and lands
through its ordinary gate; verdict `accepted` on a deliverable-only change is refused with
`validator passed on the base too`; a fix that also changed source keeps today's transplant path,
so a document's validator can never carry a code edit. A deliverable without a validator writes
no `validator` key. README: a Validators paragraph, with the sentence that whether the document
still means what it meant is the reviewers' question, not the validator's. Build $5.58 under a $10
cap from the 0.79.0 tree, 1525 in the lead's fresh-worktree gate, clean merge onto 0.80.0; Gemini
and Grok NO_FINDINGS; Opus ($2.58) found six: the validator ran on interrupted dispatches without
polling the stop flag, a hung before run read as reproduced, the timeout test patched the function
under test, the before bytes resolved from the repo root rather than the lane's cwd, the before
copy lost its suffix and its CRLF line endings, and the reproduced shortcut was not gated on the
deliverable being the only change. The fix lane ($3.85) fixed all six with tests first and
reproduced on its own tests. The resume into the fix lane was refused once by the rolling ceiling
at $25.27 in the hour (the third mission of the hour) and went through eleven minutes later; the
ceiling, not wall clock, paced the last three missions of the day. Landed by `conductor land`:
merge, gate, golden, attest green.

<a id="r-0.80.0"></a>
## Receipt 2026-09-07: F21 slice 3: attempt lifecycle extracted to attempts.py, paid-once and never-trust-a-rehearsal pinned shipped as v0.80.0 ($16.05, Shape A via the launcher with --opus-review, landed by conductor land)

Last of the three F21 slices, and review item 4 is closed. `src/conductor/attempts.py` holds
`Attempt` with its key sets, the attempt, cascade, and retry parsers, the human and script lane
validators, the escalation summary, and the resume-time trust checks (`_trusted_lane` and its
artifact-digest helpers, `_git_answer`, `_read_previous_lanes`, `_salvage_previous_lane`).
`git_run` is still reached through `mission.py` so the two cascade tests that patch it keep
applying. `_run_receipt_spend` stayed with F20, and `_run_attempts` with its closures stayed in
`_execute_mission` because they close over the scheduler's state. `tests/test_attempts.py` pins
the two invariants: three paid attempts across a retry are charged once and a resume repays
nothing, and a receipt whose last attempt never spawned or whose artifact digest moved is never
trusted. Build $6.96 under a $10 cap, 1515 in the lead's fresh-worktree gate; Gemini NO_FINDINGS;
Grok ($0.42) and Opus ($3.46) both found six moved constants missing from `mission.py`'s
re-exports, and Opus added four test gaps (the fold into `previous_attempts` on a forced rerun
untested, the paid-once assertion only exercised by one priced attempt, a top-level-only cycle
guard, the patched-runner assertion missing on one cascade test). The fix lane ($5.07) fixed all
seven and reproduced on the re-export test, which fails on the base with an ImportError. Landed by
`conductor land`: merge, gate, golden, attest green. `mission.py` is 6141 lines from 7702, a
side effect and not the goal; the README's Development section now names the three modules and
the rule.

<a id="r-0.79.0"></a>
## Receipt 2026-09-07: F21 slice 2: approval consumption extracted to approvals.py, D1 and D2 pinned shipped as v0.79.0 ($16.61, Shape A via the launcher with --opus-review; build landed by conductor land, fix lane salvaged by hand)

Second of the three F21 slices. `src/conductor/approvals.py` now holds `_check_pause`,
`_answered_pause_points`, the child digest and policy helpers, `_plan_check_child`,
`_plan_lane_failure`, `_resume_plan_child`, `_answer_human_pause`, `_plan_pause_info`,
`_launch_plan_child`, and three new named pause I/O functions (`read_pause`,
`record_pause_answer`, `write_pause_park`) lifted out of `run_mission` and `_execute_mission`.
Every `mission.py` name the moved code needs is reached lazily inside the function bodies, so the
module never imports `mission` at load; `mission.py` re-exports all twelve names. The monkeypatch
hazard was resolved by keeping every `datetime.now` call in `mission.py` and passing the stamp
down as a keyword defaulting to None. `tests/test_approvals.py` pins D1 (two parked planners: one
answer launches one child and re-asks the other; stop refuses one and the other still launches),
D2 (a one-byte edit to the child after the park is refused, no directory claimed, the recorded
digest unchanged), and the import cycle. Build $8.02 under a $10 cap, 1508 in the lead's
fresh-worktree gate; Gemini NO_FINDINGS; Grok ($0.65) and Opus ($3.54) both found the cycle guard
missing the `from . import mission` spelling, and Opus found the child stamp half of the hazard
unpinned. The fix lane ($4.35) fixed both with tests first and refused Grok's second finding by
reintroducing the regression and watching the test catch it, then failed the reproduce gate:
every change was a stricter test that already passes on the base, so nothing reproduced (kind
`reproduce`). Expected for test hardening, and the salvage path handled it: the build lane
landed by `conductor land` (merge, gate, golden, attest green) and the fix lane's one uncommitted
file was applied from its kept worktree, gated (1511), and committed by the lead. The fix lane's
own cost is the receipt's lesson: a fix whose deliverable is only stricter tests should be run as
a build-stage lane, not a fix-stage one.

<a id="r-0.78.0"></a>
## Receipt 2026-09-07: F21 slice 1: graph policy extracted to graph.py, D3 and D4 pinned shipped as v0.78.0 ($8.45, Shape A via the launcher with --opus-review, landed by conductor land)

Review item 4, first of three slices (simplification 4: extract by invariant, never by line count,
signatures intact). An Opus scoping pass found D1 to D4 already repaired on the tree, so each slice
moves the repaired code and pins its invariant. `src/conductor/graph.py` now holds `MissionInvalid`,
the stage and template constants, `_template_refs`, `_propagate_taint` (the D3 fixed point),
`_self_judging_findings`, the five graph-level validators as module functions with one-line
delegators left on `Mission`, and two named derivations lifted out of `Mission.validate`:
`collate_taint_sources` and `resolve_taint_sources` (D4). `mission.py` re-exports every name.
`tests/test_graph_policy.py` pins D3 (a reversed lane list and a resume-only edge give the same
taint map and the same Cursor refusal), D4 (the runtime resolver taint is a subset of the
load-time set; no tainted sink means never tainted), and the import cycle. Build $3.28 at a
forecast-raised $8 cap, 1502 in the lead's fresh-worktree gate; Gemini and Grok NO_FINDINGS; Opus
($2.66) found an unread `candidate_pool` field whose docstring said validate needed it, and a
cycle guard that missed absolute imports. The fix lane ($1.89) removed the field and widened the
guard, each with a test first. Landed by `conductor land`: merge, gate, golden, attest green.

<a id="r-0.77.0"></a>
## Receipt 2026-09-07: F20 one effect inventory (spend.effects) read by resume, report, spend, and export shipped as v0.77.0 ($7.64, Shape A via the launcher with --opus-review, landed by conductor land)

Review item 3 (simplification 1, D13). Four hand-written walks over the same receipt keys
(`spend.mission_run_ids`, `report._scan_missions`, `mission._run_receipt_spend` with its private
`_collate_run_ids`, `export._lane_run_ids`) become one: `spend.effects` yields an `Effect` per
distinct run id with kind, lane, stage, superseded, and the receipt's own summary record, and the
other three read it. `mission_run_ids` and `spend._collate_run_ids` keep their signatures on top
of it. Five tests, one structural: the three consumers' source may not name `previous_collates`,
`previous_resolves`, or `orders` again. Build $2.63 at a forecast-sized $11 cap; Gemini and Grok
NO_FINDINGS; Opus ($2.37) found the real one: `effects` walks the snapshot before the `lanes`
argument, so a snapshot lane's bare `final` string (record `{}`) shadowed the lane receipt's
attempt record and a run whose directory was gone priced at zero on resume. The fix lane ($2.17)
wrote the failing test first, then reads the lane receipts before the prior snapshot. It refused
Opus's second finding (a lane whose receipt file is missing is now priced from the snapshot, the
widening the spec asked for) and recorded the third as wording (the evidence map claimed all five
tests fail on the old code; only the join test and the structural one do). Landed by `conductor
land`: merge, gate, golden, attest green; 1495 in the lead's fresh-worktree gate on the build tip.

<a id="r-0.76.0"></a>
## Receipt 2026-09-07: Review item 2: cost per landed item on the ledger report shipped as v0.76.0 ($0, lead, no fleet)

AGENTS.md rule 2 sizes a build at about a dollar per spec item; until now the measured figure
was quoted by hand in these receipts. `conductor report`'s Missions table gains `merged` (land
receipts whose merge happened: `ok` true, not a dry run, not the already-merged answer; the F18
mission carries three receipts for one merge, so `landed` alone overcounted), `items` (the spec
items the build lane's evidence map names, read from the runner's captured copy only when the
runner recorded it as parsed and ok; blank before the map existed, never 0 for unknown), and
`usd_per_item` (the mission's whole cost over `items` once a merge happened). A line under the
table sums it and `--json` carries it as `landed`: merged missions and their cost, then the cost
per landed item over the subset with a map, so a merged mission without one is in the total and
out of the division. Read live on today's receipts: nine merged missions at $81.78, four with a
map naming 16 items, $1.99 per landed item (W6+W9 $2.84, W7 $1.78, W11 $1.77, F18 $1.04 on each
mission's own row). Six tests in `tests/test_report.py` cover the receipt filter, the aggregate's
denominator, the unparsed and unreadable copies, the printed columns and line, and the README
paragraph. Lead edit, no fleet, gate 1490 green, golden exit 0.

<a id="r-0.75.0"></a>
## Receipt 2026-09-07: Review item 1: current support matrix at the README entry shipped as v0.75.0 ($0, lead, no fleet)

The peer review's simplification 5: keep the research and this document as the record, and
put a compact current-support matrix at the README entry point so historical model advice and
old proposals stop reading as executable. The README now opens with "Current support", dated,
one row per fleet (status, models, structured output, taint, restricted, persona, cost source),
the shared knobs every fleet takes, and four standing-policy bullets: Shape A as the measured
default, no reviewer quotas, the shelved list with the note that its research sections are the
probe record and not a routing recommendation, and where each rule lives in the source. The
routing table's Codex row points at it as paused. The table says it wins over any older section
that disagrees. Two tests in `tests/test_fleets.py` pin it: the matrix names exactly the fleets
in `FLEETS` with every model name on its row, sits before any other section, and keeps the
policy sentences verbatim. Prose gate: zero new hits on the README. Lead edit, no fleet, gate
1484 green, golden exit 0.

<a id="r-0.74.0"></a>
## Receipt 2026-09-07: undispositioned guard applied on the no-fix-lane branch too shipped as v0.74.0 ($0, lead fix)

One line and a test. The report's no-fix-lane branch counted every parsed review lane as
undispositioned, zero-findings lanes included, while the W11 branch for a mission with dispositions
skipped them, so the same NO_FINDINGS lane read differently by mission shape. Checked first on the
F18 mission's own bytes: its failed fix lane still recorded an empty dispositions list, so the three
NO_FINDINGS reviews already read as not undispositioned; the inconsistency was reachable only on a
mission with no fix lane at all. Both branches now apply the same guard. Gate 1482, golden exit 0.

<a id="r-0.73.0"></a>
## Receipt 2026-09-07: F18 KINDS is the check order of error_kind; F19 a fix lane that wrote only its deliverable skips the reproduce gate, reproduce refusals get their own kind shipped as v0.73.0 ($4.14, Shape A via the launcher with --opus-review, build lane landed by conductor land after the fix lane failed on F19; lead fix)

F18 through `shape a --opus-review`, the first launch where the launcher raised the build cap
itself (F17): $4.14, 12 minutes. `errors.KINDS` is reordered to the sequence `error_kind` returns,
pinned by a structural test that reads the function's source, with one minimal failed `Result` per
kind proving no classification moved; the README parenthetical that excused four order divergences
and its test are gone. All three reviewers wrote NO_FINDINGS (Opus imported the module and ran the
27 fixtures itself). The fix lane then did exactly what the shape asks after three NO_FINDINGS,
wrote an empty `dispositions.json` and answered NO_CHANGES, and conductor failed it: the deliverable
write was not a git no-op, so the reproduce gate read it as a source change without a check and
refused with `fix without a reproducing check: no test-surface change`, kind `unknown`. The
reviewed build tip landed directly with `conductor land --lane build` (gate, golden, attest green).
F19 by the lead: a fix or adversarial lane whose only change is its declared deliverable skips the
reproduce gate with verdict `skipped` and settles ok, and the gate's refusals classify as kind
`reproduce` (new, after `denied`) rather than `unknown`; the new test fails on the previous
runner. Gate 1481, golden exit 0.

<a id="r-0.72.0"></a>
## Receipt 2026-09-07: F17 launcher raises a warned lane's cap to the forecast p80 and records the caps block; plan-digest refusal drilled live shipped as v0.72.0 ($0.04, lead with one Opus subagent in a worktree, gated on the merged tree; one scratch plan-lane mission)

F17: `conductor shape a` reads the forecast before writing the mission file, raises every warned
lane's `cap_usd` to its p80 rounded up to the next whole dollar and `max_cost_usd` by the same
difference, prints `cap raised: <lane> $old -> $new (forecast p80 $p80, n runs)`, and records the
arithmetic for every capped lane under a new top-level `caps` block (`rule_2_usd`,
`forecast_p80_usd`, `forecast_runs`, `cap_usd`, `basis`); `--no-forecast-cap` keeps the rule 2
figure and still records the declined p80. Probed live on the real home: build $5.00 to $8.00
against a $7.37 p80 over 55 runs, the same edit the lead had made by hand before each of the last
three launches. Three tests shown to fail without the source. Item 10's last entry drilled: a
scratch Haiku plan lane parked with `child_sha256` on the pause, the kept child file was edited by
hand before the answer, and the resume refused with `child plan changed since it was approved`,
kind `plan`, nothing launched, $0.04 (`docs/research/2026-09-07-live-drills-fail-closed-checks.md`).
Gate 1479, golden exit 0.

<a id="r-0.71.0"></a>
## Receipt 2026-09-07: W11 undispositioned sharpened, README error kinds pinned to KINDS; 0.69.0 and 0.70.0 receipts read live on the mission shipped as v0.71.0 ($5.30, Shape A via the launcher with --opus-review, landed by conductor land)

W11 through `shape a --opus-review`: $5.30, 21 minutes, landed by `conductor land`. A parsed
review lane that received no matched disposition is now `undispositioned` whether its mission
recorded no dispositions or dispositions naming only other lanes, tracked per lane; a lane with
zero findings is never counted (Opus caught that the spec as written would have over-reported on
every NO_FINDINGS review, the common case here). The README's error-kinds block is pinned to
`errors.KINDS` by a test, with the parenthetical widened to name every place `error_kind`'s check
order departs from it (Opus, second finding). The mission doubled as the consumer for the last two
releases: `usage.price` on both estimated lanes, `in_flight_dispatches: 1, outstanding_cap_usd:
8.0` in `running.json` ninety seconds in and `2 / 5.5` with two reviewers running, the same block
at zero in flight on the pause receipt, and `settings: {"checked": true, "modified": []}` on the
build. Receipts in `docs/research/2026-09-07-consumer-live-receipts-0-70.md`. Gate 1476, golden
exit 0.

<a id="r-0.70.0"></a>
## Receipt 2026-09-07: Ledger in-flight figures live in pause.json and running.json; settings digest check for Claude write lanes; land dry-run flag shipped as v0.70.0 ($0.07, lead with two Opus subagents in worktrees, merged in series, gated on the merged tree; one scratch-repo dispatch)

The two follow-ons from 0.69.0's review and drills. The mission ledger now publishes `to_dict()` to
an observer after every `start` and `finish`; `_execute_mission` points it at the run's own
`running.json` (refreshed atomically, only while the lock's owner is this run, every existing key
untouched) and the park writer puts the same document under `budget` in `pause.json`, so
`in_flight_dispatches`, `outstanding_cap_usd`, and `worst_case_usd` are readable somewhere real
while dispatches run rather than only as zeros in the finished block. A Claude write lane now has
`.claude/settings.json` and `.claude/settings.local.json` hashed before spawn and after the run;
a created, changed, or deleted file fails the run as `settings modified: <paths>`, kind `settings`,
before the deliverable check, the commit, and either gate, with `settings: {"checked", "modified"}`
on every receipt. Drilled live on the deny-rule scratch repository from the third pass: Haiku edited
the deny rule out and the run failed on the re-hash at $0.07. Also: `conductor land --dry-run` on an
already-merged lane now records `dry_run: true`. Ten tests, each shown to fail without its source
change except the pause-file compatibility guard, which cannot. Gate 1470, golden exit 0.

<a id="r-0.69.0"></a>
## Receipt 2026-09-07: W6 price basis and outstanding caps on receipts; W9 export scope declared and the D13 inventory; drill pass three; scrub guard accepts its own redaction shipped as v0.69.0 ($17.41, Shape A via the launcher with --opus-review, landed by conductor land; lead fix; four scratch-repo dispatches)

W6 and W9, the last two open review weaknesses, as one six-item spec through `shape a
--opus-review`: $17.02, 37 minutes, Sonnet build $6.60 (139 tool calls, own gate 1456), Gemini
NO_FINDINGS, Grok one finding, Opus five, Sonnet fix $6.86 with three fixed and three wording
dispositions, landed by `conductor land` with its own gate, golden, and attest green. W6: `Price.source`
(default or override), `prices.basis`, and `usage.price` on every estimated receipt naming the table
key, source, vintage, and note; `Ledger.start`/`finish` around all four tightened dispatch sites so the
mission budget block carries `in_flight_dispatches`, `outstanding_cap_usd`, and `worst_case_usd`
(a report of possible overshoot, never a reservation); the overshoot claim rewritten per enforcement
kind in `budget.py` and the README. W9: `spend.mission_run_ids` public and unioned into the export's
run set (judge orders and resolvers a lane attempt never names), a manifest `scope` object (run id
sources, missing run directories, the files copied, six omission sentences), `completeness`
under `not_verifiable_here`, and a pre-scrub scan that refuses a bundle carrying the receipt key as
bytes, hex, or base64 (exit 1, the leak class). Grok caught a free script in flight counted at the
whole remaining budget; Opus caught that the in-flight keys are only ever written after every
dispatch has returned, so they read zero in every artifact (reworded honestly; a live write is the
open follow-on), plus a stale README passage, a mislabeled provenance tag, the exit-code split, and
`run_files` declaring candidates rather than copies. Exporting the mission itself then hit a
pre-existing guard defect: the scrubber rewrites `NAME_TOKEN=value` to `NAME_TOKEN=<redacted>` and
the guard's regex matched the redaction as a live secret, so any bundle holding an env-secret shape
(here a `cache_write_tokens=` keyword argument in the diff) was refused after scrubbing; fixed by
the lead with two tests, and the export then succeeded with `scope` on real bytes (5 runs from
lanes, chain, and snapshot alike, 53 files, check ok, omissions printed). Item 3 third pass, $0.39:
the `permission_denials` path fired live on a plan-mode Claude read lane (Write and Bash denied,
note on the git verdict); the write-lane branch has no reachable input under `bypassPermissions`
because every withheld tool is absent from the tool list rather than denied; and a project-scope
`permissions.deny` rule was edited away by the lane itself, after which Bash ran and the run read
`ok: true` (recorded as a proposal for a settings-file digest check). Land and salvage refusals
fired post-hoc on the landed W7 mission (moved tip, lane environment, no branch, not kept, already
merged). The mission ran on the pre-merge code, so its own receipts carry neither `usage.price` nor
the new budget keys; the first mission after this release will. Gate 1459, golden exit 0.

<a id="r-0.68.0"></a>
## Receipt 2026-09-07: Evidence map as the build lane's deliverable; W7 precision labels landed via shape a --opus-review shipped as v0.68.0 ($5.35, Shape A via the launcher with --opus-review, landed by conductor land)

Phase H item 6, operator decision 2026-09-07: every Shape A build lane writes `evidence.json`, a
map from spec item to status, files, tests, and the check it ran, as a `commit: false`
deliverable against a schema the launcher writes beside the mission; the three reviewers get it
in an `<evidence>` block after the diff with the instruction to read it as a claim. A
`commit: false` deliverable is now removed from an isolated worktree after capture, so the build's
tip stays clean and buildable (an untracked receipt used to keep the worktree dirty, which would
have refused every reviewer based on it). Then the first real mission through the new shape:
W7's three-item spec via `shape a --opus-review`, $5.35, 22 minutes, landed by `conductor land`.
Opus caught the map's own overclaim on test coverage and a definitional defect in the new
`missions` count that the pair missed; the wall block gave its first live critical-path reading
(stretch 1.10, lead 42 s). Receipts in `docs/research/2026-09-07-consumer-evidence-map-shape-c.md`.
Gate 1445, golden exit 0.

<a id="r-0.67.0"></a>
## Receipt 2026-09-07: W8 wall block: occupied, critical path, lead seconds, stretch; W10 notify process group and bounded output; drill pass two shipped as v0.67.0 ($0.08, lead with two Opus subagents in worktrees, merged in series, gated on the merged tree; three scratch-repo dispatches)

Phase H item 5 and two weaknesses. W8: the mission `wall` block carries `occupied_s` (the union
of every attempt's interval, gate included), `critical_path_s` (the longest chain through the lane
graph, each lane its final attempt plus its gate), and `lead_s` (`wall_s` minus occupied minus
paused, clamped at zero); the report's wall table prints them with `stretch` (`wall_s` over the
critical path), and `lanes_s` and `gate_s` are now documented as lane-work sums that overlap
under concurrency, not a decomposition of elapsed time. Old receipts read blank, never zero.
W10: the notify command runs in its own process group, killed whole on timeout (a backgrounded
child used to outlive the mission), with stdin and output on temporary files and the failure
reason a bounded 500-character tail; the one existing kill helper in `verify` is shared. Item 3
second pass, $0.08: under `taint_shell: allow` a tainted write lane rewrote both hook files with
one shell redirect and answered done, and W1's digest check failed the run with kind `taint`; the
same prompt as a read lane never reached the shell (agy plan mode); Composer 2.5 asked for no
answer produced none and failed with kind `no_answer`. Thirteen tests, each shown to fail with
the source stashed. Gate 1435, golden exit 0.

<a id="r-0.66.0"></a>
## Receipt 2026-09-07: Live drills for six fail-closed checks; Shape C as shape a --opus-review shipped as v0.66.0 ($0.19, six scratch-repo dispatches; lead build for the launcher option)

Phase H items 3 and 4. Item 3, first pass: the read-only inventory of 36 fail-closed checks and
which have a live receipt (`docs/research/2026-09-07-fail-closed-check-inventory.md`), then six
drills on scratch repositories at $0.19 total
(`docs/research/2026-09-07-live-drills-fail-closed-checks.md`). Three checks fired on the
vendor's real stream for the first time: the breaker kill with D15's `fleet_status: incomplete`,
the deliverable parse failure with W4's captured digest, and the Claude inline-agent tool-list
mismatch, which failed a run whose answer was right. Two held without firing (agy's plan-mode
sandbox intercepts a read lane's write before the worktree; the verdict checklist block outranks
a contrary prompt), and one is unreachable on Gemini (it cannot be made to answer with nothing).
Noted, not changed: `--max-tool-calls 1` tripped at three because the breaker polls. Item 4:
`conductor shape a --opus-review` is Shape C on the launcher and the salvage follow-on, a
`review-opus` lane at `hard` under a $4.00 cap (F9 read plus the rule 10 dollar), the review
policy admits `anthropic`, `self_judging: allow` on the mission because the build is Sonnet, the
fix lane needs all three reviews with a `<review_opus>` block between Grok and any adversarial
block, concurrency three. Six tests including a CLI dry run that proves the mission loads with
the lift. Gate 1422, golden exit 0.

<a id="r-0.65.0"></a>
## Receipt 2026-09-07: W3 resume authenticates artifact bytes; W4 deliverable captured after the gates, tree re-judged after teardown shipped as v0.65.0 ($0, lead with two Opus subagents in worktrees, merged in series, gated on the merged tree)

Phase H item 2, the two open weaknesses the peer review ranked highest. W3: every lane receipt now
carries `artifact_sha256` for its answer, diff, and captured deliverable, and a resume re-hashes
the files on disk before trusting the receipt; a mismatch reruns the lane with the note `bytes
differ from the receipt's digest` and keeps the spend, a receipt from before the field is trusted
on path with a note saying so, and the git-unavailable behaviour is unchanged. Human lanes are
checked against the durable receipt's digests, not the result rebuilt from the same files. W4:
the deliverable is hashed when it is checked after the fleet exits and copied into the run only
after both gates and the tree capture, refused with `deliverable changed after the gate ran` if
the bytes moved; after teardown the tree is captured again, drift restates the descriptive counts,
adds a note, and sets `cleanup_required` on the receipt, and a teardown that rewrote the
deliverable fails the run. `ok` is never flipped by the teardown itself. Ten tests, each shown to
fail with the source change stashed. Gate 1416, golden exit 0, $0.

<a id="r-0.64.0"></a>
## Receipt 2026-09-07: Live taint shell-deny drill; denied_calls counted from tool errors shipped as v0.64.0 ($0.06, one tainted Antigravity read dispatch on a scratch repo; lead fix)

Phase H item 1, the live drill the 0.63.0 status paragraph called for. One tainted Antigravity
read dispatch (`gemini-3.7-flash-low`, `--taint --isolate`, cap $0.50) on a scratch repository,
with a prompt that asked it to run `git log`, overwrite `.agents/hooks.json`, and then read two
files. On bytes: `run_command` denied by the hook, `write_to_file` under `.agents/` denied, both
hook digests unchanged, preflight found the one hooks file with all 35 matchers, `agy.log` loaded
one named hook, `taint.taint_shell: "denied"`, the read step still answered correctly. $0.06, 10
tool calls, 18 seconds. The drill found one conductor defect: `denied_calls` read 5 for two
denials because the model quoted both error messages verbatim and the counter scanned every
stdout line; it now counts `step_update` tool errors carrying the marker only, with a test that
feeds an echoing text delta and answer and expects 1. Receipts and what was not exercised (W2's
quoted path, `taint_shell: allow`, a live Claude taint drill) in
`docs/research/2026-09-07-live-probe-taint-shell-deny.md`.

<a id="r-0.63.0"></a>
## Receipt 2026-09-07: G wave 3: measurement (D18, D20, D21) shipped as v0.63.0 ($0, lead with one Opus subagent in a worktree, gated on the merged tree)

One package, merged and gated: ruff clean, 1405 passed (1394 at 0.62.0), `golden check` clean.
Replay now compares the requested dispatch contract (fleet, model, effort, mode, timeout, cap,
taint, taint_shell, restricted, schema presence) against each recording and fails on a
difference, reports an unconsumed recording as a difference, and prints a note for a field an
older recording never carried (D18); every shipped fixture agrees with today's Spec derivation
on every comparable field. Dispositions are keyed by mission, reviewer lane, and finding index,
duplicates counted and unmatched indices excluded, so the corrected rate can no longer exceed
one (D20). The cap-loss line splits Claude runs capped into gate passed, gate failed, and gate
not run instead of reading an unrun gate as passed (D21); on the real receipt store that made
three gate-failed runs visible that the old line had counted as passed.

Phase G's three waves are closed: 23 defects and 5 weaknesses from the outside review fixed with
counterexample tests, 1287 to 1405 tests, no fleet spend, in one sitting.

<a id="r-0.62.0"></a>
## Receipt 2026-09-07: G wave 2: spend and lifecycle (D1, D2, D9-D15), plus salvage, collisions, the build prompt, and two README claims from wave 3 shipped as v0.62.0 ($0, lead with four Opus subagents in worktrees, merged in series, gated on the merged tree)

Four packages in worktrees (approval, lifecycle, contracts, salvage), merged in series, two
import-block conflicts resolved by hand, gated on the merged tree: ruff clean, 1394 passed
(1352 at 0.61.0), `golden check` clean with the standing drift notes only. Every fix carries a
counterexample test; the lifecycle package proved each of its tests red on the unfixed tree.

Spend and lifecycle: a pause answer is consumed by exactly the planner it names, a stop or a
human-pause answer launches no child, and a second parked planner re-pauses on the same
resume (D1); the park records the child's sha256 and policy, launch re-hashes and reruns every
child check, and an edited child is refused with both digests instead of inheriting the old
approval (D2); locks are published whole by temp file and `os.link`, carry an owner token
checked on release, and a half-written lock is never reclaimed (D10); one pre-dispatch guard
covers cancel, stop, and the ledger for first attempts and retries alike (D11), and a cancelled
lane never spawns, checked again beside the stop check before `Popen` (D12); auxiliary runs carry
`lane` and `mission`, superseded resolvers are kept in `previous_resolves`, and report and
spend join them (D13); a list where a string belongs, a bare `NaN` in fleet stdout, or a schema
file that is an array is a `parse` receipt with the cost so far, never a lost run, and a bad
dispositions deliverable fails its lane rather than the mission (D9); mission caps and price
overrides must be finite, non-negative, and not booleans (D14); an Antigravity stream that ends
without a terminal event is `incomplete` and fails the lane even with a green gate (D15).

From wave 3, landed early because nothing in wave 2 touched the files: salvage resolves the
lane's own repository, gates under the producing attempt's `test` and `timeout`, refuses a lane
whose setup, includes, ports, or env it cannot rebuild, hashes the bytes it gated and keeps the
run receipt's digest as lineage (D16, D17); collisions decode git-quoted paths (D22); the Shape A
build lane's prompt names the gate verbatim around the spec (idea 7, `shape_build` prompt
version); the README's first example no longer dispatches through the paused Codex fleet and
the reproduce section says what the check proves rather than when it runs.

One correction to the verification record while fixing D11: a lane's own unpriced failure is
classified `cap`, so the retry gap was reachable only across lanes.

<a id="r-0.61.0"></a>
## Receipt 2026-09-07: G wave 1: trust boundaries (D3-D8, D19, W1, W2, W5) shipped as v0.61.0 ($0, lead with four Opus subagents in worktrees, merged in series, gated on the merged tree)

Four packages, four Opus 5 subagents, four worktrees, merged in series into `feat/conductor-v1`
and gated on the merged tree: ruff clean, 1352 passed (1287 before), `golden check` clean with
the standing prompt-version drift notes only. One merge conflict (an import block in
`mission.py`), resolved by hand. Every fix carries a counterexample test that fails on the old
tree; the land package proved its five D6 tests red on the pre-fix tree before committing.

What changed: taint is computed to a fixed point over the whole lane graph after every lane is
built, so declaration order no longer decides whether a consumer is tainted (D3); the resolver
is validated and dispatched with the same taint bound collate uses and fences a tainted
candidate's patch as tainted (D4); tainted lanes deny `Bash` whole on Claude and `run_command` by
name on Antigravity, the hook files are digested at write and re-hashed after the run, an edit
tool naming `.agents` is denied, the hook command path is quoted, and `taint_shell: allow` is the
documented opt-in back to the prefix list (D5, W1, W2, operator decision); deliverable capture
refuses any symlink and opens without following one (W5); `conductor land` resolves
`refs/heads/<branch>^{commit}` once, refuses when it is not the receipt's `tip_sha` or the
destination is a different repository, merges the pinned SHA, and runs `golden check` as a
subprocess of the merged worktree with an import-root assertion (D6, D19); the attestation
chain binds each signed statement's `mission_id` and `index`, refuses an empty chain, compares
count and head against `result.json`, and reports a state string that land requires to be
`verified` (D7); export writes the same state and no longer calls a missing chain verified (D8).
The anti-slop consumer's cost was corrected to $1.53 on the receipt in three documents (D23).

Not verified live: no tainted lane has run under the new deny list yet; the first tainted
Antigravity lane of the next consumer is the drill.

<a id="r-0.60.0"></a>
## Receipt 2026-09-07: F15 latent items closed: settle answer reads tolerate bad bytes; multi-fix-lane dispositions kept in the report shipped as v0.60.0 ($0, lead work, two hand fixes with tests)

Two of the four latent items the Shape C review left on the F15 line, closed by hand with a
test each: `settle()` now reads a lane's answer and deliverable through one helper that decodes
with `errors="replace"` like every other answer read in `mission.py`, so a stray byte cannot
raise after the spend; and `conductor report` extends a mission's `fix_dispositions` per fix
lane instead of overwriting, so a second `stage: fix` lane's dispositions reach the precision
table. The other two (shapes not in use) stay on the line as record. Gate 1287, golden clean,
nothing dispatched. Phase F is closed with this release; the OPERANT-J sitting and the
HarnessBench live tier are deferred by operator decision on 2026-09-07 and wait for the next
roadmap.

<a id="r-0.59.0"></a>
## Receipt 2026-09-07: F10 anti-slop consumer over one document; taint hook count corrected to per-file with a per-matcher preflight shipped as v0.59.0 ($1.53, Sonnet edit lane with untrusted output and an E1 deliverable, Gemini and Opus cold reviews under taint, prose gate, lead applied the reviews by hand)

The second F10 consumer (`docs/research/2026-09-07-consumer-anti-slop.md`): Sonnet 5 edited
one research document as an E1 deliverable with `untrusted_output` set, Gemini 3.7 Flash and
Opus 5 reviewed the diff cold under the taint that setting propagates, and a new
`scripts/prose_gate.py` (no dashes, no filler words, tables intact) was the gate. $1.53. Grok
could not review (taint on Cursor stays refused) and there was no fix lane (a prose document
has no test surface for the reproduce gate), so the lead applied the reviews by hand: Opus
found eight meaning drifts in 47 changed lines at $0.77, six of them real and fixed. The
consumer's first minute found a conductor defect: E21's after-run check compared agy's "loaded
N named hooks" count with the number of matchers written, but agy counts named hooks per
`hooks.json` file, so every tainted Antigravity lane since E21 would have failed as `expected
30` against a log that said 1. Fixed: the F13 preflight now requires every written matcher back
by name (`matchers_missing` on the receipt) and the log check fails only on zero. The mission
was resumed under the fix and the Gemini lane reran green, `hooks_loaded 1`, preflight complete,
and this time it read the file and reported the same 23-word parenthetical Opus had found.

<a id="r-0.58.0"></a>
## Receipt 2026-09-07: F15 the Shape C findings: six verify, report, and wall-clock defects; F1's dispositions.json deliverable, per-finding confidence, calibration line shipped as v0.58.0 ($22.17, two Shape A missions via the launcher, both builds green under cap, Grok one real finding each (one fixed by the fix lane, one test-only added by hand), both landed by conductor land)

Two Shape A missions on this checkout closed the Shape C list from F9
(`docs/research/2026-09-07-f15-shape-c-fixes.md`). **Mission 1** ($7.45) fixed the six defects
Opus 5 had found still on the tree: a read lane whose worktree vanished read as ok (`Verdict.vanished`
beside `no_op`, the F3 skip requires it clear, `Result.failure()` names it ahead of the gate); the
precision table admitted an unparsed verdict as zero findings (now an `unparsed` column, its
dispositions untallied); a disposition naming an unknown lane vanished silently (counted and printed
with the malformed-line sum); `gate_s` omitted the reproduce gate and setup/teardown; `idle_s` reset
on resume (seeded from the prior `result.json`); `busy` exceeded 1.0 on any concurrent mission (the
wall block records `concurrency`; `busy` divides by it). Sonnet built all six green under a $15 cap
for $6.04; Gemini NO_FINDINGS; Grok's one finding was a missing assertion on the printed line, which
a fix lane cannot reproduce under the reproduce gate, so the lead stopped the fix and added it by
hand. **Mission 2** ($14.72) built the three F1 items the pair had passed as implemented: per-finding
`FINDING: <n> <file>:<line> confidence <c>` lines parsed into `review.items`; the fix lane's
`dispositions.json` as a schema-checked E1 deliverable (schema written beside the mission file by
the launcher and by `salvage --emit`, `settle()` reads the kept copy and falls back to prose lines
for older receipts, `LaneResult.from_dict` validates entries); `deliverable.commit: false` with
`commit_work(..., exclude=)` so the receipt never lands in history; and the report's calibration
line and corrected finding rate per vendor. Grok's one finding (confidence 9) was the gap the lead
had read in the diff before the reviews returned: `failure()` exempted only the literal `nothing to
commit`, so a NO_CHANGES fix lane writing only `dispositions.json` would fail; the fix lane fixed it
with a shared `NO_OP_COMMIT_REASONS` set and a reproducing test, $4.17. Both landed by `conductor
land` (gate 1262 then 1282 plus the fix's tests, golden clean, attested). Mission 2's own receipt is
the first with the new wall block: 58 minutes, 45 of them the build, 18 seconds paused, two minutes
in gates. Left on the F15 line as record: the fixture backfill (rejected in F8's sitting in favour
of code fixes) and four latent items.

<a id="r-0.57.0"></a>
## Receipt 2026-09-07: F9 Shape B and Shape C receipts; golden source placeholder landed from the Shape B sitting shipped as v0.57.0 ($11.85, Shape B twice (scratch, then conductor via land), Shape C once, three Opus verifiers)

Shape B ran twice and Shape C once; both shapes now have numbers. **Shape B** (two builders on
one one-item spec, Sonnet 5 at `hard` and Gemini 3.7 Flash, an E4 sitting of Gemini 3.8 Flash and
Sonnet 5 over both orders, `self_judging: allow` because no vendor with structured output was left
to judge cold). On the scratch `slug` spec both builds passed the gate for under a dime each and
the sitting split: the Gemini judge took Gemini's build in both orders (its tests were more
thorough), the Sonnet judge flipped with order, conductor escalated rather than picking, $0.50.
On the conductor spec (scrub `source` in a fixture's `mission.json` to `<source>`) both builds
passed the gate, Sonnet $1.79 and Gemini $0.65, the mechanical ranking put Gemini first on patch
size and cost, and the sitting was unanimous for Sonnet 4-0 with a reason the lead confirmed on
bytes: Gemini's rewrote an empty `source` and popped a missing key against the spec's "missing or
null stays as it is". `conductor land` merged it (gate 1250, golden clean, attested), $3.48 for
the pair and sitting against about $2.80 for one Sonnet build with grace: the judges picked the
build that passed the gate and the spec, and the pair cost a quarter more than one build.
**Shape C** (Opus 5 at `hard` as a third cold reviewer on the F3, F1, and F2 build commits, $2.40
to $2.76 each, $7.87 total, where the Gemini and Grok pair had reported 0, 2, and 3 findings):
Opus reported 5, 9, and 7. Three Opus verifiers checked all 21 on bytes and the lead spot-checked
three: 2 duplicate Grok; 2 are defects the pair missed that the lead later fixed by hand
(the cache column formula, the wall-clock table ignoring `--since`); 6 are defects the pair
missed that are still on the tree (a read lane whose fleet deletes its worktree reports ok; an
unparsed review verdict enters the precision table as zero findings; a disposition naming an
unknown lane is dropped silently; `gate_s` omits the reproduce and setup gates; `idle_s` resets
on resume beside whole-life columns; `busy` exceeds 1.0 under concurrency); 3 are spec items the
pair passed as built but were not built (the C5 fixture backfill, the `dispositions.json`
deliverable, per-finding confidence and the calibration line); the rest are latent or wording.
Nothing Opus reported was a misread. So: at three times the pair's cost per mission, Opus finds
real defects the pair does not, and the pair passes spec gaps. Roadmap item F15 carries the fixes.
Full record: `docs/research/2026-09-07-f9-shape-b-c.md`.

<a id="r-0.56.0"></a>
## Receipt 2026-09-07: F8 golden fixtures for the Phase E shapes shipped as v0.56.0 ($1.58, lead work, four consumer recordings, five scratch missions)

Nine fixtures recorded, five conductor defects fixed in code, none in a fixture. Four
recordings came from the day's consumer missions on the harness repository (a launcher-written
Shape A with all four lanes green, two whose fix pause was answered `stop`, the F11 unattended
read mission with its `notify` hook); five from two scratch repositories, one per Phase E shape
the suite had never replayed: script lanes ($0), a human lane parked and answered ($0), a plan
lane parked on its child and continued ($0.09), a judge sitting over two Cursor candidates
(Composer 2.5 and Grok 4.6 at `cheap`) with a Gemini 3.7 Flash collate and a Sonnet 5 judge in
both orders ($0.41; unanimous for Grok, and right: Composer's `slug` emits a leading hyphen), and
three script lanes across two repositories ($0). What `record` and `check` exposed, each now a
test: (1) replay shelled out to the fixture's `notify` command (harmless only because the
scrubbed path was not real); (2) the plan lane's child, loaded from its kept copy under
`deliverables/`, resolved `cwd: "."` to a non-repository; (3) the collate, every judge order, and
the resolver bypassed the replay dispatcher, so the judge-sitting fixture paid four real judge
dispatches at record, at check, and on a probe (about $1.08, billed into throwaway homes and so
not on the ledger), and its cache hit rate moved between replays; (4) a collate prompt's
`git merge-tree` conflict line cannot be reproduced in a replay repository, now fed back from the
recorded `collisions.conflicts`; (5) the cross-repo `overlap.files` key kept the real repository
path (keys were never scrubbed) and `record` never ran the guard that would have seen it. Also: a
`structured_output` answer was elided and failed the elision check; a recorded run with no
receipt crashed replay instead of reading as a difference; the projection now pins
`notifications`, `collate`, and `resolve`. One gate flake on the first full run (the cascade
resume test, red once under `-n auto`, the same test that failed under two concurrent suites on
E11) is in the serial xdist group and the gate passes `--dist loadgroup`. Eleven fixtures, 1,248
tests, `golden check` clean. Full record: `docs/research/2026-09-07-f8-golden-fixtures.md`.

<a id="r-0.55.0"></a>
## Receipt 2026-09-07: F2 wall clock on the ledger, shipped as v0.55.0 ($15.69, Shape A via the launcher, three findings fixed on the resumed thread, landed by `conductor land`)

Launched on 6e8d725 beside F7, the one Phase F item with the scheduler tax (the paused
interval spans a resume). Build cap $13 (`8 items + $2 scheduler + $2 breadth + $1 summary`)
with the grace band. The build (Sonnet, hard) finished green at $10.02 in 33 minutes: the
`wall` block on the mission result (launched, finished, wall, paused, gate, lanes, idle
seconds, `launched_at` carried across resumes), the "Wall clock" table and the cache column in
`conductor report`, the line in `report.md`, `wall_s` on `conductor missions`, tests, README.
Gemini: NO_FINDINGS, $0.24. Grok: three findings and `FINDINGS: 3`, $0.74, every one real: the
first resume of a pre-F2 mission stamped `launched_at` as now instead of null, hiding the hours
it had sat paused; the two-lane test summed overlapping lane durations at concurrency 2 and
could exceed the wall clock it asserted against; a receipt with no gate duration counted as
zero instead of making `gate_s` null. The fix lane (Sonnet, resumed thread) took all three with
failing tests first, $4.68, three `DISPOSITION` lines, `3 fixed` on the report. Landed by
`conductor land` first time: merge, gate 1223 green, golden, attest. Phase F's eight build
items are shipped, 0.48.0 to 0.55.0, in one sitting.

<a id="r-0.54.0"></a>
## Receipt 2026-09-07: F12 restricted Claude read lanes and permission denials on the receipt, shipped as v0.54.0 ($8.63, Shape A via the launcher, the first release landed by `conductor land`)

Launched on e0fb2c8 (0.52.0), the first mission written by the launcher with no hand edit:
`--tests-items 2` counted twice, `--ceiling` null by default, fix cap $7 from four expected
findings, grace on the Grok lane, the gate preflight passed before anything was written. Build
cap $11. The build (Sonnet, hard) finished green at $7.08 in 26 minutes: `--permission-prompts
none` on every Claude dispatch with `permission_denials` parsed from the envelope onto the
receipt (a write lane with a denial fails as `denied` naming the tool, a read lane gets a
note); a Claude read lane with a deliverable, and therefore every plan lane, dispatching under
`--restricted --permission-mode acceptEdits` so it can write inside the worktree and nothing
else, with the init event checked for `Bash` and `WebFetch` and the run failed closed if either
appears; `restricted: true` as an explicit read-lane key, refused on write and on other fleets;
tests and README. Gemini: NO_FINDINGS, $0.11. Grok: NO_FINDINGS, $1.43, inside its cap with the
new band. Fix lane answered `stop`. Then the landing, three tries, each a receipt: the first
refused because `land` demanded a branch descended from HEAD, which only a fast-forward
satisfies (the spec's wording, the lead's; now shared history is the requirement, with tests
for a diverged branch and an unrelated one); the second merged, ran the gate, saw fifteen
`test_land` failures because the gate now carries the lane marker and the tests inherited it,
and reset the checkout to the pre-merge commit exactly as designed (the tests now scrub the
marker; the one that asserts the refusal sets it itself); the third landed: merge, gate 1217
green, golden, attest, all in one command. The planner gap found by the core-guard mission this
morning is closed: a Claude read lane can now write the file it was asked for.

<a id="r-0.53.0"></a>
## Receipt 2026-09-07: F7 conductor land, shipped as v0.53.0 ($7.65, Shape A via the launcher, clean run end to end)

Launched on 6e8d725 beside F2, the first mission to run the whole Shape A pipeline under
F1's contracts. Build cap $12 (`9 items + $2 breadth + $1 summary`) with the grace band. The
build (Sonnet, hard) finished green at $4.57 in 25 minutes and passed conductor's gate first
time: `land.py` with every refusal from the spec (dirty or mid-merge checkout, wrong branch,
missing branch, a lane's own environment through the `CONDUCTOR_LANE` marker `runner.dispatch`
now stamps on every process it spawns, no gate command), the merge, a throwaway worktree for
the gate and `golden check`, attestation, the hard reset on a red step only when the merge
commit it created is HEAD, receipts under `missions/<id>/land/`, `--dry-run` and `--json`,
the `landed` column, a README section, 22 tests. Gemini: NO_FINDINGS, $0.17. Grok: three
findings and the first `FINDINGS: 3` line on record, $0.87: a merge that fails before its
commit (a rejecting hook) left `MERGE_HEAD` set with no abort; the gate environment omitted
the lane marker the spec said to match; the refusal tests never asserted exit 3 through the
CLI or compared HEAD and status before and after. The fix lane (Sonnet, on the build's
resumed thread) took the first two with failing tests first and marked the third `already`
after strengthening the six tests, $2.04, and its receipt carries the three `DISPOSITION`
lines parsed (`2 fixed, 1 already` in the report table). Merged, fresh-worktree gate 1189
green, golden clean, attested. The next release is the first that `conductor land` can cut.

<a id="r-0.52.0"></a>
## Receipt 2026-09-07: F13 Antigravity hooks preflight and read-lane schema refusal, shipped as v0.52.0 ($7.88, Shape A via the launcher, build green under cap)

Launched on 2f7189f beside F5 and F6, the item rewritten after the morning's probe found no
`denied_actions` field and a free `/hooks` answer instead. Build cap $8 (`6 items + $1
breadth + $1 summary`) with the grace band. The build (Sonnet, hard) finished green at $6.98
in 24 minutes and passed conductor's gate on the first try (1131 green even under the day's
load): `fleets.build_agy_hooks_argv`, the preflight in `runner.dispatch` before ports, setup,
and the paid spawn, failing closed as `taint hooks not enforced` with `preflight` on the
`taint_enforcement` block and `hooks-preflight.json` in the run directory; the after-the-run
log count kept as the second source; `Spec._validate_schema` refusing a schema on an
Antigravity read lane; README and AGENTS.md. Gemini: NO_FINDINGS, $0.19. Grok: two findings,
$0.71: the README said `preflight` is omitted on a run that never reached spawn where the
bytes attach it on exactly that path (wording, real); and every test swapped the argv builder
for a script so the production argv was never exercised (coverage, with a point). Both taken
by hand on the build's branch: the sentence corrected, the real argv pinned. Fix lane answered
`stop`. Merged, fresh-worktree gate 1171 green, golden clean, attested. A tainted Gemini lane
now proves its hooks loaded before it spends anything.

<a id="r-0.51.0"></a>
## Receipt 2026-09-07: F6 launcher completeness, shipped as v0.51.0 ($4.83, Shape A via the launcher, salvaged on the interrupt-test flake)

Launched on 2f7189f beside F5 and F13. Build cap $9 (`7 items + $1 breadth + $1 summary`)
with the grace band. The build (Sonnet, hard) finished at $4.09 in 15 minutes: `--ceiling
none|default|H,D` with `none` the default for an attended launch (operator decision), the same
flag on `conductor salvage --emit`; `--tests-items` counted twice and printed as its own term;
`--findings N` sizing the fix cap (`$2 base + $1 per finding + $1 summary`, $7 at the default
of four); and the gate preflight, which runs the gate once in a throwaway worktree at dry-run
time and refuses a launch whose gate cannot run there, with `--skip-preflight`. Its own suite
was green at 1131; conductor's gate failed on the interrupt-test flake, the same as F5, so the
lead cherry-picked the test fix onto the kept tree and salvaged (own gate 1132 green). Gemini:
NO_FINDINGS, $0.16. Grok: one finding, $0.58, real: the prompt files were written before the
preflight ran, so a refused launch left `prompts/*.md` beside the mission path. Fixed by hand
(preflight first, then prompts, then the mission) with the refusal test extended to assert an
empty directory; fix lane answered `stop`. Merged, fresh-worktree gate 1165 green, golden
clean, both missions attested. From this release on, a mission written by `conductor shape a`
launches without hand edits, and the gate command that cost F1 and F3 their salvages is caught
before a dollar is spent.

<a id="r-0.50.0"></a>
## Receipt 2026-09-07: F1 reviewer verdicts and fix-lane dispositions, shipped as v0.50.0 ($8.26, Shape A via the launcher, salvaged on the lead's gate command)

Launched with F3 and F4 on 156d4c6. Build cap $12 (`9 items + $2 breadth + $1 summary`, the
tests counted twice) with the grace band. The build (Sonnet, hard) wrote everything in 20
minutes at $7.37, 145 tool calls: `verdicts.review_verdict` reading the last non-empty line
(a trailing code fence skipped), `fix_dispositions` and its malformed count, `review` and
`dispositions` on the lane result and receipt, a `review/fix` column in the report table, the
reviewer precision table in `conductor report`, the two prompt contracts in the launcher, 30
tests. Its gate then failed on the lead's relative gate command (see the F3 receipt); the
lead corrected the snapshot, salvaged (own gate 1120 green), committed on
`feat/review-verdicts`, and emitted the follow-on. Gemini: NO_FINDINGS, $0.29. Grok: two
findings, $0.60: the human row and the skipped-without-attempts row emitted 18 cells under a
19-cell header so `taint` rendered under `branch` (real, two lines), and no test pinned the
README paragraphs (coverage, the reproduce gate refuses it by design). The lead stopped the fix
lane and fixed the rows by hand with a test that renders a report over a failed, a skipped,
and a human lane and asserts every row has the header's width. Merged, fresh-worktree gate
1139 green, golden clean, both missions attested. Grok's own review of this change ended
without the `FINDINGS: N` line, as it must: the contract it was reviewing was not yet the one
it ran under. From the next mission on, the report's finding rate is a parsed number.

<a id="r-0.49.0"></a>
## Receipt 2026-09-07: F5 cap grace on Cursor read lanes, shipped as v0.49.0 ($4.09, Shape A via the launcher, salvaged on the interrupt-test flake)

Launched on 2f7189f beside F6 and F13 while Group 1's follow-ons ran, the first Phase F
launches with the corrected gate command. Build cap $8 (`6 items + $1 breadth + $1 summary`)
with the grace band. The build (Sonnet, hard) finished at $2.68 in 10 minutes: `cap_grace_usd`
accepted on a cursor read lane as a band on the post-hoc verdict, refused on a cursor write
lane with the reason, the fleet-and-mode refusal message elsewhere, the launcher and the
follow-on putting the band on `review-grok`, nine tests, README. Its own suite was green at
1117; conductor's gate then failed on `test_interrupted_mission_keeps_ok_lane_and_reruns_
interrupted_lane`, the 0.8 second timer racing three suites on the machine, and so did the
first `conductor salvage`. The lead fixed the test on the main branch (the stop now comes from
inside the second lane), cherry-picked it onto the kept tree, salvaged again (own gate 1117
green), and emitted the follow-on. Gemini: NO_FINDINGS, $0.10. Grok: NO_FINDINGS, $0.42, after
noting that the worktree HEAD was the cherry-pick and reviewing its parent. Fix lane answered
`stop`. Merged, fresh-worktree gate 1122 green, golden clean, both missions attested. Two
timing flakes fixed today, each after it had cost a salvage; neither is on the standing list
any more.

<a id="r-0.48.0"></a>
## Receipt 2026-09-07: F3 no gate on a read lane that moved no source bytes, shipped as v0.48.0 ($4.09, Shape A via the launcher, salvaged on the lead's own gate command)

First Phase F build, one of three launched together on 156d4c6 (F1, F3, F4). Build cap $8
(`6 items + $1 breadth + $1 summary`) with the grace band. The build (Sonnet, hard) wrote
everything in 11 minutes at $3.20: the skip decision taken on a bytes comparison before either
gate runs, the E1 deliverable exemption factored into one helper both sites share, `gate:
{"skipped": ...}` on the receipt, the report line, five tests, a README paragraph. Then
conductor's gate exited 1, and the cause was the lead's: the launcher was given a gate command
with `.venv/bin/pytest` and no `PYTHONPATH=src`, and a worktree has no `.venv` while the venv's
editable install imports the main checkout's source, so the gate tested the wrong tree (F1 and
F4 were launched the same way). The lead corrected the command in the mission snapshot, ran
`conductor salvage` (own gate 1109 green, clean gate skipped under `test_policy: allow`),
committed on `feat/read-lane-gate`, and emitted the follow-on. Gemini: NO_FINDINGS, $0.28.
Grok: NO_FINDINGS, $0.60, its verdict on the last line as every Grok review is. Fix lane
answered `stop`. Merged onto feat/conductor-v1; the fresh-worktree gate failed once on
`test_interrupted_mission_keeps_ok_lane_and_reruns_interrupted_lane` (a 0.8 second wall-clock
timer racing three concurrent suites) and passed on the rerun (1111 green, golden clean); the
test now stops from inside the second lane and no longer races. Both missions attested.

Also on this tip, by hand: the cascade flake diagnosed by a $2.98 Opus read lane
(`verify.git_run` returned exit 1 when git could not be spawned under load, and the resume
trust check read that as a missing commit and paid for the kept lane again; now `GIT_UNRUN`,
one retry, then trust with a note); the release helper under `scripts/`; the Shape A prefix
carrying Anthropic's autonomy and scope blocks; the Composer price note. F4 was launched and
found already shipped ($0.56). The first consumer ran the same hour: the core-guard audit,
three vendors cold, $1.36, findings recorded in the Phase F roadmap.

<a id="r-0.47.0"></a>
## Receipt 2026-09-07: E10 planner lanes, second spec, shipped as v0.47.0 ($13.60, Shape A via the launcher, salvaged once, on the parallel gate)

The last Phase E item, on 0.46.0, and the first run after the speed-up: pytest-xdist in the
dev group takes the gate from about four minutes to about thirty seconds, and AGENTS.md rule 11
now says to overlap the lead's gate with the reviewers, to gate a fast-forward fix tip once on
the merged tree, and to size a test-heavy spec as if the tests were half the work. Build cap $12
(`6 items with the tests counted twice + $2 scheduler + $1 breadth + $1 summary`, raised from the
launcher's $10 by hand). The build (Sonnet, hard) wrote everything in 33 minutes at $10.83 with
its own suite green at 1089, then conductor's gate failed on one test that had nothing to do
with the change: a concurrent first use of the receipt key could read the empty file the winner
had created but not yet written, and the parallel gate made that contention likelier. The lead
fixed the race on the main branch (a short read waits for the writer; pinned, thirty clean runs
under four workers), gated the kept tree (1089 green in 31 seconds), committed, receipted the
salvage, and ran the follow-on. Gemini: one finding, $0.31. Grok: two, $0.45. The same two
bugs from both sides: an adopted child never entered the parent's `children`, so the report and
the missions listing lost it; and a receipt already marked `rolled_up` was trusted on a resume
after a crash before `result.json` landed, so the child's spend never reached the ledger. The fix
lane took both with one failing test first, $1.26. Merged tree gated once in a fresh worktree
(1091 green, ruff clean, golden clean), both missions attested. Wall clock from launch to release:
about 75 minutes, against three hours for the first half.

The feature: at launch a child's `max_cost_usd` is clamped to the parent's remaining budget and
its snapshot says `budget_from_parent`; the plan lane's receipt names the child with `state:
launched` before the child starts; when it returns its cost is rolled into the parent's ledger,
budget, `cost_usd`, `children_cost_usd`, and `pause.spend_usd`, exactly once across resumes; a
parent resumed over a child refuses while the child runs or is paused, adopts a finished child
ok or not, and fails the lane when the child is missing; `report.md` gains a Children section, a
child's report names its parent, and `conductor missions` shows parent and children.

Phase E is complete: 27 items, 0.27.0 to 0.47.0, two days.

<a id="r-0.46.0"></a>
## Receipt 2026-09-07: E10 planner lanes, first spec, shipped as v0.46.0 ($27.53 across five missions, salvaged twice)

Last of Group 7, on 0.45.0, the item the roadmap called most likely to be right in design and wrong
in the receipt. Build cap $10 (`5 items + $2 scheduler + $2 breadth + $1 summary`) with the grace
band; the build (Sonnet, hard) wrote the code, README, and KINDS pin in 31 minutes and ran out at
$10.27 with the grace band used up, no test file and no summary written, and two golden fixtures
re-recorded because it had widened `golden.projection` with a `plan: null` on every lane. The
lead narrowed the projection to plan lanes only, restored the fixtures, gated the kept tree
(1050 green), and committed. A tests-only build on that tip (cap $6, the forecast's first live
warning: a four-item cap under the $8.90 p80) wrote 23 tests at $5.89; Gemini NO_FINDINGS, Grok
one finding (the failing-dry-run case never went through the scheduler), and the fix lane
rewrote that test but was refused by its own reproduce gate, correctly: a test that passes on
the base is coverage, not a fix; the lead took the kept diff by hand. A review follow-on of the
code commit itself then had both vendors writing findings for the first time in a day: Gemini
four, Grok four, two of them the same real bug (the deliverable was loaded from the run
directory's suffix-less copy with the wrong base directory, so a TOML child could not load), one
a real golden gap (a plan lane's deliverable was never recorded, so replay could not reach the
pause), and the rest either resolved already (the tests) or wrong on the code (a stamp the
launch path already does; a receipt `ok` that is computed from `error`). A fix-only mission on
the merged tip took both real ones with failing tests first at $2.60 and refused the other two
with reasons. Every tip gated in a fresh worktree, last one 1077 green, ruff clean, golden clean;
five missions attested. Also fixed by hand today, twice: the C7 leak guard read raw base64 as an
env secret when the run spelled "KEY" before its "=" padding, first on 64-character payloads,
then on 44-character signatures; the guard now masks any base64-shaped run of 40 characters or
more for the plain-text scan (only when its "=" signs are trailing padding, so a real
`KEY=<value>` still trips it) and keeps decoding runs for the inside scan. Zero failures in sixty
reruns after. One unexplained event: a second resume of the tests mission started at 01:08 that
this session did not launch, and its conductor process died two minutes in; the lead resumed
again and the fix ran clean.

The feature: `plan: true` on a read lane whose deliverable is a `.json` or `.toml` mission.
Conductor copies the deliverable under the mission directory with its suffix, loads it, stamps
`depth` and `parent`, refuses a child that does not load, exceeds `PLAN_MAX_DEPTH`, has no
`max_cost_usd`, has one over the parent's remaining budget, or has a looser ceiling, dry-runs
it, and parks unconditionally with `kind: child`; `--answer continue` launches the child as its
own mission and links ids both ways; `--answer stop` refuses; a child that pauses leaves the
parent's lane failed naming it; `--unattended` refuses a plan lane. Rollup into the parent's
ledger and resume over a mid-run child are the second spec, not built.

Group 7 total: seven releases (0.41.0 to 0.46.0 plus the guard fixes), $60.04 fleet spend. Every
item under cap on the build except E10; three lead salvages, two of them because a build spent
its whole cap on the code and none on the tests. On this repo, a spec whose tests are a fifth of
the work wants its cap sized as if they were half.

<a id="r-0.45.0"></a>
## Receipt 2026-09-06: E19 cross-repo collisions shipped as v0.45.0 ($10.45, Shape A via the launcher, fix on the resumed thread)

Fifth of Group 7, on 0.44.0. Build cap $9 (`4 items + $2 scheduler + $2 breadth + $1 summary`)
with the grace band, fix cap $7; the E14 forecast printed its first live line at launch (build
cap $9 against a $8.04 p80 over thirty Anthropic builds, no warning). The build (Sonnet, hard)
finished green at $6.86 in 28 minutes, five files, 8 new tests, one existing multi-repo test
updated for the prefix rule, and found a real bug on the way: the D1 resolver was dispatched,
gated, and resume-trusted at the mission's cwd rather than the sinks' own repository. Gemini:
NO_FINDINGS, $0.19. Grok: three findings, $0.58: one real (a multi-repo receipt's top-level
`overlap.hotspots` carried conflict-only paths the single-repo shape never has), one a coverage
request the reproduce gate refuses by design, and one wrong at confidence 10 (`<cwd>` is not a
substring of `<cwd2>`, so replay's substitution order never mattered). The fix lane (Sonnet, on
the build's resumed thread) verified all three with real git, fixed the shape with a failing
test first, and refused the other two with reasons, $2.82. Fresh-worktree gate on the fix tip
1050 green, ruff clean, golden clean, attested. The semantics, as decided: a collision is the
same path in the same repository; each repository runs its own gate; the resolver never merges
across repositories. Top-level hotspots are prefixed `<cwd>:` only when sinks span repositories,
each `groups` entry carries its own unprefixed hotspots and overlap, the judge and the resolver
read only their group, `repositories` lists every distinct cwd on the result, and golden
fixtures scrub extra repositories to `<cwd2>`, `<cwd3>` and replay them into fresh repos.

Group 7 so far: E3 $4.81, E9 $5.95, E13 $5.58, E14 $5.72, E19 $10.45. Grok has now been wrong
once at confidence 10 and right on every other finding today; the fix lane's reproduce gate
is what caught it.

<a id="r-0.44.0"></a>
## Receipt 2026-09-06: E14 cost forecast shipped as v0.44.0 ($5.72, Shape A via the launcher, one finding fixed by hand)

Fourth of Group 7, on 0.43.0. Build cap $6 (`3 items + $2 breadth + $1 summary`) with the grace
band, fix cap $7. The build (Sonnet, hard) finished green at $4.25 in 28 minutes, five files, 11
new tests, no existing test touched. Gemini: NO_FINDINGS, $0.13. Grok: one finding, $1.35, real
but wording: an unstaged lane's warning printed "None" where the stage goes. Fixed by hand with a
test, fix lane answered `stop`. Merged tree gated in a fresh worktree (1041 green, ruff clean,
golden clean), attested. The feature: `forecast.py` groups every priced, non-dry-run receipt by
vendor and stage through the E11 reader, gives each dispatched lane its history count, median,
and nearest-rank 80th percentile (None under three runs), and warns when the lane's cap is under
the p80; `run_mission` records the block as `forecast` on the result and each warning in `notes`
on launch, resume, and dry run alike, never refusing; `conductor shape a` prints a line per lane
after the cap arithmetic. Nothing is estimated from tokens.

<a id="r-0.43.0"></a>
## Receipt 2026-09-06: E13 export bundles shipped as v0.43.0 ($5.58, Shape A via the launcher, salvaged)

Third of Group 7, on 0.42.0, the first mission launched with the E9 ceiling disabled by hand in
its file. Build cap $7 (`4 items + $2 breadth + $1 summary`) with the grace band, fix cap $7.
The build (Sonnet, hard) wrote everything in 24 minutes at $3.39, then failed its own gate on one
test and stopped, its last line saying it would wait for a background task that did not exist.
The failing test was a flake with a real cause under it: the C7 leak guard's env-secret regex
matched the raw base64 of a re-encoded receipt payload whose last run spelled "KEY" before the
"=" padding. The lead fixed the guard in the kept worktree (base64 runs are scanned decoded,
never raw; pinned by a test), gated it in full (1027 green), committed, receipted the salvage,
and ran the emitted follow-on. Gemini: NO_FINDINGS, $0.19. Grok: two findings, $0.54, both
real: a missing or malformed chain.json read as `verified_at_export: true` where `conductor
attest` would refuse it, and `--check` followed a manifest or chain path out of the bundle.
The fix lane took both with failing tests first, $0.73. Fresh-worktree gate on the fix tip 1029
green, ruff clean, golden clean, both missions attested. The feature: `conductor export
MISSION_ID --out DIR [--logs]` writes the mission directory and every run it names through the
C7 scrubber, DSSE payloads decoded, scrubbed, and re-encoded (signatures kept but no longer
verifying, which the manifest says), with a manifest of bundle and original digests, the chain
and attestation verdicts as verified at export, and `verifiable_here` / `not_verifiable_here`
lists; `conductor export --check DIR` verifies digests and chain linkage from the bundle alone.

<a id="r-0.42.0"></a>
## Receipt 2026-09-06: E9 rolling spend ceiling shipped as v0.42.0 ($5.95, Shape A via the launcher, one finding fixed by hand)

Second of Group 7, on 0.40.0 in parallel with E3, merged after it. Build cap $7 (`4 items + $2
breadth + $1 summary`) with the grace band, fix cap $7 by hand. The build (Sonnet, hard) finished
green at $4.67 in 22 minutes, five files, 23 new tests, no existing test touched. Gemini:
NO_FINDINGS, $0.11. Grok: one finding, $1.17, real: the `--unattended` refusals were skipped on a
dry run, so a rehearsal could report `unattended: true` on a mission the real launch would refuse.
Two lines and a test, so the fix lane was answered `stop` and the lead fixed it on the merged
tree. Merged tree gated in a fresh worktree (1008 green, ruff clean, golden clean), attested.
The feature: `ceiling.py` sums the run receipts under the conductor home over the last hour and
24 hours ($10 and $25 by default; a mission's `ceiling` block states both bounds, a number or
`null`), `run_mission` refuses a launch or resume over either bound before the running lock and
records the figures on the result; `conductor mission --unattended` refuses a human lane, a
fix-stage write lane not named in `pause.before`, an unstaged write lane, and a resolve block;
and a lock keyed on the mission file's path under `<home>/locks/` refuses a second overlapping
launch of the same file, naming the mission already running. The builder made the `ceiling`
block all-or-nothing (both keys, each a number or `null`) so a mission that names one bound
cannot silently inherit the other; the lead kept that.

Consequence for the lead: the default ceilings apply to attended launches too. A Shape A day runs
well past $25, so a mission launched from this checkout on a busy day is refused unless its
mission file carries `ceiling: {"per_hour_usd": null, "per_day_usd": null}` or higher bounds.
The launcher does not write that block yet; it is set by hand for now.

Group 7 so far: E3 $4.81, E9 $5.95, in parallel, both under cap, no salvage.

<a id="r-0.41.0"></a>
## Receipt 2026-09-06: E3 untrusted-output lanes shipped as v0.41.0 ($4.81, Shape A via the launcher, no fix needed)

First of Group 7, on 0.40.0, in parallel with E9. Build cap $5 (`3 items + $1 breadth + $1
summary`) with the grace band, fix cap $7 by hand. The build (Sonnet, hard) finished green at
$3.93 in 15 minutes, four files, 18 new tests, 984 total, no existing test touched. Gemini:
NO_FINDINGS, $0.07. Grok: NO_FINDINGS, $0.81, the first time Grok has written an empty review on
this repo. Fix lane answered `stop`. The lead gated the tip in a fresh worktree (984 green, ruff
clean, golden clean), read the diff, merged, attested. The feature: `untrusted_output: true` on
a model or script lane makes that lane a taint source without weakening it: any lane that
references its answer, diff, verdict, test_touched, or deliverable, or resumes its session, is
tainted with the lane in `taint_from`; a collate over it is tainted; the lane receipt and the
report table carry the mark. Refused on a human lane, which is tainted at load already.

<a id="r-0.40.0"></a>
## Receipt 2026-09-06: E6 script lanes shipped as v0.40.0 ($16.32, Shape A via the launcher, fix on the resumed thread)

Second of the Group 6 series, on 0.39.0. Sized by modules: build cap $11 (`5 items + $2 scheduler +
$3 breadth + $1 summary`) with the grace band, fix cap $7 by hand. The build (Sonnet, hard) finished
green at $10.52 in 54 minutes, ten files, 47 new tests, 964 total; it changed two existing tests
and said why (the budget block now always carries `free`; the "every model is priced" pin exempts a
fleet whose cap mode is `none`). Gemini: NO_FINDINGS, $0.20. Grok: three findings, $1.44, all real:
the prompt was written to the script's stdin synchronously before the wait loop started, so a
prompt larger than the pipe buffer into a command that never reads stdin blocked the dispatch
timeout from ever applying; the README showed a lane fragment where the spec asked for a two-lane
mission; and the write-build test had no gate, so a broken script edit would not have failed it.
The fix lane (Sonnet, on the build's resumed thread) took all three with failing tests first at
$4.15: stdin is fed from a daemon thread and the wait loop's kill unblocks it. Fresh-worktree gate
on the fix tip 966 green, ruff clean, golden clean, attested. The feature: `fleet: script` runs
`command` in the lane's worktree through the same dispatch path as a model lane (isolation, gate,
clean gate, reproduce on `fix` and `adversarial`, deliverable, commit, stage semantics, graph
ordering), with the stream ceilings forced off, the prompt on stdin, stdout as the answer, and a
third ledger state: priced at zero and verified (`free: true`), never unpriced, never capped.

Group 6 total: two items, $25.39, in series with the scheduler tax. Both builds finished green
under cap with the $7 fix cap unneeded on E4 and $4.15 of it used on E6. First group since Group 2
with no salvage.

<a id="r-0.39.0"></a>
## Receipt 2026-09-06: E4 judge sittings shipped as v0.39.0 ($9.07, Shape A via the launcher, no fix needed)

First of the Group 6 series, on 0.38.0. Sized by modules: build cap $10 (`5 items + $2 scheduler +
$2 breadth + $1 summary`) with the grace band, fix cap raised to $7 by hand after the dry run. The
build (Sonnet, hard) finished green at $7.17 in 42 minutes, six files, 15 new tests, 917 total,
golden clean; its own summary named every existing test it changed and why (the two rank
disagreement tests read the rendered prompt instead of a shared call counter, because the
fan-out made the counter a race). Gemini: NO_FINDINGS, $0.25, but its lane failed on the gate
conductor ran on its worktree: one cascade test tripped under load while Grok's suite ran beside
it, the known flake. Grok: NO_FINDINGS, $1.65, fifteen cents over its $1.50 read-only cap, the
rule-7 trap the other way round. Both answers complete, nothing to fix, so the fix lane was
skipped by the scheduler rather than paid. The lead gated the build tip in a fresh worktree (917
green, ruff clean, golden clean), read the diff, merged, attested. The feature: `collate.judges`
adds judges 2..M to a rank collate, all 2M order dispatches fan out through the mission's pool,
unanimity across every judge and order names `strongest`, any split escalates with the vote
counts, the rank answer may carry an optional 1-10 score per lane, and the receipt's `tally`
(also `tally.json` and `tally.md` in the mission directory) marks agreement per judge.

<a id="r-0.38.0"></a>
## Receipt 2026-09-06: E7 human lanes shipped as v0.38.0 ($15.25, Shape A via the launcher, fix salvaged)

Second of the Group 5 series, on 0.37.0. Build cap $9 (`5 items + $2 scheduler + $1 breadth + $1
summary`) with the grace band; the build (Sonnet, hard) finished green at $9.11 in 40 minutes, five
files, 29 tests, the band absorbing eleven cents of the summary: the first receipt of E24 saving a
build. Gemini: NO_FINDINGS, $0.24. Grok: four findings, $1.09, all real: the report's resume line
still offered `continue|stop` to a human pause; an answered human lane's `lanes/<name>.json` kept
the park receipt because kept lanes never pass through `settle`; a recorded human lane with a
deliverable could not replay; and `_trusted_lane` still refused any lane with no attempts, the human
bypass in the resume plan never checking `answer_path`. The fix lane hit its $5.25 cap after six
minutes with three of the four done and one long line; the lead wrapped the line, wrote the
fourth (the trust helper accepts a human lane on its answer file and the deliverable, with the
artifact match every other lane gets, and the resume plan routes through it), gated the kept
worktree in full (902 green), committed, merged, fresh-worktree gate 902 green, golden clean,
attested. The lane: `fleet: human`, ask rendered to `asks/<lane>.txt`, pause `kind: human`,
answered with `--answer TEXT` or `--answer-file PATH`, tainted at load always, no run receipt.

Group 5 total: two items, $28.57, in series with the scheduler tax. Both builds hit or grazed
their caps with the work complete; both fix lanes at $5 with grace were one short step from
done. Fix caps for four-finding reviews want $7, not $5.

<a id="r-0.37.0"></a>
## Receipt 2026-09-06: E16 adversarial test lanes shipped as v0.37.0 ($13.32, Shape A via the launcher, salvaged)

First of the Group 5 series on 0.36.0. Sized by modules this time: build cap $11 (`5 items + $2
scheduler + $3 breadth + $1 summary`) with the $0.25 grace band; the build (Sonnet, hard) still
stopped on the cap at $11.30 after 37 minutes and 179 tool calls, with every item written, its own
full gate green, and the summary unwritten. `conductor salvage` (first run with the own gate, added
by hand between groups): own gate green, clean gate red on the KINDS pin in `test_errors.py`, the
rule-3 trap by construction; salvage now records the clean gate as skipped when the lane ran under
`test_policy: allow`, and judges the own gate. Committed by the lead, follow-on emitted and run.
Gemini: NO_FINDINGS, $0.24. Grok: three findings, $0.94, all real: the launcher's adversarial lane
had no `commit` key, so a reproduced test could never land and the fix lane could never build on
it; a fleet that self-commits a source edit on an adversarial lane was not discarded; and the fix
prompt still told the agent a source-only fix is refused. The fix lane (with grace) took the first
two with failing tests first, $0.72, and rejected the third as prompt wording; the lead fixed that
one by hand with a test. Fresh-worktree gate 870 green, golden clean, both missions attested.
The stage: `adversarial` write lanes must name a `base` and `test_policy: allow`, may touch only
the test surface, are judged by the reproduce transplant read backwards (`reproduced` = the gate
fails on the attacked tip), commit only on `reproduced`, and a fix lane built on one inherits the
check (`verdict: inherited`). `conductor shape a --adversarial` adds the lane at $4 with grace.

<a id="r-0.36.0"></a>
## Receipt 2026-09-06: E21 taint on Antigravity shipped as v0.36.0 ($10.46, Shape A via the launcher, fix salvaged)

Third of the Group 4 trio, merged last onto 0.35.0 with no conflict. Before the build, the lead
probed what the agy PreToolUse hook receives on stdin (run 8 in the probe doc): the whole tool
call, `run_command`'s `CommandLine` included, so one conductor-owned script denies by tool name and
by shell prefix from the same list Claude's `--disallowedTools` carries. Build cap $7, build
(Sonnet, hard) green at $6.66 in 38 minutes, nine files: `taint_hook_files`, the script, the files
written after `worktrees.create` and before the baseline, `--log-file` on the argv, the loaded-count
and uncovered-tool checks failing closed with kind `taint`, `taint_enforcement` on the receipt, and
Cursor's refusal now naming why (no rule kind for its native web tools). Gemini: NO_FINDINGS, $0.15.
Grok: four findings, $0.64, every one real: the init event nests its tool list under `init.tools`,
so the uncovered check read an empty list and could never fire; the fifteen `browser_*` tools were
not named and there is no wildcard matcher, so a tainted Gemini lane kept its browser; the exclude
test read the shared checkout's file; a README sentence still said the collate is refused off
claude alone. The fix lane (no grace band yet; launched before E24) hit its $3 cap after 6.5
minutes with three of the four done and a new test whose premise its own fix had made false. The
lead finished by hand from the kept fix worktree: the failing test given a tool the deny set does
not name, and the fourth finding fixed properly, since `git rev-parse --git-path info/exclude`
resolves to the shared file from a linked worktree on git 2.55 (verified), which the build had
written to. The hook paths now go through the worktree-scoped `core.excludesFile` that `include`
already builds (`_apply_include` gained `extra_excludes`), with a direct test that the shared file
is never touched. Fresh-worktree gate 855 green, golden clean, attested.

Group 4 total: three items, $26.91, launched at once, three salvages (two builds capped with the
work done, one fix capped one test short). What the day settled: rule 2 counts modules, not the
roadmap's size; salvage is now a command and paid for itself three times; and a spec that names a
git mechanism without probing it (the exclude path) costs a review round.

<a id="r-0.35.0"></a>
## Receipt 2026-09-06: E17 prompt versions shipped as v0.35.0 ($8.42, Shape A via the launcher, salvaged)

Second of the Group 4 trio on 0.33.0, merged after E24 with no conflict. Same story as E24: build
cap $7, capped at $7.02 after 27 minutes with the code and tests written (`prompts.py`, the
`prompt_sha256` projection key with backfill from the recording, `version_drift` on prompt ids,
the launcher's `prompts/<lane>.md` convention with `--inline`) and only the README paragraphs
missing. `conductor salvage`: clean gate green; the lead wrote the two README paragraphs in the kept
worktree, committed, emitted the follow-on. Gemini: NO_FINDINGS, $0.19. Grok: NO_FINDINGS, $1.20.
Both review lanes then failed their own gate on one E501 line in the new test file, which the
salvage gate cannot see: it is the clean gate, and the clean gate restores the test surface from the
base, so a kept worktree's new tests are never linted by `conductor salvage`. The reviews were written
on the same commit before the gates ran and the lead used them as the review; the line was wrapped
by hand. Fresh-worktree gate 828 green; `golden check` now notes "prompt versions unknown" on both
fixtures, a note and not a failure, and the backfill path filled `prompt_sha256` from each recording.
Two things learned: rule 2 sizes by modules touched, not the roadmap's "size 0.5" (both Group 4
salvages were $7 caps on six-module items; $9 would have cleared both), and `conductor salvage`
should run the kept tree's full gate beside the clean gate (an E23 follow-up).

<a id="r-0.34.0"></a>
## Receipt 2026-09-06: E24 cap grace shipped as v0.34.0 ($8.03, Shape A via the launcher, salvaged)

First of the Group 4 trio on 0.33.0. Build cap $7 (`4 items + $2 breadth + $1 summary`) and the
build (Sonnet, hard) hit it at $7.06 after 24 minutes with every item written and its own gate not
yet run: the roadmap sized the item at 0.5 and it spread over six modules. The kept worktree went
through `conductor salvage` (first real use): clean gate green, committed by the lead, follow-on
review-and-fix mission emitted and run at cwd the worktree. The emit itself surfaced an E23 defect
first: the pasted diff carried `{{lanes.build.diff}}` in its context lines (the change touched
`shape.py`'s prompt constants) and the template scanner refused the mission; fixed by hand before
the follow-on ran, and the follow-on now points reviewers at `git show <sha>` with the diff written
beside the mission file. Gemini: NO_FINDINGS, $0.24. Grok: one README sentence, $0.73, correct
(the paragraph said an in-band run "still stops itself"); the lead stopped the fix lane and rewrote
the sentence. Fresh-worktree gate 818 green, golden clean, both missions attested, merged. The band
itself: `cap_grace_usd` per lane or attempt, refused on the mission and in a cascade, never onto a
fallback, folded into Claude's native flag, `budget.grace_used` on the receipt, $0.25 default in
the launcher, $0.50 ceiling. Rule 10's dollar stays.

<a id="r-0.33.0"></a>
## Receipt 2026-09-06: E23 conductor salvage shipped as v0.33.0 ($9.40, Shape A via the launcher)

Second of the Group 3 pair, launched on 0.31.0 beside E26 and merged after it. Build cap $8
(`5 items + $2 breadth + $1 summary`); build (Sonnet, hard) green at $5.99 in 33 minutes, seven
files: `salvage.py` (gate a kept worktree from a scratch copy through `runner._clean_gate`, never
touching the kept tree; a receipt under `missions/<id>/salvage/` on every call, refused or not),
`shape.shape_a_followon` (the Shape A shape with no build lane, the kept worktree as `cwd`), the
`conductor salvage` command with exits 0/1/3, and a `salvaged` column on the report's missions
table. Gemini: NO_FINDINGS, $0.21. Grok: two findings, $0.62, both confirmed: the follow-on fix
lane carried no `test_policy` and so defaulted to `clean`, the exact rule-3 trap the salvage path
exists to avoid; and `--json` printed the in-memory result rather than the receipt on disk. The
fix lane reproduced both with failing tests first, $2.58, 784 tests, harness gate green, committed
on the build's resumed thread. The lead reworded the follow-on prompt by hand (the commit is the
worktree HEAD, not "on branch"), merged onto 0.32.0, fresh-worktree gate 794 green, golden clean,
attested, and probed the command live: a lane that was not kept is refused with exit 3 and a
receipt, and the report shows the column. The probe also caught what neither reviewer nor the
suite did: a refusal for a mission that does not exist wrote its receipt anyway and so created
`missions/<typo>/`; fixed by hand after the release (no receipt when there is no mission to hold
it), the builder's test for that case rewritten to pin the absence. The first salvage under the
command waits for the next kept worktree.

Group 3 total: two items, $16.42, both launched at once from the launcher, no salvage needed on
either. Every `mission.cwd` site that belongs to a lane now follows the lane, which is what E19 and
a second repository need.

<a id="r-0.32.0"></a>
## Receipt 2026-09-06: E26 per-lane cwd shipped as v0.32.0 ($7.02, Shape A via the launcher)

First of the Group 3 pair on 0.31.0, in parallel with E23. Build cap $8 (`5 items + $2 breadth +
$1 summary`); build (Sonnet, hard) green at $5.49 in 37 minutes: `cwd` joins `_INHERITED`, resolved
per lane against the mission file like the mission-level one; dispatch, resume tip checks, branch
creation and rename, and `_check_branches` follow the lane's cwd; `merge_conflicts` runs per cwd
group and the collisions receipt carries the groups; a resolver over sinks in two repositories is
refused at load; `LaneResult.cwd` on the lane receipt and on the report line when it differs. The
builder found and fixed a real trap on the way: the mission-level cwd was cascading into every
attempt as a concrete value, which would have erased the "use the mission's" sentinel. Gemini:
NO_FINDINGS, $0.39. Grok: NO_FINDINGS, $1.14 (both read-only). Fix lane stopped at the pause with
nothing to fix; the lead branched the build tip by hand, fresh-worktree gate 779 green, golden
clean, attested, merged. No existing test changed. Both reviewers empty is a first on this repo;
the diff is one module plus its tests, which is what rule 7 says Gemini answers correctly.

<a id="r-0.31.0"></a>
## Receipt 2026-09-06: E25 clean-gate diagnosis shipped as v0.31.0 ($5.45, Shape A via the launcher)

Third of the Group 2 missions on 0.28.0. Build cap $6 (`4 items + $1 breadth + $1 summary`); build
(Sonnet, hard) green at $3.18, 82 calls: a clean-gate failure after a test-surface edit now reads
"clean gate exited N after the diff touched K test-surface files: ..." with kind
`gate_test_surface`, and the report line carries the rule-3 sentence. Gemini: NO_FINDINGS, $0.09.
Grok: one finding, $0.61: a transplant infrastructure failure (`_git_failure`: worktree add,
read-tree, or apply failing before the gate command ran) with a touched surface was classified as
the trap. Confirmed; the fix lane reproduced it with a failing test and excluded `infra_error` in
both places, 733 tests, $1.56, harness gate green, committed by conductor. Merged onto 0.30.0,
fresh-worktree gate 768 green, golden clean, attested. The only Group 2 mission with no salvage.

Group 2 total: three items, $16.43, all three launched at once from the launcher, two salvages
from the same timing-sensitive test (`test_a_resume_keeps_the_escalated_flag_on_the_kept_lane`)
under concurrent suites. The lead tried to reproduce it: 30 runs of the test alone across six
concurrent processes and three full suites at once, all green. Unreproduced in 33 attempts; it
fails only under the fleet's own load pattern (a build gate, two reviewers, and a salvage gate at
once). Left as a known flake, cheap to salvage (the kept worktree gates green alone every time).

<a id="r-0.30.0"></a>
## Receipt 2026-09-06: E12 notifications shipped as v0.30.0 ($6.17, Shape A via the launcher)

One of three Group 2 missions on 0.28.0. Build cap $6 (`5 items + $1 summary`); build (Sonnet,
hard) green at $3.92, 91 calls: a `notify` mission key, a 68-line `notify.py` whose `emit` never
raises, three emission points at settle boundaries (after `pause.json`, after `result.json` and
`report.md`, on a lane settling with a breaker), outcomes as notes on `MissionResult.notifications`
and a report section. Gemini: NO_FINDINGS, $0.11. Grok: two findings, $0.45: the stdin JSON had no
trailing newline so the README's own append-to-file example produced concatenated objects after one
event, and a non-UTF-8 byte in the command's output would raise a `UnicodeDecodeError` past `emit`
(only `TimeoutExpired` and `OSError` were caught). Both confirmed, both reproduced by failing tests
first; the fix is two keyword changes on one `subprocess.run` call, 747 tests, $1.69. The harness
gate failed on `tests/test_cascade.py::test_a_resume_keeps_the_escalated_flag_on_the_kept_lane`
again, the same test that failed E11's fix gate while another suite ran, the third such failure on
record; the kept worktree gated green alone, was committed and branched, merged, gated fresh on the
merged head (762, golden clean), attested. That test is timing-sensitive under load and is now a
lead item: read it before Group 3 launches three suites at once again.

<a id="r-0.29.0"></a>
## Receipt 2026-09-06: E22 vendor CLI versions shipped as v0.29.0 ($4.81, Shape A via the launcher)

One of three Group 2 missions launched together on 0.28.0 (E12, E22, E25). Build cap $8 (`5 items
+ $2 breadth + $1 summary`); build (Sonnet, hard) green at $3.97, 81 calls: `fleets.cli_version`
cached per binary path, `fleet_version` on every receipt and in the signed statement, `conductor
fleets` shows it, `golden.json` records `fleet_versions` and `golden check` prints drift notes
without touching the exit code. Gemini: NO_FINDINGS, $0.06. Grok: two findings, $0.78, both README
sentences contradicting the code (the old "exit 1 if anything printed" line, and a claim that old
receipts carry the "unavailable" note). Both confirmed; both documentation. The pause before the fix
was answered `stop` and the lead fixed the two sentences by hand on `feat/fleet-versions` (a $3 fix
lane for two sentences is not a good trade, and the pause primitive exists for exactly this call).
Fresh-worktree gate 742 green, golden replay clean with the two shipped fixtures reporting
"recorded version unknown" as designed, attestation verified, merged.

<a id="r-0.28.0"></a>
## Receipt 2026-09-06: E1 deliverable verdicts shipped as v0.28.0 ($11.94, Shape A via the launcher)

Mission from `conductor shape a`: build cap $9 (`5 items + $3 breadth + $1 summary`), Gemini
$1.00, Grok $1.50, fix $3, budget $16. Ran in parallel with E11. Build (Sonnet, hard): green at
$8.04 of $9, 114 calls, 47 minutes, nine files, 417 lines of tests; the cap was right by under a
dollar. Gemini: NO_FINDINGS, $0.25. Grok: three findings, $0.98: the read-lane exemption compared
a cwd-relative deliverable path to git's root-relative status paths, so a cwd inside the repo
always failed; a JSON Schema `type` given as a list crashed the dispatch after the fleet exited;
and the README's template and taint lists omitted `.deliverable`. All three confirmed. Fix lane on
the resumed thread: each finding reproduced by a failing test first, then fixed; 718 tests, $2.67,
harness gate green, committed by conductor on `feat/deliverables`. Merge onto 0.27.0 conflicted
on one hunk (the dry-run receipt gaining E11's `stage`/`lane`/`mission` and E1's `deliverable`
at the same call), resolved by hand; fresh-worktree gate 727 green, golden replay clean,
attestation verified. Group 1 of Phase E complete: two items, $19.63, both through the launcher,
one salvage (E11's, a concurrent-suite timing failure), no cap losses.

<a id="r-0.27.0"></a>
## Receipt 2026-09-06: E11 ledger report shipped as v0.27.0 ($7.69, Shape A via the launcher)

First mission written by `conductor shape a` (E5): build cap $8 from `5 items + $2 breadth + $1
summary`, Gemini $1.00 read-only, Grok $1.50 read-only, fix $3, budget $15. Ran in parallel with
E1 on the same checkout. Build (Sonnet, hard): green at $4.45, 77 calls, 30 minutes, 696 tests.
Gemini: NO_FINDINGS, $0.13. Grok: one finding, $0.70: the report keyed mission rows by the
snapshot's human `name` while live receipts carry the directory id, so one mission split into two
rows and the id-keyed row never got `ok`/`lanes`. Confirmed on the code. Fix lane on the resumed
thread: reproduced with a failing test, one-line fix, 697 tests green, $2.41. Its harness gate
failed on `tests/test_cascade.py::test_a_resume_keeps_the_escalated_flag_on_the_kept_lane`, an
unrelated test, while E1's fix lane was running the suite on the same machine; the kept worktree
gated green alone (ruff, 697, golden check), was committed there, branched as
`feat/ledger-report`, gated again in a fresh worktree (697, golden clean), attested, and merged.
Second time a fresh-gate timing failure under two concurrent suites cost a salvage (D3 was the
first); a fix lane's gate rerun on its own is a candidate for E25's diagnosis. Group 1 of Phase E.

<a id="r-e0"></a>
## Receipt 2026-09-06: Phase E roadmap drafted with two Opus 5 read lanes (E0)

Mission `e0-roadmap-opus`: two Claude Opus 5 read lanes at `hard`, $5.00 cap each, on the
conductor checkout at 67475c3. `extend` critiqued the lead's draft of `docs/ROADMAP-2026-10.md`
with citations into the code ($2.24, 34 calls); `order` never saw the draft and sized, seamed, and
ordered the same twenty-one items from the code and these receipts ($2.53, 21 calls). Both green,
$4.78 total, no bytes moved. What the lanes changed: per-lane `cwd` does not exist (the draft said
it did); standalone receipt verification is impossible under HMAC with a shared secret; the
fetch-lane item weakens D2 rather than enabling research; interactive lanes contradict the C2
decision on record; the shape picker and the lessons file were dropped; five items were added
(vendor version on the receipt, `conductor salvage`, cap grace, clean-gate diagnosis, per-lane
cwd). Operating note: the draft was uncommitted at launch, so `extend` read it from the main
checkout instead of its worktree and reported that. A read lane that must see a document gets it
committed or passed through `include`.

<a id="r-0.102.0"></a>
## Receipt 2026-09-08: Wave 18: reprice and documentation consolidation shipped as v0.102.0 ($15.14, three Grok builds; local release integration)

The three already-merged build tips are `47e009c` (reprice), `ca9c066`
(README/reference split), and `5843ff6` (documentation consolidation).
Their run receipts, all under `runs/20260908T184819Z-cursor-*`, report:

| run suffix | commit | current estimated cost |
|---|---|---|
| build-one-new-conductor-command | 47e009c | $4.578560 |
| split-conductor-s-readme-into-a | ca9c066 | $7.435212 |
| consolidate-conductor-s-accumula | 5843ff6 | $3.127032 |

Total: $15.140804 estimated, displayed as $15.14; these are existing build
receipts, not new dispatches for release bookkeeping. No provider billing
attestation is inferred from the estimates.

The ownership evaluation found that the consolidated docs broke
`scripts/release.py`: it still searched the reset index for full receipt
headings. The release helper now updates the index and appends an anchored
receipt to the archive while preserving the original-layout interface.
A regression test fails on 0.101.0's script with the split documentation
layout, and a read-only integration test plans a release against the actual
repository. The original 2115-test baseline was green. The release gate passed: ruff
clean, 2117 tests in 48.84 seconds using xdist loadgroup and an external
basetemp. Exit codes were captured separately; both were 0.

The 19 inherited review findings remain separate from this release; they
are not represented as fixed by the version bump.


<a id="r-0.103.0"></a>
## Receipt 2026-09-08: ownership corrections shipped as v0.103.0

The [ownership evaluation](../review/2026-09-08-owner-evaluation.md) records the
pre-edit structural assessment, all 19 open finding dispositions, and verification.
Forecasts now select the declared builder, disclose unknown-price history, exclude
known auxiliary effects, and apply inherited caps and generated names correctly.
Cascade totals include retries and preserve unknown costs. Resume checks recorded
digests and missing repositories, keeps recovered uncertainty, and preserves
cancelled unknown-price counts. Live and resumed budget classification share a rule;
execution and attestation share one gate interpreter. Existing call signatures remain
usable; the optional seed parameter defaults to the previous behavior.

No new conductor mission or vendor probe was dispatched. Direct helper sessions used
existing accounts; their combined charge was not measured, so no zero-cost or total
cost claim is made. Corpus receipts and unrelated working-tree changes were preserved.

Validation: ruff exit 0; 2,145 tests passed on Python 3.14.7 and again on the supported
minimum Python 3.12.13, both with the parallel loadgroup gate and external basetemp.
The new regressions produced 25 behavioral failures against v0.101.0 source, including
the release-layout fix shipped in v0.102.0; preservation cases stayed green. The initial
full gate had two wording-compatibility failures, corrected before the passing rerun.
Full dispositions and verification limits are in the evaluation. This release follows
the separate wave 18 release, v0.102.0 (`815518a`).


<a id="r-0.104.0"></a>
## Receipt 2026-09-08: unavailable verification pauses resume, v0.104.0

The operator approved the resume-policy change after the ownership evaluation.
After two unavailable Git checks, resume exits 4 with a verification diagnostic;
it does not trust the lane or pay to rerun it. The diagnostic is separate from
completed results, and checking occurs before pending human answers are consumed.
Retrying resume rechecks the evidence and reuses completed work. CLI listings,
reporting, GC protection, and parent/child adoption recognize the parked state.

Six integration regressions fail behaviorally on 0.103.0; a seventh covers an
empty child verification record. Ruff exited 0 and the full parallel gate passed
2,152 tests. A first gate under the home-directory build cache exposed an existing
export-guard path issue; the passing gate uses system-temp fixtures, as the suite
expects. That guard issue is tracked for the failure-boundary phase.
Implementation and investigation used only the operator and permitted Grok/Gemini
helpers; no Claude or OpenAI dispatch. Helper account charges are not measured.
Further accounting and lifecycle consolidation continues in this task.


<a id="r-0.105.0"></a>
## Receipt 2026-09-08: receipt and lifecycle consolidation, v0.105.0

CostFacts supplies the same normalized cost and uncertainty flags to live budgets,
resume accounting, recovered attempts, and final attempt summaries. Spend retains
strict receipt admission and Decimal arithmetic; report now parses metadata and
accounting from one JSON read. Resume dependency/cancellation selection operates
on values in resume.py; attempt finalization is separated from dispatch and its
scheduler closures. Existing entry points remain usable.

Fault injection exercises process loss after a saved run, saved lane, and saved
mission, including a second resume. The run-only boundary recovers its spend and
reruns the unfinished lane; later boundaries reuse it. A state matrix checks live,
recovered, and resumed accounting, including loss of the authoritative run file.
The full gate's earlier cache-path failure exposed a real scrub-guard gap: long
literal paths were hidden by base64 masking. Literal needles now scan raw text;
only heuristic secret patterns use masking. Two path regressions and the report
single-read regression fail behaviorally against 0.103.0.

Validation: ruff exit 0; full parallel gate 2,201 passed on Python 3.14.7.
The real-work exercise subsequently paused after Gemini's review and resumed into
2,201 passing tests on Python 3.12.13. Final CLI readback kept both lanes, added no
dispatches, and preserved every run-file digest. Conductor recorded $0.788750
estimated for the exercise, including one breaker-limited failed review. Grok's
source review returned no findings with a blocked range-diff read; Gemini's single
claim was refuted on current code. See the [completion record](../review/2026-09-08-consolidation.md)
for receipts, limitations, and retained evidence. No Claude/OpenAI lane, remote,
push, or publication was used.
