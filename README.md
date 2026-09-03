# conductor

One dispatch contract across four agent fleets installed on this machine, so an
orchestrating model can hand work to whichever fleet fits and get back a small,
honest result instead of a transcript.

```
conductor dispatch --fleet codex --model sol --effort hard --mode write \
  --cwd ~/Projects/thing --test "pytest -q" "Refactor the parser per docs/spec.md"
```

## Why it exists

Four agent CLIs live here, each with its own flags, its own effort vocabulary,
its own idea of what "auto-approve" means, and its own way of hanging. Driving
them by hand from an orchestrator means re-improvising all of that per dispatch,
and the improvisation is where the failures live: an approval prompt nobody
answers, a process group that outlives its timeout, a transcript that floods the
caller's context, or the quiet one, an agent that exits 0 having changed nothing.

conductor makes those the tool's problem instead of the prompt's.

## Routing policy

The allowlist is enforced in code, before anything spawns, because the callers
are unattended runs at 3am.

| Fleet | Binary | Models permitted | Why only these |
|---|---|---|---|
| `claude` | `claude` | opus, sonnet, haiku | Anthropic first-party |
| `codex` | `codex` | terra, sol, luna (GPT-5.6) | OpenAI first-party |
| `antigravity` | `agy` | gemini-3.8-flash, gemini-3.7-flash | Google first-party |
| `cursor` | `cursor-agent` | grok-4.6, composer-2.5 | Cursor's included pool |

Cursor and Antigravity both resell models that are already reachable
first-party here. Asking for one is refused with the route you should have
used, because a refusal a caller cannot act on gets worked around rather than
respected:

```
$ conductor dispatch --fleet cursor --model opus --cwd . "..."
{
  "refused": "fleet 'cursor' may not run model 'opus'. Permitted on this fleet:
   grok-4.6, composer-2.5. Anthropic models come from Claude Code (fleet
   'claude'), not resold via Cursor."
}
```

## One effort dial, four dialects

A mission says `cheap | standard | hard | max`. Each fleet hears its own
vocabulary: `claude --effort`, `codex -c model_reasoning_effort=`, `agy
--effort` plus a matching model-id suffix, and Cursor, which carries effort
only in the model id (`cursor-grok-4.6-xhigh`). Antigravity's ladder stops at
`high`, so `max` collapses there; Composer has no ladder and ignores effort
entirely.

Mode is `read` or `write`. Read gets each fleet's strongest read-only setting
(`--mode plan`, `--sandbox read-only`, `--sandbox`); write gets its auto-approve,
because there is nobody present to answer a permission prompt.

## What a result looks like

Small enough to read, never the transcript:

```json
{
  "run_id": "20260903T041233Z-codex-refactor-the-parser",
  "ok": false,
  "fleet": "codex",
  "model": "gpt-5.6-sol",
  "exit_code": 0,
  "duration_s": 184.2,
  "commits": 0,
  "files_changed": 0,
  "no_op": true,
  "run_dir": "~/.conductor/runs/20260903T041233Z-codex-refactor-the-parser"
}
```

That run exited 0 and is still a failure. `ok` means the process succeeded
**and** bytes moved (for write dispatches) **and** the gate passed. Full
stdout, stderr, the exact argv, and the prompt are on disk in `run_dir`; the
caller reads them only if it decides to.

## Who commits

Conductor does, when you pass `--commit "message"`. The fleets do not agree on
whether they can commit at all: Codex's `workspace-write` sandbox blocks writes
to `.git` ("Commit is blocked: this workspace disallows writes to `.git`"),
while Claude Code, Cursor, and Antigravity all commit on their own. Leaving it
to each vendor makes "did it commit?" a property of the vendor rather than of
the work, and mixes four author identities and four message conventions into
one history.

Deletions are staged like anything else and named explicitly in the receipt. A
bulk stage that quietly swallows removed source files is the failure that
reporting exists to prevent.

## What the live matrix taught

Every row below was found by running the thing, not by reading help output.
Each is now pinned by a test.

| Fleet | Finding |
|---|---|
| `antigravity` | Ignores process cwd. With no workspace set it does the work in `~/.gemini/antigravity-cli/scratch`, reports `SUCCESS`, and exits 0. Needs `--add-dir`. Caught by conductor's own no-op check on its first live write. |
| `cursor` | Headless read mode stops on an interactive "do you trust this directory?" prompt and exits 1 having done nothing. Needs `--trust` (`--force` implies it, but read mode has no `--force`). |
| `codex` | Can edit files but never commit under `workspace-write`. |
| `claude` | A one-word reply cost **$0.2314**, because each headless spawn writes a fresh ~57.8K-token prompt cache. Antigravity's equivalent moved ~14K input tokens, Cursor's ~23K. Startup overhead, not the work, dominates short dispatches: do not send small jobs to this fleet. |

## Three lessons borrowed from `peer-agent-tools`

Proven the hard way there, reused here:

1. **`cwd` is load-bearing.** `--add-dir` and `-C` grant access; they do not
   change the working directory. Without `cwd`, an agent branches and commits
   in the dispatcher's directory and the verify step sees an empty target,
   masking it as "no work landed".
2. **`start_new_session` + `killpg`.** A timeout must kill the whole tree. An
   orphaned grandchild still mutating the repo races whatever runs next.
3. **Verify on bytes.** Exit code and final message are both claims.

## Commands

- `conductor fleets` — the routing policy, and whether each binary is installed
- `conductor dispatch` — run one prompt on one fleet (`--dry-run` prints the argv,
  `--test` runs a gate afterward, `--commit` lands the work)
- `conductor verify` — inspect repo state, optionally run a gate
- `conductor runs` — recent dispatches and their verdicts

Run directories live under `$CONDUCTOR_HOME` (default `~/.conductor`).

## Development

```
uv venv && uv pip install -e . && uv pip install pytest ruff
.venv/bin/pytest
.venv/bin/ruff check .
```

Runtime is standard library only. Tests spawn real subprocesses against a
throwaway git repo, so they exercise the timeout, process-group, and no-op
paths for real without spending a token or requiring any fleet to be installed.
