# Composer build and Grok review on a real Conductor feature

The operator authorized this bounded mission and its corrections on 2026-09-08.
The feature adds `conductor reprice --fleet NAME`: audit one fleet's parser
changes without reading other fleets' transcripts, or apply only that fleet's
planned corrections after the existing full receipt archive. Actual historical
receipts were audited only; apply behavior was exercised on temporary fixtures.

## Execution and acceptance

Mission `20260908T224733Z-composer-fleet-reprice-real-use` used Composer 2.5 for
build and Grok 4.6 High for cold review, both through Conductor's Cursor fleet.
The build committed as `a14891d`. The mission had explicit build/review vendor
policies, isolated worktrees, a captured evidence map, a full gate before the
harness accepted its commit, and no fallback or collate lane.

| Stage | Conductor-recorded helper time | Tool calls | Result |
|---|---:|---:|---|
| Composer build | 91.76 s | 42 | Full gate: 2,229 passed; independent prewritten checks: 6 of 8 passed |
| Grok cold review | 301.74 s | 25 | One verified skip-classification finding |
| Composer resumed correction | 44.00 s | 12 | Three reported defects corrected; full gate: 2,240 passed |
| Lead integration | Not separately timed | Not comparable | Fixed one correction regression; final full gate: 2,241 passed |

The initial build and review mission took 452.06 seconds including its gate.
The correction dispatch took 226.85 seconds including reproduction and gating.
These do not measure total operator effort or include every lead action.

The first implementation handled ordinary filtering, dry-run byte preservation,
selected apply, reported costs, and archival correctly. Independent checks
caught list/dictionary API inputs raising TypeError instead of the specified
ValueError. Source inspection then reproduced two more issues: other-fleet
receipts with no usage or a dry-run marker were counted under selected-fleet
skip reasons, and adding a Summary field shifted existing positional arguments.
Grok independently reported the skip-classification issue; it did not report
the other two.

The lead issued one supplemental Conductor fix-stage dispatch on Composer's
original session `135275be-7c4e-4ed0-a11b-78830a03bbeb`, starting at the build
commit. This was a lead-directed correction, not an automatic mission fix lane.
Conductor transplanted the new tests onto the unfixed build: six tests failed,
all belonging to the reported defects. The corrected tree passed and committed
as `65ba315`; the returned session ID matched the requested original session.

A further lead check caught a new compatibility regression: malformed dry-run
receipts had changed classification in unfiltered mode. A three-line source
addition preserves the old ordering when no fleet is selected, with a permanent
regression test. Final acceptance: all twelve independent checks passed; Ruff
passed; 2,241 repository tests passed on Python 3.14.7; 31 affected tests passed
on Python 3.12.13. Full gates used xdist/loadgroup and external temporary roots.
The cold review was of the first build; the lead verified the final corrections.

## Real use

A final `reprice --fleet antigravity --dry-run --json` inspected 502 candidate
receipt directories. It excluded 374 other-fleet candidates before reading their
stdout, found one selected receipt without stored usage, and parsed 127 selected
logs whose token counters were already unchanged. Instrumented reads confirmed
that only Antigravity stdout was read. No corrections or archive were produced.
All 871 historical receipt files hashed before this audit retained their bytes.

Coverage is bounded: 212 Antigravity receipts were present, but only 128 had an
available `stdout.log`; the one without stored usage was skipped. Thus 84 missing
logs and one missing usage record remain outside the token comparison. There is
no evidence from this pass supporting a historical rewrite.

## What the trial supports

Composer produced the implementation and correction quickly, while independent
acceptance and lead integration remained necessary. Grok contributed a correct
finding, but a successful review dispatch was not proof of an accepted feature.
Conductor exercised isolation, staged vendor policy, full gates, evidence capture,
commit handling, native session reuse, and reproduction against the unfixed base.
Final integration was a lead-controlled fast-forward; `conductor land` was not
part of this trial.

Conductor recorded $0.569851 across the three helper dispatches using its current
price assumptions. This is not an account charge: Composer's receipt identifies
a standard-tier price assumption, and no subscription debit or billing tier was
independently verified. Grok's stream had two connection retries; terminal usage
was not reconciled against provider billing. No capacity was purchased.

The first launch inherited the generic daily ceiling and refused before dispatch.
The explicitly authorized attended mission then used the standard launcher's
mission-specific ceiling configuration with an $8 mission cap; the supplemental
correction had a $3 cap. Cursor caps are post-hoc estimate verdicts. Grok used
25 tool calls against a requested soft budget of 15, below the hard limit of 30.
These are observed setup and instruction-following costs, not reasons to widen
permissions or change provider routes.

This single task supports using Composer for bounded implementation with an
independent reviewer and explicit lead acceptance. It does not establish general
model superiority, autonomous readiness, provider savings, or broader-project
adoption. Standing model policy and defaults were not changed.

Raw mission/run receipts remain in the normal Conductor corpus. Independent
checks, gate logs, timing records, audit read tracking and byte manifests are
retained with the task's local artifacts. The clean integration checkout is
retained as the mission's repository anchor; disposable test roots are cleaned.
