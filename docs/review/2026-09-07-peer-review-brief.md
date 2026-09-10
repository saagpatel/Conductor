# Peer review brief: conductor at 0.60.0

Written 2026-09-07 by the lead session for an outside reviewer (GPT-6 Astra, run by the operator
in the Codex app, not as a conductor lane). Read this file first, then `AGENTS.md`, then
`README.md`, then the code. Write everything you conclude into
`docs/review/2026-09-07-astra-notes.md`, which already holds the section contract. Nothing else
in the tree is yours to change.

## What conductor is

Conductor is a Python 3.12, stdlib-only dispatcher (`src/conductor`, about 21,000 lines, 63 test
modules, 1287 tests) that runs headless coding agents as **lanes** inside a **mission**. A lane is
one dispatch of one vendor CLI (Claude Code for Anthropic models, `agy` for Gemini through
Antigravity, `cursor-agent` for Grok and Composer through Cursor; the Codex lane exists but is
paused by operator decision) with a prompt, a mode (`read` or `write`), an effort dial, a dollar
cap, and a git worktree of its own. Conductor builds the argv per fleet, parses each fleet's
event stream for tokens and cost, enforces the cap and the vendor allowlist at dispatch, runs the
repository's test gate on what the lane wrote, commits green work to a lane branch, and writes a
signed receipt under `~/.conductor`. Missions add stages (build, review, fix, adversarial),
reviewer policy (a review lane never shares a vendor with the build it reviews), taint (text
from outside runs with fewer tools), pause and resume, quorum and ranking collate, planner and
human lanes, a rolling spend ceiling, salvage of a capped run's kept worktree, and `conductor
land` (merge a lane branch, gate it in a fresh worktree, verify golden fixtures, attest).

The lead session (Claude, in Claude Code) writes the spec, launches the mission, reads the diff,
and judges on bytes. The operator sets direction and cuts scope. The one design principle every
rule in the repo descends from: **a fleet's word is never evidence.** A lane's claim of success is
worth nothing; the diff, the gate output, and the receipt are what count.

## Where things live

Module one-liners are the lead's summary; each module's docstring is authoritative where they
differ.

| module | what it holds |
|---|---|
| `mission.py` (6765) | the mission runner: scheduler, stages, reviewer policy, pause/resume, collate, judges, planner and human lanes, wall clock, dispositions |
| `runner.py` (2739) | one dispatch end to end: argv, spawn, stream parsing, cap and breakers, taint enforcement, gate, commit, receipt |
| `cli.py` (1350) | every subcommand |
| `golden.py` (1253) | recorded missions replayed offline; scrubbing, eliding, prompt versions, `golden check` |
| `fleets.py` (1194) | `Spec`, fleet allowlists, per-fleet argv builders, `DispatchRefused` cases, taint hook files |
| `report.py` (1025) | the ledger report over receipts: vendor/stage, error kinds, reviewer finding rate and precision, calibration, wall clock |
| `shape.py` (741) | the Shape A launcher: build, two cold reviewers, fix on the resumed thread; prompts and caps by the sizing rules |
| `outputs.py` (641) | per-fleet event stream parsing |
| `gc.py`, `worktrees.py` | worktree and branch lifecycle |
| `verify.py` (575) | git verdicts (what changed, vanished trees), the gate, `commit_work` |
| `verdicts.py` (468) | review verdict and finding parsing, checklist verdicts, dispositions |
| `export.py`, `attest.py` | export bundles; signed lane receipts |
| `land.py`, `salvage.py` | landing a lane branch; salvaging a kept worktree |
| `spend.py`, `budget.py`, `ceiling.py`, `breakers.py`, `prices.py`, `forecast.py` | cost: per-run accounting, caps, rolling ceiling, breakers, price table, forecast |
| `collisions.py`, `surface.py` | conflict-aware collate across lanes and repositories |
| `errors.py` | structured error kinds |
| `notify.py`, `ports.py`, `prompts.py`, `paths.py` | notifications, per-lane ports, prompt fragments, paths |
| `scripts/` | `release.py`, `prose_gate.py`, `notify-hub.py` |
| `tests/golden/` | eleven recorded fixtures |
| `docs/archive/roadmaps-closed.md` | three closed roadmap phases (D, E, F), each with its dropped table |
| `docs/RESET-2026-09.md` | September reset and the wave index; full receipts in `docs/archive/receipts-2026-09.md` |
| `docs/research/` | live dated research and live-probe reports with URLs and receipts |
| `docs/archive/research-shelved/` | shelved-lane receipts (OpenCode, Ollama, pi, local models) |

## Where the project stands

Version 0.60.0, branch `feat/conductor-v1`, tree clean. Phase F closed today. Since 2026-09-05 the
ledger holds 130 missions and 429 runs of conductor mostly building conductor: about sixty
releases, each one spec, one Shape A mission, one release, with the lead landing by hand or by
`conductor land`. `conductor report --since 2026-09-05` prints the ledger; the figures the lead
reads most:

| measure | value |
|---|---|
| Anthropic build lanes | 51 runs, 36 ok, 4 cap misses, 7 gate failures, mean 25 min |
| spend lost to cap-cut runs | $50.59 across 9 runs, all salvaged by hand |
| spend lost to gate failures | $41.17 across 15 runs, most of them one load-sensitive test |
| Gemini review finding rate | 1 of 49 (its answer is the verdict alone) |
| Grok review finding rate | 9 of 49 parsed, 35 unparsed (it narrates before its verdict) |
| Grok precision over missions with dispositions | 7 fixed, 1 already fixed, 0 refused |
| mission versus release wall clock | 10 to 60 min versus 75 to 180 min |

Three things built in Phase E have no live receipt outside their own tests: unattended mode with
notifications on a real mission, human lanes answered on a real mission, planner lanes launching
a child that does real work. Shape B (best of two with a judge) and Shape C (Opus as a third
reviewer) each ran once (`docs/research/2026-09-07-f9-shape-b-c.md`).

## The junction

Conductor has spent three phases building itself. The question now is whether the thing that
exists is sound enough to spend the next phase on strengthening rather than extending, and what
strengthening means. That is what a peer review from first principles is for: not "does this
match its README" but "is this the right shape for the problem, what is wrong with it, what
should go, and what is missing".

The lead's own list, for you to critique rather than adopt (keep, change, or drop each, with a
reason):

1. First-run drills for every fail-closed check (taint hooks, restricted mode, deny rules): a
   golden or live drill that proves each refusal fires. E21's hook-count check was wrong for every
   tainted Gemini lane for a day and nothing noticed.
2. Shape C as a launcher policy option: Opus third reviewer on multi-module specs (found six
   defects and three spec gaps the two-reviewer pair passed, about $2.60 a mission).
3. A spec-fidelity stage: a cheap read lane lists spec items and marks each built or not built
   before review runs (three of 21 Shape C findings were spec gaps).
4. Shape D for prose deliverables: untrusted edit lane, two cold readers, a prose gate, no fix
   lane, with a `shape d` launcher.
5. Live dispositions and calibration figures from the next code mission, then keep or retire the
   flat summary dollar in sizing rule 10 on the data.
6. Wall-clock receipts read into the cost model: one mission spent 45 of 58 minutes in a single
   build lane; lead time, not fleet spend, is the cost.
7. A leaner Sonnet build prompt that names the gate flags (builders ran the suite serially at
   four minutes instead of with xdist).
8. A reproduce gate for fix lanes on non-code deliverables.

## What is settled and what is not

Operator decisions, on record in `AGENTS.md`, that the roadmap will not reopen: local-only
repository; Codex and every OpenAI lane paused; OpenCode, OpenRouter, Ollama, pi, local models,
background lanes (C6), and cloud offload (D4) shelved; the standing rejected list (MCP wrapper,
fleet self-commit, atomic budget reservation, reclaiming crashed runs by age, `claude --bare`);
no reviewer quotas in any prompt; Cursor never a rank judge or a tainted lane.

You are reviewing from first principles, so if you believe one of these is wrong, say so in the
notes under "Disagreements with standing decisions", one paragraph each with the reasoning. Do
not spend review effort designing around them or proposing their replacements in detail; the
operator decides whether to reopen any of them.

Everything else is open: architecture, module boundaries, the mission and lane model, the trust
and taint model, cost accounting, resume and receipts integrity, the gate, the test suite, the
golden fixtures, the prompts conductor sends, the docs, the sizing rules, the shapes, and the
roadmap itself.

## How to look

- Read `AGENTS.md` in full; it is the contract for working here and it holds the model notes and
  the prompt rules with their evidence.
- The README is long (about 2800 lines). Its section headings are a map of every feature; read
  the sections that match what you are looking at rather than all of it first.
- The gate is safe to run and touches nothing in the tree if you pass a scratch temp dir:
  ```
  PYTHONPATH=src .venv/bin/ruff check src tests
  PYTHONPATH=src .venv/bin/pytest -q -p no:cacheprovider -o addopts="" -n auto --dist loadgroup --basetemp="$TMPDIR/conductor-review-gate"
  ```
  About thirty seconds. Never run it without `--basetemp`, and never run it serially.
- `.venv/bin/conductor golden check` replays the eleven fixtures offline, no spend.
- `.venv/bin/conductor report --since 2026-09-05` prints the ledger from the receipts in
  `~/.conductor`. The receipts themselves (`~/.conductor/missions/<id>/`) are JSON and readable.
- `.venv/bin/conductor --help` and each subcommand's `--help`. `conductor mission FILE --dry-run`
  validates a mission file without dispatching.
- The three most recent research reports show conductor doing real work and what went wrong:
  `docs/research/2026-09-07-f9-shape-b-c.md`, `...-f15-shape-c-fixes.md`,
  `...-consumer-anti-slop.md`.

## Rules for this sitting

- Write only to `docs/review/2026-09-07-astra-notes.md`. No other file changes, no commits, no
  branches. The repository has no remote and must never get one.
- Do not dispatch anything. `conductor dispatch`, `conductor mission` without `--dry-run`,
  `conductor shape`, `conductor salvage`, and `conductor land` all spend money or move branches.
  Read-only subcommands, `--dry-run`, the gate, and `golden check` are fine.
- Do not run `codex`, `claude`, `agy`, or `cursor-agent` yourself.
- Cite or drop: every defect and every weakness names a file and line or a receipt path. A claim
  without a citation is not a finding.
- No quotas. If a section has nothing that meets its bar, say so; an empty section is a complete
  answer. Report anything that could cause incorrect behavior, a wrong receipt, a misleading
  ledger figure, lost spend, or a trust boundary that does not hold. Omit style and naming.
- Effort estimates in the roadmap section use the repo's sizing vocabulary: spec items, modules
  touched, whether it touches the scheduler, the runner's wait loop, or resume.
