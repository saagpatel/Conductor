# Golden missions

A recorded mission can be replayed offline as a fixture so routing changes are
testable without vendors.


A change to routing, a template, or `outputs.parse` used to be testable only
by paying for a live mission. A golden fixture is a recorded transcript of a
past real mission, replayed offline through the same parser, scheduler, and
templating that ran it the first time. Evidence
(`docs/ROADMAP-2026-09.md` item C7): "Recorded transcripts of past real
missions replayed through the parser, scheduler, and templating offline, so
a routing or template change is testable without spending on live vendors."

## What a fixture holds

`conductor golden record MISSION_ID --out DIR` copies a finished mission
directory and every run it dispatched into `DIR`: `mission.json`,
`result.json`, `report.md`, `lanes/*.json`, `pause.json` when present, and
for every run id any lane's attempts name -- plus, since F8, the collate's
own run, every judge order of a rank sitting, and the resolver's run, the
ones `result.json` names outside any lane -- `runs/<run_id>/result.json`,
`stdout.jsonl`, `prompt.txt`, `answer.txt`, and `diff.patch`, each only when
it exists. A run's transcript is stored as `stdout.jsonl`, never
`stdout.log`: an operator's global git excludes routinely drop every
`*.log` path from `git add` silently, and a fixture using that name would
look committed while never actually landing in the repo. Replay restores it
to `stdout.log` when it recreates a run directory, matching what a live run
writes. `argv.json`, `stderr.log`, `liveness.json`, and `attestation.json`
are never copied: argv is reconstructible from the spec, stderr is empty on
every real run so far, liveness is a heartbeat with nothing to replay, and
attestation's DSSE payload is base64 over the real run's paths (unscrubbable
without breaking the signature) with a signature that cannot be verified
without the operator's key -- a copy would be both unscrubbed and
unverifiable, and nothing in replay reads it beyond hashing it into a
throwaway chain. `golden.json` is the manifest: format, the source mission
id, the fixture's own name, when it was recorded, the conductor version,
the fleets it exercises, each of those fleets' recorded `--version` output
(`fleet_versions`, E22 -- null for a fleet whose every receipt predates
that field), the placeholder names, and a sha256 per file. `expected.json`
holds the mission's `projection` (below) at record time.

## Scrubbing

Every copied text file and every string inside every copied JSON document is
scrubbed, longest replacement first so a home nested inside the user's own
home is replaced before the shorter path that contains it: the conductor
home becomes `<home>`, the mission's `cwd` becomes `<cwd>`, and the user's
home directory (`Path.home()`) becomes `<user>`. The mission snapshot's own
`source` -- the path of the mission file the operator launched from -- is
scrubbed whole to the literal `<source>` rather than through the placeholder
walk above, since that walk only reaches as far as `<home>`/`<cwd>`/`<user>`
match and would otherwise leave a fragment of the launch path (a jobs id, a
scratchpad directory) in the fixture; a missing or empty `source` (a mission
built in code, as the test suite does) is left as it is. Then secrets: any
`NAME=value` where `NAME` contains `TOKEN`, `SECRET`, `KEY`, or `PASSWORD`
becomes `NAME=<redacted>`; `Bearer <token>` becomes `Bearer <redacted>`;
JSON object values whose key contains those words become `<redacted>`; and
`sk-`, `xai-`, `ghp_`, or `AIza`-prefixed tokens of 16 or more characters
become `<redacted>`. JSON object keys are scrubbed like values (a
cross-repo mission's `overlap.files` is keyed by `<repository>:<path>`).
A cross-repo mission's other repositories (E26 `cwd`)
each get their own `<cwd2>`, `<cwd3>`, ... placeholder, in the order they
first appear in the mission's lanes -- see "Collisions across
repositories", above. `golden.scrub_guard(path, extra=[(real, label), ...])`
re-scans a fixture directory for the user's home path, the conductor home,
any of `extra`'s own (path, label) pairs -- a mission's repositories, say,
which `scrub_guard` has no way to recover from an already-scrubbed fixture
on its own -- or any of those secret patterns, as `file:line: <pattern
name>`; empty when clean. `record` runs it over the scrubbed copy before
anything is written to `DIR` and refuses (`fixture would leak: ...`) on any
hit, so a clean fixture is what `record` produces, not a step after it.
It also decodes any run of 64 or
more base64 characters on a line and
scans the decoded text the same way, reporting `file:line: <pattern name>
(base64)` -- the shape a DSSE envelope like `attestation.json` carries a
statement in, invisible to a plain-text scan, and part of why that file is
never copied into a fixture at all.

## Eliding a transcript

`stdout.jsonl` is elided so a multi-megabyte transcript stays small and
readable without changing what the parser sees: per JSON line, any string
value longer than 512 characters becomes `<elided N chars
sha256=<12 hex chars>>`, except the keys `result`, `response`, and `error`
inside an event whose `type` (or, for antigravity, `event`) is `result`, the
whole `structured_output` object inside such a result event (a
`--json-schema` answer, which `outputs.parse` re-serializes as the run's
answer), the key `text` inside an event whose `type` is `assistant`, and
the key `plan` anywhere, all of which are kept whole. A line that is not JSON is kept as it
is. `record` refuses (`GoldenError`, naming the run and the field) unless
`outputs.parse` agrees on `answer`, `usage`, `status`, `error`, and
`session_id` before and after eliding.

## Replaying offline

`mission.run_mission(..., dispatcher=...)` takes a callable in place of a
live `runner.dispatch`: when set, every attempt calls `dispatcher(spec,
lane=<name>, attempt=<label>, retry=<index or None>, dry_run=..., ...)`
with the same keyword values the live path gets, and the branch-claiming
step after a lane's attempt walk sets `branch` on the lane and its final
attempt without touching git. `golden.replay(fixture_dir, *, home, cwd,
cwds=None)` builds exactly that dispatcher: it loads `mission.json` (with
`<cwd>` mapped back to `cwd`, and every `<cwd2>`, `<cwd3>`, ... a cross-repo
fixture uses mapped back to `cwds`'s own real directory, or a fresh empty
repository under `home` when `cwds` does not name it) through the ordinary
snapshot loader, and for the
k-th call on a lane returns the k-th recorded run in that lane's
`lanes/<name>.json` (`previous_attempts` then `attempts`). The collate,
each judge order, and the resolver dispatch through the same callable under
their own labels (`collate`, `collate:<judge index>:<forward|reverse>` with
judge 0 the collate's own fleet, `resolve`), answered from the run ids the
fixture's `result.json` names; before F8 those three sites called the live
`runner.dispatch` directly, and the first judge-sitting fixture paid four
real judge dispatches on every `golden check`. `run_mission` likewise
takes `conflict_finder=` in place of `collisions.merge_conflicts` (a
replay repository holds none of the recorded tips) and `notifier=` in
place of `notify.emit`; replay passes the recorded `collisions.conflicts`
and a notifier that records the event without running the hook. The
replay copies each recorded run's
files into `home/runs/<run_id>/` (`stdout.jsonl` restored to `stdout.log`),
re-parsing that transcript, and recording
a difference for any of the same five fields that disagree with what was
recorded, or for a rendered prompt (template nonces normalized) that no
longer matches `prompt.txt`. A call past the recorded count is itself a
difference (`lane <name>: replay dispatched attempt <k> but the recording
has <n>`), answered with a refused result so the mission still completes
rather than raising; so is a recorded run id with no receipt in the
fixture (`recorded run <id> has no result.json in the fixture`, the shape
a pre-F8 fixture with a collate would show).

D18: the replay also compares the **dispatch contract** it was asked for
against the one the recording actually ran under, before it looks at the
transcript at all -- `fleet`, the resolved `model` id for the effort,
`effort`, `mode`, `timeout`, `cap_usd` (from the receipt's `budget`),
`taint` (and, on a tainted lane, which shell policy ran), and the
`--restricted` flag a claude read lane would carry. Each field that moved
is a difference of its own (`run <id>: contract cap_usd: recorded 4.0,
replayed 2.0`), so a regression in how a lane becomes a `Spec` -- a default
model, a tightened cap, a flipped mode, a dropped taint declaration -- fails
`check` even when the recorded transcript still parses identically. A field
the recording never carried (an older receipt that predates `taint` or
`restricted`; the requested schema, which no receipt records) is not
comparable and is reported as a note, listed once per fixture and never
failing the check. At the end of the replay every recording must have been
consumed: an unconsumed run id is a difference naming the lane and the run
ids (`lane <name>: replay dispatched 0 attempt(s) but the recording has 1`),
unless the replay deliberately did not start that lane -- a pause point, a
skip, a cancellation -- in which case it is a note, because that decision is
itself pinned in `expected.json`. `runner.Result.from_dict` rehydrates a stored
`result.json` back into a `Result`; an unknown field is refused by name, and
a field missing from an older receipt takes its dataclass default.

## Prompt versions

Conductor authors prompt text of its own in five places: the collate and
resolve defaults and the rank contract in `mission.py`, the verdict
checklist contract in `verdicts.py`, and the Shape A review, fix, and prefix
texts in `shape.py`. `prompts.prompt_versions()` gives each one a stable
name and a version id, the first twelve hex characters of the sha256 of its
text (the two contracts and the prefix rendered with a fixed sample input),
so an edit moves the id with no hand bump. `conductor fleets` and
`conductor shape a` print the map; every mission's `result.json` carries the
whole map as `prompt_versions`, and a run receipt carries the ids of the
prompts that dispatch actually appended (the checklist contract, or the
collate, resolve, or rank contract the mission handed it).

Each attempt in a fixture's projection carries `prompt_sha256`, the sha256
of its rendered prompt after scrubbing and nonce stripping, exactly as
`replay` compares it. A fixture recorded before the key existed is not
re-recorded: `check` fills the missing value from that attempt's own
recorded `prompt.txt`, never from the live replay, and then compares, so an
edit to any prompt a fixture used fails `check` naming the lane, the
attempt, and the key. `golden.json` records the prompt version map at record
time; `version_drift` lists each prompt id that has moved since, or
"prompt versions unknown" on a fixture that predates the field. Drift is a
note, never a failure.

## Checking a fixture

`golden.projection(result)` is the slice of a `MissionResult` a routing or
template change is allowed to move -- `ok`, `require`, `notes`, `errors`,
`escalation`, `early_cancel`, `paused`, `quorum`, a trimmed `ranking`
(`lane`, `rank`, `ok`), the cache hit rate, `notifications` as the ordered
list of event names that reached the mission's `notify` hook (only when any
did, so a fixture recorded without `notify` keeps a byte-identical
projection; the hook's own outcome is never pinned, since a replay's
notifier always answers ok and a live hook's exit code belongs to the
operator's machine, not the routing), `collate` (`ok`, `rank`,
`strongest`, `error`, and the sitting's `agreement` and `votes`) and
`resolve` (`ran`, `ok`, `error`) when the mission had one, and per lane
and attempt the fields that describe what happened, never a path, a
duration, a run id, a timestamp, or a dollar amount. `expected.json` pins that projection at
record time. `golden.check(fixture_dir, *, update=False, cwds=None,
notes=None)` takes the same `cwds` as `replay`; `notes`, when a list is
passed, collects the replay's own non-fatal notes (a contract field the
recordings do not carry, a recording a paused lane never consumed). `conductor golden check [DIR ...]` replays each fixture (every
directory under `tests/golden/` of the current working directory that holds
a `golden.json`, by default) into a fresh temporary home and a fresh
temporary git repository, and prints every difference -- the replay's own,
plus one line per projection field that disagrees with `expected.json` --
prefixed by the fixture's name; exit 1 if any difference printed, 0 if
every fixture was clean (the replay's own notes and the version-drift
notes, below, print without changing the exit code). `--update` is for a deliberate change: it rewrites
`expected.json` from the replay instead of reporting projection
differences, so the next `check` is clean once the new behavior is the one
you meant.

`conductor golden check` also prints a version-drift note per fixture, from
`golden.version_drift`: one line per fleet whose `golden.json`-recorded
`fleet_versions` entry (E22) disagrees with `fleets.cli_version` on this
machine (`<fleet> recorded <old>, installed <new>`), or `recorded version
unknown` when the fixture predates that field and carries none at all --
the two C5 fixtures under `tests/golden/`. Drift is a note,
not a failure: it never changes the exit code, and `--update` never writes
it back into `golden.json`.

## Fixtures shipped

Every fixture under `tests/golden/` is a real mission, recorded as-is (a
fixture is never hand-edited; a defect `record` or `check` exposes is fixed
in conductor and the fixture re-recorded). `c5-build-cascade-capped` and
`c5-review-fix` are the two C5 recordings (a capped cascade build, a
review-and-fix). F8 added four from the Phase F consumer run on the
operator's harness repository (`docs/research/2026-09-07-f8-golden-fixtures.md`):
`f10-shape-a-foreign-repo` (a launcher-written Shape A on a foreign
repository, all four lanes green, the fix on the build's resumed thread),
`f10-shape-a-fix-stopped` and `f10-shape-a-reaudit-fixes` (Shape A whose
fix pause was answered `stop`, so the fixture carries `pause.json` with
the answer and a fix lane that never ran), and `f11-unattended-read-notify`
(three read lanes under `--unattended` with a `notify` hook and a
per-mission ceiling, the `end` event pinned in the projection). Five more
came from scratch repositories, one per Phase E shape the suite had never
replayed: `e6-script-lane` (a script build lane and a script read lane,
$0), `e7-human-lane-answered` (a human lane parked and answered with its
sign-off deliverable), `e10-plan-lane-continued` (a plan lane parked on its
child and continued, the child run in the plan lane's own repository),
`e4-judge-sitting` (two Cursor candidates, a Gemini collate and a Sonnet
judge in both orders, unanimous), and `e26-cross-repo-collision` (three
script lanes across two repositories, a hotspot in one and none across).

```
conductor golden record 20260905T171417Z-c5-error-kinds --out tests/golden/c5-build-cascade-capped
conductor golden check
conductor golden check tests/golden/c5-build-cascade-capped --update
```

