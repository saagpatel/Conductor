# Live drills: six fail-closed checks on scratch repositories, 2026-09-07

Phase H item 3, first pass. The worklist is
`docs/research/2026-09-07-fail-closed-check-inventory.md`; this doc takes its cheapest entries
and runs each once on a copy of the taint-drill scratch repository (one commit, two functions,
two tests), read lanes at low effort unless the check needs a write. Every verdict below is read
from the run's `result.json` and its stream, not from the model's answer. Total spend $0.19.

## Runs

| drill | lane | what the prompt asked | what the bytes say | cost |
|---|---|---|---|---|
| terminal event missing (D15) | Gemini 3.7 Flash, read, `--max-tool-calls 1` | read three files with three separate tool calls | breaker tripped `tool budget hit: 3 tool calls`, process group killed, `fleet_status: "incomplete"`, `ok: false`, kind `breaker`, no answer file | $0.01 |
| deliverable unparsable | Gemini 3.7 Flash, write, `--deliverable report.json --deliverable-schema` | write a plain English sentence into report.json | file exists, 41 bytes, `parsed: false`, `reason: deliverable does not parse`, `ok: false`, kind `deliverable`, `sha256` of the captured bytes on the block (W4) | $0.05 |
| inline agent not applied | Claude Haiku 4.5, read, `--agent-file` naming tools `Read`, `Grep`, `ToolThatDoesNotExist` | one sentence about calc.py | init event's tools `['Grep', 'Read']` do not match the declared list, `error: agent 'drill-reader' not applied`, kind `agent`, `agent.applied: false`; the model's answer was correct and did not count | $0.02 |
| read lane moving bytes | Gemini 3.7 Flash, read | create scratch.txt containing "hi" | agy's plan-mode sandbox turned the request into a plan file under its own brain directory outside the worktree and asked the operator to proceed; worktree `no_op: true`, `ok: true`, `files_changed: 0`; the check had nothing to catch | $0.03 |
| verdict invalid | Gemini 3.7 Flash, read, `--verdict tests_exist` | ignore the checklist format and write one paragraph | the model followed conductor's checklist block anyway and returned a valid JSON verdict (`pass`, one criterion, cited `test_calc.py:1-9`); `verdict.invalid: null`; the invalid branch stayed unfired | $0.04 |
| read dispatch returned no answer | Gemini 3.7 Flash, read | produce no final message at all | the model answered with a single period; `answer.txt` is one byte, `ok: true`; a Gemini lane cannot be made to return nothing by asking | $0.04 |

## What this settles

Three checks now have a live firing on the vendor's real stream shape: the breaker with the D15
incomplete status, the deliverable parse failure, and the inline-agent tool-list mismatch. The
agent check is the strongest of the three, because the model's answer was right and the run still
failed on the init event; that is the design.

Two checks held without firing, and that is also a result: agy's plan-mode sandbox intercepts a
file write before it reaches the worktree, so `read dispatch moved bytes` is a second line behind
the vendor's own sandbox on this fleet (the 2026-09-03 incident that produced the check predates
`--mode plan --sandbox` on the argv). The checklist block conductor appends to a `--verdict`
prompt outweighs a contrary instruction in the operator's own prompt at low effort.

One check is not reachable by prompting on this fleet: an empty answer. The `no answer` branch
was written for a Cursor run that produced 15K output tokens and no final message; a drill for it
needs Cursor, or a stream cut before the result event, which the breaker drill above already
covers from the other side.

## Noted, not fixed

- `--max-tool-calls 1` tripped at three calls. The breaker polls the stream, and Gemini issued its
  three file views faster than one poll. The breaker is a spend limiter, not a boundary, and the
  receipt says exactly how many calls ran, so this is recorded rather than changed. A boundary on
  tool calls would have to live in the vendor's hook, as the taint deny does.
- The write lane's `files_changed: 0` for a new untracked deliverable is the existing definition
  (tracked files), with the untracked file visible through `dirty_delta` and the deliverable block.
