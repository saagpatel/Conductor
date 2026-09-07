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

## Waves

| wave | scope | review items |
|---|---|---|
| 1 trust boundaries | taint to a fixed point over the whole lane graph; resolver validated and fenced like collate; shell denied on tainted lanes with a tamper digest on the hook files and a quoted hook path; deliverable capture refuses symlinks; land pins the receipted SHA and the repository and runs golden as the merged tree's subprocess; attestation chain bound to mission and index with a completeness state; export reports a missing chain as missing | D3-D8, D19, W1, W2, W5 |
| 2 spend and lifecycle | one answer launches one planner's child, global stop first, approval bound to the child digest; lock publication atomic and owner-bound; retry and cancel through one pre-dispatch guard; auxiliary runs attributed and superseded resolvers kept; parser type guards with a durable receipt on a parse crash and a settle boundary; finite caps and rates; a truncated Antigravity stream fails | D1, D2, D9-D15 |
| 3 measurement and docs | disposition dedupe by finding; gate-ran cohorts in the cap-loss summary; salvage gates the producing attempt and hashes current bytes; replay compares the requested routing; git-quoted paths in collisions; README claims (Codex first example, reproduce wording); build prompt names the gate flags | D16-D18, D20-D22, idea 7 |

After the waves, a small Phase H from the review's section 8 and the lead's kept ideas:
first-run drills for every fail-closed check, Shape C as a launcher option, the wall-clock
model narrowed to critical path, an evidence map before any paid spec-fidelity stage.
