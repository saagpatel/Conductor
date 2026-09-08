# Resuming a mission

A parked or interrupted mission continues from receipts on disk rather than from
a fleet's memory.


Resume an interrupted or failed mission in its existing directory:

```
conductor mission --resume MISSION_ID
```

`--answer-file PATH` reads a file's content as the answer, for a `kind:
"human"` pause (see Human lanes, above) only -- it is refused on a `kind:
"lane"` or `kind: "spend"` pause, and mutually exclusive with `--answer`.

`MISSION_ID` is the directory name under `$CONDUCTOR_HOME/missions`. Conductor
loads that directory's versioned `mission.json` snapshot, keeps each lane whose
receipt is ok and not skipped, and reruns missing, failed, skipped, interrupted,
or incomplete lanes. A kept lane is never dispatched or committed again, and
its gate is not rerun. Its recorded answer, diff, and verdict artifacts must
still exist. If the lane landed commits, its tip commit must still exist; a
lane with a named deliverable `branch` is kept only while that branch still
points to the recorded tip. A branch left at the previous failed tip is deleted
before that lane reruns, while a branch moved elsewhere is treated as somebody
else's work and the resume is refused.

The `max_cost_usd` ledger covers the whole mission across resumes. Prior run
receipts seed both priced and unpriced spend, so restarting is not a fresh
budget. Collate is kept only when its successful result and answer still exist
and every summarized lane was kept; otherwise it runs again. `running.json`
holds the mission's pid, start time, and host while it is active. A live lock
refuses a second runner; on the same host conductor also compares the process
start time so a recycled pid cannot keep an old lock alive. A lock from another
host is refused rather than guessed stale; after verifying that host is no
longer running the mission, the operator may remove the lock. A demonstrably
stale local lock is removed and recorded in the result.

A lock is published whole: its JSON is written to a temp file beside it and
hard-linked into place, so no contender ever reads a half-written one, and
the link fails rather than replacing a lock that already exists. Only a
readable lock can be proven stale -- an empty, truncated, or unparseable one
refuses the claim, naming the file, instead of being reclaimed. Each lock
also carries an `owner` token minted by the run that took it, and both the
release at the end of a run and the removal of a stale lock check it, so
neither can remove a lock this run does not hold. The mission-file lock
(below) works the same way.

The boundary is deliberately honest. A lane that half-committed before a crash
has no trusted ok receipt, so it is rerun from its base. Kept lanes are trusted
on their receipts and artifact paths; beyond commit and named-branch existence,
their work is not re-verified during resume.

The artifacts themselves are authenticated on their bytes, not just their
paths. When a lane settles, its receipt records the sha256 of the answer, diff,
and captured deliverable it left behind, and resume rehashes those files before
a downstream lane is allowed to read them. A file that no longer hashes to what
the receipt recorded is not that lane's own output, so the lane is not trusted:
it reruns, with a note naming the artifact whose bytes differ, and its earlier
paid attempts stay in the budget as usual. A receipt written before conductor
recorded digests has none to check, so it is trusted on its paths exactly as
before and the resume notes that it was trusted on path only.

A recorded digest requires readable bytes: an unreadable file or a null path
does not downgrade that check to legacy path-only trust. A missing repository
also refuses reuse when a commit or branch must be checked. For an existing
repository, two `GIT_UNRUN` results still retain the receipt with a note;
this compatibility policy avoids repeat payment under machine load, but it
does not establish that the Git check passed.

Resume re-reads each dispatch's cost from its run receipt. A cancelled run
with no price retains `unknown_cost_dispatches` without becoming a
budget-blocking unpriced failure. A timeout receives the same classification
on resume as it did live. Recovered attempt summaries retain these markers
if the run receipt subsequently becomes unavailable.
