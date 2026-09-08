# Signed lane receipts

Each run writes a signed attestation so later tools can verify the chain on
bytes.


Every spawned dispatch writes `attestation.json` beside its `result.json`: a
Dead Simple Signing Envelope (DSSE) over HMAC-SHA256, standard library only.
The signed statement carries `base_commit`, `tip_commit`, the sha256 of
`diff.patch`, the test surface's content digests before and after, and the
gate's command line, which run counted (`clean`, `own`, or `none`), its exit
code, and whether it passed. Those are exactly the fields a caller would
otherwise have to trust the fleet's own exit code and prose for: "the fleet
did X" becomes something checkable on bytes, not a claim. A dry run or a
dispatch refused before spawn writes no envelope, since nothing ran.

A mission chains every lane's envelope together: each time a lane settles,
`receipts/<index>-<lane>.json` records that lane's run id, its
`attestation.json` path and hash, and the sha256 of the previous link's own
file, so the sequence is tamper-evident end to end, not just each entry on
its own. `receipts/chain.json` is the unsigned index; `result.json` carries
`chain: {"path", "links", "head"}` and `report.md` shows one line. A resumed
mission continues the same chain (`previous` points at the last link already
on disk); a kept lane never reruns and keeps whichever link it already
earned, so it appears once, not twice.

```
conductor attest MISSION_ID
```

walks `chain.json` in order and verifies every link's signature, that its
file matches the hash recorded in `chain.json`, that its `previous` field
matches the prior link's actual file hash, that the signed statement names
the mission actually being attested and the position it actually sits at,
and, for a link with a run, that the run's own `attestation.json` still
matches that recorded hash and still agrees with `result.json` and
`diff.patch`. It prints one JSON object naming every link's verdict and
problems, exits 0 only when the chain's whole state is `verified`, 1 for
every other state -- a chain whose every link verifies but which is
truncated (`partial`) or has nothing to be measured against (`unrecorded`)
exits 1 too -- and 3 when the mission, its chain, or the signing key does
not exist.

A valid prefix of a chain is not a complete mission, so the report also
carries a `state`: `verified`, `unrecorded`, `partial`, `empty`, `missing`,
`malformed`, or `failed`. `unrecorded` is every link verifying while
`result.json` recorded no chain to compare against, so completeness was
never checked; it is never `verified`. `all([])` is True, so an emptied `links` list and a chain with
its tail lopped off both used to verify with nothing, or almost nothing,
checked; the mission's own `result.json` records `chain: {"links", "head"}`,
and a chain whose length or head disagrees with that record is `partial`,
with `link_count`, `expected_link_count`, `head` and `expected_head` beside
it. An empty chain is `empty`. Binding each statement to the requested
mission closes the other half: a chain.json lifted from another mission used
to chain cleanly on `previous` alone, and the report took that file's word
for which mission it described. `verified` is now exactly `state ==
"verified"`, and `conductor land` refuses anything else.

The key lives at `$CONDUCTOR_HOME/keys/receipt.key`, directory mode 700, file
mode 600, created on first use and never copied into a receipt. This is
HMAC, not a keypair: whoever can read that file can forge a receipt with it,
so the trust boundary is the file's permissions, not cryptography an
attacker without the key could break. What it buys is narrower and still
real — a receipt cannot be edited after the fact by anything that does not
hold the key.

Evidence (`docs/ROADMAP-2026-09.md` item A5): "Bernstein's signed replay
receipts, the IETF signed action receipts draft
([draft-marques-asqav-compliance-receipts](https://datatracker.ietf.org/doc/html/draft-marques-asqav-compliance-receipts-08))."

Every receipt also carries `fleet_version` (E22): the fleet binary's own
`--version` output, captured once before spawn from `fleets.cli_version`
(cached per process by binary path) and written to `result.json` and into
the signed statement alike. Conductor asserts vendor-specific behavior at a
point in time -- D2's tool deny list, D3's inline personas, the Cursor
stream-json parser, agy's status-not-exit-code rule -- and a silent CLI
release can move under any of it; `fleet_version` turns "which build ran
this" from a guess into something on the receipt. It is `null` when the
binary is not installed, exits non-zero on `--version`, or times out, and in
that case `git_verdict.notes` carries one line, `fleet version unavailable`;
a receipt written before this field existed reads back as `null` with no
note, since nothing rewrites old receipts. A version capture never fails the
dispatch and never delays it past its own short timeout.

