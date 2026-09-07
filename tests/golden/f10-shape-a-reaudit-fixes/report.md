# Mission `core-guard-reaudit-fixes`

- id: `20260907T071119Z-core-guard-reaudit-fixes`
- resumed: attempt 2; kept build, review-gemini, review-grok; rerun fix
- ok: **False** (require: all, judged on the pipeline's final lanes)
- cwd: `<cwd>`
- cost: $2.0766 across 3926726 tokens
- Cache: 3709451 read, 124145 written, 50 uncached; hit rate 96.8%
- duration: 0.0s
- wall: 987.3s (paused 33.3s, gates 7.8s, lanes 995.9s, idle 0.0s)
- Receipt chain: 5 links, head e9c556f4f580

| lane | attempt | ok | verdict | review/fix | exit | no_op | test_touched | commits | branch | taint | untrusted_output | agent | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| build | (kept) | True |  |  | 0 | False | yes (1 files: hooks/tests/core-guard-test.py) | 1 | conductor/20260907T071119Z-claude-you-are-one-lane-of-a-conductor | no | no |  | 1.6611 | 3191136 | 25 | 3011003/3011053 (100%) | no | 591.4 |
| review-gemini | (kept) | True |  | NO_FINDINGS | 0 | True | no | 0 |  | no | no |  | 0.0905 | 287899 | 20 | 269136/269136 (100%) | no | 52.1 |
| review-grok | (kept) | True |  | NO_FINDINGS | 0 | True | no | 0 |  | no | no |  | 0.3249 | 447691 | 31 | 429312/429312 (100%) | no | 352.4 |
| fix | (skipped) | False | |  | | | no | | | no | no | | | | | - | no | |

## Lane `build`

- stage: build
- cache reads: 3011003/3011053 input tokens
- tip: `b2eac2cd` (clean)
- diff: `<home>/missions/20260907T071119Z-core-guard-reaudit-fixes/diffs/build.patch`

All green: `python3 hooks/tests/core-guard-test.py` exits 0 with 142 catch / 130 quiet, zero false blocks, zero misses. No existing case was weakened, deleted, or reworded — none contradicted an item.

## Summary — CG-15 group added

| Item | Fix location | Lines changed | New cases |
|---|---|---|---|
| **1** — `bash -c`/`sh -c`/`zsh -c`/`eval` bodies | `script_bodies` (`hooks/core-guard.py:684-753`): unified interpreter scan now detects `eval` and a `-c`/`-lc`/`-xc` cluster after an interpreter, treating the next bare token as literal body text instead of a filename; `-n` still short-circuits to no read. | ~40 | 5 CATCH, 4 QUIET |
| **2** — interpreter word in argument position | Same `script_bodies` scan (`hooks/core-guard.py:684-706`) plus new `WRAPPERS` set (`hooks/core-guard.py:578-593`): an interpreter now only counts as the command word itself, or reached through a chain of assignments/flags/numbers/known wrappers (and flag arguments) — never through an arbitrary command like `grep`/`diff`/`echo`. | ~35 | 2 CATCH, 5 QUIET |
| **3** — `env -S bash` shebang | `_shebang_shell` (`hooks/core-guard.py:623-633`): skips `env`'s own dash-flags before reading the interpreter word. | ~8 | 1 CATCH, 1 QUIET |
| **4** — same-line assignment resolver gaps | `ASSIGN` regex now accepts an optional `export/declare/typeset/readonly/local` prefix (`hooks/core-guard.py:437-446`); `resolve_same_line_assigns` now iterates up to 4 passes and treats `=` as a left boundary so chained assignments unwind (`hooks/core-guard.py:470-529`); `command_views` now also resolves the *folded* text, composing the two views (`hooks/core-guard.py:531-563`). | ~70 | 3 CATCH, 3 QUIET |
| **5** — `git reflog expire --expire now` | `check_history` (`hooks/core-guard.py:896-911`): also matches the two-token `--expire now`/`--expire 0` spelling, exact-matched so `--expire-unreachable` isn't caught. | ~12 | 2 CATCH, 1 QUIET |

Test file (`hooks/tests/core-guard-test.py`): added `REPO_ROOT`, two new shebang fixtures (`ENV_S_EVIL`, `ENV_S_PY`), and the CG-15 CATCH/QUIET blocks (13 CATCH + 14 QUIET; one QUIET item-5 control already existed under CG-9 and was reused, not duplicated).

Only `hooks/core-guard.py` and `hooks/tests/core-guard-test.py` were touched.

## Lane `review-gemini`

- stage: review
- needs: build
- built on: lane build at `b2eac2cd`
- tip: `b2eac2cd` (clean)
- antigravity/gemini-3.7-flash: gate skipped (read lane)

### Review of CG-15 Changes Against Spec

All five spec items and their corresponding CATCH / QUIET test cases in group CG-15 have been reviewed against the implementation in [`hooks/core-guard.py`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py) and [`hooks/tests/core-guard-test.py`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py).

1. **Item 1 (`bash -c`, `sh -c`, `zsh -c`, `eval`)**:
   - [`hooks/core-guard.py:698-724`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L698-L724) correctly recognizes `eval` and extracts joined argument tokens as a body script.
   - [`hooks/core-guard.py:726-754`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L726-L754) detects `-c` and clustered flags (e.g. `-lc`, `-xc`) on interpreters, returning the next bare token as body text while preserving the `-n` syntax check override.
   - Test cases are added in [`hooks/tests/core-guard-test.py:713-725`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L713-L725) (CATCH) and [`hooks/tests/core-guard-test.py:1215-1222`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L1215-L1222) (QUIET).

2. **Item 2 (Scoping interpreter word detection to command / known wrappers)**:
   - [`hooks/core-guard.py:582-594`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L582-L594) defines the `WRAPPERS` set containing all specified wrappers unioned with `LAUNCH_PREFIX`.
   - [`hooks/core-guard.py:688-713`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L688-L713) restricts interpreter search to valid wrapper prefixes, env assignments, flags, and flag arguments before the interpreter, preventing commands like `grep`, `diff`, or `echo` from reading trailing arguments.
   - Test cases are added in [`hooks/tests/core-guard-test.py:729-738`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L729-L738) (CATCH) and [`hooks/tests/core-guard-test.py:1225-1233`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L1225-L1233) (QUIET).

3. **Item 3 (`#!/usr/bin/env -S bash` shebang handling)**:
   - [`hooks/core-guard.py:626-634`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L626-L634) advances past flag tokens following `env` in `_shebang_shell` to identify the shell interpreter.
   - Test fixtures and cases are added in [`hooks/tests/core-guard-test.py:77-82`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L77-L82), [`hooks/tests/core-guard-test.py:742-746`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L742-L746) (CATCH), and [`hooks/tests/core-guard-test.py:1235`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py#L1235) (QUIET).

4. **Item 4 (Same-line assignment resolution: prefixes, chaining, and composition with fold)**:
   - [`hooks/core-guard.py:444-448`](file://<home>/worktrees/20260907T072120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L444-L448) updates `ASSIGN` to match optional declaration keywords (`export`, `declare`, `typeset`, `readonly`, `local`) and optional flags.
   - [`hook
[... truncated, 1670 more chars]

## Lane `review-grok`

- stage: review
- needs: build
- built on: lane build at `b2eac2cd`
- tip: `b2eac2cd` (clean)
- cursor/grok-4.6: gate skipped (read lane)

I'll review the applied guard and tests against the spec, then run the named gate without changing anything.

I'll index the guard and gate, then inspect the CG-15 paths and run the suite.

MCP was blocked, so I'll read the guard and tests directly and run the gate with `--basetemp` under `$TMPDIR`.

Gate `python3 hooks/tests/core-guard-test.py --basetemp $TMPDIR/...` exited 0: 142 catch / 130 quiet, including every CG-15 case named in the spec. Existing cases were only appended to, not reworded. All five items are present in [`hooks/core-guard.py`](hooks/core-guard.py) and [`hooks/tests/core-guard-test.py`](hooks/tests/core-guard-test.py); bodies still go only to `rm-outside-repo` ([`hooks/core-guard.py` 1322–1344](hooks/core-guard.py)).

NO_FINDINGS

## Lane `fix`

- stage: fix
- needs: review-gemini, review-grok, build
- built on: lane build at ``
Skipped: paused: operator answered stop

(skipped: paused: operator answered stop)
