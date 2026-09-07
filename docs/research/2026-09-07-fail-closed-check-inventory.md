# Fail-closed checks: what has a live receipt, 2026-09-07

Phase H item 3 is "first-run drills for every fail-closed check". This is the worklist, compiled
read-only from the source and `docs/research/` after the 0.64.0 taint drill by an Opus 5 agent and
read by the lead. A check that reads live-fleet evidence and has only fake-fleet unit tests is
unproven on the shape the vendor streams; the 0.64.0 drill found `denied_calls` wrong on
exactly that gap. Spec and argv refusals in `fleets.py` need no fleet and are out of scope.

## Fail-closed checks that read live-fleet evidence

| check | file:line | fleets | evidence read | live receipt | notes |
|---|---|---|---|---|---|
| taint on agy refused without isolate+repo | `src/conductor/runner.py:1697` | agy | pre-spawn git state of cwd | none: unit tests only | pre-flight of the pre-flight; no fleet needed to fire it |
| `/hooks` preflight: file enabled | `src/conductor/runner.py:924`, bail at `:1912` | agy | free `/hooks` `command_result` event (num_turns 0) | `docs/research/2026-09-07-live-probe-taint-shell-deny.md` (2026-09-07): confirmed to hold | ok path only; timeout/no-event paths never seen live |
| preflight: every written PreToolUse matcher back by name | `src/conductor/runner.py:975-987` | agy | `actions[].matcher` in the same event | same doc, 2026-09-07 (35 matchers, `matchers_missing: []`) | added because the log-count check misfired live |
| hook files re-hashed after run (`taint hooks modified`) | `src/conductor/runner.py:1029-1035`, digests written `:826` | agy | sha256 of `.agents/*` on disk vs write-time | same doc, 2026-09-07: held (`hooks_modified: []`) with a prompt that *asked* the model to overwrite the file | strongest existing drill; the deny hook blocked the write first, so the digest path itself is still unfired |
| `loaded N named hooks` line present / N>=1 | `src/conductor/runner.py:1036-1046` | agy | `agy.log` text | fired live (false positive) in `docs/research/2026-09-07-consumer-anti-slop.md` (2026-09-07); corrected and re-confirmed 2026-09-07 taint drill | the one check with a real live firing |
| uncovered reaching-out tools in init | `src/conductor/runner.py:864`, `:1047` | agy | init event `tools` list | 2026-09-07 taint drill: `uncovered: []` | never fired |
| `denied_calls` counted from tool-error events | `src/conductor/runner.py:874-901` | agy | `step_update` `state: ERROR`, `tool_info.error.message` marker | 2026-09-07 taint drill (two real denials, counter read 5, fixed) | receipt-only; does not fail a run |
| `restricted mode not enforced: Bash/WebFetch present` | `src/conductor/runner.py:2181-2194` | claude | `system/init` event `tools` list | vendor half live: `docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md` (2026-09-07) shows the 25-tool restricted init; conductor-side check shipped v0.54.0 (`docs/RESET-2026-09.md:757`) and ran on real lanes without firing | no receipt of the check *firing* |
| `agent '<n>' not applied` (no init / not in `agents` / tool set mismatch) | `src/conductor/runner.py:774-800`, applied `:2155-2178` | claude | init event `agents` + `tools` | vendor half live: `docs/research/2026-09-06-live-probe-inline-agents.md` (2026-09-06, agy `--agent` fails open -> claude-only) | conductor check: unit tests only |
| write-lane `permission denied: <tools>` | `src/conductor/runner.py:2135-2150` | claude | `permission_denials[]` in the result envelope | envelope shape live in the 2026-09-07 restricted/denied probe; shipped v0.54.0 | check itself unfired live |
| `resume failed: fleet reported session X` | `src/conductor/runner.py:2114-2132` | claude, codex, cursor | `session_id` on the fleet's result envelope vs `spec.resume` | happy path live all over `docs/RESET-2026-09.md` (Shape A resumed threads); failure path: none | |
| `fleet stream ended without a terminal event` (D15 INCOMPLETE) | `src/conductor/runner.py:241`, status set `src/conductor/outputs.py:344` | agy (any stream fleet) | absence of a terminal/result event in stdout stream | none: unit tests only: shipped in the $0 G wave 2 (`docs/RESET-2026-09.md:586`); the vendor observation is `docs/research/2026-09-04-research-headless-fleets.md` (opencode) | |
| `fleet reported: <error>` on exit 0 | `src/conductor/runner.py:237` | all | result envelope `is_error`/subtype | live throughout RESET receipts | |
| `read dispatch moved bytes` (deliverable-only exemption) | `src/conductor/runner.py:296-306`, exemption `:547` | all, agy especially | git diff of the worktree before/after | live origin cited in code (agy read mode wrote bytes, 2026-09-03); `docs/research/2026-09-06-live-probe-tool-deny-non-claude.md:130` (2026-09-06) notes config files trip it | no dated doc for the check firing under today's code |
| `read dispatch returned no answer` | `src/conductor/runner.py:311-313` | cursor especially | exit code + absence of `answer_path` | none at all as a dated doc (code cites a live cursor incident: 15K output tokens, no answer) | |
| `write dispatch moved no bytes` | `src/conductor/runner.py:298-300` | all | git verdict `no_op` | live in RESET release receipts | |
| working tree vanished | `src/conductor/runner.py:247-249` | all | git verdict `vanished` | found by cold review, fixed with unit tests: `docs/research/2026-09-07-f15-shape-c-fixes.md`, `docs/research/2026-09-07-consumer-core-guard-audit.md` (2026-09-07) | none: unit tests only |
| deliverable missing/empty/unparsable/schema-mismatch | `src/conductor/runner.py:612-708`, schema `:499`, failure `:287` | all | bytes of the declared file in the worktree | E1 deliverable used live in `docs/research/2026-09-07-consumer-anti-slop.md` (2026-09-07): held | failure branches: unit tests only |
| deliverable path unsafe (symlink / outside worktree) | `src/conductor/runner.py:569-599` | all | lstat + resolve of the path | none: unit tests only (W5, shipped $0 in v0.61.0) | |
| `verdict invalid: ...` (checklist parse) | `src/conductor/runner.py:2230-2237` | all with `--verdict` | the fleet's answer text vs the criteria | none at all | |
| `budget cap hit; process group killed` | `src/conductor/runner.py:2096-2101` (watcher `:1988`) | claude, cursor | watcher's live usage poll of the fleet's own ledger | live: `docs/RESET-2026-09.md:324` (Grok $0.02 over cap), `:481` (fix lane cut off $0.07 over) | |
| breaker kill | `src/conductor/runner.py:2102-2104` | all | tool-call/turn counters off the stream | none: unit tests only | |
| gate / clean gate red, gate interrupted | `src/conductor/runner.py:2380-2444`, `_clean_gate:1125` | all | exit code of the gate re-run in a scratch worktree | live: `docs/RESET-2026-09.md:110`, `:420` (clean gate red, commit undone) | |
| test surface changed under `forbid` | `src/conductor/runner.py:2251-2256`, `:2453` | all | tracked test-file digests before/after | none: unit tests only | |
| reproduce gate: `not-reproduced` blocks the commit | `src/conductor/runner.py:1329-1453` | all fix lanes | transplanted test run on the base commit | live: `docs/RESET-2026-09.md:213`, `:244`, `:512` (`reproduce: reproduced`); the blocking branch live at `:667` | |
| parse failure after the spend (D9) | `src/conductor/runner.py:2960-2990` | all | malformed fleet stdout | none: unit tests only (G wave 2, $0) | |
| `include: <p> is tracked` (DispatchRefused) | `src/conductor/runner.py:1561` | all | git index of the cwd | none: unit tests only | |
| pre-dispatch block (cancel / stop / unpriced ledger) | `src/conductor/mission.py:4786-4800`, `:5004`, `:5061` | all | prior lane receipts + ledger state | live: cap/stop cases in RESET; ledger blocker unfired live | |
| resume not applied across lanes (fleet differs / no session) | `src/conductor/mission.py:5011-5028` | all | upstream lane's recorded `session_id` and fleet | happy path live (Shape A); refusal paths: unit tests only | |
| `_trusted_lane` refuses a receipt (name, plan child not rolled up, human artifact) | `src/conductor/mission.py:3699-3756` | all | receipts on disk vs `result.json` | none: unit tests only | |
| child plan digest re-hashed at launch | `src/conductor/mission.py:4370-4396` (`_child_digest:3509`) | n/a (operator artifact) | sha256 of the child mission file | none: unit tests only (D2, $0 wave) | |
| land: attestation chain must be `verified` | `src/conductor/land.py:258-264` | all | signed statement chain vs `result.json` | none: unit tests only | |
| land: branch tip must equal the receipt's `tip_sha` | `src/conductor/land.py:386-397` | all | git rev-parse in the lane's repo vs receipt | none: unit tests only (D6, $0 wave) | |
| land: not the same repository / shares no history | `src/conductor/land.py:411-451` | all | `same_repo`, `merge-base` | fired live on the first `land` (F12, `docs/RESET-2026-09.md:757`, 2026-09-07) under the older descent rule | |
| land: refuses inside a lane env (`CONDUCTOR_LANE`) | `src/conductor/land.py:399-402` | all | env var stamped on every spawned fleet process | none: unit tests only | |
| land: dirty / mid-merge / on the branch itself | `src/conductor/land.py:416-428` | all | `git status`, `MERGE_HEAD` | live-adjacent (F7/F12 landings) | |
| salvage: kept worktree is not a worktree of the lane's repo | `src/conductor/salvage.py:200-203` | all | `_same_repo` | none: unit tests only | |
| salvage: cannot rebuild setup/includes/ports/env | `src/conductor/salvage.py:212-217` (`_unreconstructable:268`) | all | producing attempt's snapshot | none: unit tests only (D16/D17, $0 wave) | |
| salvage: worktree changed while being gated | `src/conductor/salvage.py:276-283` | all | sha256 of `diff_since` before/after the gate | none: unit tests only | |

`fleets.py` `DispatchRefused` sites (`:364`, `:411`, `:638-960`) are all spec/argv validation before any fleet runs: no live-fleet evidence: so they are out of scope here except `include` (above).

## No live receipt, ranked by drill cost

1. **`read dispatch returned no answer`**: cheapest: one cursor or agy read lane, effort low, prompt "reply with nothing at all; produce no final message." Cents.
2. **`agent '<n>' not applied`**: claude read lane, `--agent` naming a persona with a `tools` list that the harness will not honour (e.g. request a tool that does not exist), then read the init event. One cheap Haiku turn.
3. **`restricted mode not enforced`**: needs a negative: run a claude read lane *without* `--restricted` and assert the check would have fired, or receipt the unrestricted 28-tool init beside the restricted 25-tool one already in the 2026-09-07 probe. Near-free.
4. **`verdict invalid`**: read lane with `--verdict` and a prompt that answers in prose instead of the checklist ("ignore the format, write a paragraph"). One cheap turn.
5. **`fleet stream ended without a terminal event` (D15)**: agy read lane with a short timeout or `--max-tool-calls` set so the process group is killed mid-step; assert `fleet_status: incomplete` and a green gate still fails the lane.
6. **`read dispatch moved bytes`**: agy read lane, prompt "create scratch.txt with the word hi." Cheap, and re-confirms the 2026-09-03 finding under current code.
7. **deliverable failure branches**: read lane with `--deliverable` plus a JSON schema and a prompt to write prose instead of JSON; hits missing/empty/unparsable/schema-mismatch in one run.
8. **taint hook digest fired, not only held**: needs `taint_shell: allow` (so a shell exists) plus a prompt to rewrite `.agents/hooks.json` via a shell redirect rather than an edit tool; the only way to reach `taint hooks modified` live.
9. **write-lane `permission denied`**: needs a write lane deliberately denied a tool it must use; more setup, real spend.
10. **land / salvage / plan-digest refusals**: no fleet spend at all, but they need a finished mission with receipts on disk; cheapest as a post-hoc drill on an existing mission directory rather than a new fleet run.
