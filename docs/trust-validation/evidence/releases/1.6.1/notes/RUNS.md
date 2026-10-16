# Live release evidence — 1.6.1

## 2026-10-15 evaluation

Candidate: `ec116bf9af69af2a1769d73b566b53834fa4c327`, main after the 1.6.1
version change (PR #426). `check_trust_evidence.py --plan` required all seven
hosts and reused no earlier receipt, because product scripts changed after the
1.6.0 candidate. All seven hosts completed a fresh native milestone, guard
checks, archive, local-origin merge and tag. All seven receipts validated
individually. Each fresh clone of its local origin `main` passed the external
CLI oracle: 6/6 per host, 42/42 total. The candidate source was not changed
during the runs. One Cursor attempt was invalid (see below); no other attempt
was invalid.

Raw native runs, owner replies, usage fields, oracle results and guard probes
are retained locally at `~/orca/evaluations/gsd-path-release-1.6.1-ec116bf9/`
(brief, `RUN-STATUS.md`, pinned candidate) and
`~/orca/evaluations/gsd-path-release-1.6.1-ec116bf9-ec116bf9/` (harnesses).

| Host | CLI version | Model | Integration commit | External oracle |
|---|---|---|---|---|
| Codex | 0.157.1 | gpt-6-astra | `1ae2dd79f731d95d1043cfeb6e20696f430d2903` | 6/6 |
| Claude Code | 2.1.294 | claude-opus-5-5 | `e249a89f18167554ad2a452b437e37bd703d5bea` | 6/6 |
| Grok | 1.0.60 | grok-4.7-build | `cbec7bf36cbc9b431e7c55875c8b865945895532` | 6/6 |
| OpenCode | 1.18.25 | glm-5.3 | `78e3f994162b5d9ec5a586ad6010b51a30d02d2f` | 6/6 |
| Antigravity | 1.3.6 | not reported by the host stream | `490888f0c46940a7b253fb472561266256fb5ac7` | 6/6 |
| Cursor | 2026.10.14-941492e | Composer 2.5 | `6cab6868b0005b525b33eb2bc8ab0d88f0cab784` | 6/6 |
| Kimi | 0.43.0 | kimi-code/k3 | `23121a800be57bc46bdb6ff7a71ff243c3aae566` | 6/6 |

Grok and Cursor updated themselves during the round (Grok from 1.0.52,
Cursor from 2026.10.01-e373342 during its invalid attempt); the versions above
are the ones that ran the valid attempts.

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
| Codex | 38,993 | cumulative parent-session field (7,678 reasoning); four children under 4,100 each |
| Claude Code | 41,600 | sum of per-invocation parent usage; cumulative model usage including children: 91,491; guard probe session 1,397 |
| Grok | 275,085 | parent session total, 191,726 of it reasoning; launch 70,358, intent approval 36,443, build 122,182; wave reviewer child 42,981 |
| OpenCode | 34,233 + 20,325 reasoning | sum of parent fields over six runs; five children under 7,400 output each |
| Antigravity | 94,292 | parent conversation total (30,253 thinking reported separately); plan-approval/build turn 35,781; children under 11,000 |
| Cursor | 42,468 | sum of six per-invocation fields on one session; child Task usage is not reported separately; guard probe session 1,052; invalid attempt 51,641 |
| Kimi | 111,625 | whole session: main 59,487, five children 52,138 |

No single invocation of Codex, OpenCode or Cursor was over 30,000 output
tokens. Claude Code's build invocation was 33,292 counted with its children.
Grok (launch, intent approval, build), Antigravity (build) and Kimi (initial
and build, with children) had invocations over 30,000.

### Owner gates

No pre-approval existed for any host. Each evaluator reviewed the canonical
intent and plan before it answered a gate.

- Codex, Claude Code, OpenCode, Antigravity and Cursor: the first INTENT missed
  required criteria (all `int()` input forms, exact output bytes, or failure
  for unknown flags and extra positionals) and was corrected one time before
  approval.
- Grok and Kimi stopped with a define question before they wrote INTENT. The
  owner gave the input forms and failure rules there, and the INTENT needed
  no correction.
- No plan needed a correction. No plan had a Verify command that leaves the
  repository.

### Invalid attempt

- Cursor attempt 1: at the archive step the host made two
  `isolation.py checkpoint` commits of final-review outputs before
  `archive_milestone.py prepare`, against the ship skill ("final review itself
  still creates no pre-ship commit"). It re-ran project Verify, dispatched a
  final review, and tried `git reset --hard`, which the native guard refused.
  The evaluator re-prepared the harness and added a ship rule to the prompt
  and gate replies; attempt 2 went straight to `prepare`.

## Hardening gaps and oddities (none is a candidate defect)

- Only the ship skill text stops a host from committing final-review outputs
  with `isolation.py checkpoint --allow-path .project` before `prepare`
  (Cursor attempt 1).
- The first `prepare-final` refused to reuse the wave review on Claude Code,
  OpenCode and Kimi: the reviewer cited the held-out edges on the evidence
  line, but `check_handoffs.py` reads them only from the Check, Observed and
  Reference fields, while `reviewer-wave.md` says to cite them "in its
  evidence". Each host dispatched a final review the canonical way. OpenCode
  hit the same on 1.6.0.
- OpenCode ran `prepare-task`, `activate-task`, checkpoints, transitions,
  `isolation.py recover`, `check_handoffs` and `dispatch_driver complete`
  through the evaluator `activity` wrapper in the build turn. Task Verify,
  project Verify and the review sidecars ran directly. The attempt was kept
  valid, as for Kimi on 1.6.0. Antigravity's inspect children and Codex's
  launch turn also used the wrapper for read-only helpers before the first
  owner reply, as the harness prompt says. Kimi, given a stricter rule, did
  not use it.
- The harness prompt still tells hosts to use the `activity` wrapper.
- The native Bash guard refused commands that the host then wrote in a
  simpler form: on Claude Code a compound `printf` for the ship message and,
  after integration, `git fetch` and `python3 -c`; on Cursor, after
  integration, two read-only compound commands.
- Claude Code chained commands with `&&` and `;` despite the owner rule; it
  skipped no helper.
- Antigravity first looked for `workflow_run.py` in the runtime directory (it
  is a bundle-only helper) and retried from the installed skill.
- The serial Task Verify record names the primary checkout as `worktree` while
  `location` names the sidecar.
- `archive_milestone.py integrate` does not print the integration worktree
  path; hosts and evaluators derived it from the runtime's placement rule. Kimi
  named it `integrate/001`; the rule gives `integrate/M001`.
- Task-name case still varies (`build_T001`, `build_t001`); in each run the
  dispatched label and the task `agent` field match. Cursor's inspect
  redispatch descriptions were not bare logical names.
- Several hosts wrote temporary files (ship message, helper output) to fixed
  names in the shared `/tmp`, outside `.project` as required. No collision
  occurred.
- Cursor and OpenCode cannot see their own session id from the shell; receipts
  use the ids from the host streams.
- OpenCode loads MCP servers from the user configuration under `--pure`; one
  wrote an ignored `.memdb/` folder into the fixture.
- Grok's reviewer noted that the model-selection record has
  `effective_model: null` for an inherited model.
- The Git pre-commit guard refuses a commit in a repository without a staged
  `.project/STATE.md`; evaluator reference repositories made their setup
  commit before the hooks were copied, or with `--no-verify`.
- The pipeline has no separate ship-approval record: the STATE log goes from
  "final gate passed; shipping approval pending" to "shipment recorded".
- The 1.6.0 Cursor shell-wrapper stall and the Antigravity print timeout did
  not recur.
