# PR fix lane, revision 4: explicit round ownership and early final validation

Status: accepted (2026-11-18), implemented in fork 1.7.1.67. It incorporates
three review-panel rounds:

- the panel on the lane decisions;
- the panel on draft 1 of this ADR;
- the panel on sections 1 and 2 of draft 2.

Fork-only.
It revises [ADR 0006](0006-pr-review-fix-lane.md). It keeps that ADR's frame:
the archive undo, re-preparation, rebind, exact-lease publication, the ruling,
the delta wave, and the conversation answers. It also keeps generated views as
the lane's final step (the owner's decision, panel option 2B).

## Context

The lane ran its first pilot on two consumer milestones: six rounds, two of
them on a second member PR. The proportional middle worked. A round's fixes
landed and passed their delta review in about 20 minutes with one coder and
one reviewer, against 7 to 8 hours for the route the lane replaces. Of the
rounds that reached `prepare-final`, one finished with views (four agents in
total). The others fell back to the ordinary final review. The slowest round
took 2 hours 30 minutes, and 12 of its 15 agents were final-review agents.

The pilot produced thirteen fork releases in two days (1.7.1.53 to 1.7.1.66).
The panel traced most lane defects to two decisions:

1. **Round membership is inferred from plan geometry.** Delta waves stay in
   `PLAN.md`, and four readers infer round ownership from wave positions:
   - the plan gate;
   - the views;
   - the regression exemption (`build_state._pr_fix_lane_task`);
   - the wave gate (`delta_errors`).

   Releases 1.7.1.57, 58, and 59 each added a rule to that inference. The
   readers still disagree, and the legacy guess picks the wrong wave once all
   work is done.
2. **The wave gate accepts a delta review weaker than the final gate
   requires.** Missing affected-criterion fields, a missing Surface, or
   range-cited evidence tags surface only at `prepare-final` and cost a full
   ordinary final review. Release 1.7.1.62 patched one case in the wrong
   direction: it made the generator tolerate a thinner delta. The
   range-citation case is still open.

The same pilot found smaller defects in the same code:

- `start` drops the uncommitted STATE.md Log and ship lessons on every round.
- `close-members` reads the PR before GitHub shows the pushed head.
- Approval comments have no capture path.
- The delta parser accepts duplicate or contradictory conclusions.
- Archived provenance does not recompute the baseline chain.

## Decision

### 1. Round membership is an approved plan artifact

**The membership file.** A lane round owns a helper-written file,
`.project/plan/pr-fix-round-<n>.json` (schema `gsd-path/pr-fix-membership/v1`),
holding:

- the round number, the PR, and the snapshot and ruling digests;
- the approved grouping: per ruling group, its items, its `P###` findings, and
  the one task that closes them;
- the gap set the round's capsule carries. This is the only gap source for the
  wave gate and for views. A capsule that disagrees with it is refused.
- `tasks`: each initial task's id, wave, and contract digest;
- `repairs`: each repair task's id, wave, contract digest, and the path and
  sha256 of the review that opened it.

**Contract digest.** Landing allows two changes to a task file: the five
lifecycle fields (`status`, `agent`, `base`, `worktree`, `task_branch`) and an
appended body suffix (`isolation.py` `_task_contract_error`). The contract
digest therefore covers:

- the immutable frontmatter (`_immutable_frontmatter`);
- the body as bound, recorded with its byte length.

The check recomputes the digest over the same frontmatter and the first
`length` bytes of the current body. Log lines appended after binding do not
change it.

Every lane task also carries `lane_round: <n>` in its frontmatter. Landing
already refuses any change to that field, because it is not one of the five
lifecycle fields (`isolation.py` `_immutable_frontmatter`, `_task_contract_error`).

**Binding.** `pr_fix_lane.py bind --repo <root> [--repair --source <review>]` is
the only writer of the membership file.

- It reads the open round, the ruling, PATCH-FINDINGS.md, and the task files.
- It checks both directions before writing: the field against the grouping,
  and the grouping against the field.
- It writes atomically and is idempotent: the same inputs rewrite identical
  bytes.
- After approval, changed inputs are refused, never silently rebound. The
  refusal says which case applies:
  - an unapproved file that `bind` can repair;
  - an approved file that needs a new approval.

**Approval anchors the binding.** Agreement between the membership file and the
task fields proves consistency, not approval. The approval therefore records
the membership digest it approved.

- **Initial plan.** It is approved with `approve --kind plan --patch`
  (`state_checkpoint.defer_approval`), which writes STATE without a commit or an
  artifact digest.
  - Under the state lock, before it writes STATE, the deferred approval writes
    `approval.json` into the round directory with the membership digest. The
    round directory sits in the Git common directory, which the guard keeps
    away from agents. The `patch plan approved` event text stays unchanged,
    because three readers match it exactly.
  - Build entry (`dispatch_driver` `enter`) recomputes the digest and compares
    it with the recorded one before its `build: start milestone` checkpoint.
    The commit then carries the tasks and the membership file together.
  - A paired change to a task and to the membership after approval no longer
    matches the recorded digest, so build entry refuses it.
- **Repair.** Most repairs need no new approval. A blocked delta review runs
  `fix-tasks`, which writes repair tasks mechanically from the recorded review.
  - When every source task of a batch is a member of the open round, the repair
    task gets `lane_round: <n>`. `fix-tasks` then runs `bind`, which appends
    the repair with its contract and the path and sha256 of the source review.
  - A repair whose fix site falls outside the batch files goes through build
    recovery and the checkpoint approval (`checkpoint_approval`). There
    `bind --source <review>` must finish before the checkpoint journal is
    prepared, so the journal's artifact digest covers it. A bind after
    preparation trips the resume drift refusal, and its message names the
    recovery: bind, then prepare the checkpoint again.
  - The approval digest covers the membership without `repairs`, so appending
    a repair never invalidates the approved part. A recorded repair is frozen:
    its contract and source digest cannot change.

**Serialization.** The state lock is a `flock` on each worktree's own
`.project`, so it does not serialize across worktrees. Lane commands that
write the round record (`bind`, `fall-back`, and later `start` and `views`)
take a lane lock in the Git common directory instead. Contention fails closed
with "another PR fix lane command holds the lane lock; retry". The round record
keeps only identity, stage, the capsule, and the approval receipt:

- Membership lives only in the committed membership file.
- Validators never write the record. The 1.7.1.58 `_record_first_wave` side
  effect is deleted.

**Crash and retry windows.**

| Window | Behavior |
|---|---|
| Task files written, membership not bound | The plan gate refuses with the exact `bind` command. |
| Bound, then crash before approval | The file is an unapproved artifact. The gate revalidates it on retry, and approval records its digest or refuses. |
| Deferred approval written, build entry not started | Build entry compares the recorded digest before the transition. |
| Build entry transitioned STATE, checkpoint not committed | Today `enter` returns on `build/active` without committing. `enter` now detects an active build with no `build: start milestone` commit since the plan approval. It re-validates the digest and completes the checkpoint before any dispatch. |
| Repair bound, journal prepared, commit pending | The checkpoint resume refuses on artifact drift, so the membership must equal the journaled digest. |
| Two worktrees run lane commands | The lane lock serializes them, and the loser retries. |
| Retry after any commit | `bind` reproduces identical bytes. Nothing is appended twice. |

The `enter` window also exists upstream, where `enter` is identical, so its
fix is its own upstream-applicable commit.

**Gates read membership.** Every check runs in both directions: every member
task has `lane_round: <n>`, and every task with the field is a member.

- **Plan gate:**
  - the membership file matches the ruling digest and PATCH-FINDINGS.md;
  - every `P###` is closed by exactly one initial task, and every group's items
    by that group's task;
  - every member task's contract digest matches the bound one;
  - member tasks sit in delta waves that hold no other tasks, after every other
    wave;
  - delta waves owned by no open round must be done or withdrawn (history),
    except as the legacy rules below allow.
- **Regression exemption:** only member tasks of the open round, while its
  stage is `opened` or `built`. Never after `fell-back`.
- **Wave gate:** a delta review is validated against the round that owns its
  wave, open or closed, through that round's active or archived membership
  file.
- **Views and threads:** read the membership file. `_lane_waves` and
  `_first_round_wave` are replaced, not kept beside it.

**Legacy rounds.** An open round without a membership file is never guessed.
The plan gate refuses with the exact command
`pr_fix_lane.py fall-back --repo <root> --reason "<why>"`. What follows depends
on how far the round got:

| Round state at upgrade | After `fall-back` |
|---|---|
| No lane task landed, or some landed | Landed contracts stay untouched. Each unfinished task moves to a new standard wave (`Review depth: full`) through the ordinary plan route; the plan gate's refusal names that edit. The exemption ends at `fell-back`, so the moved tasks run with regression reruns and an ordinary wave review. |
| All lane tasks landed | The delta waves are history. The ordinary final review follows. |

- A landed delta wave keeps its depth, so its recorded delta review stays
  valid history.
- Nothing requires an owner withdrawal citation that nobody gave.
- Closed legacy rounds in archives are read by a schema-aware read-only reader.
  Their task contracts are never backfilled.
- `first_wave`, the legacy guess, and the geometry rules are deleted. The tests
  that assert them are replaced in the same commit: `tests/test_pr_fix_lane.py`
  (first-wave cases) and `tests/test_pr_fix_lane_cycle.py` (legacy-guess case).
- Releases reach consumers at milestone boundaries. Forced mid-milestone
  upgrades happened in the pilot, so all three rows are tested, not assumed
  unreachable.

### 2. One content validator, applied at the wave gate and at ship

**The split.** `check_handoffs.validate_final` splits into two parts.

- `validate_final_content(contract, final_text, gaps, deferred=())`:
  - `contract` is a `FinalContract` built once by `final_contract(root,
    project_dir)`. It holds the ordered criteria with their INTENT text, the
    evidence tags, and the criterion-to-Surface map that `criterion_surfaces`
    resolves from INTENT, PLAN, and ROADMAP.
  - `gaps` maps gap number to text.
  - It checks:
    - the criteria set, with the same sorted comparison the gate uses today
      (the extraction does not add a textual-order rule);
    - heading text and verdicts;
    - the Surface, Check, Observed, Reference, Finding, and Fix-direction
      fields;
    - evidence tags and placeholders;
    - gap numbering, headings, and fields;
    - the internal agreement of FINAL and gap headers;
    - overall-verdict consistency.
  - `deferred` names gaps whose structure is checked but whose verdict is
    excluded from overall consistency. Only the wave gate passes `(1,)`. Ship
    passes nothing.
  - It reads no files, Git, or member tips.
- The wrapper keeps everything else:
  - `_require_state` (`ship/active`);
  - safe artifact reads, with symlink checks;
  - gap-file discovery. Malformed names and duplicate gap numbers are refused
    before the map is built, so the map cannot hide them.
  - the live HEAD and member-tip comparison;
  - lane provenance, still skipped when a caller passes `final_text`, as today.

  It then calls the content part.
- Differential tests feed the same corpus of accepted and rejected FINAL
  reports and gap sets through the old and the new ordinary gate. The corpus
  includes Surface rules, evidence tags, malformed and duplicate gap names,
  header disagreement, placeholders, overall verdicts, and the `final_text`
  provenance case. The extraction must not change either set.

**The parser.** The delta review parser keeps ordered entries. It refuses a
duplicate item, criterion, or gap identity, and requires non-placeholder
`Evidence` for every closed item. Contradictory conclusions are refused, never
overwritten.

**Dispatch pins the reviewed vector.** Today a review dispatch records member
heads only for `current-product` scope (`dispatch_driver.py`, `member_heads`).
A delta review also records them, together with the coordinator base. The
delta brief gives the reviewer:

- these values;
- the membership digest;
- the round's member task list.

**Cumulative assessment.** Today a repair wave gets no review file of its own.
The next cycle of its source wave judges it (`build_state._unreviewed_waves`),
and that cycle still lists exactly the source wave's tasks under
`Tasks reviewed`. The lane keeps both rules. The first delta wave's latest
cycle is the round's cumulative assessment, and it must be the latest cycle
and pass; an older pass behind a newer blocked cycle never counts. That cycle
carries three more header fields:

- `Round tasks:` every member task id, initial and repair, in membership
  order;
- `Membership: <sha256>`;
- `Reviewed HEAD: <coordinator>` and one `Member reviewed HEAD: <member> <sha>`
  per member.

**The wave gate.** At the last passing cycle of the round's first delta wave,
the gate:

1. Checks that the three header fields equal the membership file, its digest,
   and the vector pinned at dispatch, and that every listed task has landed.
2. Renders the candidate FINAL.md and gap reports in memory with
   `build_final_view` and `build_gap_view`, from the owning round's capsule,
   ruling, task-to-finding mapping, and the delta. Gap 1 (project Verify) is
   rendered from the capsule baseline with consistent headers and real
   structural fields. It is passed as deferred, never as a fresh Verify
   result, and the validator's placeholder rule is not relaxed for it.
3. Runs `validate_final_content(..., deferred=(1,))` on the candidate. Any
   content error refuses the review inside the existing review cycle, with the
   validator's message.

**At ship,** `views()` repeats the full check on the real inputs:

- **Coordinator equivalence.** Every coordinator commit after the reviewed
  HEAD must satisfy all of these:
  - it is a non-merge commit;
  - it changes only `.project/` bookkeeping, outside INTENT, PLAN, and the task
    files, or installer paths;
  - it is resolvable.

  The passing-review checkpoint and the build completion are such commits.
  Contract files are covered separately by section 3 and the membership
  digests. This is a new lane predicate. The generic installer-only
  `reviewed_head_covers` keeps its contract for its other callers. Treating a
  runtime pin change as non-product is an explicit premise of this predicate,
  not a hidden one.
- **Members.** Member tips must equal the reviewed vector exactly.
- **Stale product.** The 1.7.1.66 stale-product check stays as a second guard.
- **Full validation.** The regenerated FINAL.md and gaps, with the fresh gap 1,
  pass the full `validate_final`.

The wave-gate pass is necessary, never sufficient.

**The generator** inherits only what a local fix cannot change: unaffected
blocks, and the Surface label of an affected criterion. It never inherits
evidence for an affected claim.

**Outcomes.**

- The round's stage name for a fallback stays `fell-back`. The separate
  `outcome` field records `views`, or `fallback` with a reason: `new-risk`,
  `non-local`, `stale-product`, `content`, or `provenance`.
- Wave-gate content refusals are counted by reason.
- A `content` fallback at ship after this change is a validator defect. Two of
  them reopen panel option 2C (a lane final reviewer writes FINAL.md).

### 3. Provenance is recomputed from archived files

`views()` archives the round directory into `review/pr-fix-<n>/`:

- the capsule inventory, with each file's sha256;
- the membership file digest;
- the delta's reviewed vector;
- the landing set;
- the pointer to the previous round's provenance.

`validate_views` recomputes the capsule digest from the archived baseline
files, walks the prior-round chain, re-proves the membership digest, and
checks that the landing set covers the membership. It no longer trusts the
output hashes alone.

`views()` also compares contracts against the capsule:

- INTENT must equal the capsule's recorded digest.
- PLAN may differ only by the round's membership waves and tasks; any other
  change refuses with `provenance`.

Archived validation needs no common-directory cache. Negative tests tamper
with each of: a baseline block, the membership file, a landing receipt, and
the prior-round pointer.

### 4. The continuation survives the archive undo exactly once

Before the undo, `start` writes `continuation.json` into the round directory:

- the exact STATE.md Log lines added since HEAD;
- the ship-appended lessons, identified with the existing
  `pipeline_undo._ship_lessons`;
- a digest of both.

It is written once. A `start` resumed after a crash keeps the first receipt.

After the undo:

- **Log lines.** The receipt is copied byte for byte into
  `.project/review/pr-fix-continuation-<n>.md`, so the archive carries them.
  The lane event that blocks ship ends with `continuation restored: <file>
  sha256 <digest>`. No new event type exists, and the `_append_event`
  single-line contract is kept.
- **Lessons.** A new helper, `pr_fix_lane.py restore-lessons --repo <root>`,
  runs first in the ship lessons step. It appends each preserved lesson line
  that LESSONS.md does not already hold, comparing exact lines, so a rerun
  appends nothing.
  - "Once" means once in the surviving archive attempt.
  - A later round's undo captures the restored lines again with the rest of
    the suffix, so they come back once more.
  - An ordinary undo outside the lane drops them, as it drops every
    ship-appended lesson.

### 5. `close-members` waits for the forge to show the pushed head

After the fork's source advance (pending close or adopted lineage) pushes the
bound branch, the pull request read can still return the replaced head. The
read waits only for an exact replaced head, taken from durable records:

- the pending close row's `previous_reviewed_head`;
- the proven adopted lineage before the reviewed head.

Both survive a restart after the push.

- **Wait.** While the bound branch's open, unmerged pull request still shows
  one of those heads, the read retries with backoff (1, 2, 4, 8 seconds), up to
  30 seconds in total. Each wait is logged.
- **Refuse at once.** Any other head, or a closed or merged pull request,
  refuses immediately.
- **No second push.** A stale read never triggers a push. After the bound, the
  close refuses with "the push is done, rerun close-members later".

This is fork-only. Upstream `close-members` never advances an open pull
request's source, so it has no read after its own push.

Tests:

- a stale read that clears;
- a permanently stale read;
- an unrelated head;
- a closed stale pull request;
- a restart after a successful push.

### 6. Approval comments complete their lifecycle

**Capture.** With no active change request, `snapshot --record-only` selects
the pending member PR whose approval or comment reviews on the published head
carry top-level inline comments that no lane reply answers yet. Those comments
are the items; review bodies are not. The round records `approval: true`.

**Ruling.**
- The owner's ruling is still required, through `rule`.
- The orchestrator proposes `follow-up` with the reviewer's text as the note,
  except for an item that shows a recorded criterion or gap failing. Ship
  step 2 already forbids `follow-up` for that case. This is judgment, so no
  helper default is written.

**Answers.**
- `threads` answers on the unchanged published head; an approval round is
  exempt from the new-head requirement.
- Markers prevent duplicate answers, and an answered comment is not captured
  again.

**Parking.**
- `parking.py sync` already imports every ruled follow-up at the next define,
  approval rounds included.
- `ship-check` refuses while a captured round's items are unruled, so no item
  disappears with a closed round.

### 7. Hand-off compatibility rule

The hand-off stays locked to the ruling digest, with the 1.7.1.65 quoting and
the 1.7.1.66 preflight. Any future change to hand-off generation must:

- keep or extend `pr_fix_lane.py regenerate`;
- add a test proving that a round opened by the previous release still passes
  the patch gate after the upgrade.

### 8. Release gate

The release ships only after the following pass.

- **Focused tests:**
  - binding:
    - the crash matrix, on both the deferred and the checkpoint path,
      including a crash between the build-entry transition and its checkpoint;
    - membership skew, in both directions;
    - a paired change to a task and to the membership after approval;
    - a regrouping without re-approval;
    - a bind after the checkpoint journal is prepared;
    - a contract digest that survives appended Log lines;
    - two worktrees racing on the state lock;
  - gates:
    - the stage-limited regression exemption;
    - historical validation during round two and after closure;
  - wave gate and views:
    - a repaired round reaching views, and a cumulative cycle that omits a
      repair task refused;
    - a newer blocked cycle invalidating an older pass;
    - a deferred gap 1 accepted at the wave gate, while the same report
      without a fresh gap 1 is refused at ship;
    - a passing-review checkpoint and a build completion after the reviewed
      HEAD reaching views, while a product commit in the same interval
      refuses;
    - the stale vector after an adoption;
    - the content-validator extraction equivalence;
    - delta fixtures: an omitted Surface, an evidence range, a missing
      judgment, an affected gap, a duplicate conclusion, and an empty item
      evidence;
  - continuation across two consecutive rounds, with a crash on each side of
    the append;
  - an approval-only capture with an unchanged-head reply;
  - the forge stale-read cases;
  - legacy rounds:
    - an open legacy round refused, then fallen back, in each of the three
      states: none landed, some landed, and all landed;
    - a closed legacy archive validated read-only;
  - the replaced `first_wave` and legacy-guess tests removed in the same
    commit.
- **An end-to-end cycle with the guard hooks installed:**
  - two members, one integrated before the other;
  - three rounds, including a repair, a fallback, and a second round;
  - a member conflict resolved by a rebase adoption;
  - a reviewer reply inside an earlier conversation;
  - an approval with inline comments;
  - a forge that reports the old head once after a push.

**Contracts updated in the same release:**

- ADR 0006: mark the positional ownership and lesson-regeneration statements
  superseded;
- the task template (`lane_round`);
- the plan contract (`bind`, membership);
- the build contract (exemption, repair binding);
- the delta-review template (round tasks, membership digest, reviewed vector,
  enforced fields);
- the ship contract (restore-lessons, outcomes, approval rounds).

**Metrics per round:**

- recorded in the round record: the outcome (`views`, or `fallback` with its
  kind);
- read from the gate receipts, because validators never write: membership-skew
  refusals and wave-gate content refusals by reason;
- read from the session logs: elapsed time and agent count;
- ADR 0006's safety measures: the affected mapping, Verify coverage of fixed
  surfaces, and missed surfaces.

**Rollout:** at milestone boundaries, never by a forced upgrade.

## Consequences

- Ownership is a committed, approved artifact. A wrong `lane_round` or a
  stale binding is a visible refusal with one named command, not a
  misclassification.
- A malformed delta costs one review cycle instead of a full final review.
- A repaired round can reach views, and a product change after the review
  cannot reuse it.
- Archives prove their own lane chain without local caches.
- Orchestrators stop re-recording Log lines and lessons by hand.
- Approval comments are answered and parked without hand-written files.
- The release is large. It replaces four inference rules and three
  hand-maintained recoveries with one artifact, one validator, and one
  receipt, and it adds more tests than code.

## Tripwires

- Two `content` fallbacks at ship after this release reopen option 2C.
- Membership-skew refusals outnumbering the geometry failures of the pilot
  reopen a single-source variant (the task field only).
- Any wrong `unaffected` mapping that reaches a merged PR suspends the lane,
  following ADR 0006's kill criterion.

## Not decided here

- A versioned semantic hand-off contract instead of byte equality. Reopen it
  if a hand-off stranding recurs despite section 7.
- A sanctioned mid-milestone runtime upgrade with a receipt. This is a
  separate decision if forced upgrades recur.
