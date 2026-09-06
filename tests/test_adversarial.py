"""E16: adversarial test lanes.

A `stage: adversarial` lane's deliverable is a test that fails on another
lane's tip, not a fix. These tests pin the two load rules, the reversed
reproduce verdict (a failing gate means the lane found something), what
lands and what does not, the new `adversarial` error kind for a lane that
touches source, a fix lane inheriting a reproduced check, and the
`--adversarial` launcher flag.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor import shape
from conductor.errors import error_kind
from conductor.fleets import Spec
from conductor.mission import MissionInvalid, mission_from_dict, run_mission
from conductor.runner import dispatch


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, message: str = "adversarial fixture") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _spec(repo: Path, **changes) -> Spec:
    fields = {
        "fleet": "claude",
        "prompt": "adversarial test",
        "cwd": str(repo),
        "mode": "write",
        "stage": "adversarial",
        "test_policy": "allow",
    }
    fields.update(changes)
    return Spec(**fields)


def _check_script(expect: str, *, dest: str = "tests/check_defect.py") -> str:
    """A shell fragment that writes a Python check to `dest`: exit 0 when
    app.txt's content equals `expect`, exit 1 otherwise. A quoted heredoc
    passes its body through verbatim -- no shell or printf escaping to get
    wrong -- so the generated file's own `\\n` (matching `expect`'s trailing
    newline) is written as a literal two-character escape, not interpreted
    early."""
    return (
        f"mkdir -p {shlex.quote(str(Path(dest).parent))} && cat <<'PYEOF' > {dest}\n"
        "import pathlib, sys\n"
        f'sys.exit(0 if pathlib.Path("app.txt").read_text() == {expect!r} else 1)\n'
        "PYEOF"
    )


def _fake_fleet_by_prompt(monkeypatch: pytest.MonkeyPatch, scripts: dict[str, str]) -> None:
    """Like conftest's `fake_fleet`, but the shell command run depends on the
    dispatch's own prompt text -- each lane below gets a distinct literal
    prompt naming which script it runs, so one mission can give build,
    adversarial, and fix lanes different fleet behavior with no real model
    in the loop."""

    def fake_build(spec: Spec) -> list[str]:
        script = scripts[spec.prompt]
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "usage": {"inputTokens": 10, "outputTokens": 1},
        }
        output = json.dumps(payload)
        # A newline, not `&&`: a heredoc-based script's terminator line must
        # be the delimiter alone, nothing else joined onto the same line.
        return ["sh", "-c", f"{script}\nprintf '%s\\n' {shlex.quote(output)}"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)


# --- 1. load rules -----------------------------------------------------------


def test_adversarial_lane_without_a_base_is_refused_naming_the_lane(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "name": "adversarial",
                "fleet": "claude",
                "stage": "adversarial",
                "mode": "write",
                "test_policy": "allow",
            }
        ],
    }
    with pytest.raises(
        MissionInvalid, match=r"'adversarial': an adversarial lane must declare a base"
    ):
        mission_from_dict(raw, base_dir=tmp_path)


def test_adversarial_lane_without_test_policy_allow_is_refused_naming_the_lane(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "claude", "stage": "build", "mode": "write"},
            {
                "name": "adversarial",
                "fleet": "claude",
                "stage": "adversarial",
                "mode": "write",
                "base": "build",
            },
        ],
    }
    with pytest.raises(
        MissionInvalid, match=r"'adversarial'.*must set test_policy: allow"
    ):
        mission_from_dict(raw, base_dir=tmp_path)


def test_adversarial_stage_with_a_base_and_test_policy_allow_loads_and_a_policy_may_name_it(
    tmp_path,
):
    raw = {
        "prompt": "x",
        "policy": {"adversarial": {"vendors": ["anthropic"]}},
        "lanes": [
            {"name": "build", "fleet": "claude", "stage": "build", "mode": "write"},
            {
                "name": "adversarial",
                "fleet": "claude",
                "stage": "adversarial",
                "mode": "write",
                "base": "build",
                "test_policy": "allow",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[1].stage == "adversarial"
    assert mission.lanes[1].base == "build"
    assert mission.policy["adversarial"]["vendors"] == ["anthropic"]


# --- 2. the verdict, dispatched directly -------------------------------------


def test_adversarial_lane_that_edits_source_is_refused_with_the_new_kind(repo, home, fake_fleet):
    (repo / "app.txt").write_text("bad\n")
    _commit(repo)
    fake_fleet(
        [
            "sh",
            "-c",
            "printf 'good\\n' > app.txt && mkdir -p tests && "
            "printf 'raise SystemExit(0)\\n' > tests/check.py",
        ]
    )

    result = dispatch(
        _spec(repo),
        home=home,
        test_command="python tests/check.py",
        commit_message="adversarial: found a bug",
    )

    assert result.ok is False
    assert result.error == "adversarial lane changed source: app.txt"
    assert error_kind(result) == "adversarial"
    assert result.commit is None


def test_adversarial_lane_with_no_gate_set_is_refused_without_a_reproducing_check(
    repo, home, fake_fleet
):
    fake_fleet(["sh", "-c", "mkdir -p tests && printf 'raise SystemExit(1)\\n' > tests/check.py"])

    result = dispatch(_spec(repo), home=home, commit_message="adversarial: no gate")

    assert result.reproduce["verdict"] == "no-check"
    assert result.error == "adversarial lane without a reproducing check: no gate set"
    assert result.ok is False
    assert result.commit is None


def test_adversarial_lane_that_self_commits_after_editing_source_is_discarded(
    repo, home, fake_fleet
):
    """A fleet that self-commits despite the prompt's "do not commit" must not
    be able to land a source edit through the back door: `reproduce_blocks_commit`
    must catch a self-commit the same way it catches conductor's own, since the
    spec requires "any change outside the test surface fails the dispatch" with
    nothing landed, self-commit or not."""
    (repo / "app.txt").write_text("bad\n")
    base = _commit(repo)
    fake_fleet(
        [
            "sh",
            "-c",
            "printf 'good\\n' > app.txt && mkdir -p tests && "
            "printf 'raise SystemExit(0)\\n' > tests/check.py && "
            "git add -A && git commit -m 'self commit'",
        ]
    )

    result = dispatch(_spec(repo), home=home, test_command="python tests/check.py")

    assert result.error == "adversarial lane changed source: app.txt"
    assert result.ok is False
    assert result.commit is None or result.commit["committed"] is False
    assert _git(repo, "rev-parse", "HEAD") == base


# --- 3. the verdict, through a mission (buildable / discard) -----------------


def test_adversarial_lane_whose_test_fails_on_its_base_is_reproduced_committed_and_buildable(
    repo, home, monkeypatch
):
    (repo / "app.txt").write_text("bad\n")
    _commit(repo)
    _fake_fleet_by_prompt(
        monkeypatch,
        {
            "BUILD": "printf 'feature\\n' > feature.py",
            "ADVERSARIAL": _check_script("good\n"),
        },
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "stage": "build",
                    "mode": "write",
                    "prompt": "BUILD",
                    "commit": "feat: build",
                },
                {
                    "name": "adversarial",
                    "fleet": "claude",
                    "stage": "adversarial",
                    "mode": "write",
                    "base": "build",
                    "test_policy": "allow",
                    "test": "python tests/check_defect.py",
                    "prompt": "ADVERSARIAL",
                    "commit": "adversarial: found a bug",
                },
            ],
        },
        base_dir=repo,
    )

    result = run_mission(mission, home=home)

    adversarial = next(lane for lane in result.lanes if lane["name"] == "adversarial")
    assert adversarial["attempts"][-1]["reproduce"]["verdict"] == "reproduced"
    assert adversarial["ok"] is True
    assert adversarial["clean"] is True
    assert adversarial["tip_sha"] and adversarial["tip_sha"] != adversarial["base_sha"]
    assert adversarial["attempts"][-1]["committed"] is not None


def test_adversarial_lane_whose_test_passes_is_not_reproduced_not_committed_and_ok(
    repo, home, monkeypatch
):
    (repo / "app.txt").write_text("bad\n")
    _commit(repo)
    _fake_fleet_by_prompt(
        monkeypatch,
        {
            "BUILD2": "printf 'feature\\n' > feature.py",
            "ADVERSARIAL_PASS": _check_script("bad\n"),
        },
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "stage": "build",
                    "mode": "write",
                    "prompt": "BUILD2",
                    "commit": "feat: build",
                },
                {
                    "name": "adversarial",
                    "fleet": "claude",
                    "stage": "adversarial",
                    "mode": "write",
                    "base": "build",
                    "test_policy": "allow",
                    "test": "python tests/check_defect.py",
                    "prompt": "ADVERSARIAL_PASS",
                    "commit": "adversarial: found nothing",
                },
            ],
        },
        base_dir=repo,
    )

    result = run_mission(mission, home=home)

    build = next(lane for lane in result.lanes if lane["name"] == "build")
    adversarial = next(lane for lane in result.lanes if lane["name"] == "adversarial")
    assert adversarial["attempts"][-1]["reproduce"]["verdict"] == "not-reproduced"
    assert adversarial["ok"] is True
    assert adversarial["clean"] is True
    assert adversarial["tip_sha"] == build["tip_sha"]
    assert adversarial["attempts"][-1]["committed"] is None


# --- 4. the fix lane inherits the check --------------------------------------


def test_fix_lane_on_a_reproduced_adversarial_base_inherits_the_check_and_lands(
    repo, home, monkeypatch
):
    (repo / "app.txt").write_text("bad\n")
    _commit(repo)
    _fake_fleet_by_prompt(
        monkeypatch,
        {
            "BUILD3": "printf 'feature\\n' > feature.py",
            "ADVERSARIAL3": _check_script("good\n"),
            "FIX3": "printf 'good\\n' > app.txt",
        },
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "stage": "build",
                    "mode": "write",
                    "prompt": "BUILD3",
                    "commit": "feat: build",
                },
                {
                    "name": "adversarial",
                    "fleet": "claude",
                    "stage": "adversarial",
                    "mode": "write",
                    "base": "build",
                    "test_policy": "allow",
                    "test": "python tests/check_defect.py",
                    "prompt": "ADVERSARIAL3",
                    "commit": "adversarial: found a bug",
                },
                {
                    "name": "fix",
                    "fleet": "claude",
                    "stage": "fix",
                    "mode": "write",
                    "base": "adversarial",
                    "needs": ["adversarial"],
                    "test_policy": "allow",
                    "test": "python tests/check_defect.py",
                    "prompt": "FIX3",
                    "commit": "fix: address the defect",
                },
            ],
        },
        base_dir=repo,
    )

    result = run_mission(mission, home=home)

    fix = next(lane for lane in result.lanes if lane["name"] == "fix")
    assert fix["attempts"][-1]["reproduce"]["verdict"] == "inherited"
    assert "adversarial" in fix["attempts"][-1]["reproduce"]["tail"]
    assert fix["ok"] is True
    assert fix["attempts"][-1]["committed"] is not None
    assert fix["attempts"][-1]["tests"] == 0


def test_fix_lane_on_a_not_reproduced_adversarial_base_is_refused_without_its_own_check(
    repo, home, monkeypatch
):
    (repo / "app.txt").write_text("bad\n")
    _commit(repo)
    _fake_fleet_by_prompt(
        monkeypatch,
        {
            "BUILD4": "printf 'feature\\n' > feature.py",
            "ADVERSARIAL4": _check_script("bad\n"),
            "FIX4": "printf 'x\\n' > other.txt",
        },
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "stage": "build",
                    "mode": "write",
                    "prompt": "BUILD4",
                    "commit": "feat: build",
                },
                {
                    "name": "adversarial",
                    "fleet": "claude",
                    "stage": "adversarial",
                    "mode": "write",
                    "base": "build",
                    "test_policy": "allow",
                    "test": "python tests/check_defect.py",
                    "prompt": "ADVERSARIAL4",
                    "commit": "adversarial: found nothing",
                },
                {
                    "name": "fix",
                    "fleet": "claude",
                    "stage": "fix",
                    "mode": "write",
                    "base": "adversarial",
                    "needs": ["adversarial"],
                    "test_policy": "allow",
                    "test": "python tests/check_defect.py",
                    "prompt": "FIX4",
                    "commit": "fix: attempted",
                },
            ],
        },
        base_dir=repo,
    )

    result = run_mission(mission, home=home)

    fix = next(lane for lane in result.lanes if lane["name"] == "fix")
    assert fix["attempts"][-1]["reproduce"]["verdict"] == "no-check"
    assert fix["attempts"][-1]["error"] == "fix without a reproducing check: no test-surface change"
    assert fix["ok"] is False


# --- 5. the launcher -----------------------------------------------------------


def _spec_file(tmp_path: Path) -> Path:
    spec = tmp_path / "spec.md"
    spec.write_text("# widget\n\n1. one\n")
    return spec


def test_shape_a_adversarial_flag_adds_the_lane_policy_and_fix_prompt_block(repo, tmp_path):
    spec = _spec_file(tmp_path)
    caps = shape.cap_arithmetic(2, 1, adversarial=True)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps, adversarial=True)

    names = [lane["name"] for lane in raw["lanes"]]
    assert "adversarial" in names
    assert raw["policy"]["adversarial"] == {"vendors": ["anthropic"]}
    adversarial_lane = next(lane for lane in raw["lanes"] if lane["name"] == "adversarial")
    assert adversarial_lane["stage"] == "adversarial"
    assert adversarial_lane["base"] == "build"
    assert adversarial_lane["test_policy"] == "allow"
    # Item 2: "the harness commits only on reproduced" -- with no `commit`
    # message configured, conductor's own commit_work is never invoked, so a
    # reproduced adversarial lane can never land.
    assert adversarial_lane.get("commit")
    fix_lane = next(lane for lane in raw["lanes"] if lane["name"] == "fix")
    assert fix_lane["base"] == "adversarial"
    assert "adversarial" in fix_lane["needs"] and "build" in fix_lane["needs"]
    assert fix_lane["resume"] == "build"
    assert "<adversarial>" in fix_lane["prompt"]
    assert "{{lanes.adversarial.answer}}" in fix_lane["prompt"]
    assert "{{lanes.adversarial.diff}}" in fix_lane["prompt"]

    mission = mission_from_dict(raw, base_dir=spec.parent)
    assert any(lane.stage == "adversarial" for lane in mission.lanes)


def test_shape_a_without_the_adversarial_flag_is_unchanged(repo, tmp_path):
    spec = _spec_file(tmp_path)
    caps = shape.cap_arithmetic(2, 1)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps)

    names = [lane["name"] for lane in raw["lanes"]]
    assert "adversarial" not in names
    assert "adversarial" not in raw["policy"]
    fix_lane = next(lane for lane in raw["lanes"] if lane["name"] == "fix")
    assert fix_lane["base"] == "build"
    assert "<adversarial>" not in fix_lane["prompt"]
    assert fix_lane["prompt"] == shape.FIX_PROMPT

    mission = mission_from_dict(raw, base_dir=spec.parent)
    assert all(lane.stage != "adversarial" for lane in mission.lanes)


def test_shape_a_adversarial_fix_prompt_explains_the_inherited_check(repo, tmp_path):
    """Grok's third E16 finding: the fix prompt must not tell the agent a
    source-only fix is refused when the inherited check makes it exactly
    what is wanted."""
    from conductor.shape import cap_arithmetic, shape_a

    spec_path = tmp_path / "spec.md"
    spec_path.write_text("x")
    raw = shape_a(
        spec=spec_path, repo=repo, test="true", caps=cap_arithmetic(1, 1), adversarial=True
    )
    fix_prompt = next(lane["prompt"] for lane in raw["lanes"] if lane["name"] == "fix")
    assert "is your reproducing check" in fix_prompt
    plain = shape_a(spec=spec_path, repo=repo, test="true", caps=cap_arithmetic(1, 1))
    plain_fix = next(lane["prompt"] for lane in plain["lanes"] if lane["name"] == "fix")
    assert "is your reproducing check" not in plain_fix
