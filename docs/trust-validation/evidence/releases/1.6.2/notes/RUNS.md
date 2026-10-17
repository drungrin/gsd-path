# Live release evidence — 1.6.2

## 2026-10-16 evaluation

Candidate: `c14248da2eb5db1362175f50382af8ecb9fd1bc3`, main after the 1.6.2
version change (PR #433). `check_trust_evidence.py --plan` required all seven
hosts and reused no earlier receipt, because product scripts changed after the
1.6.1 candidate (#428, #429). All seven hosts completed a fresh native
milestone, guard checks, archive, local-origin merge and tag. All seven
receipts validated individually. Each fresh clone of its local origin `main`
passed the external CLI oracle: 6/6 per host, 42/42 total. The candidate
source was not changed during the runs. No attempt was invalid.

Raw native runs, owner replies, usage fields, oracle results and guard probes
are retained locally at `~/orca/evaluations/gsd-path-release-1.6.2-c14248da/`
(brief, `RUN-STATUS.md`, pinned candidate, and one harness per host).

| Host | CLI version | Model | Integration commit | External oracle |
|---|---|---|---|---|
| Codex | 0.157.1 | gpt-6-astra | `24fcadccadbac0da983e6b0713841db26bdd518a` | 6/6 |
| Claude Code | 2.1.294 | claude-opus-5-5 | `a06d4d5cb36fed5f7f38f6f818a54744429f8485` | 6/6 |
| Grok | 1.0.62 | grok-4.6-build | `0107341e33ca16712b29c55d962c639069e1fdf2` | 6/6 |
| OpenCode | 1.18.25 | glm-5.3 | `5ec609c2e4f6dfd590e47153fb83966c803ac4a4` | 6/6 |
| Antigravity | 1.4.0 | not reported by the host stream | `1a6d9e17180b1d26f67b39435222fd6eb0e372f9` | 6/6 |
| Cursor | 2026.10.16-e3cb207 | Composer 2.5 | `7bdd8088a07478d0e06f711f44832e29e8f61bf9` | 6/6 |
| Kimi | 0.43.0 | kimi-code/k3 | `b6c7fccb744e238680a75eaf683632cc0c6e3a78` | 6/6 |

Cursor updated itself from 2026.10.14-941492e just before its session; the
evaluator re-ran the native guard probe and the manifest phase on the new
version. Grok's model is the one its session `usage.json` reports.

Claude Code and Cursor also passed the native guard probe: a read and an
ordinary shell command were allowed, and an edit of a committed archive file
was refused with the file hash unchanged. Every host passed the Git-hook
check: a commit that changes a committed archive was blocked, and a commit
outside the archive passed.

### Changes since 1.6.1 seen in the runs

- #428: on every host the first `prepare-final` reused the wave review as the
  final review, with no `review_final` dispatch. In 1.6.1 Claude Code,
  OpenCode and Kimi needed an extra final review here. A repeated
  `prepare-final` at the same HEAD reused the receipts and left the ledger at
  two entries.
- #429: no host made a commit, checkpoint, reset or checkout between
  `prepare-final` and `archive_milestone.py prepare`, so the new refusal was
  not exercised live.
- The evaluator brief added a wrapper rule for every host. No host ran a
  gsd-path helper through the evaluator `activity` wrapper (OpenCode did in
  1.6.1).

### Usage

Owner ruling (2026-10-07): **"allow and record"**. Token overages did not stop
these runs. Counts below use the fields each host exposes; their scopes differ
and are not a billing comparison.

| Host | Reported output tokens | Scope |
|---|---|---|
| Codex | 35,963 | cumulative parent-session field (4,175 reasoning); four children under 4,500 each |
| Claude Code | 52,316 | sum of per-invocation parent usage; cumulative model usage including children: 72,592; guard probe session 1,216 |
| Grok | 144,221 | parent session total, 69,948 of it reasoning; launch 30,743 and build 73,983; children under 25,000 |
| OpenCode | 32,480 + 19,359 reasoning | sum of parent fields over six runs; four children under 7,000 output each |
| Antigravity | 109,999 | parent conversation total (51,486 thinking reported separately); build turn 64,181; children under 9,300 |
| Cursor | 39,758 | sum of six per-invocation fields on one session; child Task usage is not reported separately; guard probe sessions 1,330 and 928 |
| Kimi | 110,469 | whole session: main 56,697, four children 53,772 |

No single invocation of Codex, OpenCode or Cursor was over 30,000 output
tokens. Claude Code's build invocation was 32,816 counted with its children.
Grok (launch, build), Antigravity (build) and Kimi (initial and build, with
children) had invocations over 30,000.

### Owner gates

No pre-approval existed for any host. Each evaluator reviewed the canonical
intent and plan before it answered a gate.

- Codex, Claude Code, Grok, Antigravity and Cursor: the first INTENT missed
  required criteria (all `int()` input forms, exact output bytes, or failure
  for unknown flags and extra positionals) and was corrected one time before
  approval.
- OpenCode and Kimi stopped with a define question before they wrote INTENT.
  The owner gave the input forms and failure rules there, and the INTENT
  needed no correction.
- No plan needed a correction. No plan had a Verify command that leaves the
  repository.

## Hardening gaps and oddities (none is a candidate defect)

- Grok 1.0.62 in headless mode did not exit after its launch turn: a queued
  background-command wake prompt stayed blocked for 13 minutes. The evaluator
  sent SIGINT and resumed the same session; later invocations exited normally.
- Grok first spawned the coder with a foreign agent type (`gsd-executor`),
  killed it after 7.7 s with no changes, and respawned it correctly. Only the
  respawned child landed.
- Claude Code scheduled a wakeup during inspect and idled about 18 minutes
  before a wakeup turn re-reported the gate.
- In launch turns, before the first owner reply, Codex, Grok and
  Antigravity's inspect child ran a few read-only non-helper commands through
  the `activity` wrapper, as the harness prompt says. The harness prompt still
  tells hosts to use the wrapper.
- Cursor still wraps helpers as `cd <repo> && helper`; the native guard
  refused that form in archive context and the host retried without `cd`.
  Its first ship commit message was blocked by the commit-msg guard and
  fixed with the hooks on.
- Several hosts still chain commands with `&&` and `;` despite the owner rule;
  no helper was skipped.
- The native Bash guard refused read-only compound commands after
  integration on Claude Code and Cursor; the hosts retried or skipped them.
- `archive_milestone.py integrate` does not print the integration worktree
  path; hosts and evaluators derived it from the runtime's placement rule.
  Cursor named it `integrate/count-cli-json`; the rule gives `integrate/M001`.
- The serial Task Verify record names the primary checkout as `worktree` while
  `location` names the sidecar.
- Several hosts wrote temporary files to fixed names in the shared `/tmp` or
  `$TMPDIR`, outside `.project` as required. No collision occurred.
- Cursor and OpenCode cannot see their own session id from the shell; receipts
  use the ids from the host streams.
- OpenCode loads MCP servers from the user configuration under `--pure`; one
  wrote an ignored `.memdb/` folder into the fixture. OpenCode also read the
  user-global installed plan skill before the pinned bundle (read only).
- Kimi dispatched its wave reviewer with `subagent_type: coder`; it offers no
  reviewer type.
- The Git pre-commit guard refuses a commit in a repository without a staged
  `.project/STATE.md`; evaluator reference repositories made their setup
  commit before the hooks were copied, or with `--no-verify`.
- The pipeline has no separate ship-approval record: the STATE log goes from
  "final gate passed; shipping approval pending" to "shipment recorded".
