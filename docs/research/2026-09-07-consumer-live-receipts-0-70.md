# Consumer: the 0.69.0 and 0.70.0 receipts read live on one mission (W11), 2026-09-07

Release 0.71.0. The first mission on the tree after 0.70.0, chosen so every receipt field added
across 0.69.0 and 0.70.0 gets a live reading at once: `usage.price` on estimated runs, the mission
ledger's in-flight figures in `running.json` and `pause.json`, and the `settings` block on a Claude
write lane. The spec was W11: sharpen the reviewer precision table's `undispositioned` count (a
gap noted on the W7 consumer mission) and pin the README's error-kinds block to `errors.KINDS`.
Mission `20260907T155921Z-w11-undispositioned-kinds`, `shape a --items 3 --tests-items 1
--modules 2 --opus-review`, build cap raised from $5 to $8 by hand against the forecast's p80.

## Shape

| lane | model | outcome | cost |
|---|---|---|---|
| build | Sonnet 5, hard | green, `settings: {"checked": true, "modified": []}` | $1.52 |
| review-gemini | Gemini 3.7 Flash | NO_FINDINGS; `usage.price` names `gemini-3.7-flash`, default, 2026-09-03 | $0.06 |
| review-grok | Grok 4.6, read only | NO_FINDINGS; `usage.price` names `cursor-grok-4.6`, default, 2026-09-03 | $0.24 |
| review-opus | Opus 5, hard | 2 findings, confidence 6 and 7 | $1.55 |
| fix | Sonnet 5 on the build's thread | 2 fixed; landed on `feat/w11-undispositioned-kinds` | $1.92 |

$5.30 on the mission receipt, 21 minutes wall clock, critical path 1231 s, lead 5 s, landed by
`conductor land` with its own gate, golden, and attest, then the lead's gate: 1476 passed, golden
exit 0.

## What each receipt said

**`usage.price`** (0.69.0, W6). Both estimated lanes carry the table key, source, vintage, and
note that priced them; both Claude lanes carry `price: null` beside `cost_basis: "reported"`, as
designed: the fleet's own figure is not from the table.

**Ledger in flight** (0.70.0). Ninety seconds after launch `running.json` read
`in_flight_dispatches: 1, outstanding_cap_usd: 8.0, worst_case_usd: 8.0` with nothing spent; with
the build settled and two reviewers running it read `in_flight_dispatches: 2, outstanding_cap_usd:
5.5, worst_case_usd: 7.02` against $1.52 spent. The pause receipt carried the same block at zero
in flight and $3.38 spent. These are the figures the Opus reviewer on W6 said existed nowhere a
reader could open; now they do, and the mission's own `budget` block still closes at zero in
flight.

**`settings`** (0.70.0). The build lane's receipt reads `checked: true, modified: []`; every read
lane reads `checked: false`. The check fired on the deny-rule drill the same day and held quiet on
a real build, which is both halves of what it is for.

## What the reviewers found

Opus, both real. First, the spec as written would have counted a NO_FINDINGS review lane as
undispositioned on every mission that recorded dispositions, because no disposition can ever name
a lane that reported nothing; on this repository Gemini's answer is routinely NO_FINDINGS, so the
column would have over-reported on the common case. The fix lane added the guard (a lane with
zero findings is never undispositioned) with a test, the call the lead would have made. Second, the
README sentence introducing the kinds block says "checked in this order, first match wins", and
the pinned `KINDS` order differs from `error_kind`'s check order for `taint`, `settings`, and
`gate_test_surface`, not only the `deliverable` case the parenthetical already excused; the fix
widened the parenthetical and pinned it with a test. Gemini and Grok both wrote NO_FINDINGS, and
both were right about what the diff alone shows.

## What the consumer found in conductor

Nothing that failed. Two observations for the record. The launcher's rule 2 build cap sat under
the forecast p80 for the third mission in a row and was raised by hand each time; a launcher that
takes the larger of the two would remove a step the lead performs every time. And a lane's summary
inside the mission's `lanes/*.json` does not carry `usage.price`, only the run receipt does, so a
reader wanting the basis reads `runs/<id>/result.json`; that is where every other usage detail
lives too, so it is a note, not a gap.
