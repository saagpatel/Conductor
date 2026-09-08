# Cost accounting

Spend, cache hits, caps, breakers, and the rolling ceiling are recorded from
receipts.


Only the claude fleet reports dollars. Codex reports usage only in its
`--json` event stream (now always on; the answer still lands in the `-o`
file), and Cursor and Antigravity report tokens with no price. conductor
prices those from a dated list-price table (`conductor prices`) and marks the
result `cost_basis: "estimated"`; a fleet's own figure is `"reported"` and is
never overwritten. An unpriced model yields `null`, not `$0.00`, so a gap
shows as a gap. Override or extend the table without a code change in
`$CONDUCTOR_HOME/prices.json` (a null entry drops a model; a malformed file
falls back to defaults rather than stopping a run).

The table itself is dated list price transcribed from local notes
(`prices.AS_OF`), not a live billing feed, and it can drift out from under
the vendor's own page between updates. A `cursor-grok-4.6` or
`composer-2.5` entry in particular does not establish which Cursor variant,
tier, or long-context billing band actually ran; see the per-model notes in
`prices.py` before trusting a Cursor figure past a rough order of magnitude.
An estimated run's own receipt carries the specifics: `usage.price` names
the table `key` that matched, whether that entry is a table `"default"` or
an operator `"override"`, and the table's `as_of` date -- so a stale table
is visible on the run it priced, not only in `prices.json` itself. `price`
is `null` on a `"reported"` figure (the fleet's own number, not the table's)
and on an unpriced run.

Token conventions are normalized first: `input_tokens` excludes cache reads
on every fleet (OpenAI and Google count them inside the input figure and are
split out), and `output_tokens` includes reasoning (Antigravity's separate
`thinking_tokens` are folded in, as Google bills them).

Measured on 2026-09-03, a one-line answer to "what is this README for":
codex/luna $0.0037, antigravity $0.0228, cursor/composer-2.5 $0.0131, and
the claude/haiku collate $0.0599. Startup, not the work, still dominates.

## Cache accounting

`cache_write_tokens` is tracked beside `cache_read_tokens` everywhere the
latter is: a dispatch's `usage`, a lane's summed totals, `conductor spend`'s
`cache_write_tokens` column, and a mission-level `cache` block in
`result.json` and its summary -- `{"input_tokens", "cache_read_tokens",
"cache_write_tokens", "hit_rate"}`, summed over every lane's attempts and the
collate. `hit_rate` is `cache_read / (input + cache_read + cache_write)`,
rounded to three places, and `null` when nothing was read at all. `report.md`
shows one line: `Cache: <read> read, <write> written, <input> uncached; hit
rate <pct>`. See "Cache-friendly prompts" above for what makes a hit possible
in the first place.

## Spend reports

`conductor spend` totals the durable receipts under `$CONDUCTOR_HOME/runs`,
grouped by fleet by default or by day, model, mission, or run. `--since` is
inclusive, `--until` is exclusive, and both accept a UTC date or ISO datetime;
`--json` emits machine-readable rows. Estimated and unpriced runs are counted
separately, so a missing price can never make a run look free. Malformed or
unreadable receipts are counted as skipped on the total row, and dry runs
are excluded and counted there too: a rehearsal spends nothing, and folding
it into "unpriced" made 25 of the first 43 receipts read as unverified spend.

### Ledger report

`conductor report` turns the same durable receipts into the figures behind
AGENTS.md's Shape A rules, so the lead can check the prose against the
bytes instead of re-deriving it from `conductor spend` and `conductor runs`
by hand. `--since`, `--until`, and `--json` work exactly as they do for
`conductor spend`; a bad window exits 2 the same way. Every dry run is
excluded, as in `conductor spend`.

Every dispatch's receipt now carries `stage` (the pipeline stage it ran as:
`build`, `review`, `fix`, or `null`), `lane`, and `mission` (which mission
lane made it, and that mission's id; both `null` for a plain `conductor
dispatch`). A receipt written before this field existed carries none of the
three; `conductor report` joins it back to its mission snapshot under
`$CONDUCTOR_HOME/missions` to recover its lane and stage, the same way
`conductor spend --by mission` already joins a run to its mission name.

The report has seven sections, in this order:

- **Vendor and stage**: runs, ok count, cost, unpriced runs, mean and
  median duration (over the runs whose receipt carried a finite,
  non-negative `duration_s`; `--json` also carries `unknown_durations`, the
  runs in the group that did not and are out of both figures rather than
  averaged in as zero-second runs), cache (`cache_read_tokens` over the sum of
  `input_tokens`, `cache_read_tokens` and `cache_write_tokens`, summed per
  group, as a percentage -- blank when that sum is zero),
  cap misses (`kind == "cap"`), gate failures (`kind == "gate"`), and mean
  tool calls (over the runs whose receipt carried a `breaker` block;
  `--json` also carries `unknown_tool_calls`, the runs in the group that
  did not -- a crash or spawn-failure writes `breaker: null` -- and are
  out of the mean rather than averaged in as zero), grouped by the vendor
  behind the model (`fleets.py`) and the pipeline stage (`unmatched:<fleet>`
  when the model id matches none of that fleet's registered ids, so a
  stale cursor id cannot share Composer's vendor key; `null` stage for a
  dispatch outside a staged pipeline).
- **Error kinds**: count and cost per `errors.error_kind`.
- **Reviewer finding rate**: among `stage: review` dispatches that wrote an
  answer and were not interrupted or cancelled (a run conductor itself
  stopped can leave a partial `answer.txt`, and scoring it reads a stop as a
  completed sitting), `verdicts.review_verdict` parses the LAST non-empty line (a
  trailing code fence is skipped first): exactly `NO_FINDINGS` counts as
  zero findings, `FINDINGS: N` counts as `N`, and anything else -- including
  an `answer.txt` that could not be read or decoded -- is `unparsed`, its
  own column -- a reviewer that narrates before its verdict
  (Grok routinely does) used to read every one of those narrations as a
  finding, since the old check was `answer.strip() == "NO_FINDINGS"` against
  the whole reply. `rate` divides by parsed runs only, excluding
  `unparsed` from the denominator.
- **Reviewer precision**: per reviewer vendor, over missions that have both
  a `stage: review` lane whose verdict parsed and a `stage: fix` lane that
  recorded dispositions (see below): findings written, and how many of
  them the fix lane marked fixed, refused, already true, or wording-only,
  plus `unparsed`, the review lanes on that vendor whose verdict line did
  not parse at all (F15 item 2; the same column the finding-rate table
  carries, counted here per vendor). A review conductor itself stopped
  (the last attempt's run receipt carries `interrupted` or `cancelled`)
  is not a sitting here either, matching the finding-rate table: a lane
  receipt does not carry those flags, so the join is that last attempt's
  `run_id`.
  `precision` is `fixed / (fixed + refused)`, blank under three total
  dispositions -- not enough to read as a rate. D20: a disposition is
  counted once per finding it names. Entries are keyed by (mission,
  reviewer lane, finding index) and the last one a fix lane recorded wins,
  so a restated disposition (or two fix lanes on one mission naming the same
  finding) counts once and the rest are counted as `duplicate`; a
  disposition whose index names no finding the review lane actually
  reported is counted as `unmatched` and left out of every per-vendor tally,
  so `corrected_rate` (`fixed / findings`) can never exceed 1.0. The line
  under the calibration lines carries all four totals: `dispositions naming
  an unknown lane: N | malformed disposition lines: M | duplicate
  dispositions: D | dispositions naming no reported finding: U`, and
  `--json` carries `dispositions_duplicate` and `dispositions_unmatched`
  beside the other two. A review lane whose receipt parsed no `items` at
  all (one recorded before findings were numbered) cannot match an index
  either way, so its dispositions still count as before. W7: precision is
  fixer agreement, not a truth figure, and it is scored only over the
  mission selection described above, so two more columns name that
  selection's size: `missions`, the distinct missions whose dispositions
  contributed to a vendor's row, and `undispositioned`, the `stage: review`
  lanes on that vendor that parsed *and reported at least one finding* but
  whose mission recorded no dispositions at all (counted in reviewer
  finding rate, not here). A lane that reported `NO_FINDINGS` has nothing a
  disposition could ever name, so it is never counted here. Both
  are printed after `precision` and carried in `--json` too, and a `basis`
  string spells the same two numbers out in prose, printed as its own line
  under the table: "fixer agreement over N mission(s) with dispositions; M
  review lane(s) not dispositioned". W11: a parsed review lane that
  received no matched disposition of its own on a mission that recorded
  dispositions naming only other lanes is counted as undispositioned too.
- **Missions**: cost, whether the mission was ok, how many lanes it
  declared, whether any lane hit its cap, how many times
  `conductor salvage` was run against it (`salvaged`, 0 when
  `$CONDUCTOR_HOME/missions/<id>/salvage/` does not exist), and how many
  times `conductor land` was run against it (`landed`, same rule against
  `$CONDUCTOR_HOME/missions/<id>/land/`). Three more columns turn AGENTS.md
  rule 2's "about a dollar per spec item" into a measured figure: `merged`
  is the land receipts whose merge happened (`ok` true, not a dry run, not
  the already-merged answer; a refusal receipt counts in `landed` only),
  `items` is how many spec items the build lane's evidence map names, read
  from the copy the runner captured after the gates and only when the
  runner recorded that copy as parsed and ok (blank on a mission that
  predates the map, never 0 for unknown), and `usd_per_item` is the
  mission's whole cost over `items` when a merge happened. `unpriced` is
  how many of the mission's runs spawned and came back with no price:
  `cost_usd` is then a lower bound, not the mission's cost, so
  `usd_per_item` is blank rather than a figure divided out of one. It is
  blank for the same reason under `--since`/`--until`, which bound the runs
  that reach the row while `merged` and `items` are read from the mission's
  whole life; `--json` carries `windowed` so a withheld figure is not
  mistaken for a missing map. One line under
  the table sums it: how many missions merged and what they cost, and,
  over the subset that is fully priced, unwindowed, and with an evidence
  map, that subset's own cost (`items_cost_usd`) and the cost per landed
  item. A merged mission without a map is in the first pair of figures
  and out of the division, so it neither inflates nor deflates the rate; a
  mission whose own cost is a lower bound is out of it on the same
  grounds. `with_items` counts that subset, not "has a map".
  `--json` carries the same four columns per mission -- the `merged` column is
  emitted under its field name `landed_ok`, beside the `landed` count the
  table's own line sums.
- **Wall clock**: one row per mission this report included -- every mission
  on disk when the report is unwindowed, and only missions with a run
  inside `--since`/`--until` when those are set, the same bound as every
  other table -- from that mission's own
  `result.json` `wall` block -- `{"launched_at", "finished_at", "wall_s",
  "paused_s", "gate_s", "lanes_s", "idle_s", "occupied_s",
  "critical_path_s", "lead_s"}`. `launched_at` is the
  mission's first launch, carried across every resume; `wall_s` is the
  span since; `paused_s` sums the time each pause point actually sat
  waiting for an answer (from `pause.json`'s own `asked_at`/`answered_at`
  pairs); `gate_s` sums every lane's own gate, its clean-gate re-run, its
  reproduce gate, and its setup/teardown commands' time from its run
  receipts; `lanes_s` sums every lane's dispatch time; `idle_s` is time the
  scheduler had nothing running and nothing ready to start, carried across a
  resume from the mission's own prior `result.json` (not the scheduler's
  clock, which only spans the current process) and added to by this run's
  own idle time. `lanes_s` and `gate_s` are lane-work sums that overlap
  each other whenever more than one lane runs at a time, so neither one nor
  the pair is a decomposition of `wall_s`. `occupied_s` is the one that is:
  the length of the union of every attempt's interval, where an attempt
  starts at the UTC stamp at the front of its run id and ends its own
  `duration_s` plus that run's gate seconds later, counting overlapping
  attempts once. `critical_path_s` is the longest path through the lane
  dependency graph (a lane's `needs`, `base`, and `resume` edges), each lane
  weighted by its final attempt's duration plus that run's gate seconds,
  which is the floor no amount of concurrency gets under; collate and
  resolve are not lanes and are never on the path. `lead_s` is `wall_s`
  minus `occupied_s` minus `paused_s`, clamped at zero: elapsed time the
  mission was neither waiting on the operator nor running a dispatch, which
  is the lead's own integration time. Each of the three is blank, never
  `0`, when the receipts cannot answer (an attempt with no parseable start
  or no measured duration, or a gate that ran before its time was
  recorded). The report prints `wall_s`, `paused_s`, `gate_s`, `lanes_s`,
  `idle_s`, `concurrency`, `busy` (`lanes_s / (wall_s * concurrency)`,
  blank when `wall_s`, `lanes_s`, or `concurrency` is blank or `wall_s` is
  zero), `occupied_s`, `critical_path_s`, `lead_s`, and `stretch`
  (`wall_s / critical_path_s`, blank when either is blank or the path is
  zero), which is how much longer the mission took than its own critical
  path and the figure to read for that instead of `busy`. A mission
  recorded before this field existed still gets its row, every figure
  blank, never skipped or read as `0`.
  `report.md` carries the same figures as one line under the mission
  header, and `conductor missions` carries `wall_s`.
- **Rules**: the figures behind AGENTS.md rule 7 (each review-stage
  vendor's cap-miss count and finding rate) and rule 10 (for Claude's build
  and fix stages, the runs killed at their cap). D21: a cap loss is
  reported in three cohorts -- `gate passed`, `gate failed`, `gate not run`
  -- beside the stage's `total`, because a receipt's `gate_passed` is also
  True when no gate ran at all (`runner._gate_passed` reads "nothing to
  fail" as not-failed, which is right for `ok` and wrong here). A run the
  watcher kills at its cap never reaches its gate -- `runner.dispatch` gates
  only when the run had no error -- so it now lands in `gate not run`
  instead of reading as a green run lost at the cap, which is the case rule
  10's dollar was written for. A stage with no Claude run at all reads
  `n/a`, never `0`, so a missing stage is never mistaken for a clean one.

`salvaged` is the only trace of a salvage in this report: `conductor
salvage` never dispatches a fleet, so nothing under `$CONDUCTOR_HOME/runs`
could otherwise count it (see the Salvage subsection above). `landed` is the
same trace for `conductor land` (see "Landing" above).

A mission lane's own receipt (`lanes/<name>.json`) carries the same parse:
a `stage: review` lane gets `review` (`verdicts.review_verdict` over its
own answer), a `stage: fix` lane gets `dispositions` (every `DISPOSITION:`
line `verdicts.fix_dispositions` found, in order) and
`dispositions_malformed` (how many opened with `DISPOSITION:` but did not
match the shape). `report.md`'s lane table carries a short `review/fix`
column reading the same thing -- `NO_FINDINGS`, `3 findings`, `unparsed`,
or `2 fixed, 1 refused`. A mission recorded before this shipped carries
neither key on its lane receipts; they load as `None` and the report skips
it the same way it skips any other lane with nothing to compute from.

## Reprice

A parser convention change leaves every receipt already on disk under the old
convention, so `conductor spend`, `conductor report`, the rolling ceiling, and
`forecast.py` then read a mix of two conventions. That happened on 2026-09-08
when `usage_from_raw` stopped subtracting `cache_read` from `input_tokens` for
cursor and antigravity: fixing the parser did not fix the ledger.

`conductor reprice` walks `$CONDUCTOR_HOME/runs` and, for every run directory
that has both `stdout.log` and `result.json`, re-parses the stored stdout with
the current `outputs.parse`, compares that to the receipt's stored `usage`, and
reports or rewrites the difference. `--dry-run` is the default. `--apply`
rewrites, but only after archiving every `result.json` in the corpus to a
timestamped `runs-receipts.backup-<UTC>.tgz` beside the runs directory.
`--json` emits the same summary as a machine-readable object. Exit 0 whether
or not anything moved; nothing found is a correct and expected result.

Two refusals, which are the point of the command:

1. A `cost_basis: "reported"` cost is never recomputed. The vendor printed a
   dollar figure; that figure is evidence. Token counters on such a receipt may
   be corrected; `cost_usd` may not.
2. A receipt whose token counters did not move is not touched at all, even when
   `prices.estimate` now returns a different figure. Re-price what the parser
   changed; never let a price-table revision walk backwards through stored
   history.

When it writes, it rewrites only the `usage` token counters,
`usage.total_tokens`, and -- where `cost_basis == "estimated"` --
`usage.cost_usd` and `usage.price`. Verdicts, `ok`, commit state, `cost_basis`,
and every other field are left as found. A receipt whose stdout re-parses to no
usage, a receipt with no `usage` object, and a `dry_run` receipt are skipped
and counted, not zeroed.

## Per-dispatch caps

`--cap-usd` (or `cap_usd` in a mission) bounds one dispatch in dollars, the
unit the wall-clock timeout only approximates. Each fleet is capped the way
it allows, and the result's `budget` field says which:

| Fleet | `enforcement` | What happens |
|---|---|---|
| `claude` | `native` | `--max-budget-usd`; Claude Code stops itself and reports the spend. |
| `codex` | `watcher` | conductor tails the session rollout's running totals, prices them, and kills the process group the poll after the estimate crosses the cap. |
| `antigravity` | `watcher` | the same, over the per-step usage in agy's `stream-json` output. |
| `cursor` | `post-hoc` | usage arrives once, at the end; the cap is checked then. |
| `script` | `none` | costs nothing; `cap_usd` is refused at dispatch rather than enforced. |

What "overshoots" means depends on the enforcement kind: `native` reports
whatever Claude Code itself stopped at, so that figure is the fleet's
promise, not conductor's; `watcher` (codex, antigravity) can only kill after
the fact, so it overshoots by at most one model response plus one
two-second poll; `post-hoc` (cursor) gets its usage once, at the end, so the
whole run can pass before the cap is even checked, and the mission ledger's
`outstanding_cap_usd` is the only figure that names what it could still
turn out to cost -- a report, never an admission check, written into the
finished mission's own `budget` block and, while the run is live, into
`pause.json` at every park and into `running.json` at every dispatch start
and finish, so a reader can watch it move in either file; `none`
(script) never overshoots, being free. A run over its cap is not `ok` (`failure: "over budget: $3.0000 against
a $1.0000 cap"`), whether it was killed or merely judged afterwards; work it
landed is still on its branch. The watcher runs on every codex and
antigravity dispatch, cap or not, because it is also the only price a run
that conductor killed can get: a timed-out Codex dispatch used to land in
the ledger as `cost_usd: null`. A cap on an unpriced model is refused before
spawn rather than silently unenforced, and a capped run that comes back with
no usage at all fails closed (`cap unenforced: the run came back unpriced`)
instead of reading as within budget.

A `script` dispatch is the one exception to "unpriced fails closed": it
never sets a cap (refused if it tries), so it is never unenforced -- it is
**free**. Its `budget` block reads `{"cap_usd": null, "free": true, ...}`
and its `cost_usd` is `0.0`, a third, verified state beside "capped and
priced" and "capped and unpriced" (see "Script lanes" above).

### Cap grace (E24)

`--cap-grace-usd` (or `cap_grace_usd` on a lane or an attempt) adds a small,
opt-in band on top of `--cap-usd`, so Claude Code's own terminal message has
room to finish instead of being cut off mid-summary: `--max-budget-usd`
carries `cap_usd + cap_grace_usd` as one figure, so a run that finishes
inside the band is an ordinary success with `grace_used` above zero, and a
run Claude Code stops on that figure is over budget as before. F5: on a `cursor` lane
in `mode: read`, whose cap is post-hoc (usage arrives only after the run, so the
verdict is computed then, not while it runs), the same band widens that verdict
instead: the run is over cap only when the estimated cost exceeds `cap_usd +
cap_grace_usd`, so a complete review a few cents over cap_usd settles ok rather than
failing the lane and skipping the fix stage behind it (rule 7's trap). It is
enforceable on the `claude` fleet (native cap) and a `cursor` read lane (post-hoc cap)
only -- refused on a `cursor` write lane (a write lane's cost is bytes, and the band
would only buy more of them) and on every other fleet or mode, where a watcher-killed
run never gets a terminal message to finish and there is no post-hoc verdict to widen.
It is refused without a `cap_usd`, refused
above a $0.50 ceiling, and stated per lane or per attempt -- never on a
mission or a mission-level `cascade`, and a lane's own grace never cascades
onto its fallback attempts. The receipt's `budget` gains `grace_usd` (what
was configured) and `grace_used` (how much of the band a finished run
actually drew on: zero within the plain cap, capped at `grace_usd` itself
once a run clears the whole band); both are omitted, not null, when no
grace was set. It changes nothing else: the mission ledger still charges
the actual cost, so grace draws on `max_cost_usd` like any other spend.

### Breakers

Four progress breakers run beside the budget watcher in the same two-second
poll loop:

- the stall breaker kills a fleet whose `stdout.log` size has not changed for
  600 seconds by default (any change resets it, including a truncation; a
  fleet running a long suite inside one tool call is silent until it returns,
  so the figure is sized for that). Tool calls reset the tool-idle breaker,
  not this one. `conductor dispatch --stall-timeout` and every mission lane
  both default to 600; the 900 on `Spec` itself is a dataclass default no CLI
  or mission path reaches;
- the loop breaker kills after 6 identical consecutive tool-call signatures,
  except a signature beginning `edit:` -- only a `file_change` event produces
  one, it names the paths and never the content, so six successive edits to
  the same file are indistinguishable from a loop and are exempted as
  progress. Editing six *different* files yields six different signatures and
  never reaches the limit at all;
- the tool budget kills after more than the configured total tool calls, and
  is off by default;
- the tool-idle breaker kills when no tool call has arrived for the configured
  seconds (tripping with `idle: no tool call for {idle_s}s`), and is off by
  default because a read lane thinking through a long review legitimately makes
  no tool calls.

Set `--stall-timeout 0`, `--loop-limit 0`, `--max-tool-calls 0`, or `--tool-idle-timeout 0` to disable
that breaker. Missions use the corresponding `stall_timeout`, `loop_limit`,
`max_tool_calls`, and `tool_idle_timeout` attempt keys, inherited like `timeout`. A trip kills the
whole process group, skips the gate, and is priced from the watcher's last
reading exactly like a cap kill. The receipt records the reason, tool count,
and last-output age. Claude dispatches use `stream-json --verbose` so their
tool calls reach these breakers before the final result.

### Liveness

While a dispatch runs, conductor writes `liveness.json` in the run directory on
every poll tick (and once before the first wait), atomically. It records `at`,
`elapsed_s`, `pid`, `stdout_bytes`, `spend_usd`, and, when a breaker exists,
its `tool_calls`, `last_output_age_s`, `last_tool_call_age_s`, and `tripped`
fields. When the run ends, `liveness.json` remains in place while `result.json`
becomes the authoritative receipt. `conductor runs` inspects these files: a run
with a fresh heartbeat is reported as `running`; if the heartbeat is older than
30 seconds (`LIVENESS_STALE_S`), status becomes `silent` to indicate the watching
conductor process stopped writing; and a directory with neither file is reported
as `incomplete`. `silent` is a flag for the operator, nothing more: conductor never
modifies, moves, reclaims, or deletes a run directory, and nothing is reclaimed.

## Rolling spend ceiling and unattended launches (E9)

A mission's own `max_cost_usd` bounds what that one mission may spend; it says
nothing about what conductor, across every other mission and standalone
dispatch, has spent in the last hour. `run_mission` checks a second, rolling
ceiling: the default is $10.00 over the last 60 minutes and $25.00 over the
last 24 hours, summed from the run receipts under `<home>/runs` the same way
`conductor spend` does. An unpriced run (no `usage.cost_usd`) still counts as
a run, in `unpriced_hour`/`unpriced_day`, never as dollars; a free `script`
(E6) run is priced at exactly $0.00 and so never inflates either bound. A
mission file may override either bound:

```json
"ceiling": {"per_hour_usd": 5.0, "per_day_usd": null}
```

`null` disables that bound for this mission; the object must name both keys
(a mission that names one and forgets the other would otherwise silently
inherit a default it never saw). The check runs once, after `validate()` and
before the running lock, on a launch and a resume alike (a resume starts
dispatches too), and never on a dry run. Over a bound, the mission never
starts:

```
spend ceiling: $10.42 in the last hour is over the $10.00 per-hour ceiling
```

A mission that passes carries what it saw at start on the result and in
`result.json`:

```json
"ceiling": {
  "per_hour_usd": 10.0, "per_day_usd": 25.0,
  "hour_usd": 3.2, "day_usd": 11.6,
  "unpriced_hour": 0, "unpriced_day": 1
}
```

**`--unattended`** (`conductor mission FILE --unattended`, also with
`--resume`) runs a mission only when it is safe to run with nobody reading:
it refuses when the mission has a human lane (nobody to answer), a
write-mode attempt on a `fix`-stage lane that `pause.before` does not name
(nothing may land without a lead reading the review), a write-mode attempt
on a lane with no stage at all (an unstaged write lane is a landing nobody
reads), or a `resolve` block (the resolver writes). Build and adversarial
write lanes, and every read lane, are allowed. The result carries
`unattended: true`; the flag is a launch property, not a mission property,
so the snapshot records nothing new for it.

**The file lock.** Beside `running.json` (keyed by the mission run),
`run_mission` claims `<home>/locks/<sha256 of the resolved mission source
path, first 16 hex>.json` whenever the mission has a source file, with the
same `{pid, started, host}` body and staleness rule as the running lock, plus
the id of the mission now holding it. A second launch of the same file while
the first is alive is refused, naming that mission id and the lock path; a
stale lock (holder no longer alive) is replaced with a note, the same as a
stale running lock. The lock releases when the mission returns, however it
returns: normally, paused, interrupted, or by exception. A mission built in
code with no source (as the test suite does) takes no file lock.

Salvage is never automated: a kept worktree still needs a lead to gate it,
read it, and commit it by hand before any review or fix mission runs against it.

