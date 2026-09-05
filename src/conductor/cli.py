"""conductor: one dispatch contract across four agent fleets."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import attest, prices
from .fleets import EFFORTS, FLEETS, MODES, TEST_POLICIES, DispatchRefused, Spec
from .gc import cmd_gc
from .mission import STAGES, Mission, MissionInvalid, load_mission, run_mission
from .paths import conductor_home
from .runner import Result, dispatch, kill_live_groups, request_stop, stop_requested
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
        print(f"[{mark}] {row['fleet']:<12} {row['binary']:<13} {row['vendor']}  cap: {row['cap']}")
        for m in row["models"]:
            default = " (default)" if m["name"] == row["default_model"] else ""
            note = f"  # {m['note']}" if m["note"] else ""
            print(f"          {m['name']}{default}{note}")
    missing = [r["fleet"] for r in rows if not r["installed"]]
    if missing:
        print(f"\nnot installed: {', '.join(missing)}", file=sys.stderr)
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
            test_policy=args.test_policy,
            test_surface=args.test_surface,
            stage=args.stage,
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
            )
        else:
            mission = load_mission(args.file)
            result = run_mission(mission, home=conductor_home(), dry_run=args.dry_run)
    except MissionInvalid as exc:
        print(json.dumps({"invalid": str(exc)}, indent=2), file=sys.stderr)
        return 3
    print(json.dumps(result.summary(), indent=2))
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
        if not result_file.is_file():
            rows.append(
                {
                    "mission_id": path.name,
                    "status": "incomplete",
                    "resumes": 0,
                    "running": (path / "running.json").is_file(),
                }
            )
            continue
        data = json.loads(result_file.read_text())
        rows.append(
            {
                "mission_id": data["mission_id"],
                "ok": data.get("ok"),
                "lanes": [(lane["name"], lane["ok"]) for lane in data.get("lanes", [])],
                "cost_usd": round(data.get("cost_usd", 0), 4),
                "duration_s": round(data.get("duration_s", 0), 1),
                "resumes": len(data.get("resumes") or []),
                "running": (path / "running.json").is_file(),
                "report": data.get("report_path"),
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


def _verify_run_attestation(
    home: Path, run_id: str, link_statement: dict, key: bytes
) -> list[str]:
    """Whether one mission link's run still checks out: its attestation.json
    is unmoved and verifies, and it agrees with the run's own result.json
    and diff.patch on the few things the mission link claims about it."""
    problems: list[str] = []
    run_dir = home / "runs" / run_id
    attestation_file = run_dir / "attestation.json"
    expected_sha = link_statement.get("attestation_sha256")
    actual_sha = attest.file_sha256(attestation_file)
    if actual_sha is None:
        problems.append(f"run '{run_id}': attestation.json is missing")
        return problems
    if expected_sha is not None and actual_sha != expected_sha:
        problems.append(f"run '{run_id}': attestation.json sha256 disagrees with the mission link")
    try:
        envelope = json.loads(attestation_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"run '{run_id}': attestation.json unreadable: {exc}")
        return problems
    statement, reason = attest.verify(envelope, key)
    if statement is None:
        problems.append(f"run '{run_id}': attestation signature: {reason}")
        return problems
    try:
        result_data = json.loads((run_dir / "result.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"run '{run_id}': result.json unreadable: {exc}")
        return problems
    if statement.get("ok") != result_data.get("ok"):
        problems.append(f"run '{run_id}': attestation ok disagrees with result.json")
    # Compare against the receipt's own `base_commit`/`tip_commit`, not a
    # reconstruction from `isolation`/`commit`: those are only set for an
    # isolated or landed dispatch, so a non-isolated read lane's real HEAD
    # would otherwise read back as a mismatch that never happened.
    if statement.get("base_commit") != result_data.get("base_commit"):
        problems.append(f"run '{run_id}': attestation base_commit disagrees with result.json")
    if statement.get("tip_commit") != result_data.get("tip_commit"):
        problems.append(f"run '{run_id}': attestation tip_commit disagrees with result.json")
    expected_digest = attest.file_sha256(run_dir / "diff.patch")
    if statement.get("source_diff_sha256") != expected_digest:
        problems.append(f"run '{run_id}': attestation source_diff_sha256 disagrees with diff.patch")
    return problems


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
    mission_dir = home / "missions" / mission_id
    chain_path = mission_dir / "receipts" / "chain.json"
    if not mission_dir.is_dir():
        _invalid(f"mission '{mission_id}' does not exist")
        return 3
    if not chain_path.is_file():
        _invalid(f"mission '{mission_id}' has no receipt chain")
        return 3
    key = attest.read_receipt_key(home)
    if key is None:
        _invalid("receipt key is missing")
        return 3
    try:
        chain = json.loads(chain_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _invalid(f"chain.json is invalid: {exc}")
        return 3
    if not isinstance(chain, dict) or not isinstance(chain.get("links"), list):
        _invalid("chain.json is malformed")
        return 3

    results: list[dict] = []
    previous_sha: str | None = None
    for entry in chain["links"]:
        index = entry.get("index") if isinstance(entry, dict) else None
        lane = entry.get("lane") if isinstance(entry, dict) else None
        recorded_sha = entry.get("sha256") if isinstance(entry, dict) else None
        path_str = entry.get("path") if isinstance(entry, dict) else None
        problems: list[str] = []
        run_id: str | None = None
        actual_sha: str | None = None
        link_path = Path(path_str) if isinstance(path_str, str) else None
        if link_path is None or not link_path.is_file():
            problems.append("link file missing")
        else:
            actual_sha = attest.file_sha256(link_path)
            if actual_sha != recorded_sha:
                problems.append("link file sha256 does not match chain.json")
            try:
                envelope = json.loads(link_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                envelope = None
                problems.append(f"link file is not valid JSON: {exc}")
            if envelope is not None:
                statement, reason = attest.verify(envelope, key)
                if statement is None:
                    problems.append(f"link signature: {reason}")
                else:
                    if statement.get("previous") != previous_sha:
                        problems.append("previous does not match the prior link")
                    run_id = statement.get("run_id")
                    if isinstance(run_id, str):
                        problems.extend(_verify_run_attestation(home, run_id, statement, key))
        results.append(
            {
                "index": index,
                "lane": lane,
                "run_id": run_id,
                "verified": not problems,
                "problems": problems,
            }
        )
        previous_sha = actual_sha

    out = {
        "mission_id": chain.get("mission_id", mission_id),
        "key_id": attest.key_id(key),
        "links": results,
        "verified": all(row["verified"] for row in results),
    }
    print(json.dumps(out, indent=2))
    return 0 if out["verified"] else 1


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
                "fleet": data["fleet"],
                "model": data["model"],
                "session_id": data.get("session_id"),
                "exit_code": data["exit_code"],
                "no_op": git_verdict.get("no_op"),
                "duration_s": round(data.get("duration_s", 0), 1),
                "tool_calls": (data.get("breaker") or {}).get("tool_calls", 0),
            }
        )
    print(json.dumps(rows, indent=2))
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
    p_mission.set_defaults(func=cmd_mission)

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
