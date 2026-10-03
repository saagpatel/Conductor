# Inline agents: a persona per lane

A Claude lane can carry a persona in the dispatch instead of a file on disk.


A lane's persona has always been whatever its prompt says; `agent` gives it
a system prompt and a tool allow list of its own, defined at dispatch time
with nothing on the operator's disk:

```json
{"name": "reviewer", "fleet": "claude", "stage": "review",
 "agent": {"name": "reviewer", "description": "Cold reviewer",
           "prompt": "You are a skeptical reviewer...", "tools": ["Read", "Grep"]}}
```

`name` must match `[A-Za-z][A-Za-z0-9_-]{0,63}`; `description` and `prompt`
are required and non-empty; `tools`, when given, is a list of non-empty
strings -- an allow list, next to D2's `TAINT_DISALLOWED_TOOLS` deny list. An
unknown key, a missing or empty `name`/`description`/`prompt`, a malformed
`name`, or a non-list `tools` is refused before spawn, each message naming
what is wrong.

`agent` is enforceable on Claude Code only: `agent is enforceable on the
claude fleet only: <fleet> selects agents from disk and ignores an unknown
name`, live-probed
(`docs/research/2026-09-06-live-probe-inline-agents.md`): Antigravity's
`--agent <name>` selects from a locally defined list (empty on this machine)
and fails open on an unknown name (exit 0, `status: SUCCESS`, no persona,
nothing on stderr); Cursor has no persona flag headless at all.

`build_argv` turns it into `--agents '{"<name>": {"description", "prompt",
"tools"}}'` (one compact JSON object keyed by the name) plus `--agent
<name>`, in both read and write mode. `conductor dispatch` takes it as
`--agent-file PATH`, a JSON file holding the agent object; a mission lane
takes `agent` (cascades mission → lane → fallback like `schema`, cascade
attempts included) or `agent_file`, relative to the mission file like
`prompt_file`, exclusive with `agent`. A `stage: review` lane's agent may not
carry `Edit`, `Write`, `NotebookEdit`, or `Bash` in its `tools`: refused at
load, naming the lane -- a read lane's persona may not carry write tools.

A persona instruction can shape the model's first message and never appear
in its final answer at all -- the probe's own trap: on a write task, the
fingerprint it was told to print was in the first assistant message and
absent from the `result` envelope, which keeps only the last message. So
conductor never trusts the answer for this: after a Claude dispatch with an
agent, it reads the run's own stream, not the model's word. The first
`system`/`init` line of `stdout.log` carries the session's real `agents` and
`tools` lists; the dispatch fails closed unless the agent's name is in
`agents`, and, when `tools` was set, the init event's `tools` equal that list
as a set. A mismatch sinks the run with `error` `agent '<name>' not applied:
<what was missing>`, kind `agent` (see [Structured error kinds](structured-error-kinds.md)). A stream cut
short before any init event -- `agent '<name>' not applied: no init event`
-- records `applied: false` and that same error only when the run otherwise
looks complete (exit 0, no fleet-reported error); a run that already failed
for another reason keeps that reason and records `applied: null` instead.

`result.json` and `summary()` gain `agent`: `{"name", "tools": [...] |
null, "applied": true | false | null}`, or `null` when no agent was set; the
signed `attestation.json` statement carries the same dict. `report.md`'s
lane table gains an `agent` column (the name, or empty) and `conductor runs`
rows carry `"agent": <name or null>`.

Evidence (`docs/archive/roadmaps-closed.md` item D3): `claude --agents '<json>'` and
`agy --agent` let a lane define its reviewer or fixer persona at dispatch
time with nothing on the operator's disk.

