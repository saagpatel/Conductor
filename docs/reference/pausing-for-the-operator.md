# Pausing for the operator

A mission can park and wait for an operator answer before it continues.


A mission may declare two pause points, either or both:

```json
{
  "max_cost_usd": 10,
  "pause": {"before": ["publish"], "spend_usd": 3}
}
```

`before` names lanes that must not start until the operator has answered;
`spend_usd` parks the mission once the ledger's spend reaches it (refused if
`max_cost_usd` is set and `spend_usd` is not below it). Both are refused if
empty, and `before` is refused naming an unknown lane. These are mission
data, written by whoever wrote the mission file, never a request a fleet's
own output can make: a fleet's word is not evidence, and the operator's
standing rule is that outward actions (push, publish, merge) and spend past
a threshold are the operator's to approve, not a lane's to request for
itself.

The check runs right before a lane that is otherwise ready would be
submitted (its `needs` already satisfied and ok), in this order: is the lane
named in `pause.before` and not yet answered, then has `pause.spend_usd` been
reached and not yet answered. The first one that fires parks the mission:
nothing more starts, but a lane already dispatched is already paid for and
keeps running to its own end rather than being cancelled or killed mid-flight
the way a stop signal or a passed `early_cancel` sink would; every lane still
waiting settles skipped, `paused: <reason>; not started`, and collate does
not run. Conductor then writes `pause.json` beside the mission:

```json
{
  "kind": "lane",
  "lane": "publish",
  "spent_usd": null,
  "threshold": null,
  "asked_at": "2026-09-05T12:00:00+00:00",
  "question": "Lane 'publish' is a pause point; continue the mission?",
  "answer": null,
  "answers": []
}
```

`kind` is `"lane"` or `"spend"`; a spend pause instead carries `spent_usd` and
`threshold` and a null `lane`. The mission result is not ok and carries
`paused: {"kind", "lane", "spent_usd", "threshold", "question"}`; `report.md`
shows one line: `Paused: <question> Resume with: conductor mission --resume
<id> --answer continue|stop`. `conductor mission` exits **4** for a paused
result — neither ok nor failed, waiting.

Resume with an answer:

```
conductor mission --resume MISSION_ID --answer continue
conductor mission --resume MISSION_ID --answer stop
```

`--answer` needs `--resume`, is refused on a mission that is not paused, and
resuming a paused mission without one is refused with the question.
`continue` reruns the parked lane (and anything after it) normally; kept
lanes stay kept and are not paid for again. `stop` dispatches nothing at all:
every lane not already kept settles skipped `paused: operator answered
stop`, and the result stays not ok, carrying the answered record. Either way
the resolved record moves into `pause.json`'s `answers` list; a named lane's
pause point and the spend pause point each fire at most once **after being
answered `continue`** — a `stop` leaves it free to ask again on a later
resume, so a mission can pause more than once across resumes. `mission.json`
itself is never rewritten; the answers live only in `pause.json`. A dry run
never pauses and writes no `pause.json`; a dry-run resume needs no answer and
records nothing. `conductor missions` shows `"paused": true` while
`pause.json`'s `answer` is null, `false` once it is answered.

Evidence (`docs/ROADMAP-2026-09.md` item C2): LangGraph `interrupt()`,
Microsoft request/response events.

