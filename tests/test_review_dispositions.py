"""F1: reviewer verdicts and fix dispositions on the ledger.

A review lane narrates before its verdict, so tallying its whole answer
against `NO_FINDINGS` counts narration as a finding. A fix lane's decision
on each reported item -- fixed, refused, already, wording -- is otherwise
prose with no receipt. `verdicts.review_verdict` and
`verdicts.fix_dispositions` parse each from the final marker line and the
`DISPOSITION:` lines respectively; `mission.py` records both on the lane
receipt, and `report.py` tallies them across missions.

F15 mission 2 item 1 adds per-finding `FINDING: <n> <file>:<line>
confidence <1-10>` lines to `review_verdict`'s output (`items`,
`items_malformed`); item 2 adds the fix lane's `dispositions.json`
deliverable as a second channel `verdicts.parse_dispositions_deliverable`
and `mission.py`'s `settle()` read ahead of the `DISPOSITION:` prose lines.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import (
    LaneResult,
    _read_lane_text,
    _review_fix_label,
    mission_from_dict,
    run_mission,
)
from conductor.verdicts import (
    dispositions_malformed,
    fix_dispositions,
    parse_dispositions_deliverable,
    review_verdict,
    valid_disposition_entry,
)

# --- verdicts.review_verdict -------------------------------------------------


def test_review_verdict_ignores_narration_before_no_findings():
    answer = "Looked at every hunk, nothing stood out.\n\nNO_FINDINGS"
    assert review_verdict(answer) == {
        "verdict": "no_findings",
        "findings": 0,
        "items": [],
        "items_malformed": 0,
    }


def test_review_verdict_exact_no_findings_with_no_narration():
    assert review_verdict("NO_FINDINGS") == {
        "verdict": "no_findings",
        "findings": 0,
        "items": [],
        "items_malformed": 0,
    }


def test_review_verdict_findings_zero_is_distinct_from_no_findings():
    answer = "1. file.py:12: numbered but withdrawn on reflection.\n\nFINDINGS: 0"
    assert review_verdict(answer) == {
        "verdict": "findings",
        "findings": 0,
        "items": [],
        "items_malformed": 0,
    }


def test_review_verdict_reads_the_findings_count():
    answer = "1. a.py:1: off by one\n2. b.py:9: wrong sign\n\nFINDINGS: 2"
    assert review_verdict(answer) == {
        "verdict": "findings",
        "findings": 2,
        "items": [],
        "items_malformed": 0,
    }


def test_review_verdict_with_no_final_marker_is_unparsed():
    answer = "file.py:12: off-by-one, no consequence stated"
    assert review_verdict(answer) == {
        "verdict": "unparsed",
        "findings": None,
        "items": [],
        "items_malformed": 0,
    }


def test_review_verdict_ignores_trailing_whitespace_and_code_fence():
    assert review_verdict("NO_FINDINGS\n\n  \n") == {
        "verdict": "no_findings",
        "findings": 0,
        "items": [],
        "items_malformed": 0,
    }
    assert review_verdict("```\nFINDINGS: 3\n```\n") == {
        "verdict": "findings",
        "findings": 3,
        "items": [],
        "items_malformed": 0,
    }


def test_review_verdict_findings_n_is_case_sensitive_and_exact():
    assert review_verdict("findings: 1") == {
        "verdict": "unparsed",
        "findings": None,
        "items": [],
        "items_malformed": 0,
    }
    assert review_verdict("FINDINGS: 1 (roughly)") == {
        "verdict": "unparsed",
        "findings": None,
        "items": [],
        "items_malformed": 0,
    }


# --- F15 mission 2 item 1: FINDING: <n> <file>:<line> confidence <1-10> -----


def test_review_verdict_parses_two_finding_lines_and_counts_one_malformed():
    answer = (
        "1. a.py:3: off-by-one\n"
        "2. b.py:8: wrong sign\n"
        "FINDING: 1 a.py:3 confidence 8\n"
        "FINDING: 2 b.py:8 confidence 15\n"
        "FINDING: nope not a line at all\n"
        "\nFINDINGS: 2"
    )
    result = review_verdict(answer)
    assert result["items"] == [{"index": 1, "file": "a.py", "line": 3, "confidence": 8}]
    assert result["items_malformed"] == 2
    assert result["verdict"] == "findings" and result["findings"] == 2


def test_review_verdict_old_style_answer_has_no_items():
    answer = "1. a.py:1: off by one\n2. b.py:9: wrong sign\n\nFINDINGS: 2"
    result = review_verdict(answer)
    assert result["items"] == []
    assert result["items_malformed"] == 0
    assert result["verdict"] == "findings" and result["findings"] == 2


def test_review_verdict_no_findings_writes_no_finding_lines():
    answer = "Looked around, nothing stood out.\n\nNO_FINDINGS"
    result = review_verdict(answer)
    assert result["items"] == [] and result["items_malformed"] == 0


# --- verdicts.fix_dispositions and dispositions_malformed --------------------


def test_fix_dispositions_parses_every_line_in_order():
    answer = (
        "Fixed the first, rejected the second.\n"
        "DISPOSITION: review-gemini 1 fixed: added the missing bounds check\n"
        "some other prose in between\n"
        "DISPOSITION: review-grok 2 refused: the report misread the guard clause\n"
    )
    assert fix_dispositions(answer) == [
        {
            "lane": "review-gemini",
            "index": 1,
            "disposition": "fixed",
            "reason": "added the missing bounds check",
        },
        {
            "lane": "review-grok",
            "index": 2,
            "disposition": "refused",
            "reason": "the report misread the guard clause",
        },
    ]
    assert dispositions_malformed(answer) == 0


def test_fix_dispositions_skips_and_counts_a_malformed_line():
    answer = (
        "DISPOSITION: review-gemini 1 fixed: ok\n"
        "DISPOSITION: review-grok not-a-number wording: missing the index\n"
        "DISPOSITION: review-grok 2 already: was already handled upstream\n"
    )
    parsed = fix_dispositions(answer)
    assert [item["disposition"] for item in parsed] == ["fixed", "already"]
    assert dispositions_malformed(answer) == 1


def test_fix_dispositions_empty_when_no_marker_present():
    assert fix_dispositions("NO_CHANGES") == []
    assert dispositions_malformed("NO_CHANGES") == 0


# --- LaneResult.from_dict: old receipts predate the new keys -----------------


def test_lane_result_from_dict_defaults_review_and_dispositions_to_none():
    raw = {"name": "review-gemini", "ok": True, "stage": "review"}
    lane = LaneResult.from_dict(raw)
    assert lane.review is None
    assert lane.dispositions is None
    assert lane.dispositions_malformed is None
    # And a receipt written by the new code round-trips through the same path.
    lane.review = {"verdict": "findings", "findings": 2}
    lane.dispositions = [
        {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "x"}
    ]
    lane.dispositions_malformed = 0
    assert LaneResult.from_dict(lane.to_dict()) == lane


def test_lane_result_from_dict_refuses_a_malformed_disposition_entry():
    raw = {
        "name": "fix",
        "ok": True,
        "stage": "fix",
        "dispositions": [
            {"lane": "review", "index": 1, "disposition": "fixed", "reason": "x"},
            {"lane": "review", "index": "not-an-int", "disposition": "fixed", "reason": "y"},
        ],
    }
    with pytest.raises(ValueError, match="well-formed disposition"):
        LaneResult.from_dict(raw)


# --- F15 mission 2 item 2: valid_disposition_entry and the deliverable ------


def test_valid_disposition_entry_accepts_the_canonical_shape():
    assert valid_disposition_entry(
        {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "x"}
    )


def test_valid_disposition_entry_rejects_bad_index_disposition_and_extra_keys():
    assert not valid_disposition_entry(
        {"lane": "review-gemini", "index": "1", "disposition": "fixed", "reason": "x"}
    )
    assert not valid_disposition_entry(
        {"lane": "review-gemini", "index": 1, "disposition": "wrong", "reason": "x"}
    )
    assert not valid_disposition_entry(
        {
            "lane": "review-gemini",
            "index": 1,
            "disposition": "fixed",
            "reason": "x",
            "extra": 1,
        }
    )
    assert not valid_disposition_entry("not a dict")


def test_parse_dispositions_deliverable_counts_two_entries_and_one_malformed():
    text = json.dumps(
        {
            "dispositions": [
                {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                {"lane": "review-grok", "index": 2, "disposition": "refused", "reason": "r2"},
                {"lane": "review-grok", "index": "oops", "disposition": "fixed", "reason": "r3"},
            ]
        }
    )
    dispositions, malformed = parse_dispositions_deliverable(text)
    assert dispositions == [
        {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
        {"lane": "review-grok", "index": 2, "disposition": "refused", "reason": "r2"},
    ]
    assert malformed == 1


def test_parse_dispositions_deliverable_handles_bad_shapes():
    assert parse_dispositions_deliverable("not json") == ([], 0)
    assert parse_dispositions_deliverable("[]") == ([], 0)
    assert parse_dispositions_deliverable("{}") == ([], 0)


# --- mission.py: _review_fix_label shows a fix lane's malformed count -------


def test_review_fix_label_appends_the_malformed_count_when_non_zero():
    lane = LaneResult(
        name="fix",
        ok=True,
        dispositions=[{"lane": "review", "index": 1, "disposition": "fixed", "reason": "x"}],
        dispositions_malformed=2,
    )
    assert _review_fix_label(lane) == "1 fixed, 2 malformed"


def test_review_fix_label_omits_the_malformed_count_when_zero():
    lane = LaneResult(
        name="fix",
        ok=True,
        dispositions=[{"lane": "review", "index": 1, "disposition": "fixed", "reason": "x"}],
        dispositions_malformed=0,
    )
    assert _review_fix_label(lane) == "1 fixed"


# --- mission.py: settle() computes both from the lane's own answer ----------


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


REVIEW_ANSWER = "1. a.py:3: off-by-one\n2. b.py:8: wrong sign\n\nFINDINGS: 2"
FIX_ANSWER = (
    "DISPOSITION: review 1 fixed: corrected the loop bound\n"
    "DISPOSITION: review 2 refused: b.py:8 was already correct\n"
    "DISPOSITION: review garbled wording: missing the index\n"
)


def _build_argv(spec: Spec) -> list[str]:
    if spec.prompt == "REVIEW":
        return ["sh", "-c", f"printf %s {shlex.quote(antigravity_envelope(REVIEW_ANSWER))}"]
    return ["sh", "-c", f"printf %s {shlex.quote(claude_envelope(FIX_ANSWER))}"]


def test_mission_settle_records_review_verdict_and_fix_dispositions(
    repo, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_argv", _build_argv)
    raw = {
        "cwd": str(repo),
        "concurrency": 2,
        "lanes": [
            {
                "name": "review",
                "fleet": "antigravity",
                "stage": "review",
                "mode": "read",
                "prompt": "REVIEW",
            },
            {
                "name": "fix",
                "fleet": "claude",
                "stage": "fix",
                "mode": "write",
                "prompt": "FIX",
            },
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    by_name = {lane["name"]: lane for lane in result.lanes}

    assert by_name["review"]["review"] == {
        "verdict": "findings",
        "findings": 2,
        "items": [],
        "items_malformed": 0,
    }
    assert by_name["fix"]["dispositions"] == [
        {
            "lane": "review",
            "index": 1,
            "disposition": "fixed",
            "reason": "corrected the loop bound",
        },
        {
            "lane": "review",
            "index": 2,
            "disposition": "refused",
            "reason": "b.py:8 was already correct",
        },
    ]
    assert by_name["fix"]["dispositions_malformed"] == 1

    receipt = json.loads(Path(result.mission_dir, "lanes", "review.json").read_text())
    assert receipt["review"] == {
        "verdict": "findings",
        "findings": 2,
        "items": [],
        "items_malformed": 0,
    }
    fix_receipt = json.loads(Path(result.mission_dir, "lanes", "fix.json").read_text())
    assert fix_receipt["dispositions_malformed"] == 1

    report = Path(result.report_path).read_text()
    assert "2 findings" in report
    assert "1 fixed, 1 refused" in report


DELIVERABLE_PAYLOAD = json.dumps(
    {
        "dispositions": [
            {
                "lane": "review",
                "index": 1,
                "disposition": "fixed",
                "reason": "corrected the loop bound",
            },
            {
                "lane": "review",
                "index": 2,
                "disposition": "refused",
                "reason": "b.py:8 was already correct",
            },
            {"lane": "review", "index": "oops", "disposition": "fixed", "reason": "bad index"},
        ]
    }
)


def _build_argv_with_deliverable(spec: Spec) -> list[str]:
    if spec.prompt == "REVIEW":
        return ["sh", "-c", f"printf %s {shlex.quote(antigravity_envelope(REVIEW_ANSWER))}"]
    # The prose disagrees with the deliverable on item 1 (refused here,
    # fixed in the deliverable) so the test can tell which channel won.
    answer = claude_envelope("DISPOSITION: review 1 refused: ignored, deliverable wins\n")
    write_deliverable = f"printf %s {shlex.quote(DELIVERABLE_PAYLOAD)} > dispositions.json"
    return ["sh", "-c", f"{write_deliverable}; printf %s {shlex.quote(answer)}"]


def test_mission_settle_prefers_the_dispositions_deliverable_over_prose(
    repo, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_argv", _build_argv_with_deliverable)
    raw = {
        "cwd": str(repo),
        "concurrency": 2,
        "lanes": [
            {
                "name": "review",
                "fleet": "antigravity",
                "stage": "review",
                "mode": "read",
                "prompt": "REVIEW",
            },
            {
                "name": "fix",
                "fleet": "claude",
                "stage": "fix",
                "mode": "write",
                "prompt": "FIX",
                "deliverable": {"path": "dispositions.json"},
            },
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    by_name = {lane["name"]: lane for lane in result.lanes}

    assert by_name["fix"]["dispositions"] == [
        {
            "lane": "review",
            "index": 1,
            "disposition": "fixed",
            "reason": "corrected the loop bound",
        },
        {
            "lane": "review",
            "index": 2,
            "disposition": "refused",
            "reason": "b.py:8 was already correct",
        },
    ]
    assert by_name["fix"]["dispositions_malformed"] == 1


def test_every_report_table_row_has_the_header_cell_count(repo, home, monkeypatch, tmp_path):
    """Cross-vendor review of F1: the `review/fix` column widened the header
    to 19 cells, and the human row and the skipped-without-attempts row were
    still 18, so `taint` rendered under `branch`."""
    from conductor import runner as runner_mod
    from conductor.mission import mission_from_dict, run_mission

    def build(spec):
        if "FAIL" in spec.prompt:
            return ["sh", "-c", "exit 1"]
        return ["sh", "-c", "echo ok"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "cwd": str(repo),
        "prompt": "SPEC",
        "lanes": [
            {"name": "first", "fleet": "claude", "mode": "read", "prompt": "FAIL"},
            {
                "name": "second",
                "fleet": "claude",
                "mode": "read",
                "needs": ["first"],
                "prompt": "R",
            },
            {"name": "ask", "fleet": "human", "prompt": "answer me"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    report = Path(result.report_path).read_text()
    table = [line for line in report.splitlines() if line.startswith("| ")]
    header = table[0]
    assert "review/fix" in header
    width = header.count("|")
    rows = [line for line in table[1:] if not line.startswith("|---")]
    assert rows, report
    assert {line.count("|") for line in rows} == {width}, "\n".join(rows)


def test_read_lane_text_survives_an_undecodable_byte_and_a_missing_file(tmp_path):
    """F15 latent item: `settle()` runs on every lane completion path after
    the spend, so its answer read must never raise on a stray byte."""
    answer = tmp_path / "answer.txt"
    answer.write_bytes(b"narration \xff\nNO_FINDINGS\n")
    text = _read_lane_text(str(answer))
    assert text is not None
    assert text.endswith("NO_FINDINGS\n")
    assert review_verdict(text)["verdict"] == "no_findings"
    assert _read_lane_text(str(tmp_path / "missing.txt")) is None
    assert _read_lane_text(None) is None
