# Cheap-first cascade

A mission can try a cheaper model first and escalate only when that attempt is
not ok.


A mission may set `"cascade"`: an attempt-shaped object (the same keys a
`fallback` entry accepts, `fleet` required) that becomes the **first**
attempt of every lane it applies to, with that lane's own attempts (its
primary and its `fallback` list) following as the fallbacks:

```json
{
  "cascade": {"fleet": "codex", "model": "luna", "cap_usd": 0.10},
  "lanes": [
    {"fleet": "claude", "model": "opus", "mode": "write"}
  ]
}
```

It applies to every lane whose `stage` is `build`, and, in a mission with no
stages at all, to every write lane; a lane may set `"cascade": false` to opt
out (any other value is refused at load). Fields the cascade entry does not
set are inherited from the lane's own primary the way a fallback inherits
(mode, test, commit, prompt, and the rest), except `model` when the fleet
differs, as for fallbacks. The prepended attempt is an ordinary attempt under
every existing rule: the per-stage vendor `policy`, the review-lane vendor
rule, the routing allowlist, and the write-lane isolation rule all see it
exactly as they see any other attempt, so a cascade on the reviewer's own
vendor is refused with the same message as any other attempt.

Each lane's receipt gains `escalated`: true when its first attempt was
dispatched and was not ok, and a later attempt then ran. Whenever the mission
has a cascade, the result gains an `escalation` block:

```json
{
  "lanes": 4,
  "cheap_ok": 3,
  "escalated": 1,
  "rate": 0.25,
  "cascade_usd": 0.34,
  "escalated_usd": 1.10
}
```

`lanes` is how many lanes the cascade applied to, `cheap_ok` how many of
those the cascade attempt itself passed, `escalated` how many went past it,
`rate` is `escalated / lanes` (`null` when `lanes` is 0), and `cascade_usd` /
`escalated_usd` are what the cheap attempts cost in total versus what
running past them cost. `report.md` carries one line: `Cascade: <cheap_ok> of
<lanes> lanes passed on <fleet/model>; <escalated> escalated ($<cascade_usd>
on the cheap attempts, $<escalated_usd> after)`.

The cascade is a mission option, never a default: routing one cheap lane
first cut cost 31% at 0.91 micro-F1 in one benchmark
([UCCI](https://arxiv.org/pdf/2605.18796)), and conductor's own cheap lanes
have found real defects for $0.03 — but a fixed ladder can be worse than
routing on some code tasks ([Is Escalation Worth
It](https://arxiv.org/pdf/2605.06350)); see `docs/archive/roadmaps-closed.md` item B3.

