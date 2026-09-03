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
**and** bytes moved (for write dispatches) **and** the gate passed **and**
any requested commit landed; `failure` names which of those did not hold
(here, `"write dispatch moved no bytes"`), so the caller never has to
reconstruct the reason from the raw fields. Full
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
| `antigravity` | `--print-timeout` defaults to **5m0s** whatever conductor does with the process. An 8s cap on a 25s task exits 1 with `status: ERROR`, `error: "timeout waiting for response"`, and the work cut. conductor pins it to the spec's timeout minus 5s, so agy stops itself (and still prints its usage and its own error) just before conductor's process-group kill would leave nothing to price. |
| `claude` | `--json-schema` takes the schema **text**, not a path (a path fails with "not valid JSON"). The validated object comes back under `structured_output`. Codex (`--output-schema`) and Antigravity (`--json-schema`) take a path. |
| `cursor` | Has no structured-output flag at all. A `--schema` dispatch to Cursor is refused before spawn rather than silently handed prose. |
| all | `--schema` verified live on claude, codex, and antigravity; effort `max` verified on claude; sol, luna, composer-2.5, and gemini-3.7-flash each answered a live dispatch. Every fleet's failure signal (`is_error`, `status: ERROR`, a Codex `error` event) is read and sinks `ok` even on exit 0. |

## Three lessons borrowed from `peer-agent-tools`

Proven the hard way there, reused here:

1. **`cwd` is load-bearing.** `--add-dir` and `-C` grant access; they do not
   change the working directory. Without `cwd`, an agent branches and commits
   in the dispatcher's directory and the verify step sees an empty target,
   masking it as "no work landed".
2. **`start_new_session` + `killpg`.** A timeout must kill the whole tree. An
   orphaned grandchild still mutating the repo races whatever runs next.
3. **Verify on bytes.** Exit code and final message are both claims.

## Missions: unattended runs as data, not shell

A mission file names one prompt and the lanes it fans out to. This is the
interface an orchestrating model drives: it writes the file and reads back a
summary and a report path.

```json
{
  "name": "parser-refactor",
  "prompt_file": "spec.md",
  "cwd": "~/Projects/thing",
  "mode": "write",
  "effort": "hard",
  "test": "pytest -q",
  "commit": "feat: refactor parser per spec",
  "concurrency": 2,
  "max_cost_usd": 5.0,
  "lanes": [
    {"fleet": "codex", "model": "sol", "fallback": [{"fleet": "claude", "model": "opus"}]},
    {"fleet": "antigravity"},
    {"fleet": "cursor", "model": "composer-2.5", "effort": "standard"}
  ],
  "collate": {"fleet": "claude", "model": "sonnet", "effort": "cheap"}
}
```

`conductor mission parser-refactor.json` then:

- checks every lane and fallback against the routing policy **at load time**,
  before a token is spent, and refuses the whole file with the lane named;
- runs lanes under the concurrency cap, each write lane in its own git
  worktree on branch `conductor/<run_id>` (see below);
- escalates down a lane's `fallback` list when an attempt is not `ok`, which
  includes the exit-0-no-op case, a failed gate, and a fleet's own error;
- keeps a shared dollar ledger and skips any attempt that would start after
  `max_cost_usd` is spent, saying so in the lane's `skipped` field;
- copies each lane's answer to `answers/<lane>.txt`, and, if `collate` is
  set, hands all of them to one read-mode dispatch for a synthesis;
- writes `report.md` (one table, each answer, the collated verdict) and
  `result.json` under `$CONDUCTOR_HOME/missions/<id>/`.

Fields cascade mission → lane → fallback, so the common case is one prompt,
one cwd, one mode, and a list of fleets. A fallback that switches fleet drops
the inherited `model`, because model names are fleet-local (caught live: an
Antigravity fallback inheriting `luna` from its Codex primary). `require` is
`all` (default) or `any`. TOML files load too.

## Isolation: a branch is not a worktree

HEAD and the index are shared mutable state, so two fleets editing one
checkout race each other whatever branches they think they are on. Every
write lane in a mission, and any dispatch given `--isolate`, runs in a fresh
worktree under `$CONDUCTOR_HOME/worktrees/` on branch `conductor/<run_id>`,
created from the target's HEAD. Verification and `--commit` happen there.
Afterwards the worktree is removed if it is clean (the branch keeps the
commits, ready to merge) and kept, with its path reported, if it holds
uncommitted work. Deleting an agent's uncommitted edits to tidy up is the
wrong trade. If a worktree cannot be created (not a repo, no commits yet, a
git error), a write dispatch is refused before anything spawns rather than
run in the shared checkout; a read dispatch proceeds in place and says so.

## Cost accounting

Only the claude fleet reports dollars. Codex reports usage only in its
`--json` event stream (now always on; the answer still lands in the `-o`
file), and Cursor and Antigravity report tokens with no price. conductor
prices those from a dated list-price table (`conductor prices`) and marks the
result `cost_basis: "estimated"`; a fleet's own figure is `"reported"` and is
never overwritten. An unpriced model yields `null`, not `$0.00`, so a gap
shows as a gap. Override or extend the table without a code change in
`$CONDUCTOR_HOME/prices.json` (a null entry drops a model; a malformed file
falls back to defaults rather than stopping a run).

Token conventions are normalized first: `input_tokens` excludes cache reads
on every fleet (OpenAI and Google count them inside the input figure and are
split out), and `output_tokens` includes reasoning (Antigravity's separate
`thinking_tokens` are folded in, as Google bills them).

Measured on 2026-09-03, a one-line answer to "what is this README for":
codex/luna $0.0037, antigravity $0.0228, cursor/composer-2.5 $0.0131, and
the claude/haiku collate $0.0599. Startup, not the work, still dominates.

## Commands

- `conductor fleets`: the routing policy, and whether each binary is installed
- `conductor dispatch`: run one prompt on one fleet (`--dry-run` prints the argv,
  `--schema` requests structured output, `--test` runs a gate afterward,
  `--commit` lands the work, `--isolate` runs in a fresh worktree)
- `conductor mission FILE`: run a mission file (`--dry-run` validates and
  records every argv without spawning)
- `conductor verify`: inspect repo state, optionally run a gate
- `conductor runs` / `conductor missions`: recent dispatches and missions
- `conductor prices`: the effective price table after overrides

Run directories live under `$CONDUCTOR_HOME` (default `~/.conductor`).

## Development

```
uv venv && uv sync --frozen --group dev
.venv/bin/pytest
.venv/bin/ruff check .
```

Runtime is standard library only. Tests spawn real subprocesses against a
throwaway git repo, so they exercise the timeout, process-group, and no-op
paths for real without spending a token or requiring any fleet to be installed.
