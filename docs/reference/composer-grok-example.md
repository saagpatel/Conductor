# Composer build and Grok review: opt-in example

[Copy the example directory](../../examples/composer-grok/) for a bounded
implementation with Composer 2.5, cold review with Grok 4.6 High, a lead checkpoint,
and a correction on Composer's original session. This packages the
[real-work trial](../research/2026-09-08-composer-grok-real-mission.md).
Shape A and standing model-routing policy are unchanged. Use this alternative
only when the task authorizes these routes.

The directory contains `mission.json` and a `spec.md` outline. The JSON uses
Conductor's existing mission format; there is no extra launcher or generator.

## Configure a task

1. Copy both files into a stable task directory. Keep that directory and the
   source repository available while the mission may need resuming.
2. Replace the task outline with the actual problem, numbered acceptance items,
   allowed files, compatibility requirements, and focused checks. Remove all
   `REPLACE_WITH_*` text from both files.
3. Set `cwd` to the chosen Git checkout. A relative `cwd` resolves against the
   directory containing `mission.json`, not your shell's working directory.
   Isolated lanes begin at committed state; inspect dirty work before choosing
   the base. Uncommitted source changes are not implicitly included.
4. Set `test` separately on build and fix to the repository's real gate, and edit
   their commit messages. The review lane has no test command. Use tools available
   from a fresh worktree; an untracked virtualenv is not copied automatically.
   Keep pytest's `--basetemp` outside the checkout and follow the target repo's
   required flags. In Conductor itself, unset `CONDUCTOR_LANE` for its gate and
   run the prescribed parallel suite with `--dist loadgroup`.
5. Review the illustrative caps: build $5, review $2 plus $0.25 read grace, fix $3,
   and mission $11. Cursor caps are post-hoc estimate verdicts, not hard provider
   billing limits. The example omits `ceiling`, retaining the ordinary rolling
   defaults of $10/hour and $25/day. An attended mission can explicitly choose
   different bounds under its task authority; null disables a bound. Do not
   silently alter a ceiling after a refusal.

Check for leftover placeholders, then validate the configured file:

```sh
rg 'REPLACE_WITH_' /path/to/task/mission.json /path/to/task/spec.md
conductor mission /path/to/task/mission.json --dry-run
```

The `rg` command should find nothing. A dry run validates the mission and records
argv without calling a model; it does not run the gate or prove that the spec is
adequate. Run the real gate on the selected base before dispatch. A placeholder
gate is not a pre-spend check: the gate normally runs after the model has worked.

## Build, review, and the lead checkpoint

```sh
conductor mission /path/to/task/mission.json
```

Composer works in an isolated worktree, runs the task's focused checks, and writes
an evidence map. Conductor runs the configured gate and commits the build; it
captures `evidence.json` without committing it. Grok then receives the spec,
exact patch, and captured map, and reads the relevant source without running the
suite. Build/fix vendor policy is `cursor`; review policy is `xai`.

The mission pauses before fix with exit **4**. Under
`$CONDUCTOR_HOME/missions/MISSION_ID/` (default `~/.conductor/missions/`), inspect:

- `answers/review.txt`: the complete review.
- `deliverables/build-deliverable`: the builder's evidence claims.
- `diffs/build.patch`: the captured change.
- `lanes/build.json`: the build tip and underlying run receipt reference.

Validate the candidate against independent acceptance checks and judge each
finding on the code. A successful review dispatch means the reviewer completed;
it does not mean the feature has no defects. Within the task's existing authority,
the lead can continue the checkpoint without asking the user again:

```sh
conductor mission --resume MISSION_ID --answer continue
```

Use `--answer stop` to stop instead. This lane checkpoint accepts only
`continue` or `stop`, not a findings file. Do not use `--unattended` as a substitute
for the lead's review and final acceptance.

Composer resumes its original session in a new worktree at the build tip. For
reproduced findings, Conductor requires new permanent tests to fail on the unfixed
base and the configured gate to pass on the correction. A clean no-op after
`NO_FINDINGS` is allowed. The fix has no deliverable file requirement.

## Final acceptance and additional lead findings

Inspect the final diff, reproduction result, gate, and session-reuse receipt.
Run independent acceptance checks on the final candidate, including compatibility
and boundary behavior. Integrate only the verified result under the task's scope;
this example does not automatically merge, publish, notify, or release anything.
Use the target repository's integration requirements rather than rerunning broad
tests without a reason.

If the lead discovers additional defects, retain the old receipts and issue a
bounded supplemental fix dispatch with the verified findings. Use a clean
checkout at the **latest candidate tip**, including any accepted fix, so the
correction cannot discard intervening work. Verify that checkout's HEAD first;
`--resume` restores model context, not the source commit:

```sh
conductor dispatch --fleet cursor --model composer-2.5 --effort standard \
  --mode write --stage fix --cwd /path/to/candidate-checkout --isolate \
  --resume BUILD_SESSION_ID --prompt-file /path/to/confirmed-findings.md \
  --test 'YOUR_REAL_GATE' --test-policy allow --cap-usd 3.00 \
  --commit 'fix: address verified lead findings'
```

Only dispatch this correction when findings warrant it. The prompt should require
reproduction and permanent tests, name allowed files and the current candidate,
and forbid helper commits. Conductor performs the commit after its checks; the
lead owns final integration. Account for supplemental dispatches alongside the
original mission budget.

For mechanics, see [pipelines](pipelines.md), [lane stages](lane-stages.md),
[pausing](pausing-for-the-operator.md), and [deliverables](deliverables.md).
