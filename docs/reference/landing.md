# Landing

conductor land merges a lane's branch, gates the merged head, and attests the
mission.


Every release has ended with the same sequence by hand: merge the fix
lane's branch into the checkout's branch, gate the merged head in a fresh
worktree, run `golden check`, attest the mission, and only then bump the
version. `conductor land` is that sequence made repeatable, for the lead to
run after reading the diff and once the reviewers have covered it:

```
conductor land MISSION_ID --lane fix
```

It reads the lane's receipt for its `branch` and its `tip_sha`, resolves
`refs/heads/<branch>^{commit}` once in the lane's own repository, and refuses
unless that commit is the one the lane receipted -- the same check
`mission.py` makes before it trusts a receipt at all. A branch that moved
since the run (a rebase, a stray commit, a hand-made reset) is refused with
both shas named, and the fully qualified ref means a tag of the same name can
never answer in the branch's place. Everything downstream -- the
already-merged shortcut, `--dry-run`'s log, and the merge itself -- names that
one pinned sha, never the branch name a second time. The destination must
also be the lane's own repository, or a worktree of it: an unrelated
repository holding a same-named branch is refused rather than merged into.

It refuses (exit 3, nothing changed) when the mission or lane does not exist,
the lane has no branch or no tip commit on its receipt, the branch does not
exist in the lane's repository or no longer points at the receipted commit,
the checkout (default: the lane's own repository) is not a git repository, is
not the lane's repository, is not on a branch, has uncommitted changes, or is
mid-merge, the checkout's current branch is the branch itself, or the branch
shares no history with the checkout's HEAD (a branch that diverged from an
older tip is the ordinary case and lands with a merge commit). A pinned tip
already reachable from the checkout's HEAD is not a refusal: once every
identity check above has passed, it exits 0 and reports `already_merged`
without touching the tree. The one exception is a merge an earlier `land`
of the same lane made and could not undo -- a receipt with `ok: false` and
`reset: false` whose `merge_sha` is still reachable from HEAD. The branch
then holds a merge that never passed gate, golden, or attest, so the
shortcut refuses rather than reporting a green landing over it. It also refuses when it finds itself running inside a lane's own
environment (`CONDUCTOR_LANE`, set on every dispatched process) -- `land` is
the lead's own act, never a fleet's, and no mission or lane spec can make it
run one.

Landing itself: `git merge --no-ff <pinned sha>` onto the checkout (the
message still names the branch, but what git resolves is the sha), a fresh
worktree of the merged head under `$TMPDIR`, the gate (`--test`, else the mission
snapshot's own `test`, else refused) under the same timeout and environment
a lane's own gate gets, `golden check`, and `attest.attest_mission` for the
mission -- in that order, any red step aborting the rest. The golden step
runs as a subprocess of the merged worktree's own `conductor` (`PYTHONPATH`
at its `src`, cwd at the worktree), not through the `golden` module this
process already imported: when what is landing is a change to golden, the
scheduler, or the parser, the merged tree's new fixtures have to be replayed
by the merged tree's new code. The child asserts where it imported
`conductor` from and refuses if it is not the worktree; a merged tree with
fixtures but no `src/conductor` of its own keeps the in-process replay. A failing step after the merge commit exists resets the
checkout to its pre-merge HEAD (only when that commit is still HEAD and the
tree is otherwise clean) and reports the failing step with its last twenty
lines of output; the branch itself is never deleted, on any path. `--dry-run`
runs every refusal check and prints what would merge (`git log --oneline
HEAD..<pinned sha>`) and the gate command, without merging anything. The
merge commit and the pinned tip are both on the result and in the receipt, as
`merge_sha` and `tip_sha`.

Every call, refused or not, is receipted to
`$CONDUCTOR_HOME/missions/<mission-id>/land/<lane>-<UTC timestamp>.json`
(except when the mission itself does not exist, exactly like salvage); `land`
never dispatches a fleet, so it never extends the mission's signed receipt
chain, and it never bumps a version, never pushes, and never runs inside a
mission.

