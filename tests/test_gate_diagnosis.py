"""E25: clean-gate diagnosis for the test-surface trap.

AGENTS.md rule 3 exists because a clean-gate failure caused by the lane's own
diff touching the test surface (the base tree's tests running against the
new source) reads identically to an ordinary broken build. These tests pin
the distinct message and `error_kind` that tell the two apart, and the
report.md sentence that carries the fix (`test_policy: allow`) to the lead
without them re-deriving it from the receipt by hand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conductor.errors import error_kind
from conductor.mission import mission_from_dict, run_mission
from conductor.runner import Result


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, message: str = "gate-diagnosis fixture") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _seed_conftest(repo: Path) -> None:
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text("# base test configuration\n")
    _commit(repo)


def _clean_gate_result(
    changed: list[str], *, exit_code: int = 1, touched: bool = True, **clean_overrides
) -> Result:
    """A hand-built receipt shaped like one whose clean gate already ran:
    lane gate ok, clean gate ran and did not pass, the diff touched the
    named test-surface files. Pins the pure message/kind logic without
    spending on a fake dispatch (`test_errors.py`'s own convention).

    `touched=False` builds the one shape `runner.py`'s own dispatch path
    cannot reach (a `clean_gate` only ever runs after `surface_state`
    reports `touched`), to pin the defensive fallback in `failure()` and
    `error_kind()` directly rather than leave it untested."""
    clean = {
        "ran": True,
        "exit_code": exit_code,
        "timed_out": False,
        "interrupted": False,
    }
    clean.update(clean_overrides)
    return Result(
        run_id="r1",
        fleet="claude",
        model="m",
        effort="standard",
        mode="write",
        cwd="/tmp/repo",
        timeout=60,
        exit_code=0,
        timed_out=False,
        duration_s=1.0,
        run_dir="/tmp/r1",
        stdout_path="/tmp/r1/stdout.log",
        stderr_path="/tmp/r1/stderr.log",
        tail="",
        spawned=True,
        git_verdict={"checked": True, "no_op": False},
        tests={"ran": True, "exit_code": 0, "timed_out": False, "interrupted": False},
        test_surface={"touched": touched, "changed": changed, "clean_gate": clean},
    )


def test_clean_gate_failure_after_a_test_surface_edit_gets_message_kind_and_report_sentence(
    repo, home, fake_fleet
):
    _seed_conftest(repo)
    # `tests/marker` is itself inside the test surface, so the clean gate's
    # transplant excludes it (like every test-surface change) and its own
    # gate fails -- the same shape `test_surface.py`'s
    # test_clean_policy_rejects_a_gate_that_only_passes_after_test_edits
    # proves already, here read through the new message and kind instead.
    fake_fleet(
        [
            "sh",
            "-c",
            "printf '# fleet override\\n' >> tests/conftest.py; echo cheat > tests/marker",
        ]
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "test": "test -f tests/marker",
            "lanes": [{"name": "build", "fleet": "claude", "mode": "write"}],
        },
        base_dir=repo,
    )

    result = run_mission(mission, home=home)

    attempt = result.lanes[0]["attempts"][-1]
    exit_code = attempt["test_surface"]["clean_gate"]["exit_code"]
    assert attempt["test_surface"]["touched"] is True
    assert attempt["test_surface"]["changed"] == ["tests/conftest.py", "tests/marker"]
    assert exit_code != 0
    expected_message = (
        f"clean gate exited {exit_code} after the diff touched 2 test-surface files: "
        "tests/conftest.py, tests/marker"
    )
    assert attempt["failure"] == expected_message
    assert attempt["kind"] == "gate_test_surface"
    assert result.errors.get("gate_test_surface") == 1
    report = Path(result.report_path).read_text()
    assert expected_message in report
    assert "(kind: gate_test_surface)" in report
    assert (
        "the base tree's tests ran against the new source; if the spec changes what "
        "existing missions may do, rerun with test_policy: allow and read every test edit."
        in report
    )


def test_clean_gate_failure_without_a_test_surface_edit_keeps_the_plain_message_and_kind():
    """`runner.py` only ever sets `clean_gate["ran"] = True` after
    `surface_state["touched"]` was already checked True (a dispatch whose
    diff never touched the surface skips the clean gate outright: 'test
    surface unchanged', the common case the README says pays nothing
    extra) -- so this shape is built by hand, the same way the precedence
    tests in `test_errors.py` pin unreachable-by-dispatch edges."""
    result = _clean_gate_result([], exit_code=1, touched=False)

    assert result.failure() == "clean gate exited 1"
    assert error_kind(result) == "gate"


def test_the_touched_file_list_truncates_after_five_and_counts_the_rest():
    changed = [f"tests/test_{i}.py" for i in (5, 3, 1, 4, 2, 7, 6)]
    result = _clean_gate_result(changed, exit_code=3)

    assert result.failure() == (
        "clean gate exited 3 after the diff touched 7 test-surface files: "
        "tests/test_1.py, tests/test_2.py, tests/test_3.py, tests/test_4.py, "
        "tests/test_5.py, and 2 more"
    )
    assert error_kind(result) == "gate_test_surface"


@pytest.mark.parametrize(
    "clean_overrides,label",
    [
        ({"interrupted": True}, "interrupted"),
        ({"timed_out": True}, "timed out"),
    ],
)
def test_an_interrupted_or_timed_out_clean_gate_keeps_its_own_message_and_kind(
    clean_overrides, label
):
    """Item 1's carve-out: a touched surface only changes the plain 'exited
    N' message, never the interrupted/timed-out ones -- those already say
    what happened and are not the ambiguous case rule 3 is about."""
    result = _clean_gate_result(["tests/conftest.py"], **clean_overrides)

    assert result.failure() == f"clean gate {label}"
    assert error_kind(result) == "gate"
