"""Routing policy and command-line translation.

These tests exist to fail if the allowlist ever softens: the whole point of
enforcing routing in code is that an unattended run cannot talk itself out of
it, and a test suite that only checks the happy path would not notice the
enforcement disappearing.
"""

from __future__ import annotations

import pytest

from conductor.fleets import FLEETS, DispatchRefused, Spec, build_argv
from docs import doc_section


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
    # agy ignores --mode plan whenever --disable-slash-commands is set and
    # then edits the tree anyway (caught live 2026-09-03); read mode must
    # not carry that flag. Write mode keeps it.
    assert "--disable-slash-commands" not in agy
    assert "--disable-slash-commands" in build_argv(spec(fleet="antigravity", mode="write"))
    cursor = build_argv(spec(fleet="cursor", mode="read"))
    assert "plan" in cursor
    # Without --trust, Cursor stops on an interactive trust prompt and exits 1
    # having done nothing. Verified live 2026-09-03.
    assert "--trust" in cursor


def test_write_mode_auto_approves_because_nobody_is_watching():
    claude = build_argv(spec(fleet="claude", mode="write"))
    assert "bypassPermissions" in claude
    # acceptEdits refuses Bash, so a build lane could never run its own
    # tests: Haiku edited blind, Sonnet stopped and asked. Live 2026-09-04.
    assert "acceptEdits" not in claude
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


def test_claude_headless_loads_no_operator_settings():
    """The operator's hooks ran inside every fleet lane until 2026-09-03,
    when one rewrote a memory file; project settings are the repo's own."""
    argv = build_argv(spec(fleet="claude"))
    assert argv[argv.index("--setting-sources") + 1] == "project"


def test_claude_pins_the_system_prompt_and_moves_dynamic_sections_out():
    """B2: every Claude dispatch, read and write, snapshots its system prompt
    and moves cwd/env/git status out of it, so every lane's request begins
    with identical bytes. Evidence: moving dynamic content after the static
    prefix took one production hit rate from 7% to 84%."""
    for mode in ("read", "write"):
        argv = build_argv(spec(fleet="claude", mode=mode))
        assert argv[argv.index("--system-prompt-snapshot") + 1] == "on"
        assert "--exclude-dynamic-system-prompt-sections" in argv


def test_claude_streams_progress_and_enables_verbose_print_mode():
    argv = build_argv(spec(fleet="claude"))
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


def test_timeouts_default_by_mode_and_are_never_zero():
    assert spec(mode="read").resolved_timeout() == 600
    assert spec(mode="write").resolved_timeout() == 1200
    assert spec(timeout=45).resolved_timeout() == 45
    spec(timeout=45).validate()
    spec(mode="read").validate()


@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.5, "45"])
def test_timeout_is_refused_the_way_breaker_ceilings_are(bad):
    """`timeout` was the one lifecycle integer `Spec.validate` did not
    type-check. `true` became 1, `0` made `_wait` kill before a poll, and
    a float truncated. Same shapes `_breaker_value` refuses, plus zero
    (zero does not disable a timeout; it is an immediate kill)."""
    with pytest.raises(DispatchRefused, match="positive whole number"):
        spec(timeout=bad).validate()


def test_antigravity_print_timeout_follows_the_spec_not_its_5m_default():
    """agy --print-timeout defaults to 5m0s. Verified live 2026-09-03: an 8s
    cap on a 25s task exits 1 with status ERROR "timeout waiting for
    response" and the work cut. A 20-minute write dispatch left on the
    default would die at five minutes."""
    argv = build_argv(spec(fleet="antigravity", mode="write"))
    assert argv[argv.index("--print-timeout") + 1] == "1195s"
    argv = build_argv(spec(fleet="antigravity", timeout=45))
    assert argv[argv.index("--print-timeout") + 1] == "40s"
    # agy stops itself a few seconds before conductor's kill so its usage and
    # its own error message survive; a tiny cap still yields a positive value.
    argv = build_argv(spec(fleet="antigravity", timeout=3))
    assert argv[argv.index("--print-timeout") + 1] == "1s"


def test_codex_emits_its_event_stream_so_usage_is_recorded():
    assert "--json" in build_argv(spec(fleet="codex"))


# --- structured output ---------------------------------------------------


def _schema_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text('{"type":"object","properties":{"answer":{"type":"string"}}}')
    return str(path)


def test_claude_takes_the_schema_inline_not_as_a_path(tmp_path):
    """claude --json-schema <path> fails with "not valid JSON". Verified live
    2026-09-03; the text must be inlined."""
    path = _schema_file(tmp_path)
    argv = build_argv(spec(fleet="claude", schema=path))
    value = argv[argv.index("--json-schema") + 1]
    assert value.startswith("{") and '"answer"' in value
    assert path not in argv


def test_codex_and_antigravity_take_the_schema_as_an_absolute_path(tmp_path, monkeypatch):
    """The fleet runs in the target repo, not where the caller typed the
    path, so a relative schema must be made absolute before spawn.

    Antigravity here is mode: write -- F13 refuses --schema on an
    antigravity read lane (a live probe found it takes a second turn that
    writes files under `--mode plan --sandbox`), so the schema-as-path
    assertion moves to the mode this fleet still allows it in.
    """
    path = _schema_file(tmp_path)
    codex = build_argv(spec(fleet="codex", schema=path))
    assert codex[codex.index("--output-schema") + 1] == path
    agy = build_argv(spec(fleet="antigravity", schema=path, mode="write"))
    assert agy[agy.index("--json-schema") + 1] == path
    monkeypatch.chdir(tmp_path)
    codex = build_argv(spec(fleet="codex", schema="schema.json"))
    assert codex[codex.index("--output-schema") + 1] == path
    agy = build_argv(spec(fleet="antigravity", schema="schema.json", mode="write"))
    assert agy[agy.index("--json-schema") + 1] == path


def test_antigravity_refuses_schema_in_read_mode(tmp_path):
    """F13: the live probe (docs/research/2026-09-07-live-probe-restricted-
    denied-sandbox.md) found a schema'd read lane took a second turn that
    wrote a file into the working directory under `--mode plan --sandbox`."""
    with pytest.raises(DispatchRefused, match="mode 'read' refuses --schema"):
        build_argv(spec(fleet="antigravity", schema=_schema_file(tmp_path)))


def test_cursor_refuses_a_schema_because_it_has_no_flag_for_one(tmp_path):
    """Silently dropping the schema would hand the caller prose where it
    expected JSON. Checked against cursor-agent 2026.09.02 --help."""
    with pytest.raises(DispatchRefused, match="no structured-output flag"):
        build_argv(spec(fleet="cursor", schema=_schema_file(tmp_path)))


def test_a_broken_schema_file_is_refused_before_spawn(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    with pytest.raises(DispatchRefused, match="not valid JSON"):
        build_argv(spec(fleet="claude", schema=str(bad)))
    with pytest.raises(DispatchRefused, match="unreadable"):
        build_argv(spec(fleet="codex", schema=str(tmp_path / "missing.json")))


# --- README ---------------------------------------------------------------


def _current_support_section() -> str:
    from pathlib import Path

    text = (Path(__file__).parents[1] / "README.md").read_text()
    head, _section = text.split("\n## Current support\n", 1)
    # The matrix sits at the entry point: before "Why it exists", after the
    # one-line dispatch example and nothing else.
    assert "\n## " not in head
    return doc_section("Current support")


def test_readme_current_support_matrix_names_every_fleet_and_model():
    section = _current_support_section()
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    named = {row.split("`")[1] for row in rows}
    assert named == set(FLEETS)
    for name, fleet in FLEETS.items():
        row = next(row for row in rows if row.startswith(f"| `{name}`"))
        for model in fleet.models:
            if name != "script":
                assert model.name in row, (name, model.name)


def test_readme_current_support_states_policy_not_history():
    section = _current_support_section()
    assert "Policy as of 2026-09-07, not history." in section
    assert "this table wins" in section
    assert "| `codex` | paused since 2026-09-04, operator decision |" in section
    assert "| `antigravity` | preferred, never runs the test suite |" in section
    assert "Reviewers carry no quota." in section
    assert "Shelved, not to be re-proposed without the operator raising it" in section
    for refused in (
        "| `cursor` | preferred | grok-4.6, composer-2.5 | refused | refused | refused | refused |",
        "write lanes only; refused on read",
        "read lanes only",
    ):
        assert refused in section
