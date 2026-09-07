# conductor: Phase F, from a dispatcher that builds itself to one the lead can measure

Draft, written 2026-09-07, the day Phase E closed (`docs/ROADMAP-2026-10.md`: 27 items,
0.27.0 to 0.47.0, two days, about $212 in fleet spend including the E0 roadmap lanes; receipts in `docs/RESET-2026-09.md`).
Nothing here is built. The operator cuts, then says go.

How it was written: the lead read every Phase E receipt, the Phase E dropped table, the
ledger report over the receipts, and the eleven research reports under `docs/research/`,
then ran two Opus 5 read passes without conductor (one over the three installed CLIs'
`--help` and source against what `fleets.py` actually passes, one over the vendors' public
changelogs, guides, and pricing since 2026-08-25). The research section at the end records
what they found and what it changes. No fleet spend.

Standing constraints carry over unchanged: local-only repository; first-party fleets Claude,
Antigravity, Cursor; Codex and every OpenAI lane paused; OpenCode, OpenRouter, Ollama, pi,
local models, C6 background lanes, and D4 cloud offload shelved and not to be re-proposed. The
standing rejected list in `AGENTS.md` (MCP wrapper, fleet self-commit, atomic budget
reservation, age-based reclaim, `claude --bare`) stands. Every build item ships through Shape A
via `conductor shape a`, one release per item, caps by rules 2 and 10, the lead judging on bytes.

## Shipped since this was written

| item | version | shape | cost |
|---|---|---|---|
| F3 no gate on a read lane that moved no source bytes | 0.48.0 | Shape A via the launcher, salvaged on the lead's gate command, both reviewers NO_FINDINGS | $4.09 |
| F5 cap grace on Cursor read lanes | 0.49.0 | Shape A via the launcher, salvaged on the interrupt-test flake, both reviewers NO_FINDINGS | $4.09 |
| F1 reviewer verdicts and fix-lane dispositions | 0.50.0 | Shape A via the launcher, salvaged on the lead's gate command, Grok's one real finding fixed by hand | $8.26 |

## What the receipts say

The ledger report (`conductor report --since 2026-09-05`, run 2026-09-07) over 130 missions
and 429 runs:

| measure | value | consequence |
|---|---|---|
| Anthropic build lanes | 34 runs, 24 ok, 4 cap misses, 2 gate failures, mean 30 min | the build works; what fails is sizing and the gate under load |
| spend lost to cap-cut runs | $50.59 across 9 runs | the largest single waste category, all salvaged by hand |
| spend lost to gate failures | $23.28 across 10 runs | five of them the same load-sensitive test, none a real defect |
| Grok review finding rate as reported | 34 of 34 | fiction: the report matches `NO_FINDINGS` against the whole answer and Grok narrates before its verdict, so its four real empties (E3, E4, E17, E26) count as findings |
| Gemini review finding rate as reported | 5 of 32 | roughly right (Gemini's answer is the verdict alone) |
| mission wall clock versus release wall clock | 10 to 60 min versus 75 to 180 min | the lead's time between missions (salvage, merge, gate, release bookkeeping) is now the larger cost, as rule 11 says |
| salvages | 13 receipts across 6 missions in Phase E, plus 5 by hand before `conductor salvage` existed | the salvage path is normal; what it lacks is the kept tree's own full gate |

Three things Phase E built have no live receipt outside their own test suite: the unattended
mode with notifications (E9 and E12), human lanes answered by an operator on a real mission (E7),
and planner lanes launching a child that does real work (E10, dry-runs only). Two shapes the
September reset named as worth measuring (Shape B best-of-two with a judge, Shape C with Opus as
a third reviewer) were never run. The first consumers the September roadmap named (OPERANT-J
sitting 2, the HarnessBench live tier, the cross-vendor core-guard audit, anti-slop passes) have
every feature they wanted and no mission yet.

Phase F therefore has three kinds of item, and the third kind is the point: fix what the
receipts show is wrong or missing in the measurements, cut the lead's time between missions,
and run the things Phase E was built for so the next roadmap is drawn from receipts of real work
rather than of conductor building conductor.

## Sizing

One build item is one spec, one mission, one release. Build cap by rules 2 and 10 as the
launcher computes it: a dollar per spec item, plus $2 when it touches the scheduler, the runner's
wait loop, or resume, plus a dollar per module past the second, plus a dollar for Claude's
summary; a spec whose tests are a fifth of the items counts them twice (rule 11). Review and fix
lanes add $3 to $4 per mission. Every item below states its modules, because both Group 4
salvages in Phase E were $7 caps on six-module items that the roadmap had sized at 0.5.

Two rules from the receipts govern parallelism, unchanged: parallelize on snapshot disjointness,
not file disjointness; anything with the scheduler tax ships alone.

Rule 10's flat dollar stays: since E24 the grace band absorbed one terminal-message overrun (E7,
eleven cents) and missed two (E16 at $11.30 over an $11 cap, E10a over $10 with the band spent),
so the ledger does not yet show the band absorbing every overrun.

## F-a: measurements that are wrong or missing

**F1. Reviewer verdicts and finding dispositions on the ledger.** Today a review lane's answer
is prose and the report's only reading of it is an exact match on `NO_FINDINGS`; a fix lane's
refusal of a finding ("wrong at confidence 10", "coverage, not a fix", "prompt wording") is
prose in its summary and reaches no receipt. F1: the review lane's verdict is parsed from the
answer's final non-empty line (`NO_FINDINGS`, or a findings block the prompt asks for at the end
with one line per finding: file, line, confidence), recorded as `review.findings` on the lane
result; the fix lane writes a disposition file under E1 (`dispositions.json`, schema-checked:
per finding `fixed | refused | already | wording`, one sentence why) as its deliverable beside
its diff; `conductor report` gains reviewer precision per vendor (fixed over fixed plus refused),
a calibration line (confidence of the refused findings), and the corrected finding rate. Cursor
has no schema flag, so the last-line contract is the only structured channel there; Claude and
Gemini get the same contract so one parser serves all three. Evidence: the 34-of-34 row above;
Grok wrong once at confidence 10 (E19) and right on about thirty other findings, which the
ledger cannot say; every fix-by-hand decision (E22, E14, E9) was made on a finding the receipt
does not classify. Modules: `report.py`, `shape.py` (review tail and fix prompt), `mission.py`
(lane result), `verdicts.py`. Depends on E1, E11. Size 1. Build cap $8 (`5 items + $2 breadth +
$1 summary`).

**F2. Wall clock on the ledger.** A mission result carries `duration_s` for the process and each
lane its own, and nothing for the time a mission sat paused waiting for the lead, or the time
the gates took. F2: a `wall` block on the mission result (launched, finished, seconds paused,
seconds in gates, seconds in lanes, seconds the scheduler was idle with nothing runnable),
carried across resumes; `conductor report` gains a wall-clock table per mission (wall, of which
paused, of which gate) and a cache-hit column per vendor (`cache_read_tokens` are already on every
receipt and shown nowhere). This is rule 11's cost made visible, so the next roadmap can say
what the lead's time went to instead of guessing from timestamps. Evidence: E4's mission ran 60
minutes, most of it paused before a fix that was never paid; E10a took three hours of wall clock
across five missions and the receipts can reconstruct that only by hand. Resume touch (the
paused interval spans a resume). Modules: `mission.py`, `report.py`. Depends on E11. Size 0.5.
Build cap $7 (`4 items + $2 scheduler + $1 summary`).

**F3. No gate on a read lane that moved no bytes.** `runner.py` runs the mission's gate on every
lane's worktree whenever the mission names one, read lanes included. A read lane that moved no
source bytes is gating the base tree, which the build lane already gated, and it pays for it
twice: about a minute of wall clock per reviewer (four before xdist), and a flake source that
has failed review lanes whose answers were correct (E4's Gemini lane on the cascade flake, both
E17 reviewers on a lint line in the kept tree's new test file). F3: when the lane is `mode:
read` and the git verdict is a no-op (or moved exactly its E1 deliverable), the own gate and the
clean gate are skipped and the receipt says so (`gate: skipped, read lane, source unchanged`),
the way E16 already records the adversarial stage's skip; a read lane that moved anything else
is still gated and still fails. The projection of the `c5-review-fix` golden fixture changes
(its review lane carries a gate block today): backfill, never re-record. Modules: `runner.py`,
`report.py`. Not the wait loop. Depends on E1. Size 0.5. Build cap $5 (`3 items + $1 breadth +
$1 summary`).

**F4. `conductor salvage` runs the kept tree's own full gate beside the clean gate. Already
shipped.** Recorded as an E23 follow-up in the E17 receipt and then landed by hand between
groups (commits "salvage runs the kept tree's own gate beside the clean gate" and "salvage
honors test_policy allow"), which the draft missed. The Shape A build launched for it on
2026-09-07 read the tree, found every item present with its tests, and wrote no diff, $0.56.
Dropped; the receipt is the lesson: read `git log -- <module>` before writing a spec against a
receipt's follow-up note.

## F-b: the lead's time between missions

**F5. Cap grace on a post-hoc-cap fleet.** E24's band is folded into Claude's native budget
flag and refused on every other fleet, because Cursor's cap is a verdict computed after the run
from estimated cost. That verdict is exactly where the rule-7 trap lives: a complete Grok review
a few cents over its cap fails the lane and skips the fix (A3 twice at $1.00; E4 at $1.65 against
$1.50 with `NO_FINDINGS`). F5: `cap_grace_usd` is accepted on a cursor read lane as a band on the
post-hoc verdict, same $0.50 ceiling, receipted as `budget.grace_used`, never on a write lane
(a write lane's cost is bytes, and the band would pay for more of them). The launcher sets it on
the Grok lane. Modules: `fleets.py`, `budget.py`, `shape.py`. Depends on E24. Size 0.25. Build cap
$5 (`3 items + $1 breadth + $1 summary`).

**F6. Launcher completeness.** Four things every Phase E mission file needed by hand after
`conductor shape a` wrote it, and one the first Phase F launch got wrong: the gate command
must use absolute paths and `PYTHONPATH=src` because a worktree has no `.venv` and the venv's
editable install imports the main checkout's source (F1 and F3 were launched with a relative
command on 2026-09-07 and their gates tested the wrong tree); F6 adds a gate preflight that
runs the command once in a throwaway worktree of the repository at dry-run time and refuses a
launch whose gate cannot run there (exit 127, an import from outside the worktree). The three
by-hand edits: the E9 ceiling block (`ceiling: {"per_hour_usd": null,
"per_day_usd": null}`, because the default ceilings apply to attended launches and a Shape A day
runs past $25 before noon); a raised fix cap ($7 for a four-finding review, set by hand on E4, E6,
E7, E9, E13, E14, E19); and the rule-11 test count (E10b's `--items` was raised from 10 to 12 by
hand). F6: `--ceiling default|none|H,D` (operator decision below on which is the default for an
attended launch), `--tests-items N` counted twice in the arithmetic and printed as its own term,
and the fix cap as `$2 base + $1 per expected finding` with `--findings N` defaulting to 4 (the
median Grok finding count on this repo), printed like the build cap. Lead-only inputs, no
forecaster. Modules: `shape.py`, `cli.py`. Depends on E5, E9. Size 0.5. Build cap $5 (`3 items +
$1 breadth + $1 summary`).

**F7. `conductor land`. Operator decision needed.** Every Phase E release ended with the same
ten minutes by hand: merge the fix lane's branch into the checkout's branch, gate the merged head
in a fresh worktree, run `golden check`, attest the mission, and only then bump the version.
Twenty-seven times, one conflict (E1 onto E11). F7: `conductor land MISSION_ID --lane fix`
refuses unless the checkout is clean and not mid-merge, merges the lane's branch with `--no-ff`,
creates a fresh worktree at the merged head, runs the mission's gate and `golden check` there,
attests the mission, prints the result, and on any red step aborts the merge and leaves the
branch where it was. It never bumps a version, never pushes (there is no remote), never runs
inside a mission (refused under a lane's environment, pinned by a test), and is never run by a
fleet: it is the lead's hands, made one command, after the lead has read the diff. The design
question is whether conductor should merge at all: the September roadmap's line is that a
conductor that lands bytes ships unread work, and `conductor salvage` stopped short of
committing for that reason. The distinction proposed here is that salvage lands a fleet's
unreviewed tree and `land` lands a tree the lead has already read and two reviewers have
already covered; the command does nothing the lead would not do next. If the answer is no, the
item drops and rule 11 stays prose. Modules: new `land.py`, `cli.py`, `worktrees.py`,
`attest.py`. Depends on E23, A5. Size 1. Build cap $8 (`5 items + $2 breadth + $1 summary`).

## F-c: receipts of real work

None of these is a build. Each is one or more missions whose product is a receipt, and any
defect a receipt exposes becomes a Phase F fix item sized by rule 2 when it appears.

**F8. Golden fixtures for the Phase E shapes.** The golden suite holds two fixtures, both
recorded on C5 (a capped cascade build and a review-and-fix). Every lane kind Phase E added
(script, human, plan, judge sitting, adversarial, cross-repo, deliverable) is covered by unit
tests and by no replay, and E10a's review found a plan lane whose deliverable was never recorded
so replay could not reach its pause. F8: record one small fixture per shape with `golden record`
on a scratch repository (a launcher-written Shape A on a two-line spec; a judge sitting over two
one-line candidates; a script lane; a human lane answered; a plan lane parked and continued;
a two-repo collision), each under $2, so a Phase F refactor that changes a receipt shape is a
fixture diff. Lead work plus about $10 of fleet spend. Depends on C7, E17.

**F9. Shape B and Shape C receipts.** The September reset listed both as shapes worth running
and neither ran. Shape B: two builders on one one-item spec (Sonnet 5 at `hard`, Gemini 3.7 Flash)
with an E4 sitting of two judges over both orders, conductor keeping the winner; the receipt is
whether the judges pick the build that passed the gate, and what the pair cost against one
Sonnet build. Shape C: Opus 5 as a third cold reviewer beside Gemini and Grok on three Phase F
Group 1 missions; the receipt is whether it reports anything the pair missed, with F1's
dispositions saying whether what it reported was real. About $12 for B and $9 for C. Depends
on E4, F1 for the C receipt to be readable.

**F10. First consumers.** The four missions the September roadmap named, each now buildable:
the cross-vendor core-guard audit (three read lanes over the guard and its test file in the
operator's harness repository, E26 for the cwd, no build); an anti-slop pass over one document
with E1 deliverables and E3 untrusted output; OPERANT-J sitting 2 with foreign judges through
E4; the HarnessBench live tier over the four harnesses through E6 script lanes and E26. Costs
are the consumers' own and are recorded in `docs/RESET-2026-09.md` as receipts of conductor
running something other than itself. Depends on nothing further.

**F11. One unattended read-only mission with notifications.** E9's `--unattended` and E12's
notifications have never run on a real mission. F11: the core-guard audit from F10, launched
at the end of a day under `--unattended` with `notify` configured for notification-hub and
bridge-db, the receipt read the next morning. Launched by the lead's hand, not by launchd (the
launchd fleet is a separate system and stays one). Depends on E9, E12, F10.

## Dropped or narrowed, with reasons

| candidate | call | why |
|---|---|---|
| rule 9 as a refusal (mid-merge checkout) | dropped | self-enforcing: a conflict marker in `src/` is a `SyntaxError` before any refusal could run, and a worktree is created from a commit, so a mid-merge cwd cannot reach a lane |
| a machine-wide gate semaphore across missions | dropped | it would serialize every suite on the machine to work around one test; diagnose the test instead (Group 0) |
| a fix-only shape from a stopped fix lane | narrowed to hand-written | one occurrence (E10a); `conductor salvage --emit` covers the kept-worktree case, and the other three stopped fix lanes were two-line hand fixes |
| running the emitted follow-on from `conductor salvage` | dropped | twice the emitted mission wanted a prompt edit before launch (E24's scanner refusal, E23's wording); the emit stays a file the lead reads |
| structured review output on Cursor via schema | dropped | `cursor-agent` has no schema flag (September probe, unchanged); F1's last-line contract is the channel |
| retiring rule 10's dollar | not yet | the band missed two of three overruns since E24 |
| launchd or cron for F11 | dropped, standing | separately governed system; a plist with home paths cannot be committed here |
| any shelved fleet | dropped, standing | operator decision 2026-09-04 and 2026-09-06 |

## Operator decisions, settled 2026-09-07

1. **F6**: an attended launch from the launcher writes `null` ceiling bounds; `--ceiling
   default` (E9's $10 per hour and $25 per day) is for a file meant to run unattended. Reason:
   every attended mission since E9 carried `null` by hand, and the unattended flag is already
   where the refusals live, so the ceiling follows it.
2. **F7**: approved. `conductor land` is the lead's hands after the lead has read the diff and two
   reviewers have covered it; it never runs inside a mission or from a fleet.
3. **F10**: the cross-vendor core-guard audit runs first, and conductor writes its own ask: an
   Opus 5 planner lane (E10, `plan: true`) reads the guard and its test in the harness repository
   and delivers the audit mission file, cap included; conductor dry-runs it and pauses; the lead
   reads the child and continues. The first planner receipt on real work.

## Recommended order

- **Group 0, lead only, no build:** a release helper under `scripts/` for the version bump,
  receipt stub, and roadmap row (27 repetitions on record); a $2 Opus read lane over
  `tests/test_cascade.py::test_a_resume_keeps_the_escalated_flag_on_the_kept_lane` and the
  resume path, fixed by hand or marked `xdist_group(name="serial")` with the reason (five
  salvages on record); the three probes for F12, F13, F14 on a scratch repository, receipts
  into `docs/research/`; the two verbatim Anthropic prompt blocks into the launcher's build
  prompt under E17's versioning; the Composer price-row note; the operator decisions above.
- **Group 1, two in parallel, snapshot-disjoint:** F1 verdicts (lane result), F3 read-lane
  gate (run receipt gate block, backfill). F2 waits: it touches the mission result and resume.
- **Group 2, series with the resume touch, then three in parallel:** F2 wall clock; then F5
  cursor grace, F6 launcher, F13 `denied_actions` (fleet output only).
- **Group 3:** F12 restricted read lanes and permission denials on the receipt (F14 dropped
  after its probe).
- **Group 4, if approved:** F7 `land`.
- **Group 5, receipts:** F8 fixtures, F9 shapes, F10 consumers, F11 unattended. F10 can start
  the same day as Group 1; nothing in it depends on a Phase F build.

Eight build items, build caps summing to $48, about $80 with review and fix lanes; the
receipt items about $40 to $60 depending on what the consumers spend. At the E10b pace (75
minutes launch to release) the builds are a day and a half; the receipts are another day.

## F-d: what the research pass adds

Items here come from the two read passes below, not from the receipts. Each starts with a
probe on a scratch repository (Group 0, under $0.50), because a flag's help text is a claim
and D3's `agy --agent` failed open against its own help.

**F12. Claude read lanes under `--restricted` and `--permission-prompts none`.** Claude Code
2.1.263 carries `--restricted` ("removes the built-in tools that run commands or code, and
WebFetch unless `--tools` names them; confines the file tools to the working directories;
refuses `bypassPermissions`") and `--permission-prompts none` ("anything that would prompt is
denied automatically"). Conductor's read lanes run in plan mode with D2's `--disallowedTools`
deny list when tainted; a positive confinement flag is the stronger shape (a deny list cannot
name a tool nobody listed), and the prompt policy removes the failure class where a lane stalls
on a permission nobody answers (the September fleet comparison: eight refused pytest attempts
then a question to nobody). F12: read lanes pass both flags; a tainted read lane's
`taint_enforcement` cites `--restricted` beside the deny list; `--include-hook-events` is passed
on tainted lanes and a deny that fired is recorded on the receipt as direct evidence rather than
inferred from the init event. `--restricted` refuses `bypassPermissions` (probed: exit 1, nothing spent) and runs only
under `acceptEdits`, so a restricted lane cannot run a gate and the flag is per lane
(`restricted: true`), for reviewers that read only (Gemini's role in Shape A, never Grok's);
`WebSearch` survives it, so it confines files and exec, not egress, and the deny list stays.
`--permission-prompts none` goes on every Claude lane: the probe shows denials land in
`result.permission_denials` as `{tool_name, tool_use_id, tool_input}` while the run still
exits 0 with `subtype: success`, so a non-empty list on a write lane fails it (kind
`denied`) and on a read lane is a note. `xhigh` is accepted by 2.1.263 on every model. Also the `xhigh` rung: Claude's effort ladder is `low, medium, high, xhigh, max` and
`_CLAUDE_EFFORT` maps conductor's four levels without ever emitting `xhigh`; the probe measures
whether it is worth a fifth level or a remap of `max`. Probe first: a read lane under both flags
asked to run the gate, write a file, and fetch a URL, with the init event's tool list captured.
Modules: `fleets.py`, `runner.py` (receipt fields), `outputs.py` (hook events). Depends on D2,
E21. Size 0.5. Build cap $6 (`4 items + $1 breadth + $1 summary`).

**F13. Antigravity hook verification through the free `/hooks` command. Rewritten after the
probe.** The 1.1.27 changelog's `denied_actions` field does not exist: fourteen runs found it
in no result, step, or log event, and a plan-mode refusal is legible only as an absent tool call
(`docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md`). What the probe found
instead: agy's read-only slash commands answer in print mode with `num_turns: 0` and zero
usage, and `-p "/hooks"` names every loaded hooks file with its source and enabled flag. F13:
before a tainted Antigravity dispatch, conductor runs `agy -p "/hooks"` in the lane's worktree
and requires its own hooks file to appear enabled, failing the run as `taint hooks not
enforced` before any paid turn (today the check reads the `--log-file` count after the run,
which is the same fact a turn later and a dollar poorer). Also from the probe: under `--mode
plan --sandbox` with `--json-schema`, agy took a second turn that wrote a file into the working
directory and ran a shell command, so a schema on an Antigravity read lane is refused at load
(the bytes check would fail the lane anyway; the refusal saves the spend). Modules:
`fleets.py`, `runner.py`. Depends on E21. Size 0.25. Build cap $4 (`2 items + $1 breadth + $1
summary`).

**F14. Cursor taint through `--sandbox enabled`: dropped after the probe.** The flag blocked
nothing on bytes: web fetch returned the page and the shell wrote outside the worktree under
`--sandbox enabled` exactly as under `disabled`. The `cli.json` deny list held on both shell
calls beside it and did not cover the fetch, which confirms E21's gap live. Taint on Cursor
stays refused; the probe is the record.

**A read lane's deliverable has never worked live on Claude.** The F10 planner mission
(2026-09-07, $2.18) was the first Claude read lane asked for an E1 deliverable outside the
test suite, and it could not write it: a read lane runs under `--permission-mode plan`, and
plan mode allows no write except the plan file, so Opus wrote the complete mission into its
plan file and answered "say the word and I'll write it". E1 and E10 were built and tested
against fake fleets that write whatever the test says. The fix belongs in F12: a Claude read
lane that declares a deliverable dispatches under `--restricted --permission-mode acceptEdits`
(the probe's only running combination: file tools confined to the working directory, no
exec, no fetch), and the bytes check still refuses anything beyond the declared path. A plan
lane on `antigravity` or `cursor` has the same question open and no probe yet. Until F12
lands, a planner mission on Claude is a $2 way to get a mission file into a plan file, and
the lead copies it out by hand, which is what happened.

Noted, not items: `--fallback-model` can move a lane to another model on overload, which would
break the review-vendor policy silently, so conductor should keep not passing it; `--fork-session`
is the right primitive if a second fix attempt ever needs the build's thread without mutating
it (rule 6 salvage), noted for the next time that happens; Claude 2.1.261 resumes a transcript
with a malformed id under a fresh id, which conductor's resume check already fails closed on
(`resumed.ok` false when the returned id differs), so a fix lane could fail for a reason that
did not exist last week, and the receipt would say so.

## Research pass, 2026-09-07

Two Opus 5 read passes, neither through conductor, no fleet spend.

### Installed CLIs against what conductor passes

| CLI | version | since 2026-09-05 |
|---|---|---|
| `claude` | 2.1.263 | 2.1.261 to 2.1.263; the changelog lists no 2.1.262 |
| `agy` | 1.1.27 | unchanged |
| `cursor-agent` | 2026.09.02-c22c1a3 | unchanged; no local changelog exists |

Claude flags conductor does not pass and what each could change: `--tools` and
`--allowedTools` (positive allowlist, scoped Bash grants), `--restricted` and
`--permission-prompts none` (F12), `--include-hook-events` (F12), `--no-session-persistence`
(one-shot read lanes only; conflicts with resume), `--session-id` and `--fork-session` (a resume
id known before spawn; a resume that does not mutate the thread), `--fallback-model` (refuse:
moves the vendor), `--input-format stream-json` (multi-turn without respawn), `--autocompact`
(bounds a long build's context), `--append-system-prompt` and its variants (note: passing any
of them turns `--system-prompt-snapshot` off unless `on` is passed explicitly, which conductor
already does). `--bg`, `--cloud`, `--worktree`, `--tmux`, `ultrareview` (a cloud-hosted
multi-agent review of the branch): all ship the checkout or the session somewhere else and stay
out under the local-only rule and the C6 and D4 decisions.

Claude 2.1.261 changes that touch a lane: `claude -p --resume` with a malformed id in the
transcript now resumes under a fresh id (conductor fails closed on it); a Stop sent just after
the first prompt now stops the turn (rule 8's SIGINT path); a resumed session no longer loses
hook output around parallel tool calls (a resumed fix lane's prefix is now correct where it was
not); idle CPU of `-p` sessions improved; `bashOutputMaxChars` raises inline tool output to
128K before spilling to a file, so a build lane's gate output reaches the model differently
depending on that setting. 2.1.263 is bug fixes only.

Antigravity: `--agent` stays refused (fails open, D3); `--project` is an isolation lever beside
`--add-dir` not yet used; `--input-format stream-json` exists; `agy models` could validate the
model allowlist without spend; `denied_actions` in the JSON result is new since the September
notes and unread (F13).

Cursor: `--sandbox <enabled|disabled>` (F14); `--model` accepts bracket overrides
(`'<model>[context=1m,effort=high,fast=false]'`), which bears on the 200K price-doubling trap
if `context=` applies to Grok; `--mode ask` is a second read mode beside `plan`; `--auto-review`
lets a server classifier approve tool calls (weaker than `--trust`, refuse); `create-chat`
returns a session id before dispatch; `status --format json` and `models` are spend-free
preflight; `-w --worktree` is Cursor's own isolation outside conductor's byte checks (refuse);
`worker` is a self-hosted cloud worker (barred, D4).

Contradictions with the September notes: AGENTS.md's "soft-denied and the run still exits 0"
for agy is now only half true (F13); the E21 probe's Cursor conclusion covers `cli.json` rules
and not `--sandbox` (F14); the effort ladder note (`xhigh` unreachable) is new. Everything else
the notes claim (`--bare` refuses OAuth, `--bg` refuses `--print`, no Cursor schema flag, agy's
`--json-schema` applies to the final result only) is confirmed by the current help text.

### Vendor changelogs, guides, pricing, and research since 2026-08-25

Each item is marked confirmed (the page was fetched and read) or snippet (search result only).

**Claude Code** (confirmed, `CHANGELOG.md` at head 2.1.263, undated; docs at
`code.claude.com/docs/en/cli-reference` and `/headless`): `--permission-prompts none` (2.1.259)
denies anything that would prompt, tells the model nobody can approve so it stops retrying, and
records each denial in `result.permission_denials`, so the soft-deny a fleet then lies about
becomes readable on the receipt (F12). `--restricted` (2.1.248) as described above.
`--max-budget-usd` counts subagent spend and stops running subagents at the cap (2.1.217 on),
which confirms rule 10's summary dollar. `--append-subagent-system-prompt-file` (2.1.261) lets
the no-quota template reach a reviewer's subagents. `--json-schema` with `--output-format json`
returns `structured_output` and an invalid schema now errors instead of returning text. The docs
call `--bare` "the recommended mode for scripted and SDK calls" and say it will become the
default for `-p` in a future release: reported, not proposed; it stays on the rejected list
because it refuses OAuth, which is how this machine is logged in, and a future release that
flips the default will need a receipt of its own.

**Antigravity** (confirmed, `antigravity.google/changelog` and the CLI's `CHANGELOG.md`):
Gemini 3.8 Flash is selectable headless since agy 1.1.25 (2026-09-03); 1.1.26 defaults
unselected model families to medium reasoning and stops subagents prompting under
always-proceed; 1.1.27 is the `denied_actions` change (F13) and lets custom agents declare
subagent dependencies; 1.1.25 makes custom Markdown agents inherit ambient skills, rules, and
subagents by default, so an agent file in a worktree is no longer hermetic. Snippet only:
read-only slash commands (`-p "/permissions"`, `/hooks`) may answer in print mode without
spending quota, which would let conductor verify hook loading without a turn; worth a probe
beside F13's. Nothing found that changes `.agents/hooks.json` deny semantics or the "loaded N
named hooks" log line, so E21's enforcement stands.

**Cursor** (confirmed, `cursor.com/docs/cli/changelog` and `/headless`): persistent sessions
shipped 2026-08-26 (`agent persist`, detach and reattach); the September probe found they need
tmux, which is not installed, and C6 stays shelved. Headless Max-mode model variants are sent
rather than clamped (2026-08-11), relevant to Grok's 200K cliff. Still no schema flag and no
cost or usage fields on the headless page, so costs stay post-hoc estimates. Snippet only: a
community report that granting a permission inside a project writes to the global config while
the project file takes precedence; no rule kind for the native web tools appeared in any doc.

**Prompting guidance** (confirmed): Anthropic's guide for Claude Fable 5.1 (2026-09-01) carries
two verbatim blocks that apply to the build lane on any Claude model: an autonomy block
("You are operating autonomously. The user is not watching in real time..." with a closing
"check your last paragraph; if it is a plan, do that work now") and a scope block ("keep changes
and tests to what the task asks for") that Anthropic measured as cutting unrequested fixes and
surplus test files with no drop in task success. The launcher's build prompt says the second by
hand; adopting both verbatim is a Group 0 prompt edit under E17's versioning, not a build item,
and the E17 fixture note will show the change. Also from that guide: effort names do not transfer
across models, so re-measure per model; `xhigh` and `max` can draft the deliverable twice
(thinking and reply), which is the case for `high` unless F12's probe says otherwise; base64 in
tool output can trigger a refusal (`stop_reason: refusal`), which touches the C7 leak guard's
territory if a lane ever reads a DSSE payload. Google's 3.8 Flash page (2026-09-03): `MINIMAL`
thinking errors on 3.8, medium is the recommendation for agentic code, temperature and top-p
now error. xAI still publishes no Grok prompt guide. The Opus 5 and Sonnet 5 guides are
unchanged since launch.

**Reviewer and judge research**: nothing published in the window changes the reviewer rules.
The earlier 2026 papers the pass surfaced (refute-or-promote stage gating, self-preference
mitigation, position bias in rubric judging, SWR-Bench's sub-10-percent precision for automated
review) are consistent with rules 1 through 8 and with F1's split of finding from disposition.

**Pricing** (confirmed on the three vendors' pricing pages):

| item | change | cap arithmetic |
|---|---|---|
| Sonnet 5 | $2 / $10 is permanent; the scheduled 2026-09-01 rise to $3 / $15 will not occur | rule 2 stands as written |
| Claude Fable 5.1 | $10 / $50, cache reads $0.25 per million (a 0.025x multiplier), 1M context | not a lane today; `prices.py` has no row, so a mission naming it is refused at load, which is correct until the operator wants it |
| Opus 5 | $5 / $25 unchanged; fast mode $10 / $50 | never inside a capped lane |
| `inference_geo: us` | 1.1x on every token category, folded into `--max-budget-usd` since 2.1.239 | a US-pinned lane needs ten percent more headroom; conductor pins nothing |
| Gemini 3.7 and 3.8 Flash | $0.75 / $3.75 through 2026-12-31, then $1.50 / $7.50; one shared end date, no reset for 3.8 | `prices.py` already says so |
| Grok 4.6 | $2 / $6, doubling for the whole request above 200K prompt tokens; Cursor's "fast" variant is $4 / $12 | rule 7's caps stand; which variant a lane gets is unverified |
| Composer 2.5 | standard $0.50 / $2.50; fast $3 / $15, and fast is Cursor's default | `prices.py` prices Composer at standard, so a Composer lane may be under-priced six times over; Composer runs on no shape today, and the row gets a note before it does |

Net: one price row to annotate (Composer), one to add when wanted (Fable 5.1), and the
Sonnet 5 non-increase, which is the single most useful fact in the pass for a roadmap sized in
Sonnet dollars.

## Receipt: how this document was produced

Two Opus 5 read passes on 2026-09-07, run as subagents of the lead session rather than through
conductor (no repository bytes, no vendor prompts, nothing to gate): one over the installed
CLIs' help and source against `fleets.py` and `outputs.py` (25 tool calls), one over the public
changelogs, guides, pricing pages, and arXiv (31 tool calls). Every figure in the receipts table
at the top is from `conductor report --since 2026-09-05` on the lead's machine the same day. The
Grok finding-rate row was checked on bytes: the answer files for E3 and E4 open with a sentence
of narration and carry `NO_FINDINGS` later, and `report._is_no_findings` compares the whole
stripped text.
