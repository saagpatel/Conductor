"""Routing policy and command-line translation.

These tests exist to fail if the allowlist ever softens: the whole point of
enforcing routing in code is that an unattended run cannot talk itself out of
it, and a test suite that only checks the happy path would not notice the
enforcement disappearing.
"""

from __future__ import annotations

import pytest

from conductor.fleets import FLEETS, DispatchRefused, Spec, build_argv


def spec(**kw) -> Spec:
    base = dict(fleet="claude", prompt="do the thing", cwd="/tmp")
    base.update(kw)
    return Spec(**base)


# --- policy -------------------------------------------------------------


@pytest.mark.parametrize(
    "fleet,model",
    [
        ("cursor", "opus"),
        ("cursor", "claude-opus-5"),
        ("cursor", "sol"),
        ("cursor", "gpt-5.6-sol"),
        ("antigravity", "claude-sonnet-4-6"),
        ("antigravity", "opus"),
        ("claude", "grok-4.6"),
        ("codex", "gemini-3.8-flash"),
    ],
)
def test_off_policy_models_are_refused(fleet, model):
    with pytest.raises(DispatchRefused):
        build_argv(spec(fleet=fleet, model=model))


def test_refusal_names_the_first_party_route():
    """A refusal a caller cannot act on gets worked around instead of respected."""
    with pytest.raises(DispatchRefused, match="Claude Code"):
        build_argv(spec(fleet="cursor", model="opus"))
    with pytest.raises(DispatchRefused, match="Codex"):
        build_argv(spec(fleet="cursor", model="gpt-5.6-sol"))


def test_cursor_allows_only_its_first_party_pool():
    assert {m.name for m in FLEETS["cursor"].models} == {"grok-4.6", "composer-2.5"}


def test_antigravity_carries_no_anthropic_models():
    names = " ".join(m.name for m in FLEETS["antigravity"].models)
    assert "claude" not in names and "opus" not in names and "sonnet" not in names


def test_unknown_fleet_effort_and_mode_are_refused():
    with pytest.raises(DispatchRefused):
        build_argv(spec(fleet="nope"))
    with pytest.raises(DispatchRefused):
        build_argv(spec(effort="turbo"))
    with pytest.raises(DispatchRefused):
        build_argv(spec(mode="sideways"))
    with pytest.raises(DispatchRefused):
        build_argv(spec(prompt="   "))


# --- effort translation -------------------------------------------------


def test_one_effort_level_speaks_four_dialects():
    claude = build_argv(spec(fleet="claude", model="opus", effort="max"))
    codex = build_argv(spec(fleet="codex", model="sol", effort="max"))
    agy = build_argv(spec(fleet="antigravity", effort="max"))
    cursor = build_argv(spec(fleet="cursor", model="grok-4.6", effort="max"))

    assert "--effort" in claude and claude[claude.index("--effort") + 1] == "max"
    assert "model_reasoning_effort=xhigh" in codex
    # Antigravity's ladder stops at high, and it carries effort in both the
    # model id and the flag; they must agree.
    assert "gemini-3.8-flash-high" in agy
    assert agy[agy.index("--effort") + 1] == "high"
    # Cursor carries effort only in the model id.
    assert "cursor-grok-4.6-xhigh" in cursor
    assert "--effort" not in cursor


def test_composer_ignores_effort_because_it_has_no_ladder():
    for effort in ("cheap", "standard", "hard", "max"):
        argv = build_argv(spec(fleet="cursor", model="composer-2.5", effort=effort))
        assert "composer-2.5" in argv


# --- mode ---------------------------------------------------------------


def test_read_mode_is_read_only_on_every_fleet():
    assert "plan" in build_argv(spec(fleet="claude", mode="read"))
    assert "read-only" in build_argv(spec(fleet="codex", mode="read"))
    agy = build_argv(spec(fleet="antigravity", mode="read"))
    assert "--sandbox" in agy and "plan" in agy
    cursor = build_argv(spec(fleet="cursor", mode="read"))
    assert "plan" in cursor
    # Without --trust, Cursor stops on an interactive trust prompt and exits 1
    # having done nothing. Verified live 2026-09-03.
    assert "--trust" in cursor


def test_write_mode_auto_approves_because_nobody_is_watching():
    assert "acceptEdits" in build_argv(spec(fleet="claude", mode="write"))
    assert "workspace-write" in build_argv(spec(fleet="codex", mode="write"))
    assert "--dangerously-skip-permissions" in build_argv(spec(fleet="antigravity", mode="write"))
    assert "--force" in build_argv(spec(fleet="cursor", mode="write"))


def test_antigravity_is_pinned_to_the_target_directory():
    """Regression, caught live 2026-09-03: with no workspace set, agy does the
    work in ~/.gemini/antigravity-cli/scratch, reports SUCCESS, and exits 0.
    Process cwd alone does not steer it; --add-dir does."""
    argv = build_argv(spec(fleet="antigravity", cwd="/some/target/repo", mode="write"))
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == "/some/target/repo"


def test_codex_never_waits_on_an_approval_prompt():
    """The config default is on-request; headless, that is an infinite stall."""
    argv = build_argv(spec(fleet="codex"))
    assert "approval_policy=never" in argv


def test_claude_headless_loads_no_mcp_servers():
    assert "--strict-mcp-config" in build_argv(spec(fleet="claude"))


def test_timeouts_default_by_mode_and_are_never_zero():
    assert spec(mode="read").resolved_timeout() == 600
    assert spec(mode="write").resolved_timeout() == 1200
    assert spec(timeout=45).resolved_timeout() == 45
