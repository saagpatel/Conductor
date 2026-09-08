# Human lanes

A lane with fleet human blocks until the operator answers, then resumes as data.


A lane whose fleet is the operator, not a CLI:

```json
{
  "name": "approve",
  "fleet": "human",
  "prompt": "Does this match the brief? {{lanes.build.diff}}",
  "needs": ["build"],
  "deliverable": {"path": "sign-off.txt"}
}
```

It carries `name`, `prompt` or `prompt_file` (the ask -- it may use the same
`{{lanes.<name>.answer}}` / `.diff` / `.deliverable` templates any lane's
prompt can), `needs`, and an optional `deliverable {path}`. Every other
attempt key, `fallback`, `cascade`, `stage`, `branch`, `base`, `resume`, and
being named as another lane's `base` or `resume` are refused at load, the
lane named and the reason: nothing is ever dispatched, so none of them mean
anything, and `Spec.validate` never sees this lane's attempt at all.

A human lane is tainted at load, always: what comes back is operator-pasted
text, arriving the way a fleet's own output does -- outside the trust the
mission's own prompt carries -- so `taint_from` records `human`, and every
taint rule (see below) applies unchanged: never a `branch`, refused on a
fleet that cannot enforce it downstream, fenced in a receiving prompt.
`{{lanes.<name>.test_touched}}`, `.diff`, and `.verdict` are refused
referencing one at load -- it has none of those; `.answer` and `.deliverable`
work exactly as they do for any lane.

When the scheduler reaches a human lane, it renders the ask (templates and
`prefix` included, like any dispatched prompt) to `asks/<lane>.txt`, records
the lane not started (`skipped: "paused: waiting for the operator"`, no
cost), and parks the mission exactly like a `pause.before` lane does -- a
human lane never fires `pause.before` or the spend pause on top of its own
park. `pause.json` gains `kind: "human"`, the lane, and `ask_path`;
`question` is the ask's first line plus `answer with --answer TEXT or
--answer-file PATH`.

Resume with the answer:

```
conductor mission --resume MISSION_ID --answer "looks right, ship it"
conductor mission --resume MISSION_ID --answer-file notes.txt
conductor mission --resume MISSION_ID --answer stop
```

`--answer` and `--answer-file` are mutually exclusive; `--answer-file`'s
content becomes the answer verbatim. Either is written to `answers/<lane>.txt`
-- the same place any lane's answer lives -- and the mission continues;
`continue` is refused (`a human lane needs an answer`) and `stop` stops it
like any other pause. When the lane declared a `deliverable`, its file must
already exist at answer time (the same path rules a deliverable's `path`
always follows) or the resume is refused naming the path. `pause.json`'s
`answers` history records the answer's length and when it landed, never the
text itself a second time -- it is already on disk, once. A `kind: "lane"`
or `kind: "spend"` pause still accepts only `continue`/`stop`, and refuses
`--answer-file`.

On a later resume an answered human lane needs no run receipt: its answer
file (and declared deliverable) on disk are the whole record, so it is kept,
not re-asked. `report.md` shows it as `human, answered <timestamp> (<n>
chars)` rather than an attempt row.

## Notifications

Nothing above tells anyone outside the terminal that a mission paused,
ended, or hit a breaker. `notify` is an opt-in mission key that runs a shell
command at those three moments:

```json
{
  "notify": {
    "command": "cat >> /tmp/conductor-events.jsonl",
    "events": ["pause", "end", "breaker"],
    "timeout": 10
  }
}
```

`command` is required and refused empty; `events` defaults to all three and
is refused naming anything else; `timeout` defaults to 10 seconds and is
refused zero or negative. The command runs through the shell in the
mission's `cwd`, with the event as one line of JSON on stdin and
`CONDUCTOR_EVENT` (the event name) and `CONDUCTOR_MISSION` (the mission id)
set in its environment:

- `pause`, right after `pause.json` is written: `{"event", "mission_id",
  "kind", "lane", "spent_usd", "threshold", "question"}` -- the same fields
  as the pause record above.
- `end`, right after `result.json` and `report.md` are written: `{"event",
  "mission_id", "ok", "name", "cost_usd", "lanes": [{"name", "ok", "kind"}]}`.
  Not sent while the mission is parked on a pause point -- a paused mission
  has not ended.
- `breaker`, whenever a lane's final attempt settles having tripped one:
  `{"event", "mission_id", "lane", "breaker", "run_id", "cost_usd"}`.

Notifying the operator is not the operator's decision to make: like
`setup`/`teardown` above, a notification's outcome is a note, never a
verdict. Conductor never blocks on it beyond `timeout`, and whether it
succeeded never changes `ok`, an exit code, or a pause -- it is recorded,
in order, as `notifications: [{"event", "ok", "exit_code", "timed_out",
"error"}, ...]` on the mission result and as a `## Notifications` section in
`report.md`. The command runs in its own process group, which is killed
whole on timeout so a background child it spawned cannot outlive the
mission that fired it, and its output goes to a temporary file of which
only a bounded tail is read back for the failure reason. A dry run emits
nothing, and neither does a golden replay:
`mission.run_mission(..., notifier=...)` takes a callable in place of
`notify.emit`, the way `dispatcher` stands in for `runner.dispatch`, and
`golden.replay` passes one that records the event name without running the
command -- a fixture's recorded hook is the operator's, never a replay's to
run (see "Golden missions").

