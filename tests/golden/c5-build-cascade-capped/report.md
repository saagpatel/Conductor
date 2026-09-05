# Mission `c5-error-kinds`

- id: `20260905T171417Z-c5-error-kinds`
- ok: **False** (require: all, judged on the pipeline's final lanes)
- cwd: `<cwd>`
- cost: $13.0642 across 44578286 tokens
- Cache: 43834731 read, 556989 written, 376 uncached; hit rate 98.7%
- duration: 3923.1s
- Receipt chain: 4 links, head 8bce52980915
- Cascade: 0 of 1 lanes passed on claude/sonnet; 1 escalated ($4.0152 on the cheap attempts, $9.0490 after)

| lane | attempt | ok | verdict | exit | no_op | test_touched | commits | branch | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| build | claude/sonnet | False |  | 1 | False | yes (1 files: tests/test_errors.py) | 0 | conductor/20260905T171417Z-claude-you-are-one-lane-of-a-conductor | 4.0152 | 12499984 | 65 | 12223345/12223467 (100%) | no | 1165.2 |
| build | claude/sonnet | False |  | 1 | False | yes (1 files: tests/test_errors.py) | 0 | conductor/20260905T173342Z-claude-you-are-one-lane-of-a-conductor | 9.0490 | 32078302 | 130 | 31611386/31611640 (100%) | no | 2466.5 |
| review-gemini | (skipped) | False | | | | no | | | | | | - | no | |
| review-grok | (skipped) | False | | | | no | | | | | | - | no | |
| fix | (skipped) | False | | | | no | | | | | | - | no | |

## Lane `build`

- stage: build
- cache reads: 43834731/43835107 input tokens
- tip: `e0d0b0a0` (with uncommitted work)
- claude/sonnet: Reached maximum budget ($4)
- claude/sonnet: uncommitted work kept at `<home>/worktrees/20260905T171417Z-claude-you-are-one-lane-of-a-conductor`
- claude/sonnet: Reached maximum budget ($9)
- claude/sonnet: uncommitted work kept at `<home>/worktrees/20260905T173342Z-claude-you-are-one-lane-of-a-conductor`
- diff: `<home>/missions/20260905T171417Z-c5-error-kinds/diffs/build.patch`

(no answer; error: Reached maximum budget ($9))

## Lane `review-gemini`

- stage: review
- needs: build
- built on: lane build at ``
Skipped: needs build, which was not ok

(skipped: needs build, which was not ok)

## Lane `review-grok`

- stage: review
- needs: build
- built on: lane build at ``
Skipped: needs build, which was not ok

(skipped: needs build, which was not ok)

## Lane `fix`

- stage: fix
- needs: review-gemini, review-grok, build
- built on: lane build at ``
Skipped: needs review-gemini, review-grok, build, which was not ok

(skipped: needs review-gemini, review-grok, build, which was not ok)
