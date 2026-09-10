from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec, build_argv


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
def fake_fleet(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    missing = object()

    def _set(
        argv: list[str] | None = None,
        *,
        session_id: str | None | Callable[[Spec], str | None] | object = missing,
    ) -> None:
        if argv is not None:
            monkeypatch.setattr(runner_mod, "build_argv", lambda spec: argv)
            return
        if session_id is missing:
            raise TypeError("fake_fleet needs argv or session_id")

        def fake_build(spec: Spec) -> list[str]:
            reported = session_id(spec) if callable(session_id) else session_id
            if spec.fleet == "codex":
                events = []
                if reported is not None:
                    events.append({"type": "thread.started", "thread_id": reported})
                events += [
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "ok"},
                    },
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10, "output_tokens": 1},
                    },
                ]
                output = "\n".join(json.dumps(event) for event in events)
            elif spec.fleet == "antigravity":
                payload: dict[str, object] = {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "response": "ok",
                        "usage": {"input_tokens": 10, "output_tokens": 1},
                    },
                }
                if reported is not None:
                    payload["conversation_id"] = reported
                output = json.dumps(payload)
            else:
                payload = {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "ok",
                    "usage": {"inputTokens": 10, "outputTokens": 1},
                }
                if reported is not None:
                    payload["session_id"] = reported
                output = json.dumps(payload)
            # Keep the real fleet argv after sh's ignored positional arguments
            # so argv.json still proves the translation without spawning it.
            return [
                "sh",
                "-c",
                f"printf '%s\\n' {shlex.quote(output)}",
                "fake-fleet",
                *build_argv(spec),
            ]

        monkeypatch.setattr(runner_mod, "build_argv", fake_build)

    return _set


@pytest.fixture
def git_out() -> Callable[..., str]:
    def _run(cwd: str | Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    return _run
