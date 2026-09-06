# Live probe: inline agent definitions (roadmap D3), 2026-09-06

Question: can a lane define its reviewer or fixer persona at dispatch time, with nothing on the
operator's disk, on each fleet? Scratch repos under the session scratchpad (one seed file, one
commit); Sonnet 5 for Claude, Gemini 3.7 Flash for agy. Total spend about $0.45.

## Claude Code (`claude -p`): yes, and the `tools` list is a real restriction

`--agents '<json>'` defines agents for the session; `--agent <name>` selects one for the main
session. The persona was given a fingerprint it had to print (`PERSONA=REVIEWER-7` on its own line).

| probe | argv shape | result |
|---|---|---|
| A: `--agents` + `--agent reviewer`, plan mode, `--output-format json` | conductor's read shape | fingerprint on the first line of `result`; exit 0; $0.17 |
| B: `--agents` without `--agent` | same | no fingerprint: the definition alone only registers a subagent; $0.16 |
| C: `--agent nosuchagent` | same | **exit 1**, stderr `--agent 'nosuchagent' not found. Available agents: ...`; nothing spent |
| D: `--agents` + `--agent fixer`, `bypassPermissions`, `stream-json`, a write task | conductor's write shape | file written; fingerprint present in the **first assistant message** and absent from the `result` envelope, which keeps only the last message; $0.05 and $0.02 on two samples |
| E: agent with `"tools": ["Read"]`, asked to run `ls` through Bash | write shape | init event `tools: ["Read"]`; zero tool calls; answer says Bash was unavailable; $0.015 |
| F: `--agent fixer`, `bypassPermissions`, question only | write shape | fingerprint on the first line of `result`; $0.04 |

What the stream carries: the `system`/`init` event's `agents` list includes the inline name
(`[..., "reviewer", ...]`) and its `tools` list is the restricted set, so conductor can assert both
on bytes before trusting a run. A bogus name fails closed before the model starts.

Trap: a persona instruction that shapes the first message does not shape the `result` field. Any
fingerprint or format check must read the stream's assistant messages (`outputs.claude_said`), not
the envelope. The env-independent way to check that a persona was applied is the init event's
`agents` entry, not the text.

## Antigravity (`agy`): selection only, and it fails open

`agy --agent <name>` exists ("Agent for the current CLI session") and `agy agents` lists what is
defined; on this machine that list is empty and there is no flag to define one inline. `agy -p ...
--agent reviewer` with no such agent: **exit 0, `status: SUCCESS`, no persona, nothing on stderr**.
Consistent with agy's other soft failures (`--conversation <missing id>` starts a fresh one).
Conductor must refuse `agent` on this fleet rather than pass it through.

## Cursor (`cursor-agent`): nothing

No agent, persona, or system-prompt flag in `--help`. Its persona levers are `AGENTS.md` and
`.cursor/rules` on disk, which a worktree would have to carry as files. Not inline; refuse.

## Verdict

Build D3 for the `claude` fleet: `Spec.agent` carried as `--agents` plus `--agent`, the init event's
`agents` and `tools` asserted on the stream, refused on `antigravity` and `cursor`. The `tools`
restriction is a second lever next to D2's deny list (an allow list rather than a deny list), worth
exposing on the same field.
