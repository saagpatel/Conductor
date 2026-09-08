# What conductor's own receipt corpus says about cursor and antigravity stream shapes

Written 2026-09-08, wave 16. Two findings filed against `outputs.py` in wave 15 rested on claims
about what a vendor CLI emits. Neither reviewer offered evidence for the shape it assumed, and
neither claim was shipped. This note settles both against bytes conductor already had on disk, so
they are not filed a fourth time.

## Method

Every run directory under `~/.conductor/runs` that holds both a `stdout.log` and a `result.json`
was scanned, each line parsed as JSON where it parses, grouped by the `fleet` its own receipt
records. No new dispatch was run and nothing was spent: this is conductor's own accumulated
record of what these CLIs actually printed.

Corpus at the time of writing: **145 cursor runs and 124 antigravity runs**, spanning
2026-09-03 to 2026-09-08 and covering read and write lanes, cap kills, timeouts, and clean
completions.

## Claim 1: `_parse_cursor` ignores an error event before a clean result envelope

The filed claim (confidence 8): `_parse_cursor` (`outputs.py` ~375-381) returns as soon as the
last event looks like an envelope, so the loop below it that scans backwards for
`{"type": "error"}` or `is_error: true` is never reached. A `cursor-agent` stream that emitted an
error event mid-run and then closed with a `result` envelope carrying no `is_error: true` would
therefore parse as a success.

The code does read that way. The premise does not hold:

> **0 of 145 cursor streams contain an event with `type: "error"` or `is_error: true` anywhere in
> the stream.** 141 of 145 end on a `result` event; the other 4 end on `thinking` (2) or
> `tool_call` (1) or hold no JSON at all (1), and those take the
> `cursor stream ended without a result event` path, which is correct.

The backwards-scanning loop exists for the shape where the stream ends *without* an envelope, and
that is the shape the corpus contains. Whether `cursor-agent` can emit a mid-stream error event
followed by a clean envelope is unobserved; the finding is not actionable without a live probe
that forces one, and there is no known way to force it that does not also set `is_error`.

## Claim 2: `_parse_antigravity` misreads a successful run as INCOMPLETE

The filed claim (confidence 9): `_parse_antigravity` (`outputs.py` ~338-346) inspects only
`_last_json_object(text)`. Unlike `_parse_claude`, which scans every event for the terminal one,
it takes the last JSON line and, if that line's `event` is anything other than `result`, returns
`status=INCOMPLETE` with an empty answer. A trailing lifecycle event printed after the result
event would discard a completed answer and fail a successful run.

The code does read that way. The premise does not hold:

> **0 of 124 antigravity streams have any event after the result event.** 112 end on `result`;
> 9 end on `step_update`, which is the genuinely-truncated shape `INCOMPLETE` is for and which the
> parser reads correctly.

The remaining 3 end on a JSON object with no `event` key at all. Those are from 2026-09-03 and are
single-envelope `--output-format json` runs from before conductor moved this fleet to
`stream-json`; `_parse_antigravity` falls through to `_parse_envelope` and reads them correctly,
which is the documented "a lone envelope still parses" behaviour, not a defect.

## What this does and does not establish

It establishes that neither trigger has occurred in conductor's own history across 269 runs, so
neither is a live defect and neither should be filed again without new evidence. It does not
establish that either CLI can never emit the shape: absence over 269 runs is strong, not total.

The standing rule that produced this note: **a claim about what a vendor emits is a claim about
bytes, and this repository has 493 stdout logs on disk.** Mine the corpus before proposing a
parser change, and before paying for a probe.
