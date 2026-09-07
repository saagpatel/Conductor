"""E13: the receipt export bundle.

`export` copies a finished mission directory, every run it dispatched, and a
manifest into a self-contained directory a reader outside this machine can
audit: file digests and the signed receipt chain's linkage are checked
against the manifest with nothing but the bundle itself (`check`); the
signatures themselves are not, and never can be, since `attest.py`'s key is
a shared secret that must never leave the machine that holds it. A bundle
that could be verified standalone would be a bundle that shipped the key
that forges everything -- so `export` verifies every signature once, here,
with the key, and records the verdict in `manifest.json` instead of shipping
anything a stranger could check it against again.

Every copied file passes through C7's scrubber (`golden.py`): the conductor
home, the user's home, and the mission's own `cwd` become placeholders, and
known secret shapes are redacted. A DSSE envelope (every receipt link and
every attestation) carries its statement as a base64 payload, opaque to a
plain-text scrub; it is decoded, scrubbed as JSON, and re-encoded, so its
signature deliberately no longer verifies afterward -- exactly why
`manifest.json` states `signatures` as not verifiable from the bundle.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, attest, spend
from .golden import _placeholder_map, _scrub_json_value, scrub_guard, scrub_text

FORMAT = "conductor/export/v1"

# Every file the mission directory itself may contribute, each only when it
# exists. `running.json` (a live-mission lock) is never copied: an export is
# read after the fact, and the lock says nothing about the finished mission.
MISSION_TOP_FILES = (
    "mission.json",
    "result.json",
    "report.md",
    "pause.json",
    "tally.json",
    "tally.md",
)
MISSION_SUBDIRS = ("lanes", "receipts", "diffs", "answers", "asks", "verdicts", "deliverables")
# argv is reconstructible from the spec in a golden fixture (C7), but a
# reader auditing a real mission from outside this machine has no spec to
# reconstruct it from, so E13 ships it. stdout.log/stderr.log are the bulk
# of a bundle's size and are transcripts, not evidence the manifest checks
# anything against, so they are opt-in (`logs=True`).
RUN_FILES = (
    "result.json",
    "attestation.json",
    "diff.patch",
    "prompt.txt",
    "answer.txt",
    "argv.json",
)
RUN_LOG_FILES = ("stdout.log", "stderr.log")
_SKIP_NAMES = {"running.json", "liveness.json"}
_CHAIN_RELPATH = Path("receipts") / "chain.json"

_NOTE = (
    "The receipt key is a shared secret that stays on the exporting machine; "
    "signatures were verified there at export time and cannot be re-verified "
    "from this bundle. The manifest proves that the files it lists are "
    "unchanged since export; it does not prove that work this bundle omits "
    "is absent."
)

# W9: what a bundle never holds, regardless of this particular mission --
# read by `conductor export --check` when a bundle's own manifest carries
# `scope`, so a reader outside this machine knows the boundary of what was
# checked without having to infer it from what is merely missing.
_OMITTED = [
    "stdout.log and stderr.log are omitted unless the export runs with --logs.",
    "running.json and liveness.json are never included; they describe a mission "
    "or run still in progress, not a finished one.",
    "run directories named by a lane receipt, a chain link, or the mission's "
    "result.json snapshot but not found under runs/ are omitted.",
    "worktrees and branches are never included; only the diffs and patches "
    "already captured travel with the bundle.",
    "the receipt key itself never leaves the exporting machine and is never "
    "included.",
    "any run this mission's lane receipts, its receipt chain, and its "
    "result.json snapshot never named is omitted.",
]


class ExportError(ValueError):
    """A mission cannot be exported, or a leak was found in the bundle."""

    def __init__(self, message: str, *, leaks: list[str] | None = None) -> None:
        super().__init__(message)
        self.leaks: list[str] = leaks or []


@dataclass
class ExportResult:
    bundle_dir: Path
    files: int
    bytes: int
    chain_verified_at_export: bool
    attestations_verified_at_export: tuple[int, int]
    # D8: `verified | partial | empty | missing | malformed | failed`, the
    # same states `conductor attest` reports. `chain_verified_at_export` is
    # exactly `chain_state_at_export == "verified"`.
    chain_state_at_export: str = "missing"
    leaks: list[str] = field(default_factory=list)
    # W9: the manifest's own `scope` object, carried on the result too so a
    # caller (cli.py) does not have to re-read manifest.json to print it.
    scope: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    ok: bool
    problems: list[str]
    files_checked: int
    links_checked: int


# --- scrubbing helpers, on top of golden.py's ------------------------------


def _relative_or_none(path_str: str, base: Path) -> str:
    """`path_str` relative to `base` when it is under it; unchanged
    otherwise (the DSSE payload leaves scrubbing of anything not a known
    bundle-relative path to the placeholder pass that follows)."""
    try:
        return str(Path(path_str).relative_to(base))
    except ValueError:
        return path_str


def _rewrite_chain_paths(chain_obj: dict, mission_dir: Path) -> None:
    for link in chain_obj.get("links") or []:
        if isinstance(link, dict) and isinstance(link.get("path"), str):
            link["path"] = _relative_or_none(link["path"], mission_dir)


def _is_dsse(obj: object) -> bool:
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("payloadType"), str)
        and isinstance(obj.get("payload"), str)
        and "signatures" in obj
    )


def _scrub_dsse(envelope: dict, replacements: list[tuple[str, str]], *, home: Path) -> dict:
    """Decode a DSSE envelope's payload, rewrite its `attestation_path` (a
    mission link statement's only absolute path) to a bundle-relative one,
    scrub the statement as JSON, and re-encode -- `signatures` untouched, so
    it no longer verifies against the re-encoded payload. A payload that
    fails to decode is scrubbed as a plain JSON object instead of raising:
    export still owes the caller a clean bundle even for a shape it does not
    recognize."""
    try:
        payload = base64.b64decode(envelope["payload"], validate=True)
        statement = json.loads(payload)
    except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError):
        return _scrub_json_value(envelope, replacements)
    if not isinstance(statement, dict):
        return _scrub_json_value(envelope, replacements)
    attestation_path = statement.get("attestation_path")
    if isinstance(attestation_path, str):
        statement["attestation_path"] = _relative_or_none(attestation_path, home)
    scrubbed_statement = _scrub_json_value(statement, replacements)
    new_payload = json.dumps(scrubbed_statement, sort_keys=True, separators=(",", ":")).encode()
    new_envelope = dict(envelope)
    new_envelope["payload"] = base64.b64encode(new_payload).decode("ascii")
    return new_envelope


def _scrub_json_file_text(
    text: str, replacements: list[tuple[str, str]], *, home: Path, mission_dir: Path, relpath: Path
) -> str:
    obj = json.loads(text)
    if _is_dsse(obj):
        obj = _scrub_dsse(obj, replacements, home=home)
    else:
        if relpath == _CHAIN_RELPATH and isinstance(obj, dict):
            _rewrite_chain_paths(obj, mission_dir)
        obj = _scrub_json_value(obj, replacements)
    return json.dumps(obj, indent=2)


def _copy_scrubbed(
    src: Path,
    dst: Path,
    *,
    replacements: list[tuple[str, str]],
    home: Path,
    mission_dir: Path,
    relpath: Path,
) -> tuple[str, str, int]:
    """Copy `src` to `dst` through the scrubber. Returns the original file's
    sha256, the bundled file's sha256, and the bundled file's byte size."""
    raw = src.read_bytes()
    original_sha = hashlib.sha256(raw).hexdigest()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".json":
        try:
            scrubbed_text = _scrub_json_file_text(
                raw.decode("utf-8"),
                replacements,
                home=home,
                mission_dir=mission_dir,
                relpath=relpath,
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            scrubbed_text = scrub_text(raw.decode("utf-8", errors="replace"), replacements)
    else:
        scrubbed_text = scrub_text(raw.decode("utf-8", errors="replace"), replacements)
    dst.write_text(scrubbed_text)
    final_bytes = dst.read_bytes()
    return original_sha, hashlib.sha256(final_bytes).hexdigest(), len(final_bytes)


def _find_key_material(work: Path, key: bytes) -> str | None:
    """The first bundle file, relative to `work`, that carries the receipt
    key itself -- as raw bytes, as hex, or as base64 -- or None on a clean
    bundle. The key signs every receipt; it must never travel with a bundle
    that ships to a reader outside this machine, whatever shape it hides in.
    """
    hex_form = key.hex()
    b64_form = base64.b64encode(key).decode("ascii")
    for file in sorted(p for p in work.rglob("*") if p.is_file()):
        raw = file.read_bytes()
        if key in raw:
            return str(file.relative_to(work))
        text = raw.decode("utf-8", errors="ignore")
        if hex_form in text or b64_form in text:
            return str(file.relative_to(work))
    return None


# --- run id discovery -------------------------------------------------------


def _lane_run_ids(mission_dir: Path) -> set[str]:
    """Every run id any lane receipt's attempts name (`previous_attempts`
    then `attempts`), which can include a retried or superseded attempt the
    chain's own links never name (a link only ever names a lane's final
    attempt)."""
    run_ids: set[str] = set()
    lanes_dir = mission_dir / "lanes"
    if not lanes_dir.is_dir():
        return run_ids
    for lane_file in sorted(lanes_dir.glob("*.json")):
        try:
            data = json.loads(lane_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("previous_attempts", "attempts"):
            for attempt in data.get(key) or []:
                run_id = attempt.get("run_id") if isinstance(attempt, dict) else None
                if isinstance(run_id, str):
                    run_ids.add(run_id)
    return run_ids


# --- export ------------------------------------------------------------


def export(
    home: str | Path, mission_id: str, out_dir: str | Path, *, logs: bool = False
) -> ExportResult:
    """Copy mission `mission_id` under `home` and every run it dispatched
    into `out_dir` as a scrubbed, manifest-checked bundle. Refuses
    (`ExportError`) a `mission_id` that is not a directory name, a mission
    that does not exist, an `out_dir` that already exists, or a missing
    receipt key; also refuses, after removing the bundle, when `scrub_guard`
    finds a leak (`ExportError.leaks` then carries every finding)."""
    home = Path(home)
    out_dir = Path(out_dir)
    if Path(mission_id).name != mission_id or mission_id in {".", ".."}:
        raise ExportError("MISSION_ID must be a mission directory name")
    mission_dir = home / "missions" / mission_id
    if not mission_dir.is_dir():
        raise ExportError(f"mission '{mission_id}' does not exist")
    if out_dir.exists():
        raise ExportError(f"{out_dir} already exists")
    key = attest.read_receipt_key(home)
    if key is None:
        raise ExportError("receipt key is missing")

    snapshot_path = mission_dir / "mission.json"
    mission_raw: dict = {}
    if snapshot_path.is_file():
        try:
            loaded = json.loads(snapshot_path.read_text())
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            mission_raw = loaded
    cwd = mission_raw.get("cwd")
    replacements = _placeholder_map(home=home, cwd=cwd)

    chain_path = mission_dir / "receipts" / "chain.json"
    chain_present = chain_path.is_file()
    loaded_chain: object = None
    if chain_present:
        try:
            loaded_chain = json.loads(chain_path.read_text())
        except (OSError, json.JSONDecodeError):
            loaded_chain = None
    # D8: one validator, shared with `conductor attest`, and a state rather
    # than a boolean. A missing chain.json used to leave `chain_invalid`
    # False and so exported as `verified_at_export: true` with nothing
    # verified at all; `missing`, `malformed`, `empty` and `partial` are
    # each their own answer now, and only `verified` is verified.
    chain_evaluation = attest.evaluate_chain(
        loaded_chain,
        home=home,
        key=key,
        mission_id=mission_id,
        expected=attest.recorded_chain(mission_dir),
        present=chain_present,
    )
    chain_state = chain_evaluation["state"]
    chain_problems = chain_evaluation["problems"]
    chain_rows = chain_evaluation["rows"]
    chain_verified = chain_state == "verified"

    # W9: `spend.mission_run_ids` already knows every run a mission paid
    # for, including a judge sitting's extra orders and a superseded
    # resolver that no lane attempt or chain link ever names on its own.
    result_path = mission_dir / "result.json"
    result_raw: dict = {}
    if result_path.is_file():
        try:
            loaded_result = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError):
            loaded_result = None
        if isinstance(loaded_result, dict):
            result_raw = loaded_result

    lane_run_ids = _lane_run_ids(mission_dir)
    chain_run_ids = {row["run_id"] for row in chain_rows if isinstance(row["run_id"], str)}
    snapshot_run_ids = spend.mission_run_ids(result_raw)
    run_ids = sorted(lane_run_ids | chain_run_ids | snapshot_run_ids)
    missing_run_dirs = sorted(run_id for run_id in run_ids if not (home / "runs" / run_id).is_dir())
    link_statement_by_run: dict[str, dict] = {
        row["run_id"]: row["_statement"]
        for row in chain_rows
        if isinstance(row["run_id"], str) and isinstance(row.get("_statement"), dict)
    }

    with tempfile.TemporaryDirectory(prefix="conductor-export-") as tmp:
        work = Path(tmp) / out_dir.name
        work.mkdir()
        files_manifest: dict[str, dict] = {}

        def copy_one(src: Path, relpath: Path) -> None:
            dst = work / relpath
            original_sha, final_sha, size = _copy_scrubbed(
                src,
                dst,
                replacements=replacements,
                home=home,
                mission_dir=mission_dir,
                relpath=relpath,
            )
            files_manifest[str(relpath)] = {
                "sha256": final_sha,
                "sha256_original": original_sha,
                "bytes": size,
            }

        mission_files_present: list[str] = []
        for name in MISSION_TOP_FILES:
            src = mission_dir / name
            if src.is_file():
                copy_one(src, Path(name))
                mission_files_present.append(name)

        mission_subdirs_present: list[str] = []
        for name in MISSION_SUBDIRS:
            src_dir = mission_dir / name
            if not src_dir.is_dir():
                continue
            mission_subdirs_present.append(name)
            for file in sorted(p for p in src_dir.rglob("*") if p.is_file()):
                if file.name in _SKIP_NAMES:
                    continue
                copy_one(file, file.relative_to(mission_dir))

        run_files = RUN_FILES + (RUN_LOG_FILES if logs else ())
        for run_id in run_ids:
            run_dir = home / "runs" / run_id
            if not run_dir.is_dir():
                continue
            for name in run_files:
                src = run_dir / name
                if src.is_file():
                    copy_one(src, Path("runs") / run_id / name)

        attestations_manifest: dict[str, dict] = {}
        verified_count = 0
        for run_id in run_ids:
            link_statement = link_statement_by_run.get(run_id, {})
            problems, _taint = attest.verify_run_attestation(home, run_id, link_statement, key)
            ok = not problems
            if ok:
                verified_count += 1
            attestations_manifest[run_id] = {"verified_at_export": ok, "problems": problems}

        manifest_chain_links = [
            {
                "index": row["index"],
                "lane": row["lane"],
                "run_id": row["run_id"],
                "verified": row["verified"],
                "problems": row["problems"],
            }
            for row in chain_rows
        ]

        scope = {
            "run_ids": {
                "from_lanes": len(lane_run_ids),
                "from_chain": len(chain_run_ids),
                "from_snapshot": len(snapshot_run_ids),
                "total": len(run_ids),
            },
            "missing_run_dirs": missing_run_dirs,
            "mission_files": mission_files_present,
            "mission_subdirs": mission_subdirs_present,
            "run_files": list(run_files),
            "omitted": _OMITTED,
        }

        manifest = {
            "_type": FORMAT,
            "conductor_version": __version__,
            "mission_id": mission_id,
            "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "logs": logs,
            "files": files_manifest,
            "chain": {
                "state": chain_state,
                "verified_at_export": chain_verified,
                "problems": chain_problems,
                "links": manifest_chain_links,
            },
            "attestations": attestations_manifest,
            "scope": scope,
            "verifiable_here": ["file digests", "chain linkage"],
            "not_verifiable_here": ["signatures", "completeness"],
            "note": _NOTE,
        }
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2))

        key_leak = _find_key_material(work, key)
        if key_leak is not None:
            shutil.rmtree(work)
            raise ExportError(f"export refused: receipt key material found in {key_leak}")

        leaks = scrub_guard(work)
        if leaks:
            shutil.rmtree(work)
            raise ExportError(f"export leaked: {leaks[0]}", leaks=leaks)

        total_files = sum(1 for p in work.rglob("*") if p.is_file())
        total_bytes = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())

        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), str(out_dir))

    return ExportResult(
        bundle_dir=out_dir,
        files=total_files,
        bytes=total_bytes,
        chain_verified_at_export=chain_verified,
        attestations_verified_at_export=(verified_count, len(run_ids)),
        chain_state_at_export=chain_state,
        leaks=[],
        scope=scope,
    )


# --- check ---------------------------------------------------------------


def _resolve_in_bundle(bundle_dir: Path, relpath: str) -> Path | None:
    """A manifest or chain.json path that resolves outside `bundle_dir`
    (a `..` escape, or an absolute path) is never followed -- `check` reads
    nothing but the bundle, even when the bundle's own metadata is
    tampered with."""
    root = bundle_dir.resolve()
    candidate = (bundle_dir / relpath).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def check(bundle_dir: str | Path) -> CheckResult:
    """Verify a bundle against its own `manifest.json`, reading nothing but
    the bundle: every listed file's digest and size, that no unlisted file
    is present, and the receipt chain's linkage (index order, each link's
    file hash against the manifest, each `previous` against the prior
    link's recorded hash, and each link's run id against a matching
    `runs/<run id>/attestation.json`). Never reads or verifies a
    signature -- that needs the exporting machine's key, which never
    travels with the bundle (see `manifest.json`'s own `note`)."""
    bundle_dir = Path(bundle_dir)
    problems: list[str] = []
    manifest_path = bundle_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            ok=False,
            problems=[f"manifest.json unreadable: {exc}"],
            files_checked=0,
            links_checked=0,
        )
    if not isinstance(manifest, dict) or manifest.get("_type") != FORMAT:
        return CheckResult(
            ok=False,
            problems=["manifest.json is not a conductor/export/v1 manifest"],
            files_checked=0,
            links_checked=0,
        )

    files_meta = manifest.get("files")
    if not isinstance(files_meta, dict):
        problems.append("manifest.json 'files' field is malformed")
        files_meta = {}

    files_checked = 0
    for relpath, meta in sorted(files_meta.items()):
        path = _resolve_in_bundle(bundle_dir, relpath)
        if path is None:
            problems.append(f"{relpath}: path escapes the bundle")
            continue
        if not path.is_file():
            problems.append(f"{relpath}: missing from the bundle")
            continue
        raw = path.read_bytes()
        files_checked += 1
        if hashlib.sha256(raw).hexdigest() != meta.get("sha256"):
            problems.append(f"{relpath}: sha256 does not match manifest.json")
        if len(raw) != meta.get("bytes"):
            problems.append(f"{relpath}: byte count does not match manifest.json")

    expected_names = set(files_meta) | {"manifest.json"}
    for path in sorted(p for p in bundle_dir.rglob("*") if p.is_file()):
        rel = str(path.relative_to(bundle_dir))
        if rel not in expected_names:
            problems.append(f"{rel}: present in the bundle but not listed in manifest.json")

    links: list[dict] = []
    chain_path = bundle_dir / "receipts" / "chain.json"
    if chain_path.is_file():
        try:
            chain = json.loads(chain_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"receipts/chain.json unreadable: {exc}")
            chain = {}
        if isinstance(chain, dict) and isinstance(chain.get("links"), list):
            links = chain["links"]

    links_checked = 0
    previous_sha: str | None = None
    for position, link in enumerate(links):
        if not isinstance(link, dict):
            problems.append(f"receipts/chain.json link {position}: malformed entry")
            continue
        if link.get("index") != position:
            problems.append(f"receipts/chain.json link {position}: index out of order")
        link_relpath = link.get("path")
        recorded_sha = link.get("sha256")
        if not isinstance(link_relpath, str):
            problems.append(f"receipts/chain.json link {position}: no path recorded")
            previous_sha = recorded_sha
            continue
        link_meta = files_meta.get(link_relpath)
        if link_meta is None:
            problems.append(f"{link_relpath}: not listed in manifest.json")
            previous_sha = recorded_sha
            continue
        if link_meta.get("sha256_original") != recorded_sha:
            problems.append(f"{link_relpath}: original sha256 disagrees with chain.json")
        link_path = _resolve_in_bundle(bundle_dir, link_relpath)
        if link_path is None:
            problems.append(f"{link_relpath}: path escapes the bundle")
            previous_sha = recorded_sha
            continue
        if not link_path.is_file():
            problems.append(f"{link_relpath}: missing from the bundle")
            previous_sha = recorded_sha
            continue
        try:
            envelope = json.loads(link_path.read_text())
            statement = json.loads(base64.b64decode(envelope["payload"], validate=True))
        except (
            OSError,
            TypeError,
            KeyError,
            ValueError,
            binascii.Error,
            json.JSONDecodeError,
        ) as exc:
            problems.append(f"{link_relpath}: payload unreadable: {exc}")
            previous_sha = recorded_sha
            continue
        if not isinstance(statement, dict):
            problems.append(f"{link_relpath}: payload is not a statement object")
            previous_sha = recorded_sha
            continue
        links_checked += 1
        if statement.get("previous") != previous_sha:
            problems.append(f"{link_relpath}: previous does not match the prior link")
        run_id = statement.get("run_id")
        if isinstance(run_id, str):
            attestation_relpath = f"runs/{run_id}/attestation.json"
            attestation_meta = files_meta.get(attestation_relpath)
            if attestation_meta is None:
                problems.append(
                    f"{link_relpath}: run '{run_id}' has no {attestation_relpath} in the bundle"
                )
            elif attestation_meta.get("sha256_original") != statement.get("attestation_sha256"):
                problems.append(
                    f"{attestation_relpath}: original sha256 disagrees with the link statement"
                )
        previous_sha = recorded_sha

    return CheckResult(
        ok=not problems, problems=problems, files_checked=files_checked, links_checked=links_checked
    )
