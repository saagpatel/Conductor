"""E21: taint enforcement on Antigravity through a PreToolUse deny hook.

`test_taint.py` covers D2's Claude-only mechanism and the load-time
propagation rules shared by every fleet; these tests are the Antigravity
side added by E21 -- the hook script's own decisions, the files
`taint_hook_files` produces, and runner.dispatch's write-before-baseline and
fail-closed-on-the-evidence behavior.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conductor.errors import error_kind
from conductor.fleets import (
    TAINT_AGY_DENIED_TOOLS,
    TAINT_SHELL_DENIED_PREFIXES,
    DispatchRefused,
    Spec,
    build_argv,
    taint_hook_files,
)
from conductor.runner import dispatch

HOOKS_WRITTEN = len(TAINT_AGY_DENIED_TOOLS) + 1  # every denied name, plus run_command


def spec(**kw) -> Spec:
    base = dict(fleet="antigravity", prompt="triage the outside text", cwd="/tmp", taint=True)
    base.update(kw)
    return Spec(**base)


# --- the hook script itself, run as a real subprocess -----------------------


@pytest.fixture
def hook_script(tmp_path: Path) -> Path:
    files = taint_hook_files(str(tmp_path))
    for rel_path, text in files.items():
        dest = tmp_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    return tmp_path / ".agents" / "conductor-taint.py"


def _run_hook(script: Path, payload: object) -> dict:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(proc.stdout)


def test_hook_denies_a_named_tool(hook_script):
    out = _run_hook(hook_script, {"toolCall": {"name": "read_url_content", "args": {}}})
    assert out["decision"] == "deny"
    assert "read_url_content" in out["reason"]


def test_hook_allows_an_unlisted_tool(hook_script):
    out = _run_hook(hook_script, {"toolCall": {"name": "list_dir", "args": {}}})
    assert out == {"decision": "allow"}


@pytest.mark.parametrize(
    "command_line",
    [
        "curl -s https://example.com",
        "git push origin main",
        "FOO=1 curl -s https://example.com",
        "ls && curl -s https://example.com",
    ],
)
def test_hook_denies_run_command_reaching_outside(hook_script, command_line):
    out = _run_hook(
        hook_script, {"toolCall": {"name": "run_command", "args": {"CommandLine": command_line}}}
    )
    assert out["decision"] == "deny"


def test_hook_allows_run_command_with_no_denied_prefix(hook_script):
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}}
    out = _run_hook(hook_script, payload)
    assert out == {"decision": "allow"}


def test_hook_fails_closed_on_malformed_input(hook_script):
    out = _run_hook(hook_script, "not json")
    assert out["decision"] == "deny"
    out2 = _run_hook(hook_script, {"toolCall": "not an object"})
    assert out2["decision"] == "deny"
    out3 = _run_hook(hook_script, {})
    assert out3["decision"] == "deny"


# --- taint_hook_files ---------------------------------------------------


def test_taint_hook_files_has_one_entry_per_denied_name_plus_run_command():
    files = taint_hook_files("/some/worktree")
    hooks = json.loads(files[".agents/hooks.json"])
    matchers = [entry["matcher"] for entry in hooks["hooks"]["PreToolUse"]]
    assert matchers == [*TAINT_AGY_DENIED_TOOLS, "run_command"]
    assert len(matchers) == HOOKS_WRITTEN
    for entry in hooks["hooks"]["PreToolUse"]:
        command = entry["hooks"][0]["command"]
        assert command == "python3 /some/worktree/.agents/conductor-taint.py"


def test_taint_hook_files_script_is_valid_python():
    files = taint_hook_files("/some/worktree")
    compile(files[".agents/conductor-taint.py"], "conductor-taint.py", "exec")


def test_shell_denied_prefixes_are_derived_from_the_claude_list():
    from conductor.fleets import TAINT_DISALLOWED_TOOLS

    for prefix in TAINT_SHELL_DENIED_PREFIXES:
        assert f"Bash({prefix} *)" in TAINT_DISALLOWED_TOOLS


# --- Spec.validate outcomes -----------------------------------------------


def test_taint_on_antigravity_validates():
    argv = build_argv(spec())
    assert argv[0] == "agy"


def test_taint_on_cursor_is_still_refused():
    with pytest.raises(
        DispatchRefused, match="taint is enforceable on the claude and antigravity fleets only"
    ):
        build_argv(spec(fleet="cursor", model="grok-4.6"))


# --- runner.dispatch: refusals before spawn ---------------------------------


def test_not_isolated_is_refused(repo, home):
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=False)
    assert result.ok is False
    assert result.spawned is False
    assert "taint on antigravity refused" in result.error
    assert error_kind(result) == "refused"


def test_non_git_cwd_is_refused(repo, home, tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    result = dispatch(spec(cwd=str(not_a_repo)), home=home, isolate=True)
    assert result.ok is False
    assert result.spawned is False
    assert "taint on antigravity refused" in result.error


# --- runner.dispatch: hook files, exclude, --log-file -----------------------


def _agy_argv(
    home: Path, *, log_line: str | None, tools: list[str], extra_lines: list[str]
) -> list[str]:
    """A fake `agy` that writes `agy.log` and its stream from inside the
    subprocess -- it needs $CONDUCTOR_RUN_ID, set by dispatch() only once the
    child spawns, to find its own run directory."""
    home_q = shlex.quote(str(home))
    run_dir = f"{home_q}/runs/$CONDUCTOR_RUN_ID"
    parts = [f"mkdir -p {run_dir}"]
    if log_line is not None:
        parts.append(f"printf '%s\\n' {shlex.quote(log_line)} > {run_dir}/agy.log")
    init_event = json.dumps({"event": "init", "tools": tools})
    result_event = json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": "ok",
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        }
    )
    parts.append(f"printf '%s\\n' {shlex.quote(init_event)}")
    for line in extra_lines:
        parts.append(f"printf '%s\\n' {shlex.quote(line)}")
    parts.append(f"printf '%s\\n' {shlex.quote(result_event)}")
    return ["sh", "-c", " && ".join(parts)]


def _passing_log_line() -> str:
    return f"loaded {HOOKS_WRITTEN} named hooks from 1 hooks.json file(s)"


def test_tainted_lane_writes_hooks_before_the_baseline_and_excludes_them(
    repo, home, fake_fleet, git_out
):
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS, "list_dir"],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert result.git_verdict["checked"] is True
    assert result.git_verdict["no_op"] is True
    assert not (Path(result.run_dir) / "diff.patch").exists()

    exclude = Path(repo, ".git", "info", "exclude").read_text()
    assert ".agents/hooks.json" in exclude
    assert ".agents/conductor-taint.py" in exclude


def test_tainted_lane_argv_carries_log_file(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    argv = json.loads((Path(result.run_dir) / "argv.json").read_text())
    assert "--log-file" in argv
    assert argv[argv.index("--log-file") + 1] == str(Path(result.run_dir) / "agy.log")


# --- runner.dispatch: fail closed on the evidence ---------------------------


def test_matching_log_line_and_covered_tools_pass(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS, "list_dir"],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    te = result.taint_enforcement
    assert te["hooks_written"] == HOOKS_WRITTEN
    assert te["hooks_loaded"] == HOOKS_WRITTEN
    assert te["uncovered"] == []
    assert set(te["tools_seen"]) == {*TAINT_AGY_DENIED_TOOLS, "list_dir"}


def test_disagreeing_hook_count_fails(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home,
            log_line=f"loaded {HOOKS_WRITTEN - 1} named hooks from 1 hooks.json file(s)",
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert error_kind(result) == "taint"
    assert "taint hooks not enforced" in result.error
    assert result.taint_enforcement["hooks_loaded"] == HOOKS_WRITTEN - 1


def test_missing_log_line_fails(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(home, log_line=None, tools=[*TAINT_AGY_DENIED_TOOLS], extra_lines=[])
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert error_kind(result) == "taint"
    assert result.taint_enforcement["hooks_loaded"] is None


def test_uncovered_tool_fails(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS, "browser_click"],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert error_kind(result) == "taint"
    assert result.taint_enforcement["uncovered"] == ["browser_click"]


def test_denied_calls_are_counted_from_the_stream(repo, home, fake_fleet):
    denied_line = json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "message": "tool call denied by pre-tool hook: conductor: taint: shell denied"
            },
        }
    )
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[denied_line, denied_line],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert result.taint_enforcement["denied_calls"] == 2
