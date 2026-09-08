# Pipelines: build, then independent review, then fix

Lanes can depend on each other, reuse a thread, and share a cache-stable prompt
prefix.


Lanes can depend on each other. Three lane fields make a flat fan-out a
pipeline; everything else is unchanged.

```json
{
  "name": "parser-refactor",
  "cwd": "~/Projects/thing",
  "prompt_file": "spec.md",
  "max_cost_usd": 10,
  "lanes": [
    {"name": "build", "fleet": "codex", "model": "sol", "mode": "write",
     "test": "pytest -q", "commit": "feat: refactor parser per spec"},
    {"name": "review", "fleet": "claude", "model": "opus", "mode": "read",
     "base": "build",
     "prompt": "Review this change against the spec.\n{{mission.prompt}}\n{{lanes.build.diff}}\nReport anything that could cause incorrect behavior, a test failure, or a misleading result; omit style and naming. Per item: file and line, what goes wrong, one sentence of consequence, confidence 1-10. If nothing meets that bar reply exactly NO_FINDINGS. Either answer is complete. Put the entire review in this reply."},
    {"name": "fix", "stage": "fix", "fleet": "codex", "model": "sol", "mode": "write",
     "base": "build", "needs": ["review"], "resume": "build", "no_op_ok": true,
     "test": "pytest -q", "commit": "fix: address cross-vendor review",
     "prompt": "A reviewer from another vendor reported:\n{{lanes.review.answer}}\nFor each item, first reproduce it (a failing test or a demonstrated wrong result); fix only what reproduces. If the review says NO_FINDINGS or nothing reproduces, change nothing and say so."}
  ]
}
```

Review prompts carry no quota. "Find the defects" manufactures findings and
"be conservative" makes current models silently drop real ones, so every
review, judge, and collate prompt states a consequence threshold, says that an
empty result is a complete answer, asks for a citation and a confidence per
item, and demands the whole review in the final reply. The rules and their
evidence are in `AGENTS.md`.

- `needs`: lanes that must have ended **ok** before this one starts. If one
  of them did not, this lane is skipped with the reason, and so is anything
  behind it, immediately. Cycles, unknown names, and self-needs are refused
  at load.
- `base`: the lane whose final **commit** this lane's worktree starts from.
  `base` implies `needs`. Only committed work can be built on: a lane that
  left uncommitted edits in a kept worktree, or was not isolated, cannot be
  a base, and the dependent is skipped saying so. A based dispatch is always
  isolated and is refused (in read mode too) if its worktree cannot be made,
  because reviewing HEAD instead of the build would be reviewing the wrong
  code. Its `diff.patch` is against the base's tip, and the report says so.
- `no_op_ok`: a write lane that may legitimately change nothing (the fix
  step when the review found nothing). It waives only the no-op and the
  "nothing to commit"; a failed gate, a fleet error, or an over-cap run
  still fails. A clean no-op lane can itself be a base.

## Thread reuse

A lane can set `resume` to a lane in its `needs` list or to its `base`. When
the upstream lane's final attempt used the same fleet and recorded a session
id, conductor resumes that session instead of paying for the repository
context again. The build → review → fix example above resumes `build` on
the `fix` lane while still waiting for the independent `review` lane:

```json
{"name": "fix", "fleet": "codex", "base": "build",
 "needs": ["review"], "resume": "build"}
```

Session reuse is same-fleet only. A different fleet, or an upstream receipt
without a session id, runs fresh and records why. Every resumed dispatch also
asserts that the fleet returned the requested id; a missing or different id
makes the attempt fail and its fallback starts fresh. This guard is essential
for Antigravity: `agy --conversation MISSING` warns only on stderr, starts a
new conversation, and exits 0.

Prompt templates: `{{lanes.<name>.answer}}`, `{{lanes.<name>.diff}}`,
`{{lanes.<name>.test_touched}}` (`yes (n files: ...)` or `no`),
`{{lanes.<name>.verdict}}`, `{{lanes.<name>.deliverable}}`, and
`{{mission.prompt}}` (the mission-level prompt,
verbatim). A referenced lane
must be in `needs`; anything else between double braces is refused at load,
so a misspelt name cannot render as `(none)`. Rendering is a single pass, so
braces inside an upstream answer never become new substitutions; each pasted
lane value is fenced with a per-render random nonce and labelled as another
agent's output, not instructions. The trusted mission prompt is substituted
unfenced and outside `template_max_chars`; only lane data shares that budget
(default 40000). In a dry run lane placeholders render as `(dry run: ...)`.

## Cache-friendly prompts

Every Claude dispatch, read and write, gets `--system-prompt-snapshot on`
(record the system prompt once per conversation and reuse it verbatim on
every request and resume) and `--exclude-dynamic-system-prompt-sections`
(move cwd, env info, memory paths, and git status out of the system prompt
into the first user message, so the system prompt is cache-stable across
machines and directories). Neither flag changes what the model is asked,
only what gets cached. Evidence: moving dynamic content after the static
prefix took one production hit rate from 7% to 84% for a 59 to 70% cost cut
([Don't Break the Cache](https://arxiv.org/pdf/2601.06007); see
`docs/ROADMAP-2026-09.md` item B2). We already saw the reverse: operator
hooks injecting per-lane context made a $0.05 reply cost $0.24.

A mission may also set a top-level `"prefix"` or `"prefix_file"` (mutually
exclusive, resolved like `prompt_file` relative to the mission file): a
static block of text every dispatched prompt in the mission starts with --
each lane attempt's rendered prompt and the collate's, prefix then a blank
line then the rest. Identical leading bytes are what a prompt cache needs to
hit: two lanes whose prompts diverge on the first line never share a cache
entry, however similar the rest is. The prefix is static by definition, so a
`{{` template reference inside it is refused at load (`prefix must not
contain template references`); it composes with `{{mission.prompt}}` by
sitting in front of the fully rendered prompt, mission prompt included.
`prompt.txt` in each run directory shows the full prompt, prefix and all,
and `template_max_chars` bounds the prefix and the pasted template content
together.

