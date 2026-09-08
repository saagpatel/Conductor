# pi coding harness — conductor fleet research

Subject: `@earendil-works/pi-coding-agent` **0.84.4** (released 2026-08-28), installed at
`~/.local/share/mise/installs/node/24.14.0/lib/node_modules/@earendil-works/pi-coding-agent`.
Doc paths below are relative to `<pkg>/docs/`. Source paths are relative to `<pkg>/dist/`.
Research date 2026-09-04. Live probes were run with **no provider credentials configured**
(`~/.pi/agent/auth.json` and `models-store.json` are both empty), so every claim about a *successful*
model turn is doc-derived or source-derived, not end-to-end verified. Startup, arg parsing, session-id
handling, and exit codes on the failure path **were** verified live and are marked VERIFIED.

---

## 1. What pi is

| Fact | Value | Source |
|---|---|---|
| Author | Mario Zechner (`badlogic`, creator of libGDX) | `package.json` `"author"` |
| License | MIT | `package.json` `"license"` |
| Repo | `github.com/earendil-works/pi`, monorepo dir `packages/coding-agent` | `package.json` `"repository"` |
| Runtime | Node >= 22.19.0; also ships a Bun-compiled single binary | `package.json` `"engines"`, `build:binary` |
| Release cadence | 272 versions in CHANGELOG; roughly 2–8 releases/month through 2026 (0.84.4 2026-08-28, 0.84.3 08-24, 0.84.2 08-14, 0.84.1 08-07, 0.84.0 08-06, 0.83.0 07-29) | `CHANGELOG.md` |
| Dependencies | 17 runtime deps, no native build step needed (`npm i -g --ignore-scripts` is the documented install) | `package.json`, `docs/index.md` |
| Config dir | `~/.pi/agent` (override `PI_CODING_AGENT_DIR`) | `docs/environment-variables.md#pi-process-configuration` |

**Philosophy.** "Pi is a minimal terminal coding harness. It is designed to stay small at the core while
being extended through TypeScript extensions, skills, prompt templates, themes, and pi packages"
(`docs/index.md`). The design-principles section is explicit about omissions:

> "It intentionally does not include built-in MCP, sub-agents, permission popups, plan mode, to-dos, or
> background bash. You can build or install those workflows as extensions or packages, or use external
> tools such as containers and tmux." — `docs/usage.md#design-principles`

Four default tools: `read`, `write`, `edit`, `bash` (`docs/quickstart.md#first-session`). Three more
read-only built-ins exist but are **off by default**: `grep`, `find`, `ls`. Plus `powershell` on Windows.

**Design comparison.**

| Dimension | pi | Claude Code | Codex CLI | OpenCode |
|---|---|---|---|---|
| Permission prompts | **None at all.** Not "auto-approve" — the feature does not exist. | Hook/permission stack, `--dangerously-skip-permissions` | `approval_policy` (`never`/`on-request`) + `--sandbox` | approve/deny prompts |
| Sandbox | **None built in.** OS/container isolation only (`docs/security.md#no-built-in-sandbox`) | none built in | seatbelt/landlock sandbox, `workspace-write` | none built in |
| Sub-agents | none (spawn pi instances yourself) | Agent tool | none | none |
| MCP | **not built in**; would be an extension | built in | built in | built in |
| Extensions | TypeScript modules, hot-loaded, can register tools/commands/flags/providers/UI (`docs/extensions.md`) | hooks + plugins | none comparable | plugins |
| Skills | Implements the [Agent Skills standard](https://agentskills.io/specification); reads `~/.claude/skills` and `~/.codex/skills` if you add them to `settings.skills` (`docs/skills.md#using-skills-from-other-harnesses`) | native | via AGENTS.md | — |
| Context files | `AGENTS.md` **and** `CLAUDE.md`, walking up from cwd (§8) | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` |
| Compaction | automatic + `/compact`, structured summary format, extension hooks (`docs/compaction.md`) | automatic | automatic | automatic |
| Sessions | JSONL **tree** with `id`/`parentId`; `/tree`, `/fork`, `/clone` branch in place | linear + resume | linear | linear |
| Providers | 40 built-in provider catalogs, plus arbitrary custom providers via `models.json` (§7) | Anthropic only | OpenAI only | many |

The tree-shaped session is the genuinely distinctive part: "Every entry has an `id` and `parentId`, and
the current position is the active leaf" (`docs/sessions.md#branching-with-tree`).

---

## 2. Headless operation

### 2.1 Modes and how they are chosen (VERIFIED, `dist/main.js:79-89`)

```js
function resolveAppMode(parsed, stdinIsTTY, stdoutIsTTY) {
    if (parsed.mode === "rpc")  return "rpc";
    if (parsed.mode === "json") return "json";
    if (parsed.print || !stdinIsTTY || !stdoutIsTTY) return "print";
    return "interactive";
}
```

Consequences for conductor:
- `--mode json` alone is sufficient; `-p` is redundant with it but harmless.
- A subprocess with piped stdout **cannot** land in interactive mode even by accident. Good invariant.
- `--mode json` and `--mode rpc` are mutually exclusive with each other (one `--mode` value).

Print mode also merges piped stdin into the prompt: `cat README.md | pi -p "Summarize this text"`
(`docs/usage.md#modes`). That means conductor must close stdin or pass `< /dev/null` if it does not
intend to feed the prompt via stdin — same discipline as Codex.

### 2.2 `--mode json` event stream

**First line is always the session header** (`docs/json.md#output-format`, VERIFIED live):

```json
{"type":"session","version":3,"id":"01a07000-aa0a-71c2-8ab6-6149f8d21da7","timestamp":"2026-09-05T05:18:06.858Z","cwd":"/private/tmp/pi-probe"}
```

This is the session-id capture point. It is emitted **before** any model call, so it survives even a
credential failure (VERIFIED: printed, then exit 1). With `--session-id X` the header carries `X` verbatim
(VERIFIED).

Then one JSON object per line. Event union (`docs/json.md`, `docs/rpc.md#event-types`,
`dist/modes/json-event.js`):

| Event | Carries | Use for conductor |
|---|---|---|
| `agent_start` | — | run began |
| `turn_start` | — | — |
| `message_start` | `message` (AgentMessage) | — |
| `message_update` | `usage` (cumulative), `assistantMessageEvent` (delta) | **live token + cost metering** |
| `message_end` | `message` — authoritative | **final assistant text + per-message usage/cost/stopReason** |
| `turn_end` | `message`, `toolResults[]` | per-turn accounting |
| `tool_execution_start` | `toolCallId`, `toolName`, `args` | **loop/stall detection** |
| `tool_execution_update` | `toolCallId`, `toolName`, `args`, `partialResult` (accumulated, not delta) | long-tool progress |
| `tool_execution_end` | `toolCallId`, `toolName`, `result`, `isError` | tool failure counting |
| `agent_end` | `messages[]`, `willRetry` | one low-level run done; **not terminal if `willRetry`** |
| `agent_settled` | — | **terminal marker** |
| `queue_update` | pending steering/follow-up queues | — |
| `compaction_start` / `compaction_end` | — | context pressure signal |
| `auto_retry_start` / `auto_retry_end` | — | transient-error retry |
| `summarization_retry_scheduled` / `_attempt_start` / `_finished` | — | — |
| `extension_error` | extension path + error | — |

**Terminal event:** `agent_settled`. `docs/rpc.md#agent_settled`: "Emitted after the full session-level
run settles. At this point Pi will not continue automatically through retry, compaction retry, or queued
follow-up messages." It is emitted through the same `_emit` path as the rest
(`dist/core/agent-session.js:351`), so it appears in `--mode json` too, not only RPC. **`agent_end` is
not terminal** — it carries `willRetry` and may be followed by a retry or compaction.

**Usage / cost shape.** `message_update.usage` is the latest cumulative provider-reported usage
(`docs/json.md`, `docs/rpc.md#message_update-streaming`):

```json
{"input":100,"output":1,"cacheRead":0,"cacheWrite":0,"totalTokens":101,
 "cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}}
```

Caveat stated in both docs: "may remain zero when a provider only reports usage at completion."
The authoritative per-message figure is on `message_end.message.usage` (same shape, plus `stopReason`).
`ToolResultMessage.usage` is optional and reports nested LLM work done *inside* a tool; when present it
contributes to session totals (`docs/rpc.md#toolresultmessage`).

**Cost is in USD.** It is computed by pi from the model catalog, not returned by the provider. Catalog
entries carry `cost` in **dollars per million tokens** — verified in
`node_modules/@earendil-works/pi-ai/dist/providers/data/anthropic.json`:

```json
"claude-fable-5": { "cost": { "input": 10, "output": 50, "cacheRead": 1, "cacheWrite": 12.5 }, ... }
```

Custom `models.json` entries take the same `cost` object plus optional request-wide `tiers` keyed on
`inputTokensAbove` (`docs/models.md#model-configuration`). A custom model with no `cost` defaults to
**all zeros**, so a locally-served model reports $0 — correct, but conductor must not read $0 as "no data".

### 2.3 Exit codes (VERIFIED against `dist/modes/print-mode.js`)

| Situation | Exit code | Notes |
|---|---|---|
| Startup failure (no API key, bad model, bad session arg) | **1** | plain text on **stderr**, not a JSON event. VERIFIED live. |
| `--session <id>` not found | 1 | `dist/main.js` `console.error` + `process.exit(1)` |
| `--session-id` invalid characters, or combined with `--session`/`-c`/`-r` | 1 | `validateSessionIdFlags` |
| `--fork` combined with `--session`/`-c`/`-r`/`--no-session` | 1 | `validateForkFlags` |
| `--mode text` (`-p`) and last assistant message `stopReason` is `error` or `aborted` | **1** | `print-mode.js` sets `exitCode = 1` and prints `errorMessage` to stderr |
| **`--mode json` and the model/agent errors mid-run** | **0** | ⚠️ see below |
| Uncaught exception in the run | 1 | `catch` block returns 1 |
| SIGTERM | 143 | signal handler in `print-mode.js` |
| SIGHUP | 129 | signal handler, non-Windows only |

> **⚠️ Invariant for conductor: in `--mode json`, the exit code is not a failure signal.** The
> `stopReason`-based `exitCode = 1` branch in `print-mode.js` is guarded by `if (mode === "text")`. In JSON
> mode a failed agent run exits **0**. Conductor must determine success by reading the last `message_end`
> with `role === "assistant"` and checking `stopReason`. Stop reasons: `"stop"`, `"length"`, `"toolUse"`,
> `"error"`, `"aborted"` (`docs/rpc.md#assistantmessage`).

### 2.4 `--mode rpc` — what it adds over JSON

Same event stream on stdout, **plus** a command channel on stdin. Strict JSONL, LF only; the doc warns
Node's `readline` is not protocol-compliant because it splits on U+2028/U+2029
(`docs/rpc.md#framing`). Commands carry an optional `id` echoed on the response.

Commands relevant to a dispatcher (`docs/rpc.md#commands`):

| Command | What it buys conductor |
|---|---|
| `prompt` (`streamingBehavior: "steer" \| "followUp"`) | multi-turn on one process |
| `steer` | **mid-run steering** — delivered after the current turn's tool calls, before the next LLM call |
| `follow_up` | queued until the agent stops |
| `abort` | **in-band cancel** without killing the process |
| `clear_queue` | drain and return queued messages (added 0.84.4) |
| `get_session_stats` | **authoritative token + cost + context-window snapshot** (see below) |
| `set_model`, `cycle_model`, `get_available_models` | mid-run model swap |
| `set_thinking_level`, `get_available_thinking_levels` | mid-run effort change |
| `compact`, `set_auto_compaction` | explicit context control |
| `set_auto_retry`, `abort_retry` | retry control |
| `bash`, `abort_bash` | run a shell command *outside* the model loop |
| `switch_session`, `fork`, `clone`, `get_entries`, `get_tree` | session tree navigation |
| `get_last_assistant_text` | final answer without reassembling the stream |

`get_session_stats` response (`docs/rpc.md#get_session_stats`) is the single best cap-enforcement probe:

```json
{"sessionFile":"/path/to/session.jsonl","sessionId":"abc123",
 "userMessages":5,"assistantMessages":5,"toolCalls":12,"toolResults":12,"totalMessages":22,
 "tokens":{"input":50000,"output":10000,"cacheRead":40000,"cacheWrite":5000,"total":105000},
 "cost":0.45,
 "contextUsage":{"tokens":60000,"contextWindow":200000,"percent":30}}
```

`cost` is a plain number in USD and "include[s] assistant messages, usage reported by tools, and
compaction/branch-summary generation across the full session."

**RPC has no permission-decision channel.** There is no `permission_request` event and no approve/deny
command — because pi has no permission model. The only stdin→agent controls are prompt/steer/abort.

There *is* an **extension UI protocol** in RPC mode (`docs/rpc.md#extension-ui-protocol`): if a loaded
extension calls `ctx.ui.select/confirm/input/editor`, pi emits a request on stdout and **waits for a
response on stdin**. In RPC mode `ctx.hasUI` is `true`, so an extension *can* block a headless RPC run.
In `--mode json` and `-p`, `ctx.hasUI` is `false` and "UI methods are no-ops"
(`docs/extensions.md#mode-behavior`) — so **JSON/print mode cannot block on an extension dialog, RPC mode
can.** If conductor uses RPC it must answer or cancel those requests.

### 2.5 Which integration path

For a **Python** dispatcher: parse the `--mode json` stdout stream. It is line-delimited JSON with a
stable schema, the docs ship a Python client example for RPC (`docs/rpc.md#example-basic-client-python`),
and the SDK is Node-only (`@earendil-works/pi-coding-agent` TypeScript, `docs/sdk.md`). Driving pi from
Python via the SDK is not an option; via RPC it is, and the RPC framing rules (split on `\n` only, strip
trailing `\r`) are trivial in Python.

Recommendation: **start with `--mode json` one-shot**, move to `--mode rpc` only if conductor needs
mid-run steering or `abort` without SIGTERM. JSON mode is strictly simpler and has one fewer blocking
hazard (no extension UI channel).

---

## 3. Tool control, permissions, sandbox, cwd

### 3.1 The flags

From `pi --help` and `docs/usage.md#tool-options`:

| Flag | Short | Meaning |
|---|---|---|
| `--tools <csv>` | `-t` | strict **allowlist** across built-in, extension, and custom tools |
| `--exclude-tools <csv>` | `-xt` | **denylist**, applied *after* the allowlist |
| `--no-builtin-tools` | `-nbt` | drop built-in defaults, keep extension/custom tools |
| `--no-tools` | `-nt` | drop everything |

Built-in tool names: `read`, `bash`, `powershell`, `edit`, `write`, `grep`, `find`, `ls`.
Defaults when nothing is specified: `read`, `bash`, `edit`, `write` (`dist/core/sdk.js:139`).

### 3.2 Is the boundary hard?

**Yes, at the request level.** `dist/core/sdk.js:139-145`:

```js
const defaultActiveToolNames = ["read", "bash", "edit", "write"];
const configuredDefaultToolNames = settingsManager.getDefaultTools();
const allowedToolNames = options.tools ?? (options.noTools === "all" ? [] : undefined);
const excludedToolNameSet = excludedToolNames ? new Set(excludedToolNames) : undefined;
const initialActiveToolNames = (options.tools ?? (options.noTools ? [] : (configuredDefaultToolNames ?? defaultActiveToolNames)))
    .filter((name) => !excludedToolNameSet?.has(name));
```

The resulting set becomes the agent's `state.tools`, which is what is serialized into the provider request.
A tool not in the set has no schema entry and cannot be called. The CLI help calls
`pi --tools read,grep,find,ls -p "Review the code in src/"` **"Read-only mode (no file modifications
possible)"**. That claim is sound *for pi's own tools*.

**But it is not a filesystem boundary.** Two gaps:

1. **`bash` is unconfined.** `dist/core/tools/bash.js` spawns the shell with `cwd` set, then does nothing
   else — no command inspection, no path policy. Any write, network call, or `cd ..` is allowed. Read-only
   mode is real *only because `bash` is excluded from the allowlist.*
2. **File tools are not jailed to cwd.** `dist/core/tools/path-utils.js` `resolveToCwd()` resolves relative
   paths against cwd and expands `~`, but absolute paths pass through untouched. There is no containment
   check anywhere in `read.js`, `write.js`, or `edit.js`. So with `write` enabled, the model can write to
   `/etc`, `~/.ssh`, anywhere the user can.

`docs/security.md#no-built-in-sandbox` states this as policy, not oversight:

> "Pi does not include a built-in sandbox... A partial in-process sandbox would be easy to misunderstand
> as a security boundary while still depending on the host shell, filesystem, package managers,
> credentials, and extension code. Real isolation needs to come from the operating system or a
> virtualization/container boundary."

### 3.3 Can the working directory be confined?

**Not by pi.** There is no `--cwd` flag; pi uses `process.cwd()`. Conductor sets it by spawning with
`cwd=<dir>`. Confinement requires an outside boundary. `docs/containerization.md` lists three:

| Pattern | Isolates | Cost |
|---|---|---|
| **Gondolin extension** (`examples/extensions/gondolin/`) | routes `read`/`write`/`edit`/`bash`/`grep`/`find`/`ls` and `!` commands into a local Linux micro-VM, mounting host cwd at `/workspace` | needs Node >= 23.6 and QEMU |
| **Plain Docker** | whole pi process; `-v "$PWD:/workspace"` | provider API keys enter the container |
| **NVIDIA OpenShell** | whole pi process in a policy-controlled sandbox (fs/process/network/credential/inference) | needs an OpenShell gateway |

Note the Gondolin caveat: "Extensions run wherever the `pi` process runs. If you run host `pi` with a
tool-routing extension, other custom extension tools still run on the host unless they also delegate."

### 3.4 Any prompt that can block a headless run?

`docs/security.md#project-trust` and `docs/usage.md#project-trust`, explicit:

> "Non-interactive modes (`-p`, `--mode json`, and `--mode rpc`) do not show a trust prompt."

Trust only gates *loading project-local config*: `.pi/settings.json`, `.pi/extensions|skills|prompts|themes`,
`.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, and project `.agents/skills`. It is not a runtime permission
system. Headless behavior with no saved decision: `defaultProjectTrust: "ask"` (default) and `"never"`
both **ignore** those resources; `"always"` trusts them.

- **`--approve` / `-a`** — trust project-local files for this run (loads `.pi/settings.json`, project
  extensions, project skills, project system-prompt files).
- **`--no-approve` / `-na`** — ignore them for this run.

Context files (`AGENTS.md`, `CLAUDE.md`, `AGENTS.override.md`) load **regardless of trust** unless
`--no-context-files`.

**Two real blocking hazards, both avoidable:**
1. `--session <id>` matching a session in a *different project* triggers an interactive
   `promptConfirm("Fork this session into current directory?")` via `readline` on stdin
   (`dist/main.js`). With stdin closed this will misbehave. **Use `--session-id`, never `--session`.**
2. RPC mode extension UI requests (§2.4). Not present in `--mode json`.

---

## 4. Sessions

### 4.1 Flags

| Flag | Behavior | Source |
|---|---|---|
| `--session-id <id>` | "Use exact project session ID, creating it if missing." Project-scoped exact match. **Cannot** combine with `--session`, `-c`, `-r` (exit 1). | `--help`, `dist/main.js` `validateSessionIdFlags` |
| `--session <path\|id>` | file path, or exact/prefix id match — first in project, then **globally across all projects**. Global match → interactive fork confirmation. Not found → exit 1. | `dist/main.js` `resolveSessionPath` |
| `--fork <path\|id>` | fork into a new session; combine with `--session-id` to name the fork (errors if that id already exists). Conflicts with `--session`/`-c`/`-r`/`--no-session`. | `dist/main.js` `createSessionManager` |
| `--session-dir <dir>` | storage + lookup root. Precedence: `--session-dir` > `PI_CODING_AGENT_SESSION_DIR` > `settings.sessionDir` | `docs/settings.md#sessions` |
| `--no-session` | ephemeral, nothing written. Header still gets a generated id. | VERIFIED |
| `-c` / `--continue` | most recent session for this cwd | `docs/sessions.md` |
| `-r` / `--resume` | **interactive picker — never use headless** | `docs/sessions.md` |
| `-n` / `--name <name>` | display name | `docs/sessions.md#naming-sessions` |

### 4.2 Missing session id: silent-ish new session (VERIFIED)

`dist/main.js` `createSessionManager`:

```js
if (parsed.sessionId) {
    const existingSession = await findLocalSessionByExactId(parsed.sessionId, cwd, sessionDir);
    if (existingSession) return SessionManager.open(existingSession.path, sessionDir);
    console.error(chalk.yellow(`Warning: No project session found with id '<id>'; creating a new session with that id.`));
}
return SessionManager.create(cwd, sessionDir, { id: parsed.sessionId });
```

Live probe output: `Warning: No project session found with id 'conductor-run-0001'; creating a new session
with that id.` on **stderr**, exit continues, header carries `"id":"conductor-run-0001"`. So a resume of a
lost session **does not fail** — it silently starts over. Conductor must treat that stderr warning, or a
missing session file, as the resume-failed signal.

By contrast `--session <id>` not found is a hard `exit 1`.

### 4.3 Session ids are caller-assignable

`dist/core/session-manager.js:15`:

```js
if (!/^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/.test(id)) {
    throw new Error("Session id must be non-empty, contain only alphanumeric characters, '-', '_', and '.', and start and end with an alphanumeric character");
}
```

**This is a genuine advantage over Claude Code and Codex**: conductor can mint the session id itself
(`conductor-<mission>-<step>`) rather than scraping it from output. Invalid ids exit 1 at parse time.

### 4.4 File format

JSONL, one entry per line, tree-structured. Storage: `~/.pi/agent/sessions/`, organized by working
directory; filename `<timestamp>_<sessionId>.jsonl` (`dist/core/session-manager.js:669`).
Entry types (`docs/session-format.md#entry-types`): `SessionHeader`, `SessionMessageEntry`,
`ModelChangeEntry`, `ThinkingLevelChangeEntry`, `CompactionEntry`, `BranchSummaryEntry`, `CustomEntry`,
`CustomMessageEntry`, `LabelEntry`, `SessionInfoEntry`. Every entry has `id` and `parentId`; the active
leaf defines the current context. Version 3 as of 0.84.4.

Known bug now fixed (0.84.4): "resumed sessions corrupting the next appended entry when their JSONL file
lacks a trailing newline" (issue #8345). Relevant if conductor ever writes into a session file itself —
don't.

Shell tools receive `PI_SESSION_ID` and `PI_SESSION_FILE` in their environment
(`docs/environment-variables.md#shell-tool-session-environment`), which gives conductor a second capture
path and lets a hook inside the run self-identify.

---

## 5. Structured output

**There is none.** No `--output-schema`, no `--json-schema`, no `response_format` plumbing, no
structured-final-answer feature. Grepping all of `docs/` for `structured output`, `json schema`,
`outputSchema`, and `response format` returns **zero hits**. The CLI has no such flag. This is the single
biggest gap versus Codex (`--output-schema`) for conductor's verdict/quorum lane.

Workarounds, best first:

1. **Prompt + parse.** Append a schema instruction via `--append-system-prompt` (which accepts *text or a
   file path*, and is repeatable), then extract the last `message_end` assistant text and parse JSON out of
   it. Add a retry-on-parse-failure in conductor. This is what conductor would do for OpenCode too.
2. **A submit-answer tool.** Write a small extension registering a tool whose `parameters` is a typebox
   schema (`docs/extensions.md#custom-tools`, `docs/sdk.md#custom-tools` — `defineTool({ name, parameters:
   Type.Object({...}), execute })`). Run with `--no-extensions -e ./submit-answer.ts --tools
   read,grep,find,ls,submit_answer`. The tool's arguments are then schema-validated by the provider's
   strict-tool mode, and conductor reads them off `tool_execution_end`. The model is not *forced* to call
   it, but the schema is enforced when it does. This is strictly stronger than (1) and costs one small
   TypeScript file.
3. **Grammar tools** exist (`compat.supportsOpenAIGrammarTools`, Lark/regex, enabled for GPT-5+ on several
   providers per `docs/models.md#openai-compatibility`) but are not exposed as a final-answer schema. Not a
   usable path.

Recommendation: ship (1) first, promote to (2) if verdict parsing proves flaky.

---

## 6. Thinking / effort

Seven levels, one flag: `--thinking <level>` with `off | minimal | low | medium | high | xhigh | max`
(`docs/usage.md#model-options`).

Also settable inline as a **model suffix**: `--model sonnet:high`, `--model openai/gpt-4o:low`
(`--help`, `docs/usage.md`). Both `provider/id` and `id:thinking` forms are supported in one string, so
`--model anthropic/claude-opus-5:high` is a single argument. Handy: conductor can carry one model string
per fleet entry instead of two fields.

**Per-provider mapping.** Levels are model metadata, not a global enum. Resolution order
(`dist/core/sdk.js:110-137`): CLI/session value → per-model setting → global default → clamp to model
capability via `clampThinkingLevel(model, thinkingLevel)`. A model with no `reasoning` gets `off`.

Per-model control is `thinkingLevelMap` (`docs/models.md#thinking-level-map`), tristate:

| Value | Meaning |
|---|---|
| omitted | levels through `high` use the provider default mapping; `xhigh` and `max` are unsupported |
| string | supported; this literal is sent to the provider |
| `null` | unsupported; hidden / skipped / **clamped away** |

Real catalog example (`pi-ai/dist/providers/data/anthropic.json`), showing a model that cannot turn
thinking off:

```json
"claude-fable-5": { "reasoning": true,
  "thinkingLevelMap": { "off": null, "xhigh": "xhigh", "max": "max" },
  "compat": { "forceAdaptiveThinking": true, "supportsStrictTools": true } }
```

Wire format varies by provider and is chosen by `compat.thinkingFormat`: `reasoning_effort` (OpenAI-style),
`openrouter` (`reasoning: { effort }`), `deepseek`, `together` (`reasoning: { enabled }`), `baseten`,
`zai`, `qwen` (top-level `enable_thinking`), `chat-template`, `qwen-chat-template`. Anthropic uses either
budget-based thinking or adaptive (`thinking.type: "adaptive"` + `output_config.effort`) when
`forceAdaptiveThinking` is set. Token-budget capping is a separate axis:
`compat.thinkingTokenBudgetField` = `"thinking_token_budget"` (vLLM) / `"thinking_budget"`
(Qwen/DashScope/SGLang) / `"thinking_budget_tokens"` (llama.cpp), clamped to leave >= 1024 tokens for the
answer, driven by the `thinkingBudgets` setting.

Migration note: older configs used `compat.reasoningEffortMap`; that moved to model-level
`thinkingLevelMap`.

Also exposed to shell tools as `PI_REASONING_LEVEL`.

---

## 7. Providers

### 7.1 Built-ins

**40 provider catalogs ship in the package**
(`pi-ai/dist/providers/data/`): `amazon-bedrock, ant-ling, anthropic, azure-openai-responses, baseten,
cerebras, cloudflare-ai-gateway, cloudflare-workers-ai, deepseek, fireworks, github-copilot, google,
google-vertex, groq, huggingface, kimi-coding, minimax, minimax-cn, mistral, moonshotai, moonshotai-cn,
nvidia, openai, openai-codex, opencode, opencode-go, openrouter, qwen-token-plan, qwen-token-plan-cn,
qwen-token-plan-individual, together, vercel-ai-gateway, xai, xiaomi, xiaomi-token-plan-ams,
xiaomi-token-plan-cn, xiaomi-token-plan-sgp, zai, zai-coding-cn` + a `.manifest.json`.

**Default provider is `google`** (`pi --help`: "`--provider <name>` Provider name (default: google)").
Conductor must always pass `--provider` or a `provider/id` model string; never rely on the default.

Subscription OAuth via `/login` (interactive only): ChatGPT Plus/Pro (Codex), Claude Pro/Max, GitHub
Copilot, xAI, OpenRouter, Radius (`docs/providers.md#subscriptions`). Note the Claude Pro/Max line:
"Third-party harness usage draws from extra usage and is billed per token, not against Claude plan limits."

Credential resolution order (`docs/providers.md#resolution-order`): `--api-key` → `auth.json` →
environment variable → `models.json` provider keys. `auth.json` is `0600` and its `key` field supports
`"!command"` (shell, cached for process lifetime), `"$VAR"`/`"${VAR}"` interpolation, `"$$"`/`"$!"`
escapes, and literals.

**Preflight command** (VERIFIED in `dist/main.js` and `dist/cli/auth-command.js`):

```
pi auth check --provider <name> [--model <model>] [--json] [--credentials] [--no-refresh]
```

Exit codes: **0 = ready, 1 = not_ready, 2 = invalid**. With `--json` it prints
`{"status":"ready|not_ready|invalid","provider":"...","reason":"..."}`. This is exactly the readiness probe
a conductor fleet registration should run. Also `pi auth print-api-key` / `print-bearer-token` for handing
credentials to an external client.

### 7.2 OpenRouter

Built-in provider `openrouter`, env `OPENROUTER_API_KEY`, `auth.json` key `openrouter`. OAuth path mints a
**user-controlled API key billed from OpenRouter credits** that does not auto-expire; on headless/SSH boxes
you paste the redirect URL or code into the prompt (`docs/providers.md#openrouter`).

Model id format is OpenRouter's own `vendor/model` (e.g. `anthropic/claude-3.5-sonnet`). With
`--model openrouter/anthropic/claude-3.5-sonnet` the first segment is the pi provider and the rest is the
model id.

Routing control is per-model `compat.openRouterRouting`, sent verbatim as the `provider` field of the
OpenRouter request (`docs/models.md#openai-compatibility`). Full surface seen in the doc example:
`allow_fallbacks, require_parameters, data_collection, zdr, enforce_distillable_text, order, only, ignore,
quantizations, sort {by, partition}, max_price {prompt, completion}, preferred_min_throughput {p50,p90},
preferred_max_latency {p50,p90,p99}`. Set via `modelOverrides` without redefining the model:

```json
{"providers":{"openrouter":{"modelOverrides":{
  "anthropic/claude-sonnet-4":{"compat":{"openRouterRouting":{"only":["amazon-bedrock"]}}}}}}}
```

Caveats: OpenRouter uses `reasoning: { effort }` for thinking; `sessionAffinityFormat: "openrouter"` sends
`x-session-id`. A 0.84.4 fix notes reasoning-mandatory models must not receive `effort: "none"` (PR #8614) —
i.e. `--thinking off` against such a model was previously broken.

### 7.3 Custom providers via `~/.pi/agent/models.json`

Schema (`docs/models.md`). Minimum for a local server is `baseUrl`, `api`, a placeholder `apiKey`, and a
list of `{ "id": ... }`:

```json
{"providers":{"ollama":{
  "baseUrl":"http://localhost:11434/v1","api":"openai-completions","apiKey":"ollama",
  "compat":{"supportsDeveloperRole":false,"supportsReasoningEffort":false},
  "models":[{"id":"gpt-oss:20b","reasoning":true}]}}}
```

> "pi still treats models as requiring auth before they appear in `/model`, so keyless local servers should
> keep a dummy value, save a key for that provider with `/login`, or pass `--api-key`."

**Provider fields:** `baseUrl`, `api`, `apiKey`, `oauth` (only `"radius"`), `headers`, `authHeader`
(auto `Authorization: Bearer`), `models`, `modelOverrides`, `compat`.

**Model fields:** `id` (required), `name`, `api`, `reasoning`, `thinkingLevelMap`, `input`
(`["text"]`/`["text","image"]`), `contextWindow` (default 128000), `maxTokens` (default 16384),
`samplingParams` (merged verbatim into the body, **after** pi's own fields, so its keys win; OpenAI-style
APIs only), `cost` (default all zeros, optional `tiers`), `compat`.

**`api` values — exactly four** (`docs/models.md#supported-apis`): `openai-completions`,
`openai-responses`, `anthropic-messages`, `google-generative-ai`. Settable at provider or model level.
(`azure-openai-responses` also appears as a `samplingParams`-honoring API in the same doc.)

**OpenAI-compat `compat` switches** (full table, `docs/models.md#openai-compatibility`):
`supportsStore`, `supportsDeveloperRole`, `supportsReasoningEffort`, `supportsUsageInStreaming`
(default true), `supportsFinishReason` (default true; when false pi infers `stop`/`toolUse` at stream end),
`maxTokensField` (`max_completion_tokens` | `max_tokens`), `requiresToolResultName`,
`requiresAssistantAfterToolResult`, `requiresThinkingAsText`,
`requiresReasoningContentOnAssistantMessages`, `thinkingFormat`, `chatTemplateKwargs`, `chatTemplateArgs`,
`thinkingTokenBudgetField`, `supportsThinkingTokenBudget` (deprecated alias), `cacheControlFormat`
(only `"anthropic"`), `sendSessionAffinityHeaders`, `sessionAffinityFormat`, `supportsStrictMode`,
`supportsOpenAIGrammarTools`, `deferredToolsMode` (only `"kimi"`), `supportsLongCacheRetention`,
`openRouterRouting`, `vercelGatewayRouting`.

**Anthropic-compat switches:** `supportsEagerToolInputStreaming` (default true; set false if a proxy rejects
per-tool `eager_input_streaming`), `supportsLongCacheRetention`, `sendSessionAffinityHeaders`,
`supportsCacheControlOnTools`, `forceAdaptiveThinking`, `allowEmptySignature` (real Anthropic rejects empty
thinking signatures — only for proxies that need it), `supportsStrictTools`.

**Overriding a built-in provider** is supported: give just `baseUrl` to route Anthropic through a proxy and
all built-in models stay available with their existing auth. Adding a `models` array upserts by `id`.
`modelOverrides` patches built-ins without replacing the list, supporting `name`, `reasoning`,
`thinkingLevelMap`, `input`, partial `cost`, `contextWindow`, `maxTokens`, `samplingParams` (per-key merge),
`headers`, `compat`.

`models.json` reloads whenever `/model` is opened — no restart. Shell-command `apiKey` values resolve
**at request time** with no built-in caching, TTL, or stale-reuse; pi says explicitly it will not infer the
right policy, so wrap slow/rate-limited commands yourself.

### 7.4 llama.cpp (`docs/llama-cpp.md`, read in full)

pi integrates the **llama.cpp router server** as a first-class provider — not the library, and not a
generic OpenAI shim. Start `llama-server` **without** `--model`/`-m`/`-hf` (passing one puts it in
single-model mode, not router mode):

```bash
llama-server --models-dir ~/models --no-models-autoload --jinja \
  --host 127.0.0.1 --port 8080 -ngl 999 -c 32768
```

`--jinja` is what enables tool calling. Configure with `/login llama.cpp` (default URL
`http://127.0.0.1:8080`) or `LLAMA_BASE_URL` / `LLAMA_API_KEY`. Then `/llama` is an in-TUI model manager:
load/unload router models, and **download from Hugging Face** by search or exact `owner/repo[:quant]`
(the llama.cpp server performs the download, so *its* process needs `HF_TOKEN` for gated repos). Only
**loaded** models appear in `/model`. Multi-shard and multimodal models go in subdirectories; restart the
router after adding files manually. pi never silently unloads and never deletes model files.

⚠️ **`/login` and `/llama` are interactive slash commands.** For a headless conductor fleet, the llama.cpp
provider must be pre-configured (env vars or a saved credential) and the model pre-loaded out of band. The
llama.cpp thinking-budget field is `thinking_budget_tokens`.

### 7.5 LM Studio / vLLM / MLX

None has a built-in provider; all three go through `models.json` as `openai-completions`:

```json
{"providers":{
 "lmstudio":{"baseUrl":"http://localhost:1234/v1","api":"openai-completions","apiKey":"lmstudio",
   "compat":{"supportsDeveloperRole":false,"supportsReasoningEffort":false},
   "models":[{"id":"<exact id from /v1/models>","contextWindow":131072,"maxTokens":32768,
              "cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0}}]},
 "vllm":{"baseUrl":"http://localhost:8000/v1","api":"openai-completions","apiKey":"$VLLM_API_KEY",
   "compat":{"supportsDeveloperRole":false,"thinkingTokenBudgetField":"thinking_token_budget"},
   "models":[{"id":"Qwen/Qwen3-Coder-30B","reasoning":true,"contextWindow":262144}]},
 "mlx":{"baseUrl":"http://localhost:8080/v1","api":"openai-completions","apiKey":"mlx",
   "compat":{"supportsDeveloperRole":false,"supportsReasoningEffort":false,"supportsUsageInStreaming":false},
   "models":[{"id":"mlx-community/..."}]}}}
```

`mlx_lm.server` is an OpenAI-compatible server, so the vLLM shape applies; `supportsUsageInStreaming:false`
is the flag to reach for if it does not emit `stream_options.include_usage`. Unverified for MLX
specifically — no doc or issue covers it.

There is open upstream work to make this automatic: issue **#3357** (fetch the model list from
`{baseUrl}/models`) and issue **#4155** (official LM Studio / vLLM / Ollama extensions that probe
`/api/v0/models`, `max_model_len`, and set the `compat` flags per model). Neither has shipped in 0.84.4.

---

## 8. Context files

**Yes, both `AGENTS.md` and `CLAUDE.md`, and it does walk up.** `docs/usage.md#context-files`:

- `~/.pi/agent/AGENTS.md` — global
- parent directories, walking up from cwd
- the current directory

`AGENTS.override.md` in a directory **replaces** `AGENTS.md`/`CLAUDE.md` *from that directory* while other
directories still layer normally.

Disable with `--no-context-files` / `-nc`. Context files load **regardless of project trust**
(`docs/security.md#project-trust`) — the only off switch is the flag.

System prompt: `--system-prompt <text>` replaces the default ("context files and skills are still
appended"); `--append-system-prompt <text>` appends and **accepts text or a file path, repeatable**.
Project `.pi/SYSTEM.md` / `.pi/APPEND_SYSTEM.md` and global `~/.pi/agent/SYSTEM.md` do the same via files,
but those are trust-gated.

⚠️ For conductor this is a real hazard: pi will pick up the operator's `~/.claude`-style `CLAUDE.md` files
anywhere up the tree from the working directory. If conductor wants a hermetic run it should pass
`--no-context-files` and inject the intended instructions with `--append-system-prompt`.

Skills discovery is separate and also broad: `~/.pi/agent/skills/`, `~/.agents/skills/`, project
`.pi/skills/` and `.agents/skills/` up to the git root (trust-gated), packages, `settings.skills`, and
`--skill` (additive even with `--no-skills`). Disable with `--no-skills`. Extensions:
`--no-extensions` (explicit `-e` still loads).

---

## 9. Known pitfalls

### Headless / JSON mode

1. **JSON mode swallows the failure exit code.** Confirmed by source (§2.3). The single most important
   invariant for conductor. Check `stopReason` on the last assistant `message_end`.
2. **`agent_end` is not the end.** It carries `willRetry`; automatic retry (default `retry.maxRetries: 3`,
   `baseDelayMs: 2000`), compaction retry, and queued follow-ups can all continue after it. Wait for
   `agent_settled`.
3. **Streaming usage can be all zeros.** Both `docs/json.md` and `docs/rpc.md` say `usage` "may remain zero
   when a provider only reports usage at completion." A mid-run spend cap built only on `message_update`
   will read $0 for such providers until the turn ends. Cap enforcement must fall back to per-turn
   `message_end` (JSON mode) or poll `get_session_stats` (RPC mode).
4. **JSONL framing is stricter than a naive line reader.** LF only; strip a trailing `\r`; do **not** use a
   reader that splits on U+2028/U+2029 (`docs/rpc.md#framing`). Python's `for line in f` is fine.
5. **Startup errors are plain stderr text, not JSON events.** VERIFIED. Conductor must capture stderr,
   not only parse stdout.
6. **`--session <id>` can open an interactive confirm prompt** when the id matches a session in another
   project. Use `--session-id`.
7. **A missing `--session-id` starts a fresh session with a stderr warning, not an error.** VERIFIED.
8. **RPC extension UI requests block.** `ctx.hasUI` is true in RPC mode; JSON/print mode makes UI a no-op
   (`docs/extensions.md#mode-behavior`).
9. **Fixed in 0.84.3, worth pinning above:** "JSON and RPC `toolcall_start` events omitting the tool call id
   and name" (PR #7953). Loop detection keyed on tool-call ids needs >= 0.84.3.
10. **Fixed in 0.84.4:** resumed sessions corrupting the next entry when the JSONL lacks a trailing newline
    (#8345); large tool results crossing the compaction threshold being sent uncompacted (#6879);
    compaction/branch summaries forcing `toolChoice: "none"` (#8649, #8638).
11. `retry.provider.maxRetries` should stay at `0` — the docs warn that SDK-level retries can absorb
    out-of-quota errors before pi sees them "which may block the agent until the provider quota resets."

### Local OpenAI-compatible providers

12. **`developer` role.** Many servers reject it. `compat.supportsDeveloperRole: false` sends the system
    prompt as `system` instead. Applies to Ollama, vLLM, SGLang, LM Studio.
    (`docs/models.md#minimal-example`)
13. **`reasoning_effort`.** Same servers often reject it: `compat.supportsReasoningEffort: false`.
14. **Auth placeholder required.** Keyless local servers must still carry a dummy `apiKey` or the model will
    not appear in `/model` or `--list-models`.
15. **Tool-calling is the recurring failure mode.** Open issues:
    - **#918** — GLM-4.7-Flash on LM Studio: tool calls fail with `api: openai-responses` +
      `supportsDeveloperRole:false` + `thinkingFormat:"zai"`.
    - **#2865** — Gemma4 on vLLM: `Validation failed for tool "edit": - path: must have required property
      'path'`, with `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4`.
    - A third-party design doc (`v2nic/pi-ollama-provider` issue #6) catalogues: local backends need
      `tool_choice` + `tools` to emit calls; Gemma4 hangs; models like DeepSeek-V4-Flash emit tool calls as
      **XML text** rather than structured calls; Ollama's default 4096 context silently truncates the system
      prompt and its 400-error shape is not recognized by pi's overflow detection.
    - Ollama's OpenAI shim at `/v1` is noted as **lossy for tool calls** (ollama#12557), tracked as a
      follow-up in #4155.
16. **Reasoning-tag leakage.** Handled by `requiresThinkingAsText` and
    `requiresReasoningContentOnAssistantMessages`; 0.84.3/0.84.4 fixed several reasoning-replay bugs for
    OpenAI-compatible streams (#7994, PR #8671).
17. **`supportsFinishReason: false`** exists precisely because some servers omit `finish_reason`; without it
    pi cannot distinguish `stop` from `toolUse` cleanly.
18. **`--jinja` is mandatory on llama.cpp** for tool calling.
19. Do **not** set `thinkingTokenBudgetField` on the generated Qwen catalog — "DashScope rejects
    `thinking_budget` together with `reasoning_effort`."

---

## 10. Proposed conductor fleet entry

### Read mode (analysis, review, verdicts — no mutation)

```
pi
  --mode json
  --no-context-files
  --no-extensions
  --no-skills
  --no-prompt-templates
  --no-approve
  --tools read,grep,find,ls
  --provider <provider>
  --model <provider>/<model-id>:<thinking>
  --session-dir <workdir>/.conductor/pi-sessions
  --session-id <conductor-session-id>
  --append-system-prompt <workdir>/.conductor/pi-answer-contract.md
  -p -- "<prompt>"
```
spawned with `cwd=<workdir>`, `stdin=/dev/null`, `env` carrying only the provider key, wrapped in a
wall-clock `timeout`.

Why each: `--tools read,grep,find,ls` drops `bash`, `write`, `edit` from the model's schema — the CLI's own
documented read-only recipe, and a hard boundary at the request level (§3.2). `--no-approve` guarantees no
project `.pi/settings.json` or project extension can re-add a tool. `--no-context-files` stops pi from
inhaling stray `AGENTS.md`/`CLAUDE.md` up the tree. `--no-extensions/--no-skills/--no-prompt-templates`
make the run reproducible.

### Write mode

Same, minus the read-only allowlist:

```
pi --mode json --no-context-files --no-extensions --no-skills --no-prompt-templates --no-approve
   --tools read,write,edit,bash,grep,find,ls
   --provider <p> --model <p>/<id>:<thinking>
   --session-dir <workdir>/.conductor/pi-sessions --session-id <sid>
   --append-system-prompt <contract.md>
   -p -- "<prompt>"
```

There is **no auto-approve flag to pass** — pi has no permission prompts to suppress. `--approve` is about
project *config* trust, not tool permission, and should stay **off** so a repo cannot inject an extension.

**Isolation is conductor's job, not pi's.** For untrusted repos, wrap the whole invocation in Docker per
`docs/containerization.md`, or route tools through the Gondolin micro-VM extension. Without that, write
mode can touch anything the user can (§3.2).

### Resume

`--session-id <sid>` on the next dispatch, same `--session-dir` and same cwd (sessions are keyed by working
directory). Guard: a wrong id produces a **stderr warning and a fresh session, exit 0** — conductor must
assert the session file existed before the call, or grep stderr for
`No project session found with id`.

### Session-id capture

Not needed — conductor **mints** it (`^[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]$`). Confirm by reading the
first stdout line: `{"type":"session","version":3,"id":"<sid>","cwd":"..."}`. VERIFIED live.

### Cap mode

**Watcher, on the JSON stream, with a post-hoc reconcile.** No native cap flag exists.

- Accumulate `message_end.message.usage.cost.total` (USD) and `usage.totalTokens`; also add
  `toolResult.usage` when present.
- Treat `message_update.usage` as advisory only — it can stay zero for some providers (pitfall 3).
- On breach: `SIGTERM` the process (handled cleanly, exit 143, children killed via
  `killTrackedDetachedChildren`). In RPC mode prefer the in-band `abort` command.
- Reconcile after exit from the session JSONL, or from `get_session_stats` in RPC mode.
- Local models report `cost: 0` if their `models.json` entry has no `cost` block. Cap those on **tokens or
  wall clock**, not dollars.

### Stall / loop detection

`tool_execution_start` gives `toolCallId`, `toolName`, and full `args`; `tool_execution_end` gives `result`
and `isError`. Hash `(toolName, args)` for repeat detection; count consecutive `isError: true`. Requires
>= 0.84.3 for `toolcall_start` to carry `id`/`toolName` (fix #7953).

### Success determination

1. Stream must contain `agent_settled`.
2. Last `message_end` with `role === "assistant"` must have `stopReason === "stop"`.
3. `stopReason` of `error` / `aborted` / `length` is a failure or truncation; `errorMessage` carries the
   detail.
4. Process exit code is only meaningful for **startup** failures (exit 1) and signals (143/129).

### Preflight (fleet registration)

`pi auth check --provider <p> --json` → exit 0 ready / 1 not_ready / 2 invalid. Cheap, network-light,
machine-readable.

### Invariants this breaks

| Conductor invariant | Status with pi |
|---|---|
| Machine-readable final answer against a JSON schema | **BROKEN.** No structured-output support at all (§5). Needs prompt-and-parse or a custom submit-answer tool. |
| Non-zero exit on agent failure | **BROKEN in `--mode json`.** Must read `stopReason` (§2.3). |
| Hard read-only mode | **HOLDS** for pi's tools (`--tools read,grep,find,ls` removes write/edit/bash from the schema). |
| Confined to the working directory | **BROKEN.** No path jail; `bash` unconfined; absolute paths pass through (§3.2). Needs Docker/Gondolin/OpenShell. |
| No interactive prompt can stall a run | **HOLDS in `--mode json`** (trust prompt suppressed, extension UI is a no-op). **At risk in `--mode rpc`** (extension UI channel) and with `--session <id>` (cross-project fork confirm). |
| Caller-assigned resumable session id | **HOLDS, and better than peers** — `--session-id` accepts an arbitrary safe string. |
| Per-step usage and USD cost | **HOLDS**, with the streaming-zero caveat; cost comes from pi's own catalog, so custom/local models need a `cost` block or they price at $0. |
| Reproducible run independent of ambient config | **HOLDS only with the full `--no-*` set** — pi otherwise reads `CLAUDE.md`/`AGENTS.md` up the tree, `~/.agents/skills`, global extensions, and `~/.pi/agent/settings.json`. |

### Fit assessment

pi is a **good** conductor fleet for read/review lanes and a **usable** one for write lanes behind a
container. Its distinguishing wins are the caller-assigned session id, the 40-provider catalog with USD
pricing baked in, first-class local-model support via `models.json`, and a clean 7-level thinking ladder
with a `model:level` shorthand. Its two real gaps are the missing structured-output contract and the
JSON-mode exit code, both of which conductor can work around with code it already needs for other fleets.

---

## Sources

Primary (bundled docs, read in full unless noted): `docs/index.md`, `docs/quickstart.md`, `docs/json.md`,
`docs/sessions.md`, `docs/security.md`, `docs/containerization.md`, `docs/llama-cpp.md`,
`docs/providers.md`, `docs/models.md`, `docs/environment-variables.md`, `docs/skills.md`;
partial: `docs/rpc.md` (§Starting, Framing, Commands/Prompting, get_session_stats, Events, Error Handling,
Types, Python example), `docs/usage.md` (§Context Files onward), `docs/settings.md`
(§Compaction, Retry, Message Delivery, Shell, Tools, Sessions, Model Cycling), `docs/sdk.md`
(§Tools, Custom Tools, Run Modes), `docs/compaction.md` (§Overview, Compaction), `docs/extensions.md`
(§Mode Behavior, Error Handling); `CHANGELOG.md`; `package.json`.

Source read: `dist/main.js`, `dist/modes/print-mode.js`, `dist/modes/json-event.js`, `dist/core/sdk.js`,
`dist/core/session-manager.js`, `dist/core/tools/path-utils.js`, `dist/core/tools/bash.js`,
`dist/cli/auth-command.js`, `node_modules/@earendil-works/pi-ai/dist/providers/data/anthropic.json`.

Live probes (this machine, no credentials): `pi --help`, `pi auth --help`, `PI_OFFLINE=1 pi --list-models`,
`PI_OFFLINE=1 pi --mode json --no-session --tools read,grep,find,ls -p "say hi"`,
`PI_OFFLINE=1 pi --mode json --session-dir ... --session-id conductor-run-0001 --tools read -p "hi"`.

Web: <https://github.com/earendil-works/pi> · [rpc.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) ·
[sdk.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md) ·
[issue #272 Agent SDK equivalent](https://github.com/earendil-works/pi/issues/272) ·
[discussion #4444 ACP support](https://github.com/earendil-works/pi/discussions/4444) ·
[issue #918 LM Studio tool calls](https://github.com/earendil-works/pi/issues/918) ·
[issue #2865 Gemma4 on vLLM](https://github.com/earendil-works/pi/issues/2865) ·
[issue #4155 official local-LLM provider extensions](https://github.com/earendil-works/pi/issues/4155) ·
[issue #3357 dynamic model list](https://github.com/earendil-works/pi/issues/3357) ·
[v2nic/pi-ollama-provider issue #6](https://github.com/v2nic/pi-ollama-provider/issues/6) ·
[Armin Ronacher on pi](https://lucumr.pocoo.org/2026/1/31/pi/) ·
[Pragmatic Engineer: Building Pi](https://newsletter.pragmaticengineer.com/p/building-pi-and-what-makes-self-modifying)
