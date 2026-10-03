# conductor

One dispatch contract across four agent fleets installed on this machine, so an
orchestrating model can hand work to whichever fleet fits and get back a small,
honest result instead of a transcript.

```
conductor dispatch --fleet claude --model sonnet --effort hard --mode write \
  --cwd ~/Projects/thing --test "pytest -q" "Refactor the parser per docs/spec.md"
```

## Current support

Policy as of 2026-09-07, not history. This table is what a mission may do
today; when it and an older section, a research report under `docs/research/`,
or a fleet note in `AGENTS.md` disagree, this table wins and the older text is
the record of how the rule was learned. Nothing below is deleted from that
record; it is separated from it.

| Fleet | Status | Models | Structured output | Taint | Restricted | Persona | Cost |
|---|---|---|---|---|---|---|---|
| `claude` | preferred | opus, sonnet, haiku | `schema`, `verdict` | yes, shell denied by default | read lanes only | yes | reported by the fleet |
| `antigravity` | preferred, never runs the test suite | gemini-3.8-flash, gemini-3.7-flash | write lanes only; refused on read | yes, PreToolUse hook counted before spend | refused | refused | estimated from list prices |
| `cursor` | preferred | grok-4.6, composer-2.5 | refused | refused | refused | refused | estimated from list prices |
| `codex` | paused since 2026-09-04, operator decision | terra, sol, luna | `schema`, `verdict` | refused | refused | refused | estimated from list prices |
| `script` | always | none, a shell command | refused | refused | refused | refused | none |

Every fleet takes `mode: read | write`, `effort: cheap | standard | hard |
max` (Antigravity stops at `high`, Composer ignores effort), a `cap_usd`, and
a `deliverable`. "Refused" means `Spec.validate` raises `DispatchRefused`
before anything spawns, with the reason and the route to use instead; a
paused fleet is refused by policy in the lead's hands, not by the code, which
still knows how to drive it. Every write lane is judged on bytes, whatever
the fleet reported.

Standing policy alongside the table:

- **Shape A is the measured default:** Sonnet 5 builds at `hard`, Gemini 3.7
  Flash and Grok 4.6 review cold in parallel, Sonnet fixes on the build's
  thread. `conductor shape a` writes it; `--opus-review` adds Opus 5 as a
  third reviewer, `--adversarial` adds a lane whose deliverable is a failing
  test. Caps follow `AGENTS.md` rule 2, and the launcher raises every warned
  lane's cap to the forecast p80 rounded up to a whole dollar.
- **Reviewers carry no quota.** Every review, judge, and collate prompt says
  an empty answer is complete; the template is in `AGENTS.md`.
- **Opt-in Composer/Grok example:** [copy and configure this mission](docs/reference/composer-grok-example.md)
  for a bounded Composer build, Grok review, resumed correction, and lead acceptance.
  This example records a tested alternative; it does not change Shape A or standing routing policy.
- **Shelved, not to be re-proposed without the operator raising it:** the
  Codex lanes above; OpenCode, OpenRouter, Ollama, pi, and local models; cloud
  offload for this repository. Their sections in `AGENTS.md` and
  `docs/research/` are the probe record, not a routing recommendation.
- **Where the rules live:** the allowlist and every refusal in
  `src/conductor/fleets.py`; list prices, dated, in `src/conductor/prices.py`
  with `$CONDUCTOR_HOME/prices.json` as the override; the per-lane keys under
  "Missions" below; the error kinds under "Structured error kinds".

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
| `codex` | `codex` | terra, sol, luna (GPT-5.6) | OpenAI first-party; paused, see "Current support" |
| `antigravity` | `agy` | gemini-3.8-flash, gemini-3.7-flash | Google first-party |
| `cursor` | `cursor-agent` | grok-4.6, composer-2.5 | Cursor's included pool |
| `script` | `sh` | `sh` | not a model at all -- a shell command; see "Script lanes" |

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
The verdict hashes porcelain status plus every dirty and untracked file's
contents, so editing an already-dirty file cannot hide behind an unchanged count.
Structured Codex and Cursor streams must end in `turn.completed` and `result`
respectively; a cut-short stream may retain its answer, but fails closed. An
Antigravity stream that stops before its own `result` event fails the same
way, on a status of `incomplete` rather than a fleet-reported error --
without it a write lane that exited 0, moved bytes, and passed its gate on a
truncated stream had nothing left to fail on.

## What a result looks like

Small enough to read, never the transcript:

```json
{
  "run_id": "20260903T041233Z-codex-refactor-the-parser",
  "ok": false,
  "fleet": "codex",
  "model": "gpt-5.6-sol",
  "session_id": "01a06560-2a84-7522-9f95-6043c032fabd",
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
reconstruct the reason from the raw fields. Two more failures name themselves
there: `"fleet stream ended without a terminal event"` for a turn that never
finished, and `"parse failed: ..."` for a run that was paid for and then
raised while conductor was reading its output -- the receipt is written
anyway, with whatever price was recoverable, because a run directory holding
only `stdout.log` is spend that `conductor spend` and `conductor report`
cannot see. Full stdout, stderr, the exact
argv, prompt, and fleet-reported `session_id` are on disk in `run_dir`; the
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

A non-isolated `--commit` is refused when the checkout already has tracked or
untracked changes; use `--isolate` so conductor cannot sweep up operator work.
Only a descendant commit on the dispatch's original branch is treated as a
fleet self-commit; switching to an existing branch never makes that history
eligible for gate rollback.

Deletions are staged like anything else and named explicitly in the receipt. A
bulk stage that quietly swallows removed source files is the failure that
reporting exists to prevent.

### The gate a fleet cannot edit

A fleet passes its own gate most cheaply by editing the gate: a `conftest.py`
that skips the failing test, a `pyproject.toml` that narrows `addopts`, a
Makefile the gate calls. So every dispatch pins its **test surface** before
spawn: the content hash of every test file and gate configuration file
(`tests/**`, `**/conftest.py`, `**/pyproject.toml`, `**/Makefile`,
`.github/workflows/**`, and the rest of `surface.DEFAULT_TEST_SURFACE`,
ignored files included), and hashes it again after the fleet exits. The
receipt carries `test_surface.touched` and the changed paths, and missions
render `{{lanes.X.test_touched}}` for downstream lanes.

What a touched surface means is the dispatch's `test_policy`:

- `clean` (default): the fleet's own gate run is not the verdict. Conductor
  adds a detached worktree at the base commit, transplants only the
  non-surface changes into it (through a temporary index, never the
  fleet's), runs the gate there, and counts only that run. A commit whose
  clean gate fails is uncommitted like any other gate failure. When the
  surface did not move the fleet's own run counts and nothing is repeated,
  so the common case pays nothing extra.
- `allow`: the surface may change and the fleet's own gate counts. This is
  the per-stage allowlist for a lane whose job is to write tests.
- `forbid`: any surface change fails the dispatch outright.

A `clean` gate that fails is not always a broken build: when the lane's own
diff is what touched the test surface, the base tree's tests just ran
against the new source, and both the build that changed a fixture's shape
and the fixture it changed can be right at once (this rejected two real
builds and a fix before it had a name; see `docs/RESET-2026-09.md`). So a
clean-gate failure whose diff touched the surface gets its own message --
`clean gate exited N after the diff touched K test-surface files: a, b, c`
(paths sorted, at most five, then `and M more`) -- and its own kind,
`gate_test_surface`, instead of the plain `gate` an ordinary broken build
still gets. The fix is AGENTS.md rule 3: rerun the lane with
`test_policy: allow` and read every test edit by hand, not to keep
re-running it under `clean`.

The clean worktree is pristine: it has no installed dependencies, so the
gate command must bring its own toolchain (an absolute interpreter path, a
`uv run --project`, a `make` target that installs). The worktree is removed
when the gate ends, on every path; `gc` recognises a leftover
`<run_id>-clean` tree and keeps it while its run is live.

A `mode: read` lane's own gate and clean gate are skipped outright when a
bytes comparison taken *before* either would run shows nothing but a no-op or
exactly the lane's declared deliverable (the same exemption the read-only
check applies) -- a review lane that moved nothing has nothing for the base
tree's gate, already run by whatever it is reviewing, to re-check. The
receipt records `gate: {"skipped": "read lane, source unchanged", "command":
<the gate>}` and `tests: null`; every verdict that reads a passed gate (a
mission's `require`, `conductor report`'s `ok` column and `gate_failures`,
salvage) treats the skip as a pass, and a mission's `report.md` names the
lane's skip as `gate skipped (read lane)`. A read lane that moves anything
else is gated exactly as before, and a write lane's gate always runs,
whether or not it moved bytes.

## What the live matrix taught

Every row below was found by running the thing, not by reading help output.
Each is now pinned by a test.

| Fleet | Finding |
|---|---|
| `antigravity` | Ignores process cwd. With no workspace set it does the work in `~/.gemini/antigravity-cli/scratch`, reports `SUCCESS`, and exits 0. Needs `--add-dir`. Caught by conductor's own no-op check on its first live write. |
| `cursor` | Headless read mode stops on an interactive "do you trust this directory?" prompt and exits 1 having done nothing. Needs `--trust` (`--force` implies it, but read mode has no `--force`). |
| `codex` | Can edit files but never commit under `workspace-write`. |
| `claude` | A one-word reply cost **$0.2314**, because each headless spawn writes a fresh ~57.8K-token prompt cache. Antigravity's equivalent moved ~14K input tokens, Cursor's ~23K. Startup overhead, not the work, dominates short dispatches: do not send small jobs to this fleet. |
| `claude` | Loading the operator's user settings ran 46 hook invocations on a one-word prompt (24 at session start), one of which rewrote a memory file, and cost $0.24 against $0.05 without them. conductor passes `--setting-sources project`: the target repo's own settings still apply, the operator's do not. It uses `--output-format stream-json --verbose` so progress is visible while Claude works; the final `result` event keeps the same answer, session, usage, cost, error, and structured-output fields. The parser still accepts the older array and single-object receipts. `--bare` would also drop CLAUDE.md discovery but refuses OAuth. |
| `antigravity` | `--print-timeout` defaults to **5m0s** whatever conductor does with the process. An 8s cap on a 25s task exits 1 with `status: ERROR`, `error: "timeout waiting for response"`, and the work cut. conductor pins it to the spec's timeout minus 5s, so agy stops itself (and still prints its usage and its own error) just before conductor's process-group kill would leave nothing to price. |
| `claude` | `--json-schema` takes the schema **text**, not a path (a path fails with "not valid JSON"). The validated object comes back under `structured_output`. Codex (`--output-schema`) and Antigravity (`--json-schema`) take a path. |
| `cursor` | Has no structured-output flag at all. A `--schema` dispatch to Cursor is refused before spawn rather than silently handed prose. |
| all | `--schema` verified live on claude, codex, and antigravity; effort `max` verified on claude; sol, luna, composer-2.5, and gemini-3.7-flash each answered a live dispatch. Every fleet's failure signal (`is_error`, `status: ERROR`, a Codex `error` event) is read and sinks `ok` even on exit 0. |
| `claude` | `--max-budget-usd` is the only native dollar cap on any fleet. When it trips: exit 1, `is_error`, `subtype: error_max_budget_usd`, the reason under `errors` ("Reached maximum budget ($0.01)"), no `result` text, and the spend so far still reported. |
| `codex` | Prints usage on stdout exactly once, at `turn.completed`. But it appends a `token_count` event with cumulative totals to its session rollout (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl`) after every model response: 52 of them in one 12-minute run. That file, found by the `thread_id` in the first stdout event, is how conductor caps Codex mid-run. |
| `antigravity` | `--output-format stream-json` prints a `step_update` carrying that step's own usage after every model response (three steps summed exactly to the final figure). conductor now always runs agy this way. |
| `antigravity` | A stream whose last event is a `step_update` rather than the wrapped `result` was cut short (conductor's kill, or agy's own crash). The steps' usage is still real and is still priced, but the turn did not finish: conductor stamps `status: incomplete` on it and fails the run, exit code, bytes, and gate notwithstanding. |
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
- refuses write lanes and write fallbacks with `isolate: false`; read lanes may
  still opt out explicitly;
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
  and an unknowable budget is treated as spent (`budget unverifiable`); the
  result's `budget` block also carries `in_flight_dispatches`,
  `outstanding_cap_usd` (the sum of the caps still running right now, `null`
  when any of them has no cap), and `worst_case_usd` (`spent_usd +
  outstanding_cap_usd`) -- a report of possible overshoot while dispatches
  are still in flight, never subtracted from what a dispatch may still
  spend (W6); the same block is written into `pause.json` under `budget`
  whenever the mission parks, and refreshed into the mission directory's
  `running.json` lock under `budget` every time a dispatch starts or
  finishes, so those figures can be read from either file during a run and
  not only from the finished mission;
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
`all` (default), `any`, or `{"pass": n, "of": [...]}` for a verdict quorum.
TOML files load too. Two dollar fields, two
meanings: `max_cost_usd` is the mission's total, `cap_usd` is one dispatch's
ceiling (see below). A key the loader does not know is refused, so `need`
cannot quietly turn a dependent lane into a root.

Attempt keys are `fleet`, `model`, `effort`, `mode`, `cwd`, `prompt`, `prompt_file`,
`timeout`, `stall_timeout`, `loop_limit`, `max_tool_calls`, `test`, `test_policy`,
`test_surface`, `commit`, `isolate`, `cap_usd`, `no_op_ok`, `schema`, `verdict`,
and `deliverable`.
`test_policy` is `clean` (the default),
`allow`, or `forbid`; `test_surface` is a list of Git pathspec globs that
replaces the default test/CI surface for that attempt.

#### Per-lane `cwd`: more than one repository per mission

The mission's own `cwd` is only the default. `cwd` cascades like `schema`
(mission → lane → fallback), so a lane, or one of its own fallbacks, may
name a different repository; a relative path resolves against the mission
file's own directory, exactly like the mission-level `cwd` does. Everything
that site is actually about follows that lane's own resolved cwd: where its
attempt dispatches, where its branch is created and renamed, and which other
sinks its diff can conflict with (two sinks sharing a cwd are grouped and
compared with `git merge-tree`; sinks in different repositories are never
paired). A lane's receipt carries the `cwd` it actually ran in, and
`report.md`'s per-lane section names it when it differs from the mission's.
Two lanes may claim the same `branch` name as long as they land in different
repositories -- the same name twice in the same repository is refused at
load, as before. `resolve` is refused at load when its sink lanes span more
than one repository -- the resolver never crosses repositories -- and, since
every sink then shares one repository, it dispatches, is gated, and (on a
resume) has its committed tip checked in that repository, never the
mission's own `cwd` when the two differ. The three `notify` hooks (pause,
end, breaker) likewise always run in the mission's own `cwd`, never a
lane's. See "Collisions across repositories" in
`docs/reference/structured-review.md` for what a mission whose lanes span
more than one repository does to `collisions` and the mission result.

The rest of the mission, isolation, cost, and golden-fixture reference is
linked from [Reference](#reference).

## Commands

- `conductor fleets`: the routing policy, and whether each binary is installed
- `conductor dispatch`: run one prompt on one fleet (`--dry-run` prints the argv,
  `--resume SESSION_ID` continues a fleet session,
  `--schema` requests caller-defined structured output; repeatable `--verdict`
  ids and `--verdict-file` request conductor's checklist schema. `--test` runs a gate afterward,
  `--test-policy {clean,allow,forbid}` chooses how test-surface edits count,
  repeatable `--test-surface PATTERN` replaces the default surface,
  `--commit` lands the work, `--isolate` runs in a fresh worktree,
  `--cap-usd` bounds the spend, `--ports N` claims free TCP ports, `--setup`
  and `--teardown` run shell commands around the fleet, repeatable
  `--include PATH` copies an untracked path into the worktree)
- `conductor mission FILE`: run a mission file (`--dry-run` validates and
  records every argv without spawning)
- `conductor shape a --spec FILE --repo DIR --test CMD --items N --modules M`: write a
  Shape A mission from the versioned template, print every term of its cap arithmetic
  (AGENTS.md rules 2 and 10), and validate it (`--scheduler` adds the tax,
  `--grok-runs-suite` raises Grok's cap, `--tests-items` doubles the test items' share of
  the build cap, `--findings` sizes the fix cap, `--ceiling` sets the mission's E9 rolling
  spend ceiling, `--skip-preflight` skips the gate preflight, `--out`, `--force`, `--dry-run`)
- `conductor verify`: inspect repo state, optionally run a gate
- `conductor runs` / `conductor missions`: recent dispatches and missions
- `conductor prices`: the effective price table after overrides
- `conductor spend`: summarize cost and tokens by day, fleet, model, mission,
  or run
- `conductor report`: the ledger report -- AGENTS.md's Shape A rules as
  numbers, computed from the same receipts
- `conductor reprice`: re-parse every stored receipt's own stdout with the
  current parser and report what a parser convention change moved; `--dry-run`
  is the default and `--apply` archives the corpus before writing. Optional
  `--fleet NAME` limits the pass to one fleet's receipts. It never recomputes a
  `reported` cost and never touches a receipt whose token counters did not
  move; see "Cost accounting"
- `conductor gc`: plan safe worktree, `conductor/*` branch, and stale
  port-claim cleanup; pass `--apply` to execute it
- `conductor attest MISSION_ID`: verify a mission's signed receipt chain on
  bytes
- `conductor salvage MISSION_ID --lane NAME`: re-run the clean gate from a
  kept lane's worktree by hand (AGENTS.md rule 6), and, once the lead has
  committed it, `--emit PATH --items N --modules M` writes the follow-on
  review-and-fix mission
- `conductor land MISSION_ID --lane NAME`: merge a lane's branch, gate the
  merged head, run `golden check`, and attest the mission (`--checkout PATH`,
  `--test CMD`, `--dry-run`); see "Landing"
- `conductor golden record MISSION_ID --out DIR`: record a finished mission
  under `$CONDUCTOR_HOME` as an offline, scrubbed fixture (`--max-bytes`
  overrides the 3,000,000-byte default)
- `conductor golden check [DIR ...]`: replay fixtures offline and compare
  against `expected.json` (`--update` rewrites it instead)

Run directories live under `$CONDUCTOR_HOME` (default `~/.conductor`).

## Reference

The feature reference that used to live under Missions, Isolation, Cost
accounting, and Golden missions is one page per topic. Content is the same;
this table is the index.

| Page | Covers |
|---|---|
| [`docs/reference/deliverables.md`](docs/reference/deliverables.md) | A file as the lane's product, plus validators. |
| [`docs/reference/cheap-first-cascade.md`](docs/reference/cheap-first-cascade.md) | Try a cheaper model first; escalate when it is not ok. |
| [`docs/reference/structured-error-kinds.md`](docs/reference/structured-error-kinds.md) | Closed list of failure kinds, fallback.on, and retry. |
| [`docs/reference/resuming-a-mission.md`](docs/reference/resuming-a-mission.md) | Continue a parked or interrupted mission from receipts. |
| [`docs/reference/pipelines.md`](docs/reference/pipelines.md) | needs, thread reuse, and cache-friendly prompt prefixes. |
| [`docs/reference/taint.md`](docs/reference/taint.md) | Text from outside the operator's trust runs with fewer tools. |
| [`docs/reference/restricted-read-lanes.md`](docs/reference/restricted-read-lanes.md) | Claude read lanes confined to the worktree (`--restricted`). |
| [`docs/reference/untrusted-output.md`](docs/reference/untrusted-output.md) | A lane marks its own output as a taint source. |
| [`docs/reference/inline-agents.md`](docs/reference/inline-agents.md) | A Claude persona carried in the dispatch, not on disk. |
| [`docs/reference/shape-a-launcher.md`](docs/reference/shape-a-launcher.md) | conductor shape a: measured default mission and cap arithmetic. |
| [`docs/reference/composer-grok-example.md`](docs/reference/composer-grok-example.md) | Opt-in Composer build, Grok review, correction checkpoint, and lead acceptance. |
| [`docs/reference/salvage.md`](docs/reference/salvage.md) | Gate a kept worktree, commit it, emit the follow-on mission. |
| [`docs/reference/landing.md`](docs/reference/landing.md) | Merge a lane's branch, gate the merged head, attest. |
| [`docs/reference/lane-stages.md`](docs/reference/lane-stages.md) | build / review / fix, reviewer policy, reproduce-before-fix, adversarial. |
| [`docs/reference/script-lanes.md`](docs/reference/script-lanes.md) | A shell command as a lane, usually a build feeding a review. |
| [`docs/reference/structured-review.md`](docs/reference/structured-review.md) | Quorum, judge hygiene, ranking, best-of-n, collisions, resolve. |
| [`docs/reference/signed-lane-receipts.md`](docs/reference/signed-lane-receipts.md) | Signed attestation chain; conductor attest. |
| [`docs/reference/export-bundles.md`](docs/reference/export-bundles.md) | Portable bundle of receipts, diffs, and answers. |
| [`docs/reference/pausing-for-the-operator.md`](docs/reference/pausing-for-the-operator.md) | Park a mission and wait for an operator answer. |
| [`docs/reference/human-lanes.md`](docs/reference/human-lanes.md) | fleet: human; blocks until --answer / --answer-file. |
| [`docs/reference/planner-lanes.md`](docs/reference/planner-lanes.md) | Ask the operator to launch a child mission; one approval each. |
| [`docs/reference/isolation.md`](docs/reference/isolation.md) | Worktrees, per-lane setup/teardown/ports, garbage collection. |
| [`docs/reference/cost-accounting.md`](docs/reference/cost-accounting.md) | Spend reports, caps, cap grace, breakers, liveness, ceiling. |
| [`docs/reference/golden-missions.md`](docs/reference/golden-missions.md) | Record, scrub, elide, and replay a mission offline. |

## Development

```
# From the repository root, with Python 3.12+ and uv installed
uv venv && uv sync --frozen --group dev
.venv/bin/ruff check src tests
# mktemp supplies a fresh directory outside this checkout; pytest may clear it.
conductor_test_tmp=$(mktemp -d "${TMPDIR:-/tmp}/conductor-tests.XXXXXX")
.venv/bin/pytest -p no:cacheprovider -o addopts="-q" -n auto --dist loadgroup --basetemp="$conductor_test_tmp"
```

For a focused, provider-free check, add `tests/test_prices.py` to the pytest
command above. Keep `-n auto --dist loadgroup`, cache isolation, and the fresh
external `--basetemp` for both focused and broader runs. Use a temporary path
without spaces: some subprocess fixtures interpolate paths into shell snippets. See [AGENTS.md](AGENTS.md)
for the commit gate; Ruff covers linting, and no separate typecheck/build gate is
configured. In a linked worktree, set `PYTHONPATH="$PWD/src"` and use the main checkout's
venv executable if a local venv has not been installed. Tests use temporary homes
and repositories; do not launch a real mission/fleet or use personal receipts as
a smoke test. There is no browser UI requiring a browser lane.

`-n auto` runs the suite across workers through pytest-xdist (a dev-group dependency; the
runtime is still standard library only) and brings it from about four minutes to about thirty
seconds on an M4 Pro. Every test uses its own temporary home and repository, so the workers
never share state.

Runtime is standard library only. Tests spawn real subprocesses against a
throwaway git repo, so they exercise the timeout, process-group, and no-op
paths for real without spending a token or requiring any fleet to be installed.

`mission.py` is sliced by invariant, never by line count (peer review 2026-09-07,
simplification 4): `graph.py` holds the lane-graph policy (taint to a fixed
point over the whole graph, the resolver as a taint sink; D3, D4),
`approvals.py` holds plan-lane approval consumption (one answer launches one
child, the approval is bound to the child bytes checked at park time; D1, D2),
and `attempts.py` holds attempt parsing and the resume-time trust checks (every
paid dispatch counted once across a retry, a rehearsal never trusted). Each
module re-exports its names through `mission.py`, keeps every call signature,
never imports `mission` at module level (a structural test per module), and
carries the tests that name its invariant. The scheduler body of
`_execute_mission` stays whole: everything in it is closure state, and moving it
would change a signature.
