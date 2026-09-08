# OpenCode headless live probes (2026-09-04, opencode 1.18.20, model opencode/nemotron-3.5-lightning-free)

All runs: `opencode run --dir <repo> --model <id> --format json "<prompt>" < /dev/null`, wrapped in `timeout`.

## Stream shape (verified)
- NDJSON events: `step_start`, `tool_use`, `text`, `step_finish`, `error`. Every event carries `sessionID` at the top level and inside `part`.
- `step_finish.part.tokens = {total,input,output,reasoning,cache:{write,read}}` and `step_finish.part.cost` (USD, 0 for free models) after EVERY model turn. Per-step usage => a `watcher` cap is feasible (conductor prices it or trusts OpenCode's own `cost`).
- Terminal event: `step_finish` with `part.reason == "stop"`; intermediate steps have `reason == "tool-calls"`. The final answer is the last `text` event before the terminal step.
- `tool_use.part.state.status` is `completed` or `error`; `state.error` carries the message. Tool name in `part.tool`. Loop/stall detection can use `tool` + `state.input` signatures exactly like the other fleets.
- `opencode export <sessionID>` returns `{info:{cost,tokens,model,directory,...}, messages:[{info:{role,tokens,cost,time}}]}` => post-hoc totals for free.

## Failure modes (verified)
- Bogus `--session`: exit 1, stderr `Error: Session not found`, no events. Fails closed (unlike antigravity).
- Bogus model id: exit 1 with a single `{"type":"error",...UnknownError...}` event. Fails closed.
- `--variant high` on a free model: accepted silently; no evidence it was applied.

## Permissions: "ask" does not ask headlessly
- Operator's global config has `write: ask`. Under `run` with no TTY the write happened anyway, exit 0, with AND without `--auto`. Headless `run` is effectively auto-approve. Read-only cannot rely on `ask`.

## Config isolation (verified)
- `OPENCODE_CONFIG=<file>` MERGES with `~/.config/opencode/opencode.json`: the operator's five MCP servers still loaded, and the MCP `filesystem_write_file` tool wrote a file from an agent whose own `tools` had write/edit/bash false. Not an isolation lever.
- `XDG_CONFIG_HOME=<conductor-owned dir>` (with `opencode/opencode.json` inside it) replaces the global config; credentials still come from `~/.local/share/opencode/auth.json`. Input tokens for a trivial prompt fell from ~31K (operator config) to ~14K (conductor config, `--pure`, no MCP, no instructions). This is the isolation lever.

## Read-only enforcement (verified)
- Agent `tools: {edit,write,bash,patch: false}` alone is NOT enough: the model used the `task` tool to spawn a full-access subagent, which wrote the file.
- Agent `permission: {edit,write,bash,patch,task,webfetch,websearch,external_directory: deny}` PLUS `tools: {...: false, task: false}` held: model reported it could not write; no file created. Nemotron tried an `invalid` tool twice and a `skill` tool before giving up.

## Write lane (verified)
- Under the isolated home with `permission: {edit,write,bash: allow, task: deny, external_directory: deny}`: file write and shell both worked. `external_directory: deny` blocked the direct file-tool write outside `--dir` (tool error), but the model routed around it with `bash` (`mkdir && echo >`), and the outside file was created. Parity with every other fleet (their shells can write anywhere); conductor judges bytes in the worktree, not the fleet's claims.
- The model's final message claimed "All three tasks completed successfully" after several refused calls. Self-report is unreliable; bytes verdict stays authoritative.

## Overhead
- ~14K input tokens of system prompt + tool schemas per fresh session even with a minimal config; subsequent steps are cache reads (`cache.read` ~13-15K). Cheap on free/cheap models; matters on paid ones.

## Operator config drift (observed, not changed)
- `~/.config/opencode/opencode.json` references models absent from today's catalog: `opencode/minimax-m2.5-free`, `opencode/glm-4-7-free`, `opencode/claude-3-5-haiku`, `opencode/minimax-m2-5-free`. Any run relying on those defaults would fail at model resolution.
- "OX Alpha" does not appear in `opencode models` output on 1.18.20 (592 ids across opencode/openrouter/amazon-bedrock/google/lmstudio).

## Implication for a conductor `opencode` fleet
- binary `opencode`, argv: `opencode run --pure --dir <cwd> --agent cond-read|cond-write --model <provider/model> [--variant <effort>] --format json [--session <id>] <prompt>` with env `XDG_CONFIG_HOME=<conductor-owned config home>`.
- cap: `watcher` (per-step tokens+cost in stream). session id: first event's `sessionID`. Structured output: no schema flag on `run`; enforce by prompt + conductor-side validation (fail closed, as verdicts.py already does).
- Resume: `--session <id>` fails closed on a missing id.

## Paid-model probe (2026-09-04): fix two failing tests in a scratch Python repo
Task: run pytest, read failures, fix `pkg/stats.py` (median even-length, mean empty -> ValueError), re-run, summarize. Agent `cond-write` under the isolated config home, `--pure`, `--format json`.

- OpenCode Zen paid models: **blocked, 401 "No payment method"** for every non-free Zen model (deepseek-v4-flash, glm-5.3-flash). Operator action: add a payment method at the workspace billing page, or keep using OpenRouter.
- `openrouter/deepseek/deepseek-v4-flash`: 5 steps, 4 tool calls (bash, read, edit, bash), 16.9K input + 65K cache-read, 631 output, 209 reasoning, **$0.0027, 23 s**. 0 reasoning-only stops, no duplicate bursts (pytest ran exactly twice, as asked). 5/5 pass on bytes; diff touched only pkg/stats.py (+4).
- `openrouter/z-ai/glm-5.3-flash`: 5 steps, 5 tool calls (bash, read, read, edit, bash), 16.6K input + 62.7K cache-read, 542 output, 173 reasoning, **$0.0024, 27 s**. Same clean profile. 5/5 pass; diff pkg/stats.py (+6/-1). No `!!!!!` degeneration on OpenRouter's serving for this (short) prompt.
- Both final summaries were accurate and cited the right functions. Neither committed (as instructed).
- Caveat: one small task each, one run each. It settles "can they do a multi-step edit-and-test loop headlessly in OpenCode" (yes), not long-horizon behavior.
