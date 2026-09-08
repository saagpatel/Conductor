"""F12: Claude read lanes under `--restricted` and `--permission-prompts
none`.

`--permission-prompts none` goes on every claude dispatch, read and write:
anything that would need a permission prompt is denied automatically, and
the denial lands in `result.permission_denials` while the run still exits 0
(docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md). A write
lane with a non-empty list fails, kind `denied`; a read lane's list is a
note, not a failure. `--restricted` (file tools confined to cwd, no Bash, no
WebFetch) turns on automatically for a claude read lane that declares a
`deliverable` -- the fix for a read lane's plan-mode deliverable never being
writable -- and explicitly for any claude read lane via `restricted: true`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from conductor.fleets import TAINT_DISALLOWED_TOOLS, DispatchRefused, Spec, build_argv
from conductor.mission import MissionInvalid, mission_from_dict
from conductor.runner import dispatch

DELIVERABLE = {"path": "child.json"}


def spec(**kw) -> Spec:
    base = dict(fleet="claude", prompt="do the thing", cwd="/tmp")
    base.update(kw)
    return Spec(**base)


# --- fleets.py: argv shape (items 1, 2, 3) -----------------------------------


@pytest.mark.parametrize("mode", ["read", "write"])
def test_permission_prompts_none_on_every_claude_dispatch(mode):
    argv = build_argv(spec(mode=mode))
    assert argv[argv.index("--permission-prompts") + 1] == "none"


def test_plain_read_lane_keeps_plan_mode():
    argv = build_argv(spec(mode="read"))
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--restricted" not in argv


def test_deliverable_read_lane_is_restricted_acceptedits_not_plan():
    argv = build_argv(spec(mode="read", deliverable=DELIVERABLE))
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--restricted" in argv
    assert "plan" not in argv


def test_a_plan_lanes_argv_is_the_deliverable_shape():
    # README's planner example: {"plan": true, "deliverable": {"path": ...}}.
    # `plan` itself is a mission-level Lane concept with no Spec field of its
    # own; every plan lane is required to declare a deliverable, so its argv
    # is exactly this shape.
    argv = build_argv(spec(mode="read", deliverable={"path": "child.json"}))
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--restricted" in argv


def test_write_lane_deliverable_does_not_force_restricted():
    argv = build_argv(spec(mode="write", deliverable=DELIVERABLE))
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--restricted" not in argv


def test_restricted_true_forces_the_same_shape_without_a_deliverable():
    argv = build_argv(spec(mode="read", restricted=True))
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--restricted" in argv


def test_restricted_is_refused_on_a_write_lane():
    with pytest.raises(DispatchRefused, match="restricted is refused on a write lane"):
        spec(mode="write", restricted=True).validate()


@pytest.mark.parametrize("fleet", ["antigravity", "cursor", "codex", "script"])
def test_restricted_is_refused_off_the_claude_fleet(fleet):
    kwargs = dict(fleet=fleet, prompt="", cwd="/tmp", mode="read", restricted=True)
    if fleet == "script":
        kwargs["command"] = "true"
    else:
        kwargs["prompt"] = "x"
    with pytest.raises(
        DispatchRefused, match=f"restricted is enforceable on the claude fleet only: {fleet}"
    ):
        Spec(**kwargs).validate()


# --- outputs.py -> runner.py: permission_denials (items 1, 4) ---------------


def _result_line(*, permission_denials: list[dict] | None = None) -> str:
    payload: dict = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "usage": {"inputTokens": 10, "outputTokens": 1},
    }
    if permission_denials is not None:
        payload["permission_denials"] = permission_denials
    return json.dumps(payload)


def _init_line(tools: list[str]) -> str:
    return json.dumps({"type": "system", "subtype": "init", "tools": tools})


def _sh(*lines: str) -> list[str]:
    printf = "printf '%s\\n' " + " ".join(shlex.quote(line) for line in lines)
    return ["sh", "-c", printf]


DENIAL = {
    "tool_name": "Write",
    "tool_use_id": "toolu_1",
    "tool_input": {"file_path": "../outside/f.txt"},
}


def test_write_lane_permission_denial_fails_as_denied_naming_the_tool(repo, home, fake_fleet):
    fake_fleet(_sh(_result_line(permission_denials=[DENIAL])))
    result = dispatch(spec(mode="write", cwd=str(repo)), home=home)
    assert result.ok is False
    assert result.permission_denials == [DENIAL]
    assert result.error == "permission denied: Write"
    assert result.summary()["kind"] == "denied"


def test_read_lane_permission_denial_is_a_note_not_a_failure(repo, home, fake_fleet):
    fake_fleet(_sh(_result_line(permission_denials=[DENIAL])))
    result = dispatch(spec(mode="read", cwd=str(repo)), home=home)
    assert result.ok is True
    assert result.permission_denials == [DENIAL]
    assert any(
        "permission denied" in note and "Write" in note for note in result.git_verdict["notes"]
    )


def test_no_denials_is_an_empty_list_on_the_receipt(repo, home, fake_fleet):
    fake_fleet(_sh(_result_line()))
    result = dispatch(spec(mode="read", cwd=str(repo)), home=home)
    assert result.permission_denials == []


# --- runner.py: the restricted init-event assertion (item 4) ---------------


def _restricted_argv(*lines: str) -> list[str]:
    # dispatch() derives `permission_mode`/`restricted` straight from the
    # real argv, not from re-deriving fleets._build_claude's own branching;
    # the fake fleet must carry the same tokens a real restricted dispatch
    # would for that detection to fire. sh ignores the extra positional
    # arguments (they become $1, $2, ... and the printf script never
    # references them), so they are free to append.
    return [*_sh(*lines), "--permission-mode", "acceptEdits", "--restricted"]


def test_restricted_lane_with_bash_in_init_event_fails_closed_kind_taint(repo, home, fake_fleet):
    fake_fleet(_restricted_argv(_init_line(["Read", "Bash", "Grep"]), _result_line()))
    result = dispatch(spec(mode="read", cwd=str(repo), restricted=True), home=home)
    assert result.ok is False
    assert result.error == "restricted mode not enforced: Bash present in the init tool list"
    assert result.summary()["kind"] == "taint"


def test_restricted_lane_with_webfetch_in_init_event_fails_closed(repo, home, fake_fleet):
    fake_fleet(_restricted_argv(_init_line(["Read", "WebFetch"]), _result_line()))
    result = dispatch(spec(mode="read", cwd=str(repo), restricted=True), home=home)
    assert result.ok is False
    assert "WebFetch" in result.error
    assert result.summary()["kind"] == "taint"


def test_restricted_lane_with_a_clean_init_event_is_ok(repo, home, fake_fleet):
    fake_fleet(_restricted_argv(_init_line(["Read", "Grep", "Edit"]), _result_line()))
    result = dispatch(spec(mode="read", cwd=str(repo), restricted=True), home=home)
    assert result.ok is True
    assert result.restricted is True
    assert result.permission_mode == "acceptEdits"


def test_restricted_lane_with_no_init_event_does_not_fail_closed(repo, home, fake_fleet):
    # A stream cut short for an unrelated reason is not, on its own,
    # evidence the flag failed -- only positive evidence (Bash/WebFetch
    # actually present) fails the run.
    fake_fleet(_restricted_argv(_result_line()))
    result = dispatch(spec(mode="read", cwd=str(repo), restricted=True), home=home)
    assert result.ok is True


def test_plain_read_lane_receipt_carries_permission_mode_and_restricted(repo, home, fake_fleet):
    # Real argv this time (not the bare `_sh` fake): dispatch() derives
    # `permission_mode`/`restricted` from the actual argv fleets.build_argv
    # produced, and a plain read lane's is `plan`, `--restricted` absent.
    fake_fleet(session_id="s1")
    result = dispatch(spec(mode="read", cwd=str(repo)), home=home)
    assert result.permission_mode == "plan"
    assert result.restricted is False


def test_tainted_restricted_claude_lane_records_both_mechanisms(repo, home, fake_fleet):
    fake_fleet(_restricted_argv(_init_line(["Read", "Grep", "Edit"]), _result_line()))
    result = dispatch(spec(mode="read", cwd=str(repo), restricted=True, taint=True), home=home)
    assert result.ok is True
    assert result.taint_enforcement == {
        "disallowed_tools": list(TAINT_DISALLOWED_TOOLS),
        "restricted": True,
    }


# --- mission.py: restricted cascades as a lane key --------------------------


def test_restricted_is_a_recognized_lane_key(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "reviewer",
                "fleet": "claude",
                "mode": "read",
                "prompt": "R",
                "restricted": True,
            }
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[0].restricted is True


def test_restricted_write_lane_is_refused_at_load(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "a",
                "fleet": "claude",
                "mode": "write",
                "prompt": "A",
                "restricted": True,
            }
        ],
    }
    with pytest.raises(MissionInvalid, match="restricted is refused on a write lane"):
        mission_from_dict(raw, base_dir=tmp_path)


# --- README ------------------------------------------------------------


def test_readme_documents_restricted_read_lanes():
    readme = Path(__file__).parents[1] / "README.md"
    raw_section = readme.read_text().split(
        "### Restricted read lanes: `--restricted` and `--permission-prompts none` (F12)", 1
    )[1].split("\n## ", 1)[0]
    section = " ".join(raw_section.split())
    assert "permission_denials" in section
    assert "kind `denied`" in section
    assert "acceptEdits" in section and "bypassPermissions" in section
    assert "restricted: true" in section
    assert "restricted mode not enforced" in section
    assert "docs/research/2026-09-07-live-probe-restricted-denied-sandbox.md" in section


def test_settings_digest_watches_the_repository_root_not_a_subdirectory_cwd(
    repo, home, fake_fleet
):
    """Claude Code reads project settings from the repo toplevel. A lane
    whose cwd is a subdirectory used to hash `<cwd>/.claude/` while the
    files that actually govern the run sit at `<worktree>/.claude/`."""
    sub = repo / "pkg" / "inner"
    sub.mkdir(parents=True)
    (sub / "keep.txt").write_text("keep\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "sub"], cwd=repo, check=True)
    fake_fleet(
        [
            "sh",
            "-c",
            'mkdir -p "$CONDUCTOR_WORKTREE/.claude" && '
            'printf \'{"permissions":{"allow":["*"]}}\\n\' > '
            '"$CONDUCTOR_WORKTREE/.claude/settings.json" && '
            "printf '%s\\n' "
            '\'{"type":"result","subtype":"success","is_error":false,'
            '"result":"ok","usage":{"inputTokens":10,"outputTokens":1}}\'',
        ]
    )
    result = dispatch(
        spec(mode="write", cwd=str(sub)),
        isolate=True,
        home=home,
    )
    assert result.settings["checked"] is True
    assert ".claude/settings.json" in result.settings["modified"]
    assert result.ok is False
    assert "settings modified" in (result.error or "")
