# Native dispatch and build recovery

Read only when no owner-supplied child command exists, a driver receipt requires
manual recovery, or the wave uses verify-only review. Entry and bookkeeping
preconditions remain in the build skill. Do not repeat a proven driver step.

## Wave loop

Native Claude review assignments use the active dispatch contract's
final-message publication: the child returns the complete report, and the
parent stages it unchanged through `isolation.py stage-review`. References
below to a reviewer staging a file include this transport. It preserves the
cycle, review base, independent author, and all validation/collection gates;
it never authorizes backfilling an earlier cycle or writing review findings
in the parent. Owner-supplied CLI commands keep their file-output contract.

For each `## Wave N` in PLAN.md order (a wave's tasks are the task files whose
`wave` equals N):

**Dispatch driver.** Steps 1–5 below are deterministic, and the bundled
`scripts/dispatch_driver.py` runs them: recover, settle exited coders, the
bookkeeping checkpoint, `ready`, brief lint, `prepare-task`, `activate-task`,
one child per ready task, then Verify in the isolate, Log append, `land`,
`verify-record`, and `retire` in task-id order. Prefer it whenever the owner
supplies a child command (a headless CLI that reads the brief on stdin, for
example `claude -p --output-format json ...` or `codex exec --json ...`; the
owner's flags own the child's permissions):

```text
python3 <absolute dispatch_driver.py> round --repo <absolute primary> --wave <N> \
  --child-command '<owner command>' [--wait <owner seconds>] \
  [--child-timeout <owner seconds>]
```

Use the absolute `dispatch_driver.py` from the active installed
`gsd-path-build` skill when orchestrating a consuming project's build; that
copy resolves role briefs and templates from its own skill bundle. The
repository or npm package also supports the top-level `scripts/dispatch_driver.py`:
when a default is not beside that script, it resolves from the shipped
`skills/gsd-path` bundle. Explicit `--role-brief`, `--task-template`, and
`--template` paths override those defaults and must name existing files.

`round` owns the entry transition described in Preconditions and branch
binding, checkpointed as `build: start milestone`, and sets `build/blocked`
on a dependency deadlock or a blocked
recovery; every other stop stays with the parent. Act only on its receipt:
`done` means every wave task landed — run
`review --wave <N> --cycle <C> --child-command '<owner command>' [--wait
<owner seconds>]` for step 6 at `full` or `deep` depth: it records the clean
review base, creates one verify sidecar per lens, briefs one reviewer child
per lens with every task's base and landing commit, validates each file in
its sidecar, copies it to the canonical path, retires the sidecar, and only
after every lens settles concludes: a passing cycle with no panel is
checkpointed; with a panel configured (`panel_required: true`) run `panel
--wave <N> --cycle <C> --advertised <slugs> [--parent-slug <slug>]
--child-command '<owner command>'`, using the
[model-policy CLI contract](model-policy.md#cli-lifecycle). The panel command
resolves membership,
persists a skipped receipt or runs one panelist per family in its own
sidecar, merges the family files, and makes the single on-pass checkpoint;
`blocked` returns the `review_findings.py collect` grouping; when its
`skeptic_groups` is non-empty, `skeptics --wave <N> --cycle <C>
--child-command '<owner command>'` runs one read-only skeptic per locator in
its own sidecar at the review base, validates and collects each file, and
reruns the helper; then `fix-tasks --wave <N> --cycle <C>` writes one
lint-clean fix task per batch carrying the failed criteria and observations
verbatim, or returns `escalate` for structural blockers, skeptic groups, the
cycle cap, an all-refuted ruling, or a pending redesign ruling, which stay with
step 7 and the user.
Each settled `review` receipt and each merged `panel` receipt adds a
`recurrence` diagnostic for step 7; its `status: error` never changes the
receipt's review result.
The driver never writes a `verify-only` file (`status: not-applicable`);
`in-flight` means call `round` again; `question` means answer from the
approved artifacts or the user, record it with `answer --task-id <id>
--answer '<answer> — <citation>'`, and call `round` again, which redispatches
the retained isolate; `blocked` names the task, reason, and output and leaves
an existing isolate in place. A model-selection rejection before launch creates
no isolate; follow the [model-policy contract](model-policy.md). For other
blocks, apply the recovery, retry, or plan-defect procedure in
steps 1–2 by hand, then call `round` again. The driver counts dispatches per
task per milestone and stops at `--max-attempts` (default 2: the first
dispatch plus this contract's one logged redispatch); owner token limits, when
supplied, gate every dispatch through the milestone's budget ledger. Do not repeat a step the receipt
already proves. Without a child command, dispatch through the runtime
dispatch contract and perform steps 1–5 by hand.

1. **Recover before dispatch.** Run `python3 <absolute isolation.py> recover
   --repo <absolute primary>`. It is read-only and proves every `done` task's
   landing commit from its exact stamped base (first-parent scan for
   `<task-id>: <task title>`, body `Task:`/`Base:`/`Files:`, the base task's
   declared path allow-list from the task file at that base, exact landing
   metadata, append-only task-file body, and
   patch equality with a retained task branch). Pending and `in-progress`
   tasks bypass landing-history proof and report retained resume state or
   `none`; failed and blocked tasks with retained isolation report
   `reconcile`. Act only on its verdicts;
   do not re-derive them in prose, rerun Verify on a proven commit, use an
   unanchored log grep, infer a SHA from `done`, or reset unknown work.
   - `attested`: an owner ruling stands in for the landing commit (see the
     block escalation below); treat it as landed and never redispatch.
   - `recovered`: the task is landed (`land` already stamped `status: done`
     and `base`). Retire a still-present task worktree with `isolation.py
     retire` only when the report shows it present, clean, and on the task
     branch. If the checkout directory is absent and `task_branch` remains,
     finish its proven interrupted retirement with `retire --branch
     <task_branch> --force --landed-commit <commit>`. This also clears a stale
     registration at that task's sidecar path after the landing proof passes;
     a locked registration fails without deleting the branch. Both absent is
     valid.
   - `resume`: continue from the returned `base`, `task_branch`, and worktree
     (the primary itself when no task branch exists); when the report shows
     the worktree absent, return the task to `pending` only when ownership is
     clear. When `dispatch_retry` is true, reuse the returned clean isolate,
     write fresh dispatch metadata and agent there, then dispatch; do not
     recreate it. When `landing_retry` is true, do not resume implementation
     or redispatch; rerun `land` against the returned isolate and base.
   - `reconcile`: do not resume implementation. Preserve the returned isolate
     evidence and follow the failed/blocked reconciliation in step 2.
   - `block` on a `done` task with reason `done but no landing commit proves
     it` or `done task has invalid base`: the work was committed outside
     `land`. Never repair history or task frontmatter by hand. Set
     `build/blocked`, present **Outcome** naming every such task, and ask the
     user for a ruling per task; put any suggested ruling text in its own
     fenced code block so it pastes cleanly. On an explicit ruling, run that task's Verify
     at HEAD, record it with `python3 <absolute build_state.py> verify-record
     --repo <absolute primary> --command <task Verify> --commit <HEAD>
     --result pass`, then run `python3 <absolute isolation.py> attest --repo
     <absolute primary> --task-file <task file> --ruling <ruling verbatim>`
     (add `--base <SHA>` only to repair an abbreviated or invalid recorded
     base). The helper stamps the task and creates the attestation commit
     itself; each one moves HEAD, so attest tasks one at a time and rerun
     `recover` afterwards.
     - Rewritten lineage: when every `done` task is rejected with `commit does
       not descend from its Base:` after a rebase or replay, the owner may run
       `python3 <absolute isolation.py> adopt-rebase --repo <absolute primary>
       --original-head <preserved original tip> --head <HEAD> --ruling
       "<ruling verbatim>"`, commit the receipt
       `.project/build/rebase-adoption.json` as `.project/` bookkeeping, and
       rerun `recover`. The original lineage must stay reachable: keep a local
       branch on the old tip until the milestone ships. Adopted tasks report
       `attested` with `provenance: owner-authorized-rebase`.
   - `block` with a `leftover` path: the task is landed, and that path is what
     a failed removal left of its isolate; it holds nothing that Git does not
     have. Do not set `build/blocked`. Stop with the returned reason; after
     the path is removed, rerun `recover`.
   - `block` for any other reason: set `build/blocked` with the returned
     reason and stop.
   - `none`: take no recovery action for that task.

   **Member rebase adoption.** A member bound branch never receives a merge
   of its default branch. When a locked member's recorded default branch has
   moved and the member work must include the new commits (for example the
   member PR's merge result fails the project's checks), only an explicit
   owner ruling may rebase that branch, and only the helper performs it. With
   no task in flight (`plan/active`, `plan/blocked`, `build/blocked`, or ship
   before archive preparation), present the preview, answer the `rebase`
   lenses from [decision lenses](decision-lenses.md) for scope
   `member <member> base <target sha12>`, record the `lenses` Log event, and
   ask for the ruling with a recommendation, then apply it with the same
   arguments (adoption refuses an enforced milestone without the event):

   ```text
   python3 <absolute isolation.py> adopt-member-rebase --repo <absolute primary> \
     --member <name> --onto <full SHA of origin/<recorded default>> \
     --ruling '<ruling verbatim>' [--dry-run]
   ```

   `--dry-run` is read-only: it checks every precondition and simulates the
   replay in a disposable object store. Apply fetches the member's default
   branch, requires `--onto` to equal it and to descend from the locked base,
   requires the remote bound branch to be absent or at the local tip, then
   journals and replays the locked range on a `chore/rebase-…` sidecar branch.
   Each original commit must have exactly one replay with the same message,
   author, and ordered edits; a conflict, an empty commit, or a failed proof
   rolls back with no trace. It then pushes with an exact lease, moves the
   local bound branch by compare-and-swap, writes the lock base and
   `.project/build/member-rebase-adoption.json`, and commits both as one
   `.project/`-only coordinator commit. An interrupted apply is rerun with the
   same arguments: an incomplete replay restarts, a proven replay is reused,
   and once the journal records `publishing` (before the push) the adoption
   only rolls forward. Every resume revalidates its owned targets, task files,
   and replay; `--abort` rolls back only a `prepared` or `replayed` adoption.
   A push the forge reports as rejected while the ref keeps its lease returns
   to `replayed` (`publish-refused`); any other push failure stays at
   `publishing`, so preserve that journal and rerun or ask the owner. Recovered
   member tasks then report `recovered` with the replayed `landing`,
   `recorded_landing`, and `provenance: owner-ruled-member-rebase`.
   If an undone archive left a pending member PR close, the helper first
   proves the same PR is open/unmerged without auto-complete, checks it again
   after the push, and preserves the pending close row and old evidence. Its
   receipt records that prior close/source proof. Never erase that journal.
   `define/active` refuses adoption; finish the normal definition/plan gates
   first and wait for planner/task dispatch to be quiet. Later `close-members`
   requires fresh final review and publication, proves and lease-publishes any
   repair landings after the adopted tip, and reuses that same PR.
   The adoption proves identical edits, not behavior: Verify ledger entries and
   final reviews recorded at old member commits do not carry over. Ship
   reruns project Verify and the member final review at the new tip, and a
   repair that needs a task Verify at a replayed landing records a new run.

2. **Prepare the ready set.** Run `python3 <absolute build_state.py> ready
   --repo <absolute primary>`. It is read-only: it validates every task's
   frontmatter against its dispatch metadata and the recovery report, then
   returns `ready` — the current wave's `pending` tasks whose dependencies are
   all `done`, each with `id`, `title`, `deps`, `files`, `task_file`, and
   `verify_heavy` — after proving that ready and `in-progress` tasks have
   disjoint `files`. Dispatch exactly the tasks it returns; do not select,
   order, or overlap-check tasks in prose. Readiness is continuous, not
   layered: a task becomes selectable the moment its last dependency lands,
   even while unrelated tasks still run, so rerun `ready` after each landing.
   Two tasks that each share a file with the same `done` task of an earlier
   wave never run together: `ready` returns one and holds the other until the
   first is `done`. The second then starts from a base that has the first
   landing, and the step 5 rerun of the earlier Verify checks both edits. A
   task with a retained isolate keeps its turn. A task that a `supersedes`
   field retires still counts: the superseding task never runs together
   with a task that shares a file with the superseded task.
   `review-panel-required` holds dispatch when an earlier recorded cycle
   lacks its configured panel or has an invalid skipped receipt. Complete
   that cycle's panel procedure in step 6 before further task or review
   dispatch. A blocked canonical verdict and a focused re-check still owe
   their panels when their PLAN depth is `full` or `deep`.
   A `NEEDS-ORCHESTRATOR` block stays unselectable until its `Orchestrator
   answer` is recorded in the task Log and the task is back to `pending`.
   An all-done wave whose latest canonical review cycle has any blocked lens
   gates later waves: `ready` selects unfinished later tasks with a `## Review
   findings` section that depend on it, sets `current_wave` to the earliest
   repair wave, and adds `repair_for_wave` with the source wave. When no such
   repair remains, `ready` returns no tasks and adds `blocked_review` (`wave`,
   `cycle`, `waiting_waves`); a later passing canonical review cycle releases
   the waiting waves.
   `unreviewed_waves`, when present, lists earlier waves whose tasks are all
   `done` and that have no canonical review file. A repair wave is not
   listed: the next review cycle of its source wave judges it. `ready` reports
   the listed waves and does not hold the returned tasks: run step 6 for each
   listed wave before you dispatch.
   Its typed errors are the recovery rule:
   - `task-recovery-required`: for each listed failed or blocked task run
     `python3 <absolute build_state.py> reconcile --repo <absolute primary>
     --task-id <id>` and act on its `classification`. `proven-landed`: the
     task already landed at `landed_commit` and its frontmatter is stale —
     reconcile the task file to that landed state and checkpoint it.
     `resumable` or `reconcile`: apply the retry procedure below with the
     returned `history.candidates` as the rejected evidence. `blocked`: set
     `build/blocked` with the returned `reasons` and stop.
   - `dependency-deadlock`: set `build/blocked` with its `tasks` list and stop.
   - `ready-file-overlap` or `invalid-task-state`: a documented plan defect or
     drifted bookkeeping; repair it before a new clean base.
   A documented plan
   defect may be repaired against INTENT.md and SYNTHESIS.md and logged before
   a new clean base. A repair edits only the fields the lint or ruling named
   in the tasks it named — `files`, Verify, Interface contract text, Intent
   coverage rows, an ambiguous AC. It never adds, removes, splits, merges, or
   renames tasks, moves a task between waves, or rewrites another task's
   acceptance criteria; a defect that needs any of those sets `build/blocked`
   with the lint output and follows [build recovery](build-recovery.md) to
   plan, which owns the task inventory.
   A user ruling that changes a success criterion,
   constraint, or veto is not a plan defect: set `build/blocked`, do not
   rewrite the AC, and follow [build recovery](build-recovery.md) to
   `$gsd-path-define` to append INTENT.md
   `## Corrections` before plan re-gates. A task Log is not that record. One failed implementation gets one logged redispatch when
   its contract remains valid. Repeated failure, ambiguous ownership, a user
   ruling, or a dependency deadlock sets build state to `blocked` and stops.
   Concurrent tasks must have disjoint `files`; serialize overlapping fix
   tasks. A retry never reuses a rejected dirty worktree: first copy its
   validated append-only task Log delta into the primary task file, record the
   rejected diff's exact path set and hash, and checkpoint the block bookkeeping.
   After confirming every old change is task-owned, retire that exact worktree
   with `python3 <absolute isolation.py> retire --repo <absolute primary>
   --worktree <path> --branch <task_branch> --force --task-file <task path>`,
   then create the retry. If retirement was interrupted after removing the
   worktree, rerun the same command; the recorded failed or blocked task and
   its exact base prove the remaining branch before deletion. Create the retry
   from the new clean primary HEAD. A member task (`repo: <member>`) stays
   `pending` in the coordinator; its rejected attempt lives in the member
   sidecar. Copy the live copy's Log delta into the coordinator task file,
   checkpoint it, then retire the sidecar with `python3 <absolute isolation.py>
   retire-member-task --repo <absolute primary> --member <member> --task-id
   <id> --task-file <task path> --force`. It refuses until the committed task
   file holds that delta, and a rerun finishes an interrupted retirement. A
   copy that fails the append-only check holds no trusted delta: record your
   own rejection evidence in the coordinator task Log and checkpoint it, as for
   a coordinator diff rejection; retirement then reports `copy_rejected`. The
   next `prepare-task` creates the retry sidecar at the member bound tip. After
   resolving a recoverable `build/blocked` condition, use
   `pipeline_state.py transition` with the blocked state and bound branch as
   expected fields to set `build/active` and log the resolution. Checkpoint it
   through the bookkeeping rule before recording that new dispatch-round base.

3. **Isolate every task.** Checkpoint pending bookkeeping through the rule
   above, then record clean `HEAD` as
   this dispatch round's base — tasks dispatched in the same round share it;
   a later round unlocked by fresh landings records the later HEAD — and
   lint every ready task's brief with the bundled
   `scripts/check_task_briefs.py --repo <absolute repo root> --base <recorded
   base> --project-dir <routed project dir>` and re-check Intent coverage with
   `python3 <absolute check_handoffs.py> plan --repo <absolute repo root>`
   before creating any worktree; a lint or coverage failure is a documented plan
   defect — repair it against INTENT.md and SYNTHESIS.md, then re-establish
   the base. Then isolate each ready task with
   `python3 <absolute workflow_run.py> prepare-task --repo <absolute primary>
   --expected-head <recorded base> --task-id <id> --round-size <N>` where N is the
   number of tasks in this dispatch round. For a coordinator task, serial
   (`N=1`) returns the primary worktree and `task_branch: null` — the coder
   works on the bound branch.
   The runner also creates a named verification sidecar at this clean base
   before dispatch. Retain both helper results in the dispatch evidence.
   For a coordinator task, parallel (`N>=2`) creates a named
   `chore/task-<id>` branch and linked worktree at that base; never a
   detached HEAD. Record dispatch through
   `python3 <absolute isolation.py> activate-task --repo <returned worktree>
   --base <recorded base> --task-id <id> --agent build_<id> --task-file
   <exact selected task-file path> [--task-branch <returned task_branch>]`.
   The helper sets `base`, `worktree`, `task_branch`, `status: in-progress`,
   and `agent` in the isolated task and records parallel build authorization.
   Do not edit those fields directly. Do not commit this dispatch state: it lands
   inside the task's own commit, and `recover` derives it from the task
   branch and worktree meanwhile. The primary stays clean during a parallel
   round. Do not append a dispatch Log entry: the isolated task later appends
   at that location, and two parallel appends make the cherry-pick ambiguous. Reuse a retained worktree only when
   its recorded base, branch, and task agree exactly.
   A member task (`repo: <member>`) returns `mode: member` from
   `prepare-task`: a member sidecar at the member bound tip, with no
   coordinator verify sidecar. Record its dispatch with `python3 <absolute
   isolation.py> activate-member-task --repo <absolute primary> --member
   <member> --task-id <id> --agent build_<id> --task-file <exact task-file
   path> --base <recorded base>`. It writes the live task copy (returned as
   `copy`) inside the member sidecar; the coordinator task file stays
   unchanged. Brief the coder with the member sidecar root and that live copy
   as its task file, plus the coordinator's INTENT.md. Step 5's `finish` lands
   it in the member and records it in the coordinator.

4. **Dispatch the round.** Following the local runtime dispatch contract,
   spawn one implementation-capable child per task with deterministic logical
   task name `build_<task_id>`.
   Its brief contains the absolute isolated-worktree root, coder role, task
   file, task template, and the absolute INTENT.md path in the coordinator for
   member tasks or in that worktree for coordinator tasks.
   Generate the coder's intent view with `python3 <absolute task_context.py>
   --repo <absolute coordinator root for member tasks, otherwise isolated-worktree>
   --task <absolute live task copy for member tasks, otherwise isolated task file>` and include
   its output in the brief. The helper preserves all global rules and falls
   back to full intent when projection is ambiguous. Never summarize it by hand.
   Add no hidden implementation context; repair a
   defective task contract before establishing the round base. Run ready work
   up to capacity. Verify commands marked `Heavy: yes` (`verify_heavy` in the
   ready set) run one at a time across every concurrent task, including the
   step 5 rerun; light ones are unconstrained. `verify_heavy` is also true
   when step 5 reruns a heavy Verify of an earlier task. Do not wait for the whole
   round before unlocking dependents: each task landing in step 5 re-opens
   step 2, and a newly ready
   task dispatches in a fresh round at the current clean HEAD while unrelated
   tasks still run. The wave advances to review only when every wave task is
   `done`.

5. **Verify and land serially as results arrive.** A coder returns only
   `ready` or `blocked`; it never owns frontmatter or Git. Process each
   completion when it lands — never wait for slower in-flight tasks first;
   when several results wait, land them in task-id order.

   - For a `ready` coordinator task, compare the complete worktree diff to
     `base`. Permit only declared `files` plus append-only Log changes in that
     task file. Include additions, deletions, renames, and binary changes; an
     unexpected path blocks before any product commit.
   - For a `ready` member task, compare the complete member sidecar diff to the
     returned `member_base`. Permit only declared `files`, including additions,
     deletions, renames, and binary changes. Separately check the returned live
     task `copy` against the coordinator task contract for an append-only Log.
     An unexpected path or Log change blocks before any product commit.
   - For a returned `ready` coder, run `python3 <absolute dispatch_driver.py>
     finish --repo <absolute primary> --task-id <id>`. This action works with
     native child tools; no child command is needed. It reproduces serial work
     in its prepared sidecar, runs authoritative Verify, records exact output,
     lands through `isolation.py`, records eligible ledger evidence, and retires
     the isolate. Authoritative Verify is the task's own Verify, then the
     Verify of each `done` task of an earlier wave, in the same repo, that
     shares a declared file with this task. `finish` selects those commands;
     never select them by hand. An earlier command does not run again in two
     cases only: it is identical to the own Verify, or the own Verify is the
     unchanged Verify that `fix-tasks` wrote from the task's `deps` and the
     command belongs to one of them. A Verify that only contains the command
     text does not replace the rerun. A failed earlier-task Verify blocks the landing as a
     failed own Verify does. The receipt (`regressions`) and the task Log
     entry `orchestrator regression Verify of <task id>` name the earlier
     task. The ledger records only the own command.
     A plan-approved `supersedes` field retires the Verify of each earlier
     task that it names: `finish` does not run that Verify for the task, or
     for a task that lands after the task is `done`. The receipt (`retired`)
     and the task Log entry `orchestrator regression Verify of <task id>
     retired: superseded by <task id>` name both tasks. When the generated
     Verify of a fix task loses only the copy of a retired Verify, the
     `retired` entry has the key `part` and the Log entry is `orchestrator
     regression Verify of <fix task id>: part of <task id> retired:
     superseded by <task id>`. The superseding
     task inherits the role of each task that it names, also through a
     chain: after it is `done`, its Verify runs in the place of the retired
     Verify for each later landing that shares a file with a superseded
     task, also in its own wave. The Log entry is `orchestrator regression
     Verify of <task id> (supersedes <task id>)`.
     A `landed` receipt proves those steps; do not run them again
     or author another implementation narrative. A `cleanup_warning` on a
     landing means only that the isolate was not removed: the task is landed,
     and the next `round` or `finish` checks the isolate again. A blocked receipt preserves
     its failure evidence and follows the recovery rule, never a manual bypass.
     Keep the pre-finish declared-path check above. Each landing re-opens step 2
     so ready dependents can run while unrelated children remain in flight.
   - A block whose Log delta leads with `NEEDS-ORCHESTRATOR:` is a contract
     question, not a failure. When the approved artifacts (PLAN.md,
     INTENT.md, SYNTHESIS.md, and the Interface contracts of every involved
     task) pin exactly one answer, append `Orchestrator answer: <answer> —
     <artifact citation>` to the task Log, return the task to `pending`,
     checkpoint the bookkeeping, and let a later layer redispatch it; a question
     redispatch never consumes the failed-implementation redispatch. When the
     runtime's structured layer exposes a blocking ask/reply channel, relay
     the answer through it with the worker held alive per the runtime
     dispatch contract instead of redispatching. A dispatch-driver question
     keeps the isolate active: record the answer with `dispatch_driver.py
     answer`, and the next `round` redispatches that same isolate. When
     the artifacts admit more than one reading, ask the user through an
     interactive user-input tool when available, record the ruling verbatim
     as the answer, and repair the task contract as a documented plan defect
     when the ruling changes it — except a ruling that changes a success
     criterion, constraint, or veto, which follows the define-Corrections
     path above. A question block creates no product commit
     and preserves the isolated worktree under the same retirement rule. Before
     changing its canonical bookkeeping, revoke a parallel isolate's active
     dispatch with `python3 <absolute isolation.py> deactivate-task --repo
     <isolated worktree> --task-id <id> --task-branch <task_branch>`.
   - A blocked report, invalid diff, or failed Verify creates no product
     commit. Validate and copy the isolated task's append-only Log delta once;
     it is the coder's sole block/implementation narrative. Add orchestrator
     evidence only for a distinct diff or Verify rejection. Before changing the
     task to `blocked` or `failed`, revoke a parallel isolate's active dispatch
     with `python3 <absolute isolation.py> deactivate-task
     --repo <isolated worktree> --task-id <id> --task-branch <task_branch>`.
     A member task keeps `pending` instead; follow the member retry in step 2.
     Then update and checkpoint the bookkeeping and apply the recovery
     rule. Preserve the isolated worktree unless and until the explicit clean
     retry-retirement procedure in step 2 owns and removes it.

6. **Review the wave.** The build orchestrator owns wave reviews; ship never
   runs them. Only after every wave task is done, read the wave's
   `Review depth` from PLAN.md (default `full`). At `full` or `deep` the
   dispatch driver's `review` action performs the sidecar, brief, validate,
   collect, and retire work of this step; perform it by hand only without a
   child command.
   - `full`: spawn one independent reviewer using deterministic logical task
     name `review_wave_<wave>_cycle_<cycle>`. Supply every task path, its
     recorded base and proven landing commit, the reviewer role, and
     wave-review template, and the absolute INTENT.md path.
     When INTENT selects quick lane and PLAN has one full wave, also assign
     final scope: supply PLAN.md's Surface contract and the recorded review HEAD.
     The same reviewer checks all success criteria and any remaining walkthrough
     in this pass, records `Review scope: final` and `Reviewed HEAD`, and adds
     Surface/Check/Observed fields to each surface criterion. Reuse recorded
     command evidence when it already proves the named walkthrough; execute
     only missing checks. Ship can then derive FINAL.md without another reviewer.
     Record current clean primary HEAD as the review collection base. Create
     a fresh review sidecar there; the serial task verification sidecar belongs
     to the earlier task base and cannot be collected after landing.
     Use `python3 <absolute isolation.py> isolate-verify --repo <absolute primary>
     --base <recorded review base> --name wave-<N>-cycle-<C>`. Each task's
     recorded base and landing commit still define its isolated review diff.
     Keep primary HEAD fixed until collection completes. Brief the
     recorded isolated Verify output per task (the Log entry the
     orchestrator appended at landing). The reviewer must not re-run that
     command or PLAN.md's project Verify. The reviewer
     stages `.project/review/wave-N.cycleC.md` there; the
     orchestrator validates it, atomically copies it to the primary canonical
     path, and only then retires that sidecar with `retire`.
   - `deep`: spawn two independent reviewers in parallel, each with a fresh
     isolated context and its own verify sidecar from `isolate-verify` at the
     recorded review base (`--name wave-<N>-cycle-<C>-contract` and
     `wave-<N>-cycle-<C>-adversarial`). Supply both every task path, its
     recorded base and proven landing commit, the reviewer role, and the
     wave-review template, and the absolute INTENT.md path. The contract lens —
     logical task name `review_wave_<wave>_cycle_<cycle>_contract` — does the
     full review: apply each task's `commit^..commit` product patch to the
     recorded base, check the recorded Verify plus the isolated diff, and
     check every acceptance criterion,
     owned INTENT success criterion, and interface contract. The adversarial
     lens — logical task name
     `review_wave_<wave>_cycle_<cycle>_adversarial` — tries to kill the work:
     security holes, unhandled edge cases, failure modes, data-loss and
     concurrency risks, and missing error handling. Each stages its own file
     in its own worktree — `.project/review/wave-N.cycleC.contract.md` and
     `.project/review/wave-N.cycleC.adversarial.md`; the orchestrator validates
     each, atomically copies both to their primary canonical paths, and only
     then retires those sidecars. The wave passes only when both lenses
     return `pass`; any `blocked` lens blocks the wave, and both files'
     findings feed the fix-task batching in step 7.
   - A review file is written only during that cycle, at that cycle's recorded
     base, with content authored by the selected depth's owner: a dispatched
     reviewer at `full` or `deep`, or the orchestrator at `verify-only`.
     Native Claude parent publication does not change that authorship. Never backfill,
     split, rename, or reconstruct a review file for a cycle that already ran,
     and never write a placeholder verdict for a task that did not exist at
     that cycle. A missing or non-canonical earlier artifact is reported to
     the user; the
     only repair is a new review cycle at the current HEAD, which counts
     toward the cap.
   - `verify-only`: spawn no reviewer. The orchestrator writes
     `.project/review/wave-N.cycleC.md` itself from evidence it already
     holds — per task, the Verify evidence and the declared-files diff
     check — recording `Depth: verify-only`. Same command, same commit
     reuses the recorded pass: the task's Verify evidence is the ledger
     entry when `python3 <absolute build_state.py> verify-lookup --repo
     <absolute primary> --command <task Verify> --commit <landed commit>`
     returns `reuse: true`; otherwise run the command in an
     `isolate-verify --historical-task <task id>` sidecar at that commit, record it with
     `verify-record`, and use that result. A re-review after a fix cycle
     follows the same rule. It checks each acceptance
     criterion and each INTENT success criterion owned by the wave's tasks
     against that evidence and the diff; anything it cannot
     confirm from them is a finding, not a pass. Never spawn a review panel
     at `verify-only`.
   - After the canonical reviewer (or orchestrator-written
     `verify-only` file) is collected, run the optional review panel only
     for `full` and `deep`. Inspect advertised model slugs and run
     `python3 <absolute review_panel.py> resolve --plan <absolute PLAN.md>
     --intent <absolute INTENT.md> --advertised <comma slugs>
     --parent-slug <current model slug when known>
     --exclude-family <known canonical-reviewer family>`; repeat
     `--exclude-family` for every known parent and canonical-reviewer family,
     including all deep review lenses, before persisting the roster. Omit
     unknown identities. Also pass `--charter
     <absolute .project/CHARTER.md>` when that file exists. `status: off`
     continues with no current-cycle panel artifact. For `skipped`,
     persist the helper's exact JSON stdout as
     `.project/review/wave-N.cycleC.panel.skipped.json` and continue without
     a panel; do not translate or summarize the receipt. Exit 2 / `error`
     blocks the wave. `ready` requires that skipped-receipt path to be absent,
     then spawns one child per selected family with
     logical task name `review_wave_<wave>_cycle_<cycle>_panel_<family>`,
     the reviewer role in wave-panel mode, the wave-panel template, and the
     exact helper-returned model slug when the host advertises model
     selection. On `deep`, each panel brief is the adversarial lens only.
     Resolve model choices through the dispatch model-policy contract. Each child stages
     its family file in its own verify sidecar from `isolate-verify`
     (`--name wave-<N>-cycle-<C>-panel-<family>`); the parent validates
     those files and runs `python3 <absolute review_panel.py> merge --kind
     wave --wave <N> --cycle <C> --inputs <family files> --output
     <absolute .project/review/wave-N.cycleC.panel.md> --mode
     <detected|named>`. The canonical reviewer remains the only Wave verdict.
     Do not average panel findings into that verdict or auto-create fix
     tasks from preference findings.
   - After the canonical reviewer file (and each deep lens file) is
     on the primary path, run
     `python3 <absolute check_handoffs.py> wave --repo <absolute primary>
     --review <canonical wave-review path>`. A non-zero exit is a blocked
     wave, not a pass, including `verify-only`. Do not advance on helper
     failure.

7. **Fix or advance.** A valid canonical `pass` advances unless an
   actionable review-panel finding is waiting for a user ruling. Read
   `recurrence` from the driver's `review` or `panel` receipt after the
   applicable cycle files settle. For manual native review, invoke the same
   read-only diagnostic with `python3 <absolute review_findings.py>
   recurrence --repo <absolute primary> --wave <N> --cycle <C> --project-dir
   <routed project dir> --review-base <recorded review base>`. Without a driver
   journal, supply that authoritative coordinator review snapshot; never
   substitute current HEAD. A member snapshot is the coordinator's last member
   landing record at that base, or the locked build base before any landing;
   `--member-head <member>=<full sha>` supplies a recorded one explicitly.
   Missing or unresolvable attribution stays explicit.
   The diagnostic reports `recurrence.status: ok`, `partial`, or `error`.
   Its failure is `status: error` inside `recurrence`, not a blocked review,
   a helper failure for `collect`, or a reason to reject a finding.
   Recurrence means a new current criterion finding cites lines written by an
   earlier completed repair Task landing. Original lines in that same file
   and preference findings do not count. It is informational, not a gate or
   an automatic architectural diagnosis.
   In the existing user questions below, show repairs cited per file from
   `recurrence.files` (`repo`, `path`, `repairs_cited`), including unavailable
   coverage, and offer `Reconsider the component approach` when any
   `recurrence.findings[].recurrent` is true.
   Recommend that option only with a reason drawn from the evidence, not from
   recurrence alone. Selecting it opens the brief below, not permission to
   dispatch or change a contract. Recurrence adds no question, mandatory
   artifact, token budget, or default agent; the redesign checkpoint below
   comes from `collect`, not from this diagnostic.
   On `blocked`, run `python3 <absolute review_findings.py> collect --repo
   <absolute primary> --wave <N> --cycle <C>`, adding one `--helper-failure
   "<helper and stderr>"` per bundled helper that exited non-zero in this
   cycle. The helper alone reads PLAN.md Config (`max_review_cycles`,
   `finding_skeptics`, `wave_budget`), the cycle's lens files, and every
   skeptic file of the wave; it groups blocking findings by criterion
   locator, collapses true duplicates, carries earlier refutations forward,
   separates structural blockers, batches fix groups by disjoint file scope,
   and reports `cap_reached`. The cap counts blocked cycles per wave for the
   whole milestone. `history` lists the earlier cycles, including those a
   build recovery moved to `plan/build-recovery-<base>/review/`; an identical
   restored review counts once only when Git proves the same publication;
   independently published identical text is another attempt. `history.prior_blocked` counts the blocked
   cycles set aside, and `cap_reached` is true when
   `history.effective_cycle`, those plus the current cycle number, reaches
   `max_review_cycles`. A recovery never refills the budget. `redesign` lists
   each fix group whose criterion already blocked two earlier cycles of the
   wave, with those cycles. Exit 2 blocks the wave. Never regroup,
   re-derive a locator, or re-decide refutation in model reasoning; act on
   the result in this order:
   - `structural_blockers` non-empty: a missing or invalid lens, panel, or
     skeptic artifact, a helper failure, or a finding that names no
     contract criterion never enters skeptic filtering. Keep it blocking
     through the ordinary blocked-wave handling — a fix task when applicable
     or `build/blocked` escalation otherwise.
   - `cap_reached` true: skip every branch below and use the cycle-cap
     escalation at the end of this step.
   - `skeptic_groups` non-empty: spawn one independent read-only skeptic
     per listed locator, concurrently up to the advertised child capacity,
     with logical task name
     `review_wave_<wave>_cycle_<cycle>_skeptic_<criterion_locator>`, the
     reviewer role in skeptic mode, the skeptic template, and its own verify
     sidecar from `isolate-verify`
     (`--name wave-<N>-cycle-<C>-skeptic-<criterion_locator>`) at the
     recorded review base. Brief each with the group's `locator`,
     `criterion` verbatim, every `observations` entry verbatim, the group's
     `tasks` files, their recorded bases and proven landing commits, and the
     absolute INTENT.md path. Each skeptic stages
     `.project/review/wave-N.cycleC.skeptic-<criterion_locator>.md` in its
     sidecar; validate each against the skeptic template, atomically copy it
     to the primary canonical path, then retire that sidecar. Rerun the
     helper; a locator refuted in an earlier cycle never gets a second
     skeptic, and the helper already routes it.
   - `all_refuted` true: answer the `refuted` lenses for `wave <N> cycle
     <C>` and record the `lenses` Log event, then stop and ask; a lens may
     change the recommended option. Present **Outcome** with the blocked
     verdict and the refutation count, **Review** linking the lens and
     skeptic files, and **Next** listing `Re-run the review cycle with the
     skeptic files in the reviewer briefs (recommended — no finding survived
     scrutiny)` first, then `Open fix tasks from the findings anyway (an
     explicit ruling that overrides their refutations)`. A selected re-run
     consumes a cycle. After the user selects either option, record exact
     full HEAD, then use `pipeline_state.py transition` with the exact
     current phase, status, branch, and archive as expected and unchanged
     phase/status as the result. Append `wave <N> cycle <C> all-refuted
     ruling: <selected option verbatim>` to the STATE.md Log and require the
     returned position to remain `build/active`. Checkpoint STATE.md and the
     collected skeptic artifacts through the Bookkeeping checkpoint rule with
     subject `build: record wave <N> cycle <C> skeptic ruling`, body `Why:
     persist the all-refuted user ruling before acting`, and `Wave: <N>`. Do
     not start the selected review cycle or create, batch, or dispatch fix
     tasks until the checkpoint returns its commit. When the persisted ruling
     selects `Open fix tasks from the findings anyway`, the override set is
     every `refuted_groups` locator with its preserved `observations`; its
     governing refutation is the group's `skeptic` entry (the current
     cycle's file, or the earlier cycle's file for a same-evidence repeat).
     Add that set to `fix_groups` for batching. No other ruling re-admits
     refuted groups. Preserve their skeptic verdicts and eligibility
     history. After a crash, resume the recorded choice without asking
     again.
   - `redesign_pending` true: two fixes of the same criterion already
     failed, so stop before another fix task. First give the brief at the
     end of this step for the `redesign` groups, with their
     `blocked_before` cycles and the `recurrence` files. Then ask, with
     **Outcome** naming each criterion and how many cycles it blocked,
     **Review** linking the current review files and the earlier ones that
     `blocked_before` names, and **Next** listing
     `Reconsider the component approach` and `Open fix tasks anyway`. Before
     asking, answer the `redesign` lenses for `wave <N> cycle <C>` and record
     the `lenses` Log event. Mark one `(recommended)` only with a reason
     drawn from the review evidence and those lenses.
     Record the ruling exactly as for an all-refuted ruling, appending
     `wave <N> cycle <C> redesign ruling: <selected option verbatim>` and
     checkpointing with subject `build: record wave <N> cycle <C> redesign
     ruling`, body `Why: persist the redesign ruling before acting`, and
     `Wave: <N>`. The helper reads the committed STATE.md Log and requires
     that ruling to be unchanged in the working file: `Open fix tasks anyway`
     (with an optional parenthesized recommendation) clears `redesign_pending`,
     and `fix-tasks` then batches as below. `Reconsider the component approach` leaves it
     pending; continue with that brief's options through the plan-defect,
     build recovery, or define-Corrections route. A build recovery starts
     a new ruling scope, because it numbers the changed wave from cycle 1
     again. After a crash, resume the recorded choice without asking again.
   - Before `fix-tasks`, answer the `fix-routing` lenses for `wave <N> cycle
     <C>` and record the `lenses` Log event; `fix-tasks` refuses an enforced
     milestone without it (`blocked`, reason naming the event to record;
     recording it is the whole repair). When a fired lens there returns
     `redesign` or `fail-closed`, `collect` reports `lens_redesign_pending`
     and `fix-tasks` escalates even if no criterion blocked two earlier
     cycles: ask the redesign question above and record its ruling the same
     way. Only `Open fix tasks anyway`, recorded after the latest such lens
     event, releases fix creation; a later quiet lens event does not.
   - Otherwise batch `fix_batches` into complete fix tasks from the task
     template — one task per batch, never one per finding — each carrying
     its groups' failed criteria and observed evidence verbatim. Each batch
     is in one repo: a batch with `repo` is for that member and its fix task
     has `repo: <member>`; a batch without `repo` is for the coordinator. A
     finding whose source tasks are in two repos gives one batch for each
     repo. A group
     with `repeat` true is evidence its earlier fix failed, never a new
     finding; a re-review never spawns a duplicate fix task for a finding
     that a fix task not yet `done` already carries in the same repo. A
     repeat after its fix
     landed gets the next fix task. After a build recovery, a changed wave
     is reviewed again from cycle 1; the fix tasks and PLAN.md repair wave
     of the review that recovery set aside do not count for the new one.
     Its blocked cycles still count toward the cap and the redesign
     checkpoint through `history`.
     Write each fix task to `.project/tasks/` with `wave`
     set to the current wave or a newly appended heading in PLAN.md before
     dispatch. An appended heading names its source exactly as the driver
     does, `## Wave <M> — fix wave <N> cycle <C> review findings`: completion
     and archive accept a wave of repair tasks without a review of its own
     only through that heading (or repair receipts naming one source cycle)
     and a later review cycle of wave N. Its dependencies include every source task of
     the batch that is in the same repo, and every landed fix task of the
     same source wave and repo that carried one of its findings; its Verify
     runs the Verify of those source tasks; its files are the batch files or a subset of them, never
     none. When a finding names a fix site outside the batch files, follow
     [build recovery](build-recovery.md) to plan, which adds a repair task
     with those files, the batch's `## Review findings` and the same
     dependencies; `repair-evidence` accepts that task. In
     `## Review findings`, use one
     `### <locator>` block per group, `Criterion: <verbatim criterion>`, and
     every observation verbatim. Run the repair through the same isolated
     layer loop, even when its appended wave repairs an earlier blocked wave.
     An appended repair wave has the highest wave number and still runs before
     the pending waves, because `ready` gates them on the blocked wave (step
     2). Do not renumber waves or move the repair wave.
     After landing, call `review_findings.py repair-evidence --repo <primary>
     --wave <source wave> --cycle <source cycle> --task <repair id>` and save
     its JSON as the repair evidence for the next review. The helper selects
     the batch by the repair's locators and repo, then validates
     the carried batch, original and repair landings, isolated product scope,
     unchanged source reports and reusable Verify. For a member repair it
     checks the product in the member repository, against the member commits
     and the member Verify record, and the receipt adds `member`,
     `member_base` and `member_commit`. Another change to the checked files
     is accepted only when it is the landing of a linked repair: a `done`
     task of the same repo that carries one of the repair's findings in
     `## Review findings`, with the same locator and criterion, and is
     joined to the repair by `deps`. A finding on an acceptance criterion of
     a fix task counts as the finding that this fix task carries with the
     same criterion; the same criterion text under another locator is
     another finding. When another landing changed those files before the
     repair base, for example a task of a recovery plan, the helper anchors
     on the product that the source review judged instead of the original
     landing. This requires an explicitly assigned current-product source
     review, not an ordinary review of each task's isolated product. Dispatch
     the full fallback with `review --current-product`; it pins each member
     and validates the same snapshots in the report. The helper authenticates
     every collected lens by its identity, successful execution, validated
     artifact hash and path, including records retained in earlier recovery
     namespaces. A native/manual source report must likewise record
     `Product scope: current`, `Reviewed HEAD: <sha>` and each
     `Reviewed member: <name> <sha>` before the repair. Pass
     `--review-base <recorded review base>`, and optionally
     `--member-head <member>=<full sha>`. These flags must match the
     report's pinned snapshots; they cannot replace a member pin with a later
     unreviewed commit. The base must precede the
     publication of the source review and be in the repair base. From that
     product to the repair base, only linked repairs may change the files.
     Such originals carry `anchor: source-review`, and the receipt adds
     `source_review` with `review_base` and, for a member, `member_head`.
     Linked
     repairs in the repair base are in `earlier_repairs`. Linked repairs
     after the repair are in `later_repairs`; then the landing and the
     recorded Verify of the last one are the evidence, and `chain_files`
     lists the files checked from the repair base to the tip: those of the
     repair and of every later linked repair. From the repair base to the
     parent of the repair landing, no landing can change these files, also
     not a linked one. On success, brief the next
     original-wave review with this receipt and the reviewer role's
     **Re-review after a proven repair** contract. Reuse command evidence;
     the new review judges the repaired product against the original criteria.
     Preserve prior verdicts and source tasks. On helper rejection, resolve
     the evidence blocker rather than redispatching an already proven fix.
     When the blocker cannot be resolved, for example `repair base contains
     unlinked changes to original product` with no recorded review base, or
     with a change after it, or `product changed after the
     repair landing` because a task that is not a linked repair changed the
     same files, run the next review cycle of the source wave in full at the
     current HEAD, without the receipt, explicitly assigning current-product
     scope (`review --current-product`, or the same scope and pinned metadata
     in a native review brief). This is the supported fallback: it
     needs no ruling and no plan change.
   At the cap, record all attempts in the STATE.md log and ask the user —
   through an interactive user-input tool when available — after linking
   the resolved absolute blocking wave review, every deep lens file for the
   cycle, and every skeptic file collected for the wave, plus the STATE.md
   attempt log. Show `history.cycles` (recovery, cycle, verdict, locators)
   and `history.prior_blocked`, so the question counts the blocked cycles
   that earlier build recoveries set aside. Ask whether to redirect the
   approach, raise the cap to a value above `history.effective_cycle`, or send
   define to amend INTENT.md `## Corrections` — never rewrite an AC from a
   Log waiver — or, in program flow (ROADMAP.md exists), to abandon the
   milestone under the Milestone abandon procedure — listing the
   orchestrator's recommended option first marked `(recommended)` with a
   one-line reason drawn from the review evidence and the `cap` lenses for
   `wave <N> cycle <C>`, whose `lenses` Log event is recorded first. Never
   choose silently. Record the ruling as for an all-refuted ruling,
   appending `wave <N> cycle <C> cap ruling: <selected option verbatim>` and
   checkpointing with subject `build: record wave <N> cycle <C> cap ruling`,
   body `Why: persist the cap ruling before acting`, and `Wave: <N>`.
   At the cap, or when the user selects `Reconsider the component approach`,
   first brief in chat: the existing invariant or contract; findings grouped
   by suspected design decision, marking uncertain grouping; accidental and
   adversarial inputs only as INTENT.md authorizes; SYNTHESIS.md settled
   constraints; and options to keep the approach with justification, fail
   closed at a boundary, or replace the approach or source of truth. Name
   residual-risk acceptance only through existing user or define authority;
   it never waives an AC. Keep the cap options above, including program
   abandon. A strategy change follows the existing plan-defect, build
   recovery, or define-Corrections route, never the cycle cap alone. Its
   class-wide AC states the rule, not a substitute list of examples; examples
   go in Context or Approach. Its Verify uses suitable structural or generated
   checks and never claims finitely many tests prove a universal rule.
   When the canonical verdict is `pass` and `wave-N.cycleC.panel.md` reports
   `Actionable` greater than 0, do not advance silently: present **Outcome**
   with the canonical pass plus the actionable count, **Review** linking the
   panel file, and **Next** listing `Open fix tasks from panel findings
   (recommended)` first, then `Advance and keep panel findings as warnings`.
   Answer the `panel` lenses for `wave <N> cycle <C>` and record the
   `lenses` Log event first. They may move the recommendation to the advance
   option only when no finding's consequence reaches an approved criterion
   or the security exception, judged by consequence and severity rather than
   by reviewer agreement; say which findings. Record the ruling as for an
   all-refuted ruling, appending `wave <N> cycle <C> panel ruling: <selected
   option verbatim>` and checkpointing with subject `build: record wave <N>
   cycle <C> panel ruling`, body `Why: persist the panel ruling before
   acting`, and `Wave: <N>`.
   Preference-only panel warnings do not block advance. On pass, checkpoint
   the review artifact, the panel file when present, STATE.md, and wave
   bookkeeping, then report `wave N/M done, C review cycle(s)`.
   Opening fixes from a panel preserves the canonical reviewer's `pass`.
   A later review cycle does not turn an earlier pass into a blocked verdict.
   Completion and archive validation require every last-cycle lens and task
   verdict to pass while preserving the earlier recorded verdicts.

## Completion

Run `python3 <absolute dispatch_driver.py> complete --repo <absolute primary>`
even when the coders used native child tools. Follow the build skill's Completion
handoff. The runtime owns landing evidence, transition, and checkpoint; ship owns
project Verify. Never repeat a proven operation in a manual sequence.
