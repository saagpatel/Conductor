# Same task, every fleet (2026-09-04)

Task: scratch Python repo, two failing tests (median of an even-length list; mean of an empty list must raise ValueError). Prompt: run pytest, read the failures, fix `pkg/`, do not edit tests, re-run until green, two-line summary, do not commit. Write mode, gate `pytest -q`, test policy allow, one run each. Bytes checked by me after each run.

| Lane | Route | Result on bytes | Tool calls | Wall | Cost | Notes |
|---|---|---|---|---|---|---|
| DeepSeek V4 Flash | opencode → OpenRouter | 5/5 pass, +4 lines | 4 | 23 s | $0.003 | ran pytest exactly twice; accurate summary |
| GLM-5.3-Flash | opencode → OpenRouter | 5/5 pass, +6/-1 | 5 | 27 s | $0.002 | same profile; no `!!!!!` degeneration on this prompt |
| Gemini 3.8 Flash (medium) | antigravity (conductor) | 5/5 pass, +8/-1 | 18 | 20 s | $0.020 | most tool calls; also added an unrequested empty-list guard to median |
| Grok 4.6 (medium) | cursor (conductor) | 5/5 pass, +7/-2 | 7 | 37 s | $0.084 | narrated intent before each step; correct |
| Composer 2.5 | cursor (conductor) | 5/5 pass, +7/-2 | 5 | 18 s | $0.045 | fastest of the paid fleets |
| Claude Haiku 4.5 | claude (conductor) | 5/5 pass, +6/-1 | 16 | 62 s | $0.136 | **never ran a test**: every pytest Bash call was refused ("This command requires approval"); edited blind, reported "I've fixed" anyway |
| Claude Sonnet 5 | claude (conductor) | **no change**, gate exit 1 | 11 | 36 s | $0.246 | 8 refused pytest attempts (5 identical in a row), then asked the operator for approval and stopped |
| Codex (any) | paused by operator | not run | | | | |
| OpenCode Zen paid ids | opencode → Zen | **401 No payment method** | 0 | | $0 | operator billing action if Zen is wanted; OpenRouter works today |

## Findings that change something

1. **Claude write lanes cannot run tests.** conductor spawns write mode with `--permission-mode acceptEdits`, which auto-approves Edit/Write but not Bash beyond trivial read-only commands (`pwd`, `ls`, `test -e` passed; `pytest` did not). Every release so far used Claude only for read-mode review, so this never surfaced. Fix candidates (verify against `claude --help`): `--permission-mode bypassPermissions`, `--dangerously-skip-permissions`, or a scoped `--allowedTools "Bash(<gate command>)"`. Until fixed, a Claude build lane is a blind editor and Haiku's transcript shows it will still say "fixed".
2. **Cheap open models did this task at 1/10 to 1/80 the cost of the paid fleets** with the cleanest tool profiles in the table. One task, one run each: proves capability, not long-horizon reliability.
3. **Gemini 3.8 Flash over-delivered** (an extra guard nobody asked for) and used 3x the tool calls of the cheap models. Fine for a build lane; a reviewer should be told the spec is the boundary.
4. **Cost per task, cheapest to dearest:** GLM-5.3-Flash $0.002, DeepSeek $0.003, Gemini $0.02, Composer $0.045, Grok $0.084, Haiku $0.136 (blind), Sonnet $0.246 (nothing). The Claude figures are inflated by the refusal loop; a fixed lane would cost less, but not 40x less.
5. Nemotron 3.5 Lightning (free) is fine as a read-only probe subject but is trial-use-only per NVIDIA's terms: never point it at a private repo.
