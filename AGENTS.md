# conductor: project instructions

Read by every agent that works in this repo (Claude Code via `CLAUDE.md`, Codex, Antigravity,
Cursor, Grok Build, OpenCode all read `AGENTS.md`). The README explains what conductor does;
this file says how to work on it and how to prompt the models it drives. Evidence for every
claim below lives in `docs/research/` (dated reports with URLs and live-probe receipts).

## The repo contract

- Python 3.12, stdlib only, `src/conductor`. Branch `feat/conductor-v1`; never commit to `main`.
- **Local-only repository. Never add a remote, never push, never publish.** Back up with an
  encrypted archive if a backup is needed.
- Gate before any commit, exit codes captured to files, never piped through `tail` or `head`:
  ```
  .venv/bin/ruff check src tests
  .venv/bin/pytest -p no:cacheprovider -o addopts="-q"
  ```
  Inside a worktree: `PYTHONPATH=src ~/Projects/conductor/.venv/bin/pytest -q -p no:cacheprovider`.
- Conventional commits, one logical unit each, no co-author trailers, no absolute home paths or
  personal details in messages or committed docs.
- A fleet's word is never evidence. Verify on bytes: diff, gate output, receipts in `~/.conductor`.
- Every design rule in `fleets.py`'s docstring (first-party routing, allowlist enforced at
  dispatch) stands. Rejected and not to be re-proposed: an MCP wrapper, fleet self-commit,
  atomic budget reservation, reclaiming crashed runs by age, `claude --bare`.

## Fleet policy as of 2026-09-04

- **Codex / OpenAI lanes are paused** until the operator lifts it: no `codex exec`, no `codex`
  fleet in any mission, no `codex-delegate`. Sol, Terra, Luna alike.
- Preferred lanes: Gemini (antigravity), Grok and Composer (cursor), Claude, and the cheap open
  models through OpenCode (DeepSeek V4 Flash, GLM-5.3-Flash first). Orchestration judgment sits
  with the lead session, not with the most expensive lane.
- Free-tier gateway models (`*-free`, `contributor-free`, Big Pickle) never see a private repo:
  NVIDIA's free endpoints are trial-use-only with a "no confidential data" clause, Meta's
  contributor tier trains on your prompts, the rest may. Scratch repos only.
- Pipeline shape is measured, not assumed. "Sol builds, Opus reviews" was a habit; the receipts
  in `docs/RESET-2026-09.md` show cheaper shapes doing the same work.

## Reviewer prompts: no quotas, ever

A review lane measures what is there. The evidence (`docs/research/2026-09-04-research-frontier-models.md` §6):
"find the defects" framing inflates findings, "be conservative / only high-severity" framing
makes current models silently drop real bugs (Anthropic states this for Opus 5 and Sonnet 5),
and demanding a fix alongside each finding made judges reject correct code three times as often.

Rules for every review, judge, and collate prompt conductor sends:

1. **Say that an empty result is a correct answer.** Literally: "If you find nothing that meets
   the bar, say so; an empty list is a complete and expected answer."
2. **Never ask for a number of findings**, a minimum, or "at least". Never say "hunt", "find the
   defects", or prefix a format like `DEFECT:` that presumes there are some.
3. **Use a consequence threshold, not adjectives.** "Report anything that could cause incorrect
   behavior, a test failure, or a misleading result. Omit style and naming." Not "important",
   "serious", "nitpick".
4. **Split finding from filtering.** Stage one reports everything it sees with a confidence and
   a severity per item and a file:line citation. A separate stage (or conductor's verdict
   checklist) applies the bar. One prompt cannot maximize both recall and precision.
5. **Cite or drop.** Every finding names the hunk. No citation, no finding.
6. **The whole review is the final message.** A Claude read lane in plan mode once wrote its
   findings to a plan file and answered "see above"; the receipt was empty.
7. **Judging two candidates: present both orders and reconcile.** Position bias is systematic.
   A judge never scores its own vendor's output (self-preference is not fixed by capability).
8. **Agreement between models is not truth.** Eighty agents including adversarial reviewers once
   unanimously endorsed a vulnerability that did not exist. A quorum passes a gate; it does not
   prove correctness.

Template that satisfies all of the above:

```
Review the change below against the spec. Report anything that could cause incorrect behavior,
a test failure, or a misleading result. Omit pure style and naming. For each item: file and
line, what goes wrong, one sentence of consequence, your confidence 1-10. If nothing meets that
bar, reply exactly: NO_FINDINGS. Either answer is complete. Put the entire review in this reply.
```

## Model notes: strengths, traps, prompting

Vendor guidance conflicts across vendors (xAI says capitalize ALWAYS/NEVER; Anthropic and Google
say the opposite), so lane prompts are written per fleet, never one prompt for all. Full detail
and sources: `docs/research/2026-09-04-research-frontier-models.md` and `...-research-open-models.md`.

### Claude Opus 5, Sonnet 5, Haiku 4.5 (fleet `claude`)

- Opus 5: best on multi-file features and cold review ("additional findings are mostly real").
  Traps: over-verifies when told to verify (remove "double-check" instructions), expands scope,
  over-delegates to subagents, narrates. Keep thinking on and lower effort instead of disabling
  thinking (thinking off leaks tool calls as plain text that poison the transcript).
- Sonnet 5: a quarter of Opus's price, more agentic by default, **literal**: it does exactly the
  stated scope and follows "be conservative" to the letter. State scope explicitly. Under-thinks
  at `low` on anything non-trivial; raise effort rather than prompt around it. Temperature and
  manual thinking budgets return 400.
- Haiku 4.5: 200K context, no effort parameter, oldest knowledge cutoff (mid-2025). Cheap read
  lanes and mechanical edits only.
- Prompt shape: XML tags for structure, long context first and the question last, explain the
  why behind a rule, positive instructions over prohibitions, no over-firm language. Anthropic's
  verbatim anti-test-gaming and investigate-before-answering snippets belong in every build
  prompt.
- **Conductor bug (found 2026-09-04):** write lanes run with `--permission-mode acceptEdits`,
  which refuses Bash beyond `pwd`/`ls`. A Claude build lane cannot run tests; Haiku edited blind
  and reported success, Sonnet stopped and asked. Fix before any Claude build lane:
  `--permission-mode bypassPermissions` or `--allowedTools "Bash(<gate>)"`.

### Gemini 3.8 Flash and 3.7 Flash (fleet `antigravity`, binary `agy`)

- 3.8 is the long-horizon agent tier and, by Google's own words, spends more tokens on iterative
  self-verification by design; 3.7 is the everyday tier at the same list price ($0.75 / $3.75
  per million, doubling 2027-01-01). Use 3.7 when the task does not need the extra loop.
- Prompting: precise and direct, no persuasive language, no forced chain-of-thought (use
  `thinking_level`), never touch temperature. Behavioral constraints at the top, bulk context
  first and the question last with "Based on the information above". Terse by default; ask for
  detail explicitly. Tool overuse: lower effort first, then "You have a budget of N tool calls."
- Headless traps: **`status` in the JSON result is the verdict, not the exit code**; a tool
  that needed approval is soft-denied and the run still exits 0. `--conversation <missing id>`
  starts a new conversation silently (conductor asserts the returned id). Needs `--add-dir` or it
  works in its own scratch directory. Effort `max` collapses to `high`.
- Observed: over-delivers (added an unrequested guard on the probe task) and uses 3x the tool
  calls of the cheap models for the same result.

### Grok 4.6 and Composer 2.5 (fleet `cursor`, binary `cursor-agent`)

- Grok 4.6: 500K context, reasoning cannot be disabled, `xhigh` exists. **Price doubles above
  200K prompt tokens** ($2/$6 to $4/$12) mid-run. xAI ships no coding prompt guide; its only
  structural advice is short specific rules, only name tools that exist, capitalize the
  non-negotiables. Reads `AGENTS.md` and `.cursor/rules`.
- Composer 2.5: Cursor's own model, trained against the Cursor tool harness, no public API,
  no effort dial, no confirmed structured output. Fastest paid lane on the probe (18 s).
- Headless traps: no structured-output flag (conductor refuses `--schema`/`--verdict` here);
  the single-envelope `json` format keeps only the last message, so conductor uses
  `stream-json`; read mode needs `--trust`. Costs are post-hoc estimates.

### Cheap open models through OpenCode (planned fleet `opencode`)

- **DeepSeek V4 Flash** ($0.14 / $0.28 on Zen, similar on OpenRouter): the only cheap model
  with a positive OpenCode field report, first-party OpenCode support, MIT weights. Probe:
  clean four-call fix, $0.003. Risk: long-horizon token burn; cap tool calls.
- **GLM-5.3-Flash** ($0.15 / $0.50; this is what "OX Alpha" became): highest intelligence per
  dollar in the catalog. Probe: clean five-call fix, $0.002. Known serving bug elsewhere
  (`!!!!!` reasoning degeneration under long multi-tool prompts); not seen on OpenRouter today.
  No effort dial on Zen.
- Qwen 3.5 Plus ($0.20 / $1.20): research and summarization lanes; weaker coder; no field data.
- Grok Build 0.1 ($1 / $2): cheap output for verbose loops; xAI names OpenCode as a harness.
- Not recommended today: Kimi K3 and K2.7-code (HTTP 400 on any tool with an input schema on
  Zen, 193-call tool storms), Muse Spark (stream never sends `finish_reason`; contributor tier
  trains on your data), MiniMax M3 (silent reasoning-only turns), Nemotron 3.5 Lightning for
  write lanes (ignored an operator STOP in a governed run; fine for read-only scratch probes).
- Model ids are `provider/model`; OpenCode Zen paid ids need a payment method on the workspace
  (none today); OpenRouter ids work now. Rate limits on OpenRouter `:free` (20/min) make them
  unusable for agent loops.

### GPT-5.6 Sol / Terra / Luna (fleet `codex`) — paused, record only

Subtractive prompting (leaner system prompts scored 10-15% higher in OpenAI's own evals), state
each instruction once, no "always/never" absolutes, name safe local actions so the model does
not ask for approval. Superseded upstream by GPT-6 Astra. Nothing here is a routing recommendation.

## OpenCode as a fleet: what the probes settled

Full receipts: `docs/research/2026-09-04-live-probes-opencode.md` and `...-research-headless-fleets.md`.

- Stream: NDJSON, `sessionID` on every event, per-step `tokens` and `cost` on `step_finish`,
  terminal `step_finish` with `reason: "stop"`. Cap mode `watcher`; price from tokens, never
  from `cost` (it is 0 on free models). No model or agent id in the stream; carry both from the
  Spec. Bogus session id and bogus model id both fail closed.
- **Permission `ask` silently allows headless.** Only `deny` gates. Read mode needs the agent's
  `tools` map false AND `permission` denies covering `bash`, `edit`, `write`, `patch`, `task`
  (subagents), `webfetch`, `websearch`, `external_directory`, `doom_loop`. Verified to hold.
- **Isolation lever is `XDG_CONFIG_HOME=<conductor-owned dir>`**, not `OPENCODE_CONFIG` (that
  merges with the operator's config and its MCP file-write tool). Also `--pure`, `mcp: {}`,
  `autoupdate: false`, always `--dir <cwd>` plus `PWD` in the env (session resolves from a
  stale `PWD` otherwise). Overhead drops from ~31K to ~14K input tokens per run.
- Write confinement is a configuration, not a sandbox: `external_directory: deny` blocked the
  file tools and a heredoc, and a plain shell redirect still wrote outside the directory.
  Conductor judges bytes in the worktree, as with every fleet. The model then claimed success.
- No schema flag on `opencode run`: refuse `--schema` and `--verdict` as for cursor, or use
  `opencode serve` (`format: {type: "json_schema"}`, permission events, abort) when OpenCode
  earns a permanent lane.
- **Ollama is not a fleet.** No cwd, no tools, no sessions. Express local weights as
  `opencode` with `ollama/<model>` (needs `num_ctx` ≥ 32K or tool schemas truncate). Best local
  fits on this 48 GB machine: `qwen3.8:27b-q4_K_M` (18 GB) and
  `nemotron-3.5-lightning:30b-a3b-q4_K_M` (25 GB, 1M context). Throughput unmeasured.

## When adding or changing a fleet

1. Probe live on a scratch repo before touching `fleets.py`: bogus model, bogus session, read
   mode asked to write (including via subagent and shell), write outside cwd, kill mid-run,
   what the stream carries. Write the receipts into `docs/research/`.
2. A fleet that reads as green on exit code alone is a false green; find its verdict field.
3. Add the price to `prices.py` with the date, or `cap_usd` will be refused.
4. Add the argv builder, the `DispatchRefused` cases for what the fleet cannot do, and a test
   per refusal. Then one live dispatch per mode through `conductor dispatch`.
