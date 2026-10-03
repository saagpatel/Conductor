# Deliverables: a file as the verdict

A lane can name a file as its product, and a validator can accept, reproduce, or
reject that file.


A lane's product is sometimes a file, not a reply: a report a research lane
writes, a document a generator produces, a JSON record a lane hands the next
stage. `deliverable` on an attempt (mission-level, per-lane, per-fallback,
cascading like `schema`) names it: `{"path": "report.md"}`, or
`{"path": "record.json", "schema": "record.schema.json"}` to also require it
to validate; `schema` resolves relative to the mission file, like
`Spec.schema` does. `conductor dispatch` takes `--deliverable PATH` and
`--deliverable-schema FILE`. `path` must be repo-relative, contain no `..`
component, and resolve inside `cwd`; a `schema` must be a readable JSON
file, parsed at load time like `Spec.schema` is.

Conductor checks the declared path on the filesystem of the lane's actual
working tree (its worktree when isolated) after the fleet exits and before
the gate -- never through `git status`, because an operator's own global
excludes can hide an untracked file from Git entirely, which is exactly how
one fixture's own deliverable went missing from its receipt (C7). The
verdict lands on `Result.deliverable`: `{"path", "exists", "bytes",
"parsed", "ok", "reason"}`, plus `sha256` once the bytes are captured. A missing file, an empty file, or (when
`schema` was set) a file that is not valid JSON or does not satisfy the
schema each sink `ok` and give `Result.failure()` one of `deliverable
missing: <path>`, `deliverable empty: <path>`, `deliverable does not parse:
<path>`, or `deliverable does not match schema: <detail>`; the check runs
after the exit-code and fleet-error checks and before the moved-bytes
checks. The schema check is deliberately narrow, the same shape as a
verdict checklist's own contract: every name in `required` is present, and
every present property whose schema declares a `type` (string, number,
integer, boolean, array, or object) has a value of that type -- not a
general JSON Schema validator. A schema whose top-level `type` is `array`
checks a list deliverable one element at a time against its `items` schema
under the same contract, and reports the first mismatch as `item <n>: ...`.
A deliverable that fails this check is judged like a failed gate: `ok`
sinks, and a commit conductor made for the dispatch is undone so the branch
never carries it, with the work left staged in the tree. On a dry run the deliverable is recorded as
declared (`ok: null`) and not checked. `errors.KINDS` gains `deliverable`
for the four failures above.

The check decides the verdict before the gate, but the copy under the run
directory is taken afterwards, once the lane's own gate, the clean gate, and
the final Git capture have judged the tree. A gate is an arbitrary shell
command the spec names, and so is a teardown, so either could rewrite the
product after it was checked; the bytes are hashed at the check and hashed
again at the capture, and if they differ the copy is refused, the receipt
carries no `deliverable_path`, and the run fails with `deliverable changed
after the gate ran: <path>`. What a downstream lane reads through
`{{lanes.<name>.deliverable}}` is therefore the artifact the run leaves
behind, not an earlier draft of it.

The path is validated at load, but the lane runs after that, so the check
is repeated on bytes before anything reads the file: no component from the
working tree down may be a symlink, and the file must still resolve inside
the working tree. A lane that plants `plan.json -> ../outside.txt` where its
product belongs fails with `deliverable is a symlink: <path>` (or
`deliverable resolves outside the worktree: <path>`) and nothing is
captured; without that, the link passed `is_file()` and was copied into the
run directory as the lane's own product, so bytes from outside the worktree
became the declared deliverable. The rule is the simplest one that closes
it -- no symlink at all, not even to a sibling in the same worktree -- and
the copy itself opens the file with `O_NOFOLLOW`. A human lane's
deliverable, and every artifact path a resumed mission trusts from a
receipt, is held to the same rule.

A write lane's deliverable is ordinary bytes, gated the same way any other
change is. A read lane is normally held to no bytes moved at all;
declaring a deliverable lifts that by exactly the deliverable's own path:
when the only difference between the before and after manifests is that
one file (added or modified), the "read dispatch moved bytes" check passes
and `Result.git_verdict` carries `deliverable_only: true`. Any other
change -- a second file, a commit, a branch move -- still fails as it
always has. A read lane that declares a deliverable and neither moves any
bytes nor writes the file fails with `deliverable missing`, not with the
generic "read dispatch returned no answer".

A downstream lane reads an upstream one's deliverable the way it reads its
answer: `{{lanes.<name>.deliverable}}` renders the file's text, fenced and
budgeted like `{{lanes.<name>.answer}}`, empty when the lane declared none
or left none behind.

`deliverable.commit` (optional boolean, default `true`) keeps a receipt out
of the harness's own commit: `false` means the declared path is staged like
everything else and then unstaged before conductor commits, so a fix lane's
`dispositions.json` -- a record of what it did, not source -- never lands
in the repository's history. The file is still checked on the filesystem
and still copied into the mission's `deliverables/` directory beforehand,
same as any other deliverable; only the commit is affected. When the
excluded path was the only change in the tree, nothing is committed and the
run's `ok` is unaffected by that, the same as any other no-op write lane
with `no_op_ok` set. In an isolated worktree the excluded file is also removed after
every capture and verdict, with a note on the receipt, so the worktree is released clean
and a later lane may build on the tip; the captured copy under the run directory is the
deliverable. In the operator's own checkout nothing is removed.

## Validators

A code change gets `runner._reproduce_receipt`'s reproduce gate: a `stage:
fix` lane's own check must fail on the base and pass after. A prose or data
deliverable has no test surface, so it had no equivalent -- a fix lane on a
document was refused with `fix without a reproducing check` and the lead
applied reviews by hand. `deliverable.validator` closes that gap without
pretending a document is a test: a non-empty shell command in which the
literal `{path}` is replaced by the deliverable's repo-relative path before
it runs (a command without `{path}` runs as written). `conductor dispatch`
takes `--deliverable-validator CMD` beside `--deliverable-schema`.

Once `_check_deliverable` has judged the file and before the gate runs, a
declared validator runs twice with the same command and the same
environment the gate gets: once against the deliverable as it stood at the
base commit (`git show <base>:<path>`, written to a temporary file under the
run directory, never into the worktree -- recorded as `null` when the file
did not exist at the base or the tree is not a repository), and once against
the file in the worktree. Each run is capped by the dispatch's gate timeout
and records `exit_code`, `timed_out`, and a 20-line tail. The result lands
on `Result.deliverable["validator"]`: `{"command", "before", "after",
"verdict"}`, where `verdict` is `accepted` (the after run passed, and the
before check did not prove a failure), `reproduced` (the after run passed,
the before run failed without timing out), `no-base` (the after run passed
and the file did not exist at the base commit), or `rejected` (the after run
failed or timed out). A
`rejected` verdict sinks `deliverable["ok"]` and gives `Result.failure()`
`deliverable rejected by validator: <path>: <first line of the after
tail>`, kind `deliverable`. On a dry run the block is recorded with
`before` and `after` `null` and `verdict` `null`, like the rest of the
deliverable record. A deliverable with no declared validator carries no
`validator` key at all, and the rest of the receipt is unaffected.

For a `stage: fix` lane, a `reproduced` verdict is itself the reproduction
when only its document deliverable changed and there is no test-surface
change to transplant: the reproduce receipt
reads `verdict: "validator"`, and the lane lands through its ordinary gate
exactly like a fix whose reproduce verdict is `reproduced`. An `accepted`
verdict on a fix that changed only its document deliverable is refused
with `fix without a reproducing check: validator passed on the base too`,
the same shape as the existing no-test-surface-change refusal. A fix that
also changed the test surface keeps the ordinary transplant path, with the
validator's own verdict recorded beside it on the deliverable block. A
validator checks what a machine can check; whether the document still means
what it meant is the reviewers' question, not the validator's.

