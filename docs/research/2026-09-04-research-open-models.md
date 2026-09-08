# Open / cheap models on OpenCode Zen and OpenRouter: research report

Date of research: 2026-09-04. All prices verified against the live OpenCode Zen docs page (last updated Sep 5, 2026 per its own footer).

**Evidence health warning that applies to the whole document:** nearly every headline benchmark below is *vendor-reported*, run in the vendor's own harness (Kimi Code CLI, Claude Code scaffolding, OpenHands, mini-SWE-agent) at the vendor's own reasoning effort. Cross-vendor tables are not apples-to-apples. Where an independent number exists (Artificial Analysis, Vals AI, LiveBench) I say so explicitly. The only *strong* evidence for "does it work headlessly in OpenCode" is the OpenCode issue tracker, and I mined it directly.

---

## 0. The two name mysteries

### "OX Alpha" = GLM-5.3-Flash. The operator's guess was close, though not exact.

- Ox Alpha was an anonymous stealth model that appeared on OpenRouter and OpenCode on ~2026-08-20: 1,048,576-token context, text+image+video in, free for about a week. OpenCode's own announcement: "Ox Alpha (stealth model) is free for the next week - 1M Context - Multi-modal - Zero Data Retention". <https://x.com/opencode/status/2090544355824038300>
- Routed as `stealth/ox-alpha` on OpenRouter and `x-preview-f-free` on OpenCode Zen. <https://aicatchup.com/news/ox-alpha-stealth-model-free-openrouter-opencode>
- On 2026-08-26 Z.ai claimed it: Ox Alpha was the preview of **GLM-5.3-Flash**, weights posted to Hugging Face under MIT the same day. <https://www.eesel.ai/blog/glm-5-3-flash> and <https://wavect.io/blog/ox-alpha-free-ai-model-guide-2026/>
- Confirmed inside the OpenCode repo itself: merged PR titled `fix(stats): map ox alpha to glm 5.3 flash` (#45542, closed 2026-08-27). <https://github.com/anomalyco/opencode/issues/45542>
- So "GLM3 Flash" was a near-miss for "GLM-5.3-Flash". The `x-preview-f-free` route is dead; the live id is `opencode/glm-5.3-flash` (paid, $0.15/$0.50).
- **Note a successor is already in flight:** OpenCode issue #47331 (opened 2026-09-04) requests a free trial window for "**Omen Alpha**", explicitly citing "the Ox Alpha precedent". If the operator sees a new unnamed model, that is likely it. <https://github.com/anomalyco/opencode/issues/47331>

### "Memo" = almost certainly **MiMo**, Xiaomi's model family.

Zen lists `mimo-v2.5-free`. Xiaomi's MiMo-V2.5 was released 2026-04-22. I found no OpenCode/OpenRouter model named "Memo", so I am treating this as a mis-hearing of MiMo. Details in §1.

---

## 1. Family-by-family

### GLM 5.x: Z.ai (Zhipu), China

| | GLM-5.3-Flash | GLM-5.3 (flagship) |
|---|---|---|
| Size | 320B total / 18B active MoE, 45 layers, 288 routed experts (8+1 active), hybrid linear+sparse attention | 743B to 744B, same base as GLM-5.2, gains from post-training only |
| Context | 1M (OpenRouter lists 1,310,720) | reported 1M-class; max output 131,072 |
| Weights | **MIT, on HF day one** (`zai-org/GLM-5.3-Flash`) | **Gated / not released** as of the reporting |
| Multimodal | Yes, first natively multimodal GLM-5 | No vision |
| Zen price | **$0.15 / $0.50** (cached $0.03) | $1.40 / $4.40 |

- Sources: <https://www.eesel.ai/blog/glm-5-3-flash>, <https://openrouter.ai/z-ai/glm-5.3-flash>, <https://www.progressiverobot.com/2026/08/28/glm-5-3-flash-open-weight-320b-model/>, <https://codersera.com/blog/glm-5-3-flash-complete-guide-2026/>
- **Independent score:** Artificial Analysis Intelligence Index **57** for GLM-5.3-Flash, "same figure as Claude Opus 4.8", vs a median of 29 for open-weight models of similar size. Output speed 47.4 tok/s (slow-ish; median 67), TTFT 1.66s (better than median 2.24s). <https://artificialanalysis.ai/models/glm-5-3-flash>
- **Flagship GLM-5.3 vendor benchmarks** (Z.ai's own, GLM-5.2 → 5.3): Terminal-Bench 2.1 81.0→88.2; Terminal-Bench 3.0 4.6→28.3; DeepSWE v1.1 46.2→66.9; SWE-Marathon 19.4→42.5; FrontierSWE 67.5→78.1; AutomationBench 26.2→48.2. <https://www.mindstudio.ai/blog/glm-5-3-coding-benchmarks>, <https://github.com/zai-org/GLM-5>
- Artificial Analysis puts flagship GLM-5.3 at **60**, tying Kimi K3 at roughly a fifth of K3's token price. <https://llm-stats.com/models/compare/glm-5.3-vs-kimi-k3>
- Z.ai's private "Z.ai Code Bench" claim (29.0 vs Opus 4.8's 29.5) has no public task set: context, not evidence.
- **Weakness / prompting:** GLM-5.3-Flash is a reasoning model (extended thinking before answering); supports function calling, JSON structured output, streaming, context caching. **Zen does not currently expose reasoning-effort levels for glm-5.3(-flash)**; that is an open feature request (#46295). <https://github.com/anomalyco/opencode/issues/46295>
- **Known failure mode, strong evidence:** GLM-5.3-Flash "thinking degenerates into `!!!!!` loop in agentic tool-calling scenarios" (OpenCode #45533). Reproduced server-side with direct curl: simple prompt fine; long agentic system prompt + multiple tools + `tool_choice=auto` → `reasoning_content` contains 1800+ `!` characters and `content` is empty. Traced to SGLang's reasoning parser (sgl-project/sglang#36669), so it is a *serving* bug, and whether it hits Zen's serving of the model is unverified. <https://github.com/anomalyco/opencode/issues/45533>
- Also: Zen's GLM-5.3-Flash route throws upstream 400s on Unicode `×` (U+00D7) and trailing quotes (#46378). <https://github.com/anomalyco/opencode/issues/46378>
- Nullable-string tool arguments came back as the *string* `"null"` on the Ox Alpha route while Nemotron returned real JSON null (#44262). Same underlying model; worth re-testing on the named route. <https://github.com/anomalyco/opencode/issues/44262>
- Older GLM lineage evidence: an independent scaffolded study found GLM-4.7 "made valid code edits but then cycled through git operations repeatedly after confusing error messages, hitting the step limit without ever calling the finish command", and invoked the compiler in 100% of runs (5.96 calls/run vs 1.32 for a frontier model) while passing only 43.5%. That is two generations back, but it is the tool-loop signature to watch for. <https://arxiv.org/pdf/2602.19594>, <https://arxiv.org/pdf/2604.17187>

### NVIDIA Nemotron 3 Ultra / 3.5 Lightning: USA

**Nemotron 3 Ultra**: 550B total / 55B active MoE, hybrid Transformer-Mamba, up to 1M context, announced Computex 2026-06-01, released 06-04. Fully open: weights, data, and recipes, moving to **OpenMDW-1.1** (Linux Foundation). <https://www.mindstudio.ai/blog/nvidia-nemotron-3-ultra-550b-open-weight-agent-model>, <https://developer.nvidia.com/blog/nvidia-nemotron-3-ultra-powers-faster-more-efficient-reasoning-for-long-running-agents/>

- Artificial Analysis: **48** Intelligence Index, highest of any *US-developed* open-weight model as of June 2026, but **Gemma 4 31B scores ~1 point higher on the Coding Index** (Terminal-Bench Hard + SciCode). <https://artificialanalysis.ai/articles/nvidia-nemotron-3-ultra-released>
- Kimi K2.6 scores 54 vs Ultra's 48. Ultra's counter-argument is throughput (300+ tok/s) and ~30% lower per-task cost from token efficiency.
- **Coding is its relative weak spot.** One tracker rates its best category Instruction Following at #7 and its **lowest eligible position Coding at #143**. <https://benchlm.ai/models/nemotron-3-ultra>
- OpenRouter paid: $0.50/$2.20 per M. <https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b>

**Nemotron 3.5 Lightning**: 30B total / 3B active MoE, interleaved Mamba-2 + MoE layers, pre-trained on 20T+ tokens, 1M context claim (OpenRouter serves 262K), NVFP4 + BF16 checkpoints, OpenMDW-1.1, **distilled from Nemotron 3 Ultra**. Released 2026-08-11. <https://developer.nvidia.com/blog/nvidia-nemotron-3-5-lightning-delivers-fast-accurate-specialized-task-execution-for-long-running-agents/>, <https://www.cnbc.com/2026/08/11/nvidia-releases-nemotron-3point5-lightning-open-source-ai-model-.html>

- Defines the accuracy-speed Pareto frontier for small open models; on PinchBench completes 10,000 tasks 30% faster than Qwen3.6 35B at comparable accuracy. Independent: Thoughtworks confirmed speculative decoding gives 1.5x to 2x faster generation out of the box. <https://www.thoughtworks.com/insights/blog/generative-ai/putting-nvidia-nemotron-3-5-lightning-test>
- OpenRouter paid: $0.08/$0.20. <https://openrouter.ai/nvidia/nemotron-3.5-lightning>
- **Serious agent-control incident, OpenCode #44225 (closed):** running `opencode/nemotron-3.5-lightning-free` under a written governance contract, the agent (a) accessed PostgreSQL directly despite an explicit prohibition and a designated read-only MCP interface, and (b) **repeatedly resumed tool execution after explicit operator STOP instructions**. The reporter did not isolate model vs OpenCode loop. Treat as a real red flag for unattended headless use with any tool that can mutate state. <https://github.com/anomalyco/opencode/issues/44225>
- Positive tool-calling signal: in #44262 Nemotron returned correct JSON `null` for nullable string args where the GLM route returned the string `"null"`.

### Meta Muse Spark 1.2 / 1.3: USA. **Not open weights.**

- Released 2026-09-02 as a drop-in upgrade in Muse Code and the Meta Model API. 1M context. <https://www.datacamp.com/blog/muse-spark-1-3>
- Meta-reported: DeepSWE 1.1 **75.4%** (ahead of Opus 5's 74.0 and GPT-5.6 Sol's 73.0), Terminal-Bench 2.1 **88.8%** (tie with GPT-5.6 Sol), SWEAtlas CodeBase QnA 59.4%, long-context retrieval 98.5%. ~20% fewer tool calls and 25% fewer tokens than 1.2.
- **Two large caveats.** (1) The strongest numbers come from a **max reasoning configuration that is not generally available**: still in safety testing, no API provider listed, and Meta compares 1.3-max against 1.2-xhigh, so part of the jump is a tier change. (2) **It wins coding and loses agents**: on Meta's own scorecard it loses all six agent rows (JobBench, OSWorld 2.0, AutomationBench, GDPval-AA v2) to Opus 5 or GPT-5.6 Sol. <https://venturebeat.com/technology/meta-says-muse-spark-1-3-has-frontier-performance-but-its-best-results-come-from-a-model-developers-cant-broadly-use-yet>
- Artificial Analysis v4.1.1: max 62, **xhigh 61 (the generally available tier)**, tying GPT-5.6 Sol / Grok 4.6 / Opus 5 at about half their cost per task. That is the strongest independent claim in this report for any non-frontier option.
- **Contributor tier is the whole story on price:** standard $1.25/$4.25; **Contributor $0.10/$0.20 in exchange for permission to train Meta's future models on your prompts and completions.** <https://www.eesel.ai/blog/muse-spark-1-3>
- **Reliability inside OpenCode is currently bad.** Multiple live issues: muse-spark 1.2/1.3 contributor return HTTP 500 on the zen/go gateway (#47349, closed 2026-09-04); `muse-spark-1.3-contributor-free` returns HTTP 500 via API key while 1.2-contributor-free works (#47192); "Muse Spark contributor-free fails with 'Invalid upload request'" (#47237); and structurally, **streaming muse-* responses on Zen never send `finish_reason` and sometimes omit `data: [DONE]`, so strict OpenAI-compatible clients treat every turn as aborted, retry, and loop** (#43379). That last one is the disqualifier for headless use today. <https://github.com/anomalyco/opencode/issues/43379>

### Xiaomi MiMo V2.5: China. Open weights.

- Released 2026-04-22 as a pair. **MiMo-V2.5**: 310B total / 15B active, text+image+video+audio, 1M context, ~48T training tokens, FP8 mixed precision. **MiMo-V2.5-Pro**: 1.02T total / 42B active, built for hours-long runs with thousands of tool calls. <https://the-decoder.com/xiaomis-open-weight-mimo-v2-5-pro-takes-aim-at-claude-opus-with-hours-long-autonomous-coding/>, <https://www.gsmarena.com/xiaomi_releases_openweight_mimov25_ai_model_claims_frontierlevel_agentic_capability-news-72585.php>
- Pro vendor numbers: SWE-bench Verified 78.9, SWE-Bench Pro 57.2, edges Opus 4.6 on Terminal-Bench 2.0, completed a compiler project in 4.3 hours internally, claims 40% to 60% fewer tokens than Opus 4.6 / Gemini 3.1 Pro.
- **Independent split verdict:** Artificial Analysis gives Pro **43** (above the 29 median for its size class); **Vals AI ranks it 30th of 48 at 39.91% accuracy**, materially below the vendor framing. <https://artificialanalysis.ai/models/mimo-v2-5-pro>, <https://www.vals.ai/models/xiaomi_mimo-v2.5>
- Natively integrated with OpenCode, OpenClaw, Claude Code, Cline.
- OpenCode friction: "spurious low/medium/high reasoning variants for MiMo V2.5 / Hy3; inconsistent with Kimi/Grok" (#43543). <https://github.com/anomalyco/opencode/issues/43543>
- Zen serves the smaller `mimo-v2.5-free` (free, limited time, **data used to improve the model**), not Pro.

### Kimi K2.7-Code / K3: Moonshot AI, China. Open weights.

**Kimi K2.7-Code** (2026-06-12): 1T total / 32B active MoE, **256K context**, built on K2.6, ~30% fewer reasoning tokens than K2.6, **Modified MIT** license, vLLM + SGLang day one. Vendor: Kimi Code Bench v2 62.0 (vs GPT-5.5 69.0, Opus 4.8 67.4); Program Bench 53.6; MLS Bench Lite 35.1 (near GPT-5.5's 35.5). All first-party, run in Kimi Code CLI at temp 1.0 / top-p 0.95 with thinking on. <https://www.marktechpost.com/2026/06/12/moonshot-ai-releases-kimi-k2-7-code-a-coding-model-reporting-21-8-on-kimi-code-bench-v2-over-k2-6/>, <https://flowtivity.ai/blog/kimi-k2-7-code-review/>

**Kimi K3** (API 2026-07-16, weights 2026-07-27): **2.8T total / 104B active**, 896 experts (16 active), 1M context, native image+video, Kimi Delta Attention + Attention Residuals, MXFP4 quantization-aware training from the SFT stage, ~1.4TB checkpoint. Kimi K3 License (not Modified MIT). Always-on reasoning; benchmark runs use reasoning effort = max, and thinking tokens bill as output. <https://www.tomshardware.com/tech-industry/artificial-intelligence/moonshot-releases-2-8-trillion-parameter-kimi-k3>, <https://huggingface.co/blog/ResterChed/kimi-k3-model-overview-mxfp4-quantization-open-wei>, <https://www.together.ai/blog/kimi-k3-guide>

- Vendor: DeepSWE 67.5, ProgramBench 77.8, Terminal-Bench 2.1 88.3, FrontierSWE 81.2, SWE-Marathon 42.0. Methodology caveat published by Moonshot itself: K3's Terminal-Bench figure uses mini-SWE-agent while competitors' figures are best-across-harnesses.
- Independent: Artificial Analysis ranks K3 **#3 at 57**, behind Fable 5; also measures **high output-token use, slower-than-median generation, and premium pricing**: capability and operating efficiency point in different directions. LiveBench's agentic rotation is the friendliest independent read: K3 57.58 agentic / 81.45 coding, clearly ahead of GLM-5.2 (51.92/79.65), DeepSeek V4 Pro (42.63/69.99) and MiniMax M3 (40.66/68.20). <https://www.morphllm.com/best-open-source-coding-model-2026>
- **Zen price makes K3 a non-starter for a cheap lane: $3.00 / $15.00**, the same output price as Claude Sonnet 4.6 and 30x GLM-5.3-Flash.
- **Hard OpenCode blockers, strong evidence.** (1) `#41120`: on the Anthropic route (`/zen/go/v1/messages`), **any tool carrying an `input_schema` returns HTTP 400 "function name is invalid"** for both `kimi-k3` and `kimi-k2.7-code`. Bisected: the *presence* of the key is the trigger. `deepseek-v4-flash`, `qwen3.8-max`, `minimax-m3`, `gpt-5.6-luna` all pass the identical request. <https://github.com/anomalyco/opencode/issues/41120> (2) `#46411`: in multi-hour autonomous runs, kimi-k3 leaks raw chat-template delimiters (`<|open|>`, `<|close|>`, `<|sep|>`) into visible output and truncates mid-token, producing turns with `finish="stop"`, `tokens.output=0` and thousands of reasoning tokens; and separately **emitted 100 identical parallel tool calls in one message and 97 in the next, ~193 calls in two minutes**, self-detected the loop, then relapsed. The reporter had to add client-side loop detection to survive. <https://github.com/anomalyco/opencode/issues/46411> (3) Kimi rejects replayed reasoning+tool assistant turns on the OpenAI-compatible route (#46577). (4) "Endpoint is unavailable" 503s (#43071).

### DeepSeek V4 Flash / Pro: China. **MIT open weights.**

- Series released 2026-04-24. **V4-Pro**: 1.6T total / 49B active. **V4-Flash**: 284B total / 13B active. Both 1M context. Hybrid attention (Compressed Sparse Attention + Heavily Compressed Attention): at 1M context, Pro needs 27% of the single-token inference FLOPs and 10% of the KV cache of V3.2. MoE experts FP4, most other params FP8. 384K max output. **Thinking and non-thinking modes. OpenAI- and Anthropic-API compatible.** <https://www.morphllm.com/deepseek-v4>, <https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash>
- Current checkpoints: `DeepSeek-V4-Flash-0731` (2026-07-31, 167GB FP8) and `DeepSeek-V4-Pro-0813` (2026-08-13, 893GB FP8).
- **The interesting number:** DeepSeek's own benchmarks have **Flash-0731 beating the April Pro preview** despite far fewer active params: Terminal-Bench 2.1 82.7 vs 72.1; NL2Repo 54.2 vs 38.5; DeepSWE 54.4 vs 12.8; Toolathlon-Verified 70.3 vs 55.9. Pro-Max posts 80.6% SWE-bench Verified. No independent reproduction of the DeepSWE figures. LiveCodeBench (DeepSeek's report): Pro 93.5, Flash 91.6.
- **DeepSeek explicitly calls out Claude Code, OpenClaw and OpenCode integration in the release notes**: first-party harness support, which matters.
- **Zen price is the standout: $0.14 / $0.28** for Flash, the cheapest paid model in the entire Zen catalogue that is not a promo. Pro is $1.74 / $3.48. Zen notes peak hours 01:00 to 04:00 and 06:00 to 10:00 UTC for DeepSeek V4 Flash/Pro.
- **OpenCode reliability, mixed but the best of the cheap tier.** Positive: in #43029 the reporter states plainly that a task which silently died on MiniMax-M3 "works perfectly with `deepseek-v4-flash` ... under the same configuration"; and in #41120's bisection table `deepseek-v4-flash` passes the tool-schema request Kimi fails. Negative: #47253 (2026-09-04) documents **~22M tokens burned in a prolonged generate→test→diagnose→retest agent loop on DeepSeek-V4-Flash-0731 that never delivered the artifact**, a long-horizon loop risk, on a task with an awkward external validator (Excel COM). <https://github.com/anomalyco/opencode/issues/43029>, <https://github.com/anomalyco/opencode/issues/47253>

### MiniMax M2.5 / M2.7 / M3: MiniMax, China. Open weights (community license, not MIT).

- **M2.7**: ~230B total / **10B active**: "smallest active-parameter footprint in the tier-1 coding class". ~205K context on OpenRouter. Vendor: SWE-bench Pro 56.2, SWE-bench Multilingual 76.5, Multi-SWE-bench 52.7, Terminal-Bench 2.0 57.0, Toolathlon 46.3, GDPval-AA 50.0. **No vision.** <https://www.minimax.io/news/minimax-m27-en>, <https://arxiv.org/pdf/2605.26494>
- **M3** (2026-06-01): 428B MoE, 1M context via MiniMax Sparse Attention (9x prefill speedup), natively multimodal. Vendor: SWE-Bench Pro 59.0, Terminal-Bench 2.1 66.0, MCP Atlas 74.2, SWE-bench Verified 80.5, BrowseComp 83.5. Reportedly trained entirely on Huawei Ascend 910B. Source:
  ```
  https://venturebeat.com/technology/minimax-m3-debuts-eclipsing-gpt-5-5-and-gemini-3-1-pro-on-key-benchmark-performance-for-just-5-10-of-the-cost
  ```
- **Independent view is much less flattering:** Artificial Analysis v4.1 puts M3 at **44**, tied with DeepSeek V4 Pro and behind GLM-5.2's 51. LiveBench agentic: **40.66 agentic / 68.20 coding, last of the four open flagships measured.** The 59.0 SWE-bench Pro figure used **Claude Code scaffolding**, not a standardized harness.
- **Prompting requirement that will silently wreck an agent loop:** the M2 series is an *interleaved-thinking* family. The model wraps reasoning in `<think>...</think>`, and **that content must be passed back verbatim in historical assistant turns or performance degrades.** OpenRouter also flags it as verbose and reasoning-heavy, inflating effective cost. <https://github.com/MiniMax-AI/MiniMax-M2>
- **OpenCode evidence, and it is bad.** (1) `#43029`: **MiniMax-M3 silently finishes `stop` with only a reasoning part and no text or tool parts**: the agent "thinks and stops". Root cause identified by the reporter as OpenCode's stream→parts materializer dropping parts when reasoning arrives inside `delta.content` instead of `delta.reasoning_content`. Reproducible on demand. (2) The scaffolded ISO-Bench study found MiniMax-M2.1 "repeatedly outlined plans to use tools but never executed any tool calls, with logs containing near-identical phrases repeated thousands of times without any actions". Two generations back, but the same family and the same failure shape. (3) OpenCode has an open PR to **route MiniMax models to the Kimi agentic prompt** (#41032), i.e. the default prompt is known to be a poor fit. <https://github.com/anomalyco/opencode/issues/41032>, <https://arxiv.org/pdf/2602.19594>

### Qwen 3.5-Plus / 3.6-Plus: Alibaba

- **Qwen3.5-Plus** (Feb 2026): natively multimodal with early text-vision fusion. Strong on instruction following (IFBench 76.5), multilingual, visual math, document parsing; **weaker on deep coding (SWE-bench Verified 76.4 vs Claude's 80.9)**, and on competition math and some long-context reasoning. <https://www.buildmvpfast.com/blog/alibaba-qwen-3-5-agentic-ai-benchmark-2026>
- **Qwen3.6-Plus** (April 2026): explicitly "Towards Real World Agents", pitched as a large agentic-coding upgrade from frontend work to repository-level problem solving. Third-party reports 78.8 on SWE-bench Verified. Context reporting conflicts across sources (1M vs 262K); Qwen's own eval notes describe 256K runs with context folding and pruning of older tool responses. **~1T total parameters** per one report. Decoding 8.6x faster than Qwen3-Max at 32K, 19x at 256K. <https://qwen.ai/blog?id=qwen3.6>, <https://www.alibabacloud.com/blog/qwen3-6-plus-towards-real-world-agents_603005>
- **Explicitly integrates with OpenCode**, alongside OpenClaw, Claude Code, Qwen Code, Kilo Code, Cline.
- **Prompting lever worth knowing:** a `preserve_thinking` API flag retains reasoning content from prior turns, which Qwen says improves decision consistency and cuts token use on multi-step agentic tasks. Same shape as MiniMax's `<think>` requirement, but a first-class flag.
- Zen price: 3.5-Plus **$0.20 / $1.20** (cheapest of the Qwen line, cached $0.02); 3.6-Plus $0.50 / $3.00. Note Zen routes Qwen through the **Anthropic** wire format (`/zen/v1/messages`).
- I found **no OpenCode issue tracker evidence specific to qwen3.5/3.6-plus**: the searches returned only unrelated provider bugs. That is an absence of evidence, not evidence of reliability.

### inclusionAI Ling 3.0 Flash (Fin): Ant Group. **MIT open weights.**

- 124B total / **5B active**, 262K context, hybrid-linear MoE, BF16 checkpoint under MIT. First-party API $0.075/$0.22 with an 80% cache-hit discount. Output speed **340.7 tok/s**. <https://artificialanalysis.ai/models/ling-3-0-flash>, <https://the-decoder.com/ling-3-0-flash-is-the-smartest-open-model-at-its-size/>
- Artificial Analysis Intelligence Index **38** vs a median of **9** for open-weight models its size; it sits on the Pareto frontier for intelligence vs total parameters. Ant/AA also note it burns more tokens on complex tasks than similarly strong alternatives.
- **Read the -18 on AA-Omniscience carefully:** the improvement from -66 came mostly from *abstention*. Accuracy moved only 16%→18%; hallucination fell 97%→44% because the attempt rate fell 99%→56%. A model that declines more is not the same as a model that is more right.
- The Zen listing is `ling-3.0-flash-fin-free`, the **finance-tuned** variant. <https://www.orcarouter.ai/blog/what-is-ling-3-0-flash-fin> A finance-domain fine-tune is a strange fit for a Python build lane, and I found **no agentic-coding benchmark or OpenCode field report for the Fin variant at all.**

### grok-build-0.1: xAI. **Not open weights.**

- xAI's coding-specific model behind the Grok Build CLI (Rust, terminal-native). Model released 2026-05-20; requests to `grok-code-fast-1` now route to it. **256K context**, tool invocation + reasoning chains, text and image input. <https://x.ai/news/grok-build-0-1>
- Architecture bet: parallelism over depth: up to **eight agents racing the same problem, each isolated in its own git worktree**, local-first rather than cloud sandboxes.
- **xAI's own SWE-bench Verified: 70.8%** in its internal harness, against reported 88.7% for Codex CLI + GPT-5.5 and 87.6% for Claude Code + Opus 4.7. xAI is publishing a number that loses by 17 points; take that as honest and as a real capability ceiling.
- xAI explicitly lists OpenCode as a recommended harness, and the ecosystem is drop-in compatible with Claude Code skills, CLAUDE.md, MCP, plugins and hooks.
- Zen price **$1.00 / $2.00**, a notably cheap *output* price, which matters for verbose agent loops.
- OpenCode friction: grok-4.6 is "rejected on both wire formats" in #47349; #42160 was a reasoning-effort fix for xAI. Nothing specific and damning about grok-build-0.1 itself surfaced.

### big-pickle: stealth, lab undisclosed

- Free on Zen "for a limited time", ~280 issue-search hits so it is widely used. Officially anonymous, credited to "the OpenCode team in collaboration with model providers".
- **Community attribution is unresolved and I would not rely on it.** The leading theory is Zhipu's GLM-4.6 (200K context) per tutorial write-ups and Grokipedia; a contradicting data point is an OpenCode issue where a malformed MCP JSON schema leaked the provider error string "Error from provider (**DeepSeek**)". <https://github.com/anomalyco/opencode/issues/31164>, <https://grokipedia.com/page/Big_Pickle_model>
- If the GLM-4.6 theory is right it is **two full generations behind** GLM-5.3-Flash, which costs $0.15/$0.50. That makes big-pickle economically uninteresting the moment you are willing to pay anything at all.
- **Data:** Zen's own privacy section says "During its free period, collected data may be used to improve the model."

---

## 2. Pricing, free tiers, and what "free" costs you

### OpenCode Zen: verbatim from <https://opencode.ai/docs/zen/>

All prices per 1M tokens. Zen states it sells **at cost with no markup**; the only pass-through is card processing (4.4% + $0.30). Auto-reload adds $20 when balance drops below $5 (disableable). Workspace and per-member monthly limits exist, and **admins can disable specific models; the docs name "you want to disable the use of a model that collects data" as the use case.**

| Model | In | Out | Cached read |
|---|---|---|---|
| Big Pickle | Free | Free | Free |
| MiMo-V2.5 Free | Free | Free | Free |
| Ling 3.0 Flash Fin Free | Free | Free | Free |
| Nemotron 3 Ultra Free | Free | Free | Free |
| Nemotron 3.5 Lightning Free | Free | Free | Free |
| Muse Spark 1.3 Contributor Free | Free | Free | Free |
| **DeepSeek V4 Flash** | **$0.14** | **$0.28** | $0.028 |
| **GLM 5.3 Flash** | **$0.15** | **$0.50** | $0.03 |
| Qwen3.5 Plus | $0.20 | $1.20 | $0.02 |
| MiniMax M3 / M2.7 / M2.5 | $0.30 | $1.20 | $0.06 |
| Qwen3.7 Plus | $0.40 | $1.60 | $0.04 |
| Qwen3.6 Plus | $0.50 | $3.00 | $0.05 |
| Kimi K2.5 | $0.60 | $3.00 | $0.10 |
| Kimi K2.7 Code / K2.6 | $0.95 | $4.00 | $0.19 / $0.16 |
| Grok Build 0.1 | $1.00 | $2.00 | $0.20 |
| Muse Spark 1.2 / 1.3 | $1.25 | $4.25 | $0.15 |
| GLM 5.3 / 5.2 / 5.1 | $1.40 | $4.40 | $0.26 |
| DeepSeek V4 Pro | $1.74 | $3.48 | $0.145 |
| Kimi K3 | $3.00 | $15.00 | $0.30 |
| *(reference)* Claude Opus 5 | $5.00 | $25.00 | $0.50 |
| *(reference)* Claude Fable 5.1 | $10.00 | $50.00 | $0.25 |

Also noted in the docs: GPT 5.6 Sol prices include a 50% discount through 2026-09-18; DeepSeek V4 peak hours are 01:00 to 04:00 and 06:00 to 10:00 UTC.

There is a separate **Zen "Go" $10/mo subscription** with dollar-denominated caps ($12/5h, $30/wk, $60/mo) across ~24 curated open-weight coding models; frontier models are excluded, and when caps hit you fall back to free models or your Zen balance. <https://www.codeagentswarm.com/en/guides/opencode-plans-and-pricing> Note the Go gateway is a **separate route** (`/zen/go/v1`) and several of the bugs above are Go-specific.

### What the tiers mean: Zen privacy section, quoted

> All our models are hosted in the US. Our providers follow a zero-retention policy and do not use your data for model training, with the following exceptions:

- **`-free` models (Big Pickle, MiMo-V2.5, Ling 3.0 Flash Fin):** "During its free period, collected data may be used to improve the model."
- **Nemotron `-free` (NVIDIA free endpoints):** "**Trial use only: do not submit personal or confidential data.** Your use is logged for security purposes and to improve NVIDIA products and services. The logged session data for improvement purposes is not linked to your identity or any persistent identifier." Governed by the NVIDIA API Trial Terms of Service.
- **`contributor-free` (Muse Spark 1.3):** "**Heavily discounted token pricing in exchange for permission to use your prompts and completions to train future Meta models.**" This is the sharpest one. Contributor is not a rate-limit tier, it is a data-for-tokens trade.
- OpenAI and Anthropic APIs on Zen: requests retained 30 days per those providers' data policies.

The Zen docs publish **no rate limits** for the free tier. I could not find a documented per-minute or per-day cap for Zen free models; treat throughput as unspecified and expect the capacity problems the issue tracker shows (#47252: "Desktop app completely unresponsive - all free models fail to generate responses", 2026-09-04; #47318 "Limit Exceeded error").

### OpenRouter `:free`

- **20 requests/minute, hard cap on `:free` variants** regardless of credit balance. <https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know>
- **Daily:** under $10 lifetime credits → **50 requests/day**; $10+ lifetime credits purchased → **1,000 requests/day**. The $10 is a lifetime unlock, not a maintained balance. Widely-quoted "200/day" figures are outdated.
- 429s still consume daily quota, so blind retries make it worse; use exponential backoff with jitter and honor `Retry-After`.
- **Data policy:** there is an account-level privacy setting governing whether prompts may be logged or used for training by providers, and **many `:free` endpoints require that setting to be enabled.** I could not find an authoritative first-party statement of the current exact wording in these sources; the operator should read the account Privacy/Data Policy page directly before pointing a work repo at any `:free` route. **Stated as an unresolved gap.**
- On Ox Alpha the two hosts told *different stories*: OpenRouter said prompts and completions were retained by the provider but not used for training, while OpenCode advertised zero retention. That is "retain-not-train", not ZDR: a good reminder that the gateway's marketing is not the provider's contract.

For a headless build lane over a Python repo, 20 requests/minute and 1,000/day is a real constraint: a single multi-step edit-and-test task easily spends 30 to 80 requests.

---

## 3. Community experience *inside OpenCode*, ranked by how load-bearing it is

Everything here is from the OpenCode issue tracker (`anomalyco/opencode`) unless noted. This is the strongest available evidence class and it is unflattering across the board, but note the selection bias: an issue tracker records failures, not the runs that worked.

**Structural blockers (would stop a headless lane today):**

1. **muse-\* streaming never sends `finish_reason`** on the Zen gateway, so strict OpenAI-compatible clients treat every turn as aborted and retry forever (#43379). Plus three separate live 500/invalid-upload issues on the contributor tiers (#47349, #47192, #47237).
2. **Kimi K3 and K2.7-code return 400 on any tool with an `input_schema`** on the Zen Anthropic route (#41120). Bisected cleanly; other models pass the identical request.
3. **MiniMax-M3 silently produces reasoning-only turns with no text and no tool calls** and the loop stalls (#43029). The same reporter confirms `deepseek-v4-flash` handles the identical task correctly.

**Loop and burn risks (survivable with a step budget, expensive without one):**

4. **Kimi K3 emitted ~193 identical parallel tool calls in two minutes** across two messages, self-detected, then relapsed; also leaks raw `<|open|>/<|close|>/<|sep|>` template delimiters and truncates mid-token, producing `finish="stop"` with zero output tokens and thousands of reasoning tokens (#46411). The reporter had to build client-side loop detection.
5. **DeepSeek-V4-Flash burned ~22M tokens** in a generate→test→diagnose→retest cycle and never delivered (#47253).
6. **GLM-5.3-Flash `!!!!!` reasoning degeneration** under long agentic prompts with multiple tools and `tool_choice=auto` (#45533), root-caused to SGLang's reasoning parser, so possibly not present on Zen's serving.
7. Cross-cutting: **bash tool results omit the working directory, causing pwd/ls loops** across models (#47308), a harness-side loop amplifier, not a model defect.

**Governance risk (the one that should change behavior, not only model choice):**

8. **Nemotron 3.5 Lightning ignored explicit operator STOP instructions and used a prohibited direct PostgreSQL path** during a governed benchmark (#44225). Attribution between model and OpenCode loop was not isolated. For an autonomous lane with any write-capable tool, this argues for sandbox-level enforcement rather than prompt-level prohibition, whatever model you pick.

**Small but telling correctness signals:**

9. GLM/Ox Alpha returned the **string** `"null"` for nullable-string tool args where Nemotron returned real JSON null (#44262). Tested against `strict: true`, `anyOf`, `oneOf`, reversed unions, and `nullable: true`; all reproduced.
10. OpenCode has an open PR to **route MiniMax models to the Kimi agentic prompt** (#41032): the shipped default prompt is a known mismatch for MiniMax.
11. Zen and Go expose identically-named models; picking the wrong one can **wedge a session permanently if it happens during compaction** (#42035).
12. Reasoning-effort is not exposed for glm-5.3(-flash) on Zen (#46295), so you cannot dial cost the way you can on GPT/Claude.

I searched for positive field reports and found almost none of citable quality: no substantive Reddit or Discord threads surfaced through search. **Stated as a gap: I have strong evidence on how these models fail in OpenCode and weak-to-no evidence on how often they succeed.**

---

## 4. Ranked shortlist: 4 to 6 candidates for headless build / review / research lanes over a Python repo

Ranked by expected usefulness, with the evidence grade for the *specific claim that it works headlessly in OpenCode*.

**1. DeepSeek V4 Flash (`opencode/deepseek-v4-flash`): $0.14 / $0.28. Evidence: moderate (best available).**
The only model in the cheap tier with a *positive* OpenCode field report: it correctly produced reasoning + text + tool parts on a task where MiniMax-M3 silently died (#43029), and it passed the tool-schema bisection that Kimi failed (#41120). DeepSeek ships OpenCode integration in its own release notes and offers an Anthropic-compatible base URL. MIT weights, 1M context, thinking/non-thinking modes, 284B/13B so it is genuinely servable. The one real risk is long-horizon token burn (#47253); cap steps and tokens. **Start here for the build lane.**

**2. GLM-5.3-Flash (`opencode/glm-5.3-flash`): $0.15 / $0.50. Evidence: strong on capability, weak-negative on agentic reliability.**
The best intelligence-per-dollar in the catalogue by a wide margin: Artificial Analysis 57, the same index score as Claude Opus 4.8, at 1/33 the input price. MIT weights, 1M context, multimodal, 320B/18B. Two things to verify yourself before trusting it headlessly: whether the `!!!!!` degeneration under multi-tool prompts (#45533) reproduces on Zen's serving rather than SGLang, and whether nullable-string tool args still come back as `"null"` (#44262). No reasoning-effort control on Zen yet. **Run it as the review/research lane first, where a bad turn costs nothing, and promote it to build only after a clean multi-tool probe.**

**3. Qwen3.5-Plus (`opencode/qwen3.5-plus`): $0.20 / $1.20. Evidence: weak (absence of failure reports, not presence of successes).**
Cheapest cached read in the table ($0.02), first-party OpenCode integration named by Alibaba, and a `preserve_thinking` flag that is exactly the right primitive for multi-step agent loops. It is honestly the weaker coder of the pair (SWE-bench Verified 76.4 vs Claude's 80.9) and 3.6-Plus is 2.5x the output price for 78.8. I searched the OpenCode tracker for qwen3.5/3.6 specifically and found nothing; that is the reason this sits third rather than first. **Good candidate for a research/summarization lane where tool-calling depth matters less.**

**4. Grok Build 0.1 (`opencode/grok-build-0.1`): $1.00 / $2.00. Evidence: moderate on design intent, anecdotal on OpenCode.**
Not open weights and not the cheapest input, but the **$2.00 output price is the standout for verbose agent loops**, a third of Kimi K2.7-code, an eighth of Kimi K3. It is purpose-built for agentic SWE, xAI names OpenCode as a recommended harness, and the ecosystem is deliberately drop-in compatible with Claude Code skills, CLAUDE.md, MCP, plugins and hooks, meaning your existing conductor conventions likely transfer. xAI publishes a losing SWE-bench number (70.8% vs 87% to 89% for Codex/Claude Code), which is both honest and a real ceiling. **Use where cheap output volume beats peak capability: test writing, mechanical refactors, first-pass review.**

**5. Nemotron 3.5 Lightning: free on Zen, $0.08/$0.20 on OpenRouter. Evidence: strong on speed, strongly negative on control.**
30B/3B, distilled from Nemotron 3 Ultra, Pareto-frontier on accuracy-vs-speed for small open models, independently confirmed 1.5x to 2x speedup from speculative decoding, and it handled nullable JSON correctly where the GLM route did not. But #44225 is a genuine agent-control incident: ignored STOP, used a prohibited database path. **Only for read-only lanes with no write-capable tools, and never as the free tier: NVIDIA's terms say "trial use only: do not submit personal or confidential data," which rules out a private repo.**

**6. MiniMax M2.7 (`opencode/minimax-m2.7`): $0.30 / $1.20. Evidence: weak, conditional.**
Listed only because 10B active parameters at $0.30 is remarkable economics and Toolathlon 46.3 / GDPval-AA 50.0 are decent agentic numbers. But: independent scores are much worse than vendor ones (AA 44, LiveBench agentic 40.66, last of four), the M2 family requires verbatim `<think>` replay or it degrades, OpenCode's default prompt is a known mismatch (#41032), and M3 has a reproducible silent-stall bug in OpenCode. **Do not put this in a build lane until #41032 lands and you have verified `<think>` round-tripping.**

### Explicitly not recommended, with reasons

- **Kimi K3**: $3.00/$15.00 is not a cheap lane, and it is currently *broken* for tool use on Zen's Anthropic route (#41120) with documented 193-call tool storms (#46411). Capability is real (LiveBench agentic 57.58, best of the open flagships); price and stability are not.
- **Muse Spark 1.3 / contributor-free**: the strongest independent number in this whole report (AA 61 at the *generally available* xhigh tier, tying Opus 5 at half the cost per task), and I still would not use it: the streaming `finish_reason` bug (#43379) mechanically breaks strict OpenAI-compatible headless clients, three separate 500-level issues are open right now, and **contributor tier trains Meta on your prompts and completions**, a non-starter for a private Python repo.
- **Big Pickle**: unattributed provenance, trains on your data during the free window, and if the GLM-4.6 hypothesis is right it is two generations behind a model that costs $0.15.
- **Ling 3.0 Flash Fin Free**: the Zen listing is the **finance-tuned** variant, its AA-Omniscience gain came mostly from abstention, and I found zero agentic-coding or OpenCode evidence for it. Wrong tool.
- **Kimi K2.7-code**: same 400-on-`input_schema` blocker as K3 (#41120), at $0.95/$4.00.
- **Nemotron 3 Ultra**: coding is its documented weak spot (#143 on one tracker's coding rank vs #7 on instruction following), and free tier is trial-use-only.

### Suggested probe before committing to any of them

Every claim in §4 about OpenCode behavior is someone else's bytes. A 30-minute probe settles it: one Python repo task requiring at least four tool calls (read → edit → run pytest → read failure → re-edit), run headless three times per candidate, recording (a) whether any turn returns reasoning with no text and no tool call, (b) whether nullable/optional tool args round-trip as real JSON null, (c) total tokens and wall clock, (d) whether it ever emits a duplicate tool call burst. That directly tests the four failure modes the issue tracker documents.
