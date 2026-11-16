# Post-review fixes to an open member PR use a proportional fix lane

Status: accepted (2026-11-16), revision 3 as implemented, after two rounds of a
two-model design panel. Fork-only. Upstream `main` (`d5a30f5f`) has no route for a forge
review that requests changes on a milestone PR.

## Context

A member in `pull-request` integration closes through one PR per milestone.
Ship prepares the archive, and `close-members` opens the PR. The coordinator
ship commit waits for the human merge (`integration.py` `close_members`).
When the PR reviewer requests changes, the only route back to code is the one
built for a red required check:

1. Undo the uncommitted archive (`pipeline_undo.py`, kind `uncommitted-archive`).
2. Run a fresh final review at the unchanged, already-approved HEAD, so the
   review items become gap verdicts and `PATCH-FINDINGS.md`.
3. Write a patch plan: planner, plan panel, approval.
4. Build. Every landing reruns the earlier task Verifies that share a file and
   are not already covered (`build_state.regression_verifies`).
5. Run a wave review, then a second complete final review at the new HEAD.
6. Prepare the archive again. `close-members` rebinds the pending close
   (`pending_close_can_advance`, `previous_reviewed_head`) and lease-publishes
   the proven task landings onto the same PR (`_advance_pending_member_source`).

Measured on consumer runs (2026-11):

- One return with six review items took seven to eight hours and about twenty
  agents.
- One landing spent 38 minutes on regression Verifies for a three-minute coder
  change.
- A one-line fix costs about three hours, of which about 25 minutes are
  essential. Steps 2 to 5 re-prove what did not change, and the PR reviewer
  reviews the delta again anyway.

Smaller milestones do not remove this cost. Across 30 measured milestones,
cost per task does not grow with milestone size, and every return pays steps
2 to 5 again.

The frame (steps 1 and 6) is sound and tested. Revision 1 of this ADR worked
on top of the prepared archive with addenda; the panel found that this breaks
the archive inventory, the FINAL-pinned reviewed heads, source-advance proofs
and `prepare-final`. Revision 2 kept the frame and replaced only the middle.
Revision 3 specifies the contracts the second panel found missing.

## Decision

An opt-in **PR fix lane** replaces steps 2 to 5 for one review round whose
selected items are all local fixes. Steps 1 and 6 keep their transactions,
journals, leases and validators. One helper, `pr_fix_lane.py`, owns lane entry,
lane state and the lane's acceptance outputs. Router, plan, build and ship call
it, and do not reconstruct its steps.

### Entry and round identity

The lane enters only when every condition holds:

- the project enabled it in Path settings (it is off by default);
- STATE is `ship/active` with a prepared archive;
- every member with a pending close is a GitHub `pull-request` member;
- that close row is `pending`, its recorded PR is open and unmerged, and the
  PR head equals the row's reviewed head;
- the PR has an active `CHANGES_REQUESTED` review;
- the prepared archive's `FINAL.md` and gap reports validate at the current
  HEAD with every verdict `met` or `pass`;
- fewer than three lane rounds were already recorded for this milestone.

Anything else uses the ordinary route. An Azure member, a single-repo PR, or
`external-landing` integration gets an error that names the ordinary route.
Azure joins after its fixtures and a live run pass.

A round is identified by: PR identity, PR head, the ids of the reviews and
comments it answers, and the digest of its snapshot. The helper writes this
identity before any destructive step.

### Baseline capsule (before undo)

Undo resets tracked `.project/` files to HEAD. It also deletes the untracked
archive and its publication records. The lane's baseline would therefore be
lost. Before undo, `pr_fix_lane.py open` writes an immutable **capsule**,
atomically, in the Git common dir. Resume discovers it there, without the undo
response. The capsule contains:

- `FINAL.md` and every `final-gap-N.md`, byte for byte;
- the revision vector: the coordinator HEAD and every member reviewed head;
- the hashes of `INTENT.md` and `PLAN.md`;
- the last Path publication record and the live PR body;
- the forge snapshot: the review, its comments, the PR head, and the PR body;
- the user's ruling, once it is given.

The capsule content is copied into `review/pr-fix/<round>/` when the archive is
prepared again, so the manifest covers it. Historical validation reads the
archived copy. The common-dir copy is only a resume cache.

Lessons need no new code. Undo drops the lines that ship appended, and ship
appends them again when it prepares the archive (`pipeline_undo.py` effect
text; ship SKILL archive step 3).

### Triage and approval

The orchestrator reads the snapshot. It confirms each item against the code at
the PR head, and proposes one class per item:

- `fix`: local, with no change to a success criterion, interface contract, or
  design decision;
- `follow-up`: left for a later milestone. This class is refused for an item
  that shows a recorded criterion or gap to be failing;
- `redesign`: everything else.

It also proposes the grouping of `fix` items into tasks. One user question
confirms the classes, the selection and the grouping. If any selected item is
`redesign`, the round uses the ordinary route and the capsule is discarded.

### State path

1. `pr_fix_lane.py snapshot` checks entry and writes the capsule and round
   identity. It changes nothing in `.project/`.
2. `pr_fix_lane.py start` records the ruling and runs the existing
   `uncommitted-archive` undo. It then writes
   `review/pr-review-<n>.md` and `PATCH-FINDINGS.md`, and blocks ship
   (`ship/active → ship/blocked`, event `PR fix lane opened: round <n>; ...`).
   Each stage is journaled, so a crash resumes. From `ship/blocked`, the
   existing patch route applies unchanged:
   - the router opens plan patch mode;
   - `patch plan reopened` moves the state to `plan/active`;
   - `approve --kind plan --patch` runs;
   - build's patch re-entry checkpoint commits the hand-off.

   The patch validator accepts `pr-review-<n>.md` as a source only when it
   is byte for byte what the helper derives from the active round's snapshot
   and ruling. Identifiers stay contiguous (`P###`).
3. The orchestrator writes one task per group. There is no planner agent and no
   plan panel. Each task lists the items it closes and names a test that fails
   before the fix; a docs-only item, or one that states why it cannot be
   tested, is exempt. The tasks form one appended wave.
4. The ordinary `pipeline_state.py approve --kind plan --patch` runs. The
   ruling is its input. The approval binds the ruling to the digest of the task
   set; a changed grouping or scope requires the question again. The Log
   records the ruling verbatim.
5. Build runs the wave (below). After the lane wave review passes, build
   completes to `ship/active`, as today.
6. Ship runs in lane mode (below), then prepares the archive and calls
   `close-members` as today.

### Build

Lane tasks land through the ordinary dispatch driver. A landing runs its own
Verify. `regression_verifies` returns no command only when the task belongs to
the active lane round: the plan stay was opened by the lane event, the task
lies in the approved lane wave, and the capsule digest matches. A `lane` value
in any other task is refused by the task gate. The project Verify at ship
covers regressions.

The lane wave carries `Review depth: delta`, the wave-scoped policy. The plan
gate accepts it only in an open lane round, and only for the last waves of the
plan (repair waves inherit their source wave's depth). The panel rule already
applies only to `full` and `deep` cycles, so a delta cycle owes no panel in
dispatch, readiness, or archive validation. A delta wave review is one
**delta reviewer**, with no panel. Earlier waves keep their own policy and
evidence obligations. The delta reviewer reads:

- the lane diff, and its exact revision vector;
- the snapshot items;
- the capsule's `FINAL.md` and gap reports.

It returns a canonical wave report with these conclusions:

1. Per item: closed or not closed.
2. Eligibility: the landed diff changes no success criterion, interface
   contract, or design decision.
3. Per success criterion and per numbered gap risk: `affected` or `unaffected`,
   with the reason. An affected one gets a delta verdict with checked evidence.
4. Risk inventory: the old list is still complete, or new numbered risks are
   listed and evaluated.

Findings go back to the coder once. The lane leaves the round and falls back
to ordinary review and planning in three cases:

- a second failure;
- a failed eligibility check;
- any `not-met`, `unverifiable` or blocked delta verdict.

Landed lane tasks are ordinary tasks, so the ordinary route adopts them. It
discards nothing, and its full final review re-reviews them. The lane sets
`ship/blocked`, and its question names that route.

### Ship in lane mode

`prepare-final` gains a lane mode (`lean_verification.prepare_final`), used
only for the active round. It runs `verify_project` as today, and
`final-gap-1.md` stays the fresh project Verify view. Instead of `reuse_final`
(which refuses member milestones) or a dispatch of `review_final`, it calls
`pr_fix_lane.views`. On any refusal it returns `lane: fallback`, and ship runs
the ordinary final review. The refusals include a `not-local` eligibility and
a `new` risk inventory: the lane never generates a gap for a risk the
baseline did not review. That helper generates `FINAL.md` and the other gap
reports at the new head and the new member heads:

- criteria and risks marked `unaffected` keep their capsule verdict blocks
  verbatim, marked as inherited from the capsule head;
- affected ones carry the delta verdict;
- a `## PR fix rounds` section lists the round identity, each item with its
  class and task, the ruling, and any pre-shipment override.

The generated outputs carry a provenance block. It binds them to: the capsule
digest, the snapshot and ruling digests, the approved task set and its proven
landings, the revision vector the delta reviewer read, the project Verify
execution at that vector, and the delta report digest. `views` refuses to
generate them in four cases:

- a lane task is not proven landed;
- the delta report is missing, stale against the revision vector, or does not
  map every criterion and risk;
- the project Verify did not pass at that vector;
- the provenance does not chain.

`check_handoffs final` validates the views and their provenance from the files
that will be archived. Generation is deterministic and can rerun.

Ship then runs the archive transaction as today, followed by these lane
additions:

- **Publication.** Member pushes happen only through
  `_advance_pending_member_source`. It already proves the PR identity, the
  open and unmerged state, the merge provenance, GitHub auto-merge and merge
  queue, and an exact lease (`integration.py`, `azure_repos.py`).
- **PR description.** The existing editorial rules apply: a stub body is
  replaced, and a substantive body is kept. The capsule keeps the last
  publication record, so a later revision can stop and ask when the live body
  differs from it. That comparison is a follow-up and is not implemented.
- **PR conversations.** After the push, `threads` posts one COMMENT review
  that lists every item with its disposition, so body-only items are answered
  too. Inside it, each inline item gets a reply: a fixed item cites the
  published head and its thread is resolved; a follow-up item gives the
  owner's reason and stays open. The reviewer checks the resolved
  conversations and reopens any that are not fixed. A remote marker per reply
  and per round makes a retry skip what was posted, and a pending review a
  crash left behind is replaced. One review instead of one reply per item
  matters: GitHub files each separate reply under its own review.
- **Before `record-shipment`.** The helper re-reads the review state. A change
  request that is still active, from any reviewer, blocks until it clears or
  the user rules. That ruling binds the published head and the exact set of
  outstanding requests. A new request invalidates it, and the ruling is
  recorded in `## PR fix rounds`. Unresolved discussion threads are not
  change requests.

### Rounds

A new change request on the new head is a new round with a new identity. The
third round is the last. A fourth change request uses the ordinary route,
unless the user explicitly overrides that.

## Invariants kept

- Steps 1 and 6 keep their transactions, journals, leases and validators. A
  crash leaves capsule, undo, task, or close state, and each of them resumes.
- Every new member commit on the PR is a proven task landing, published
  through the existing source advance.
- Every criterion and gap in the shipped `FINAL.md` is in one of two states:
  - reviewed at the shipped head;
  - inherited, backed by a delta report at the shipped vector that marks it
    unaffected, and by a passing project Verify at that vector.
- The original verdict text survives verbatim in the archived capsule.
- Archives are immutable after the ship commit. Default-off projects and
  non-lane waves behave exactly as today.

## Considered options

- **No change:** correct, but costs a full milestone cycle for each round.
- **Review items straight into the patch plan:** removes step 2 only. About
  half of the cost remains.
- **A return milestone, full or quick lane:** adds lifecycle machinery while
  the member close is pending. Small fixes still cost two to three hours.
- **Lane on top of the prepared archive with addenda (revision 1):** breaks
  the archive inventory, the reviewed-head readers, and source advance. It also
  needs a sealing journal.
- **Fix outside the pipeline:** source advance refuses the unproven commits.

## Consequences

- The delta gets a targeted review, not a complete final review. A wrong
  `unaffected` mapping can let a regression through on a surface the project
  Verify does not exercise. The project Verify does not run every skipped
  task Verify.
- During the pilot, each round records four things:
  - the affected mapping;
  - whether the project Verify exercised each fixed surface;
  - every later finding that the mapping missed;
  - the misclassified items.

  The lane goes back to off if either holds:
  - more than one in five rounds has a misclassification or a missed affected
    surface;
  - a lane fix breaks a surface that the project Verify did not cover.
- New code:
  - `pr_fix_lane.py`, covering the capsule, round identity, snapshot,
    start, views, provenance, replies, and the pre-shipment check;
  - the `pr-review` patch source in `check_handoffs.py`;
  - the `delta` review depth, with its plan, wave, and final-provenance gates;
  - the round-scoped regression exemption in `build_state.py`;
  - no panel and a delta brief in `dispatch_driver.py`;
  - the lane mode of `prepare_final`;
  - the `pr_fix_lane` setting, user or project, `off` by default and never
    locked by ship;
  - the `delta-review.md` and `pr-review.md` templates.
- The PR-body comparison against the capsule's last publication is not
  implemented yet; the existing editorial rules keep a substantive
  human-edited body. Inline replies follow the publication's editorial
  language.

## Acceptance

- Synthetic fixture tests for every new helper and gate.
- Default-on-panel fixtures proving that only the authorized lane wave is
  exempt from its panel.
- A crash and fallback matrix:
  - a crash after the capsule is written and before undo;
  - a crash after undo and before its response is consumed;
  - crashes at landing, delta review, view generation, push, and reply;
  - the PR merged, closed, or moved during the lane;
  - a failed Verify, a failed delta review, and a failed eligibility check;
  - stale delta evidence after a head moves;
  - a second round;
  - two members, one of them already integrated;
  - a concurrent human edit of the PR description;
  - validation from the committed archive with no local cache.
- One live run against a private, disposable GitHub repository: change
  request, lane, republication, merge, ship, and `validate-integrated`.
- A consumer pilot on the next real change request. It records essential time,
  orchestration time and user wait separately. The targets are a one-line fix
  in 45 minutes or less, and five small items in two hours or less, end to
  end. If the pilot misses them, the lane stays off and the measurement is
  recorded here.

## Decided questions

- The delta reviewer always runs, including when the PR reviewer is
  automated. This holds at least for the pilot.
- A lane item that contradicts a recorded `met` or `pass` verdict yields one
  repeat-offender line in `LESSONS.md`, through the existing ship lesson scan.
  For this, the delta report format must be readable by that scan.

## Update 2026-11-16 (PR conversations on every route)

A consumer member PR ended with 19 review threads and no reply. The
only resolved ones were the 7 the owner asked to close by hand, and the 8
threads of the change-request round stayed open after their fixes landed and
the PR merged. A second PR showed the same: 17 threads, no reply. The ordinary
route never captured the comment ids, so it could not answer them.

Every round of a GitHub member PR now records its items. When the lane does
not apply, `snapshot --record-only` and `rule` capture the items and the
ruling (classes `fix`, `follow-up`, and `redesign`, the latter fixed by the
ordinary route) without entering the lane. A record-only round never changes
the plan, the build, or final review: the lane's gates read only lane rounds.
At republication, both routes run `threads`. `threads --plan <file>`
answers a round outside a recorded one, for a PR that predates this update.
