# Live probe: background lanes (roadmap C6), 2026-09-05

Question: can conductor hand a lane to a fleet's own background mode, exit, and collect the result
later (`claude --bg`, `cursor-agent persist`, roadmap item C6)? Probed on a scratch git repository
with a one-line mechanical task ("append the word probe to notes.txt, reply DONE").

## Claude Code 2.1.261, `claude --bg`

- `--bg` **refuses `--print`**: "`--print` never starts the interactive session that `claude agents`
  attaches to, so the job would be unattachable." A background session is an interactive TUI session
  detached from the terminal, with the prompt as the positional argument. There is no `stream-json`,
  no `--json-schema`, no final `result` envelope: everything conductor's runner reads today.
- `claude --bg '<task>' --model claude-sonnet-5 --effort low --max-budget-usd 0.50
  --permission-mode bypassPermissions --strict-mcp-config --setting-sources project` exits 0 at once
  and prints a short id (`backgrounded · b97c2a7a`) plus the `attach`, `logs`, `stop` lines.
- `claude agents --json` (add `--all` for exited ones) lists every session, interactive and
  background: `pid`, `cwd`, `kind: "background"`, `status: "busy" | "idle"`, `name` (an
  auto-generated title), `sessionId` (the full UUID), `exitCode`, `id`. The task finished inside
  15 seconds and the session then sat `idle` **indefinitely**: an interactive session waits for the
  next prompt and never exits on its own. Turn completion is the `busy` to `idle` transition, polled.
- `claude logs <id>` is a raw terminal dump with ANSI escapes and spinner frames. Not a receipt.
- The transcript at `~/.claude/projects/<cwd slug>/<sessionId>.jsonl` carries per-message `usage`
  (input, output, cache read, cache creation tokens) and the assistant text, no dollar figure.
  Conductor would price from tokens (`prices.py`) and read the final text from the last assistant
  message.
- `claude stop <id>` stops it (status and pid become null); `claude rm <id>` removes it from the
  list. Both worked first time. The edit landed in the working tree (`notes.txt` modified).
- `--max-budget-usd` was accepted on the command line; whether it is enforced in an interactive
  session was not tested (the task cost well under it).

## Cursor `cursor-agent persist`

- `persist` **requires tmux** ("Persistence requires tmux. Install tmux with: brew install tmux.
  Cannot continue without persistence from a non-interactive terminal"). tmux is not installed on
  this machine and installing a system dependency for one fleet's mode is an operator decision.
- Subcommands: `persist [prompt]`, `persist list`, `persist attach`, `persist stop`.

## Antigravity `agy`

- No background or detached mode. `--conversation <id>` resumes a conversation in a new process;
  that is the existing B1 thread reuse, not a background lane.

## What this means for C6

Only one fleet has a background mode, and it is an interactive session without the stream conductor
judges on: no breakers, no watcher pricing, no schema, no verdict lanes, and a session that must be
polled to idle and then stopped and removed by hand. Supporting it means a second runner path that
re-implements caps and liveness against `claude agents --json` and the transcript file, for one
fleet, to remove a wall-clock ceiling that the C1 resume plus a background shell already removes in
practice (every mission today ran that way; none was limited by conductor's own process lifetime).
Recommendation: shelve C6 as measured; revisit when a fleet ships a headless background mode with
a machine-readable stream and a terminal event.
