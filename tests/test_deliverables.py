"""E1: deliverable verdicts.

A lane's product can be a file, not just its reply. `Spec.deliverable`
(fleets.py) declares it, `runner.dispatch` checks it on the filesystem after
the fleet exits, a read lane may move exactly that one file, and a
downstream lane can read it through `{{lanes.<name>.deliverable}}`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from conductor import golden as golden_mod
from conductor import runner as runner_mod
from conductor.cli import build_parser, cmd_dispatch
from conductor.errors import error_kind
from conductor.fleets import DispatchRefused, Spec, build_argv
from conductor.mission import LaneResult, Mission, _render, mission_from_dict, run_mission
from conductor.runner import dispatch


def spec(**kw) -> Spec:
    base = dict(fleet="claude", prompt="do the thing", cwd="/tmp")
    base.update(kw)
    return Spec(**base)


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test deliverable", cwd=str(repo))
    base.update(kw)
    return Spec(**base)


# --- Spec.validate() refusals (item 1) --------------------------------------


def test_deliverable_must_be_an_object():
    with pytest.raises(DispatchRefused, match="deliverable must be an object"):
        build_argv(spec(deliverable="report.txt"))


def test_deliverable_unknown_field_is_refused():
    with pytest.raises(DispatchRefused, match="unknown field"):
        build_argv(spec(deliverable={"path": "a.txt", "bogus": 1}))


def test_deliverable_missing_path_is_refused():
    with pytest.raises(DispatchRefused, match="non-empty 'path'"):
        build_argv(spec(deliverable={}))


def test_deliverable_empty_path_is_refused():
    with pytest.raises(DispatchRefused, match="non-empty 'path'"):
        build_argv(spec(deliverable={"path": ""}))


def test_deliverable_absolute_path_is_refused():
    with pytest.raises(DispatchRefused, match="not absolute"):
        build_argv(spec(deliverable={"path": "/etc/passwd"}))


def test_deliverable_dotdot_path_is_refused():
    with pytest.raises(DispatchRefused, match="must not contain '..'"):
        build_argv(spec(deliverable={"path": "../escape.txt"}))


def test_deliverable_path_resolving_outside_cwd_is_refused(tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (cwd / "link").symlink_to(outside)
    with pytest.raises(DispatchRefused, match="resolves outside cwd"):
        build_argv(spec(cwd=str(cwd), deliverable={"path": "link/file.txt"}))


def test_deliverable_schema_unreadable_is_refused(tmp_path):
    with pytest.raises(DispatchRefused, match="schema file unreadable"):
        build_argv(
            spec(deliverable={"path": "a.txt", "schema": str(tmp_path / "missing.json")})
        )


def test_deliverable_schema_not_json_is_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(DispatchRefused, match="schema file is not valid JSON"):
        build_argv(spec(deliverable={"path": "a.txt", "schema": str(bad)}))


def test_deliverable_commit_must_be_a_boolean():
    with pytest.raises(DispatchRefused, match="commit must be a boolean"):
        build_argv(spec(deliverable={"path": "a.txt", "commit": "no"}))


def test_deliverable_commit_false_is_accepted():
    build_argv(spec(deliverable={"path": "a.txt", "commit": False}))


# --- F22: validator, Spec.validate() (item 1) --------------------------------


def test_deliverable_validator_empty_string_is_refused():
    with pytest.raises(DispatchRefused, match="validator must be a non-empty string"):
        build_argv(spec(deliverable={"path": "a.txt", "validator": ""}))


def test_deliverable_validator_non_string_is_refused():
    with pytest.raises(DispatchRefused, match="validator must be a non-empty string"):
        build_argv(spec(deliverable={"path": "a.txt", "validator": 3}))


def test_deliverable_validator_accepted_alongside_schema_and_commit_false(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}))
    build_argv(
        spec(
            deliverable={
                "path": "a.json",
                "schema": str(schema),
                "commit": False,
                "validator": "true {path}",
            }
        )
    )


def test_cli_dispatch_deliverable_validator_flag_reaches_the_spec(repo, home, monkeypatch):
    import conductor.cli as cli_mod
    from conductor import runner as real_runner

    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--mode",
            "read",
            "--deliverable",
            "out.txt",
            "--deliverable-validator",
            "true {path}",
            "--dry-run",
        ]
    )
    captured: dict = {}
    real_dispatch = real_runner.dispatch

    def capturing(spec_arg, **kwargs):
        captured["spec"] = spec_arg
        kwargs["dry_run"] = True
        kwargs["home"] = home
        return real_dispatch(spec_arg, **kwargs)

    monkeypatch.setattr(cli_mod, "dispatch", capturing)
    rc = cmd_dispatch(args)
    assert rc == 0
    assert captured["spec"].deliverable == {"path": "out.txt", "validator": "true {path}"}


def test_cli_dispatch_deliverable_validator_without_deliverable_is_refused(repo, capsys):
    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--deliverable-validator",
            "true {path}",
        ]
    )
    rc = cmd_dispatch(args)
    assert rc == 3
    out = json.loads(capsys.readouterr().err)
    assert "--deliverable-validator needs --deliverable" in out["refused"]


# --- F22: validator, before/after evidence (item 2) ---------------------------

_TODO_GATE = "! grep -q TODO {path}"


def _commit_doc(repo: Path, text: str) -> None:
    (repo / "doc.txt").write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "doc"], cwd=repo, check=True, capture_output=True)


def test_validator_reproduced_when_the_fleet_removes_the_todo(repo, home, fake_fleet):
    _commit_doc(repo, "before\nTODO: fix this\n")
    fake_fleet(["sh", "-c", "printf 'before\\nfixed\\n' > doc.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.ok is True, result.failure()
    validator = result.deliverable["validator"]
    assert validator["command"] == "! grep -q TODO doc.txt"
    assert validator["before"]["exit_code"] != 0
    assert validator["before"]["timed_out"] is False
    assert validator["after"]["exit_code"] == 0
    assert validator["verdict"] == "reproduced"
    assert result.deliverable["ok"] is True


def test_validator_rejected_when_the_todo_remains(repo, home, fake_fleet):
    _commit_doc(repo, "before\nTODO: fix this\n")
    fake_fleet(["sh", "-c", "printf 'before\\nTODO: still not fixed\\n' > doc.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.ok is False
    assert result.deliverable["validator"]["verdict"] == "rejected"
    assert result.deliverable["ok"] is False
    assert result.failure().startswith("deliverable rejected by validator: doc.txt:")
    assert error_kind(result) == "deliverable"


def test_validator_before_is_none_when_the_deliverable_is_new(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "printf 'no problem words here\\n' > doc.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.ok is True, result.failure()
    validator = result.deliverable["validator"]
    assert validator["before"] is None
    assert validator["after"]["exit_code"] == 0
    assert validator["verdict"] == "accepted"


def test_validator_that_hangs_past_the_gate_timeout_is_rejected(
    repo, home, fake_fleet, monkeypatch
):
    _commit_doc(repo, "before\n")
    fake_fleet(["sh", "-c", "printf 'still fine\\n' > doc.txt"])
    monkeypatch.setattr(
        runner_mod,
        "_run_validator_command",
        lambda cwd, cmd, timeout, env, **kw: {
            "exit_code": None,
            "timed_out": True,
            "tail": f"timed out after {timeout}s; process group killed",
        },
    )
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.deliverable["validator"]["after"]["timed_out"] is True
    assert result.deliverable["validator"]["verdict"] == "rejected"


def test_run_validator_command_real_timeout_kills_the_process_group(tmp_path):
    """Peer review finding (Opus, item 3): the mocked hang test above proves
    the receipt shape but not the deadline loop or process-group kill in
    `_run_validator_command` itself, which is `verify.run_tests`'s own
    subprocess/poll/kill shape and deserves the same live coverage."""
    marker = tmp_path / "grandchild.pid"
    script = f"sh -c 'echo $$ > {marker}; sleep 60' & sleep 60"
    started = time.monotonic()
    result = runner_mod._run_validator_command(str(tmp_path), script, 1, None)
    elapsed = time.monotonic() - started

    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert elapsed < 30
    time.sleep(0.5)
    pid = int(marker.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_validator_does_not_run_when_the_dispatch_did_not_complete_cleanly(
    repo, home, fake_fleet, monkeypatch
):
    """Peer review finding (Opus, item 1): `_check_deliverable_validator` used
    to run unconditionally, spending up to two more gate-length subprocess
    runs on a dispatch that already timed out, was interrupted, or exited
    non-zero -- exactly the state `_reproduce_receipt` itself already
    refuses to build a verdict on."""
    _commit_doc(repo, "before\n")
    fake_fleet(["sh", "-c", "printf 'still fine\\n' > doc.txt; exit 3"])
    calls: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_run_validator_command",
        lambda cwd, cmd, timeout, env, **kw: calls.append(cmd)
        or {"exit_code": 0, "timed_out": False, "tail": ""},
    )
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.exit_code == 3
    assert calls == []
    assert "validator" not in (result.deliverable or {})


def test_validator_before_run_that_could_not_produce_a_verdict_is_not_reproduced(
    repo, home, fake_fleet, monkeypatch
):
    """Peer review finding (Opus, item 2): a before run that timed out or
    never started is not evidence the base was broken -- `_reproduce_gate`
    already draws exactly this line (`infra_error`/`timed_out` -> `no-check`,
    never `reproduced`), and the validator's own verdict must draw it too."""
    _commit_doc(repo, "before\nTODO: fix this\n")
    fake_fleet(["sh", "-c", "printf 'before\\nfixed\\n' > doc.txt"])

    def fake_run(cwd, cmd, timeout, env, **kw):
        if "validator-before" in cmd:
            return {
                "exit_code": None,
                "timed_out": True,
                "tail": f"timed out after {timeout}s; process group killed",
            }
        return {"exit_code": 0, "timed_out": False, "tail": "(no output)"}

    monkeypatch.setattr(runner_mod, "_run_validator_command", fake_run)
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    validator = result.deliverable["validator"]
    assert validator["before"]["timed_out"] is True
    assert validator["verdict"] == "accepted"


def test_validator_before_run_resolves_the_deliverable_relative_to_the_repo_not_cwd(
    repo, home, fake_fleet
):
    """Peer review finding (Opus, item 4): `git show <rev>:<path>` resolves a
    bare path relative to the repository toplevel, not the current working
    directory -- the same reason `_read_deliverable_only` runs a declared
    deliverable path through `_repo_relative` before comparing it against
    anything git reported. A validator on a lane whose `cwd` is a
    subdirectory used to read nothing at all from the base commit."""
    subdir = repo / "src"
    subdir.mkdir()
    (subdir / "doc.txt").write_text("before\nTODO: fix this\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add doc"], cwd=repo, check=True, capture_output=True)
    fake_fleet(["sh", "-c", "printf 'before\\nfixed\\n' > doc.txt"])
    result = dispatch(
        spec_for(subdir, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.ok is True, result.failure()
    validator = result.deliverable["validator"]
    assert validator["before"] is not None
    assert validator["before"]["exit_code"] != 0
    assert validator["verdict"] == "reproduced"


def test_validator_before_file_preserves_the_base_bytes_exactly(repo, home, fake_fleet):
    """Peer review finding (Opus, item 5): `git_run` decodes with `text=True`,
    which applies universal-newline translation -- a base file with CRLF
    line endings must still reach the validator as the bytes git actually
    holds, not a normalized copy, or the before and after runs are not
    judging comparable inputs."""
    (repo / "doc.txt").write_bytes(b"before\r\nTODO: fix this\r\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "crlf doc"], cwd=repo, check=True, capture_output=True)
    raw = subprocess.run(
        ["git", "show", "HEAD:doc.txt"], cwd=repo, capture_output=True, check=True
    ).stdout
    fake_fleet(["sh", "-c", "printf 'before\\r\\nfixed\\r\\n' > doc.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        home=home,
    )
    assert result.ok is True, result.failure()
    before_files = list(Path(result.run_dir).glob("validator-before*"))
    assert len(before_files) == 1
    assert before_files[0].read_bytes() == raw


def test_dry_run_records_the_validator_block_as_declared_and_unchecked(repo, home):
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "doc.txt", "validator": _TODO_GATE}),
        dry_run=True,
        home=home,
    )
    assert result.deliverable["validator"] == {
        "command": "! grep -q TODO doc.txt",
        "before": None,
        "after": None,
        "verdict": None,
    }


def test_a_deliverable_without_a_validator_carries_no_validator_key(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", f"echo out > report.txt; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is True, result.failure()
    assert "validator" not in result.deliverable


# --- conductor dispatch CLI flags (item 1) ----------------------------------


def test_cli_dispatch_deliverable_flags_reach_the_spec(repo, home, tmp_path, monkeypatch):
    import conductor.cli as cli_mod
    from conductor import runner as real_runner

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}))
    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--mode",
            "read",
            "--deliverable",
            "out.json",
            "--deliverable-schema",
            str(schema),
            "--dry-run",
        ]
    )
    captured: dict = {}
    real_dispatch = real_runner.dispatch

    def capturing(spec_arg, **kwargs):
        captured["spec"] = spec_arg
        kwargs["dry_run"] = True
        kwargs["home"] = home
        return real_dispatch(spec_arg, **kwargs)

    monkeypatch.setattr(cli_mod, "dispatch", capturing)
    rc = cmd_dispatch(args)
    assert rc == 0
    assert captured["spec"].deliverable == {"path": "out.json", "schema": str(schema)}


def test_cli_dispatch_deliverable_schema_without_deliverable_is_refused(repo, tmp_path, capsys):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}))
    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--deliverable-schema",
            str(schema),
        ]
    )
    rc = cmd_dispatch(args)
    assert rc == 3
    out = json.loads(capsys.readouterr().err)
    assert "--deliverable-schema needs --deliverable" in out["refused"]


# --- the mission key (item 2) ------------------------------------------------


def test_deliverable_inherits_from_mission_and_lane_overrides(repo, tmp_path):
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "deliverable": {"path": "default.json"},
        "lanes": [
            {"name": "a", "fleet": "claude"},
            {"name": "b", "fleet": "claude", "deliverable": {"path": "b.json"}},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[0].deliverable == {"path": "default.json"}
    assert mission.lanes[1].attempts[0].deliverable == {"path": "b.json"}


def test_deliverable_schema_resolves_relative_to_the_mission_file(repo, tmp_path):
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (specs_dir / "schema.json").write_text(json.dumps({"type": "object"}))
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [
            {"fleet": "claude", "deliverable": {"path": "out.json", "schema": "schema.json"}}
        ],
    }
    mission = mission_from_dict(raw, base_dir=specs_dir)
    resolved = mission.lanes[0].attempts[0].deliverable["schema"]
    assert resolved == str((specs_dir / "schema.json").resolve())


def test_deliverable_snapshot_round_trips(repo, home, tmp_path):
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "claude", "deliverable": {"path": "out.json"}}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    assert reloaded.to_dict() == mission.to_dict() == snapshot


def test_backfill_snapshot_fills_the_missing_deliverable_default(repo, tmp_path):
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "claude"}]}
    mission = mission_from_dict(raw, base_dir=tmp_path)
    snapshot = mission.to_dict()
    for attempt in snapshot["lanes"][0]["attempts"]:
        del attempt["deliverable"]
    backfilled = golden_mod._backfill_snapshot(snapshot)
    reloaded = Mission.from_snapshot(backfilled)
    assert reloaded.lanes[0].attempts[0].deliverable is None


# --- through the fake fleet (item 3 and 4) -----------------------------------

_OK_ANSWER = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "looked around",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
)
_EMPTY_ANSWER = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
)


def test_read_lane_writes_its_deliverable_and_passes(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", f"echo out > report.txt; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is True, result.failure()
    assert result.deliverable == {
        "path": "report.txt",
        "exists": True,
        "bytes": 4,
        "parsed": None,
        "ok": True,
        "reason": None,
        # W4: the sha256 of the bytes actually captured into run_dir, added
        # by the capture that now runs after the gate.
        "sha256": hashlib.sha256(b"out\n").hexdigest(),
    }
    assert result.git_verdict["deliverable_only"] is True
    assert error_kind(result) is None


def test_read_lane_deliverable_plus_another_file_fails_moved_bytes(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", f"echo out > report.txt; echo extra > other.txt; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is False
    assert result.failure() == "read dispatch moved bytes"
    assert result.deliverable["ok"] is True
    assert not result.git_verdict.get("deliverable_only")


def test_read_lane_that_writes_nothing_fails_deliverable_missing_not_no_answer(
    repo, home, fake_fleet
):
    fake_fleet(["sh", "-c", f"echo '{_EMPTY_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.failure() == "deliverable missing: report.txt"
    assert error_kind(result) == "deliverable"


def test_deliverable_empty_file_fails(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "touch report.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "report.txt"}), home=home
    )
    assert result.failure() == "deliverable empty: report.txt"


def test_deliverable_non_json_under_schema_fails_to_parse(repo, home, fake_fleet, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}))
    fake_fleet(["sh", "-c", "echo 'not json' > record.json"])
    result = dispatch(
        spec_for(
            repo, mode="write", deliverable={"path": "record.json", "schema": str(schema)}
        ),
        home=home,
    )
    assert result.failure() == "deliverable does not parse: record.json"


def test_deliverable_schema_that_vanishes_during_the_run_fails(
    repo, home, fake_fleet, tmp_path
):
    """Would catch the deletion of `_check_deliverable`'s post-run "schema
    unreadable" branch: `Spec.validate` reads the schema before spawn, but
    `_check_deliverable` reads it again after the run to judge the
    deliverable, and a schema file that moved or was deleted in between must
    fail the lane rather than raise or silently skip the schema check."""
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}))
    fake_fleet(
        ["sh", "-c", f"printf '%s' '{{}}' > record.json && rm {shlex.quote(str(schema))}"]
    )
    result = dispatch(
        spec_for(
            repo, mode="write", deliverable={"path": "record.json", "schema": str(schema)}
        ),
        home=home,
    )
    assert result.failure() is not None
    assert "deliverable schema unreadable" in result.failure()
    assert result.deliverable["parsed"] is True
    assert result.deliverable["ok"] is False


def test_deliverable_json_missing_required_key_fails_schema(repo, home, fake_fleet, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "required": ["name"]}))
    fake_fleet(["sh", "-c", "printf '%s' '{}' > record.json"])
    result = dispatch(
        spec_for(
            repo, mode="write", deliverable={"path": "record.json", "schema": str(schema)}
        ),
        home=home,
    )
    assert (
        result.failure() == "deliverable does not match schema: missing required property 'name'"
    )


def test_deliverable_json_wrong_typed_property_fails_schema(repo, home, fake_fleet, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"count": {"type": "integer"}}}))
    fake_fleet(["sh", "-c", "printf '%s' '{\"count\": \"nope\"}' > record.json"])
    result = dispatch(
        spec_for(
            repo, mode="write", deliverable={"path": "record.json", "schema": str(schema)}
        ),
        home=home,
    )
    assert (
        result.failure()
        == "deliverable does not match schema: property 'count' must be of type integer"
    )


def test_deliverable_array_schema_checks_each_item(repo, home, fake_fleet, tmp_path):
    """Found live 2026-09-07: a `type: array` schema refused every list
    deliverable as "top level is not a JSON object"."""
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "array",
                "items": {"required": ["name"], "properties": {"name": {"type": "string"}}},
            }
        )
    )
    fake_fleet(["sh", "-c", "printf '%s' '[{\"name\": \"a\"}, {\"name\": 2}]' > list.json"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "list.json", "schema": str(schema)}),
        home=home,
    )
    assert (
        result.failure()
        == "deliverable does not match schema: item 1: property 'name' must be of type string"
    )

    fake_fleet(["sh", "-c", "printf '%s' '[{\"name\": \"a\"}]' > list.json"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "list.json", "schema": str(schema)}),
        home=home,
    )
    assert result.deliverable["ok"] is True, result.failure()

    fake_fleet(["sh", "-c", "printf '%s' '{\"name\": \"a\"}' > list.json"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "list.json", "schema": str(schema)}),
        home=home,
    )
    assert result.failure() == "deliverable does not match schema: top level is not a JSON array"


def test_failed_deliverable_check_keeps_the_commit_off_the_branch(
    repo, home, fake_fleet, git_out, tmp_path
):
    """Found live 2026-09-07: a schema mismatch sank `ok` and the receipt
    still carried a committed sha. E1's check counts as a gate; the work
    stays in the tree, uncommitted, like a failed test gate."""
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "required": ["count"]}))
    base = git_out(repo, "rev-parse", "HEAD")
    fake_fleet(["sh", "-c", "printf '%s' '{}' > record.json"])
    result = dispatch(
        spec_for(
            repo, mode="write", deliverable={"path": "record.json", "schema": str(schema)}
        ),
        commit_message="feat: record",
        home=home,
    )
    assert result.ok is False
    assert (
        result.failure()
        == "deliverable does not match schema: missing required property 'count'"
    )
    assert not result.commit["committed"]
    assert git_out(repo, "rev-parse", "HEAD") == base
    assert (repo / "record.json").read_text() == "{}"


def test_write_lane_deliverable_commit_false_is_kept_out_of_the_commit(repo, home, fake_fleet):
    """F15 mission 2 item 3: a dispatched write lane with `commit: false`
    has the deliverable in its receipt and not in its commit."""
    fake_fleet(
        ["sh", "-c", f"echo done > report.txt; echo receipt > record.json; echo '{_OK_ANSWER}'"]
    )
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "record.json", "commit": False}),
        commit_message="feat: add report",
        home=home,
    )
    assert result.ok is True, result.failure()
    assert result.deliverable["ok"] is True
    log = subprocess.run(
        ["git", "show", "--stat", result.commit["sha"]],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "report.txt" in log
    assert "record.json" not in log


def test_no_op_ok_waives_a_commit_that_only_excluded_the_deliverable(repo, home, fake_fleet):
    """Cross-vendor review (Grok) of F15 mission 2: `commit_work`'s more
    specific "nothing to commit beyond the excluded deliverable" reason
    must be waived by `no_op_ok` the same way the plain "nothing to
    commit" is -- `Result.failure()` and `errors._commit_failed` both
    matched only the old exact string, so a fix lane that wrote nothing
    but its excluded `dispositions.json` failed with "commit did not
    land" even though the spec says `ok` is unaffected."""
    fake_fleet(["sh", "-c", f"echo receipt > record.json; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "record.json", "commit": False}),
        commit_message="feat: add report",
        no_op_ok=True,
        home=home,
    )
    assert result.ok is True, result.failure()
    assert result.commit["committed"] is False
    assert result.commit["reason"] == "nothing to commit beyond the excluded deliverable"
    assert error_kind(result) is None


def test_write_lane_deliverable_is_ordinary_bytes_part_of_the_diff(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo done > report.txt && git add -A && git commit -qm work"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is True, result.failure()
    assert result.deliverable["ok"] is True
    assert result.diff_path is not None


def test_dry_run_records_deliverable_as_declared_and_unchecked(repo, home):
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), dry_run=True, home=home
    )
    assert result.deliverable == {
        "path": "report.txt",
        "exists": None,
        "bytes": None,
        "parsed": None,
        "ok": None,
        "reason": None,
    }
    assert result.ok is True


def test_deliverable_missing_does_not_mask_a_more_fundamental_failure(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "exit 3"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "missing.txt"}), home=home
    )
    assert result.failure() == "exit code 3"
    assert error_kind(result) == "exit"


# --- W5: a deliverable is bytes in the worktree, never a symlink -------------


def test_deliverable_symlinked_to_a_file_outside_the_worktree_fails(
    repo, home, fake_fleet, tmp_path
):
    """W5: the declared path is validated at load, but the lane runs after
    that. A symlink planted where the product belongs used to pass
    `is_file()` and be copied into the run directory with `copyfile`, so
    bytes from outside the worktree landed there as the lane's own."""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    fake_fleet(["sh", "-c", f"ln -s '{outside}' report.txt; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is False
    assert result.failure() == "deliverable is a symlink: report.txt"
    assert error_kind(result) == "deliverable"
    assert result.deliverable["exists"] is False
    assert result.deliverable_path is None
    assert not (Path(result.run_dir) / "deliverable").exists()


def test_deliverable_symlinked_inside_the_worktree_is_refused_too(repo, home, fake_fleet):
    """The simplest rule that closes W5 is no symlink at all: a link to a
    sibling in the same worktree is refused as well, so there is no
    resolve-and-compare step left to get wrong."""
    fake_fleet(
        ["sh", "-c", f"echo real > real.txt; ln -s real.txt report.txt; echo '{_OK_ANSWER}'"]
    )
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is False
    assert result.failure() == "deliverable is a symlink: report.txt"
    assert result.deliverable_path is None


def test_deliverable_under_a_symlinked_directory_is_refused(repo, home, fake_fleet, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.txt").write_text("secret\n")
    fake_fleet(["sh", "-c", f"ln -s '{outside}' sub; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "sub/report.txt"}), home=home
    )
    assert result.ok is False
    assert result.failure() == "deliverable is a symlink: sub/report.txt"
    assert result.deliverable_path is None


def test_a_regular_file_deliverable_is_still_captured(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", f"echo out > report.txt; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is True, result.failure()
    assert result.deliverable["ok"] is True
    assert Path(result.deliverable_path).read_text() == "out\n"


# --- W4: the judged artifact is the artifact left behind ---------------------


def test_a_gate_that_rewrites_the_deliverable_fails_the_run(repo, home, fake_fleet):
    """W4: the gate is an arbitrary shell command. One that rewrites the
    declared deliverable after the pre-gate check means the bytes on the
    receipt would not be the bytes the run was judged on -- the capture is
    refused and the run fails closed instead."""
    fake_fleet(["sh", "-c", "echo out > report.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "report.txt"}),
        home=home,
        test_command="echo tampered > report.txt",
    )
    assert result.ok is False
    assert result.failure() == "deliverable changed after the gate ran: report.txt"
    assert result.deliverable_path is None
    assert not (Path(result.run_dir) / "deliverable").exists()


def test_a_gate_that_leaves_the_deliverable_alone_captures_its_bytes(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo out > report.txt"])
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "report.txt"}),
        home=home,
        test_command="true",
    )
    assert result.ok is True, result.failure()
    assert result.deliverable["sha256"] == hashlib.sha256(b"out\n").hexdigest()
    assert Path(result.deliverable_path).read_text() == "out\n"


def test_a_teardown_that_rewrites_the_deliverable_fails_the_run(repo, home, fake_fleet):
    """The same rule after the run is judged: a teardown that rewrites the
    deliverable invalidates the copy already taken, and the run fails."""
    fake_fleet(["sh", "-c", "echo out > report.txt"])
    result = dispatch(
        spec_for(
            repo,
            mode="write",
            deliverable={"path": "report.txt"},
            teardown="echo tampered > report.txt",
        ),
        home=home,
    )
    assert result.ok is False
    assert result.failure() == "deliverable changed after the gate ran: report.txt"
    assert result.deliverable_path is None
    assert result.cleanup_required is True
    assert not (Path(result.run_dir) / "deliverable").exists()


# --- {{lanes.<name>.deliverable}} template (item 2) --------------------------


def test_deliverable_template_renders_into_a_downstream_prompt(tmp_path):
    deliverable_file = tmp_path / "upstream-deliverable"
    deliverable_file.write_text("upstream product")
    mission = mission_from_dict(
        {
            "prompt": "root",
            "cwd": str(tmp_path),
            "lanes": [
                {"name": "a", "fleet": "codex"},
                {
                    "name": "b",
                    "fleet": "codex",
                    "needs": ["a"],
                    "prompt": "{{lanes.a.deliverable}}",
                },
            ],
        },
        base_dir=tmp_path,
    )
    rendered = _render(
        mission.lanes[1].attempts[0].prompt,
        mission,
        {"a": LaneResult("a", True, deliverable_path=str(deliverable_file))},
        dry_run=False,
    )
    assert "upstream product" in rendered


def test_deliverable_template_is_empty_when_the_lane_left_none(tmp_path):
    mission = mission_from_dict(
        {
            "prompt": "root",
            "cwd": str(tmp_path),
            "lanes": [
                {"name": "a", "fleet": "codex"},
                {
                    "name": "b",
                    "fleet": "codex",
                    "needs": ["a"],
                    "prompt": "{{lanes.a.deliverable}}",
                },
            ],
        },
        base_dir=tmp_path,
    )
    rendered = _render(
        mission.lanes[1].attempts[0].prompt,
        mission,
        {"a": LaneResult("a", True)},
        dry_run=False,
    )
    assert rendered == "(none)"


# --- review findings ---------------------------------------------------------


def test_read_lane_exemption_holds_when_cwd_is_a_repo_subdirectory(repo, home, fake_fleet):
    """The deliverable exemption compares against git status paths, which are
    repo-root-relative, while `spec.deliverable["path"]` is cwd-relative. A
    dispatch whose cwd is a subdirectory of the repo (an ordinary, supported
    shape: README's "Isolation" section: "A cwd inside the repo stays the
    same subdirectory inside the worktree") must still recognize its own
    declared file as the only thing that moved."""
    subdir = repo / "src"
    subdir.mkdir()
    (subdir / "keep.txt").write_text("existing\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "add subdir"], cwd=repo, check=True, capture_output=True
    )
    fake_fleet(["sh", "-c", f"echo out > report.txt; echo '{_OK_ANSWER}'"])
    result = dispatch(
        spec_for(subdir, mode="read", deliverable={"path": "report.txt"}), home=home
    )
    assert result.ok is True, result.failure()
    assert result.git_verdict["deliverable_only"] is True


def test_schema_type_as_a_list_does_not_crash_the_dispatch(repo, home, fake_fleet, tmp_path):
    """JSON Schema allows `"type"` to be a list of allowed types (e.g.
    `["string", "null"]`). `_SCHEMA_TYPE_CHECKS.get(expected)` looks that list
    up in a dict keyed by strings, which raises `TypeError: unhashable type`
    instead of the documented `deliverable does not match schema` outcome."""
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps({"type": "object", "properties": {"note": {"type": ["string", "null"]}}})
    )
    fake_fleet(["sh", "-c", "printf '%s' '{\"note\": \"hi\"}' > record.json"])
    result = dispatch(
        spec_for(
            repo, mode="write", deliverable={"path": "record.json", "schema": str(schema)}
        ),
        home=home,
    )
    assert result.ok is True, result.failure()


def test_readme_documents_the_deliverable_validator_contract():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text()
    section = text.split("#### Validators", 1)[1].split("\n### ", 1)[0]
    assert "{path}" in section
    assert "--deliverable-validator" in section
    assert "`accepted`" in section
    assert "`reproduced`" in section
    assert "`rejected`" in section
    assert "the reviewers' question, not the validator's" in section
    reproduce_section = text.split("#### Reproduce before fix", 1)[1].split("\n#### ", 1)[0]
    assert "`validator`" in reproduce_section


def test_readme_lists_deliverable_among_the_template_and_taint_surfaces():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text()
    templates_section = text.split("Prompt templates:", 1)[1].split("\n\n", 1)[0]
    assert "{{lanes.<name>.deliverable}}" in templates_section
    taint_section = text.split("Taint spreads forward", 1)[1].split("\n\n", 1)[0]
    assert "deliverable" in taint_section


# --- Evidence map (Phase H item 6): a commit-false deliverable leaves the ---
# --- isolated worktree clean so later lanes can build on the tip ----------


def test_commit_false_deliverable_is_removed_from_the_isolated_worktree_after_capture(
    repo, home, fake_fleet
):
    """A build lane's `evidence.json` (commit: false) used to stay behind as
    an untracked file: the worktree was kept as dirty, `clean` read False,
    and `LaneResult.buildable()` refused every reviewer based on it. After
    every capture and verdict, the file is removed and the worktree released
    clean; the captured copy in the run directory is the deliverable."""
    fake_fleet(
        [
            "sh",
            "-c",
            f"echo done > report.txt; echo '{{\"items\": []}}' > evidence.json; "
            f"echo '{_OK_ANSWER}'",
        ]
    )
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "evidence.json", "commit": False}),
        commit_message="feat: add report",
        home=home,
        isolate=True,
    )
    assert result.ok is True, result.failure()
    assert result.deliverable["ok"] is True
    assert Path(result.deliverable_path).read_text().strip() == '{"items": []}'
    assert result.isolation["clean"] is True
    assert result.isolation["kept"] is False
    assert not Path(result.isolation["worktree"]).exists()
    assert any("removed from the worktree after capture" in n for n in result.git_verdict["notes"])
    log = subprocess.run(
        ["git", "show", "--stat", result.commit["sha"]], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "report.txt" in log and "evidence.json" not in log


def test_commit_false_deliverable_stays_in_a_non_isolated_checkout(repo, home, fake_fleet):
    fake_fleet(
        ["sh", "-c", f"echo done > report.txt; echo receipt > record.json; echo '{_OK_ANSWER}'"]
    )
    result = dispatch(
        spec_for(repo, mode="write", deliverable={"path": "record.json", "commit": False}),
        commit_message="feat: add report",
        home=home,
    )
    assert result.ok is True, result.failure()
    assert (repo / "record.json").read_text().strip() == "receipt"
    assert not any("removed from the worktree" in n for n in result.git_verdict["notes"])


# --- 2026-09-08 cold review: the deliverable surface after the Astra baseline ---


def test_an_array_schema_of_primitives_is_checked_as_primitives():
    """`type: array` with an `items` of `{"type": "string"}` recursed into
    `_schema_mismatch`, which had no primitive branch and fell through to the
    object check, so every list of strings or numbers came back as
    "item 0: top level is not a JSON object" and no such deliverable could
    pass. 0.84.0 made array schemas check each item; this is the item check
    itself."""
    strings = {"type": "array", "items": {"type": "string"}}
    assert runner_mod._schema_mismatch(["a", "b"], strings) is None
    assert runner_mod._schema_mismatch(["a", 2], strings) == "item 1: value must be of type string"

    numbers = {"type": "array", "items": {"type": "number"}}
    assert runner_mod._schema_mismatch([1, 2.5], numbers) is None
    assert runner_mod._schema_mismatch([1, "x"], numbers) == "item 1: value must be of type number"

    # The object path is unchanged.
    objects = {"type": "array", "items": {"type": "object", "required": ["k"]}}
    assert runner_mod._schema_mismatch([{"k": 1}], objects) is None
    assert runner_mod._schema_mismatch([{}], objects) == "item 0: missing required property 'k'"


def test_a_validator_path_with_a_space_is_shell_quoted():
    """The validator runs under `shell=True`. An unquoted `{path}` holding a
    space split across arguments, which fails the validator for a reason that
    has nothing to do with the bytes; `_validator_ran_and_failed` counts any
    non-zero exit as evidence the base was broken, so the shell error could
    manufacture a `reproduced` verdict. W2 was this same defect in the taint
    hook command."""
    command = runner_mod._validator_command("check {path}", "docs/a b.md")
    assert command == "check 'docs/a b.md'"

    hostile = runner_mod._validator_command("check {path}", "a'; touch pwned; '.md")
    assert "touch pwned" in hostile
    assert hostile.startswith("check '")
    # Quoted, so the shell sees one word and no second command.
    assert shlex.split(hostile) == ["check", "a'; touch pwned; '.md"]


def test_a_validator_command_without_the_placeholder_is_unchanged():
    assert runner_mod._validator_command("make lint", "docs/a.md") == "make lint"
