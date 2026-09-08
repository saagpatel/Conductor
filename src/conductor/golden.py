"""Golden missions: record a finished mission as an offline fixture, then
replay it through the parser, the scheduler, and the templating without
spending on a live vendor.

`record` copies a mission directory and every run it dispatched into a
self-contained, scrubbed, deterministic fixture. `replay` loads that
fixture's mission snapshot and runs it again through `mission.run_mission`,
but with a `dispatcher` that returns the recorded receipt for each attempt
instead of spawning a fleet -- so a change to routing, a template, or
`outputs.parse` is testable against real recorded transcripts without a
network call. `check` compares a fresh replay's `projection` against the
fixture's own `expected.json`, pinning what a routing or template change is
allowed to move.

Evidence (`docs/archive/roadmaps-closed.md` item C7): "Recorded transcripts of past
real missions replayed through the parser, scheduler, and templating
offline, so a routing or template change is testable without spending on
live vendors."
"""

from __future__ import annotations

import base64
import dataclasses as _dc
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from . import fleets as fleets_mod
from . import outputs as outputs_mod
from . import prompts as prompts_mod
from .mission import Attempt, Mission, MissionResult, run_mission
from .runner import Result

FORMAT = "conductor/golden/v1"
DEFAULT_MAX_BYTES = 3_000_000

# Every file a run directory may contribute to a fixture, in the order they
# are considered; each only when it exists. argv.json, stderr.log, and
# liveness.json are never copied -- argv is reconstructible from the spec,
# stderr is empty on every real run so far, and liveness is a heartbeat with
# nothing to replay. attestation.json is never copied either: its DSSE
# payload is base64 over the real run's paths (unscrubbable without breaking
# the signature) and the signature itself cannot be verified without the
# operator's key, so a copy would be both unscrubbed and unverifiable, and
# nothing in replay reads it beyond hashing it into a throwaway chain.
RUN_FILES = (
    "result.json",
    "stdout.log",
    "prompt.txt",
    "answer.txt",
    "diff.patch",
    # E10: a plan lane's declared deliverable, copied here suffix-less by
    # `runner.dispatch` before `mission._plan_check_child` ever runs -- a
    # recorded plan lane's `load_mission` call has nothing to read back
    # without this, and the fixture can never replay up to its pause.
    "deliverable",
)
MISSION_FILES = ("mission.json", "result.json", "report.md")

# stdout.log is never the fixture's own filename: the operator's global git
# excludes drop every `*.log` path from `git add` silently, so a fixture
# using that name looks committed (the working tree still has it) but never
# actually lands in the repo. The fixture stores it as stdout.jsonl -- an
# accurate name, since the content is NDJSON -- and replay restores it to
# stdout.log when it recreates a run directory, matching what a live run
# actually writes.
_FIXTURE_STDOUT_NAME = "stdout.jsonl"

_ELIDE_LIMIT = 512
_SECRET_KEY_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD")
_ENV_SECRET_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:" + "|".join(_SECRET_KEY_WORDS) + r")[A-Za-z0-9_]*)=(\S+)",
    re.IGNORECASE,
)
# `<redacted>` is excluded so the guard does not fire on the scrubber's own
# output: `Bearer <redacted>` still matches `\S+`, so every already-clean
# bundle carrying an Authorization header was reported as leaking a bearer
# token, and `export` deleted the bundle and raised rather than shipping it
# (2026-09-08 review). Every other rule here already exempts its own marker.
_BEARER_RE = re.compile(r"Bearer\s+(?!<redacted>)\S+")
_TOKEN_PREFIX_RE = re.compile(
    # 2026-09-08 review: `github_pat_`, `gho_`, `glpat-`, `hf_`, `AKIA`, and
    # Anthropic's `sk-ant-` all shipped verbatim through an export bundle
    # that `scrub_guard` then declared clean. Same review, second pass:
    # Stripe's `sk_live_`/`sk_test_` use an underscore where the `sk-` rule
    # wanted a hyphen, Slack's `xox[abps]-` and npm's `npm_` had no rule at
    # all, and only AWS's permanent `AKIA` was listed, never the `ASIA` a
    # temporary STS credential carries.
    r"(?:sk-ant-|sk-|sk_live_|sk_test_|rk_live_|rk_test_|xai-|xox[abprs]-"
    r"|ghp_|gho_|ghu_|ghs_|github_pat_|glpat-|hf_|npm_|AKIA|ASIA|AIza)"
    r"[A-Za-z0-9_-]{16,}"
)
# NOT covered, deliberately, and recorded here so it stays a known gap: a
# secret written as a mapping (`"token": "..."` in JSON, `api_token: ...` in
# YAML) inside a plain-text artifact. A rule for that shape was written and
# withdrawn on 2026-09-08: a recorded transcript is full of ordinary source
# where `key:` and `secret:` are a dict literal or a type annotation, so the
# rule redacted golden fixtures and its guard side failed four committed
# ones. Bundle `.json` files are already covered by `_scrub_json_value`,
# which redacts by field name (see `_json_key_is_secret`); the gap is
# `answer.txt`, `prompt.txt`, `diff.patch`, and the logs under `--logs`.
# Closing it needs a value-shape test (entropy, or a vendor prefix) rather
# than a name test.
# `--api-key VALUE`, `--token=VALUE`: the shape a command line uses.
_FLAG_SECRET_RE = re.compile(
    r"(--[A-Za-z0-9-]*(?:" + "|".join(_SECRET_KEY_WORDS) + r")[A-Za-z0-9-]*)([=\s]+)(\S{8,})",
    re.IGNORECASE,
)
# Credentials in a URL's userinfo: `https://user:password@host`.
_URL_CRED_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
# A pasted private key. The body is redacted whole rather than line by line.
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_NONCE_RE = re.compile(r"\[[0-9a-f]{6}\]")
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/=]{64,}")
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "total_tokens",
)


class GoldenError(ValueError):
    """A fixture cannot be recorded or replayed as asked."""


# --- scrubbing ---------------------------------------------------------




def _redact_secrets(text: str) -> str:
    text = _PRIVATE_KEY_RE.sub("<redacted private key>", text)
    text = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    text = _FLAG_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _URL_CRED_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:<redacted>@", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _TOKEN_PREFIX_RE.sub("<redacted>", text)
    return text


def _placeholder_map(
    *, home: Path, cwd: str | None, extra: list[tuple[str, str]] | None = None
) -> list[tuple[str, str]]:
    """(real value, placeholder) pairs, longest real value first, so a home
    nested inside the user's own home is replaced before the shorter path
    that contains it.

    E19: `extra` is every repository beyond the mission's own `cwd` a
    cross-repo mission's lanes named (E26), already paired with its own
    `<cwd2>`, `<cwd3>`, ... placeholder by the caller -- `record`'s own
    first-appearance walk, or `replay`'s `cwds` argument."""
    pairs: list[tuple[str, str]] = [(str(home), "<home>"), (str(Path.home()), "<user>")]
    if cwd:
        pairs.append((str(cwd), "<cwd>"))
    if extra:
        pairs.extend(extra)
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return [pair for pair in pairs if pair[0]]


_CWD_PLACEHOLDER_RE = re.compile(r"<cwd(\d+)>")


def _extra_cwds(mission_raw: dict, cwd: str | None) -> list[str]:
    """E19: every distinct lane- or attempt-level `cwd` (E26) in
    `mission_raw` other than the mission's own, in first-appearance order --
    what `record` gives a `<cwd2>`, `<cwd3>`, ... placeholder each."""
    seen: list[str] = []
    for lane in mission_raw.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        for attempt in lane.get("attempts") or []:
            if not isinstance(attempt, dict):
                continue
            value = attempt.get("cwd")
            if isinstance(value, str) and value and value != cwd and value not in seen:
                seen.append(value)
    return seen


def scrub_text(text: str, replacements: list[tuple[str, str]]) -> str:
    """Placeholders, then secrets. Idempotent: scrubbing an already-scrubbed
    string changes nothing, since the placeholders and `<redacted>` never
    match a replacement's own pattern."""
    for old, new in replacements:
        text = text.replace(old, new)
    return _redact_secrets(text)


# Split `api_key`, `apiKey`, `API_KEY` into components so a JSON field name
# can be judged as a whole identifier, not a substring. `keyboard` is one
# word; `apiKey` is `api` + `Key`.
_FIELD_NAME_COMPONENT_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+"
)
# TOKEN / SECRET / PASSWORD as a field-name component are secret-shaped.
# KEY is too generic as a whole name -- `prices.py` puts the matched price
# table entry under `"key"`, and a verdict checklist criterion uses it as an
# id -- so it only counts as a component of a longer name (`api_key`,
# `apiKey`, `private_key`). Substring matches (`keyboard`, `monkey`,
# `keywords`) are left alone.
_SECRET_FIELD_WORDS = frozenset({"token", "secret", "password"})


def _json_key_is_secret(key: str) -> bool:
    """True when a JSON *field name* is a secret identifier.

    A key-name rule is a heuristic either way; this is the narrowest one
    that still redacts `api_key` / `token` / `password` and leaves a field
    literally named `key` (and `keyboard` / `monkey` / `keywords`) alone.

    Inverse gap, left open: a dict re-keys its children, so
    `{"token": {"value": "hunter2"}}` is not redacted while
    `{"token": "hunter2"}` is. Closing it by inheriting the parent name
    would re-widen this rule: every nested field under `usage.tokens`
    would redact. A mapping whose own keys are not secret-shaped is
    judged on those keys, not the parent's.
    """
    components: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", key):
        if chunk:
            components.extend(_FIELD_NAME_COMPONENT_RE.findall(chunk) or [chunk])
    lowered = [c.lower() for c in components]
    if any(c in _SECRET_FIELD_WORDS for c in lowered):
        return True
    return "key" in lowered and len(lowered) > 1


def _scrub_json_value(
    value: object, replacements: list[tuple[str, str]], *, key: str | None = None
):
    if isinstance(value, str):
        if key is not None and _json_key_is_secret(key):
            return "<redacted>"
        return scrub_text(value, replacements)
    if isinstance(value, dict):
        # F8: keys too -- a cross-repo mission's `overlap.files` is keyed by
        # `<repository>:<path>` (E19), and a key is as much a path as a value.
        # Children are judged on their own field names, not the parent's:
        # inheriting a secret-shaped parent into a nested object is the
        # inverse of `_json_key_is_secret`'s narrowing, and is left open.
        return {
            scrub_text(k, replacements): _scrub_json_value(v, replacements, key=k)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_json_value(v, replacements, key=key) for v in value]
    return value


def scrub_json_text(text: str, replacements: list[tuple[str, str]]) -> str:
    obj = json.loads(text)
    return json.dumps(_scrub_json_value(obj, replacements), indent=2)


def _pattern_hits(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    """Which of the scrub's patterns -- a real path or a secret shape --
    appear anywhere in `text`, by name."""
    hits: list[str] = []
    for needle, name in patterns:
        if needle and needle in text:
            hits.append(name)
    # A value the scrubber already replaced is not a leak: `_redact_secrets`
    # rewrites `NAME_TOKEN=value` to `NAME_TOKEN=<redacted>`, and the same
    # regex would otherwise match the redaction itself, refusing every
    # bundle that held an env-secret shape after it was scrubbed (the first
    # live export of a diff carrying `cache_write_tokens=...` kwargs). Inside
    # a JSON string the run continues as an escaped `\n`, so the exemption
    # is on the prefix, which no real value can start with.
    if any(not m.group(2).startswith("<redacted>") for m in _ENV_SECRET_RE.finditer(text)):
        hits.append("env secret")
    if _BEARER_RE.search(text):
        hits.append("bearer token")
    if _TOKEN_PREFIX_RE.search(text):
        hits.append("prefixed token")
    # Every shape `_redact_secrets` rewrites needs its guard side too, or a
    # bundle still passes with the secret in it. Same `<redacted>` exemption
    # as the env case above: the scrubber's own output must not be a hit.
    if any(not m.group(3).startswith("<redacted") for m in _FLAG_SECRET_RE.finditer(text)):
        hits.append("secret flag")
    if any(not m.group(3).startswith("<redacted") for m in _URL_CRED_RE.finditer(text)):
        hits.append("url credentials")
    if _PRIVATE_KEY_RE.search(text):
        hits.append("private key")
    return hits


def _decode_base64_runs(line: str) -> list[str]:
    """Every run of 64+ base64 alphabet characters on the line, decoded as
    text where that succeeds. A DSSE envelope (attestation.json's shape)
    carries its statement this way, unreadable to a plain-text scan."""
    decoded: list[str] = []
    for candidate in _BASE64_RUN_RE.findall(line):
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            decoded.append(base64.b64decode(padded, validate=False).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
    return decoded


_MASKABLE_B64_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")


def _mask_base64_runs(line: str) -> str:
    """The line with every base64-shaped run of 40+ characters replaced by
    `<b64>`, for the plain-text scan only. A DSSE signature is 44 characters
    and a payload far longer; either can spell "key" before its "=" padding
    and read as an env secret to a plain-text scan, which is not a leak. A
    run is masked only when its "=" signs are trailing padding (at most
    two, at the end): `KEY=<40 alphanumerics>` has its "=" in the middle
    and is left alone, so a real secret assignment is still caught. The
    decoded scan in `scrub_guard` still sees everything inside a run."""

    def mask(match: re.Match[str]) -> str:
        run = match.group(0)
        stripped = run.rstrip("=")
        if "=" in stripped or len(run) - len(stripped) > 2:
            return run
        return "<b64>"

    return _MASKABLE_B64_RE.sub(mask, line)


def scrub_guard(
    path: str | Path,
    *,
    extra: list[tuple[str, str]] | None = None,
    home: str | Path | None = None,
) -> list[str]:
    """Every occurrence in a fixture directory of the user's home path, the
    conductor home, any of `extra`'s (path, label) pairs, or any of the
    scrub's secret patterns, as `file:line: <pattern name>`, including one
    hiding inside a base64-encoded run (`file:line: <pattern name>
    (base64)`); empty when clean.

    E19: `extra` lets a caller that knows a mission's own repository paths
    (a cross-repo mission's, say) check for them too -- `scrub_guard` itself
    has no way to recover a real path from an already-scrubbed fixture, so it
    cannot find one it is not told about.

    `home` is the conductor home the caller actually used (export's `home`
    argument, golden.record's `home`). When omitted, the environment default
    (`conductor_home()`) is the needle, which is right for callers that
    pass nothing and wrong for `export(home=other, ...)` -- the leak
    backstop has to check the tree the bundle was built from, not a
    different tree the environment happens to name."""
    from .paths import conductor_home

    path = Path(path)
    conductor = Path(home) if home is not None else conductor_home()
    patterns: list[tuple[str, str]] = [
        (str(Path.home()), "user home"),
        (str(conductor), "conductor home"),
        *(extra or []),
    ]
    findings: list[str] = []
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        try:
            text = file.read_text(errors="replace")
        except OSError:
            continue
        rel = file.relative_to(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            # E13: a base64 run is scanned decoded, never raw -- the raw
            # alphabet can spell "key" before its "=" padding and read as an
            # env secret to a plain-text scan, which is not a leak, only
            # base64. The decoded scan below still sees everything inside it.
            # Literal path needles must see the raw line: long directory
            # names also match the base64 alphabet. Mask only the heuristic
            # secret scan, which can mistake signature padding for KEY=.
            literal_hits = [name for needle, name in patterns if needle and needle in line]
            for name in literal_hits + _pattern_hits(_mask_base64_runs(line), []):
                findings.append(f"{rel}:{line_no}: {name}")
            for decoded in _decode_base64_runs(line):
                for name in _pattern_hits(decoded, patterns):
                    findings.append(f"{rel}:{line_no}: {name} (base64)")
    return findings


# --- elision -------------------------------------------------------------


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _elide_string(value: str) -> str:
    if len(value) <= _ELIDE_LIMIT:
        return value
    return f"<elided {len(value)} chars sha256={_sha12(value)}>"


def _elide_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return line
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return line
    if not isinstance(obj, dict):
        return line
    is_result_event = obj.get("type") == "result" or obj.get("event") == "result"
    is_assistant_event = obj.get("type") == "assistant"

    def protected(key: str | None) -> bool:
        if key == "plan":
            return True
        if is_result_event and key in ("result", "response", "error"):
            return True
        return bool(is_assistant_event and key == "text")

    def walk(value: object, key: str | None = None):
        if isinstance(value, str):
            return value if protected(key) else _elide_string(value)
        if is_result_event and key == "structured_output":
            # F8: a `--json-schema` answer is the whole object, kept as one --
            # `outputs.parse` re-serializes it as the run's answer, so any
            # string inside it (a judge's `reason`, say) elided here would
            # change the answer and fail `record`'s own elision check.
            return value
        if isinstance(value, dict):
            return {k: walk(v, key=k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key=key) for v in value]
        return value

    return json.dumps(walk(obj))


def elide_stream(text: str) -> str:
    return "\n".join(_elide_line(line) for line in text.splitlines())


def _output_fields(output: outputs_mod.FleetOutput) -> tuple:
    return (
        output.answer,
        output.usage.to_dict() if output.usage else None,
        output.status,
        output.error,
        output.session_id,
    )


def _verify_elision(fleet: str, original: str, elided: str, run_id: str) -> None:
    before = outputs_mod.parse(fleet, original)
    after = outputs_mod.parse(fleet, elided)
    names = ("answer", "usage", "status", "error", "session_id")
    pairs = zip(names, _output_fields(before), _output_fields(after), strict=True)
    for name, was, now in pairs:
        if was != now:
            raise GoldenError(f"run {run_id}: elision changed {name}")


# --- recording -------------------------------------------------------------


_ORDER_LABELS = ("forward", "reverse")


def _aux_recordings(result_raw: dict | None) -> list[tuple[str, str, str]]:
    """F8: the (label, run_id, fleet) rows a mission result names outside
    its lanes -- the collate (`collate`), each judge order of a rank sitting
    (`collate:<judge index>:<forward|reverse>`, judge 0 being the collate's
    own fleet), and the resolver (`resolve`) -- in the order
    `mission._dispatch_aux` labels them, so `record` copies those runs and
    `replay` answers them from the fixture instead of a live vendor."""
    rows: list[tuple[str, str, str]] = []
    if not isinstance(result_raw, dict):
        return rows
    collate = result_raw.get("collate")
    if isinstance(collate, dict):
        if collate.get("rank"):
            sittings = [(0, collate)] + [
                (i + 1, judge)
                for i, judge in enumerate(collate.get("judges") or [])
                if isinstance(judge, dict)
            ]
            for judge_index, judge in sittings:
                for k, order in enumerate(judge.get("orders") or []):
                    run_id = order.get("run_id") if isinstance(order, dict) else None
                    if isinstance(run_id, str) and k < len(_ORDER_LABELS):
                        rows.append(
                            (
                                f"collate:{judge_index}:{_ORDER_LABELS[k]}",
                                run_id,
                                str(judge.get("fleet") or ""),
                            )
                        )
        elif isinstance(collate.get("run_id"), str):
            rows.append(("collate", collate["run_id"], str(collate.get("fleet") or "")))
    resolve = result_raw.get("resolve")
    if isinstance(resolve, dict) and isinstance(resolve.get("run_id"), str):
        rows.append(("resolve", resolve["run_id"], str(resolve.get("fleet") or "")))
    return rows


def _run_ids_and_fleets(
    lane_files: list[Path], result_raw: dict | None = None
) -> tuple[list[str], dict[str, str], set[str]]:
    """Every run id named by any attempt row, in first-seen order, its
    fleet, and the set of fleets seen (for the manifest). F8: plus the
    collate, judge, and resolve runs the mission result names."""
    run_ids: list[str] = []
    fleet_by_run: dict[str, str] = {}
    fleets: set[str] = set()
    for _label, run_id, fleet in _aux_recordings(result_raw):
        if run_id not in fleet_by_run:
            run_ids.append(run_id)
        if fleet:
            fleet_by_run[run_id] = fleet
            fleets.add(fleet)
    for lane_file in lane_files:
        data = json.loads(lane_file.read_text())
        for key in ("previous_attempts", "attempts"):
            for attempt in data.get(key) or []:
                run_id = attempt.get("run_id")
                fleet = attempt.get("fleet")
                if not isinstance(run_id, str):
                    continue
                if run_id not in fleet_by_run:
                    run_ids.append(run_id)
                if isinstance(fleet, str):
                    fleet_by_run[run_id] = fleet
                    fleets.add(fleet)
    return run_ids, fleet_by_run, fleets


def _mission_fleet_versions(
    home: Path, run_ids: list[str], fleet_by_run: dict[str, str], fleets: set[str]
) -> dict[str, str | None]:
    """E22: one version per fleet the mission used, taken from the first run
    receipt that names it and carries a `fleet_version`; null for a fleet
    whose every receipt predates that field (or whose result.json is
    unreadable)."""
    versions: dict[str, str | None] = dict.fromkeys(sorted(fleets))
    for run_id in run_ids:
        fleet = fleet_by_run.get(run_id)
        if fleet is None or versions.get(fleet) is not None:
            continue
        try:
            raw_result = json.loads((home / "runs" / run_id / "result.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        versions[fleet] = raw_result.get("fleet_version")
    return versions


def _field_defaults(cls: type) -> dict:
    return {f.name: f.default for f in _dc.fields(cls) if f.default is not _dc.MISSING}


_MISSION_FIELD_DEFAULTS = _field_defaults(Mission)
_ATTEMPT_FIELD_DEFAULTS = _field_defaults(Attempt)


def _backfill_snapshot(mission_raw: dict) -> dict:
    """Fill any field the current snapshot schema expects but an older
    recording predates (`retry` and `on`, added by C5, are not on any
    mission run before it; `taint`/`tainted`/`taint_from`, added by D2,
    likewise) with that field's own dataclass default -- the value the
    mission actually ran with, before the field existed to set otherwise.
    `Mission.from_snapshot` requires an exact key match, so a recording from
    an earlier version of conductor could not be replayed at all without
    this."""
    for key, default in _MISSION_FIELD_DEFAULTS.items():
        mission_raw.setdefault(key, default)
    for lane in mission_raw.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        # D2: no recording before this field existed ever ran a tainted
        # lane, so `False`/`[]` is not a guess -- it is what actually ran.
        lane.setdefault("taint", False)
        lane.setdefault("tainted", False)
        lane.setdefault("taint_from", [])
        # E7: no recording before this field existed ever ran a human lane.
        lane.setdefault("human", False)
        # E6: no recording before this field existed ever ran a script lane.
        lane.setdefault("script", False)
        # E3: no recording before this field existed ever ran an
        # untrusted-output lane.
        lane.setdefault("untrusted_output", False)
        # E10: no recording before this field existed ever ran a plan lane.
        lane.setdefault("plan", False)
        for attempt in lane.get("attempts") or []:
            if isinstance(attempt, dict):
                for key, default in _ATTEMPT_FIELD_DEFAULTS.items():
                    attempt.setdefault(key, default)
    collate = mission_raw.get("collate")
    if isinstance(collate, dict):
        # E4: no recording before judge sittings existed ever ran one, so an
        # empty list is what actually ran, not a guess.
        collate.setdefault("judges", [])
    return mission_raw


def _copy_json(src: Path, dst: Path, replacements: list[tuple[str, str]]) -> None:
    dst.write_text(scrub_json_text(src.read_text(), replacements))


def _copy_text(src: Path, dst: Path, replacements: list[tuple[str, str]]) -> None:
    dst.write_text(scrub_text(src.read_text(errors="replace"), replacements))


def record(
    mission_dir: str | Path,
    out_dir: str | Path,
    *,
    home: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Copy a finished mission and every run it dispatched into `out_dir` as
    a self-contained, scrubbed, deterministic fixture. Refuses (raising
    `GoldenError`) when the fixture would exceed `max_bytes`, or when
    replaying it back offline disagrees with what was actually recorded."""
    mission_dir = Path(mission_dir)
    out_dir = Path(out_dir)
    home = Path(home)
    if out_dir.exists():
        raise GoldenError(f"{out_dir} already exists")
    snapshot_path = mission_dir / "mission.json"
    if not snapshot_path.is_file():
        raise GoldenError(f"{mission_dir}: no mission.json")
    mission_raw = json.loads(snapshot_path.read_text())
    cwd = mission_raw.get("cwd")
    # E19: a cross-repo mission's other repositories (E26 lane/attempt `cwd`)
    # each get their own `<cwd2>`, `<cwd3>`, ... placeholder, in the order
    # they first appear in the mission's lanes, so the fixture scrubs clean
    # even when a lane's repository is not the mission's own.
    extra_pairs = [
        (path, f"<cwd{2 + i}>") for i, path in enumerate(_extra_cwds(mission_raw, cwd))
    ]
    replacements = _placeholder_map(home=home, cwd=cwd, extra=extra_pairs)

    lanes_dir = mission_dir / "lanes"
    lane_files = sorted(lanes_dir.glob("*.json")) if lanes_dir.is_dir() else []
    result_path = mission_dir / "result.json"
    result_raw = json.loads(result_path.read_text()) if result_path.is_file() else None
    run_ids, fleet_by_run, fleets = _run_ids_and_fleets(lane_files, result_raw)

    with tempfile.TemporaryDirectory(prefix="conductor-golden-") as tmp:
        work = Path(tmp) / out_dir.name
        work.mkdir()

        scrubbed_source = False
        for name in MISSION_FILES:
            src = mission_dir / name
            if not src.is_file():
                continue
            if name == "mission.json":
                snapshot = _backfill_snapshot(dict(mission_raw))
                # F9: `source` is the path of the mission file the operator
                # launched from -- the placeholder walk above only reaches as
                # far as `<home>`/`<cwd>`/`<user>` match, leaving the rest of
                # the path (a jobs id, a scratchpad dir) in the fixture. It
                # carries nothing replay needs back (`from_snapshot` only
                # requires a string), so it is scrubbed whole instead.
                source_value = snapshot.get("source")
                scrubbed_source = isinstance(source_value, str) and source_value != ""
                if scrubbed_source:
                    snapshot = {**snapshot, "source": "<source>"}
                backfilled = json.dumps(snapshot, indent=2)
                (work / name).write_text(scrub_json_text(backfilled, replacements))
            elif name.endswith(".json"):
                _copy_json(src, work / name, replacements)
            else:
                _copy_text(src, work / name, replacements)
        pause_src = mission_dir / "pause.json"
        if pause_src.is_file():
            _copy_json(pause_src, work / "pause.json", replacements)

        work_lanes = work / "lanes"
        work_lanes.mkdir()
        for lane_file in lane_files:
            _copy_json(lane_file, work_lanes / lane_file.name, replacements)

        # E7: a human lane has no run to record -- its answer file is the
        # whole recording, so replay can stand it in for the operator.
        human_lane_names = [
            lane["name"]
            for lane in mission_raw.get("lanes") or []
            if isinstance(lane, dict) and lane.get("human")
        ]
        if human_lane_names:
            work_answers = work / "answers"
            work_answers.mkdir(exist_ok=True)
            for name in human_lane_names:
                answer_src = mission_dir / "answers" / f"{name}.txt"
                if answer_src.is_file():
                    _copy_text(answer_src, work_answers / f"{name}.txt", replacements)
                # A declared deliverable lives in the lane's cwd, not under
                # the mission directory -- `_fresh_replay`'s checkout starts
                # empty, so without a copy here the fixture would freeze the
                # lane as "deliverable missing" even though it was answered
                # with the file present.
                lane_receipt_src = mission_dir / "lanes" / f"{name}.json"
                if lane_receipt_src.is_file():
                    lane_receipt = json.loads(lane_receipt_src.read_text())
                    deliverable_src = lane_receipt.get("deliverable_path")
                    if isinstance(deliverable_src, str) and Path(deliverable_src).is_file():
                        _copy_text(
                            Path(deliverable_src),
                            work_answers / f"{name}.deliverable",
                            replacements,
                        )

        work_runs = work / "runs"
        for run_id in run_ids:
            run_src = home / "runs" / run_id
            run_dst = work_runs / run_id
            run_dst.mkdir(parents=True)
            fleet = fleet_by_run.get(run_id, "")
            for name in RUN_FILES:
                src = run_src / name
                if not src.is_file():
                    continue
                if name == "stdout.log":
                    original = src.read_text(errors="replace")
                    elided = elide_stream(original)
                    _verify_elision(fleet, original, elided, run_id)
                    (run_dst / _FIXTURE_STDOUT_NAME).write_text(scrub_text(elided, replacements))
                elif name.endswith(".json"):
                    _copy_json(src, run_dst / name, replacements)
                else:
                    _copy_text(src, run_dst / name, replacements)

        # F8: the guard is part of `record`, not a separate step an operator
        # remembers to run -- the first cross-repo fixture carried a real
        # repository path in a JSON key that only the guard would have seen.
        leaks = scrub_guard(work, extra=extra_pairs, home=home)
        if leaks:
            raise GoldenError(
                f"{mission_dir.name}: fixture would leak: " + "; ".join(leaks[:8])
            )

        replayed = _fresh_replay(work)
        if replayed.differences:
            raise GoldenError(
                f"{mission_dir.name}: replay disagrees with the recording: "
                + "; ".join(replayed.differences)
            )
        (work / "expected.json").write_text(
            json.dumps(replayed.projection, indent=2, sort_keys=True)
        )

        files: dict[str, str] = {}
        for file in sorted(p for p in work.rglob("*") if p.is_file()):
            files[str(file.relative_to(work))] = hashlib.sha256(file.read_bytes()).hexdigest()
        total = sum(file.stat().st_size for file in work.rglob("*") if file.is_file())
        if total > max_bytes:
            largest = max(
                (p for p in work.rglob("*") if p.is_file()), key=lambda p: p.stat().st_size
            )
            raise GoldenError(
                f"fixture would be {total} bytes, over the {max_bytes} limit; "
                f"largest file: {largest.relative_to(work)} ({largest.stat().st_size} bytes)"
            )

        manifest = {
            "format": FORMAT,
            "mission_id": mission_dir.name,
            "name": out_dir.name,
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "conductor_version": __version__,
            "fleets": sorted(fleets),
            "fleet_versions": _mission_fleet_versions(home, run_ids, fleet_by_run, fleets),
            # E17: the whole conductor-authored prompt catalog as it stood at
            # record time, so `version_drift` can later say which of them
            # moved; null-map fixtures (recorded before this field existed)
            # read as unknown, never as a failure.
            "prompt_versions": prompts_mod.prompt_versions(),
            "placeholders": [
                "<home>",
                "<cwd>",
                "<user>",
                *(["<source>"] if scrubbed_source else []),
                *(p for _, p in extra_pairs),
            ],
            "files": files,
        }
        (work / "golden.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), str(out_dir))

    return out_dir


# --- replay ----------------------------------------------------------------


@dataclass
class Replay:
    differences: list[str] = field(default_factory=list)
    projection: dict = field(default_factory=dict)
    result: MissionResult | None = None
    # D18: what the replay could not compare (a contract field no recording
    # in this fixture carries). Never a difference: an old recording that
    # predates a field is not evidence that the field regressed.
    notes: list[str] = field(default_factory=list)


def _strip_nonce(text: str) -> str:
    return _NONCE_RE.sub("[nonce]", text)


def _token_fields(usage: dict | None) -> dict | None:
    if usage is None:
        return None
    return {key: usage.get(key, 0) for key in _TOKEN_FIELDS}


def _compare(
    differences: list[str], run_id: str, name: str, recorded, replayed, *, kind: str = "parser"
) -> None:
    if recorded != replayed:
        differences.append(f"run {run_id}: {kind} {name}: recorded {recorded}, replayed {replayed}")


# D18: the dispatch contract a replay compares. Everything here is decided
# before a fleet is spawned -- it is what mission.py asked the runner for,
# not what the vendor did with it -- so a regression in how a lane becomes a
# Spec (a default model, a tightened cap, a flipped mode, a dropped taint)
# shows up as a fixture difference instead of staying golden-green.
_CONTRACT_KEYS = (
    "fleet",
    "model",
    "effort",
    "mode",
    "timeout",
    "cap_usd",
    "taint",
    "taint_shell",
    "restricted",
    "schema",
)


def _requested_contract(spec) -> dict[str, object]:
    """The contract the currently loaded code asked for, normalized the way
    a receipt records it: `model` is the resolved model id for the effort
    (what `runner.dispatch` writes), `restricted` is the `--restricted` flag
    `fleets._build_claude` would actually put on the argv, and `taint_shell`
    is the policy name the receipt carries rather than the spec's spelling."""
    try:
        model = fleets_mod.FLEETS[spec.fleet].model(spec.model).id_for(spec.effort)
    except (KeyError, fleets_mod.DispatchRefused):
        # A spec this build would refuse outright is itself the difference;
        # compare the raw name rather than raising inside the dispatcher.
        model = spec.model or ""
    contract: dict[str, object] = {
        "fleet": spec.fleet,
        "model": model,
        "effort": spec.effort,
        "mode": spec.mode,
        "timeout": spec.resolved_timeout(),
        "cap_usd": spec.cap_usd,
        "taint": bool(spec.taint),
        # fleets.py `_build_claude`: `--restricted` is a claude-only flag,
        # set for a read lane that either declares `restricted` or declares
        # a deliverable.
        "restricted": spec.fleet == "claude"
        and spec.mode == "read"
        and bool(spec.restricted or spec.deliverable is not None),
        # A verdict checklist generates its own schema, so either one is a
        # structured-output request.
        "schema": bool(spec.schema or spec.verdict),
    }
    if spec.taint:
        contract["taint_shell"] = "prefix" if spec.taint_shell == "allow" else "denied"
    return contract


def _recorded_contract(recorded_result: dict) -> dict[str, object]:
    """What a recorded receipt actually holds of the dispatch contract. A
    key this returns is comparable; a key it omits was never recorded (an
    older fixture predating the field, or a field no receipt carries) and is
    reported as a note instead of failing the check."""
    out: dict[str, object] = {}
    for key in ("fleet", "model", "effort", "mode", "timeout"):
        if key in recorded_result:
            out[key] = recorded_result[key]
    if "budget" in recorded_result:
        # `runner.dispatch` writes a budget block exactly when a cap was
        # asked for (or the fleet is `script`, which is priced at zero), so a
        # recorded `null` budget is a recorded "no cap", not a missing field.
        budget = recorded_result["budget"]
        if budget is None:
            out["cap_usd"] = None
        elif isinstance(budget, dict) and "cap_usd" in budget:
            out["cap_usd"] = budget["cap_usd"]
    if "taint" in recorded_result:
        taint = recorded_result["taint"]
        out["taint"] = bool(isinstance(taint, dict) and taint.get("declared"))
        if isinstance(taint, dict) and taint.get("taint_shell") is not None:
            out["taint_shell"] = taint["taint_shell"]
    if recorded_result.get("restricted") is not None and (
        recorded_result.get("fleet") != "claude" or recorded_result.get("permission_mode")
    ):
        # `runner.dispatch` reads both `restricted` and `permission_mode`
        # straight off the claude argv, and a real claude dispatch always
        # carries `--permission-mode`. A claude receipt without one was not
        # dispatched through the real argv builder (a test's faked fleet), so
        # its `restricted: false` is not evidence about the flag.
        out["restricted"] = bool(recorded_result["restricted"])
    if "structured" in recorded_result:
        # Added 2026-09-08; a fixture recorded before it stays uncomparable
        # on this key, which is the documented behaviour for an older
        # receipt rather than a difference.
        out["schema"] = bool(recorded_result["structured"])
    return out


def _compare_contract(
    differences: list[str],
    uncomparable: set[str],
    run_id: str,
    spec,
    recorded_result: dict,
) -> None:
    """D18: compare the requested dispatch contract against the recording's
    own, one `_compare` line per field that moved. Field names absent from
    the recording are collected in `uncomparable` for the caller to note."""
    requested = _requested_contract(spec)
    recorded = _recorded_contract(recorded_result)
    for key in _CONTRACT_KEYS:
        if key not in requested:
            continue
        if key not in recorded:
            uncomparable.add(key)
            continue
        _compare(differences, run_id, key, recorded[key], requested[key], kind="contract")


def _jail_run_id(base_dir: Path, run_id: str) -> Path:
    """Ensure run_id is a valid run directory name that does not escape base_dir."""
    if (
        not isinstance(run_id, str)
        or not run_id
        or Path(run_id).name != run_id
        or run_id in {".", ".."}
    ):
        raise GoldenError(f"run_id {run_id!r} escapes recordings directory {base_dir}")
    try:
        base_resolved = base_dir.resolve()
        target = (base_dir / run_id).resolve()
    except (ValueError, OSError) as exc:
        raise GoldenError(f"invalid run_id {run_id!r}: {exc}") from exc
    if not target.is_relative_to(base_resolved) or target == base_resolved:
        raise GoldenError(f"run_id {run_id!r} escapes recordings directory {base_dir}")
    return target


def _lane_recordings(fixture_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Per lane, the recorded (run_id, fleet) pairs in order: previous
    attempts (from an earlier resume), then this run's own attempts."""
    out: dict[str, list[tuple[str, str]]] = {}
    lanes_dir = fixture_dir / "lanes"
    if not lanes_dir.is_dir():
        return out
    for lane_file in sorted(lanes_dir.glob("*.json")):
        try:
            data = json.loads(lane_file.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            raise GoldenError(f"{lane_file}: corrupt lane JSON: {exc}") from exc
        if not isinstance(data, dict) or "name" not in data or not isinstance(data["name"], str):
            raise GoldenError(f"{lane_file}: missing or invalid 'name'")
        rows: list[tuple[str, str]] = []
        for key in ("previous_attempts", "attempts"):
            attempts = data.get(key)
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                run_id = attempt.get("run_id")
                if isinstance(run_id, str):
                    _jail_run_id(fixture_dir / "runs", run_id)
                    rows.append((run_id, attempt.get("fleet") or ""))
        out[data["name"]] = rows
    result_path = fixture_dir / "result.json"
    if result_path.is_file():
        try:
            result_raw = json.loads(result_path.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            raise GoldenError(f"{result_path}: corrupt result JSON: {exc}") from exc
        if not isinstance(result_raw, dict):
            raise GoldenError(f"{result_path}: corrupt result JSON: expected object")
    else:
        result_raw = None
    for label, run_id, fleet in _aux_recordings(result_raw):
        if isinstance(run_id, str):
            _jail_run_id(fixture_dir / "runs", run_id)
            out.setdefault(label, []).append((run_id, fleet))
    return out


def _init_replay_repo(path: Path) -> None:
    """An empty git repository with one commit -- the same shape
    `_fresh_replay`'s own `cwd` gets -- so a lane dispatched into it can
    still gate, diff, and commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "golden@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "golden"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "golden"], cwd=path, check=True)


def _resolve_extra_cwds(
    mission_text: str, home: Path, cwds: dict[str, str] | None
) -> dict[str, str]:
    """E19: every `<cwd2>`, `<cwd3>`, ... placeholder `mission_text` actually
    uses, mapped to a real directory -- the caller's own `cwds`, when given,
    else a fresh empty repository under `home` created just for the replay."""
    cwds = cwds or {}
    out: dict[str, str] = {}
    for n in sorted({int(m) for m in _CWD_PLACEHOLDER_RE.findall(mission_text)}):
        placeholder = f"<cwd{n}>"
        if placeholder in cwds:
            out[placeholder] = str(Path(cwds[placeholder]).resolve())
            continue
        repo_dir = home / f"_replay_cwd{n}"
        _init_replay_repo(repo_dir)
        out[placeholder] = str(repo_dir.resolve())
    return out


def replay(
    fixture_dir: str | Path, *, home: Path, cwd: str, cwds: dict[str, str] | None = None
) -> Replay:
    """Load a fixture's mission snapshot and run it again through
    `mission.run_mission`, with a dispatcher that replays each attempt's
    recorded receipt instead of spawning a fleet.

    E19: `cwds` maps each extra `<cwd2>`, `<cwd3>`, ... placeholder a
    cross-repo fixture uses to a real directory; a placeholder the caller
    does not name gets a fresh empty repository under `home` instead, so a
    fixture recorded before this field existed (or replayed by a caller that
    never names one) still replays."""
    fixture_dir = Path(fixture_dir)
    home = Path(home)
    # `mission_from_dict` resolves `cwd` (symlinks included) when it loads a
    # mission; the snapshot's own round-trip check re-derives it the same
    # way, so a `cwd` that is not already resolved (macOS's /tmp -> /private
    # /tmp, for one) would make every snapshot fail to round-trip.
    cwd = str(Path(cwd).resolve())
    mission_path = fixture_dir / "mission.json"
    if not mission_path.is_file():
        raise GoldenError(f"{mission_path}: no mission.json")
    try:
        raw_mission_text = mission_path.read_text()
    except OSError as exc:
        raise GoldenError(f"{mission_path}: {exc}") from exc
    mission_text = raw_mission_text.replace("<cwd>", cwd)
    extra_cwds = _resolve_extra_cwds(raw_mission_text, home, cwds)
    for placeholder, real in extra_cwds.items():
        mission_text = mission_text.replace(placeholder, real)
    # A fixture recorded before a schema field existed (D2's `taint`, C5's
    # `retry`/`on`, ...) predates that field in its own mission.json; without
    # backfilling here, every such fixture would stop replaying the moment
    # `Mission.from_snapshot`'s exact key match tightens around a new field.
    try:
        raw_snapshot = json.loads(mission_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise GoldenError(f"{mission_path}: corrupt mission JSON: {exc}") from exc
    try:
        mission = Mission.from_snapshot(_backfill_snapshot(raw_snapshot))
    except (KeyError, ValueError, TypeError) as exc:
        raise GoldenError(f"{mission_path}: invalid mission snapshot: {exc}") from exc

    user_home = str(home / "_replay_user_home")

    def restore(text: str) -> str:
        text = text.replace("<cwd>", cwd).replace("<home>", str(home)).replace("<user>", user_home)
        for placeholder, real in extra_cwds.items():
            text = text.replace(placeholder, real)
        return text

    # E7: a human lane's recorded answer stands in for the operator -- see
    # `record`'s `answers/` copy and `_execute_mission`'s `human_answers`.
    human_answers: dict[str, str] = {}
    answers_dir = fixture_dir / "answers"
    if answers_dir.is_dir():
        for lane in mission.lanes:
            if not lane.human:
                continue
            answer_file = answers_dir / f"{lane.name}.txt"
            if answer_file.is_file():
                human_answers[lane.name] = restore(answer_file.read_text())
            deliverable = lane.attempts[0].deliverable
            deliverable_file = answers_dir / f"{lane.name}.deliverable"
            if deliverable is not None and deliverable_file.is_file():
                dest = Path(cwd) / deliverable["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(restore(deliverable_file.read_text()))

    lane_recordings = _lane_recordings(fixture_dir)
    differences: list[str] = []
    notes: list[str] = []
    # D18: contract fields the recordings do not carry, so `check` can say
    # what it could not compare instead of silently comparing nothing.
    uncomparable: set[str] = set()
    call_index: dict[str, int] = {}

    def dispatcher(
        spec,
        *,
        lane: str,
        attempt: str,
        retry: int | None,
        dry_run: bool,
        test_command: str | None,
        commit_message: str | None,
        isolate: bool,
        home: Path,
        no_op_ok: bool,
        base_ref: str | None,
        cancel,
    ) -> Result:
        k = call_index.get(lane, 0)
        call_index[lane] = k + 1
        recordings = lane_recordings.get(lane, [])

        def unrecorded(reason: str) -> Result:
            differences.append(f"lane {lane}: {reason}")
            return Result(
                run_id=f"golden-unrecorded-{lane}-{k + 1}",
                fleet=spec.fleet,
                model=spec.model or "",
                effort=spec.effort,
                mode=spec.mode,
                cwd=cwd,
                timeout=spec.resolved_timeout(),
                exit_code=None,
                timed_out=False,
                duration_s=0.0,
                run_dir="",
                stdout_path="",
                stderr_path="",
                tail="",
                spawned=False,
                error="golden: no recorded run",
            )

        if k >= len(recordings):
            return unrecorded(
                f"replay dispatched attempt {k + 1} but the recording has {len(recordings)}"
            )
        run_id, fleet = recordings[k]
        src = _jail_run_id(fixture_dir / "runs", run_id)
        if not (src / "result.json").is_file():
            # F8: a fixture recorded before collate, judge, and resolve runs
            # were copied names their run ids in result.json but holds no
            # receipt for them -- a difference to report, never a crash.
            return unrecorded(f"recorded run {run_id} has no result.json in the fixture")
        dst = _jail_run_id(Path(home) / "runs", run_id)
        dst.mkdir(parents=True, exist_ok=True)
        recorded_stdout = ""
        for name in RUN_FILES:
            # stdout.log is stored in the fixture as stdout.jsonl (never a
            # .log name, see RUN_FILES above); the recreated run directory
            # gets the live convention name back.
            file_src = src / (_FIXTURE_STDOUT_NAME if name == "stdout.log" else name)
            if not file_src.is_file():
                continue
            text = restore(file_src.read_text())
            (dst / name).write_text(text)
            if name == "stdout.log":
                recorded_stdout = text

        recorded_result_path = dst / "result.json"
        if recorded_result_path.is_file():
            try:
                recorded_result = json.loads(recorded_result_path.read_text())
            except (json.JSONDecodeError, ValueError) as exc:
                raise GoldenError(f"{recorded_result_path}: corrupt result JSON: {exc}") from exc
        else:
            recorded_result = {}
        recorded_answer = (dst / "answer.txt").read_text() if (dst / "answer.txt").is_file() else ""
        # D18: what this build asked for, against what the recording was
        # actually dispatched with. Compared before the parser fields so a
        # routing regression is reported even when the transcript still
        # parses identically.
        _compare_contract(differences, uncomparable, run_id, spec, recorded_result)
        parsed = outputs_mod.parse(fleet, recorded_stdout)
        _compare(differences, run_id, "answer", recorded_answer, parsed.answer)
        _compare(
            differences,
            run_id,
            "usage",
            _token_fields(recorded_result.get("usage")),
            _token_fields(parsed.usage.to_dict() if parsed.usage else None),
        )
        _compare(differences, run_id, "status", recorded_result.get("fleet_status"), parsed.status)
        _compare(differences, run_id, "error", recorded_result.get("fleet_error"), parsed.error)
        _compare(
            differences, run_id, "session_id", recorded_result.get("session_id"), parsed.session_id
        )

        # E17: both sides compared (and hashed) in placeholder form: the
        # fixture's prompt.txt was scrubbed at record time and is never
        # restored to real paths, and the freshly rendered prompt is scrubbed
        # the same way here, so a real replay `cwd`/`home` never leaks into
        # the comparison (or the diff) by accident.
        replacements = _placeholder_map(
            home=Path(home),
            cwd=cwd,
            extra=[(real, placeholder) for placeholder, real in extra_cwds.items()],
        )
        rendered_mapped = _strip_nonce(scrub_text(spec.prompt, replacements))
        prompt_sha256 = hashlib.sha256(rendered_mapped.encode()).hexdigest()

        recorded_prompt_path = src / "prompt.txt"
        if recorded_prompt_path.is_file():
            recorded_prompt = _strip_nonce(recorded_prompt_path.read_text())
            if rendered_mapped != recorded_prompt:
                diff_lines = list(
                    difflib.unified_diff(
                        recorded_prompt.splitlines(),
                        rendered_mapped.splitlines(),
                        lineterm="",
                    )
                )[:40]
                differences.append(
                    f"lane {lane} attempt {attempt}: rendered prompt differs from the recording\n"
                    + "\n".join(diff_lines)
                )

        overrides = {
            "run_dir": str(dst),
            "stdout_path": str(dst / "stdout.log"),
            "answer_path": str(dst / "answer.txt") if (dst / "answer.txt").is_file() else None,
            "diff_path": str(dst / "diff.patch") if (dst / "diff.patch").is_file() else None,
            # attestation.json is never in the fixture (see RUN_FILES above),
            # so the recreated run directory never gets one either.
            "attestation_path": None,
            "cwd": cwd,
            "prompt_sha256": prompt_sha256,
        }
        return Result.from_dict({**recorded_result, **overrides})

    result_path = fixture_dir / "result.json"
    if result_path.is_file():
        try:
            recorded_result = json.loads(result_path.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            raise GoldenError(f"{result_path}: corrupt result JSON: {exc}") from exc
    else:
        recorded_result = {}
    mission_result = run_mission(
        mission,
        home=home,
        dispatcher=dispatcher,
        human_answers=human_answers or None,
        notifier=_replay_notifier,
        conflict_finder=_recorded_conflict_finder(recorded_result),
    )
    # D18: a replay that never dispatched an attempt the recording holds has
    # not replayed the recorded mission -- a lane dropped by a scheduling or
    # routing regression would otherwise leave the projection green. A lane
    # the replay deliberately did not start (a pause point, a skip, a
    # cancellation) is a note instead: that decision is itself projected and
    # already compared against `expected.json`, and a fixture recorded past
    # its own pause point (f10-shape-a-foreign-repo) is the normal case.
    skipped_lanes = {
        lane.get("name"): lane.get("skipped")
        for lane in mission_result.lanes
        if isinstance(lane, dict) and lane.get("skipped")
    }
    for lane_name, recordings in sorted(lane_recordings.items()):
        consumed = call_index.get(lane_name, 0)
        if consumed >= len(recordings):
            continue
        unconsumed = ", ".join(run_id for run_id, _ in recordings[consumed:])
        line = (
            f"lane {lane_name}: replay dispatched {consumed} attempt(s) but the recording "
            f"has {len(recordings)}; unconsumed: {unconsumed}"
        )
        if lane_name in skipped_lanes:
            notes.append(f"{line} ({skipped_lanes[lane_name]})")
        else:
            differences.append(line)
    if uncomparable:
        notes.append(
            "contract fields absent from the recording, not compared: "
            + ", ".join(sorted(uncomparable))
        )
    return Replay(
        differences=differences,
        notes=notes,
        projection=projection(mission_result),
        result=mission_result,
    )


def _recorded_conflict_finder(recorded_result: dict):
    """F8: what a replay uses in place of `collisions.merge_conflicts`. A
    replay repository holds none of the recorded tips, so `git merge-tree`
    would find nothing and every collate prompt that names a conflict would
    differ from its recording. The recorded mission's `collisions.conflicts`
    is the answer: for the lanes asked about, the recorded pairs among them
    and the files those pairs conflicted on."""
    collisions = recorded_result.get("collisions") if isinstance(recorded_result, dict) else None
    conflicts = (collisions or {}).get("conflicts") if isinstance(collisions, dict) else None
    recorded_pairs = list((conflicts or {}).get("pairs") or [])

    def find(repo: str, tips: dict[str, str]) -> dict:
        del repo
        names = set(tips)
        pairs = [
            pair
            for pair in recorded_pairs
            if isinstance(pair, dict) and set(pair.get("lanes") or []) <= names
        ]
        files: dict[str, list[list[str]]] = {}
        for pair in pairs:
            for path in pair.get("conflicts") or []:
                files.setdefault(path, []).append(list(pair.get("lanes") or []))
        return {"pairs": pairs, "files": files}

    return find


def _replay_notifier(config: dict, event: dict) -> dict:
    """F8: what a replay records in place of `notify.emit`. An offline
    replay never runs a mission's notify command: the recorded command is
    the operator's hook (a scrubbed one is not even a real path), and a
    `golden check` that delivered a notification, or failed one, would be a
    side effect on every run of the suite. The event name is what the
    projection pins, so a change that moves a settle boundary into or out of
    the notify path is a fixture diff."""
    del config
    return {
        "event": event.get("event"),
        "ok": True,
        "exit_code": 0,
        "timed_out": False,
        "error": None,
    }


def _fresh_replay(fixture_dir: Path, *, cwds: dict[str, str] | None = None) -> Replay:
    with (
        tempfile.TemporaryDirectory(prefix="conductor-golden-home-") as home_dir,
        tempfile.TemporaryDirectory(prefix="conductor-golden-cwd-") as cwd_dir,
    ):
        cwd_path = Path(cwd_dir)
        _init_replay_repo(cwd_path)
        return replay(fixture_dir, home=Path(home_dir), cwd=str(cwd_path), cwds=cwds)


# --- projection and check ---------------------------------------------------


_ATTEMPT_PROJECTION_KEYS = (
    "fleet",
    "model",
    "effort",
    "mode",
    "ok",
    "kind",
    "failure",
    "no_op",
    "over_cap",
    "tool_calls",
    "tokens",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_basis",
    "session_id",
    "retry_of",
    "retry",
    "note",
    "prompt_sha256",
)
_LANE_PROJECTION_KEYS = (
    "name",
    "stage",
    "needs",
    "base",
    "ok",
    "skipped",
    "escalated",
    "kinds",
    "branch",
)


def projection(result: MissionResult) -> dict:
    """The fields a routing or template change can move and a clock cannot:
    no paths, durations, run ids, timestamps, or dollar amounts."""
    data = result.to_dict()
    lanes = []
    for lane in data["lanes"]:
        attempts = [
            {key: attempt.get(key) for key in _ATTEMPT_PROJECTION_KEYS}
            for attempt in lane.get("attempts") or []
        ]
        entry = {key: lane.get(key) for key in _LANE_PROJECTION_KEYS}
        entry["attempts"] = attempts
        # E10: a plan lane's `refused` and `dry_run_ok` describe what
        # happened, the same scope as every other lane-level projection
        # field; `child_path`, `child_name`, `child_max_cost_usd`, and the
        # child's own report path and dollar cost are excluded, same as
        # every other path/dollar amount this projection drops.
        # Only when the lane is a plan lane: a recording made before E10 has
        # no `plan` key at all, and its projection must stay byte-identical.
        plan = lane.get("plan")
        if plan is not None:
            entry["plan"] = {"refused": plan.get("refused"), "dry_run_ok": plan.get("dry_run_ok")}
        lanes.append(entry)
    paused = data.get("paused")
    if paused is not None and "child_path" in paused:
        # E10: `child_path` is a filesystem path under the mission directory
        # that a live launch and a replay never agree on (a fresh temp home
        # per run), the same reason the plan block above drops it -- unlike
        # `ask_path` (a human lane's own pause field, unaffected by this
        # item), which no test here has ever exercised through a still-paused
        # recording.
        #
        # D2: `child_sha256` and `child_policy` go with it. The digest is
        # over the child file's own bytes, which a fixture scrubs (its `cwd`
        # is a real path), so a recording and a replay can never agree on
        # it; the policy summary is dollar amounts, which this projection
        # drops everywhere else.
        paused = {
            key: value
            for key, value in paused.items()
            if key not in ("child_path", "child_sha256", "child_policy")
        }
    # F8: which settle boundaries reached the notify hook, in order. Only
    # when any did: a fixture recorded without `notify` (both C5 fixtures)
    # keeps a byte-identical projection, and an event's own outcome is
    # never pinned -- a replay's notifier always answers ok (above), and a
    # live hook's exit code is the operator's machine, not the routing.
    notified = [note.get("event") for note in data.get("notifications") or []]
    # F8: what a sitting or a resolver decided, never what it cost or which
    # run said it -- only when the mission had one, same reason as `plan`.
    collate = data.get("collate")
    collate_projection = None
    if isinstance(collate, dict):
        tally = collate.get("tally") if isinstance(collate.get("tally"), dict) else {}
        collate_projection = {
            "ok": collate.get("ok"),
            "rank": collate.get("rank"),
            "strongest": collate.get("strongest"),
            "error": collate.get("error"),
            "agreement": tally.get("agreement"),
            "votes": tally.get("votes"),
        }
    resolve = data.get("resolve")
    resolve_projection = (
        {"ran": resolve.get("ran"), "ok": resolve.get("ok"), "error": resolve.get("error")}
        if isinstance(resolve, dict)
        else None
    )
    projected = {
        "ok": data.get("ok"),
        "require": data.get("require"),
        "notes": data.get("notes"),
        "errors": data.get("errors"),
        "escalation": data.get("escalation"),
        "early_cancel": data.get("early_cancel"),
        "paused": paused,
        "quorum": data.get("quorum"),
        "ranking": [
            {"lane": row.get("lane"), "rank": row.get("rank"), "ok": row.get("ok")}
            for row in data.get("ranking") or []
        ],
        "cache_hit_rate": (data.get("cache") or {}).get("hit_rate"),
        "lanes": lanes,
    }
    if notified:
        projected["notifications"] = notified
    if collate_projection is not None:
        projected["collate"] = collate_projection
    if resolve_projection is not None:
        projected["resolve"] = resolve_projection
    return projected


def _diff_projection(expected: object, actual: object, path: str = "$") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                diffs.append(f"projection {path}.{key}: expected <missing>, got {actual[key]}")
            elif key not in actual:
                diffs.append(f"projection {path}.{key}: expected {expected[key]}, got <missing>")
            else:
                diffs.extend(_diff_projection(expected[key], actual[key], f"{path}.{key}"))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"projection {path}: expected {len(expected)} item(s), got {len(actual)}"]
        diffs = []
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            diffs.extend(_diff_projection(e, a, f"{path}[{i}]"))
        return diffs
    if expected != actual:
        return [f"projection {path}: expected {expected}, got {actual}"]
    return []


def version_drift(fixture_dir: str | Path) -> list[str]:
    """E22: one line per fleet whose `golden.json`-recorded `fleet_versions`
    entry differs from `fleets.cli_version` on this machine, or a single
    "recorded version unknown" line when the fixture predates that field
    and carries none at all. E17: the same, one line per conductor-authored
    prompt whose recorded id differs from `prompts.prompt_versions()` today,
    or "prompt versions unknown" when the fixture predates that field. Drift
    is a note, never a check failure: the caller must not fold this into a
    fixture's pass/fail exit code."""
    manifest_path = Path(fixture_dir) / "golden.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        raise GoldenError(f"{manifest_path}: corrupt golden JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GoldenError(f"{manifest_path}: corrupt golden JSON: expected object")
    lines: list[str] = []
    recorded_fleets = manifest.get("fleet_versions")
    if not recorded_fleets:
        lines.append("recorded version unknown")
    else:
        for fleet_name in sorted(recorded_fleets):
            old = recorded_fleets[fleet_name]
            new = fleets_mod.cli_version(fleet_name)
            if old != new:
                lines.append(f"{fleet_name} recorded {old}, installed {new}")
    recorded_prompts = manifest.get("prompt_versions")
    if not recorded_prompts:
        lines.append("prompt versions unknown")
    else:
        current = prompts_mod.prompt_versions()
        for name in sorted(recorded_prompts):
            old = recorded_prompts[name]
            new = current.get(name)
            if old != new:
                lines.append(f"prompt {name} recorded {old}, now {new}")
    return lines


def _prompt_sha256_from_recording(fixture_dir: Path, run_id: str) -> str | None:
    """E17: an attempt's `prompt_sha256`, recomputed from its own recorded
    `prompt.txt` -- never from the live replay, so a legacy fixture that
    predates this field gets the value it actually ran with, not today's."""
    path = _jail_run_id(fixture_dir / "runs", run_id) / "prompt.txt"
    if not path.is_file():
        return None
    return hashlib.sha256(_strip_nonce(scrub_text(path.read_text(), [])).encode()).hexdigest()


def _backfill_prompt_sha256(fixture_dir: Path, expected: dict) -> dict:
    """A fixture recorded before `prompt_sha256` existed lacks it on every
    attempt; fill it in from that attempt's own recorded run (matched by
    position, the same order `replay`'s dispatcher consumes) so `check`
    compares like for like instead of manufacturing a difference on every
    fixture on record the first time this ships."""
    lane_recordings = _lane_recordings(fixture_dir)
    for lane in expected.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        recordings = lane_recordings.get(lane.get("name"), [])
        for index, attempt in enumerate(lane.get("attempts") or []):
            if not isinstance(attempt, dict) or "prompt_sha256" in attempt:
                continue
            run_id = recordings[index][0] if index < len(recordings) else None
            attempt["prompt_sha256"] = (
                _prompt_sha256_from_recording(fixture_dir, run_id) if run_id else None
            )
    return expected


def check(
    fixture_dir: str | Path,
    *,
    update: bool = False,
    cwds: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> list[str]:
    """Replay a fixture into a fresh temporary home and a fresh temporary
    git repository (one empty commit) as `cwd`. Returns the replay's own
    differences plus, unless `update`, one line per projection field that
    differs from the fixture's `expected.json`. `update` rewrites
    `expected.json` from the replay instead.

    E19: `cwds` is `replay`'s own -- a cross-repo fixture's extra
    placeholders each get a fresh empty repository when the caller does not
    name one.

    D18: `notes`, when given, is extended with the replay's own notes (a
    contract field the recordings do not carry). Notes are never returned as
    differences: like version drift, they are printed beside the check and
    never fail it."""
    fixture_dir = Path(fixture_dir)
    replayed = _fresh_replay(fixture_dir, cwds=cwds)
    if notes is not None:
        notes.extend(replayed.notes)
    expected_path = fixture_dir / "expected.json"
    if update:
        expected_path.write_text(json.dumps(replayed.projection, indent=2, sort_keys=True))
        return list(replayed.differences)
    if expected_path.is_file():
        try:
            expected = json.loads(expected_path.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            raise GoldenError(f"{expected_path}: corrupt expected JSON: {exc}") from exc
        if not isinstance(expected, dict):
            raise GoldenError(f"{expected_path}: corrupt expected JSON: expected object")
    else:
        expected = {}
    expected = _backfill_prompt_sha256(fixture_dir, expected)
    return list(replayed.differences) + _diff_projection(expected, replayed.projection)
