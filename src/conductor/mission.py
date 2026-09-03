"""Missions: unattended runs as data, not shell.

A mission file names one prompt and the lanes it fans out to. Each lane is a
fleet plus an ordered list of fallbacks; conductor runs the lanes with a
concurrency cap, escalates down a lane's fallback list when an attempt fails
(non-zero exit, timeout, no-op on a write, failed gate, fleet-reported error),
keeps a running dollar ledger against an optional budget, isolates every write
lane in its own worktree, and ends by writing one report the orchestrator can
read instead of N transcripts. An optional collate step hands every lane's
answer to one more read-mode dispatch for synthesis.

This is the interface an orchestrating model actually drives: it writes a
JSON (or TOML) file and reads back a summary and a report path. Nothing in
the file needs to know four CLIs' flag vocabularies, and every routing rule
is checked at load time, before a token is spent.

Field inheritance is the only clever thing here: an attempt inherits from its
lane, a lane from the mission, so the common case is one prompt, one cwd,
one mode, and a list of fleets.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .fleets import DispatchRefused, Spec
from .runner import Result, _slug, claim_dir, conductor_home, dispatch

REQUIRE = ("all", "any")
_LANE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

# Fields an attempt may set, in the order they cascade mission -> lane -> attempt.
_INHERITED = (
    "fleet",
    "model",
    "effort",
    "mode",
    "prompt",
    "timeout",
    "test",
    "commit",
    "isolate",
)

DEFAULT_COLLATE_INSTRUCTIONS = (
    "Compare the lane results above. State where they agree, where they disagree, "
    "and which lane's result is strongest and why. Be concrete and brief."
)
COLLATE_MAX_CHARS = 8000
REPORT_MAX_CHARS = 4000


class MissionInvalid(ValueError):
    """A mission file that cannot be run as written. Raised at load time."""


@dataclass
class Attempt:
    """One dispatch within a lane: the primary or one of its fallbacks."""

    fleet: str
    model: str | None = None
    effort: str = "standard"
    mode: str = "read"
    prompt: str = ""
    timeout: int | None = None
    test: str | None = None
    commit: str | None = None
    schema: str | None = None
    isolate: bool | None = None

    def spec(self, cwd: str) -> Spec:
        return Spec(
            fleet=self.fleet,
            prompt=self.prompt,
            cwd=cwd,
            model=self.model,
            effort=self.effort,
            mode=self.mode,
            timeout=self.timeout,
            schema=self.schema,
        )

    def isolated(self) -> bool:
        """Write lanes isolate by default: two fleets must never share a tree."""
        return self.isolate if self.isolate is not None else self.mode == "write"

    def label(self) -> str:
        return f"{self.fleet}/{self.model}" if self.model else self.fleet


@dataclass
class Lane:
    name: str
    attempts: list[Attempt]


@dataclass
class Collate:
    fleet: str
    model: str | None = None
    effort: str = "standard"
    timeout: int | None = None
    schema: str | None = None
    instructions: str = DEFAULT_COLLATE_INSTRUCTIONS
    max_chars: int = COLLATE_MAX_CHARS

    def spec(self, cwd: str, prompt: str) -> Spec:
        return Spec(
            fleet=self.fleet,
            prompt=prompt,
            cwd=cwd,
            model=self.model,
            effort=self.effort,
            mode="read",
            timeout=self.timeout,
            schema=self.schema,
        )


@dataclass
class Mission:
    name: str
    cwd: str
    lanes: list[Lane]
    concurrency: int = 2
    require: str = "all"
    max_cost_usd: float | None = None
    collate: Collate | None = None
    source: str = ""

    def validate(self) -> None:
        if not self.lanes:
            raise MissionInvalid("a mission needs at least one lane")
        if self.require not in REQUIRE:
            raise MissionInvalid(f"require must be one of {', '.join(REQUIRE)}")
        if self.concurrency < 1:
            raise MissionInvalid("concurrency must be at least 1")
        if self.max_cost_usd is not None and self.max_cost_usd <= 0:
            raise MissionInvalid("max_cost_usd must be positive")
        seen: set[str] = set()
        for lane in self.lanes:
            if not _LANE_NAME.fullmatch(lane.name):
                raise MissionInvalid(
                    f"lane name '{lane.name}' must match {_LANE_NAME.pattern}; it names a file"
                )
            if lane.name in seen:
                raise MissionInvalid(f"duplicate lane name '{lane.name}'")
            seen.add(lane.name)
            for attempt in lane.attempts:
                try:
                    attempt.spec(self.cwd).validate()
                except DispatchRefused as exc:
                    raise MissionInvalid(f"lane '{lane.name}' ({attempt.label()}): {exc}") from exc
        if self.collate:
            try:
                self.collate.spec(self.cwd, "collate").validate()
            except DispatchRefused as exc:
                raise MissionInvalid(f"collate: {exc}") from exc

    def to_dict(self) -> dict:
        return asdict(self)


# --- loading ----------------------------------------------------------------


def load_mission(path: str | Path) -> Mission:
    """Read a .json or .toml mission file and resolve every inherited field.

    Relative `cwd`, `prompt_file`, and `schema` paths resolve against the
    mission file's own directory, so a mission directory is portable.
    """
    file = Path(path).expanduser().resolve()
    try:
        text = file.read_text()
    except OSError as exc:
        raise MissionInvalid(f"cannot read mission file: {exc}") from exc
    try:
        if file.suffix == ".toml":
            raw = tomllib.loads(text)
        else:
            raw = json.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise MissionInvalid(f"mission file is not valid {file.suffix or 'json'}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MissionInvalid("mission file must be an object at the top level")
    return mission_from_dict(raw, base_dir=file.parent, source=str(file))


def mission_from_dict(raw: dict, *, base_dir: Path, source: str = "") -> Mission:
    base_dir = Path(base_dir)
    name = str(raw.get("name") or "mission")
    cwd = str((base_dir / Path(str(raw.get("cwd", "."))).expanduser()).resolve())

    defaults = _attempt_fields(raw, base_dir, {})
    if "fleet" in defaults:
        # A mission-level fleet would make every lane the same fleet; the
        # lanes list is where fleets belong.
        raise MissionInvalid("set fleet on each lane, not on the mission")

    raw_lanes = raw.get("lanes")
    if not isinstance(raw_lanes, list):
        raise MissionInvalid("mission needs a 'lanes' list")
    lanes: list[Lane] = []
    for i, raw_lane in enumerate(raw_lanes):
        if not isinstance(raw_lane, dict):
            raise MissionInvalid(f"lane {i} must be an object")
        primary_fields = _attempt_fields(raw_lane, base_dir, defaults)
        primary = _attempt(primary_fields, where=f"lane {i}")
        attempts = [primary]
        for j, raw_fb in enumerate(raw_lane.get("fallback") or []):
            if not isinstance(raw_fb, dict):
                raise MissionInvalid(f"lane {i} fallback {j} must be an object")
            attempts.append(
                _attempt(
                    _attempt_fields(raw_fb, base_dir, primary_fields),
                    where=f"lane {i} fallback {j}",
                )
            )
        lane_name = str(raw_lane.get("name") or _default_lane_name(primary, lanes))
        lanes.append(Lane(name=lane_name, attempts=attempts))

    collate = None
    raw_collate = raw.get("collate")
    if isinstance(raw_collate, dict):
        if "fleet" not in raw_collate:
            raise MissionInvalid("collate needs a fleet")
        schema = raw_collate.get("schema")
        collate = Collate(
            fleet=str(raw_collate["fleet"]),
            model=raw_collate.get("model"),
            effort=str(raw_collate.get("effort", "standard")),
            timeout=raw_collate.get("timeout"),
            schema=str((base_dir / str(schema)).expanduser().resolve()) if schema else None,
            instructions=str(raw_collate.get("instructions") or DEFAULT_COLLATE_INSTRUCTIONS),
            max_chars=int(raw_collate.get("max_chars", COLLATE_MAX_CHARS)),
        )

    try:
        concurrency = int(raw.get("concurrency", 2))
        max_cost = float(raw["max_cost_usd"]) if raw.get("max_cost_usd") is not None else None
    except (TypeError, ValueError) as exc:
        raise MissionInvalid(f"concurrency and max_cost_usd must be numbers: {exc}") from exc
    mission = Mission(
        name=name,
        cwd=cwd,
        lanes=lanes,
        concurrency=concurrency,
        require=str(raw.get("require", "all")),
        max_cost_usd=max_cost,
        collate=collate,
        source=source,
    )
    mission.validate()
    return mission


def _attempt_fields(raw: dict, base_dir: Path, parent: dict) -> dict:
    """Merge one level of the cascade: explicit keys override the parent's."""
    out = dict(parent)
    if parent.get("fleet") and raw.get("fleet") and raw["fleet"] != parent["fleet"]:
        # Model names are fleet-local. A fallback that switches fleet must
        # not carry its parent's model with it: caught live 2026-09-03 when
        # an antigravity fallback inherited "luna" from its codex primary.
        # (A mission-level model has no fleet to differ from and does cascade;
        # load-time validation refuses it on any lane it does not fit.)
        out.pop("model", None)
    for key in _INHERITED:
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    if raw.get("prompt_file"):
        prompt_path = (base_dir / str(raw["prompt_file"])).expanduser().resolve()
        try:
            out["prompt"] = prompt_path.read_text()
        except OSError as exc:
            raise MissionInvalid(f"cannot read prompt_file: {exc}") from exc
    if "schema" in raw and raw["schema"]:
        out["schema"] = str((base_dir / str(raw["schema"])).expanduser().resolve())
    return out


def _attempt(fields: dict, *, where: str) -> Attempt:
    if "fleet" not in fields:
        raise MissionInvalid(f"{where}: fleet is required")
    if not str(fields.get("prompt", "")).strip():
        raise MissionInvalid(f"{where}: no prompt (set prompt or prompt_file on the mission)")
    for key in ("model", "test", "commit", "schema"):
        if fields.get(key) is not None and not isinstance(fields[key], str):
            raise MissionInvalid(f"{where}: {key} must be a string")
    if fields.get("isolate") is not None and not isinstance(fields["isolate"], bool):
        raise MissionInvalid(f"{where}: isolate must be true or false")
    try:
        return Attempt(
            fleet=str(fields["fleet"]),
            model=fields.get("model"),
            effort=str(fields.get("effort", "standard")),
            mode=str(fields.get("mode", "read")),
            prompt=str(fields["prompt"]),
            timeout=int(fields["timeout"]) if fields.get("timeout") is not None else None,
            test=fields.get("test"),
            commit=fields.get("commit"),
            schema=fields.get("schema"),
            isolate=fields.get("isolate"),
        )
    except (TypeError, ValueError) as exc:
        raise MissionInvalid(f"{where}: {exc}") from exc


def _default_lane_name(primary: Attempt, existing: list[Lane]) -> str:
    base = primary.label().replace("/", "-")
    taken = {lane.name for lane in existing}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# --- running ----------------------------------------------------------------


class Ledger:
    """Dollars spent so far, shared across lanes, checked before every spend.

    The budget is a stop line, not a hard ceiling: a dispatch's cost is only
    known after it finishes, so up to `concurrency` dispatches that all passed
    the check at the same instant can still land. The overshoot is bounded by
    one dispatch per lane in flight. A hard per-dispatch cap is a fleet
    feature (claude has --max-budget-usd; the others have none).
    """

    def __init__(self, max_cost_usd: float | None) -> None:
        self.max = max_cost_usd
        self.spent = 0.0
        self.unpriced = 0  # dispatches that reported no cost at all
        self._lock = threading.Lock()

    def can_spend(self) -> bool:
        with self._lock:
            return self.max is None or self.spent < self.max

    def add(self, result: Result) -> None:
        cost = (result.usage or {}).get("cost_usd")
        with self._lock:
            if cost is None:
                self.unpriced += 1
            else:
                self.spent += float(cost)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "max_cost_usd": self.max,
                "spent_usd": round(self.spent, 6),
                "exceeded": self.max is not None and self.spent >= self.max,
                "unpriced_dispatches": self.unpriced,
            }


@dataclass
class LaneResult:
    name: str
    ok: bool
    attempts: list[dict] = field(default_factory=list)
    answer_path: str | None = None
    cost_usd: float = 0.0
    tokens: int = 0
    skipped: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MissionResult:
    mission_id: str
    name: str
    ok: bool
    require: str
    lanes: list[dict]
    cost_usd: float
    tokens: int
    duration_s: float
    budget: dict
    collate: dict | None
    mission_dir: str
    report_path: str
    dry_run: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "ok": self.ok,
            "require": self.require,
            "lanes": [
                {
                    "name": lane["name"],
                    "ok": lane["ok"],
                    "attempts": len(lane["attempts"]),
                    "final": (lane["attempts"][-1]["run_id"] if lane["attempts"] else None),
                    "branch": (lane["attempts"][-1].get("branch") if lane["attempts"] else None),
                    "cost_usd": lane["cost_usd"],
                    "skipped": lane.get("skipped"),
                }
                for lane in self.lanes
            ],
            "cost_usd": round(self.cost_usd, 6),
            "tokens": self.tokens,
            "duration_s": round(self.duration_s, 1),
            "budget": self.budget,
            "collate": (
                {k: self.collate.get(k) for k in ("ok", "answer_path", "cost_usd", "error")}
                if self.collate
                else None
            ),
            "report_path": self.report_path,
            "mission_dir": self.mission_dir,
        }


def _usd(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def run_mission(
    mission: Mission, *, home: Path | None = None, dry_run: bool = False
) -> MissionResult:
    mission.validate()
    base = home or conductor_home()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    mission_id, mission_dir = claim_dir(
        base / "missions", f"{stamp}-{_slug(mission.name, default='mission')}"
    )
    (mission_dir / "mission.json").write_text(json.dumps(mission.to_dict(), indent=2))
    answers_dir = mission_dir / "answers"
    answers_dir.mkdir(exist_ok=True)

    ledger = Ledger(mission.max_cost_usd)
    started = time.monotonic()

    def run_lane(lane: Lane) -> LaneResult:
        # One lane's crash must not take the mission's other lanes, its
        # ledger, or its report down with it: the failure becomes that
        # lane's result and the mission still writes result.json.
        out = LaneResult(name=lane.name, ok=False)
        try:
            _run_attempts(lane, out)
        except Exception as exc:  # noqa: BLE001 - boundary for an unattended run
            out.ok = False
            out.skipped = f"lane crashed: {type(exc).__name__}: {exc}"
        return out

    def _run_attempts(lane: Lane, out: LaneResult) -> None:
        for attempt in lane.attempts:
            if not dry_run and not ledger.can_spend():
                out.skipped = (
                    f"budget exhausted before {attempt.label()}: "
                    f"${_usd(ledger.spent)} of ${_usd(ledger.max)}"
                )
                break
            result = dispatch(
                attempt.spec(mission.cwd),
                dry_run=dry_run,
                test_command=attempt.test,
                commit_message=attempt.commit,
                isolate=attempt.isolated(),
                home=base,
            )
            ledger.add(result)
            summary = result.summary()
            summary["lane"] = lane.name
            summary["attempt"] = attempt.label()
            out.attempts.append(summary)
            out.cost_usd += float(summary.get("cost_usd") or 0.0)
            out.tokens += int(summary.get("tokens") or 0)
            if result.answer_path:
                dest = answers_dir / f"{lane.name}.txt"
                shutil.copyfile(result.answer_path, dest)
                out.answer_path = str(dest)
            if dry_run or result.ok:
                out.ok = True
                break

    with ThreadPoolExecutor(max_workers=mission.concurrency) as pool:
        lane_results = list(pool.map(run_lane, mission.lanes))

    collate_out: dict | None = None
    if mission.collate and not dry_run:
        collate_out = _run_collate(mission, lane_results, ledger, mission_dir, base)

    lanes_ok = [lane.ok for lane in lane_results]
    ok = all(lanes_ok) if mission.require == "all" else any(lanes_ok)
    if collate_out is not None and not collate_out.get("ok"):
        ok = False

    duration = time.monotonic() - started
    report_path = mission_dir / "report.md"
    result = MissionResult(
        mission_id=mission_id,
        name=mission.name,
        ok=ok,
        require=mission.require,
        lanes=[lane.to_dict() for lane in lane_results],
        cost_usd=sum(lane.cost_usd for lane in lane_results)
        + float((collate_out or {}).get("cost_usd") or 0.0),
        tokens=sum(lane.tokens for lane in lane_results)
        + int((collate_out or {}).get("tokens") or 0),
        duration_s=duration,
        budget=ledger.to_dict(),
        collate=collate_out,
        mission_dir=str(mission_dir),
        report_path=str(report_path),
        dry_run=dry_run,
    )
    report_path.write_text(_report(mission, result, lane_results))
    (mission_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
    return result


def _lane_answer(lane: LaneResult, limit: int) -> str:
    if lane.answer_path and Path(lane.answer_path).is_file():
        text = Path(lane.answer_path).read_text(errors="replace").strip()
        if len(text) > limit:
            return text[:limit] + f"\n[... truncated, {len(text) - limit} more chars]"
        return text or "(empty answer)"
    if lane.skipped:
        return f"(skipped: {lane.skipped})"
    last = lane.attempts[-1] if lane.attempts else {}
    return f"(no answer; error: {last.get('error') or 'none recorded'})"


def _run_collate(
    mission: Mission,
    lanes: list[LaneResult],
    ledger: Ledger,
    mission_dir: Path,
    base: Path,
) -> dict:
    col = mission.collate
    assert col is not None
    if not ledger.can_spend():
        return {"ok": False, "error": "budget exhausted before collate", "cost_usd": None}

    original = mission.lanes[0].attempts[0].prompt
    parts = [
        "You are collating the results of a mission that sent one prompt to several "
        "agent fleets.\n",
        "## Original prompt\n",
        original.strip(),
        "\n\n## Lane results\n",
    ]
    for lane in lanes:
        last = lane.attempts[-1] if lane.attempts else {}
        parts.append(
            f"\n### Lane `{lane.name}` ({last.get('attempt', '?')}, ok={lane.ok}, "
            f"cost_usd={_usd(lane.cost_usd)})\n\n{_lane_answer(lane, col.max_chars)}\n"
        )
    parts.append(f"\n## Instructions\n\n{col.instructions.strip()}\n")
    prompt = "".join(parts)
    (mission_dir / "collate-prompt.txt").write_text(prompt)

    result = dispatch(
        col.spec(mission.cwd, prompt),
        home=base,
    )
    ledger.add(result)
    summary = result.summary()
    answer_path = None
    if result.answer_path:
        dest = mission_dir / "collated.txt"
        shutil.copyfile(result.answer_path, dest)
        answer_path = str(dest)
    return {
        "ok": result.ok,
        "run_id": result.run_id,
        "fleet": result.fleet,
        "model": result.model,
        "answer_path": answer_path,
        "cost_usd": (
            round(summary["cost_usd"], 6) if summary.get("cost_usd") is not None else None
        ),
        "tokens": summary.get("tokens"),
        "error": summary.get("error"),
    }


def _report(mission: Mission, result: MissionResult, lanes: list[LaneResult]) -> str:
    lines = [
        f"# Mission `{mission.name}`",
        "",
        f"- id: `{result.mission_id}`",
        f"- ok: **{result.ok}** (require: {mission.require})",
        f"- cwd: `{mission.cwd}`",
        f"- cost: ${_usd(result.cost_usd)} across {result.tokens} tokens"
        + (
            f" ({result.budget['unpriced_dispatches']} dispatch(es) unpriced)"
            if result.budget.get("unpriced_dispatches")
            else ""
        ),
        f"- duration: {result.duration_s:.1f}s",
        "",
        "| lane | attempt | ok | exit | no_op | commits | branch | cost_usd | tokens | dur_s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lane in lanes:
        if not lane.attempts and lane.skipped:
            lines.append(f"| {lane.name} | (skipped) | False | | | | | | | |")
        for a in lane.attempts:
            lines.append(
                f"| {lane.name} | {a['attempt']} | {a['ok']} | {a['exit_code']} | "
                f"{a['no_op']} | {a['commits']} | {a.get('branch') or ''} | "
                f"{_usd(a.get('cost_usd'))} | "
                f"{a.get('tokens') or ''} | {a['duration_s']} |"
            )
    if result.budget.get("exceeded"):
        lines += ["", f"**Budget exceeded**: {json.dumps(result.budget)}"]
    for lane in lanes:
        lines += ["", f"## Lane `{lane.name}`", ""]
        if lane.skipped:
            lines.append(f"Skipped: {lane.skipped}")
        for a in lane.attempts:
            if a.get("error"):
                lines.append(f"- {a['attempt']}: {a['error']}")
            if a.get("worktree"):
                lines.append(f"- {a['attempt']}: uncommitted work kept at `{a['worktree']}`")
        lines += ["", _lane_answer(lane, REPORT_MAX_CHARS)]
    if result.collate:
        lines += ["", "## Collated", ""]
        if result.collate.get("answer_path"):
            lines.append(Path(result.collate["answer_path"]).read_text(errors="replace").strip())
        else:
            lines.append(f"(collate failed: {result.collate.get('error')})")
    lines.append("")
    return "\n".join(lines)
