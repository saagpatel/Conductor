# Peer review notes: conductor at 0.60.0

Reviewer: GPT-6 Astra, operator-run in the Codex app, 2026-09-07.
Baseline: `67a47f36cf30d5e2dff013564f0f4ac222fb1860`, `feat/conductor-v1`.
Brief: `docs/review/2026-09-07-peer-review-brief.md`.

## 1. What conductor is, in the reviewer's words

Conductor is a local execution controller around independently changing coding-agent CLIs.
A mission declares work, dependencies, budgets, and review policy. A lane produces an attempt,
not a trustworthy assertion: conductor observes execution, prices usage, examines changes,
runs checks, and retains artifacts. The lead owns the specification and acceptance decision.
A successful mission and a shipped release are different outcomes.

The useful abstraction is a bounded work attempt with explicit inputs, permissions, lineage,
outputs, and evidence. Fleet-specific adapters are necessary because permissions, terminal
events, pricing, and resume are not interchangeable. Worktrees separate source changes;
receipts support recovery. Human inputs, planner children, auxiliary judgments, and salvage
now make the lifecycle more diverse than an ordinary lane (`mission.py:4440`,
`mission.py:5873`, `salvage.py:117`).

Execution success, artifact acceptance, and authorization for the next effect should be
separate concepts. Bytes and gates support acceptance; neither supplies authorization.
The findings below concern those distinctions through concurrency, recovery, and landing.
Unless otherwise qualified, source citations refer to files under `src/conductor/`.

## 2. First-principles assessment

**Keep the central design; strengthen it before extending autonomy.** Keep first-party
routing, capability refusals, cold reviewers, clean-test transplants, preserved partial work,
and lead-owned landing. The receipts candidly document costly failures as well as successes
(`docs/RESET-2026-09.md`, A3, C4, E10, and F8). This is a useful operator-led tool, not a
failed architecture that needs replacement.

The main problem is feature interaction, not absent features. `mission.py` combines parsing,
graph policy, scheduling, durable state, accounting, approval, child launch, and reporting.
Ordinary lanes, collates, judges, resolvers, and children do not share one effect lifecycle
(`mission.py:3690`, `mission.py:4518`, `mission.py:5873`). A new invariant on ordinary lanes
therefore does not automatically hold for other paid work. Tests are extensive, but some
encode the implementation's assumptions rather than independently challenging them.

I would incrementally establish four internal boundaries:

1. A validated graph with resolved inputs, taint, capabilities, and review policy.
2. An effect executor with exact approval/cancel checks, attempt identity, spawn, and durable
   terminal outcomes even when parsing or settlement fails.
3. Artifact acceptance binding repository, base, candidate, checks, and output digests.
4. Derived views consuming the same reconciled effect/artifact inventory.

Do not begin with a big scheduler rewrite or a replacement workflow framework. Reproduce the
counterexamples, fix them locally, and extract shared logic as those fixes settle. Normal
operator-led Shape A can remain useful. I would not rely on multi-planner approval, the
tainted-shell egress claim, or branch-name-based landing identity until corrected.

## 3. Defects

Evidence labels: **probe** = in-memory, no dispatch; **source** = concrete control/data flow,
not a live exploit; **receipt** = persisted local evidence inspected. Confidence rates the
finding, not completeness of its proposed fix. P1 covers authorization, trust, artifact
integrity, or material accounting; P2 covers narrower behavior/measurement. No quotas apply.

### D1. One planner answer can launch another planner's child (P1)

**`mission.py:4852-4870`, `mission.py:4082-4089`, `mission.py:4894-4899`.** Resume visits every
successful parked planner, not just the lane named by the answered pause. Stop is recognized
only for the matching child; other planners fall through to launch before the global stop
branch. Multiple planners can complete because ready lanes are submitted and settled after
the first pause (`mission.py:5069-5116`). Consequence: continuing A can launch B, and stopping
A can still launch B. **Confidence 10; source.** Consume an approval for exactly one planner
and child digest; process global stop first. Park each additional planner separately. Test
two planners completing in either order, continue, stop, and restart.

### D2. Approval is not bound to the child bytes checked at park time (P1)

**`mission.py:4089-4101`, `mission.py:3344` (`_plan_check_child`).** Launch reloads the child
file without matching an approved digest or repeating the full child-policy check. Clamping
the remaining budget does not validate the approved task, destinations, or ceiling.
Consequence: changed child content can execute under an answer to an earlier plan.
**Confidence 9; source.** Save a canonical digest and policy summary in the pause; verify them
when consuming the answer. Intentional edits should create a revised plan requiring a new
approval, not silently inherit the old one.

### D3. Taint depends on lane declaration order (P1)

**`mission.py:1503-1530`, `mission.py:877`, `tests/test_pipeline.py:127-144`.** The forward
taint pass assumes later references are rejected, but valid forward dependencies are allowed.
A Cursor consumer declared before an `untrusted_output` source, with `needs: [source]` and
`{{lanes.source.answer}}`, loaded with `tainted=False` in the probe. Reversing only the
declarations correctly raised the Cursor taint refusal. Consequence: equivalent DAGs have
different permissions. **Confidence 10; probe.** Compute taint in topological order or to a
fixed point over the complete validated graph, including resume edges, then validate every
consumer's capabilities.

### D4. The resolver is an unguarded taint sink (P1)

**`mission.py:6424-6438`, `mission.py:510` (`Resolve.spec`), versus collate at
`mission.py:5916`.** Resolver prompts contain candidate patches, but its spec receives no
derived taint. Candidates may themselves be tainted or declare untrusted output.
Consequence: untrusted patch text reaches a fully capable writer, including a fleet refused
for ordinary tainted consumers. **Confidence 9; source.** Derive restrictions from every
actual resolver input and enforce both at load and immediately before dispatch. Share input
selection between validation and execution; a failed candidate's patch is not trusted.

### D5. The Antigravity shell hook does not enforce no egress (P1)

**`fleets.py:143-182`, `README.md:674-680`.** The hook matches shell prefixes, not capabilities.
The generated decision function denied the string `curl example.invalid` but allowed
`command curl example.invalid`, `/usr/bin/curl example.invalid`, and `env curl example.invalid`.
No shell string was executed. Interpreters remain a source-level gap too. Consequence: the
README's no-network-egress claim exceeds enforcement. **Confidence 10; probe.** Do not repair
this by endlessly extending a prefix list. Deny general execution for tainted work unless a
demonstrated capability boundary constrains it; refuse incompatible workloads and state the
supported boundary precisely.

### D6. Land identifies accepted work by a mutable branch name (P1)

**`land.py:280-290`, `land.py:316-327`, `land.py:200`.** The branch is checked in the lane
repository, then resolved/merged by name in the destination. Neither the recorded tip nor
repository identity is bound to that destination ref. Shared history is not identity. The
already-merged shortcut returns success before validation/attestation. Consequence: a moved
branch or same-name ref in a related repository can land different bytes under the original
mission evidence. **Confidence 10; source.** Resolve a fully qualified ref once, compare SHA
and repository identity to an accepted-artifact record, and merge that pinned SHA. Apply the
same checks to already-merged returns; revised salvage needs revised acceptance evidence.

### D7. Valid chain links do not prove a complete mission (P1)

**`attest.py:259-268`, `attest.py:285-319`.** An empty list yields no failed rows and
`all([]) == True`; dropping a suffix preserves all retained predecessor checks. Signed
mission/lane/index fields are not bound to the requested mission and unsigned chain entries.
Consequence: incomplete or substituted evidence can report verified. **Confidence 10;
source and empty-list probe.** Bind identities and expected terminal count/head to a signed
mission manifest. Distinguish verified prefix from complete mission. A terminal manifest
alone cannot detect rollback of the entire directory; retain that limitation unless there
is an independent checkpoint.

### D8. Export calls a missing chain verified (P2)

**`export.py:264-280`.** `chain_invalid` starts false and changes only for an existing bad
file. With no chain, rows are empty and verification is true, contradicting the comment and
E13's documented fix. Consequence: metadata falsely describes absent evidence.
**Confidence 10; source.** Represent missing, malformed, partial, and verified explicitly;
use the same completeness validator as attest. Test missing-file separately from bad JSON
and empty links.

### D9. Invalid structured output can escape after spend (P1)

**`verdicts.py:205`, `verdicts.py:421`, `runner.py:496-499`, `outputs.py:98-103`,
`runner.py:2399-2405`, `mission.py:4445-4449`, `mission.py:4537`.** Pure probes raised
TypeError for list-valued verdict/disposition, AttributeError for a list schema, and
ValueError for NaN usage. Dispatch exceptions escape before normal terminal receipt and
ledger addition; the mission catches a crashed lane, not an accounted spawned obligation.
Settlement parsing can also raise outside the worker boundary. Consequence: malformed JSON
types can lose terminal/accounting evidence after paid execution. **Confidence 10 for parser
failures, 9 for lifecycle consequence; probe/source.** Validate before coercion/membership;
make parser and settlement errors durable outcomes preserving known cost or explicit unpriced
spend. Returning a zero-cost failure is not sufficient.

### D10. Lock publication can be mistaken for staleness (P1)

**`mission.py:3225-3244`, `mission.py:3265-3286`, `mission.py:3188`.** Exclusive creation
publishes an empty file before JSON is written. A contender reads `{}`, finds no live PID,
unlinks, and acquires its replacement while the original owner continues. Reclaim also lacks
an identity-bound compare before unlink. Consequence: duplicate launch/resume and spend.
**Confidence 9; source interleaving, not a live race reproduction.** Use ownership-safe
locking/publication, reject unreadable content as proof of staleness, and verify an owner
token on release/reclaim. Test with barriers between create/write, not timing luck. This
does not require atomic budget reservation or age-based reclamation.

### D11. Retry skips a newly unverifiable ledger check (P1)

**`mission.py:4647-4652`, `mission.py:4689-4709`.** The outer attempt loop checks the ledger;
same-attempt retry calls dispatch directly. If the failed attempt makes spend unverifiable,
a finite remaining estimate can still allow more paid work. Consequence: retries bypass the
mission's own accounting refusal. **Confidence 9; source.** Place authorization, budget,
stop, and cancel checks at the common pre-dispatch boundary, covering retries and auxiliary
jobs rather than selected outer loops.

### D12. Queued early-cancel lanes can still spawn (P2)

**`mission.py:5069-5070`, `mission.py:4647-4652`, `tests/test_best_of_n.py:103-115`.** All ready
jobs are submitted, including those beyond worker capacity. A queued worker does not test
its lane event before dispatch; cancellation is observed after spawn/polling. The test
accepts a fast successful fleet despite a pre-set event. Consequence: paid work starts after
the winner despite the no-new-work expectation (`README.md:1514`). **Confidence 10;
source/test.** Cancel queued futures and check immediately before spawn, writing a non-spawned
outcome. Test more ready jobs than slots and a fast cancelled job.

### D13. Auxiliary attempts lack the ordinary durable accounting path (P1)

**`mission.py:5873-5896`, `mission.py:3690-3700`, `mission.py:5325`, `report.py:235-246`.**
Live auxiliary dispatches omit normal lane/mission metadata. Later joins repair collates,
but the report omits the resolver join. Recovery discovers auxiliaries through saved
summaries; previous collates are retained, previous resolvers are not. Rerun overwrites
resolve, and a crash before summary persistence can orphan paid auxiliary work from the
mission's recovered run set. Consequence: mission attribution/recovered budget can omit
real spend even with a global run receipt. **Confidence 9; source.** Journal effect identity
before spawn, retain superseded attempts, and reconcile run receipts before re-dispatch.
Share that inventory across resume, report, spend, and export.

### D14. Non-finite caps and invalid price rates are admitted (P2)

**`mission.py:612-613`, `prices.py:143-154`.** Pure loading accepted NaN and infinity mission
caps alongside a finite explicit lane cap. Price overrides convert rates without finite,
non-negative validation. Consequence: comparisons/estimates stop representing the configured
finite aggregate budget. **Confidence 10 for cap acceptance, 9 for rates; probe/source.**
Centralize numeric validation, exclude booleans, and cover cache rates, ceilings, timeouts,
and backoffs. This proves invalid budget acceptance, not unlimited observed provider spend.

### D15. A truncated Antigravity stream is not a terminal failure (P2)

**`outputs.py:292-312`, `tests/test_budget.py:198-214`, `tests/test_dispatch.py:50-67`.** A
stream ending at a step returns parsed true, partial usage, and no error. A writer can
otherwise pass on exit zero, valid changed bytes, and a green gate without an answer.
Consequence: missing fleet terminal status can read as complete success. **Confidence 9;
source/test.** Preserve partial usage while marking completion absent. Test a zero-exit
truncated writer, not only conductor-killed streams that have another failure signal.

### D16. Salvage selects the wrong repository/attempt contract (P2)

**`salvage.py:132-158`, `salvage.py:184-212`.** The worktree is from the final attempt, but
repository identity uses mission cwd and gate/timeout use the first attempt snapshot.
Includes, setup, ports, and lane environment are not reconstructed by the transplant calls.
Consequence: valid cross-repo salvage is refused or fallback work is judged under another
contract. **Confidence 10 for selection, 9 for environment; source.** Resolve the producing
attempt's effective repository and validation contract; explicitly refuse unsupported
environment reconstruction instead of substituting another one.

### D17. Salvage receipts can hash different bytes from those gated (P2)

**`salvage.py:169-192`, `salvage.py:214-225`.** Diff/digest come from the old lane receipt;
the transplant gates read today's kept worktree. A post-run repair can pass with the
pre-repair digest still in the salvage result. Consequence: acceptance evidence identifies
the wrong candidate. **Confidence 10; source.** Snapshot current bytes once, derive the diff
and both gates from that snapshot, and retain the original digest only as lineage.

### D18. Golden replay masks requested routing changes (P2)

**`golden.py:868-904`, `golden.py:938-949`, `README.md:2543`.** Replay uses the recorded fleet
and returns recorded fleet/model/mode/effort without comparing them with the newly requested
spec. Projection therefore sees the old route. Consequence: routing regression can remain
golden-green. **Confidence 10; source.** Compare normalized requested dispatch contracts,
including relevant caps/permissions, and ensure expected attempts were consumed. Intentional
contract migrations should be explicit, not silent backfills.

### D19. Land's separate golden step uses the caller's implementation (P2)

**`land.py:106-120`, `land.py:164`.** The already imported golden module receives fixture
paths in the merged tree; merged code is not imported. Consequence: conductor landing its
own parser/mission/golden changes tests new fixtures using old implementation in this step.
A correctly rooted pytest gate does not make this separate claim true. **Confidence 10;
source.** Run merged code in a fresh interpreter with import-root verification and replay's
no-live-effects guard active.

### D20. Dispositions can count findings repeatedly or count nonexistent indices (P2)

**`report.py:229-234`, `report.py:707-730`.** Fix histories are concatenated and counted by
reviewer name without uniqueness or finding-index checks. Matching confidence does not gate
the count. Pure report probe: one finding plus three copies of its fixed disposition yielded
fixed 3, precision 1.0, corrected rate 3.0. Consequence: the quality metric exceeds its
denominator. **Confidence 10; probe/source.** Join mission/reviewer-attempt/finding-index;
preserve history but choose explicit final state. Report duplicate, conflicting, unmatched,
and missing entries. A fixer declaration also needs artifact/check evidence before it is
called a verified repair.

### D21. Cap-loss summary calls an unrun gate passed (P2)

**`report.py:779`, `report.py:992`, `tests/test_report.py:802-830`.** Generic non-failure when
no gate exists is reused for “capped after their gate passed”; the test pins that case as
green. Consequence: headroom advice counts not-checked as passed. **Confidence 10; source/test.**
Separate acceptable-without-gate from gate-ran-and-passed; show passed/failed/not-run cohorts.
Do not adjust rule 10 from the mixed cohort.

### D22. Collision parsing drops Git-quoted filenames (P2)

**`collisions.py:16-37`.** Only `diff --git a/` headers are recognized. Git quotes tabs and
often non-ASCII bytes; a quoted-header probe returned no paths. Consequence: overlap counts
and resolver hotspots can omit shared files; actual merge-conflict detection is a separate
partial safety net. **Confidence 10; probe.** Prefer NUL-delimited Git path inventories;
decode Git quoting for persisted patches. Cover renames, Unicode, tabs, and non-UTF-8 paths.

### D23. The anti-slop report double-counts an attempt (P2)

**`docs/research/2026-09-07-consumer-anti-slop.md:36-40`; receipt
`$CONDUCTOR_HOME/missions/20260907T101347Z-f10-anti-slop-f15-doc/result.json`.** The first
Gemini attempt, $0.096866, is added to its cumulative lane cost $0.200380 as though the
latter were the rerun. Actual rerun: $0.103514. Edit $0.5563568 + Opus $0.774933 + both
Gemini attempts = **$1.531670**, not $1.63. RESET and Phase F repeat the inflated total.
Consequence: experiment-cost prose is misleading. **Confidence 10; receipt.** Correct
attempt-versus-cumulative arithmetic and derive totals from unique run IDs. This is a docs
bug, not evidence that the current ledger double-counts this mission.

## 4. Weaknesses and risks

These qualify the threat model and claim ceiling; they are not additional live exploits.

**W1. Worktrees are not hostile-code sandboxes.** `worktrees.py:34`, `fleets.py:124-182`,
`runner.py:1749-1757`: fleets share the user's OS identity and can have access beyond tracked
source. Hooks sit in a writable worktree, and includes/setup follow preflight. Loaded policy
is not immutable policy. Confidence 10. State a cooperative-agent threat model; reserve
hook/config paths, bind their content identity at execution, and keep sensitive material out
of exposed inputs. Those measures do not constrain an unrestricted same-user process.

**W2. Restriction evidence has absence and command-execution gaps.**
`tests/test_restricted.py:192-198` accepts no init event; `fleets.py:225` emits an unquoted
hook command path. Named preflight matchers do not prove the command ran correctly.
Confidence 9. Distinguish configured, loaded, exercised, and enforced. Drill missing init,
malformed files, command failure, paths with spaces, and changed tools. No-init acceptance
is an explicit tested tradeoff, not a hidden undocumented refusal I assume exists.

**W3. Resume trust is not complete artifact authentication.** `mission.py:3456`,
`mission.py:3491-3595`: availability/Git checks do not bind every answer/deliverable digest;
Git-unavailable can intentionally retain trust to avoid paying again. Confidence 9. Preserve
do-not-repay, but hold verification-pending work instead of blindly consuming it or rerunning
it. Authenticate accepted outputs when resuming and retry only the read-only identity check.

**W4. Gate, commit, deliverable, and teardown snapshots can differ.**
`runner.py:2126-2160`, `runner.py:2222`, `runner.py:2289`, `runner.py:2383-2398`: outputs are
copied and commits made before gates; final diff and teardown happen later. A mutating gate
or teardown can change the actual artifact after an earlier judgment. Confidence 9. Gate
and accept an immutable candidate, require source stability, and expose cleanup-required
state. `LaneResult.buildable()` already refuses dirty lineage: I am not claiming ordinary
downstream builds necessarily accept the dirty result.

**W5. Deliverable capture follows post-run symlinks.** `runner.py:571-572`, `runner.py:2137`:
initial path validation does not cover a symlink installed later; `is_file`/`copyfile` follow
it. External bytes can enter the receipt as the declared product. Confidence 10. Revalidate
confinement at capture, reject symlinks or resolve strictly inside the worktree, and bind
validation to the opened/copied bytes. This is an artifact/export boundary, not a claim
that conductor grants the fleet otherwise unavailable OS access.

**W6. Estimates are not billing or hard aggregate limits.** `prices.py:72-105`,
`budget.py:225`, `mission.py:4647`, `README.md:2396`: rate prefixes do not establish Cursor
variant/long-context billing; concurrent lanes intentionally share a soft remainder; delayed
usage limits watcher precision. Confidence 9, source/datable local notes, not live pricing.
Receipt the price basis and native-versus-estimated cost, show possible outstanding caps,
and avoid a universal one-response overshoot claim. Keep the rejection of atomic reservation.

**W7. Precision is conditional fixer agreement.** `report.py:693-730`,
`docs/research/2026-09-07-f9-shape-b-c.md:60-79`: only selected disposition-bearing missions
contribute; hand fixes and unparsed outcomes are incomplete; a builder may adjudicate its own
review. F9's verifiers were also Opus. Confidence 10. Label the selection and denominators;
retain missing/failed cohorts and obtain a small operator-adjudicated sample before routing
or calibration decisions. Agreement is useful, not independent truth.

**W8. Wall figures are not additive elapsed time or complete utilization.**
`mission.py:5433-5447`, `report.py:529-549`: ordinary lane/gate sums omit auxiliary and child
phases, and gates overlap lane duration. Configured concurrency and pauses affect busy.
Confidence 9. Label lane-work measures; add critical-path and integration intervals only
where needed. Do not infer release lead time from mission timestamps or sum overlapping
columns as a wall-clock decomposition.

**W9. Export integrity is not completeness.** `export.py:203-224`, `export.py:282-283`,
`export.py:140-152`: ordinary attempt and chain discovery is not one recursively complete
effect inventory. A digest manifest proves included files unchanged, not omitted work absent;
scrubbing DSSE payloads intentionally invalidates original signatures. Confidence 9. Declare
scope/omissions, use D13's shared inventory, and do not export the HMAC key to create apparent
independent verifiability.

**W10. Notification cleanup/output bounds are weaker than gate handling.** `notify.py:38-66`
uses shell execution, captured output, and timeout without `verify.run_tests` process-group
cleanup. Descendants can outlive the shell; output capture is not tightly bounded.
Confidence 9, source only. Bound output and terminate the process group while keeping
notification failure non-fatal. No notification or macOS timeout hang was reproduced here.

## 5. Simplifications

1. **Merge effect accounting, not lane kinds.** `mission.py:3690`, `report.py:235`,
   `export.py:203`, and `spend.py` independently discover paid work. A shared effect inventory
   removes omission paths while human inputs and child missions retain distinct semantics.
   Preserve old receipts through versioned adapters; deleting compatibility breaks recovery.
2. **Separate precise predicates.** Acceptance without a gate, actual gate success,
   buildability, authentication, and authorization are different. D6/D7/D21 show the cost of
   convenient shared booleans. Keep CLI summaries derived from explicit states.
3. **Keep shapes as policy over the executor.** Shape C can be a small option. Defer a new
   prose launcher until repeated consumers establish a contract. Each new path otherwise
   adds validation/resume/replay obligations (`shape.py`, D18).
4. **Extract repaired slices from mission.py.** Graph policy, then approval consumption,
   then attempt lifecycle. Do not split by line count or break call signatures. A large
   mechanical rewrite spends review effort without establishing D1-D4/D13's invariants.
5. **Separate current policy from historical evidence.** Keep research and RESET; add a
   compact current support matrix at the README entry point. Historical model advice and
   old proposals should not look executable (`README.md:7`, Phase F opening/shipped sections,
   AGENTS fleet policy). No historical receipt needs deletion.
6. **Do not add routing heuristics before measurement.** Forecast stays advisory. Mechanical
   ranking remains a disclosed shortlist, not correctness: F9's smaller/cheaper candidate
   lost on spec fidelity despite passing the gate. No new automatic shape picker is needed.

## 6. Test suite and golden fixtures

All source and test modules were read in full. The suite has valuable real-Git, process,
interruption, malformed-output, and lifecycle coverage. Keep xdist/loadgroup and external
basetemp. RESET's F3/F5 timer-to-in-lane synchronization fixes are the correct response to
load flakes, not a serial global gate or machine-wide semaphore.

The gap is interaction coverage and independent expected results, not raw test count.

| Contract | Regression to add |
|---|---|
| Exact approval | Two planners; answer one; stop launches none; edited child needs a new answer |
| Graph taint | Permute equivalent declarations; include resolver and resume edges |
| Account every spawn | Inject failures after spawn, parse, receipt write, settlement, auxiliary completion |
| One lock owner | Barrier-controlled partial publication and stale replacement |
| Pinned accepted bytes | Move branch; same-name related repo ref; repair kept tree after original receipt |
| Complete evidence | Empty/truncated/swapped chain; missing export chain; superseded auxiliary run |
| Cancel before spend | More ready lanes than slots; pre-set event; fast losing process |
| Honest metrics | Duplicate/unknown indices, multiple fix histories, no gate, unpriced attempt |
| Replay observes intent | Change requested fleet/model/mode/cap/taint; assert a detected difference |

Fake fleets prove state transitions, not provider permissions. F12's recorded planner failure
is the example: a fake read lane could write the deliverable that real Claude plan mode
refused (`docs/ROADMAP-2026-11.md`, “A read lane's deliverable has never worked live on
Claude”). Add separately authorized small adapter drills keyed by CLI version/capability.
Ordinary unit and golden checks must never reach paid adapters.

All eleven golden fixtures replayed successfully. Forty printed lines were informational
unknown-version/prompt-version notes, not forty failures. They exercise historical stream
parsing, scheduler and snapshot rendering; they do not prove current vendor behavior,
confinement, or current launcher prompt behavior merely because a hash drift is noted.
D18 is a routing coverage defect; making version notes non-fatal is explicit policy.

F8's receipt says replay previously paid real auxiliary judges and invoked notify
(`docs/RESET-2026-09.md`, F8). Keep a deny-by-default replay environment and assertions that
every live effect path is unreachable. Extend fixtures for missing interactions, not merely
more successful transcripts. Intentional semantics changes need migration tests, not
re-recording historical evidence until the gate turns green.

## 7. Prompts, docs, and the model notes

Keep no quotas, consequence thresholds, cited findings, explicit NO_FINDINGS, whole-answer
delivery, scope, compatibility, and test-policy instructions. No evidence here warrants
provider/effort changes from remembered model reputations.

- Apply the review contract consistently to auxiliary judgments. Default collate at
  `mission.py:201` and rank at `mission.py:1265` are not equivalent to Shape A's reviewer tail.
  A forced strongest-candidate contract needs abstention/insufficient evidence; agreement
  under forced choice does not prove either candidate acceptable.
- Reduce irrelevant cues in `mission.py:5797` candidate presentation: stable anonymous IDs
  for quality review, costs only when economics is part of the rubric. Use consistent
  nonce-delimited upstream data, as `_render` does, rather than ordinary fences everywhere.
  This reduces one bias/injection cue, not all authorship inference or prompt injection.
- Give spec items stable IDs and ask for implementation location, acceptance check, and
  unresolved assumptions. F9/F15 show missing items despite positive review. A second model's
  built checkbox is evidence to inspect, not another authoritative gate.

The README is a good feature map but its first example uses paused Codex (`README.md:7`),
its taint text overclaims egress, and reproduce wording implies enforcement before the
fixer edits (`README.md:1203`, `runner.py:2110-2124`). The actual controller checks a
transplant after model work; describe before/after behavior evidence, not guaranteed editing
chronology. The development command should also consistently name external basetemp.

The brief's no-live-receipt assertions are stale against later Phase F evidence.
`docs/ROADMAP-2026-11.md:37-63` and fixtures `f11-unattended-read-notify`,
`e7-human-lane-answered`, `e10-plan-lane-continued` record real foreign-repo unattended work
and scratch human/child flows. They do not establish a successful real-work planner child,
natural scheduler firing, or current human acceptance. The failed core-guard planner is
an adapter-gap receipt, not successful autonomous planning.

Model/pricing reports are dated observations. “Confirmed” in an old report means confirmed
in that sitting, not revalidated here. I did not browse vendor sites or run their CLIs.
Maintain a current per-capability support matrix pointing to actual probes and invalidate
confidence on CLI/config drift; keep old reports as evidence, not present policy.

## 8. The lead's eight ideas

| # | idea | verdict | reason |
|---|---|---|---|
| 1 | First-run fail-closed drills | Keep, broaden | Include bypasses and missing evidence, not only expected denials. Distinguish pure tests, adapter drills, and operational checks. D3-D5 defeat a hook-count-only success. |
| 2 | Shape C launcher policy | Keep, opt-in | F9 supports broader cold review for lifecycle/security/spec-risk work. Three selected commits do not establish a universal module threshold or justify removing the pair. |
| 3 | Spec-fidelity stage | Change | First add an evidence map to existing build/review artifacts. Add a paid stage only when a bounded comparison shows incremental omissions caught. |
| 4 | Shape D prose launcher | Defer launcher | Repeat the useful anti-slop consumer as explicit missions with semantic-preservation acceptance. One document is insufficient for another command/contract; D23 shows preservation needs factual checks too. |
| 5 | Dispositions/calibration, then rule 10 | Change order | Repair D20/D21 and denominators first, then collect genuine live dispositions. Keep the summary dollar until appropriate observed data supports changing it. |
| 6 | Wall-clock cost model | Keep, narrow | Measure critical path and lead integration time, not summed lane durations as elapsed time. Separate lead attention from machine occupancy. |
| 7 | Leaner Sonnet prompt, exact gate flags | Keep | Name worktree-safe xdist command, basetemp, policy, scope, and compatibility once. Test emitted prompts; do not remove rules that prevented documented failures merely to shorten text. |
| 8 | Non-code reproduce gate | Change abstraction | Separate artifact validator with before/after acceptance. Mechanical violations can reproduce; semantic meaning preservation may need human judgment, not a pretend code test. |

## 9. Recommended roadmap items

Ranked work packages, not authorization to dispatch. Source module counts exclude tests/docs.
Counts include test items; test-heavy items should be counted twice for launcher sizing.
These are decomposition estimates, not dollar/time promises. Scheduler-tax work ships in series.

| Rank | Package and evidence | Modules | Scheduler / wait loop / resume | Rough spec items |
|---|---|---|---|---|
| 1 | Exact planner approval, stop, child identity (D1-D2) | mission, optional approval helper; 1-2 | yes / no / yes | 5: identity, consumption, stop, drift, interaction tests |
| 2 | Complete taint graph and enforceable capabilities (D3-D5, W1-W2) | mission, fleets, runner; 3 | yes / conditional / yes | Split graph 4 and adapter 4; live drills separately approved |
| 3 | Accepted artifact and complete evidence (D6-D8, D17, W3-W5) | attest, land, salvage, runner, mission, export; 6 across specs | some / no / yes | Terminal evidence 5; land identity 4; capture/salvage 5 |
| 4 | Ownership-safe locks (D10) | mission, optional helper; 1-2 | yes / no / yes | 4: publication, reclaim, release, deterministic races |
| 5 | Durable effects and common pre-spawn refusal (D9, D11-D13) | mission, runner, spend, report, export; 5 | yes / yes / yes | Identity/reconcile 6; retry/cancel 4; derived readers 4 |
| 6 | Strict numeric/structured/terminal contracts (D9, D14-D15) | outputs, verdicts, fleets, prices, mission, runner; 6 across specs | settlement / parser interaction / yes | Validation 5; error-to-receipt lifecycle 5 |
| 7 | Replay requested contract and merged implementation (D18-D19) | golden, land, cli; 3 | replay / no / replay | 5: contract, attempts, import identity, effect guard, tests |
| 8 | Producing-attempt salvage and quoted paths (D16, D22) | salvage, runner, collisions; 3 | no / no / no | Salvage 4; path handling 3, independent after identity work |
| 9 | Honest quality/cap/cost measures (D20-D23, W6-W8) | report, spend, mission, prices; 4 | wall only / no / conditional | Finding identity 5; cohorts 4; arithmetic a bounded docs fix |
| 10 | Non-code artifact acceptance (idea 8) | runner, fleets, mission, shape; 4 | settlement / no / yes | About 5: contract, evidence, policy, receipt, consumer tests |
| 11 | Scoped Shape C and consumer evaluation | shape, cli, prompts; 3 | no / no / no | 3-4 launcher items, then bounded approved consumers |

Land can fail closed on identity before a larger receipt-format change; do not wait for all
rank-3 modules to be redesigned. The first accounting containment is preserving unknown-cost
spawned obligations and refusing further effects until reconciled, not building an event
platform. Fix pure parser errors without demanding paid experiments.

Hardening exit criterion: named counterexamples fail on old code and pass on fixes; full
gate and replay remain green; separately authorized adapter drills prove the claimed
boundaries; a bounded real consumer reconciles exact artifact, unique attempts, approval
history, and acceptance. Do not substitute feature/release count for this outcome.

## 10. Disagreements with standing decisions

None requires reopening. Keep local-only, paused OpenAI lanes, shelved fleets/cloud/background
work, lead-owned commits/landing, no quotas, and the rejected architecture list. Durable
effects and ownership-safe locks do not require atomic reservation or age-based reclaim.
A narrower honest threat-model claim is preferable to reopening a shelved platform for a
stronger-sounding sandbox. Applying “a fleet's word is never evidence” to approval and
artifact identity is consistency with the existing principle, not opposition to it.

## 11. Questions for the operator

No answer was needed to finish this review. Before implementation:

1. Must tainted execution resist intentionally adversarial shell behavior, or is the supported
   model cooperative-but-fallible agents? Recommend refusing general execution in the tainted
   case until the needed boundary is demonstrated.
2. After lead salvage, should acceptance be a new signed candidate record or explicit pinned
   SHA with new gate/review evidence? Either is clearer than a changed branch under an old receipt.
3. Which already-intended bounded consumer supplies the first post-hardening acceptance?
   Do not reopen deferred OPERANT-J/HarnessBench work merely to satisfy this review.

Next: implement and regression-test exact planner approval (rank 1). The remaining items are
ranked scope, not a mandate to launch them all. Implementation and paid drills require a
separate task; neither was authorized in this sitting.

## 12. What was checked and what was not

### Scope and full reads

At the operator's request I restarted from the beginning, rechecking candidates against full
source/tests. Baseline stayed `67a47f36cf30d5e2dff013564f0f4ac222fb1860`. Only this notes file
was edited. No source/test changes, commits, branches, remotes, dispatches, land, salvage,
or paid reviews. No vendor CLI directly invoked. Authorized golden replay can perform its
own bounded version queries; that is not a model run.

Read in full:

- Brief, repository AGENTS.md, original section contract, all README.md (2,824 lines).
- All thirty `src/conductor/*.py` modules, 21,308 lines: __init__, attest, breakers, budget,
  ceiling, cli, collisions, errors, export, fleets, forecast, gc, golden, land, mission,
  notify, outputs, paths, ports, prices, prompts, report, runner, salvage, shape, spend,
  surface, verdicts, verify, worktrees. Long modules were read in contiguous bounded chunks;
  truncated portions were reread, not treated as inspected.
- All sixty `tests/test_*.py` plus conftest.py, 25,698 lines. This includes complete
  mission/resume/plan/pause/human, fleet/taint/restricted, gate/artifact/land/salvage,
  accounting/report, and golden groups, not only nearby tests. The brief's approximate
  module count was not used as the inventory.
- All three scripts: release.py, prose_gate.py, notify-hub.py, 522 lines.
- All ROADMAP-2026-09/10/11.md and RESET-2026-09.md, including historical/dropped sections.
- All three requested research reports: 2026-09-07-f9-shape-b-c.md,
  2026-09-07-f15-shape-c-fixes.md, 2026-09-07-consumer-anti-slop.md.

### Selective inspection, not full-reading claims

Other historical provider reports were cross-referenced through AGENTS/README/roadmap/RESET;
their entire corpus and external URLs were not reread/browsed. Eleven golden fixtures were
exercised and their machinery/tests fully read, but not every byte of each transcript was
manually read. Metadata/help and targeted lines were inspected using git, rg, cat, sed, nl,
wc, and jq. No provider source was live-revalidated.

Direct selected receipt readbacks beyond the report's scan:

- `$CONDUCTOR_HOME/missions/20260907T101347Z-f10-anti-slop-f15-doc/result.json`: unique
  attempts, cumulative costs, total 1.531670, terminal and wall fields.
- `$CONDUCTOR_HOME/missions/20260907T083702Z-f15-m1-verify-report-wall/result.json`:
  total 7.447634 and stopped-fix versus mission-verdict distinction.
- `$CONDUCTOR_HOME/missions/20260907T090336Z-f15-m2-dispositions-deliverable/result.json`:
  total 14.724807; wall 3456.1, paused 18.1, gates 119.9, lanes 3419.3, concurrency 2.

These are selected readbacks, not cryptographic verification of the whole ledger.

### Substantive commands and results

```sh
PYTHONPATH=src .venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/pytest -q -p no:cacheprovider -o addopts="" \
  -n auto --dist loadgroup --basetemp="$TMPDIR/conductor-review-gate"
.venv/bin/conductor --help
.venv/bin/conductor golden check
.venv/bin/conductor report --since 2026-09-05
```

First gate: Ruff clean; 1,287 passed in 36.15s. Closeout commands used
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src`; lint added `--no-cache`, exit 0.
A closeout full pytest run passed 1,287 in 36.50s in a unique
`/tmp/conductor-astra-review.*` scratch directory. That is outside the tree but not the
requested system TMPDIR, so a further complete run used
`$TMPDIR/conductor-astra-final.*/pytest`: **1,287 passed in 34.86s, exit 0**. Same parallel
flags throughout. Exit codes were captured separately from unpiped logs. Scratch logs were
retained outside the tree; no new source fixture was written.

Golden closeout: exit 0, eleven fixtures, forty informational lines: four unknown-version
notes across two old fixtures, four prompt-version drifts for each of nine newer fixtures.
No behavioral differences reported. This has section 6's limits.

Report closeout: exit 0. Literal output: Anthropic builds 51/36 ok, 4 cap misses, 7 gate
failures; cap category 9 runs/$50.59; gate category 15/$41.17. Finding table Google 49 runs,
1 finding, 5 unparsed; xAI 49/9/35. Dispositions: xAI 7 fixed, 1 already, 0 refused, and two
malformed lines globally. These are current report observations, not independently validated
quality metrics. The prose cost discrepancy was independently reconciled from unique attempts.

Pure probes used inline Python, no filesystem writes:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
# In-memory calls: mission_from_dict(..., base_dir='.'), generated hook _decide,
# outputs.parse, parse_verdict, parse_dispositions_deliverable, _schema_mismatch,
# touched_files, verify_chain_links, and report helpers.
# Concrete input/output cases are recorded in D3/D5/D7/D9/D14/D20/D22.
PY
```

One repeated probe initially omitted base_dir and stopped with TypeError; corrected and
rerun. Hook probes called the generated decision function, not the shell strings. No
network operation, real child mission, lock race, malicious file capture, or destructive
operation was reproduced. Source-only findings are explicitly labeled above. A first notes
patch was rejected by patch syntax validation and made no change; the replacement succeeded.

### Not established

No live provider permission/pricing proof, natural scheduler execution, notification delivery,
real-work planner-child adoption, external consumer acceptance, or adversarial OS sandbox was
established. No fixes were implemented. Green local gates and historical offline replay are
the achieved verification, not a general safety certificate.
