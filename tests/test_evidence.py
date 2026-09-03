"""Evidence over claims, round two.

Everything here was learned by running conductor's own brainstorm mission on
2026-09-03: a cursor lane that spent 15K output tokens and handed back 680
characters of narration, marked ok; a capped run that would read as within
budget if it came back unpriced; a collate step that judged prose while the
patches sat unread on disk.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conductor import runner as runner_mod
from conductor.fleets import Spec, build_argv
from conductor.mission import mission_from_dict, run_mission
from conductor.outputs import parse
from conductor.runner import dispatch
from conductor.verify import diff_since


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
