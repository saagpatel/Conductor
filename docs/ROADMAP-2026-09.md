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

Receipts and the operating rules they produced: `docs/RESET-2026-09.md` and `AGENTS.md`
("Shape A"). A5 and C2 ran as two missions in parallel against one checkout and merged in that
order; the second merge was the lead's. Next candidates: the open Phase B and C items (B2, B3, C4
through C7), through the same shape.

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
