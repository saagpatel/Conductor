# Isolation: a branch is not a worktree

Every write lane runs in its own git worktree; a branch is not isolation.


HEAD and the index are shared mutable state, so two fleets editing one
checkout race each other whatever branches they think they are on. Every
write lane in a mission, and any dispatch given `--isolate`, runs in a fresh
worktree under `$CONDUCTOR_HOME/worktrees/` on branch `conductor/<run_id>`,
created from the target's HEAD. Verification and `--commit` happen there.
Afterwards the worktree is removed if it is clean (the branch keeps the
commits, ready to merge) and kept, with its path reported, if it holds
uncommitted work. Deleting an agent's uncommitted edits to tidy up is the
wrong trade. If a worktree cannot be created (not a repo, no commits yet, a
git error), a write dispatch is refused before anything spawns rather than
run in the shared checkout. Any write dispatch on a non-repository cwd is
likewise refused; a read dispatch may proceed in place and says so.

Ctrl-C or `kill` on `conductor dispatch` or `conductor mission` ends the run
the same way a timeout does. Every running dispatch kills its fleet's
process group at its next poll (within `POLL_S`, 2s), is priced from the
watcher's last reading and receipted with `interrupted: true`, and releases
its worktree; a mission skips the lanes that had not started, does not
spend the collate, and still writes its report, marked **Interrupted**. A
second signal synchronously kills every registered fleet and gate group, then
exits 130 for SIGINT or 143 for SIGTERM; `conductor verify --test` installs the
same handlers. Before this,
killing conductor left the fleet running in a worktree nothing would
release (found live 2026-09-03, on the first production pipeline). An
interrupted run is not over its cap and not "unpriced": the stop is the
only verdict it gets.

## Per-lane setup, teardown, and ports

A worktree isolates files, not ports, sockets, scratch databases, or
gitignored config, so two lanes each starting a dev server on the same port,
or each needing a gitignored `.env`, collide or fail. Claude Code answers
this with `.worktreeinclude`, Cursor with `.cursor/worktrees.json`, Emdash
with injected port variables (`docs/archive/roadmaps-closed.md` item C4); conductor's
own answer is `ports`, `setup`, `teardown`, and `include` on `Spec` and on
every lane.

`ports` claims that many free TCP ports on 127.0.0.1 before the fleet spawns:
each is drawn by binding to port 0 and reading back what the kernel assigned,
then locked by creating `$CONDUCTOR_HOME/ports/<port>` with
`O_CREAT | O_EXCL`, so two dispatches sharing a conductor home can never be
handed the same port even when the kernel hands it out twice. Allocation
gives up after 50 draws (`ports: could not allocate <n> free ports`) rather
than spinning forever. The claimed ports, the run id, and the worktree path
(or the plain `cwd` when the dispatch is not isolated) are exported to the
fleet, `setup`, `teardown`, and the gate as `CONDUCTOR_PORT_1` ..
`CONDUCTOR_PORT_<n>`, `CONDUCTOR_PORTS` (comma-separated), `CONDUCTOR_RUN_ID`,
and `CONDUCTOR_WORKTREE` -- the last two on every dispatch, ports or not.

`setup` is a shell command that runs in the fleet's working directory (the
worktree when isolated) after the worktree exists and before the fleet
spawns. A nonzero exit, a timeout (600s), or a stop request means the fleet
is never spawned and nothing is spent: the dispatch fails with `setup
failed: exit <code>` (or `setup timed out`, or the interrupted error).
`teardown` runs the same way, after the gate and the commit decision,
whether or not the run was ok; its outcome never changes `ok` -- the work is
already judged by then, so a failing teardown (`teardown failed: exit
<code>`, or `teardown timed out`) is only a note in the git verdict. What
teardown does to the tree is reported, though: conductor captures the tree
again after teardown and, when HEAD, the branch, or any dirty path moved,
notes `teardown changed the tree after it was judged: <paths>`, restates the
verdict's `files_changed`, `dirty_delta`, and branch fields from the tree the
run actually leaves behind, and sets `cleanup_required: true` on the receipt
so a caller knows the checkout needs cleaning before anything builds on it.
The verdict itself is not re-decided, for the same reason: the work was
already judged. A teardown that rewrote the lane's declared deliverable is
the one exception that does fail the run, under the deliverable rule above.
Both setup and teardown land in the receipt's `lane_env`: `{"ports": [...], "setup": <outcome or
null>, "teardown": <outcome or null>, "included": [...]}`, present only when
a dispatch actually used one of these four keys; `summary()` also carries
`ports`.

`include` copies repo-relative, untracked paths (files or directories) from
the original checkout into the isolated worktree at the same relative path,
right after the worktree is created and before `setup`. A tracked path is
refused before spawn (`include: <path> is tracked; the worktree already has
it`) because copying it would smuggle an uncommitted edit past the base
commit a reviewer diffs against; a path missing from the checkout is a note,
not a failure, and `include` without `--isolate` is a note (`include
ignored: dispatch is not isolated`). Conductor keeps a copied path untracked
by pointing a worktree-scoped `core.excludesFile` at it, never the shared
`info/exclude` that every worktree of one repo shares (writing there would
leak this lane's pattern into the next one): the diff and the commit never
carry an included path, while the test-surface pin, which deliberately
ignores exclude rules, still sees it like any other untracked file. That
file is seeded with the operator's own global excludes (`git config --get
core.excludesFile`, else `$XDG_CONFIG_HOME/git/ignore`, else
`~/.config/git/ignore`, whichever exists) before the include paths are
appended, so setting it for the run does not un-ignore whatever the
operator already globally ignores; `extensions.worktreeConfig`, once turned
on to make the per-worktree override possible, is left on rather than
unset at release, the same way `.git/worktrees` itself outlives any one
worktree -- a concurrent lane's own worktree-scoped config depends on it
staying enabled. Included paths are recorded as `lane_env.included`.

`ports`, `setup`, `teardown`, and `include` are attempt keys, cascading
mission to lane to fallback like `test`. `conductor dispatch` gains `--ports
N`, `--setup CMD`, `--teardown CMD`, and repeatable `--include PATH`.

## Garbage collection

`conductor gc` reconstructs a cleanup plan from current Git state and the
isolation records under `$CONDUCTOR_HOME/runs`. It reports one JSON object per
worktree, `conductor/*` branch, port claim, or incomplete audit directory and
changes nothing by default; pass `--apply` to prune vanished registrations,
remove clean conductor-home worktrees, delete conductor branches whose
commits are already reachable from `HEAD` or another non-conductor branch,
and remove port claim files whose run has ended. `--repo` adds repositories
to those discovered from receipts, and `--older-than` limits actions to
older run ids. A receipt whose `isolation.repo` is a lane worktree names the
repository that worktree belongs to: repositories are identified by their
git common dir, so one repository is planned once however many of its
worktrees the receipts name.

GC never removes a dirty worktree, a worktree outside
`$CONDUCTOR_HOME/worktrees`, a branch outside `conductor/`, an unmerged
conductor branch, a port claim file whose run has no `result.json` yet, or
any run or mission directory. Directories without a `result.json` are
reported as possible in-progress or crashed work and kept; their worktrees,
branches, and port claims are also kept, with liveness checked again
immediately before every applied remove, branch delete, or claim removal.

