"""E23: `conductor salvage` -- the lead's by-hand gate step, as data.

AGENTS.md rule 6's salvage path is a kept worktree (its own gate green, the
clean gate rejecting it -- a flaky test under load, or the base tree's tests
failing against new source) that the lead currently reads, gates, and commits
by hand, then writes a review-and-fix mission for by hand too. This module is
that path made repeatable: `salvage()` re-runs the clean gate from the kept
worktree in a scratch copy (never touching the kept worktree itself), and
`emit()` writes the follow-on mission (`shape.shape_a_followon`) once the lead
has committed what `salvage()` showed them.

Every call is receipted under `<home>/missions/<mission_id>/salvage/`, refused
or not: salvage is the lead's own act, not a lane's, so it never extends the
mission's signed receipt chain.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import attest, shape
from .fleets import DEFAULT_TIMEOUT
from .mission import LaneResult, Mission, MissionInvalid, mission_from_dict
from .runner import _clean_gate
from .verify import diff_since, git_run


class SalvageInvalid(ValueError):
    """A salvage request that cannot proceed as asked."""


@dataclass
class SalvageResult:
    mission: str
    lane: str
    worktree: str
    base_sha: str
    head_sha: str
    dirty: bool
    diff: str
    diff_sha256: str | None
    test_command: str
    gate: dict = field(default_factory=dict)
    # Set after the receipt is written; not itself part of the receipt (the
    # timestamp in its name is not known before the write happens).
    receipt_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _git_common_dir(cwd: Path | str) -> Path | None:
    result = git_run(cwd, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else Path(cwd).resolve() / path


def _same_repo(worktree: Path, repo: str) -> bool:
    a = _git_common_dir(worktree)
    b = _git_common_dir(repo)
    if a is None or b is None:
        return False
    return a.resolve() == b.resolve()


def _write_receipt(home: Path, mission_id: str, lane: str, payload: dict) -> Path:
    salvage_dir = home / "missions" / mission_id / "salvage"
    salvage_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision, not `claim_dir`'s second-granularity stamp: two
    # salvage calls for the same lane within one second (a quick re-run after
    # a fix) must not silently overwrite each other's receipt.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = salvage_dir / f"{lane}-{stamp}.json"
    doc = {
        **payload,
        "mission": mission_id,
        "lane": lane,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(doc, indent=2))
    return path


def _load_json(path: Path, *, what: str) -> dict:
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise SalvageInvalid(f"{what} is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SalvageInvalid(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SalvageInvalid(f"{what} must be a JSON object")
    return raw


def _lane_snapshot(mission_raw: dict, lane: str) -> dict | None:
    lanes = mission_raw.get("lanes")
    if not isinstance(lanes, list):
        return None
    for entry in lanes:
        if isinstance(entry, dict) and entry.get("name") == lane:
            return entry
    return None


def _gather(
    home: Path, mission_id: str, lane: str, *, stop: Callable[[], bool] | None
) -> SalvageResult:
    mission_dir = home / "missions" / mission_id
    if not mission_dir.is_dir():
        raise SalvageInvalid(f"mission '{mission_id}' does not exist")
    lane_path = mission_dir / "lanes" / f"{lane}.json"
    if not lane_path.is_file():
        raise SalvageInvalid(f"lane '{lane}' does not exist in mission '{mission_id}'")
    lane_raw = _load_json(lane_path, what=f"lane '{lane}' receipt")
    try:
        lane_result = LaneResult.from_dict(lane_raw)
    except ValueError as exc:
        raise SalvageInvalid(f"lane '{lane}' receipt is invalid: {exc}") from exc

    last_attempt = lane_result.attempts[-1] if lane_result.attempts else None
    worktree_str = last_attempt.get("worktree") if last_attempt else None
    if not worktree_str:
        raise SalvageInvalid(f"lane '{lane}' was not kept")
    worktree = Path(worktree_str)
    if not worktree.is_dir():
        raise SalvageInvalid(f"lane '{lane}' kept worktree is missing on disk: {worktree}")

    mission_raw = _load_json(mission_dir / "mission.json", what=f"mission '{mission_id}' snapshot")
    repo = mission_raw.get("cwd")
    if not isinstance(repo, str) or not repo:
        raise SalvageInvalid(f"mission '{mission_id}' snapshot has no cwd")
    if not _same_repo(worktree, repo):
        raise SalvageInvalid(
            f"lane '{lane}' kept worktree is not a git worktree of {repo}"
        )

    lane_snapshot = _lane_snapshot(mission_raw, lane)
    attempt_snapshot = (
        lane_snapshot.get("attempts", [None])[0]
        if lane_snapshot and lane_snapshot.get("attempts")
        else None
    )
    test_command = attempt_snapshot.get("test") if attempt_snapshot else None
    if not test_command:
        raise SalvageInvalid(f"lane '{lane}' has no test command to salvage")
    timeout = (attempt_snapshot.get("timeout") if attempt_snapshot else None) or DEFAULT_TIMEOUT[
        "write"
    ]

    base_sha = lane_result.base_sha
    if not base_sha:
        raise SalvageInvalid(f"lane '{lane}' has no base commit to salvage from")

    test_surface = last_attempt.get("test_surface") or {}
    patterns = test_surface.get("patterns") or []

    diff_text = ""
    diff_sha256: str | None = None
    if lane_result.diff_path:
        diff_path = Path(lane_result.diff_path)
        try:
            diff_text = diff_path.read_text()
        except OSError:
            diff_text = ""
        diff_sha256 = attest.file_sha256(diff_path)

    status = git_run(worktree, "status", "--porcelain")
    dirty = status.returncode != 0 or bool(status.stdout.strip())
    head_sha = git_run(worktree, "rev-parse", "HEAD").stdout.strip()

    scratch = home / "salvage" / mission_id / lane
    outcome = _clean_gate(
        str(worktree),
        base_sha=base_sha,
        patterns=list(patterns),
        command=test_command,
        timeout=int(timeout),
        stop=stop or (lambda: False),
        worktree=scratch,
    )

    return SalvageResult(
        mission=mission_id,
        lane=lane,
        worktree=str(worktree),
        base_sha=base_sha,
        head_sha=head_sha,
        dirty=dirty,
        diff=diff_text,
        diff_sha256=diff_sha256,
        test_command=test_command,
        gate=outcome,
    )


def salvage(
    home: Path, mission_id: str, lane: str, *, stop: Callable[[], bool] | None = None
) -> SalvageResult:
    """Gate a kept lane's worktree from a scratch copy, and receipt the result.

    Never commits, never writes into the kept worktree, and never touches its
    index -- `runner._clean_gate` already keeps that promise for its own
    scratch worktree; this only ever points it at the kept one as a source.
    """
    home = Path(home)
    try:
        result = _gather(home, mission_id, lane, stop=stop)
    except SalvageInvalid as exc:
        # A mission that does not exist has nowhere to hold a receipt: writing
        # one would create `missions/<typo>/salvage/` out of thin air, which
        # `conductor missions` and `gc` would then list as a mission.
        if (home / "missions" / mission_id).is_dir():
            _write_receipt(home, mission_id, lane, {"refused": str(exc)})
        raise
    path = _write_receipt(home, mission_id, lane, result.to_dict())
    result.receipt_path = str(path)
    return result


def emit(
    result: SalvageResult,
    out: Path,
    *,
    test: str,
    caps: shape.CapArithmetic,
    name: str,
    branch: str = "",
    fix_commit: str = "",
    about: str | None = None,
    spec_prompt: str = "",
) -> dict:
    """Write the follow-on review-and-fix mission for a salvage the lead has
    already committed. `spec_prompt` is the original mission's prompt (the
    spec), read from its snapshot by the caller, so the reviewers judge the
    salvage against the text the build was given.

    Refused (never writes `out`) when the kept worktree's HEAD still equals
    `base_sha` (nothing has been committed there yet) or the worktree is
    dirty (a commit is in progress, or the lead has not finished): either way
    the follow-on would start reviewing something the lead never actually
    landed.
    """
    worktree = Path(result.worktree)
    status = git_run(worktree, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        raise SalvageInvalid(
            "the kept worktree is dirty; commit the salvage before emitting a follow-on mission"
        )
    head_sha = git_run(worktree, "rev-parse", "HEAD").stdout.strip()
    if not head_sha or head_sha == result.base_sha:
        raise SalvageInvalid(
            "the kept worktree's HEAD still equals base_sha; commit the salvage before "
            "emitting a follow-on mission"
        )
    diff = diff_since(str(worktree), result.base_sha)
    out = Path(out).expanduser().resolve()
    mission_dict = shape.shape_a_followon(
        worktree=worktree,
        salvage_sha=head_sha,
        diff=diff,
        test=test,
        caps=caps,
        name=name,
        about=about,
        branch=branch,
        fix_commit=fix_commit,
        mission_dir=out.parent,
        spec_prompt=spec_prompt,
        base_sha=result.base_sha,
    )
    try:
        mission: Mission = mission_from_dict(mission_dict, base_dir=out.parent, source=str(out))
    except MissionInvalid as exc:
        raise SalvageInvalid(f"follow-on mission is invalid: {exc}") from exc
    del mission  # validated only; the raw dict is what gets written
    out.write_text(json.dumps(mission_dict, indent=2) + "\n")
    return mission_dict
