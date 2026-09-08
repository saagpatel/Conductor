# Closed roadmaps

Phases D, E, and F are closed. Each file's full content is below, unedited. The current phase is [`docs/ROADMAP-2026-12.md`](../ROADMAP-2026-12.md) (Phase G).

| phase | original file | written | notes |
|---|---|---|---|
| D (A1–D3) | [`ROADMAP-2026-09.md`](#phase-d-where-it-stands-and-where-it-goes) | 2026-09-03 | closed 2026-09-06 |
| E | [`ROADMAP-2026-10.md`](#phase-e-from-self-building-tool-to-general-dispatcher) | 2026-09-06 | closed 2026-09-07 |
| F | [`ROADMAP-2026-11.md`](#phase-f-from-a-dispatcher-that-builds-itself-to-one-the-lead-can-measure) | 2026-09-07 | closed 2026-09-07 |

# Phase D: where it stands and where it goes

Original file: `docs/ROADMAP-2026-09.md`. Written 2026-09-03, covers items A1–D3; closed 2026-09-06.

# conductor: where it stands and where it goes

Written 2026-09-03 after a four-vendor audit of v0.6.1 (Opus, Sol, Gemini, Grok
reading the same code, Sol collating; $8.56), the closure of that audit's
findings through conductor's own build → review → fix pipeline ($15.88,
232 tests), and four parallel research sweeps of how the field builds
orchestrators. Sources are linked where a claim rests on one.

## Shipped since this was written

| item | version | shape | cost |
|---|---|---|---|
| A1 clean re-run of the gate | 0.8.0 | Sol build, Opus review, Sol fix | $13.40 |
| A2 structured verdicts and quorum | 0.9.0 | same | $15.45 |
| B1 thread reuse | 0.10.0 | same | $14.53 |
| B4 rate and progress breakers | 0.11.0 | same | $19.32 |
| C1 checkpoint and resume | 0.12.0 | same | $19.40 |
| C3 liveness | 0.13.0 | Shape A (Sonnet build, Gemini + Grok review, Sonnet fix) | $5.05 |
| A3 judge hygiene | 0.14.0 | Shape A | $19.65 ($8.90 for the work; the rest is operating error, receipted) |
| A4 reviewer direction and reproduce-before-fix | 0.15.0 | Shape A | $8.03 |
| B5 early cancel and best-of-n | 0.16.0 | Shape A | $9.49 |
| A5 signed lane receipts | 0.17.0 | Shape A, in parallel with C2 | $8.92 |
| C2 pause primitive | 0.18.0 | Shape A, in parallel with A5 | $8.24 |
| B2 cache-friendly prompts | 0.19.0 | Shape A, one of three in parallel, salvaged | $7.13 |
| B3 cheap-first cascade | 0.20.0 | Shape A, one of three in parallel | $7.79 |
| C4 per-lane setup, teardown, ports, includes | 0.21.0 | Shape A, one of three in parallel, review rerun | $11.88 |
| C5 structured error kinds | 0.22.0 | Shape A on 0.21.0 with cascade, prefix, ports live; salvaged | $14.78 |
| C7 golden-mission suite | 0.23.0 | Shape A on 0.22.0, pause fired live; fix salvaged | $12.21 |
| D2 taint tracking | 0.24.0 | Shape A on 0.23.0, in parallel with D1 | $13.17 |
| D1 conflict-aware collate | 0.25.0 | Shape A on 0.23.0, in parallel with D2; fix salvaged | $11.17 |
| D3 inline agent definitions | 0.26.0 | Probe, then Shape A on 0.25.0; fix as its own mission | $9.72 |

The next phase is `docs/ROADMAP-2026-10.md` (Phase E, drafted 2026-09-06). Receipts and the operating rules they produced: `docs/RESET-2026-09.md` and `AGENTS.md`
("Shape A"). A5 and C2 ran as two missions in parallel against one checkout and merged in that
order; the second merge was the lead's. B2, B3, and C4 ran as three missions at once and merged in that order. C5 ran on 0.21.0 with the
new features engaged. C6 was probed live and shelved (`docs/research/2026-09-05-live-probe-background-lanes.md`:
`claude --bg` is an interactive session with no stream, `cursor-agent persist` needs tmux, agy has no
background mode). C7 shipped on 0.22.0 as one mission plus a salvaged fix. D1 and D2 ran as two missions at once on
0.23.0 and merged in the order D2, D1. D3 was probed and built for the Claude fleet only. **D4 shelved 2026-09-06, operator decision:** every
cloud mode ships the checkout to a vendor, which the local-only rule exists to prevent, and the
receipts show review is not the bottleneck. Reopen only for a repository that already lives on a remote.

## What conductor is, in one paragraph

One dispatch contract over four coding-agent fleets (Claude Code, Codex,
Antigravity, Cursor) with first-party models only. A run is judged on the bytes
it moved and the gate it passed, never on its exit code or its prose. Every
write runs in its own worktree; conductor owns the commit. Every dispatch has a
dollar cap enforced from the fleet's live usage, and a mission has a total
budget whose remainder caps each dispatch it starts. Missions fan out, fall
back, collate, and now chain: build on one vendor, review cold on another, fix
on the first, with lineage by commit SHA. A stop signal ends everything cleanly
and priced. `gc` and `spend` keep the house in order.

## What the audit closed (v0.7.0)

Fourteen findings, five of them P1, confirmed on the code and fixed:

- `gc` can no longer touch a run in progress (a fresh worktree is clean until
  the fleet writes; it looked deletable).
- A second Ctrl-C kills every live process group synchronously and exits 130 or
  143; `conductor verify --test` cleans up too. The registry lock is reentrant
  so the handler cannot deadlock the thread it interrupts.
- Write lanes must isolate; a non-isolated `--commit` on a dirty checkout is
  refused before spawn (it would have swept the operator's own edits).
- A dispatch that never spawned no longer counts as unpriced spend and cannot
  block a mission's remaining lanes.
- An exceeded or unverifiable ledger sinks the mission verdict.
- The bytes verdict hashes content (status plus every dirty and untracked
  file), not counts: editing an already-dirty file is no longer a no-op.
- Codex and Cursor streams without their terminal event fail closed, as
  Antigravity's already did.
- A fleet's self-commit faces the gate and is reset if the gate fails; only a
  same-branch descendant commit counts, so a branch switch is never mistaken
  for a commit (Opus caught the first version of this fix destroying a branch).
- Costs must be finite, non-negative numbers; unpriced attempts are rendered as
  such, never as $0.
- The mission prompt is trusted and unfenced; pasted lane output carries a
  per-render nonce in its fence so upstream text cannot forge the closing
  marker.
- Quoted and non-UTF-8 untracked filenames survive into `diff.patch`.

Kept as documented limits: concurrent lanes can each receive the same remainder
(a reservation would serialize lanes); a run killed by a second signal leaves a
worktree that `gc` protects forever until removed by hand.

## What conductor already does better than the field

The frameworks sweep (LangGraph, CrewAI, Microsoft Agent Framework, OpenAI
Agents SDK, Claude Agent SDK, Google ADK, and the coding-fleet projects) found
nothing that caps **spend from live vendor usage**; they cap steps, and one step
can cost anything. Nothing judges on **bytes moved**; they trust return values.
Those two are conductor's moat. Keep them at the center of every addition.

## The expansion plan

Ordered by what removes the most risk or cost per unit of work. Each item names
the failure it removes and the evidence.

### Phase A: trust the verdict

**A1. Clean re-run of the gate.** After a lane reports green, re-apply only its
source diff onto a pristine checkout at the base commit, restore every test
file and CI config from the base by hash, and run the gate again; only that run
counts. Removes: agents passing their own gate by editing it. Evidence: 63% of
one top model's SWE-bench Pro "solutions" retrieved the fix rather than derived
it ([Cursor](https://cursor.com/blog/reward-hacking-coding-benchmarks)); a
ten-line conftest resolves all 500 SWE-bench Verified instances
([BenchJack](https://arxiv.org/pdf/2605.12673)); agents modify tests and add
mocks far more than humans across 1.2M commits
([Building to the Test](https://arxiv.org/pdf/2606.28430)). Cost: gate
wall-clock doubles. Design: a `test_surface` hash-pin per dispatch, a
`test_touched` flag on the receipt, and a per-stage allowlist so a
test-writing lane is not punished.

**A2. Structured verdicts and heterogeneous voting.** Review and collate lanes
answer a fixed checklist: one yes/no per criterion, each citing a diff hunk,
validated against a schema before templating. Missions get `require: n-of-m`
over those verdicts. Removes: free-text judgments that cannot be tallied and
silent upstream garbage poisoning a downstream prompt. Evidence: at matched
budget, majority voting beats debate and heterogeneity is the lever
([Debate or Vote](https://arxiv.org/html/2508.17536v1)); per-criterion rubrics
grade near human level ([Rubric Is All You
Need](https://dl.acm.org/doi/10.1145/3702652.3744220)); typed edges between
stages reject mismatched messages ([Microsoft Agent
Framework](https://learn.microsoft.com/en-us/agent-framework/migration-guide/from-autogen/)).

**A3. Judge hygiene.** A judge never scores its own vendor's lane; candidates
are presented in shuffled order and the winning pair is re-scored swapped, and
disagreement escalates instead of picking. Quorum caps at three with a dissent
slot. Evidence: self-preference bias
([arXiv 2410.21819](https://arxiv.org/abs/2410.21819)), systematic position
bias ([arXiv 2406.07791](https://arxiv.org/abs/2406.07791)), three agents with
structured disagreement beating five (ICML 2026 workshop).

**A4. Reviewer direction is a policy, not a free choice.** Encode per-stage
allowed reviewer vendors, and require a fix lane to reproduce a reviewer's
finding as a failing check before it may edit. Evidence: Claude reviewing
Codex lifted pass rate 71.6% → 89.7%; Codex reviewing Claude dropped it
91.4% → 82.8% ([arXiv 2607.21656](https://arxiv.org/abs/2607.21656)). Our
own runs match: Opus reviewing Sol found real defects both times.

**A5. Signed lane receipts.** One DSSE envelope per lane over base commit,
source-diff digest, test-surface digests before and after, gate command line
and exit code, hash-chained across the mission. Removes: "the fleet did X" as
a claim rather than evidence. Fits the existing CheckSeal and Verification
Ledger work. Evidence: Bernstein's signed replay receipts, the IETF signed
action receipts draft
([draft-marques-asqav-compliance-receipts](https://datatracker.ietf.org/doc/html/draft-marques-asqav-compliance-receipts-08)).

### Phase B: spend less for the same result

**B1. Thread reuse across pipeline stages.** All four CLIs support it
(`claude --resume`/`--session-id`, `codex exec resume`, `agy --conversation`,
`cursor-agent --resume`). A build → review → fix pipeline pays full repo
context three times today; with a thread per lane, stages two and three are
cache reads (Opus input $5.00 → $0.50 per million; Terra $2.00 → $0.20).
Expect 40 to 70% off input spend on staged pipelines. Guard: assert the
returned session id on every resume (Codex 0.125 silently starts a new session
when the saved thread is missing).

**B2. Cache-friendly prompts.** Pass `--system-prompt-snapshot on` and
`--exclude-dynamic-system-prompt-sections` to Claude lanes; build every prompt
as static repo context and mission spec first, lane-specific text last, and
keep stages inside the cache window. Evidence: moving dynamic content after the
static prefix took one production hit rate from 7% to 84% for a 59 to 70% cost
cut ([Don't Break the Cache](https://arxiv.org/pdf/2601.06007)). We already saw
the reverse: operator hooks injecting per-lane context made a $0.05 reply cost
$0.24.

**B3. Cheap-first cascade.** Run one cheap lane (Luna, Haiku) first and fan out
to the full fleet only when its gate fails; log the escalation rate beside the
cap. Evidence: 31% cost cut at 0.91 micro-F1 ([UCCI](https://arxiv.org/pdf/2605.18796));
our own cheap lanes found real defects at $0.03. Risk: a fixed ladder can be
worse than routing on some code tasks ([Is Escalation Worth
It](https://arxiv.org/pdf/2605.06350)), so keep it a mission option, not a
default.

**B4. Rate and progress breakers.** Beside the absolute caps: a spend-rate
breaker (dollars per minute per mission), a no-progress detector (tool-call
count climbing while the diff hash is unchanged), a fleet-wide rolling hourly
and daily cap, and an iteration ceiling on any loop. Evidence: a four-agent
pipeline with no ceiling burned $47,000 over 11 days; a tokens-per-minute
breaker caught the same shape in 60 seconds
([source](https://www.getreadyforagents.com/blog/agent-cost-runaway-detection-token-enforcement-production/));
Ralph loops overbaking as OWASP ASI08
([LinearB](https://linearb.io/blog/ralph-loop-agentic-engineering-geoffrey-huntley)).

**B5. Early cancel and best-of-n.** Under `require: any`, kill the remaining
lanes the moment one sink passes its gate; rank survivors mechanically on
bytes and gate results and spend judge tokens only on the top two
([Generative Verifiers](https://arxiv.org/abs/2408.15240)).

### Phase C: run unattended for real

**C1. Checkpoint and resume at lane granularity.** A mission that dies at
collate after four paid lanes resumes at collate. Every dispatch gets an
idempotency key bound to its worktree commit so a resumed run never re-spends
or re-commits. Evidence: Microsoft checkpoints every superstep
([docs](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints));
LangGraph's rule that post-checkpoint nodes re-execute on resume
([durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)).
Per-lane receipts already exist; this is the reader for them.

**C2. A pause primitive.** A lane that wants to push, publish, or spend past a
threshold parks the mission with its state on disk and resumes on the
operator's answer. The operator merges by standing rule; this gives that rule
a mechanism instead of a failure. Evidence: LangGraph `interrupt()`, Microsoft
request/response events.

**C3. A liveness reconciler.** The watcher sees spend, not progress; a hung CLI
reads as "still working" until its timeout. Track last-output age and
tool-call cadence per run and kill or flag on silence
([baton](https://github.com/andyrewlee/awesome-agent-orchestrators)).

**C4. Per-lane setup, teardown, and port allocation.** A worktree isolates
files, not ports, sockets, scratch databases, or gitignored config, so two
lanes running an integration suite collide. Claude Code answers with
`.worktreeinclude`, Cursor with `.cursor/worktrees.json`, Emdash with injected
port variables. Conductor should own a per-lane setup script and a port range.

**C5. Structured error kinds.** A cap hit, a rate limit, a model refusal, a
transport failure, and an empty diff each select a different fallback instead
of walking the same list; transient failures retry the same vendor before
escalating to a more expensive one (the audit's own top capability ask;
OpenAI Agents SDK `error_handlers`).

**C6. Background lanes.** `claude --bg` (with `agents`, `logs`, `stop`,
`respawn`), `cursor-agent persist`, and `codex queue` let conductor poll a
long lane instead of holding a process, removing the wall-clock ceiling.

**C7. A golden-mission regression suite.** Recorded transcripts of past real
missions replayed through the parser, scheduler, and templating offline, so a
routing or template change is testable without spending on live vendors.

### Phase D: widen the aperture

**D1. Conflict-aware collate.** A dedicated resolver lane and a per-file
collision counter that quarantines hotspots. Evidence: 27.7% conflict rate
across 107k simulated agentic merges ([AgenticFlict](https://arxiv.org/pdf/2604.03551));
Cursor's swarm accumulating 70k conflicts, 7,771 on one file
([Cursor](https://cursor.com/blog/agent-swarm-model-economics)).

**D2. Taint tracking.** Issue and PR text pulled into a prompt is tainted; a
tainted lane runs with minimal tools and never holds push rights. Evidence:
CVSS 9.4 prompt injection through repo comments across Claude, Gemini, and
Copilot CI agents ([CSA, April
2026](https://labs.cloudsecurityalliance.org/research/csa-research-note-claude-code-github-action-prompt-injection/)).

**D3. Inline agent definitions.** `claude --agents '<json>'` and `agy --agent`
let a lane define its reviewer or fixer persona at dispatch time with nothing
on the operator's disk.

**D4. Cloud offload, last.** `claude ultrareview`, `claude --cloud`,
`codex cloud`, `cursor-agent worker` move review off the laptop. Adopt after
everything above; it changes where code and credentials live.

## First consumers

The week plan conductor was built for: OPERANT-J sitting 2 with Grok, Sol, and
Gemini as foreign judges (A2 and A3 make that sitting's verdicts tallyable),
the HarnessBench live tier over the four real harnesses, the cross-vendor
core-guard audit, and anti-slop passes reviewed by a model that did not write
the text.

## Recommended order

A1, A2, B1, B4 first: they remove the largest remaining ways to be wrong
(gamed gates, untallyable judgments) and the largest cost (re-paid context,
runaway loops). Then C1 and C3 for unattended runs, A3 through A5 for the
judge lanes OPERANT-J needs, B2, B3, B5, C2, C4 through C7, and D last.

## One design assumption to drop

Deterministic reruns are not reachable through seeds or temperature: batch-size
non-invariance in inference kernels varies output with server load
([Thinking Machines](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)).
Reproducibility lives at the artifact level: base commit, gate command,
toolchain, receipts. Lane text is inherently non-reproducible; never build a
check that assumes otherwise.

# Phase E: from self-building tool to general dispatcher

Original file: `docs/ROADMAP-2026-10.md`. Written 2026-09-06, closed 2026-09-07.

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

# Phase F: from a dispatcher that builds itself to one the lead can measure

Original file: `docs/ROADMAP-2026-11.md`. Written 2026-09-07, closed 2026-09-07.

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
| F6 launcher: ceiling, test items, fix cap by findings, gate preflight | 0.51.0 | Shape A via the launcher, salvaged on the interrupt-test flake, Grok's one finding fixed by hand | $4.83 |
| F13 Antigravity hooks verified free before dispatch; schema refused on its read lanes | 0.52.0 | Shape A via the launcher, build green under cap, Grok's two findings fixed by hand | $7.88 |
| F7 conductor land | 0.53.0 | Shape A via the launcher, build green under cap, Grok's two real findings fixed on the resumed thread with dispositions | $7.65 |
| F12 Claude read lanes under --restricted and --permission-prompts none; deliverable read lanes can write | 0.54.0 | Shape A via the launcher, build green under cap, both reviewers NO_FINDINGS, landed by conductor land | $8.63 |
| F2 wall clock on the ledger | 0.55.0 | Shape A via the launcher, build green under cap, Grok's three findings all fixed on the resumed thread, landed by conductor land | $15.69 |
| F8 golden fixtures for the Phase E shapes | 0.56.0 | lead work, four consumer recordings, five scratch missions | $1.58 |
| F9 Shape B and Shape C receipts; golden source placeholder landed from the Shape B sitting | 0.57.0 | Shape B twice (scratch, then conductor via land), Shape C once, three Opus verifiers | $11.85 |
| F15 the Shape C findings: six verify, report, and wall-clock defects; F1's dispositions.json deliverable, per-finding confidence, calibration line | 0.58.0 | two Shape A missions via the launcher, both builds green under cap, Grok one real finding each (one fixed by the fix lane, one test-only added by hand), both landed by conductor land | $22.17 |
| F10 anti-slop consumer over one document; taint hook count corrected to per-file with a per-matcher preflight | 0.59.0 | Sonnet edit lane with untrusted output and an E1 deliverable, Gemini and Opus cold reviews under taint, prose gate, lead applied the reviews by hand | $1.53 |
| F15 latent items closed: settle answer reads tolerate bad bytes; multi-fix-lane dispositions kept in the report | 0.60.0 | lead work, two hand fixes with tests | $0 |

Status 2026-09-07, end of the first Phase F sitting: every build item that survived Group 0
is shipped (F1, F2, F3, F5, F6, F7, F12, F13; F4 was already on the tree, F14 dropped on its
probe), 0.48.0 to 0.55.0, about $68 of fleet spend including the probes and the consumer run,
in one sitting of about two and a half hours. Of the receipt items, F10's first two consumers
ran: the core-guard audit (`docs/research/2026-09-07-consumer-core-guard-audit.md`) and the
Shape A fix that followed it (`docs/research/2026-09-07-consumer-core-guard-fix.md`, $8.56,
three commits on a branch of the harness repository, gated green, the operator's to merge);
F11 ran (`docs/research/2026-09-07-f11-unattended-reaudit.md`: the re-audit unattended, $1.73,
end event accepted by notification-hub; bridge-db unreachable from the lead's session; a third
fix mission, $2.08, closed the re-audit's agreed shapes on a second harness branch). F8 ran
(`docs/research/2026-09-07-f8-golden-fixtures.md`, 0.56.0: nine fixtures, $0.50 on the ledger
plus about $1.08 the pre-fix replays billed into throwaway homes, five replay defects fixed in
code). F9 ran (`docs/research/2026-09-07-f9-shape-b-c.md`, 0.57.0, $11.85: Shape B's sitting
split on the scratch spec and was unanimous and right on the conductor spec, landed by `conductor
land`; Shape C's Opus reviewer found six defects still on the tree and three spec gaps the pair
had passed, now F15). F15 ran (`docs/research/2026-09-07-f15-shape-c-fixes.md`, 0.58.0, $22.17:
two Shape A missions, the six defects and the three F1 spec gaps all on the tree, Grok one real
finding per mission, both landed by `conductor land`). F10's anti-slop consumer ran
(`docs/research/2026-09-07-consumer-anti-slop.md`, 0.59.0, $1.53: Sonnet edit as an untrusted
E1 deliverable, Gemini and Opus cold under taint, a prose gate; it found E21's hook-count check
wrong for every tainted Antigravity lane, fixed and re-proven live). Two of F15's latent items
closed by hand (0.60.0). OPERANT-J sitting 2 and the HarnessBench live tier are deferred by
operator decision (2026-09-07: Operant is updated separately, later) and carry into the next
roadmap. Phase F is closed. The last five build releases were landed by `conductor land`.

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

**F8. Golden fixtures for the Phase E shapes.** *Shipped 0.56.0, see the status above.* The golden suite held two fixtures, both
recorded on C5 (a capped cascade build and a review-and-fix). Every lane kind Phase E added
(script, human, plan, judge sitting, adversarial, cross-repo, deliverable) is covered by unit
tests and by no replay, and E10a's review found a plan lane whose deliverable was never recorded
so replay could not reach its pause. F8: record one small fixture per shape with `golden record`
on a scratch repository (a launcher-written Shape A on a two-line spec; a judge sitting over two
one-line candidates; a script lane; a human lane answered; a plan lane parked and continued;
a two-repo collision), each under $2, so a Phase F refactor that changes a receipt shape is a
fixture diff. Lead work plus about $10 of fleet spend. Depends on C7, E17.

**F9. Shape B and Shape C receipts.** *Shipped 0.57.0, see the status above.* The September reset listed both as shapes worth running
and neither ran. Shape B: two builders on one one-item spec (Sonnet 5 at `hard`, Gemini 3.7 Flash)
with an E4 sitting of two judges over both orders, conductor keeping the winner; the receipt is
whether the judges pick the build that passed the gate, and what the pair cost against one
Sonnet build. Shape C: Opus 5 as a third cold reviewer beside Gemini and Grok on three Phase F
Group 1 missions; the receipt is whether it reports anything the pair missed, with F1's
dispositions saying whether what it reported was real. About $12 for B and $9 for C. Depends
on E4, F1 for the C receipt to be readable.

**F15. What Shape C found (from F9).** *Shipped 0.58.0; the two `settle`/report latent items closed in 0.60.0; the fixture backfill and the two shapes-not-in-use items stay here as record.* Opus 5's cold review of the F3, F1, and F2 build commits,
verified on bytes (`docs/research/2026-09-07-f9-shape-b-c.md`). Defects still on the tree: (1) a
read lane whose fleet deletes its worktree comes back ok with `tests: null`, because `verify`
maps a vanished tree to a no-op and F3's skip then never gates it (`verify.py`, `runner.py`);
(2) `conductor report`'s precision table admits a review lane whose verdict did not parse, with
`findings` coerced to 0, and drops a disposition naming an unknown lane with no printed signal
(`report.py`); (3) `gate_s` omits the reproduce gate and setup/teardown, `idle_s` resets on
resume beside whole-life columns, and `busy` exceeds 1.0 whenever lanes overlap (`mission.py`,
`report.py`). Spec items passed as built but not built: the `c5-review-fix` projection backfill
F3 asked for; F1's schema-checked `dispositions.json` deliverable and per-finding confidence with
the calibration line. Latent: `settle()` reads an answer without `errors="replace"`; a rehydrated
disposition row is indexed unvalidated; a second fix lane overwrites the first's dispositions in
the report. Two or three Shape A missions, about $6 each, the F1 spec gap sized as its own item.
Depends on F9.

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

