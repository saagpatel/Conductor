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


def test_mission_budget_covers_every_lane_plus_its_grace_plus_slack():
    """The grace band is spend against the same ledger, so leaving it out of
    the mission budget let every lane finish inside its own cap+grace and
    the mission still run out before the fix lane."""
    caps = shape.cap_arithmetic(2, 1)
    grace = 3 * caps.cap_grace_usd
    assert caps.graced_lanes == 3
    assert caps.mission_budget == caps.build_cap + 1.0 + 1.5 + caps.fix_cap + grace + 1.5


def test_mission_budget_carries_grace_for_the_optional_lanes_too():
    caps = shape.cap_arithmetic(2, 1, adversarial=True, opus_review=True)
    assert caps.graced_lanes == 5
    assert caps.mission_budget == round(
        caps.build_cap
        + caps.review_caps
        + caps.fix_cap
        + caps.adversarial_cap
        + 5 * caps.cap_grace_usd
        + 1.5,
        2,
    )


def test_the_followon_fix_prompt_does_not_claim_a_thread_it_never_had():
    """`shape_a`'s fix lane resumes the build's session, so "by you earlier
    in this thread" is true there. A salvage follow-on has no build lane and
    nothing to resume."""
    assert "by you earlier in this thread" in shape.FIX_PROMPT

    rewritten = shape.followon_fix_prompt(shape.FIX_PROMPT)

    assert "by you earlier in this thread" not in rewritten
    assert "You did not write it" in rewritten
    # Everything after the opening is untouched.
    assert "<spec>\n{{mission.prompt}}\n</spec>" in rewritten
    assert "reproduce gate" in rewritten


@pytest.mark.parametrize("flag", ["adversarial", "opus_review"])
def test_a_lane_flag_the_cap_arithmetic_was_not_built_with_is_refused(
    tmp_path: Path, repo: Path, flag
):
    """`max_cost_usd` comes from the caps alone, so emitting a lane the caps
    were not sized for produces a mission that loads and then runs out of
    budget partway through."""
    spec = _spec(tmp_path)
    with pytest.raises(shape.ShapeInvalid, match=flag):
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1),
            **{flag: True},
        )

    # And the other direction: caps sized for a lane the shape will not emit.
    with pytest.raises(shape.ShapeInvalid, match=flag):
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1, **{flag: True}),
        )


def test_followon_budget_is_the_mission_budget_minus_the_build_cap():
    """A salvage follow-on has no build lane, but the grace on grok, fix,
    and opus is still spend against the same ledger. The old inline sum
    left that band out of the ceiling."""
    caps = shape.cap_arithmetic(2, 1)
    assert caps.followon_budget == round(caps.mission_budget - caps.build_cap, 2)
    omitted = round(caps.review_caps + caps.fix_cap + shape.USD_MISSION_SLACK, 2)
    assert caps.followon_budget == round(
        omitted + caps.graced_lanes * caps.cap_grace_usd, 2
    )
    with_opus = shape.cap_arithmetic(2, 1, opus_review=True)
    assert with_opus.followon_budget == round(
        with_opus.mission_budget - with_opus.build_cap, 2
    )
    assert with_opus.followon_budget == round(
        caps.followon_budget + with_opus.opus_cap + with_opus.cap_grace_usd, 2
    )


def test_followon_max_cost_uses_the_followon_budget(repo):
    caps = shape.cap_arithmetic(1, 1)
    raw = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test="true",
        caps=caps,
        name="salvaged",
    )
    assert raw["max_cost_usd"] == caps.followon_budget
    # The old inline sum left grace out of the ceiling.
    assert raw["max_cost_usd"] != round(
        caps.review_caps + caps.fix_cap + shape.USD_MISSION_SLACK, 2
    )


def test_followon_refuses_an_opus_flag_the_cap_arithmetic_was_not_built_with(repo):
    """Emitting review-opus against caps that were not sized for it ships
    a $4 lane the follow-on budget never counted."""
    with pytest.raises(shape.ShapeInvalid, match="opus_review"):
        shape.shape_a_followon(
            worktree=repo,
            salvage_sha="abc123",
            diff="",
            test="true",
            caps=shape.cap_arithmetic(1, 1),
            name="salvaged",
            opus_review=True,
        )
    with pytest.raises(shape.ShapeInvalid, match="opus_review"):
        shape.shape_a_followon(
            worktree=repo,
            salvage_sha="abc123",
            diff="",
            test="true",
            caps=shape.cap_arithmetic(1, 1, opus_review=True),
            name="salvaged",
        )


def test_disabled_grace_puts_nothing_in_the_mission_budget():
    caps = shape.cap_arithmetic(2, 1, cap_grace_usd=0.0)
    assert caps.graced_lanes == 0
    assert caps.mission_budget == caps.build_cap + 1.0 + 1.5 + caps.fix_cap + 1.5


def test_render_prints_every_term_not_just_the_sum():
    """Every line the lead reads to find which term is wrong. The old body
    checked the build line alone, so dropping the fix, grace, or review
    lines left the suite green."""
    caps = shape.cap_arithmetic(5, 4, scheduler=True, adversarial=True, opus_review=True)
    text = caps.render()
    assert "$5.00 5 spec items + $2.00 scheduler tax + $2.00 2 modules past the second" in text
    assert "build cap: " in text and f"= ${caps.build_cap:.2f}" in text
    assert f"review-gemini cap: ${caps.gemini_cap:.2f}" in text
    assert f"review-grok cap: ${caps.grok_cap:.2f}" in text
    assert "review-opus cap: " in text and f"= ${caps.opus_cap:.2f}" in text
    assert "adversarial cap: " in text and f"= ${caps.adversarial_cap:.2f}" in text
    assert "fix cap: " in text and f"= ${caps.fix_cap:.2f}" in text
    grace_total = caps.graced_lanes * caps.cap_grace_usd
    assert f"grace: ${caps.cap_grace_usd:.2f}" in text
    assert f"{caps.graced_lanes} lanes, ${grace_total:.2f} in the mission budget" in text
    assert f"+ ${grace_total:.2f} grace + $1.50 slack = ${caps.mission_budget:.2f}" in text


@pytest.mark.parametrize("items,modules", [(0, 1), (1, 0)])
def test_counts_below_one_are_refused(items, modules):
    with pytest.raises(shape.ShapeInvalid):
        shape.cap_arithmetic(items, modules)


# --- the mission the template produces ---------------------------------------


def test_shape_a_mission_loads_and_carries_the_shape(repo, tmp_path):
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(2, 1)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps)
    shape.write_shape_schemas(spec.parent)
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
    assert grok["cap_usd"] == 1.5 and "DO NOT RUN THE TEST SUITE" in grok["prompt"]
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
    assert "<gate>\ntrue\n</gate>" in grok["prompt"]
    assert "gate in the <gate> block" in grok["prompt"]
    assert "named in the spec" not in grok["prompt"]
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
    shape.write_shape_schemas(tmp_path)
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
    shape.write_shape_schemas(spec.parent)
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
    # The cap, and the grace band that lane may draw on top of it.
    assert with_opus.mission_budget == round(
        plain.mission_budget + 4.0 + plain.cap_grace_usd, 2
    )
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
    assert raw["max_cost_usd"] == caps.followon_budget
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
    written = (out.parent / "prompts" / "review-opus.md").read_text()
    assert written == shape.review_prompt_with_evidence(shape.OPUS_REVIEW_PROMPT)


# --- Evidence map: the build lane's deliverable (Phase H item 6) ------------


def test_build_lane_declares_the_evidence_map_and_reviewers_read_it(repo, tmp_path):
    spec = _spec(tmp_path)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1))
    shape.write_shape_schemas(spec.parent)
    mission = mission_from_dict(raw, base_dir=spec.parent)
    assert [lane.name for lane in mission.lanes] == ["build", "review-gemini", "review-grok", "fix"]
    build = raw["lanes"][0]
    assert build["deliverable"] == {
        "path": "evidence.json",
        "schema": "evidence.schema.json",
        "commit": False,
    }
    assert "evidence.json" in build["prompt"] and '"not_built"' in build["prompt"]
    assert (spec.parent / "evidence.schema.json").is_file()
    schema = json.loads((spec.parent / "evidence.schema.json").read_text())
    assert schema["required"] == ["items"]
    for lane in raw["lanes"][1:3]:
        prompt = lane["prompt"]
        assert "<evidence>\n{{lanes.build.deliverable}}\n</evidence>" in prompt
        assert prompt.index("</change>") < prompt.index("<evidence>")
        assert prompt.index("<evidence>") < prompt.index("Report anything")
        assert "Treat it as a claim" in prompt
        assert "NO_FINDINGS" in prompt and "at least" not in prompt
    assert "<evidence>" not in raw["lanes"][3]["prompt"]


def test_opus_reviewer_reads_the_evidence_map_too(repo, tmp_path):
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(1, 1, opus_review=True)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=caps, opus_review=True)
    assert "<evidence>\n{{lanes.build.deliverable}}\n</evidence>" in raw["lanes"][3]["prompt"]


def test_followon_has_no_evidence_block_because_it_has_no_build_lane(repo, tmp_path):
    raw = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        name="salvaged",
    )
    for lane in raw["lanes"]:
        assert "<evidence>" not in lane["prompt"]
        assert "deliverable" not in lane or lane["deliverable"]["path"] != "evidence.json"


def test_cli_writes_the_evidence_schema_beside_the_mission(
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
    assert (out.parent / "evidence.schema.json").is_file()
    assert '"ok": true' in capsys.readouterr().out


# --- F23: a document or data deliverable through the shape ---------------------


def _deliverable_mission(tmp_path: Path, repo: Path, **kw) -> dict:
    # The caps carry the same lane flags the shape does: a mission budget
    # sized without the extra lane's cap runs out partway through.
    return shape.shape_a(
        spec=_spec(tmp_path),
        repo=repo,
        test="python3 check.py doc.md",
        caps=shape.cap_arithmetic(
            1,
            1,
            adversarial=kw.get("adversarial", False),
            opus_review=kw.get("opus_review", False),
        ),
        deliverable="doc.md",
        deliverable_validator="python3 check.py {path}",
        **kw,
    )


def test_deliverable_replaces_the_evidence_map_and_the_fix_lane_becomes_a_build_lane(
    tmp_path: Path, repo: Path
):
    raw = _deliverable_mission(tmp_path, repo)
    lanes = {lane["name"]: lane for lane in raw["lanes"]}
    assert lanes["build"]["deliverable"] == {
        "path": "doc.md",
        "validator": "python3 check.py {path}",
    }
    assert "evidence.json" not in lanes["build"]["prompt"]
    assert "`doc.md`" in lanes["build"]["prompt"]
    assert "python3 check.py doc.md" in lanes["build"]["prompt"]
    assert "python3 check.py {path}" not in lanes["build"]["prompt"]
    assert lanes["fix"]["stage"] == "build"
    assert lanes["fix"]["deliverable"]["path"] == "dispositions.json"
    assert "reproduce gate" not in lanes["fix"]["prompt"].split("not under the reproduce gate")[0]
    assert "there is no test to write" in lanes["fix"]["prompt"]
    assert "dispositions.json" in lanes["fix"]["prompt"]
    for name in ("review-gemini", "review-grok"):
        assert "<evidence>" not in lanes[name]["prompt"]
        assert "The build's deliverable is the file `doc.md`" in lanes[name]["prompt"]
        assert "validator `python3 check.py doc.md` passed" in lanes[name]["prompt"]
        assert "python3 check.py {path}" not in lanes[name]["prompt"]
    assert raw["policy"] == {
        "build": {"vendors": ["anthropic"]},
        "review": {"vendors": ["google", "xai"]},
    }
    assert raw["pause"] == {"before": ["fix"]}
    shape.write_shape_schemas(tmp_path / "specs")
    mission = mission_from_dict(raw, base_dir=tmp_path / "specs")
    assert [lane.stage for lane in mission.lanes] == ["build", "review", "review", "build"]


def test_deliverable_with_opus_review_notes_the_third_reviewer_too(tmp_path: Path, repo: Path):
    raw = _deliverable_mission(tmp_path, repo, opus_review=True)
    lanes = {lane["name"]: lane for lane in raw["lanes"]}
    assert "The build's deliverable is the file `doc.md`" in lanes["review-opus"]["prompt"]
    assert "<review_opus>" in lanes["fix"]["prompt"]
    shape.write_shape_schemas(tmp_path / "specs")
    mission = mission_from_dict(raw, base_dir=tmp_path / "specs")
    assert mission.self_judging == "allow"


def test_deliverable_without_a_validator_says_so_in_neither_prompt(tmp_path: Path, repo: Path):
    raw = shape.shape_a(
        spec=_spec(tmp_path),
        repo=repo,
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        deliverable="doc.md",
    )
    lanes = {lane["name"]: lane for lane in raw["lanes"]}
    assert lanes["build"]["deliverable"] == {"path": "doc.md"}
    assert "Conductor runs" not in lanes["build"]["prompt"]
    assert "validator" not in lanes["review-gemini"]["prompt"].split("Report anything")[0]


def test_deliverable_refusals(tmp_path: Path, repo: Path):
    spec = _spec(tmp_path)
    with pytest.raises(shape.ShapeInvalid, match="needs --deliverable"):
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1),
            deliverable_validator="true",
        )
    for bad in ("/abs/doc.md", "../doc.md"):
        with pytest.raises(shape.ShapeInvalid, match="repo-relative"):
            shape.shape_a(
                spec=spec,
                repo=repo,
                test="true",
                caps=shape.cap_arithmetic(1, 1),
                deliverable=bad,
            )
    with pytest.raises(shape.ShapeInvalid, match="adversarial"):
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1),
            deliverable="doc.md",
            adversarial=True,
        )


def test_without_a_deliverable_the_shape_is_unchanged(tmp_path: Path, repo: Path):
    spec = _spec(tmp_path)
    before = shape.shape_a(spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1))
    after = shape.shape_a(
        spec=spec,
        repo=repo,
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        deliverable="",
        deliverable_validator="",
    )
    assert before == after


def test_cli_deliverable_writes_the_lane_and_loads(tmp_path: Path, repo: Path, capsys):
    spec = _spec(tmp_path)
    out = tmp_path / "mission.json"
    code = main(
        [
            "shape",
            "a",
            "--spec",
            str(spec),
            "--repo",
            str(repo),
            "--test",
            "true",
            "--items",
            "1",
            "--modules",
            "1",
            "--deliverable",
            "doc.md",
            "--deliverable-validator",
            "true",
            "--out",
            str(out),
            "--skip-preflight",
        ]
    )
    assert code == 0, capsys.readouterr().err
    raw = json.loads(out.read_text())
    lanes = {lane["name"]: lane for lane in raw["lanes"]}
    assert lanes["build"]["deliverable"] == {"path": "doc.md", "validator": "true"}
    assert lanes["fix"]["stage"] == "build"
    code = main(["shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
                 "--items", "1", "--modules", "1", "--deliverable-validator", "true",
                 "--out", str(tmp_path / "m2.json"), "--skip-preflight"])
    assert code == 3
    assert "needs --deliverable" in capsys.readouterr().err


# --- shipped prompt defects (items 1-9) ---------------------------------------


def _lanes(raw: dict) -> dict[str, dict]:
    return {lane["name"]: lane for lane in raw["lanes"]}


def test_review_adversarial_and_fix_prompts_name_the_gate_block_not_the_spec(repo, tmp_path):
    """Item 1: Grok (when it runs the suite), adversarial, and fix used to
    say 'the gate named in the spec', but the spec is the operator's
    prompt_file. The assembled prompts now carry the same <gate> block
    BUILD_PROMPT does, filled from `test`."""
    spec = _spec(tmp_path)
    gate = 'pytest -q -n auto --dist loadgroup --basetemp="$TMPDIR/x"'
    caps = shape.cap_arithmetic(1, 1, grok_runs_suite=True, adversarial=True)
    raw = shape.shape_a(
        spec=spec, repo=repo, test=gate, caps=caps, adversarial=True
    )
    lanes = _lanes(raw)
    block = f"<gate>\n{gate}\n</gate>"
    for name in ("review-grok", "adversarial", "fix"):
        prompt = lanes[name]["prompt"]
        assert block in prompt
        assert "{gate}" not in prompt
        assert "named in the spec" not in prompt
        assert "gate in the <gate> block" in prompt
    # The read-only Grok lane is told not to run a gate, so it must not
    # receive one.
    read = shape.shape_a(
        spec=spec, repo=repo, test=gate, caps=shape.cap_arithmetic(1, 1)
    )
    grok_ro = _lanes(read)["review-grok"]["prompt"]
    assert "<gate>" not in grok_ro
    assert "run the gate" not in grok_ro.lower()
    # A follow-on fix lane has no build thread to recall the gate from.
    followon = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test=gate,
        caps=shape.cap_arithmetic(1, 1),
        name="salvaged",
    )
    followon_fix = _lanes(followon)["fix"]["prompt"]
    assert block in followon_fix
    assert "named in the spec" not in followon_fix
    assert "gate in the <gate> block" in followon_fix


def test_a_prompt_with_no_gate_does_not_claim_there_is_one():
    """Item 1: empty `test` on the helpers must not tell the model to run
    a gate that was never named."""
    for assembled in (
        shape.grok_review_prompt(""),
        shape.grok_review_prompt(),
        shape.adversarial_prompt(""),
        shape.fix_prompt(""),
    ):
        assert "<gate>" not in assembled
        assert "{gate}" not in assembled
        assert "run the gate" not in assembled.lower()
        assert "named in the spec" not in assembled


def test_dispositions_schema_follows_the_three_reviewer_rewrite(tmp_path):
    """Item 2: `--opus-review` rewrote FIX_PROMPT to name review-opus, but
    DISPOSITIONS_SCHEMA's description still said only gemini or grok.
    The schema the fixer receives as the deliverable contract must match."""
    two = json.loads(shape.write_dispositions_schema(tmp_path / "two").read_text())
    two_desc = two["properties"]["dispositions"]["description"]
    assert '"review-gemini" or "review-grok"' in two_desc
    assert "review-opus" not in two_desc
    assert "both reviews said NO_FINDINGS" in two_desc
    assert two == shape.DISPOSITIONS_SCHEMA
    # Existing positional signature still writes the two-reviewer schema.
    default = json.loads(shape.write_dispositions_schema(tmp_path / "default").read_text())
    assert default == shape.DISPOSITIONS_SCHEMA

    three = json.loads(
        shape.write_dispositions_schema(tmp_path / "three", opus_review=True).read_text()
    )
    three_desc = three["properties"]["dispositions"]["description"]
    assert "review-opus" in three_desc
    assert '"review-gemini" or "review-grok"' not in three_desc
    assert "both reviews" not in three_desc
    assert "every review said NO_FINDINGS" in three_desc
    assert "any reviewer numbered" in three_desc
    # The prompt rewrite and the schema rewrite agree on the lane list.
    assert (
        '{"lane": "review-gemini", "review-grok", or "review-opus", "index": '
        in shape.fix_prompt_with_opus(shape.fix_prompt("true"))
    )
    assert '{"lane": "review-gemini", "review-grok", or "review-opus", "index": ' in three_desc


def test_deliverable_fix_prompt_does_not_claim_reproduce_or_basetemp(tmp_path, repo):
    """Item 3: a document fix lane is `stage: build` with a file validator,
    so 'nothing reproduces' and pytest's --basetemp are both false. Item 1's
    <gate> block still names the mission test command."""
    raw = _deliverable_mission(tmp_path, repo)
    prompt = _lanes(raw)["fix"]["prompt"]
    gate = "python3 check.py doc.md"
    assert f"<gate>\n{gate}\n</gate>" in prompt
    assert "named in the spec" not in prompt
    assert "nothing reproduces" not in prompt
    assert "--basetemp" not in prompt
    assert "Run the gate in the <gate> block before finishing." in prompt
    assert "If both reviews say NO_FINDINGS, change nothing and reply NO_CHANGES." in prompt
    # Opus wording too: the reproduce phrase is gone after that rewrite.
    opus_spec = tmp_path / "opus.md"
    opus_spec.write_text("# opus\n")
    opus = shape.shape_a(
        spec=opus_spec,
        repo=repo,
        test=gate,
        caps=shape.cap_arithmetic(1, 1, opus_review=True),
        deliverable="doc.md",
        deliverable_validator="python3 check.py {path}",
        opus_review=True,
    )
    opus_fix = _lanes(opus)["fix"]["prompt"]
    assert "nothing reproduces" not in opus_fix
    assert "--basetemp" not in opus_fix
    assert "If every review says NO_FINDINGS, change nothing and reply NO_CHANGES." in opus_fix


def test_deliverable_build_prompt_drops_code_rules_and_does_not_repeat_them(tmp_path, repo):
    """Item 4: a one-file deliverable is not a code change, so call-signature
    and test-gaming sentences do not apply, and the commit / investigate
    lines must appear once (from the deliverable paragraph, not twice)."""
    raw = _deliverable_mission(tmp_path, repo)
    prompt = _lanes(raw)["build"]["prompt"]
    assert "existing call signature" not in prompt
    assert "special-case a test" not in prompt
    assert prompt.count("Do not commit; the harness commits") == 1
    assert prompt.count("Investigate before answering") == 1
    assert "read the file in full" in prompt
    assert "read the code a change touches" not in prompt
    # A code build still carries both rules.
    code_spec = tmp_path / "code.md"
    code_spec.write_text("# code\n")
    code = shape.shape_a(
        spec=code_spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1)
    )
    code_prompt = _lanes(code)["build"]["prompt"]
    assert "existing call signature" in code_prompt
    assert "special-case a test" in code_prompt


def test_validator_command_in_prompts_has_path_substituted(tmp_path, repo):
    """Item 5: conductor substitutes `{path}` at run time; the copy in
    prompt text must already name the deliverable, everywhere a validator
    command appears."""
    raw = _deliverable_mission(tmp_path, repo)
    lanes = _lanes(raw)
    assert lanes["build"]["deliverable"]["validator"] == "python3 check.py {path}"
    for name in ("build", "review-gemini", "review-grok", "fix"):
        prompt = lanes[name]["prompt"]
        assert "python3 check.py doc.md" in prompt
        assert "{path}" not in prompt
    # review_prompt_with_deliverable is the helper the reviewers go through.
    note = shape.review_prompt_with_deliverable(
        shape.GEMINI_REVIEW_PROMPT, "doc.md", "python3 check.py {path}"
    )
    assert "validator `python3 check.py doc.md` passed" in note
    assert "{path}" not in note


def test_build_prompt_timing_claim_is_conditional_on_a_test_suite(repo, tmp_path):
    """Item 6: 'the flags are what make it take under a minute' is true of
    this repo's pytest gate, not of a one-file validator. The claim is
    worded as conditionally as the existing pytest/--basetemp sentence."""
    spec = tmp_path / "suite.md"
    spec.write_text("# suite\n")
    suite = shape.shape_a(
        spec=spec,
        repo=repo,
        test='pytest -q -n auto --dist loadgroup --basetemp="$TMPDIR/x"',
        caps=shape.cap_arithmetic(1, 1),
    )
    suite_prompt = _lanes(suite)["build"]["prompt"]
    assert "When the gate is a test suite" in suite_prompt
    assert "the flags are what make it take under a minute" in suite_prompt
    assert "before you finish: the flags are what make it take under a minute" not in suite_prompt
    raw = _deliverable_mission(tmp_path, repo)
    deliverable_prompt = _lanes(raw)["build"]["prompt"]
    assert "When the gate is a test suite" in deliverable_prompt
    assert "before you finish: the flags are what make it take under a minute" not in (
        deliverable_prompt
    )


def test_evidence_note_says_what_to_cite_when_the_map_omits_an_item(repo, tmp_path):
    """Item 7: a spec item the map does not name has no map entry, so
    telling the reviewer to cite 'the map's entry' drops the finding
    under cite-or-drop. Cite the spec item and the map as the omission."""
    spec = _spec(tmp_path)
    raw = shape.shape_a(spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1))
    for name in ("review-gemini", "review-grok"):
        prompt = _lanes(raw)[name]["prompt"]
        assert "Treat it as a claim" in prompt
        assert "map's entry as the citation" in prompt
        assert "spec item the map does not name" in prompt
        assert "cite the spec item itself" in prompt
        assert "map as the thing that omits it" in prompt
        # The old instruction, citing a missing entry, is gone.
        assert (
            "or a spec item the map does not name is reportable "
            "the same as any other item, with the map's entry as the citation"
            not in prompt
        )


def test_followon_salvage_note_does_not_tell_reviewers_to_fix(repo):
    """Item 8: salvage_note is inside {{mission.prompt}}, which every lane
    including the read-only reviewers renders. It must describe the
    situation without asking a reviewer to do the fixer's job."""
    raw = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        name="salvaged",
        spec_prompt="the original spec",
        branch="feat/salvaged",
    )
    shared = raw["prompt"]
    assert "already committed at abc123" in shared
    assert "Review or fix" not in shared
    assert "The reviews judge the change as it stands" in shared
    assert "the fix lane, if it edits anything, lands on branch 'feat/salvaged'" in shared
    # Reviewers are still told CHANGE NOTHING / Change nothing in their
    # own prompt; the shared spec must not contradict that with 'fix'.
    gemini = _lanes(raw)["review-gemini"]["prompt"]
    grok = _lanes(raw)["review-grok"]["prompt"]
    assert "{{mission.prompt}}" in gemini and "{{mission.prompt}}" in grok
    assert "Change nothing" in gemini
    assert "CHANGE NOTHING" in grok


def test_grok_read_only_capitalizes_do_not_run_the_test_suite(repo, tmp_path):
    """Item 9: xAI capitalizes non-negotiables; running the suite in a
    Grok review worktree fails the lane on bytes. Gemini and Opus stay
    sentence case (Google and Anthropic guidance is the opposite)."""
    spec = _spec(tmp_path)
    raw = shape.shape_a(
        spec=spec, repo=repo, test="true", caps=shape.cap_arithmetic(1, 1)
    )
    lanes = _lanes(raw)
    grok = lanes["review-grok"]["prompt"]
    gemini = lanes["review-gemini"]["prompt"]
    assert "DO NOT RUN THE TEST SUITE" in grok
    assert "Do not run the test suite" not in grok
    assert "Do not run the test suite" in gemini
    assert "DO NOT RUN THE TEST SUITE" not in gemini
    opus_raw = shape.shape_a(
        spec=spec,
        repo=repo,
        test="true",
        caps=shape.cap_arithmetic(1, 1, opus_review=True),
        opus_review=True,
    )
    opus = _lanes(opus_raw)["review-opus"]["prompt"]
    assert "Do not run the test suite" in opus
    assert "DO NOT RUN THE TEST SUITE" not in opus
    # The follow-on Grok lane is also read-only.
    followon = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test="true",
        caps=shape.cap_arithmetic(1, 1),
        name="salvaged",
    )
    assert "DO NOT RUN THE TEST SUITE" in _lanes(followon)["review-grok"]["prompt"]
