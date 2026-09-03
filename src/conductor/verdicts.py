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
    return criteria


def checklist_schema(criteria: list[Criterion]) -> dict:
    """The fleet-facing JSON Schema for one fixed checklist."""
    ids = [criterion.id for criterion in criteria]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
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


def _balanced_objects(text: str) -> list[str]:
    """Balanced top-level brace spans, ignoring braces inside JSON strings."""
    spans: list[str] = []
    start: int | None = None
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"' and depth:
            quoted = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append(text[start : index + 1])
                start = None
    return spans


def _answer_object(text: str) -> tuple[dict | None, str | None]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        for candidate in reversed(_balanced_objects(text)):
            try:
                raw = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                return raw, None
        return None, "answer contains no valid JSON object"
    if not isinstance(raw, dict):
        return None, "answer JSON must be an object"
    return raw, None


def _one_line(value: object) -> str:
    return " ".join(str(value).splitlines())


def _invalid_verdict(
    reason: str, criteria: list[Criterion], raw: dict | None = None
) -> Verdict:
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
    if raw["verdict"] not in {"pass", "fail"}:
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
        items.append(
            {"id": criterion_id, "ok": item["ok"], "evidence": item["evidence"]}
        )

    missing_ids = [criterion_id for criterion_id in expected_ids if criterion_id not in seen]
    if missing_ids:
        return _invalid_verdict(f"missing criterion id {missing_ids[0]!r}", criteria, raw)
    if len(items) != len(expected_ids):
        return _invalid_verdict(
            f"expected {len(expected_ids)} criteria, got {len(items)}", criteria, raw
        )
    actual_ids = [item["id"] for item in items]
    if actual_ids != expected_ids:
        return _invalid_verdict("criteria are not in checklist order", criteria, raw)

    passed = all(item["ok"] for item in items)
    computed = "pass" if passed else "fail"
    summary = raw["summary"]
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
