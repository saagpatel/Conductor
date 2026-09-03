"""conductor: one dispatch contract across four agent fleets."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import prices
from .fleets import EFFORTS, FLEETS, MODES, DispatchRefused, Spec
from .mission import MissionInvalid, load_mission, run_mission
from .runner import Result, conductor_home, dispatch
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
        print(f"[{mark}] {row['fleet']:<12} {row['binary']:<13} {row['vendor']}")
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

    spec = Spec(
        fleet=args.fleet,
        prompt=prompt,
        cwd=str(Path(args.cwd).resolve()),
        model=args.model,
        effort=args.effort,
        mode=args.mode,
        timeout=args.timeout,
        schema=args.schema,
    )
    try:
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
        out["tests"] = run_tests(cwd, args.test).to_dict()
    print(json.dumps(out, indent=2))
    if args.test and out["tests"]["exit_code"] != 0:
        return 1
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    """Print the price table conductor will estimate with, after overrides."""
    table = prices.load_prices()
    out = {
        "as_of": prices.AS_OF,
        "override_file": str(conductor_home() / "prices.json"),
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
        mission = load_mission(args.file)
    except MissionInvalid as exc:
        print(json.dumps({"invalid": str(exc)}, indent=2), file=sys.stderr)
        return 3
    result = run_mission(mission, dry_run=args.dry_run)
    print(json.dumps(result.summary(), indent=2))
    if args.dry_run:
        return 0
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
            rows.append({"mission_id": path.name, "status": "incomplete"})
            continue
        data = json.loads(result_file.read_text())
        rows.append(
            {
                "mission_id": data["mission_id"],
                "ok": data.get("ok"),
                "lanes": [(lane["name"], lane["ok"]) for lane in data.get("lanes", [])],
                "cost_usd": round(data.get("cost_usd", 0), 4),
                "duration_s": round(data.get("duration_s", 0), 1),
                "report": data.get("report_path"),
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


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
            rows.append({"run_id": path.name, "status": "incomplete"})
            continue
        data = json.loads(result_file.read_text())
        rows.append(
            {
                "run_id": data["run_id"],
                "ok": data.get("ok"),
                "fleet": data["fleet"],
                "model": data["model"],
                "exit_code": data["exit_code"],
                "no_op": data.get("verdict", {}).get("no_op"),
                "duration_s": round(data.get("duration_s", 0), 1),
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
    p_dispatch.add_argument("--schema", help="JSON Schema path for the final message")
    p_dispatch.add_argument("--test", help="gate to run after the dispatch, in --cwd")
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

    p_mission = sub.add_parser(
        "mission",
        help="run a mission file: one prompt fanned out to N lanes with fallbacks, "
        "a concurrency cap, a dollar budget, worktree isolation, and one report",
    )
    p_mission.add_argument("file", help="mission .json or .toml")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
