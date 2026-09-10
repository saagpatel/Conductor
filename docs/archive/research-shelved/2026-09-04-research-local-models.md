# Local model serving on a 48 GB M4 Pro for the `pi` harness

Research date: 2026-09-04. Machine: Apple M4 Pro, 20 cores, 48 GB unified memory, 243 GB free, macOS 26. Installed: Ollama 0.33.3. Not installed: LM Studio, llama.cpp, mlx-lm, vLLM.

**Evidence grading used throughout:** `[strong]` = first-party doc or independent measurement; `[weak]` = vendor-reported or single third-party blog; `[anecdotal]` = forum/community report. Numbers from hardware other than M4 Pro are labeled.

**Honest gaps up front.** Three things in the brief could not be verified and are flagged inline rather than guessed: (1) **"Ornith 35B" returned zero results** across multiple searches and may not exist; (2) **"MiMo-V2-Flash"** returned no results, the real Xiaomi model found is MiMo-V2.5-Pro (1.02T MoE, not local); (3) **Kimi K2.7 pricing on OpenRouter** could not be confirmed on a first-party page. Two tool-gating notes: `WebFetch` and `ctx_fetch_and_index` were blocked by this session's egress guard, so every claim below rests on search-result summaries, including quoted first-party text surfaced through domain-scoped search rather than a direct page read. Treat exact figures as needing a confirm-on-the-page pass before you spend money or disk on them.

---

# PART 1 — How to serve local models

## 1.1 The decision in one paragraph

For an agentic coding harness on this machine, the serving stack matters *more* than the model format. An independent comparison found the runtime choice dominates the format choice: on an M4 Max 128 GB, `vllm-mlx` held a 21% speed advantage over llama.cpp at 30B, and Ollama measured **37% slower than LM Studio on the identical llama.cpp engine** — a caching and scheduling difference, not an engine one ([famstack.dev](https://famstack.dev/guides/mlx-vs-gguf-part-2-isolating-variables/), [yage.ai](https://yage.ai/share/mlx-apple-silicon-en-20260331.html)) `[weak — third-party blogs, M4 Max not M4 Pro]`.

## 1.2 Ollama 0.33.x

**Version state.** Latest is v0.33.2, released 2026-08-27, with trackers disagreeing on whether 0.33.2 is stable or RC ([localaimaster](https://localaimaster.com/blog/ollama-version-history), [promptquorum](https://www.promptquorum.com/local-llms/top-open-source-models-ollama)) `[weak]`. The operator reports 0.33.3 installed, which is newer than anything the search surfaced — treat 0.33.3 as ahead of the documented record.

**Engine — it is now dual-stack, not llama.cpp-only.** Ollama runs an MLX backend *alongside* llama.cpp on Apple silicon. MLX arrived in preview at v0.19, raising Qwen3.5-35B-A3B generation from 58 to 112 tok/s in Ollama's own benchmarks. GGUF models still run on llama.cpp; MLX handles compatible safetensors models. **On 32 GB+ Apple silicon Macs, MLX is the default**; below 32 GB you get llama.cpp Metal automatically ([runaihome](https://runaihome.com/blog/ollama-v030-mlx-stable-upgrade-2026/), [cloudnews.tech](https://cloudnews.tech/ollama-nearly-doubles-llm-speed-on-mac-with-mlx-but-theres-a-catch/)) `[weak — vendor benchmark relayed by third parties]`. This 48 GB machine therefore gets MLX by default.

**0.33-line changes relevant here:** v0.33.0 (Aug 21) made KV-cache prefill restore points more reliable so a cancelled long prefill resumes rather than reprocesses — directly relevant to agent loops. v0.33.1 (Aug 26) added MLX Qwen3.8-Flash-Next support **and structured output**. Gemma 4 gained an MLX text-only runtime; the MLX backend picked up mixed-precision quantization ([updatify](https://updatify.io/releases/ollama), [localaimaster](https://localaimaster.com/blog/ollama-version-history)) `[weak]`.

**The `num_ctx` gotcha — this is the single biggest Ollama trap for a coding agent.** The OpenAI-compatible `/v1` endpoints follow OpenAI's request schema, and **that schema has no `num_ctx` field**, so an OpenAI-speaking client cannot raise the window per request. Ollama maintainers declined PRs on this on the grounds that OpenAI's API does not allow setting context length ([ollama/ollama#5356](https://github.com/ollama/ollama/issues/5356), [PR #6137](https://github.com/ollama/ollama/pull/6137)) `[strong — issue tracker]`. Failure is **silent**: no warning, oldest content truncated, model answers from what is left. A January 2026 report documented a 10,573-token prompt truncated to 4,096, cutting away the project-context section ([openclaw#4028](https://github.com/openclaw/openclaw/issues/4028)) `[strong]`.

Defaults are in flux. Ollama's source now describes `OLLAMA_CONTEXT_LENGTH` as "Context length to use unless otherwise specified (default: 4k/32k/256k based on VRAM)" — newer builds auto-select by memory, where older docs cite a flat 4096 ([envconfig/config.go](https://github.com/ollama/ollama/blob/main/envconfig/config.go), [Ollama FAQ](https://docs.ollama.com/faq)) `[strong]`. On 48 GB you would likely land in the 32k or 256k bucket, but **verify with `ollama ps` rather than assume** — this is exactly the class of silent default that costs a debugging session.

Three fixes, in order of reliability: a Modelfile with `PARAMETER num_ctx 65536` then `ollama create` (a Modelfile `num_ctx` takes precedence over the server-level setting); the server-wide `OLLAMA_CONTEXT_LENGTH` env var; or use the native `/api/chat` endpoint, which does accept `options.num_ctx` ([Ollama FAQ](https://docs.ollama.com/faq), [lumadock](https://lumadock.com/tutorials/ollama-context-window)) `[strong]`.

**Tool calling quality.** Ollama's own docs state OpenAI compatibility is **experimental and subject to breaking changes**, and at least one client's docs warn that the OpenAI-compatible route "selects OpenAI-compatible mode, where tool calling is not reliable" ([ollama.readthedocs.io](https://ollama.readthedocs.io/en/openai/), [openclaw docs](https://docs.openclaw.ai/providers/ollama)) `[strong for the Ollama disclaimer; weak for the reliability claim]`. A concrete 0.33-era break: `qwen3.8:27b-mlx` reportedly **rejects the `developer` role** on `/v1/responses`, breaking `ollama launch codex`, while direct `ollama run` works ([yottalabs](https://www.yottalabs.ai/post/how-to-run-qwen-3-8-27b-locally-ollama-gguf-single-gpu-2026)) `[anecdotal]`. Note `pi` has a `compat.supportsDeveloperRole: false` flag for exactly this (see 1.7).

**Concurrency and memory.** `OLLAMA_NUM_PARALLEL` defaults to 1 (docs disagree; one mirror says auto-selects 4 or 1 by memory). Critically, **each parallel slot gets a private KV cache of `num_ctx` tokens**, so total cache is the product — 4 slots at 64k is 256k of cache. Weights are shared across slots, not copied. `OLLAMA_MAX_LOADED_MODELS` defaults to 3 and *does* cost a full weight copy per model. `OLLAMA_KEEP_ALIVE` defaults to 5 minutes. `OLLAMA_MAX_QUEUE` defaults to 512 ([Ollama FAQ](https://docs.ollama.com/faq), [multigrid](https://multigrid.ai/learn/ollama-num-parallel-setting)) `[strong]`. For one operator on one machine, leave parallel at 1: unused slots still divide the context allocation.

**Endpoint for `pi`:** `http://localhost:11434/v1`, `api: "openai-completions"`.

## 1.3 LM Studio

**Headless is a first-class mode now.** LM Studio 0.4 (January 2026) added parallel requests with continuous batching, a **standalone headless daemon called `llmster`**, and LM Link for remote instances over encrypted tunnels. 0.4.2 added MLX parallel requests on Mac; 0.4.5–0.4.6 added end-to-end encrypted remote access via Tailscale; 0.4.12 added MCP OAuth; **0.4.14 promoted multi-token-prediction speculative decoding to stable, and the REST API is now both OpenAI- and Anthropic-compatible** ([lmstudio.ai/blog](https://lmstudio.ai/blog)) `[weak — vendor changelog]`.

**JIT loading and TTL.** `/v1/models` returns everything on disk, not only what is in RAM; hitting an inference endpoint with an unloaded model triggers an automatic load. JIT-loaded models auto-unload after inactivity, set via `--ttl <seconds>`, default 3600 ([DeepWiki: LM Studio server management](https://deepwiki.com/lmstudio-ai/docs/2.6-server-management-and-headless-mode)) `[strong — docs mirror]`. That 1-hour default pinning ~20 GB is worth lowering on a 48 GB machine.

**MLX performance.** LM Studio reports MLX on Mac roughly **2–2.5x faster than llama.cpp with Qwen 3.5**, and `mlx-engine` v1.8.5 improves repeated long-context agentic workflows by **checkpointing the KV cache** — the single most relevant feature on this list for an agent loop that re-sends a growing transcript ([lmstudio.ai/blog](https://lmstudio.ai/blog)) `[weak — vendor claim]`.

**Speculative decoding caveats, both important here.** It is **problematic for MoE models**: during verification the main model must load the union of all experts activated across the speculative tokens, inflating bandwidth use and potentially *slowing things down*. And sources conflict on MLX support — a 2026 guide says the feature requires the llama.cpp runtime v2.0.0+ and is **not yet available on the MLX backend**, contradicting an older 2025 article ([insiderllm](https://insiderllm.com/guides/lm-studio-tips-and-tricks/)) `[weak, contradictory]`. Since nearly every model recommended in Part 2 is a MoE, treat speculative decoding as a non-factor unless measured.

**Licensing.** Not established by this research. LM Studio is closed-source freeware with terms that have historically distinguished personal from commercial use — **confirm on lmstudio.ai/terms before using it for paid work.** `[unverified]`

**Gotcha:** some settings, such as Max Concurrent Predictions, have **no CLI flag** and must be set in the desktop app or per-model defaults, which then apply to `lms load` ([insiderllm](https://insiderllm.com/guides/lm-studio-tips-and-tricks/)) `[weak]`. A fully headless machine cannot configure those.

**Endpoint for `pi`:** `http://localhost:1234/v1`, `api: "openai-completions"`; or the Anthropic-compatible `/v1/messages` with `api: "anthropic-messages"`.

## 1.4 llama.cpp `llama-server` directly

**Tool calling requires `--jinja`.** The official docs show native invocations like `llama-server --jinja -fa -hf bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M`. Several models need a template override via `--chat-template-file` because the shipped template is buggy — DeepSeek R1 distills are called out by name ([llama.cpp function-calling docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)) `[strong — first-party]`.

**Constrained output is the strongest of any option here.** The chat-completions endpoint accepts the OpenAI tool shape (`tools`, `tool_choice`, `tool_calls`), JSON mode via `response_format`, **and llama.cpp's own `grammar` and `json_schema` extensions** ([llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [endpoint tour](https://mvysny.github.io/llama-server-endpoints/)) `[strong]`. Internally an autoparser renders the Jinja template with dummy data to detect the tool-call format, supporting both JSON-native (Llama 3.1) and tag-based (`[TOOL_CALLS]`) styles, with PEG-based parsing and JSON healing for streaming ([DeepWiki: chat templates and tool calling](https://deepwiki.com/qualcomm/llama.cpp/8.2-chat-templates-and-tool-calling)) `[strong]`.

**One open friction point:** a user reported that enabling `--jinja` appeared to *disable* GBNF grammar, with the request's `grammar` property ignored. The maintainer said jinja *should* work with grammar and asked for a repro — **so this is unresolved, not confirmed broken** ([discussion #12204](https://github.com/ggml-org/llama.cpp/discussions/12204)) `[anecdotal, unresolved]`.

**KV cache quantization — the lever that buys you context on 48 GB.** Default cache is FP16/BF16. `q8_0` halves it with, per community analysis, **no significant quality loss**; `q4_0` quarters it at a noticeable cost. The K cache is far more sensitive than V, which is why the common asymmetric setup is `--cache-type-k q8_0 --cache-type-v q4_0`. Flash attention (`-fa`) is generally **required** for V-cache quantization ([smcleod.net](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/), [rigel/medium](https://medium.com/rigel-computer-com/optimize-your-gpu-kv-cache-for-llama-cpp-opencode-co-13b6bc74f5ec)) `[weak — community benchmark]`.

It is **not free at long context**: one community benchmark reported throughput relative to f16 of roughly +0.7% at 6K, **−11.9% at 24K, and −36.8% at 110K with q4_0** ([discussion #20969](https://github.com/ggml-org/llama.cpp/discussions/20969)) `[weak]`. For a coding agent living at 32–64K, the middle number is the one that matters: expect roughly a tenth of your speed traded for half your cache.

**Speculative decoding has a documented flag bug.** With `llama-server`, `--cache-type-k/v q8_0` alongside a draft model (`-md`) applies quantization to the **main** model's cache but the draft model's cache still initializes at f16; `llama-speculative` applies it to both, which the maintainers called the expected behavior ([issue #11200](https://github.com/ggml-org/llama.cpp/issues/11200)) `[strong — issue tracker, but dated early 2025; verify against current build]`. If you budget memory around speculative decoding, read the `llama_kv_cache_init` log line rather than trusting the flag.

**It can now speak Anthropic.** A PR added Anthropic Messages API support to `llama-server`, converting Anthropic format into the internal OpenAI-compatible format so Claude Code and other Anthropic clients work against it ([PR #17570](https://github.com/ggml-org/llama.cpp/pull/17570)) `[strong]`. That makes `api: "anthropic-messages"` viable against localhost.

**What it lacks vs Ollama:** model management. There is no pull/library/tag system beyond `-hf`, no automatic model swapping, no keep-alive daemon, no registry. The `id` field defaults to the `-m` file path unless you set `--alias`.

**Endpoint for `pi`:** `http://127.0.0.1:8080/v1`, `api: "openai-completions"`.

## 1.5 MLX-based servers

**`mlx_lm.server`** is essentially single-stream — no batching or queueing layer. Fine for one agent, wrong for anything concurrent. `[weak — inferred from comparison, no first-party doc read]`

**`vllm-mlx`** is the most interesting option for this use case. It ships continuous batching, paged KV cache, prefix caching, and an SSD-tiered cache, and exposes **both OpenAI `/v1/*` and Anthropic `/v1/messages` from a single process**. Tool calling is enabled with `--enable-auto-tool-choice` plus a `--tool-call-parser` matching the model family, with 7 built-in parser formats and streaming support. Documented example configs include Qwen3-Coder-Next with `--tool-call-parser hermes --prefill-step-size 8192 --kv-bits 8` ([waybarrios/vllm-mlx](https://github.com/waybarrios/vllm-mlx), [tool-calling guide](https://github.com/waybarrios/vllm-mlx/blob/main/docs/guides/tool-calling.md)) `[strong for feature existence; weak for the "400+ tok/s" and "10–30x faster multi-turn" headline claims, which are the project's own]`.

Two cautions: there are **two active repos** (`raullenchai/vllm-mlx` and `waybarrios/vllm-mlx`) plus a `Rapid-MLX` fork, so you must pick a lineage deliberately; and its migration pitch versus Ollama (better prompt caching, more reliable tool calling, better agent stability) is self-reported.

**`mlx-openai-server`** is a FastAPI OpenAI-compatible server, macOS M-series only. It supports multi-model YAML configs with per-model parsers (`--tool-call-parser qwen3`, `minimax_m2`, etc.), and in multi-model mode **each model runs in a spawned subprocess**, isolating Metal runtime state and avoiding fork-semaphore issues on macOS. Tunables include `--decode-concurrency`, `context_length`, `prompt_cache_max_bytes`, `kv_bits` ([cubist38/mlx-openai-server](https://github.com/cubist38/mlx-openai-server), [PyPI 1.0.14](https://pypi.org/project/mlx-openai-server/1.0.14/)) `[strong]`.

**MLX vs GGUF, measured.** This is the closest thing to real M4 Pro data found:

| Machine | Model | Engine | Prefill (pp512) | Generation (tg128) |
|---|---|---|---|---|
| M4 Pro 48 GB | Nemotron-3-Nano-30B-A3B Q4_K_M | llama.cpp/GGUF | ~628 tok/s | ~52 tok/s |
| M2 Max 96 GB | same | llama.cpp/GGUF | ~860 tok/s | ~63 tok/s |

Source: [Andreas Kunar, Medium](https://medium.com/@andreask_75652/apples-m4-pro-is-faster-than-an-m2-max-for-nemotron-3-nano-with-mlx-2f19efbc5e50) `[weak — single third-party measurement, but it IS an M4 Pro 48 GB, the exact machine]`. Prefill tracks GPU compute; generation is bandwidth-bound. The same author found MLX (mlx-lm 0.30.0, 5-bit) reversed the M2 Max/M4 Pro ordering.

**The wall-clock trap.** Displayed tok/s ignores prefill entirely. On an M1 Max with a ~650-token prompt, MLX's *effective* throughput was 13 tok/s vs GGUF's 20, because MLX spent 94% of its time in prefill ([famstack.dev](https://famstack.dev/guides/mlx-vs-gguf-part-2-isolating-variables/)) `[weak, different hardware]`. MLX pulls ahead on long outputs (250+ tokens) and loses on short ones. For an agentic coding loop with long outputs, prefill amortizes and MLX wins — but for a fast classification lane, GGUF may finish first.

**Memory.** Qwen3-Coder-30B-A3B occupies 34.7 GB under MLX vs 40 GB as GGUF, a ~13% saving. At 30B+, the quality gap between Q4_K_M and 4-bit MLX is described as essentially noise ([atomic.chat](https://atomic.chat/blog/guides/gguf-vs-mlx), [muhammadraza.me](https://muhammadraza.me/2026/gguf-vs-mlx-decision-guide/)) `[weak]`. One M4 Pro data point on context growth: a 4-bit MLX Qwen3-Coder-30B-A3B used 16.6 GB at 1K context and **25.5 GB at 64K**, with generation falling from 73.6 to 13.5 tok/s ([insiderllm](https://insiderllm.com/guides/best-local-coding-models-2026/)) `[weak, but M4 Pro]`. **That 5.4x speed decay across the context window is the most operationally important number in this report** — an agent at 64K runs at roughly a fifth the speed it does at 1K.

## 1.6 Anything else notable

Nothing found that changes the answer. Apple Foundation Models, Exo, Msty, and Jan did not surface as relevant in 2026 sources for this use case. `[unverified — absence of search hits is not proof of absence]`

## 1.7 How `pi` points at any of these

`pi` (earendil-works/pi) configures providers in a `models.json` keyed by provider ID with `baseUrl`, `api`, `apiKey`, and a `models` array. `api` values include `openai-completions`, `openai-responses`, and `anthropic-messages` ([pi models doc](https://pi.dev/docs/latest/models), [pi-mono models.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/models.md)) `[strong]`.

Use `api: "openai-completions"` for llama.cpp, vLLM, LiteLLM, Ollama, and LM Studio. Reserve `openai-responses` for genuinely non-standard responses-format APIs. For local models only `id` is required per model; `apiKey` needs a dummy value since local servers ignore it.

The `compat` block handles partial compatibility, and provider-level `compat` sets defaults that model-level `compat` overrides. The flags that matter for the failure modes in this report:

- `compat.supportsDeveloperRole: false` — sends the system prompt as a system message. **This is the fix for the `qwen3.8:27b-mlx` developer-role rejection noted in 1.2.**
- `compat.supportsReasoningEffort: false` — for servers that choke on `reasoning_effort`.
- `compat.supportsUsageInStreaming: false`, `maxTokensField: "max_tokens"` — the typical local-server shape.

For a server exposing `/v1/models`, prefer `discovery: type: openai-models-list` over hand-listing.

One caveat found in a *sibling* project's docs, not confirmed for `pi` itself: private/loopback destinations may require `request.allowPrivateNetwork: true`. **Check whether `pi` has an equivalent gate before assuming localhost just works.** `[unverified for pi]`

A working local block:

```json
{
  "providers": {
    "local-llm": {
      "baseUrl": "http://127.0.0.1:8080/v1",
      "api": "openai-completions",
      "apiKey": "not-needed",
      "compat": { "supportsUsageInStreaming": false, "maxTokensField": "max_tokens" },
      "models": [{ "id": "qwen3.8-27b", "contextWindow": 262144, "maxTokens": 32768 }]
    }
  }
}
```

---

# PART 2 — Models for a 48 GB M4 Pro

## 2.0 The memory budget, stated once

48 GB unified, minus macOS overhead leaves roughly **38–40 GB usable** ([openclawdc](https://openclawdc.com/blog/best-local-llms-48gb-ram/)) `[weak]`. Target ~20–26 GB of weights so a 64K–256K KV cache fits. With `q8_0` K-cache quantization you roughly halve cache cost. The measured M4 Pro data point above (16.6 GB at 1K → 25.5 GB at 64K for a 4-bit 30B MoE) means **a 4-bit 30B-class model plus a 64K cache lands near 26 GB — comfortable. Anything at Q8 does not leave room for a real cache.**

## 2.1 What simply does not fit — say this plainly

| Model | Size | Verdict |
|---|---|---|
| GLM-5.3-Flash | 320B total / 18B active, MIT, 1M ctx | **No.** Needs an 8-GPU node. No quant fits 48 GB. |
| DeepSeek V4 Flash | 284B total / 13B active, MIT | **No.** Checkpoint ships pre-compressed at 166.9 GB, fits a 2×H200 pod. |
| Qwen3.8-Flash-Next | ~176–180B stored / 6B active | **No.** FP8 checkpoint is 172.78 GiB; even a 1-bit GGUF is ~123 GB, needing a 256 GB-class machine. |
| Kimi K3 | 2.8T MoE, ~50–104B active | **No.** Rack-scale. |
| Kimi K2.6 / K2.7 Code | 1T MoE, 32B active | **No.** |
| MiMo-V2.5-Pro | 1.02T MoE, 42B active | **No.** |
| Devstral 2 (flagship) | 123B dense | **No**, and its modified-MIT license gates large-enterprise use. |

Sources: [yottalabs GLM vs DeepSeek](https://www.yottalabs.ai/post/glm-5-3-flash-vs-deepseek-v4-flash-2026), [vLLM recipe for Qwen3.8-Flash-Next](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next), [modelfit](https://modelfit.io/blog/qwen38-flash-next-open-weights/), [codersera Kimi K2.6](https://codersera.com/blog/kimi-k2-6-complete-guide-2026/), [interconnects](https://www.interconnects.ai/p/latest-open-artifacts-21-open-model), [Mistral Devstral 2](https://mistral.ai/news/devstral-2-vibe-cli/).

**Note on Qwen3.8-Flash-Next specifically:** its 51B N-gram embedding table is described as offloadable to host RAM and adds no per-token compute. On a unified-memory Mac there is no separate host RAM to offload *to*, so that architectural escape hatch does not help you. `[reasoned inference, not a cited claim]`

## 2.2 Candidate table

| Model | Maker / license | Total/active | Ctx | 4-bit size | Agentic evidence |
|---|---|---|---|---|---|
| **Qwen3.8-27B** | Alibaba, **Apache 2.0** | 27.78B dense | 262K | ~15.0 GiB MLX / 17.9 GB UD-Q4_K_XL | Terminal-Bench 2.1 **73.0**, SWE-bench **Pro** 61.7, LiveCodeBench v6 90.3 `[weak — all vendor]`. Artificial Analysis independent index **52**, up from 38 for Qwen3.6-27B `[strong — independent]` |
| **Muse Glimmer 30B** | Meta, **Apache 2.0** | 29.6B dense (+ViT-G/14) | 131K+ | 19.4 GB (mlx-community 4bit) | SWE-bench Verified **76.0**, MCP Atlas 75.5, AIME 2026 94.7 `[weak — vendor]` |
| **Qwen3.6-35B-A3B** | Alibaba | 35B / 3B | 262K→1M YaRN | ~19–20 GB | SWE-bench Verified **73.4**, SWE-bench Multilingual 67.2, Terminal-Bench 2.0 51.5 `[weak — vendor]` |
| **Qwen3-Coder-30B-A3B** | Alibaba | 30B / 3.3B | 256K | ~19 GB Q4_K_M / 16.6 GB MLX | Independent local harness: 80% code gen, **77% tool selection**, 80% agent accuracy — most balanced of 4 tested `[strong-ish — independent harness]`. SWE-bench Verified ~50.3 unofficial |
| **Nemotron 3.5 Lightning 30B-A3B** | NVIDIA, **OpenMDW 1.1**, commercial OK | 30B / 3B, Mamba-2+MoE | **1M** | ~17–20 GB est. | SWE-bench Verified **51.6** (NVFP4), PinchBench 83.4–85.4, but **Terminal-Bench 2.1 only 24.58** and τ³-bench Banking **9.28** `[weak — vendor]` |
| **Nemotron 3 Nano 30B-A3B** | NVIDIA Open Model License | 31.6B / ~3.5B | **1M** | ~17–20 GB | SWE-bench **38.8**, LiveCodeBench 68.3, RULER-100 at 1M: **86.3** `[weak — vendor]` |
| **Devstral Small 2** | Mistral, **Apache 2.0** | 24B dense | 256K | ~14 GB | SWE-bench Verified **68.0** `[weak — vendor]`; independent Vals AI puts the *flagship* at 38.44% avg across a broader suite `[strong — independent, but different model]` |
| **Gemma 4 26B-A4B** | Google, Apache 2.0 | 25.2B / 3.8B | 256K | ~15 GB est. | Coding index 39.3, AA Intelligence Index 26 `[strong — independent]`. Independent Codex CLI test: fine one-shot, **struggled on agentic coding** `[anecdotal]` |
| **Gemma 4 31B dense** | Google, Apache 2.0 | 30.7B dense | 256K | ~18 GB est. | GPQA Diamond 84.3. **~16s to first token on a 32k prompt** — poor for codebase analysis `[weak]` |
| **gpt-oss-20b** | OpenAI | 20B | 130K | ~12 GB | AA Intelligence Index **15 at high effort** — lowest here. See the Harmony problem below. |
| **Granite 4.2 30B** | IBM, Apache 2.0 | 30B | 128K | GGUF to Q4_K_M shipped | SWE-bench Verified **57.0**; 8B and 30B went through an agentic RL block for terminal/tool use `[weak — vendor]` |
| **Granite 4.1 30B** | IBM, Apache 2.0 | 30B dense | — | — | Superseded by 4.2 (2026-08-25). Use 4.2. |
| **Ornith 35B** | — | — | — | — | **Not found. Zero search results. May not exist.** |

Sources by row: [Qwen3.8-27B benchmarks](https://www.yottalabs.ai/post/qwen-3-8-benchmarks-what-is-verified-2026), [Qwen3.8-27B MLX](https://www.orcarouter.ai/blog/qwen-3-8-27b-mlx); [Muse Glimmer model card](https://huggingface.co/meta-models/Muse-Glimmer-30B), [Meta AI Research](https://research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model), [VentureBeat](https://venturebeat.com/technology/meta-returns-to-open-source-with-muse-glimmer-an-apache-2-0-licensed-30b-parameter-ai-model-optimized-for-agents-available-now); [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B); [Qwen3-Coder-30B tool-calling harness](https://insiderllm.com/guides/best-local-coding-models-2026/); [Nemotron 3.5 Lightning model card](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard), [benchmarklist](https://benchmarklist.com/models/nvidia-nemotron-3.5-lightning-30b-a3b/); [Nemotron 3 Nano](https://llm-stats.com/models/nemotron-3-nano-30b-a3b); [Devstral 2](https://mistral.ai/news/devstral-2-vibe-cli/), [Vals AI](https://www.vals.ai/models/mistralai_devstral-2512); [Gemma 4 26B-A4B card](https://huggingface.co/google/gemma-4-26B-A4B-it), [AA comparison](https://artificialanalysis.ai/models/comparisons/gemma-4-26b-a4b-vs-gpt-oss-20b), [Infinum](https://infinum.com/blog/gemma-4-newest-benchmarks/), [HN Codex CLI report](https://news.ycombinator.com/item?id=47744255); [Granite 4.2](https://www.explainx.ai/blog/ibm-granite-4-2-open-reasoning-models-august-2026), [Granite 4.1](https://research.ibm.com/blog/granite-4-1-ai-foundation-models).

## 2.3 Two model-specific traps worth more than a table row

**gpt-oss-20b's Harmony problem is a harness problem, and it will bite `pi`.** OpenAI states both gpt-oss models "were trained on the harmony response format and should only be used with the harmony format, as they will not work correctly otherwise" ([gpt-oss-20b card](https://huggingface.co/openai/gpt-oss-20b)) `[strong — first-party]`. A 2026 arXiv paper argues the gap between published and reproduced scores "is not a model deficiency – it is a harness deficiency," and reproduced OpenAI's numbers using native harmony encoding ([arXiv 2604.00362](https://arxiv.org/html/2604.00362v1)) `[strong]`. Multiple HF discussions report the shipped Jinja chat template disagreeing with Harmony serialization on function-call token ordering ([discussion #218](https://huggingface.co/openai/gpt-oss-20b/discussions/218)), and named harnesses have broken on it — opencode ([issue #1633](https://github.com/anomalyco/opencode/issues/1633)) and NVIDIA NeMo Agent Toolkit ([forum](https://forums.developer.nvidia.com/t/nemo-agent-toolkit-failed-to-use-openai-gpt-oss-20b/341870)). **Recommendation: do not put gpt-oss-20b on the build lane.** If you use it, pull Unsloth's GGUF variants, which include chat-template fixes pushed upstream ([Unsloth gpt-oss guide](https://unsloth.ai/docs/models/gpt-oss-how-to-run-and-fine-tune)).

**Muse Glimmer has a real tooling gap on MLX.** `mlx-community/Muse-Glimmer-30B-4bit` exists at 19.4 GB with ~35k monthly downloads, converted with mlx-vlm 0.6.12. But **the `muse_glimmer` architecture is carried by neither mlx-lm nor mlx-vlm**, so `mlx_vlm.load()` cannot read these repos with stock tooling; the OptiQ variant works around it by registering the architecture via `import optiq` ([mlx-community/Muse-Glimmer-30B-4bit](https://huggingface.co/mlx-community/Muse-Glimmer-30B-4bit), [OptiQ variant](https://huggingface.co/mlx-community/Muse-Glimmer-30B-OptiQ-4bit)) `[strong — model cards]`. So the model with the best claimed SWE-bench number here (76.0) is the one whose local runtime story is least settled. Verify it loads before committing to it.

**Nemotron 3.5 Lightning's benchmark spread is a warning, not noise.** SWE-bench Verified 51.6 alongside Terminal-Bench 2.1 of 24.58 and τ³-bench Banking of 9.28 is a **76.1-point spread between best and worst agentic scores** ([benchmarklist](https://benchmarklist.com/models/nvidia-nemotron-3.5-lightning-30b-a3b/)) `[weak]`. That pattern says performance is sharply task-dependent. Also note NVIDIA says the BF16 release is intended for customization and post-training rather than production inference — benchmark numbers differ between BF16 and NVFP4 checkpoints.

## 2.4 Ranking by lane

### Lane (a) — build: reliable multi-step tool calling and editing

1. **Qwen3-Coder-30B-A3B** at 4-bit, ~19 GB. `[evidence: strong-ish]` The only candidate with an **independent** tool-calling measurement (77% tool selection, 80% agent accuracy, most balanced of four tested), plus a real M4 Pro memory-and-speed curve. Its raw SWE-bench (~50) is beaten by others, but agentic loops are throughput-bound and 3.3B active params is the reason this is the daily driver.
2. **Qwen3.8-27B** at 4-bit, ~15–18 GB. `[evidence: weak on agentic, strong on general]` Terminal-Bench 2.1 of 73.0 is the best agentic-shaped number in the fitting set, but vendor-reported. The +14-point independent Intelligence Index jump over Qwen3.6-27B at identical parameter count is the strongest independent signal any model here has. Dense, so slower than the A3B MoEs.
3. **Devstral Small 2** at 4-bit, ~14 GB. `[evidence: weak]` SWE-bench Verified 68.0, Apache 2.0, purpose-built for agentic coding, smallest footprint of the serious options. Best second-opinion model.
4. **Muse Glimmer 30B**. `[evidence: weak, tooling unproven]` Highest claimed SWE-bench (76.0) and explicitly built for always-on local agents, but see the MLX architecture gap above.

Not recommended for this lane: gpt-oss-20b (Harmony), Gemma 4 (independent report of agentic weakness), Nemotron 3.5 Lightning (Terminal-Bench 24.58).

### Lane (b) — read/review: long context and judgment

1. **Nemotron 3 Nano 30B-A3B**. `[evidence: weak but specific]` **1M context with RULER-100 at 86.3 measured at the 1M length** is the only long-context retention number in this whole set. Its SWE-bench of 38.8 disqualifies it from building and is irrelevant to reading. ~3.5B active keeps a huge context affordable.
2. **Qwen3.6-35B-A3B**. 262K native, extensible to 1M via YaRN, with a "Thinking Preservation" option retaining reasoning across historical messages — useful for a review pass over a long transcript.
3. **Qwen3.8-27B**. 262K native, best general judgment of the fitting set per the independent index.

Avoid **Gemma 4 31B dense** here specifically: ~16s to first token on a 32k prompt makes it a poor fit for RAG or codebase analysis, which is exactly this lane.

### Lane (c) — fast/cheap: summarization and classification

1. **Granite 4.2 8B**. `[evidence: strong on the efficiency claim]` Artificial Analysis noted the Granite 4.1 8B used ~4M output tokens to run its Intelligence Index, roughly **20x fewer than Qwen3.5 9B** — token efficiency is precisely the virtue this lane wants. Fits in under 6 GB, so it can stay resident alongside a build-lane model.
2. **Gemma 4 26B-A4B**. 3.8B active, 256K context, beats gpt-oss-20b (low) by 11.7 points on reasoning. Good where the classification needs some judgment.
3. **Nemotron 3 Nano 30B-A3B**, if already resident for lane (b) — reuse beats a second load.

Note the format wrinkle for this lane: GGUF beat MLX on wall clock for short classification tasks (1.0s vs 2.2s on M5 Max) because MLX's prefill dominates when output is short. **If you run a classification lane, run it on GGUF, not MLX** ([famstack.dev](https://famstack.dev/guides/mlx-vs-gguf-part-2-isolating-variables/)) `[weak, different hardware]`.

---

# PART 3 — OpenRouter through `pi`

## 3.1 The two controls are different things, and this matters

ZDR means the provider **does not retain** prompt or response at rest. `data_collection: "deny"` means the provider **does not train** on the data. A provider can satisfy one and not the other — it might retain logs 30 days (failing ZDR) while never training (passing data_collection) ([OpenRouter ZDR docs](https://openrouter.ai/docs/guides/features/zdr)) `[strong — first-party]`.

Both are set inside the `provider` object on a request: `order`/`only` restricts which providers may serve it, `data_collection: "deny"` blocks providers that store or train, and `zdr: true` requires ZDR endpoints. With `allow_fallbacks: false`, OpenRouter **returns an error instead of routing outside your list** — that is the setting that turns a preference into a guarantee.

The request-level `zdr` parameter operates as an **OR** with account-wide and guardrail settings: if any is enabled, enforcement applies. So you cannot loosen at request level what you tightened at account level.

**Conservative default:** if OpenRouter cannot establish a clear policy for an endpoint, it assumes that endpoint **both retains and trains** ([ZDR docs](https://openrouter.ai/docs/guides/features/zdr)) `[strong]`.

**Two carve-outs worth knowing.** ZDR enforcement applies only to provider routing for inference — **not to plugins and tools you enable**, such as web search, which are third parties with their own retention. And in-memory caching is treated as ZDR-compatible, so implicit-caching endpoints still qualify; use the "No Caching" filter if you need complete non-retention.

## 3.2 Free variants and the training opt-in — first-party wording

Yes, effectively. OpenRouter's settings page states you can set whether to allow routing to providers that may train on your data, and **"there are separate settings for paid and free models"** — opting out for paid does not cover `:free` ([provider logging docs](https://openrouter.ai/docs/guides/privacy/provider-logging), [privacy settings](https://openrouter.ai/settings/privacy)) `[strong — first-party]`.

If you opt out of training, OpenRouter will not route to providers that train. In practice **many `:free` endpoints become unavailable and requests fail rather than being silently rerouted**, because free tiers are frequently subsidized by data usage `[strong for the mechanism, weak for "many"]`.

Separately, OpenRouter's own logging: basic request metadata is logged; **prompts and completions are not logged by default**; you can opt in to prompt logging for a small discount (~1%), which gives OpenRouter the right to use your data commercially. That is a distinct decision from the provider-training toggle.

Org-level **Guardrails** can disable all retaining/training endpoints in one click, block individual models or providers, or restrict a workspace to an allowlist; disallowed requests fail with a 404. Guardrails can only be *more* restrictive than account policy ([Guardrails](https://openrouter.ai/blog/announcements/guardrails/)) `[strong]`.

## 3.3 Rate limits

| Tier | Limit |
|---|---|
| Paid models | No platform-level request cap; inherits upstream provider limits |
| Free models, before $10 credit purchase | 20 requests/min, **50/day** |
| Free models, after $10 purchase | 20 requests/min, **1,000/day** |

The 20 RPM cap does not move regardless of spend; the daily limit is the only lever, and the $10 purchase raises it permanently ([OpenRouter FAQ](https://openrouter.ai/docs/faq)) `[strong]`. Token-per-minute limits also apply at the provider level. **20 RPM is a hard ceiling for an agent loop** — a multi-tool-call turn can burn that in seconds.

## 3.4 Pricing (verify on the model page before budgeting)

| Model | Input /M | Output /M | Context |
|---|---|---|---|
| DeepSeek V4 Flash 0423 | $0.0679 | $0.168 (cache read $0.0168) | 1,048,576 |
| DeepSeek V4 Flash "Latest" | $0.04998 | $0.09996 (cache $0.009996) | 1,310,720 |
| GLM-5.3-Flash | $0.15 | $0.50 | 1.05M |
| Qwen3.8-27B | $0.22 | $2.42 | 1,000,000 |
| Muse Glimmer 30B | $0.35 | $1.50 | — |
| Nemotron 3.5 Lightning 30B-A3B | $0.08 | $0.20 | 262,144 |
| Kimi K2.7 | **Not found** | — | — |

Sources: [DeepSeek V4 Flash](https://openrouter.ai/deepseek/deepseek-v4-flash), [DeepSeek V4 Flash Latest](https://openrouter.ai/~deepseek/deepseek-v4-flash-latest), [GLM-5.3-Flash pricing via codersera](https://codersera.com/blog/glm-5-3-flash-complete-guide-2026/), [Qwen3.8 27B](https://openrouter.ai/qwen/qwen3.8-27b), [Muse Glimmer pricing](https://anotherwrapper.com/tools/llm-pricing/muse-glimmer-30b/qwen3.6-27b), [Nemotron 3.5 Lightning](https://openrouter.ai/nvidia/nemotron-3.5-lightning).

**Three warnings on these numbers.** Third-party trackers disagree with OpenRouter's own pages (one lists DeepSeek V4 Flash at $0.09/$0.18 against OpenRouter's $0.0679/$0.168). Model-page prices reflect **the cheapest provider under default load balancing** — pinning to a ZDR provider can cost substantially more, which means privacy and the quoted price are in direct tension. And **"qwen3.6" and "kimi-k2.7" did not resolve to current OpenRouter listings**; the live names appear to be Qwen3.8 Max / Qwen3.7 Max / Qwen3.8 Flash and Kimi K3 / K2.7 Code / K2.6.

**Fees.** No markup on inference, but credit purchases cost 5.5% on card (min $0.80) and 5% on crypto. BYOK is free below $25,000/month of list-price usage, 5% above ([truefoundry](https://www.truefoundry.com/blog/openrouter-pricing), [costbench](https://costbench.com/software/llm-api-providers/openrouter/)) `[weak — third-party]`.

## 3.5 `pi`'s OpenRouter provider config

**Model ID format: OpenRouter slugs are used verbatim as the model `id`, with the provider named separately.** Not `openrouter/<vendor>/<model>` — that is the `oh-my-pi` convention, a differently-named project. In `pi`:

```json
{
  "providers": {
    "openrouter": {
      "baseUrl": "https://openrouter.ai/api/v1",
      "api": "openai-completions",
      "apiKey": "$OPENROUTER_API_KEY",
      "models": [{ "id": "qwen/qwen3.8-27b" }]
    }
  }
}
```

Keys start with `sk-or-v1` ([bertomill guide](https://bertomill.medium.com/pi-coding-agent-setup-free-ai-models-via-openrouter-full-guide-fd40ea5dadb4), [pi models doc](https://pi.dev/docs/latest/models)) `[strong]`.

**Provider routing passes through.** `pi` has an OpenRouter provider-routing preferences object **sent as-is in the `provider` field** of the OpenRouter request, reachable via `modelOverrides.<model>.compat.openRouterRouting`. That is your ZDR lever:

```json
{
  "providers": {
    "openrouter": {
      "modelOverrides": {
        "qwen/qwen3.8-27b": {
          "compat": {
            "openRouterRouting": {
              "zdr": true,
              "data_collection": "deny",
              "allow_fallbacks": false
            }
          }
        }
      }
    }
  }
}
```

`modelOverrides` also supports `name`, `reasoning`, `thinkingLevelMap`, `input`, `cost`, `contextWindow`, `maxTokens`, `samplingParams`, and `headers` per model. Reasoning uses `reasoning: { effort }`. For openai-completions/openai-responses, `pi` sends an `x-session-id` header for session affinity, auto-detected by default ([pi models doc](https://pi.dev/docs/latest/models)) `[strong]`.

**Unverified:** the exact key spelling inside `openRouterRouting` was not read from a `pi` page directly. Since `pi` passes the object through unchanged, the field names are OpenRouter's, which is why the block above uses OpenRouter's documented `zdr` / `data_collection` / `allow_fallbacks`. **Confirm against a live request before trusting it for privacy.**

---

# RECOMMENDATION

## Server: run two, not one

**Primary — `llama-server` from Homebrew.** It is the only option with grammar and JSON-schema-constrained output, which is the difference between a tool call that always parses and one that mostly does. It gives explicit KV-cache quantization for buying context on 48 GB, speaks both OpenAI and Anthropic, and has first-party tool-calling docs. Its weakness is model management, which is not a real problem for three models. This satisfies the operator's requirement of not being constrained to Ollama.

**Keep Ollama 0.33.3 as the convenience lane** for pulling and trying models, and because on a 32 GB+ Mac it now defaults to MLX. **Set `OLLAMA_CONTEXT_LENGTH` explicitly and verify with `ollama ps`** — the silent 4K truncation through `/v1` is the documented failure that eats agent context without an error.

**Worth an evaluation, not a commitment: `vllm-mlx`.** It is the only option combining continuous batching, prefix caching, MLX, and both API shapes in one process, and it advertises Claude Code compatibility. But its performance claims are self-reported and there are two competing repos. Test it against a measured llama.cpp baseline before adopting.

**Skip LM Studio** unless you want its GUI: its licensing for commercial use is unconfirmed, some settings have no CLI path at all, and it adds a closed component to an otherwise open stack.

## Models: three, one per lane

1. **Qwen3-Coder-30B-A3B, 4-bit — build lane.** The only independent tool-calling evidence, plus a measured M4 Pro memory curve. ~19 GB.
2. **Qwen3.8-27B, 4-bit — heavy build and review.** Apache 2.0, 262K, the largest independent quality jump in the set. ~15–18 GB.
3. **Granite 4.2 8B, Q4_K_M — fast lane.** ~5–6 GB, exceptional token efficiency, stays resident alongside the others.

Optional fourth if you need long-context review: **Nemotron 3 Nano 30B-A3B** for its 1M context with an actual measured retention score at that length.

**Do not start with gpt-oss-20b** on the build lane. The Harmony format mismatch has broken named harnesses and is a documented harness-level problem, not a tuning issue.

## Commands (not executed)

```bash
# Server
brew install llama.cpp

# Build lane — Qwen3-Coder-30B-A3B, 64K context, quantized KV cache
llama-server --jinja -fa \
  -hf unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q4_K_M \
  --alias qwen3-coder-30b \
  -c 65536 --cache-type-k q8_0 --cache-type-v q4_0 \
  --host 127.0.0.1 --port 8080

# Heavy lane — Qwen3.8-27B
llama-server --jinja -fa \
  -hf unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL \
  --alias qwen3.8-27b \
  -c 65536 --cache-type-k q8_0 --cache-type-v q4_0 \
  --host 127.0.0.1 --port 8081

# Fast lane — Granite 4.2 8B
llama-server --jinja -fa \
  -hf ibm-granite/granite-4.2-8b-instruct-GGUF:Q4_K_M \
  --alias granite-4.2-8b \
  -c 32768 --host 127.0.0.1 --port 8082

# Ollama convenience lane — set context explicitly, then verify
export OLLAMA_CONTEXT_LENGTH=65536
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=10m
ollama serve
ollama pull qwen3.8:27b
ollama ps    # confirm the context actually applied

# Optional MLX evaluation
uv tool install mlx-openai-server
mlx_lm.server --model mlx-community/Qwen3.8-27B-4bit --port 8090
```

**Verify these repo and tag names on Hugging Face before pulling.** Exact GGUF quant filenames and Ollama tags were reported by third-party blogs, not read off the model pages, and squatted name-alike repos were reported for Qwen3.8-27B before the official release.

## `pi` config for all four lanes

```json
{
  "providers": {
    "local-build":  { "baseUrl": "http://127.0.0.1:8080/v1", "api": "openai-completions", "apiKey": "local",
                      "compat": { "supportsUsageInStreaming": false },
                      "models": [{ "id": "qwen3-coder-30b", "contextWindow": 65536, "maxTokens": 16384 }] },
    "local-heavy":  { "baseUrl": "http://127.0.0.1:8081/v1", "api": "openai-completions", "apiKey": "local",
                      "models": [{ "id": "qwen3.8-27b", "contextWindow": 65536, "maxTokens": 32768 }] },
    "local-fast":   { "baseUrl": "http://127.0.0.1:8082/v1", "api": "openai-completions", "apiKey": "local",
                      "models": [{ "id": "granite-4.2-8b", "contextWindow": 32768, "maxTokens": 8192 }] },
    "openrouter":   { "baseUrl": "https://openrouter.ai/api/v1", "api": "openai-completions",
                      "apiKey": "$OPENROUTER_API_KEY",
                      "models": [{ "id": "deepseek/deepseek-v4-flash" }, { "id": "z-ai/glm-5.3-flash" }] }
  }
}
```

Set the account-wide privacy toggles for **both** paid and free models before the first OpenRouter request, and expect `:free` endpoints to stop working once you opt out of training-permitted providers. That is the intended behavior, not a fault.

## The three things to measure before trusting any of this

1. **Time-to-first-token at 32K and 64K on your build model.** The one M4 Pro measurement found shows generation falling 5.4x from 1K to 64K context. Everything about lane assignment depends on where that curve sits for you.
2. **A 20-turn tool-calling loop, counting malformed tool calls.** Every agentic number in Part 2 except one is vendor-reported. Your own count is worth more than all of them.
3. **Whether Ollama's context setting actually applied.** `ollama ps` after a real agent request, not after a toy prompt.
