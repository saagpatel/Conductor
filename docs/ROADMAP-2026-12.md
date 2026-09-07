# conductor: Phase G, hardening from the outside review

Written 2026-09-07, the day Phase F closed (`docs/ROADMAP-2026-11.md`) and GPT-6 Astra's peer
review came back (`docs/review/2026-09-07-astra-notes.md`, verified by the lead in
`docs/review/2026-09-07-lead-verification.md`: 23 defects and 6 weaknesses checked, all
standing, seven worse than written). The review's verdict, which the lead shares: keep the
design, harden it before extending autonomy. Conductor applied "a fleet's word is never
evidence" to code and gates but not yet to approval, artifact identity, or its own enforcement
hooks.

How it is built: the lead builds directly with Opus 5 subagents, one worktree and branch per
package, merged in series, gated on the merged tree, one release per wave. No fleet missions:
the operator asked for speed, and the fixes are known counterexamples with tests, not specs to
explore. Fleet spend for the phase is therefore zero on the ledger; the cost is subagent tokens
and lead time.

Standing constraints carry over unchanged from Phase F. One new operator decision (2026-09-07):
**tainted lanes deny shell execution by default**; `taint_shell: allow` is a documented opt-in
that restores the old prefix list as a discouragement, not a boundary.

## Shipped since this was written

| item | version | shape | cost |
|---|---|---|---|
| G wave 1: trust boundaries (D3-D8, D19, W1, W2, W5) | 0.61.0 | lead with four Opus subagents in worktrees, merged in series, gated on the merged tree | $0 |
| G wave 2: spend and lifecycle (D1, D2, D9-D15), plus salvage, collisions, the build prompt, and two README claims from wave 3 | 0.62.0 | lead with four Opus subagents in worktrees, merged in series, gated on the merged tree | $0 |
| G wave 3: measurement (D18, D20, D21) | 0.63.0 | lead with one Opus subagent in a worktree, gated on the merged tree | $0 |
| Live taint shell-deny drill; denied_calls counted from tool errors | 0.64.0 | one tainted Antigravity read dispatch on a scratch repo; lead fix | $0.06 |
| W3 resume authenticates artifact bytes; W4 deliverable captured after the gates, tree re-judged after teardown | 0.65.0 | lead with two Opus subagents in worktrees, merged in series, gated on the merged tree | $0 |
| Live drills for six fail-closed checks; Shape C as shape a --opus-review | 0.66.0 | six scratch-repo dispatches; lead build for the launcher option | $0.19 |
| W8 wall block: occupied, critical path, lead seconds, stretch; W10 notify process group and bounded output; drill pass two | 0.67.0 | lead with two Opus subagents in worktrees, merged in series, gated on the merged tree; three scratch-repo dispatches | $0.08 |
| Evidence map as the build lane's deliverable; W7 precision labels landed via shape a --opus-review | 0.68.0 | Shape A via the launcher with --opus-review, landed by conductor land | $5.45 |
| W6 price basis and outstanding caps on receipts; W9 export scope declared and the D13 inventory; drill pass three; scrub guard accepts its own redaction | 0.69.0 | Shape A via the launcher with --opus-review, landed by conductor land; lead fix; four scratch-repo dispatches | $17.41 |
| Ledger in-flight figures live in pause.json and running.json; settings digest check for Claude write lanes; land dry-run flag | 0.70.0 | lead with two Opus subagents in worktrees, merged in series, gated on the merged tree; one scratch-repo dispatch | $0.07 |
| W11 undispositioned sharpened, README error kinds pinned to KINDS; 0.69.0 and 0.70.0 receipts read live on the mission | 0.71.0 | Shape A via the launcher with --opus-review, landed by conductor land | $5.30 |
| F17 launcher raises a warned lane's cap to the forecast p80 and records the caps block; plan-digest refusal drilled live | 0.72.0 | lead with one Opus subagent in a worktree, gated on the merged tree; one scratch plan-lane mission | $0.04 |
| F18 KINDS is the check order of error_kind; F19 a fix lane that wrote only its deliverable skips the reproduce gate, reproduce refusals get their own kind | 0.73.0 | Shape A via the launcher with --opus-review, build lane landed by conductor land after the fix lane failed on F19; lead fix | $4.14 |
| undispositioned guard applied on the no-fix-lane branch too | 0.74.0 | lead fix | $0 |
| Review item 1: current support matrix at the README entry | 0.75.0 | lead, no fleet | $0 |
| Review item 2: cost per landed item on the ledger report | 0.76.0 | lead, no fleet | $0 |
| F20 one effect inventory (spend.effects) read by resume, report, spend, and export | 0.77.0 | Shape A via the launcher with --opus-review, landed by conductor land | $7.64 |
| F21 slice 1: graph policy extracted to graph.py, D3 and D4 pinned | 0.78.0 | Shape A via the launcher with --opus-review, landed by conductor land | $8.45 |
| F21 slice 2: approval consumption extracted to approvals.py, D1 and D2 pinned | 0.79.0 | Shape A via the launcher with --opus-review; build landed by conductor land, fix lane salvaged by hand | $16.61 |
| F21 slice 3: attempt lifecycle extracted to attempts.py, paid-once and never-trust-a-rehearsal pinned | 0.80.0 | Shape A via the launcher with --opus-review, landed by conductor land | $16.05 |

Status 2026-09-07, end of the sitting: all three waves shipped (0.61.0, 0.62.0, 0.63.0). Of the
review's 23 defects, every one is fixed on the tree with a test that fails without it; of the ten
weaknesses, W1, W2, W5 are fixed, W3 (resume authenticates paths, not bytes), W4 (deliverable copy
before the gates, teardown after capture), and W6 to W10 are open and carry into Phase H as
record. Verified live 2026-09-07 (0.64.0, `docs/research/2026-09-07-live-probe-taint-shell-deny.md`):
a tainted Antigravity read lane on a scratch repo had `run_command` and a write to its own
`.agents/hooks.json` denied by the hook on bytes, digests untouched, preflight naming all 35
matchers, six cents. The drill also found `denied_calls` counting the model's quoted error text;
fixed the same release. Phase H item 1 is done. Item 2 shipped as 0.65.0: W3 (artifact digests on
every receipt, re-hashed on resume) and W4 (deliverable captured after the gates, tree re-judged
after teardown with `cleanup_required`). Item 3's worklist is
`docs/research/2026-09-07-fail-closed-check-inventory.md`; its first pass (0.66.0) fired three
checks live and settled three more. Item 4 shipped in 0.66.0 as `shape a --opus-review`. Item 5 (W8) and
W10 shipped in 0.67.0, with a second drill pass that fired the taint digest and Cursor's empty
answer. Item 6 shipped in 0.68.0 as the build lane's `evidence.json`, and the first mission through the
full shape (evidence map, Opus reviewer, W8 wall figures) landed W7. W6 and W9 shipped in 0.69.0 through the same shape (price basis and outstanding caps on
receipts; export scope declared over the D13 inventory), with item 3's third pass: the permission
denial path fired on a plan-mode read lane, the land and salvage refusals fired post-hoc on W7, and
a project deny rule was shown to be no boundary for a write lane. Every review weakness is closed
or carried as a stated limit. Both follow-ons shipped in 0.70.0: the ledger's in-flight figures are
written to `pause.json` and refreshed in `running.json`, and a Claude write lane that rewrites its
own `.claude/settings*.json` fails as kind `settings` (drilled live). 0.71.0 (W11) sharpened `undispositioned` and pinned the
README kinds block, and read every 0.69.0 and 0.70.0 receipt live on the mission itself
(`docs/research/2026-09-07-consumer-live-receipts-0-70.md`). Both closed in 0.72.0: the launcher raises a
warned lane's cap to the forecast p80 and records every cap's basis on the mission file (F17), and
the plan-digest refusal fired live on a scratch plan lane. Every fail-closed check in the inventory
that a finished mission or a scratch repository can reach now has a live receipt. 0.73.0 made
`KINDS` the true check order (F18) and, from that mission's own fix lane failing after three
NO_FINDINGS reviews, fixed the reproduce gate to skip a deliverable-only write and gave its refusals
kind `reproduce` (F19). Five review items remain, approved in order: the README support matrix
(shipped 0.75.0), cost per landed item on the report (shipped 0.76.0, read live at $1.99 over
today's four mapped missions), one effect inventory shared by `mission.py`
and `report.py` (shipped 0.77.0 as `spend.effects`, F20), `mission.py` extracted by invariant with
signatures intact (F21, three slices, one release each: graph policy 0.78.0, approval consumption
0.79.0, attempt lifecycle 0.80.0), and non-code artifact acceptance with a before/after validator
(F22, mission running).

## Waves

| wave | scope | review items |
|---|---|---|
| 1 trust boundaries | taint to a fixed point over the whole lane graph; resolver validated and fenced like collate; shell denied on tainted lanes with a tamper digest on the hook files and a quoted hook path; deliverable capture refuses symlinks; land pins the receipted SHA and the repository and runs golden as the merged tree's subprocess; attestation chain bound to mission and index with a completeness state; export reports a missing chain as missing | D3-D8, D19, W1, W2, W5 |
| 2 spend and lifecycle | one answer launches one planner's child, global stop first, approval bound to the child digest; lock publication atomic and owner-bound; retry and cancel through one pre-dispatch guard; auxiliary runs attributed and superseded resolvers kept; parser type guards with a durable receipt on a parse crash and a settle boundary; finite caps and rates; a truncated Antigravity stream fails | D1, D2, D9-D15 |
| 3 measurement and docs | disposition dedupe by finding; gate-ran cohorts in the cap-loss summary; salvage gates the producing attempt and hashes current bytes; replay compares the requested routing; git-quoted paths in collisions; README claims (Codex first example, reproduce wording); build prompt names the gate flags | D16-D18, D20-D22, idea 7 |

After the waves, a small Phase H from the review's section 8 and the lead's kept ideas:
first-run drills for every fail-closed check, Shape C as a launcher option, the wall-clock
model narrowed to critical path, an evidence map before any paid spec-fidelity stage.
