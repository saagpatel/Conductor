"""Golden missions: record a finished mission as an offline fixture, then
replay it through the parser, the scheduler, and the templating without
spending on a live vendor.

`record` copies a mission directory and every run it dispatched into a
self-contained, scrubbed, deterministic fixture. `replay` loads that
fixture's mission snapshot and runs it again through `mission.run_mission`,
but with a `dispatcher` that returns the recorded receipt for each attempt
instead of spawning a fleet -- so a change to routing, a template, or
`outputs.parse` is testable against real recorded transcripts without a
network call. `check` compares a fresh replay's `projection` against the
fixture's own `expected.json`, pinning what a routing or template change is
allowed to move.

Evidence (`docs/ROADMAP-2026-09.md` item C7): "Recorded transcripts of past
real missions replayed through the parser, scheduler, and templating
offline, so a routing or template change is testable without spending on
live vendors."
"""

from __future__ import annotations

import dataclasses as _dc
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from . import outputs as outputs_mod
from .mission import Attempt, Mission, MissionResult, run_mission
from .runner import Result

FORMAT = "conductor/golden/v1"
DEFAULT_MAX_BYTES = 3_000_000

# Every file a run directory may contribute to a fixture, in the order they
# are considered; each only when it exists. argv.json, stderr.log, and
# liveness.json are never copied -- argv is reconstructible from the spec,
# stderr is empty on every real run so far, and liveness is a heartbeat with
# nothing to replay.
RUN_FILES = (
    "result.json",
    "stdout.log",
    "prompt.txt",
    "answer.txt",
    "diff.patch",
    "attestation.json",
)
MISSION_FILES = ("mission.json", "result.json", "report.md")

_ELIDE_LIMIT = 512
_SECRET_KEY_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD")
_ENV_SECRET_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:" + "|".join(_SECRET_KEY_WORDS) + r")[A-Za-z0-9_]*)=(\S+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"Bearer\s+\S+")
_TOKEN_PREFIX_RE = re.compile(r"(?:sk-|xai-|ghp_|AIza)[A-Za-z0-9_-]{16,}")
_NONCE_RE = re.compile(r"\[[0-9a-f]{6}\]")
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "total_tokens",
)


class GoldenError(ValueError):
    """A fixture cannot be recorded or replayed as asked."""


# --- scrubbing ---------------------------------------------------------


def _redact_secrets(text: str) -> str:
    text = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _TOKEN_PREFIX_RE.sub("<redacted>", text)
    return text


def _placeholder_map(*, home: Path, cwd: str | None) -> list[tuple[str, str]]:
    """(real value, placeholder) pairs, longest real value first, so a home
    nested inside the user's own home is replaced before the shorter path
    that contains it."""
    pairs: list[tuple[str, str]] = [(str(home), "<home>"), (str(Path.home()), "<user>")]
    if cwd:
        pairs.append((str(cwd), "<cwd>"))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return [pair for pair in pairs if pair[0]]


def scrub_text(text: str, replacements: list[tuple[str, str]]) -> str:
    """Placeholders, then secrets. Idempotent: scrubbing an already-scrubbed
    string changes nothing, since the placeholders and `<redacted>` never
    match a replacement's own pattern."""
    for old, new in replacements:
        text = text.replace(old, new)
    return _redact_secrets(text)


def _scrub_json_value(
    value: object, replacements: list[tuple[str, str]], *, key: str | None = None
):
    if isinstance(value, str):
        if key is not None and any(word.lower() in key.lower() for word in _SECRET_KEY_WORDS):
            return "<redacted>"
        return scrub_text(value, replacements)
    if isinstance(value, dict):
        return {k: _scrub_json_value(v, replacements, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_json_value(v, replacements, key=key) for v in value]
    return value


def scrub_json_text(text: str, replacements: list[tuple[str, str]]) -> str:
    obj = json.loads(text)
    return json.dumps(_scrub_json_value(obj, replacements), indent=2)


def scrub_guard(path: str | Path) -> list[str]:
    """Every occurrence in a fixture directory of the user's home path, the
    conductor home, or any of the scrub's secret patterns, as
    `file:line: <pattern name>`; empty when clean."""
    from .paths import conductor_home

    path = Path(path)
    patterns: list[tuple[str, str]] = [
        (str(Path.home()), "user home"),
        (str(conductor_home()), "conductor home"),
    ]
    findings: list[str] = []
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        try:
            text = file.read_text(errors="replace")
        except OSError:
            continue
        rel = file.relative_to(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for needle, name in patterns:
                if needle and needle in line:
                    findings.append(f"{rel}:{line_no}: {name}")
            if _ENV_SECRET_RE.search(line):
                findings.append(f"{rel}:{line_no}: env secret")
            if _BEARER_RE.search(line):
                findings.append(f"{rel}:{line_no}: bearer token")
            if _TOKEN_PREFIX_RE.search(line):
                findings.append(f"{rel}:{line_no}: prefixed token")
    return findings


# --- elision -------------------------------------------------------------


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _elide_string(value: str) -> str:
    if len(value) <= _ELIDE_LIMIT:
        return value
    return f"<elided {len(value)} chars sha256={_sha12(value)}>"


def _elide_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return line
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return line
    if not isinstance(obj, dict):
        return line
    is_result_event = obj.get("type") == "result" or obj.get("event") == "result"
    is_assistant_event = obj.get("type") == "assistant"

    def protected(key: str | None) -> bool:
        if key == "plan":
            return True
        if is_result_event and key in ("result", "response", "error"):
            return True
        return bool(is_assistant_event and key == "text")

    def walk(value: object, key: str | None = None):
        if isinstance(value, str):
            return value if protected(key) else _elide_string(value)
        if isinstance(value, dict):
            return {k: walk(v, key=k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key=key) for v in value]
        return value

    return json.dumps(walk(obj))


def elide_stream(text: str) -> str:
    return "\n".join(_elide_line(line) for line in text.splitlines())


def _output_fields(output: outputs_mod.FleetOutput) -> tuple:
    return (
        output.answer,
        output.usage.to_dict() if output.usage else None,
        output.status,
        output.error,
        output.session_id,
    )


def _verify_elision(fleet: str, original: str, elided: str, run_id: str) -> None:
    before = outputs_mod.parse(fleet, original)
    after = outputs_mod.parse(fleet, elided)
    names = ("answer", "usage", "status", "error", "session_id")
    pairs = zip(names, _output_fields(before), _output_fields(after), strict=True)
    for name, was, now in pairs:
        if was != now:
            raise GoldenError(f"run {run_id}: elision changed {name}")


# --- recording -------------------------------------------------------------


def _run_ids_and_fleets(lane_files: list[Path]) -> tuple[list[str], dict[str, str], set[str]]:
    """Every run id named by any attempt row, in first-seen order, its
    fleet, and the set of fleets seen (for the manifest)."""
    run_ids: list[str] = []
    fleet_by_run: dict[str, str] = {}
    fleets: set[str] = set()
    for lane_file in lane_files:
        data = json.loads(lane_file.read_text())
        for key in ("previous_attempts", "attempts"):
            for attempt in data.get(key) or []:
                run_id = attempt.get("run_id")
                fleet = attempt.get("fleet")
                if not isinstance(run_id, str):
                    continue
                if run_id not in fleet_by_run:
                    run_ids.append(run_id)
                if isinstance(fleet, str):
                    fleet_by_run[run_id] = fleet
                    fleets.add(fleet)
    return run_ids, fleet_by_run, fleets


def _field_defaults(cls: type) -> dict:
    return {f.name: f.default for f in _dc.fields(cls) if f.default is not _dc.MISSING}


_MISSION_FIELD_DEFAULTS = _field_defaults(Mission)
_ATTEMPT_FIELD_DEFAULTS = _field_defaults(Attempt)


def _backfill_snapshot(mission_raw: dict) -> dict:
    """Fill any field the current snapshot schema expects but an older
    recording predates (`retry` and `on`, added by C5, are not on any
    mission run before it) with that field's own dataclass default -- the
    value the mission actually ran with, before the field existed to set
    otherwise. `Mission.from_snapshot` requires an exact key match, so a
    recording from an earlier version of conductor could not be replayed at
    all without this."""
    for key, default in _MISSION_FIELD_DEFAULTS.items():
        mission_raw.setdefault(key, default)
    for lane in mission_raw.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        for attempt in lane.get("attempts") or []:
            if isinstance(attempt, dict):
                for key, default in _ATTEMPT_FIELD_DEFAULTS.items():
                    attempt.setdefault(key, default)
    return mission_raw


def _copy_json(src: Path, dst: Path, replacements: list[tuple[str, str]]) -> None:
    dst.write_text(scrub_json_text(src.read_text(), replacements))


def _copy_text(src: Path, dst: Path, replacements: list[tuple[str, str]]) -> None:
    dst.write_text(scrub_text(src.read_text(errors="replace"), replacements))


def record(
    mission_dir: str | Path,
    out_dir: str | Path,
    *,
    home: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Copy a finished mission and every run it dispatched into `out_dir` as
    a self-contained, scrubbed, deterministic fixture. Refuses (raising
    `GoldenError`) when the fixture would exceed `max_bytes`, or when
    replaying it back offline disagrees with what was actually recorded."""
    mission_dir = Path(mission_dir)
    out_dir = Path(out_dir)
    home = Path(home)
    if out_dir.exists():
        raise GoldenError(f"{out_dir} already exists")
    snapshot_path = mission_dir / "mission.json"
    if not snapshot_path.is_file():
        raise GoldenError(f"{mission_dir}: no mission.json")
    mission_raw = json.loads(snapshot_path.read_text())
    cwd = mission_raw.get("cwd")
    replacements = _placeholder_map(home=home, cwd=cwd)

    lanes_dir = mission_dir / "lanes"
    lane_files = sorted(lanes_dir.glob("*.json")) if lanes_dir.is_dir() else []
    run_ids, fleet_by_run, fleets = _run_ids_and_fleets(lane_files)

    with tempfile.TemporaryDirectory(prefix="conductor-golden-") as tmp:
        work = Path(tmp) / out_dir.name
        work.mkdir()

        for name in MISSION_FILES:
            src = mission_dir / name
            if not src.is_file():
                continue
            if name == "mission.json":
                backfilled = json.dumps(_backfill_snapshot(dict(mission_raw)), indent=2)
                (work / name).write_text(scrub_json_text(backfilled, replacements))
            elif name.endswith(".json"):
                _copy_json(src, work / name, replacements)
            else:
                _copy_text(src, work / name, replacements)
        pause_src = mission_dir / "pause.json"
        if pause_src.is_file():
            _copy_json(pause_src, work / "pause.json", replacements)

        work_lanes = work / "lanes"
        work_lanes.mkdir()
        for lane_file in lane_files:
            _copy_json(lane_file, work_lanes / lane_file.name, replacements)

        work_runs = work / "runs"
        for run_id in run_ids:
            run_src = home / "runs" / run_id
            run_dst = work_runs / run_id
            run_dst.mkdir(parents=True)
            fleet = fleet_by_run.get(run_id, "")
            for name in RUN_FILES:
                src = run_src / name
                if not src.is_file():
                    continue
                if name == "stdout.log":
                    original = src.read_text(errors="replace")
                    elided = elide_stream(original)
                    _verify_elision(fleet, original, elided, run_id)
                    (run_dst / name).write_text(scrub_text(elided, replacements))
                elif name.endswith(".json"):
                    _copy_json(src, run_dst / name, replacements)
                else:
                    _copy_text(src, run_dst / name, replacements)

        replayed = _fresh_replay(work)
        if replayed.differences:
            raise GoldenError(
                f"{mission_dir.name}: replay disagrees with the recording: "
                + "; ".join(replayed.differences)
            )
        (work / "expected.json").write_text(
            json.dumps(replayed.projection, indent=2, sort_keys=True)
        )

        files: dict[str, str] = {}
        for file in sorted(p for p in work.rglob("*") if p.is_file()):
            files[str(file.relative_to(work))] = hashlib.sha256(file.read_bytes()).hexdigest()
        total = sum(file.stat().st_size for file in work.rglob("*") if file.is_file())
        if total > max_bytes:
            largest = max(
                (p for p in work.rglob("*") if p.is_file()), key=lambda p: p.stat().st_size
            )
            raise GoldenError(
                f"fixture would be {total} bytes, over the {max_bytes} limit; "
                f"largest file: {largest.relative_to(work)} ({largest.stat().st_size} bytes)"
            )

        manifest = {
            "format": FORMAT,
            "mission_id": mission_dir.name,
            "name": out_dir.name,
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "conductor_version": __version__,
            "fleets": sorted(fleets),
            "placeholders": ["<home>", "<cwd>", "<user>"],
            "files": files,
        }
        (work / "golden.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), str(out_dir))

    return out_dir


# --- replay ----------------------------------------------------------------


@dataclass
class Replay:
    differences: list[str] = field(default_factory=list)
    projection: dict = field(default_factory=dict)
    result: MissionResult | None = None


def _strip_nonce(text: str) -> str:
    return _NONCE_RE.sub("[nonce]", text)


def _token_fields(usage: dict | None) -> dict | None:
    if usage is None:
        return None
    return {key: usage.get(key, 0) for key in _TOKEN_FIELDS}


def _compare(differences: list[str], run_id: str, name: str, recorded, replayed) -> None:
    if recorded != replayed:
        differences.append(f"run {run_id}: parser {name}: recorded {recorded}, replayed {replayed}")


def _lane_recordings(fixture_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Per lane, the recorded (run_id, fleet) pairs in order: previous
    attempts (from an earlier resume), then this run's own attempts."""
    out: dict[str, list[tuple[str, str]]] = {}
    lanes_dir = fixture_dir / "lanes"
    if not lanes_dir.is_dir():
        return out
    for lane_file in sorted(lanes_dir.glob("*.json")):
        data = json.loads(lane_file.read_text())
        rows: list[tuple[str, str]] = []
        for key in ("previous_attempts", "attempts"):
            for attempt in data.get(key) or []:
                run_id = attempt.get("run_id")
                if isinstance(run_id, str):
                    rows.append((run_id, attempt.get("fleet") or ""))
        out[data["name"]] = rows
    return out


def replay(fixture_dir: str | Path, *, home: Path, cwd: str) -> Replay:
    """Load a fixture's mission snapshot and run it again through
    `mission.run_mission`, with a dispatcher that replays each attempt's
    recorded receipt instead of spawning a fleet."""
    fixture_dir = Path(fixture_dir)
    home = Path(home)
    # `mission_from_dict` resolves `cwd` (symlinks included) when it loads a
    # mission; the snapshot's own round-trip check re-derives it the same
    # way, so a `cwd` that is not already resolved (macOS's /tmp -> /private
    # /tmp, for one) would make every snapshot fail to round-trip.
    cwd = str(Path(cwd).resolve())
    mission_text = (fixture_dir / "mission.json").read_text().replace("<cwd>", cwd)
    mission = Mission.from_snapshot(json.loads(mission_text))

    user_home = str(home / "_replay_user_home")

    def restore(text: str) -> str:
        return text.replace("<cwd>", cwd).replace("<home>", str(home)).replace("<user>", user_home)

    lane_recordings = _lane_recordings(fixture_dir)
    differences: list[str] = []
    call_index: dict[str, int] = {}

    def dispatcher(
        spec,
        *,
        lane: str,
        attempt: str,
        retry: int | None,
        dry_run: bool,
        test_command: str | None,
        commit_message: str | None,
        isolate: bool,
        home: Path,
        no_op_ok: bool,
        base_ref: str | None,
        cancel,
    ) -> Result:
        k = call_index.get(lane, 0)
        call_index[lane] = k + 1
        recordings = lane_recordings.get(lane, [])
        if k >= len(recordings):
            differences.append(
                f"lane {lane}: replay dispatched attempt {k + 1} but the recording has "
                f"{len(recordings)}"
            )
            return Result(
                run_id=f"golden-unrecorded-{lane}-{k + 1}",
                fleet=spec.fleet,
                model=spec.model or "",
                effort=spec.effort,
                mode=spec.mode,
                cwd=cwd,
                timeout=spec.resolved_timeout(),
                exit_code=None,
                timed_out=False,
                duration_s=0.0,
                run_dir="",
                stdout_path="",
                stderr_path="",
                tail="",
                spawned=False,
                error="golden: no recorded run",
            )
        run_id, fleet = recordings[k]
        src = fixture_dir / "runs" / run_id
        dst = Path(home) / "runs" / run_id
        dst.mkdir(parents=True, exist_ok=True)
        recorded_stdout = ""
        for name in RUN_FILES:
            file_src = src / name
            if not file_src.is_file():
                continue
            text = restore(file_src.read_text())
            (dst / name).write_text(text)
            if name == "stdout.log":
                recorded_stdout = text

        recorded_result = (
            json.loads((dst / "result.json").read_text()) if (dst / "result.json").is_file() else {}
        )
        recorded_answer = (dst / "answer.txt").read_text() if (dst / "answer.txt").is_file() else ""
        parsed = outputs_mod.parse(fleet, recorded_stdout)
        _compare(differences, run_id, "answer", recorded_answer, parsed.answer)
        _compare(
            differences,
            run_id,
            "usage",
            _token_fields(recorded_result.get("usage")),
            _token_fields(parsed.usage.to_dict() if parsed.usage else None),
        )
        _compare(differences, run_id, "status", recorded_result.get("fleet_status"), parsed.status)
        _compare(differences, run_id, "error", recorded_result.get("fleet_error"), parsed.error)
        _compare(
            differences, run_id, "session_id", recorded_result.get("session_id"), parsed.session_id
        )

        recorded_prompt_path = src / "prompt.txt"
        if recorded_prompt_path.is_file():
            # Both sides compared in placeholder form: the fixture's
            # prompt.txt was scrubbed at record time and is never restored
            # to real paths, and the freshly rendered prompt is scrubbed the
            # same way here, so a real replay `cwd`/`home` never leaks into
            # the comparison (or the diff) by accident.
            recorded_prompt = _strip_nonce(recorded_prompt_path.read_text())
            replacements = _placeholder_map(home=Path(home), cwd=cwd)
            rendered_mapped = _strip_nonce(scrub_text(spec.prompt, replacements))
            if rendered_mapped != recorded_prompt:
                diff_lines = list(
                    difflib.unified_diff(
                        recorded_prompt.splitlines(),
                        rendered_mapped.splitlines(),
                        lineterm="",
                    )
                )[:40]
                differences.append(
                    f"lane {lane} attempt {attempt}: rendered prompt differs from the recording\n"
                    + "\n".join(diff_lines)
                )

        overrides = {
            "run_dir": str(dst),
            "stdout_path": str(dst / "stdout.log"),
            "answer_path": str(dst / "answer.txt") if (dst / "answer.txt").is_file() else None,
            "diff_path": str(dst / "diff.patch") if (dst / "diff.patch").is_file() else None,
            "attestation_path": (
                str(dst / "attestation.json") if (dst / "attestation.json").is_file() else None
            ),
            "cwd": cwd,
        }
        return Result.from_dict({**recorded_result, **overrides})

    mission_result = run_mission(mission, home=home, dispatcher=dispatcher)
    return Replay(
        differences=differences,
        projection=projection(mission_result),
        result=mission_result,
    )


def _fresh_replay(fixture_dir: Path) -> Replay:
    with (
        tempfile.TemporaryDirectory(prefix="conductor-golden-home-") as home_dir,
        tempfile.TemporaryDirectory(prefix="conductor-golden-cwd-") as cwd_dir,
    ):
        cwd_path = Path(cwd_dir)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cwd_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "golden@example.invalid"], cwd=cwd_path, check=True
        )
        subprocess.run(["git", "config", "user.name", "golden"], cwd=cwd_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "golden"], cwd=cwd_path, check=True
        )
        return replay(fixture_dir, home=Path(home_dir), cwd=str(cwd_path))


# --- projection and check ---------------------------------------------------


_ATTEMPT_PROJECTION_KEYS = (
    "fleet",
    "model",
    "effort",
    "mode",
    "ok",
    "kind",
    "failure",
    "no_op",
    "over_cap",
    "tool_calls",
    "tokens",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_basis",
    "session_id",
    "retry_of",
    "retry",
    "note",
)
_LANE_PROJECTION_KEYS = (
    "name",
    "stage",
    "needs",
    "base",
    "ok",
    "skipped",
    "escalated",
    "kinds",
    "branch",
)


def projection(result: MissionResult) -> dict:
    """The fields a routing or template change can move and a clock cannot:
    no paths, durations, run ids, timestamps, or dollar amounts."""
    data = result.to_dict()
    lanes = []
    for lane in data["lanes"]:
        attempts = [
            {key: attempt.get(key) for key in _ATTEMPT_PROJECTION_KEYS}
            for attempt in lane.get("attempts") or []
        ]
        entry = {key: lane.get(key) for key in _LANE_PROJECTION_KEYS}
        entry["attempts"] = attempts
        lanes.append(entry)
    return {
        "ok": data.get("ok"),
        "require": data.get("require"),
        "notes": data.get("notes"),
        "errors": data.get("errors"),
        "escalation": data.get("escalation"),
        "early_cancel": data.get("early_cancel"),
        "paused": data.get("paused"),
        "quorum": data.get("quorum"),
        "ranking": [
            {"lane": row.get("lane"), "rank": row.get("rank"), "ok": row.get("ok")}
            for row in data.get("ranking") or []
        ],
        "cache_hit_rate": (data.get("cache") or {}).get("hit_rate"),
        "lanes": lanes,
    }


def _diff_projection(expected: object, actual: object, path: str = "$") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                diffs.append(f"projection {path}.{key}: expected <missing>, got {actual[key]}")
            elif key not in actual:
                diffs.append(f"projection {path}.{key}: expected {expected[key]}, got <missing>")
            else:
                diffs.extend(_diff_projection(expected[key], actual[key], f"{path}.{key}"))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"projection {path}: expected {len(expected)} item(s), got {len(actual)}"]
        diffs = []
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            diffs.extend(_diff_projection(e, a, f"{path}[{i}]"))
        return diffs
    if expected != actual:
        return [f"projection {path}: expected {expected}, got {actual}"]
    return []


def check(fixture_dir: str | Path, *, update: bool = False) -> list[str]:
    """Replay a fixture into a fresh temporary home and a fresh temporary
    git repository (one empty commit) as `cwd`. Returns the replay's own
    differences plus, unless `update`, one line per projection field that
    differs from the fixture's `expected.json`. `update` rewrites
    `expected.json` from the replay instead."""
    fixture_dir = Path(fixture_dir)
    replayed = _fresh_replay(fixture_dir)
    expected_path = fixture_dir / "expected.json"
    if update:
        expected_path.write_text(json.dumps(replayed.projection, indent=2, sort_keys=True))
        return list(replayed.differences)
    expected = json.loads(expected_path.read_text()) if expected_path.is_file() else {}
    return list(replayed.differences) + _diff_projection(expected, replayed.projection)
