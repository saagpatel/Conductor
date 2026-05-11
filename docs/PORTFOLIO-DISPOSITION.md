# Conductor — Portfolio Disposition

**Status:** Active — Phase 2 (real JSONL log parsing) complete, no
release-readiness work yet. Disposition is **not** Release Frozen; the
gate isn't Apple signing, it's "decide whether to package this for
distribution at all."

> Disposition uses strict `origin/main` verification.

---

## Verification posture

This disposition reads `origin/main` (saagpatel) only. There is
**no** `legacy-origin` remote configured on this repo — the project
has a clean account-migration state, unlike most of the other Vanity
or Money-bucket repos. That's a relief: no risk of the
`legacy-origin`/`origin` misread that produced bad dispositions
earlier in the session for FreeLanceInvoice and PersonalKBDrafter.

Specifically verified:
- `origin/main` tip: `8ed5d59` chore: add initial CHANGELOG
- Last substantive feature commit on `origin/main`:
  `16f0578 feat: implement Phase 2 real JSONL log parsing`
- Source tree: `Conductor.xcodeproj`, `Conductor/`,
  `ConductorTests/`, `project.yml` (XcodeGen), 58 source files
- Default branch: `main`

---

## Current state in one paragraph

Conductor is a native macOS Swift app that visualizes Claude Code
agentic workflows as interactive force-directed node graphs. It
parses real JSONL session logs from `~/.claude/projects/`, builds a
delegation tree of message → tool call → subagent edges, and
exposes a three-panel session-browser + graph + detail-inspector
UI. XcodeGen is used to drive `project.yml` → `Conductor.xcodeproj`.
The Phase 2 work that landed put the real JSONL parsing in place;
prior work was Phase 1 scaffolding plus the GitHub Actions CI
workflow.

For full detail see `README.md`.

---

## Why "Active" instead of Release Frozen

Other repos in this round (DesktopPEt, ContentEngine, AIGCCore,
Relay, FreeLanceInvoice, Nexus) ended up in the Release Frozen
cluster because each of them has shipped a release-readiness
artifact:

- A release runbook
- A signing/notarization checklist
- Hard gates that define "what passes counts as ready"

Conductor has **none of those**. It has source code, tests, and CI,
but no release-readiness doc. There is no document anywhere in the
repo that says "what would it take to package this for someone else
to use." That doesn't mean the answer is hard — Swift apps with
XcodeGen are mechanical to package — but it means the
release-readiness packet hasn't been written yet.

So the right disposition is:

- **Active** — the next move is to decide whether to package this at
  all, then (if yes) write the release-readiness packet.
- Not Release Frozen — there's no readiness doc to gate.
- Not Cold Storage — the source is functional.
- Not Archived — Phase 2 was recent (last meaningful commit on
  `origin/main` is `16f0578`).

---

## Possible next moves (operator choice)

There are three reasonable next moves. The choice is operator-level:

### Option 1 — Package for distribution

The natural next phase if Conductor has audience. Required scope:

- Write `docs/RELEASE-READINESS.md` (akin to other macOS repos)
- Wire `xcodebuild` signing + notarization via CI
- Cut a v0.1.0 release
- Then transition disposition to `Release Frozen` and join the
  signing cluster (which is now 6 repos)

Estimated effort: ~6 hours including signing and first
notarization round-trip.

### Option 2 — Open-source as a tool, no packaged binary

Keep `main` public, document the local-build workflow well, do
nothing about signing. Users who want it run `xcodegen generate`
and build it themselves. Disposition stays `Active` and the row
shows up only when meaningful product work happens.

Estimated effort: ~1 hour, mostly polishing the README "How to
build" section.

### Option 3 — Mark as personal-use tool

Decide that the audience is just the author. Move disposition to a
shape similar to job-search-2026 ("Not a portfolio item, exclude
from scans"). Keep the repo public for transparency, but stop
treating it as something with a release path.

Estimated effort: ~30 minutes, document and move on.

The portfolio operating system can't make this choice — it's a
product decision about the value of Conductor specifically.

---

## Portfolio operating system instructions

| Aspect | Posture |
|---|---|
| Portfolio status | `Active` |
| Next packet shape | "Decide between Option 1 / 2 / 3 above" |
| Review cadence | Resume normal cadence — this row needs decision-time |
| Resurface conditions | Once the operator picks an option, surface a packet for the work each option implies |
| Do **not** auto-add to signing cluster | The cluster is for repos that already have release-readiness docs. Conductor doesn't — adding it would bring an unrelated decision into a signing session. |

---

## Reactivation procedure (for the next code session)

1. Verify `git branch -vv` shows `main` tracking `origin/main`
   (this repo doesn't have legacy-origin, so the trap isn't here,
   but verify anyway as a habit).
2. Delete stale `codex/*` branches that pre-date Phase 2.
3. Re-run `xcodegen generate && xcodebuild -scheme Conductor build`
   to confirm the toolchain still works after the freeze.
4. If picking Option 1, write the release-readiness doc first.
   Don't start signing work without that doc in place.

---

## Last known reference

| Field | Value |
|---|---|
| `origin/main` tip | `8ed5d59` chore: add initial CHANGELOG |
| Last substantive commit | `16f0578` feat: implement Phase 2 real JSONL log parsing |
| Default branch | `main` |
| Build system | XcodeGen (`project.yml` → `Conductor.xcodeproj`) |
| Tests present | Yes (`ConductorTests/`) |
| CI present | Yes (commit `3534c02` added GitHub Actions CI workflow) |
| Release readiness doc | **None** — gate before joining the signing cluster |
| Migration state | Clean (no `legacy-origin` remote) |
