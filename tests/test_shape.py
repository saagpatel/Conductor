"""E5: the Shape A launcher writes a mission that loads, and prints every cap term."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conductor import ceiling as ceiling_mod
from conductor import shape
from conductor.cli import main
from conductor.mission import mission_from_dict


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n2. two\n")
    return spec


# --- the arithmetic is rules 2 and 10, term by term --------------------------


def test_build_cap_is_items_plus_summary():
    caps = shape.cap_arithmetic(3, 2)
    assert caps.build_cap == 4.0
    assert [name for name, _ in caps.build_terms] == ["3 spec items", "Claude summary"]


def test_scheduler_tax_and_extra_modules_each_add_their_term():
    caps = shape.cap_arithmetic(5, 4, scheduler=True)
    assert caps.build_cap == 10.0
    assert ("scheduler tax", 2.0) in caps.build_terms
    assert ("2 modules past the second", 2.0) in caps.build_terms
    # F6: the fix cap now carries a default findings term ($1.00 x 4), so the
    # old $5.00 (fix base + scheduler tax + summary) becomes $9.00.
    assert caps.fix_cap == 9.0


def test_grok_cap_depends_on_whether_it_runs_the_suite():
    assert shape.cap_arithmetic(1, 1).grok_cap == 1.5
    assert shape.cap_arithmetic(1, 1, grok_runs_suite=True).grok_cap == 2.0


def test_mission_budget_covers_every_lane_plus_slack():
    caps = shape.cap_arithmetic(2, 1)
    assert caps.mission_budget == caps.build_cap + 1.0 + 1.5 + caps.fix_cap + 1.5


def test_render_prints_every_term_not_just_the_sum():
    text = shape.cap_arithmetic(5, 4, scheduler=True).render()
    assert "$5.00 5 spec items + $2.00 scheduler tax + $2.00 2 modules past the second" in text
    assert "= $10.00" in text
    assert "mission budget" in text


@pytest.mark.parametrize("items,modules", [(0, 1), (1, 0)])
def test_counts_below_one_are_refused(items, modules):
    with pytest.raises(shape.ShapeInvalid):
        shape.cap_arithmetic(items, modules)


# --- the mission the template produces ---------------------------------------


def test_shape_a_mission_loads_and_carries_the_shape(repo, tmp_path):
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(2, 1)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps)
    shape.write_dispositions_schema(spec.parent)
    mission = mission_from_dict(raw, base_dir=spec.parent)
    names = [lane.name for lane in mission.lanes]
    assert names == ["build", "review-gemini", "review-grok", "fix"]
    assert raw["prompt_file"] == "widget.md"
    assert raw["cwd"] == str(repo)
    assert raw["policy"] == {
        "build": {"vendors": ["anthropic"]},
        "review": {"vendors": ["google", "xai"]},
        "fix": {"vendors": ["anthropic"]},
    }
    assert raw["pause"] == {"before": ["fix"]}
    assert raw["max_cost_usd"] == caps.mission_budget
    build, gemini, grok, fix = raw["lanes"]
    assert build["cap_usd"] == caps.build_cap and build["test_policy"] == "allow"
    # E24/F5: the default grace band lands on the two claude (build/fix)
    # lanes (native cap) and the grok read lane (post-hoc cap), not gemini
    # (antigravity, a watcher cap with no terminal message or verdict to help).
    assert build["cap_grace_usd"] == caps.cap_grace_usd == 0.25
    assert fix["cap_grace_usd"] == 0.25
    assert grok["cap_grace_usd"] == 0.25
    assert "cap_grace_usd" not in gemini
    assert gemini["mode"] == "read" and "Do not run the test suite" in gemini["prompt"]
    assert grok["cap_usd"] == 1.5 and "Do not run the test suite" in grok["prompt"]
    assert fix["resume"] == "build" and fix["branch"] == "feat/widget"
    assert fix["commit"] == f"fix({repo.name}): address cross-vendor review of widget"
    assert fix["deliverable"] == {
        "path": "dispositions.json",
        "schema": "dispositions.schema.json",
        "commit": False,
    }


def test_build_prompt_names_the_gate_verbatim_and_wraps_the_spec(repo, tmp_path):
    # Review idea 7: builders ran the suite serially because the spec never
    # named the gate's flags. The build lane now carries the gate command
    # exactly as the lead runs it, the basetemp rule, and the signature rule,
    # around the spec as a template reference the mission still renders.
    spec = _spec(tmp_path)
    gate = 'pytest -q -n auto --dist loadgroup --basetemp="$TMPDIR/x"'
    raw = shape.shape_a(spec=spec, repo=repo, test=gate, caps=shape.cap_arithmetic(1, 1))
    build = raw["lanes"][0]
    assert f"<gate>\n{gate}\n</gate>" in build["prompt"]
    assert "--basetemp" in build["prompt"]
    assert "existing call signature" in build["prompt"]
    assert build["prompt"].endswith("<spec>\n{{mission.prompt}}\n</spec>")
    assert "{gate}" not in build["prompt"]
    from conductor.prompts import prompt_versions

    assert "shape_build" in prompt_versions()


def test_grok_prompt_lets_it_run_the_gate_only_when_asked(repo, tmp_path):
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(1, 1, grok_runs_suite=True)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps)
    grok = raw["lanes"][2]
    assert grok["cap_usd"] == 2.0
    assert "run the gate named in the spec" in grok["prompt"]
    assert "--basetemp" in grok["prompt"]


def test_review_prompts_follow_the_no_quota_rules(repo, tmp_path):
    spec = _spec(tmp_path)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1))
    for lane in raw["lanes"][1:3]:
        assert "NO_FINDINGS" in lane["prompt"]
        assert "Either answer is complete" in lane["prompt"]
        assert "at least" not in lane["prompt"]


def test_ports_and_overrides_land_on_the_right_lanes(repo, tmp_path):
    spec = _spec(tmp_path)
    raw = shape.shape_a(
        spec=spec,
        repo=repo,
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        name="thing",
        ports=2,
        branch="feat/custom",
        build_commit="feat(x): build it",
        fix_commit="fix(x): fix it",
        test_policy="clean",
    )
    assert raw["name"] == "thing"
    assert raw["lanes"][0]["ports"] == 2
    assert raw["lanes"][0]["commit"] == "feat(x): build it"
    assert raw["lanes"][0]["test_policy"] == "clean"
    assert raw["lanes"][3]["branch"] == "feat/custom"
    assert raw["lanes"][3]["commit"] == "fix(x): fix it"
    assert "ports" not in shape.shape_a(
        spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1)
    )["lanes"][0]


def test_paths_are_relative_to_the_mission_dir_when_under_it(repo, tmp_path):
    spec = _spec(tmp_path)
    raw = shape.shape_a(
        spec=spec,
        repo=repo,
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        mission_dir=tmp_path,
    )
    assert raw["prompt_file"] == "specs/widget.md"
    assert raw["cwd"] == "repo"
    shape.write_dispositions_schema(tmp_path)
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.cwd == str(repo)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"test": ""}, "--test"),
        ({"branch": "conductor/x"}, "outside conductor/"),
        ({"ports": -1}, "--ports"),
    ],
)
def test_bad_inputs_are_refused(repo, tmp_path, kwargs, message):
    spec = _spec(tmp_path)
    base = {"spec": spec, "repo": repo, "test": "true", "caps": shape.cap_arithmetic(1, 1)}
    with pytest.raises(shape.ShapeInvalid, match=message):
        shape.shape_a(**(base | kwargs))


def test_missing_spec_and_non_repo_are_refused(repo, tmp_path):
    caps = shape.cap_arithmetic(1, 1)
    with pytest.raises(shape.ShapeInvalid, match="spec is not a file"):
        shape.shape_a(spec=tmp_path / "nope.md", repo=repo, test="true", caps=caps)
    spec = _spec(tmp_path)
    with pytest.raises(shape.ShapeInvalid, match="not a git repository"):
        shape.shape_a(spec=spec, repo=tmp_path, test="true", caps=caps)


# --- the CLI ---------------------------------------------------------------


def test_cli_writes_the_mission_and_prints_the_arithmetic(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "5", "--modules", "4", "--scheduler", "--out", str(out), "--dry-run",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "= $10.00" in printed and "scheduler tax" in printed
    assert f"wrote {out}" in printed
    raw = json.loads(out.read_text())
    assert raw["prompt_file"] == str(spec)
    assert raw["lanes"][0]["cap_usd"] == 10.0
    assert '"ok": true' in printed  # the dry run ran and passed


def test_cli_defaults_the_output_beside_the_spec_and_refuses_to_clobber(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    argv = [
        "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
        "--items", "1", "--modules", "1",
    ]
    assert main(argv) == 0
    written = spec.parent / "mission.json"
    assert written.is_file()
    assert main(argv) == 3
    assert "exists; pass --force" in capsys.readouterr().err
    assert main(argv + ["--force"]) == 0


def test_cli_refuses_bad_counts_with_exit_3(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "0", "--modules", "1",
        ]
    )
    assert code == 3
    assert "--items" in capsys.readouterr().err


# --- F6: --ceiling ------------------------------------------------------------


def test_ceiling_none_writes_null_bounds():
    assert shape.parse_ceiling("none") == {"per_hour_usd": None, "per_day_usd": None}


def test_ceiling_default_reads_ceiling_module_constants():
    assert shape.parse_ceiling("default") == {
        "per_hour_usd": ceiling_mod.USD_PER_HOUR,
        "per_day_usd": ceiling_mod.USD_PER_DAY,
    }


def test_ceiling_explicit_numbers():
    assert shape.parse_ceiling("5,10") == {"per_hour_usd": 5.0, "per_day_usd": 10.0}


@pytest.mark.parametrize("value", ["nope", "1,2,3", "-1,5", "5,-1", "x,5"])
def test_ceiling_rejects_bad_values(value):
    with pytest.raises(shape.ShapeInvalid, match="--ceiling"):
        shape.parse_ceiling(value)


def test_shape_a_ceiling_defaults_to_none(repo, tmp_path):
    spec = _spec(tmp_path)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1))
    assert raw["ceiling"] == {"per_hour_usd": None, "per_day_usd": None}


def test_cli_ceiling_defaults_to_none_in_the_mission_file(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out),
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    assert raw["ceiling"] == {"per_hour_usd": None, "per_day_usd": None}


def test_cli_ceiling_prints_and_writes_explicit_bounds(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--ceiling", "5,10", "--out", str(out),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "ceiling: per_hour $5.00, per_day $10.00" in printed
    raw = json.loads(out.read_text())
    assert raw["ceiling"] == {"per_hour_usd": 5.0, "per_day_usd": 10.0}


def test_cli_ceiling_default_reads_the_module_constants(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--ceiling", "default", "--out", str(out),
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    assert raw["ceiling"] == {
        "per_hour_usd": ceiling_mod.USD_PER_HOUR,
        "per_day_usd": ceiling_mod.USD_PER_DAY,
    }


# --- F6: --tests-items ---------------------------------------------------------


def test_tests_items_are_counted_twice_in_the_build_cap():
    caps = shape.cap_arithmetic(5, 1, tests_items=2)
    assert ("tests counted twice (2 items)", 2.0) in caps.build_terms
    assert caps.build_cap == 5.0 + 2.0 + 1.0


def test_tests_items_cannot_exceed_items():
    with pytest.raises(shape.ShapeInvalid, match="--tests-items"):
        shape.cap_arithmetic(1, 1, tests_items=2)


def test_cli_tests_items_land_in_the_printed_arithmetic(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "5", "--modules", "1", "--tests-items", "2", "--out", str(out),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "$2.00 tests counted twice (2 items)" in printed


# --- F6: --findings -------------------------------------------------------------


def test_findings_default_raises_the_fix_cap_to_seven_dollars():
    assert shape.cap_arithmetic(1, 1).fix_cap == 7.0
    assert (f"{shape.DEFAULT_FINDINGS} findings", 4.0) in shape.cap_arithmetic(1, 1).fix_terms


def test_findings_two_gives_a_five_dollar_fix_cap():
    assert shape.cap_arithmetic(1, 1, findings=2).fix_cap == 5.0


# --- F6: gate preflight ---------------------------------------------------------


def _worktree_list(repo: Path) -> str:
    return subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def test_gate_preflight_passes_on_true(repo):
    shape.gate_preflight(repo, "true")


def test_gate_preflight_refuses_a_nonzero_exit_with_the_tail(repo):
    with pytest.raises(shape.ShapeInvalid) as excinfo:
        shape.gate_preflight(repo, "echo gate-boom && exit 1")
    message = str(excinfo.value)
    assert "exited 1" in message
    assert "gate-boom" in message


def test_gate_preflight_refuses_exit_127(repo):
    with pytest.raises(shape.ShapeInvalid, match="exited 127"):
        shape.gate_preflight(repo, "conductor-test-no-such-command-xyz")


def test_gate_preflight_removes_the_throwaway_worktree_even_on_failure(repo):
    before = _worktree_list(repo)
    with pytest.raises(shape.ShapeInvalid):
        shape.gate_preflight(repo, "exit 1")
    assert _worktree_list(repo) == before


def test_gate_preflight_removes_the_throwaway_worktree_on_success(repo):
    before = _worktree_list(repo)
    shape.gate_preflight(repo, "true")
    assert _worktree_list(repo) == before


def test_cli_gate_preflight_refuses_the_launch_and_writes_nothing(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "exit 1",
            "--items", "1", "--modules", "1", "--out", str(out),
        ]
    )
    assert code == 3
    assert "gate preflight" in capsys.readouterr().err
    assert not out.exists()
    # Cross-vendor review (Grok): the prompt files are written after the
    # preflight, so a refused launch leaves nothing beside the mission path.
    assert not (out.parent / "prompts").exists()
    assert sorted(path.name for path in out.parent.iterdir()) == []


def test_cli_skip_preflight_says_so_and_still_writes_the_file(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "exit 1",
            "--items", "1", "--modules", "1", "--out", str(out), "--skip-preflight",
        ]
    )
    assert code == 0
    assert "gate preflight: skipped (--skip-preflight)" in capsys.readouterr().out
    assert out.is_file()


# --- F15 mission 2 item 2: dispositions.json deliverable on the fix lane -----


def test_cli_writes_the_dispositions_schema_and_dry_run_passes(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out), "--dry-run",
        ]
    )
    assert code == 0
    schema_path = out.parent / "dispositions.schema.json"
    assert schema_path.is_file()
    assert json.loads(schema_path.read_text()) == shape.DISPOSITIONS_SCHEMA
    raw = json.loads(out.read_text())
    fix = next(lane for lane in raw["lanes"] if lane["name"] == "fix")
    assert fix["deliverable"] == {
        "path": "dispositions.json",
        "schema": "dispositions.schema.json",
        "commit": False,
    }
    printed = capsys.readouterr().out
    assert '"ok": true' in printed


def test_cli_inline_still_writes_the_dispositions_schema(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out), "--inline",
        ]
    )
    assert code == 0
    assert not (out.parent / "prompts").exists()
    assert (out.parent / "dispositions.schema.json").is_file()


# --- F9 Shape C as a launcher option (Phase H item 4) -----------------------


def test_opus_review_adds_a_third_cold_reviewer_the_mission_accepts(repo, tmp_path):
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(2, 1, opus_review=True)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps, opus_review=True)
    shape.write_dispositions_schema(spec.parent)
    # Loads: the Sonnet build and the Opus reviewer share a vendor, and the
    # mission declares the lift itself rather than leaving the lead to add it.
    mission = mission_from_dict(raw, base_dir=spec.parent)
    names = [lane.name for lane in mission.lanes]
    assert names == ["build", "review-gemini", "review-grok", "review-opus", "fix"]
    assert raw["self_judging"] == "allow"
    assert raw["policy"]["review"] == {"vendors": ["google", "xai", "anthropic"]}
    assert raw["concurrency"] == 3
    opus = raw["lanes"][3]
    assert opus["fleet"] == "claude" and opus["model"] == "opus" and opus["effort"] == "hard"
    assert opus["mode"] == "read" and opus["base"] == "build"
    assert opus["cap_usd"] == caps.opus_cap == 4.0
    assert opus["cap_grace_usd"] == caps.cap_grace_usd
    assert "NO_FINDINGS" in opus["prompt"] and "at least" not in opus["prompt"]
    assert "Do not run the test suite" in opus["prompt"]
    fix = raw["lanes"][4]
    assert fix["needs"] == ["review-gemini", "review-grok", "review-opus"]
    assert "<review_opus>\n{{lanes.review-opus.answer}}\n</review_opus>" in fix["prompt"]
    assert fix["prompt"].index("</review_grok>") < fix["prompt"].index("<review_opus>")
    assert "Three reviewers" in fix["prompt"] and "Two reviewers" not in fix["prompt"]
    assert '"review-opus"' in fix["prompt"]
    assert "both reviews" not in fix["prompt"] and "either reviewer" not in fix["prompt"]
    assert raw["max_cost_usd"] == caps.mission_budget
    from conductor.prompts import prompt_versions

    assert "shape_opus_review" in prompt_versions()


def test_without_opus_review_the_shape_is_unchanged(repo, tmp_path):
    spec = _spec(tmp_path)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(2, 1))
    names = [lane["name"] for lane in raw["lanes"]]
    assert names == ["build", "review-gemini", "review-grok", "fix"]
    assert "self_judging" not in raw
    assert raw["concurrency"] == 2
    assert "review_opus" not in raw["lanes"][3]["prompt"]


def test_opus_review_with_adversarial_keeps_the_block_order(repo, tmp_path):
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(2, 1, opus_review=True, adversarial=True)
    raw = shape.shape_a(
        spec=spec, repo=repo, test="true", caps=caps, opus_review=True, adversarial=True
    )
    names = [lane["name"] for lane in raw["lanes"]]
    assert names == ["build", "review-gemini", "review-grok", "review-opus", "adversarial", "fix"]
    fix = raw["lanes"][-1]
    assert fix["needs"] == ["build", "review-gemini", "review-grok", "review-opus", "adversarial"]
    prompt = fix["prompt"]
    assert prompt.index("<review_opus>") < prompt.index("<adversarial>")
    assert "When the adversarial block above" in prompt


def test_opus_review_cap_arithmetic_adds_its_own_line_and_budget_term():
    plain = shape.cap_arithmetic(2, 1)
    with_opus = shape.cap_arithmetic(2, 1, opus_review=True)
    assert with_opus.opus_cap == 4.0
    assert with_opus.mission_budget == round(plain.mission_budget + 4.0, 2)
    assert "review-opus cap: $3.00 Opus cold read + $1.00 Claude summary = $4.00" in (
        with_opus.render()
    )
    assert "review-opus" not in plain.render()


def test_followon_carries_the_opus_reviewer_when_asked(repo, tmp_path):
    caps = shape.cap_arithmetic(1, 1, opus_review=True)
    raw = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test="true",
        caps=caps,
        name="salvaged",
        opus_review=True,
    )
    names = [lane["name"] for lane in raw["lanes"]]
    assert names == ["review-gemini", "review-grok", "review-opus", "fix"]
    opus = raw["lanes"][2]
    assert "git show abc123" in opus["prompt"] and "{{lanes.build.diff}}" not in opus["prompt"]
    assert raw["policy"]["review"]["vendors"] == ["google", "xai", "anthropic"]
    assert raw["lanes"][3]["needs"] == ["review-gemini", "review-grok", "review-opus"]
    assert "<review_opus>" in raw["lanes"][3]["prompt"]
    assert raw["max_cost_usd"] == round(1.0 + 1.5 + 4.0 + caps.fix_cap + shape.USD_MISSION_SLACK, 2)
    assert "self_judging" not in raw


def test_cli_opus_review_writes_the_lane_and_prints_its_cap(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--opus-review", "--out", str(out), "--dry-run",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "5 lanes" in printed
    assert "review-opus cap: $3.00 Opus cold read + $1.00 Claude summary = $4.00" in printed
    assert '"ok": true' in printed  # the dry run accepted the self-judging lift
    raw = json.loads(out.read_text())
    assert raw["self_judging"] == "allow"
    assert (out.parent / "prompts" / "review-opus.md").read_text() == shape.OPUS_REVIEW_PROMPT
