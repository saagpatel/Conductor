# Salvage

A kept worktree whose own gate was green can be gated, committed, and followed
by a review-and-fix mission.


AGENTS.md rule 6: when a write lane's own gate is green but the clean gate
rejects it (a flaky test under load, or the base tree's tests failing
against new source), conductor keeps the lane's worktree rather than
discard uncommitted work. Today the lead reads that diff, gates it, and
commits it by hand, then writes a review-and-fix mission by hand too.
`conductor salvage` is that path made repeatable:

```
conductor salvage MISSION_ID --lane build
```

It reads the lane's receipt and the mission snapshot, and refuses (exit 3)
when the mission or lane does not exist, the lane was not kept, its
recorded worktree is missing on disk or is not a git worktree of the
lane's own repository, or the lane's effective test command is empty. The
contract salvage gates under is the producing attempt's, not the lane's
first: the kept worktree comes from the attempt that actually ran, so its
repository (`cwd`, which a lane or one of its attempts may override), its
`test` command, and its `timeout` come from that same attempt -- a fallback
is never judged under the primary's gate. What a scratch gate cannot rebuild
it refuses rather than substitutes: an attempt declaring `setup`, `include`,
`ports`, or a `teardown` is answered with `salvage cannot reconstruct
<setup|includes|ports|env> for lane <name>; gate the kept worktree by hand`,
because a verdict without the lane environment is a verdict under a
different contract. It then runs two gates from the kept worktree, each in a
fresh scratch copy under `$CONDUCTOR_HOME/salvage/<mission-id>/<lane>/`: the
tree's own gate (everything transplanted, new tests included) and the clean
gate (test surface restored from the base; recorded as skipped, not judged,
when the lane ran under `test_policy: allow`, rule 3) -- never committing,
writing into, or touching the index of the kept worktree itself -- and
prints the worktree, its base and HEAD shas, whether it is dirty, the
diff, and each gate's exit code and tail.

The diff and `diff_sha256` on the result are the bytes salvage gated: they
are read from the kept worktree at salvage time, not copied from the run's
receipt, since a worktree repaired by hand after the run would otherwise
pass carrying the pre-repair digest. The run's own digest is kept beside
them as `lineage_diff_sha256`, evidence of where the tree came from rather
than of what was gated; the two differ exactly when the tree was touched
since the run. A tree that changes while the gates are running is refused
outright. Exit 0 means both
gates passed, 1 means one failed. Every call, refused or not, writes a
receipt to `$CONDUCTOR_HOME/missions/<mission-id>/salvage/<lane>-<UTC
timestamp>.json` (except when the mission itself does not exist: a typo
never conjures a mission directory); salvage is the lead's own act, not a
lane's, so it never extends the mission's signed receipt chain.

Once the lead has read the diff and committed it in the kept worktree by
hand, `--emit PATH` (with `--ceiling none|default|H,D` for the follow-on
mission's E9 rolling-spend ceiling, same default and meaning as
`shape a --ceiling`; `--items` and `--modules` are required by the CLI but
size only `build_cap`, and a follow-on mission has no build lane, so today
they change nothing about what it emits -- its fix cap comes from
`USD_FIX_BASE`, findings, the scheduler tax and the Claude summary alone)
writes a follow-on mission there: `shape.shape_a_followon`, the
same Shape A shape as `conductor shape a` except there is no build lane --
the kept worktree, already at the lead's commit, is the mission's `cwd`
directly, so the two review lanes and the fix lane need no `base` and the
fix lane has nothing to `resume`. The mission prompt is the original spec
plus one paragraph naming the salvage commit; the review prompts point at
`git show <sha>` rather than carrying the diff, because a diff pasted into
a prompt is scanned as a template and a change to conductor's own prompt
text carries template syntax in its context lines. The diff is written
beside the mission file as `<name>-diff.patch` for the lead. `--emit` is refused when the gate is red,
and `emit()` itself is refused when the kept worktree is still dirty or its
HEAD still equals `base_sha` -- either way nothing has actually been
committed yet, so the follow-on would review the wrong thing.

