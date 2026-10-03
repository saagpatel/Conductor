# Untrusted output: marking a lane's own output as a taint source

A lane can mark its own output as a taint source for every downstream consumer.


A lane that reads the web, or anything else outside the operator's control,
with its full tool set is not itself weakened by that -- taint restricts what
a lane may *do*, not what it may *produce*. But its answer is exactly as
untrusted as the pages it read, and a downstream lane that pastes that
answer into a build prompt should not run trusted just because the research
lane itself was never tainted. `untrusted_output` names that lane as a taint
*source*, without touching its own dispatch:

```json
{
  "lanes": [
    {"name": "research", "fleet": "claude", "mode": "read",
     "untrusted_output": true,
     "prompt": "Look up the current API for library X and summarize it."},
    {"name": "build", "fleet": "claude", "mode": "write", "needs": ["research"],
     "prompt": "Using this summary, wire up library X:\n{{lanes.research.answer}}"}
  ]
}
```

`research` runs with its ordinary tool set (no `--disallowedTools`), is not
itself `tainted`, and may hold a deliverable `branch` if nothing else taints
it. `build` references `research`'s `.answer`, so it becomes `tainted: true`
with `taint_from: ["research"]` -- exactly the propagation rule taint itself
uses (an attempt referencing `.answer`, `.diff`, `.verdict`, `.test_touched`,
or `.deliverable`, or a `resume` of the lane's session), computed in the same
load-time fixed-point propagation and folded into the same `taint_from` list (a lane
that is both tainted and untrusted-output names itself once, not twice).
`build`'s every attempt then dispatches with taint set on its `Spec`, the
same tool-deny consequences a directly-tainted lane gets.

`untrusted_output` is a boolean, refused when not one; it may be set on a
model lane or a `fleet: "script"` lane (a script's own shell output can be
exactly as untrusted as a web page), and refused on a human lane, naming the
lane -- a human lane is already `tainted` at load, and declaring it a source
on top of that would be a no-op. A `collate` whose candidate pool contains an
untrusted-output lane is refused at load off the claude and antigravity
fleets, and tainted on claude or antigravity, the same way and with the same
message as a collate over a tainted lane.

`LaneResult` and `lanes/<name>.json` carry `untrusted_output`; `report.md`'s
lane table gets an `untrusted_output` column beside `taint`, reading `yes` or
`no`; a lane tainted by referencing one still shows it in the ordinary
`taint` column's `yes (from research)` text -- there is no separate
propagation column.

