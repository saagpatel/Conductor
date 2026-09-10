# Structured review and a 2-of-3 quorum

Independent reviewers, a 2-of-3 quorum, judges, ranking, and collision-aware
collate.


A verdict lane declares one fixed checklist. Conductor generates the fleet's
output schema, appends the checklist contract to the prompt, validates the
answer, and computes the pass bit from the criterion booleans. A model-reported
headline never overrides those booleans. Invalid output makes the lane not ok;
a valid fail verdict does not, because the reviewer completed its job and its
data is safe to tally or paste.

```json
{
  "name": "three-reviewer-fix",
  "cwd": "~/Projects/thing",
  "prompt_file": "spec.md",
  "concurrency": 3,
  "require": {"pass": 2, "of": ["review-claude", "review-codex", "review-gemini"]},
  "lanes": [
    {"name": "build", "fleet": "cursor", "model": "grok-4.6", "mode": "write",
     "test": "pytest -q", "commit": "feat: implement the spec"},
    {"name": "review-claude", "fleet": "claude", "model": "opus", "base": "build",
     "prompt": "Review the build against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nAnswer each criterion from the diff; cite the hunk. A verdict with every criterion passing is a complete, expected answer.",
     "verdict": [
       {"id": "correct", "question": "Does the change implement every requirement?"},
       {"id": "tested", "question": "Do focused tests pin the changed behavior?"}
     ]},
    {"name": "review-codex", "fleet": "codex", "model": "sol", "base": "build",
     "prompt": "Review the build against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nAnswer each criterion from the diff; cite the hunk. A verdict with every criterion passing is a complete, expected answer.",
     "verdict": [
       {"id": "correct", "question": "Does the change implement every requirement?"},
       {"id": "tested", "question": "Do focused tests pin the changed behavior?"}
     ]},
    {"name": "review-gemini", "fleet": "antigravity", "model": "gemini-3.8-flash",
     "base": "build",
     "prompt": "Review the build against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nAnswer each criterion from the diff; cite the hunk. A verdict with every criterion passing is a complete, expected answer.",
     "verdict": [
       {"id": "correct", "question": "Does the change implement every requirement?"},
       {"id": "tested", "question": "Do focused tests pin the changed behavior?"}
     ]},
    {"name": "fix", "fleet": "codex", "model": "sol", "mode": "write",
     "base": "build", "needs": ["review-claude", "review-codex", "review-gemini"],
     "no_op_ok": true, "test": "pytest -q", "commit": "fix: address review verdicts",
     "prompt": "Fix the supported failures in these review verdicts:\n{{lanes.review-claude.verdict}}\n{{lanes.review-codex.verdict}}\n{{lanes.review-gemini.verdict}}"}
  ]
}
```

`needs` still waits for lanes to be **ok**, which for a verdict lane means the
judgment is valid and tallyable, not that it passed. Thus the fix lane runs
after a valid dissent and reads exactly which criteria failed through the
fenced, budgeted verdict templates. An invalid reviewer blocks a dependent
lane; when there is no such dependency it simply counts as not passing the
quorum. The quorum also requires every sink outside its `of` list to be ok.

## Judge hygiene: vendor span, self-judging, and ranking

A quorum's `of` names at most three lanes — three judges with a dissent slot
tally better than five. Three lanes need that dissent slot: `pass` must be
at most 2, so the gate never requires all three lanes to agree, even though
all three can still pass. Two-lane quorums keep the old rule (`pass` may
equal the length of `of`). A quorum's lanes, over
every attempt including fallbacks, must also span at least two vendors
(`anthropic`, `openai`, `google`, `xai`, `cursor`); a quorum confined to one
vendor is refused at load, because a disagreement within one vendor's family
is not the independent evidence a disagreement across vendors is.

A judge never scores its own vendor. At load, conductor refuses a verdict
lane whose `base` could end up on the same vendor as the lane itself — any
attempt's fleet/model against any attempt of the base, fallbacks included,
since which attempt is final is not known yet — and refuses a `collate` that
shares a vendor with any attempt of any lane it would collate over (every
lane in the mission). Both refusals name the two lanes. Set
`"self_judging": "allow"` at the mission's top level to lift them when
self-judging is deliberate; the mission result and `report.md` then carry a
note per pair, `self-judging allowed by the mission: <judge> judges <lane>
on <vendor>`. Any other value for `self_judging` is refused.

`collate` also takes `"rank": true` to become a comparative judge instead of
a free-form synthesis. It asks for exactly one JSON object,
`{"strongest": "<lane name>", "reason": "<one sentence>"}`, with the
generated schema's `strongest` enum restricted to the mission's lane names.
Position in the prompt is itself a bias a judge cannot see past, so
conductor dispatches the ranking collate twice — once with the lanes in
mission order, once reversed — and prices and records both
(`collate.orders`). Agreement across the two orders sets `collate.strongest`
to the winner and the reason given; any disagreement, or an answer naming an
unknown lane or that is not valid JSON, sets `collate.strongest` to null and
the mission not ok (`judges disagreed: <lane>=<n>, <lane>=<n>`, or
`judge <j> order <k> invalid: <reason>`) — a split decision escalates to the
operator rather than being resolved by picking one order's answer. `rank` needs at
least two lanes and, like any structured-output request, is refused at load
on a fleet with no schema flag (Cursor).

### Judge sittings (E4)

A rank collate is not limited to one judge. `collate.judges` names M - 1
more of them (judge 1 is the collate's own `fleet`/`model`); each takes the
same keys a collate does (`fleet` required, `model`, `effort`, `timeout`,
`cap_usd` — defaulting to the collate's own `cap_usd` when unset), and is
refused at load unless `rank: true` (`collate judges need rank`). Every
judge is dispatched in both lane orders — 2M dispatches total — fanned out
in parallel through the mission's own `concurrency` cap rather than one at a
time. Unanimity, every judge and both of its orders naming the same lane,
sets `collate.strongest`; one invalid order anywhere escalates
(`judge <j> order <k> invalid: <reason>`, both one-based) and any
disagreement among otherwise-valid votes escalates too
(`judges disagreed: <lane>=<n>, <lane>=<n>, ...`, descending count then
name). A judge is refused at load the same way a lone collate is if it
shares a vendor with a lane it collates over — labelled `collate` for judge
1 and `collate.judges[<i>]` (zero-based) for the rest, so
`self_judging: allow` still lifts the refusal one pair at a time.

The schema a judge sitting hands every dispatch also accepts an optional
`scores` object, one integer 1 to 10 per candidate lane — welcome, never
required, and folded into `collate.orders[i].scores` /
`collate.judges[i].orders[k].scores` (`null` when a judge did not score).
`collate.tally` (and the mission directory's `tally.json` / `tally.md`)
records the whole sitting: `candidates` (lane names), `votes` (per lane,
across every valid order), `judges` (`judge`, `fleet`, `model`, `forward`,
`reverse`, `agrees`, `scores` — that judge's own mean per lane), `agreement`
(`"unanimous"`, `"split"`, or `"invalid"`), and `mean_scores` (per lane,
over every judge that scored it). `report.md`'s `## Collated` section
prints the tally as a markdown table after the strongest or escalation
line. A one-judge `rank` collate is a one-row sitting: it produces exactly
the receipt it always did, plus `judges: []`, `tally`, and `scores: null` on
each order.

```json
"collate": {
  "fleet": "antigravity",
  "rank": true,
  "judges": [
    {"fleet": "claude", "model": "opus"},
    {"fleet": "codex", "model": "sol", "cap_usd": 2.0}
  ]
}
```

A pipeline is judged on its outputs: `require` applies to the lanes nothing
else depends on, so `build ok, fix failed` is a failed pipeline whatever
`any` would say. In a flat mission every lane is an output, as before. Each
lane's receipt is written to `lanes/<name>.json` the moment it ends, so a
crash mid-mission does not lose the finished stages. A fleet that commits
on its own (Claude Code, Cursor, and Antigravity do) is landed work, not
"nothing to commit".

A lane's `branch` names its deliverable: once the lane's commits land, its
`conductor/<run_id>` branch is renamed to that name (`refactor/x`), so what
the operator merges is not a timestamp. The name is checked before any
fleet spawns: an invalid name, a name already in the repo, a `conductor/`
prefix, or two lanes claiming one name are refused at mission start, not
after a $5 build. A based lane that landed nothing (the fix step after a
clean review) names the tip it was built on, because that tip is then the
pipeline's deliverable; a flat lane that landed nothing has no branch to
name and says so. Upstream lanes keep their run-id branches; the first production
pipeline (2026-09-03) needed a hand rename, which is how this field earned
its place.

## Early cancel, mechanical ranking, and best-of-n

A `require: any` mission may set `"early_cancel": true` (refused at load on
any other `require`, message `early_cancel needs require: any`, since
cancelling the rest only makes sense once one lane passing is already enough
to win). The moment a sink lane settles ok, conductor cancels every other
lane still running (its fleet is killed the way a cap kill ends one: process
group killed, priced from the watcher's last reading, no gate, no commit)
and skips every lane not yet started, without starting anything new. A
cancelled lane's `LaneResult` is not ok; its `skipped` reads
`cancelled: lane <name> already passed`, so `report.md`'s table shows it and
`require: any` still reads true from the lane that actually passed. The
mission result carries `early_cancel: {"winner": "<lane>", "cancelled":
["<lane>", ...]}`, null when nothing needed cancelling. A lane that was
still sitting in the worker pool's queue when the winner passed never
spawns at all: its cancel event is checked when the lane starts, again
before every dispatch and retry, and once more immediately before the fleet
process would be created, so a queued lane's `skipped` reads `cancelled
before spawn: lane <name> already passed` and its receipt, if it got that
far, is `cancelled` with `spawned: false` rather than a paid run. On resume, a lane
`skipped` for this reason is treated as finished rather than rerun, as long
as the mission it belongs to was itself ok — rerunning it would just repeat
the same cancellation.

Once every lane has settled, conductor ranks the sink lanes that were
actually dispatched (not skipped) on a fixed order, best first, and no model
judgment: ok before not; a passing verdict before a failing or absent one;
an untouched test surface (`test_touched: no`) before a touched one; a gate
exit code of 0 before nonzero before none; a smaller `diffs/<lane>.patch`
before a larger one (no patch ranks last); lower `cost_usd` before higher;
mission order as the final tie-break. Bytes and gate results come before any
model judgment because a judge is the expensive, fallible step and a broken
gate or an empty diff is settled evidence, not something worth a model's
opinion (Generative Verifiers, `docs/archive/roadmaps-closed.md` item B5,
<https://arxiv.org/abs/2408.15240>). The result carries `ranking: [{"lane",
"rank", "ok", "verdict", "test_touched", "gate_exit", "patch_bytes",
"cost_usd"}, ...]`, and `report.md` shows it as a `## Ranking` table. A
mission with a single sink still gets a one-row ranking.

`collate` also takes `"candidates": <int>` (0, the default, means every
lane; set it, it must be at least 2 or refused at load) to judge only the
ranking's top N sink lanes instead of every lane, so a judge is not spent
rereading redundant losers. The forward dispatch sees the chosen lanes in
rank order; a ranking collate's schema enum and reverse order cover only
those lanes too. The prompt says which lanes were left out and why
(`omitted by ranking: <lane>, <lane>`), and the collate receipt records
`candidates: ["<lane>", ...]`. `candidates` larger than the number of
dispatched sink lanes just uses what there is.

## Conflict-aware collate: collisions and a resolver lane

Nothing so far looks at where two sink lanes touch the same ground. Two
builds that both edit `mission.py` are judged on prose and patches like any
other pair, and the operator finds the conflict at merge time. Evidence
(`docs/archive/roadmaps-closed.md` item D1): a 27.7% conflict rate across 107k
simulated agentic merges
([AgenticFlict](https://arxiv.org/pdf/2604.03551)), and Cursor's own agent
swarm accumulating 70k conflicts, 7,771 of them on one file
([Cursor](https://cursor.com/blog/agent-swarm-model-economics)).

Once every sink lane has settled (and before the collate, if there is one),
conductor computes `collisions` over whichever sinks left a diff:

- `overlap`: which files each sink's patch touches
  (`conductor.collisions.touched_files`, read from a unified diff's
  `diff --git a/<p> b/<p>` headers; a rename counts both paths), and which
  files two or more sinks touch -- a **hotspot**. Git's quoted header form
  (`diff --git "a/caf\303\251.txt" ...`, which it writes by default for a
  path with a tab or a non-ASCII byte) is decoded back to the path it names,
  on either side independently, so those files count like any other.
- `conflicts`: for every pair of sinks that both left a clean commit,
  whether `git merge-tree --write-tree --name-only` on their two tips would
  actually conflict, and on which paths. A pair whose merge would fail for
  any other reason (a missing tip, a timeout) records that pair's error
  rather than raising.
- `hotspots`: the sorted union of both -- a file two sinks' diffs both
  touch, or that a real merge would conflict on.

`collisions` is `null` when fewer than two sinks left a diff, or on a dry
run; otherwise every mission result carries it:

```json
{
  "overlap": {
    "files": {"mission.py": ["build-a", "build-b"]},
    "hotspots": ["mission.py"],
    "lanes": {"build-a": 1, "build-b": 1}
  },
  "conflicts": {
    "pairs": [{"lanes": ["build-a", "build-b"], "conflicts": ["mission.py"]}],
    "files": {"mission.py": [["build-a", "build-b"]]}
  },
  "hotspots": ["mission.py"]
}
```

`report.md` gets a `## Collisions` section listing each hotspot and the
lanes that touch it, `(conflict: build-a, build-b)` appended when a real
merge would fail there, and `conductor missions` rows carry
`"hotspots": <count or null>`. When a mission sets `collate` and there are
any hotspots, the collate's own prompt gets the same `## Collisions`
section between the original prompt and the lane results, so the judge
sees where the candidates collide instead of grading each in isolation.

A mission may also set `"resolve"`, a dedicated resolver lane:

```json
{
  "resolve": {"fleet": "claude", "model": "opus", "commit": "merge: reconcile candidates"}
}
```

Keys: `fleet` (required), `model`, `effort`, `timeout`, `cap_usd`
(cascades from the mission's own `cap_usd` like the collate's),
`commit` (the resolver's commit message), `instructions`, and `max_chars`
(defaulted the way the collate's are). `resolve` is refused at load on a
mission with fewer than two sink lanes, and on an unknown fleet, the same
as any other attempt.

When the mission has hotspots, conductor dispatches the resolver after the
collate (or right after the sinks, when there is none): one write-mode,
isolated lane from the mission HEAD, gated by the mission's own top-level
`test`, committed with `resolve.commit`. Its prompt (written to
`resolve-prompt.txt`, prefixed like every other dispatch) carries the
original prompt, the `## Collisions` section, one line naming the lane a
rank collate judged strongest (when one ran and agreed), then every
candidate sink's patch, fenced and labelled as another agent's data and
each clipped to `max_chars`, then the instructions. The default
instructions ask for one change that applies the strongest candidate (or
the first lane in mission order when none was named) everywhere except the
hotspot files, keeps what each candidate did right on the hotspot files
themselves rather than picking one wholesale, and expects the answer to
explain what was kept from which lane.

The outcome lands in `result.json` as `"resolve"`:

```json
{"ran": true, "ok": true, "run_id": "...", "cost_usd": 0.41, "tokens": 8213,
 "branch": "conductor/20260906T...", "tip": "abc1234...", "hotspots": ["mission.py"],
 "error": null}
```

`{"ran": false, "reason": "no hotspots"}` when `resolve` is set but nothing
collided, and `{"ran": false, "reason": "dry run"}` on a dry run (nothing is
dispatched either way); `null` when the mission sets no `resolve` at all. A
resolver that ran and failed its gate or its fleet makes the mission not
ok, the same as a failed collate. `report.md` shows a `## Resolve` section
with the outcome, and `conductor missions` rows carry
`"resolve": "ok" | "failed" | "skipped" | null`. The ledger's blocker rules
apply before the resolver starts, exactly as they do before the collate.

A resume that dispatches a second resolver (the first one failed, or a sink
lane reran) keeps the first one's outcome in `previous_resolves`, oldest
first -- what `previous_collates` already does for the collate -- so a paid
resolver is never overwritten by its own rerun. Resume accounting, the
mission's token and cache totals, `conductor spend --by mission`, and
`conductor report`'s join all read it; it is absent on a receipt written
before this, and every reader treats that as an empty list. The resolver's
and the collate's own run receipts also carry `lane` (`resolve`,
`collate`, `collate:<judge>:<order>`) and `mission`, the same two fields a
lane's dispatch stamps, so an auxiliary run is attributable on its own
bytes rather than only through the mission snapshot.

F20: all four readers discover a mission's paid dispatches through one
walker, `spend.effects(snapshot, lanes=...)`, which yields one `Effect`
(`run_id`, `kind` of `attempt | collate | order | resolve`, the lane name
and stage for an attempt, `superseded` for an entry a rerun replaced, and
the summary `record` the receipt carried) per distinct run id, first
occurrence wins. It is the versioned adapter for every receipt generation:
`previous_collates` and judge `orders` from E4, `resolve` and
`previous_resolves` from D13, a bare `final` string from before either. A
resume prices the freshly loaded lane receipts first and fills in from the
prior snapshot, so a lane's own record wins a run id the snapshot also
names. A structural test keeps `report`, `mission`, and `export` from
growing a hand-written walk of their own again.

## Collisions across repositories

A mission whose lanes span more than one repository (per-lane `cwd`, E26)
follows three rules, decided by the operator 2026-09-06: a collision is the
same path in the same repository; each repository runs its own gate; the
resolver never merges across repositories. `overlap` is computed per cwd
group the same way `merge_conflicts` already was -- two sinks in different
repositories are never paired, even when both touch a file of the same
name.

`collisions.groups` (already present for `merge_conflicts`) gains two
fields per group: that repository's own `hotspots` and its own `overlap`,
both unprefixed -- exactly the shape the mission-wide `hotspots`/`overlap`
have on a single-repository mission:

```json
{
  "overlap": {"files": {"shared.txt": ["a", "b"]}, "hotspots": ["shared.txt"], "lanes": {"a": 1, "b": 1}},
  "conflicts": null,
  "hotspots": ["shared.txt"],
  "groups": [
    {
      "cwd": "/repo-a",
      "lanes": ["a", "b"],
      "hotspots": ["shared.txt"],
      "overlap": {"files": {"shared.txt": ["a", "b"]}, "hotspots": ["shared.txt"], "lanes": {"a": 1, "b": 1}}
    },
    {"cwd": "/repo-b", "lanes": ["c"], "hotspots": [], "overlap": {"files": {}, "hotspots": [], "lanes": {"c": 0}}}
  ]
}
```

The moment a mission's sinks span more than one repository, the top-level
`hotspots` and `overlap` become the union of every group's own, each path
prefixed `<cwd>:` (the group's own repository, a colon, then the
repo-relative path) so that two repositories' same-named files never merge
into one hotspot:

```json
{"hotspots": ["/repo-a:shared.txt"], "overlap": {"files": {"/repo-a:shared.txt": ["a", "b"], "/repo-b:other.txt": ["c"]}, ...}}
```

A single-repository mission's top level is exactly its one group,
unprefixed -- identical to what it produced before this. The collate's
`## Collisions` section and the resolver's prompt each read only the group
of the one repository they concern (the collate's own dispatch `cwd`; the
resolver's is the single cwd its sinks share, already enforced at load), so
neither ever sees another repository's paths, prefixed or not. The mission
result gains `"repositories"`: every distinct repository (E26 `cwd`) the
mission's lanes resolve to, sorted.

A recorded golden fixture (see "Golden missions" below) gives every
repository beyond the mission's own `cwd` its own placeholder, `<cwd2>`,
`<cwd3>`, ..., in the order its lanes first name it. `golden.replay` (and
`golden.check`) take a `cwds` argument mapping each extra placeholder to a
real directory; a placeholder the caller does not name gets a fresh, empty
repository under the replay's own temporary home instead, so a cross-repo
fixture still replays with no arguments at all.

