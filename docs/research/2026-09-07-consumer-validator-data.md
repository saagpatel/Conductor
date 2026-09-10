# Consumer: the F22 validator on a data deliverable, three verdicts live, 2026-09-07

Release 0.84.0. The prose consumer earlier today (`2026-09-07-consumer-validator-live.md`) read
one verdict live, `reproduced`. This run reads the other two, `accepted` refused and `rejected`,
on a JSON deliverable with a schema beside the validator, through `conductor dispatch` on a
scratch repository. Four dispatches, Sonnet 5 at `standard`, $0.30 in total, and two defects in
conductor found by the first one.

## The scratch repository

`catalog.json`, a list of four priced models; `schema.json`, a `type: array` schema whose
`items` require `name`, `price_in`, `price_out`, `as_of`; `validate.py`, a stdlib script that
also requires positive prices, `YYYY-MM-DD` dates, unique names, and alphabetical order. Two
copies: one with a valid catalog and one with three defects (a prose date, a missing date, two
entries out of order), so the validator fails at that base with three lines.

Every dispatch: `--stage fix --deliverable catalog.json --deliverable-schema schema.json
--deliverable-validator 'python3 validate.py {path}' --test 'python3 validate.py catalog.json'
--commit ...`, cap $0.75.

## The runs

| drill | base | prompt | what the bytes say | cost |
|---|---|---|---|---|
| 1, first attempt | broken | fix the catalog so the check passes | validator before exit 1, after exit 0, verdict `reproduced`; then `deliverable does not match schema: top level is not a JSON object`, `ok: false`, and a committed sha on the same receipt | $0.16 |
| 1, after the fixes | broken | same | schema ok, validator `reproduced`, reproduce verdict `validator`, gate exit 0, committed, `ok: true` | $0.04 |
| 2 | valid | add one entry, keep the list sorted | validator `accepted` (0 and 0), reproduce `no-check`, `fix without a reproducing check: validator passed on the base too`, no commit, the edit left in the tree | $0.05 |
| 3 | valid | append one entry at the end, run nothing | validator `rejected` (0 then 1, `entries are not sorted by name`), `deliverable rejected by validator`, gate exit 1, commit undone and the work left staged | $0.05 |

## What the first dispatch found in conductor

Two defects, both fixed in this release with a test that fails without the fix.

**A `type: array` schema refused every list.** `_schema_mismatch` demanded a top-level object
before reading the schema at all, so a list deliverable could never satisfy any schema. The check
now reads the schema's own `type`: `array` checks each element against `items` under the same
narrow contract and reports the first mismatch as `item <n>: ...`; anything else keeps the
object requirement.

**A failed deliverable check left the commit on the branch.** The E1 checks (missing, empty,
unparsable, schema mismatch) sank `ok` but did not withhold conductor's commit, while a validator
`rejected` withheld it through `error` and a failed gate undid it. The first receipt carried
`ok: false` and `committed: true` together. E1's check now counts as a gate: the commit is undone
and the work stays staged in the tree, the same path a failed test gate takes.

## Reading

Drill 2 is the shape to remember. Adding a valid entry to valid data is correct work that a fix
lane cannot land: the validator passed on the base, so there is nothing reproduced, and the
refusal says so. That is the data twin of the test-only rule shipped in 0.82.0: run it as a build
lane. Drill 3 is the validator doing what a reviewer should not have to: the lane followed its
prompt exactly, the prompt was wrong about order, and the bytes were refused before any human
read them. Both verdicts now have a receipt beside `reproduced`.

The schema and the validator are complementary here, not redundant. The schema said the shape
was right and the validator said the content was wrong (drill 3); on the first attempt the
validator said the content was right and the schema check was wrong about conductor itself. A
check that fires on the first real input was worth its sixteen cents.
