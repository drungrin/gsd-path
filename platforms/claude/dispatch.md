# Claude Code agent dispatch

Apply this contract whenever a GSD Path skill delegates work:

- Use Claude Code's `Agent` tool with `subagent_type: general-purpose`. Coders
  and multi-file producers need write capability; single-report reviewers use
  the publication procedure below. Do not use the one-shot `Explore` or `Plan`
  agents. The linked role brief supplies each role's read-only boundary.
- Supply the deterministic logical task name as the Agent `description`:
  `inspect_codebase`, `inspect_docs` (per member: `inspect_codebase_<member>`, `inspect_docs_<member>`), `docs_audit`,
  `research_<dimension>`, `decide`, `roadmap`, `plan`, `plan_patch`,
  `build_<task_id>`, `review_wave_<wave>_cycle_<cycle>` (a `deep` wave review
  appends the lens suffix `_contract` or `_adversarial`; an optional review
  panel uses `review_wave_<wave>_cycle_<cycle>_panel_<family>`; an
  optional finding skeptic uses
  `review_wave_<wave>_cycle_<cycle>_skeptic_<criterion_locator>`),
  `review_plan_panel_<family>`,
  `review_final`, or
  `review_gap_<number>`. Normalize variable parts to lowercase ASCII and
  replace non-alphanumeric runs with one underscore. Never add a random
  suffix.
- Keep the returned agent ID when the host advertises a resume facility. For a
  correction, retry, post-patch review, repeated audit, or later milestone,
  resume that child when supported; otherwise start a fresh `general-purpose`
  child. Always send the entire new self-contained brief. If a child with the
  same logical task name is still running, wait for it rather than dispatching
  a colliding target.
- Resolve the phase's linked role brief to an absolute path. Include that path
  in the prompt and require the agent to read it before acting.
- Model selection: before every child launch or continuation, follow
  [the model-policy contract](model-policy.md) and apply its recorded
  native arguments. It owns defaults, project settings, task overrides,
  capability failures, and reassignment. Pass `--host claude`. Do not ask Claude Code to create
  another worktree: GSD Path supplies the exact repository or
  linked-worktree root in the brief, and the child must work only there.
  Host isolation: none.
- Give every child a self-contained prompt with absolute input, template, and
  output paths plus its bounded responsibility. A coder prompt also names its
  isolated linked-worktree root; no child may infer the primary worktree.
- Launch independent Agent calls concurrently up to the available limit, using
  background execution when exposed by the host. Batch any remainder and
  collect every result before applying the phase gate.
- When the host exposes a blocking ask/reply channel, route a coder's
  `NEEDS-ORCHESTRATOR` question through it as a live question with the worker
  held alive for the reply, instead of block-and-redispatch. Append the
  answer to the task Log as `Orchestrator answer:` so the portable on-disk
  record stays complete; a timed-out or unavailable channel falls back to
  the portable block path.
- A child that receives a disposable worktree stages its assigned artifact
  under that root. The parent validates it and atomically transfers it to
  the canonical project path before removing the exact disposable root. A
  child never writes a disposable-review output directly into the primary
  worktree. Native review reports use parent publication below instead of
  asking the child to create a report file.
- If the `Agent` tool or full-capability `general-purpose` agent is unavailable,
  stop and report the missing capability. Do not silently collapse an
  independence boundary into the main context.

## Native review report publication

Claude Code can append a system instruction that forbids Markdown report
files and requires final-message output, even when the child has `Write`.
For native `review_plan_panel_*`, `review_wave_*` (including lenses, panels,
and skeptics), `review_final`, and `review_gap_*`, request the complete assigned
template as the child's final assistant message. Return raw Markdown only,
without fences, a path-only reply, or a second publication summary. The child
still owns every finding and verdict and performs its review in the supplied
sidecar. In these assignments, a role's instruction to write or stage its
report describes the required artifact, not a requirement to call `Write`.

After the host reports terminal completion, bind its returned child ID to
that child's recorded JSONL transcript and companion `.meta.json` assignment
metadata. Use the exact host-reported model identity. Publish through the
bundled helper, never by retyping or reconstructing the report:

```text
python3 -B <isolation.py> stage-review --repo <primary> --source <verify-sidecar> \
  --base <recorded-review-base> --branch <verify-branch> \
  --source-path <assigned-.project/review/report.md> \
  --transcript <absolute-child-transcript.jsonl> \
  --assignment <logical-review-name> --agent-id <returned-child-id> \
  --model <host-reported-model>
```

The helper takes the latest completed final response. If a completed report
already exists before a later file-only refusal, select its exact transcript
UUID with `--message-id`; do not request another review. It copies only text
blocks, preserving their UTF-8 content, and records child, assignment, model,
message ID, content hash, and `publisher: parent` in its returned receipt
outside `.project/`. Never append provenance or credits to the report body.
Replacing an admitted invalid staged report requires its recorded SHA-256
with `--expected-source`; unknown content must not be overwritten.

`status: staged` is publication only. Apply the existing mode-specific
validators and `collect-artifact` procedure before advancing. Preserve the
staging receipt alongside the native assignment's lifecycle evidence. A
native inline report's `collect-artifact` call must include
`--expected-source <stage-review-sha256>` so changed text cannot be collected
under the original reviewer's provenance. A complete inline report needs no
file-publication retry or investigation of
the host's instruction origin. Missing/truncated output, a failed or active
child, an unavailable transcript, identity mismatch, or invalid report still
blocks; a correction must address the report content through the usual review
retry rules. Never synthesize missing findings or a passing verdict.

This transport applies to native single-report review assignments only.
Coders, multi-file planners, other hosts, and owner-supplied CLI child commands
keep their existing file contracts. A timeout or cancellation never becomes
a completed review merely because partial text exists.

## Parent lifecycle

The parent owns lifecycle: wait for terminal completion, enforce timeout and
cancellation, transfer staged outputs from disposable roots, clean up every
child and temporary root before the phase gate, and never let a child delegate
another GSD Path child. A timeout or cancellation is a blocked result.
