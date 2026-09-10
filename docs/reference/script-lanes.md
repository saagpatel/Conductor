# Script lanes (E6)

A lane can be a shell command instead of a model, typically as a build that a
review lane then reads.


`fleet: "script"` runs a shell command in the lane's worktree instead of a
model: same receipt, timeout, bytes verdict, gate, clean gate, commit, stage
semantics (reproduce gate included), and graph ordering as a model lane, and
costs nothing in a way the ledger can verify rather than fail closed.

```json
{
  "prompt": "lint, then review the result",
  "lanes": [
    {
      "name": "lint",
      "fleet": "script",
      "command": "ruff check src && ruff format --check src",
      "stage": "build",
      "no_op_ok": true
    },
    {
      "name": "review",
      "fleet": "claude",
      "mode": "read",
      "stage": "review",
      "needs": ["lint"],
      "prompt": "the lint lane's own output: {{lanes.lint.answer}}"
    }
  ]
}
```

`command` is required on a script attempt (primary, fallback, or cascade)
and refused, by name, on every other fleet's. `prompt`/`prompt_file` are
optional — a script's work is its command, not its ask — and, when given,
delivered on the process's stdin (an empty prompt just closes stdin at
once). `mode` defaults to `write` rather than `read` (a script lane is
usually the work, not the review of it) and may be set to `read`. `stage`
may be any of `build`, `review`, `fix`, or `adversarial`, under exactly the
same mode rule and reproduce-before-fix gate a model lane gets. `needs`,
`base`, `commit`, `test_policy`, `test_surface`, `deliverable`, `setup`,
`teardown`, `ports`, `include`, `timeout`, `branch`, and `cwd` all work as
today. `fallback` may name a script or model attempt interchangeably, so a
model lane may fall back to a script command and a script lane may fall
back to a model.

`cascade`, `effort`, `model`, `cap_usd`, `cap_grace_usd`, `schema`,
`verdict`, `agent`, and `taint` are refused on a script lane or attempt,
named, at load: there is no effort dial, no model but `sh`, and no tool
surface for a persona, structured output, or a deny list to apply to.
`resume` is refused the same way, on a script lane's own `resume` and on
any other lane naming a script lane as `resume` — a shell command holds no
fleet session to continue. Because a script attempt never carries
`cap_usd`, a mission's `defaults.cap_usd` is not inherited onto one either
(it would be refused at dispatch); a mission-wide `max_cost_usd` still
bounds every model lane. `policy` may name `script` as an allowed vendor
for a stage, and judge hygiene treats it like any other vendor: a
`stage: review` script lane based on a `stage: build` script lane is
flagged the same way two lanes on any other shared vendor are.

There is no stream for a breaker to read: `stall_timeout`, `loop_limit`,
`max_tool_calls`, and `tool_idle_timeout` are forced to 0 (disabled) on
every script dispatch, and no cap watcher runs either. A non-zero exit
fails the lane as `exit code N`, the same as any other fleet; a write lane
that moves no bytes and a read lane that moves any both fail as they
always do.

A script run is priced at zero **and verified**, not left unpriced: its
receipt's `budget` block reads `{"cap_usd": null, "free": true, ...}`
(everything else the block always carries alongside those two), its
`cost_usd` is `0.0`, and `Result.failure()` never fails it for landing
"unpriced" — it never does. The mission ledger counts it as $0 spent, never
toward `unverifiable`; `conductor spend` and `conductor report` count it as
an ordinary priced $0 run, not a skipped or unpriced one.

