"""Unavailable verification parks resume without repeating completed work."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conductor import mission as mission_mod
from conductor.cli import main
from conductor.gc import _in_progress_run_ids
from conductor.mission import mission_from_dict, run_mission
from conductor.report import _scan_missions
from conductor.verify import GIT_UNRUN


def _build(repo, home, fake_fleet, *, pending, human=False):
    envelope = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                           "result": "built", "total_cost_usd": 0.2,
                           "usage": {"input_tokens": 10, "output_tokens": 1}})
    fake_fleet(["sh", "-c", "echo built > built.txt\nprintf '%s\\n' '" + envelope + "'"])
    raw = {"cwd": str(repo), "max_cost_usd": 5, "lanes": [
        {"name": "paid", "fleet": "claude", "prompt": "build", "mode": "write",
         "commit": "feat: build", "branch": "feat/paid"},
        {"name": "next", "fleet": "script", "command": "true", "needs": ["paid"]},
    ]}
    if human:
        raw['lanes'][1] = {'name': 'next', 'fleet': 'human', 'prompt': 'Name the result',
                           'needs': ['paid']}
    if pending and not human:
        raw["pause"] = {"before": ["next"]}
    result = run_mission(mission_from_dict(raw, base_dir=repo), home=home)
    assert result.ok or result.paused
    return result


@pytest.mark.parametrize("command", ["cat-file", "rev-parse"])
@pytest.mark.parametrize("pending", [False, True])
def test_unavailable_git_preserves_receipts_and_can_retry_without_repayment(
    repo, home, fake_fleet, monkeypatch, capsys, command, pending
):
    first = _build(repo, home, fake_fleet, pending=pending)
    mission_dir = Path(first.mission_dir)
    before = {p.relative_to(mission_dir): p.read_bytes()
              for p in mission_dir.rglob('*') if p.is_file()}
    runs_before = {p.name for p in (home / 'runs').iterdir()}
    real_git = mission_mod.git_run
    calls = []

    def unavailable(cwd, *args, **kwargs):
        if args and args[0] == command and any('^{commit}' in arg for arg in args):
            calls.append(args)
            return subprocess.CompletedProcess(['git', *args], GIT_UNRUN, '', 'EAGAIN')
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setenv('CONDUCTOR_HOME', str(home))
    monkeypatch.setattr(mission_mod, 'git_run', unavailable)
    argv = ['mission', '--resume', mission_dir.name]
    if pending:
        argv += ['--answer', 'continue']
    assert main(argv) == 4
    response = json.loads(capsys.readouterr().out)
    assert response.get('paused', {}).get('kind') == 'verification'
    assert response['paused']['reason'] == 'EAGAIN'
    assert response['paused']['lane'] == 'paid'
    assert len(calls) == 2
    assert {p.name for p in (home / 'runs').iterdir()} == runs_before
    assert all((mission_dir / path).read_bytes() == content for path, content in before.items())
    assert not (mission_dir / 'running.json').exists()
    assert (mission_dir / 'resume-verification.json').is_file()
    assert _scan_missions(home)[1][mission_dir.name]['unfinished'] is True
    assert runs_before <= _in_progress_run_ids(home)
    assert main(['missions']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]['paused'] is True
    assert listed[0]['verification']['lane'] == 'paid'

    monkeypatch.setattr(mission_mod, 'git_run', real_git)
    assert main(argv) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed['budget']['spent_usd'] == 0.2
    assert 'paid' in completed['resumed_from']['kept']
    assert not (mission_dir / 'resume-verification.json').exists()
    new_runs = set(p.name for p in (home / 'runs').iterdir()) - runs_before
    assert len(new_runs) == int(pending)
    assert all(json.loads((home / 'runs' / rid / 'result.json').read_text())['fleet'] == 'script'
               for rid in new_runs)


def test_git_pause_does_not_consume_a_pending_human_answer(
    repo, home, fake_fleet, monkeypatch
):
    first = _build(repo, home, fake_fleet, pending=True, human=True)
    directory = Path(first.mission_dir)
    old_pause = (directory / 'pause.json').read_bytes()
    mission = mission_mod.Mission.from_snapshot(
        json.loads((directory / 'mission.json').read_text())
    )
    real_git = mission_mod.git_run

    def unavailable(cwd, *args, **kwargs):
        if args[:1] == ('cat-file',):
            return subprocess.CompletedProcess(['git', *args], GIT_UNRUN, '', 'busy')
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(mission_mod, 'git_run', unavailable)
    with pytest.raises(mission_mod.MissionInvalid, match='resume paused'):
        run_mission(mission, home=home, resume_dir=directory, answer='Approved result')
    assert (directory / 'pause.json').read_bytes() == old_pause
    assert not (directory / 'answers/next.txt').exists()
    monkeypatch.setattr(mission_mod, 'git_run', real_git)
    second = run_mission(mission, home=home, resume_dir=directory, answer='Approved result')
    assert second.ok
    assert (directory / 'answers/next.txt').read_text() == 'Approved result'
    assert second.budget['spent_usd'] == 0.2


@pytest.mark.parametrize('pause_record', [{'kind': 'verification', 'reason': 'busy'}, {}])
def test_parent_does_not_adopt_a_child_blocked_on_verification(repo, home, pause_record):
    from dataclasses import asdict

    from test_plan_lanes import _plan_lane

    directory = home / 'missions/parent'
    (directory / 'lanes').mkdir(parents=True)
    child = home / 'missions/child'
    child.mkdir()
    (child / 'result.json').write_text(json.dumps({'ok': True, 'cost_usd': 2}))
    block = child / 'resume-verification.json'
    block.write_text(json.dumps(pause_record))
    mission = mission_from_dict({'cwd': str(repo), 'lanes': [_plan_lane()]}, base_dir=repo)
    result = mission_mod.LaneResult(name='plan', ok=True, plan={'child': {
        'mission_id': 'child', 'state': 'finished', 'rolled_up': True,
    }})
    (directory / 'lanes/plan.json').write_text(json.dumps(asdict(result)))
    (directory / 'result.json').write_text(json.dumps({'children': ['child']}))
    with pytest.raises(mission_mod.MissionInvalid, match='child.*paused'):
        mission_mod._build_resume_plan(mission, directory, home)
    block.unlink()
    plan = mission_mod._build_resume_plan(mission, directory, home)
    assert 'plan' in plan.kept
    assert plan.children_rollups == []
