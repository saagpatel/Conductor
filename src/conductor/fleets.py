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
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import prices
from .verdicts import Criterion

EFFORTS = ("cheap", "standard", "hard", "max")
MODES = ("read", "write")
TEST_POLICIES = ("clean", "allow", "forbid")
# The company behind a model, independent of which fleet dispatches it. A
# fleet can carry more than one vendor (cursor resells Grok and runs its own
# Composer); a judge scoring a lane on its own vendor is what A3 refuses.
VENDORS = ("anthropic", "openai", "google", "xai", "cursor")

# Per-mode default wall-clock caps. No fleet has a native cap; an agent that
# loses its way will happily spin until something outside it says stop.
DEFAULT_TIMEOUT = {"read": 600, "write": 1200}

# D2: what a tainted Claude dispatch may not use, headless. Every entry closes
# one path an instruction hiding in quoted outside text (an issue, a PR, a web
# page) could otherwise use to do damage beyond misleading this one dispatch's
# own answer or diff.
TAINT_DISALLOWED_TOOLS: tuple[str, ...] = (
    "WebFetch",  # network egress: exfiltrate repo contents, fetch a second-stage payload
    "WebSearch",  # network egress, same risk as WebFetch
    "Task",  # a subagent inherits the tainted context without inheriting this deny list
    "Agent",  # Claude Code's other subagent-spawning name; same risk as Task
    "Bash(curl *)",  # network egress via the shell
    "Bash(wget *)",  # network egress via the shell
    "Bash(git push *)",  # push rights: an injected instruction must not publish anything
    "Bash(gh *)",  # push rights and network egress via the GitHub CLI
    "Bash(ssh *)",  # remote command execution and network egress
    "Bash(scp *)",  # file exfiltration over the network
)

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
    # 900s, the gate's own default: a fleet running a long suite inside one
    # tool call prints nothing until it returns, and must not be killed for it.
    stall_timeout: int | None = 900  # silence before conductor kills the fleet; 0 disables
    loop_limit: int | None = 6  # identical consecutive tool calls; 0 disables
    max_tool_calls: int | None = None  # total tool-call ceiling; 0 disables
    tool_idle_timeout: int | None = None  # no tool call before kill; 0 disables
    test_surface: list[str] | None = None  # None uses surface.DEFAULT_TEST_SURFACE
    test_policy: str = "clean"
    # A pipeline stage (mission.STAGES: build, review, fix), or None outside
    # a staged pipeline. A "fix" dispatch in write mode is the only one that
    # triggers runner.dispatch's reproduce-before-fix gate.
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
    # D3: an inline persona -- {"name", "description", "prompt",
    # "tools": [...] (optional)}. Opt-in, never inferred; enforceable on
    # Claude Code only (see _AGENT_KEYS above).
    agent: dict | None = None
    # E1: a lane's product can be a file, not just its reply --
    # {"path": <repo-relative>, "schema": <path, optional>}. Checked on the
    # filesystem after the fleet exits (runner.dispatch), never through
    # `git status` (see the module docstring there for why).
    deliverable: dict | None = None

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
        if not self.prompt.strip():
            raise DispatchRefused("empty prompt")
        if self.resume is not None and (
            not isinstance(self.resume, str) or not self.resume.strip()
        ):
            raise DispatchRefused("resume must be a non-empty session id")
        FLEETS[self.fleet].model(self.model)  # raises if the model is off-policy
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
        # D2: only Claude Code exposes a headless tool deny list
        # (--disallowedTools); antigravity and cursor have nothing to enforce
        # taint with, so a tainted dispatch on either is refused, not run
        # unguarded.
        if self.taint and self.fleet != "claude":
            raise DispatchRefused(
                f"taint is enforceable on the claude fleet only: {self.fleet} exposes no "
                "tool deny list headless"
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
        # inf passes a plain "> 0" and no finite spend ever exceeds it, which
        # would leave a watcher fleet uncapped with the flag still set.
        if not (math.isfinite(self.cap_usd) and self.cap_usd > 0):
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
        try:
            json.loads(Path(self.schema).read_text())
        except OSError as exc:
            raise DispatchRefused(f"schema file unreadable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise DispatchRefused(f"schema file is not valid JSON: {exc}") from exc

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
        unknown = sorted(set(deliverable) - {"path", "schema"})
        if unknown:
            raise DispatchRefused(f"deliverable has unknown field(s): {', '.join(unknown)}")
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
                json.loads(Path(schema).read_text())
            except OSError as exc:
                raise DispatchRefused(f"deliverable schema file unreadable: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise DispatchRefused(f"deliverable schema file is not valid JSON: {exc}") from exc

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
    """
    spec.validate()
    fleet = FLEETS[spec.fleet]
    model = fleet.model(spec.model).id_for(spec.effort)
    return _BUILDERS[spec.fleet](spec, model)


def _build_claude(spec: Spec, model: str) -> list[str]:
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
    ]
    # Write mode bypasses permissions outright. `acceptEdits` auto-approves
    # Edit/Write but still refuses Bash beyond `pwd`/`ls`, so a build lane
    # could edit and never run its gate: Haiku edited blind and reported
    # success, Sonnet stopped after eight refused pytest calls (live
    # 2026-09-04). The worktree is the sandbox, as it is for every fleet.
    argv += ["--permission-mode", "bypassPermissions" if spec.mode == "write" else "plan"]
    if spec.schema:
        # Claude Code wants the schema text, not a path: a path is rejected
        # with "--json-schema is not valid JSON". Verified live 2026-09-03.
        argv += ["--json-schema", Path(spec.schema).read_text()]
    if spec.cap_usd is not None:
        # Claude Code stops itself: exit 1, is_error, subtype
        # error_max_budget_usd, and "Reached maximum budget ($N)" under
        # `errors`, with the spend so far still reported. Verified live
        # 2026-09-03.
        argv += ["--max-budget-usd", _usd_arg(spec.cap_usd)]
    if spec.resume is not None:
        argv += ["--resume", spec.resume]
    if spec.taint:
        # D2: one name per argv element; the CLI also accepts a comma-joined
        # string, but a list of exact names cannot be reassembled wrong.
        argv += ["--disallowedTools", *TAINT_DISALLOWED_TOOLS]
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


_BUILDERS = {
    "claude": _build_claude,
    "codex": _build_codex,
    "antigravity": _build_antigravity,
    "cursor": _build_cursor,
}
