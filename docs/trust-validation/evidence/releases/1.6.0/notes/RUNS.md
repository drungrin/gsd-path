# Live release evidence — 1.6.0

## 2026-10-11 evaluation

Candidate: `2aad06114ebf7d4b88ba70d3de63627e4c238c2f`, main after the 1.6.0
version change (PR #405). `check_trust_evidence.py --plan` required all seven
hosts and reused no earlier receipt, because product scripts changed after the
1.5.0 candidate. All seven hosts completed a fresh native milestone, guard
checks, archive, local-origin merge and tag. All seven receipts validated
individually. Each fresh clone of its local origin `main` passed the external
CLI oracle: 6/6 per host, 42/42 total. The candidate source was not changed
during the runs. No attempt was invalid.

Raw native runs, owner replies, usage fields, oracle results and guard probes
are retained locally at `~/orca/evaluations/gsd-path-release-1.6.0-2aad0611/`.
`RUN-STATUS.md` there records each host result and the evaluator brief.

| Host | CLI version | Model | Integration commit | External oracle |
|---|---|---|---|---|
| Codex | 0.157.1 | gpt-6-astra | `2715b2615182158f5409ef0c8da62028772c067b` | 6/6 |
| Claude Code | 2.1.294 | claude-opus-5-5 | `39afc5d3d4249b5e01f2db0e6ac4c244c3daede5` | 6/6 |
| Grok | 1.0.50 | grok-4.7-build | `b151afe71abdfcb29f46a530847ce3664a228b55` | 6/6 |
| OpenCode | 1.18.25 | glm-5.3 | `ce844d68de1dac7b230eee1fad7d65bc47b7ebed` | 6/6 |
| Antigravity | 1.3.3 | not reported by the host stream | `0e558fbb87c45f28d9a958991d168b9d6bed03be` | 6/6 |
| Cursor | 2026.10.01-e373342 | Composer 2.5 | `b46af1c40d09dd8cebc3ca1010cfedb02c0d4181` | 6/6 |
| Kimi | 0.43.0 | kimi-code/k3 | `a12bc1fbf40d7b59ea3532ef60e006763929cd9a` | 6/6 |

Claude Code and Cursor also passed the native guard probe: a read and an
ordinary shell command were allowed, and an edit of a committed archive file
was refused with the file hash unchanged. Every host passed the Git-hook
check: a commit that changes a committed archive was blocked, and a commit
outside the archive passed.

### Usage

Owner ruling (2026-10-07): **"allow and record"**. Token overages did not stop
these runs. Counts below use the fields each host exposes; their scopes differ
and are not a billing comparison.

| Host | Reported output tokens | Scope |
|---|---|---|
| Codex | 32,669 | cumulative parent-session field (3,791 reasoning); four children under 5,000 each |
| Claude Code | 53,367 | sum of per-invocation parent usage; cumulative model usage including children: 89,209; guard probe session 1,367 |
| Grok | 226,975 | cumulative parent field, 162,444 of it reasoning; launch 64,941 and build 91,563; review child 33,844; the host does not state if the parent field includes children |
| OpenCode | 33,315 + 24,037 reasoning | sum of parent `step_finish` fields; five children under 8,000 output each |
| Antigravity | 95,557 | running parent conversation total (52,756 thinking reported separately); build child 36,214; review child 34,495 |
| Cursor | 51,893 | sum of seven per-invocation fields on one session; child Task usage is not reported separately; guard probe session 804 |
| Kimi | 107,974 | whole session: main 58,543, four children 49,431 |

No single invocation of Codex, Claude Code, OpenCode, Antigravity or Cursor
was over 30,000 output tokens. Grok (launch and build) and Kimi (initial and
build, with children) had invocations over 30,000.

### Owner gates

No pre-approval existed for any host. Each evaluator reviewed the canonical
intent and plan before it answered a gate.

- Codex, Claude Code, Antigravity and OpenCode: the first INTENT missed
  required criteria (all `int()` input forms, exact output bytes, or failure
  for unknown flags and extra positionals) and was corrected one time before
  approval. Cursor needed two corrections.
- Grok and Kimi stopped with a define question before they wrote INTENT. The
  owner gave the input forms and failure rules there, and the INTENT needed
  no correction.
- No plan needed a correction. No plan had a Verify command that leaves the
  repository.

## Hardening gaps and oddities (none is a candidate defect)

- Kimi ran `workflow_run.py prepare-task`, plan approval, state transitions
  and checkpoints through the evaluator `activity` wrapper in the build turn.
  The wrapper only runs the command from the primary repository and records
  the time. Task Verify, project Verify and the review sidecar helpers ran
  directly. The attempt was kept valid.
- The harness prompt still tells hosts to use the `activity` wrapper, which
  conflicts with the evaluator rule against it for Verify and sidecar work.
- Cursor's CLI stalled for about 19 minutes after the build work was complete:
  a read-only `wc -l` hung in the host's shell wrapper until the evaluator
  ended the orphan process. The run then ended with exit 0. No evidence was
  touched.
- The native Bash guard refused read-only commands that the host then wrote in
  a simpler form: on Claude Code a write through a shell-variable path, an
  `rm -f` inside a compound, and a `git log --format=…`; on Cursor, after
  integration, a `python3 -c` path inspection and
  `git log --format='%H%nP%n%B'`.
- Cursor's first ship commit was refused by the commit-msg guard (subject and
  body not canonical). The host corrected the message file and committed again
  with the hooks on.
- OpenCode's first `prepare-final` refused reuse because the wave review did
  not cite two held-out edges. The host dispatched an independent final
  review and did not edit the wave review.
- The serial Task Verify record names the primary checkout as `worktree` while
  `location` names the sidecar (Claude Code, Codex, Grok).
- `archive_milestone.py integrate` does not print the integration worktree
  path; hosts and evaluators derived it from the runtime's placement rule.
- Docs disagree on task-name case (`build_T001` and `build_t001`). In each run
  the dispatched label and the task `agent` field match.
- The Git pre-commit guard refuses a commit in a repository without a staged
  `.project/STATE.md`; evaluator reference repositories made their setup
  commit before the hooks were copied, or with `--no-verify`.
- Several hosts wrote the ship commit message to a fixed name in the shared
  `/tmp` (outside `.project`, as required). Parallel runs could collide on the
  name; no collision occurred.
- Kimi reported the evaluator's session id at the archive checkpoint
  (inherited environment). Receipts use the ids from the host streams.
- OpenCode loads MCP servers from the user configuration under `--pure`; one
  wrote ignored `.memdb/` and `.memtrace/` folders into the fixture.
- The pipeline has no separate ship-approval record: the STATE log goes from
  "final gate passed; shipping approval pending" to "shipment recorded".
