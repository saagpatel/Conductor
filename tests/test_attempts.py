"""F21 slice 3: the attempt lifecycle extracted into attempts.py pins two
invariants -- every paid dispatch is counted once across a retry, and a
rehearsal is never trusted on resume."""

from __future__ import annotations

import ast
import json
import shlex
from dataclasses import replace
from pathlib import Path

from conductor import attempts as attempts_mod
from conductor import mission as mission_mod
from conductor import runner as runner_mod
from conductor.mission import (
    LaneResult,
    Mission,
    _cancel_winner,
    _keep_cancelled_lanes,
    _trusted_lane,
    mission_from_dict,
    run_mission,
)


def _transport_envelope(cost: float) -> str:
    return (
        '{"type":"result","subtype":"error_during_execution","is_error":true,'
        '"result":"","error":"ECONNRESET while streaming",'
        f'"usage":{{"inputTokens":3,"outputTokens":1}},"total_cost_usd":{cost}}}'
    )


def _ok_envelope(cost: float) -> str:
    return (
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        f'"usage":{{"inputTokens":10,"outputTokens":5}},"total_cost_usd":{cost}}}'
    )


def _flaky_script(
    counter: Path, fail_costs: list[float], ok_cost: float, *, rerun_cost: float | None = None
) -> str:
    """Fails with a transport-shaped error the first `len(fail_costs)` calls,
    each at its own distinct reported cost (tracked in a counter file so
    every dispatch of the same argv can tell which try it is), then succeeds
    at `ok_cost` -- the same shape test_errors.py's retry tests already use,
    with a nonzero, distinct cost on every failing attempt too (Opus review:
    a transport failure can still land after tokens were spent, and the
    'priced once' invariant is untested if only the surviving attempt ever
    carries a cost). Any call past the first success (a redispatch on a
    forced resume) reports `rerun_cost` instead, defaulting to `ok_cost`."""
    branches = []
    for n, cost in enumerate(fail_costs, start=1):
        branches.append(
            f'if [ "$n" -eq {n} ]; then\n'
            f"  printf '%s\\n' {shlex.quote(_transport_envelope(cost))}\n"
            "  exit 1\n"
            "fi"
        )
    body = "\n".join(branches)
    first_ok_n = len(fail_costs) + 1
    rerun = ok_cost if rerun_cost is None else rerun_cost
    return f"""
n=$(cat {counter})
n=$((n + 1))
echo $n > {counter}
{body}
if [ "$n" -eq {first_ok_n} ]; then
  printf '%s\\n' {shlex.quote(_ok_envelope(ok_cost))}
  exit 0
fi
printf '%s\\n' {shlex.quote(_ok_envelope(rerun))}
exit 0
"""


def test_mission_reexports_every_name_moved_to_attempts():
    """Cross-vendor review (Grok, Opus) of f21-attempts: item 4 requires
    `conductor.mission` to keep resolving every moved name; six constants
    (`_INHERITED`, `_BREAKER_KEYS`, `_FALLBACK_KEYS`, `_HUMAN_ATTEMPT_ALLOWED`,
    `_SCRIPT_ATTEMPT_DENIED`, `_DEFAULT_RETRY_KINDS`) were left out of the
    re-export block. A caller doing `from conductor.mission import
    _INHERITED` (as an old caller of the pre-extraction module could) must
    not see an `ImportError`."""
    for name in (
        "_INHERITED",
        "_BREAKER_KEYS",
        "_FALLBACK_KEYS",
        "_HUMAN_ATTEMPT_ALLOWED",
        "_SCRIPT_ATTEMPT_DENIED",
        "_DEFAULT_RETRY_KINDS",
    ):
        assert hasattr(mission_mod, name), f"conductor.mission.{name} no longer resolves"
        assert getattr(mission_mod, name) is getattr(attempts_mod, name)


def test_retry_dispatches_are_counted_once_and_a_resume_never_repays(
    repo, home, monkeypatch, tmp_path
):
    """(a) Expected to hold on the code before this extraction too: a lane's
    two retries and its eventual success are three attempts of one lane, each
    priced from its own run receipt exactly once (never doubled by the retry
    loop, never doubled again by a resume). Every attempt, including the two
    that failed, reports its own distinct nonzero cost, so the sum only adds
    up if each run receipt is read once and none is skipped or double
    counted. Resuming the already-finished mission trusts the lane's receipt
    outright (`_trusted_lane`'s ordinary path, not a rerun): its three
    attempts stay exactly where they were, `previous_attempts` empty, and the
    mission's `cost_usd` is unchanged -- the lane was never asked to fold its
    history, because it was never asked to run again.
    """
    counter = tmp_path / "count"
    counter.write_text("0")
    fail_costs = [0.01, 0.02]
    ok_cost = 0.05
    script = _flaky_script(counter, fail_costs=fail_costs, ok_cost=ok_cost)
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", script])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "retry": {"kinds": ["transport"], "attempts": 2, "backoff_s": 0},
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True
    lane = first.lanes[0]
    assert len(lane["attempts"]) == 3
    assert lane["kinds"] == ["transport", "transport", None]

    receipt_costs = []
    for attempt in lane["attempts"]:
        receipt = json.loads((home / "runs" / attempt["run_id"] / "result.json").read_text())
        usage = receipt.get("usage") or {}
        receipt_costs.append(usage.get("cost_usd"))
    assert receipt_costs == [*fail_costs, ok_cost]
    assert sum(receipt_costs) == first.cost_usd == lane["cost_usd"]

    snapshot = json.loads(Path(first.mission_dir, "mission.json").read_text())
    resumed = run_mission(
        Mission.from_snapshot(snapshot), home=home, resume_dir=Path(first.mission_dir)
    )
    assert resumed.ok is True
    assert resumed.resumed_from["kept"] == ["a"]
    assert resumed.resumed_from["rerun"] == []
    rlane = resumed.lanes[0]
    assert len(rlane["attempts"]) == 3
    assert rlane["previous_attempts"] == []
    assert resumed.cost_usd == first.cost_usd


def test_retry_history_folds_into_previous_attempts_on_a_forced_rerun_without_repaying(
    repo, home, monkeypatch, tmp_path
):
    """(a) Cross-vendor review (Grok, Opus): the spec's literal wording --
    resuming 'moves all three under previous_attempts' -- only ever happens
    when the lane is actually forced to rerun; a fully trusted, kept lane
    (the test above) never folds, because `fresh_lane_result` only folds a
    lane's attempts into `previous_attempts` when it is about to redispatch
    it. Tamper the answer bytes so `_trusted_lane` refuses the receipt and
    the lane must rerun: the three original attempts, run ids intact, move
    under `previous_attempts`; the fresh redispatch is priced at zero, so
    the mission's `cost_usd` stays literally unchanged, which only holds if
    `_run_receipt_spend`'s dedupe by run id prices the three original
    attempts once from their own receipts rather than re-summing them on
    top of the redispatch.
    """
    counter = tmp_path / "count"
    counter.write_text("0")
    fail_costs = [0.01, 0.02]
    ok_cost = 0.05
    script = _flaky_script(counter, fail_costs=fail_costs, ok_cost=ok_cost, rerun_cost=0.0)
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", script])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "retry": {"kinds": ["transport"], "attempts": 2, "backoff_s": 0},
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True
    original_run_ids = [a["run_id"] for a in first.lanes[0]["attempts"]]
    assert len(original_run_ids) == 3

    answer_path = Path(first.mission_dir) / "answers" / "a.txt"
    answer_path.write_text("tampered, not what the lane wrote\n")

    snapshot = json.loads(Path(first.mission_dir, "mission.json").read_text())
    resumed = run_mission(
        Mission.from_snapshot(snapshot), home=home, resume_dir=Path(first.mission_dir)
    )
    assert resumed.ok is True
    assert resumed.resumed_from["rerun"] == ["a"]
    rlane = resumed.lanes[0]
    assert [a["run_id"] for a in rlane["previous_attempts"]] == original_run_ids
    assert len(rlane["attempts"]) == 1
    assert resumed.cost_usd == first.cost_usd


def test_trusted_lane_refuses_an_unspawned_rehearsal_attempt(repo, home, monkeypatch, tmp_path):
    """(b) Expected to hold on the code before this extraction too: an ok
    receipt whose last attempt was never actually spawned (a dry-run
    rehearsal's shape) is never trusted -- trusting it would turn the next
    real resume into another rehearsal that dispatches nothing."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", f"printf '%s\\n' {shlex.quote(_ok_envelope(0.1))}"],
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True
    mission_dir = Path(result.mission_dir)
    lane = mission.lanes[0]
    receipt = LaneResult.from_dict(json.loads((mission_dir / "lanes" / "a.json").read_text()))
    assert _trusted_lane(mission, mission_dir, lane, receipt) is True

    rehearsed = replace(receipt)
    rehearsed.attempts = [{**receipt.attempts[-1], "spawned": False}]
    assert _trusted_lane(mission, mission_dir, lane, rehearsed) is False


def test_trusted_lane_refuses_a_receipt_whose_artifact_digest_no_longer_matches(
    repo, home, monkeypatch, tmp_path
):
    """(b) Expected to hold on the code before this extraction too: an ok
    receipt is never trusted once the bytes on disk no longer match the
    digest it recorded -- the file being in the right place is not enough,
    the bytes inside it must be what the receipt actually saw."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", f"printf '%s\\n' {shlex.quote(_ok_envelope(0.1))}"],
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True
    mission_dir = Path(result.mission_dir)
    lane = mission.lanes[0]
    receipt = LaneResult.from_dict(json.loads((mission_dir / "lanes" / "a.json").read_text()))
    assert _trusted_lane(mission, mission_dir, lane, receipt) is True

    (mission_dir / "answers" / "a.txt").write_text("tampered, not what the lane wrote\n")
    assert _trusted_lane(mission, mission_dir, lane, receipt) is False


class _MissionImportVisitor(ast.NodeVisitor):
    """Finds every `from .mission import ...`, `from . import mission[ as
    x]`, `from conductor.mission import ...`, or `import conductor.mission`
    that is not inside a function body or a `TYPE_CHECKING` block -- every
    spelling that names `conductor.mission` and would reintroduce the
    module cycle if hoisted to module scope. Copied from
    tests/test_approvals.py's guard (widened one commit ago, 0a1d14f, after
    a `tree.body`-only walk missed an import nested one level down -- Opus
    review of f21-attempts found the same hole here)."""

    def __init__(self) -> None:
        self.violations: list[ast.AST] = []
        self._function_depth = 0
        self._type_checking_depth = 0

    def _guarded(self) -> bool:
        return self._function_depth > 0 or self._type_checking_depth > 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        if not is_type_checking:
            self.generic_visit(node)
            return
        self._type_checking_depth += 1
        for child in node.body:
            self.visit(child)
        self._type_checking_depth -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._guarded():
            return
        if node.level == 1 and node.module == "mission":
            self.violations.append(node)
        elif node.level == 1 and node.module is None and any(
            alias.name == "mission" for alias in node.names
        ):
            self.violations.append(node)
        elif node.level == 0 and node.module == "conductor.mission":
            self.violations.append(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._guarded():
            return
        for alias in node.names:
            if alias.name in ("conductor.mission", "mission"):
                self.violations.append(node)


def test_attempts_module_has_no_module_level_import_of_mission():
    """Structural (c): mission.py re-exports every name attempts.py defines,
    so a module-level import back from here would be a cycle -- every
    reach into mission.py's own state is a lazy `from . import mission as
    mission_mod` inside the function that needs it."""
    source = Path(attempts_mod.__file__).read_text()
    tree = ast.parse(source, filename="attempts.py")
    visitor = _MissionImportVisitor()
    visitor.visit(tree)
    assert visitor.violations == []


def test_the_import_cycle_guard_catches_an_import_nested_under_a_plain_if():
    """Opus review of f21-attempts: the guard's predecessor here walked only
    `tree.body`, so an import that still executes at module load time but
    sits one level down (an `if` that is not `TYPE_CHECKING`, for instance)
    passed silently. `visit_If` only special-cases `TYPE_CHECKING`; every
    other `if` is walked like any other statement."""
    poisoned = (
        "from __future__ import annotations\n"
        "if True:\n"
        "    from . import mission as mission_mod\n"
    )
    visitor = _MissionImportVisitor()
    visitor.visit(ast.parse(poisoned, filename="poisoned.py"))
    assert len(visitor.violations) == 1


def test_the_import_cycle_guard_catches_the_relative_module_spelling():
    """`from . import mission as mission_mod` hoisted to module scope must be
    rejected exactly as `from .mission import ...` is -- both name the same
    module, and this is the exact spelling attempts.py's own lazy imports
    use inside function bodies (`ast` represents it as `ImportFrom` with
    `module is None`, not `"mission"`)."""
    poisoned = (
        "from __future__ import annotations\n"
        "from . import mission as mission_mod\n"
        "def f():\n"
        "    pass\n"
    )
    visitor = _MissionImportVisitor()
    visitor.visit(ast.parse(poisoned, filename="poisoned.py"))
    assert len(visitor.violations) == 1


def test_the_import_cycle_guard_catches_the_absolute_spelling():
    """Same regression, absolute form: `from conductor.mission import ...`
    names the same module as `from .mission import ...` but is a level-0
    import, which a level-1-only check would miss."""
    poisoned = "from conductor.mission import load_mission\n"
    visitor = _MissionImportVisitor()
    visitor.visit(ast.parse(poisoned, filename="poisoned.py"))
    assert len(visitor.violations) == 1


def test_trusted_lane_keeps_a_lane_cancelled_before_it_ever_spawned(
    repo, home, monkeypatch, tmp_path
):
    """What settles a cancelled lane is the winner that beat it, not the
    lane's own receipt and not whether the prior mission finished ok.

    `_trusted_lane` cannot answer this: the winner may sit later in
    `mission.lanes` than the lane it cancelled, so `kept` is still being
    built when the cancelled lane is judged. It therefore returns False for
    every cancelled lane and `_keep_cancelled_lanes` decides afterwards.

    The rule this replaced was `and prior_ok`, which read as conservative
    and was not: an interrupt forces `ok = False`, and SIGINT-then-resume is
    the documented flow (AGENTS.md rule 8), which promises finished lanes
    are not paid twice. Under that rule every cancelled lane was
    re-dispatched on exactly the resume it exists to serve.
    """
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", f"printf '%s\\n' {shlex.quote(_ok_envelope(0.1))}"],
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    mission_dir = Path(result.mission_dir)
    lane = mission.lanes[0]
    receipt = LaneResult.from_dict(json.loads((mission_dir / "lanes" / "a.json").read_text()))

    mid_run = replace(receipt)
    mid_run.skipped = "cancelled: lane b already passed"
    pre_spawn = replace(receipt)
    pre_spawn.skipped = "cancelled before spawn: lane b already passed"
    other = replace(receipt)
    other.skipped = "paused: pause.before not answered; not started"

    # No skipped lane is trusted on its own receipt any more.
    for skipped in (mid_run, pre_spawn, other):
        assert _trusted_lane(mission, mission_dir, lane, skipped) is False

    # Both cancel spellings name their winner, and nothing else does.
    assert _cancel_winner(mid_run.skipped) == "b"
    assert _cancel_winner(pre_spawn.skipped) == "b"
    assert _cancel_winner(other.skipped) is None
    assert _cancel_winner("cancelled: another lane already passed") is None

    # The second pass keeps a cancelled lane when its winner is kept, and
    # only then. `b` is not a lane of this one-lane mission, so it is named
    # in `kept` directly -- what the pass reads is the kept set, not the graph.
    for cancelled in (mid_run, pre_spawn):
        kept: dict[str, LaneResult] = {"b": replace(receipt)}
        rerun = {lane.name}
        notes: list[str] = []
        _keep_cancelled_lanes(mission, {lane.name: cancelled}, kept, rerun, notes)
        assert lane.name in kept and not rerun
        assert kept[lane.name].kept is True
        assert notes == [f"lane '{lane.name}' stays cancelled: 'b' is kept"]

        # Winner being rerun reopens the decision, so the loser reruns too.
        kept = {}
        rerun = {lane.name, "b"}
        _keep_cancelled_lanes(mission, {lane.name: cancelled}, kept, rerun, [])
        assert lane.name in rerun and lane.name not in kept

    # A skip that is not a cancellation is still unfinished work.
    kept = {"b": replace(receipt)}
    rerun = {lane.name}
    _keep_cancelled_lanes(mission, {lane.name: other}, kept, rerun, [])
    assert lane.name in rerun and lane.name not in kept
