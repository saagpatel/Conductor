"""conductor: one dispatch contract across four agent fleets."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import attest, golden, prices, prompts, shape
from . import export as export_mod
from . import forecast as forecast_mod
from . import land as land_mod
from . import salvage as salvage_mod
from .errors import error_kind
from .fleets import (
    CAP_GRACE_CEILING_USD,
    EFFORTS,
    FLEETS,
    MODES,
    TAINT_SHELL_MODES,
    TEST_POLICIES,
    DispatchRefused,
    Spec,
    cli_version,
)
from .gc import cmd_gc
from .mission import (
    STAGES,
    Mission,
    MissionInvalid,
    load_mission,
    mission_from_dict,
    run_mission,
)
from .paths import conductor_home
from .report import cmd_report
from .runner import Result, dispatch, kill_live_groups, request_stop, stop_requested
from .runner import _gate_passed as _runner_gate_passed
from .spend import cmd_spend
from .verdicts import parse_checklist
from .verify import GitState, run_tests


def cmd_fleets(args: argparse.Namespace) -> int:
    """Print the routing policy, including whether each binary is installed."""
    rows = []
    for fleet in FLEETS.values():
        path = shutil.which(fleet.binary)
        rows.append(
            {
                "fleet": fleet.name,
                "binary": fleet.binary,
                "installed": bool(path),
                "path": path or "",
                "version": cli_version(fleet.name),
                "vendor": fleet.vendor,
                "cap": fleet.cap,
                "default_model": fleet.default_model,
                "models": [
                    {
                        "name": m.name,
                        "ids": {e: m.id_for(e) for e in EFFORTS},
                        "note": m.note,
                    }
                    for m in fleet.models
                ],
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        mark = "ok " if row["installed"] else "MISSING"
        version = f"  version: {row['version']}" if row["version"] else ""
        print(
            f"[{mark}] {row['fleet']:<12} {row['binary']:<13} {row['vendor']}  "
            f"cap: {row['cap']}{version}"
        )
        for m in row["models"]:
            default = " (default)" if m["name"] == row["default_model"] else ""
            note = f"  # {m['note']}" if m["note"] else ""
            print(f"          {m['name']}{default}{note}")
    missing = [r["fleet"] for r in rows if not r["installed"]]
    if missing:
        print(f"\nnot installed: {', '.join(missing)}", file=sys.stderr)
    print("\nprompt versions:")
    for prompt_name, version_id in sorted(prompts.prompt_versions().items()):
        print(f"  {prompt_name}: {version_id}")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text()
    if not prompt:
        print("error: give a prompt argument or --prompt-file", file=sys.stderr)
        return 2

    try:
        raw_verdict: list[object] = list(args.verdict)
        if args.verdict_file:
            try:
                from_file = json.loads(Path(args.verdict_file).read_text())
            except OSError as exc:
                raise DispatchRefused(f"verdict file unreadable: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise DispatchRefused(f"verdict file is not valid JSON: {exc}") from exc
            if not isinstance(from_file, list):
                raise DispatchRefused("verdict file must contain a JSON list")
            raw_verdict.extend(from_file)
        try:
            criteria = (
                parse_checklist(raw_verdict)
                if args.verdict or args.verdict_file is not None
                else None
            )
        except ValueError as exc:
            raise DispatchRefused(str(exc)) from exc
        agent = None
        if args.agent_file:
            try:
                agent = json.loads(Path(args.agent_file).read_text())
            except OSError as exc:
                raise DispatchRefused(f"agent file unreadable: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise DispatchRefused(f"agent file is not valid JSON: {exc}") from exc
        deliverable = None
        if args.deliverable:
            deliverable = {"path": args.deliverable}
            if args.deliverable_schema:
                deliverable["schema"] = args.deliverable_schema
        elif args.deliverable_schema:
            raise DispatchRefused("--deliverable-schema needs --deliverable")
        spec = Spec(
            fleet=args.fleet,
            prompt=prompt,
            cwd=str(Path(args.cwd).resolve()),
            model=args.model,
            effort=args.effort,
            mode=args.mode,
            timeout=args.timeout,
            stall_timeout=args.stall_timeout,
            loop_limit=args.loop_limit,
            max_tool_calls=args.max_tool_calls,
            tool_idle_timeout=args.tool_idle_timeout,
            schema=args.schema,
            verdict=criteria,
            resume=args.resume,
            cap_usd=args.cap_usd,
            cap_grace_usd=args.cap_grace_usd,
            test_policy=args.test_policy,
            test_surface=args.test_surface,
            stage=args.stage,
            ports=args.ports,
            setup=args.setup,
            teardown=args.teardown,
            include=args.include,
            taint=args.taint,
            taint_shell=args.taint_shell,
            agent=agent,
            deliverable=deliverable,
            restricted=args.restricted,
        )
        result = dispatch(
            spec,
            dry_run=args.dry_run,
            test_command=args.test,
            commit_message=args.commit,
            isolate=args.isolate,
        )
    except DispatchRefused as exc:
        print(json.dumps({"refused": str(exc)}, indent=2), file=sys.stderr)
        return 3

    _report(result, args)
    if args.dry_run:
        return 0
    return 0 if result.ok else 1


def _report(result: Result, args: argparse.Namespace) -> None:
    if args.dry_run:
        argv = json.loads((Path(result.run_dir) / "argv.json").read_text())
        print(json.dumps({"would_run": argv, "run_dir": result.run_dir}, indent=2))
        return
    print(json.dumps(result.summary(), indent=2))
    if not result.ok and result.tail:
        print("\n--- tail ---", file=sys.stderr)
        print(result.tail, file=sys.stderr)


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-run the byte check by hand, or run a gate against a repo."""
    cwd = str(Path(args.cwd).resolve())
    state = GitState.capture(cwd)
    out: dict = {"cwd": cwd, "git": state.__dict__}
    if args.test:
        out["tests"] = run_tests(cwd, args.test, stop=stop_requested).to_dict()
    print(json.dumps(out, indent=2))
    if args.test and out["tests"]["exit_code"] != 0:
        return 1
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    """Print the price table conductor will estimate with, after overrides."""
    errors: list[str] = []
    table = prices.load_prices(errors=errors)
    out = {
        "as_of": prices.AS_OF,
        "override_file": str(conductor_home() / "prices.json"),
        "override_errors": errors,
        "usd_per_million_tokens": {
            key: {
                "input": p.input,
                "output": p.output,
                "cache_read": p.cache_read,
                "cache_write": p.cache_write,
                "note": p.note,
            }
            for key, p in sorted(table.items())
        },
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_mission(args: argparse.Namespace) -> int:
    try:
        if (args.answer or args.answer_file) and not args.resume:
            raise MissionInvalid("--answer needs --resume")
        if args.resume:
            if Path(args.resume).name != args.resume or args.resume in {".", ".."}:
                raise MissionInvalid("--resume must be a mission directory name")
            home = conductor_home()
            mission_dir = home / "missions" / args.resume
            if not mission_dir.is_dir():
                raise MissionInvalid(f"mission '{args.resume}' does not exist")
            snapshot = mission_dir / "mission.json"
            if not snapshot.is_file():
                raise MissionInvalid(f"mission '{args.resume}' has no mission.json snapshot")
            try:
                raw = json.loads(snapshot.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise MissionInvalid(f"mission snapshot is invalid: {exc}") from exc
            mission = Mission.from_snapshot(raw)
            result = run_mission(
                mission,
                home=home,
                dry_run=args.dry_run,
                resume_dir=mission_dir,
                answer=args.answer,
                answer_file=args.answer_file,
                unattended=args.unattended,
            )
        else:
            mission = load_mission(args.file)
            result = run_mission(
                mission, home=conductor_home(), dry_run=args.dry_run, unattended=args.unattended
            )
    except MissionInvalid as exc:
        print(json.dumps({"invalid": str(exc)}, indent=2), file=sys.stderr)
        return 3
    print(json.dumps(result.summary(), indent=2))
    if result.paused and "answer" not in result.paused:
        # An `answer` already on `paused` (the operator said `stop`) is a
        # resolved, terminal result, not a mission still waiting on one.
        return 4
    return 0 if result.ok else 1


def cmd_missions(args: argparse.Namespace) -> int:
    missions_dir = conductor_home() / "missions"
    if not missions_dir.is_dir():
        print("[]")
        return 0
    entries = sorted((p for p in missions_dir.iterdir() if p.is_dir()), reverse=True)
    rows = []
    for path in entries[: args.limit]:
        result_file = path / "result.json"
        paused = False
        pause_file = path / "pause.json"
        if pause_file.is_file():
            try:
                pause_doc = json.loads(pause_file.read_text())
            except (OSError, json.JSONDecodeError):
                pause_doc = None
            if isinstance(pause_doc, dict):
                paused = pause_doc.get("answer") is None
        # E10 second spec: `parent`/`depth` are load-derived mission.json
        # fields (never in result.json), so a child's parent id is read off
        # its own snapshot, present whether or not the mission ever finished.
        parent_id = None
        mission_file = path / "mission.json"
        if mission_file.is_file():
            try:
                mission_raw = json.loads(mission_file.read_text())
            except (OSError, json.JSONDecodeError):
                mission_raw = None
            if isinstance(mission_raw, dict) and isinstance(mission_raw.get("parent"), dict):
                parent_id = mission_raw["parent"].get("mission_id")
        if not result_file.is_file():
            rows.append(
                {
                    "mission_id": path.name,
                    "status": "incomplete",
                    "resumes": 0,
                    "running": (path / "running.json").is_file(),
                    "paused": paused,
                    "parent": parent_id,
                    "children": 0,
                }
            )
            continue
        data = json.loads(result_file.read_text())
        collisions = data.get("collisions")
        resolve = data.get("resolve")
        if resolve is None:
            resolve_status = None
        elif not resolve.get("ran"):
            resolve_status = "skipped"
        else:
            resolve_status = "ok" if resolve.get("ok") else "failed"
        rows.append(
            {
                "mission_id": data["mission_id"],
                "ok": data.get("ok"),
                "lanes": [(lane["name"], lane["ok"]) for lane in data.get("lanes", [])],
                "cost_usd": round(data.get("cost_usd", 0), 4),
                "duration_s": round(data.get("duration_s", 0), 1),
                "wall_s": (data.get("wall") or {}).get("wall_s"),
                "resumes": len(data.get("resumes") or []),
                "running": (path / "running.json").is_file(),
                "report": data.get("report_path"),
                "paused": paused,
                "escalation": data.get("escalation"),
                "errors": data.get("errors"),
                "tainted": [
                    lane["name"] for lane in data.get("lanes", []) if lane.get("tainted")
                ],
                "hotspots": len(collisions["hotspots"]) if collisions else None,
                "resolve": resolve_status,
                "parent": parent_id,
                "children": len(data.get("children") or []),
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


def _invalid(reason: str) -> None:
    print(json.dumps({"invalid": reason}, indent=2), file=sys.stderr)


def cmd_attest(args: argparse.Namespace) -> int:
    """Verify a mission's signed receipt chain on bytes: every link's
    signature, its place in the hash chain, and, for a link with a run,
    that the run's own attestation still matches its result.json and diff."""
    mission_id = args.mission_id
    if Path(mission_id).name != mission_id or mission_id in {".", ".."}:
        _invalid("MISSION_ID must be a mission directory name")
        return 3
    home = conductor_home()
    try:
        out = attest.attest_mission(home, mission_id)
    except attest.AttestInvalid as exc:
        _invalid(str(exc))
        return 3
    print(json.dumps(out, indent=2))
    return 0 if out["verified"] else 1


def cmd_salvage(args: argparse.Namespace) -> int:
    """E23: re-run the clean gate from a kept lane's worktree by hand, and
    optionally emit the follow-on review-and-fix mission once the lead has
    committed what it showed."""
    mission_id = args.mission_id
    if Path(mission_id).name != mission_id or mission_id in {".", ".."}:
        _invalid("MISSION_ID must be a mission directory name")
        return 3
    if args.emit and (args.items is None or args.modules is None):
        _invalid("--emit needs --items and --modules")
        return 3

    home = conductor_home()
    try:
        result = salvage_mod.salvage(home, mission_id, args.lane)
    except salvage_mod.SalvageInvalid as exc:
        _invalid(str(exc))
        return 3

    passed = _runner_gate_passed(result.gate, None) and _runner_gate_passed(
        result.own_gate, None
    )
    if args.json:
        # The receipt on disk, not `result.to_dict()`: the two differ
        # (`recorded_at` is only ever known once the receipt is written;
        # `receipt_path` is only ever known once it isn't). `--json` shows
        # the caller exactly what `<home>/missions/.../salvage/...json` holds.
        print(Path(result.receipt_path).read_text().rstrip("\n"))
    else:
        print(f"worktree: {result.worktree}")
        print(f"base_sha: {result.base_sha}")
        print(f"head_sha: {result.head_sha}")
        print(f"dirty: {result.dirty}")
        print("--- diff ---")
        print(result.diff)
        print(f"gate: {result.test_command}")
        print(f"own gate exit code: {result.own_gate.get('exit_code')}")
        print(result.own_gate.get("tail", ""))
        print(f"clean gate exit code: {result.gate.get('exit_code')}")
        print(result.gate.get("tail", ""))
        print(f"receipt: {result.receipt_path}")

    if args.emit:
        if not passed:
            _invalid("--emit refused: a gate is red")
            return 3
        try:
            ceiling = shape.parse_ceiling(args.ceiling)
            caps = shape.cap_arithmetic(args.items, args.modules, scheduler=args.scheduler)
            snapshot = json.loads((home / "missions" / mission_id / "mission.json").read_text())
            spec_prompt = snapshot.get("prompt") if isinstance(snapshot, dict) else None
            salvage_mod.emit(
                result,
                Path(args.emit),
                test=result.test_command,
                caps=caps,
                name=args.name or f"salvage-{mission_id}-{args.lane}",
                branch=args.branch or "",
                fix_commit=args.fix_commit or "",
                about=args.about,
                spec_prompt=spec_prompt if isinstance(spec_prompt, str) else "",
                ceiling=ceiling,
            )
        except (salvage_mod.SalvageInvalid, shape.ShapeInvalid, MissionInvalid, OSError) as exc:
            _invalid(str(exc))
            return 3
        print(f"conductor mission {args.emit}")
        return 0

    return 0 if passed else 1


def _default_land_checkout(home: Path, mission_id: str, lane: str) -> str:
    """The lane's repository: its own recorded `cwd`, else the mission
    snapshot's `cwd`. Never raises -- an unreadable or missing snapshot
    just falls through to an empty string, and `land()` itself refuses with
    a proper reason once it checks the mission and lane exist."""
    mission_file = home / "missions" / mission_id / "mission.json"
    try:
        mission_raw = json.loads(mission_file.read_text())
    except (OSError, json.JSONDecodeError):
        mission_raw = {}
    mission_cwd = mission_raw.get("cwd") if isinstance(mission_raw, dict) else None

    lane_file = home / "missions" / mission_id / "lanes" / f"{lane}.json"
    try:
        lane_raw = json.loads(lane_file.read_text())
    except (OSError, json.JSONDecodeError):
        lane_raw = {}
    lane_cwd = lane_raw.get("cwd") if isinstance(lane_raw, dict) else None

    checkout = lane_cwd or mission_cwd
    return checkout if isinstance(checkout, str) else ""


def cmd_land(args: argparse.Namespace) -> int:
    """F7: the lead's hands after the diff is read and the reviewers have
    covered it -- merge a lane's branch, gate the merged head in a fresh
    worktree, run `golden check`, and attest the mission."""
    mission_id = args.mission_id
    if Path(mission_id).name != mission_id or mission_id in {".", ".."}:
        _invalid("MISSION_ID must be a mission directory name")
        return 3

    home = conductor_home()
    checkout = args.checkout or _default_land_checkout(home, mission_id, args.lane)
    try:
        result = land_mod.land(
            mission_id,
            args.lane,
            home=home,
            checkout=checkout,
            gate_command=args.test,
            dry_run=args.dry_run,
        )
    except land_mod.LandInvalid as exc:
        _invalid(str(exc))
        return 3

    if args.json:
        if result.receipt_path:
            print(Path(result.receipt_path).read_text().rstrip("\n"))
        else:
            print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"mission: {result.mission}  lane: {result.lane}  branch: {result.branch}")
        print(f"checkout: {result.checkout}")
        if result.already_merged:
            print("already merged")
        elif result.dry_run:
            print(f"gate: {result.gate_command}")
            print("would merge:")
            for line in result.would_merge:
                print(f"  {line}")
        else:
            for step in result.steps:
                mark = "ok " if step["ok"] else "RED"
                print(f"[{mark}] {step['name']}")
                if not step["ok"]:
                    print(step["detail"])
            if result.reset:
                print(f"checkout reset to pre-merge HEAD {result.pre_merge_sha}")
        print(f"receipt: {result.receipt_path}")

    if result.dry_run or result.already_merged:
        return 0
    return 0 if result.ok else 1


LIVENESS_STALE_S: int = 30


def _is_pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


_RESULT_FIELDS = {f.name for f in dataclasses.fields(Result)}


def _kind_from_legacy_receipt(data: dict) -> str | None:
    """`kind` is computed, not stored, so a receipt written before it existed
    (no `kind` key on disk at all) must still classify here instead of
    reading back as `null`. Rebuild just enough of a `Result` from the raw
    receipt fields to run it back through `error_kind`."""
    try:
        result = Result(**{k: v for k, v in data.items() if k in _RESULT_FIELDS})
    except TypeError:
        return None
    return error_kind(result)


def cmd_runs(args: argparse.Namespace) -> int:
    """List recent dispatches. The run directory is the audit trail."""
    runs_dir = conductor_home() / "runs"
    if not runs_dir.is_dir():
        print("[]")
        return 0
    entries = sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True)
    rows = []
    for path in entries[: args.limit]:
        result_file = path / "result.json"
        if not result_file.is_file():
            liveness_file = path / "liveness.json"
            if liveness_file.is_file():
                try:
                    live = json.loads(liveness_file.read_text())
                except (OSError, json.JSONDecodeError):
                    rows.append({"run_id": path.name, "status": "incomplete"})
                    continue
                if not isinstance(live, dict):
                    rows.append({"run_id": path.name, "status": "incomplete"})
                    continue
                at_str = live.get("at")
                heartbeat_age = 0.0
                if isinstance(at_str, str):
                    try:
                        heartbeat_dt = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
                        if heartbeat_dt.tzinfo is None:
                            heartbeat_dt = heartbeat_dt.replace(tzinfo=UTC)
                        heartbeat_age = max(
                            0.0, (datetime.now(UTC) - heartbeat_dt).total_seconds()
                        )
                    except ValueError:
                        pass
                status = "silent" if heartbeat_age > LIVENESS_STALE_S else "running"
                has_breaker = "tool_calls" in live
                rows.append(
                    {
                        "run_id": path.name,
                        "status": status,
                        "heartbeat_age_s": round(heartbeat_age, 1),
                        "elapsed_s": live.get("elapsed_s"),
                        "pid": live.get("pid"),
                        "pid_alive": _is_pid_alive(live.get("pid")),
                        "spend_usd": live.get("spend_usd"),
                        "tool_calls": live.get("tool_calls") if has_breaker else None,
                        "last_output_age_s": (
                            live.get("last_output_age_s") if has_breaker else None
                        ),
                        "last_tool_call_age_s": (
                            live.get("last_tool_call_age_s") if has_breaker else None
                        ),
                    }
                )
                continue
            rows.append({"run_id": path.name, "status": "incomplete"})
            continue
        data = json.loads(result_file.read_text())
        git_verdict = data.get("git_verdict")
        if not isinstance(git_verdict, dict):
            legacy = data.get("verdict")
            git_verdict = legacy if isinstance(legacy, dict) and "checked" in legacy else {}
        rows.append(
            {
                "run_id": data["run_id"],
                "ok": data.get("ok"),
                "kind": data["kind"] if "kind" in data else _kind_from_legacy_receipt(data),
                "fleet": data["fleet"],
                "model": data["model"],
                "session_id": data.get("session_id"),
                "exit_code": data["exit_code"],
                "no_op": git_verdict.get("no_op"),
                "duration_s": round(data.get("duration_s", 0), 1),
                "tool_calls": (data.get("breaker") or {}).get("tool_calls", 0),
                "taint": data.get("taint") is not None,
                "agent": (data.get("agent") or {}).get("name"),
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


def cmd_golden_record(args: argparse.Namespace) -> int:
    home = conductor_home()
    mission_dir = home / "missions" / args.mission_id
    try:
        fixture = golden.record(
            mission_dir, Path(args.out), home=home, max_bytes=args.max_bytes
        )
    except golden.GoldenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    total = sum(f.stat().st_size for f in fixture.rglob("*") if f.is_file())
    print(json.dumps({"fixture": str(fixture), "bytes": total}, indent=2))
    return 0


def cmd_golden_check(args: argparse.Namespace) -> int:
    if args.dirs:
        fixture_dirs = [Path(d) for d in args.dirs]
    else:
        golden_root = Path("tests/golden")
        fixture_dirs = (
            sorted({p.parent for p in golden_root.glob("*/golden.json")})
            if golden_root.is_dir()
            else []
        )
    any_diff = False
    for fixture_dir in fixture_dirs:
        notes: list[str] = []
        try:
            diffs = golden.check(fixture_dir, update=args.update, notes=notes)
        except (golden.GoldenError, OSError, ValueError) as exc:
            print(f"{fixture_dir.name}: {exc}")
            any_diff = True
            continue
        for line in diffs:
            print(f"{fixture_dir.name}: {line}")
        if diffs:
            any_diff = True
        # D18/E22: a contract field the recording never carried, and version
        # drift, are notes -- printed alongside the projection diffs but
        # never added to any_diff.
        for line in notes:
            print(f"{fixture_dir.name}: {line}")
        for line in golden.version_drift(fixture_dir):
            print(f"{fixture_dir.name}: {line}")
    return 1 if any_diff else 0


def cmd_export(args: argparse.Namespace) -> int:
    """E13: write a scrubbed, manifest-checked receipt bundle for a mission
    (`conductor export MISSION_ID --out DIR`), or verify one against its own
    manifest with nothing but the bundle (`conductor export --check DIR`)."""
    if args.check:
        result = export_mod.check(Path(args.check))
        not_verifiable_here: list = []
        try:
            manifest = json.loads((Path(args.check) / "manifest.json").read_text())
            if isinstance(manifest, dict) and isinstance(manifest.get("not_verifiable_here"), list):
                not_verifiable_here = manifest["not_verifiable_here"]
        except (OSError, json.JSONDecodeError):
            pass
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": result.ok,
                        "problems": result.problems,
                        "files_checked": result.files_checked,
                        "links_checked": result.links_checked,
                        "not_verifiable_here": not_verifiable_here,
                    },
                    indent=2,
                )
            )
        else:
            print(f"ok: {result.ok}")
            print(f"files checked: {result.files_checked}")
            print(f"links checked: {result.links_checked}")
            for problem in result.problems:
                print(f"- {problem}")
            if not_verifiable_here:
                print(f"not verifiable here: {', '.join(not_verifiable_here)}")
        return 0 if result.ok else 1

    if not args.mission_id or not args.out:
        _invalid("MISSION_ID and --out are required unless --check is given")
        return 3

    home = conductor_home()
    try:
        result = export_mod.export(home, args.mission_id, Path(args.out), logs=args.logs)
    except export_mod.ExportError as exc:
        if exc.leaks:
            print(json.dumps({"invalid": str(exc), "leaks": exc.leaks}, indent=2), file=sys.stderr)
            return 1
        _invalid(str(exc))
        return 3

    verified, total = result.attestations_verified_at_export
    if args.json:
        print(
            json.dumps(
                {
                    "bundle_dir": str(result.bundle_dir),
                    "files": result.files,
                    "bytes": result.bytes,
                    "chain_state_at_export": result.chain_state_at_export,
                    "chain_verified_at_export": result.chain_verified_at_export,
                    "attestations_verified_at_export": [verified, total],
                    "leaks": result.leaks,
                },
                indent=2,
            )
        )
    else:
        print(f"bundle: {result.bundle_dir}")
        print(f"files: {result.files}, bytes: {result.bytes}")
        print(f"chain at export: {result.chain_state_at_export}")
        print(f"chain verified at export: {result.chain_verified_at_export}")
        print(f"attestations verified at export: {verified}/{total}")
    return 0


def _write_lane_prompts(raw: dict, base_dir: Path) -> None:
    """E17: the prompts/ convention. Each lane's prompt text becomes its own
    file beside the mission, `prompt_file`-referenced instead of inlined, so
    the prompts a mission ran with are under version control next to the
    spec and an edit to one is a diff."""
    prompts_dir = base_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for lane in raw["lanes"]:
        prompt_text = lane.pop("prompt", None)
        if prompt_text is None:
            continue
        (prompts_dir / f"{lane['name']}.md").write_text(prompt_text)
        lane["prompt_file"] = f"prompts/{lane['name']}.md"


def _ceiling_line(raw_ceiling: dict) -> str:
    per_hour = raw_ceiling["per_hour_usd"]
    per_day = raw_ceiling["per_day_usd"]
    hour = "none" if per_hour is None else f"${per_hour:.2f}"
    day = "none" if per_day is None else f"${per_day:.2f}"
    return f"ceiling: per_hour {hour}, per_day {day}"


def cmd_shape_a(args: argparse.Namespace) -> int:
    """E5: write a Shape A mission from a spec, print the cap arithmetic, validate it."""
    try:
        ceiling = shape.parse_ceiling(args.ceiling)
        caps = shape.cap_arithmetic(
            args.items,
            args.modules,
            scheduler=args.scheduler,
            grok_runs_suite=args.grok_runs_suite,
            cap_grace_usd=args.cap_grace_usd,
            adversarial=args.adversarial,
            tests_items=args.tests_items,
            findings=args.findings,
            opus_review=args.opus_review,
        )
        out = Path(args.out).expanduser().resolve() if args.out else None
        mission_dir = out.parent if out else None
        raw = shape.shape_a(
            spec=Path(args.spec),
            repo=Path(args.repo),
            test=args.test,
            caps=caps,
            name=args.name or "",
            about=args.about,
            ports=args.ports,
            test_policy=args.test_policy,
            mission_dir=mission_dir,
            branch=args.branch or "",
            build_commit=args.build_commit or "",
            fix_commit=args.fix_commit or "",
            adversarial=args.adversarial,
            ceiling=ceiling,
            opus_review=args.opus_review,
        )
        base_dir = mission_dir or Path(args.spec).expanduser().resolve().parent
        if out is None:
            out = base_dir / "mission.json"
        if out.exists() and not args.force:
            print(
                json.dumps({"invalid": f"{out} exists; pass --force to overwrite"}),
                file=sys.stderr,
            )
            return 3
        # Cross-vendor review (Grok): the preflight runs before anything is
        # written beside the mission file, so a refused launch leaves no
        # prompts/*.md behind for the lead to clean up.
        if args.skip_preflight:
            preflight_note = "gate preflight: skipped (--skip-preflight)"
        else:
            shape.gate_preflight(Path(args.repo), args.test)
            preflight_note = "gate preflight: passed"
        if not args.inline:
            _write_lane_prompts(raw, base_dir)
        # F15 mission 2 item 2: a schema is a path, not a prompt -- written
        # beside the mission file whether or not --inline was given.
        shape.write_dispositions_schema(base_dir)
        mission = mission_from_dict(raw, base_dir=base_dir, source=str(out))
    except (shape.ShapeInvalid, MissionInvalid) as exc:
        print(json.dumps({"invalid": str(exc)}, indent=2), file=sys.stderr)
        return 3
    print(f"shape {shape.SHAPE_VERSION}: {len(mission.lanes)} lanes, mission '{mission.name}'")
    print(preflight_note)
    print(_ceiling_line(raw["ceiling"]))
    print(caps.render())
    # E14: a low cap is easier to fix before the launch than after it, so
    # this is printed against the real conductor home right here -- whether
    # or not --dry-run is also given (that branch's own JSON carries the
    # same block, via MissionResult.summary()).
    fc = forecast_mod.forecast(mission, conductor_home())
    print("forecast:")
    for lane_fc in fc.lanes:
        marker = " [warn]" if lane_fc.warn else ""
        cap = "n/a" if lane_fc.cap_usd is None else f"${lane_fc.cap_usd:.2f}"
        median = "n/a" if lane_fc.median_usd is None else f"${lane_fc.median_usd:.2f}"
        p80 = "n/a" if lane_fc.p80_usd is None else f"${lane_fc.p80_usd:.2f}"
        print(
            f"  {lane_fc.lane}: cap {cap}, {lane_fc.runs} {lane_fc.vendor} runs, "
            f"median {median}, p80 {p80}{marker}"
        )
    for warning in fc.warnings:
        print(f"  warning: {warning}")
    print("prompt versions:")
    for prompt_name, version_id in sorted(prompts.prompt_versions().items()):
        print(f"  {prompt_name}: {version_id}")
    text = json.dumps(raw, indent=2) + "\n"
    out.write_text(text)
    print(f"wrote {out}")
    if args.dry_run:
        result = run_mission(load_mission(out), home=conductor_home(), dry_run=True)
        print(json.dumps(result.summary(), indent=2))
        return 0 if result.ok else 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conductor",
        description="One dispatch contract across the Claude Code, Codex, "
        "Antigravity, and Cursor fleets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fleets = sub.add_parser("fleets", help="show routing policy and install state")
    p_fleets.add_argument("--json", action="store_true")
    p_fleets.set_defaults(func=cmd_fleets)

    p_dispatch = sub.add_parser("dispatch", help="run one prompt on one fleet")
    p_dispatch.add_argument("prompt", nargs="?", default="")
    p_dispatch.add_argument("--prompt-file")
    p_dispatch.add_argument("--fleet", required=True, choices=sorted(FLEETS))
    p_dispatch.add_argument("--model", help="fleet-local model name, e.g. opus, sol, grok-4.6")
    p_dispatch.add_argument("--effort", default="standard", choices=EFFORTS)
    p_dispatch.add_argument("--mode", default="read", choices=MODES)
    p_dispatch.add_argument("--cwd", default=".", help="the fleet's working directory")
    p_dispatch.add_argument("--timeout", type=int, help="seconds; per-mode default otherwise")
    p_dispatch.add_argument(
        "--stall-timeout",
        type=int,
        default=600,
        metavar="SECONDS",
        help="kill after this many seconds without stdout growth; 0 disables",
    )
    p_dispatch.add_argument(
        "--loop-limit",
        type=int,
        default=6,
        metavar="N",
        help="kill after N identical consecutive tool calls; 0 disables",
    )
    p_dispatch.add_argument(
        "--max-tool-calls",
        type=int,
        metavar="N",
        help="kill after more than N tool calls; 0 disables",
    )
    p_dispatch.add_argument(
        "--tool-idle-timeout",
        type=int,
        metavar="SECONDS",
        help="kill after this many seconds without a tool call; 0 disables",
    )
    p_dispatch.add_argument("--schema", help="JSON Schema path for the final message")
    p_dispatch.add_argument(
        "--deliverable",
        metavar="PATH",
        help="repo-relative file this dispatch must produce; checked on disk after it exits",
    )
    p_dispatch.add_argument(
        "--deliverable-schema",
        metavar="FILE",
        help="JSON Schema path the deliverable must satisfy; needs --deliverable",
    )
    p_dispatch.add_argument("--resume", metavar="SESSION_ID", help="resume a fleet session")
    p_dispatch.add_argument(
        "--verdict",
        action="append",
        default=[],
        metavar="ID",
        help="repeatable checklist id; the question defaults to 'Is <id> satisfied?'",
    )
    p_dispatch.add_argument(
        "--verdict-file",
        metavar="PATH",
        help="JSON checklist appended after any --verdict ids",
    )
    p_dispatch.add_argument(
        "--cap-usd",
        type=float,
        help="per-dispatch dollar cap: claude stops itself, codex and antigravity are killed "
        "when their running usage prices over it, cursor is judged after the run",
    )
    p_dispatch.add_argument(
        "--cap-grace-usd",
        type=float,
        help="claude, or cursor in read mode: a band on top of --cap-usd so claude's own "
        "terminal message can finish and a complete cursor answer a few cents over cap "
        f"still settles ok, ceiling ${CAP_GRACE_CEILING_USD:.2f}",
    )
    p_dispatch.add_argument("--test", help="gate to run after the dispatch, in --cwd")
    p_dispatch.add_argument("--test-policy", choices=TEST_POLICIES, default="clean")
    p_dispatch.add_argument(
        "--stage",
        choices=sorted(STAGES),
        help="pipeline stage; a 'fix' dispatch in write mode runs the reproduce-before-fix gate",
    )
    p_dispatch.add_argument(
        "--test-surface",
        action="append",
        metavar="PATTERN",
        help="replace the default test surface with this repeatable Git pathspec glob",
    )
    p_dispatch.add_argument(
        "--commit",
        metavar="MSG",
        help="conductor commits the dispatch's work itself, uniformly across fleets "
        "(Codex's sandbox cannot write .git, the others commit on their own)",
    )
    p_dispatch.add_argument(
        "--isolate",
        action="store_true",
        help="run in a fresh git worktree on branch conductor/<run_id>; the worktree is "
        "removed afterwards if clean, kept and reported if it holds uncommitted work",
    )
    p_dispatch.add_argument(
        "--ports",
        type=int,
        default=0,
        metavar="N",
        help="claim N free TCP ports before spawning; exported as CONDUCTOR_PORT_1.. and "
        "CONDUCTOR_PORTS to the fleet, --setup, --teardown, and the gate",
    )
    p_dispatch.add_argument(
        "--setup",
        metavar="CMD",
        help="shell command run in the worktree before the fleet spawns; a nonzero exit, "
        "a timeout, or a stop request means the fleet is never spawned",
    )
    p_dispatch.add_argument(
        "--teardown",
        metavar="CMD",
        help="shell command run after the gate, ok or not; its outcome is a note, never a "
        "reason to flip the verdict",
    )
    p_dispatch.add_argument(
        "--include",
        action="append",
        metavar="PATH",
        help="repeatable, repo-relative untracked path copied into the isolated worktree "
        "before --setup; refused if Git already tracks it",
    )
    p_dispatch.add_argument(
        "--taint",
        action="store_true",
        help="this dispatch handles text pulled from outside the operator's trust; "
        "claude and antigravity only, runs with a tool deny list (no web, no subagents, "
        "no shell)",
    )
    p_dispatch.add_argument(
        "--taint-shell",
        choices=list(TAINT_SHELL_MODES),
        default="deny",
        help="what a tainted dispatch may do with a shell: 'deny' (default, the boundary) "
        "or 'allow', the opt-in that runs the old command-prefix list instead -- a "
        "discouragement, not a boundary (README, 'Taint')",
    )
    p_dispatch.add_argument(
        "--agent-file",
        metavar="PATH",
        help="JSON file holding an inline agent {name, description, prompt, tools}; "
        "claude only, asserted against the run's own init event",
    )
    p_dispatch.add_argument(
        "--restricted",
        action="store_true",
        help="claude read lane only; runs under --restricted --permission-mode acceptEdits "
        "(no Bash, no WebFetch, file tools confined to cwd) instead of plan mode",
    )
    p_dispatch.add_argument("--dry-run", action="store_true", help="print argv, spawn nothing")
    p_dispatch.set_defaults(func=cmd_dispatch)

    p_prices = sub.add_parser("prices", help="show the effective per-model price table")
    p_prices.set_defaults(func=cmd_prices)

    p_spend = sub.add_parser("spend", help="summarize cost and usage from run receipts")
    p_spend.add_argument("--since", help="inclusive UTC date or ISO datetime")
    p_spend.add_argument("--until", help="exclusive UTC date or ISO datetime")
    p_spend.add_argument(
        "--by", choices=("day", "fleet", "model", "mission", "run"), default="fleet"
    )
    p_spend.add_argument("--json", action="store_true")
    p_spend.set_defaults(func=cmd_spend)

    p_report = sub.add_parser(
        "report", help="the ledger report: AGENTS.md's Shape A rules as numbers from receipts"
    )
    p_report.add_argument("--since", help="inclusive UTC date or ISO datetime")
    p_report.add_argument("--until", help="exclusive UTC date or ISO datetime")
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_mission = sub.add_parser(
        "mission",
        help="run a mission file: one prompt fanned out to N lanes with fallbacks, "
        "a concurrency cap, a dollar budget, worktree isolation, and one report",
    )
    mission_source = p_mission.add_mutually_exclusive_group(required=True)
    mission_source.add_argument("file", nargs="?", help="mission .json or .toml")
    mission_source.add_argument(
        "--resume",
        metavar="MISSION_ID",
        help="resume a mission directory under CONDUCTOR_HOME/missions",
    )
    p_mission.add_argument(
        "--dry-run", action="store_true", help="validate and record argv, spawn nothing"
    )
    p_mission.add_argument(
        "--unattended",
        action="store_true",
        help="refuse to run unless it is safe with nobody reading: no human lane, no "
        "unstaged write lane, no fix-stage write lane outside pause.before, no resolve",
    )
    answer_group = p_mission.add_mutually_exclusive_group()
    answer_group.add_argument(
        "--answer",
        help="answer a paused mission: 'continue' or 'stop' for a pause.before lane or "
        "pause.spend_usd threshold, any text for a human lane's ask, or 'stop' for one "
        "too; needs --resume",
    )
    answer_group.add_argument(
        "--answer-file",
        metavar="PATH",
        help="answer a human lane's ask with a file's contents instead of --answer TEXT; "
        "needs --resume",
    )
    p_mission.set_defaults(func=cmd_mission)

    p_shape = sub.add_parser(
        "shape", help="write a mission file from a versioned shape and print its cap arithmetic"
    )
    shape_sub = p_shape.add_subparsers(dest="shape_name", required=True)
    p_shape_a = shape_sub.add_parser(
        "a",
        help="Shape A: Sonnet builds, Gemini and Grok review cold, Sonnet fixes, "
        "caps sized by AGENTS.md rules 2 and 10",
    )
    p_shape_a.add_argument("--spec", required=True, help="spec file the build lane implements")
    p_shape_a.add_argument("--repo", required=True, help="git repository the mission runs in")
    p_shape_a.add_argument(
        "--test", required=True, help="the gate command; run in every lane's worktree"
    )
    p_shape_a.add_argument(
        "--items", type=int, required=True, help="hand-counted spec items (rule 2)"
    )
    p_shape_a.add_argument(
        "--modules",
        type=int,
        required=True,
        help="hand-counted source modules the spec touches (rule 2: a dollar past the second)",
    )
    p_shape_a.add_argument(
        "--scheduler",
        action="store_true",
        help="the spec touches the scheduler, the runner wait loop, or resume (rule 2: +$2)",
    )
    p_shape_a.add_argument(
        "--grok-runs-suite",
        action="store_true",
        help="let Grok run the gate (rule 7: cap $2.00 instead of $1.50)",
    )
    p_shape_a.add_argument(
        "--cap-grace-usd",
        type=float,
        default=shape.USD_CLAUDE_GRACE,
        help=f"grace band on the build and fix (claude) lanes and the review-grok (cursor "
        f"read) lane (E24/F5); 0 disables it, ceiling ${CAP_GRACE_CEILING_USD:.2f} "
        f"(default ${shape.USD_CLAUDE_GRACE:.2f})",
    )
    p_shape_a.add_argument(
        "--adversarial",
        action="store_true",
        help="add an adversarial lane that writes a test failing on the build's tip (E16); "
        "the fix lane builds on it and inherits a reproduced check",
    )
    p_shape_a.add_argument(
        "--opus-review",
        action="store_true",
        help="Shape C (F9): add Opus 5 at hard as a third cold reviewer beside Gemini and "
        "Grok, at the $4.00 cap; the mission carries self_judging: allow because the build "
        "is Sonnet, and the fix lane reads all three reviews",
    )
    p_shape_a.add_argument(
        "--tests-items",
        type=int,
        default=0,
        metavar="N",
        help="spec items, already counted in --items, that are tests; counted twice in the "
        "build cap (rule 11)",
    )
    p_shape_a.add_argument(
        "--findings",
        type=int,
        default=shape.DEFAULT_FINDINGS,
        metavar="N",
        help="expected review findings, at $1.00 each on the fix cap "
        f"(default {shape.DEFAULT_FINDINGS}, the median Grok finding count on this repository)",
    )
    p_shape_a.add_argument(
        "--ceiling",
        default="none",
        metavar="none|default|H,D",
        help="the mission's E9 rolling-spend ceiling: 'none' (default) writes null bounds -- "
        "an attended launch is watched, so the ceiling follows --unattended, not this "
        "launcher; 'default' copies ceiling.py's own constants; 'H,D' sets both explicitly",
    )
    p_shape_a.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip running the gate command once in a throwaway worktree before writing "
        "the mission file",
    )
    p_shape_a.add_argument("--name", help="mission name (default: the spec's stem)")
    p_shape_a.add_argument(
        "--about", help="one phrase naming the repo for the shared prefix, e.g. 'the X service'"
    )
    p_shape_a.add_argument("--ports", type=int, default=0, help="TCP ports the build lane claims")
    p_shape_a.add_argument("--branch", help="branch the fix lane lands on (default feat/<name>)")
    p_shape_a.add_argument("--build-commit", help="build lane commit message")
    p_shape_a.add_argument("--fix-commit", help="fix lane commit message")
    p_shape_a.add_argument(
        "--test-policy",
        choices=TEST_POLICIES,
        default="allow",
        help="test-surface policy on the build and fix lanes (default allow; rule 3)",
    )
    p_shape_a.add_argument(
        "--out", help="mission file to write (default: mission.json beside the spec)"
    )
    p_shape_a.add_argument("--force", action="store_true", help="overwrite an existing --out")
    p_shape_a.add_argument(
        "--dry-run", action="store_true", help="also run `conductor mission --dry-run` on it"
    )
    p_shape_a.add_argument(
        "--inline",
        action="store_true",
        help="keep every lane's prompt inline in the mission file instead of writing "
        "prompts/<lane>.md beside it",
    )
    p_shape_a.set_defaults(func=cmd_shape_a)

    p_missions = sub.add_parser("missions", help="list recent missions")
    p_missions.add_argument("--limit", type=int, default=20)
    p_missions.set_defaults(func=cmd_missions)

    p_verify = sub.add_parser("verify", help="inspect repo state, optionally run a gate")
    p_verify.add_argument("--cwd", default=".")
    p_verify.add_argument("--test")
    p_verify.set_defaults(func=cmd_verify)

    p_runs = sub.add_parser("runs", help="list recent dispatches")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)

    p_gc = sub.add_parser("gc", help="plan safe worktree and conductor-branch cleanup")
    p_gc.add_argument("--repo", action="append", default=[], metavar="PATH")
    p_gc.add_argument("--older-than", type=float, default=0, metavar="HOURS")
    p_gc.add_argument("--apply", action="store_true")
    p_gc.set_defaults(func=cmd_gc)

    p_attest = sub.add_parser(
        "attest", help="verify a mission's signed receipt chain on bytes"
    )
    p_attest.add_argument("mission_id", metavar="MISSION_ID")
    p_attest.set_defaults(func=cmd_attest)

    p_salvage = sub.add_parser(
        "salvage",
        help="gate a mission lane's kept worktree by hand (AGENTS.md rule 6), and "
        "optionally emit the follow-on review-and-fix mission",
    )
    p_salvage.add_argument("mission_id", metavar="MISSION_ID")
    p_salvage.add_argument("--lane", required=True, help="the kept lane's name")
    p_salvage.add_argument(
        "--emit", metavar="PATH", help="write a follow-on mission file here; needs a green gate"
    )
    p_salvage.add_argument(
        "--items", type=int, help="hand-counted spec items for the fix cap (rule 2); needs --emit"
    )
    p_salvage.add_argument(
        "--modules", type=int, help="hand-counted modules touched (rule 2); needs --emit"
    )
    p_salvage.add_argument(
        "--scheduler", action="store_true", help="rule 2: +$2 fix cap"
    )
    p_salvage.add_argument(
        "--name", help="follow-on mission name (default: salvage-<mission>-<lane>)"
    )
    p_salvage.add_argument("--branch", help="branch the fix lane lands on (default feat/<name>)")
    p_salvage.add_argument("--fix-commit", help="fix lane commit message")
    p_salvage.add_argument(
        "--about", help="one phrase naming the repo for the shared prefix, e.g. 'the X service'"
    )
    p_salvage.add_argument(
        "--ceiling",
        default="none",
        metavar="none|default|H,D",
        help="the follow-on mission's E9 rolling-spend ceiling; see 'shape a --ceiling'",
    )
    p_salvage.add_argument("--json", action="store_true")
    p_salvage.set_defaults(func=cmd_salvage)

    p_land = sub.add_parser(
        "land",
        help="F7: merge a lane's branch, gate the merged head, run golden check, and "
        "attest the mission -- the lead's own act, never run inside a mission",
    )
    p_land.add_argument("mission_id", metavar="MISSION_ID")
    p_land.add_argument("--lane", required=True, help="the lane whose branch lands")
    p_land.add_argument(
        "--checkout", help="repo to merge into (default: the lane's own repository)"
    )
    p_land.add_argument(
        "--test", help="gate command (default: the mission snapshot's own test)"
    )
    p_land.add_argument(
        "--dry-run",
        action="store_true",
        help="run every refusal check and print what would merge; merges nothing",
    )
    p_land.add_argument("--json", action="store_true")
    p_land.set_defaults(func=cmd_land)

    p_golden = sub.add_parser(
        "golden", help="record and replay golden-mission fixtures offline (C7)"
    )
    golden_sub = p_golden.add_subparsers(dest="golden_command", required=True)

    p_golden_record = golden_sub.add_parser(
        "record", help="record a finished mission under CONDUCTOR_HOME as an offline fixture"
    )
    p_golden_record.add_argument("mission_id", metavar="MISSION_ID")
    p_golden_record.add_argument("--out", required=True, metavar="DIR")
    p_golden_record.add_argument(
        "--max-bytes", type=int, default=golden.DEFAULT_MAX_BYTES, metavar="N"
    )
    p_golden_record.set_defaults(func=cmd_golden_record)

    p_golden_check = golden_sub.add_parser(
        "check",
        help="replay every fixture under tests/golden (or the given directories) and "
        "compare against expected.json",
    )
    p_golden_check.add_argument("dirs", nargs="*", metavar="DIR")
    p_golden_check.add_argument(
        "--update", action="store_true", help="rewrite expected.json from the replay"
    )
    p_golden_check.set_defaults(func=cmd_golden_check)

    p_export = sub.add_parser(
        "export", help="export a scrubbed, manifest-checked receipt bundle for a mission (E13)"
    )
    p_export.add_argument("mission_id", nargs="?", metavar="MISSION_ID")
    p_export.add_argument("--out", metavar="DIR", help="write the bundle here")
    p_export.add_argument("--logs", action="store_true", help="include stdout.log/stderr.log")
    p_export.add_argument(
        "--check", metavar="DIR", help="verify a bundle against its own manifest.json instead"
    )
    p_export.add_argument("--json", action="store_true")
    p_export.set_defaults(func=cmd_export)

    return parser


def _stop_handler(exit_hook: Callable[[int], object] = os._exit) -> Callable[[int, object], None]:
    """Build the two-stage handler; the hook keeps the hard-exit path testable."""

    def on_signal(signum: int, frame: object) -> None:
        if stop_requested():
            kill_live_groups()
            exit_hook(130 if signum == signal.SIGINT else 143)
            return
        request_stop()

    return on_signal


def _install_stop_handlers(exit_hook: Callable[[int], object] = os._exit) -> None:
    """Ctrl-C or a `kill` ends the run cleanly instead of orphaning the fleet.

    The first signal asks every running dispatch to stop: each one kills
    its fleet's process group at its next poll, is priced and receipted,
    and releases its worktree; a mission skips what has not started and
    still writes its report. A second signal is the operator insisting: live
    groups are killed synchronously before the conventional exit status.
    """
    on_signal = _stop_handler(exit_hook)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, on_signal)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.func in (cmd_dispatch, cmd_mission, cmd_verify):
        _install_stop_handlers()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
