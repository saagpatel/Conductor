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

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import attest, shape
from .fleets import DEFAULT_TIMEOUT
from .mission import LaneResult, Mission, MissionInvalid, mission_from_dict
from .runner import _clean_gate, _transplant_gate
from .verify import TestOutcome, diff_since, git_run
from .verify import same_repo as _same_repo


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
    # The kept tree's own gate: the same transplant with everything included,
    # test surface and all. The clean gate above restores the test surface
    # from the base, so it never lints or runs a kept worktree's new tests;
    # E17's salvage lost a review round to one long line for exactly that.
    own_gate: dict = field(default_factory=dict)
    # D17: the digest the run's own receipt carried for this lane's diff --
    # lineage, not evidence. `diff_sha256` above hashes the bytes this
    # salvage actually gated; when they differ, the kept worktree was
    # repaired after the run and the run's digest names a different tree.
    lineage_diff_sha256: str | None = None
    # Set after the receipt is written; not itself part of the receipt (the
    # timestamp in its name is not known before the write happens).
    receipt_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


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


def _text_sha256(text: str) -> str:
    """The digest of a diff held in memory, comparable with the digest
    `attest.file_sha256` takes of the same patch written to disk (both hash
    the UTF-8 bytes `Path.write_text` would have stored)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _matching_attempt(lane_snapshot: dict | None, attempt: dict, position: int) -> dict | None:
    """The declared attempt that produced `attempt`, the lane receipt's last
    dispatch (D16).

    The receipt's attempts are dispatches, the snapshot's are declarations:
    a retry repeats one declaration, so the two lists are not the same length
    and `attempts[0]` is not the attempt whose worktree salvage gates. Match
    on the dispatch contract (fleet, model, effort, mode), preferring the
    declaration at the same position when it also matches, and fall back to
    position alone when nothing matches (an older receipt, a hand-edited
    snapshot) rather than silently gating under the primary's contract.
    """
    raw = lane_snapshot.get("attempts") if lane_snapshot else None
    if not isinstance(raw, list):
        return None
    declared = [entry for entry in raw if isinstance(entry, dict)]
    if not declared:
        return None
    keys = ("fleet", "model", "effort", "mode")
    matches = [
        index
        for index, entry in enumerate(declared)
        if all(entry.get(key) == attempt.get(key) for key in keys)
    ]
    if position in matches:
        return declared[position]
    if matches:
        return declared[matches[-1]]
    if 0 <= position < len(declared):
        return declared[position]
    return declared[-1]


def _unreconstructable(attempt_snapshot: dict) -> list[str]:
    """What the producing attempt declared that a salvage gate cannot rebuild.

    `_transplant_gate` runs the command with no lane environment: no ports
    claimed and exported, no `setup` run first, no `include` copied in. A
    gate missing any of those is not the contract the lane ran under, so
    salvage refuses instead of reporting a verdict from a different one.
    """
    missing: list[str] = []
    if attempt_snapshot.get("setup"):
        missing.append("setup")
    if attempt_snapshot.get("include"):
        missing.append("includes")
    if attempt_snapshot.get("ports"):
        missing.append("ports")
    if attempt_snapshot.get("teardown"):
        missing.append("env")
    return missing


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
    # D16: a lane (or one of its attempts) may declare its own cwd, and the
    # attempt that ran is the one whose worktree is being gated -- so the
    # repository this worktree must belong to is the lane's effective cwd,
    # as `land.py` already reads it, with the mission's own cwd only as the
    # default for a lane that never overrode it.
    repo = lane_result.cwd or mission_raw.get("cwd")
    if not isinstance(repo, str) or not repo:
        raise SalvageInvalid(f"mission '{mission_id}' snapshot has no cwd")
    if not _same_repo(worktree, repo):
        raise SalvageInvalid(
            f"lane '{lane}' kept worktree is not a git worktree of {repo}"
        )

    lane_snapshot = _lane_snapshot(mission_raw, lane)
    attempt_snapshot = _matching_attempt(
        lane_snapshot, last_attempt, len(lane_result.attempts) - 1
    )
    test_command = attempt_snapshot.get("test") if attempt_snapshot else None
    if not test_command:
        raise SalvageInvalid(f"lane '{lane}' has no test command to salvage")
    missing = _unreconstructable(attempt_snapshot) if attempt_snapshot else []
    if missing:
        raise SalvageInvalid(
            f"salvage cannot reconstruct {', '.join(missing)} for lane {lane}; "
            "gate the kept worktree by hand"
        )
    timeout = (attempt_snapshot.get("timeout") if attempt_snapshot else None) or DEFAULT_TIMEOUT[
        "write"
    ]

    base_sha = lane_result.base_sha
    if not base_sha:
        raise SalvageInvalid(f"lane '{lane}' has no base commit to salvage from")

    test_surface = last_attempt.get("test_surface") or {}
    patterns = test_surface.get("patterns") or []

    # D17: the diff the receipt records is what the fleet left at run time;
    # what the gates below judge is what the kept worktree holds now. A
    # worktree repaired by hand after the run would otherwise pass here with
    # the pre-repair digest attached. Snapshot the current bytes once, hash
    # those, and keep the run's own digest as lineage only.
    diff_text = diff_since(str(worktree), base_sha)
    diff_sha256 = _text_sha256(diff_text)
    lineage_diff_sha256 = (
        attest.file_sha256(Path(lane_result.diff_path)) if lane_result.diff_path else None
    )

    status = git_run(worktree, "status", "--porcelain")
    dirty = status.returncode != 0 or bool(status.stdout.strip())
    head_sha = git_run(worktree, "rev-parse", "HEAD").stdout.strip()

    scratch = home / "salvage" / mission_id / lane
    own_outcome = _transplant_gate(
        str(worktree),
        base_sha=base_sha,
        pathspecs=["."],
        command=test_command,
        timeout=int(timeout),
        stop=stop or (lambda: False),
        worktree=scratch.with_name(f"{lane}-own"),
    )
    if test_surface.get("policy") == "allow":
        # Rule 3: the lane ran under `test_policy: allow`, so the harness
        # would not have judged it on the clean gate either -- a spec that
        # changes what existing missions may do fails that gate by design
        # (the base tree's tests against the new source). The own gate above
        # is the verdict; the clean gate is recorded as skipped, not judged.
        outcome = TestOutcome(
            ran=False, exit_code=None, tail="skipped: test_policy allow"
        ).to_dict()
        outcome.update(worktree="", patch_bytes=0)
    else:
        outcome = _clean_gate(
            str(worktree),
            base_sha=base_sha,
            patterns=list(patterns),
            command=test_command,
            timeout=int(timeout),
            stop=stop or (lambda: False),
            worktree=scratch,
        )

    # Both gates read the kept worktree, so the digest above is only evidence
    # about what they judged for as long as those bytes held still. Re-read
    # them once: an edit landing mid-salvage is refused, never receipted as
    # if the snapshot had been gated.
    if _text_sha256(diff_since(str(worktree), base_sha)) != diff_sha256:
        raise SalvageInvalid(
            f"lane '{lane}' kept worktree changed while it was being gated; "
            "re-run the salvage"
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
        own_gate=own_outcome,
        lineage_diff_sha256=lineage_diff_sha256,
    )


def salvage(
    home: Path, mission_id: str, lane: str, *, stop: Callable[[], bool] | None = None
) -> SalvageResult:
    """Gate a kept lane's worktree from a scratch copy, and receipt the result.

    Two gates: the tree's own (everything transplanted) and the clean gate
    (test surface restored from the base). Both must pass for the salvage to
    count as green; `cmd_salvage` reads both.

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
    ceiling: dict | None = None,
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
        ceiling=ceiling,
    )
    # F15 mission 2 item 2: the fix lane's dispositions.json deliverable
    # needs its schema file on disk before the mission can load, the same
    # as `cmd_shape_a` writes it beside a freshly launched mission.
    shape.write_dispositions_schema(out.parent)
    try:
        mission: Mission = mission_from_dict(mission_dict, base_dir=out.parent, source=str(out))
    except MissionInvalid as exc:
        raise SalvageInvalid(f"follow-on mission is invalid: {exc}") from exc
    del mission  # validated only; the raw dict is what gets written
    out.write_text(json.dumps(mission_dict, indent=2) + "\n")
    return mission_dict
