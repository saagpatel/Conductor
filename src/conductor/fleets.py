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

from dataclasses import dataclass

EFFORTS = ("cheap", "standard", "hard", "max")
MODES = ("read", "write")

# Per-mode default wall-clock caps. No fleet has a native cap; an agent that
# loses its way will happily spin until something outside it says stop.
DEFAULT_TIMEOUT = {"read": 600, "write": 1200}


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
    note: str = ""

    def id_for(self, effort: str) -> str:
        return self.resolve[effort]


def _flat(name: str, model_id: str, note: str = "") -> Model:
    """A model whose id does not change with effort."""
    return Model(name=name, resolve={e: model_id for e in EFFORTS}, note=note)


@dataclass(frozen=True)
class Fleet:
    name: str
    binary: str
    models: tuple[Model, ...]
    default_model: str
    vendor: str

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
        models=(
            _flat("opus", "claude-opus-5"),
            _flat("sonnet", "claude-sonnet-5"),
            _flat("haiku", "claude-haiku-4-5"),
        ),
    ),
    "codex": Fleet(
        name="codex",
        binary="codex",
        vendor="OpenAI (first-party)",
        default_model="terra",
        models=(
            _flat("terra", "gpt-5.6-terra"),
            _flat("sol", "gpt-5.6-sol"),
            _flat("luna", "gpt-5.6-luna"),
        ),
    ),
    "antigravity": Fleet(
        name="antigravity",
        binary="agy",
        vendor="Google (first-party)",
        default_model="gemini-3.8-flash",
        models=(
            Model(
                "gemini-3.8-flash",
                {
                    "cheap": "gemini-3.8-flash-low",
                    "standard": "gemini-3.8-flash-medium",
                    "hard": "gemini-3.8-flash-high",
                    "max": "gemini-3.8-flash-high",
                },
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
                note="fallback if 3.8 is unavailable",
            ),
        ),
    ),
    "cursor": Fleet(
        name="cursor",
        binary="cursor-agent",
        vendor="Cursor first-party pool",
        default_model="grok-4.6",
        models=(
            Model(
                "grok-4.6",
                {
                    "cheap": "cursor-grok-4.6-low",
                    "standard": "cursor-grok-4.6-medium",
                    "hard": "cursor-grok-4.6-high",
                    "max": "cursor-grok-4.6-xhigh",
                },
            ),
            _flat(
                "composer-2.5",
                "composer-2.5",
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
    last_message: str | None = None  # path the fleet should write its answer to

    def validate(self) -> None:
        if self.fleet not in FLEETS:
            raise DispatchRefused(
                f"unknown fleet '{self.fleet}'. Known: {', '.join(sorted(FLEETS))}"
            )
        if self.effort not in EFFORTS:
            raise DispatchRefused(f"unknown effort '{self.effort}'. Known: {', '.join(EFFORTS)}")
        if self.mode not in MODES:
            raise DispatchRefused(f"unknown mode '{self.mode}'. Known: {', '.join(MODES)}")
        if not self.prompt.strip():
            raise DispatchRefused("empty prompt")
        FLEETS[self.fleet].model(self.model)  # raises if the model is off-policy

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
        "json",
        # Headless runs load no MCP servers: --strict-mcp-config with no
        # --mcp-config means an empty server set, which is both leaner and
        # the standing rule for unattended Claude Code.
        "--strict-mcp-config",
    ]
    argv += ["--permission-mode", "acceptEdits" if spec.mode == "write" else "plan"]
    return argv


def _build_codex(spec: Spec, model: str) -> list[str]:
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
    ]
    if spec.schema:
        argv += ["--output-schema", spec.schema]
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
        "--output-format",
        "json",
        "--disable-slash-commands",
    ]
    if spec.schema:
        argv += ["--json-schema", spec.schema]
    if spec.mode == "write":
        argv += ["--mode", "accept-edits", "--dangerously-skip-permissions"]
    else:
        argv += ["--mode", "plan", "--sandbox"]
    return argv


def _build_cursor(spec: Spec, model: str) -> list[str]:
    argv = [
        "cursor-agent",
        "-p",
        spec.prompt,
        "--model",
        model,
        "--output-format",
        "json",
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
    return argv


_BUILDERS = {
    "claude": _build_claude,
    "codex": _build_codex,
    "antigravity": _build_antigravity,
    "cursor": _build_cursor,
}
