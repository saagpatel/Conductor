"""Shape A prompt and schema defects: adversarial dispositions, fingerprints,
the no-quota bar, ports on gate-running lanes, and build_prompt through with_gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conductor import shape
from conductor.prompts import prompt_versions


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n2. two\n")
    return spec


def _lanes(raw: dict) -> dict[str, dict]:
    return {lane["name"]: lane for lane in raw["lanes"]}


# --- item 1: adversarial lane in the fix prompt and dispositions schema ------


def test_adversarial_mission_tells_the_fixer_about_that_lane(repo, tmp_path):
    """`--adversarial` used to append the report block and leave FIX_PROMPT
    saying both-reviews-NO_FINDINGS means change nothing, with no legal
    `lane` value for the test the mission just paid for."""
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(1, 1, adversarial=True)
    raw = shape.shape_a(
        spec=spec, repo=repo, test="true", caps=caps, adversarial=True
    )
    prompt = _lanes(raw)["fix"]["prompt"]
    assert '"lane": "review-gemini", "review-grok", or "adversarial"' in prompt
    assert '"lane": "review-gemini" or "review-grok"' not in prompt
    assert (
        "If both reviews say NO_FINDINGS and the adversarial lane says NO_FINDINGS"
        in prompt
    )
    assert "If both reviews say NO_FINDINGS or nothing reproduces" not in prompt
    assert "no items to disposition" in prompt
    assert (
        "not merely when both reviews said NO_FINDINGS and the adversarial lane "
        "said NO_FINDINGS"
        in prompt
    )
    assert 'lane "adversarial" and index 1' in prompt
    assert "that lane writes at most one test" in prompt
    assert "plus the adversarial lane's test when it wrote one" in prompt
    assert "adversarial-only finding" not in prompt

    written = shape.dispositions_schema(adversarial=True)
    description = written["properties"]["dispositions"]["description"]
    assert '"lane": "review-gemini", "review-grok", or "adversarial"' in description
    assert "review-opus" not in description
    assert "no items to disposition" in description
    assert "the adversarial lane said NO_FINDINGS" in description
    assert 'lane "adversarial" and index 1' in description
    assert "adversarial-only finding" not in description
    on_disk = json.loads(
        shape.write_dispositions_schema(tmp_path / "adv", adversarial=True).read_text()
    )
    assert on_disk == written

    # Default two-reviewer schema is unchanged when the flag is off.
    two = shape.dispositions_schema()
    assert two == shape.DISPOSITIONS_SCHEMA
    assert "adversarial" not in two["properties"]["dispositions"]["description"]


def test_adversarial_and_opus_review_compose_on_prompt_and_schema(repo, tmp_path):
    """The two rewrites must not fight: both flags together name every lane
    the fixer may dispose, including an adversarial-only finding."""
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(1, 1, adversarial=True, opus_review=True)
    raw = shape.shape_a(
        spec=spec,
        repo=repo,
        test="true",
        caps=caps,
        adversarial=True,
        opus_review=True,
    )
    prompt = _lanes(raw)["fix"]["prompt"]
    assert (
        '{"lane": "review-gemini", "review-grok", "review-opus", or "adversarial", "index": '
        in prompt
    )
    assert "Three reviewers" in prompt
    assert "Two reviewers" not in prompt
    assert (
        "If every review says NO_FINDINGS and the adversarial lane says NO_FINDINGS"
        in prompt
    )
    assert "If both reviews say NO_FINDINGS" not in prompt
    assert 'lane "adversarial" and index 1' in prompt

    description = shape.dispositions_schema(
        opus_review=True, adversarial=True
    )["properties"]["dispositions"]["description"]
    assert (
        '{"lane": "review-gemini", "review-grok", "review-opus", or "adversarial", "index": '
        in description
    )
    assert "every review said NO_FINDINGS and the adversarial lane said NO_FINDINGS" in (
        description
    )
    assert "no items to disposition" in description
    assert "both reviews" not in description
    # Each flag alone still does its own rewrite.
    opus_only = shape.dispositions_schema(opus_review=True)["properties"]["dispositions"][
        "description"
    ]
    assert "review-opus" in opus_only
    assert "adversarial" not in opus_only
    adv_only = shape.dispositions_schema(adversarial=True)["properties"]["dispositions"][
        "description"
    ]
    assert "adversarial" in adv_only
    assert "review-opus" not in adv_only


# --- item 2: every conductor-authored prompt is fingerprinted -----------------


def test_prompt_versions_fingerprints_the_adversarial_prompt_and_matches_the_inventory():
    """ADVERSARIAL_PROMPT was the one paid-lane prompt with no version id.
    The docstring's inventory is the function's keys: six review/fix texts,
    the build wrapper, and the prefix. Fragments (REVIEW_TAIL, FOLLOWON_FIX_OPENING,
    the insertion blocks) are not separate catalog entries."""
    versions = prompt_versions()
    expected_id = hashlib.sha256(shape.ADVERSARIAL_PROMPT.encode()).hexdigest()[:12]
    assert versions["shape_adversarial"] == expected_id
    assert set(k for k in versions if k.startswith("shape_")) == {
        "shape_gemini_review",
        "shape_grok_review",
        "shape_grok_read_only",
        "shape_opus_review",
        "shape_adversarial",
        "shape_fix",
        "shape_build",
        "shape_prefix",
    }
    assert versions["shape_build"] == hashlib.sha256(shape.BUILD_PROMPT.encode()).hexdigest()[
        :12
    ]


# --- item 3: adversarial prompt carries the same bar as the other reviews ---


def test_adversarial_prompt_states_the_consequence_threshold_and_asks_for_a_citation():
    """Rule 3 (consequence threshold, not adjectives) and rule 5 (cite or
    drop). `If you find` stays conditional; FINDING:/FINDINGS: N stay off
    this constant -- those markers belong to REVIEW_TAIL for F15."""
    text = shape.ADVERSARIAL_PROMPT
    assert "incorrect behavior, a test failure, or a misleading result" in text
    assert "including a spec item that is missing or only partly implemented" in text
    assert "Omit pure style and naming" in text
    assert "file and line" in text
    assert "If you find" in text
    assert "FINDING:" not in text
    assert "FINDINGS:" not in text
    assert "at least" not in text
    filled = shape.adversarial_prompt("true")
    assert "incorrect behavior, a test failure, or a misleading result" in filled
    assert "file and line" in filled
    # The other review constants still carry the same bar; this is reuse,
    # not a third phrasing.
    assert "incorrect behavior, a test failure, or a misleading result" in shape.REVIEW_TAIL
    assert "Omit pure style and naming" in shape.GROK_READ_ONLY_PROMPT


# --- item 4: every gate-running lane gets ports ------------------------------


def test_ports_reach_every_lane_that_runs_the_gate(repo, tmp_path):
    """`--ports N` is a count. dispatch claims N distinct free ports per
    spawn (bind + O_EXCL), so two gate-running lanes that overlap in time
    (adversarial and suite-running Grok, after build) get different numbers,
    never the same CONDUCTOR_PORT_1. Gemini, Opus, and read-only Grok do
    not run the gate and do not claim."""
    spec = _spec(tmp_path)
    default = _lanes(
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1),
            ports=2,
        )
    )
    assert default["build"]["ports"] == 2
    assert default["fix"]["ports"] == 2
    assert "ports" not in default["review-gemini"]
    assert "ports" not in default["review-grok"]

    overlapping = _lanes(
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1, grok_runs_suite=True, adversarial=True),
            ports=2,
            adversarial=True,
        )
    )
    assert overlapping["build"]["ports"] == 2
    assert overlapping["fix"]["ports"] == 2
    assert overlapping["adversarial"]["ports"] == 2
    assert overlapping["review-grok"]["ports"] == 2
    assert "ports" not in overlapping["review-gemini"]

    opus = _lanes(
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1, opus_review=True, grok_runs_suite=True),
            ports=1,
            opus_review=True,
        )
    )
    assert opus["build"]["ports"] == 1
    assert opus["fix"]["ports"] == 1
    assert opus["review-grok"]["ports"] == 1
    assert "ports" not in opus["review-opus"]
    assert "ports" not in opus["review-gemini"]


# --- item 5: build_prompt goes through with_gate -------------------------------


def test_build_prompt_with_no_gate_does_not_claim_there_is_one():
    """The other builders already strip an empty test. build_prompt used to
    emit an empty <gate> block and keep the instruction to run it."""
    assembled = shape.build_prompt("")
    assert "<gate>" not in assembled
    assert "{gate}" not in assembled
    assert "That is the gate the lead will run" not in assembled
    assert "Run it the same way" not in assembled
    assert "run the gate" not in assembled.lower()
    # A named gate still fills the block and keeps the run sentences.
    filled = shape.build_prompt("true")
    assert "<gate>\ntrue\n</gate>" in filled
    assert "That is the gate the lead will run" in filled
    # The other builders' empty-test and filled-gate behaviour is unchanged.
    assert "<gate>" not in shape.grok_review_prompt("")
    assert "<gate>\ntrue\n</gate>" in shape.grok_review_prompt("true")
    assert "<gate>" not in shape.fix_prompt("")
    assert "<gate>\ntrue\n</gate>" in shape.fix_prompt("true")
    assert "<gate>" not in shape.adversarial_prompt("")
    assert "<gate>\ntrue\n</gate>" in shape.adversarial_prompt("true")


# --- item 1: mission prefix is sent to every lane, including readers ----------


def test_prefix_is_true_for_a_read_lane_and_still_binds_a_write_lane(repo, tmp_path):
    """`shape_a` puts one prefix on the mission; mission._with_prefix prepends
    it to every lane, reviewers included. Telling a Change-nothing lane to
    execute a plan in its last paragraph contradicts the review prompt."""
    prefix = shape._prefix(repo, None)
    assert (
        "Before ending, check your last paragraph: if it is a plan or a promise, "
        "do that work now."
    ) not in prefix
    assert "if the prompt below asked you to write" in prefix
    assert "if it is a plan or a promise, do that work now" in prefix
    assert "Keep any changes and tests to what the prompt below asks for" in prefix
    assert "no unrequested fixes, no surplus test files" in prefix
    raw = shape.shape_a(
        spec=_spec(tmp_path), repo=repo, test="true", caps=shape.cap_arithmetic(1, 1)
    )
    assert raw["prefix"] == prefix
    gemini = _lanes(raw)["review-gemini"]["prompt"]
    grok = _lanes(raw)["review-grok"]["prompt"]
    build = _lanes(raw)["build"]["prompt"]
    assert "Change nothing" in gemini
    assert "CHANGE NOTHING" in grok
    # One prefix text: the write gate is in the prefix, not a per-lane parameter.
    assert "if the prompt below asked you to write" in raw["prefix"]
    assert "Keep every existing call signature working" in build


# --- item 2: adversarial answer is the reply the fixer interpolates ----------


def test_adversarial_prompt_says_the_whole_answer_is_the_reply():
    """The fix lane interpolates {{lanes.adversarial.answer}}. A Claude read
    lane that writes a plan file and answers 'see above' hands the fixer an
    empty block. Wording fits a test deliverable, not a review."""
    text = shape.ADVERSARIAL_PROMPT
    assert "Put the entire answer in this reply" in text
    assert "the reply is the only thing the next agent receives" in text
    assert "what the test proves, or NO_FINDINGS" in text
    assert "Put the entire review in this reply" not in text
    filled = shape.adversarial_prompt("true")
    assert "the reply is the only thing the next agent receives" in filled
    assert "Put the entire review in this reply" in shape.REVIEW_TAIL


# --- item 3: deliverable review note is true with and without a validator ----


def test_deliverable_review_note_is_true_whether_or_not_a_validator_ran():
    """`--deliverable` with no `--deliverable-validator` is a supported shape.
    'What a machine can check is checked' is then false and narrows the
    reviewer away from defects nobody checked."""
    with_v = shape.review_prompt_with_deliverable(
        shape.OPUS_REVIEW_PROMPT, "doc.md", "python3 check.py {path}"
    )
    assert "validator `python3 check.py doc.md` passed" in with_v
    assert "What a machine can check is checked" in with_v
    assert "whether the file still says what the spec asked" in with_v

    without = shape.review_prompt_with_deliverable(shape.OPUS_REVIEW_PROMPT, "doc.md", "")
    assert "What a machine can check is checked" not in without
    assert "No machine check ran on it" in without
    assert "the whole file is your question" in without
    # test_shape.py::test_deliverable_without_a_validator_says_so_in_neither_prompt
    # asserts the word 'validator' is absent before 'Report anything'.
    assert "validator" not in without.split("Report anything")[0]


# --- item 4: fix lane is not told conductor enforces the document validator --


def test_deliverable_fix_prompt_does_not_claim_conductor_runs_the_validator(
    repo, tmp_path
):
    """The fix lane's deliverable is dispositions.json with no validator key.
    runner._check_deliverable_validator runs nothing on it. The document
    validator stays on the build lane, where conductor does run it."""
    spec = _spec(tmp_path)
    raw = shape.shape_a(
        spec=spec,
        repo=repo,
        test="python3 check.py doc.md",
        caps=shape.cap_arithmetic(1, 1),
        deliverable="doc.md",
        deliverable_validator="python3 check.py {path}",
    )
    lanes = _lanes(raw)
    fix_prompt = lanes["fix"]["prompt"]
    build_prompt = lanes["build"]["prompt"]
    assert lanes["fix"]["deliverable"] == {
        "path": "dispositions.json",
        "schema": "dispositions.schema.json",
        "commit": False,
    }
    assert "validator" not in lanes["fix"]["deliverable"]
    assert "Conductor runs" not in fix_prompt
    assert "refuses a file that fails it" not in fix_prompt
    assert "python3 check.py doc.md" in fix_prompt
    assert "conductor does not run that command on this lane" in fix_prompt
    assert "Conductor runs `python3 check.py doc.md`" in build_prompt
    assert lanes["build"]["deliverable"]["validator"] == "python3 check.py {path}"


# --- item 5: adversarial lane-and-index rule is not adversarial-only --------


def test_adversarial_disposition_note_applies_whenever_that_lane_wrote_a_test():
    """The mixed case (reviews numbered items AND the adversarial lane wrote
    a test) used to be told to write an entry and never told the lane or
    index. Index 1 is because that lane writes at most one test."""
    note = shape.ADVERSARIAL_DISPOSITION_NOTE
    assert "adversarial-only finding" not in note
    assert "the reviews said NO_FINDINGS" not in note
    assert 'lane "adversarial" and index 1' in note
    assert "at most one test" in note
    mixed = shape._adversarial_wording(shape.FIX_PROMPT)
    assert note in mixed
    assert "plus the adversarial lane's test when it wrote one" in mixed
    assert mixed.count('lane "adversarial" and index 1') == 1


# --- item 6: empty array is correct when there are no items to disposition --


def test_empty_dispositions_array_is_keyed_off_items_in_every_composition():
    """A review can come back unparsed with no numbered items and not say
    NO_FINDINGS. An empty array is then the honest answer. `_three_reviewer_wording`
    and `_adversarial_wording` match exact substrings of this sentence
    ('when both reviews said NO_FINDINGS'); a rewrite that stops matching
    is worse than the defect."""
    items = "no items to disposition"
    sole = "only correct when both reviews said NO_FINDINGS"

    two_prompt = shape.fix_prompt("true")
    two_desc = shape.dispositions_schema()["properties"]["dispositions"]["description"]
    assert items in two_prompt and items in two_desc
    assert sole not in two_prompt and sole not in two_desc
    assert "not merely when both reviews said NO_FINDINGS" in two_prompt
    assert "not merely when both reviews said NO_FINDINGS" in two_desc
    assert "either reviewer numbered" in two_prompt
    assert '"lane": "review-gemini" or "review-grok"' in two_desc

    three_prompt = shape.fix_prompt_with_opus(shape.fix_prompt("true"))
    three_desc = shape.dispositions_schema(opus_review=True)["properties"]["dispositions"][
        "description"
    ]
    assert items in three_prompt and items in three_desc
    assert "not merely when every review said NO_FINDINGS" in three_prompt
    assert "not merely when every review said NO_FINDINGS" in three_desc
    assert "not merely when both reviews said NO_FINDINGS" not in three_prompt
    assert "any reviewer numbered" in three_prompt and "any reviewer numbered" in three_desc
    assert "either reviewer numbered" not in three_prompt
    assert "If every review says NO_FINDINGS" in three_prompt
    assert "If both reviews say NO_FINDINGS" not in three_prompt
    assert '{"lane": "review-gemini", "review-grok", or "review-opus", "index": ' in three_desc

    adv_prompt = shape._adversarial_wording(shape.fix_prompt("true"))
    adv_desc = shape.dispositions_schema(adversarial=True)["properties"]["dispositions"][
        "description"
    ]
    assert items in adv_prompt and items in adv_desc
    assert (
        "not merely when both reviews said NO_FINDINGS and the adversarial lane "
        "said NO_FINDINGS"
        in adv_prompt
    )
    assert (
        "not merely when both reviews said NO_FINDINGS and the adversarial lane "
        "said NO_FINDINGS"
        in adv_desc
    )
    assert "plus the adversarial lane's test when it wrote one" in adv_prompt
    assert "plus the adversarial lane's test when it wrote one" in adv_desc
    assert (
        "If both reviews say NO_FINDINGS and the adversarial lane says NO_FINDINGS"
        in adv_prompt
    )
    assert '{"lane": "review-gemini", "review-grok", or "adversarial", "index": ' in adv_desc

    both_prompt = shape._adversarial_wording(
        shape.fix_prompt_with_opus(shape.fix_prompt("true"))
    )
    both_desc = shape.dispositions_schema(opus_review=True, adversarial=True)[
        "properties"
    ]["dispositions"]["description"]
    assert items in both_prompt and items in both_desc
    assert (
        "not merely when every review said NO_FINDINGS and the adversarial lane "
        "said NO_FINDINGS"
        in both_prompt
    )
    assert (
        "not merely when every review said NO_FINDINGS and the adversarial lane "
        "said NO_FINDINGS"
        in both_desc
    )
    assert "any reviewer numbered, plus the adversarial lane's test when it wrote one" in (
        both_prompt
    )
    assert (
        '{"lane": "review-gemini", "review-grok", "review-opus", or "adversarial", "index": '
        in both_desc
    )
    assert (
        "If every review says NO_FINDINGS and the adversarial lane says NO_FINDINGS"
        in both_prompt
    )
    assert "If both reviews say NO_FINDINGS" not in both_prompt


# --- item 7: Gemini note lands before the transition phrase ------------------


def test_review_note_lands_before_the_gemini_transition_not_after_it():
    """Inserting the note by replacing REVIEW_TAIL's first sentence put it
    after 'Based on the information above:' on Gemini, so the phrase
    introduced background. The same helpers run on Grok and Opus, which
    have no transition. The anchor is </change>\\n\\n -- missing it asserts."""
    transition = "Based on the information above: "
    tail = "Report anything that could cause incorrect behavior"

    evidence_gemini = shape.review_prompt_with_evidence(shape.GEMINI_REVIEW_PROMPT)
    assert shape.EVIDENCE_REVIEW_NOTE in evidence_gemini
    assert evidence_gemini.index(shape.EVIDENCE_REVIEW_NOTE) < evidence_gemini.index(
        transition
    )
    assert transition + shape.EVIDENCE_REVIEW_NOTE not in evidence_gemini
    assert shape.EVIDENCE_REVIEW_NOTE + transition in evidence_gemini

    evidence_grok = shape.review_prompt_with_evidence(shape.GROK_READ_ONLY_PROMPT)
    assert transition not in evidence_grok
    assert shape.EVIDENCE_REVIEW_NOTE + tail in evidence_grok

    evidence_opus = shape.review_prompt_with_evidence(shape.OPUS_REVIEW_PROMPT)
    assert transition not in evidence_opus
    assert shape.EVIDENCE_REVIEW_NOTE + tail in evidence_opus

    deliverable_gemini = shape.review_prompt_with_deliverable(
        shape.GEMINI_REVIEW_PROMPT, "doc.md", "python3 check.py {path}"
    )
    assert "The build's deliverable is the file `doc.md`" in deliverable_gemini
    assert deliverable_gemini.index("The build's deliverable") < deliverable_gemini.index(
        transition
    )
    assert transition + "The build's deliverable" not in deliverable_gemini

    deliverable_opus = shape.review_prompt_with_deliverable(
        shape.OPUS_REVIEW_PROMPT, "doc.md", ""
    )
    assert transition not in deliverable_opus
    assert "the whole file is your question. " + tail in deliverable_opus

    with pytest.raises(AssertionError):
        shape.review_prompt_with_evidence(
            "Report anything that could cause incorrect behavior, but no change block"
        )
    with pytest.raises(AssertionError):
        shape.review_prompt_with_deliverable(
            "Report anything that could cause incorrect behavior, but no change block",
            "doc.md",
            "",
        )
