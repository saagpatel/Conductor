# Mission `c5-error-kinds-review`

- id: `20260905T182328Z-c5-error-kinds-review`
- ok: **True** (require: all, judged on the pipeline's final lanes)
- cwd: `<cwd>`
- cost: $1.7208 across 4003626 tokens
- Cache: 3882360 read, 77019 written, 50 uncached; hit rate 98.1%
- duration: 1169.9s
- Receipt chain: 3 links, head cae08b9bc2e3

| lane | attempt | ok | verdict | exit | no_op | test_touched | commits | branch | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| review-gemini | antigravity/gemini-3.7-flash | True |  | 0 | True | no | 0 |  | 0.0811 | 513762 | 26 | 502177/502177 (100%) | no | 37.3 |
| review-grok | cursor/grok-4.6 | True |  | 0 | True | no | 0 |  | 0.7143 | 1251201 | 58 | 1235072/1235072 (100%) | no | 286.4 |
| fix | claude/sonnet | True |  | 0 | False | yes (1 files: tests/test_errors.py) | 1 | feat/error-kinds-fix | 0.9254 | 2238663 | 25 | 2145111/2145161 (100%) | no | 338.6 |

## Lane `review-gemini`

- stage: review
- tip: `f2f54883` (clean)

NO_FINDINGS

## Lane `review-grok`

- stage: review
- tip: `f2f54883` (clean)

I'll review the C5 change against the spec on the bytes in this worktree, then run the named gate with pytest scratch under `$TMPDIR`.

Context-mode was blocked, so I’ll read the implementation files and `Result.failure()` directly and compare them to the spec.

The breaker/cap interaction looks like a real misclassification; I’ll confirm against a live-shaped Result and run the spec’s gate with `--basetemp` under `$TMPDIR`.

The spec’s cap-before-breaker order collides with `Budget.settle(killed=breaker)`, so a capped stall/loop kill is labeled `cap`. I’m recording that as the review finding.

# C5 review (read-only)

The change implements items 1–5 in [`src/conductor/errors.py`](src/conductor/errors.py), [`src/conductor/mission.py`](src/conductor/mission.py), [`src/conductor/runner.py`](src/conductor/runner.py), [`src/conductor/cli.py`](src/conductor/cli.py), [`README.md`](README.md), and [`tests/test_errors.py`](tests/test_errors.py). Ruff passed. Full pytest could not finish here (`git init` blocked in the sandbox); the hand-built `error_kind` tests in `tests/test_errors.py` passed.

## Findings

1. **Capped breaker kill classified as `cap`** — [`src/conductor/errors.py`](src/conductor/errors.py) lines 151–155 check `budget.exceeded` before `breaker.tripped`. [`src/conductor/runner.py`](src/conductor/runner.py) lines 1355–1357 pass `killed=capped or breaker_reason is not None` into [`src/conductor/budget.py`](src/conductor/budget.py) lines 75–78, which sets `exceeded` for every breaker kill when a cap is present. Live proof: [`tests/test_breakers.py`](tests/test_breakers.py) lines 349–362 (`cap_usd=10.0`, stall trip, `budget["exceeded"] is True`). Hand-built breaker fixtures omit `budget`, so they miss this. Consequence: `fallback.on: ["breaker"]` is skipped and `retry.kinds: ["cap"]` may retry a looping/stalled run. Confidence 9.

2. **`conductor runs` does not reclassify old receipts** — [`src/conductor/cli.py`](src/conductor/cli.py) line 486 uses `data.get("kind")` from JSON. Spec/README say kind is computed, not stored, so a pre-C5 receipt still classifies on read-back. Those files have no `kind` key, so the row is `null` even when `ok` is false. Confidence 7.

## Spec items that are present

- `KINDS` order, `error_kind`, `Result.to_dict`/`summary` `kind`, `fallback.on` skip note, retry with `retry_of`/`backoff_s * 2**i` and pollable sleep, `MissionResult.errors`, report line, `conductor missions` `errors`, README + `tests/test_errors.py`.
- `on` is on `_FALLBACK_ENTRY_KEYS` rather than `_FALLBACK_KEYS` so cascade still cannot set `on` (matches “cascade never sets `on`”).

## Lane `fix`

- stage: fix
- needs: review-gemini, review-grok
- cache reads: 2145111/2145161 input tokens
- tip: `706add5f` (clean)
- diff: `<home>/missions/20260905T182328Z-c5-error-kinds-review/diffs/fix.patch`

Gate passes cleanly (ruff clean, full suite green).

## Summary

**Gemini review:** `NO_FINDINGS` — nothing to act on.

**Grok review — two findings, both confirmed and fixed:**

1. **Capped breaker kill misclassified as `cap`.** `budget.settle()` (`budget.py`) sets `exceeded=True` for *any* kill — breaker or cap watcher alike (`killed=capped or breaker_reason is not None` in `runner.py`) — so a stall/loop breaker kill under a cap nowhere near hit still carried `budget["exceeded"] is True`, and `error_kind`'s cap-before-breaker check turned every such kill into `cap`. Live evidence already existed in `tests/test_breakers.py` (`cap_usd=10.0`, actual spend far under it, `budget["exceeded"] is True`). Added `test_a_breaker_kill_under_a_generous_cap_is_breaker_not_cap` (failed on the current tree: got `cap`, expected `breaker`) and a companion `test_a_genuine_cap_kill_is_still_cap_even_with_a_breaker_dict_present` (already passed, to pin that the fix doesn't overcorrect). Fixed with a new `_capped()` helper in `src/conductor/errors.py` that only calls it `cap` when there's independent evidence — the native `error_max_budget_usd` flag, an observed spend that actually cleared the cap, or no breaker trip to blame it on instead.

2. **`conductor runs` doesn't reclassify pre-C5 receipts.** The spec requires `kind` to be "computed, not stored, so an old receipt read back still classifies," but `cmd_runs` in `cli.py` just did `data.get("kind")` off the raw JSON, so a receipt written before this field existed (no `kind` key on disk) showed `null` even when `ok: false`. Added `test_conductor_runs_recomputes_kind_for_a_pre_c5_receipt` (deletes the `kind` key from a real receipt, failed on current tree: got `None`, expected `exit`). Fixed by adding `_kind_from_legacy_receipt()` in `cli.py`, which rebuilds a `Result` from the receipt's known dataclass fields and reruns `error_kind` when the stored `kind` key is absent.

Both fixes are scoped to what the failing tests proved; no other code touched. Existing test suite (`test_errors.py`'s other 44 cases plus the full repo suite) stays green. Ruff clean. Not committing — leaving that to the harness.

## Ranking

| rank | lane | ok | verdict | test_touched | gate_exit | patch_bytes | cost_usd |
|---|---|---|---|---|---|---|---|
| 1 | fix | True |  | yes (1 files: tests/test_errors.py) | 0 | 7006 | 0.9254 |
