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
The verdict hashes porcelain status plus every dirty and untracked file's
contents, so editing an already-dirty file cannot hide behind an unchanged count.
Structured Codex and Cursor streams must end in `turn.completed` and `result`
respectively; a cut-short stream may retain its answer, but fails closed.

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
reconstruct the reason from the raw fields. Full stdout, stderr, the exact
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

The clean worktree is pristine: it has no installed dependencies, so the
gate command must bring its own toolchain (an absolute interpreter path, a
`uv run --project`, a `make` target that installs). The worktree is removed
when the gate ends, on every path; `gc` recognises a leftover
`<run_id>-clean` tree and keeps it while its run is live.

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
`all` (default), `any`, or `{"pass": n, "of": [...]}` for a verdict quorum.
TOML files load too. Two dollar fields, two
meanings: `max_cost_usd` is the mission's total, `cap_usd` is one dispatch's
ceiling (see below). A key the loader does not know is refused, so `need`
cannot quietly turn a dependent lane into a root.

Attempt keys are `fleet`, `model`, `effort`, `mode`, `prompt`, `prompt_file`,
`timeout`, `stall_timeout`, `loop_limit`, `max_tool_calls`, `test`, `test_policy`,
`test_surface`, `commit`, `isolate`, `cap_usd`, `no_op_ok`, `schema`, and `verdict`.
`test_policy` is `clean` (the default),
`allow`, or `forbid`; `test_surface` is a list of Git pathspec globs that
replaces the default test/CI surface for that attempt.

### Resuming a mission

Resume an interrupted or failed mission in its existing directory:

```
conductor mission --resume MISSION_ID
```

`MISSION_ID` is the directory name under `$CONDUCTOR_HOME/missions`. Conductor
loads that directory's versioned `mission.json` snapshot, keeps each lane whose
receipt is ok and not skipped, and reruns missing, failed, skipped, interrupted,
or incomplete lanes. A kept lane is never dispatched or committed again, and
its gate is not rerun. Its recorded answer, diff, and verdict artifacts must
still exist. If the lane landed commits, its tip commit must still exist; a
lane with a named deliverable `branch` is kept only while that branch still
points to the recorded tip. A branch left at the previous failed tip is deleted
before that lane reruns, while a branch moved elsewhere is treated as somebody
else's work and the resume is refused.

The `max_cost_usd` ledger covers the whole mission across resumes. Prior run
receipts seed both priced and unpriced spend, so restarting is not a fresh
budget. Collate is kept only when its successful result and answer still exist
and every summarized lane was kept; otherwise it runs again. `running.json`
holds the mission's pid, start time, and host while it is active. A live lock
refuses a second runner; on the same host conductor also compares the process
start time so a recycled pid cannot keep an old lock alive. A lock from another
host is refused rather than guessed stale; after verifying that host is no
longer running the mission, the operator may remove the lock. A demonstrably
stale local lock is removed and recorded in the result.

The boundary is deliberately honest. A lane that half-committed before a crash
has no trusted ok receipt, so it is rerun from its base. Kept lanes are trusted
on their receipts and artifact paths; beyond commit and named-branch existence,
their work is not re-verified during resume.

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
     "prompt": "Review this change against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nReport anything that could cause incorrect behavior, a test failure, or a misleading result; omit style and naming. Per item: file and line, what goes wrong, one sentence of consequence, confidence 1-10. If nothing meets that bar reply exactly NO_FINDINGS. Either answer is complete. Put the entire review in this reply."},
    {"name": "fix", "stage": "fix", "fleet": "codex", "model": "sol", "mode": "write",
     "base": "build", "needs": ["review"], "resume": "build", "no_op_ok": true,
     "test": "pytest -q", "commit": "fix: address cross-vendor review",
     "prompt": "A reviewer from another vendor reported:\n{{lanes.review.answer}}\nFor each item, first reproduce it (a failing test or a demonstrated wrong result); fix only what reproduces. If the review says NO_FINDINGS or nothing reproduces, change nothing and say so."}
  ]
}
```

Review prompts carry no quota. "Find the defects" manufactures findings and
"be conservative" makes current models silently drop real ones, so every
review, judge, and collate prompt states a consequence threshold, says that an
empty result is a complete answer, asks for a citation and a confidence per
item, and demands the whole review in the final reply. The rules and their
evidence are in `AGENTS.md`.

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

#### Thread reuse

A lane can set `resume` to a lane in its `needs` list or to its `base`. When
the upstream lane's final attempt used the same fleet and recorded a session
id, conductor resumes that session instead of paying for the repository
context again. The build → review → fix example above resumes `build` on
the `fix` lane while still waiting for the independent `review` lane:

```json
{"name": "fix", "fleet": "codex", "base": "build",
 "needs": ["review"], "resume": "build"}
```

Session reuse is same-fleet only. A different fleet, or an upstream receipt
without a session id, runs fresh and records why. Every resumed dispatch also
asserts that the fleet returned the requested id; a missing or different id
makes the attempt fail and its fallback starts fresh. This guard is essential
for Antigravity: `agy --conversation MISSING` warns only on stderr, starts a
new conversation, and exits 0.

Prompt templates: `{{lanes.<name>.answer}}`, `{{lanes.<name>.diff}}`,
`{{lanes.<name>.test_touched}}` (`yes (n files: ...)` or `no`),
`{{lanes.<name>.verdict}}`, and `{{mission.prompt}}` (the mission-level prompt,
verbatim). A referenced lane
must be in `needs`; anything else between double braces is refused at load,
so a misspelt name cannot render as `(none)`. Rendering is a single pass, so
braces inside an upstream answer never become new substitutions; each pasted
lane value is fenced with a per-render random nonce and labelled as another
agent's output, not instructions. The trusted mission prompt is substituted
unfenced and outside `template_max_chars`; only lane data shares that budget
(default 40000). In a dry run lane placeholders render as `(dry run: ...)`.

### Lane stages, reviewer policy, and reproduce before fix

A lane may declare `"stage"`: `build`, `review`, or `fix`. A `review` lane
must be read mode; a `build` or `fix` lane must be write mode. Any other
mode for a staged lane, or any stage string outside those three, is refused
at load with the lane named.

A mission may also set a top-level `"policy"` object naming which vendors
may run each stage:

```json
{
  "policy": {
    "review": {"vendors": ["anthropic", "google"]}
  }
}
```

Every attempt of a lane with that stage, fallbacks included, must be on an
allowed vendor; the first one that is not is refused by name: `lane
'<name>' (<attempt>) is on vendor '<v>'; policy allows <list> for stage
<stage>`. Reviewer direction is a policy rather than a free choice because
it is not a wash which vendor reviews which: in one controlled study,
Claude reviewing Codex lifted pass rate 71.6% → 89.7%, while Codex reviewing
Claude dropped it 91.4% → 82.8% ([arXiv 2607.21656](https://arxiv.org/abs/2607.21656);
see `docs/ROADMAP-2026-09.md` item A4). A `policy` entry naming an unknown
stage, an unknown vendor id, or a stage no lane declares is refused at load,
so a typo cannot silently allow everything.

A `stage: review` lane with a `base` is judge hygiene's business too (see
below): it is treated exactly like a verdict lane even with no `verdict`
checklist of its own, refused if it could share a vendor with its base, and
lifted the same way with `self_judging: allow`.

#### Reproduce before fix

A `fix` lane must show its own check failing before it may edit. When a
`stage: fix` write dispatch's fleet has changed the test surface, conductor
adds a detached worktree at the base commit — the mirror image of the clean
gate above, `include` pathspecs instead of `exclude` through the same
temporary index — transplants only the test-surface change into it, and
runs the gate there. That run must **fail**: a check that already fails
against the unfixed base is the reproduction, and only then does the fix's
own gate (or the clean gate, per `test_policy`) run and have to pass.

Three outcomes besides a normal reproduction:

- the fleet changed source but never touched the test surface: refused
  without attempting a base run, no commit, ordinary gate skipped —
  `fix without a reproducing check: no test-surface change`;
- the base run **passes**: the new or changed check reproduces nothing, no
  commit, ordinary gate skipped —
  `reproduce gate passed on the base: the check does not reproduce the finding`;
- the fleet changed nothing at all: this gate has nothing to do with it; the
  existing no-op handling applies unchanged.

The receipt gains a `reproduce` block: `ran`, `exit_code`, `timed_out`,
`tail`, `worktree`, `patch_bytes`, and `verdict` — `reproduced`,
`not-reproduced`, `no-check`, or `skipped` (reason in `tail`) for every
dispatch that is not a `stage: fix` write, including a plain dispatch with
no stage at all. The reproduce worktree is always removed once the gate
ends, exactly like the clean gate's.

### Structured review and a 2-of-3 quorum

A verdict lane declares one fixed checklist. Conductor generates the fleet's
output schema, appends the checklist contract to the prompt, validates the
answer, and computes the pass bit from the criterion booleans. A model-reported
headline never overrides those booleans. Invalid output makes the lane not ok;
a valid fail verdict does not, because the reviewer completed its job and its
data is safe to tally or paste.

```json
{
  "name": "three-reviewer-fix",
  "cwd": "~/Projects/thing",
  "prompt_file": "spec.md",
  "concurrency": 3,
  "require": {"pass": 2, "of": ["review-claude", "review-codex", "review-gemini"]},
  "lanes": [
    {"name": "build", "fleet": "cursor", "model": "grok-4.6", "mode": "write",
     "test": "pytest -q", "commit": "feat: implement the spec"},
    {"name": "review-claude", "fleet": "claude", "model": "opus", "base": "build",
     "prompt": "Review the build against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nAnswer each criterion from the diff; cite the hunk. A verdict with every criterion passing is a complete, expected answer.",
     "verdict": [
       {"id": "correct", "question": "Does the change implement every requirement?"},
       {"id": "tested", "question": "Do focused tests pin the changed behavior?"}
     ]},
    {"name": "review-codex", "fleet": "codex", "model": "sol", "base": "build",
     "prompt": "Review the build against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nAnswer each criterion from the diff; cite the hunk. A verdict with every criterion passing is a complete, expected answer.",
     "verdict": [
       {"id": "correct", "question": "Does the change implement every requirement?"},
       {"id": "tested", "question": "Do focused tests pin the changed behavior?"}
     ]},
    {"name": "review-gemini", "fleet": "antigravity", "model": "gemini-3.8-flash",
     "base": "build",
     "prompt": "Review the build against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nAnswer each criterion from the diff; cite the hunk. A verdict with every criterion passing is a complete, expected answer.",
     "verdict": [
       {"id": "correct", "question": "Does the change implement every requirement?"},
       {"id": "tested", "question": "Do focused tests pin the changed behavior?"}
     ]},
    {"name": "fix", "fleet": "codex", "model": "sol", "mode": "write",
     "base": "build", "needs": ["review-claude", "review-codex", "review-gemini"],
     "no_op_ok": true, "test": "pytest -q", "commit": "fix: address review verdicts",
     "prompt": "Fix the supported failures in these review verdicts:\n{{lanes.review-claude.verdict}}\n{{lanes.review-codex.verdict}}\n{{lanes.review-gemini.verdict}}"}
  ]
}
```

`needs` still waits for lanes to be **ok**, which for a verdict lane means the
judgment is valid and tallyable, not that it passed. Thus the fix lane runs
after a valid dissent and reads exactly which criteria failed through the
fenced, budgeted verdict templates. An invalid reviewer blocks a dependent
lane; when there is no such dependency it simply counts as not passing the
quorum. The quorum also requires every sink outside its `of` list to be ok.

#### Judge hygiene: vendor span, self-judging, and ranking

A quorum's `of` names at most three lanes — three judges with a dissent slot
tally better than five. Three lanes need that dissent slot: `pass` must be
at most 2, so the gate never requires all three lanes to agree, even though
all three can still pass. Two-lane quorums keep the old rule (`pass` may
equal the length of `of`). A quorum's lanes, over
every attempt including fallbacks, must also span at least two vendors
(`anthropic`, `openai`, `google`, `xai`, `cursor`); a quorum confined to one
vendor is refused at load, because a disagreement within one vendor's family
is not the independent evidence a disagreement across vendors is.

A judge never scores its own vendor. At load, conductor refuses a verdict
lane whose `base` could end up on the same vendor as the lane itself — any
attempt's fleet/model against any attempt of the base, fallbacks included,
since which attempt is final is not known yet — and refuses a `collate` that
shares a vendor with any attempt of any lane it would collate over (every
lane in the mission). Both refusals name the two lanes. Set
`"self_judging": "allow"` at the mission's top level to lift them when
self-judging is deliberate; the mission result and `report.md` then carry a
note per pair, `self-judging allowed by the mission: <judge> judges <lane>
on <vendor>`. Any other value for `self_judging` is refused.

`collate` also takes `"rank": true` to become a comparative judge instead of
a free-form synthesis. It asks for exactly one JSON object,
`{"strongest": "<lane name>", "reason": "<one sentence>"}`, with the
generated schema's `strongest` enum restricted to the mission's lane names.
Position in the prompt is itself a bias a judge cannot see past, so
conductor dispatches the ranking collate twice — once with the lanes in
mission order, once reversed — and prices and records both
(`collate.orders`). Agreement across the two orders sets `collate.strongest`
to the winner and the reason given; any disagreement, or an answer naming an
unknown lane or that is not valid JSON, sets `collate.strongest` to null and
the mission not ok (`judge disagreed across orders: <a> vs <b>`, or
`judge order <n> invalid: <reason>`) — a split decision escalates to the
operator rather than being resolved by picking one order's answer. `rank` needs at
least two lanes and, like any structured-output request, is refused at load
on a fleet with no schema flag (Cursor).

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
after a $5 build. A based lane that landed nothing (the fix step after a
clean review) names the tip it was built on, because that tip is then the
pipeline's deliverable; a flat lane that landed nothing has no branch to
name and says so. Upstream lanes keep their run-id branches; the first production
pipeline (2026-09-03) needed a hand rename, which is how this field earned
its place.

#### Early cancel, mechanical ranking, and best-of-n

A `require: any` mission may set `"early_cancel": true` (refused at load on
any other `require`, message `early_cancel needs require: any`, since
cancelling the rest only makes sense once one lane passing is already enough
to win). The moment a sink lane settles ok, conductor cancels every other
lane still running (its fleet is killed the way a cap kill ends one: process
group killed, priced from the watcher's last reading, no gate, no commit)
and skips every lane not yet started, without starting anything new. A
cancelled lane's `LaneResult` is not ok; its `skipped` reads
`cancelled: lane <name> already passed`, so `report.md`'s table shows it and
`require: any` still reads true from the lane that actually passed. The
mission result carries `early_cancel: {"winner": "<lane>", "cancelled":
["<lane>", ...]}`, null when nothing needed cancelling. On resume, a lane
`skipped` for this reason is treated as finished rather than rerun, as long
as the mission it belongs to was itself ok — rerunning it would just repeat
the same cancellation.

Once every lane has settled, conductor ranks the sink lanes that were
actually dispatched (not skipped) on a fixed order, best first, and no model
judgment: ok before not; a passing verdict before a failing or absent one;
an untouched test surface (`test_touched: no`) before a touched one; a gate
exit code of 0 before nonzero before none; a smaller `diffs/<lane>.patch`
before a larger one (no patch ranks last); lower `cost_usd` before higher;
mission order as the final tie-break. Bytes and gate results come before any
model judgment because a judge is the expensive, fallible step and a broken
gate or an empty diff is settled evidence, not something worth a model's
opinion (Generative Verifiers, `docs/ROADMAP-2026-09.md` item B5,
<https://arxiv.org/abs/2408.15240>). The result carries `ranking: [{"lane",
"rank", "ok", "verdict", "test_touched", "gate_exit", "patch_bytes",
"cost_usd"}, ...]`, and `report.md` shows it as a `## Ranking` table. A
mission with a single sink still gets a one-row ranking.

`collate` also takes `"candidates": <int>` (0, the default, means every
lane; set it, it must be at least 2 or refused at load) to judge only the
ranking's top N sink lanes instead of every lane, so a judge is not spent
rereading redundant losers. The forward dispatch sees the chosen lanes in
rank order; a ranking collate's schema enum and reverse order cover only
those lanes too. The prompt says which lanes were left out and why
(`omitted by ranking: <lane>, <lane>`), and the collate receipt records
`candidates: ["<lane>", ...]`. `candidates` larger than the number of
dispatched sink lanes just uses what there is.

### Pausing for the operator

A mission may declare two pause points, either or both:

```json
{
  "max_cost_usd": 10,
  "pause": {"before": ["publish"], "spend_usd": 3}
}
```

`before` names lanes that must not start until the operator has answered;
`spend_usd` parks the mission once the ledger's spend reaches it (refused if
`max_cost_usd` is set and `spend_usd` is not below it). Both are refused if
empty, and `before` is refused naming an unknown lane. These are mission
data, written by whoever wrote the mission file, never a request a fleet's
own output can make: a fleet's word is not evidence, and the operator's
standing rule is that outward actions (push, publish, merge) and spend past
a threshold are the operator's to approve, not a lane's to request for
itself.

The check runs right before a lane that is otherwise ready would be
submitted (its `needs` already satisfied and ok), in this order: is the lane
named in `pause.before` and not yet answered, then has `pause.spend_usd` been
reached and not yet answered. The first one that fires parks the mission:
nothing more starts, but a lane already dispatched is already paid for and
keeps running to its own end rather than being cancelled or killed mid-flight
the way a stop signal or a passed `early_cancel` sink would; every lane still
waiting settles skipped, `paused: <reason>; not started`, and collate does
not run. Conductor then writes `pause.json` beside the mission:

```json
{
  "kind": "lane",
  "lane": "publish",
  "spent_usd": null,
  "threshold": null,
  "asked_at": "2026-09-05T12:00:00+00:00",
  "question": "Lane 'publish' is a pause point; continue the mission?",
  "answer": null,
  "answers": []
}
```

`kind` is `"lane"` or `"spend"`; a spend pause instead carries `spent_usd` and
`threshold` and a null `lane`. The mission result is not ok and carries
`paused: {"kind", "lane", "spent_usd", "threshold", "question"}`; `report.md`
shows one line: `Paused: <question> Resume with: conductor mission --resume
<id> --answer continue|stop`. `conductor mission` exits **4** for a paused
result — neither ok nor failed, waiting.

Resume with an answer:

```
conductor mission --resume MISSION_ID --answer continue
conductor mission --resume MISSION_ID --answer stop
```

`--answer` needs `--resume`, is refused on a mission that is not paused, and
resuming a paused mission without one is refused with the question.
`continue` reruns the parked lane (and anything after it) normally; kept
lanes stay kept and are not paid for again. `stop` dispatches nothing at all:
every lane not already kept settles skipped `paused: operator answered
stop`, and the result stays not ok, carrying the answered record. Either way
the resolved record moves into `pause.json`'s `answers` list; a named lane's
pause point and the spend pause point each fire at most once **after being
answered `continue`** — a `stop` leaves it free to ask again on a later
resume, so a mission can pause more than once across resumes. `mission.json`
itself is never rewritten; the answers live only in `pause.json`. A dry run
never pauses and writes no `pause.json`; a dry-run resume needs no answer and
records nothing. `conductor missions` shows `"paused": true` while
`pause.json`'s `answer` is null, `false` once it is answered.

Evidence (`docs/ROADMAP-2026-09.md` item C2): LangGraph `interrupt()`,
Microsoft request/response events.

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
run in the shared checkout. Any write dispatch on a non-repository cwd is
likewise refused; a read dispatch may proceed in place and says so.

Ctrl-C or `kill` on `conductor dispatch` or `conductor mission` ends the run
the same way a timeout does. Every running dispatch kills its fleet's
process group at its next poll (within `POLL_S`, 2s), is priced from the
watcher's last reading and receipted with `interrupted: true`, and releases
its worktree; a mission skips the lanes that had not started, does not
spend the collate, and still writes its report, marked **Interrupted**. A
second signal synchronously kills every registered fleet and gate group, then
exits 130 for SIGINT or 143 for SIGTERM; `conductor verify --test` installs the
same handlers. Before this,
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
`result.json` are reported as possible in-progress or crashed work and kept;
their worktrees and branches are also kept, with liveness checked again
immediately before every applied remove or branch delete.

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
unreadable receipts are counted as skipped on the total row, and dry runs
are excluded and counted there too: a rehearsal spends nothing, and folding
it into "unpriced" made 25 of the first 43 receipts read as unverified spend.

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

#### Breakers

Four progress breakers run beside the budget watcher in the same two-second
poll loop:

- the stall breaker kills a fleet whose `stdout.log` has not grown for 900
  seconds by default (the gate's own default: a fleet running a long suite
  inside one tool call is silent until it returns);
- the loop breaker kills after 6 identical consecutive tool-call signatures;
- the tool budget kills after more than the configured total tool calls, and
  is off by default;
- the tool-idle breaker kills when no tool call has arrived for the configured
  seconds (tripping with `idle: no tool call for {idle_s}s`), and is off by
  default because a read lane thinking through a long review legitimately makes
  no tool calls.

Set `--stall-timeout 0`, `--loop-limit 0`, `--max-tool-calls 0`, or `--tool-idle-timeout 0` to disable
that breaker. Missions use the corresponding `stall_timeout`, `loop_limit`,
`max_tool_calls`, and `tool_idle_timeout` attempt keys, inherited like `timeout`. A trip kills the
whole process group, skips the gate, and is priced from the watcher's last
reading exactly like a cap kill. The receipt records the reason, tool count,
and last-output age. Claude dispatches use `stream-json --verbose` so their
tool calls reach these breakers before the final result.

#### Liveness

While a dispatch runs, conductor writes `liveness.json` in the run directory on
every poll tick (and once before the first wait), atomically. It records `at`,
`elapsed_s`, `pid`, `stdout_bytes`, `spend_usd`, and, when a breaker exists,
its `tool_calls`, `last_output_age_s`, `last_tool_call_age_s`, and `tripped`
fields. When the run ends, `liveness.json` remains in place while `result.json`
becomes the authoritative receipt. `conductor runs` inspects these files: a run
with a fresh heartbeat is reported as `running`; if the heartbeat is older than
30 seconds (`LIVENESS_STALE_S`), status becomes `silent` to indicate the watching
conductor process stopped writing; and a directory with neither file is reported
as `incomplete`. `silent` is a flag for the operator, nothing more: conductor never
modifies, moves, reclaims, or deletes a run directory, and nothing is reclaimed.

## Commands

- `conductor fleets`: the routing policy, and whether each binary is installed
- `conductor dispatch`: run one prompt on one fleet (`--dry-run` prints the argv,
  `--resume SESSION_ID` continues a fleet session,
  `--schema` requests caller-defined structured output; repeatable `--verdict`
  ids and `--verdict-file` request conductor's checklist schema. `--test` runs a gate afterward,
  `--test-policy {clean,allow,forbid}` chooses how test-surface edits count,
  repeatable `--test-surface PATTERN` replaces the default surface,
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
