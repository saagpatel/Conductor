from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conductor import runner as runner_mod


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.invalid"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "conductor-home"


@pytest.fixture
def fake_fleet(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], None]:
    def _set(argv: list[str]) -> None:
        monkeypatch.setattr(runner_mod, "build_argv", lambda spec: argv)

    return _set


@pytest.fixture
def git_out() -> Callable[..., str]:
    def _run(cwd: str | Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    return _run
