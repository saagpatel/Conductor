"""Evidence over claims, round two.

Everything here was learned by running conductor's own brainstorm mission on
2026-09-03: a cursor lane that spent 15K output tokens and handed back 680
characters of narration, marked ok; a capped run that would read as within
budget if it came back unpriced; a collate step that judged prose while the
patches sat unread on disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor import verify as verify_mod
from conductor.fleets import Spec, build_argv
from conductor.mission import mission_from_dict, run_mission
from conductor.outputs import parse
from conductor.runner import dispatch
from conductor.verify import GitState, diff_since, git_run, run_tests


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test evidence", cwd=str(repo))
    base.update(kw)
    return Spec(**base)


def _assistant(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


# --- a read dispatch's answer is its work ------------------------------------


def test_a_read_dispatch_with_no_answer_is_not_ok(repo, home, fake_fleet):
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,"result":"",'
        '"usage":{"input_tokens":1,"output_tokens":1}}'
    )
    fake_fleet(["sh", "-c", f"echo '{envelope}'"])
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.exit_code == 0 and result.answer_path is None
    assert result.summary()["failure"] == "read dispatch returned no answer"


def test_a_write_dispatch_needs_bytes_not_words(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo work > w.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.answer_path is None and result.ok is True


def test_a_dry_run_is_not_faulted_for_silence(repo, home):
    result = dispatch(spec_for(repo), dry_run=True, home=home)
    assert result.summary()["failure"] is None


def test_a_lane_answer_is_its_final_attempts_not_a_failed_primarys(
    repo, home, monkeypatch, tmp_path
):
    said = json.dumps(
        {"result": "the wrong answer", "usage": {"input_tokens": 1, "output_tokens": 1}}
    )
    by_fleet = {"claude": ["sh", "-c", f"echo '{said}'; exit 1"], "codex": ["sh", "-c", "exit 0"]}
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "claude", "fallback": [{"fleet": "codex"}]}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    lane = result.lanes[0]
    assert lane["ok"] is False and len(lane["attempts"]) == 2
    assert lane["answer_path"] is None
    assert lane["attempts"][1]["failure"] == "read dispatch returned no answer"


# --- cursor: keep everything the model said ----------------------------------


def test_cursor_runs_in_stream_json():
    argv = build_argv(Spec(fleet="cursor", prompt="x", cwd="/tmp"))
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_cursor_answer_is_the_whole_transcript_not_the_last_message():
    """The json envelope's `result` is only the last assistant message; a
    model that answers and then adds a closing remark loses the answer."""
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        _assistant("## Findings\n\n1. The real answer."),
        json.dumps({"type": "thinking", "subtype": "delta", "text": "..."}),
        _assistant("Writing the four-part consultation now."),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Writing the four-part consultation now.",
                "usage": {"inputTokens": 10, "outputTokens": 5},
            }
        ),
    ]
    out = parse("cursor", "\n".join(lines))
    assert out.answer.startswith("## Findings") and out.answer.endswith("consultation now.")
    assert out.usage.input_tokens == 10 and out.status == "success"

    cut_short = parse("cursor", "\n".join(lines[:2]))
    assert cut_short.answer.startswith("## Findings") and cut_short.usage is None
    assert cut_short.error == "cursor stream ended without a result event"

    err = json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": "boom"})
    failed = parse("cursor", "\n".join(lines[:2] + [err]))
    assert failed.error == "boom" and failed.answer == ""


# --- a capped run that comes back unpriced was never capped -----------------


def test_a_capped_run_that_comes_back_unpriced_fails_closed(repo, home, fake_fleet):
    envelope = '{"type":"result","subtype":"success","is_error":false,"result":"PONG"}'
    fake_fleet(["sh", "-c", f"echo '{envelope}'"])
    capped = dispatch(spec_for(repo, fleet="cursor", model="composer-2.5", cap_usd=1.0), home=home)
    assert capped.usage is None
    assert capped.budget["unpriced"] is True and capped.budget["exceeded"] is False
    assert capped.summary()["failure"] == "cap unenforced: the run came back unpriced"
    uncapped = dispatch(spec_for(repo, fleet="cursor", model="composer-2.5"), home=home)
    assert uncapped.ok is True


# --- the patch is the evidence -----------------------------------------------


def test_diff_since_shows_committed_uncommitted_and_untracked_work(repo, git_out):
    base = git_out(repo, "rev-parse", "HEAD")
    (repo / "seed.txt").write_text("seed\nmore\n")
    subprocess.run(["git", "commit", "-qam", "edit seed"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\nmore\neven more\n")
    (repo / "fresh.txt").write_text("fresh\n")
    patch = diff_since(str(repo), base)
    assert "+more" in patch and "+even more" in patch and "+fresh" in patch
    assert diff_since(str(repo), base, limit=20).endswith("truncated at 20 chars]\n")


def test_diff_since_includes_an_untracked_quoted_name(repo, git_out):
    base = git_out(repo, "rev-parse", "HEAD")
    name = "résumé draft.txt"
    (repo / name).write_text("quoted path\n")
    patch = diff_since(str(repo), base)
    assert "quoted path" in patch
    assert name in patch


def test_a_dispatch_records_its_diff_and_the_collate_sees_it(repo, home, monkeypatch, tmp_path):
    said = json.dumps(
        {"result": "I refactored everything.", "usage": {"input_tokens": 1, "output_tokens": 1}}
    )
    by_fleet = {
        "codex": ["sh", "-c", "printf 'actual change\\n' > change.txt"],
        "claude": ["sh", "-c", f"echo '{said}'"],
    }
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "write",
        "lanes": [{"name": "builder", "fleet": "codex"}],
        "collate": {"fleet": "claude"},
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    lane = result.lanes[0]
    assert lane["ok"] is True
    assert lane["diff_path"] == str(Path(result.mission_dir) / "diffs" / "builder.patch")
    assert Path(lane["diff_path"]).read_text().count("+actual change") == 1
    prompt = (Path(result.mission_dir) / "collate-prompt.txt").read_text()
    assert "What this lane actually changed" in prompt and "+actual change" in prompt
    assert "diff:" in Path(result.report_path).read_text()


def test_a_no_op_dispatch_records_no_diff(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo 'looked around'"])
    result = dispatch(spec_for(repo), home=home)
    assert result.diff_path is None and result.summary()["diff_path"] is None


# --- cursor's plan mode files its answer as a plan ---------------------------


def test_cursor_plan_is_read_as_the_answer():
    """Composer wrote a full four-finding audit into a createPlan tool call
    and said only "auditing..." out loud (live, 2026-09-03)."""
    plan_call = {"createPlanToolCall": {"args": {"plan": "# Audit\n\n## Finding 1\n\nreal."}}}
    lines = [
        _assistant("Auditing the codebase."),
        json.dumps({"type": "tool_call", "subtype": "started", "tool_call": plan_call}),
        json.dumps({"type": "tool_call", "subtype": "completed", "tool_call": plan_call}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": ""}),
    ]
    out = parse("cursor", "\n".join(lines))
    assert out.answer.count("## Finding 1") == 1
    assert out.answer.startswith("Auditing the codebase.")


# --- the four-fleet trust audit's confirmed findings -------------------------


def test_a_failed_gate_takes_its_commit_back_off_the_branch(repo, home, fake_fleet, git_out):
    """A branch must never carry a commit that failed its gate; the receipt
    says not ok, but the commit would outlive the receipt."""
    fake_fleet(["sh", "-c", "echo work > f.txt"])
    result = dispatch(
        spec_for(repo, mode="write"), commit_message="feat: x", test_command="exit 1", home=home
    )
    assert result.summary()["failure"] == "gate exited 1"
    assert result.commit["committed"] is False and "undone" in result.commit["reason"]
    assert result.summary()["committed"] is None
    assert git_out(repo, "log", "--oneline").count("\n") == 0  # only the seed commit
    assert git_out(repo, "status", "--porcelain") == "A  f.txt"  # the work stays, staged
    assert result.git_verdict["commits_added"] == 0 and result.git_verdict["dirty_delta"] == 1
    assert any("undone" in n for n in result.git_verdict["notes"])


def test_a_fleets_self_commit_is_undone_when_the_gate_fails(repo, home, fake_fleet, git_out):
    fake_fleet(
        ["sh", "-c", "echo work > self.txt && git add -A && git commit -qm 'fleet commit'"]
    )
    result = dispatch(spec_for(repo, mode="write"), test_command="exit 1", home=home)
    assert result.ok is False and result.summary()["failure"] == "gate exited 1"
    assert result.commit["committed"] is False and "undone" in result.commit["reason"]
    assert git_out(repo, "log", "--oneline").count("\n") == 0
    assert "self.txt" in git_out(repo, "diff", "--cached", "--name-only")


def test_switching_branches_is_not_mistaken_for_a_self_commit(
    repo, home, fake_fleet, git_out
):
    subprocess.run(["git", "switch", "-qc", "side"], cwd=repo, check=True)
    (repo / "side.txt").write_text("important side work\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "important side work"], cwd=repo, check=True)
    side_tip = git_out(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "switch", "-q", "main"], cwd=repo, check=True)

    fake_fleet(["git", "switch", "-q", "side"])
    result = dispatch(spec_for(repo, mode="write"), test_command="exit 1", home=home)

    assert result.summary()["failure"] == "gate exited 1"
    assert result.commit is None
    assert git_out(repo, "rev-parse", "side") == side_tip
    assert "important side work" in git_out(repo, "log", "--all", "--format=%s")


def test_dispatch_takes_only_two_content_manifests(repo, home, fake_fleet, monkeypatch):
    calls = 0
    real_manifest = verify_mod._manifest

    def manifest(*args):
        nonlocal calls
        calls += 1
        return real_manifest(*args)

    monkeypatch.setattr(verify_mod, "_manifest", manifest)
    fake_fleet(["sh", "-c", "echo work > work.txt"])
    dispatch(spec_for(repo, mode="write"), home=home)
    assert calls == 2


def test_manifest_streams_file_content_instead_of_reading_it_whole(repo, monkeypatch):
    (repo / "large.bin").write_bytes(b"x" * 1024 * 1024)

    def whole_file_read(_path):
        raise AssertionError("manifest loaded a dirty file whole")

    monkeypatch.setattr(Path, "read_bytes", whole_file_read)
    state = GitState.capture(str(repo))
    assert state.manifest


def test_git_run_surrogateescapes_non_utf8_output(repo):
    result = git_run(repo, "-c", r'alias.raw=!printf "\\377"', "raw")
    assert result.returncode == 0
    assert result.stdout.encode("utf-8", errors="surrogateescape") == b"\xff"


def test_a_fleet_that_reports_failure_is_never_committed(repo, home, fake_fleet, git_out):
    envelope = '{"status":"ERROR","response":"","error":"boom"}'
    fake_fleet(["sh", "-c", f"echo work > f.txt; echo '{envelope}'"])
    result = dispatch(
        spec_for(repo, fleet="antigravity", mode="write"), commit_message="feat: x", home=home
    )
    assert result.commit is None
    assert git_out(repo, "log", "--oneline").count("\n") == 0
    assert result.summary()["failure"] == "fleet reported: boom"


def test_a_timed_out_gate_leaves_no_grandchild(repo):
    """The gate gets a process group like a fleet: a killed suite must not
    leave workers behind to keep editing the tree after the verdict."""
    marker = repo / "gate-child.pid"
    outcome = run_tests(str(repo), f"sleep 60 & echo $! > {marker}; sleep 60", timeout=2)
    assert outcome.timed_out is True
    pid = int(marker.read_text().strip())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_an_unpriced_dispatch_makes_the_mission_budget_unverifiable(
    repo, home, monkeypatch, tmp_path
):
    silent = json.dumps({"result": "done, trust me"})  # no usage, no cost
    by_fleet = {
        "claude": ["sh", "-c", f"echo '{silent}'"],
        "cursor": ["sh", "-c", f"echo '{silent}'"],
    }
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "concurrency": 1,
        "max_cost_usd": 5.0,
        "lanes": [{"fleet": "claude"}, {"fleet": "cursor"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    first, second = result.lanes
    # Two layers agree: the budget's remainder capped lane one, so its silent
    # run failed closed at dispatch level; the ledger then stops lane two.
    assert first["attempts"][0]["failure"] == "cap unenforced: the run came back unpriced"
    assert second["attempts"] == [] and "budget unverifiable" in second["skipped"]
    assert result.budget["unverifiable"] is True and result.budget["spent_usd"] == 0.0


def test_read_lanes_are_isolated_so_a_misbehaving_fleet_cannot_touch_the_checkout(
    repo, home, monkeypatch, tmp_path, git_out
):
    said = json.dumps({"result": "looked around", "usage": {"input_tokens": 1, "output_tokens": 1}})
    by_fleet = {"claude": ["sh", "-c", f"echo leak > leak.txt; echo '{said}'"]}
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "claude"}]}
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    attempt = result.lanes[0]["attempts"][0]
    assert attempt["failure"] == "read dispatch moved bytes"
    assert attempt["branch"] and attempt["worktree"]  # kept, with the leak in it
    assert git_out(repo, "status", "--porcelain") == ""
    assert not (repo / "leak.txt").exists()
