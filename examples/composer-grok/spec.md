# REPLACE_WITH_TASK_TITLE — edit before launch

**Do not launch this mission until every `REPLACE_WITH_*` section below is real task
content.** The checked-in outline is intentionally incomplete.

## Repository

- **Path:** set `cwd` in `mission.json` to your git checkout (relative paths resolve
  against the directory that contains `mission.json`; see
  `docs/reference/composer-grok-example.md`).
- **Scope:** name the modules and files this task may touch. Everything else is out of
  bounds.

## Task

REPLACE_WITH_TASK_DESCRIPTION: what to build or change, in plain language, with enough
detail that a builder can finish without guessing.

## Acceptance

Number each item. A reviewer will treat the builder's `evidence.json` as a claim beside
the diff; every item here must appear in that map with status, files, tests, and the
check that was run.

1. REPLACE_WITH_ACCEPTANCE_ITEM_1
2. REPLACE_WITH_ACCEPTANCE_ITEM_2
3. REPLACE_WITH_ACCEPTANCE_ITEM_3

## File ownership

- **May edit:** REPLACE_WITH_ALLOWED_PATHS (for example `src/foo.py`, `tests/test_foo.py`)
- **Must not edit:** REPLACE_WITH_FORBIDDEN_PATHS (for example `AGENTS.md`, `.github/`,
  unrelated modules)

## Gate

The build and fix lanes each run a gate command you set in `mission.json`
(`REPLACE_WITH_BUILD_GATE_COMMAND` and `REPLACE_WITH_FIX_GATE_COMMAND`). Use your
repository's real check — full test suite, lint plus tests, or a focused command — with
any flags your tree needs (`PYTHONPATH=src`, `--basetemp` under `$TMPDIR` for pytest,
and so on). Do not leave the placeholder strings in place.

## Focused checks

REPLACE_WITH_FOCUSED_CHECK_COMMANDS: checks the builder may run before the harness
runs the configured gate. State temporary-output locations outside the checkout.
For browser changes, provide a focused check against the candidate's own server;
do not point tests at the user's running app or leave browser tests unexercised
until the final gate. Name any required lane `setup`/`teardown` commands.

## Notes for the builder

- Keep every existing call signature working; add new parameters as keywords with defaults.
- Do not commit; the harness commits after the gate passes.
- Write `evidence.json` at the repository root before finishing (see the build lane
  prompt). The harness keeps it out of the commit.
