"""Structured verdicts turn review prose into validated, tallyable data.

The failures pinned here are silent schema drift, model-reported headlines
overriding criterion data, and one garbled judge poisoning a downstream lane.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from conductor import cli as cli_mod
from conductor import runner as runner_mod
from conductor.fleets import DispatchRefused, Spec
from conductor.mission import MissionInvalid, mission_from_dict, run_mission
from conductor.runner import dispatch
from conductor.verdicts import (
    _RANK_KEYS,
    Criterion,
    Verdict,
    _answer_object,
    checklist_contract,
    checklist_schema,
    parse_checklist,
    parse_dispositions_deliverable,
    parse_verdict,
    render_verdict,
    valid_disposition_entry,
)
from docs import doc_section

CRITERIA = parse_checklist(
    [
        {"id": "correct", "question": "Does the change implement the spec?"},
        "tested",
    ]
)


def verdict_answer(*oks: bool, reported: str | None = None) -> str:
    items = [
        {"id": criterion.id, "ok": ok, "evidence": f"src/x.py:{index + 1}"}
        for index, (criterion, ok) in enumerate(zip(CRITERIA, oks, strict=True))
    ]
    computed = "pass" if all(oks) else "fail"
    return json.dumps({"verdict": reported or computed, "criteria": items, "summary": "reviewed"})


def codex_stream(answer: str) -> str:
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}})
    done = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return f"{item}\n{done}\n"


def install_fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir(exist_ok=True)
    binary = binary_dir / "codex"
    binary.write_text(f"#!/bin/sh\nprintf %s {shlex.quote(codex_stream(answer))}\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def test_parse_checklist_accepts_strings_and_objects_and_refuses_bad_input():
    assert parse_checklist(["tested", {"id": "no-regressions", "question": "All green?"}]) == [
        Criterion("tested", "Is tested satisfied?"),
        Criterion("no-regressions", "All green?"),
    ]
    for raw, match in [
        ("tested", "must be a list"),
        ([], "at least one criterion"),
        (["Bad"], "item 0"),
        (["a", "a"], "item 1"),
        ([{"id": "a"}], "item 0"),
        ([1], "item 0"),
    ]:
        with pytest.raises(ValueError, match=match):
            parse_checklist(raw)


def test_checklist_schema_pins_shape_and_cardinality():
    schema = checklist_schema(CRITERIA)
    assert "$schema" not in schema  # Claude Code rejects the draft URI
    assert schema["additionalProperties"] is False
    array = schema["properties"]["criteria"]
    assert array["minItems"] == array["maxItems"] == 2
    assert array["items"]["additionalProperties"] is False
    assert array["items"]["properties"]["id"]["enum"] == ["correct", "tested"]


def test_checklist_contract_requires_a_citation_for_an_ok_criterion():
    """An ok: true criterion with evidence 'no evidence' used to satisfy the
    contract; rule 5 is cite or drop, so a pass must name a hunk."""
    contract = checklist_contract(CRITERIA)
    assert "An ok: true criterion's evidence must cite a file and line or a hunk" in contract
    assert "only a correct value for a criterion reported as not ok" in contract
    assert "or say 'no evidence'" not in contract


def test_checklist_contract_requires_the_verdict_object_in_the_reply():
    """Rule 6: parse_verdict reads only the answer text. 'Write that object
    once and end there' forbids a second copy; it does not say the object
    must be in the reply. A model that writes the object to a file and
    answers 'verdict written' fails as 'answer contains no valid JSON
    object'."""
    contract = checklist_contract(CRITERIA)
    assert "Put the entire verdict in this reply" in contract
    assert "Write that object once and end there" in contract
    assert "no second copy of the object after it" in contract


def test_answer_object_refuses_two_different_ranking_objects():
    """A rank judge's `{"strongest", "reason"}` objects are never
    verdict-shaped, so without ranking keys the last object won silently."""
    text = (
        '{"strongest": "a", "reason": "a has the test"}\n\n'
        "For example:\n"
        '{"strongest": "b", "reason": "b is cheaper"}\n'
    )
    raw, problem = _answer_object(text, keys=_RANK_KEYS)
    assert raw is None
    assert problem == (
        "the answer carries 2 different ranking objects; "
        "conductor cannot tell which is the judgment"
    )
    # The default key set still names them verdict objects.
    verdict_text = (
        '{"verdict":"fail","criteria":[],"summary":"a"}\n'
        '{"verdict":"pass","criteria":[],"summary":"b"}\n'
    )
    _, verdict_problem = _answer_object(verdict_text)
    assert verdict_problem == (
        "the answer carries 2 different verdict objects; "
        "conductor cannot tell which is the judgment"
    )


def test_parse_verdict_accepts_whole_json_prose_and_code_fences():
    answer = verdict_answer(True, True)
    for wrapped in (answer, f"review follows\n{answer}\ndone", f"```json\n{answer}\n```"):
        verdict = parse_verdict(wrapped, CRITERIA)
        assert verdict.invalid is None and verdict.passed is True
        assert [item["id"] for item in verdict.criteria] == ["correct", "tested"]


def test_parse_verdict_finds_the_last_object_after_stray_prose_braces():
    answer = verdict_answer(True, True)
    for wrapped in (
        f"The guard `if (x) {{` is missing a close.\n{answer}",
        f'It printed "{{" first.\n{answer}',
    ):
        verdict = parse_verdict(wrapped, CRITERIA)
        assert verdict.invalid is None and verdict.passed is True


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda raw: raw["criteria"].pop(), "missing criterion"),
        (
            lambda raw: raw["criteria"].__setitem__(1, raw["criteria"][1] | {"id": "unknown"}),
            "unknown criterion",
        ),
        (
            lambda raw: raw["criteria"].__setitem__(1, raw["criteria"][1] | {"id": "correct"}),
            "duplicate criterion",
        ),
        (lambda raw: raw["criteria"][0].__setitem__("ok", 1), "must be a boolean"),
        (lambda raw: raw["criteria"][0].__setitem__("ok", "true"), "must be a boolean"),
        (lambda raw: raw["criteria"][0].__setitem__("evidence", ""), "non-empty string"),
        (lambda raw: raw.__setitem__("verdict", "maybe"), "pass.*fail"),
        (lambda raw: raw.__setitem__("summary", 1), "summary must be a string"),
    ],
)
def test_parse_verdict_rejects_every_untallyable_shape(mutate, reason):
    raw = json.loads(verdict_answer(True, True))
    mutate(raw)
    verdict = parse_verdict(json.dumps(raw), CRITERIA)
    assert verdict.passed is False
    assert verdict.invalid and __import__("re").search(reason, verdict.invalid)


def test_ok_true_with_reserved_no_evidence_is_invalid():
    """The contract reserves 'no evidence' for a failing criterion; an ok:
    true criterion with that literal used to parse as a pass because the
    parser only required a non-empty string."""
    raw = json.loads(verdict_answer(True, True))
    raw["criteria"][0]["evidence"] = "no evidence"
    verdict = parse_verdict(json.dumps(raw), CRITERIA)
    assert verdict.passed is False
    assert verdict.invalid == "criterion 'correct' ok: true evidence cannot be 'no evidence'"
    assert verdict.reported == "pass"
    assert verdict.criteria[0]["ok"] is True
    assert verdict.criteria[0]["evidence"] == "no evidence"
    raw_fail = json.loads(verdict_answer(True, False))
    raw_fail["criteria"][1]["evidence"] = "no evidence"
    allowed = parse_verdict(json.dumps(raw_fail), CRITERIA)
    assert allowed.invalid is None and allowed.passed is False
    assert allowed.criteria[1]["evidence"] == "no evidence"


def test_reported_verdict_never_overrides_computed_criteria():
    verdict = parse_verdict(verdict_answer(True, False, reported="pass"), CRITERIA)
    assert verdict.invalid is None and verdict.passed is False and verdict.reported == "pass"
    assert verdict.failed == ["tested"]
    assert "model reported pass; computed fail from the criteria" in verdict.summary


def test_model_reported_fail_does_not_become_a_pass():
    """A reviewer that reports fail while marking every criterion ok used
    to be computed into a pass; the headline is the only channel for
    something the checklist did not ask."""
    verdict = parse_verdict(verdict_answer(True, True, reported="fail"), CRITERIA)
    assert verdict.invalid is None and verdict.passed is False and verdict.reported == "fail"
    assert verdict.failed == []
    assert "model reported fail; computed pass from the criteria" in verdict.summary
    assert render_verdict(verdict).splitlines()[0] == "verdict: fail (2/2 ok)"


def test_render_verdict_is_deterministic_one_line_and_bounded():
    verdict = Verdict(
        passed=False,
        reported="fail",
        criteria=[
            {"id": "correct", "ok": True, "evidence": "x" * 301},
            {"id": "tested", "ok": False, "evidence": "no\nevidence"},
        ],
        failed=["tested"],
        summary="s" * 1001,
    )
    rendered = render_verdict(verdict)
    assert rendered == render_verdict(verdict)
    lines = rendered.splitlines()
    assert lines[0] == "verdict: fail (1/2 ok)"
    assert len(lines[1].removeprefix("- correct: ok: ")) == 300
    assert lines[2] == "- tested: FAIL: no evidence"
    assert len(lines[3].removeprefix("summary: ")) == 1000
    invalid = parse_verdict("not json", CRITERIA)
    assert render_verdict(invalid).splitlines()[0].startswith("verdict: invalid: ")


def test_dispatch_generates_schema_contract_and_valid_verdict(repo, home, tmp_path, monkeypatch):
    install_fake_codex(tmp_path, monkeypatch, verdict_answer(True, True))
    result = dispatch(
        Spec(fleet="codex", model="sol", prompt="review", cwd=str(repo), verdict=CRITERIA),
        home=home,
    )
    run_dir = Path(result.run_dir)
    argv = json.loads((run_dir / "argv.json").read_text())
    schema_path = run_dir / "verdict.schema.json"
    assert result.ok is True and result.verdict["passed"] is True
    assert result.summary()["verdict"] == "pass"
    assert json.loads(schema_path.read_text()) == checklist_schema(CRITERIA)
    assert argv[argv.index("--output-schema") + 1] == str(schema_path)
    assert (run_dir / "prompt.txt").read_text() == "review" + checklist_contract(CRITERIA)
    assert json.loads((run_dir / "verdict.json").read_text()) == result.verdict


def test_dispatch_fails_closed_on_prose_but_a_valid_fail_is_still_ok(
    repo, home, tmp_path, monkeypatch
):
    install_fake_codex(tmp_path, monkeypatch, "only prose")
    invalid = dispatch(
        Spec(fleet="codex", prompt="review", cwd=str(repo), verdict=CRITERIA), home=home
    )
    assert invalid.ok is False
    assert invalid.error == "verdict invalid: answer contains no valid JSON object"
    assert invalid.summary()["verdict"] == "invalid"

    install_fake_codex(tmp_path, monkeypatch, verdict_answer(True, False))
    failed = dispatch(
        Spec(fleet="codex", prompt="review again", cwd=str(repo), verdict=CRITERIA), home=home
    )
    assert failed.ok is True and failed.verdict["passed"] is False
    assert failed.summary()["verdict"] == "fail"


def test_empty_read_answer_keeps_the_existing_empty_answer_failure(
    repo, home, tmp_path, monkeypatch
):
    install_fake_codex(tmp_path, monkeypatch, "")
    result = dispatch(
        Spec(fleet="codex", prompt="review", cwd=str(repo), verdict=CRITERIA), home=home
    )
    assert result.ok is False
    assert result.failure() == "read dispatch returned no answer"
    assert result.verdict["invalid"] == "answer contains no valid JSON object"


def test_cli_combines_repeatable_ids_before_the_verdict_file(tmp_path, repo, monkeypatch, capsys):
    checklist_file = tmp_path / "checklist.json"
    checklist_file.write_text('[{"id":"from-file","question":"From file?"}]')
    monkeypatch.setenv("CONDUCTOR_HOME", str(tmp_path / "home"))
    code = cli_mod.main(
        [
            "dispatch",
            "review",
            "--fleet",
            "codex",
            "--cwd",
            str(repo),
            "--verdict",
            "first",
            "--verdict",
            "second",
            "--verdict-file",
            str(checklist_file),
            "--dry-run",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    prompt = Path(output["run_dir"], "prompt.txt").read_text()
    assert code == 0
    assert prompt.index("1. first:") < prompt.index("2. second:") < prompt.index("3. from-file:")


def test_verdict_refuses_caller_schema_and_cursor(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    with pytest.raises(DispatchRefused, match="verdict generates its own schema"):
        Spec(
            fleet="codex",
            prompt="x",
            cwd=str(tmp_path),
            schema=str(schema),
            verdict=CRITERIA,
        ).validate()
    with pytest.raises(DispatchRefused, match="no structured-output flag"):
        Spec(fleet="cursor", prompt="x", cwd=str(tmp_path), verdict=CRITERIA).validate()


@pytest.mark.parametrize(
    "extra,match",
    [
        (["--schema", "schema.json"], "verdict generates its own schema"),
        (["--fleet", "cursor"], "no structured-output flag"),
    ],
)
def test_cli_refuses_verdict_with_schema_or_cursor(tmp_path, monkeypatch, capsys, extra, match):
    (tmp_path / "schema.json").write_text('{"type":"object"}')
    monkeypatch.chdir(tmp_path)
    base = ["dispatch", "review", "--fleet", "codex", "--verdict", "correct", "--dry-run"]
    if extra[:2] == ["--fleet", "cursor"]:
        base[base.index("codex")] = "cursor"
        extra = []
    assert cli_mod.main([*base, *extra]) == 3
    assert match in capsys.readouterr().err


def claude_envelope(answer: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


def antigravity_envelope(answer: str) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": answer,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )


def _stream_for(fleet: str, answer: str) -> str:
    """Fleet-shaped stdout, so a quorum lane can be given a fleet other than
    codex without the runner failing to parse it (parsing is keyed on
    `spec.fleet`, not content)."""
    if fleet == "claude":
        return claude_envelope(answer)
    if fleet == "antigravity":
        return antigravity_envelope(answer)
    return codex_stream(answer)


def mission_streams(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, str], seen: list[str]):
    def build(spec: Spec) -> list[str]:
        seen.append(spec.prompt)
        key = spec.prompt.split()[0]
        return ["sh", "-c", f"printf %s {shlex.quote(_stream_for(spec.fleet, outcomes[key]))}"]

    monkeypatch.setattr(runner_mod, "build_argv", build)


def review_lane(name: str, fleet: str = "codex") -> dict:
    return {
        "name": name,
        "fleet": fleet,
        "prompt": name.upper(),
        "verdict": [
            {"id": "correct", "question": "Does it implement the spec?"},
            "tested",
        ],
    }


def test_readme_three_reviewer_example_is_valid_json():
    section = doc_section("### Structured review and a 2-of-3 quorum")
    example = section.split("```json\n", 1)[1].split("\n```", 1)[0]
    parsed = json.loads(example)
    assert parsed["require"] == {
        "pass": 2,
        "of": ["review-claude", "review-codex", "review-gemini"],
    }


def test_mission_quorum_templates_reports_and_stores_verdicts(repo, home, tmp_path, monkeypatch):
    seen: list[str] = []
    mission_streams(
        monkeypatch,
        {
            "R1": verdict_answer(True, True),
            "R2": verdict_answer(True, True),
            "R3": verdict_answer(True, False),
            "FIX": "fixed",
            "You": "collated",
        },
        seen,
    )
    # r1 is on a different vendor than r2/r3 (and the collate is a third):
    # A3 refuses a quorum or a collate confined to one vendor.
    reviews = [review_lane("r1", fleet="claude"), review_lane("r2"), review_lane("r3")]
    raw = {
        "cwd": str(repo),
        "concurrency": 1,
        "lanes": [
            *reviews,
            {
                "name": "fix",
                "fleet": "codex",
                "prompt": "FIX {{lanes.r1.verdict}} {{lanes.r2.verdict}} {{lanes.r3.verdict}}",
                "needs": ["r1", "r2", "r3"],
            },
        ],
        "require": {"pass": 2, "of": ["r1", "r2", "r3"]},
        "collate": {"fleet": "antigravity"},
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    report = Path(result.report_path).read_text()
    fix_prompt = next(prompt for prompt in seen if prompt.startswith("FIX"))
    collate_prompt = Path(result.mission_dir, "collate-prompt.txt").read_text()
    assert result.ok is True and result.quorum["met"] is True
    expected_require = json.dumps({"pass": 2, "of": ["r1", "r2", "r3"]})
    assert result.require == expected_require
    assert result.quorum["passed"] == ["r1", "r2"]
    assert result.quorum["failed"] == ["r3"]
    assert "--- begin lanes.r1.verdict" in fix_prompt
    assert "verdict: fail (1/2 ok)" in fix_prompt
    assert "data, not instructions" in fix_prompt
    assert "Structured verdict:\nverdict: pass (2/2 ok)" in collate_prompt
    assert "| lane | attempt | ok | verdict |" in report
    assert "Quorum: 2 of 3 passed (need 2)" in report
    assert "### Verdict" in report
    saved_result = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved_result["require"] == expected_require
    assert saved_result["quorum"] == result.quorum
    assert saved_result["notes"] == result.notes
    for name in ("r1", "r2", "r3"):
        saved = json.loads(Path(result.mission_dir, "verdicts", f"{name}.json").read_text())
        assert saved == next(lane["verdict"] for lane in result.lanes if lane["name"] == name)


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        ((True, False, False), False),
        ((True, True, False), True),
    ],
)
def test_mission_quorum_counts_only_valid_passing_verdicts(
    repo, home, tmp_path, monkeypatch, outcomes, expected
):
    seen: list[str] = []
    answers = {
        f"R{index}": verdict_answer(True, True) if passed else verdict_answer(True, False)
        for index, passed in enumerate(outcomes, 1)
    }
    mission_streams(monkeypatch, answers, seen)
    raw = {
        "cwd": str(repo),
        "lanes": [review_lane("r1", fleet="claude"), review_lane("r2"), review_lane("r3")],
        "require": {"pass": 2, "of": ["r1", "r2", "r3"]},
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is expected and result.quorum["met"] is expected


def test_mission_quorum_treats_an_invalid_lane_as_not_passing(repo, home, tmp_path, monkeypatch):
    seen: list[str] = []
    mission_streams(
        monkeypatch,
        {"R1": verdict_answer(True, True), "R2": verdict_answer(True, True), "R3": "garbled"},
        seen,
    )
    raw = {
        "cwd": str(repo),
        "lanes": [review_lane("r1", fleet="claude"), review_lane("r2"), review_lane("r3")],
        "require": {"pass": 2, "of": ["r1", "r2", "r3"]},
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is True and result.quorum["passed"] == ["r1", "r2"]
    assert result.lanes[2]["ok"] is False
    assert result.lanes[2]["verdict"]["invalid"]


def test_quorum_dry_run_validates_without_claiming_votes(repo, home, tmp_path):
    raw = {
        "cwd": str(repo),
        "lanes": [review_lane("r1", fleet="claude"), review_lane("r2"), review_lane("r3")],
        "require": {"pass": 2, "of": ["r1", "r2", "r3"]},
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, dry_run=True)
    report = Path(result.report_path).read_text()
    assert result.ok is True and result.dry_run is True
    assert result.quorum is None
    assert "Quorum:" not in report


@pytest.mark.parametrize(
    "require,lanes,match",
    [
        ({"pass": 1, "of": ["a", "missing"]}, [review_lane("a")], "unknown lane"),
        (
            {"pass": 1, "of": ["a", "b"]},
            [review_lane("a"), {"name": "b", "fleet": "codex", "prompt": "B"}],
            "without a verdict|must have a verdict",
        ),
        (
            {"pass": 3, "of": ["a", "b"]},
            [review_lane("a"), review_lane("b")],
            "cannot exceed",
        ),
        ({"pass": 1, "of": ["a"]}, [review_lane("a")], "at least two"),
    ],
)
def test_bad_quorum_is_refused_at_load(tmp_path, require, lanes, match):
    with pytest.raises(MissionInvalid, match=match):
        mission_from_dict(
            {"cwd": str(tmp_path), "lanes": lanes, "require": require}, base_dir=tmp_path
        )


def test_verdict_is_inherited_by_fallback_attempts(tmp_path):
    mission = mission_from_dict(
        {
            "prompt": "review",
            "verdict": ["correct"],
            "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude"}]}],
        },
        base_dir=tmp_path,
    )
    primary, fallback = mission.lanes[0].attempts
    assert primary.verdict == fallback.verdict == [Criterion("correct", "Is correct satisfied?")]


def test_out_of_order_criteria_are_normalized_not_refused():
    criteria = parse_checklist(["alpha", "beta"])
    answer = json.dumps(
        {
            "verdict": "pass",
            "criteria": [
                {"id": "beta", "ok": True, "evidence": "b"},
                {"id": "alpha", "ok": True, "evidence": "a"},
            ],
            "summary": "fine",
        }
    )
    verdict = parse_verdict(answer, criteria)
    assert verdict.invalid is None and verdict.passed
    assert [item["id"] for item in verdict.criteria] == ["alpha", "beta"]
    assert "normalized" in verdict.summary


# --- D9: a fleet's own answer must not be able to raise ---------------------


def test_a_verdict_field_that_is_not_a_string_is_invalid_not_a_crash():
    """`x in {"pass", "fail"}` raises TypeError on an unhashable value, and
    the value here is whatever a fleet wrote. An invalid verdict is already a
    recorded outcome; a crash after the spend loses the whole receipt."""
    answer = json.dumps({"verdict": ["pass"], "criteria": [], "summary": "s"})
    verdict = parse_verdict(answer, [Criterion(id="a", question="a?")])
    assert verdict.passed is False
    assert verdict.invalid == "verdict must be 'pass' or 'fail'"
    assert verdict.criteria[0]["id"] == "a" and verdict.criteria[0]["ok"] is False


def test_a_disposition_that_is_not_a_string_is_refused_not_a_crash():
    """The same shape one level down: `disposition` is compared against a
    frozenset, so a list there raised where the entry should merely have
    been counted malformed."""
    entry = {"lane": "fix", "index": 1, "disposition": ["fixed"], "reason": "r"}
    assert valid_disposition_entry(entry) is False
    dispositions, malformed = parse_dispositions_deliverable(
        json.dumps({"dispositions": [entry]})
    )
    assert dispositions == [] and malformed == 1


def test_a_disposition_with_an_empty_lane_is_refused_not_a_crash():
    """Would catch the deletion of `valid_disposition_entry`'s `bool(entry[
    "lane"])` half: `lane` is a non-empty string, not merely a string, so
    an empty lane name -- otherwise a well-formed entry -- must still be
    counted malformed rather than accepted."""
    entry = {"lane": "", "index": 1, "disposition": "fixed", "reason": "x"}
    assert valid_disposition_entry(entry) is False
    dispositions, malformed = parse_dispositions_deliverable(
        json.dumps({"dispositions": [entry]})
    )
    assert dispositions == [] and malformed == 1
