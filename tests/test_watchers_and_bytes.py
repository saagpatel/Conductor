"""The failing paths of the primitives everything else trusts.

A cold read of breakers.py, collisions.py, verify.py, and errors.py
(2026-09-08) found eight places where a limit, a path, or a git command that
did not answer was read as an answer. Each test here goes red without its
fix.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from conductor.breakers import Breaker, _limit
from conductor.collisions import _parse_conflicts, merge_conflicts
from conductor.errors import TRANSPORT_PATTERNS, error_kind
from conductor.verify import GitState, compare, run_tests, same_repo


def _codex_tool_line(call_id: str, command: str = "ls") -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"id": call_id, "type": "command_execution", "command": command},
        },
        separators=(",", ":"),
    )


# --- breaker limits ---------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [True, False, -1, -0.5, float("nan"), float("inf"), "5", None, 0],
)
def test_a_limit_that_is_not_a_positive_finite_number_disables_the_breaker(value):
    """`value or None` let three of these through: `True` compares equal to 1,
    so `stall_timeout: true` killed a healthy run after a second; a negative
    is already behind every elapsed time; NaN is False against every
    comparison and turns the breaker off without saying so."""
    assert _limit(value) is None


@pytest.mark.parametrize(("value", "expected"), [(1, 1), (600, 600), (2.0, 2)])
def test_a_usable_limit_is_kept(value, expected):
    assert _limit(value) == expected


def test_a_true_stall_timeout_does_not_kill_a_healthy_run(tmp_path: Path):
    log = tmp_path / "stdout.log"
    log.write_text("")
    breaker = Breaker(
        "codex", log, stall_s=True, loop_limit=None, max_tool_calls=None
    )
    assert breaker.stall_s is None
    assert breaker.check() is None


def test_a_replaced_log_is_counted_afresh_not_mixed_with_the_old_tally(tmp_path: Path):
    """The file about to be re-read from zero is the only record of what the
    run did. Keeping the old signatures counted every re-read call with a
    fresh identity twice while skipping every identity already seen."""
    log = tmp_path / "stdout.log"
    log.write_text("\n".join(_codex_tool_line(f"c{i}") for i in range(4)) + "\n")
    breaker = Breaker("codex", log, stall_s=None, loop_limit=None, max_tool_calls=10)
    breaker.check()
    assert len(breaker.signatures) == 4

    # A shorter file at the same path: a truncation or a replacement.
    log.write_text(_codex_tool_line("c0") + "\n")
    breaker.check()

    assert len(breaker.signatures) == 1
    assert breaker.tripped is None


def test_a_tool_call_carrying_a_unicode_line_separator_is_still_counted(tmp_path: Path):
    """The stream is newline-delimited JSON, and U+2028 is legal inside a
    JSON string. `splitlines()` broke the line into fragments that failed to
    parse and vanished from the count."""
    log = tmp_path / "stdout.log"
    # A writer that does not escape non-ASCII (`ensure_ascii=False`, which
    # every JS `JSON.stringify` does by default) puts the separator in the
    # stream raw.
    separator = "\u2028"
    event = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "c1",
                "type": "command_execution",
                "command": f"printf 'a{separator}b'",
            },
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert separator in event
    assert len(event.splitlines()) == 2 and len(event.split("\n")) == 1
    log.write_text(event + "\n")
    breaker = Breaker("codex", log, stall_s=None, loop_limit=None, max_tool_calls=None)

    breaker.check()

    assert len(breaker.signatures) == 1


# --- collisions -------------------------------------------------------------


def test_a_quoted_conflict_path_is_unquoted_the_way_a_diff_header_is():
    """`touched_files` unquotes; `_parse_conflicts` did not, so a conflicting
    path and the hotspot naming the same file never matched each other."""
    stdout = 'treeoid\n"caf\\303\\251.txt"\nplain.txt\n'

    assert _parse_conflicts(stdout) == ["café.txt", "plain.txt"]


def test_a_merge_tree_conflict_that_names_no_file_is_an_error_not_a_clean_merge(
    monkeypatch, tmp_path: Path
):
    """Exit 1 is git saying the merge conflicts. Recording `conflicts: []`
    for it read as exactly the opposite."""

    class _Proc:
        returncode = 1
        stdout = "treeoid\n"
        stderr = ""

    monkeypatch.setattr(
        "conductor.collisions.subprocess.run", lambda *a, **kw: _Proc()
    )

    report = merge_conflicts(str(tmp_path), {"a": "sha1", "b": "sha2"})

    entry = report["pairs"][0]
    assert "conflicts" not in entry
    assert entry["error"] == "git merge-tree reported a conflict but named no files"


# --- verify -----------------------------------------------------------------


def test_a_status_that_could_not_be_read_is_not_a_clean_tree(tmp_path: Path):
    """`_manifest("", {})` is the manifest of a clean tree, so two failed
    `git status` calls compared equal and the lane was failed for a no-op it
    never made."""
    before = GitState(is_repo=True, head="a" * 40, branch="main", status_read=False)
    after = GitState(is_repo=True, head="a" * 40, branch="main", status_read=False)

    verdict = compare(str(tmp_path), before, after)

    assert verdict.checked is False
    assert verdict.no_op is False
    assert "git status could not be read" in verdict.notes[0]


def test_two_states_that_were_read_still_compare_as_before(tmp_path: Path):
    before = GitState(is_repo=True, head="a" * 40, branch="main", manifest="m")
    after = GitState(is_repo=True, head="a" * 40, branch="main", manifest="m")

    verdict = compare(str(tmp_path), before, after)

    assert verdict.checked is True
    assert verdict.no_op is True


def test_same_repo_says_no_for_a_path_that_is_not_in_a_repository_at_all(
    tmp_path: Path, repo: Path
):
    """The `a is None or b is None` guard: without it, two non-repositories
    would compare equal and `land` would merge into a stranger's checkout."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    other = tmp_path / "also-not-a-repo"
    other.mkdir()

    assert same_repo(str(outside), str(repo)) is False
    assert same_repo(str(repo), str(outside)) is False
    assert same_repo(str(outside), str(other)) is False
    # And the true case still holds, for the repository and a subdirectory
    # of it, whose `--git-common-dir` answer is relative.
    assert same_repo(str(repo), str(repo)) is True


# --- error kinds ------------------------------------------------------------


def _failed(**overrides) -> object:
    from conductor.runner import Result

    base: dict = dict(
        run_id="r1",
        fleet="cursor",
        model="grok-4.6",
        effort="hard",
        mode="read",
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
        answer_path="/tmp/r1/answer.txt",
        git_verdict={"checked": True, "no_op": True},
    )
    base.update(overrides)
    return Result(**base)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("setup failed: exit 2", "setup"),
        ("setup timed out", "setup"),
        ("taint hooks not enforced: hook missing", "taint"),
        ("settings modified: .claude/settings.json changed", "settings"),
        ("isolation failed: worktree add failed", "refused"),
    ],
)
def test_an_unpriced_run_conductor_stopped_itself_is_not_called_a_cap(error, expected):
    """Every cursor lane is unpriced, and `capped` reads an unpriced run as a
    cap because it cannot be shown to have stayed under one. A lane that
    failed its own setup has its cause on the receipt already, and calling it
    `cap` sent `fallback: [{on: ["cap"]}]` chasing a budget that was never
    the problem."""
    result = _failed(budget={"unpriced": True}, error=error, spawned=False)

    assert result.ok is False
    assert error_kind(result) == expected


def test_an_unpriced_run_with_no_other_cause_is_still_a_cap():
    result = _failed(budget={"unpriced": True}, error="")

    assert error_kind(result) == "cap"


def test_a_connection_refused_in_prose_is_transport_not_a_safety_refusal():
    """`_is_refusal` matches the substring "refus", and the transport table
    carried only `ECONNREFUSED`, so a transient network failure classified as
    a model refusal -- which no retry policy covers."""
    assert any("connection refused" == pattern.lower() for pattern in TRANSPORT_PATTERNS)

    result = _failed(
        fleet="claude",
        fleet_error="Connection refused",
        fleet_status="error_during_execution",
    )

    assert error_kind(result) == "transport"


# --- parsers and the commit gate --------------------------------------------


def test_a_trailing_non_verdict_object_does_not_displace_the_verdict():
    """`_answer_object` kept the last complete JSON object in the answer, so a
    metadata or usage blob printed after the judgment was parsed as the
    judgment. The last VERDICT-SHAPED object wins now.

    A trailing object that is itself verdict-shaped and DIFFERENT is refused
    outright; see the two tests below.
    """
    from conductor.verdicts import Criterion, parse_verdict

    criteria = [Criterion("a", "Is a satisfied?")]
    answer = (
        '{"verdict":"fail","criteria":[{"id":"a","ok":false,"evidence":"x.py:1"}],'
        '"summary":"a is broken"}\n\n'
        "Run metadata:\n"
        '{"tokens": 1200, "elapsed_s": 31.4}\n'
    )

    verdict = parse_verdict(answer, criteria)

    assert verdict.passed is False
    assert verdict.invalid is None
    assert verdict.summary == "a is broken"


def test_two_differing_verdict_objects_are_refused_rather_than_picked_between():
    """A model that answers and then pastes a filled-in example writes two
    verdict-shaped objects. Neither "first wins" nor "last wins" is a rule,
    only a coin flip, so conductor refuses and says why."""
    from conductor.verdicts import Criterion, parse_verdict

    criteria = [Criterion("a", "Is a satisfied?")]
    answer = (
        '{"verdict":"fail","criteria":[{"id":"a","ok":false,"evidence":"x.py:1"}],'
        '"summary":"a is broken"}\n\n'
        "For reference, a passing answer looks like:\n"
        '{"verdict":"pass","criteria":[{"id":"a","ok":true,"evidence":"x.py:1"}],'
        '"summary":"all good"}\n'
    )

    verdict = parse_verdict(answer, criteria)

    assert verdict.invalid == (
        "the answer carries 2 different verdict objects; "
        "conductor cannot tell which is the judgment"
    )
    assert verdict.passed is False


def test_an_answer_that_repeats_one_identical_verdict_object_still_parses():
    """The refusal is about disagreement, not repetition: a model that echoes
    its own answer verbatim has said one thing, and a paid judgment is not
    thrown away for it."""
    from conductor.verdicts import Criterion, parse_verdict

    criteria = [Criterion("a", "Is a satisfied?")]
    one = (
        '{"verdict":"pass","criteria":[{"id":"a","ok":true,"evidence":"x.py:1"}],'
        '"summary":"a holds"}'
    )
    answer = f"Here is my verdict:\n{one}\n\nRestating it:\n{one}\n"

    verdict = parse_verdict(answer, criteria)

    assert verdict.invalid is None
    assert verdict.passed is True
    assert verdict.summary == "a holds"


def test_the_checklist_contract_forbids_a_second_copy_of_the_answer():
    """The refusal above only helps if the prompt asked for one object."""
    from conductor.verdicts import Criterion, checklist_contract

    contract = checklist_contract([Criterion("a", "Is a satisfied?")])

    assert "no second copy of the object" in contract


def test_an_answer_whose_only_object_is_malformed_is_still_reported_malformed():
    from conductor.verdicts import Criterion, parse_verdict

    verdict = parse_verdict('here you go: {"verdict": "pass"}', [Criterion("a", "?")])

    assert verdict.passed is False
    assert verdict.invalid is not None


@pytest.mark.parametrize("value", [-1, -0.0001])
def test_a_negative_token_count_is_not_a_token_count(value):
    """A negative `cache_read_tokens` is subtracted from input on the cursor
    and antigravity paths, which turns it into extra billed input."""
    from conductor.outputs import usable_int

    assert usable_int(value) is None


def test_a_cut_short_stream_is_not_committed(tmp_path: Path):
    """D15's other half: a truncated antigravity turn says so with a status,
    not an `error`, because no fleet reported it. The commit gate read only
    `output.error`, so a write that exited 0, moved bytes, and passed its
    gate was committed under a receipt saying the run failed."""
    from conductor.outputs import INCOMPLETE, _parse_antigravity

    text = json.dumps({"event": "step_update", "step_update": {"step_index": 1}})
    out = _parse_antigravity(text)

    assert out.parsed is True
    assert out.status == INCOMPLETE
    assert out.error is None

    # And the gate that reads it. Recomputing the condition here would pin a
    # copy of it rather than the one that runs, so this reads dispatch's own
    # source -- the same trick `test_error_kind_return_order_matches_kinds`
    # uses for the kind order.
    import inspect

    from conductor.runner import dispatch

    source = inspect.getsource(dispatch)
    gate = source.split("commit: CommitOutcome | None = None", 1)[1].split("\n        ):", 1)[0]
    assert "not output.error" in gate
    assert "output.status != INCOMPLETE" in gate


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def test_a_self_commit_does_not_survive_a_run_that_failed_before_the_gate(
    repo, home, fake_fleet
):
    """The commit gate stopped conductor from committing a failed run's work,
    but the self-commit adoption right after it ran unconditionally, so a
    fleet that committed its own bytes and then exited non-zero landed them
    anyway. The undo blocks below it do not catch this: `_gate_passed` reads
    a gate that never ran as nothing to fail, so the receipt said the run
    failed while carrying a real committed sha, and `worktrees.release` keeps
    the branch. Same class as the D15 truncated-stream case.
    """
    from conductor.fleets import Spec
    from conductor.runner import dispatch

    head_before = _head(repo)
    fake_fleet(
        ["sh", "-c", "echo work > new.txt && git add -A && git commit -qm 'agent work' && exit 1"]
    )

    result = dispatch(
        Spec(fleet="claude", prompt="p", cwd=str(repo), mode="write"),
        home=home,
        commit_message="feat: x",
    )

    assert result.ok is False
    assert result.failure() == "exit code 1"
    assert result.commit["committed"] is False
    assert "the run failed before the gate" in result.commit["reason"]
    # The branch is back where it started; the work is still in the tree.
    assert _head(repo) == head_before
    assert (repo / "new.txt").read_text() == "work\n"


def _cost_envelope(cost: float) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "usage": {"inputTokens": 10, "outputTokens": 5},
            "total_cost_usd": cost,
        },
        separators=(",", ":"),
    )


@pytest.mark.parametrize(
    ("cost", "why"),
    [(9.99, "over budget"), (None, "cap unenforced")],
)
def test_a_run_that_failed_its_cap_does_not_keep_its_commit(repo, home, fake_fleet, cost, why):
    """`budget.settle` is the cap verdict, and it runs AFTER the commit
    decision. The wait loop re-checks the breaker with `final=True` for
    exactly this reason -- a runaway must not evade the ceiling by exiting in
    the same poll tick -- but the watcher has no such re-check, and a cursor
    lane has no in-run watcher at all. So a run that crossed its cap was
    committed and only then receipted as over budget: the branch and the
    verdict disagreeing about the same run.

    `unpriced` is the same case. `Result.failure` fails it closed as an
    unenforced cap, and it kept its commit too.
    """
    from conductor.fleets import Spec
    from conductor.runner import dispatch

    head_before = _head(repo)
    body = "echo work > new.txt"
    if cost is not None:
        body += f"; printf '%s\\n' {shlex.quote(_cost_envelope(cost))}"
    fake_fleet(["sh", "-c", body])

    result = dispatch(
        Spec(fleet="claude", prompt="p", cwd=str(repo), mode="write", cap_usd=0.10),
        home=home,
        commit_message="feat: x",
    )

    assert result.ok is False
    assert result.commit["committed"] is False
    assert result.commit["reason"].startswith(why)
    assert _head(repo) == head_before
    # Undone, not discarded: a kept worktree still holds the work for salvage.
    assert (repo / "new.txt").read_text() == "work\n"


def test_run_tests_reap_after_kill_is_bounded(tmp_path, monkeypatch):
    """`run_tests` did the unbounded `proc.wait()` after `_kill_live_group`
    that `_wait` was hardened against. A gate or teardown whose process
    cannot be reaped then hung `dispatch` with no timeout and no stop-flag
    escape. A process that still does not exit leaves `returncode` None;
    the timeout path already receipts that as no clean exit (`timed_out`,
    `exit_code` unset)."""
    from conductor import runner as runner_mod

    waits: list[float | None] = []
    real_wait = subprocess.Popen.wait

    def tracked_wait(self, timeout=None):
        waits.append(timeout)
        if timeout is None:
            raise AssertionError("unbounded proc.wait() after kill")
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", tracked_wait)
    monkeypatch.setattr(runner_mod, "KILL_WAIT_S", 0.05)
    outcome = run_tests(str(tmp_path), "sleep 60", timeout=0)
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert waits
    assert waits[-1] == 0.05
