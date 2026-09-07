"""E21: taint enforcement on Antigravity through a PreToolUse deny hook.

`test_taint.py` covers D2's Claude-only mechanism and the load-time
propagation rules shared by every fleet; these tests are the Antigravity
side added by E21 -- the hook script's own decisions, the files
`taint_hook_files` produces, and runner.dispatch's write-before-baseline and
fail-closed-on-the-evidence behavior.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.errors import error_kind
from conductor.fleets import (
    TAINT_AGY_DENIED_TOOLS,
    TAINT_AGY_EDIT_TOOLS,
    TAINT_AGY_HOOKS_REL,
    TAINT_AGY_SCRIPT_REL,
    TAINT_SHELL_DENIED_PREFIXES,
    DispatchRefused,
    Spec,
    build_argv,
    taint_agy_matchers,
    taint_hook_files,
)
from conductor.runner import dispatch

HOOKS_WRITTEN = len(taint_agy_matchers())  # one entry per matcher written


def spec(**kw) -> Spec:
    base = dict(fleet="antigravity", prompt="triage the outside text", cwd="/tmp", taint=True)
    base.update(kw)
    return Spec(**base)


def _hooks_preflight_argv(hooks: list[dict] | None):
    """F13: a fake `build_agy_hooks_argv` -- never the real `agy` binary,
    per the repo's fake-it-with-a-script-on-PATH rule. Reports `hooks` as
    the free `/hooks` query's `command_result` answer; `None` reports
    whichever `.agents/hooks.json` actually exists at the real `cwd` it is
    called with, matching what the dispatch itself just wrote there."""

    def _argv(cwd: str) -> list[str]:
        if hooks is None:
            hooks_path = os.path.join(cwd, TAINT_AGY_HOOKS_REL)
            if os.path.exists(hooks_path):
                with open(hooks_path) as fh:
                    written = json.load(fh)["hooks"]["PreToolUse"]
                actions = [
                    {
                        "event": "PreToolUse",
                        "matcher": entry["matcher"],
                        "type": "command",
                        "command": entry["hooks"][0]["command"],
                    }
                    for entry in written
                ]
                found = [
                    {"name": "hooks", "enabled": True, "source": hooks_path, "actions": actions}
                ]
            else:
                found = []
        else:
            found = hooks
        payload = json.dumps(
            {"event": "command_result", "command": {"name": "hooks", "data": {"hooks": found}}}
        )
        return ["sh", "-c", f"printf '%s\\n' {shlex.quote(payload)}"]

    return _argv


@pytest.fixture(autouse=True)
def _default_hooks_preflight(monkeypatch):
    """Every test in this file gets a preflight that answers truthfully
    from the real `.agents/hooks.json` the dispatch wrote, so only the
    tests that mean to exercise a failing preflight need to override it."""
    monkeypatch.setattr(runner_mod, "build_agy_hooks_argv", _hooks_preflight_argv(None))


# --- the hook script itself, run as a real subprocess -----------------------


def _write_hook(root: Path, *, taint_shell: str = "deny") -> Path:
    for rel_path, text in taint_hook_files(str(root), taint_shell=taint_shell).items():
        dest = root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    return root / TAINT_AGY_SCRIPT_REL


@pytest.fixture
def hook_script(tmp_path: Path) -> Path:
    return _write_hook(tmp_path)


@pytest.fixture
def prefix_hook_script(tmp_path: Path) -> Path:
    """D5: the opt-in `taint_shell: "allow"` script -- the pre-2026-09-07
    behavior, kept under test because the opt-in still ships."""
    return _write_hook(tmp_path / "allow", taint_shell="allow")


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
        # D5 (Astra 2026-09-07, probed): every one of these ran under the
        # prefix list, which is why the shell is denied whole now.
        "command curl -s https://example.com",
        "/usr/bin/curl -s https://example.com",
        "env curl -s https://example.com",
        "bash -c 'curl -s https://example.com'",
        "python3 -c \"import urllib.request\"",
        "nc example.com 80",
        "ls",
    ],
)
def test_hook_denies_run_command_outright_by_default(hook_script, command_line):
    """D5, operator decision: a tainted lane runs no shell. The command line
    is not consulted at all -- `run_command` is denied by name."""
    out = _run_hook(
        hook_script, {"toolCall": {"name": "run_command", "args": {"CommandLine": command_line}}}
    )
    assert out["decision"] == "deny"
    assert "run_command" in out["reason"]


def test_hook_script_carries_no_prefix_list_by_default(hook_script):
    """A prefix list in the default script would be a boundary that is not
    one; there is nothing left for it to decide."""
    assert "DENIED_PREFIXES = ()" in hook_script.read_text()


@pytest.mark.parametrize(
    "command_line",
    ["curl -s https://example.com", "git push origin main", "FOO=1 curl -s https://x"],
)
def test_prefix_hook_denies_a_denied_prefix(prefix_hook_script, command_line):
    out = _run_hook(
        prefix_hook_script,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": command_line}}},
    )
    assert out["decision"] == "deny"


def test_prefix_hook_allows_run_command_with_no_denied_prefix(prefix_hook_script):
    """The opt-in restores the old behavior in full, bypasses included --
    which is what the README now says out loud."""
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}}
    assert _run_hook(prefix_hook_script, payload) == {"decision": "allow"}
    bypass = {"toolCall": {"name": "run_command", "args": {"CommandLine": "env curl -s https://x"}}}
    assert _run_hook(prefix_hook_script, bypass) == {"decision": "allow"}


@pytest.mark.parametrize("tool", TAINT_AGY_EDIT_TOOLS)
def test_hook_denies_an_edit_tool_naming_the_policy_directory(hook_script, tool):
    """W1: the hook files sit in a writable worktree and are re-read on every
    tool call. The payload shape for these tools is not on record from any
    probe, so this scans the call's string arguments for a `.agents` path
    component; the digest check in the runner is the evidence that does not
    depend on the shape."""
    out = _run_hook(
        hook_script,
        {"toolCall": {"name": tool, "args": {"TargetFile": ".agents/conductor-taint.py"}}},
    )
    assert out["decision"] == "deny"
    assert ".agents" in out["reason"]


def test_hook_allows_an_edit_tool_elsewhere_in_the_worktree(hook_script):
    out = _run_hook(
        hook_script,
        {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "src/thing.py"}}},
    )
    assert out == {"decision": "allow"}


def test_hook_fails_closed_on_malformed_input(hook_script):
    out = _run_hook(hook_script, "not json")
    assert out["decision"] == "deny"
    out2 = _run_hook(hook_script, {"toolCall": "not an object"})
    assert out2["decision"] == "deny"
    out3 = _run_hook(hook_script, {})
    assert out3["decision"] == "deny"


# --- taint_hook_files ---------------------------------------------------


def test_taint_hook_files_has_one_entry_per_matcher():
    files = taint_hook_files("/some/worktree")
    hooks = json.loads(files[".agents/hooks.json"])
    matchers = [entry["matcher"] for entry in hooks["hooks"]["PreToolUse"]]
    assert matchers == list(taint_agy_matchers())
    assert "run_command" in matchers  # D5: matched, and denied by name
    assert len(matchers) == HOOKS_WRITTEN
    for entry in hooks["hooks"]["PreToolUse"]:
        command = entry["hooks"][0]["command"]
        assert command == "python3 /some/worktree/.agents/conductor-taint.py"


def test_taint_hook_files_matchers_do_not_change_with_the_shell_mode():
    """The opt-in changes the hook's decision for `run_command`, never which
    calls it sees -- so the preflight's matcher list is one constant."""
    deny = json.loads(taint_hook_files("/w")[".agents/hooks.json"])
    allow = json.loads(taint_hook_files("/w", taint_shell="allow")[".agents/hooks.json"])
    assert [e["matcher"] for e in deny["hooks"]["PreToolUse"]] == [
        e["matcher"] for e in allow["hooks"]["PreToolUse"]
    ]


def test_taint_hook_command_path_is_quoted(tmp_path):
    """W2: `fleets.py` emitted `python3 <path>` unquoted, so a worktree path
    with a space in it split into two arguments and the hook failed
    silently. The written command must survive shlex.split back into one
    interpreter and one script path -- and the script must actually run."""
    worktree = tmp_path / "a worktree with spaces"
    worktree.mkdir()
    script = _write_hook(worktree)
    hooks = json.loads((worktree / TAINT_AGY_HOOKS_REL).read_text())
    command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert " " in str(script)
    assert shlex.split(command) == ["python3", str(script)]
    out = _run_hook(script, {"toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}})
    assert out["decision"] == "deny"


def test_denied_tools_cover_every_browser_tool_in_a_recorded_agy_stream():
    """Review finding (Grok, E21): the live probe's writeup never enumerated
    every `browser_*` tool name; a recorded transcript in this repo does
    (57 tools). There is no wildcard matcher, so every one of them must be
    individually denied or the hook simply never sees it."""
    fixture = (
        Path(__file__).parent
        / "golden"
        / "c5-review-fix"
        / "runs"
        / "20260905T182328Z-antigravity-you-are-one-lane-of-a-conductor"
        / "stdout.jsonl"
    )
    tools = json.loads(fixture.read_text().splitlines()[0])["init"]["tools"]
    browser_tools = [name for name in tools if name.startswith("browser_")]
    assert browser_tools, "fixture must actually list browser_* tools, or this test proves nothing"
    missing = sorted(set(browser_tools) - set(TAINT_AGY_DENIED_TOOLS))
    assert missing == []


def test_taint_hook_files_script_is_valid_python():
    files = taint_hook_files("/some/worktree")
    compile(files[".agents/conductor-taint.py"], "conductor-taint.py", "exec")


def test_shell_denied_prefixes_are_derived_from_the_opt_in_claude_list():
    from conductor.fleets import TAINT_DISALLOWED_TOOLS, TAINT_SHELL_PREFIX_DISALLOWED_TOOLS

    for prefix in TAINT_SHELL_DENIED_PREFIXES:
        assert f"Bash({prefix} *)" in TAINT_SHELL_PREFIX_DISALLOWED_TOOLS
    # D5: the default list has no prefixes left to derive from -- it denies
    # the shell whole.
    assert not [name for name in TAINT_DISALLOWED_TOOLS if name.startswith("Bash(")]


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
    home: Path,
    *,
    log_line: str | None,
    tools: list[str],
    extra_lines: list[str],
    marker: Path | None = None,
    tamper: str | None = None,
) -> list[str]:
    """A fake `agy` that writes `agy.log` and its stream from inside the
    subprocess -- it needs $CONDUCTOR_RUN_ID, set by dispatch() only once the
    child spawns, to find its own run directory. `marker`, when given, is
    touched first -- proof this is the paid turn's own argv, distinct from
    the free `/hooks` preflight query, that actually ran."""
    home_q = shlex.quote(str(home))
    run_dir = f"{home_q}/runs/$CONDUCTOR_RUN_ID"
    parts = [f"mkdir -p {run_dir}"]
    if tamper is not None:
        # W1: the lane rewrites its own deny script mid-run. The hook files
        # live in the writable worktree and are re-read on every tool call.
        script_q = shlex.quote(TAINT_AGY_SCRIPT_REL)
        parts.append(
            f"printf '%s\\n' {shlex.quote(tamper)} >> \"$CONDUCTOR_WORKTREE\"/{script_q}"
        )
    if marker is not None:
        parts.append(f"touch {shlex.quote(str(marker))}")
    if log_line is not None:
        parts.append(f"printf '%s\\n' {shlex.quote(log_line)} > {run_dir}/agy.log")
    init_event = json.dumps({"event": "init", "conversation_id": "c1", "init": {"tools": tools}})
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
    # The real shape (F10 anti-slop consumer, 2026-09-07): agy counts named
    # hooks per hooks.json file, so the whole deny file is one named hook.
    return "loaded 1 named hooks from 1 hooks.json file(s)"


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

    # The no-op verdict above is the proof the hook files stayed out of the
    # bytes; the shared info/exclude, which every worktree of the repository
    # reads, is never the lever (see test_hook_files_are_kept_untracked_...).
    shared = Path(repo, ".git", "info", "exclude")
    assert not shared.is_file() or ".agents/" not in shared.read_text()


def test_hook_files_are_kept_untracked_by_the_worktree_scoped_excludes_file(repo, home, git_out):
    """Review finding (Grok, E21): `git rev-parse --git-path info/exclude`
    resolves to the file every worktree of the repository shares, so writing
    there mutates the operator's checkout. The hook paths go through the same
    worktree-scoped `core.excludesFile` that `include` uses instead."""
    from conductor import worktrees
    from conductor.runner import _apply_include, _write_taint_agy_hooks

    iso = worktrees.create(str(repo), "taint-probe", home / "worktrees")
    assert iso.active, iso.reason
    written, digests = _write_taint_agy_hooks(iso.worktree, iso)
    assert sorted(written) == [".agents/conductor-taint.py", ".agents/hooks.json"]
    assert sorted(digests) == [".agents/conductor-taint.py", ".agents/hooks.json"]
    assert git_out(Path(iso.worktree), "status", "--porcelain").strip() != ""
    _, _, exclude_file = _apply_include(
        spec(cwd=iso.worktree), iso, home, "taint-probe", extra_excludes=written
    )
    assert exclude_file is not None
    listed = exclude_file.read_text()
    assert ".agents/hooks.json" in listed and ".agents/conductor-taint.py" in listed
    assert git_out(Path(iso.worktree), "status", "--porcelain").strip() == ""
    shared = Path(repo, ".git", "info", "exclude")
    assert not shared.is_file() or ".agents/" not in shared.read_text()
    worktrees.release(iso)


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
    assert te["hooks_loaded"] == 1
    assert te["preflight"]["matchers_missing"] == []
    assert te["uncovered"] == []
    assert set(te["tools_seen"]) == {*TAINT_AGY_DENIED_TOOLS, "list_dir"}


def test_zero_named_hooks_loaded_fails(repo, home, fake_fleet):
    """The live probe's malformed-file signal: agy logs "loaded 0 named
    hooks from 1 hooks.json file(s)" and runs on with nothing denied."""
    fake_fleet(
        _agy_argv(
            home,
            log_line="loaded 0 named hooks from 1 hooks.json file(s)",
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert error_kind(result) == "taint"
    assert "taint hooks not enforced" in result.error
    assert "did not parse" in result.error
    assert result.taint_enforcement["hooks_loaded"] == 0


def test_one_named_hook_for_many_matchers_is_the_passing_shape(repo, home, fake_fleet):
    """Regression for the F10 anti-slop consumer's Gemini lane: thirty
    matchers written, agy logged one named hook, and the count check failed
    a lane whose hooks the preflight had already shown loaded in full."""
    fake_fleet(
        _agy_argv(
            home,
            log_line="loaded 1 named hooks from 1 hooks.json file(s)",
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert result.taint_enforcement["hooks_written"] == HOOKS_WRITTEN
    assert result.taint_enforcement["hooks_loaded"] == 1


def test_missing_log_line_fails(repo, home, fake_fleet):
    fake_fleet(_agy_argv(home, log_line=None, tools=[*TAINT_AGY_DENIED_TOOLS], extra_lines=[]))
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


def test_uncovered_tool_fails_with_the_real_nested_init_event_shape(repo, home, fake_fleet):
    """Review finding (Grok, E21): a real `agy` init event nests its tool
    list under `init.tools`, not at the event's top level -- confirmed
    against a recorded transcript,
    tests/golden/c5-review-fix/runs/20260905T182328Z-antigravity-.../stdout.jsonl
    line 1: `{"event": "init", "conversation_id": ..., "init": {"tools":
    [...], ...}}`. An uncovered tool in that real shape must still fail the
    run; a top-level-only reader silently sees an empty tool list and passes
    every uncovered tool through.
    """
    init_event = json.dumps(
        {
            "event": "init",
            "conversation_id": "c1",
            "init": {"tools": [*TAINT_AGY_DENIED_TOOLS, "browser_future_tool"]},
        }
    )
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
    home_q = shlex.quote(str(home))
    run_dir = f"{home_q}/runs/$CONDUCTOR_RUN_ID"
    script = " && ".join(
        [
            f"mkdir -p {run_dir}",
            f"printf '%s\\n' {shlex.quote(_passing_log_line())} > {run_dir}/agy.log",
            f"printf '%s\\n' {shlex.quote(init_event)}",
            f"printf '%s\\n' {shlex.quote(result_event)}",
        ]
    )
    fake_fleet(["sh", "-c", script])
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False, "an uncovered tool in the real nested init event must fail the run"
    assert error_kind(result) == "taint"
    assert result.taint_enforcement["tools_seen"] == [
        *TAINT_AGY_DENIED_TOOLS,
        "browser_future_tool",
    ]
    assert result.taint_enforcement["uncovered"] == ["browser_future_tool"]


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


# --- W1: the hook files must be the ones conductor wrote --------------------


def test_a_lane_rewriting_its_own_deny_script_fails_the_run(repo, home, fake_fleet):
    """W1 (Astra 2026-09-07): every other piece of evidence here is about
    what conductor wrote and what agy loaded at startup. The script is
    re-read from a writable worktree on every tool call, so a lane that
    rewrote it mid-run kept passing all of them; the digest taken at write
    time is re-checked after the run."""
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
            tamper="# denied by nobody",
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert error_kind(result) == "taint"
    assert "taint hooks modified during the run" in result.error
    te = result.taint_enforcement
    assert te["hooks_modified"] == [".agents/conductor-taint.py"]
    assert sorted(te["hook_digests"]) == [".agents/conductor-taint.py", ".agents/hooks.json"]


def test_untouched_hook_files_record_their_digests_and_pass(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home, log_line=_passing_log_line(), tools=[*TAINT_AGY_DENIED_TOOLS], extra_lines=[]
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    te = result.taint_enforcement
    assert te["hooks_modified"] == []
    assert all(len(digest) == 64 for digest in te["hook_digests"].values())


def test_a_deleted_hook_file_fails_the_run(repo, home, fake_fleet):
    home_q = shlex.quote(str(home))
    run_dir = f"{home_q}/runs/$CONDUCTOR_RUN_ID"
    init_event = json.dumps(
        {"event": "init", "conversation_id": "c1", "init": {"tools": [*TAINT_AGY_DENIED_TOOLS]}}
    )
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
    fake_fleet(
        [
            "sh",
            "-c",
            " && ".join(
                [
                    f"mkdir -p {run_dir}",
                    f"printf '%s\\n' {shlex.quote(_passing_log_line())} > {run_dir}/agy.log",
                    f'rm "$CONDUCTOR_WORKTREE"/{shlex.quote(TAINT_AGY_HOOKS_REL)}',
                    f"printf '%s\\n' {shlex.quote(init_event)}",
                    f"printf '%s\\n' {shlex.quote(result_event)}",
                ]
            ),
        ]
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert "taint hooks modified during the run" in result.error
    assert result.taint_enforcement["hooks_modified"] == [TAINT_AGY_HOOKS_REL]


# --- D5: the per-lane shell opt-in on antigravity ---------------------------


def test_taint_shell_allow_writes_the_prefix_script_and_receipts_prefix(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home, log_line=_passing_log_line(), tools=[*TAINT_AGY_DENIED_TOOLS], extra_lines=[]
        )
    )
    result = dispatch(spec(cwd=str(repo), taint_shell="allow"), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert result.taint["taint_shell"] == "prefix"
    assert "run_command" not in result.taint["tools_denied"]


def test_default_taint_receipt_says_the_shell_is_denied(repo, home, fake_fleet):
    fake_fleet(
        _agy_argv(
            home, log_line=_passing_log_line(), tools=[*TAINT_AGY_DENIED_TOOLS], extra_lines=[]
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert result.taint["taint_shell"] == "denied"
    assert "run_command" in result.taint["tools_denied"]


# --- F13: the free /hooks preflight, before any paid turn -------------------


def test_preflight_finding_the_hooks_file_enabled_lets_the_dispatch_proceed(
    repo, home, fake_fleet
):
    """The default (autouse) fake answers truthfully from the real
    `.agents/hooks.json` `_write_taint_agy_hooks` wrote -- this is the
    ordinary passing path, no override needed."""
    fake_fleet(
        _agy_argv(
            home, log_line=_passing_log_line(), tools=[*TAINT_AGY_DENIED_TOOLS], extra_lines=[]
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert result.taint_enforcement["preflight"]["ok"] is True
    assert result.taint_enforcement["preflight"]["loaded"]
    assert (Path(result.run_dir) / "hooks-preflight.json").exists()


def test_preflight_finding_no_hooks_file_fails_before_the_paid_turn(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_agy_hooks_argv", _hooks_preflight_argv([]))
    marker = tmp_path / "paid-turn-called"
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
            marker=marker,
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert result.spawned is False
    assert error_kind(result) == "taint"
    assert "taint hooks not enforced" in result.error
    assert result.taint_enforcement["preflight"]["ok"] is False
    assert result.taint_enforcement["preflight"]["loaded"] == []
    assert not marker.exists(), "the paid turn's own argv must never have run"


def test_preflight_missing_a_matcher_fails_before_the_paid_turn(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    """Our file is listed and enabled, but agy's answer names one matcher
    fewer than conductor wrote: the per-tool evidence is the answer's own
    `actions`, so this fails closed with the missing name."""
    def _short_by_one(cwd: str) -> list[str]:
        hooks_path = os.path.join(cwd, TAINT_AGY_HOOKS_REL)
        with open(hooks_path) as fh:
            written = json.load(fh)["hooks"]["PreToolUse"]
        actions = [
            {"event": "PreToolUse", "matcher": entry["matcher"], "type": "command", "command": "x"}
            for entry in written
            if entry["matcher"] != "search_web"
        ]
        return _hooks_preflight_argv(
            [{"name": "hooks", "enabled": True, "source": hooks_path, "actions": actions}]
        )(cwd)

    monkeypatch.setattr(runner_mod, "build_agy_hooks_argv", _short_by_one)
    marker = tmp_path / "paid-turn-called"
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
            marker=marker,
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert result.spawned is False
    assert error_kind(result) == "taint"
    assert "without matcher(s) for search_web" in result.error
    assert result.taint_enforcement["preflight"]["matchers_missing"] == ["search_web"]
    assert not marker.exists()


def test_preflight_query_timeout_fails_closed(repo, home, fake_fleet, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "_TAINT_AGY_PREFLIGHT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runner_mod, "build_agy_hooks_argv", lambda cwd: ["sleep", "5"])
    marker = tmp_path / "paid-turn-called"
    fake_fleet(
        _agy_argv(
            home,
            log_line=_passing_log_line(),
            tools=[*TAINT_AGY_DENIED_TOOLS],
            extra_lines=[],
            marker=marker,
        )
    )
    result = dispatch(spec(cwd=str(repo)), home=home, isolate=True)
    assert result.ok is False
    assert result.spawned is False
    assert error_kind(result) == "taint"
    assert "timed out" in result.taint_enforcement["preflight"]["detail"]
    assert not marker.exists()


def test_untainted_antigravity_lane_runs_no_preflight(repo, home, fake_fleet, monkeypatch):
    called: list[str] = []

    def _track(cwd: str) -> list[str]:
        called.append(cwd)
        return ["sh", "-c", "true"]

    monkeypatch.setattr(runner_mod, "build_agy_hooks_argv", _track)
    fake_fleet(
        _agy_argv(home, log_line=None, tools=["list_dir"], extra_lines=[])
    )
    result = dispatch(spec(cwd=str(repo), taint=False), home=home, isolate=True)
    assert result.ok is True, result.failure()
    assert called == []
    assert result.taint_enforcement is None


def test_the_real_hooks_preflight_argv_is_the_probed_shape(tmp_path):
    """Cross-vendor review (Grok): every test above swaps the argv builder
    for a script, so nothing exercised the production argv. This pins it to
    the shape the 2026-09-07 probe ran: the free `/hooks` query, stream-json,
    the lane's cwd as the only added directory, no model, effort, or schema."""
    from conductor.fleets import build_agy_hooks_argv

    argv = build_agy_hooks_argv(str(tmp_path))
    assert argv == [
        "agy",
        "-p",
        "/hooks",
        "--output-format",
        "stream-json",
        "--add-dir",
        str(tmp_path),
    ]
    assert "--model" not in argv and "--json-schema" not in argv
