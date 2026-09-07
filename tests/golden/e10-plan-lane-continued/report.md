# Mission `f8-plan-lane`

- id: `20260907T074956Z-f8-plan-lane`
- resumed: attempt 2; kept plan; rerun none
- ok: **True** (require: all)
- cwd: `<cwd>`
- cost: $0.0870 across 39481 tokens
- Cache: 19388 read, 19814 written, 4 uncached; hit rate 49.5%
- duration: 0.5s
- wall: 296.7s (paused 290.5s, gates 0.0s, lanes 5.2s, idle 0.0s)
- Receipt chain: 2 links, head 0ede1853c229

| lane | attempt | ok | verdict | review/fix | exit | no_op | test_touched | commits | branch | taint | untrusted_output | agent | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| plan | (kept) | True |  |  | 0 | False | no | 0 | conductor/20260907T074956Z-claude-write-the-file-child-json-in-the | no | no |  | 0.0870 | 39481 | 1 | 19388/19392 (100%) | no | 5.2 |

**Note**: child '20260907T075453Z-f8-plan-child' spent $0.0000 (rolled into this budget)

**Note**: lane 'plan': cap $1.00 is under the $3.53 80th percentile of 31 anthropic unstaged runs

## Lane `plan`

- cache reads: 19388/19392 input tokens
- tip: `94d70263` (with uncommitted work)
- claude/sonnet: uncommitted work kept at `<home>/worktrees/20260907T074956Z-claude-write-the-file-child-json-in-the`
- diff: `<home>/missions/20260907T074956Z-f8-plan-lane/diffs/plan.patch`

written

## Ranking

| rank | lane | ok | verdict | test_touched | gate_exit | patch_bytes | cost_usd |
|---|---|---|---|---|---|---|---|
| 1 | plan | True |  | no |  | 460 | 0.0870 |

## Children

| lane | mission_id | ok | cost_usd | state | report |
|---|---|---|---|---|---|
| plan | 20260907T075453Z-f8-plan-child | True | $0.0000 | finished | <home>/missions/20260907T075453Z-f8-plan-child/report.md |
