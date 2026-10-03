# Restricted read lanes: `--restricted` and `--permission-prompts none` (F12)

A Claude read lane can be confined to the worktree by denying every write tool.


`--permission-prompts none` goes on every Claude Code dispatch, read and
write: anything that would need a permission prompt is denied automatically
instead of stalling forever with nobody there to answer it (the September
fleet comparison: eight refused pytest calls, then a question to nobody).
The run still exits 0 with `subtype: success`; the only signal is
`result.permission_denials`, a list of `{tool_name, tool_use_id,
tool_input}` conductor parses into `FleetOutput.permission_denials` and
carries onto the receipt unchanged. A write lane with a non-empty list
fails outright, kind `denied`, naming the tools: the fleet's own summary
will say it succeeded, and it did not. A read lane's list is not a
failure -- a read lane's tool set is meant to be thin -- and lands instead
as a note on `Result.git_verdict`.

**A write lane may not rewrite its own settings files (W8).** A project-scope
`permissions.deny` rule is not a boundary for a write lane: the lane can edit
the file the rule lives in, and Claude Code applies the edit to the next
subagent it spawns. Before spawning any `claude` dispatch in `mode: write`,
`runner.dispatch` records the sha256 (or the absence) of
`.claude/settings.json` and `.claude/settings.local.json` at the repository root
(or the dispatch cwd outside a repository; the isolated worktree root under
`--isolate`), and re-hashes both
after the run. A file the run created, changed, or deleted
fails the lane as `settings modified: <comma-separated relative paths>`, kind
`settings`, and the lane is not committed. The check runs before the
deliverable check, the commit, and either gate, so a green gate does not
rescue it. Every receipt carries `settings`: `{"checked": true, "modified":
[...]}` on a claude write lane, `{"checked": false, "modified": []}` on every
other fleet and on read mode (a read lane runs in plan mode, which cannot edit
these files, and a denial there is already a note). A lane whose own `commit`
was meant to include a settings file is not a case this supports: the operator
edits settings by hand, never through a lane. Evidence: the third pass of
`docs/research/2026-09-07-live-drills-fail-closed-checks.md`, where a write
lane's subagent edited `permissions.deny` out of `.claude/settings.json`, the
next subagent ran Bash, and the receipt read `ok: true` with `dirty_delta: 1`
as the only trace.

`--restricted` removes the tools that run commands or fetch a URL (Bash,
WebFetch) from the model's tool list and confines the file tools to the
process working directory, on bytes; `WebSearch` survives it, so it is a
code-execution and fetch block, not an egress block, and taint's own deny
list still matters. It refuses `--permission-mode bypassPermissions`
outright (exit 1, nothing spent), so it can only ever run a read lane,
under `acceptEdits` -- a restricted lane can edit files but never run a
gate. Two things turn it on: a claude read lane that declares a
`deliverable` gets it automatically (below), and `restricted: true` turns
it on explicitly for a lane with nothing to write but no need for Bash or
WebFetch either -- a cold reviewer, Gemini's role in Shape A translated to
the Claude side. `restricted: true` is refused at load on a write lane and
on every fleet but claude, naming the fleet; the launcher never sets it.
Conductor does not take the flag's word for it: the same way D3 checks an
inline agent against the stream's own `system`/`init` event rather than the
model's answer, a restricted lane's init event must list neither `Bash` nor
`WebFetch` in its tools, or the run fails closed as `restricted mode not
enforced: <tool>`, kind `taint` (no init event at all -- a stream cut short
for an unrelated reason -- is not evidence the flag failed, so this only
ever fires on positive evidence). `Result` carries `permission_mode` (the
actual `--permission-mode` value: `plan`, `acceptEdits`, or
`bypassPermissions`) and `restricted` (whether `--restricted` was passed)
on every claude dispatch. A tainted claude read lane that is also
restricted records both mechanisms on `taint_enforcement`:
`{"disallowed_tools": [...], "restricted": true}`.

**The plan-mode deliverable gap this closes:** a read lane runs under
`--permission-mode plan` by default, and plan mode allows no write except
its own plan file -- so a read lane with an E1 `deliverable` (every `plan:
true` lane included, see [Planner lanes](planner-lanes.md)) could write only a plan of what
it would do, never the file itself. The first live planner lane
(2026-09-07, $2.18) hit exactly this: Opus wrote the whole child mission
into its plan file and answered "say the word and I'll write it". A claude
read lane that declares `deliverable` now dispatches under `--restricted
--permission-mode acceptEdits` instead of plain `plan`; the E1 bytes check
still refuses anything beyond the declared path, so the stronger
confinement does not loosen what the lane may actually leave behind. A
claude read lane without a deliverable, and every non-claude fleet, is
unaffected -- plain `plan` mode exactly as before.

Live-probed in `docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md`
(F12): the denial record's exact shape, `--restricted`'s mutual exclusion
with `bypassPermissions`, and the file-tool confinement, all captured from
the real binary (Claude Code 2.1.263).

