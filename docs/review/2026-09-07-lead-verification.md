# Lead verification of the Astra review

Written 2026-09-07 by the lead session after four Opus 5 read agents reproduced every cited
finding in `docs/review/2026-09-07-astra-notes.md` against `67a47f3`, in memory, with no
dispatch and no edits. Every defect D1 to D23 and weaknesses W1 to W5 stand. Nothing was
refuted. Below: only where the verification sharpened, narrowed, or extended the finding.

## Sharper than written

- **D1.** The stop guard checks `kind == "child"` and the lane name, so answering `stop` to a
  *human* pause launches every parked planner's child (`mission.py:4082-4087`, `4215`).
- **D2.** An edited child that drops `max_cost_usd` hits `assert` at `mission.py:4099` on the
  main thread and takes the resume down.
- **D3.** The comment at `mission.py:1505-1507` claims `_validate_graph` rejects forward
  references; it does not (`930-950`). README:668-670 states the ordering assumption as fact.
  Probe: consumer declared first loads `tainted=False` and dispatches without the deny list.
- **D5.** Nine bypasses of the prefix hook, not four: `command curl`, `/usr/bin/curl`,
  `env curl`, `python3 -c`, `bash -c 'curl x'`, `\curl`, `(curl x)`, `nc`. The same prefixes
  are Claude's `--disallowedTools`, so "no network egress" (README:675-678) overclaims on both
  fleets. README:764 already words `--restricted` honestly.
- **D6.** A tag named like the branch shadows it in `git rev-parse` (probe). `_perform` resolves
  the name a second time at merge (`land.py:200`). `land.py` never reads `tip_sha`, while
  `mission._trusted_lane` (`3577-3591`) already does the check land omits. No repo-identity
  check (`salvage._same_repo` exists, unused here).
- **D7.** Chain with `mission_id: OTHER` verifies for mission `m2` (`attest.py:265` reads the
  untrusted file). `_ReceiptChain.append` swallows write failures (`mission.py:2361`), so a
  short chain is a realistic local state.
- **D9.** Four crash sites: `verdicts.py:205`, `verdicts.py:421`, `outputs.py:98-103` (bare
  `NaN` from fleet stdout), `runner.py:489-498` (list-valued schema passes preflight). A parse
  crash leaves `runs/<id>/` with only `stdout.log`, invisible to `spend` and `report` forever;
  `ledger.add` never runs. `parse_dispositions_deliverable` runs in `settle()` on the scheduler
  thread with no boundary: one malformed `dispositions.json` aborts the mission.
- **D12.** `tests/test_best_of_n.py:106-118` pins the wrong behavior (event set before dispatch,
  asserts ok). Cancel is first consulted inside `_wait`, after spawn and after `setup`.
- **D13.** `_dispatch_aux` passes no `lane`/`mission` to `dispatch`. `spend._mission_runs`
  (`191-217`) also omits `resolve`, so `spend --by mission` files resolvers under standalone.
  No `previous_resolves` exists anywhere.
- **D14.** A `NaN` rate in `prices.json` defeats both `budget.over_cap` and `Ledger.add`: every
  dollar guardrail off, no warning line.
- **W1.** A tainted Antigravity lane can overwrite its own `.agents/conductor-taint.py` mid-run
  (re-read per tool call, untracked by design, edit tools not denied); the receipt cannot see it.
- **W2.** `fleets.py:225` emits the hook command path unquoted; a space in the path fails the
  hook at exec and the lane reads green.

## Narrower than written

- **D10.** Real interleaving, sub-millisecond window, one operator launching by hand: not
  reachable today. Matters only if two resumes ever fire together.
- **D11.** Stop and cancel are already checked in `_pollable_sleep`; only the ledger check is
  missing on retry. Bounded by the per-dispatch cap.
- **D15.** Exposure is exactly a write lane on agy exiting 0 with a cut stream; a read lane
  fails on the empty answer. `tests/test_budget.py:210` pins the current behavior.
- **D21.** Deliberate (comment in `tests/test_report.py:802`). All nine cap receipts in the
  corpus were settlement caps with a gate that ran, so the printed number is right today; the
  defect is on the watcher-kill path.
- **W4.** The pre-gate commit is undone at `runner.py:2264`; the real gap is the deliverable
  copy before the gates and teardown after `GitState.capture`.
- **D22.** Real git emits the quoted form for tabs and non-ASCII names by default; this repo's
  paths are all ASCII.
- **D23.** Docs only. The receipt's own `cost_usd` is 1.53167; the $1.63 recurs in
  `RESET-2026-09.md:547,552` and `ROADMAP-2026-11.md:36,57`.

## Doc claims checked by the lead

- README:7 first example dispatches through the paused Codex fleet.
- README:1202 "must show its own check failing before it may edit" describes a check that runs
  after the edit, on the transplanted test surface.
- `notify.py:38-66` runs the command through a shell with unbounded capture and no
  process-group kill on timeout; no hang reproduced.
