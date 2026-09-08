# Ownership evaluation and disposition, 2026-09-08

## Judgment before implementation

The project needs consolidation before another feature wave. Its strongest property is
that it keeps raw evidence and exercises actual Git and process behavior in tests. Its
weakest property is that several consumers independently reinterpret the same evidence.
The last accounting failures are instances of that architecture, not isolated bad arithmetic.

This assessment was delivered to the operator before source changes. The inspection started
at `f41a22d` on `feat/conductor-v1`: 34 source modules, about 30,812 source lines, and a green
2,115-test baseline. The following initial line references refer to that commit; symbol names
remain useful after this change.

### Structural weaknesses and test limits

1. **The lifecycle is the real monolith.** `mission.py::_execute_mission` (3908–5469) is
   1,562 lines, with a large nested attempt runner sharing scheduler, ledger, cancellation,
   retry, artifact, and persistence state. `runner.py::dispatch` (2453–3974) is another
   1,522 lines joining process control, gates, worktrees, pricing, and signing. Total file
   length alone is not the problem; these state transitions must agree across crash and
   resume boundaries. Extracting functions without giving those transitions explicit inputs
   and outputs moves the difficulty rather than reducing it.
2. **The extraction retains a dependency cycle.** `attempts.py:32–44` explicitly explains
   lazy imports back into `mission` to preserve monkeypatch targets. `_git_answer` (856–872)
   calls through `mission.git_run`. Compatibility matters, but internal test patch points
   have become architectural constraints. New tests should exercise receipt transitions and
   actual injected boundaries, rather than make another internal alias permanent.
3. **One gate had two interpreters.** `runner.py::_gate_summary` (1241–1265) and
   `attest.py::_gate_summary_from_result` (197–227) duplicate precedence and the distinction
   between no gate and a passed gate. The latter's own comment records a merged change that
   broke 13 attestation/export tests by updating only one side. Those tests caught the drift;
   retaining the duplication invited it back. This pass removes it into `verify.py`.
4. **Some tests faithfully prove the wrong number.** The cheap-retry test in
   `tests/test_cascade.py` dispatched a failed $0.01 attempt followed by a successful $0.02
   retry, then asserted `cheap_ok == 0` and `cascade_usd == 0.01`. Its process simulation is
   useful; its expected summary was wrong. This pass corrects the assertion and adds tests
   that account for every dispatch in each phase, including unknown prices.
5. **Synthetic layout tests missed a broken real workflow.** `tests/test_release_script.py:1`
   stated that the real files were never read. Its old-layout fixtures all passed while
   `scripts/release.py` refused the newly reorganized receipt index. A read-only release-plan
   test against the actual repository and a split-layout regression now cover that boundary.
6. **Golden replay has a real but bounded claim.** `golden.py` reparses recorded stdout with
   the current parser and compares rendered prompts to their recorded bytes. A helper's
   claim that it never parses the stream was false. Replay verifies parser/orchestrator
   compatibility with captured scenarios; it cannot establish that a live vendor still
   serves the same stream or that a model semantically follows a changed prompt.
7. **The full suite is local evidence.** It exercises useful refusal, Git, receipt, replay,
   cancellation, and subprocess paths. It does not establish current vendor billing,
   semantic review quality, all crash interleavings, or machine-load behavior. The configured
   venv is Python 3.14.7, despite the brief's Python 3.12 description; minimum-runtime
   verification is reported separately below.

### Where a receipt still substitutes for verification

- `attempts.py::_trusted_lane` (726–853) accepts pre-digest receipts on paths with a note.
  It also keeps a receipt after two `GIT_UNRUN` results. These are explicit compatibility
  decisions, not hidden vulnerabilities. I disagree with describing those outcomes as
  verified: an unavailable check is not a passing check. This pass refuses missing
  repositories and unreadable artifacts that have recorded digests. Changing the remaining
  compatibility rule should be a separate operator decision: pause as unverifiable instead
  of silently accepting or automatically paying to rerun.
- Mission snapshots embed old prices even after authoritative run receipts are repriced.
  A read-only join found 72 mission snapshots containing at least one different cost. For
  example, mission `20260905T160000Z-b2-cache-prompts-review` retains $1.005538 for run
  `20260905T160000Z-cursor-review-the-change-against-the-sp`, whose current receipt records
  $3.026412. `conductor report` reads run receipts; this is historical snapshot drift, not
  proof of current double charging. The reference now states which reader to use.
- Forecasting cannot infer that a finite, recorded price is wrong. It previously presented
  all-unpriced history exactly like no history and mixed known auxiliaries into unstaged
  lane history. This pass makes missing/estimated/zero counts visible and removes known
  auxiliary effects from lane forecasts. It does not pretend those checks detect parser
  mistakes or validate a vendor invoice.
- Signed receipts authenticate bytes against a local HMAC key. A process with access to
  that key is inside the trust boundary. Scrubbed export manifests and local authentication
  are documented mechanisms, not independent provider attestation or a same-user sandbox.
  Building another signing layer would not resolve the observed lifecycle disagreements.

### Value against effort

| Order | Change | Value / effort | Decision |
|---|---|---|---|
| 1 | Correct current-attempt selection, retry totals, and unknown-price propagation | High / small | Implemented in this pass |
| 2 | Delete duplicate gate interpretation; share budget-blocking classification | High / small | Implemented in this pass |
| 3 | Make maintained docs and release tooling agree with actual bytes | High / small | Implemented in this pass |
| 4 | Define an explicit unverifiable resume outcome | High / medium | Next design decision; affects reuse and repeat-payment behavior |
| 5 | Replace nested lifecycle state with a few explicit transition boundaries | High / large | Follow the state decision; no mechanical file split proposed |
| 6 | Remove unused planner/export/variant features after usage evidence | Potentially useful / unknown | No deletion without evidence that users do not rely on them |

Stop expanding planner, export, and pipeline variants in the meantime. Deleting duplicated
interpretation is justified now; deleting entire capabilities merely because the source is
large is not. Atomic reservation, cloud checkout offload, and shelved fleet routes remain
out of scope. No dispatch-policy change is part of this work.

## Corpus boundary

The read-only audit counted 868 run results: 210 Antigravity, 236 Cursor, 353 Claude,
55 historical Codex, and 14 script receipts. Of these, 364 were dry runs, 504 were not;
496 result directories contained stdout. Among non-dry runs, 26 had no usable price and
9 recorded zero. These are the captured audit counts, not a claim that the directory will
never grow. The known auxiliary join contained 15 paid effects (12 orders, 3 collates).
No corpus receipt was changed or deleted during this task, and no new live fleet probe
was needed to validate these fixes.

## All 19 open findings

G1–G8 refer to the Grok report in run
`20260908T184555Z-cursor-you-are-cold-reviewing-two-modul`.
M1–M11 refer to the Gemini report in run
`20260908T184818Z-antigravity-behavioral-constraints-before-an`.
The rows preserve duplicates rather than counting them as separate discoveries.

| ID | Disposition | Evidence and resulting action |
|---|---|---|
| G1 | Confirmed; fixed | A cascade prepends attempt 0. Forecast now uses the declared primary at index 1 for cascaded lanes; the regression observes the correct vendor and cap raise. |
| G2 | Wording | Nearest-rank p80 at n=3/4 is correctly the maximum. The false outlier-protection claim is removed from code and reference; percentile and rounding policy remain. |
| G3 | Partly confirmed; fixed | Missing prices were invisible. Forecast now counts and warns on them and exposes estimates/zeros. Valid zeros and estimates remain eligible: their existence does not prove underpricing, and deleting them would discard legitimate data. |
| G4 | Refuted as a defect | Whole-dollar rounding and increasing the mission budget by the same delta are the explicit F17 policy. Missing max_cost_usd means no mission-wide ceiling. No silent cap ceiling or new spending policy added. |
| G5 | Confirmed; fixed | Retry dispatches were omitted from phase costs and successful cheap retries from cheap_ok; missing prices became zero. The summary now includes retries and renders an unpriced phase as null/unknown. |
| G6 | Partly confirmed; fixed | Recovery rebuilt a spawned unpriced attempt without its uncertainty marker. If the original run receipt later became unavailable, fallback accounting could lose it. Recovery now retains classification. The claimed GC trigger is wrong: conductor GC does not remove run receipt directories. |
| G7 | Partly confirmed; fixed | A recorded digest plus an unreadable artifact or null path could be skipped. Digest checks now refuse that state. Generic missing paths normally fail the earlier path check; pre-digest compatibility is deliberate and remains. |
| G8 | Partly confirmed; fixed | Boolean timeout/cap values were coerced to 1; fractional timeout was truncated. They now fail before coercion. Boolean grace is explicitly rejected too, although true already exceeded the existing grace ceiling. |
| M1 | Duplicate G1 | Same primary-versus-cascade bug; covered by the same fix and red regression. |
| M2 | Duplicate G5 | Same retry classification, cost, and cheap-success bug; covered together. |
| M3 | Duplicate G7 | Recorded-digest failures are fixed; the broader claim that every missing file passed omitted earlier path checks. |
| M4 | Confirmed; fixed | A removed repository became GIT_UNRUN and was trusted as machine load. Required repository existence is now checked before commit/branch reuse. |
| M5 | Duplicate G2; wording | Small-sample p80 math is correct; the claimed outlier protection was not. |
| M6 | Confirmed API defect; fixed | Naive datetime versus UTC receipt timestamps raised TypeError. The API now interprets naive since as UTC. No current launcher call was found passing a naive bound. |
| M7 | Confirmed API composition defect; fixed | Parsed default names and inherited caps did not match raw unnamed/defaulted lanes. Forecast carries the original lane position and normalized cap; apply_caps matches them and remains idempotent. The current Shape A emitter already writes explicit names/caps. |
| M8 | Partly confirmed; overlaps G8 | Type coercion is fixed. The alleged late negative-value refusal is false: mission validation already rejected negative timeout, cap, and grace before any dispatch. |
| M9 | Refuted as a defect | Mission-level defaults apply to attempts, and script attempts explicitly refuse model-only settings. Only cap inheritance has the documented script exception. Silently dropping other defaults would change the contract. |
| M10 | Partly confirmed; fixed | Infinity produced an endless pollable delay; nonfinite and boolean retry delays now fail at load. The alleged NaN sleep crash is false: max(0.0, NaN) evaluates to 0.0 on the actual path. No change to _pollable_sleep. |
| M11 | Confirmed; fixed | Known collates/orders/resolvers joined into vendor/None alongside unstaged lanes. Forecast excludes identified auxiliaries while retaining real lanes and standalone runs. The corpus contained 15 paid auxiliary effects; impact direction depends on actual prices. |

## Additional work beyond the open list

- Released already-merged wave 18 separately as **0.102.0**, commit `815518a`.
  Fixed release indexing rather than bypassing the broken release workflow.
- Shared gate verdict and summary logic in `verify.py`; existing runner/attestation entry
  points remain compatible. This is subtraction, with no new policy or feature surface.
- Shared live/resume/recovery unpriced-dispatch classification in `budget.py`. A timeout
  no longer acquires a new blocking error merely because it is resumed.
- Preserved cancelled unknown-price counts through resume, including recovered summaries
  and auxiliary dispatches; an authoritative later price replaces the uncertainty.
- Corrected README forecast scope, the stale token-convention paragraph, cascade accounting,
  resume trust exceptions, and the distinction between historical snapshots and current spend.

## Verification and limits

Verification results are recorded here after the final gate. The new regression files are
`tests/test_attempt_accounting_ownership.py` and `tests/test_forecast_ownership.py`; the
release-layout regressions are in `tests/test_release_script.py`.

All defect regressions were copied onto detached **v0.101.0 source (`2f3e5d3`)** with
PYTHONPATH pointing at that worktree. The initial release-layout test failed, 15 attempt
cases failed (3 preservation cases passed), all 7 forecast cases failed, and 2 cancelled
resume cases failed (1 later-price preservation case passed). That is **25 observed red
cases**, not 25 independent defects. Failures were behavioral assertions or the reproduced
naive/aware datetime TypeError, not import failures.

The first forecast helper's fixtures were invalid (stage/mode/default combinations).
They were rejected, simplified, and rerun. An intermediate parent fixture also omitted the
receipt's required ok field; its results were discarded. Only the corrected seven-case
red run and passing focused run count as evidence. The earlier 144-case focused run found
one stale cascade expectation, which was corrected to represent an unknown phase cost.

The initial independent Grok architecture review timed out without a terminal answer;
it is not a completed review. Gemini's completed review was advisory: claims about golden
replay, global Claude settings, and planner recovery were checked and rejected when source
contradicted them. Model agreement never served as the acceptance gate.

Final verification:

- `env -u CONDUCTOR_LANE .venv/bin/ruff check src tests`: exit 0.
- `env -u CONDUCTOR_LANE .venv/bin/pytest -p no:cacheprovider -o addopts="-q" -n auto --dist loadgroup --basetemp=<external task directory>`:
  **2,145 passed in 50.73s**, exit 0, Python 3.14.7.
- The same full parallel suite through Python **3.12.13**, with source and the existing
  pure-Python development dependencies on PYTHONPATH: **2,145 passed in 54.31s**, exit 0.
  No package installation or venv replacement was needed.
- The shared gate interpreter matched the previous runner across 225 combinations of
  absent, skipped, passing, failing, timed-out, and interrupted own/clean gate states.
- `git diff --check`: clean. All gate and red-run exit codes were captured to files.

These checks used local test processes and synthetic receipts, not live model dispatch.
The change ships as **0.103.0** after the separate **0.102.0** release. No provider billing,
remote integration, scheduler adoption, or human acceptance claim follows from this gate.

The necessary implementation and verification in this handoff are complete. Next is the
resume-policy decision described above: whether an unavailable Git check should pause as
unverifiable. The larger lifecycle extraction follows that decision, rather than silently
changing reuse or repeat-payment behavior during a compatibility fix.

The final read-only Gemini review completed with four claims. All were refuted on
source: every retry uses the same first_run_id (not the preceding retry); live timeout
classification does not increment cancelled unknown-cost counts; explicit raw attempts
lists are rejected by mission_from_dict; and command without fleet is also rejected.
Forecast lane_index is the original mission position, so skipped lanes do not shift it.
No changes were accepted solely on this helper's confidence scores.

The first full ownership gate returned **2 failed, 2143 passed**: the negative retry-delay
error string had changed, and the documentation lost a phrase an existing test required.
The original negative-value message and truthful three-run-floor wording were restored.
