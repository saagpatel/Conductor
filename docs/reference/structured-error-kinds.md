# Structured error kinds

Every failed attempt is classified into a closed list of kinds that fallbacks
and retries filter on.


A cap hit, a rate limit, a model refusal, a transport failure, and an empty
diff used to escalate down a lane's fallback list the same way: "not ok",
with the reason readable only in the attempt's prose. Every failed dispatch
now also classifies as exactly one of a fixed set of kinds, checked in this
order, first match wins:

```
interrupted, cancelled, parse, setup, taint, settings, refused, agent, adversarial, plan,
denied, reproduce, resume, gate_test_surface, gate, cap, breaker, timeout, rate_limit, transport, refusal, fleet_error, exit,
deliverable, no_op, read_moved_bytes, no_answer, commit, unknown
```

`parse` is checked that early on purpose: a dispatch that raised while its
output was being read comes back with no priced usage, which the cap check
below would otherwise read as a cap it could not enforce. Every kind
conductor decides for itself, from the `error` text it wrote -- `setup`
through `gate` -- is checked ahead of `cap` for the same reason: an
unpriced run (every cursor lane) cannot be shown to have stayed under its
cap, so without that order a lane that failed its setup, or was refused
before it ever spawned, came back as `cap` and sent a
`fallback: [{"on": ["cap"]}]` chasing a budget that was never the problem.

A cap kill that also timed out is `cap`, not `timeout`; a fleet error that
also mentions a rate limit is `rate_limit`, not `fleet_error`. `kind` is
computed from the receipt's own fields, never stored as one of its own, so
a receipt written before this feature still classifies correctly when read
back. It shows up as `kind` on every dispatch receipt (`result.json`,
`conductor runs`; `null` when the dispatch was `ok`).

`rate_limit`, `transport`, and `refusal` are read from the fleet's own
reported error text and status, case-insensitive:

| Kind | Patterns |
|---|---|
| `rate_limit` | `rate limit`, `rate_limit`, `429`, `overloaded`, `529`, `quota`, `resource exhausted`, `too many requests` |
| `transport` | `ECONNRESET`, `ECONNREFUSED`, `connection refused`, `ETIMEDOUT`, `EPIPE`, `socket hang up`, `fetch failed`, `network`, `502`, `503`, `504`, `stream ended without a result event` |
| `refusal` | Claude's `subtype` starting with `error_` (other than `error_max_budget_usd` and `error_max_turns`) when the text says `refus`, `cannot help`, or `not able to`; on every fleet, the text `I can't help` or `I cannot help` |
| `agent` | The receipt's own `error` field (never a fleet's), matched on the `agent '` prefix and the ` not applied: ` marker -- D3's own persona assertion, not anything a fleet reported |
| `adversarial` | The receipt's own `error` field, matched on the `adversarial lane changed source:` prefix -- E16's own check of the diff against an adversarial lane's base, not anything a fleet reported |

A fallback may set `"on": [<kind>, ...]` to run only in answer to those
kinds; an unknown kind is refused at load, naming the lane and the entry.
When the previous attempt's kind is not in the next attempt's `on`, that
attempt is passed over with a note
(`skipped fallback <label>: does not handle <kind>`) and the walk continues
to the one after it:

```json
{"fleet": "codex", "fallback": [{"fleet": "claude", "on": ["rate_limit", "transport"]}]}
```

A mission may also set `retry`, to try the same attempt again on its own
vendor before the fallback walk moves to a different one:

```json
{"retry": {"kinds": ["rate_limit", "transport"], "attempts": 2, "backoff_s": 1}}
```

`kinds` defaults to `["rate_limit", "transport"]` when omitted, `backoff_s`
to 0, and `attempts` (1 to 5) is required. An attempt that fails with a kind
in `kinds` is redispatched — same spec, a fresh run id, the same cancel
event and ledger rules — up to `attempts` more times, waiting `backoff_s *
2^i` between tries; a stop or a lane cancel arriving during that wait ends
it exactly as it would end a running dispatch, as `interrupted` or
`cancelled`. Each retry is its own attempt row, `retry_of` naming the first
attempt's run id and `retry` its index; the lane's `kinds` lists every
attempt's kind in order, retries included. A dry run never retries.

A retry passes the same pre-dispatch gate the outer attempt walk does, so it
starts nothing the mission may no longer pay for: when the attempt it is
retrying (or any lane running beside it) came back with no priced usage at
all, the budget is unverifiable and the retry is refused rather than
dispatched, the lane's `skipped` reading `budget unverifiable: ...; retry
<n> of <attempt> not started`.

`MissionResult.errors` (`result.json`, `conductor missions`) tallies every
kind seen across every lane's attempts, empty when nothing failed; when it
is non-empty `report.md` shows one line, `Errors: <kind> x<n>, ...`, and
each attempt's own line in its lane section carries `(kind: <kind>)` beside
its error text. This was the audit's own top capability ask
(`docs/archive/roadmaps-closed.md` item C5; OpenAI Agents SDK `error_handlers`).

