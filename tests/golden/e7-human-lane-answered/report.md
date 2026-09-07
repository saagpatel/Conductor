# Mission `f8-human-lane`

- id: `20260907T074942Z-f8-human-lane`
- resumed: attempt 2; kept build, approve; rerun none
- ok: **True** (require: all, judged on the pipeline's final lanes)
- cwd: `<cwd>`
- cost: $0.0000 across 0 tokens
- Cache: 0 read, 0 written, 0 uncached; hit rate -
- duration: 0.0s
- wall: 13.9s (paused 13.6s, gates 0.0s, lanes 0.0s, idle 0.0s)
- Receipt chain: 2 links, head ff35b4e0c5df

| lane | attempt | ok | verdict | review/fix | exit | no_op | test_touched | commits | branch | taint | untrusted_output | agent | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| build | (kept) | True |  |  | 0 | False | no | 1 | conductor/20260907T074942Z-script-run-4 | no | no |  | 0.0000 |  | 0 | - | no | 0.0 |
| approve | human, answered 2026-09-07T07:49:56.831831Z (44 chars) | True | |  | | | no | | | yes (from human) | no | | | | | - | no | |

## Lane `build`

- stage: build
- tip: `30875458` (clean)
- diff: `<home>/missions/20260907T074942Z-f8-human-lane/diffs/build.patch`

(no answer; error: none recorded)

## Lane `approve`

- needs: build

Yes: one entry for greet, matches the brief.

## Ranking

| rank | lane | ok | verdict | test_touched | gate_exit | patch_bytes | cost_usd |
|---|---|---|---|---|---|---|---|
| 1 | approve | True |  | no |  |  | 0.0000 |
