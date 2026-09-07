# Mission `f8-cross-repo`

- id: `20260907T074942Z-f8-cross-repo`
- ok: **True** (require: all)
- cwd: `<cwd>`
- cost: $0.0000 across 0 tokens
- Cache: 0 read, 0 written, 0 uncached; hit rate -
- duration: 0.7s
- wall: 0.7s (paused 0.0s, gates 0.0s, lanes 0.0s, idle 0.0s)
- Receipt chain: 3 links, head a44952a3cc0f

| lane | attempt | ok | verdict | review/fix | exit | no_op | test_touched | commits | branch | taint | untrusted_output | agent | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| a | script | True |  |  | 0 | False | no | 1 | conductor/20260907T074942Z-script-run | no | no |  | 0.0000 |  | 0 | - | no | 0.0 |
| b | script | True |  |  | 0 | False | no | 1 | conductor/20260907T074942Z-script-run-2 | no | no |  | 0.0000 |  | 0 | - | no | 0.0 |
| c | script | True |  |  | 0 | False | no | 1 | conductor/20260907T074942Z-script-run-3 | no | no |  | 0.0000 |  | 0 | - | no | 0.0 |

## Lane `a`

- tip: `4b2442ff` (clean)
- diff: `<home>/missions/20260907T074942Z-f8-cross-repo/diffs/a.patch`

(no answer; error: none recorded)

## Lane `b`

- tip: `6b0ac85e` (clean)
- diff: `<home>/missions/20260907T074942Z-f8-cross-repo/diffs/b.patch`

(no answer; error: none recorded)

## Lane `c`

- cwd: `<cwd2>`
- tip: `e46bef16` (clean)
- diff: `<home>/missions/20260907T074942Z-f8-cross-repo/diffs/c.patch`

(no answer; error: none recorded)

## Ranking

| rank | lane | ok | verdict | test_touched | gate_exit | patch_bytes | cost_usd |
|---|---|---|---|---|---|---|---|
| 1 | a | True |  | no |  | 125 | 0.0000 |
| 2 | b | True |  | no |  | 125 | 0.0000 |
| 3 | c | True |  | no |  | 125 | 0.0000 |

## Collisions

- `<cwd>:shared.txt`: a, b
