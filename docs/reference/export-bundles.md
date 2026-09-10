# Export bundles

A mission can be exported as a portable bundle of receipts, diffs, and answers.


A mission's signed receipts live under `$CONDUCTOR_HOME`, and `conductor
attest` needs that home and its key to check them. A reader outside this
machine has neither, so E13 adds a bundle everything they need to audit a
mission travels in, with a manifest that says exactly what can and cannot be
re-verified from it.

```
conductor export MISSION_ID --out DIR [--logs]
```

writes `DIR/`: every mission-directory file (`mission.json`, `result.json`,
`report.md`, `pause.json`, `lanes/`, `receipts/`, `diffs/`, `answers/`,
`asks/`, `verdicts/`, `deliverables/`, `tally.*`, each only when it exists),
and `runs/<run id>/` for every run id the receipt chain or any lane's
attempts name -- `result.json`, `attestation.json`, `diff.patch`,
`prompt.txt`, `answer.txt`, and `argv.json` always, `stdout.log` and
`stderr.log` only with `--logs` (the bulk of a bundle's size, and nothing
the manifest checks anything against). Every file passes through C7's
scrubber (`golden.scrub_text` / `golden.scrub_json_text`), the conductor
home, the mission's `cwd`, and the user's home becoming placeholders. A DSSE
envelope -- every receipt link and every run's `attestation.json` -- carries
its statement as a base64 payload, opaque to a plain-text scrub: it is
decoded, scrubbed as JSON, and re-encoded, with `signatures` left exactly as
they were. That means the envelope's signature deliberately no longer
verifies against its re-encoded payload -- the same trade every scrubbed
secret makes, made explicit in `manifest.json` rather than left for a reader
to discover by trying to verify one. `receipts/chain.json`'s own `path`
fields and a link statement's `attestation_path` are rewritten to
bundle-relative paths after scrubbing, so they still point somewhere real
inside the bundle instead of at a placeholder. After writing, `export` runs
`golden.scrub_guard` over the whole bundle; a finding removes the bundle and
refuses the export, exactly as `golden record` refuses a leaking fixture.

Before scrubbing anything, `export` verifies the mission on bytes with the
exporting machine's own key -- the same checks `conductor attest` runs, for
the chain and, individually, for every run's attestation -- and records the
verdict in `manifest.json`: `chain.state` and `chain.verified_at_export`,
`chain.problems`, one row per link (`index`, `lane`, `run_id`, `verified`,
`problems`), and one entry per run id under `attestations`
(`verified_at_export`, `problems`). `chain.state` is the same state string
`conductor attest` reports, from the same validator: a mission with no
chain.json at all used to export as `verified_at_export: true`, because the
only thing that could contradict it was a chain file that existed and was
bad. `missing`, `malformed`, `empty`, and `partial` each say so now, and
`verified_at_export` is exactly `state == "verified"`. `conductor export`
prints the state alongside the boolean, and `ExportResult` carries it as
`chain_state_at_export`. `files` lists every
bundled file by its bundle-relative path with three numbers: `sha256` (the
scrubbed bytes actually in the bundle), `sha256_original` (the file's digest
on the exporting machine, before scrubbing), and `bytes`. `verifiable_here`
names what a reader can check from the bundle alone -- file digests and the
chain's linkage; `not_verifiable_here` names what they cannot -- signatures,
and, since W9, completeness (the manifest proves the files it lists are
unchanged, never that nothing was omitted). `note` says why: the receipt
key is a shared secret (`attest.py`), so a bundle a third party could
verify standalone would be a bundle that shipped the key that forges
everything. Verifying it once, here, with the key, and shipping the
verdict instead of the key is the whole design. A `scope` object records
what the bundle actually holds and why: `run_ids` (how many run ids each
of `from_lanes`, `from_chain`, and `from_snapshot` contributed, and their
union's `total`), `missing_run_dirs` (a run id named by a receipt, a chain
link, or the mission's own snapshot whose directory was never found under
`runs/`), `mission_files`/`mission_subdirs` (which of `MISSION_TOP_FILES`/
`MISSION_SUBDIRS` were actually copied), `run_files` (the run file names a
copied run may carry, logs included only with `--logs`), and `omitted` (a
fixed list of plain sentences naming what a bundle never holds at all --
logs without `--logs`, `running.json`/`liveness.json`, a run directory
never found, worktrees and branches, the receipt key, and any run this
mission's receipts, chain, and snapshot never named). A manifest written
before W9 carries no `scope` at all.

```
conductor export --check DIR
```

reads only `DIR` -- never `$CONDUCTOR_HOME`, never the key -- and confirms
the manifest still describes the bundle sitting in front of it: every listed
file exists at its recorded `sha256` and `bytes`, no unlisted file is
present, `receipts/chain.json`'s links are in index order, each link file's
`sha256_original` matches what `chain.json` recorded for it, each
statement's `previous` matches the prior link's recorded hash (`null` on the
first), and each statement's `run_id` has a `runs/<run id>/attestation.json`
in the bundle whose `sha256_original` matches the statement's own
`attestation_sha256`. It says nothing about signatures -- that would need
the key -- and exits 0 when every check passes, 1 otherwise, printing
`not_verifiable_here` alongside whatever it found. When the manifest
carries a `scope` (W9), `--check` also prints `omitted:` followed by each
of its sentences; a manifest recorded before `scope` existed is read
exactly as before, with no `omitted:` line.

