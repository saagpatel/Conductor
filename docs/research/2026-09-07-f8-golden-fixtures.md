# F8: golden fixtures for the Phase E shapes

Date: 2026-09-07. Release 0.56.0. Roadmap item F8 (`docs/ROADMAP-2026-11.md`).

## What was asked

One recorded fixture per lane kind Phase E added, so a Phase F refactor that changes a receipt
shape is a fixture diff rather than a live mission. The suite held two C5 recordings and
nothing that exercised script, human, plan, judge, cross-repo, unattended, or notify paths.

## What was recorded

| fixture | source | shape | ledger cost |
|---|---|---|---|
| `f10-shape-a-foreign-repo` | consumer mission on the harness repository | launcher-written Shape A, all four lanes green, fix on the resumed thread | (F10) |
| `f10-shape-a-fix-stopped` | consumer mission 2 | Shape A, both reviewers read, fix pause answered `stop` | (F10) |
| `f10-shape-a-reaudit-fixes` | consumer mission 4 | Shape A, fix pause answered `stop` | (F10) |
| `f11-unattended-read-notify` | F11 re-audit | three read lanes, `--unattended`, `notify` hook, per-mission ceiling | (F11) |
| `e6-script-lane` | scratch | script build lane (commit) and script read lane (`cat` of the build's answer) | $0.00 |
| `e7-human-lane-answered` | scratch | script build, human approval with a `sign-off.txt` deliverable, answered on resume | $0.00 |
| `e10-plan-lane-continued` | scratch | Sonnet 5 plan lane writes a one-lane script child, parked, `continue` | $0.09 |
| `e4-judge-sitting` | scratch | Composer 2.5 and Grok 4.6 candidates, Gemini 3.7 Flash collate + Sonnet 5 judge, both orders | $0.41 |
| `e26-cross-repo-collision` | scratch | three script lanes, two repositories, hotspot in one, none across | $0.00 |

Every scratch mission carried `ceiling: {per_hour_usd: 10, per_day_usd: null}` (the default day
ceiling refuses a launch after an attended sitting, decision on record from F11). The judges
were right: Composer's `slug` appends a hyphen for a leading separator and only trims a trailing
one; Grok's skips leading separators and never appends a pending one. Scores 4.0/10 and 3.5/9.

## What record and check exposed

Each of these is fixed in conductor and pinned by a test; no fixture was edited by hand.

1. **Replay ran the mission's notify hook.** `golden.replay` shelled out to the fixture's
   recorded `notify.command`. It failed only because the scrubbed path (`<user>/...`) is not a
   real path. `run_mission(notifier=)` now stands in for `notify.emit` as `dispatcher` does for
   `runner.dispatch`; the projection pins the event names that fired.
2. **A planned mission's relative `cwd` resolved against `deliverables/`.** The plan lane's child
   is loaded from its kept copy under the parent's mission directory, which the planner never
   saw, so `cwd: "."` resolved to a non-repository (the dry run passed anyway). `load_mission`
   takes `base_dir`; both child loaders pass the plan lane's own repository.
3. **Collate, judge, and resolve dispatches bypassed the replay dispatcher.** The judge-sitting
   fixture dispatched its four judges live at record, at check, and on one probe: about $1.08,
   billed into throwaway replay homes and therefore invisible to the ledger. The fixture's own
   `fleets` read `["cursor"]`, the tell. `_dispatch_aux` routes the three sites through the
   dispatcher under labels (`collate`, `collate:<judge>:<order>`, `resolve`); `record` copies the
   runs `result.json` names; replay maps them by label. Before the fix the cache hit rate moved
   between replays (0.736 vs 0.773) because the four live dispatches were real and different.
4. **A collate prompt's conflict line cannot be reproduced offline.** `git merge-tree` over the
   recorded tips finds nothing in a replay repository, so every collate prompt with a
   `(conflict: a, b)` line differed from its recording. `run_mission(conflict_finder=)` stands in
   for `collisions.merge_conflicts`; replay answers from the recorded `collisions.conflicts`.
5. **JSON keys were never scrubbed, and `record` never ran the guard.** The cross-repo
   `overlap.files` key `<repository>:shared.txt` carried the real path. Keys are scrubbed like
   values, and `record` runs `scrub_guard` on the scrubbed copy and refuses on any hit.
6. A `structured_output` answer (the Sonnet judge's, over 512 characters of `reason`) was elided
   and failed the elision check; the whole object is kept now. A recorded run with no receipt in
   the fixture (the pre-fix judge fixture) crashed `Result.from_dict`; it reads as a difference.

One gate flake: `test_a_resume_keeps_the_escalated_flag_on_the_kept_lane` went red once on the
first full run under `-n auto` (green solo and under a 24-run stress; the same test failed under
two concurrent suites on E11's receipt). It carries the serial xdist group and the gate passes
`--dist loadgroup`, per the standing rule.

## Left as is

- `source` in every fixture's `mission.json` is the mission file's path under `<user>` or
  `<cwd>`; the two C5 fixtures carry a `/private/tmp/...` scratchpad path the same way. Not a
  home leak; a placeholder for it is a small follow-on if the operator wants one.
- The two C5 fixtures still predate `fleet_versions` and `prompt_versions` (drift notes only).
- A resumed mission's `previous_collates` are not re-dispatched in replay and are not copied.

## Cost

$0.50 on the ledger (judge sitting $0.41, plan lane $0.09, the rest $0), plus about $1.08 the
three pre-fix replays billed into temporary homes. Lead time about two hours.
