# Live probe: per-lane tool deny lists on Antigravity and Cursor (E21)

Date: 2026-09-06. Binaries: `agy` 1.1.27, `cursor-agent` 2026.09.02-c22c1a3. Scratch git
repository under the session scratchpad (one seed commit, two files); `<scratch>` below.
Spend: under $0.10 across six runs (Grok 4.6 and Gemini 3.7 Flash at low effort).

Question, from `docs/ROADMAP-2026-10.md` E21: D2 recorded that "no other fleet exposes a
tool deny list headless" and D3 recorded that `agy --agent` fails open and Cursor has no
persona flag. Is there any headless allow or deny mechanism under a different name?

Answer: yes, on both, and both are files inside the working directory, so a conductor-owned
worktree can carry them. Both fail open on malformed input in a way conductor can detect.

## Cursor: `<cwd>/.cursor/cli.json`

Found by reading the bundle: `cursor-agent` walks from the workspace root to `process.cwd()`
and loads every `.cursor/cli.json` it finds, merges them with `~/.cursor/cli-config.json`,
and has a hidden `--disable-project-configs` flag that ignores them. The permission rule
kinds that appear in the bundle are `Shell(<command>)`, `Write(<path glob>)`, and
`Mcp(<server>)`; the shipped default config is `{"permissions": {"allow": ["Shell(ls)"],
"deny": []}}`.

Run 1, deny write and shell, `--force` on (the flag that "force allows commands unless
explicitly denied"):

```
<scratch>/.cursor/cli.json = {"permissions":{"allow":[],"deny":["Write(**)","Shell(*)"]}}
cursor-agent -p --trust --force --model grok-4.6 --output-format stream-json \
  "Create probe.txt containing hi with the write tool, else with echo, then say which worked"
```

Result: exit 0. Stream shows `editToolCall` completed with `writePermissionDenied`
("Blocked by permission rule"), then `shellToolCall` with `permissionDenied` on
`echo hi > probe.txt`. Final message: "Both were refused." `git status`: nothing but the
config file. **The deny held on bytes, under `--force`.**

Run 2, config without the required `allow` key: exit 1 before any run, a validation
error on stderr naming `allow` as required. **Malformed shape fails closed.**

Run 3, valid shape with one unknown rule kind, `{"allow":[],"deny":["Bogus(x)"]}`:
exit 0, the write tool ran, `probe2.txt` created, "It worked". **An unknown rule kind is
silently ignored: fails open.** Conductor must validate every rule it writes against the
three known kinds and never rely on the vendor rejecting a typo.

Tool call kinds Cursor's stream can carry (from the bundle, 45): among them `editToolCall`,
`shellToolCall`, `deleteToolCall`, `webFetchToolCall`, `webSearchToolCall`, `mcpToolCall`,
`taskToolCall`, `fetchToolCall`, `computerUseToolCall`. Whether `Write(**)` also covers
delete, and whether a `Read(...)` kind exists, was not probed.

## Antigravity: `<cwd>/.agents/hooks.json`

Found by reading the binary: hooks load from `<workspace>/.agents/hooks.json`,
`~/.gemini/antigravity-cli/hooks.json`, and the shared `~/.gemini/config/hooks.json`. The
hook contract (documented inside the binary) is a `PreToolUse` command hook receiving the
tool call on stdin and answering `{"decision": "allow" | "deny" | "ask" | "force_ask"}`;
`deny` is a hard block. The settings schema also carries `permissions.allow` and
`permissions.deny` with `command(<cmd>)`, `mcp(<server>/<tool>)`, and `read(...)` grants,
but settings are global (`~/.gemini/antigravity-cli/settings.json`, and per-project files
under `~/.gemini/config/projects/`), which conductor does not reconfigure; the workspace
hooks file is the per-lane lever.

Run 4, a hooks file at the wrong path (`.gemini/antigravity-cli/hooks.json` inside the
workspace): log says "loaded 0 named hooks from 0 hooks.json file(s)", the shell wrote
the file. Not a search path.

Run 5, `.agents/hooks.json` with two `PreToolUse` entries, matchers `run_command` and
`write_to_file`, each a command hook printing `{"decision":"deny","reason":"..."}`:

```
agy --print "<same prompt>" --output-format stream-json --print-timeout 240s \
  --mode accept-edits --dangerously-skip-permissions --disable-slash-commands \
  --add-dir <scratch> --model gemini-3.7-flash --effort low --log-file <run>/agy.log
```

Result: exit 0, `status: SUCCESS`. Stream shows `write_to_file` state `ERROR` with
"tool call denied by pre-tool hook: conductor: write denied", then `run_command` state
`ERROR` with "conductor: shell denied". Final message: "Both methods were refused."
`git status`: nothing but the hooks file. Log: "loaded 1 named hooks from 1 hooks.json
file(s)". **The deny held on bytes.**

Run 6, an unsupported hook `type`: log says "failed to parse hooks.json ... unsupported
hook type" and "loaded 0 named hooks from 1 hooks.json file(s)"; the run continued, the
shell wrote the file, exit 0, `status: SUCCESS`. **A malformed hooks file fails open**, but
the failure is on the log line, which conductor can require: `--log-file` into the run
directory and assert "loaded N named hooks" with N equal to what it wrote.

Run 7, matcher `"*"`: "loaded 0 named hooks from 1 hooks.json file(s)", `list_dir` and
`run_command` ran, file written. **No wildcard matcher.** A deny list must name each tool;
the init event lists every tool the session has (55 on this run, including
`browser_subagent`, `invoke_subagent`, `call_mcp_tool`, `read_url_content`, `search_web`,
`open_browser_url`, `send_message`, `schedule`), so conductor can generate the matchers
from that list and refuse the run if the log count disagrees.

Side observation, unrelated to hooks: under this argv `write_to_file` failed twice on its
own with "not a valid artifact path; artifacts must be in <home>/.gemini/antigravity-cli/
brain/<conversation>/", and the model fell back to the shell. Gemini write lanes in the
September missions edited through `replace_file_content` and the shell, so this did not
surface there; worth knowing when a Gemini write lane reports a refused write.

## What this changes

- D2's refusal "taint is Claude-only, no other fleet exposes a tool deny list headless"
  can be lifted for both fleets by writing a config file into the worktree before the
  baseline capture: `.cursor/cli.json` with `deny` rules for Cursor, `.agents/hooks.json`
  with a named `PreToolUse` deny hook per tool for Antigravity.
- Both need conductor-side validation because both fail open: Cursor on an unknown rule
  kind (silent), agy on a malformed file or a wildcard (log line). Cursor has no equivalent
  of agy's log count; the check there is a self-test dispatch or the rule kinds
  whitelisted in `fleets.py`.
- The file lives in the worktree, so it must be written before the bytes baseline or
  excluded through the worktree's `.git/info/exclude`, or every read lane will look like
  it moved bytes.
- Neither mechanism is an inline persona (D3 stays Claude-only). Both are deny lists, not
  allow lists: Cursor's `allow` only pre-approves, and agy's hook decides per call.
