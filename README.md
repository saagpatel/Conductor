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
because there is nobody present to answer a permission prompt. The flags are
a request, the bytes are the check: a read dispatch that changed the tree is
not `ok`, whatever its fleet promised, and neither is one that came back
with no answer, since the answer is a read dispatch's only work product.

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

The commit is conditional on the evidence. A fleet that reports its own
failure is never committed, whatever its exit code. A commit whose gate then
fails is taken back off the branch (`git reset --soft`), with the work left
staged in the kept worktree: a branch must never carry a commit that failed
its gate, because the commit outlives the receipt that says it did. The gate
itself runs in its own process group, like a fleet, so a killed suite leaves
no workers behind.

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
| `claude` | `--max-budget-usd` is the only native dollar cap on any fleet. When it trips: exit 1, `is_error`, `subtype: error_max_budget_usd`, the reason under `errors` ("Reached maximum budget ($0.01)"), no `result` text, and the spend so far still reported. |
| `codex` | Prints usage on stdout exactly once, at `turn.completed`. But it appends a `token_count` event with cumulative totals to its session rollout (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl`) after every model response: 52 of them in one 12-minute run. That file, found by the `thread_id` in the first stdout event, is how conductor caps Codex mid-run. |
| `antigravity` | `--output-format stream-json` prints a `step_update` carrying that step's own usage after every model response (three steps summed exactly to the final figure). conductor now always runs agy this way. |
| `antigravity` | `--mode plan` is silently ignored whenever `--disable-slash-commands` is set (a stderr warning, then the file gets written anyway), and `--sandbox` only restricts the terminal. Asked to create a file in read mode, agy created it. conductor's own byte check caught it; read mode now drops the slash-command flag so plan mode holds (verified: agy wrote an implementation plan in its own brain directory and left the tree alone), and a read dispatch that moves bytes on any fleet is no longer `ok`. |
| `cursor` | Reports usage once, in its final `result`, in both `json` and `stream-json` modes. A cap on Cursor is a verdict after the run, never a stop. |
| `cursor` | The `json` envelope's `result` is only the **last** assistant message. On a four-fleet brainstorm, grok-4.6 spent 15K output tokens and its envelope held 680 characters of "writing the answer now". conductor runs Cursor in `stream-json` and keeps every assistant message, and a read dispatch that returns no answer is no longer `ok` on any fleet. |
| `cursor` | In plan mode (conductor's read mode) Cursor files its real answer through a `createPlan` tool call and says only "auditing..." out loud. Composer wrote a full four-finding audit that way and it was invisible until the tool call was read. conductor now takes the plan text as part of the answer. |

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
  "cap_usd": 2.0,
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
- runs lanes under the concurrency cap, each lane (read or write, and the
  collate) in its own git worktree on branch `conductor/<run_id>` (see
  below), so a fleet that ignores its read-only flag edits a throwaway
  tree rather than the orchestrator's checkout;
- escalates down a lane's `fallback` list when an attempt is not `ok`, which
  includes the exit-0-no-op case, a failed gate, and a fleet's own error;
- keeps a shared dollar ledger and skips any attempt that would start after
  `max_cost_usd` is spent, saying so in the lane's `skipped` field; what
  remains of the budget also caps every dispatch it starts (tightening any
  `cap_usd` the lane set), so the overshoot is bounded in dollars, not just
  in dispatches; a dispatch that lands unpriced makes the total unknowable,
  and an unknowable budget is treated as spent (`budget unverifiable`);
- copies each lane's final attempt's answer to `answers/<lane>.txt` and its
  patch (committed, uncommitted, and untracked work against the base) to
  `diffs/<lane>.patch`; if `collate` is set, hands all of them to one
  read-mode dispatch for a synthesis, patches included (`include_diffs`),
  so the judge grades what each lane changed rather than what it claimed;
- writes `report.md` (one table, each answer, the collated verdict) and
  `result.json` under `$CONDUCTOR_HOME/missions/<id>/`.

Fields cascade mission → lane → fallback, so the common case is one prompt,
one cwd, one mode, and a list of fleets. A fallback that switches fleet drops
the inherited `model`, because model names are fleet-local (caught live: an
Antigravity fallback inheriting `luna` from its Codex primary). `require` is
`all` (default) or `any`. TOML files load too. Two dollar fields, two
meanings: `max_cost_usd` is the mission's total, `cap_usd` is one dispatch's
ceiling (see below). A key the loader does not know is refused, so `need`
cannot quietly turn a dependent lane into a root.

### Pipelines: build, then independent review, then fix

Lanes can depend on each other. Three lane fields make a flat fan-out a
pipeline; everything else is unchanged.

```json
{
  "name": "parser-refactor",
  "cwd": "~/Projects/thing",
  "prompt_file": "spec.md",
  "max_cost_usd": 10,
  "lanes": [
    {"name": "build", "fleet": "codex", "model": "sol", "mode": "write",
     "test": "pytest -q", "commit": "feat: refactor parser per spec"},
    {"name": "review", "fleet": "claude", "model": "opus", "mode": "read",
     "base": "build",
     "prompt": "Review this change against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nList concrete defects; say NO_DEFECTS if none."},
    {"name": "fix", "fleet": "codex", "model": "sol", "mode": "write",
     "base": "build", "needs": ["review"], "no_op_ok": true,
     "test": "pytest -q", "commit": "fix: address cross-vendor review",
     "prompt": "A reviewer from another vendor found:\n{{lanes.review.answer}}\nFix every real one; change nothing if there are none."}
  ]
}
```

- `needs`: lanes that must have ended **ok** before this one starts. If one
  of them did not, this lane is skipped with the reason, and so is anything
  behind it, immediately. Cycles, unknown names, and self-needs are refused
  at load.
- `base`: the lane whose final **commit** this lane's worktree starts from.
  `base` implies `needs`. Only committed work can be built on: a lane that
  left uncommitted edits in a kept worktree, or was not isolated, cannot be
  a base, and the dependent is skipped saying so. A based dispatch is always
  isolated and is refused (in read mode too) if its worktree cannot be made,
  because reviewing HEAD instead of the build would be reviewing the wrong
  code. Its `diff.patch` is against the base's tip, and the report says so.
- `no_op_ok`: a write lane that may legitimately change nothing (the fix
  step when the review found nothing). It waives only the no-op and the
  "nothing to commit"; a failed gate, a fleet error, or an over-cap run
  still fails. A clean no-op lane can itself be a base.

Prompt templates: `{{lanes.<name>.answer}}`, `{{lanes.<name>.diff}}`, and
`{{mission.prompt}}` (the mission-level prompt, verbatim). A referenced lane
must be in `needs`; anything else between double braces is refused at load,
so a misspelt name cannot render as `(none)`. Rendering is a single pass, so
braces inside an upstream answer never become new substitutions; each pasted
value is fenced and labelled as another agent's output, not instructions;
and the total pasted text per prompt is bounded by `template_max_chars`
(default 40000). In a dry run the placeholders render as `(dry run: ...)`.

A pipeline is judged on its outputs: `require` applies to the lanes nothing
else depends on, so `build ok, fix failed` is a failed pipeline whatever
`any` would say. In a flat mission every lane is an output, as before. Each
lane's receipt is written to `lanes/<name>.json` the moment it ends, so a
crash mid-mission does not lose the finished stages. A fleet that commits
on its own (Claude Code, Cursor, and Antigravity do) is landed work, not
"nothing to commit".

A lane's `branch` names its deliverable: once the lane's commits land, its
`conductor/<run_id>` branch is renamed to that name (`refactor/x`), so what
the operator merges is not a timestamp. The name is checked before any
fleet spawns: an invalid name, a name already in the repo, a `conductor/`
prefix, or two lanes claiming one name are refused at mission start, not
after a $5 build. A lane that landed nothing has no branch to name and says
so. Upstream lanes keep their run-id branches; the first production
pipeline (2026-09-03) needed a hand rename, which is how this field earned
its place.

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

Ctrl-C or `kill` on `conductor dispatch` or `conductor mission` ends the run
the same way a timeout does. Every running dispatch kills its fleet's
process group at its next poll (within `POLL_S`, 2s), is priced from the
watcher's last reading and receipted with `interrupted: true`, and releases
its worktree; a mission skips the lanes that had not started, does not
spend the collate, and still writes its report, marked **Interrupted**. A
second signal is the operator insisting and exits at once. Before this,
killing conductor left the fleet running in a worktree nothing would
release (found live 2026-09-03, on the first production pipeline). An
interrupted run is not over its cap and not "unpriced": the stop is the
only verdict it gets.

### Garbage collection

`conductor gc` reconstructs a cleanup plan from current Git state and the
isolation records under `$CONDUCTOR_HOME/runs`. It reports one JSON object per
worktree, `conductor/*` branch, or incomplete audit directory and changes
nothing by default; pass `--apply` to prune vanished registrations, remove
clean conductor-home worktrees, and delete conductor branches whose commits
are already reachable from `HEAD` or another non-conductor branch. `--repo`
adds repositories to those discovered from receipts, and `--older-than`
limits actions to older run ids.

GC never removes a dirty worktree, a worktree outside
`$CONDUCTOR_HOME/worktrees`, a branch outside `conductor/`, an unmerged
conductor branch, or any run or mission directory. Directories without a
`result.json` are reported as possible in-progress or crashed work and kept.

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

### Spend reports

`conductor spend` totals the durable receipts under `$CONDUCTOR_HOME/runs`,
grouped by fleet by default or by day, model, mission, or run. `--since` is
inclusive, `--until` is exclusive, and both accept a UTC date or ISO datetime;
`--json` emits machine-readable rows. Estimated and unpriced runs are counted
separately, so a missing price can never make a run look free. Malformed or
unreadable receipts are counted as skipped on the total row.

### Per-dispatch caps

`--cap-usd` (or `cap_usd` in a mission) bounds one dispatch in dollars, the
unit the wall-clock timeout only approximates. Each fleet is capped the way
it allows, and the result's `budget` field says which:

| Fleet | `enforcement` | What happens |
|---|---|---|
| `claude` | `native` | `--max-budget-usd`; Claude Code stops itself and reports the spend. |
| `codex` | `watcher` | conductor tails the session rollout's running totals, prices them, and kills the process group the poll after the estimate crosses the cap. |
| `antigravity` | `watcher` | the same, over the per-step usage in agy's `stream-json` output. |
| `cursor` | `post-hoc` | usage arrives once, at the end; the cap is checked then. |

A watched fleet overshoots by at most one model response plus one two-second
poll. A run over its cap is not `ok` (`failure: "over budget: $3.0000 against
a $1.0000 cap"`), whether it was killed or merely judged afterwards; work it
landed is still on its branch. The watcher runs on every codex and
antigravity dispatch, cap or not, because it is also the only price a run
that conductor killed can get: a timed-out Codex dispatch used to land in
the ledger as `cost_usd: null`. A cap on an unpriced model is refused before
spawn rather than silently unenforced, and a capped run that comes back with
no usage at all fails closed (`cap unenforced: the run came back unpriced`)
instead of reading as within budget.

## Commands

- `conductor fleets`: the routing policy, and whether each binary is installed
- `conductor dispatch`: run one prompt on one fleet (`--dry-run` prints the argv,
  `--schema` requests structured output, `--test` runs a gate afterward,
  `--commit` lands the work, `--isolate` runs in a fresh worktree,
  `--cap-usd` bounds the spend)
- `conductor mission FILE`: run a mission file (`--dry-run` validates and
  records every argv without spawning)
- `conductor verify`: inspect repo state, optionally run a gate
- `conductor runs` / `conductor missions`: recent dispatches and missions
- `conductor prices`: the effective price table after overrides
- `conductor spend`: summarize cost and tokens by day, fleet, model, mission,
  or run
- `conductor gc`: plan safe worktree and `conductor/*` branch cleanup; pass
  `--apply` to execute it

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
