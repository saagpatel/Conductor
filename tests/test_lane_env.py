"""C4: per-lane setup, teardown, and port allocation.

A worktree isolates files, not ports, sockets, scratch databases, or
gitignored config. These tests pin: ports are claimed exclusively and always
released, `setup` gates the fleet spawning at all, `teardown` never changes
the verdict, and `include` keeps a copied path out of the diff and the
commit while still being visible to the test-surface pin.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conductor import ports as ports_mod
from conductor import runner as runner_mod
from conductor.cli import build_parser, main
from conductor.fleets import Spec
from conductor.mission import mission_from_dict
from conductor.runner import dispatch

# --- ports -------------------------------------------------------------


def test_ports_are_distinct_exported_everywhere_and_released(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "env > fleet-env.out"])
    result = dispatch(
        Spec(
            fleet="claude",
            prompt="ports",
            cwd=str(repo),
            mode="write",
            ports=2,
            setup=f"ls {home}/ports > claims-during-setup.txt && env > setup-env.out",
            teardown="env > teardown-env.out",
        ),
        home=home,
        test_command="env > gate-env.out",
    )
    assert result.ok is True
    ports = result.lane_env["ports"]
    assert len(ports) == 2
    assert len(set(ports)) == 2

    claim_names = sorted((repo / "claims-during-setup.txt").read_text().split())
    assert claim_names == sorted(str(p) for p in ports)

    for name in ("setup-env.out", "fleet-env.out", "gate-env.out", "teardown-env.out"):
        env_text = (repo / name).read_text()
        assert f"CONDUCTOR_PORT_1={ports[0]}" in env_text
        assert f"CONDUCTOR_PORT_2={ports[1]}" in env_text
        assert f"CONDUCTOR_PORTS={ports[0]},{ports[1]}" in env_text
        assert f"CONDUCTOR_RUN_ID={result.run_id}" in env_text
        assert f"CONDUCTOR_WORKTREE={repo}" in env_text

    assert list((home / "ports").iterdir()) == []
    assert result.summary()["ports"] == ports


def test_ports_are_released_after_a_killed_run(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "sleep 60"])
    result = dispatch(
        Spec(fleet="claude", prompt="slow", cwd=str(repo), ports=1, timeout=1),
        home=home,
    )
    assert result.timed_out is True
    assert list((home / "ports").iterdir()) == []


def test_lane_resources_are_released_when_dispatch_raises_mid_run(
    repo, home, fake_fleet, monkeypatch
):
    """Ports (and the include exclude file) must be released on every path,
    per spec item 1 -- including a crash inside dispatch's own bookkeeping,
    not just the ordinary success and refusal paths."""
    fake_fleet(["sh", "-c", "echo work > new.txt"])

    def boom(cwd, before, after):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_mod, "compare", boom)

    with pytest.raises(RuntimeError):
        dispatch(
            Spec(fleet="claude", prompt="p", cwd=str(repo), mode="write", ports=1),
            home=home,
        )

    assert list((home / "ports").iterdir()) == []


def test_run_id_and_worktree_env_vars_exported_with_and_without_isolation(
    repo, home, fake_fleet
):
    fake_fleet(["sh", "-c", "env > env.out"])
    result = dispatch(
        Spec(fleet="claude", prompt="env", cwd=str(repo), mode="write"), home=home
    )
    env_text = (repo / "env.out").read_text()
    assert f"CONDUCTOR_RUN_ID={result.run_id}" in env_text
    assert f"CONDUCTOR_WORKTREE={repo}" in env_text

    fake_fleet(["sh", "-c", "env > env-iso.out"])
    result2 = dispatch(
        Spec(fleet="claude", prompt="env2", cwd=str(repo), mode="write"),
        isolate=True,
        home=home,
    )
    iso = result2.isolation
    assert iso["kept"] is True  # dirty (env-iso.out), never committed
    env_text2 = (Path(iso["worktree"]) / "env-iso.out").read_text()
    assert f"CONDUCTOR_RUN_ID={result2.run_id}" in env_text2
    assert f"CONDUCTOR_WORKTREE={iso['worktree']}" in env_text2


def test_ports_claim_skips_a_preexisting_claim_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "ports").mkdir(parents=True)
    (home / "ports" / "40001").write_text("some-other-run")
    draws = iter([40001, 40001, 40002])
    monkeypatch.setattr(ports_mod, "_draw", lambda: next(draws))

    claimed, error = ports_mod.claim(1, home, "run-a")
    assert error is None
    assert claimed == [40002]
    assert (home / "ports" / "40001").read_text() == "some-other-run"
    assert (home / "ports" / "40002").read_text() == "run-a"


def test_ports_claim_gives_up_after_max_draws(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(ports_mod, "_draw", lambda: 40001)
    (home / "ports").mkdir(parents=True)
    (home / "ports" / "40001").write_text("someone-else")

    claimed, error = ports_mod.claim(1, home, "run-b")
    assert claimed == []
    assert error == "ports: could not allocate 1 free ports"
    # nothing new was left behind
    assert sorted(p.name for p in (home / "ports").iterdir()) == ["40001"]


def test_gc_plans_and_removes_a_stale_port_claim_and_keeps_a_live_one(home, monkeypatch, capsys):
    (home / "ports").mkdir(parents=True)
    (home / "ports" / "40001").write_text("20200101T000000Z-stale")
    (home / "ports" / "40002").write_text("20200101T000000Z-live")
    stale_run = home / "runs" / "20200101T000000Z-stale"
    stale_run.mkdir(parents=True)
    (stale_run / "result.json").write_text("{}")
    (home / "runs" / "20200101T000000Z-live").mkdir(parents=True)

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["gc", "--apply"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    stale = next(r for r in rows if r.get("name") == "20200101T000000Z-stale")
    live = next(r for r in rows if r.get("name") == "20200101T000000Z-live")

    assert stale["kind"] == "port" and stale["action"] == "remove" and stale["done"] is True
    assert live["kind"] == "port" and live["action"] == "keep"
    assert not (home / "ports" / "40001").exists()
    assert (home / "ports" / "40002").exists()


# --- setup / teardown ---------------------------------------------------


def test_setup_runs_in_the_worktree_before_the_fleet(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "test -f setup-marker.txt && echo ran > fleet-marker.txt"])
    result = dispatch(
        Spec(
            fleet="claude",
            prompt="setup",
            cwd=str(repo),
            mode="write",
            setup="touch setup-marker.txt",
        ),
        isolate=True,
        home=home,
    )
    assert result.ok is True
    assert result.lane_env["setup"]["exit_code"] == 0
    assert (Path(result.isolation["worktree"]) / "fleet-marker.txt").read_text() == "ran\n"


def test_a_failing_setup_never_spawns_the_fleet_and_spends_nothing(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo should-not-run > spawned.txt"])
    result = dispatch(
        Spec(fleet="claude", prompt="setup-fail", cwd=str(repo), mode="write", setup="exit 3"),
        isolate=True,
        home=home,
    )
    assert result.ok is False
    assert result.spawned is False
    assert result.error == "setup failed: exit 3"
    assert result.lane_env["setup"]["exit_code"] == 3
    assert result.usage is None
    assert not (repo / "spawned.txt").exists()


def test_teardown_runs_after_an_ok_gate_and_is_recorded(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo work > new.txt"])
    result = dispatch(
        Spec(fleet="claude", prompt="td", cwd=str(repo), mode="write", teardown="true"),
        home=home,
    )
    assert result.ok is True
    assert result.lane_env["teardown"]["exit_code"] == 0


def test_a_failing_teardown_is_a_note_not_a_failure(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo work > new.txt"])
    result = dispatch(
        Spec(fleet="claude", prompt="td", cwd=str(repo), mode="write", teardown="exit 5"),
        home=home,
    )
    assert result.ok is True
    assert result.lane_env["teardown"]["exit_code"] == 5
    assert any("teardown failed: exit 5" in note for note in result.git_verdict["notes"])


def test_teardown_runs_even_when_the_gate_failed_and_does_not_change_ok(
    repo, home, fake_fleet
):
    fake_fleet(["sh", "-c", "echo work > new.txt"])
    result = dispatch(
        Spec(
            fleet="claude",
            prompt="td",
            cwd=str(repo),
            mode="write",
            teardown="touch teardown-ran.txt",
        ),
        home=home,
        test_command="exit 1",
    )
    assert result.ok is False
    assert result.lane_env["teardown"]["exit_code"] == 0
    assert (repo / "teardown-ran.txt").exists()


def test_clean_gate_receives_the_lane_env(repo, home, fake_fleet):
    """The clean gate re-runs the lane's own gate command at the base
    commit through `_transplant_gate`; per spec item 1 it must see the same
    CONDUCTOR_PORT_* env as the lane gate and the fleet, not conductor's own
    inherited environment."""
    (repo / "app.txt").write_text("bad\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "check.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "ok = Path('app.txt').read_text() == 'good\\n' and 'CONDUCTOR_PORT_1' in os.environ\n"
        "raise SystemExit(0 if ok else 1)\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed check"], cwd=repo, check=True)
    fake_fleet(
        ["sh", "-c", "echo good > app.txt; printf '# harmless test edit\\n' >> tests/check.py"]
    )
    result = dispatch(
        Spec(fleet="claude", prompt="p", cwd=str(repo), mode="write", ports=1),
        home=home,
        test_command="python3 tests/check.py",
        commit_message="genuine source fix",
    )
    assert result.tests["exit_code"] == 0
    assert result.test_surface["clean_gate"]["exit_code"] == 0
    assert result.ok is True


# --- include -------------------------------------------------------------


def test_an_untracked_include_lands_in_the_worktree_and_stays_out_of_diff_and_commit(
    repo, home, fake_fleet, git_out
):
    (repo / "secret.env").write_text("TOKEN=abc\n")
    fake_fleet(["sh", "-c", "echo work > new.txt && git add -A && git commit -qm work"])
    result = dispatch(
        Spec(fleet="claude", prompt="inc", cwd=str(repo), mode="write", include=["secret.env"]),
        isolate=True,
        home=home,
    )
    assert result.ok is True
    assert result.lane_env["included"] == ["secret.env"]
    tree = git_out(repo, "ls-tree", "-r", "--name-only", result.isolation["branch"])
    assert "secret.env" not in tree
    assert "new.txt" in tree
    if result.diff_path:
        assert "secret.env" not in Path(result.diff_path).read_text()


def test_a_tracked_include_path_is_refused_before_spawn(repo, home, fake_fleet):
    (repo / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add tracked"], cwd=repo, check=True)
    fake_fleet(["sh", "-c", "echo x > x.txt"])
    result = dispatch(
        Spec(fleet="claude", prompt="inc", cwd=str(repo), mode="write", include=["tracked.txt"]),
        isolate=True,
        home=home,
    )
    assert result.ok is False
    assert result.spawned is False
    assert result.error == "include: tracked.txt is tracked; the worktree already has it"


def test_a_missing_include_path_is_a_note_not_a_failure(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo x > x.txt"])
    result = dispatch(
        Spec(fleet="claude", prompt="inc", cwd=str(repo), mode="write", include=["nope.txt"]),
        isolate=True,
        home=home,
    )
    assert result.ok is True
    assert result.lane_env["included"] == []
    assert any("nope.txt does not exist" in note for note in result.git_verdict["notes"])


def test_include_preserves_the_operators_global_excludes(
    repo, home, fake_fleet, monkeypatch, tmp_path, git_out
):
    """The worktree-scoped core.excludesFile that keeps an included path
    untracked must not shadow the operator's own global excludes; per the
    lead note it should be seeded with those patterns first. A build
    artifact the operator globally ignores (never named in `include`) must
    stay untracked in the worktree too."""
    global_ignore = tmp_path / "global-gitignore"
    global_ignore.write_text("operator-ignored.txt\n")
    global_gitconfig = tmp_path / "gitconfig-global"
    global_gitconfig.write_text(f"[core]\n\texcludesFile = {global_ignore}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_gitconfig))

    (repo / "include-me.txt").write_text("payload\n")
    fake_fleet(
        [
            "sh",
            "-c",
            "echo ignored > operator-ignored.txt; echo work > new.txt; "
            "git add -A && git commit -qm work",
        ]
    )
    result = dispatch(
        Spec(
            fleet="claude", prompt="inc", cwd=str(repo), mode="write", include=["include-me.txt"]
        ),
        isolate=True,
        home=home,
    )
    assert result.ok is True
    tree = git_out(repo, "ls-tree", "-r", "--name-only", result.isolation["branch"])
    assert "operator-ignored.txt" not in tree
    assert "new.txt" in tree


def test_include_without_isolation_is_a_note(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo x > x.txt"])
    result = dispatch(
        Spec(fleet="claude", prompt="inc", cwd=str(repo), mode="write", include=["seed.txt"]),
        home=home,
    )
    assert result.ok is True
    assert any(
        note == "include ignored: dispatch is not isolated" for note in result.git_verdict["notes"]
    )


# --- missions and the CLI -------------------------------------------------


def test_lane_env_attempt_keys_cascade_through_a_mission(tmp_path):
    mission = mission_from_dict(
        {
            "prompt": "p",
            "ports": 2,
            "setup": "true",
            "teardown": "true",
            "include": ["a.txt"],
            "lanes": [
                {"name": "primary", "fleet": "claude"},
                {"name": "overridden", "fleet": "claude", "ports": 1, "include": ["b.txt"]},
            ],
        },
        base_dir=tmp_path,
    )
    inherited = mission.lanes[0].attempts[0]
    assert inherited.ports == 2
    assert inherited.setup == "true"
    assert inherited.teardown == "true"
    assert inherited.include == ["a.txt"]
    spec = inherited.spec(str(tmp_path))
    assert spec.ports == 2
    assert spec.setup == "true"
    assert spec.teardown == "true"
    assert spec.include == ["a.txt"]

    overridden = mission.lanes[1].attempts[0]
    assert overridden.ports == 1
    assert overridden.include == ["b.txt"]
    assert overridden.setup == "true"  # still cascaded from the mission


def test_cli_lane_env_flags_parse_and_reach_the_spec_on_a_dry_run(repo, home, monkeypatch):
    import conductor.cli as cli_mod
    from conductor import runner as real_runner

    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--ports",
            "2",
            "--setup",
            "true",
            "--teardown",
            "false",
            "--include",
            "a.txt",
            "--include",
            "b/c.txt",
            "--dry-run",
        ]
    )
    assert args.ports == 2
    assert args.setup == "true"
    assert args.teardown == "false"
    assert args.include == ["a.txt", "b/c.txt"]

    captured: dict = {}
    real_dispatch = real_runner.dispatch

    def capturing(spec, **kwargs):
        captured["spec"] = spec
        kwargs["dry_run"] = True
        kwargs["home"] = home
        return real_dispatch(spec, **kwargs)

    monkeypatch.setattr(cli_mod, "dispatch", capturing)
    rc = cli_mod.cmd_dispatch(args)
    assert rc == 0
    spec = captured["spec"]
    assert spec.ports == 2
    assert spec.setup == "true"
    assert spec.teardown == "false"
    assert spec.include == ["a.txt", "b/c.txt"]


# --- README ----------------------------------------------------------------


def test_readme_documents_per_lane_setup_teardown_and_ports():
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    section = readme.split("### Per-lane setup, teardown, and ports", 1)[1].split(
        "### Garbage collection", 1
    )[0]
    compact = " ".join(section.split())

    assert "CONDUCTOR_PORT_1" in compact and "CONDUCTOR_PORTS" in compact
    assert "CONDUCTOR_RUN_ID" in compact and "CONDUCTOR_WORKTREE" in compact
    assert "ports or not" in compact
    assert "O_CREAT | O_EXCL" in compact
    assert "could not allocate" in compact
    assert "setup failed: exit <code>" in compact
    assert "teardown failed: exit" in compact
    assert "never changes `ok`" in compact
    assert "include: <path> is tracked" in compact
    assert "include ignored: dispatch is not isolated" in compact
    assert "core.excludesFile" in compact
    assert "info/exclude" in compact
    assert "lane_env.included" in compact
    assert ".worktreeinclude" in compact and ".cursor/worktrees.json" in compact
    assert "docs/ROADMAP-2026-09.md" in compact and "item C4" in compact
    assert "--ports" in compact
    assert "--setup" in compact
    assert "--teardown" in compact
    assert "--include" in compact
