"""Fleet registry: which CLI, which models are permitted, how one abstract
effort level and one abstract mode translate into each CLI's own dialect.

The routing policy this file encodes is deliberate and not a matter of taste:

  * Anthropic models come from Claude Code, OpenAI models from Codex.
    Both CLIs also appear inside other vendors' products as resold endpoints;
    calling them there costs more for the identical model.
  * Cursor is restricted to its first-party pool (Grok 4.6, Composer 2.5).
    Every other model Cursor exposes is a resale of something already
    reachable first-party on this machine.
  * Antigravity is the Gemini route. It also exposes Claude models; those are
    refused here for the same reason Cursor's are.

The allowlist is enforced at dispatch time rather than left to the caller's
discipline, because the callers are unattended agent runs at 3am.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import prices
from .verdicts import Criterion

EFFORTS = ("cheap", "standard", "hard", "max")
# E24: the hard ceiling on Spec.cap_grace_usd -- an operator decision, not a
# fleet limit; a terminal-message grace band bigger than this is not a grace
# band, it is a second cap.
CAP_GRACE_CEILING_USD = 0.50
MODES = ("read", "write")
TEST_POLICIES = ("clean", "allow", "forbid")
# The company behind a model, independent of which fleet dispatches it. A
# fleet can carry more than one vendor (cursor resells Grok and runs its own
# Composer); a judge scoring a lane on its own vendor is what A3 refuses.
VENDORS = ("anthropic", "openai", "google", "xai", "cursor", "script")

# Per-mode default wall-clock caps. No fleet has a native cap; an agent that
# loses its way will happily spin until something outside it says stop.
DEFAULT_TIMEOUT = {"read": 600, "write": 1200}

# D5: how a tainted dispatch treats shell execution. "deny" is the default and
# the only setting that is a boundary: a tainted lane runs no shell at all.
# "allow" is an opt-in per-lane discouragement -- the pre-2026-09-07 prefix
# list -- kept for a lane whose work genuinely needs a shell and whose operator
# has read what it does not stop (README, "Taint").
TAINT_SHELL_MODES = ("deny", "allow")

# D2/D5: what a tainted Claude dispatch may not use, headless. Every entry
# closes one path an instruction hiding in quoted outside text (an issue, a PR,
# a web page) could otherwise use to do damage beyond misleading this one
# dispatch's own answer or diff. `Bash` is denied whole: a prefix list is not a
# boundary (see TAINT_SHELL_PREFIX_DISALLOWED_TOOLS below).
TAINT_DISALLOWED_TOOLS: tuple[str, ...] = (
    "WebFetch",  # network egress: exfiltrate repo contents, fetch a second-stage payload
    "WebSearch",  # network egress, same risk as WebFetch
    "Task",  # a subagent inherits the tainted context without inheriting this deny list
    "Agent",  # Claude Code's other subagent-spawning name; same risk as Task
    "Bash",  # D5: the shell reaches every one of the above, by a hundred spellings
)

# D5: the opt-in `taint_shell: "allow"` list -- what a tainted Claude dispatch
# ran under until 2026-09-07. It denies a handful of command *prefixes* and
# leaves the shell itself, so `command curl x`, `/usr/bin/curl x`, `env curl
# x`, `\curl x`, `(curl x)`, `bash -c 'curl x'`, `nc host 80`, and
# `python3 -c "import urllib.request"` all still run. It is a discouragement,
# never a boundary; the Astra review (2026-09-07, D5) probed exactly these.
TAINT_SHELL_PREFIX_DISALLOWED_TOOLS: tuple[str, ...] = (
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    "Bash(curl *)",  # network egress via the shell
    "Bash(wget *)",  # network egress via the shell
    "Bash(git push *)",  # push rights: an injected instruction must not publish anything
    "Bash(gh *)",  # push rights and network egress via the GitHub CLI
    "Bash(ssh *)",  # remote command execution and network egress
    "Bash(scp *)",  # file exfiltration over the network
)

# E21: the shell prefixes an opt-in `taint_shell: "allow"` dispatch may not
# run, derived from TAINT_SHELL_PREFIX_DISALLOWED_TOOLS's own `Bash(<prefix>
# *)` entries so the Claude list and the Antigravity hook script can never
# drift apart. Empty under the default, where there is no shell to prefix.
TAINT_SHELL_DENIED_PREFIXES: tuple[str, ...] = tuple(
    pattern[len("Bash(") : -len(" *)")]
    for pattern in TAINT_SHELL_PREFIX_DISALLOWED_TOOLS
    if pattern.startswith("Bash(") and pattern.endswith(" *)")
)


def taint_disallowed_tools(taint_shell: str = "deny") -> tuple[str, ...]:
    """D5: the `--disallowedTools` names for a tainted Claude dispatch. The
    default denies `Bash` outright; `taint_shell="allow"` returns the older
    prefix list instead (opt-in, per lane, and not a boundary)."""
    return (
        TAINT_SHELL_PREFIX_DISALLOWED_TOOLS if taint_shell == "allow" else TAINT_DISALLOWED_TOOLS
    )


# E21: the Antigravity tool names that reach outside the worktree, named
# individually because agy's hooks.json has no wildcard matcher (a matcher
# of "*" loads zero hooks; verified live,
# docs/research/2026-09-06-live-probe-tool-deny-non-claude.md). The probe's
# init event listed 57 tools and named these as reaching outside the
# worktree; it did not enumerate every `browser_*` tool by name, so a
# `browser_*` (or subagent/mcp/web/url/message/schedule/inbox) tool this
# tuple does not name is instead caught at runtime by runner.dispatch's
# `uncovered` check, which fails the run rather than silently missing it.
TAINT_AGY_DENIED_TOOLS: tuple[str, ...] = (
    "read_url_content",  # network egress: fetch a page or a second-stage payload
    "search_web",  # network egress, same risk as read_url_content
    "open_browser_url",  # drives the browser to a network destination
    "read_browser_page",  # reads whatever the browser last loaded from the network
    "execute_browser_javascript",  # arbitrary code in the browser's network-connected context
    # Named individually from a recorded transcript's own init event (57
    # tools; tests/golden/c5-review-fix/runs/20260905T182328Z-antigravity-
    # .../stdout.jsonl line 1) rather than the live probe's writeup, which
    # said "every browser_* tool" without listing them all -- there is no
    # wildcard matcher, so an unnamed one is invisible to the hook.
    "browser_click_element",  # drives the browser, network-connected context
    "browser_drag_pixel_to_pixel",  # drives the browser, network-connected context
    "browser_get_dom",  # reads whatever the browser last loaded from the network
    "browser_get_network_request",  # reads network traffic the browser made
    "browser_input",  # drives the browser, network-connected context
    "browser_list_network_requests",  # reads network traffic the browser made
    "browser_mouse_down",  # drives the browser, network-connected context
    "browser_mouse_up",  # drives the browser, network-connected context
    "browser_move_mouse",  # drives the browser, network-connected context
    "browser_press_key",  # drives the browser, network-connected context
    "browser_refresh_page",  # network egress: reloads whatever URL is open
    "browser_resize_window",  # drives the browser, network-connected context
    "browser_scroll",  # drives the browser, network-connected context
    "browser_scroll_dom",  # drives the browser, network-connected context
    "browser_select_option",  # drives the browser, network-connected context
    # The same init event also names four browser tools that do not carry the
    # `browser_` prefix, so both the enumeration above and the
    # `startswith("browser_")` net in `runner._uncovered_agy_tools` walked
    # straight past them, and the coverage test filtered on the same prefix
    # and could not fail (2026-09-08 review). Matched by substring now.
    "click_browser_pixel",  # drives the browser, network-connected context
    "list_browser_pages",  # enumerates whatever the browser has open
    "capture_browser_screenshot",  # reads whatever the browser last loaded
    "capture_browser_console_logs",  # reads the network-connected page's console
    # A notebook kernel is a second code-execution surface next to the shell:
    # a cell may import urllib and reach the network whether or not
    # `run_command` is denied. Denied for the same reason `run_command` is.
    "notebook_execution",
    # Feeds stdin to a process `run_command` started. Under the opt-in
    # `taint_shell: allow` the prefix list only inspects the command line, so
    # an allowed interactive process could be driven past it through here.
    "send_command_input",
    "browser_subagent",  # a subagent inherits the tainted context without inheriting this deny list
    "invoke_subagent",  # same risk as browser_subagent, for a non-browser subagent
    "define_subagent",  # defines a subagent that would not carry this deny list
    "manage_subagents",  # controls subagents that would not carry this deny list
    "call_mcp_tool",  # an MCP server is an arbitrary external integration
    "send_message",  # an exfiltration channel to somewhere outside the worktree
    "manage_inbox",  # reads and writes messages outside the worktree
    "schedule",  # schedules work that runs after this dispatch's oversight ends
    "generate_image",  # network egress to an image-generation backend
    # D5: the shell, denied outright by default -- the same decision as
    # Claude's bare `Bash` above. Under the opt-in `taint_shell: "allow"` it
    # is still matched (the hook must see the call) but decided by
    # TAINT_SHELL_DENIED_PREFIXES instead; see taint_hook_files.
    "run_command",
)

# W1: the tool names that edit files in the worktree. They are not denied --
# a tainted write lane's job is to edit -- but they are matched, so the hook
# can refuse a write to its own policy directory. The hook payload's argument
# shape for these tools is not on record from any probe (only `run_command`'s
# `CommandLine` is), so the check is a conservative scan of the call's string
# arguments for a `.agents` path component; the digest check in
# `runner._taint_agy_enforcement` is the evidence that does not depend on it.
TAINT_AGY_EDIT_TOOLS: tuple[str, ...] = (
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "sed_file",
    "notebook_edit",
)

# The directory the hook files live in, relative to the lane's cwd. A tainted
# lane may not write into it (W1: the hook script is re-read from a writable
# worktree on every tool call).
TAINT_AGY_RESERVED_DIR = ".agents"

# E21: the PreToolUse deny hook, written by taint_hook_files() into a tainted
# Antigravity dispatch's worktree. Placeholders are substituted with
# `repr()`, not `.format()`, so the JSON literals in the script body (and its
# own docstring) never need brace-escaping.
_TAINT_AGY_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""conductor E21: PreToolUse deny hook for a tainted Antigravity dispatch.

Written by taint_hook_files(); never edited by hand. Reads one JSON object
from stdin and denies a tool call that reaches outside the worktree by name
-- which by default includes run_command, the shell, outright. Under the
opt-in taint_shell "allow" mode run_command is decided instead by a denied
shell prefix found after leading whitespace, environment assignments, sudo,
or a chain operator (; && || |), which is a discouragement and not a
boundary. An edit tool naming a path inside the reserved policy directory is
denied too, so the hook cannot be rewritten under its own feet.
Fails closed: anything this script cannot parse is denied, not allowed.
"""
import json
import re
import sys

DENIED_TOOLS = __DENIED_TOOLS__
DENIED_PREFIXES = __DENIED_PREFIXES__
EDIT_TOOLS = __EDIT_TOOLS__
RESERVED_DIR = __RESERVED_DIR__
_CHAIN_RE = re.compile(r";|&&|\\|\\||\\|")
_ENV_RE = re.compile(r"^\\s*[A-Za-z_][A-Za-z0-9_]*=\\S*\\s+")


def _stripped(segment):
    s = segment.lstrip()
    while True:
        m = _ENV_RE.match(s)
        if m:
            s = s[m.end():]
            continue
        if s == "sudo" or s.startswith("sudo "):
            s = s[len("sudo"):].lstrip()
            continue
        break
    return s


def _shell_denied(command_line):
    for segment in _CHAIN_RE.split(command_line):
        cleaned = _stripped(segment)
        for prefix in DENIED_PREFIXES:
            if cleaned == prefix or cleaned.startswith(prefix + " "):
                return True
    return False


def _reserved_strings(value, found):
    if isinstance(value, str):
        parts = value.replace("\\\\", "/").split("/")
        # Case-folded: this machine's APFS volume is case-insensitive by
        # default, so ".AGENTS/conductor-taint.py" opens the very file this
        # check exists to protect while a case-sensitive comparison waves it
        # through. The post-run digest check would still catch bytes left
        # changed, but not a lane that edits the deny script, uses what it
        # unlocked, and writes the original bytes back (2026-09-08 review).
        if any(part.lower() == RESERVED_DIR.lower() for part in parts):
            found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _reserved_strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _reserved_strings(item, found)
    return found


def _decide(payload):
    call = payload.get("toolCall")
    if not isinstance(call, dict):
        return "deny", "conductor: taint: malformed tool call"
    name = call.get("name")
    if not isinstance(name, str) or not name:
        return "deny", "conductor: taint: malformed tool call"
    if name in DENIED_TOOLS:
        return "deny", "conductor: taint: %s reaches outside the worktree" % name
    if name in EDIT_TOOLS and _reserved_strings(call.get("args"), []):
        return "deny", "conductor: taint: %s may not write under %s/" % (name, RESERVED_DIR)
    if name == "run_command":
        args = call.get("args")
        command = args.get("CommandLine") if isinstance(args, dict) else None
        if not isinstance(command, str):
            return "deny", "conductor: taint: malformed run_command call"
        if _shell_denied(command):
            return "deny", "conductor: taint: shell command reaches outside the worktree"
    return "allow", None


def main():
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError):
        print(json.dumps({"decision": "deny", "reason": "conductor: taint: unparseable input"}))
        return
    decision, reason = _decide(payload)
    out = {"decision": decision}
    if reason:
        out["reason"] = reason
    print(json.dumps(out))


if __name__ == "__main__":
    main()
'''


# F13: the relative paths taint_hook_files() writes, named here so
# runner.py's free `/hooks` preflight query can look for the same file
# without re-deriving or re-writing it.
TAINT_AGY_HOOKS_REL = ".agents/hooks.json"
TAINT_AGY_SCRIPT_REL = ".agents/conductor-taint.py"


def taint_agy_matchers(taint_shell: str = "deny") -> tuple[str, ...]:
    """Every `PreToolUse` matcher `taint_hook_files` writes, in the order it
    writes them. The same names in both shell modes: under `taint_shell:
    "allow"` `run_command` is still matched, only decided by prefix rather
    than denied outright."""
    return (*TAINT_AGY_DENIED_TOOLS, *TAINT_AGY_EDIT_TOOLS)


def taint_agy_denied_tools(taint_shell: str = "deny") -> tuple[str, ...]:
    """The names the hook denies outright. `run_command` leaves this set --
    and only this set -- under the opt-in `taint_shell: "allow"`."""
    if taint_shell == "allow":
        return tuple(name for name in TAINT_AGY_DENIED_TOOLS if name != "run_command")
    return TAINT_AGY_DENIED_TOOLS


def taint_hook_files(cwd: str, *, taint_shell: str = "deny") -> dict[str, str]:
    """E21: the two files a tainted Antigravity dispatch needs in its
    worktree, keyed by their path relative to `cwd` -- `.agents/hooks.json`,
    naming one `PreToolUse` command hook per `taint_agy_matchers()` name, and
    the command hook script itself, stdlib only. `cwd` is baked into the
    hook's own command line because agy runs it as a plain subprocess with no
    fixed working directory guarantee, and quoted (W2) because a worktree path
    with a space in it would otherwise split into two arguments and the hook
    would fail silently.

    D5: under the default `taint_shell="deny"` the script denies
    `run_command` by name and carries no prefix list at all; `"allow"` is the
    opt-in that restores the prefix list instead.
    """
    script_abs = str(Path(cwd) / TAINT_AGY_SCRIPT_REL)
    prefixes = TAINT_SHELL_DENIED_PREFIXES if taint_shell == "allow" else ()
    script_text = (
        _TAINT_AGY_HOOK_SCRIPT.replace(
            "__DENIED_TOOLS__", repr(frozenset(taint_agy_denied_tools(taint_shell)))
        )
        .replace("__DENIED_PREFIXES__", repr(prefixes))
        .replace("__EDIT_TOOLS__", repr(frozenset(TAINT_AGY_EDIT_TOOLS)))
        .replace("__RESERVED_DIR__", repr(TAINT_AGY_RESERVED_DIR))
    )
    command = f"python3 {shlex.quote(script_abs)}"
    entries = [
        {"matcher": name, "hooks": [{"type": "command", "command": command}]}
        for name in taint_agy_matchers(taint_shell)
    ]
    hooks_text = json.dumps({"hooks": {"PreToolUse": entries}}, indent=2) + "\n"
    return {TAINT_AGY_HOOKS_REL: hooks_text, TAINT_AGY_SCRIPT_REL: script_text}


def build_agy_hooks_argv(cwd: str) -> list[str]:
    """F13: the free `/hooks` slash-command query `runner.dispatch` runs
    before a tainted antigravity dispatch's paid turn. Print mode answers
    with `num_turns: 0` and every usage counter zero -- no model spend --
    and a `command_result` event naming every loaded hooks file with its
    `source` path and `enabled` flag (docs/research/2026-09-07-live-probe-
    restricted-denied-sandbox.md, F13). Nothing else on the argv: no model,
    no effort, no schema.
    """
    return ["agy", "-p", "/hooks", "--output-format", "stream-json", "--add-dir", cwd]

# D3: an inline persona -- {"name", "description", "prompt", "tools": [...]}.
# `tools`, when given, is an allow list; unlike D2's deny list, it is the only
# field that is optional. Live-probed
# (docs/research/2026-09-06-live-probe-inline-agents.md): Antigravity's
# `--agent <name>` selects from a locally defined list and fails open on an
# unknown name (exit 0, `status: SUCCESS`, no persona, nothing on stderr);
# Cursor has no persona flag headless at all. Enforceable on Claude Code only.
_AGENT_KEYS = frozenset({"name", "description", "prompt", "tools"})
_AGENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")


class DispatchRefused(ValueError):
    """A dispatch that violates routing policy. Raised before anything spawns."""


@dataclass(frozen=True)
class Model:
    """One model a fleet may run, plus how each effort level names it.

    `resolve` maps an abstract effort to the concrete id the CLI expects.
    Fleets that carry effort in a separate flag map every level to the same
    id; fleets that encode effort in the id itself (Cursor, Antigravity) map
    each level to a different one.
    """

    name: str
    resolve: dict[str, str]
    vendor: str
    note: str = ""

    def id_for(self, effort: str) -> str:
        return self.resolve[effort]


def _flat(name: str, model_id: str, vendor: str, note: str = "") -> Model:
    """A model whose id does not change with effort."""
    return Model(name=name, resolve={e: model_id for e in EFFORTS}, vendor=vendor, note=note)


@dataclass(frozen=True)
class Fleet:
    name: str
    binary: str
    models: tuple[Model, ...]
    default_model: str
    vendor: str
    # How a per-dispatch dollar cap is enforced on this fleet (see budget.py):
    # "native" (the CLI stops itself), "watcher" (conductor prices the
    # fleet's running usage and kills it), or "post-hoc" (usage arrives only
    # at the end, so the cap is a verdict rather than a stop).
    cap: str

    def model(self, name: str | None) -> Model:
        wanted = name or self.default_model
        for m in self.models:
            if m.name == wanted:
                return m
        allowed = ", ".join(m.name for m in self.models)
        raise DispatchRefused(
            f"fleet '{self.name}' may not run model '{wanted}'. "
            f"Permitted on this fleet: {allowed}. "
            f"{_why_refused(self.name, wanted)}"
        )


def model_vendor(fleet: str, model: str | None) -> str:
    """The vendor behind one dispatch, resolving the fleet's default model
    when `model` is None. Raises DispatchRefused for an unknown fleet or
    model, the same as any other off-policy dispatch."""
    return FLEETS[fleet].model(model).vendor


def _why_refused(fleet: str, model: str) -> str:
    """Name the policy, not just the rule. A refusal a caller cannot act on
    gets worked around instead of respected."""
    m = model.lower()
    if fleet == "cursor" and ("claude" in m or "opus" in m or "sonnet" in m):
        return "Anthropic models come from Claude Code (fleet 'claude'), not resold via Cursor."
    if fleet == "cursor" and ("gpt" in m or "codex" in m or "sol" in m):
        return "OpenAI models come from Codex (fleet 'codex'), not resold via Cursor."
    if fleet == "cursor":
        return "Cursor is limited to its first-party pool: grok-4.6 and composer-2.5."
    if fleet == "antigravity" and ("claude" in m or "opus" in m or "sonnet" in m):
        return (
            "Anthropic models come from Claude Code (fleet 'claude'), not resold via Antigravity."
        )
    if fleet == "antigravity":
        return "Antigravity is the Gemini route; it carries no other first-party models."
    if fleet == "claude":
        return (
            "Fleet 'claude' carries Anthropic models only; try 'codex', 'antigravity', or 'cursor'."
        )
    if fleet == "codex":
        return "Fleet 'codex' carries OpenAI models only; try 'claude', 'antigravity', or 'cursor'."
    return ""


FLEETS: dict[str, Fleet] = {
    "claude": Fleet(
        name="claude",
        binary="claude",
        vendor="Anthropic (first-party)",
        default_model="sonnet",
        cap="native",
        models=(
            _flat("opus", "claude-opus-5", "anthropic"),
            _flat("sonnet", "claude-sonnet-5", "anthropic"),
            _flat("haiku", "claude-haiku-4-5", "anthropic"),
        ),
    ),
    "codex": Fleet(
        name="codex",
        binary="codex",
        vendor="OpenAI (first-party)",
        default_model="terra",
        cap="watcher",
        models=(
            _flat("terra", "gpt-5.6-terra", "openai"),
            _flat("sol", "gpt-5.6-sol", "openai"),
            _flat("luna", "gpt-5.6-luna", "openai"),
        ),
    ),
    "antigravity": Fleet(
        name="antigravity",
        binary="agy",
        vendor="Google (first-party)",
        default_model="gemini-3.8-flash",
        cap="watcher",
        models=(
            Model(
                "gemini-3.8-flash",
                {
                    "cheap": "gemini-3.8-flash-low",
                    "standard": "gemini-3.8-flash-medium",
                    "hard": "gemini-3.8-flash-high",
                    "max": "gemini-3.8-flash-high",
                },
                vendor="google",
                note="max collapses to high; Antigravity's ladder stops there",
            ),
            Model(
                "gemini-3.7-flash",
                {
                    "cheap": "gemini-3.7-flash-low",
                    "standard": "gemini-3.7-flash-medium",
                    "hard": "gemini-3.7-flash-high",
                    "max": "gemini-3.7-flash-high",
                },
                vendor="google",
                note="fallback if 3.8 is unavailable",
            ),
        ),
    ),
    "cursor": Fleet(
        name="cursor",
        binary="cursor-agent",
        vendor="Cursor first-party pool",
        default_model="grok-4.6",
        cap="post-hoc",
        models=(
            Model(
                "grok-4.6",
                {
                    "cheap": "cursor-grok-4.6-low",
                    "standard": "cursor-grok-4.6-medium",
                    "hard": "cursor-grok-4.6-high",
                    "max": "cursor-grok-4.6-xhigh",
                },
                vendor="xai",
            ),
            _flat(
                "composer-2.5",
                "composer-2.5",
                "cursor",
                note="no effort ladder; effort is ignored",
            ),
        ),
    ),
    # E6: a shell command run in the lane's worktree, not a model. One model
    # ("sh") because there is nothing to choose between; cap mode "none"
    # because it costs nothing to enforce a cap against -- see budget.py and
    # `dispatch`'s script-specific handling in runner.py.
    "script": Fleet(
        name="script",
        binary="sh",
        vendor="script (local)",
        default_model="sh",
        cap="none",
        models=(_flat("sh", "sh", "script"),),
    ),
}


# --- effort translation -------------------------------------------------
# One abstract level in, each CLI's own vocabulary out. A mission spec says
# "hard"; it does not have to know four dialects.

_CLAUDE_EFFORT = {"cheap": "low", "standard": "medium", "hard": "high", "max": "max"}
_CODEX_EFFORT = {"cheap": "low", "standard": "medium", "hard": "high", "max": "xhigh"}
_AGY_EFFORT = {"cheap": "low", "standard": "medium", "hard": "high", "max": "high"}


@dataclass(frozen=True)
class Spec:
    """One dispatch, fleet-independent."""

    fleet: str
    prompt: str
    cwd: str
    model: str | None = None
    effort: str = "standard"
    mode: str = "read"
    timeout: int | None = None
    schema: str | None = None  # path to a JSON Schema for the final message
    verdict: list[Criterion] | None = None  # checklist; runner generates its schema
    last_message: str | None = None  # path the fleet should write its answer to
    resume: str | None = None  # fleet session id to continue
    cap_usd: float | None = None  # per-dispatch dollar cap; see budget.py
    # E24: a per-lane, opt-in band added on top of cap_usd. On claude (native
    # cap) the CLI's own terminal message finishes inside cap_usd +
    # cap_grace_usd instead of being cut off mid-summary. F5: on a cursor
    # read lane (post-hoc cap) it is a band on the after-the-fact verdict --
    # a complete answer a few cents over cap_usd is not failed for it.
    # Never inherited silently; see mission.py's _ATTEMPT_KEYS handling.
    cap_grace_usd: float | None = None
    # 900s, the gate's own default: a fleet running a long suite inside one
    # tool call prints nothing until it returns, and must not be killed for it.
    stall_timeout: int | None = 900  # silence before conductor kills the fleet; 0 disables
    loop_limit: int | None = 6  # identical consecutive tool calls; 0 disables
    max_tool_calls: int | None = None  # total tool-call ceiling; 0 disables
    tool_idle_timeout: int | None = None  # no tool call before kill; 0 disables
    test_surface: list[str] | None = None  # None uses surface.DEFAULT_TEST_SURFACE
    test_policy: str = "clean"
    # A pipeline stage (mission.STAGES: build, review, fix, adversarial), or
    # None outside a staged pipeline. A "fix" or "adversarial" dispatch in
    # write mode is the only kind that triggers runner.dispatch's
    # reproduce-before-fix gate (E16 points it at the adversarial lane's own
    # base and reads the verdict the other way round).
    stage: str | None = None
    # C4: per-lane setup, teardown, and port allocation. A worktree isolates
    # files, not ports, sockets, scratch databases, or gitignored config.
    ports: int = 0  # free TCP ports to claim before the fleet spawns
    setup: str | None = None  # shell command run before the fleet spawns
    teardown: str | None = None  # shell command run after the gate, always
    include: list[str] | None = None  # untracked repo-relative paths to copy in
    # D2: this dispatch handles text pulled from outside the operator's trust
    # (an issue, a PR, a web page). Opt-in, never inferred; enforced with a
    # Claude tool deny list because no other fleet exposes one headless.
    taint: bool = False
    # D5: what a tainted dispatch may do with a shell. "deny" (the default)
    # denies it outright -- `Bash` on claude, `run_command` on antigravity.
    # "allow" is the per-lane opt-in that restores the old command-prefix
    # list, which stops the literal spellings and nothing else; the operator
    # asks for it by name, on the lane, and the receipt records which one ran.
    taint_shell: str = "deny"
    # F12: forces `--restricted --permission-mode acceptEdits` on a claude
    # read lane even without a declared `deliverable` -- a reviewer that
    # reads only (Gemini's role in Shape A, on the Claude side). A read lane
    # that declares `deliverable` gets the same shape automatically (see
    # `_build_claude`); this key is for the lane that has no file to write
    # but still benefits from the stronger, cheaper confinement. Enforceable
    # on the claude fleet, read mode only: `--restricted` refuses
    # `--permission-mode bypassPermissions` outright (probed: exit 1, nothing
    # spent), and `acceptEdits` alone cannot run a gate.
    restricted: bool = False
    # D3: an inline persona -- {"name", "description", "prompt",
    # "tools": [...] (optional)}. Opt-in, never inferred; enforceable on
    # Claude Code only (see _AGENT_KEYS above).
    agent: dict | None = None
    # E1: a lane's product can be a file, not just its reply --
    # {"path": <repo-relative>, "schema": <path, optional>, "commit": <bool,
    # optional, default True>}. Checked on the filesystem after the fleet
    # exits (runner.dispatch), never through `git status` (see the module
    # docstring there for why). F15 mission 2 item 3: `commit: false` keeps
    # the deliverable out of the harness's own commit (verify.commit_work's
    # `exclude`) -- a receipt, not source. F22: `validator`, optional, a
    # shell command run on the deliverable's before and after bytes -- see
    # `_validate_deliverable` and `runner._check_deliverable_validator`.
    deliverable: dict | None = None
    # E6: the shell command a `fleet: "script"` dispatch runs; ignored by
    # every other fleet. Refused as missing (script) or as set (any other
    # fleet) by mission.py at load time; `_validate_script` refuses it here
    # too, for a bare Spec built outside a mission.
    command: str | None = None

    def validate(self) -> None:
        if self.fleet not in FLEETS:
            raise DispatchRefused(
                f"unknown fleet '{self.fleet}'. Known: {', '.join(sorted(FLEETS))}"
            )
        if self.effort not in EFFORTS:
            raise DispatchRefused(f"unknown effort '{self.effort}'. Known: {', '.join(EFFORTS)}")
        if self.mode not in MODES:
            raise DispatchRefused(f"unknown mode '{self.mode}'. Known: {', '.join(MODES)}")
        if self.test_policy not in TEST_POLICIES:
            raise DispatchRefused(
                f"unknown test policy '{self.test_policy}'. Known: {', '.join(TEST_POLICIES)}"
            )
        if self.test_surface is not None and (
            not isinstance(self.test_surface, list)
            or not all(isinstance(pattern, str) for pattern in self.test_surface)
        ):
            raise DispatchRefused("test_surface must be a list of strings")
        for pattern in self.test_surface or []:
            path = PurePosixPath(pattern)
            if not pattern or "\0" in pattern or path.is_absolute() or ".." in path.parts:
                raise DispatchRefused(
                    "test_surface patterns must be non-empty repo-relative Git globs "
                    f"without '..': {pattern!r}"
                )
        if self.fleet != "script" and not self.prompt.strip():
            raise DispatchRefused("empty prompt")
        if self.resume is not None and (
            not isinstance(self.resume, str) or not self.resume.strip()
        ):
            raise DispatchRefused("resume must be a non-empty session id")
        FLEETS[self.fleet].model(self.model)  # raises if the model is off-policy
        if self.fleet == "script":
            self._validate_script()
        if self.verdict is not None and (
            not isinstance(self.verdict, list)
            or not all(isinstance(criterion, Criterion) for criterion in self.verdict)
        ):
            raise DispatchRefused("verdict must be a parsed checklist of Criterion values")
        if self.verdict is not None:
            ids = [criterion.id for criterion in self.verdict]
            if len(ids) != len(set(ids)):
                raise DispatchRefused("verdict criterion ids must be unique")
        if self.verdict is not None and self.schema:
            raise DispatchRefused("verdict generates its own schema; drop --schema")
        if self.verdict is not None and self.fleet == "cursor":
            raise DispatchRefused(
                "fleet 'cursor' has no structured-output flag; drop --verdict or route the "
                "dispatch to claude, codex, or antigravity"
            )
        if self.schema:
            self._validate_schema()
        # D2/E21: Claude Code exposes a headless tool deny list
        # (--disallowedTools); Antigravity exposes a per-lane PreToolUse deny
        # hook, written into the worktree by runner.dispatch
        # (taint_hook_files, see docs/research/2026-09-06-live-probe-tool-
        # deny-non-claude.md). Cursor's `.cursor/cli.json` has no rule kind
        # for its native web fetch and search tools, so a tainted lane there
        # would keep network egress whatever the config said; taint on
        # Cursor (and on Codex, which exposes no deny list at all) stays
        # refused, not run unguarded.
        if self.taint and self.fleet not in {"claude", "antigravity"}:
            detail = (
                "its .cursor/cli.json has no rule for the native web fetch and search tools"
                if self.fleet == "cursor"
                else f"{self.fleet} exposes no tool deny list headless"
            )
            raise DispatchRefused(
                f"taint is enforceable on the claude and antigravity fleets only: {detail}"
            )
        # D5: the shell mode is part of the taint mechanism, so it is refused
        # exactly where taint is -- and refused on a dispatch that is not
        # tainted at all, rather than silently describing nothing.
        if self.taint_shell not in TAINT_SHELL_MODES:
            raise DispatchRefused(
                f"unknown taint_shell '{self.taint_shell}'. Known: {', '.join(TAINT_SHELL_MODES)}"
            )
        if self.taint_shell != "deny":
            if not self.taint:
                raise DispatchRefused(
                    "taint_shell describes what a tainted dispatch may do with a shell; "
                    "set taint too, or drop it"
                )
            if self.fleet not in {"claude", "antigravity"}:
                raise DispatchRefused(
                    "taint_shell is enforceable on the claude and antigravity fleets only: "
                    f"{self.fleet} exposes no tool deny list headless"
                )
        # F12: --restricted is a Claude Code flag; every other fleet is
        # refused by name, not silently ignored. A write lane is refused too:
        # --restricted refuses --permission-mode bypassPermissions outright
        # (probed: exit 1, nothing spent), and the only mode it does run
        # under, acceptEdits, cannot run a gate.
        if self.restricted and self.fleet != "claude":
            raise DispatchRefused(
                f"restricted is enforceable on the claude fleet only: {self.fleet} exposes no "
                "--restricted flag"
            )
        if self.restricted and self.mode == "write":
            raise DispatchRefused(
                "restricted is refused on a write lane: --restricted refuses "
                "--permission-mode bypassPermissions, and acceptEdits alone cannot run a gate"
            )
        # D3: only Claude Code exposes an inline persona headless; agy's
        # --agent selects from disk and fails open on an unknown name, and
        # Cursor has no persona flag at all (see the comment above
        # TAINT_DISALLOWED_TOOLS for the probe).
        if self.agent is not None:
            if self.fleet != "claude":
                raise DispatchRefused(
                    f"agent is enforceable on the claude fleet only: {self.fleet} selects "
                    "agents from disk and ignores an unknown name"
                )
            self._validate_agent()
        if self.deliverable is not None:
            self._validate_deliverable()
        if self.cap_usd is not None:
            self._validate_cap()
        if self.cap_grace_usd is not None:
            self._validate_cap_grace()
        # Same shapes `_breaker_value` refuses (bool, non-int, negative): a
        # `timeout: true` became 1s, a float truncated, a negative or zero
        # made `_wait`'s first remaining already <= 0 so conductor spawned,
        # paid, and killed before a single poll. Unlike stall_timeout etc.,
        # 0 does not disable -- it is an immediate kill -- so it is refused
        # too. null still means the mode default.
        if self.timeout is not None and (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, int)
            or self.timeout <= 0
        ):
            raise DispatchRefused("timeout must be a positive whole number of seconds")
        for name, value in (
            ("stall_timeout", self.stall_timeout),
            ("loop_limit", self.loop_limit),
            ("max_tool_calls", self.max_tool_calls),
            ("tool_idle_timeout", self.tool_idle_timeout),
        ):
            if value is None or value == 0:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DispatchRefused(f"{name} must be positive; 0 or null disables")
        if isinstance(self.ports, bool) or not isinstance(self.ports, int) or self.ports < 0:
            raise DispatchRefused("ports must be a non-negative integer")
        if self.setup is not None and not isinstance(self.setup, str):
            raise DispatchRefused("setup must be a string")
        if self.teardown is not None and not isinstance(self.teardown, str):
            raise DispatchRefused("teardown must be a string")
        if self.include is not None and (
            not isinstance(self.include, list)
            or not all(isinstance(item, str) for item in self.include)
        ):
            raise DispatchRefused("include must be a list of strings")
        for path in self.include or []:
            p = PurePosixPath(path)
            if not path or "\0" in path or p.is_absolute() or ".." in p.parts:
                raise DispatchRefused(
                    "include paths must be non-empty repo-relative paths "
                    f"without '..': {path!r}"
                )

    def _validate_cap(self) -> None:
        """A cap conductor cannot enforce is refused, not silently ignored.

        Claude Code caps itself. Every other fleet is capped by conductor
        pricing its running usage, which needs a price for the model; an
        operator override that dropped the model from the table would
        otherwise leave the dispatch uncapped without a word.
        """
        # `prices.finite_positive`: inf/NaN never fire, and True is not a
        # dollar figure either (`True + 0.0` would put `--max-budget-usd 1`
        # on a Claude argv and write `cap_usd: true` on the receipt).
        if not prices.finite_positive(self.cap_usd):
            raise DispatchRefused("cap_usd must be a positive finite number")
        fleet = FLEETS[self.fleet]
        if fleet.cap == "native":
            return
        model_id = fleet.model(self.model).id_for(self.effort)
        if prices.lookup(model_id) is None:
            raise DispatchRefused(
                f"model '{model_id}' is unpriced, so a ${self.cap_usd} cap cannot be enforced on "
                f"fleet '{self.fleet}'; price it in prices.json or drop the cap"
            )

    def _validate_cap_grace(self) -> None:
        """E24: on claude (native cap) the grace band finishes the CLI's own
        terminal message. F5: on a cursor read lane (post-hoc cap) there is
        no terminal message to finish, but the verdict is computed after the
        run from an estimated cost, and that estimate is where a complete
        answer a few cents over cap_usd was wrongly failed (rule 7's trap);
        the band there widens the post-hoc verdict instead. It needs a
        cap_usd to extend. Every other fleet's cap is a watcher kill (codex,
        antigravity) or nothing at all (script): a killed run has no
        terminal message left to finish and no verdict left to widen, so the
        band is refused there, and refused on a cursor write lane -- a write
        lane's cost is bytes, and the band would only buy more of them."""
        if self.cap_usd is None:
            raise DispatchRefused("cap_grace_usd needs a cap_usd to extend")
        if self.fleet == "cursor":
            if self.mode != "read":
                raise DispatchRefused(
                    "cap_grace_usd is refused on a cursor write lane: a write lane's cost is "
                    "bytes, and the band would only buy more of them"
                )
        elif self.fleet != "claude":
            raise DispatchRefused(
                "cap_grace_usd is enforceable on the claude fleet and on a cursor read lane "
                f"only: {self.fleet}'s cap mode is '{FLEETS[self.fleet].cap}', which has no "
                "terminal message to finish and no post-hoc verdict to widen"
            )
        if not prices.finite_positive(self.cap_grace_usd):
            raise DispatchRefused("cap_grace_usd must be a positive finite number")
        if self.cap_grace_usd > CAP_GRACE_CEILING_USD:
            raise DispatchRefused(
                f"cap_grace_usd must not exceed the ${CAP_GRACE_CEILING_USD:.2f} ceiling"
            )

    def _validate_script(self) -> None:
        """E6: a script dispatch has no model dialogue, no tool surface to
        deny, and costs nothing -- a dollar cap has nothing to enforce
        against it. Every field refused below describes a dispatch that
        never happens on this fleet; each gets its own reason so a caller
        can act on the refusal rather than guess at it."""
        if not self.command or not self.command.strip():
            raise DispatchRefused("fleet 'script' needs a non-empty command")
        if self.schema:
            raise DispatchRefused("fleet 'script' has no structured-output flag; drop --schema")
        if self.verdict is not None:
            raise DispatchRefused("fleet 'script' has no structured-output flag; drop --verdict")
        if self.resume is not None:
            raise DispatchRefused("fleet 'script' holds no session to resume")
        if self.cap_usd is not None:
            raise DispatchRefused(
                "fleet 'script' costs nothing; a dollar cap has nothing to enforce"
            )
        if self.cap_grace_usd is not None:
            raise DispatchRefused(
                "fleet 'script' costs nothing; a dollar cap has nothing to enforce"
            )
        if self.agent is not None:
            raise DispatchRefused("fleet 'script' has no tool surface for a persona; drop --agent")
        if self.taint:
            raise DispatchRefused(
                "fleet 'script' has no tool surface to deny; taint is a no-op there"
            )
        if self.taint_shell != "deny":
            raise DispatchRefused(
                "fleet 'script' has no tool surface to deny; taint_shell is a no-op there"
            )

    def _validate_schema(self) -> None:
        """A structured-output request must fail here, not after the spend.

        Cursor has no structured-output flag at all (checked against
        cursor-agent 2026.09.02 --help); silently dropping the schema would
        hand the caller prose where it expected JSON. The schema file is also
        parsed now, because Claude Code takes the schema text inline and a
        broken file would otherwise surface as a fleet error after spawn.
        """
        if self.fleet == "cursor":
            raise DispatchRefused(
                "fleet 'cursor' has no structured-output flag; drop --schema or route the "
                "dispatch to claude, codex, or antigravity"
            )
        # F13: the live probe (docs/research/2026-09-07-live-probe-restricted-
        # denied-sandbox.md) ran a schema'd read lane under `--mode plan
        # --sandbox` and got a second turn that wrote a file into the working
        # directory and ran a shell command -- a schema turn on a plan-mode
        # Antigravity lane has written files on record. Write mode drops
        # `--mode plan --sandbox` (see `_build_antigravity`) and is unaffected.
        if self.fleet == "antigravity" and self.mode == "read":
            raise DispatchRefused(
                "fleet 'antigravity' mode 'read' refuses --schema: the live probe recorded a "
                "schema turn on a plan-mode Antigravity lane writing files into the working "
                "directory; drop --schema or switch to mode: write"
            )
        try:
            schema = json.loads(Path(self.schema).read_text())
        except OSError as exc:
            raise DispatchRefused(f"schema file unreadable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise DispatchRefused(f"schema file is not valid JSON: {exc}") from exc
        # D9: parsing is not enough. Every consumer -- the vendors' own
        # structured-output flags and `runner._schema_mismatch` alike --
        # reads a schema as an object with `required` and `properties`; a
        # top-level array parses fine and then fails as an AttributeError
        # after the spend.
        if not isinstance(schema, dict):
            raise DispatchRefused("schema file must be a JSON object")

    def _validate_agent(self) -> None:
        """D3: an inline persona's shape, checked before spawn so a typo in
        `tools` or a malformed name fails here rather than as a Claude Code
        stderr line after the fleet already started.
        """
        agent = self.agent
        if not isinstance(agent, dict):
            raise DispatchRefused("agent must be an object")
        unknown = sorted(set(agent) - _AGENT_KEYS)
        if unknown:
            raise DispatchRefused(f"agent has unknown field(s): {', '.join(unknown)}")
        for key in ("name", "description", "prompt"):
            value = agent.get(key)
            if not isinstance(value, str) or not value.strip():
                raise DispatchRefused(f"agent needs a non-empty '{key}'")
        name = agent["name"]
        if not _AGENT_NAME.fullmatch(name):
            raise DispatchRefused(f"agent name {name!r} must match {_AGENT_NAME.pattern}")
        tools = agent.get("tools")
        if tools is not None and (
            not isinstance(tools, list)
            or not all(isinstance(t, str) and t.strip() for t in tools)
        ):
            raise DispatchRefused("agent tools must be a list of non-empty strings")

    def _validate_deliverable(self) -> None:
        """E1: `path` is checked on a real filesystem after the fleet exits,
        so it must stay inside `cwd` -- never absolute, never a `..` escape,
        never resolving out through a symlink -- the same shape as `include`
        above. `schema`, when set, is parsed now for the same reason
        `_validate_schema` parses its schema now: a broken file should fail
        here, not after the run.
        """
        deliverable = self.deliverable
        if not isinstance(deliverable, dict):
            raise DispatchRefused("deliverable must be an object")
        unknown = sorted(set(deliverable) - {"path", "schema", "commit", "validator"})
        if unknown:
            raise DispatchRefused(f"deliverable has unknown field(s): {', '.join(unknown)}")
        if "commit" in deliverable and type(deliverable["commit"]) is not bool:
            raise DispatchRefused("deliverable commit must be a boolean")
        path = deliverable.get("path")
        if not isinstance(path, str) or not path:
            raise DispatchRefused("deliverable needs a non-empty 'path'")
        p = PurePosixPath(path)
        if p.is_absolute():
            raise DispatchRefused(f"deliverable path must be repo-relative, not absolute: {path!r}")
        if ".." in p.parts:
            raise DispatchRefused(f"deliverable path must not contain '..': {path!r}")
        root = Path(self.cwd).resolve()
        resolved = (root / path).resolve()
        if resolved != root and root not in resolved.parents:
            raise DispatchRefused(f"deliverable path resolves outside cwd: {path!r}")
        schema = deliverable.get("schema")
        if schema is not None:
            if not isinstance(schema, str) or not schema:
                raise DispatchRefused("deliverable schema must be a non-empty string")
            try:
                parsed = json.loads(Path(schema).read_text())
            except OSError as exc:
                raise DispatchRefused(f"deliverable schema file unreadable: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise DispatchRefused(f"deliverable schema file is not valid JSON: {exc}") from exc
            # D9: same reason as `_validate_schema` above -- `_schema_mismatch`
            # reads `required` and `properties` off this file after the run.
            if not isinstance(parsed, dict):
                raise DispatchRefused("deliverable schema file must be a JSON object")
        validator = deliverable.get("validator")
        if validator is not None and (not isinstance(validator, str) or not validator):
            raise DispatchRefused("deliverable validator must be a non-empty string")

    def resolved_timeout(self) -> int:
        return self.timeout if self.timeout is not None else DEFAULT_TIMEOUT[self.mode]


def build_argv(spec: Spec) -> list[str]:
    """Translate a Spec into one fleet's command line.

    Every builder below closes over the same three invariants:
      * the model id is policy-checked before it gets here,
      * write mode gets the fleet's own auto-approve, because an unattended
        run has nobody to answer a permission prompt,
      * read mode gets the fleet's strongest read-only setting, so a
        misbehaving research dispatch cannot edit the tree.

    E21's `--log-file` for a tainted Antigravity dispatch is not built here:
    it needs this run's own directory, which no `Spec` field carries, so
    `runner.dispatch` appends it to whatever this returns (real argv or a
    test's faked one) rather than changing this function's signature --
    dozens of tests replace `build_argv` wholesale with a single-argument
    fake, and a new required (or even optional-but-passed) parameter here
    would break every one of them.
    """
    spec.validate()
    fleet = FLEETS[spec.fleet]
    model = fleet.model(spec.model).id_for(spec.effort)
    return _BUILDERS[spec.fleet](spec, model)


def claude_restricted(spec: Spec) -> bool:
    """F12: a read lane with a declared deliverable gets the same shape as an
    explicit `restricted: true` -- a plan lane's own file is exactly this
    case, and the live gap it closes is real: a plan lane on plain `plan`
    mode can write nothing but its own plan.md, so the first live planner
    lane (2026-09-07) wrote the mission into its plan file instead of the
    declared deliverable path.

    Exported because it decides `--permission-mode acceptEdits`, which is a
    lane that can Edit and Write: `runner` reads it to know whether the
    settings digest must be taken (2026-09-08 review).
    """
    return spec.mode == "read" and (spec.restricted or spec.deliverable is not None)


def claude_can_edit(spec: Spec) -> bool:
    """Whether this claude lane's permission mode lets it write files at all.

    `bypassPermissions` on a write lane and `acceptEdits` on a restricted
    read lane both do; plain `plan` mode does not. The settings drill turns
    on exactly this: `.claude/settings.json` is a file, Claude Code
    hot-reloads it for the next subagent, and a project `permissions.deny`
    rule is therefore not a boundary for any lane that can edit it.
    """
    return spec.fleet == "claude" and (spec.mode == "write" or claude_restricted(spec))


def _build_claude(spec: Spec, model: str) -> list[str]:
    restricted = claude_restricted(spec)
    argv = [
        "claude",
        "-p",
        spec.prompt,
        "--model",
        model,
        "--effort",
        _CLAUDE_EFFORT[spec.effort],
        "--output-format",
        "stream-json",
        # Print mode otherwise refuses stream-json before the model starts,
        # leaving a lane with neither progress events nor a useful receipt.
        "--verbose",
        # Headless runs load no MCP servers: --strict-mcp-config with no
        # --mcp-config means an empty server set, which is both leaner and
        # the standing rule for unattended Claude Code.
        "--strict-mcp-config",
        # And none of the operator's own settings: a lane that loads
        # ~/.claude/settings.json runs the operator's hooks (46 invocations
        # on a one-word prompt, measured 2026-09-03, one of which rewrote a
        # memory file) and pays for the context they inject (a $0.24 reply
        # that costs $0.05 without them). Project settings still apply: they
        # are the target repo's own contract. `--bare` would go further but
        # refuses OAuth, which is how this machine is logged in.
        "--setting-sources",
        "project",
        # Roadmap B2: pin the system prompt after its first render and move
        # cwd, env info, memory paths, and git status out of it into the
        # first user message, so every lane's request begins with identical
        # bytes -- what a prompt cache needs to hit. Evidence: moving dynamic
        # content after the static prefix took one production hit rate from
        # 7% to 84% (docs/ROADMAP-2026-09.md item B2).
        "--system-prompt-snapshot",
        "on",
        "--exclude-dynamic-system-prompt-sections",
        # F12: every claude dispatch, read and write -- anything that would
        # otherwise stall on a permission prompt nobody can answer is denied
        # automatically instead, and the denial lands in the result envelope's
        # `permission_denials` rather than being invisible while the run
        # still exits 0 (docs/research/2026-09-07-live-probe-restricted-
        # denied-sandbox.md).
        "--permission-prompts",
        "none",
    ]
    if spec.mode == "write":
        # Write mode bypasses permissions outright. `acceptEdits` auto-approves
        # Edit/Write but still refuses Bash beyond `pwd`/`ls`, so a build lane
        # could edit and never run its gate: Haiku edited blind and reported
        # success, Sonnet stopped after eight refused pytest calls (live
        # 2026-09-04). The worktree is the sandbox, as it is for every fleet.
        permission_mode = "bypassPermissions"
    elif restricted:
        # F12: the only combination that actually runs restricted --
        # `--restricted` refuses `bypassPermissions` outright (probed: exit
        # 1, nothing spent), and `acceptEdits` is what lets the file tools
        # write inside the worktree while Bash and WebFetch are gone.
        permission_mode = "acceptEdits"
    else:
        permission_mode = "plan"
    argv += ["--permission-mode", permission_mode]
    if restricted:
        argv.append("--restricted")
    if spec.schema:
        # Claude Code wants the schema text, not a path: a path is rejected
        # with "--json-schema is not valid JSON". Verified live 2026-09-03.
        argv += ["--json-schema", Path(spec.schema).read_text()]
    if spec.cap_usd is not None:
        # Claude Code stops itself: exit 1, is_error, subtype
        # error_max_budget_usd, and "Reached maximum budget ($N)" under
        # `errors`, with the spend so far still reported. Verified live
        # 2026-09-03. E24: cap_grace_usd, when set, is folded into the same
        # flag -- the CLI knows only one ceiling, so its own terminal message
        # gets to finish inside cap_usd + cap_grace_usd.
        argv += ["--max-budget-usd", _usd_arg(spec.cap_usd + (spec.cap_grace_usd or 0.0))]
    if spec.resume is not None:
        argv += ["--resume", spec.resume]
    if spec.taint:
        # D2: one name per argv element; the CLI also accepts a comma-joined
        # string, but a list of exact names cannot be reassembled wrong.
        argv += ["--disallowedTools", *taint_disallowed_tools(spec.taint_shell)]
    if spec.agent:
        # D3: --agents defines the persona for the session; --agent selects
        # it for the main dispatch. One name, one compact JSON object, in
        # both read and write mode; runner.dispatch asserts it actually took
        # by reading the stream's own init event rather than trusting either
        # flag (docs/research/2026-09-06-live-probe-inline-agents.md).
        definition: dict = {
            "description": spec.agent["description"],
            "prompt": spec.agent["prompt"],
        }
        if spec.agent.get("tools") is not None:
            definition["tools"] = list(spec.agent["tools"])
        argv += [
            "--agents",
            json.dumps({spec.agent["name"]: definition}, separators=(",", ":")),
            "--agent",
            spec.agent["name"],
        ]
    return argv


def _usd_arg(value: float) -> str:
    """A dollar figure as a flag value: 0.25 -> "0.25", 5.0 -> "5"."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _build_codex(spec: Spec, model: str) -> list[str]:
    if spec.resume is not None:
        argv = [
            "codex",
            "-C",
            spec.cwd,
            "--sandbox",
            "workspace-write" if spec.mode == "write" else "read-only",
            "exec",
            "resume",
            spec.resume,
            "-m",
            model,
            "-c",
            "approval_policy=never",
            "-c",
            f"model_reasoning_effort={_CODEX_EFFORT[spec.effort]}",
            "--skip-git-repo-check",
            "--json",
        ]
        if spec.schema:
            argv += ["--output-schema", str(Path(spec.schema).resolve())]
        if spec.last_message:
            argv += ["-o", spec.last_message]
        argv.append(spec.prompt)
        return argv
    argv = [
        "codex",
        "exec",
        "-C",
        spec.cwd,
        "-m",
        model,
        # The config default is on-request; a headless dispatch stalls forever
        # on an approval prompt nobody answers.
        "-c",
        "approval_policy=never",
        "-c",
        f"model_reasoning_effort={_CODEX_EFFORT[spec.effort]}",
        "--sandbox",
        "workspace-write" if spec.mode == "write" else "read-only",
        "--skip-git-repo-check",
        # Codex prints bare text by default and reports usage nowhere. The
        # event stream is the only place its token counts appear
        # (turn.completed), and the final answer still lands in the -o file.
        "--json",
    ]
    if spec.schema:
        # Absolute, because the fleet's working directory is the target repo
        # (or its worktree), not wherever the caller typed the path.
        argv += ["--output-schema", str(Path(spec.schema).resolve())]
    if spec.last_message:
        argv += ["-o", spec.last_message]
    argv.append(spec.prompt)
    return argv


def _build_antigravity(spec: Spec, model: str) -> list[str]:
    argv = [
        "agy",
        "-p",
        spec.prompt,
        # Antigravity is the one fleet where process cwd is NOT enough: with no
        # workspace set it silently does the work in its own scratch directory
        # (~/.gemini/antigravity-cli/scratch), reports SUCCESS, and exits 0.
        # Caught live 2026-09-03 by conductor's own no-op check.
        "--add-dir",
        spec.cwd,
        "--model",
        model,
        "--effort",
        _AGY_EFFORT[spec.effort],
        # stream-json prints a step_update carrying that step's usage after
        # every model response, which is what lets conductor cap the spend
        # mid-run; the final envelope still arrives, wrapped as
        # {"event": "result", "result": {...}}.
        "--output-format",
        "stream-json",
        # agy's own print-mode cap defaults to 5m0s regardless of anything
        # conductor does with the process. Left alone, a 20-minute write
        # dispatch dies at five minutes with status ERROR ("timeout waiting
        # for response"), exit 1, and the work cut mid-way. Verified live
        # 2026-09-03 with an 8s cap on a 25s task. Pin it just under the
        # spec's cap: when agy stops itself it still prints its usage and its
        # own error, whereas conductor's process-group kill (the backstop)
        # leaves nothing to price.
        "--print-timeout",
        f"{_agy_print_timeout(spec.resolved_timeout())}s",
    ]
    if spec.schema:
        argv += ["--json-schema", str(Path(spec.schema).resolve())]
    if spec.mode == "write":
        argv += [
            "--mode",
            "accept-edits",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
        ]
    else:
        # --mode plan is silently ignored whenever --disable-slash-commands is
        # set (agy warns on stderr, and then creates the file anyway; caught
        # live 2026-09-03 by conductor's byte check). --sandbox alone only
        # restricts the terminal. Read mode therefore keeps slash-command
        # expansion on so plan mode actually holds: asked to write a file it
        # writes an implementation plan in its own brain directory instead.
        argv += ["--mode", "plan", "--sandbox"]
    if spec.resume is not None:
        argv += ["--conversation", spec.resume]
    return argv


AGY_TIMEOUT_MARGIN = 5


def _agy_print_timeout(timeout: int) -> int:
    """agy's own cap, a few seconds under conductor's hard kill."""
    return max(timeout - AGY_TIMEOUT_MARGIN, 1)


def _build_cursor(spec: Spec, model: str) -> list[str]:
    argv = [
        "cursor-agent",
        "-p",
        spec.prompt,
        "--model",
        model,
        # The single-envelope `json` format keeps only the LAST assistant
        # message as `result`. A model that writes its answer and then adds a
        # closing remark loses the answer: grok-4.6 spent 15K output tokens
        # on a brainstorm and delivered 680 characters of narration (live,
        # 2026-09-03). stream-json carries every assistant message.
        "--output-format",
        "stream-json",
    ]
    if spec.mode == "write":
        # --force implies workspace trust as well as command approval.
        argv.append("--force")
    else:
        # Read mode still needs --trust: without it Cursor stops on an
        # interactive "do you trust this directory?" prompt that a headless
        # run can never answer, and exits 1 having done nothing. --trust
        # grants workspace trust without granting command approval, so plan
        # mode stays read-only.
        argv += ["--mode", "plan", "--trust"]
    if spec.resume is not None:
        argv += ["--resume", spec.resume]
    return argv


def _build_script(spec: Spec, model: str) -> list[str]:
    # `model` is always "sh" (the fleet's only model) and carries no dialect
    # of its own; the run's own worktree is the process cwd, set by the
    # caller the same way as for every other fleet.
    return [FLEETS["script"].binary, "-c", spec.command]


_BUILDERS = {
    "claude": _build_claude,
    "codex": _build_codex,
    "antigravity": _build_antigravity,
    "cursor": _build_cursor,
    "script": _build_script,
}


# --- CLI versions (E22) --------------------------------------------------
# Every vendor CLI prints its own version on `--version`; conductor asserts
# behavior against a version at a point in time (D2's deny list, D3's inline
# personas, the Cursor stream-json parser, agy's status-not-exit-code rule),
# and a silent vendor release can move under any of that. Recording the
# version on the receipt turns "which build ran this" from a guess into
# something checkable on bytes.

_VERSION_CACHE: dict[str, str | None] = {}


def clear_version_cache() -> None:
    """Empty the per-process version cache; tests need a clean slate."""
    _VERSION_CACHE.clear()


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def cli_version(fleet_name: str, *, timeout: float = 10.0) -> str | None:
    """The fleet binary's own version line, cached per process by binary
    path so a mission with many lanes on the same fleet probes it once.

    Never raises and never prints: an unknown fleet, a binary that is not
    installed, a non-zero exit, a hang past `timeout`, or a spawn failure
    (OSError) all come back as None rather than stopping the caller.
    """
    fleet = FLEETS.get(fleet_name)
    if fleet is None:
        return None
    path = shutil.which(fleet.binary)
    if path is None:
        return None
    if path in _VERSION_CACHE:
        return _VERSION_CACHE[path]
    try:
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        version = None
    else:
        version = None
        if proc.returncode == 0:
            version = _first_nonempty_line(proc.stdout) or _first_nonempty_line(proc.stderr)
    _VERSION_CACHE[path] = version
    return version
