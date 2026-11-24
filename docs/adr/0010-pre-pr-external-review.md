# Pre-PR review by an external reviewer, before final review

Status: accepted (2026-11-24), implemented in fork 1.7.1.76 after a code review
panel. Fork-only. Upstream has no external
reviewer.

Draft 1 went through a two-model review panel. Both members found that the
pre-push hook refuses publishing unshipped work under the bound branch name.
They also found an incomplete crash recovery, an archive undo blocked by an
untracked report, and a findings record the helper could not bind. Draft 2
changed publication and the fix route, and stated the missing contracts. Both
members approved draft 2 with changes. Draft 3 makes those changes:

- the cap and the counter are enforced by commands;
- the closure gate also guards archive entry;
- a rendering rule keeps reviewer text from stranding the hand-off;
- the undo restores the report;
- follow-ups get an execution-qualified identity;
- the language of the objective is locked.

## Context

Milestone PRs on GitHub may be reviewed by an external review service. When a
repository has no such reviewer, nothing reviews the PR at all. The service
exposes an MCP server, registered in the host as `revisor`, that reviews a
**published branch before its PR exists**:

| Tool | Input | Output |
|---|---|---|
| `revisar_branch` | `repo` (owner/name), `branch`, `base?`, `objetivo` (required), `branch_do_pr?` | `execucao_id`, `repo`, `branch`, `base`, `head_sha` |
| `situacao` | `execucao_id` | `situacao` (`na_fila`, `executando`, `concluida`, `falhou`, `cancelada`), `terminou`, `veredito` (suggested only), `achados_incluidos`, `erro`, `parou_por_cota` |
| `achados` | `execucao_id` (`concluida` only) | `achados[]`: `id` (`A<n>`), `item`, `severidade` (`bloqueio`, `importante`, `sugestao`, `nit`), `arquivo`, `linha`, `titulo`, `descricao`, `cenario`, `sugestao`, `suggestion`; `registro[]` |
| `responder` | `execucao_id`, `respostas[]` of `{achado, decisao: corrijo \| contesto \| follow-up, motivo}` | `respondidos[]` |

Service behavior, measured by the owner on a disposable repository:

- The review job fetches `branch` from GitHub while it runs, and records its
  registry under `branch_do_pr` (default: `branch`). GitHub only.
- The request is refused when an open PR exists from either branch. A new
  request on the same branch cancels an unfinished one. Nothing is posted to
  GitHub.
- A round takes about 9 minutes and costs USD 1.20 to 1.40 on small diffs.
  Each round reviews the whole diff against the base.
- Answers stay in the registry. `corrijo` and `contesto` leave the item open;
  `follow-up` parks it. The first review of a PR opened later from
  `branch_do_pr` adopts that registry and judges each answer in the code. It
  does not reopen what the code shows as fixed, and it does not mention the
  pre-PR. Where no reviewer watches the PR, the registry is simply unused.
- `objetivo` stands in for the PR description: the review checks the diff
  against it.

Owner rules (settled):

1. Optional. Without the MCP tools, ship continues, and the Log says why the
   pre-PR did not run. A reviewer failure (error, quota, `falhou`) does not
   block ship by itself: it becomes a warning and an owner decision.
2. A finding is not an order. Each one ends as `corrijo`, `contesto`, or
   `follow-up`, and every answer goes back through `responder`.
3. Contesting requires evidence in the repository: a file and line.
4. At most three rounds per milestone; then the owner decides. Stop earlier
   when no `bloqueio` or `importante` remains to confirm.
5. Fixes go through the pipeline: the **ordinary patch route** (owner ruling of
   2026-11-24, after the panel; the delta-wave route was dropped).
6. One mode for repositories with and without a PR reviewer: publish to
   `pre-pr/<suffix>` and pass the bound branch as `branch_do_pr`.

Facts in this repository that shape the design:

- `git_guard.pre_push_violations` lets a bound-named ref move only to or from
  its strict ship commit, with no authorization escape. Any other non-default
  ref may carry unshipped work only at a commit the project authorized under
  `refs/gsd-path/authorizations/<project>/push/<ref>`
  (`authorized_publication`). Deleting a non-bound ref is not judged. In a
  member repository, `member_pre_push_violations` accepts an authorized push to
  any ref, and only reserved prefixes (`feature/<slug>-milestone-`,
  `chore/...`) need a delete authorization. `pre-pr/` is not reserved.
- `integration.publishable_bound_branch` and `_publish_member_bound` read only
  the bound branch. A remote `pre-pr/...` ref is invisible to integration.
- `pipeline_undo` admits into an archive only the tracked inventory, plus a
  validated FINAL.md, its gaps, and PR fix lane rounds. Any other untracked
  file in `review/` makes the undo refuse.
- `lean_verification.prepare_final` may write FINAL.md as a reused or
  generated view (quick lane, retry at the same HEAD) before any reviewer runs.

## Decision

### 1. Scope and targets

`pre_pr.py targets --repo <root>` lists the repositories a round reviews:

- a single-repository milestone whose `STATE.integration` is `pull-request`
  with a GitHub.com origin (`integration.github_repository`). The branch is
  `pre-pr/milestone-<NNN>`, and `branch_do_pr` is `feature/milestone-<NNN>`;
- in a multi-repository milestone, each locked member whose integration is
  `pull-request` with a GitHub.com origin. The branch is
  `pre-pr/<slug>-milestone-<NNN>`, and `branch_do_pr` is the member bound
  branch. The base is the member's recorded `Default branch`. The coordinator
  is not reviewed, because its diff holds pipeline artifacts only.

`direct`, Azure DevOps, and `external-landing` have no target. A round is one
request per target. Inside the milestone, a finding is identified as
`<target> A<n>`, and the external `A<n>` is kept verbatim.

### 2. The sequence, and its place in ship

The pre-PR sequence of a milestone is open until it closes, and it never
reopens. It runs in ship final mode after step 3: `workflow_run.py
prepare-final` has passed the project Verify, and no gap or final reviewer has
been dispatched. Final review starts only after the sequence is closed. Every
ship entry runs `pre_pr.py status`, which returns one of:

- `closed`: continue with step 4;
- `due`: run a round (decision 4);
- `cap-reached`: three rounds concluded and the latest one leaves a `bloqueio`
  or `importante` fix to confirm. Ask the owner: close and continue to final
  review (recommended), or run one more round;
- `pending`: an attempt is in progress. Resume it (decision 5).

**The counter and the cap are enforced, not reported.** The journal keeps a
monotonic count of concluded rounds. A round with no included finding still
concludes and writes its report. `publish` refuses to start a round past the
third unless it gets `--owner-ruling <file>`, which is recorded verbatim in the
Log. A failed attempt does not count. In a round over several targets, a target
that already concluded is not requested again when a failed target is retried.

The sequence closes when:

- the latest concluded round has no `corrijo` on a `bloqueio` or `importante`
  finding (nothing left to confirm);
- there are no targets, or the session lacks the MCP tools (the reason is
  recorded); or
- the owner rules it after a failure or at the cap.

Closing writes one log-only STATE event (`pre-PR closed: <reason>`) through
`pipeline_state.py transition --set-status active`, the ship step 7 mechanism.
The journal marks the event pending first, so a retry after an interruption
writes it exactly once. Every explicit `close` needs `--owner-ruling <file>`,
read before any change. Only `status` closes on its own, when there is no
target or no MCP tool.

**Re-entry after a fix.** A round with `corrijo` blocks ship, and the patch
route runs (decision 6). When build returns `ship/active`, ship restarts at
step 1. The landing proof, member mergeability, and project Verify all run
again through a fresh `prepare-final` at the new HEAD. Then `status` decides
whether the next round is due. The cost is one more project Verify per fix
round.

**Stale generated outputs.** When a round blocks ship, `pre_pr.py finish`
removes an uncommitted `review/FINAL.md` and `review/final-gap-*.md` whose
`Reviewed HEAD` equals the round's reviewed HEAD. `prepare-final` produced them
at that HEAD, and they are superseded. The verify ledger keeps the execution.
This prevents the patch checkpoint from committing a passing FINAL.md that
ship step 1 could not replace later. Committed outputs are never touched.

A patch that comes from the final review does not reopen the sequence. The PR
review, where one exists, sees that code.

### 3. Publication

Per target, `pre_pr.py publish`:

1. checks that the remote `pre-pr/...` ref is absent, or equals the SHA this
   journal recorded for it. Any other value is refused and never touched;
2. journals `publishing` with the target and the HEAD to push (the member bound
   tip for a member), before any push;
3. writes the exact-commit push authorization:
   `members.authorize_push(<checkout>, <project>, refs/heads/pre-pr/..., <sha>)`
   in the coordinator for a single repository, or in the member checkout;
4. pushes `<sha>:refs/heads/pre-pr/...` under a lease (an absent ref, or the
   recorded SHA). The source operand is the SHA, never the bound branch name,
   because the hook judges both names of a refspec;
5. confirms the remote ref with `ls-remote`, and journals `published`. After an
   ambiguous push failure, the next command reads the remote and resolves the
   state the same way (the member rebase publisher pattern,
   `isolation.py` publishing journal).

The authorization is narrow: one ref under `pre-pr/`, one commit, written only
by this helper, and cleared when the branch is retired. The owner's approval of
this ADR is the standing approval for these publications. The guard does not
change.

**Retirement.** `pre_pr.py retire --target <t>` deletes the remote ref with
`push --force-with-lease=refs/heads/pre-pr/...:<sha> origin :refs/heads/pre-pr/...`
(a test proves the form and the moved-ref refusal), clears the authorization,
and journals `retired`. It runs only when:

- the recorded execution reported `terminou: true`; or
- no execution was recorded and the attempt is being abandoned.

It never runs while a recorded execution is unfinished: the job fetches the
branch while it runs. A leftover `pre-pr/...` ref is harmless to integration,
and the next `publish` on that target reuses or retires it. A live ref with no
journal, for example in a fresh clone, is deleted only with `retire --adopt
<sha> --owner-ruling <file>`. Adoption reads the ruling before any change, and
it refuses a target that the running round records. The hook does not judge
deleting a ref outside the bound name. Abandoning an interrupted publish
deletes the SHA that the push may already have published.

### 4. A round

The orchestrator owns the MCP calls. The helper owns Git, files, state, and the
checks. All state lives in a journal in the Git common dir
(`gsd-path/pre-pr/<STATE.project>/<STATE.branch>/`), under a lock in the same
directory, because concurrent worktrees do not share the state lock.

1. **Objective.** Before the first round, write per target the PR's title,
   problem, and scope in the editorial language. Ground the member's scope in
   its own diff. `publish --objective <file>` checks each one with
   `pr_editorial`'s identifier and self-contained checks, which are extracted
   so they run without an archive. It stores the text and the language in the
   journal. Later rounds reuse it unchanged. `objetivo` is the title, a blank
   line, the problem, a blank line, and the scope.
2. **Request.** For each target, call `revisar_branch(repo, branch=pre-pr/...,
   base, objetivo, branch_do_pr=<bound branch>)`. Then `pre_pr.py record
   --target <t> --execution <id> --head <head_sha>` refuses a head other than
   the pushed SHA, and journals `requested`.
3. **Wait.** Poll `situacao` with the host's own wait mechanism, never a busy
   loop, until `terminou`. Then `pre_pr.py ended --target <t> --status
   <situacao.json>` journals the outcome and retires the branch.
4. **Failure.** On `falhou`, `cancelada`, quota, or a tool error, the attempt
   stays journaled and does not count toward the cap. Ask the owner: close and
   continue (recommended, rule 1), or retry the round.
5. **Capture.** For `concluida`, call `achados` and pass its result to
   `pre_pr.py capture --target <t> --findings <achados.json>`. The capture
   refuses a payload for another execution, repository, branch, or head SHA,
   and a count of included findings that differs from `situacao`'s
   `achados_incluidos`. It stores the raw payload and its digest once. A
   different payload for the same execution is refused afterwards.
6. **Ruling.** Check each captured finding against the code at the reviewed
   SHA, and propose a decision with a reason. `contesto` cites `path:line`
   evidence. `corrijo` carries a fix direction. One owner question per round
   covers every target. A round with no included finding asks nothing:
   `finish` records an empty ruling itself and needs no answers. Then
   `pre_pr.py rule --ruling <file>` refuses unless:
   - every captured finding has exactly one decision;
   - each `contesto` reason cites a path that exists at that target's
     reviewed SHA, with a line within the file;
   - each `corrijo` has a fix direction.

   It stores the ruling and the owner's answer verbatim.
7. **Answers.** Send every decision through `responder`, then call `pre_pr.py
   finish --responses <file>`. It refuses unless every captured finding of
   every target appears in a `respondidos` receipt for its own execution.
8. **Report.** `finish` writes `.project/review/pre-pr-<n>.md`. The header holds
   `Reviewed HEAD:` (the coordinator HEAD) and one `Member reviewed HEAD:` line
   per member target. Then, for each target, the objective, execution id, and
   suggested verdict. Then one block per finding:
   `### Finding <target> A<n> — <title>`, with Severity, Location, Found
   (`descricao`), Scenario, Decision, Reason, and Fix direction. One renderer
   writes every field that holds external text, in the report and in
   PATCH-FINDINGS.md:
   - the fixed label `Reviewer text:`, then the text inside a code span;
   - the code span uses one more backtick than the longest run of backticks
     inside the text, and a space before and after the text when it starts or
     ends with a backtick;
   - each line break becomes the two characters `\n`;
   - an empty value renders as `Reviewer text: (empty)`.

   With the label, the value is never wholly a placeholder, and the code span
   hides `<codigo>` from the placeholder scan. Placeholder-shaped text
   therefore cannot strand the hand-off (the 1.7.1.65 defect class). Tests pass
   the generated files through `validate_patch_findings`. Their cases include
   a value that is only `<codigo>`, backticks at the edges, pipes, line breaks,
   an empty `cenario`, and a literal `none`. A `## Record` section at the end
   holds a fenced JSON record that no field parser reads: each target's
   objective, execution, head, and raw findings, and every decision. Parking
   and the editorial step read it.
9. **Outcome.** With at least one `corrijo`, `finish` also writes
   PATCH-FINDINGS.md with one `P###` per `corrijo`: Locator `Finding <target>
   A<n>`, and Evidence and Fix direction rendered exactly as in the report. It
   then moves `ship/active → ship/blocked` with the event `pre-PR round <n>:
   <c> to fix, <k> contested, <f> follow-up`. Otherwise it records the same
   event as log-only and closes the sequence when decision 2 says so.

### 5. Findings stay bound until answered

- The journal is the transaction: `publishing → published → requested → ended
  → captured → ruled → finished`. `status` returns `pending` for any attempt
  between `captured` and `finished`. Ship cannot close the sequence, skip it,
  or reach final review while captured findings lack a ruling and answers. A
  session without the MCP tools stops there and tells the owner. It does not
  take the "tools missing" skip.
- An execution id lost between the MCP call and `record` needs no recovery:
  the next request on the same branch cancels the orphan.
- One read-only assertion, `pre_pr.require_closed`, refuses progression while
  the milestone's journal shows an open sequence, an attempt in progress, or a
  concluded round without its report. Two places call it: the `final` command
  of `check_handoffs.py` (ship step 5) and the archive transaction
  (`archive_milestone.py prepare` and `preflight`), which reads FINAL.md
  through its own parser. `validate_final` itself does not call it, because
  `prepare-final` uses that function to judge reusable evidence in step 3,
  before the sequence runs.
- When no journal exists for the milestone, the assertion passes. This covers
  a milestone that started before this runtime, and a journal that was lost.
  The ship step creates the journal on its first `status`. A lost journal that
  held unanswered findings is a known limit. The findings stay unanswered in
  the external registry, and a PR reviewer, where one exists, raises them
  again.
- Limit: the helper cannot call the MCP server and has no key, so it cannot
  prove that the captured payload is the server's complete answer. It binds
  that payload against `situacao`'s count from the moment of capture. After
  that, no later step can drop a finding.

### 6. Fix route: the ordinary patch route

`check_handoffs.py patch` accepts `.project/review/pre-pr-<n>.md` as a source.
The Locator heading must exist, its Decision must be `corrijo`, and Evidence and
Fix direction must equal the source fields. PATCH-FINDINGS.md must copy every
`corrijo` finding of the round exactly once, so a rewrite cannot drop an
accepted fix. The pre-PR branch of the check also
proves that `finish` ran. It refuses when the journal round is not `finished`,
and while an uncommitted FINAL.md or `final-gap-*.md` carries the round's
reviewed HEAD. Otherwise the patch checkpoint would commit a stale passing
FINAL.md. The router needs no change: it
already routes `ship/blocked` with a valid PATCH-FINDINGS.md to plan patch
mode. The patch planner, plan approval, build wave, and wave review run as for
a final-review patch. A member rebase adoption during patch planning behaves
as it already does for final-review findings.

### 7. The report survives archive undo

A round with no `corrijo` leaves its report uncommitted at `ship/active` until
the ship commit. Archive preparation moves it into the untracked archive. A
later uncommitted-archive undo, such as the PR fix lane's, refuses the
unexpected file (`pipeline_undo.py`, inventory check). Even an admitted file
would be deleted along with the untracked archive tree
(`_finish_transaction_locked`).

`pipeline_undo` therefore admits an archived `review/pre-pr-<n>.md` only when
its bytes match the digest the journal recorded for it. Unknown files stay
refused. The undo transaction stores the admitted bytes before it deletes
anything, as it already does for the discussion files. It restores them to the
active `review/` after removing the archive tree, and a resumed undo does this
idempotently. The next archive preparation carries the report again. Reports
that went through a patch are already tracked.

When the journal is lost, `pre_pr.py adopt-report --owner-ruling <file>`
records the digest of each surviving report in a new journal. That restores
undo admission without claiming to recover findings that are missing.

### 8. The objective in the PR description

At archive step 4, `pr_editorial.py context --member <m>` returns the latest
pre-PR objective of exactly that target, read from the archived reports. It
returns the objective's language and the current editorial language together.

- The editorial title, problem, and scope must equal the objective.
  `pr_editorial.py prepare` refuses a difference unless the input carries
  `objective_change: "<reason>"`.
- A translation is a difference, so a change of `editorial_language` after
  the pre-PR also needs `objective_change`.
- The publication records the reviewed objective, the published text, and the
  reason. The escape discloses the change; it does not claim that the new text
  was reviewed.
- The `changes`, `validation`, and `risks` sections are written then, as today.

### 9. Follow-ups

There is no reader for this today. `parking.py` gains one: it parses the
archived `review/pre-pr-<n>.md` reports and returns each `follow-up` to `sync`,
the way it returns PR round follow-ups from lane records. The source is
`<repo>@<branch_do_pr>#<execucao_id>/A<n>`. External ids repeat across
executions, and `sync` deduplicates by source. A test carries one pre-PR
follow-up to `PARKING.md`, and two `A1` follow-ups from different executions
both arrive.

### 10. Guard and host

- `pre_pr.py` joins the runtime helper set allowed at `ship/active`,
  `ship/blocked`, and in patch planning. It writes only its reports,
  PATCH-FINDINGS.md, STATE transitions through `pipeline_state.py`, and its
  journal and authorization refs. Tests run with the installed hooks
  (`core.hooksPath`), not only the helper (the 1.7.1.61 lesson).
- MCP configuration and its key belong to the operator: `claude mcp add` in the
  sandbox, or the host gateway. Availability means only that the tools exist in
  the session. This has not been verified in a Paseo sandbox.

## Alternatives considered

- **Publish under the bound name** (draft 1): the pre-push hook refuses it, so
  it would need a guard exception that changes an upstream invariant, and a
  crash leftover would block `integrate`. Replaced by `pre-pr/...` plus
  `branch_do_pr`.
- **Delta wave without a planner** (draft 1): it needed round ownership by kind,
  a round closure before a final-review patch, two delta kinds in one PLAN.md,
  and a new delta format. Dropped for the ordinary patch route until rounds
  that produce fixes are measured.
- **Review after the PR opens:** the service refuses branches with an open PR.
  The PR fix lane accepts only member PRs with a forge change request. A
  single-repository PR has no fix route after its ship commit. Rejected.
- **Pre-PR after final review:** every accepted fix would force a second
  complete final review. Rejected.

## Consequences

- When a milestone is eligible, it pays one round, and up to two more when a
  `bloqueio` or `importante` fix needs confirmation. A round costs one request
  per target, about 9 minutes and USD 1.30 each. A round that finds a fix
  usually leads to a second round to confirm it. For a milestone with three
  members, that is about USD 7.80 for two rounds.
- A round with fixes costs a patch planner, the plan approval, a build wave
  with its wave review and panel, one more project Verify, and the owner's
  triage answer. It costs no second final review.
- Quick-lane reuse of FINAL.md is lost when a round produces fixes, because the
  product changed.
- Pushing `pre-pr/...` can start the consumer's GitHub workflows that run on
  every branch push.
- New code: `scripts/pre_pr.py`; a patch source in `check_handoffs.py` and its
  final gate check; `pipeline_undo` admission; `pr_editorial.py` (extracted
  checks, `context`, `prepare`); `parking.py sync`; and the guard helper set.
  Skill text changes in ship (the new step and the re-entry rules), plan
  (patch sources), and the patch-findings template.

## Rollout

Consumers take this runtime at a milestone boundary. A milestone already in
ship has no journal, so the gate passes and ship runs as before.

## Known limits

- The contestation check proves only that one cited `path:line` exists at the
  reviewed SHA. It does not prove that the citation supports the contestation,
  and other citations in the same reason are not checked. The owner's triage
  answer judges relevance.
- The objective is checked for pipeline identifiers against the `.project/`
  files that exist at publication. The editorial step checks again at archive
  time, against more files. A token that gets defined in between makes the
  verbatim title, problem, or scope fail there. The disclosed
  `objective_change` is the way out.
- A pre-PR report is a patch source only while its journal proves that `finish`
  wrote it. After a lost journal, `adopt-report --owner-ruling <file>` records
  the surviving reports as finished rounds.
- The helper cannot prove that the captured payload is the server's complete
  answer (decision 5).
- A member rebase adoption during patch planning re-bases the code under
  findings that quote the old base. Final-review patch findings already
  behave this way.

- GitHub.com only, and only repositories that one reviewer account covers. The
  service's out-of-scope error is a failure that the owner rules on; it never
  blocks ship.
- The registry is adopted only by a PR from `branch_do_pr` in the same
  repository and reviewed by the same account.
- The external text is in the reviewer's language. The report quotes it
  verbatim.
