# Live probe: `pi` + llama.cpp + Qwen3-Coder-30B-A3B on the 48 GB M4 Pro

Probe date: 2026-09-04 (evening). Companion to `2026-09-04-research-pi.md` and
`2026-09-04-research-local-models.md`; this file records what actually happened on the machine.
Every number below is measured here, none is vendor-reported.

## Setup that worked

- `brew install llama.cpp` → 0.4.0 (build 10809), ggml 0.23.0. Metal is compiled in (`llama-server
  --list-devices` lists `MTL0: Apple M4 Pro (38338 MiB)`); there is no separate Metal dylib to look for.
- Model: `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF`, file `Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf`,
  18.56 GB, verified on the Hugging Face tree API before pulling (the `Qwen/` org's own GGUF repo
  returns 401, gated). `-hf` downloads into `~/.cache/huggingface/hub/`, about 1 GB/min here, ~19 min.
- Server command that is correct for this machine:
  ```
  llama-server --jinja -fa on -ngl 99 -m <gguf> --alias qwen3-coder-30b -c 65536 --parallel 1 \
    --host 127.0.0.1 --port 8080
  ```
  Loads in 8 s, 23.4 GB resident (17 GB weights, ~6 GB KV cache at 64K), idle at 0.3% CPU.
- `~/.pi/agent/models.json` provider `local-build`, `api: openai-completions`, `apiKey: "local"`,
  `compat: {supportsDeveloperRole: false, supportsReasoningEffort: false, supportsUsageInStreaming: true}`,
  model `qwen3-coder-30b` with `contextWindow 65536`, `maxTokens 16384`, all costs 0. `pi --list-models`
  shows it. Usage arrived on every assistant `message_end` (input, output, cacheRead), so the streaming
  usage flag is fine against llama.cpp.

## Trap 1: the researched KV-cache flags put the model on the CPU

The research recommended `--cache-type-k q8_0 --cache-type-v q4_0` to buy context. On this build that
combination ran attention on the CPU: the server sat at 942% CPU and 22% GPU (Activity Monitor), fans
loud, and measured 55 tok/s prompt processing / 9 tok/s generation. `llama-bench` on the same file:

| config | prompt 512 (tok/s) | generate 64 (tok/s) |
|---|---|---|
| `-fa 1 -ngl 99`, f16 cache | 708 ± 94 | 71.8 ± 3.8 |
| same plus `-ctk q8_0 -ctv q4_0` | 351 ± 2 | 35.1 ± 5.3 |

Even where it does not fall off the GPU, KV quantization halves throughput here. Do not use it on this
machine; the f16 cache at 64K costs ~6 GB, which fits.

## Trap 2: long-context prefill is minutes, not seconds

Cold prompt, single request, corrected server, `max_tokens 20`:

| prompt tokens | time to first token | prefill rate | generation after |
|---|---|---|---|
| 13 | 0.1 s | n/a | 62 tok/s |
| 32,683 | 136 s | 240 tok/s | 27 tok/s |
| 62,933 | 360 s | 84 tok/s | 18 tok/s |

Prefill rate collapses with context (quadratic attention cost), and generation drops 3.4x from 1K to
64K. The fans run for the whole prefill. Consequences for lanes:

- An agent loop is fine: llama.cpp keeps the prefix cache (the probe below reused 79K cached tokens
  across 21 turns and paid 645 tok/s on the ~2.4K new tokens per turn).
- A cold read lane handed a 30K-token diff waits over two minutes before its first word. Anything
  needing a cold 64K prompt is not a lane on this machine. Cap local read lanes near 16K of input, or
  accept the wait explicitly in the mission.

## The probe: fix-two-tests through `pi --mode json`, write mode

Same task and prompt as the seven-lane comparison in `docs/RESET-2026-09.md`: scratch repo with two
failing tests (`median` on even length, `mean([])` should raise), told to run pytest via conductor's
venv path, fix under `pkg/`, do not edit tests, do not commit.

```
pi --mode json --no-context-files --no-extensions --no-skills --no-prompt-templates --no-approve \
   --tools read,write,edit,bash,grep,find,ls --provider local-build --model local-build/qwen3-coder-30b \
   --session-dir <scratch>/pi-sessions --session-id cond-probe-001 -p -- "<prompt>" < /dev/null
```

Result, verified on bytes: `5 passed`, one file changed (`pkg/stats.py`, +10/−1), no test edited,
nothing committed. Diff is correct; small scope creep (an unrequested empty-list guard on `median`).

| measure | value |
|---|---|
| wall clock | 432 s (7.2 min) |
| assistant turns | 21 |
| tool calls | 20: bash 11, ls 6, read 2, edit 1 |
| malformed tool calls | 0 (three `isError: true` results were real command failures, none a parse error) |
| repeated identical calls | 0 |
| tokens (summed over turns) | input 5,050 new, output 1,787, cacheRead 79,459 |
| cost | $0 (local) |
| exit code | 0; last assistant `message_end.stopReason == "stop"`; `agent_settled` present |

**What went wrong inside a green run.** The model read the venv path in the prompt as the working
directory: its first 12 calls ran conductor's own 353-test suite three times, listed conductor's
source tree, and searched conductor for a `pkg/` directory. Only after `pwd` did it find the scratch
repo. Two lessons for a local write lane: (1) the prompt must name the working directory and say "the
repo in your current directory", never lean on a path elsewhere; (2) pi has no path jail, so a lane
can run commands in any repo the user can reach. Conductor's worktree isolation still judges the
bytes, but a wandering lane can still *read* or *run* things elsewhere. Same finding as the OpenCode
probe: write confinement is the conductor's job.

## Event stream shapes conductor would parse

First line: `{"type":"session","version":3,"id":"cond-probe-001","cwd":"<abs path>","timestamp":...}`.
Then, per run: `agent_start`, repeated `turn_start` / `message_start` / `message_update` (1,523 of them,
ignore) / `message_end` / `tool_execution_start` / `tool_execution_update` (608, ignore) /
`tool_execution_end` / `turn_end`, then `agent_end` and `agent_settled`.

- `tool_execution_start`: `toolCallId`, `toolName`, `args` (full). Hash `(toolName, args)` for loop
  detection.
- `tool_execution_end`: `toolCallId`, `toolName`, `isError`, `result` (`{content:[{type:"text",text}]}`).
- `message_end.message`: `role`, `content`, `timestamp`; assistant messages add `stopReason`, `usage`
  (`input`, `output`, `cacheRead`, `cacheWrite`, `reasoning`, `totalTokens`, `cost{...,total}`),
  `model`, `provider`, `errorMessage`. The user-role `message_end` has no usage.
- Cap watcher: sum assistant `usage.totalTokens` per `message_end`; `cost.total` is 0 for a local
  model, so cap on tokens or wall clock.

## Corrections to the pi research

- `pi auth check --json` does not exist in 0.84.4 ("Unknown option"). `pi auth check --provider <p>`
  exists, exits 1 with "Connection error." when the server is down, but against a live local server
  it ran a **full agent turn** (a 5.2K-token system prompt plus two follow-up requests) and did not
  return within 3 minutes on the CPU-bound server. It is not a cheap readiness ping. Preflight a local
  lane with an HTTP `GET /health` on the server instead.
- `--session-id <new id>` on a first run prints `Warning: No project session found with id '...';
  creating a new session with that id.` on stderr and exits 0. The same warning appears for a bad
  resume, so it cannot distinguish "fresh" from "wrong id". Conductor must check the session file
  exists before a resume, as the research already said; the warning alone is not a signal.

## Verdict for a `pi` fleet entry

Worth adding, with these terms:

- Local lane = `pi` + llama.cpp on `127.0.0.1`, `--parallel 1`, f16 cache, no KV quantization.
  Preflight by `GET /health`; refuse dispatch when it is not 200.
- Cap mode `watcher` on assistant `message_end.usage.totalTokens` and wall clock; `cap_usd` refused
  for providers whose `cost` is all zero (price it at $0 in `prices.py` with the date, so the refusal
  is explicit, not accidental).
- Read lanes: `--tools read,grep,find,ls`; input budget under ~16K tokens on this machine or the
  mission accepts a stated prefill wait.
- Write lanes: prompt names the working directory; conductor's worktree plus bytes verdict as for
  every fleet. Refuse `--schema` / `--verdict` (no structured output), or prompt-and-parse.
- Session id minted by conductor; resume asserts the session file exists.
- Fans and memory: the server holds ~23 GB while up and runs the GPU flat out during prefill. Start it
  per mission and stop it after, do not leave it resident.
