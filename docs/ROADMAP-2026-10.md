# conductor: Phase E, from self-building tool to general dispatcher

Written 2026-09-06, the day the September roadmap closed (`docs/ROADMAP-2026-09.md`:
A1 to D3 shipped through 0.26.0, C6 and D4 shelved). Nineteen items cost about $229 in
fleet spend; receipts in `docs/RESET-2026-09.md`. This roadmap widens conductor from
code work on its own repository to any work with a verdict.

How it was written: the lead drafted twenty-one items, then ran two Opus 5 read lanes
through conductor ($4.78, receipt below). One lane critiqued and extended the draft with
citations into the code; the other never saw the draft and sized, seamed, and ordered
the same item list from the code and the receipts alone. Where they disagreed with the
draft, the code won; where they disagreed with each other, the disagreement is noted.
The operator cuts.

Standing constraints carry over unchanged: local-only repository; first-party fleets
Claude, Antigravity, Cursor; Codex and every OpenAI lane paused; OpenCode, OpenRouter,
Ollama, pi, local models, C6 background lanes, and D4 cloud offload shelved and not to be
re-proposed. Every item ships through Shape A (Sonnet 5 builds, Gemini 3.7 Flash and
Grok 4.6 review cold, Sonnet fixes, the lead judges on bytes), one release per item, with
the cap rules in `AGENTS.md`.

## Shipped since this was written

| item | version | shape | cost |
|---|---|---|---|
| E5 Shape A launcher, E21 probe | in 0.27.0 | lead work, group 0 | under $0.10 |
| E11 ledger report | 0.27.0 | Shape A on 0.26.0 via `conductor shape a`, in parallel with E1; fix salvaged | $7.69 |
| E1 deliverable verdicts | 0.28.0 | Shape A on 0.26.0 via `conductor shape a`, in parallel with E11; merged after E11 | $11.94 |
| E22 vendor CLI versions | 0.29.0 | Shape A on 0.28.0, one of three in parallel; fix lane stopped, two README sentences tidied by the lead | $4.81 |
| E12 notifications | 0.30.0 | Shape A on 0.28.0, one of three in parallel; fix salvaged | $6.17 |
| E25 clean-gate diagnosis | 0.31.0 | Shape A on 0.28.0, one of three in parallel; clean run end to end | $5.45 |
| E26 per-lane cwd | 0.32.0 | Shape A on 0.31.0, in parallel with E23; both reviewers NO_FINDINGS, fix lane stopped, build tip landed by hand | $7.02 |
| E23 conductor salvage | 0.33.0 | Shape A on 0.31.0, in parallel with E26; merged after it; Grok's two findings fixed on the resumed thread | $9.40 |
| E24 cap grace | 0.34.0 | Shape A on 0.33.0, one of three in parallel; build capped at $7 with everything written, salvaged through `conductor salvage`; docs-only finding tidied by hand | $8.03 |
| E17 prompt versions | 0.35.0 | Shape A on 0.33.0, one of three in parallel; build capped at $7 with the code done, salvaged; both reviewers NO_FINDINGS; one lint line wrapped by hand | $8.42 |
| E21 taint on Antigravity | 0.36.0 | Shape A on 0.33.0, one of three in parallel; Grok's four findings all real; fix lane capped, finished by hand from its kept worktree | $10.46 |
| E16 adversarial test lanes | 0.37.0 | Shape A on 0.36.0, first of Group 5; build capped at $11 with the work done, salvaged; Grok's three findings all real | $13.32 |
| E7 human lanes | 0.38.0 | Shape A on 0.37.0, second of Group 5; build green inside the grace band; Grok's four findings all real, fix finished by hand from its kept worktree | $15.25 |
| E4 judge sittings | 0.39.0 | Shape A on 0.38.0, first of Group 6; build green under cap; both reviewers NO_FINDINGS, no fix paid | $9.07 |
| E6 script lanes | 0.40.0 | Shape A on 0.39.0, second of Group 6; build green under cap; Grok's three findings all real, fixed on the resumed thread | $16.32 |
| E3 untrusted-output lanes | 0.41.0 | Shape A on 0.40.0, first of Group 7, in parallel with E9; build green under cap; both reviewers NO_FINDINGS, no fix paid | $4.81 |
| E9 rolling spend ceiling | 0.42.0 | Shape A on 0.40.0, in parallel with E3, merged after it; Grok's one finding real, fixed by hand | $5.95 |
| E13 export bundles | 0.43.0 | Shape A on 0.42.0, third of Group 7; build stopped on a leak-guard flake, salvaged; Grok's two findings real, fixed on the follow-on | $5.58 |
| E14 cost forecast | 0.44.0 | Shape A on 0.43.0, fourth of Group 7; build green under cap; Grok's one finding wording, fixed by hand | $5.72 |
| E19 cross-repo collisions | 0.45.0 | Shape A on 0.44.0, fifth of Group 7; build green under cap and fixed the resolver cwd; Grok one real, one coverage, one wrong at confidence 10, fix lane sorted them | $10.45 |
| E10 planner lanes (first spec) | 0.46.0 | Shape A on 0.45.0, last of Group 7; build capped on the code, tests built separately, code reviewed on a follow-on, two real findings fixed on the merged tip | $27.53 |
| E10 planner lanes (second spec) | 0.47.0 | Shape A on 0.46.0 on the parallel gate; build salvaged after an unrelated key race tripped the gate; both reviewers found the same two receipt bugs, fixed on the follow-on | $13.60 |

## What "general" means here

Today a lane is judged one of two ways: a write lane on the bytes it moved and the gate
they passed; a read lane on its reply, optionally against a schema. That is enough for
code and for review. It is not enough for a research lane that produces a document, a
judge sitting that produces a tally, or a script that produces nothing but an exit code.
Phase E adds verdicts for those, adds ways for missions to be created and consumed by
things other than the lead session's hands, and turns the Shape A rules from prose into
functions of the ledger where the receipts support it, and leaves them as prose where
they do not.

## Sizing and the two rules that shape the order

One item is one spec, one mission, one release: roughly $8 to $14 in fleet spend and 50
to 100 minutes of lead time including judging. The build cap per item below is computed
by AGENTS.md rules 2 and 10: a dollar per spec item, plus $2 if it touches the scheduler,
the runner's wait loop, or resume ("scheduler tax"), plus a dollar per source module past
the second, plus a dollar for Claude's own summary. Review and fix lanes add $3 to $4 per
mission on top.

Two rules from the September receipts govern parallelism:

- Three missions on one checkout works (B2, B3, C4). Two missions that both grow the
  mission snapshot collide in the golden fixtures (D1, D2). Parallelize on snapshot
  disjointness, not file disjointness.
- Anything with the scheduler tax ships alone.

Fixture discipline for every item that grows the snapshot or a receipt: extend
`golden._backfill_snapshot`, never edit recorded bytes, and carry `test_policy: allow`
with the lead reading every existing-test edit (rule 3).

## E-a: verdicts for work that is not code

**E1. Deliverable verdicts.** A lane may declare `deliverable: {path, schema?}`. The
verdict: the file exists in the lane's worktree after the run, parses, matches the schema
when one is given, and the lane touched nothing else. The "nothing else" half is shipped
(`Result.failure()` already sinks a read lane that moved bytes and a write lane is judged
on a content hash); the new part is declaring the path, asserting existence, validating,
and making absence its own error kind. Two traps from the record: the deliverable is an
untracked file in a worktree that inherits the operator's global excludes (C7's `*.log`
transcripts), and a read lane with a deliverable must be distinguished from a read lane
that ignored read-only, or every review lane with an output file reads as a violation.
Depends on nothing. Size 0.5. Build cap $8.

**E3. Untrusted-output lanes.** The draft's fetch-allowed tainted lane was dropped on
2026-09-06 (operator decision): a tainted lane's deny list exists to stop the outside text
in its prompt from exfiltrating the repository or pulling a second stage, and an untainted
lane already has fetch and search. The hole that remains is the other direction: a research
lane that reads the web produces untrusted text, and a build lane that pastes its answer is
not tainted today. E3 is now: a lane may declare `untrusted_output: true`; it runs with its
full tool set, and any lane whose template references its answer or diff becomes tainted
(the D2 propagation walk, one more source). No tool weakening. Depends on D2. Size 0.5.
Build cap $5.

**E4. Judge sittings.** N candidates times M judges over the existing rank collate
(`Collate.rank` already dispatches both orders; `candidates` narrows; A3 hygiene already
refuses self-vendor scoring), plus a tally table exported as markdown and JSON with
agreement and disagreement marked. The draft's `swap` key duplicates `rank` and is
dropped. Rubric lanes (the draft's E2) merge in here: `Criterion` and `checklist_schema`
already give a per-criterion yes/no rubric with judge hygiene, and the only real gap is a
score, which pays off only with a tally. Missing today: M judges (collate is one object
with one fleet, and it runs after the scheduler loop closes, single-threaded, so M in
parallel needs its own fan-out). Scheduler tax. Turning `collate` into a list changes the
snapshot shape: backfill, never re-record. Depends on E1 for scored deliverables. Size 1.
Build cap $9.

## E-b: missions from somewhere other than the lead's hands

**E5. Shape A template and one-line launcher.** `conductor shape a --spec <path> --repo
<path> --items N --modules M [--scheduler]` writes a mission from the versioned template
(the one AGENTS.md describes), computes the caps by rules 2 and 10, dry-runs it, and
prints the arithmetic, not just the number (`5 items + $2 scheduler + $2 breadth + $1
summary = $10`), because B2's $2 shortfall was a module undercount and a launcher that
prints one number reproduces it. Spec items and modules are hand-counted flags; conductor
has no notion of either and must not grow a forecaster here (that is E14). Lead work, no
fleet spend. Cap mis-sizing has cost money five times on record (A3, B5, B2, C5, D1).
First, because every later mission is written with it. Also carries the README sentence
that read lanes already run over non-git directories (the draft's E20, read half; the
write half is dropped, see below). Depends on nothing. Size 0.5. Cap $0.

**E6. Script lanes.** `fleet: script` runs a subprocess in the lane's worktree with the
same receipt, timeout, bytes verdict, and stage semantics as a model lane. Setup and
teardown already run a shell command under a timeout and are receipted, so the delta is
stage semantics, graph-node ordering, and a verdict. The hard part: "zero cost" is not a
state conductor can express. A run with no cost figure settles as `unpriced`,
`Result.failure()` fails that closed ("cap unenforced"), and `cap_usd` is inherited from
the mission, so any mission with a top-level cap would fail every script lane in it. The
spec must design a third ledger state, priced at zero and verified, or refuse `cap_usd`
on a script lane at load. The breakers and the stall, loop, and tool-call ceilings in the
wait loop have no stream to read and must be disabled coherently. Scheduler tax
(wait loop). `test_policy: allow`. Depends on nothing. Size 1. Build cap $11.

**E7. Human lanes.** `fleet: human`: the mission writes the ask to the receipt, pauses
(C2), and resumes when the operator answers with `--answer <text>` or drops a file at the
deliverable path; the answer becomes `{{lanes.<name>.answer}}` for downstream lanes. Two
requirements from the review: the answer is operator-pasted text going into a prompt, so
it is tainted by construction, or D2's invariant breaks silently; and a lane with no
dispatch receipt is a new pause kind that resume's trust and salvage paths must accept.
This is also where the draft's E8 (a lane asks a question, conductor pauses) lands in
reduced form: operator-initiated, mission-declared, content-answered. E8 as drafted is
dropped; see below. Scheduler tax and resume. Depends on E1 and C2. Size 1. Build cap $8.

**E9. Rolling spend ceiling.** The launchd half of the draft is out: the launchd fleet is
a separately governed system and a plist with home paths cannot be committed here. What
stays: a per-day and per-hour spend ceiling read from the receipts that refuses to start a
mission over it, and a mission-file-scoped lock (the running lock claims one mission
directory, and every launch mints a new id, so two overlapping firings of the same file
both run today). Check B4's existing fleet-wide hourly and daily cap before building;
it may already be most of this. The real risk of unattended runs is not money but
salvage: five of fourteen Shape A runs needed a lead salvage and a cap-cut build leaves a
kept worktree that `gc` protects forever. Decision 2026-09-06: any read-only mission may
run unattended at any hour; a write mission only when it pauses before its fix stage, so
nothing lands without a lead reading it; salvage is never automated. Ceiling defaults $10
per hour and $25 per day, overridable per mission. Depends on E11. Size 1. Build cap $6.

**E10. Planner lanes.** A read lane whose deliverable is a mission file (E1 with the
mission schema). Conductor dry-runs it, refuses it over the parent's remaining budget or a
depth limit, pauses before launching it (C2), and links parent and child by id. The first
place conductor spends on the operator's behalf without a human-written mission, so
`pause.before: [child]` is mandatory until lifted. Two specs: child launch with dry-run
and depth limit first; budget rollup and resume second. The running lock and the receipt
chain both assume one mission per directory; a child either shares the parent's ledger
(and concurrent lanes already each receive the same remainder, so the child's cap is
soft) or has its own (and the moat is gone for the child). Resume on a parent whose child
was mid-run has no defined state. The item most likely to be right in design and wrong in
the receipt. Scheduler tax and resume. Depends on E1, E5, E7 with live receipts, E11.
Size 2. Build cap $7 twice. Last.

## E-c: seeing what happened and using it

**E11. Ledger report.** `conductor report [--since]` over every receipt: cost per vendor
per stage, cap-miss rate, mean lane duration, spend per release, reviewer finding rate.
Cap-miss rate and duration are free today (`kind` and `duration_s` are on every receipt).
`stage` is in the signed attestation statement but not on `Result`, so per-stage cost is
a join through the mission snapshot; the spec adds `stage` to `Result` and makes it a
scan. Salvage rate has no seam and no data (a lead salvage is a hand commit with no
receipt) until E23 lands and writes one. The likely failure is the join missing runs:
A3's receipt records a ranking collate's two priced order runs invisible to `spend --by
mission`, and `_collate_run_ids` exists in two modules that can drift. Depends on
nothing; E9, E14, E23 want it. Size 1. Build cap $7.

**E12. Notifications.** On pause, mission end, and breaker trip: one line to
notification-hub and one ledger line to bridge-db, configured, optional, fire-and-forget,
with a failed notification recorded as a note and never as a verdict, the shape teardown
already uses. The hook sits at the settle boundary, not inside the wait loop, or it buys
the scheduler tax and a new way to stall a poll. No notification code exists under
`src/conductor` today. Depends on nothing. Size 0.5. Build cap $5.

**E13. Receipt export bundle.** `conductor export <mission>` writes receipts,
attestations, and diffs through the C7 scrubber. Standalone verification is dropped:
attestation is HMAC-SHA256 under a shared secret because stdlib Python has no vetted
asymmetric primitive, so a bundle a third party can verify is a bundle that ships the key
that forges everything. The bundle verifies file digests and the receipt chain only, and
says so. C7 found real paths inside base64 DSSE payloads; the export walks that path with
more files. Depends on A5, C7. Size 1. Build cap $6.

**E14. Cost forecast and warn.** Before dispatch, estimate spend from the E5 inputs and
the E11 history for that fleet and stage, and warn when a cap is under the estimate.
Never refuse: fourteen rows spread from $5 to $20 for comparable items, and a load-time
veto derived from a fleet's past spending is the category of evidence AGENTS.md says not
to trust. Depends on E11. Size 1. Build cap $5.

**E22. Vendor CLI version on the receipt, with drift reporting.** Capture `claude`,
`agy`, and `cursor-agent` versions once per mission, timeout-bounded, never failing a
mission on a slow `--version`; record on each receipt and in the signed statement; show
in `conductor fleets`; make `golden check` report a fixture recorded under a different
version. Every assertion conductor makes about vendor behavior (D3's init-event
agent check, D2's Claude-only deny list, the cursor stream parser, agy's status-not-exit
rule) is pinned to nothing today; `cmd_fleets` records `installed` and `path` only.
Depends on nothing. Size 0.5. Build cap $5.

**E23. `conductor salvage`.** `conductor salvage <mission> --lane <name>` runs A1's clean
gate against the lane's kept worktree, prints the diff and the gate result, and emits the
follow-on review-and-fix mission with `cwd` at the salvage commit and the diff templated
in. It stops short of committing: if it landed bytes, conductor would be shipping unread
work. Salvage was the path for four of nineteen September items and the September
roadmap names the lead's salvage time as the cost that grew. This also gives E11 its
salvage receipt. Depends on E5, E26. Size 1. Build cap $7.

**E24. Cap grace for the terminal message. Approved 2026-09-06.** A bounded
`cap_grace_usd` that lets a Claude dispatch's final summary turn finish past the cap,
receipted separately as `budget.grace_used`, refused above a ceiling, stated per lane and
never inherited silently. This is rule 10 as a function: a green build was lost for five
cents twice in one day and a green fix for seven cents on D1. It is also a hole in the
one guarantee the September roadmap calls conductor's moat, spend capped from live vendor
usage. Decision: per lane, opt-in, default $0.25 in the Shape A template, hard ceiling
$0.50, receipted as `budget.grace_used`. Rule 10's flat dollar stays on the cap until the
ledger shows the band absorbs every terminal-message overrun. Depends on nothing
(`BudgetState.settle` and `over_cap`). Size 0.5. Build cap $5.

**E25. Clean-gate diagnosis for the test-surface trap.** When the clean gate fails and
the lane's own diff touched the test surface, emit a distinct error kind and a report line
naming those files, instead of an undifferentiated gate failure. The data is already on
the receipt (`digest_before`, `digest_after`, `touched`). This is rule 3 as a function:
"both builders were right and both were rejected" on A3, and C7's fix salvaged for the
same reason. Keep it to "the diff touched N test-surface files and the clean gate
failed", never per-test attribution. Depends on A1, C5. Size 0.5. Build cap $5.

## E-d: quality of the builds themselves

**E16. Adversarial test lanes.** A fourth stage whose deliverable is a test file that
fails on the build lane's tip. The A4 reproduce gate already transplants a test-surface
change onto a base and demands it fail there; this points the transplant at another
lane's tip instead of the base. A failing test found here goes to the fix lane with the
reviews. Two constraints: the target tip may not exist when the build was cap-cut (a
third of recorded runs), so the stage is unusable exactly when most wanted until E23
gives salvaged builds a commit; and a lane whose deliverable is a test trips the clean
gate by construction, so `test_policy: allow` is not optional. Lead call at spec time
whether to merge with E1 as one spec. Depends on E1. Size 1. Build cap $6.

**E17. Prompt versioning through golden replay.** Mostly shipped: `golden.replay`
already re-renders each attempt's prompt and diffs it against the recorded one, and
`prompt_file`, `agent_file`, and `prefix_file` already let prompts live in versioned
files. The remainder: a version id on each prompt constant (collate, resolve, rank
contract, checklist contract), rendered prompts in the projection so a prompt edit is a
fixture diff, and a `prompts/` convention for the Shape A template. Grow the projection
through backfill, never by re-recording (that put C7's fix through a red clean gate).
Depends on C7, E5. Size 0.5. Build cap $5.

## E-e: scope of a mission

**E26. Per-lane `cwd`.** The draft claimed a lane's `cwd` may already differ per lane. It
may not: `cwd` is a mission key, absent from the inherited keys, and the module docstring
says "one prompt, one cwd". This is the precondition for adopting conductor on a second
repository at all, for E23's emitted follow-on mission, and for E19. `mission.cwd` is
load-bearing in the scheduler, the branch checks, and the collate's own dispatch; a
half-migration means a lane gating the wrong repo. `gc` is already multi-repo (it groups
worktrees by the repo on each receipt). Split out of E19 so it lands early and E19's
remainder stays size 1. Depends on nothing. Size 1. Build cap $8.

**E21. Tool deny lists on Antigravity and Cursor.** Probed 2026-09-06
(`docs/research/2026-09-06-live-probe-tool-deny-non-claude.md`); the expected answer was
no and the answer is yes on both. Cursor loads `<cwd>/.cursor/cli.json` with
`permissions.deny` rules (`Shell(...)`, `Write(...)`, `Mcp(...)`) and held them on bytes
under `--force`; Antigravity loads `<cwd>/.agents/hooks.json` and a `PreToolUse` command
hook answering `deny` blocked `write_to_file` and `run_command` on bytes. Both fail open on
bad input (Cursor ignores an unknown rule kind silently; agy logs "loaded 0 named hooks" on a
malformed file or a wildcard matcher and continues), so conductor validates what it writes,
generates agy matchers from the init event's tool list, and asserts the log count. The
file is written into the worktree before the bytes baseline. This lifts D2's Claude-only
refusal for taint on both fleets; D3 personas stay Claude-only. Depends on D2. Size 1.
Build cap $7.

**E19. Cross-repo collisions. Semantics decided 2026-09-06.** With E26 in,
collisions across repositories. Today `collisions.touched_files` keys on repo-relative
paths with no repo qualifier, so two lanes touching `src/foo.py` in two different
repositories would report a hotspot and feed the D1 resolver, which writes, into
resolving a conflict that does not exist. Decision: a collision is the same path in the
same repository; each repository runs its own gate; the resolver never merges across
repositories. Cross-repo missions are fully allowed under that; this item and E26 are what
make them possible. A second repo is an absolute path in a mission file, so the golden
suite cannot cover it without a placeholder. Resume touches (per-repo branch
reclamation). Depends on E26, D1. Size 1. Build cap $7.

## Dropped or narrowed, with reasons

| draft item | call | why |
|---|---|---|
| E2 rubric lanes | merged into E4 | `Criterion`, `checklist_schema`, and judge hygiene already give a rubric; the gap is a score, which only pays off with a tally |
| E8 interactive lanes | dropped | Contradicts the C2 decision on record: pause points are mission data, "never a request a fleet's output can make". It is a parser for prose. E7 delivers the useful half |
| E9 launchd scheduling | dropped; ceiling kept | launchd is a separately governed system; a plist with home paths cannot be committed here |
| E13 standalone verification | dropped; export kept | HMAC under a shared secret: verifying elsewhere means shipping a key that forges everything, and stdlib-only forbids the asymmetric fix |
| E14 refusal | narrowed to warn | fourteen noisy rows; a load-time veto from a fleet's spending history is not evidence |
| E15 shape picker | dropped | no seam, one dominant shape, n=14: it would be a constant function. AGENTS.md is the picker, written by hand from the same data |
| E18 lessons file | dropped | the read half is `prefix_file`, shipped; the write half changes the prefix every mission and destroys the B2 cache it rides on (98.7 percent hits on C5), and injects unaudited framing into reviewer prompts |
| E20 non-git directories | read half is one README line; write half dropped | read lanes already run there; a non-git write lane has no worktree, commit, clean gate, reproduce gate, or attestation bounds, and would trade away the bytes-moved verdict |

## Operator decisions, settled 2026-09-06

1. **E3**: a tainted lane never keeps ingress tools. The fetch-allowed item is dropped and
   replaced by untrusted-output lanes (output taints downstream, no tool weakening).
2. **E24**: a grace band is acceptable: opt-in per lane, default $0.25, ceiling $0.50,
   receipted separately.
3. **E19**: collisions are per repository, one gate per repository, the resolver never
   crosses repositories.
4. **E9**: read-only missions may run unattended at any hour; write missions only with a
   pause before the fix stage; salvage never automated; ceiling $10 per hour and $25 per
   day by default.

## Recommended order

Groups are parallel where named; everything with the scheduler tax runs alone.

- **Group 0, lead only, no fleet spend:** E5 launcher; E21 probe. Both done 2026-09-06;
  the probe came back positive and E21 is now a build item in Group 4.
- **Group 1, two in parallel, snapshot-disjoint:** E1 deliverables; E11 ledger report
  with `stage` on `Result`.
- **Group 2, three in parallel, small and disjoint:** E12 notifications; E22 vendor
  versions; E25 clean-gate diagnosis. Shipped 2026-09-06 as 0.29.0 to 0.31.0, $16.43.
- **Group 3, two in parallel:** E23 salvage; E26 per-lane cwd. These discount every
  mission after them.
- **Group 4, three in parallel:** E17 prompt versioning (nothing else that touches
  fixtures runs beside it); E24 cap grace; E21 deny lists on the two fleets.
- **Group 5, series, scheduler tax:** E16 adversarial tests; E7 human lanes.
- **Group 6, series, scheduler tax:** E4 judge sittings; E6 script lanes.
- **Group 7, series:** E3 untrusted-output lanes; E9 ceiling; E13 export; E14 warn; E19
  cross-repo; E10 planner, two specs, last.

Roughly 20 releases and 21 missions. Build caps sum to about $157; with review and fix
lanes, $220 to $240 of fleet spend, three to four weeks at the September pace. Groups 0
through 4 are about $85 across eleven missions and roughly two working days.

## First consumers, carried over

The core-guard cross-vendor audit needs nothing from this roadmap and can run today.
OPERANT-J sitting 2 wants E4. Anti-slop passes want E1 and E3. The
HarnessBench live tier wants E6 and E26.

## Receipt: how this document was produced

Mission `e0-roadmap-opus`, 2026-09-06, two Claude Opus 5 read lanes at `hard`, $5.00 cap
each, both green, $4.78 total (extend $2.24, 34 tool calls; order $2.53, 21 tool calls).
One operating note for the record: the draft was uncommitted when the mission launched,
so the extend lane found it in the main checkout rather than in its worktree, and said
so. A read lane that must see a document gets it committed or passed through `include`.
