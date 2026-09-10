# Frontier models for headless coding-agent lanes — strengths, weaknesses, vendor prompting

Research date: 2026-09-04. Every claim below carries the URL it came from. Where a claim is vendor marketing rather than an independent measurement, it is labelled **[vendor claim]**. Where no 2026 primary source could be found, the gap is stated explicitly rather than filled with a guess.

Scope: these models as run *headlessly* inside coding-agent CLIs over a git repo (build from spec / cold review / targeted fix / test writing / research-reading / judging another model's output).

---

## 1. Anthropic Claude 5 family

Model IDs, context, effort defaults and price, all from the Anthropic models overview page (https://platform.claude.com/docs/en/models/overview) and the pricing page (https://platform.claude.com/docs/en/about-claude/pricing):

| Model | API ID | Context | Max output | Thinking | Default effort | Input $/MTok | Output $/MTok | Cache read $/MTok |
|---|---|---|---|---|---|---|---|---|
| Claude Fable 5.1 | `claude-fable-5-1` | 1M | 128K | Adaptive, always on | `high` | $10 | $50 | $0.25 (0.025x) |
| Claude Opus 5 | `claude-opus-5` | 1M | 128K | Adaptive (default on) | `high` | $5 | $25 | $0.50 |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | 128K | Adaptive (default on) | `high` | $2 | $10 | $0.20 |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | 200K | 64K | Extended (manual) | not supported | $1 | $5 | $0.10 |

Notes on the table: Sonnet 5's $2/$10 was introductory pricing through 2026-08-31 and is now the standard price; the scheduled rise to $3/$15 on 2026-09-01 did **not** happen (https://platform.claude.com/docs/en/about-claude/pricing). Batch API is 50% off. Claude 4.7-and-later models use a new tokenizer producing roughly 30% more tokens for the same text, so `max_tokens` budgets tuned on older models will truncate (https://platform.claude.com/docs/en/about-claude/pricing, https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5).

### 1a. Claude Opus 5 (`claude-opus-5`)

**Best at.** Anthropic states Opus 5 is strongest on difficult coding: multi-file features, larger refactors, end-to-end feature work; it completes full tasks rather than leaving stubs or placeholders, and "performs best when given the complete task specification up front and left to run" **[vendor claim]** (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5). It is also explicitly called out for **code review and bug-finding**: "finds real bugs at a high rate per pass, and its additional findings are mostly real issues rather than false positives," with accuracy holding at lower effort settings, which supports a cheap fast pass plus a thorough pass later **[vendor claim]** (same URL). Multi-agent coordination is called out as good, with effective writer-verifier patterns and few agents overwriting each other's work (same URL). Instruction following, tool calling and reasoning are claimed consistent across the full 1M window (same URL).

**Worst at / failure modes for unattended runs**, all from the same Opus 5 page:
- **Over-verification.** It verifies its own work unprompted. Explicit "include a final verification step" / "use a subagent to verify" instructions cause *over*-verification; removing them "reduces wasted tokens with no loss in quality."
- **Scope expansion.** It adds steps that were not requested and applies its own judgment about what the task should be.
- **Subagent over-delegation.** It delegates more readily than prior models, multiplying cost and time on small tasks.
- **Verbosity.** Default user-facing responses run longer than prior Opus models. Effort controls *thinking*, not visible response length; lowering effort does not reliably shorten the reply.
- **Long written deliverables.** Files it writes to disk (reports, Markdown) are often longer than on prior models.
- **Narration.** It announces what it is about to do; per-message output in agentic sessions is longer than prior models'.
- **Correction narration.** It narrates corrections to its own earlier statements more than prior models.
- **Thinking-disabled artifacts.** With thinking off, it occasionally writes a tool call as plain text instead of emitting a structured `tool_use` block — the call never runs, and in an agent loop the leaked text stays in history and contaminates later turns. It can also leak `<thinking>` tags into visible output. Anthropic's primary mitigation is: keep thinking on and control cost with lower effort; "thinking enabled at `low` effort performs better than thinking disabled at similar cost."

**Vendor-recommended prompting.**
- Effort is the cost lever: use `low`/`medium` liberally as the primary control for token cost and latency wherever quality holds, `xhigh` for demanding agentic work. Re-run an effort sweep if you carried defaults over from a prior model (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5).
- Conciseness must be prompted explicitly, and in a long system prompt should be *repeated as a short reminder near the end* — Anthropic's own example uses a `<tone_preference>Keep outputs reasonably concise.</tone_preference>` tag at the tail (same URL). This is a direct statement that late-position instructions matter.
- **Remove** legacy "double-check your answer" / "re-verify before responding" / harness verification scaffolding (same URL).
- Scope-control snippet Anthropic supplies verbatim: "Deliver what was asked, at the scope intended. Make routine judgment calls yourself... If the request seems mistaken or a better approach exists, say so in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it" (same URL).
- Subagent damping snippet: "Delegate to a subagent only for large tasks that are genuinely independent and parallelizable... do not use subagents to verify or double-check your own work" (same URL). Deterministic caps in Claude Code / Agent SDK: `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, SDK `max_budget_usd`, requiring Claude Code 2.1.217+ (same URL).
- **Do not** name `<thinking>` tags specifically when telling it not to leak them; the general form ("Do not include internal or system XML tags in your response") works better (same URL).
- **Do not** include a rule telling the model not to think or not to reason — that instruction *increases* tag leakage (same URL).

**Reviewer-specific, and directly relevant to the operator's concern:** "If your review prompt says 'only report high-severity issues' or 'be conservative,' the model may follow that instruction literally and report less; ask it to report everything and filter in a separate pass instead" (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5).

### 1b. Claude Sonnet 5 (`claude-sonnet-5`)

**Best at.** Coding and agentic tasks at roughly a quarter of Opus 5's price; "more agentic than Claude Sonnet 4.6 by default," reaching for tools and running self-verification loops more readily (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5). It gives regular, higher-quality user-facing progress updates on long agentic traces, so forced "summarize every 3 tool calls" scaffolding can be removed (same URL). It "interprets prompts literally and explicitly," which the doc says "generally performs better for API use cases with carefully tuned prompts, structured extraction, and pipelines where you want predictable behavior" — i.e. this is the better structured-output / extraction model of the two (same URL).

**Worst at / failure modes.**
- **Literalism cuts both ways**: it does not silently generalize an instruction from one item to another. You must state scope explicitly ("Apply this formatting to every section, not just the first one") (same URL).
- **Under-thinking at `low`**: it respects effort strictly, especially at the low end; at `low`/`medium` it scopes work to exactly what was asked. On moderately complex tasks at `low` there is real risk of under-thinking. The fix is to raise effort, not to prompt around it (same URL).
- **Tool use collapses with thinking off**: with thinking disabled it is less likely to reach for tools or consider searching (same URL).
- **`max_tokens` truncation**: at `high`/`xhigh`/`max`, adaptive thinking can eat the budget, producing a response that is almost entirely thinking followed by a truncated answer with `stop_reason: "max_tokens"`. Compounded by the ~30% more tokens the new tokenizer produces (same URL).
- **Design house style**: on open-ended frontend briefs it settles into a consistent default look; generic nudges ("make it cleaner") just move it to a different fixed palette (same URL).

**Vendor-recommended prompting.**
- Effort ladder, verbatim: `max` (absolute maximum), `xhigh` (recommended for hardest coding/agentic), `high` (default), `medium` (cost-sensitive), `low` (short scoped, latency-sensitive, not intelligence-sensitive) (same URL). Cross-model mapping: Sonnet 5 at `medium` ≈ Sonnet 4.6 at `high`; Sonnet 5 at `high` ≈ Sonnet 4.6 at `max`.
- Manual extended thinking (`budget_tokens`) returns **400**. `temperature`, `top_p`, `top_k` at non-default values return **400** — new for Sonnet-class. Use system-prompt instructions for tone/variety instead (same URL).
- Positive examples of the style you want beat negative "don't do X" instructions (same URL).
- For interactive coding products: use `xhigh` or `high`, add an auto mode, and reduce human turns; specify task, intent and constraints fully in the *first* human turn. "Ambiguous or underspecified prompts conveyed progressively over multiple user turns tend to relatively reduce token efficiency and sometimes performance" (same URL).

**Code-review harness guidance (the single most decision-relevant passage found in this whole research pass).** Anthropic says a harness tuned for an earlier model may show *lower recall* on Sonnet 5, and that this is a harness effect, not a capability regression: told "only report high-severity issues" or "be conservative" or "don't nitpick," Sonnet 5 follows more faithfully — it investigates just as deeply, finds the bugs, then declines to report findings below your stated bar. Precision rises, measured recall falls (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5). Their recommended language, verbatim:

> "Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage - a separate verification step will do that. Your goal here is coverage: it is better to surface a finding that later gets filtered out than to silently drop a real bug. For each finding, include your confidence level and an estimated severity so a downstream filter can rank them."

And: if you *do* want single-pass self-filtering, "be concrete about where the bar is rather than using qualitative terms like 'important': for example, 'report any bugs that could cause incorrect behavior, a test failure, or a misleading result; only omit nits like pure style or naming preferences.'"

### 1c. Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)

Positioned as "the fastest model with near-frontier intelligence." 200K context (not 1M), 64K max output, **extended** thinking rather than adaptive, and the `effort` parameter is **not supported** (https://platform.claude.com/docs/en/models/overview). Reliable knowledge cutoff Feb 2025, training cutoff Jul 2025 — the oldest in the family by a wide margin, which matters for any lane that reasons about current tooling. Retirement "not sooner than October 15, 2026," the nearest of the four (same URL). It does have context awareness (tracking its own remaining token budget) alongside Sonnet 5 / 4.6 / 4.5 (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices). Anthropic publishes **no** model-specific prompting page for Haiku 4.5; the model-specific guidance table lists pages only for Fable 5.1/5, Sonnet 5, Opus 5 and Opus 4.8 (same URL). No 2026 Anthropic source was found stating Haiku 4.5's coding-agent strengths or weaknesses in benchmark terms.

### 1d. Claude Fable 5.1 (`claude-fable-5-1`) — brief

$10/$50 per MTok, 2x Opus 5, with an unusually cheap cache read at 0.025x base ($0.25/MTok) rather than the standard 0.1x (https://platform.claude.com/docs/en/about-claude/pricing). Positioned "for demanding reasoning and long-horizon agentic work, or when your evals on Claude Opus 5 at higher effort still fall short" (https://platform.claude.com/docs/en/models/overview). Thinking is always on; adaptive is the only mode.

Behaviors that matter for unattended runs, all from https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1:
- **It goes quiet.** It writes fewer user-facing updates during long tool-calling turns than Fable 5, worse at higher effort and in longer tool chains. Progress notes arrive as progress-update `thinking` blocks which are *empty* under the default `thinking.display: "omitted"` — you must set `display: "updates"` (beta header `thinking-display-updates-2026-08-18`) to receive them at all.
- **It stops early.** "Sometimes describes what it would do next instead of doing it ('Next, I'll …') or stops to ask permission for a step the original request already covered." Anthropic's fix is the autonomous-operation system prompt (the same text the operator's own harness already carries).
- **Serialized tool calls in coding loops.** In custom coding agents and bash-and-editor harnesses it may issue implied independent calls one per turn rather than batching. Fix: "First privately list what you need next; then request every item that doesn't depend on another's result in this one response," appended each turn.
- **Whole-file rewrites** for small changes, costing output tokens. Fix: instruct surgical edits.
- **Scope and test creep**: it may fix nearby code and commit more test files than the change warrants. Anthropic supplies a long verbatim damping instruction ("...report it as a follow-up in your summary... Commit tests only where the task asks for them...").
- **Search skipping at `low` effort**: more likely to answer from memory than call retrieval.
- **Safety classifier refusals**: it can return `stop_reason: "refusal"`. Finding vulnerabilities in source is permitted, but compile-check phrasing ("Does this program compile without errors?") raises false positives; "Are there any bugs in this program?" is the recommended phrasing. Base64 in tool output also triggers them.
- **`bound to a different conversation` errors** if the harness edits earlier turns; history must be append-only.
- Effort: at `medium` it roughly matches Fable 5 at lower cost; at `low` it is "often competitive with Claude Opus and Claude Sonnet models on cost per task while scoring higher."

### 1e. Cross-family prompting rules (all current Claude models)

From https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices:

- **Structure with XML tags.** `<instructions>`, `<context>`, `<input>`; nest for hierarchy; consistent descriptive tag names. Wrap examples in `<example>` / `<examples>`. 3–5 examples is the recommended count.
- **Role in the system prompt.** Even one sentence changes behavior.
- **Long context: data first, query last.** "Place your long documents and inputs near the top of your prompt, above your query, instructions, and examples." Anthropic states "Queries at the end can improve response quality by up to 30 percent in tests, especially with complex, multidocument inputs" **[vendor claim, no methodology published]**. Note this is the *opposite* placement rule from short prompts, where critical constraints go first. Wrap documents in `<document>` / `<document_content>` / `<source>`.
- **Ground long-document work in quotes.** Ask the model to pull relevant quotes into `<quotes>` tags before doing the task.
- **Explain *why*.** "NEVER use ellipses" is weaker than "...will be read aloud by a text-to-speech engine, so never use ellipses since the engine will not know how to pronounce them."
- **Tell it what to do, not what not to do.**
- **Over-firm / over-eager instructions backfire.** "Remove over-prompting. Tools that undertriggered in previous models are likely to trigger appropriately now. Instructions like 'If in doubt, use [tool]' will cause overtriggering." Replace "Default to using [tool]" with "Use [tool] when it would enhance your understanding of the problem."
- **Overeagerness / overengineering damping snippet** is published verbatim (scope, documentation, defensive coding, abstractions).
- **Anti-test-gaming snippet** is published verbatim: "Do not hard-code values or create solutions that only work for specific test inputs... If the task is unreasonable or infeasible, or if any of the tests are incorrect, please inform me rather than working around them." This is the direct countermeasure to a build lane editing tests to make them pass.
- **Anti-hallucination snippet** for agentic coding, verbatim: "`<investigate_before_answering>` Never speculate about code you have not opened. If the user references a specific file, you MUST read the file before answering..."
- **Destructive-action confirmation snippet** is published verbatim (deleting files/branches, dropping tables, `rm -rf`, `git push --force`, `git reset --hard`, amending published commits, plus "do not use destructive actions as a shortcut... don't bypass safety checks (e.g. `--no-verify`)").
- **Prefilled assistant responses are being migrated away from**; there is a dedicated section on it.
- **Chain prompts** only when you need to inspect intermediate output or enforce a pipeline; the canonical pattern is draft → review against criteria → refine, as separate API calls.

---

## 2. Google Gemini 3.8 Flash and 3.7 Flash (via Antigravity CLI `agy`)

### Specs and price

| Model | Context | Max output | Thinking levels | Default | Input $/MTok | Output $/MTok (incl. thinking) |
|---|---|---|---|---|---|---|
| `gemini-3.8-flash` | 1M | 64K | low, medium, high (**no `minimal`**) | medium | $0.75 → $1.50 on 2027-01-01 | $3.75 → $7.50 on 2027-01-01 |
| `gemini-3.7-flash` | (not stated on the pricing page) | — | low, medium, high | — | $0.75 → $1.50 on 2027-01-01 | $3.75 → $7.50 on 2027-01-01 |

Sources: https://ai.google.dev/gemini-api/docs/latest-model and https://ai.google.dev/gemini-api/docs/pricing. Both Flash tiers carry identical list prices; the differentiator is behavior, not cost per token. Batch and Flex are half price ($0.375/$1.875); Priority is 1.8x ($1.35/$6.75). Context caching $0.075/MTok plus $0.50 per 1M tokens per hour storage. Google Search grounding: 5,000 free requests/month shared across Gemini 3.x, then $14 per 1,000.

Positioning of 3.8 Flash: "our most intelligent Flash model, engineered for long-horizon software engineering, autonomous agents, and complex enterprise workflows"; strong on "complex multi-file refactoring and deterministic tool execution" and reduced failed loops **[vendor claim]** (https://ai.google.dev/gemini-api/docs/latest-model). 3.7 Flash is described on the pricing page as "our high-speed, efficient Flash model built for everyday coding, agentic tool use, and reliable multi-step execution" (https://ai.google.dev/gemini-api/docs/pricing).

### The 3.8-versus-3.7 tradeoff Google states in its own words

"Gemini 3.8 Flash can use more tokens on longer running and complex tasks, by design. To deliver higher-quality results on difficult, multi-step goals, the model takes smaller reasoning steps, calls tools iteratively, and verifies its work along the way. **Not every workflow needs this level of verification.** For everyday tasks, you can lower the reasoning effort to reduce token consumption. Alternatively, Gemini 3.7 Flash remains fully supported." (https://ai.google.dev/gemini-api/docs/latest-model)

That is the vendor telling you 3.8 costs more tokens for iterative self-verification and 3.7 is the right pick when you do not need it. Effort semantics on 3.8: `low` for latency-critical, `medium` (default) "best quality for most tasks; recommended for complex code and agentic use cases," `high` for "deep reasoning, mathematics, and difficult multi-step tasks" and maximum tool orchestration (same URL).

### Vendor-recommended prompt structure

From https://ai.google.dev/gemini-api/docs/prompting-strategies (Gemini 3 section) and https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5:

- **Be precise and direct. "Avoid unnecessary or overly persuasive language."** Verbose or complex prompt-engineering techniques designed for older models "may cause the model to over-analyze."
- **Delimiters: XML-style tags (`<context>`, `<task>`) or Markdown headings both work. Pick one and stay consistent inside a single prompt.** Google publishes both an XML template (`<role>` / `<constraints>` / `<context>` / `<task>`) and a Markdown template (`# Identity` / `# Constraints` / `# Output format`).
- **Instruction placement is split by kind.** Behavioral constraints, persona, and output-format requirements go in the **System Instruction or at the very beginning** of the user prompt. When supplying a large body of data (documents, code, long video), supply all the context **first** and put the specific instruction or question **at the very end**, then bridge with an anchoring phrase: "Based on the information above...".
- **Verbosity: Gemini 3 is terse by default.** If you want conversational or detailed output you must ask for it explicitly.
- **Do not use `temperature`, `top_p`, `top_k`.** "We strongly recommend not changing the default values. Gemini 3's reasoning capabilities are optimized for the default settings." For determinism, use a system instruction with explicit rules instead. `candidate_count` is unsupported in Gemini 3.x.
- **Do not use `thinking_budget`.** Use the `thinking_level` string enum. On 3.8 Flash, `minimal` errors out.
- **Do not force chain-of-thought.** "If you used chain-of-thought prompt engineering to force reasoning, try `thinking_level: 'medium'` or `'high'` with simpler prompts instead." And: "it's generally not necessary to have the model outline, plan, or detail reasoning steps in the returned response itself." For genuinely hard problems, a simple "Think very hard before answering" can help at the cost of thinking tokens.
- **Tool overuse has a two-step fix.** First lower the thinking level ("higher thinking levels encourage the model to use more tools to explore and verify"). If overuse persists, add a system instruction with an explicit budget: **"You have a limited action budget of `<n>` tool calls. Use them efficiently."**
- **Function-response hygiene** (real failure sources in agent loops): `id`, `name` and response count must match the preceding calls; multimodal content goes *inside* the function response, not alongside; inline instructions appended to function-response text separated by **two newlines**, not as separate parts. `Malformed_Function_Call` errors tie to pre-tool text.
- **Thought signatures.** From 3.5 Flash on, the model uses reasoning context from all previous turns when thought signatures are present; you must pass the full unmodified history. Thought preservation is on by default and "may increase token usage."
- **Grounding.** Google publishes a verbatim strictly-grounded-assistant system instruction for when the model must not use its own knowledge ("Treat the provided context as the absolute limit of truth... If the exact answer is not explicitly written in the context, you must state that the information is not available"). This is the closest thing in Google's docs to an explicit abstention instruction.
- **Date/cutoff anchoring.** Google recommends literally telling Flash models the year: "You MUST follow the provided current time (date and year) when formulating search queries in tool calls. Remember it is 2026 this year," and stating the knowledge cutoff.
- **Structured output:** JSON Schema on the response is supported; the prompting guide notes that simple JSON shapes can be specified in the prompt but complex ones should use a response JSON Schema. Structured outputs combine with built-in tools (Search, URL context, code execution, function calling).

### Antigravity CLI (`agy`) headless specifics

From https://antigravity.google/docs/cli/headless/ (CLI v1.1.25):

- `-p` / `--print` / `--prompt` runs once and exits. Response to **stdout**, all diagnostics to **stderr**.
- `--output-format text|json|stream-json`; `--json-schema` enforces a schema on print-mode JSON output (inline string or file path).
- **Effort is baked into the model slug as well as a flag.** `agy models` lists `gemini-3.8-flash-high`, `gemini-3.8-flash-medium`, `gemini-3.7-flash-high`, `gemini-3.7-flash-medium`, `gemini-3.6-flash-*`, `gemini-3.1-pro-high`, and `claude-sonnet-4-6`. `--effort low|medium|high` sets it separately. Note the published slug list exposes only **high and medium** for the Flash tiers.
- **Unknown model names fail loudly** in headless mode: exit non-zero with `status: "ERROR"`, no silent fallback. Good for pinned pipelines.
- **`status` is not the exit code.** A successful run exits 0, but a tool that needs approval it cannot obtain is **soft-denied: the run continues and still exits 0**, printing a notice to stderr. Check the `status` field (`SUCCESS`, `ERROR`, `CANCELED`, `INTERRUPTED`, ...), not just `$?`. This is a real false-green hazard for an unattended lane.
- File reads/writes inside the active workspace are auto-allowed; shell commands default to Ask and are soft-denied headlessly unless granted via `permissions.allow` rules like `command(git)`, `command(npm run (build|lint|test))`, `write_file(src/)` in `~/.gemini/antigravity-cli/settings.json`. `--dangerously-skip-permissions` approves everything.
- Headless uses cached credentials by default (authenticate interactively once). Per third-party reporting there is a CI path via `modelProvider: "gemini"` plus `GEMINI_API_KEY` from v1.1.13 — this was **not** confirmed in the official page text I retrieved (https://www.aibuilderclub.com/blog/antigravity-cli-guide).
- Sessions are stateless per run; `--continue`/`-c` or `--conversation <id>` resume; `--input-format stream-json` keeps one process across turns.

From https://antigravity.google/docs/cli/best-practices/: Google's own top recommendation is **"Establish verification loops"** — give the agent a local test/build/format command and instruct it to run it, and partition complex changes into explicit explore → plan → execute phases. Codebase rules live in `GEMINI.md` or `AGENTS.md` at the workspace root.

**Gap:** I found no official Google source quantifying Gemini 3.x long-context degradation, hallucinated-file-path rates, or a tendency to modify tests. Google's only stated long-context guidance is the instruction-placement rule above.

---

## 3. xAI Grok 4.6 (via `cursor-agent`)

### Specs and price

From https://docs.x.ai/developers/grok-4-6 (the "At a glance" table) and https://docs.x.ai/llms.txt (release notes):

| Property | Value |
|---|---|
| Model name | `grok-4.6` |
| Context window | 500,000 tokens |
| Knowledge cutoff | January 2026 |
| Output limit | none stated |
| Input price | $2.00 / 1M (rises to $4.00 above 200k prompt tokens) |
| Cached input | $0.50 / 1M (rises to $1.00 above 200k) |
| Output price | $6.00 / 1M (rises to $12.00 above 200k) |
| Reasoning effort | `low`, `medium`, `high` (default), `xhigh` — **cannot be disabled** |
| Modalities | text + image in, text out |

The **200k-token price cliff** is stated only in the release-notes entry, not the model page: "$2 / $0.50 / $6 per 1M tokens (input / cached input / output) below 200k prompt tokens, and $4 / $1 / $12 above" (https://docs.x.ai/llms.txt). For a long-context agent lane this doubles the marginal rate mid-run.

Positioning: "xAI's frontier model built for coding, agentic tasks, and knowledge work" **[vendor claim]** (https://docs.x.ai/developers/grok-4-6). Benchmark figures live in the launch post at https://x.ai/news/grok-4-6, which is vendor marketing; I did not retrieve it directly and am not quoting numbers from it.

### Effort semantics (verbatim from xAI's reasoning docs, via https://docs.x.ai/llms.txt)

| Setting | xAI's description | xAI's "best for" |
|---|---|---|
| `low` | some reasoning tokens, still fast | latency-sensitive agentic use, simple tool calling |
| `medium` | more thinking, less latency-sensitive | complex data analysis, long-context reasoning |
| `high` (default) | more reasoning tokens, deeper thinking | very challenging problems, complex math, multi-step logic |
| `xhigh` | maximum reasoning depth, higher latency | hardest problems, answer quality over response time |

`xhigh` is 4.6-and-later only; on 4.5 it silently degrades to `high`. **`presencePenalty`, `frequencyPenalty` and `stop` cannot be used with reasoning models and return an error.**

### Prompting guidance

**This is the weakest documentation of the five.** xAI publishes **no prompt-engineering guide for Grok 4.6 as a coding model.** The `docs.x.ai/docs/guides/prompt-engineering` path 404s. Searching the full `llms.txt` corpus, the only prompting guides xAI ships are for **speech-to-speech** and for the **multi-agent** research model. The speech-to-speech guide's structural advice is generic enough to be worth noting but is explicitly scoped to voice agents (https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/prompting-guide): second person, Markdown `##` sections in fixed order, short bullets over long paragraphs, guide with examples, **only mention tools that actually exist in the tool definition** ("the model follows instructions closely, so a mismatch produces bad responses"), capitalize key rules (`ALWAYS`/`NEVER`/`EVERY`) for emphasis, convert symbolic logic to plain English, and put non-negotiable overrides in an appended `## CRITICAL INSTRUCTIONS` section after the standard sections.

Note that the "capitalize ALWAYS/NEVER" advice is the **direct opposite** of Anthropic's "avoid over-firm language" guidance and of Google's "avoid overly persuasive language." A shared prompt across vendors cannot satisfy both.

What xAI *does* document for agentic runs (https://docs.x.ai/developers/grok-4-6, https://docs.x.ai/developers/advanced-api-usage/prompt-caching/best-practices):
- **Set `prompt_cache_key` (Responses API) or the `x-grok-conv-id` header (Chat Completions).** "It routes a conversation's requests to the same server, making cache hits reliable; without it you often pay full input price on a cache-cold server." Use a stable UUID. **Never modify earlier messages — only append**; any edit, removal or reorder breaks the cache. Front-load static content. Monitor `cached_tokens`; consistently 0 means your ID or ordering is wrong. Cache entries can be evicted at any time, so the application must work without caching.
- **Long agent loops benefit from context compaction** (`/developers/advanced-api-usage/context-compaction`).
- Structured Outputs are a first-class documented feature (https://docs.x.ai/llms.txt, "Structured Outputs" and "Structured Outputs with Tools" sections).
- Grok reads `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, `CLAUDE.local.md` and `.grok/rules/*.md` (plus `.claude/rules/` and `.cursor/rules/` for compatibility). "Files are loaded in full, with no size cap; **short, specific instructions are followed more reliably than long ones**" (https://docs.x.ai/build/features/project-rules). Per-run overrides: `--rules "<text>"` appends to the system prompt, `--system-prompt-override` replaces it.
- Generic deployment advice on the Vertex integration page: "Use clear tool schemas and explicit output formats" (https://docs.x.ai/llms.txt).

**Gaps.** No xAI source found for: Grok 4.6 over-editing, stopping early, narrating instead of acting, hallucinating file paths, modifying tests, prompt-position sensitivity, or long-context degradation across the 500k window. Third-party guidance exists (e.g. https://venice.ai/blog/grok-4-6-prompt-tips: name role, goal and numbered done-criteria, because "if you only name a vibe, the model invents scope") but it is a reseller blog, not a vendor source, and should be treated as anecdote.

---

## 4. Cursor Composer 2.5 (`cursor-agent`)

### What it is

Cursor's own agentic model, built on Composer 2 with "stronger intelligence on long agentic tasks, better effort calibration, tool selection, intent understanding, and reliability" **[vendor claim]** (https://cursor.com/docs/models/cursor-composer-2-5). Stated strengths, verbatim from the same page: strong on long-horizon tasks via reinforcement learning on long-horizon coding tasks; default fast variant for interactive sessions with a standard tier optimized for cost per token; **"tuned for tool use, file edits, and terminal operations inside Cursor."**

That last clause is the key architectural fact: Composer 2.5 was RL-trained against a simulated agentic harness whose tools match the Cursor CLI. It is a harness-coupled model, not a general one. It is exclusive to the Cursor IDE, Cursor CLI and Cursor web — **there is no public API endpoint and no third-party hosting** (https://cursor.com/docs/models/cursor-composer-2-5, corroborated by https://www.thesys.dev/blogs/cursor-composer-2-5).

### Price

$3/M input and $15/M output for the faster variant, which "is the default in the product and is priced lower than other fast models at similar speeds" (https://cursor.com/docs/models/cursor-composer-2-5). On individual and team plans it draws from the Cursor Models pool alongside Cursor Grok 4.6 and Grok 4.5; on-demand usage is charged at those rates. The docs page renders its full price table client-side and I could not extract the standard-tier row; treat $3/$15 as the fast-variant figure only.

### Architecture and benchmarks — secondary sources only

Cursor's own blog post at `cursor.com/blog/composer-2-5` 404s to the marketing shell, so the following comes from secondary reporting and should be treated accordingly. Reported: a mixture-of-experts transformer, 1.04T parameters with 32B active per token, up to 200,000 tokens of context, based on Moonshot's open-weights Kimi K2.5, further pretrained on code then RL'd in a harness matching Cursor CLI, with "targeted textual feedback" (inserting a hint at the exact point the model could have done better) and a 25x larger synthetic task set. Reported placement: third on the Artificial Analysis Coding Agent Index, first on SWE-Bench-Pro-Hard-AA (https://www.thesys.dev/blogs/cursor-composer-2-5, https://www.deeplearning.ai/the-batch/cursor-fits-its-model-to-its-agent). **The 200K context figure conflicts with nothing in Cursor's own docs but is not confirmed by them.**

### Prompting

Cursor's agent prompting page (https://cursor.com/docs/agent/prompting.md) is a UI-features page — `@` mentions, Custom Modes, image and voice input, the context ring, model switching. It contains **no model-specific prompt-structure guidance** for Composer 2.5: no system-versus-user placement rule, no XML-versus-Markdown recommendation, no verbosity control, no effort parameter. One operationally useful line: "Use @ mentions when you know which files are relevant. If you're not sure which files matter, skip it — Agent finds relevant files through its own search."

Custom Modes are the closest thing to a persistent reviewer persona: a skill attached as a Mode "stays in context on every turn, even as the agent works for hours, until you exit the mode," and Cursor's own example is "Keep a code-review checklist active while you move through several files." Custom Modes work in the Agents Window and the CLI. Context management is automatic: "When the window gets close to full, Cursor compresses older parts of the conversation into a summary."

**Gaps.** No vendor source found for Composer 2.5 on: over-editing, stopping early, hallucinating file paths, modifying tests, prompt-position sensitivity, long-context degradation, or JSON-schema structured output support. The model has no public API, so schema-constrained output is only available to the extent the Cursor CLI exposes it. **Do not assume it does.** Cursor's docs also list Grok 4.6, Grok 4.5, Claude Sonnet 5, Claude Opus 5, Claude Fable 5.1, Gemini 3.1 Pro, Gemini 3.8 Flash, GPT-5.6 Sol/Terra/Luna as selectable in the same harness (https://cursor.com/docs/llms.txt), so `cursor-agent` is a multiplexer, not a Composer-only path.

---

## 5. OpenAI GPT-5.6 Sol / Terra / Luna — **PAUSED BY OPERATOR POLICY. RECORD ONLY.**

**Status: not for use.** Standing operator policy is no headless Codex/OpenAI until explicitly approved. Documented here for the record only; nothing in this section is a recommendation to route work.

### Naming, price, specs

The `gpt-5.6` alias routes to `gpt-5.6-sol` (flagship); `gpt-5.6-terra` for strong performance at lower price; `gpt-5.6-luna` for efficient high-volume work (https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6).

Standard-tier API pricing per 1M tokens, from https://developers.openai.com/api/docs/pricing. Note the **short-context / long-context split** — the long-context column applies once a request crosses the threshold:

| Model | Input (short) | Cached in | Cache write | Output (short) | Input (long) | Output (long) |
|---|---|---|---|---|---|---|
| `gpt-5.6-sol` | $4.00 | $0.40 | $5.00 | $20.00 | $8.00 | $30.00 |
| `gpt-5.6-terra` | $2.00 | $0.20 | $2.50 | $12.00 | $4.00 | $18.00 |
| `gpt-5.6-luna` | $0.20 | $0.02 | $0.25 | $1.20 | $0.40 | $1.80 |
| (`gpt-6-astra`, successor) | $10.00 | $1.00 | $12.50 | $50.00 | $20.00 | $75.00 |

Batch is half. "GPT-5.6 Sol's promotional pricing is available at least through November 21, 2026." Priority processing was renamed Fast mode on 2026-07-30; both `service_tier: "priority"` and `"fast"` work. **Cache writes cost 1.25x the uncached input rate**, so naive caching can cost more than it saves; track `cached_tokens` and `cache_write_tokens`.

**GPT-5.6 has been superseded by GPT-6 Astra**, which is now the "Using..." headline page in OpenAI's docs (https://developers.openai.com/api/docs/guides/latest-model). That is a further reason to treat this section as historical.

### Reasoning and modes

`reasoning.effort` supports `none`, `low`, `medium`, `high`, `xhigh`, `max`; default `medium` in both standard and pro modes. `reasoning.mode: "pro"` applies more model work for a single final answer, billed at standard token rates, independent of effort. Persisted reasoning defaults to `all_turns` (earlier models defaulted to `current_turn`). Explicit prompt-cache breakpoints via `prompt_cache_options.mode: "explicit"` and `prompt_cache_options.ttl`. Programmatic Tool Calling lets the model write JavaScript to call eligible tools in a hosted runtime — aimed at "bounded, tool-heavy workflows that do not require fresh model judgment between each step." Multi-agent is a Responses API beta. (All: https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6)

### Prompting guidance — the "subtract, don't add" result

OpenAI's headline prompting finding, verbatim: "Removing repeated instructions and examples and simplifying tool descriptions can improve task performance and token efficiency. In a sample of internal coding-agent eval runs, configurations with leaner system prompts improved evaluation scores by roughly **10–15%** while reducing total tokens by **41–66%** and cost by **33–67%**." OpenAI itself labels these "directional" and says to validate on your own tasks **[vendor claim, internal evals, no methodology published]** (same URL).

Their method for lean prompts: start from a working prompt and tool set, remove one group of instructions/examples/tools at a time and rerun the same evals; **state each instruction once**; expose only relevant tools with concise descriptions; keep examples that encode a product requirement or correct a measured gap; track context both at run start and as the conversation grows.

Other stated rules (same URL):
- **Autonomy policy, not repetition.** "Repeating instructions such as 'ask first,' 'do not mutate,' or 'wait for approval' can cause unnecessary approval requests for safe, expected actions." Name safe local actions explicitly. Their compact policy example distinguishes answer/explain/review/diagnose/plan requests (inspect and report, do not implement) from change/build/fix requests (make in-scope local changes and run non-destructive validation without asking), requiring confirmation only for external writes, destructive actions, purchases, or material scope expansion.
- **Verbosity: 5.6 is already more concise than 5.5**, so inherited "Be concise" instructions can over-correct into answers that are too short. Use the `text.verbosity` parameter (`low`/`medium`/`high`) for the global default and the prompt for task-specific requirements. When you want brevity, specify *what a short answer must preserve* rather than just asking for short.
- **Effort: do not assume higher is better.** When migrating, preserve the current effort as baseline, then compare one level *lower*.
- **Pro mode: don't prompt for it.** "You do not need to ask the model to 'use pro mode,' 'think harder,' or generate several candidate answers."
- **Intent understanding**: it infers the underlying goal, "so you often do not need to prescribe every step. Continue to provide domain context, hard constraints, approval boundaries, and success criteria. Tell the model when an important ambiguity should trigger a question."
- **Safeguard latency and refusals**: real-time cyber and biology misuse classifiers run on outputs; generation can pause for several seconds mid-stream, and "safeguards may occasionally intervene on legitimate work, particularly in dual-use areas where defensive and offensive activity can initially look similar." Directly relevant to any security-review lane.

Third-party reporting adds that OpenAI advises against steering with absolutes like "always"/"never," on the grounds that where an older model would pick one instruction on a conflict, 5.6 burns reasoning tokens trying to reconcile both (https://decrypt.co/373439/openai-new-gpt-5-6-prompt-guide-chatgpt). Treat as secondary.

---

## 6. Reviewer prompting: does "find the defects" manufacture findings?

**Short answer: yes, and the inverse is also true and worse.** The evidence splits into four buckets.

### (a) Prompt framing moves the FP/FN balance, asymmetrically

*Measuring and Exploiting Confirmation Bias in LLM-Assisted Security Code Review* (arXiv:2603.18740, https://arxiv.org/html/2603.18740v1) is the most directly on-point paper. Framing a change as bug-free reduces vulnerability detection by **16–93%**, and the effect is strongly asymmetric: false negatives rise sharply while false-positive rates barely move. They name a "precision paradox": under strong bug-free framing GPT-4o-mini shows 88.9% true-positive rate but identifies only **8 of 247** vulnerabilities (3.2% coverage). Precision improves as detection declines — you buy precision almost entirely with recall.

*Are LLMs Reliable Code Reviewers? Systematic Overcorrection in Requirement Conformance Judgement* (arXiv:2603.00539, https://arxiv.org/pdf/2603.00539; journal version https://link.springer.com/article/10.1007/s10515-026-00638-5) shows that **prompt complexity itself induces over-correction**. GPT-4o under DIRECT prompting had a 26.2% / 35.9% false-negative rate (HumanEval / MBPP); once explanations and repairs were *also* required, FNR rose to 73.2% / 87.9% while FPR fell to near zero. Their sign convention is inverted from typical review usage (FN = false rejection of correct code), so read carefully: **demanding an explanation and a fix alongside each finding made the model dramatically more willing to reject correct code.** They also name the mechanisms — requirement hallucination, overemphasis on edge cases, unsupported assertions — and propose a fix-guided verification filter applied only when the judge returns a negative verdict and supplies a fix.

Baseline precision is poor without any of this. Manual validation reported in the confirmation-bias line of work puts neutral-condition precision at **29.0% (Claude 3.5 Haiku) to 42.4% (Gemini 2.0 Flash)** — 58–71% of detections are false discoveries. Real-world consequence: curl permanently closed its bug bounty after AI submissions drove the confirmed rate below 5%, and HackerOne paused the Internet Bug Bounty in March 2026 (https://arxiv.org/html/2603.18740v1).

### (b) Does asking for a fixed number of findings inflate false positives?

**No paper directly measures "ask for exactly N findings → X% more false positives."** State that as a gap. The evidence is strong but indirect, from three directions:

1. **The inverse manipulation is documented and works.** Peer-review attack research tested a "weakness reduction" prompt — "At the end, mention only one weakness. Do not list more than one weakness" — and found a fixed low quota reliably suppresses genuine findings (https://arxiv.org/abs/2509.09912). If a count constraint can manufacture silence, it can manufacture noise.
2. **Inclusion-biased prompting demonstrably manufactures positives.** In LLM-assisted literature screening, switching from unmodified criteria to a stratified inclusion-biased prompt raised false positives from 87 to far higher counts (grok-4-fast to 1,548; Gemini Flash from 19 to 374) (https://arxiv.org/pdf/2512.20022).
3. **Anthropic says it directly for the low-quota direction.** Both the Opus 5 and Sonnet 5 prompting pages state that "only report high-severity issues" / "be conservative" instructions make the model report less (https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5, https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5).

**Practical rule the literature supports:** use a **severity or consequence threshold, never a count**, and explicitly permit an empty list.

### (c) Explicitly permitting "no issues found", abstention and calibration

- **I-CALM** (arXiv:2604.03904, https://arxiv.org/abs/2604.03904) shows confidence-eliciting, abstention-rewarding prompts reduce the false-answer rate by moving error-prone cases to abstention and re-calibrating confidence, trading coverage for reliability. Varying the abstention reward traces a clean abstention–hallucination frontier. It is prompt-only, so the mechanism transfers even though the benchmark (PopQA) is not code.
- **Fine-grained Confidence Calibration in Automated Code Revision** (arXiv:2604.06723, https://arxiv.org/abs/2604.06723) notes post-trained LLMs are not inherently well-calibrated, motivating post-hoc calibration so users can accept outputs or abstain on error-prone ones.
- **Calibration Without Comprehension** (arXiv:2606.20502, https://arxiv.org/html/2606.20502v1) documents three distinct failure modes across models on systems-software vulnerability detection: one over-flags and buys true positives with false positives; GPT-4.1-mini is "strongly skeptical," almost always predicting "safe"; Qwen3-4B shows near-total abstention at 36.9% coverage. **Abstention is not free — you must measure coverage, not just FP rate.**
- **The End of Code Review** (arXiv:2606.13175, https://arxiv.org/pdf/2606.13175) frames the design goal precisely: a human reviewer unsure about a change says so, whereas an LLM may silently produce an approval — motivating calibrated uncertainty reporting where agents can say "I don't know" and emit confidence estimates that track empirical correctness, rather than always issuing a binary verdict.
- **Anthropic's own shipped reviewer prompt** is the best available worked example of a high-precision configuration (https://github.com/anthropics/claude-code-security-review/blob/main/.claude/commands/security-review.md). Its structure:
  - "MINIMIZE FALSE POSITIVES: Only flag issues where you're >80% confident of actual exploitability."
  - An explicit hard-exclusion list of 17 categories (DoS, rate limiting, theoretical race conditions, outdated dependencies, memory safety in memory-safe languages, test-only files, log spoofing, path-only SSRF, regex injection, findings in docs, lack of audit logs, ...).
  - A **precedents** list encoding prior adjudications ("UUIDs can be assumed unguessable," "React and Angular are generally secure against XSS unless using `dangerouslySetInnerHTML`," "environment variables and CLI flags are trusted values").
  - A **1–10 confidence score per finding**, with a separate parallel sub-task per finding applying the false-positive filter, and a **hard programmatic cut at confidence < 8**.
  - Final reminder: "Better to miss some theoretical issues than flood the report with false positives."
  
  Note the tension worth flagging to the operator: this prompt is the *conservative* configuration, and per Anthropic's own Sonnet 5 guidance it is exactly the shape that suppresses recall on newer models. Anthropic's resolution is architectural, not textual — **separate the finding stage (coverage, all findings, self-reported confidence and severity) from a filter stage (precision, hard threshold)** — and the security-review prompt implements that split with sub-tasks.

- Practitioner corroboration of the two-pass pattern: G-Research reported single-pass review yielding eight findings of which two or three were false positives, and fixed it with a second LLM call that sends findings back for genuineness classification with false-positive examples (https://www.gresearch.com/news/building-a-code-review-tool-the-llm-patterns-that-actually-work/). Datadog reports the symmetric trap: prompts optimized to catch true positives misclassified more false positives, and prompts tuned to filter false positives missed real issues (https://www.datadoghq.com/blog/using-llms-to-filter-out-false-positives/).
- Anthropic's security-guidance plugin adds two operational levers: to silence a specific finding, add an inline code comment explaining why it is safe (the reviewer treats inline justifications as exclusions); for systemic exclusions, document them in a `claude-security-guidance.md` (https://code.claude.com/docs/en/security-guidance).

### (d) Position bias and self-preference bias

- **Position bias** is well established (Wang et al. 2024a; Zheng et al. 2024; Wu & Aji 2024; systematic study at https://arxiv.org/abs/2406.07791). The cheap, standard mitigation is **swap the order of the compared items and average the scores**; split-and-merge calibration is a refinement. Surveyed in https://www.sciencedirect.com/science/article/pii/S2666675825004564.
- **Self-preference bias**: LLM judges systematically favor their own outputs. Automated quantification finds self-preference bias is **not strongly correlated with judge capability** (arXiv:2604.22891, https://arxiv.org/html/2604.22891v2) — a stronger judge is not automatically a fairer one. Rubric-based evaluation does not escape it (arXiv:2604.06996). And it is inheritable: a distilled proxy judge can carry the teacher's preference bias (arXiv:2505.19176, https://arxiv.org/pdf/2505.19176).
- **Systematic mitigation review**: arXiv:2604.23178 (https://arxiv.org/html/2604.23178) evaluates prompting and aggregation mitigations that apply to any general-purpose judge without retraining. Families: panel-of-judges / juries (https://arxiv.org/abs/2404.18796), debate and deliberation, adversarial rationale pairs, and length-controlled win rates for verbosity bias (Dubois et al. 2024).
- **Cautionary data point on multi-agent adversarial review**: in Refute-or-Promote (arXiv:2604.19049, https://arxiv.org/pdf/2604.19049), **80+ agents including dedicated adversarial reviewers unanimously endorsed a Bleichenbacher padding-oracle vulnerability in OpenSSL's CMS module that did not exist.** Consensus among model reviewers is not evidence. This is the strongest single argument against treating multi-model quorum as ground truth.
- **Architectural alternative that works**: iCodeReviewer (https://arxiv.org/pdf/2510.12186) uses lightweight static analysis to route prompts so the LLM is never asked about security issues that are not applicable — removing the opportunity to invent a finding rather than instructing against it. LLM4FPM (https://arxiv.org/html/2411.03079v2) and an industrial study (https://arxiv.org/pdf/2601.18844) report 0.93–0.94 accuracy filtering static-analysis false positives, cutting warning validation from ~10 minutes to ~3.

### Synthesis for a reviewer lane

1. **Split finding from filtering.** Stage 1 maximizes coverage and self-reports confidence and severity per finding. Stage 2 applies a hard threshold. Both Anthropic's docs and Anthropic's own shipped prompt do this; G-Research arrived at it independently.
2. **Never ask for a count.** Use a consequence threshold ("bugs that could cause incorrect behavior, a test failure, or a misleading result") and explicitly permit an empty result. A fixed quota is a documented manipulation lever in both directions.
3. **Require evidence per finding, but be aware of the cost.** arXiv:2603.00539 shows demanding explanation-plus-fix made the judge drastically more conservative. If your lane wants coverage, ask for a file:line citation and a one-line exploit/consequence path, and defer the full fix to the filter stage.
4. **Do not use qualitative severity words as the bar.** "Important," "high-severity," "don't nitpick" are the exact strings Anthropic says newer models now obey literally, silently dropping real bugs.
5. **Measure coverage, not just false positives.** Every abstention-tuning result in the literature buys precision with recall. A reviewer that never flags anything scores perfectly on FP rate.
6. **For any pairwise or cross-model judging: swap positions and average.** It is the cheapest real mitigation with the strongest evidence base.
7. **Do not treat multi-model agreement as truth.** The OpenSSL false-consensus result is decisive on this point.

---

## Sources

Anthropic
- Prompting best practices — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- Prompting Claude Opus 5 — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5
- Prompting Claude Sonnet 5 — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5
- Prompting Claude Fable 5.1 — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1
- Models overview — https://platform.claude.com/docs/en/models/overview
- Pricing — https://platform.claude.com/docs/en/about-claude/pricing
- Claude Code security guidance — https://code.claude.com/docs/en/security-guidance
- security-review.md prompt — https://github.com/anthropics/claude-code-security-review/blob/main/.claude/commands/security-review.md

Google
- What's new in Gemini 3.8 Flash — https://ai.google.dev/gemini-api/docs/latest-model
- What's new in Gemini 3.5 Flash (carries current 3.x prompting best practices) — https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5
- Prompt design strategies (Gemini 3 section) — https://ai.google.dev/gemini-api/docs/prompting-strategies
- Gemini API pricing — https://ai.google.dev/gemini-api/docs/pricing
- Gemini 3 developer guide (deprecated, redirects to the above) — https://ai.google.dev/gemini-api/docs/gemini-3
- Antigravity CLI headless mode — https://antigravity.google/docs/cli/headless/
- Antigravity CLI best practices — https://antigravity.google/docs/cli/best-practices/
- Antigravity CLI guide (third party, `--effort`/CI auth claims) — https://www.aibuilderclub.com/blog/antigravity-cli-guide

xAI
- Grok 4.6 model page — https://docs.x.ai/developers/grok-4-6
- Full docs corpus (reasoning effort, caching best practices, AGENTS.md, release notes) — https://docs.x.ai/llms.txt
- Grok Build project rules — https://docs.x.ai/build/features/project-rules
- Speech-to-speech prompting guide (only structural prompting guide xAI ships) — https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/prompting-guide
- Grok 4.6 announcement (vendor marketing, benchmarks) — https://x.ai/news/grok-4-6
- Third-party prompt tips — https://venice.ai/blog/grok-4-6-prompt-tips

Cursor
- Composer 2.5 model page — https://cursor.com/docs/models/cursor-composer-2-5
- Agent prompting — https://cursor.com/docs/agent/prompting.md
- Docs sitemap (model roster, CLI pages) — https://cursor.com/docs/llms.txt
- Composer 2.5 architecture/benchmarks (secondary) — https://www.thesys.dev/blogs/cursor-composer-2-5 and https://www.deeplearning.ai/the-batch/cursor-fits-its-model-to-its-agent

OpenAI (paused — record only)
- Using GPT-5.6 — https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6
- Model guidance index (now headed by GPT-6 Astra) — https://developers.openai.com/api/docs/guides/latest-model
- API pricing — https://developers.openai.com/api/docs/pricing
- Builder's guide to GPT-5.6 — https://openai.com/index/builders-guide-to-gpt-5-6/
- Third-party summary of the subtractive-prompting result — https://decrypt.co/373439/openai-new-gpt-5-6-prompt-guide-chatgpt

Reviewer prompting research
- Measuring and Exploiting Confirmation Bias in LLM-Assisted Security Code Review — https://arxiv.org/html/2603.18740v1
- Are LLMs Reliable Code Reviewers? Systematic Overcorrection — https://arxiv.org/pdf/2603.00539 · https://link.springer.com/article/10.1007/s10515-026-00638-5
- I-CALM: Incentivizing Confidence-Aware Abstention — https://arxiv.org/abs/2604.03904
- Fine-grained Confidence Calibration in Automated Code Revision — https://arxiv.org/abs/2604.06723
- Calibration Without Comprehension — https://arxiv.org/html/2606.20502v1
- The End of Code Review — https://arxiv.org/pdf/2606.13175
- Refute-or-Promote (false-consensus result) — https://arxiv.org/pdf/2604.19049
- iCodeReviewer — https://arxiv.org/pdf/2510.12186
- LLM4FPM — https://arxiv.org/html/2411.03079v2
- Reducing False Positives in Static Bug Detection with LLMs (industry) — https://arxiv.org/pdf/2601.18844
- When Your Reviewer is an LLM (weakness-quota manipulation) — https://arxiv.org/abs/2509.09912
- OLIVER abstract screening (inclusion-bias → FP inflation) — https://arxiv.org/pdf/2512.20022
- Judging the Judges: bias mitigation strategies — https://arxiv.org/html/2604.23178
- Quantifying and Mitigating Self-Preference Bias of LLM Judges — https://arxiv.org/html/2604.22891v2
- Assistant-Guided Mitigation of Teacher Preference Bias — https://arxiv.org/pdf/2505.19176
- Judging the judges: position bias systematic study — https://arxiv.org/abs/2406.07791
- Replacing Judges with Juries — https://arxiv.org/abs/2404.18796
- A survey on LLM-as-a-judge — https://www.sciencedirect.com/science/article/pii/S2666675825004564
- G-Research: LLM code review patterns that work — https://www.gresearch.com/news/building-a-code-review-tool-the-llm-patterns-that-actually-work/
- Datadog: filtering static-analysis false positives with LLMs — https://www.datadoghq.com/blog/using-llms-to-filter-out-false-positives/

### Explicit gaps (no 2026 source found)
- Anthropic publishes no model-specific prompting page for Haiku 4.5.
- xAI publishes no coding-oriented prompt-engineering guide for Grok 4.6; the `/docs/guides/prompt-engineering` path 404s.
- Cursor publishes no model-specific prompt-structure guidance for Composer 2.5, and no confirmation of JSON-schema structured output through `cursor-agent`.
- No vendor source quantifies long-context degradation, hallucinated-file-path rates, or test-modification tendency for Gemini 3.x, Grok 4.6, or Composer 2.5.
- No study directly measures the effect of requesting a fixed *number* of review findings on false-positive rate; the evidence for that specific claim is indirect (see section 6b).
