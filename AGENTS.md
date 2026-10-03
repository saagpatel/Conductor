# conductor: project instructions

Read by every agent that works in this repo (Claude Code via `CLAUDE.md`, Codex, Antigravity,
Cursor, Grok Build, OpenCode all read `AGENTS.md`). The README explains what conductor does;
this file says how to work on it and how to prompt the models it drives. Evidence for every
claim below lives in `docs/research/` (dated reports with URLs and live-probe receipts).

## Peer review sitting, 2026-09-07

An outside reviewer (GPT-6 Astra, run by the operator in the Codex app, not as a conductor lane)
is reviewing the project from first principles. The brief is
`docs/review/2026-09-07-peer-review-brief.md`; the reviewer writes only to
`docs/review/2026-09-07-astra-notes.md`. If you are that reviewer, read the brief first; its
rules for the sitting (no other file changes, no commits, no dispatch) apply on top of this file.

## The repo contract

- Python 3.12, stdlib only, `src/conductor`. Branch `feat/conductor-v1`; never commit to `main`.
- **Local-only repository. Never add a remote, never push, never publish.** Back up with an
  encrypted archive if a backup is needed.
- Gate before any commit, exit codes captured to files, never piped through `tail` or `head`:
  ```
  .venv/bin/ruff check src tests
  conductor_test_tmp=$(mktemp -d "${TMPDIR:-/tmp}/conductor-tests.XXXXXX")
  .venv/bin/pytest -p no:cacheprovider -o addopts="-q" -n auto --dist loadgroup --basetemp="$conductor_test_tmp"
  ```
  Inside a worktree, allocate the fresh external temporary directory as above, then use `PYTHONPATH="$PWD/src" ~/Projects/conductor/.venv/bin/pytest -q -p no:cacheprovider -n auto --dist loadgroup --basetemp="$conductor_test_tmp"`.
  `-n auto` is pytest-xdist (dev group only; the runtime stays stdlib): the suite runs in about
  30 seconds across workers instead of four minutes, and was clean three times in a row when it
  was adopted on 2026-09-07. Always pass `--basetemp` outside the tree, with a path containing no spaces (some subprocess fixtures interpolate paths into shell snippets). If a load-sensitive test
  ever flakes under workers, mark it `@pytest.mark.xdist_group(name="serial")`; `--dist loadgroup`
  is part of the gate for that reason (first member: a cascade resume test, 2026-09-07). Never go
  back to the serial gate (operator decision 2026-09-07).
- Conventional commits, one logical unit each, no co-author trailers, no absolute home paths or
  personal details in messages or committed docs.
- A fleet's word is never evidence. Verify on bytes: diff, gate output, receipts in `~/.conductor`.
- Every design rule in `fleets.py`'s docstring (first-party routing, allowlist enforced at
  dispatch) stands. Rejected and not to be re-proposed: an MCP wrapper, fleet self-commit,
  atomic budget reservation, reclaiming crashed runs by age, `claude --bare`.

## Fleet policy as of 2026-09-04

- **Codex / OpenAI lanes are paused** until the operator lifts it: no `codex exec`, no `codex`
  fleet in any mission, no `codex-delegate`. Sol, Terra, Luna alike.
- Preferred lanes: Gemini (antigravity), Grok and Composer (cursor), Claude. Orchestration
  judgment sits with the lead session, not with the most expensive lane.
- **Shelved 2026-09-06, operator decision:** D4 cloud offload, for this repository: every cloud mode ships
  the checkout to a vendor, which the local-only rule forbids. Not to be re-proposed here.
- **Shelved 2026-09-04, operator decision:** OpenCode, OpenRouter, Ollama, pi, and local models. Not
  fleets, not to be re-proposed without the operator raising it. Reasons on record: free hosted tiers
  carry training and confidentiality terms; local inference runs the GPU flat out (fans, 23 GB
  resident, minutes per task) and a 30B mixture-of-experts is already the lightest useful option; paid
  OpenRouter saves nothing over the existing lanes. The research and probe receipts in
  `docs/research/` stay as the record; the OpenCode and pi sections below are that record, not policy.
- Free-tier gateway models (`*-free`, `contributor-free`, Big Pickle) never see a private repo:
  NVIDIA's free endpoints are trial-use-only with a "no confidential data" clause, Meta's
  contributor tier trains on your prompts, the rest may. Scratch repos only.
- Pipeline shape is measured, not assumed. "Sol builds, Opus reviews" was a habit; the receipts
  in `docs/RESET-2026-09.md` show cheaper shapes doing the same work.

## Shape A: the measured default and its operating rules

Shape A shipped C3, A3, A4, and B5 on 2026-09-05 (v0.13.0 to v0.16.0) at 26 to 49 percent of the
Sol/Opus cost per item; receipts in `docs/RESET-2026-09.md`. The shape: Sonnet 5 builds at `hard`
under a per-lane cap; Gemini 3.7 Flash and Grok 4.6 review cold, in parallel, with the no-quota
template; Sonnet fixes on the build's resumed thread; the lead reads the diff, runs the gate in a
fresh worktree, tidies, merges, and cuts the release. Every rule below cost a receipt to learn:

1. **Declare stages and a vendor policy on the mission.** `stage: build | review | fix` per lane,
   `policy` naming the vendors each stage may use. A `fix` lane runs under the reproduce gate, so it
   cannot verify with a throwaway script; a `review` lane is refused if any build attempt shares its
   vendor, so a build fallback on the reviewer's vendor is not allowed (drop the fallback, rerun by
   hand if the build fails).
2. **Size the build cap to the spec:** about a dollar per spec item on Sonnet at `hard`, plus two
   dollars for any item that touches the scheduler, the runner's wait loop, or resume, plus a dollar
   for every module past the second that the spec touches. A cap sized for a small item cut two
   builders off one lint error and one README phrase from green; a five-item spec spread over four
   modules (B2) was cut $2 short the same way. The launcher now raises **every warned lane's**
   cap to the forecast p80, rounded up, whenever the E14 forecast warns that this figure
   sits under it (F17), and raises the mission budget with them, so the hand edit before
   every launch is gone; `--no-forecast-cap` keeps the old behaviour. This line used to say
   "the build and fix caps"; two cold reviewers read that against `forecast.apply_caps`,
   which has always raised every warned lane, and both filed the code as the defect
   (2026-09-08). The code is right and the prose was stale: a review cap warned by history
   is a cap that will fail its lane, and Grok's read cap on this repository was raised by
   hand across three waves for exactly that reason. Rule 7's figures are the starting
   estimate, not a ceiling the forecast may not move.
   `conductor report` measures the figure this rule estimates: `usd_per_item` per merged
   mission and one cost per landed item across them (review item 2, 0.76.0).
3. **`test_policy: allow` on the build lane when the spec changes what existing missions may do.**
   The clean gate reruns the original tests against the new source; when the new source refuses a
   shape the old fixtures build, both builders were right and both were rejected. The lead then reads
   every edit to an existing test. The same holds for a fix lane that re-records or rewrites test fixtures:
   the clean gate runs the base tree's tests against the new fixtures and fails (C7's fix was salvaged
   from its kept worktree for exactly this). **Test-only hardening is a build-stage lane, never a
   fix lane:** a `fix` lane whose every change sits under the test surface, and whose stricter tests
   pass on the base, is refused by the reproduce gate with `run this as a build lane` (F21 slice 2's
   fix lane was salvaged by hand for this before the refusal was named).
4. **Every spec says: keep existing call signatures working** (new parameters as keywords with
   defaults). The first Sonnet build changed one and the clean gate rejected a green run.
5. **Reviewers that run the suite pass `--basetemp` under `$TMPDIR`.** A read lane that leaves pytest
   scratch in its worktree fails on bytes even when its findings are right. **Gemini does not run the
   suite at all on this repo:** `agy` starts it as a background task and polls the log every few
   seconds, and a two-minute suite burns the tool ceiling before it returns (killed twice on C4, at
   261 and 196 calls, no answer written). Its review prompt says "do not run the test suite; read the
   diff and the code". Grok runs it fine in the foreground.
6. **The salvage path is normal, not exceptional.** A kept worktree with a green own gate is a
   deliverable: gate it, read it, commit it, then run the review and fix lanes as a mission with
   `cwd` at that commit and the reviewers pointed at `git show`. Cost of the remainder: $1.50 to
   $2.50. `conductor salvage MISSION_ID --lane NAME` runs the gate step and receipts it; `--emit`,
   once the commit is made, writes that review-and-fix mission instead of by hand (README, "Salvage").
7. **Keep both reviewers.** Across seven runs Grok found real defects outside the diff's own lines every
   time it reported, and Gemini's answer was correct every time it wrote one (NO_FINDINGS on what the
   diff alone shows, or the same bug Grok found). Complementary, not redundant, and $0.70 to $1.00 for
   the pair. **Grok's cap is $2.00 when it runs the suite on this repo, $1.50 when it reads only:** on D3 at $1.50
   it finished a complete two-finding review at $1.96 after a five-minute suite; at $1.00 it twice finished a
   complete review a few cents over, which fails the lane and skips the fix (Cursor's cap is a verdict
   after the run).
8. **A session restart kills a background mission.** Send conductor SIGINT (it writes an interrupted
   receipt), restart, then `conductor mission --resume <id>`; finished lanes are not paid twice.
9. **Never launch a mission from a checkout that is mid-merge.** The venv's `conductor` imports
   `src/` from the working tree, so a conflict marker in `mission.py` is a `SyntaxError` at startup
   and the mission never starts. A mission already running is unaffected (its modules are loaded), so
   merging while lanes run is fine; launching is not. Three parallel missions merged in series worked
   (B2, B3, C4); the lead's merge and salvage time, not the fleet spend, was the cost that grew.
10. **A cascade is for small items, and every Claude build cap gets a dollar for the summary.** Sonnet
   at medium under $4 could not build a five-item spec (C5: escalation rate 1.0, $4 bought nothing);
   put the cascade on one- or two-item specs and fix lanes. Claude stops itself at exactly the cap and
   its final summary costs, so a green build was lost for five cents twice in one day and a green fix
   for seven cents the next (D1): every Claude cap, build or fix, = estimate from rule 2, plus one dollar.
   E24 adds a smaller, opt-in per-lane `cap_grace_usd` for the same trap, without moving rule 2's
   estimate. On a Claude lane it is folded into the same native cap, so the terminal message itself
   has room to finish. F5 extends it to a cursor *read* lane, whose cap is post-hoc: there it widens
   the after-the-fact verdict, so a complete review a few cents over `cap_usd` is not failed for it.
   A cursor write lane refuses it (`DispatchRefused`) -- a write lane's cost is bytes, and the band
   would only buy more of them.

11. **Wall clock is the lead's cost, so overlap what does not depend on what.** Independent items
   (no shared module, no scheduler tax) launch in parallel and merge in series, as E3 and E9 did.
   The lead's fresh-worktree gate on a build tip runs while the reviewers read, not after. A fix
   tip that fast-forwards a gated build tip is gated once, on the merged tree, not twice. And a
   spec whose tests are a fifth of the items is sized as if they were half (count the tests item
   twice in `--items`): E10's build spent its whole cap on code and none on tests, and the salvage
   cost an hour of wall clock and two extra missions.

12. **A lane's `--test` on this repository runs under `env -u CONDUCTOR_LANE`, and lane gates run
   one at a time.** Every dispatched process carries `CONDUCTOR_LANE=1` so `land.py` can refuse to
   run inside a lane; seven tests in `test_land_failure_paths.py` assert that refusal, so the suite
   fails inside any lane that runs it. Wave 6 lost four lane gates to this and nothing else (the
   commits were undone, the worktrees kept, and every one salvaged clean). Separately, five `-n auto`
   suites at once flaked `test_stop.py::test_a_stop_request_kills_the_fleet_and_releases_its_worktree`
   in all five worktrees including one that touched only `prices.py`: launch lanes in parallel, gate
   their tips in series.

`conductor shape a --adversarial` (E16) adds a lane whose deliverable is a test that fails
on the build's tip rather than prose, and moves the fix lane onto it so a reproduced defect
is inherited rather than re-earned.

Every Shape A build lane writes `evidence.json`, a map from spec item to files, tests, and the
check run, that the reviewers read as a claim beside the diff (Phase H item 6). A spec item the
map does not name is a finding.

`conductor shape a --opus-review` (F9 Shape C, Phase H) adds Opus 5 at `hard` as a third cold
reviewer at a $4.00 cap, with `self_judging: allow` on the mission because the build is Sonnet.
About three times the pair's cost; for lifecycle, security, and spec-risk work, not the default.

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
- Sonnet 5: two fifths of Opus's price ($2/$10 against $5/$25 per million, `prices.py`), more
  agentic by default, **literal**: it does exactly the
  stated scope and follows "be conservative" to the letter. State scope explicitly. Under-thinks
  at `low` on anything non-trivial; raise effort rather than prompt around it. Temperature and
  manual thinking budgets return 400.
- Haiku 4.5: 200K context, no effort parameter, oldest knowledge cutoff (mid-2025). Cheap read
  lanes and mechanical edits only.
- Prompt shape: XML tags for structure, long context first and the question last, explain the
  why behind a rule, positive instructions over prohibitions, no over-firm language. Anthropic's
  verbatim anti-test-gaming and investigate-before-answering snippets belong in every build
  prompt.
- Write lanes run with `--permission-mode bypassPermissions` (fixed 2026-09-05, 2881fe1, pinned by a
  test). Before that they ran with `acceptEdits`, which refuses Bash beyond `pwd`/`ls`: Haiku edited
  blind and reported success, Sonnet stopped and asked. Sonnet 5 at `hard` has since built four
  roadmap items; its one recurring trap is changing an existing call signature, so every spec says
  not to. A project `permissions.deny` rule is not a boundary for a write lane (the lane can edit
  the settings file and Claude Code hot-reloads it for the next subagent), so conductor fails a
  write lane that changes `.claude/settings.json` or `.claude/settings.local.json`, kind
  `settings`.

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
- Per-lane tool denial exists headless: `<cwd>/.agents/hooks.json` with a named `PreToolUse`
  command hook answering `{"decision": "deny"}` per tool name blocks the call on bytes. No
  wildcard matcher; a malformed file logs "loaded 0 named hooks" and the run continues, so
  pass `--log-file` and check the count (`docs/research/2026-09-06-live-probe-tool-deny-non-claude.md`).
  E21 builds taint enforcement on exactly this: `fleets.taint_hook_files` *returns* the hook file
  plus a stdlib-only deny script as text, `runner._write_taint_agy_hooks` writes both into the
  tainted lane's worktree, and `runner.dispatch` refuses to trust that write. Two checks, in this
  order:
  1. **Pre-spawn (F13), free.** `runner._taint_agy_preflight` runs `-p "/hooks" --output-format
     stream-json`, which answers in print mode at `num_turns: 0` with zero usage and names every
     loaded hooks file with its `source` and `enabled` flag. It fails the run, before any spend,
     when the lane's own hooks file is absent or disabled, *and* when the file is loaded without a
     `PreToolUse` matcher for every tool `fleets.taint_agy_matchers()` names. This is the only
     check that compares matchers by name, and it only runs when `agy` can spawn at all.
  2. **After the run.** The `loaded N named hooks` count is read back out of `agy.log` and the run
     fails when it is zero (a hooks file that did not parse), or as `taint hooks not enforced`
     when the init event's tool list still names something reaching outside the worktree that no
     hook covered. The count itself is *not* compared against the number of matchers written.
  3. **Digest read-back.** The hook files' sha256 is recorded when they are written, and a file
     that differs, is gone, or is unreadable afterwards fails the run as `taint hooks modified
     during the run`: the script sits in a writable worktree and is re-read on every tool call.
- **`--json-schema` on a read-mode lane is refused at load (F13):** the same probe found a
  schema'd read lane take a second turn, under `--mode plan --sandbox`, that wrote a file into
  the working directory and ran a shell command. `Spec.validate` refuses `schema` on
  `antigravity` in `mode: read` before spawn; write mode is unaffected. Conductor's own
  checklist-verdict and ranking-collate schemas are generated internally and always duplicated
  into the prompt as text, so `mission.py`/`runner.py` just drop the flag for this one fleet in
  read mode rather than losing structured review on it.

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
- Per-lane tool denial exists headless: `<cwd>/.cursor/cli.json` with `permissions.deny`
  rules (`Shell(...)`, `Write(...)`, `Mcp(...)`) holds on bytes even under `--force`. An
  unknown rule kind is ignored silently, so validate rules before writing them (same probe doc).
  No rule kind covers Cursor's native web fetch and search tools, so E21 leaves taint refused
  here (Antigravity's `PreToolUse` hook does not have that gap; see above).

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

### Local models through `pi` + llama.cpp (planned fleet `pi`)

Receipts: `docs/research/2026-09-04-live-probe-pi-local.md`. Qwen3-Coder-30B-A3B Q4_K_M on the
48 GB M4 Pro fixed the two-test probe with zero malformed tool calls in 20, but took 7 minutes and
spent 12 calls in the wrong repo because the prompt named a path elsewhere: **name the working
directory in every local write prompt.** Server: `llama-server --jinja -fa on -ngl 99 -c 65536
--parallel 1`, no KV-cache quantization (it halves throughput and pushed attention onto the CPU here).
Cold prefill is 136 s at 32K and 6 min at 64K, so local read lanes stay under ~16K tokens of input.
`pi auth check` runs a full agent turn, not a ping; preflight with `GET /health`. Start the server per
mission and stop it after (23 GB resident, GPU flat out while prefilling).

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
