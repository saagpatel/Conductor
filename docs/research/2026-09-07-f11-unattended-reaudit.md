# F11: the first unattended mission, with notifications (2026-09-07)

E9's `--unattended` and E12's `notify` had never run on a real mission. The mission was
the core-guard audit again, cold, from three vendors, on the guard as fixed earlier the same
day (`2026-09-07-consumer-core-guard-fix.md`), launched with `--unattended` and left alone.

## What ran

| lane | model | cap | spent | wall | answer |
|---|---|---|---|---|---|
| audit-claude | Sonnet 5, hard | $3.00 | $0.98 | 10 min | 3 findings, each confirmed by running the guard, plus one low-confidence note |
| audit-gemini | Gemini 3.7 Flash | $1.00 | $0.32 | 3 min | 10 findings |
| audit-grok | Grok 4.6 (ran the gate, probed live) | $2.00 | $0.43 | 8 min | 14 findings |

$1.73 of a $7.50 mission cap; 10.2 minutes of wall clock for 20 minutes of lane time; every
lane green on bytes; attested. `result.json` carries `unattended: true` and the ceiling it saw
at start (`per_hour_usd: 10.0`, `per_day_usd: null`, `hour_usd: 4.09`, `day_usd: 284.20`).

**Notifications.** `notify.command` was `scripts/notify-hub.py` (new, stdlib): it appends the
raw event to `<CONDUCTOR_HOME>/notify-events.jsonl` and hands a hub event to the harness's own
`notification-hub-producer.py`, which owns the producer token and a durable outbox. The one
`end` event was recorded on the result as `{"event": "end", "ok": true, "exit_code": 0}`, the
local log holds it, and the producer's outbox shows the row `accepted` with receipt `http:201`
on the first attempt. No pause and no breaker fired, so those two paths are still unexercised
live; both go through the same script and the same producer.

**bridge-db** was named in the roadmap as the second notification target. It has no shell
entry point for `log_activity` (the runtime is an MCP server; the only CLI writes are
session-boundary and operator ceremonies), and the MCP client in the lead's session had no
principal token bound, so nothing reached bridge-db. Recorded here, not worked around: the
bridge is separately governed.

## What the ceiling did

The mission file set `per_day_usd: null` on purpose. The default day bound is $25 and the
rolling 24 hours already held about $284 of attended work (this sitting and the one before
it), so the default would have refused the launch outright. The hour bound stayed at its
default $10 and saw $4.09. For a genuinely overnight launch the day bound is the right one
to keep; for "launch at the end of a sitting" it measures the sitting, not the night.

## What the re-audit found

The verdict parser marked all three answers `unparsed`: these prompts end with the no-quota
template ("reply exactly NO_FINDINGS") but not F1's `FINDINGS: N` tail, so the precision table
gets nothing from them. A consumer prompt should carry the Shape A review tail.

Agreement on the fixed guard, two or more vendors each, confirmed by running it:

- `bash -c 'rm -rf ~'`, `sh -c`, `eval`, and the same behind `timeout` or `sudo`: the payload
  is an argument, never a file, so `script_bodies` has nothing to read (Sonnet, Grok; Grok
  alone reported it on the first audit).
- The last fix's "an interpreter word anywhere in the segment" made `grep -n bash file` read
  `file` and deny on its prose; both Sonnet and Gemini reproduced it on the guard's own test
  file. The earlier spec had said "no launcher list"; that instruction produced this.
- `#!/usr/bin/env -S bash` shebangs are not read (Gemini, Grok).
- Same-line assignment resolution misses `export V=rm`, `A=rm; B=$A`, and `V=r$1m` composed
  with the empty-expansion fold (Grok; Gemini named the last as a gate gap).
- `git reflog expire --expire now` as two tokens (Gemini, on both audits).

Single-vendor, recorded for the operator: a closed backtick verb (`` `rm` -rf ~ ``),
`PIPED_TO_SHELL` without `source` and `.`, `/private/etc` on macOS, `cat drop.sql | psql`,
`echo path | xargs bash`, a newline instead of `;` before the assignment, and Homebrew under
`/usr/local` plus `/Library/Caches` and `/Applications` now denied by the descendant-root rule
(the operator's policy call from the first fix). The manifest's gate count (86 / 84) is stale.

The agreed items went to a third Shape A mission on the harness main: Sonnet built all five
at $1.66 against a $9 cap (gate 142 catch / 130 quiet), Gemini and Grok both answered
NO_FINDINGS, the fix lane was stopped unspent, $2.08 in all. The lead's probe of seventeen
shapes against the old and new guard matched expectations line for line, including
`grep -n bash hooks/tests/core-guard-test.py` allowed again. The result is
`feat/core-guard-reaudit-fixes` in the harness repository (the fix plus a one-line manifest
count update), gated green in a fresh worktree, the operator's to merge.

## What the receipt says about conductor

- `--unattended` with three read lanes ran, ended, notified, and left a complete receipt with
  nobody watching. The shape works; the pause and breaker notifications are still unproven live.
- The day ceiling and "end of a sitting" launches disagree; a per-mission override is the
  right tool and it exists.
- The notify script is the harness's producer plus a local log, not a second delivery path;
  when the hub is down the producer's outbox retries, and the local log is the morning read.
- Each cold audit of this guard has cost under $2 and found agreed, confirmed shapes; the fix
  missions cost $7 to $9 each. A guard is a good consumer: small, self-testing, adversarial.
