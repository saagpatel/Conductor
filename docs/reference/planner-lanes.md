# Planner lanes

A plan lane asks the operator to launch a child mission and consumes one
approval per child.


A lane whose deliverable is a mission file: conductor loads and dry-runs it,
then asks the operator to launch it. This is the first place conductor
spends on the operator's behalf without a human-written mission, so the
pause is unconditional.

```json
{
  "name": "plan",
  "fleet": "claude",
  "mode": "read",
  "plan": true,
  "deliverable": {"path": "child.json"},
  "prompt": "Write a mission file at child.json that fixes the failing test."
}
```

`plan` is a boolean lane key. It must be a read-mode lane on every attempt
(primary, fallback, and any cascade attempt), must declare a `deliverable`
whose `path` ends in `.json` or `.toml`, and may not be a `human` or
`script` lane, tainted, `untrusted_output`, or named as another lane's
`base` or `resume` -- each refused at load, the lane named. A mission
carries three load-derived fields no mission file may set directly: `depth`
(0 unless conductor stamped it as a child), `parent`
(`{"mission_id", "lane"}`, or `null`), and `budget_from_parent` (`false`
unless the launch clamped this child's own `max_cost_usd` to its parent's
remaining ledger -- see budget rollup, below). `mission.PLAN_MAX_DEPTH` (1)
bounds how deep a plan lane's own child may itself plan a grandchild.

When a plan lane's own dispatch settles ok, conductor loads the deliverable
with `load_mission` (so the child's `source` is the deliverable's own path;
a relative `cwd`, `prompt_file`, or `schema` in the child resolves against
the plan lane's own repository, since the kept copy lives under the parent's
`deliverables/`, a directory the planner never saw -- F8's first scratch
plan lane wrote `cwd: "."` and resolved to a non-repository before this),
stamps it with `depth = parent depth + 1` and `parent = {parent mission id,
lane name}`, and checks, in order: the file loads as a mission (a
`MissionInvalid` fails the lane with the message, kind `plan`); its `depth`
does not exceed `PLAN_MAX_DEPTH`, and it declares no plan lane of its own
when it is already at the limit; it declares `max_cost_usd`, bounded, and at
or under the parent's `ledger.remaining()` when the parent has a budget; its
`ceiling` is no looser than the parent's (a bound the parent has may not be
`null` or larger in the child). Conductor then dry-runs the child
(`run_mission(child, home=home, dry_run=True)`); a dry run that is not ok
fails the plan lane with its first error. Every one of these is conductor's
own check, never a fleet's word, and reads back as `kind: "plan"` like the
[agent](inline-agents.md), [taint](taint.md), and
[adversarial](lane-stages.md#adversarial-test-lanes-e16) checks. The lane's `LaneResult` and
receipt gain `plan: {"child_path", "child_name", "child_max_cost_usd",
"depth", "dry_run_ok", "refused": <reason or null>, "child_sha256",
"child_policy"}` -- the last two being what the operator is about to be
asked to approve: the sha256 of the child file's own bytes, and a summary
of the policy those bytes declare (`max_cost_usd`, the effective `ceiling`,
`depth`, lane count). Both are repeated in `pause.json` and in the paused
result.

Once every check passes the mission parks, unconditionally -- there is no
key that disables it, and `pause.before` need not name the lane:

```json
{
  "kind": "child",
  "lane": "plan",
  "child_path": "/repo/child.json",
  "child_name": "fix-the-test",
  "child_max_cost_usd": 3.0,
  "child_sha256": "9f2c...",
  "child_policy": {"max_cost_usd": 3.0, "ceiling": {"per_hour_usd": 10.0,
    "per_day_usd": null}, "depth": 1, "lanes": 2},
  "question": "Lane 'plan' planned mission 'fix-the-test' ($3.00); launch it?"
}
```

`conductor mission --resume MISSION_ID --answer continue` launches the child
as its own mission, synchronously, through `run_mission(child, home=home,
mission_id=<pre-claimed id>)` in the parent's process -- its own directory,
running lock, and receipt chain, never the parent's; the child's snapshot
carries its `depth` and `parent`. `--answer stop` fails the plan lane as
`child launch refused by the operator`, and the mission settles as any
stopped pause does. `--unattended` refuses a mission with a plan lane --
nobody is there to answer its pause.

**One answer launches exactly one child.** Two plan lanes can park in the
same pass -- the scheduler submits every ready lane and raises a pause only
for the first completion -- so an answer is consumed by the one lane the
pause it answers names, and by no other. A `continue` launches that lane's
child alone; a `stop` refuses that lane alone; an answer to a `human`,
`lane`, or `spend` pause, which names no plan lane, launches nothing at all.
Any planner still parked raises its own `kind: "child"` pause on the same
resume, once nothing else has parked the mission, so a two-planner mission
takes two answers and the mission parks again after the first.

**The approval is of the bytes that were checked.** Before a `continue`
launches anything, the child file is read and hashed again: a digest that
does not match the one recorded at the park fails the lane as `child plan
changed since it was approved: <old8> -> <new8>; re-run to approve the
revised plan`, and nothing is launched. Every check the park ran -- depth, a
bounded `max_cost_usd` within the parent's remaining ledger, a ceiling no
looser, a clean dry run -- is then rerun in full against the ledger as it
stands now, and any refusal fails the lane as `child plan no longer passes
its checks: <reason>`. Both are ordinary recorded lane failures, never an
exception out of the scheduler, so an edited child (one that dropped its
`max_cost_usd` included) refuses the launch rather than taking the resume
down with it. Re-running the planner is how a revised plan gets approved.

**The child's budget is the parent's.** At launch, when the parent has a
budget, the child's `max_cost_usd` is clamped to `min(child's own, parent's
ledger.remaining())` and the child's snapshot gets `budget_from_parent:
true`; a child launched under a budgetless parent runs under its own
`max_cost_usd`, unclamped. Before the child ever dispatches, the plan
lane's receipt (`lanes/<name>.json`) is rewritten with `plan.child:
{"mission_id": <the id the child will run under>, "state": "launched"}` --
`result.json` is not yet final, so a parent whose process dies mid-child
leaves a receipt naming it. When the child returns, `plan.child` gains
`state: "finished"` with the fields it has today (`"mission_id", "ok",
"cost_usd", "paused", "report_path"`); once the child is genuinely final
(not still paused), its `cost_usd` and any unpriced dispatches are rolled
into the parent's ledger through `Ledger.add_child` -- the parent's
`budget` block, `cost_usd`, and `pause.spend_usd` all see it, `plan.child`
gains `rolled_up: true`, the parent's `MissionResult` gains
`children_cost_usd`, and the note in `notes` becomes `child '<id>' spent $X
(rolled into this budget)`. A still-paused child's spend is noted but not
yet rolled up (`... (not rolled into this budget; child is paused)`). The
lane is ok when the child was ok. A child that pauses on its own pause
point is left paused; the parent's plan lane fails as `child paused: <child
id>`, naming it, so the operator resumes the child by hand.

**Resuming a parent whose plan lane already named a child** re-derives the
outcome from the child's own disk state rather than trusting the parent's
stale receipt: a child whose directory still holds a live running lock
refuses the resume (`MissionInvalid("child '<id>' is still running")`); a
child that is paused (its `result.json` has `paused` with no terminal
answer, or its own `pause.json` is unanswered) refuses the resume too
(`child '<id>' is paused; resume it first`); a child whose `result.json` is
final, ok or not, is adopted without dispatching anything -- the plan lane
reads ok when the child was ok, failed as above when not, the budget rollup
applied exactly once (`plan.child.rolled_up` guards against a second
resume double-counting it); a child whose directory or `result.json` has
gone missing fails the lane as `child '<id>' missing`. Once a child has
reached that rolled-up, finished state, `_trusted_lane` accepts the plan
lane's receipt as-is on every later resume, ok or not, rather than
re-deriving it again.

