# OpenCode + Ollama as conductor fleets

Research date 2026-09-04. Machine: Apple M4 Pro, 48 GB unified memory, 243 GB free.
Versions verified on this machine: `opencode 1.18.20` (Homebrew, `/opt/homebrew/bin/opencode`),
`ollama 0.33.3` (server up on 127.0.0.1:11434, **zero models pulled**, `/api/tags` → `{"models":[]}`).

Everything marked **VERIFIED** was run live on this machine today against the free model
`opencode/nemotron-3.5-lightning-free` (OpenCode Zen free tier, $0.00 cost confirmed in every
`step_finish`). Everything marked **DOCS** is from opencode.ai/docs (footer "Last updated: Sep 5,
2026") or the anomalyco/opencode issue tracker. Everything marked **UNVERIFIED** could not be
tested without pulling a model or spending money.

Upstream repo is **`anomalyco/opencode`**, not `sst/opencode` (the old repo name; the docs "Edit
page" link resolves to `https://github.com/anomalyco/opencode/edit/dev/packages/web/src/content/docs/cli.mdx`).
Issue searches against `sst/opencode` return HTTP 422.

---

## PART 1 — OpenCode as a headless fleet

### 1.1 `opencode run` flags (VERIFIED against `opencode run --help`, 1.18.20)

Exact flag names, copied from `--help`:

```
opencode run [message..]
      --command      the command to run, use message for args            [string]
  -c, --continue     continue the last session                          [boolean]
  -s, --session      session id to continue                              [string]
      --fork         fork the session before continuing (requires --continue or --session)
      --share        share the session                                  [boolean]
  -m, --model        model to use in the format of provider/model        [string]
      --agent        agent to use                                        [string]
      --format       format: default (formatted) or json (raw JSON events)
                             [string] [choices: "default", "json"] [default: "default"]
  -f, --file         file(s) to attach to message                         [array]
      --title        title for the session                               [string]
      --attach       attach to a running opencode server                 [string]
  -p, --password     basic auth password (defaults to OPENCODE_SERVER_PASSWORD)
  -u, --username     basic auth username (defaults to OPENCODE_SERVER_USERNAME or 'opencode')
      --dir          directory to run in, path on remote server if attaching
      --port         port for the local server (defaults to random port)  [number]
      --variant      model variant (provider-specific reasoning effort, e.g., high, max, minimal)
      --thinking     show thinking blocks                               [boolean]
  -i, --interactive  run in direct interactive split-footer mode  [default: false]
      --auto         auto-approve permissions that are not explicitly denied (dangerous!)
                                                        [boolean] [default: false]
      --pure         run without external plugins                       [boolean]
      --log-level    DEBUG | INFO | WARN | ERROR
      --print-logs   print logs to stderr
```

There is **no** `--effort`, no `--output-schema`, no `--json-schema`, no `--timeout`, and no
budget/spend cap flag. `--pure` disables **external plugins only**; it does **not** disable MCP
servers (VERIFIED: with `--pure` and the operator's global config, `filesystem_write_file` was
still offered and used).

### 1.2 The `--format json` event stream (VERIFIED, exact bytes)

Output is **NDJSON on stdout**, one JSON object per line. Every object has the same envelope:

```json
{"type": "<event>", "timestamp": 1788583086027, "sessionID": "ses_f90242c07ffeEJ07Qs1iS3kwuQ", "part": { ... }}
```

Observed `type` values across live runs: `step_start`, `text`, `tool_use`, `step_finish`.

**`step_finish` is where usage lives, and it is emitted per model step** (VERIFIED — a two-step
run produced two `step_finish` events with different token counts):

```json
{"type":"step_finish","timestamp":1788583107031,
 "sessionID":"ses_f9023baa8ffeJXpIelgoS02hy8",
 "part":{"id":"prt_...","reason":"tool-calls","messageID":"msg_...","sessionID":"ses_...",
  "type":"step-finish",
  "tokens":{"total":31043,"input":28799,"output":25,"reasoning":43,
            "cache":{"write":0,"read":2176}},
  "cost":0}}
```

`reason` takes the AI-SDK finish reasons: `"tool-calls"` on an intermediate step, `"stop"` on the
final one.

**`tool_use`** carries the full tool call *and its result* in one event (there is no separate
start/end pair on the CLI stream):

```json
{"type":"tool_use","timestamp":...,"sessionID":"ses_...",
 "part":{"type":"tool","tool":"bash","callID":"call-ccc3e34e-...",
  "state":{"status":"completed",
           "input":{"command":"echo hi"},
           "output":"hi\n",
           "metadata":{"output":"hi\n","exit":0,"truncated":false},
           "title":"echo hi",
           "time":{"start":...,"end":...}},
  "id":"prt_...","sessionID":"ses_...","messageID":"msg_..."}}
```

`state.status` is `"completed"` on success and `"error"` on a permission denial, with the denial
text in `state.error` (VERIFIED — see §1.4).

**`text`** carries a completed text block (not deltas):
`{"type":"text",...,"part":{"type":"text","text":"PONG","time":{"start":...,"end":...}}}`.

Answers to the specific questions:

| Question | Answer |
|---|---|
| Token usage per step? | **Yes** — `step_finish.part.tokens` `{total,input,output,reasoning,cache{read,write}}` |
| Cost per step? | **Yes** — `step_finish.part.cost`, a float in USD |
| Session id in the stream? | **Yes** — top-level `sessionID` on **every** event, including the first |
| Final "result" event? | **No.** The stream ends on the last `step_finish` (`reason:"stop"`) and the process exits 0. There is no summary envelope, no total, no exit-status object. |
| Model id in the stream? | **No** — open issue #40544, "run --format json events don't say which model produced them" |
| Agent name in the stream? | **No** — open issue #46652, VERIFIED here (`grep -c '"agent"'` → 0 on our runs) |
| Permission events in the stream? | **No** — open issue #39459. Denials appear only as a `tool_use` with `state.status:"error"`. |

The **server-side** event names (from the OpenAPI spec, richer than the CLI's) are
`session.next.step.started`, `session.next.step.ended`, `session.next.tool.called`,
`session.next.tool.success`, `session.next.tool.failed`, `session.next.text.delta`,
`session.next.retried`, `session.idle`, `session.error`, `permission.v2.asked`, `server.connected`
and ~90 more. `session.next.step.ended` carries the same `{cost, tokens{input,output,reasoning,
cache{read,write}}}` shape plus a `files: string[]` list of files the step touched. The CLI's
`step_finish` is a flattened projection of it.

### 1.3 Getting the spec yourself

`opencode serve --port N` then **`GET http://127.0.0.1:N/doc`** returns the raw OpenAPI 3.1 JSON
(479 KB, 472 schemas) — despite the docs describing it as "HTML page with OpenAPI spec"
(VERIFIED: the body starts `{"openapi":"3.1.0","info":{"title":"opencode",...`). `GET /openapi.json`
returns an HTML shell instead; use `/doc`.

### 1.4 Permissions when non-interactive — **the biggest finding**

**A permission set to `"ask"` is silently ALLOWED in `opencode run`, not denied and not hung.**

VERIFIED: with `permission.bash = "ask"` and **no** `--auto`, the model called `bash` with
`echo hi` and it **executed** (`"status":"completed","output":"hi\n","exit":0`). No prompt, no
stall, no event announcing the decision.

Note this contradicts issue #44267 and #39459, which describe headless `ask` as *auto-rejected*.
The difference is rule ordering, explained next — but the practical rule for conductor is the same
either way: **never treat `"ask"` as a gate. Only `"deny"` is a gate.**

#### Why a `deny` can also fail to hold: last-match-wins across merged configs

Permission rules are flattened into an ordered list and **the last matching rule wins** (DOCS:
"Rules are evaluated by pattern match, with the last matching rule winning"). Config sources are
merged with the **global** `~/.config/opencode/opencode.json` appended **after** anything you
supply via `OPENCODE_CONFIG` or `OPENCODE_CONFIG_DIR`.

VERIFIED on this machine, from `GET /agent` with `OPENCODE_CONFIG_DIR` set to a directory whose
config denied bash:

```
118 {'permission': 'edit',  'pattern': '*', 'action': 'deny'}   <- my config
119 {'permission': 'bash',  'pattern': '*', 'action': 'deny'}   <- my config
120 {'permission': 'webfetch','pattern':'*','action': 'deny'}   <- my config
124 {'permission': '*',     'pattern': '*', 'action': 'allow'}  <- OPERATOR GLOBAL, wins
125 {'permission': 'write', 'pattern': '*', 'action': 'ask'}    <- operator global
```

Rule 124 comes from the operator's global `"permission": {"*": "allow"}` and overrides rules
118-120. Result: my `bash: deny` did nothing and the command ran. **Neither `OPENCODE_CONFIG` nor
`OPENCODE_CONFIG_DIR` isolates a run from the global config** (VERIFIED: the operator's five MCP
servers and their `"*": "allow"` were still present under both).

#### The lever that does work: agent-level permissions

Agent rules are appended **last**, after the global config, so they win (DOCS: "Agent permissions
are merged with the global config, and agent rules take precedence"). VERIFIED — with the same
global config in place, an agent markdown file's denies landed at indexes 128-133, after the
global `*: allow` at 122:

```
122 {'permission': '*',     'pattern': '*', 'action': 'allow'}
128 {'permission': 'bash',  'pattern': '*', 'action': 'deny'}   <- agent frontmatter, wins
129 {'permission': 'edit',  'pattern': '*', 'action': 'deny'}
130 {'permission': 'write', 'pattern': '*', 'action': 'deny'}
133 {'permission': 'task',  'pattern': '*', 'action': 'deny'}
```

Agent files are discovered from **both** `<config-dir>/agent/*.md` and `<config-dir>/agents/*.md`
(VERIFIED: both loaded and produced identical rule lists), and per-project from `.opencode/agent/`
in the repo (DOCS).

#### MCP tools are NOT covered by the permission keys — a real breach

VERIFIED, and this is the one that matters most. Running a "read-only" agent with
`permission: {bash: deny, edit: deny, write: deny, ...}`, I asked it to run a shell command and
write a file. `bash` was correctly refused. The model then called **`filesystem_write_file`**, an
MCP tool from the operator's global `filesystem` MCP server rooted at `~`, and wrote
`pwn.txt` to disk. The file existed on disk afterwards with the expected content.

The permission keys (`read`, `edit`, `bash`, `glob`, `grep`, `task`, `skill`, `lsp`, `question`,
`webfetch`, `websearch`, `external_directory`, `doom_loop`) are keyed by **built-in tool name**.
An MCP tool named `filesystem_write_file` matches none of them, and the catch-all `*: allow`
matches it.

#### The recipe that actually produces a hard read-only agent (VERIFIED)

Both halves are required. Config half, disabling every MCP server the global config defines:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "lsp": {}, "formatter": {},
  "mcp": {
    "sequential-thinking": { "enabled": false },
    "memory":              { "enabled": false },
    "filesystem":          { "enabled": false },
    "github":              { "enabled": false },
    "context7":            { "enabled": false }
  }
}
```

Agent half, `<config-dir>/agent/roagent.md`:

```markdown
---
description: Hard read-only research agent
mode: primary
tools:
  bash: false
  edit: false
  write: false
  patch: false
  todowrite: false
  webfetch: false
permission:
  bash: deny
  edit: deny
  write: deny
  webfetch: deny
  websearch: deny
  task: deny
  external_directory: deny
---
Read-only. Never modify anything.
```

Result (VERIFIED): the model's tool list collapsed to `glob, grep, invalid, read, skill` and it
reported *"the only tools available are glob, grep, read, and skill"*. No file was written. Note
`skill` survives; add `skill: false` if skills should not load.

The `tools:` map is the stronger of the two halves — it removes the tool from the model's schema
entirely rather than refusing it at call time, which also saves the tokens.

#### `--dir` does NOT confine the filesystem

VERIFIED. With `--auto` and `--dir <wk>`, asked to write to an absolute path outside `<wk>`, the
agent ran `bash`, then `write`, and created the file outside the directory. `--dir` sets the
project root, not a sandbox. **OpenCode has no filesystem sandbox** comparable to Codex's
`--sandbox read-only`.

The confinement lever is `external_directory: deny` in agent frontmatter. VERIFIED — with it set,
the same prompt produced:

```
TOOL write error 'The user has specified a rule which prevents you from using this specific
                  tool call. Here are some of the relevant rules [...external_dire...'
TOOL bash  error  (same)
```

and no file was created, even when the model fell back to a bash heredoc. This is the only tested
way to keep an OpenCode write-mode fleet inside its worktree.

Caveat worth carrying: `external_directory` **defaults to `"ask"`** (DOCS: "doom_loop and
external_directory default to `ask`"), and `ask` is not a gate headless. It must be set to `deny`
explicitly.

### 1.5 `--auto`

`--auto` "auto-approve permissions that are not explicitly denied (dangerous!)". Explicit `deny`
rules are still enforced under `--auto` (DOCS, and VERIFIED: the `external_directory: deny` test
above ran **with** `--auto` and still blocked). So `--auto` plus targeted denies is a coherent
write mode.

### 1.6 `--variant` (reasoning effort)

`--variant <name>` is the effort dial. Built-in variant names (DOCS, models page):

| Provider | Variants |
|---|---|
| Anthropic | `high` (default), `max` |
| OpenAI | roughly `none`, `minimal`, `low`, `medium`, `high`, `xhigh` (varies by model) |
| Google | `low`, `high` |

Custom variants are defined per provider/model in config under
`provider.<id>.models.<model>.variants.<name>` with keys like `reasoningEffort`, `textVerbosity`,
`reasoningSummary`, `budgetTokens`, or `disabled: true`.

**Warning (VERIFIED):** `--variant high` against a model with no such variant was accepted
silently, exited 0, and was recorded as `"variant": "high"` in the session export. There is no
validation and no error. A conductor effort ladder must be checked against the model's declared
variants, not assumed.

### 1.7 Sessions, resume, export, stats

- **Session id**: on every JSON event as `sessionID`, format `ses_[A-Za-z0-9]{26}`.
- **Resume**: `opencode run --session <id> "<prompt>"` — VERIFIED. Told the codeword TANGERINE in
  run A, asked "What was the codeword?" in run B with `--session`; it answered `TANGERINE` and
  reused the same session id. `--continue`/`-c` resumes the last session; `--fork` branches it.
- **`opencode session list --format json -n N`** — VERIFIED, returns
  `[{id, title, updated, created, projectId, directory}]`. `--format` choices are `table` (default)
  and `json`.
- **`opencode export <sessionID>`** — VERIFIED, prints JSON `{info, messages}` to stdout with the
  banner line `Exporting session: ses_...` on **stderr**. `info` carries session totals:
  ```json
  {"id":"ses_...","slug":"crisp-cabin","projectID":"global",
   "directory":"/...","title":"...","agent":"roagent",
   "model":{"id":"nemotron-3.5-lightning-free","providerID":"opencode","variant":"default"},
   "version":"1.18.20",
   "summary":{"additions":0,"deletions":0,"files":0},
   "cost":0,
   "tokens":{"input":13848,"output":130,"reasoning":1007,"cache":{"read":13056,"write":0}},
   "permission":[...],"time":{"created":...,"updated":...}}
  ```
  `--sanitize` redacts transcript and file data. This is the ideal **post-hoc** cap source: one
  call, session totals, plus a diff summary (`additions`/`deletions`/`files`) that conductor could
  cross-check against its own byte check.
- **`opencode stats`** — **table output only, no JSON flag** (VERIFIED; flags are `--days`,
  `--tools`, `--models`, `--project`). Not a machine surface. Use `export` instead.

### 1.8 `opencode serve` HTTP API — the better integration surface

`opencode serve [--port N] [--hostname H] [--cors O]`, default port 4096, hostname 127.0.0.1.
`OPENCODE_SERVER_PASSWORD` enables HTTP basic auth (username defaults to `opencode`, override with
`OPENCODE_SERVER_USERNAME`). `GET /global/health` → `{"healthy":true,"version":"1.18.20"}`
(VERIFIED).

Endpoints that matter for a dispatcher (DOCS, cross-checked against the live spec):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/session` | create session; body `{parentID?, title?}` |
| `POST` | `/session/:id/message` | send prompt **and wait**; returns `{info, parts}` |
| `POST` | `/session/:id/prompt_async` | send prompt, return `204` immediately |
| `POST` | `/session/:id/abort` | abort a running session → `boolean` |
| `POST` | `/session/:id/fork` | fork at a message |
| `GET` | `/session/:id/diff` | `FileDiff[]` for the session |
| `POST` | `/session/:id/permissions/:permissionID` | answer a permission request; body `{response, remember?}` |
| `GET` | `/event` | SSE bus; first event is `server.connected`, then bus events |
| `GET` | `/session/status` | `{[sessionID]: SessionStatus}` |
| `GET` | `/agent` | resolved agent list **including flattened permission rules** |
| `GET` | `/config` | fully merged config |
| `GET` | `/doc` | raw OpenAPI 3.1 JSON |

**Is it better than `run`? Yes, on four counts,** and I would recommend it if conductor wants
OpenCode to be a first-class fleet rather than a cheap add:

1. It is the only way to get a **JSON schema on the final message** (§1.9).
2. It exposes `permission.v2.asked` on the bus and `POST /session/:id/permissions/:id` to answer,
   so a permission decision becomes conductor's rather than a silent auto-allow.
3. `POST /session/:id/abort` is a clean kill; with `run` the only lever is killing the process
   group, and issue #44901 says the process sometimes will not exit on its own.
4. `GET /agent` lets conductor **verify the resolved permission rule list before spending a
   token** — which is exactly how the `*: allow` override above was caught. A preflight that
   asserts the last matching rule for `bash`/`edit`/`external_directory` is `deny` would turn a
   silent breach into a refusal at dispatch time.

The cost is that conductor would own session lifecycle, SSE parsing, and server health, instead of
spawning a process and reading its stdout. `opencode run --attach http://localhost:4096` is a
middle path: process-per-dispatch ergonomics, shared server, no MCP cold boot per run (DOCS
explicitly recommends this "to avoid MCP server cold boot times on every run").

### 1.9 Structured output

**The CLI cannot do it. The server can.**

`opencode run` has no schema flag. But `POST /session/:id/message` accepts:

```json
{"parts":[{"type":"text","text":"..."}],
 "agent":"roagent",
 "model":{"providerID":"opencode","modelID":"..."},
 "variant":"high",
 "tools":{"bash":false,"write":false},
 "system":"...",
 "format":{"type":"json_schema","schema":{...},"retryCount":3}}
```

`OutputFormat` is `{"type":"text"}` or `{"type":"json_schema","schema":<JSONSchema>,"retryCount":int}`.
The parsed result lands on `AssistantMessage.structured`, and a schema failure surfaces as
`StructuredOutputError` with `{message, retries}` — a first-class error, not silent prose. Note the
request body also accepts a per-message `tools` boolean map and a `system` override, so a
read-only, schema-constrained, single-shot dispatch is fully expressible in one HTTP call.

**Practical workaround if conductor stays on the CLI:** put the schema in the prompt, ask for a
fenced JSON block, and validate the last `text` event yourself, retrying on parse failure. That is
strictly worse than what Codex and Claude Code already give conductor, and it is the strongest
single argument for the `serve` path.

### 1.10 Ollama and LM Studio as OpenCode providers

Ollama (DOCS, providers page) — a custom provider over the OpenAI-compatible shim:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "qwen3.8": { "name": "Qwen3.8 27B" } }
    }
  }
}
```

Model ids are then `ollama/<model>`. DOCS carry a specific warning: *"If tool calls aren't working,
try increasing `num_ctx` in Ollama. Start around 16k - 32k."* This matters — a coding agent's
system prompt plus tool schemas alone ran **~31 K input tokens** in my smoke tests, so Ollama's
default context will truncate the tool definitions and tool calling will fail in ways that look
like model stupidity.

Ollama can also auto-configure itself for OpenCode (DOCS references "the Ollama integration docs").

**LM Studio** is a first-class provider in the OpenCode directory and is **already authenticated on
this machine** — `opencode models` lists an `lmstudio` provider alongside `opencode`, `openrouter`,
`google`, and `amazon-bedrock`.

**Ollama Cloud** in OpenCode (DOCS): generate an API key at ollama.com Settings → Keys, run
`/connect`, search "Ollama Cloud". Critically — *"Before using cloud models in OpenCode, you must
pull the model information locally: `ollama pull gpt-oss:20b-cloud`"*. So a cloud model still needs
a local `pull` of its manifest before OpenCode will route to it.

### 1.11 Known headless pitfalls (issue tracker, all open as of today)

Ranked by how much they would hurt conductor:

1. **#44901 — `opencode run --format=json` emits terminal `step_finish` but never exits.** Opened
   2026-08-25, open. The stream completes with `reason:"stop"` and the process stays alive
   indefinitely and "must be terminated externally". *Conductor already has the right defence: a
   hard wall-clock timeout and a process-group kill. But the terminal-event and process-exit
   signals must be treated as independent — conductor should stop reading on the `reason:"stop"`
   `step_finish` and not wait on exit.*
2. **#47080 — session directory resolves from `$PWD`, not process cwd.** Opened 2026-09-03, open.
   **REPRODUCED HERE.** Spawning with `cwd=<wk2>` but a stale `PWD` env var, and no `--dir`, the
   session registered against `PWD`, not the cwd. With `--dir` it was correct:

   | run | PWD | `--dir` | resulting `session.directory` |
   |---|---|---|---|
   | A | `…/scratchpad` | no | `…/scratchpad` — **wrong** |
   | B | `…/scratchpad` | yes | `…/scratchpad/wk2` — correct |
   | C | `…/scratchpad/wk2` | no | `…/scratchpad/wk2` — correct |

   This is the same class of failure as the Antigravity scratch-directory bug already documented
   in `fleets.py`. **Always pass `--dir`, and set `PWD` in the child env for belt and braces.**
3. **#44267 — auto-rejected permission yields a zero-byte final message, exit 0, nothing on stdout
   or stderr.** Opened 2026-08-22, open. A conductor consumer reads that as "the model answered
   nothing" rather than "the run was blocked". *Defence: treat an empty final `text` with a
   `tool_use` carrying `state.status:"error"` as a failure, not an empty answer.*
4. **#39459 — `--format json` never emits permission events.** Denials are invisible on the
   stream except as tool errors. Confirmed by my own runs.
5. **#46652 — no agent identity on the event bus.** `--agent <name>` is honoured but never appears
   in the stream; conductor must track it itself. Confirmed by my own runs.
6. **#40544 — events don't say which model produced them.** Same: conductor must carry the model
   id it dispatched with, since the stream will not tell it. This matters for pricing.
7. **#31435 — in containerised environments the run loop breaks on `session.status=idle` before
   `text` and `step_finish` are delivered**, so only `step_start` is emitted. A latency race in
   `run.ts`'s `loop()`. Not observed on bare metal here, but it is a *silent truncation of the
   usage record*, which for conductor means an uncapped run that looks free.
8. **#35870 — headless run intermittently hangs at startup** (parked fiber / lost wakeup in the
   Effect runtime). Not observed in ~12 runs today, but it is why a startup timeout is mandatory.
9. **#41513 — `run --interactive`/`-i` is a no-op**; interactive only activates via the hidden
   `--mini`. Harmless for conductor, but do not pass `-i` expecting anything.
10. **#45531 — `question` permission deny blocks a same-named custom tool in headless mode.**
    Relevant only if conductor ships a custom tool called `question`.
11. **`doom_loop`** is a built-in loop guard: it fires "when the same tool call repeats 3 times
    with identical input" and defaults to `"ask"` — which headless means **allow**. Set it to
    `"deny"` in the agent frontmatter and OpenCode will break its own loops, complementing
    conductor's `loop_limit`. Note the threshold (3) is fixed and not configurable.
12. **MCP cold start**: measured here at roughly **+0.9 s** per run with the operator's five global
    MCP servers vs. an isolated config (~3.5 s → ~4.3 s wall clock, two runs each, npx caches
    warm). Much smaller than the docs imply, but the docs' remedy (`--attach` to a shared
    `opencode serve`) is real and free. The larger MCP cost is not latency but the tool surface:
    those servers are what created the `filesystem_write_file` breach in §1.4.
13. **`autoupdate`** defaults to `true` and is `true` in the operator's global config. An unattended
    fleet that silently upgrades its own binary mid-campaign breaks reproducibility. Set
    `"autoupdate": false`.

---

## PART 2 — Ollama as a headless fleet

### 2.1 CLI (VERIFIED against `ollama run --help`, 0.33.3)

```
ollama run MODEL [PROMPT] [flags]
      --format string           Response format (e.g. json)
      --hidethinking            Hide thinking output (if provided)
      --insecure                Use an insecure registry
      --keepalive string        Duration to keep a model loaded (e.g. 5m)
      --nowordwrap              Don't wrap words to the next line automatically
      --think string[="true"]   Enable thinking mode: true/false or high/medium/low
      --verbose                 Show timings for response
      --dimensions int          (embedding models only)
      --truncate                (embedding models only)
```

Env: `OLLAMA_HOST` (default `127.0.0.1:11434`), `OLLAMA_NOHISTORY`, `OLLAMA_EDITOR`.

With a prompt argument, `ollama run` is non-interactive: it prints the completion to stdout and
exits. `--verbose` prints timing statistics to stderr. **UNVERIFIED** — no model is pulled on this
machine, and I did not pull one. The CLI's output is human-formatted text either way; it is not a
machine surface. `--format json` constrains the *model's* output to JSON, it does not wrap the CLI
output in JSON.

### 2.2 HTTP API — the real surface (DOCS: ollama/ollama `docs/api.md`)

`POST /api/chat` and `POST /api/generate`. Parameters that matter:

- `model` (required), `messages` / `prompt`
- `tools`: list of tools in JSON for the model to use if supported
- `think`: boolean **or** a level — `"low"`, `"medium"`, `"high"`, `"max"`. This is the effort dial.
- `format`: `"json"` **or a full JSON schema** — this is native structured output
- `options`: Modelfile parameters, including **`num_ctx`**
- `stream`: `false` collapses to a single response object
- `keep_alive`: how long the model stays resident, default `5m`

The message object carries `role`, `content`, `thinking` (the model's reasoning), `images`,
`tool_calls`, `tool_name`.

**Token counts and timings are reported** on the final response object:

| Field | Meaning |
|---|---|
| `prompt_eval_count` | tokens in the prompt |
| `prompt_eval_duration` | ns evaluating uncached prompt tokens |
| `eval_count` | tokens in the response |
| `eval_duration` | ns generating the response |
| `total_duration`, `load_duration` | wall clock, model load |

Tokens/sec = `eval_count / eval_duration * 1e9` (DOCS states this formula explicitly).

Local server probes (VERIFIED): `/api/version` → `{"version":"0.33.3"}`, `/api/tags` →
`{"models":[]}`, `/api/ps` → `{"models":[]}`.

### 2.3 Ollama is not a coding agent

It has no file tools, no shell, no permission model, no session persistence, and no working
directory. There is nothing to make read-only because there is nothing that can write. The two
routes to make it agentic:

**(i) As a model provider inside OpenCode** — config snippet in §1.10. You inherit OpenCode's
tools, permissions, agents, sessions, structured output, and event stream for free. Costs: the
~31 K-token system-plus-tools prompt has to fit in `num_ctx`, and local models are markedly worse
at tool calling than the hosted ones.

**(ii) A thin agent loop of conductor's own** — read/write/bash tools defined as Ollama `tools`,
a loop over `/api/chat`, a JSON schema on the final answer via `format`. This gives exact control
over the sandbox and exact token accounting.

**Recommendation: (i), and do not build (ii).** Route (ii) means conductor stops being a
dispatcher and starts being an agent framework — a different product, and the one part of this
whole design that would need its own permission model, its own loop detection, and its own
verification of tool-call safety, all of which conductor already gets from the CLIs it drives.
Route (i) costs one config block. If local models later prove good enough to matter, the cheap
upgrade is `opencode serve` + `ollama/<model>`, not a bespoke loop.

### 2.4 Ollama Cloud (2026)

`ollama signin` authenticates; cloud models carry a `-cloud` suffix (e.g. `gpt-oss:20b-cloud`) or a
`:cloud` tag. Cloud model manifests must still be pulled locally before use.

Cloud catalogue today includes `glm-5.3`, `glm-5.3-flash`, `glm-5.2`, `glm-5.1`, `deepseek-v4-flash`,
`deepseek-v4-pro`, `gemma4`, `kimi-k3`, `kimi-k2.7-code`, `kimi-k2.6`, `minimax-m3`, `minimax-m2.7`,
`gpt-oss:20b-cloud`, `gpt-oss:120b-cloud`.

Plans (ollama.com/pricing): Free ($0, starter credits, starter models only), **Pro $20/mo** ($60 of
usage credits, larger models, concurrent models), **Max $100/mo** ($300 credits, 10 concurrent
requests), Team $500/mo ($1,000 shared credits). Per-million-token pricing:

| Model | Input | Cached input | Output |
|---|---|---|---|
| gpt-oss:20b | $0.07 | $0.035 | $0.30 |
| gpt-oss:120b | $0.15 | $0.014 | $0.60 |
| gemma4 | $0.14 | $0.05 | $0.40 |
| glm-5.3-flash | $0.15 | $0.03 | $0.50 |
| deepseek-v4-flash | $0.22 | $0.007 | $0.66 |
| minimax-m3 | $0.60 | — | — |
| deepseek-v4-pro | $0.66 | $0.022 | $1.98 |
| kimi-k2.7-code | $0.95 | $0.19 | $4.00 |
| glm-5.1 | $1.00 | $0.20 | $3.20 |
| glm-5.3 / glm-5.2 | $1.40 | $0.26 | $4.40 |
| kimi-k3 | $3.00 | $0.30 | $15.00 |

**Can they be used in OpenCode? Yes** — Ollama Cloud is a named provider in the OpenCode directory
with a `/connect` flow (§1.10). Note the overlap: `glm-5.3`, `deepseek-v4-flash/pro`, `minimax-m2.7`
and several others are **also** on OpenCode Zen, already authenticated on this machine. Buying them
twice would be the Cursor-resale mistake `fleets.py` already refuses.

### 2.5 Local models for a 48 GB M4 Pro

Sizes are Ollama's own download sizes (VERIFIED from `ollama.com/library/<model>/tags`). Practical
ceiling: macOS defaults to about 75% of unified memory for GPU work, so roughly **34-36 GB** of
weights before it spills, and a 256 K context needs several GB of KV cache on top. Anything at or
under ~20 GB leaves comfortable headroom for a long agentic context.

| Model | Tag | Size | Context | Notes |
|---|---|---|---|---|
| **qwen3.8** | `27b-q4_K_M` | **18 GB** | 256 K | "substantial gains across coding … and long-horizon agentic tasks"; vision, tools, thinking. Best default. |
| qwen3.8 | `27b-q8_0` | 30 GB | 256 K | Fits, but tight with a long context |
| **qwen3.6** | `27b-coding` | **18 GB** | 256 K | A coding-specific tag; `35b-a3b-coding` is 23 GB |
| **nemotron-3.5-lightning** | `30b-a3b-q4_K_M` | **25 GB** | **1 M** | 30B MoE, 3B active — fast on a Mac. Built "for always-on agents". |
| **muse-glimmer** | `30b-q4_K_M` | **18 GB** | 128 K | Meta, Apache 2.0, "tuned for tool use, long tasks, and failure recovery", explicitly single-GPU |
| **gemma4** | `31b` | **20 GB** | 256 K | `26b` is 19 GB, `12b` is 7.6 GB; vision + audio + tools |
| **ornith** | `35b-q4_K_M` | **21 GB** | 256 K | "self-improving family … for agentic coding" |
| gpt-oss | `20b` | 14 GB | 128 K | The safe small option; `120b` at 65 GB does **not** fit |
| granite4.1 | `30b` | 17 GB | 128 K | IBM, strong structured-output story |
| qwen3-coder | `30b-a3b-q4_K_M` | 19 GB | 256 K | Now ~11 months old; superseded by qwen3.6/3.8 |

Too large for this machine: `qwen3-coder:480b` (290 GB), `gpt-oss:120b` (65 GB),
`nemotron-3.5-lightning:30b-a3b-bf16` (66 GB), `ornith:35b-bf16` (69 GB), any `bf16` 27B+ (55-59 GB).

**Pick two:** `qwen3.8:27b-q4_K_M` (18 GB) as the quality local option and
`nemotron-3.5-lightning:30b-a3b-q4_K_M` (25 GB) when the 1 M context or MoE speed matters.

**Tokens/sec on M4 Pro: no source found.** I did not find a citable benchmark for these specific
2026 models on M4 Pro hardware, and I did not measure any because no model is pulled. Treat any
throughput number as unknown until measured. The structural expectation — MoE models with ~3 B
active parameters (nemotron-3.5-lightning, qwen3.6:35b-a3b) run far faster than dense models of the
same download size — is architecture, not a benchmark.

---

## PART 3 — How conductor would add them

### 3.1 Should an `ollama` fleet exist?

**No. Express it as `opencode` with `ollama/<model>`.**

A `Fleet` in `fleets.py` is a CLI that takes a prompt, a cwd, and a mode, and returns an event
stream. `ollama run` satisfies none of that: no working directory, no file tools, no mode, no
session, no event stream. It would fail conductor's own no-op byte check on every write dispatch
because it cannot write. Adding it as a fleet would mean adding an agent loop to conductor, which
§2.3 argues against.

The natural shape is one `opencode` fleet whose `models` tuple includes local entries:

```python
_flat("qwen3.8-local", "ollama/qwen3.8:27b-q4_K_M", note="local; needs num_ctx >= 32k"),
```

The vendor line in the fleet docstring's policy stays honest: local weights are not a resale of
anything, and Ollama Cloud models that duplicate OpenCode Zen (`glm-5.3`, `deepseek-v4-*`,
`minimax-m2.7`) should be refused there for the same reason Cursor's Claude models are.

### 3.2 Proposed fleet entry

```python
"opencode": Fleet(
    name="opencode",
    binary="opencode",
    vendor="OpenCode Zen / OpenRouter / local via Ollama",
    default_model="glm-5.3",
    cap="watcher",
    models=(
        _flat("glm-5.3",  "opencode/glm-5.3"),
        _flat("minimax-m3", "opencode/minimax-m3"),
        _flat("qwen3.8-local", "ollama/qwen3.8:27b-q4_K_M"),
    ),
),
```

`cap="watcher"` is correct and is the strongest reason to take OpenCode seriously as a fleet:
`step_finish` carries `{cost, tokens}` **per model step**, so conductor can price the running spend
and kill mid-run exactly as it does for Codex's `turn.completed`. Better than Codex, in fact, since
`cost` arrives pre-computed in USD — though conductor should keep pricing from `tokens` itself,
because `cost` is `0` for free-tier models and would silently disable a cap.

Effort maps onto `--variant`, but the ladder is **per provider, not per fleet** (§1.6), so
`_OPENCODE_EFFORT` cannot be a single dict the way `_CODEX_EFFORT` is. It has to hang off the
`Model`, using the existing `Model.resolve` mechanism the Cursor and Antigravity entries already
use, or be omitted for models with no declared variants. Passing an unknown variant is accepted
silently and does nothing, which is exactly the kind of quiet no-op `fleets.py` exists to prevent.

### 3.3 argv — read mode

```python
def _build_opencode(spec: Spec, model: str) -> list[str]:
    argv = [
        "opencode", "run",
        "--pure",                      # no external plugins
        "--format", "json",            # NDJSON events on stdout
        "--dir", spec.cwd,             # MANDATORY: without it the session
                                       # resolves from a stale $PWD (issue
                                       # #47080, reproduced 2026-09-04)
        "--model", model,
        "--agent", "conductor-read" if spec.mode == "read" else "conductor-write",
    ]
    if spec.mode == "write":
        argv += ["--auto"]             # explicit denies still hold under --auto
    if spec.variant:                   # only when the model declares it
        argv += ["--variant", spec.variant]
    if spec.resume is not None:
        argv += ["--session", spec.resume]
    argv.append(spec.prompt)
    return argv
```

Spawn with `env["PWD"] = spec.cwd` and `env["OPENCODE_CONFIG_DIR"] = <conductor's own dir>`,
`stdin=DEVNULL`.

The read/write distinction lives **entirely in the agent definition**, not the command line, because
that is the only layer whose rules land after the operator's global config (§1.4). Conductor ships
two agent files in its own config dir:

- **`conductor-read`** — the §1.4 recipe: `tools: {bash,edit,write,patch,webfetch: false}` plus
  `permission: {bash,edit,write,webfetch,websearch,task,external_directory,doom_loop: deny}`.
- **`conductor-write`** — `permission: {external_directory: deny, doom_loop: deny}`, everything
  else left to `--auto`.

And its config file disables every MCP server by name and sets `"autoupdate": false`.

### 3.4 Session id capture

From the **first line of stdout**: every event carries top-level `sessionID`. Conductor can record
it before the first token is generated, which is better than Codex (where the id arrives in a
config event) and much better than Cursor.

```python
sid = json.loads(first_line)["sessionID"]   # ses_[A-Za-z0-9]{26}
```

Resume is `--session <sid>`, VERIFIED to carry conversation state.

### 3.5 Invariants this would break, and what to do

| Invariant | Status | Action |
|---|---|---|
| **A fleet must not work outside cwd** | **BROKEN two ways.** `--dir` does not confine the filesystem at all (§1.4), and without `--dir` the session resolves from a stale `$PWD` (§1.11 #2, reproduced). | Pass `--dir` **and** set `PWD` in the child env, **and** set `external_directory: deny` in both agent files. That combination was verified to block `write` *and* a bash heredoc fallback. |
| **A read fleet must be unable to write** | **BROKEN by default.** `permission` keys cover built-in tools only; an MCP tool (`filesystem_write_file`) wrote a file from inside a fully-denied read agent. | Disable every MCP server by name in conductor's config, and use the agent `tools:` map (which removes the tool from the model's schema) rather than relying on `permission` alone. |
| **`ask` means stop** | **BROKEN.** `ask` is silently allowed headless; no event is emitted either way. | Never use `ask`. Only `deny`. Preflight `GET /agent` (or `opencode agent list`) and assert the **last** matching rule per permission is `deny` before dispatching. |
| **A fleet must emit a terminal event** | **Partly broken.** The stream ends on `step_finish` with `reason:"stop"` — but there is no result envelope, no totals, and per issue #44901 **the process may never exit**. | Treat `step_finish{reason:"stop"}` as the terminal event and stop reading there. Keep the existing wall-clock timeout and process-group kill as the exit mechanism, not as an error path. Reconcile totals with `opencode export <sid>`. |
| **A blocked run must not look like an empty answer** | **BROKEN** (issue #44267): auto-rejected permission → zero-byte final message, exit 0, silence. | Fail the dispatch when the last `text` is empty and any `tool_use` had `state.status == "error"`. |
| **Conductor must know the model it priced** | **BROKEN** (issues #40544, #46652): neither model nor agent appears in the stream. | Carry both from the Spec. Never read them back from the stream. |
| **A cap must be enforceable** | **HOLDS**, with a caveat. Per-step `tokens` support the watcher. But `cost` is `0` on free models, so a cap keyed on `cost` would be silently uncapped. | Price from `tokens` via `prices.json`, exactly as `_validate_cap` already requires. Refuse a cap on an unpriced local model — a local model has no dollar cost, so `cap_usd` on it is meaningless and should be refused rather than treated as free. |
| **Structured output must not be silently dropped** | **BROKEN on the CLI**: there is no schema flag at all. | Refuse `--schema` and `--verdict` on an `opencode` fleet, exactly as `_validate_schema` already refuses them for `cursor`. Or take the `serve` path, where `format:{type:"json_schema",schema:…}` is native and failures surface as `StructuredOutputError`. |
| **A fleet must not mutate itself mid-campaign** | At risk: `autoupdate` defaults to `true`. | `"autoupdate": false` in conductor's config. |

### 3.6 The recommendation

Add OpenCode as a **read-mode fleet first**, `cap="watcher"`, with `--schema`/`--verdict` refused
the way Cursor's are. It buys conductor a fifth vendor lane, real per-step usage, cheap non-frontier
models (GLM, MiniMax, DeepSeek, Gemma) and a path to local weights — for one argv builder and two
agent markdown files.

Hold write mode until the confinement story is tested against a real repo rather than a scratch
directory. `external_directory: deny` held in every case I tried, including the bash fallback, but
"held in four tests" is not "cannot escape", and unlike Codex there is no OS-level sandbox
underneath it. The honest summary is that OpenCode's read-only mode is a *configuration*, not a
*sandbox*, and conductor should price that difference into where it sends write work.

If OpenCode turns out to earn a permanent lane, migrate it from `run` to `serve`: structured
output, `POST /abort`, real permission events, and a preflight that can verify the resolved rule
list before spending anything are all only available there.

---

## Appendix — reproduction commands

```bash
# OpenAPI spec (479 KB JSON, 472 schemas)
opencode serve --port 4137 &
curl -s http://127.0.0.1:4137/doc            # raw OpenAPI 3.1 JSON, NOT html
curl -s http://127.0.0.1:4137/agent          # resolved agents + flattened permission rules
curl -s http://127.0.0.1:4137/config         # fully merged config

# JSON event stream
opencode run --pure --format json --dir "$PWD" \
  --model opencode/nemotron-3.5-lightning-free "Reply with exactly: PONG" < /dev/null

# session totals
opencode session list --format json -n 3
opencode export <sessionID> 2>/dev/null | jq '.info | {cost, tokens, model, directory}'
```

Artefacts from this research are in the same directory: `openapi-full.json` (pretty-printed spec),
`run1`–`run9` `.jsonl` (captured event streams), `export.json`, `doc-*.txt` (converted docs),
`ollama-api.md`, `ocdir/` (the verified isolated config and agent definitions).
