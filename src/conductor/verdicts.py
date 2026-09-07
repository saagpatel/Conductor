"""Structured review checklists and fail-closed verdict parsing.

Review prose is useful to a person but cannot be tallied by a mission. A
checklist makes the judgment a typed edge: conductor owns the schema, checks
the fleet's answer itself, and computes pass or fail from the individual
criteria instead of trusting the model's headline.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

_CRITERION_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}")


@dataclass(frozen=True)
class Criterion:
    id: str
    question: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _CRITERION_ID.fullmatch(self.id):
            raise ValueError("criterion id must match [a-z][a-z0-9_-]{0,39}")
        if not isinstance(self.question, str):
            raise ValueError("criterion question must be a string")


@dataclass
class Verdict:
    passed: bool
    reported: str
    criteria: list[dict]
    failed: list[str]
    summary: str
    invalid: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_checklist(raw: object) -> list[Criterion]:
    """Turn the compact mission form into checked, unique criteria."""
    if not isinstance(raw, list):
        raise ValueError(f"checklist must be a list, got {type(raw).__name__}")
    criteria: list[Criterion] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"checklist item {index} ({item!r})"
        if isinstance(item, str):
            criterion_id = item
            question = f"Is {item} satisfied?"
        elif isinstance(item, dict) and set(item) == {"id", "question"}:
            criterion_id = item["id"]
            question = item["question"]
            if not isinstance(criterion_id, str) or not isinstance(question, str):
                raise ValueError(f"{where} needs string id and question")
        else:
            raise ValueError(f"{where} must be an id string or an {{id, question}} object")
        if not _CRITERION_ID.fullmatch(criterion_id):
            raise ValueError(
                f"{where} has invalid id {criterion_id!r}; expected [a-z][a-z0-9_-]{{0,39}}"
            )
        if criterion_id in seen:
            raise ValueError(f"{where} duplicates id {criterion_id!r}")
        seen.add(criterion_id)
        criteria.append(Criterion(criterion_id, question))
    if not criteria:
        raise ValueError("checklist needs at least one criterion")
    return criteria


def checklist_schema(criteria: list[Criterion]) -> dict:
    """The fleet-facing JSON Schema for one fixed checklist."""
    ids = [criterion.id for criterion in criteria]
    # No "$schema" key: Claude Code's --json-schema validator rejects the
    # draft URI outright ("no schema with key or ref"), killing the lane
    # before a model is ever called. Verified live 2026-09-03.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "criteria", "summary"],
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "criteria": {
                "type": "array",
                "minItems": len(criteria),
                "maxItems": len(criteria),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "ok", "evidence"],
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "ok": {"type": "boolean"},
                        "evidence": {"type": "string", "minLength": 1},
                    },
                },
            },
            "summary": {"type": "string"},
        },
    }


def checklist_contract(criteria: list[Criterion]) -> str:
    """Prompt suffix that says exactly what conductor will accept."""
    numbered = "\n".join(
        f"{index}. {criterion.id}: {criterion.question}"
        for index, criterion in enumerate(criteria, 1)
    )
    schema = json.dumps(checklist_schema(criteria), separators=(",", ":"), sort_keys=True)
    return (
        "\n\n## Conductor verdict checklist\n\n"
        f"{numbered}\n\n"
        "Your final answer must be exactly one JSON object matching this schema:\n"
        f"{schema}\n"
        "Return one criteria entry per checklist item in the given order. Each evidence value "
        "must cite a file and line or a hunk from the diff, or say 'no evidence'. Set verdict "
        "to pass only when every criterion is ok."
    )


def _embedded_objects(text: str) -> list[dict]:
    """Decode complete objects without letting a stray prose brace hide later JSON."""
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    cursor = 0
    while (start := text.find("{", cursor)) >= 0:
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        # Jump past a decoded object so its nested dictionaries cannot
        # displace the top-level verdict as the last answer object.
        cursor = max(end, start + 1)
    return objects


def _answer_object(text: str) -> tuple[dict | None, str | None]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        objects = _embedded_objects(text)
        if objects:
            return objects[-1], None
        return None, "answer contains no valid JSON object"
    if not isinstance(raw, dict):
        return None, "answer JSON must be an object"
    return raw, None


def _one_line(value: object) -> str:
    return " ".join(str(value).splitlines())


def _invalid_verdict(reason: str, criteria: list[Criterion], raw: dict | None = None) -> Verdict:
    source = raw or {}
    raw_items = source.get("criteria")
    by_id: dict[str, dict] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                by_id.setdefault(item["id"], item)
    normalized: list[dict] = []
    for criterion in criteria:
        item = by_id.get(criterion.id, {})
        ok = item.get("ok") if type(item.get("ok")) is bool else False
        evidence = item.get("evidence")
        normalized.append(
            {
                "id": criterion.id,
                "ok": ok,
                "evidence": evidence if isinstance(evidence, str) and evidence else "no evidence",
            }
        )
    reported = source.get("verdict")
    summary = source.get("summary")
    return Verdict(
        passed=False,
        reported=reported if isinstance(reported, str) else "",
        criteria=normalized,
        failed=[item["id"] for item in normalized if not item["ok"]],
        summary=summary if isinstance(summary, str) else "",
        invalid=_one_line(reason),
    )


def parse_verdict(text: str, criteria: list[Criterion]) -> Verdict:
    """Parse and validate a fleet judgment without trusting its headline."""
    raw, problem = _answer_object(text)
    if problem or raw is None:
        return _invalid_verdict(problem or "answer JSON must be an object", criteria)

    expected_root = {"verdict", "criteria", "summary"}
    extra_root = sorted(set(raw) - expected_root)
    if extra_root:
        return _invalid_verdict(f"unknown top-level field {extra_root[0]!r}", criteria, raw)
    missing_root = [key for key in ("verdict", "criteria", "summary") if key not in raw]
    if missing_root:
        return _invalid_verdict(f"missing top-level field {missing_root[0]!r}", criteria, raw)
    # D9: `x in {...}` raises TypeError on an unhashable value, and a fleet
    # that answered `"verdict": []` is exactly the shape that reaches here.
    # An invalid verdict is already a recorded outcome; a crash after the
    # spend is not.
    if not isinstance(raw["verdict"], str) or raw["verdict"] not in {"pass", "fail"}:
        return _invalid_verdict("verdict must be 'pass' or 'fail'", criteria, raw)
    if not isinstance(raw["summary"], str):
        return _invalid_verdict("summary must be a string", criteria, raw)
    if not isinstance(raw["criteria"], list):
        return _invalid_verdict("criteria must be a list", criteria, raw)

    expected_ids = [criterion.id for criterion in criteria]
    seen: set[str] = set()
    items: list[dict] = []
    for index, item in enumerate(raw["criteria"]):
        if not isinstance(item, dict):
            return _invalid_verdict(f"criterion {index} must be an object", criteria, raw)
        extra = sorted(set(item) - {"id", "ok", "evidence"})
        if extra:
            return _invalid_verdict(
                f"criterion {index} has unknown field {extra[0]!r}", criteria, raw
            )
        missing = [key for key in ("id", "ok", "evidence") if key not in item]
        if missing:
            return _invalid_verdict(
                f"criterion {index} is missing field {missing[0]!r}", criteria, raw
            )
        criterion_id = item["id"]
        if not isinstance(criterion_id, str):
            return _invalid_verdict(f"criterion {index} id must be a string", criteria, raw)
        if criterion_id not in expected_ids:
            return _invalid_verdict(f"unknown criterion id {criterion_id!r}", criteria, raw)
        if criterion_id in seen:
            return _invalid_verdict(f"duplicate criterion id {criterion_id!r}", criteria, raw)
        if type(item["ok"]) is not bool:
            return _invalid_verdict(
                f"criterion {criterion_id!r} ok must be a boolean", criteria, raw
            )
        if not isinstance(item["evidence"], str) or not item["evidence"]:
            return _invalid_verdict(
                f"criterion {criterion_id!r} evidence must be a non-empty string", criteria, raw
            )
        seen.add(criterion_id)
        items.append({"id": criterion_id, "ok": item["ok"], "evidence": item["evidence"]})

    missing_ids = [criterion_id for criterion_id in expected_ids if criterion_id not in seen]
    if missing_ids:
        return _invalid_verdict(f"missing criterion id {missing_ids[0]!r}", criteria, raw)
    if len(items) != len(expected_ids):
        return _invalid_verdict(
            f"expected {len(expected_ids)} criteria, got {len(items)}", criteria, raw
        )
    # The schema cannot pin order, so a complete answer in another order is
    # the contract satisfied, not broken: normalize instead of discarding a
    # paid judgment. The reorder is noted so the receipt says what happened.
    reordered = [item["id"] for item in items] != expected_ids
    by_id = {item["id"]: item for item in items}
    items = [by_id[criterion_id] for criterion_id in expected_ids]

    passed = all(item["ok"] for item in items)
    computed = "pass" if passed else "fail"
    summary = raw["summary"]
    if reordered:
        note = "criteria arrived out of checklist order; normalized"
        summary = f"{summary} [conductor note: {note}]" if summary else note
    if raw["verdict"] != computed:
        note = f"model reported {raw['verdict']}; computed {computed} from the criteria"
        summary = f"{summary} [conductor note: {note}]" if summary else note
    return Verdict(
        passed=passed,
        reported=raw["verdict"],
        criteria=items,
        failed=[item["id"] for item in items if not item["ok"]],
        summary=summary,
    )


_FENCE_LINE = re.compile(r"^`{3,}$")
_FINDINGS_LINE = re.compile(r"FINDINGS: (0|[1-9][0-9]*)")
_DISPOSITION_PREFIX = "DISPOSITION:"
_DISPOSITION_LINE = re.compile(
    r"^DISPOSITION:\s+(?P<lane>\S+)\s+(?P<index>\d+)\s+"
    r"(?P<disposition>fixed|refused|already|wording):\s*(?P<reason>.+)$"
)
_FINDING_PREFIX = "FINDING:"
_FINDING_LINE = re.compile(
    r"^FINDING:\s+(?P<index>\d+)\s+(?P<file>.+):(?P<line>\d+)\s+confidence\s+(?P<confidence>\d+)$"
)
_DISPOSITION_KINDS = frozenset({"fixed", "refused", "already", "wording"})
_DISPOSITION_ENTRY_KEYS = frozenset({"lane", "index", "disposition", "reason"})


def _final_marker_line(answer: str) -> str:
    """The last non-empty line of a review answer, with a trailing code
    fence (an answer wrapped in ```...```) skipped so the marker under it
    is still found."""
    lines = answer.splitlines()
    index = len(lines) - 1
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped or _FENCE_LINE.fullmatch(stripped):
            index -= 1
            continue
        return stripped
    return ""


def _parse_review_items(answer: str) -> tuple[list[dict], int]:
    items: list[dict] = []
    malformed = 0
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line.startswith(_FINDING_PREFIX):
            continue
        match = _FINDING_LINE.fullmatch(line)
        if match is None:
            malformed += 1
            continue
        confidence = int(match.group("confidence"))
        if not 1 <= confidence <= 10:
            malformed += 1
            continue
        items.append(
            {
                "index": int(match.group("index")),
                "file": match.group("file"),
                "line": int(match.group("line")),
                "confidence": confidence,
            }
        )
    return items, malformed


def review_verdict(answer: str) -> dict:
    """A review lane narrates before its verdict; only the final line is
    the answer conductor tallies. `NO_FINDINGS` is exact, `FINDINGS: N`
    names a count, anything else is unparsed rather than guessed at.

    F15 mission 2 item 1: every `FINDING: <n> <file>:<line> confidence
    <1-10>` line, in order, is parsed into `items` regardless of the final
    verdict; a line that opens with the marker but does not match the
    shape (or whose confidence falls outside 1-10) is skipped and counted
    in `items_malformed`, the same convention `fix_dispositions` uses for
    `DISPOSITION:` lines."""
    line = _final_marker_line(answer)
    items, items_malformed = _parse_review_items(answer)
    if line == "NO_FINDINGS":
        return {
            "verdict": "no_findings",
            "findings": 0,
            "items": items,
            "items_malformed": items_malformed,
        }
    match = _FINDINGS_LINE.fullmatch(line)
    if match:
        return {
            "verdict": "findings",
            "findings": int(match.group(1)),
            "items": items,
            "items_malformed": items_malformed,
        }
    return {
        "verdict": "unparsed",
        "findings": None,
        "items": items,
        "items_malformed": items_malformed,
    }


def _parse_dispositions(answer: str) -> tuple[list[dict], int]:
    dispositions: list[dict] = []
    malformed = 0
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line.startswith(_DISPOSITION_PREFIX):
            continue
        match = _DISPOSITION_LINE.fullmatch(line)
        if match is None:
            malformed += 1
            continue
        dispositions.append(
            {
                "lane": match.group("lane"),
                "index": int(match.group("index")),
                "disposition": match.group("disposition"),
                "reason": match.group("reason").strip(),
            }
        )
    return dispositions, malformed


def fix_dispositions(answer: str) -> list[dict]:
    """Every `DISPOSITION: <lane> <index> <fixed|refused|already|wording>:
    <reason>` line in a fix lane's answer, in order. A line that starts
    with the marker but does not match the shape is skipped, not raised;
    `dispositions_malformed` reports how many were skipped."""
    dispositions, _ = _parse_dispositions(answer)
    return dispositions


def dispositions_malformed(answer: str) -> int:
    """The count of lines `fix_dispositions` skipped: they opened with
    `DISPOSITION:` but did not match the required shape."""
    _, malformed = _parse_dispositions(answer)
    return malformed


def valid_disposition_entry(entry: object) -> bool:
    """A well-formed disposition record, whichever channel it arrived
    through -- the fix lane's `dispositions.json` deliverable or a
    rehydrated lane receipt: exactly `lane` (non-empty string), `index`
    (int), `disposition` (one of the four kinds), `reason` (string), no
    other keys. Shared so a rehydrated receipt with a malformed entry is
    refused the same way a freshly parsed one is skipped."""
    return (
        isinstance(entry, dict)
        and set(entry) == _DISPOSITION_ENTRY_KEYS
        and isinstance(entry.get("lane"), str)
        and bool(entry["lane"])
        and type(entry.get("index")) is int
        # D9: isinstance first -- an unhashable value (a list, a dict) makes
        # the membership test itself raise, and this runs on a fleet's file.
        and isinstance(entry.get("disposition"), str)
        and entry["disposition"] in _DISPOSITION_KINDS
        and isinstance(entry.get("reason"), str)
    )


def parse_dispositions_deliverable(text: str) -> tuple[list[dict], int]:
    """F15 mission 2 item 2: the fix lane's `dispositions.json` deliverable,
    `{"dispositions": [...]}`. Every entry that fails `valid_disposition_entry`
    is counted in the second return value, not stored. A file that is not
    JSON, not an object, or carries no `dispositions` list parses to no
    entries and no malformed count -- the deliverable's own file-level
    schema check (fleets/runner) is what refuses that shape; this function
    only sorts the entries once the file shape is already right."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return [], 0
    if not isinstance(raw, dict) or not isinstance(raw.get("dispositions"), list):
        return [], 0
    dispositions: list[dict] = []
    malformed = 0
    for entry in raw["dispositions"]:
        if valid_disposition_entry(entry):
            dispositions.append(dict(entry))
        else:
            malformed += 1
    return dispositions, malformed


def _clip_line(text: object, limit: int) -> str:
    line = _one_line(text)
    return line if len(line) <= limit else line[:limit]


def render_verdict(verdict: Verdict) -> str:
    """A deterministic, bounded block safe to paste as upstream data."""
    ok_count = sum(item.get("ok") is True for item in verdict.criteria)
    if verdict.invalid:
        lines = [f"verdict: invalid: {_one_line(verdict.invalid)}"]
    else:
        state = "pass" if verdict.passed else "fail"
        lines = [f"verdict: {state} ({ok_count}/{len(verdict.criteria)} ok)"]
    for item in verdict.criteria:
        state = "ok" if item.get("ok") is True else "FAIL"
        evidence = _clip_line(item.get("evidence", ""), 300)
        lines.append(f"- {item.get('id', '')}: {state}: {evidence}")
    lines.append(f"summary: {_clip_line(verdict.summary, 1000)}")
    return "\n".join(lines)
