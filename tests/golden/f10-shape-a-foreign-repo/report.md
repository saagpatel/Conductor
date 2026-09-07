# Mission `core-guard-audit-fixes`

- id: `20260907T055537Z-core-guard-audit-fixes`
- resumed: attempt 2; kept build, review-gemini, review-grok; rerun fix
- ok: **True** (require: all, judged on the pipeline's final lanes)
- cwd: `<cwd>`
- cost: $6.7651 across 16407871 tokens
- Cache: 15763857 read, 443955 written, 166 uncached; hit rate 97.3%
- duration: 220.0s
- wall: 2401.5s (paused 44.8s, gates 14.0s, lanes 2380.3s, idle 0.0s)
- Receipt chain: 5 links, head e6d2705a426c

| lane | attempt | ok | verdict | review/fix | exit | no_op | test_touched | commits | branch | taint | untrusted_output | agent | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| build | (kept) | True |  |  | 0 | False | yes (1 files: hooks/tests/core-guard-test.py) | 1 | conductor/20260907T055537Z-claude-you-are-one-lane-of-a-conductor | no | no |  | 4.4671 | 11860282 | 68 | 11519307/11519443 (100%) | no | 1534.5 |
| review-gemini | (kept) | True |  | NO_FINDINGS | 0 | True | no | 0 |  | no | no |  | 0.1192 | 248119 | 10 | 220759/220759 (100%) | no | 48.3 |
| review-grok | (kept) | True |  | 1 findings | 0 | True | no | 0 |  | no | no |  | 0.4243 | 523505 | 29 | 493952/493952 (100%) | no | 592.1 |
| fix | claude/sonnet | True |  | 1 fixed | 0 | False | yes (1 files: hooks/tests/core-guard-test.py) | 1 | feat/core-guard-audit-fixes | no | no |  | 1.7545 | 3775965 | 14 | 3529839/3529869 (100%) | yes | 205.4 |

## Lane `build`

- stage: build
- cache reads: 11519307/11519443 input tokens
- tip: `c0d3cf5d` (clean)
- diff: `<home>/missions/20260907T055537Z-core-guard-audit-fixes/diffs/build.patch`

No new imports added (still stdlib-only, one file). Everything is green: 120 catch / 108 quiet, all 86 original cases preserved unmodified in behavior, all 34 new CG-14 cases passing.

## Summary

All 13 items implemented in `hooks/core-guard.py`, with regression coverage added to `hooks/tests/core-guard-test.py` (group **CG-14**). Gate: `python3 hooks/tests/core-guard-test.py` → **120 catch / 108 quiet, ALL GREEN**.

**Items 1–12** (core-guard.py):
1. Same-line variable resolution — new `resolve_same_line_assigns` + third view in `command_views`.
2. Direct-path script execution — `script_bodies` rewritten with `_shebang_shell`, reads a script run with no interpreter word if its shebang names a shell.
3. Bundled/lease force-push — `check_history`'s `hit` search extended for `-[A-Za-z]+` bundles containing `f` and `--flag=value` splitting.
4. Case-insensitive verbs — `verb()` casefolds when `CASE_INSENSITIVE`; also fixed `check_exfil`'s raw `os.path.basename` comparison to use `verb()`.
5. Command/process substitution — `verb()` now strips `$(`, `<(`, `>(`, backtick found anywhere in the token (not just at position 0); also had to fix `SPLIT_REDIR` (item 9) which was eating `<(`/`>(` tokens before `verb()` saw them.
6. Heredoc/pipe/stdin/file SQL — `check_db` gained `raw_cmd`/`cwd` params, new `heredoc_bodies_data`, `_db_file_ref`, `_read_bounded`.
7. Credential dirs without trailing slash — `CRED`'s four directory alternatives now match at a boundary (`/`, whitespace, quote, or end).
8. System-root subdirectories — `SYSTEM_ROOTS` split into `SYSTEM_DESCENDANT_ROOTS`/`SYSTEM_EXACT_ROOTS`; new `_under_system_root` in `_judge_target`.
9. Redirect glued to target — `SPLIT_REDIR` splits tokens at the operator, keeping a real pre-operator argument.
10. Long-option letter soup — new shared `_rm_flags` helper used by `check_rm` and `check_xargs`.
11. Any `://host` read as DB host — new `_uri_hosts`/`DB_URI_SCHEMES`, host only taken from a token beginning with an accepted scheme.
12. Credential-shaped payload as upload — new `_upload_names_a_file`/`UPLOAD_ALWAYS_FILE`, requires the flag's own argument to name a file.

**Item 13** (core-guard-test.py): every CATCH tuple now carries its expected rule via a `C(name, cmd, rule, cwd=None)` helper; `main()` asserts `[<rule>]` appears in the deny reason and prints per-rule counts. All 86 pre-existing cases retagged with their actual firing rule (verified by running each). Nothing weakened, deleted, or reworded.

Nothing was left undone — all 12 items plus the gate restructuring are complete and verified.

## Lane `review-gemini`

- stage: review
- needs: build
- built on: lane build at `c0d3cf5d`
- tip: `c0d3cf5d` (clean)
- antigravity/gemini-3.7-flash: gate skipped (read lane)

I have reviewed the changes in [`hooks/core-guard.py`](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py) and [`hooks/tests/core-guard-test.py`](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/tests/core-guard-test.py) against all 13 items in the specification:

1. **Same-line variable resolution**: [hooks/core-guard.py:441-496](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L441-L496) implements `resolve_same_line_assigns` and adds the resolved command view to `command_views`.
2. **Direct script invocation by path**: [hooks/core-guard.py:523-635](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L523-L635) introduces `_shebang_shell` and scans direct script paths containing `/` when the shebang specifies an interpreter in `INTERPRETERS`.
3. **Bundled short options and force leases**: [hooks/core-guard.py:752-770](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L752-L770) inspects short flag bundles for `f` and handles `=` in long flags such as `--force-with-lease=main`.
4. **Capitalised verbs on case-insensitive filesystem**: [hooks/core-guard.py:260-270](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L260-L270) casefolds verb basenames when `CASE_INSENSITIVE` is true.
5. **Command/process substitution attached to verbs**: [hooks/core-guard.py:257-266](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L257-L266) strips leading `$(` , `<(`, `>(`, and `` ` `` in `verb()`.
6. **Destructive SQL via heredoc, pipe, stdin, or file**: [hooks/core-guard.py:1017-1096](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L1017-L1096) inspects heredoc bodies, upstream pipeline segments, and bounded files referenced by `-f`, `--file`, or `<`.
7. **Credential directories without trailing slash**: [hooks/core-guard.py:43-50](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L43-L50) updates `CRED` to match directory names at word boundaries.
8. **Subdirectories of protected system roots**: [hooks/core-guard.py:75-96, 804-818, 840](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L75-L96) differentiates `SYSTEM_DESCENDANT_ROOTS` from `SYSTEM_EXACT_ROOTS` in `_under_system_root`.
9. **Redirect glued to targets**: [hooks/core-guard.py:349-385](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L349-L385) splits redirect operators without dropping leading target arguments.
10. **Long option parsing for `rm`**: [hooks/core-guard.py:853-870](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L853-L870) extracts flags via `_rm_flags` to prevent false matches from options like `--verbose`.
11. **Connection-URI host extraction**: [hooks/core-guard.py:989-1015](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L989-L1015) parses URIs only from tokens beginning with recognized database schemes.
12. **Credential uploads**: [hooks/core-guard.py:639-689](file://<home>/worktrees/20260907T062120Z-antigravity-you-are-one-lane-of-a-conductor/hooks/core-guard.py#L639-L689) validates that upload flag arguments specifically name credential files.
13. **Rule tag assertions in the regression test gate**: [hooks/tests/core-guard-test.py:86-643, 1058-1093](file://<home>/worktrees/
[... truncated, 261 more chars]

## Lane `review-grok`

- stage: review
- needs: build
- built on: lane build at `c0d3cf5d`
- tip: `c0d3cf5d` (clean)
- cursor/grok-4.6: gate skipped (read lane)

I'll review the applied change against the spec, read the guard and gate, and run the named suite without writing into the working tree.

I'll index the two hook files, run the gate under `$TMPDIR`, and check helper signatures against the spec.

MCP calls were blocked, so I’ll inspect the files and run the gate with readonly tools instead.

The gate is green at **120 catch / 108 quiet**, exit 0. Existing cases still pass under the named rules. One spec miss remains.

1. [hooks/core-guard.py](hooks/core-guard.py) L1084–1085 (and L226–227 vs L970): item 6(c) is supposed to search earlier stages of the **same pipeline**, split on `|` the way `check_xargs` does (`cmd.split("|")` at L970). `check_db` instead scans `segs[:i]`, and `segments()` also splits on `;`, `&&`, `||`, `&`, and newlines. Consequence: a remote `psql` that only runs `SELECT` is denied if an earlier `;`/`&&`/`&` piece of the same command merely mentions `DROP TABLE`/`DELETE FROM` (for example `echo 'DROP TABLE users'; psql -h prod.example.com -c 'SELECT 1'`). Confidence: 9.

FINDINGS: 1

## Lane `fix`

- stage: fix
- needs: review-gemini, review-grok, build
- built on: lane build at `c0d3cf5d`
- resume from: lane build (applied)
- cache reads: 3529839/3529869 input tokens
- tip: `ed23b410` (clean)
- claude/sonnet: resumed session 179a0616-56ab-48f9-ad45-a9bf875f82eb from lane build
- diff: `<home>/missions/20260907T055537Z-core-guard-audit-fixes/diffs/fix.patch`

Gate green: 120 catch / 110 quiet, exit 0.

## Summary

**review-grok item 1** (only reported item): confirmed real. `check_db` at `hooks/core-guard.py` (was L1084, now ~L1078-1092) checked `segs[:i]` for "earlier stage of the same pipeline," but `segs` comes from `segments()`, which also splits on `;`, `&&`, `||`, `&`, and newlines — not just `|`. Reproduced with `echo 'DROP TABLE users'; psql -h prod.example.com -c 'SELECT 1'` (and the `&&` variant), both wrongly denied before the fix. Added two failing QUIET cases under item 6 in `hooks/tests/core-guard-test.py` (~L958-970), confirmed they failed on the pre-fix tree, then fixed `check_db` to derive `pipe_parts = cmd.split("|")` (mirroring `check_xargs`) and only check `pipe_parts[:pidx]` where `pidx` is the pipe-stage containing the current segment. Existing pipe CATCH case and all other db cases still pass.

**review-gemini**: `NO_FINDINGS` — no action.

DISPOSITION: review-grok 1 fixed: `check_db`'s "earlier pipeline stage" check used `segs[:i]` (split on `;`/`&&`/`||`/`&`/newline too) instead of a `|`-only pipeline split, so an unrelated sequential command earlier on the line was misread as an upstream pipe stage; reproduced, added two QUIET regression cases, and scoped the check to `cmd.split("|")` stages before the current segment's pipe index.

## Ranking

| rank | lane | ok | verdict | test_touched | gate_exit | patch_bytes | cost_usd |
|---|---|---|---|---|---|---|---|
| 1 | fix | True |  | yes (1 files: hooks/tests/core-guard-test.py) | 0 | 2716 | 1.7545 |
