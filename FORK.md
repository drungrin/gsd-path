# Fork history

This file tracks differences in [this fork](https://github.com/drungrin/gsd-path)
from [upstream GSD Path](https://github.com/open-gsd/gsd-path), including changes
that can be proposed upstream independently.

Current fork version: **1.7.1.49**. Upstream package base: **1.7.1**.

## Version policy

Versions use `X.Y.Z.N`. The first three components follow the upstream version
most recently synchronized into this fork. The fourth component is a monotonically
increasing fork revision. Local features and fixes increment only that revision;
an upstream sync changes the first three components without resetting it.
A maintenance release takes the next revision on a branch cut from an earlier
release, for a consumer that cannot take main in the middle of a milestone.
It contains only the changes its entry names, and main records it here.

This is a fork version format, not npm SemVer. Install the fork from its source
checkout. The upstream npm release workflow, conventional-commit bump helper,
and npm release notes remain specific to the upstream package. A Git push does
not publish an npm package or create a release tag. Keep upstream release
records in `CHANGELOG.md`; record fork changes here.

## 1.7.1.49 — 2026-11-11

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 48 to 49. This release
contains 1.7.1.47 plus the changes that 1.6.2.48 delivered on the
maintenance branch. Main already has that release's two archive-undo fixes
(`c26bbc9c` and `c9eac8b6`).

Changes that apply upstream. Upstream main has member pull requests, the
member lock, and `prepare-final`. The probe is self-contained; the
`prepare-final` hunk needs a small context port.
- [`b868b01c — block final review when a member branch conflicts with its default`](https://github.com/drungrin/gsd-path/commit/b868b01c246c15a22b3c4bbe75ff3b9230548da2)
  — the same change as 1.6.2.48's `ba807c6b`.
- [`055ae19d — keep one unreadable member from hiding another member's conflict`](https://github.com/drungrin/gsd-path/commit/055ae19d6d73722ee61cd2ff5f2f29ed4210aca8)
  — the same change as `e009a47f`.

Fork only, because member rebase adoption exists only in this fork:
- [`87ddaf79 — adopt a conflicting member rebase through a reviewed resolution`](https://github.com/drungrin/gsd-path/commit/87ddaf79ac98612d88b4256c6352040fcf55487d)
  — the same change as `2feac105`.
- [`d88cf9a3 — bind every resolved adoption proof to its reviewed tip and review`](https://github.com/drungrin/gsd-path/commit/d88cf9a3ef2e1a71ae422bd0f3e1758717ea134f)
  — the same change as `0c363b83`.
- [`032ac70d — keep an edited resolution review abortable`](https://github.com/drungrin/gsd-path/commit/032ac70d5446d051645f72cfe0564dc49dc70e6f)
  — the same change as `becdf62c`.

See 1.6.2.48 for the behavior, the review findings, and the limits. The
maintenance branch is merged into main with the `ours` strategy again. All
five commits cherry-picked onto main without conflicts. On main, after an
undone archive, `c9eac8b6` still rebinds a pending close to a descendant
head. A rebased head does not descend from the old head, so it follows the
adoption's preserved close instead.

Validation: on main with the five commits, the full Python suite passed
(3,801 tests, 41 skipped, eight shards), the daemon suite passed (496
tests, 13 skipped), Node tests passed (118, one skipped), resource
synchronization checked 556 resources, and `check:locks` passed. The
affected modules passed 148 tests, including the archive-undo tests from
1.7.1.45. The release commit changes only version metadata.

Limits: the same as 1.6.2.48. Consumers on the 1.7.1 line get the skill
text with their next skill update, and the helpers with the runtime pin.
No upstream submission.

## 1.6.2.48 — 2026-11-11 (maintenance)

A maintenance release on the branch `chore/runtime-1-6-2-27-maintenance`:
1.6.2.32 plus a reviewed path for a member pull request that conflicts with
its default branch, and the two archive-undo fixes that path needs. It still
excludes 1.6.2.28, the upstream synchronization of 1.6.2.29, and the 1.7.1
line. Upstream package base remains `1.6.2`, synchronized through tag
`v1.6.2`. The fork revision continues from 47 to 48.

Changes that apply upstream (upstream has member pull requests and
`prepare-final`, but no member rebase adoption):
- [`ba807c6b — block final review when a member branch conflicts with its default`](https://github.com/drungrin/gsd-path/commit/ba807c6bed8664521c3ef9dc82937e4103492e60)
- [`e009a47f — keep one unreadable member from hiding another member's conflict`](https://github.com/drungrin/gsd-path/commit/e009a47fe7a5cdc7cf2323564caf365ee0ac5c73)

Fork only (member rebase adoption exists only in this fork):
- [`2feac105 — adopt a conflicting member rebase through a reviewed resolution`](https://github.com/drungrin/gsd-path/commit/2feac105a1b8db7229f2f5855ae508634573643b)
- [`0c363b83 — bind every resolved adoption proof to its reviewed tip and review`](https://github.com/drungrin/gsd-path/commit/0c363b8313cd7d1f726596942c6096c7fd02813f)
- [`becdf62c — keep an edited resolution review abortable`](https://github.com/drungrin/gsd-path/commit/becdf62c0bff8363241bdd7b7695d5c59c007fd2)

Ported from main for this line:
- [`001463aa — undo the lessons a ship appended with its uncommitted archive`](https://github.com/drungrin/gsd-path/commit/001463aa11795fe46f7e64316a60894be53095b8)
  — the same change as main's `c26bbc9c`, which applies upstream.
- [`a07e2a55 — delete an uncommitted archive's own publication copies`](https://github.com/drungrin/gsd-path/commit/a07e2a55a76a6e5f74ef76169d7caad94bc5fb31)
  — the undo part of main's fork-only `c9eac8b6`. Its close-members rebind
  is not ported: on this line, the adoption rebinds the preserved close.

### Member mergeability before final review

- In a consumer milestone, a member pull request was found to conflict
  with its default branch only when ship published it. By then final review
  had already passed, and seven pull requests from other people had reached
  the default branch.
- `isolation.py member-mergeability` fetches each locked member's default
  branch into a disposable object store and merges it with the bound branch
  there. It changes no ref, FETCH_HEAD, index, or worktree.
- `prepare-final` runs it first. A `conflict` blocks before the landing
  proof, project Verify, or any reviewer. An unreachable remote, an
  unreadable member, or an older runtime is recorded only as advisory.
  Ship reruns the probe before archive preparation.

### Conflicting member rebase adoption

- Before this release, adoption refused any replay conflict. A member
  branch whose default branch had moved into the same lines therefore had
  no legal path: the bound branch never takes a merge, and the squash proof
  refuses unreviewed resolutions.
- With `--resolve-conflicts`, each conflicting commit is replayed favoring
  the member side of its conflicting hunks. This is Git's deterministic
  merge, and every validator recomputes it. Every other replay stays exact.
  The helper stops at `awaiting-resolution` with the replay worktree and
  publishes nothing.
- `member-rebase-resolution` reports the conflicts, their three sides, the
  allowed paths, and the candidate tree.
- A coder restores only the conflicting paths. A review at
  `.project/build/member-rebase-resolution/<member>-<old tip>.md` binds the
  exact tree with `Verdict: pass`. Then `--resolution-ruling` commits the
  resolution without member hooks, proves it, and publishes it.
- Scope, conflict markers, a stale or failing review, a committed review,
  and non-content conflicts refuse. Abort still rolls back an unpublished
  adoption.
- The receipt becomes `member-rebase-adoption/v2` only when an adoption
  records a resolution: its conflicts, commit, tree, review digest, and
  ruling. The adoption commit also adds the review. v1 receipts validate
  unchanged.

### Archive undo for a reopened ship

- Undo of an uncommitted ship archive refused the lessons that ship had
  appended and the archive's editorial publication copy. The blocked
  consumer is in exactly that state: archive prepared, member PR open.
- Undo now restores those lessons and returns them for the next ship. It
  also deletes the archive's own untracked publication copies, as on main.

An independent code review of the adoption found two critical gaps and one
important gap, all fixed before release:
- a sibling commit could stand in for the reviewed resolution tip;
- a review committed early made a published adoption unprovable;
- the review's content was not rechecked after apply.

A re-review found that the content recheck could block abort, which was
also fixed. The probe's per-member masking and the quoting of non-ASCII
paths were fixed as well.

Validation: on this branch at the final tree, the full Python suite passed
(3,481 tests, 39 skipped, eight shards), the daemon suite passed (389
tests, 12 skipped), Node tests passed (118, one skipped), resource
synchronization checked 616 resources, and `check:locks` passed. The new
modules `test_member_rebase_resolution` and `test_member_mergeability`
cover apply, resume, abort, tampering, CLI, archive close, and
prepare-final. Each review finding has a regression test. The existing
adoption tests (66) pass unchanged.
- Against the blocked consumer's member repository, the simulated replay
  of its 31 commits found the 3 conflicting commits and their 10 paths
  that its pull request reports. The member checkout was left unchanged.
- The consumer sandbox's Git (2.53) supports `merge-tree -X`.
- The archive undo preview, run with this branch's helper on the consumer,
  now passes. It restores 8 appended lessons and deletes the publication
  copy.

Limits:
- Only content conflicts can be resolved. Delete/modify, rename, and mode
  conflicts still refuse.
- The resolution is reviewed, but no behavior is proven until ship reruns
  project Verify and the member final review at the new tip.
- Changes to the skill text reach a consumer only through a skill update.
  A consumer that receives only the runtime pin gets the helpers, and its
  orchestrator needs the procedure in the prompt.
- Not yet on main; the port is the next release.

## 1.7.1.47 — 2026-11-10

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 46 to 47.

Fix:
- [`dd2aa85f — validate collect expectations and settle a retired native record`](https://github.com/drungrin/gsd-path/commit/dd2aa85f8b00b0603dea57163da6af19176ab61c)
  — applies upstream: upstream main has the same unvalidated
  `--expected-destination` input, the same strict journal reader, and the
  same `finish` call outside the failure-recording block. The patch needs a
  small port, because the context in `isolation.py` and
  `tests/test_isolation.py` differs from upstream main.

### A commit SHA as the expected destination broke every status read

- In a coordinator build, the orchestrator collected six wave-1 review and
  panel reports with `--expected-destination <review base commit>`.
  `collect-artifact` accepted the value and, because each destination did
  not exist yet, completed the collection. Its journal reader accepts only
  the literal `base` or a SHA-256, so afterwards status, diagnose and build
  recovery all failed with "artifact collection journal has invalid
  expected destination".
- `collect-artifact` now refuses such a value before writing a journal, and
  its help text names the literal `base`. A complete journal already written
  with such a value is read as inert history, so the affected repository
  recovers without editing `.git`. An incomplete journal with an invalid
  value still refuses, since its retry would use the value.

### A retired native member sidecar left its dispatch record unsettled

- The owner ruled a repair task while a native member task (an operator
  pause) was halted. Its member sidecar was retired and the task reset to
  pending without `finish`. Build recovery then refused with "settle
  dispatch records before recovery", and `finish` stopped with "has no
  activated live copy" before it reached the block that records a failure,
  so the record kept a null outcome. Only `round` could settle it, and
  `round` would have resumed the build and redispatched the task.
- `finish` now records that stop as `blocked`, which recovery accepts. A
  retry at a new base opens a new attempt; a retry at the same base still
  reopens the blocked native record.

Validation: the three new tests fail on the previous code and pass with the
fix. They cover a commit, `BASE`, and an empty string refused as the
expected destination with no journal written, a complete journal with a
commit expectation read as inert while an incomplete one still refuses,
and a member sidecar retired before `finish`: recovery refuses, `finish`
records `blocked`, recovery then accepts, and a new attempt lands. The full
Python suite passed in eight parallel shards (3,775 tests, 41 skipped). The
daemon suite passed (496 tests, 13 skipped), Node tests passed (118, one
skipped), resource synchronization checked 556 resources, and the lock
check passed.

Limits:
- The consumer still has to rerun the refused recovery after installing
  this runtime; `finish` must run once for the halted task to settle its
  record.
- No upstream submission has been made.

## 1.7.1.46 — 2026-11-10

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 45 to 46.

Fix:
- [`570523ec — adopt a pull-request member landing made by another PR`](https://github.com/drungrin/gsd-path/commit/570523ec0cd7ebdda2e247501f68998f388c2b12)
  — applies upstream: upstream allows `--adopt-landed-member` only for
  direct-mode members and matches the other PR by head SHA the same way. The
  commit does not apply to upstream main as is, because this fork's
  `integrate_member_pull_request` also carries Azure Repos, editorial
  publication and the pending-PR advance; an upstream submission needs a
  port onto upstream's GitHub-only function.

### A member landing made by an owner-approved PR from another branch

- In a coordinator milestone, the owner ruled that five members merge a smoke
  PR (`smoke/<milestone>`) during build, so the OIDC checks could run from the
  default branch. In four members that PR carried the reviewed HEAD itself,
  or a merge commit on top of it. At ship, `close-members` looked up the
  member PR by the reviewed HEAD's SHA, found the merged smoke PR, and
  refused with "GitHub pull request head is not the bound branch". A PR from
  the bound branch could not exist, because it would have no difference.
  `--adopt-landed-member`, which handles a reviewed HEAD that reached the
  default branch outside Path (#458), refused every pull-request member.
- A pull-request member whose reviewed HEAD is on the default branch, with no
  milestone tag and no merged PR from its bound branch, now gets the
  direct-mode refusal that names `--adopt-landed-member <member>=<live tip>`.
  With that user-approved flag, close-members publishes the bound branch and
  an `adopted-landing` tag at the tip and opens no PR. Validation accepts the
  tag for pull-request members, and a rerun does not reopen the member. An
  open PR from the bound branch, a wrong tip, or an unlanded head still
  refuses. A merged PR from the bound branch keeps the ordinary path.

Validation: the new tests fail on the previous code (21 failures and errors
across three modules) and pass with the fix. They cover the refusal that
names the adoption, an adoption that opens no PR and revalidates, a wrong
tip, an unlanded head, an open milestone PR, the flag reaching a
pull-request member, and an adopted row that is not reopened. Two existing
tests that asserted the direct-only rule now assert the new behavior. The
full Python suite passed in eight parallel shards (3,772 tests, 41
skipped). The daemon suite passed (496 tests, 13 skipped), Node tests passed
(118, one skipped), resource synchronization checked 556 resources, and the
lock check passed.

Limits:
- An adopted pull-request member records no milestone PR. Human review of
  that code happened in the other PR, which the owner approved.
- A member whose other PR was a squash merge does not qualify, because its
  reviewed HEAD is not on the default branch; it closes through an ordinary
  PR that may repeat the squashed commits.
- No upstream submission has been made.

## 1.7.1.45 — 2026-11-10

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 44 to 45.

Fixes:
- [`c26bbc9c — undo the lessons a ship appended with its uncommitted archive`](https://github.com/drungrin/gsd-path/commit/c26bbc9ce93ffc2576ad3359a373e5640bdf7fcf)
  — applies upstream: upstream `pipeline_undo.py` refuses the same
  LESSONS.md change. The code and docs apply to upstream main cleanly; the
  test class conflicts only on the fork's neighbouring tests at the end of
  the file and passes on upstream code once appended there.
- [`c9eac8b6 — reship onto the same open member PR after an undone archive`](https://github.com/drungrin/gsd-path/commit/c9eac8b6289415f77c0f768eac805287cff3d9b4)
  — fork-only: the editorial publications and the pending-PR rebind exist
  only in the fork.

### A red member PR check cannot go back to patch

- In a coordinator project, `close-members` opened the member PR before the
  ship commit, and its required CI check failed on a timing test the
  milestone had added. The owner chose to fix the test through the pipeline
  and keep the same PR. Undo of the uncommitted archive refused:
  `archive undo found unowned .project changes: .project/LESSONS.md,
  .project/publications/<archive>/<member>.json`. Ship step 3 writes the
  lessons, and `pr_editorial.py prepare` writes the publication, both before
  close-members. Upstream's undo refuses the lessons the same way.
- Even with undo unblocked, the re-ship would stop at close-members: the
  pending journal row names the old member head, and only a member rebase
  adoption could rebind it to the fresh FINAL head.
- Upstream #574 (`abandon-integration`, not in this fork) covers a different
  case: the coordinator's own PR after the ship commit. It closes that PR,
  and the re-ship opens a new one.
- Archive undo now owns LESSONS.md when the only change since HEAD is
  appended lines that start with `- <archive name> — `. It restores the
  file, removes it when the ship created it, and returns the dropped lines
  as `dropped_lessons`. It also deletes the archive's untracked
  `.project/publications/<archive name>/*.json`. Any other change to either
  path still blocks without mutation.
- close-members rebinds a pending pull-request row when fresh FINAL names a
  member head that descends from the row's head. It records
  `previous_reviewed_head` and lease-publishes the new head onto the same
  open, unmerged PR only when the published source is still the previous
  head, the PR is the recorded one without auto-complete, and every new
  commit is a proven task landing. A moved source, a closed or completed
  PR, an unproven commit, or a head off that lineage refuses.

Validation: the new tests fail on the previous code (seven failures and
errors across the three test groups) and pass with the fixes. They cover
appended lessons with and without a LESSONS.md at HEAD, five other
LESSONS.md edits that block, the archive's publication copy, three
publication changes that block and an edited committed publication, the
same-PR advance, an unproven tail, abandoned and completed PRs, a moved
source, and a head off the pending lineage. A read-only undo preview against
the blocked consumer state now returns the archive undo with both effects.
The full Python suite passed in eight parallel shards (3,768 tests, 41
skipped). The daemon suite passed (496 tests, 13 skipped), Node tests passed
(118, one skipped), resource synchronization checked 556 resources, and the
lock check passed.

Limits:
- Azure may report the old PR source commit for a moment after the push;
  rerun close-members if its identity check refuses right after publishing.
- The dropped lessons must be appended again at re-ship; the runtime does
  not keep them.
- No upstream submission has been made.

## 1.7.1.44 — 2026-11-08

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 43 to 44.

Fix:
- [`516fe2f0 — keep multiline final evidence on one manifest row`](https://github.com/drungrin/gsd-path/commit/516fe2f057dd632342a8e100587626eafd5fdde6)
  — applies upstream: it cherry-picks cleanly onto upstream main `d5a30f5f`,
  where the same code fails the same way, and its test passes there.

### Multiline final evidence blocked the archive

- Upstream `c16a7206` (#294) taught the final gate and the archive to read a
  FINAL.md field written as indented sub-bullets. The archive kept that value
  with its line breaks, and `render-manifest` wrote it into one table row. The
  row spanned several lines, the manifest validator reads only lines that
  start with a pipe, and every such archive failed with "manifest success
  criteria do not match FINAL.md evidence in order".
- A consumer's ship stopped there after the final gate had passed: the
  Reference fields of three criteria were sub-bullet lists. The only recovery
  the runtime offered was to undo the archive and have the final reviewer
  rewrite an accepted report.
- The archive now stores the evidence joined by spaces, as it already stores
  sub-bullet criterion headings. The placeholder scan still reads the evidence
  as written. Single-line evidence is unchanged, so existing manifests stay
  valid.

Validation: the new test fails on the previous code with the consumer's
error and passes with the change. Against the consumer's prepared archive,
read-only, the previous code fails the row comparison and the new one matches
all four criteria. The full Python suite passed in eight parallel shards,
with `npm test`, `npm run test:sync`, `npm run test:daemon`, and
`npm run check:locks`.

Limits:
- The manifest shows multiline evidence as one line, with the sub-bullet
  markers inline.
- No upstream submission has been made.

## 1.7.1.43 — 2026-11-07

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 42 to 43.

Fix:
- [`cdeeea2d — accept repair waves without a later-numbered source review`](https://github.com/drungrin/gsd-path/commit/cdeeea2d629b8e35e03258c65960c36a4128aac7)
  — fork-only: it removes a fork rule and returns to upstream `1e7ca2aa`
  behavior.

### Repair-wave source proof removed

- 1.6.2.21 (`e3d3a796`) closed an upstream gap: completion required a review
  file for every PLAN wave, although upstream `ddc998bd` had made a repair
  wave need no review of its own. The fork also required each repair wave to
  name a source cycle and its source wave to have a review with a higher
  cycle number; 1.6.2.26 (`8c97aa42`) added a title-pair fallback. Upstream
  fixed the same gap a day later in `1e7ca2aa`, without the source proof, and
  the 1.7.1 sync kept the fork's stricter version.
- A build recovery restarts review numbering when it makes a wave's reviews
  stale. The proof then demanded reviews numbered past the pre-recovery cycle
  on an unchanged product. It blocked build completion four times in two
  consumer projects, each time after a recovery and with a post-recovery
  review that already judged the repairs. The latest case required 9
  canonical and 18 panel reviews. No case was found in which it caught a
  repair that no review had judged. This block came from the fork's rule.
- Completion and archive now accept a wave whose tasks all carry
  `## Review findings` without a review of its own, as upstream does. Repair
  receipts must still match an archived cycle and, for a task of the receipt's
  wave, a later one. The source wave's latest cycle must still pass, and
  `build_state` still holds the build while a blocked review has unfinished
  repairs. Judged repair waves keep reporting 0 review cycles, so manifests
  written by earlier fork releases stay valid. The fix-wave heading stays the
  documented form; completion no longer reads it.
- Two review panels (gpt and muse) preferred an epoch-aware version of the
  proof over removing it, because removal drops the mechanical check for
  repairs opened from a passing cycle on panel findings. The maintainer chose
  removal: the gap the rule was written for is closed upstream, and the rule
  had produced only false blocks.

Validation: the new tests fail on the previous code (six errors, one
failure) and pass with the change. Replayed read-only at the two blocked
commits, the previous validator refused and the new one accepted. Twelve
existing archives in four consumer projects give the same review counts
under both versions. The full Python suite passed in eight parallel shards
(3,758 tests, 41 skipped; five tests of the removed rule were deleted). The
daemon suite passed (496 tests, 13 skipped), Node tests passed (118, one
skipped), resource synchronization checked 556 resources, and the lock check
passed.

Limits:
- A wave of repair tasks opened from a passing cycle on panel findings is no
  longer mechanically held for a later review of its source wave. The build
  contract and final review cover it.
- Consumers get the change at their next milestone boundary; a milestone in
  build still runs its confirmation reviews.
- No upstream submission has been made.

## 1.7.1.42 — 2026-11-04

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 41 to 42.

Fix:
- [`fa51a970 — publish a finished child's final text recorded without a stop reason`](https://github.com/drungrin/gsd-path/commit/fa51a970f7e7c80079787742b6a0557e92a8d7d6)
  — fork-only: `stage-review` does not exist upstream.

### Proxied panel reports refused for a missing stop reason

- 1.6.2.30 through 1.6.2.33 accepted reports delivered through the host's
  `SubagentHandback` tool, including a call recorded with no stop reason. A
  final text message still had to record `stop_reason: end_turn`. Through a
  model proxy, Claude Code sometimes records the final text block and never
  records the stream's stop reason. A survey of 899 child transcripts in
  five consumer projects found 18 such finals: 17 from a proxied muse panel
  model and one from a proxied gpt panel model, none from a native Claude
  model, all with `finished: true` in the child's metadata. Fifteen were
  complete panel reports. `stage-review` refused them as having no completed
  final response. Parents then copied the text by their own scripts under a
  standing user ruling, which wrote no publication receipt. This block came
  from this fork's publication rule.
- A text-only reply with no stop reason is now accepted when it is the
  transcript's last conversational event, the metadata records `finished`
  as exactly `true`, it has a message id, every other event of that API
  message holds only thinking, and neither the reply nor those events carry
  an API error marker. An earlier message of that kind cannot be selected
  explicitly. The receipt records the transport as
  `final-message-unstopped`. Other stop reasons and every identity, report,
  and collection check are unchanged. Replayed over the survey, the rule
  matches the 18 finals; the 14 that start with a Markdown heading would
  publish and the other four still fail the report check.
- Two review panels (gpt and muse) chose this option over no change and over
  requiring the parent to pass the host notification's text. They found that
  equality with text the parent can read from the same transcript proves
  little. gpt added the strict `finished` value, the message id, the
  sibling API-error check, and the rule that an earlier message cannot be
  selected. muse asked whether one report can span several text events; no
  message among the 899 transcripts had more than one text event.

Validation: five of the new tests fail on the previous code and all pass
with the fix. They cover accepted finals with thinking, redacted thinking,
or no sibling and a trailing attachment; refusals for absent or non-boolean
`finished`, a missing message id, API errors, sibling calls or text,
mixed or unknown blocks, later events, explicit selection of an earlier
message, other stop reasons, and identity and report-text checks; and a
structurally valid partial report that still parses. The full Python suite
passed in eight parallel shards (3,763 tests, 41 skipped). The daemon suite
passed (496 tests, 13 skipped), Node tests passed (118, one skipped),
resource synchronization checked 556 resources, and the lock check passed.

Limits:
- The host's `finished` flag is the completion evidence. A child cut short
  after a whole text block that the host still marks finished would publish
  a partial report; the panel parser does not prove completeness. No such
  case appears in the survey.
- The cause inside Claude Code or the proxy is unconfirmed.
- Consumers receive the fix with a runtime update; a consumer in the middle
  of a milestone still needs a compatible runtime or a maintenance release.
- No upstream submission has been made.

## 1.7.1.41 — 2026-11-04

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 40 to 41.

Fixes:
- [`daf04d2e — accept a completed Azure landing that auto-complete triggered`](https://github.com/drungrin/gsd-path/commit/daf04d2ee31d9e4f6883dbbae2ed55d787634459)
  — fork-only: Azure Repos support does not exist upstream.

### Azure auto-complete blocks a completed member close

- The fork refused Azure auto-complete in every provenance check, copying
  upstream's GitHub auto-merge rule. In one coordinator project, the previous
  milestone added a required build-validation policy to the member's target
  branch. The owner then armed auto-complete on the member PR, and Azure
  completed it as a squash once the build passed. The squash tree equals the
  merge of the reviewed HEAD on the landing parent, the PR was closed by the
  owner, and no policy was bypassed. `close-members` still refused it. No
  recovery existed for a pull-request member, and validation queries the
  provider again on every run, so the ship stayed blocked with valid reviewed
  content already on the member's default branch. That block came from this
  fork's rule.
- A completed Azure landing now passes provenance when auto-complete
  triggered it, `closedBy` is a user (non-container, with an e-mail unique
  name), and no policy was bypassed. The reviewed-source, landing,
  first-parent, and squash proofs are unchanged. The completed incident PR
  reported `autoCompleteSetBy: null`: Azure documents the field as set while
  auto-complete is enabled, and clears it once the PR completes. `closedBy`
  is therefore the identity evidence. A setter that Azure still reports must
  also be a user.
- An open PR with auto-complete armed still refuses. This covers the
  awaiting-merge checks, the push of a new source to an existing member PR,
  and the member rebase adoption proof: the provider could land a newly
  pushed source before anyone looks at it. A rerun while an armed PR is still
  open therefore stops until the PR completes. Path still never enables
  auto-complete. GitHub auto-merge and merge queues are unchanged.
- Two review panels (gpt and muse) agreed that rejecting an already-completed
  landing protects no content beyond the Git proofs, and that the open-PR and
  source-advance refusals must stay. muse preferred an owner ruling per PR;
  the maintainer chose automatic acceptance because the required build policy
  makes auto-complete the ordinary path for every later milestone. The panels
  assumed the setter field survives completion; gpt warned against inferring
  a missing setter. The maintainer accepted `closedBy` as the evidence after
  the incident PR showed the field cleared.

Validation: The new tests fail on the previous provenance code (three failures) and pass
with the fix. They cover a completed landing that auto-complete triggered
with the setter cleared or recorded as a user, a service or group setter or
closer, policy bypass, a wrong landing, an open armed PR, and member close plus
revalidation. The full Python suite passed in eight parallel shards (3,753
tests, 41 skipped). The daemon suite passed (496 tests, 13 skipped), Node
tests passed (118, one skipped), resource synchronization checked 556
resources, and the lock check passed.

Limits:
- Acceptance trusts Azure's `closedBy` identity field. Azure documents it as
  the user who closed the PR, not explicitly as the user who armed
  auto-complete; in the incident they were the same person. A non-container
  identity with an e-mail unique name is evidence of a user account, not of
  who reviewed the final checks.
- No upstream submission applies.

## 1.7.1.40 — 2026-11-03

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 39 to 40.

Fixes:
- [`d9502ca6 — stop on command output in a sidecar and discard an exact reviewed list`](https://github.com/drungrin/gsd-path/commit/d9502ca6f64cbce773b6aa94655e85e3b42c479c)
  — applies upstream: its canonical files apply cleanly onto upstream main
  `040bfae2`, and the isolation, workflow, router-contract, and resource sync
  tests pass there (230 tests).
- [`6117fdb4 — discard reviewed command output from a member checkout`](https://github.com/drungrin/gsd-path/commit/6117fdb4e0cce265bc096686816e5730b8b3bc88)
  — fork-only: member inspection does not exist upstream.

### Command output left in an inspect sidecar

- The mapper and docs-auditor roles allow build and test commands in their
  sidecars, but collection, retirement, and the member checks refuse any
  path beyond the assigned artifact, and the guard denies deletes there. In
  one coordinator project, a member mapper ran `go build ./...` in five
  nested modules that each hold a single main package. Go wrote five
  executables into the pinned member checkout, and `advance-inspect` stopped
  with no recovery short of the owner deleting them on the host. The same
  block reproduces on upstream code for the coordinator pair: an extra file
  in either sidecar stops `finish-inspect`. A file in the docs sidecar
  stopped it after the mapper was already collected and retired, so a rerun
  could not complete.
- `check-inspect-output` proves an inspect sidecar changed nothing but its
  artifact. Single-repo and member `finish-inspect` run it for every sidecar
  before collecting any.
- `discard-inspect-output` (coordinator sidecars) and
  `discard-member-inspect-output` (member checkouts) delete an exact list of
  untracked files. The list must equal the untracked set outside `.project/`.
  Both refuse tracked or staged changes, paths under `.project/`, symlinks,
  nested repositories, and non-regular files, and never touch ignored files.
  A journal in the coordinator's Git directory records the request and
  reason before the first deletion, so only that request resumes an
  interrupted discard.
- The roles tell children to leave no file in the tree and to name any file a
  command left. The inspect skill discards only output the parent reviewed,
  records it in the STATE Log, and blocks for the user when a path could have
  changed what the child measured.
- Member inspection's runtime capability probe requires
  `check-inspect-output`, so an older selected runtime blocks with the upgrade
  message.
- Two advisory review panels (GPT and Muse) chose this design. They
  rejected automatic cleanup, which would erase the trace of a file that
  changed a measurement, and reverting member inspection. Member inspection
  added this stop compared with upstream, which does not map members; the
  panels judged the coverage worth keeping.

Validation: before the fix, an extra file in the mapper sidecar stopped
upstream `finish-inspect` with `unexpected sidecar paths`. With the fix
applied to upstream code, the same reproduction stops at
`check-inspect-output` and names the discard command. The new tests cover the
coordinator and member discards, refused lists, tracked and staged changes,
symlinks, nested repositories, interrupted discards, the CLI, and the
single-repo and member workflows. The full Python suite passed in eight
parallel shards (3,750 tests, 41 skipped). The daemon suite passed (496 tests,
13 skipped), Node tests passed (118, one skipped), resource synchronization
checked 556 resources, and the lock check passed.

Limits:
- The discard trusts the parent's judgment that the files are pure output; it
  cannot prove a file was never read.
- Deletion validates each path's parents before it starts, but a parent folder
  swapped for a symlink during the discard is not detected.
- No upstream submission has been made.

## 1.7.1.39 — 2026-11-03

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 38 to 39.

Fix:
[`afba9861 — re-read session folders and prices from the config file`](https://github.com/drungrin/gsd-path/commit/afba98612a1aef27d926e73d497c6bf197574f46)
— applies upstream: it cherry-picks cleanly onto upstream main `040bfae2`,
and the daemon config and watcher tests pass there.

### Daemon session folders and prices follow the config file

- The daemon read `session_dirs` and `prices` from `daemon.json` only at
  start. Another program that registers its own session folders, such as a
  sandbox integration writing transcripts outside the default locations,
  needed a daemon restart before those sessions counted.
- `serve` and `tray` now take both settings from the config file at the next
  session scan when the file's modification time changes. A folder added to
  the file starts counting and a removed one stops.
- Discovery settings (`parents`, `excludes`, depth) still change only through
  the settings API, so a project scan never sees them switch midway. An
  unreadable or invalid file keeps the settings in memory.
- `daemon/README.md` states the rule.

Validation: the seven new config tests fail without the change. The daemon
test modules passed (483 tests, 13 skipped), the daemon suite passed (496
tests, 13 skipped), Node tests passed (118, one skipped) and resource
synchronization checked 556 resources.

Limits:
- A config passed to `tray` without its file path is not re-read.
- No upstream submission has been made.

## 1.7.1.38 — 2026-11-03

Upstream package base remains `1.7.1`, synchronized through upstream main
`040bfae2`. The fork revision continues from 37 to 38.

Fix:
[`b33d0080 — split milestone usage at the ship commit time`](https://github.com/drungrin/gsd-path/commit/b33d00809d94a809b2a58cef331c5384eecaa753)
— applies upstream: it cherry-picks cleanly onto upstream main `040bfae2`,
whose daemon has the same day rule, and the daemon session and probe tests
pass there.

### Daemon usage per milestone split at the ship commit

- The daemon split usage per milestone by day: every turn dated on or before
  a milestone's `Shipped:` date counted toward it. The next milestone usually
  starts the same day its predecessor ships, so its first day of work counted
  toward the shipped milestone. In one coordinator project, about US$ 150 of
  the second milestone's first day counted toward the first, and the third
  milestone's first hours toward the second.
- The probe now reads each archived milestone's ship time from the commit
  that added its `MANIFEST.md`, using the author time that a rebase merge
  keeps. Turns at or before that time count toward the milestone, later turns
  toward the next. Without a committed manifest, or without Git, the whole
  shipped day still counts.
- `daemon/README.md` states the rule.

Validation: the two new tests fail without the change. Read-only against
that project, the probe returns both ship commit times and the split matches
an independent recount by ship commit time. The daemon test modules passed
(476 tests, 13 skipped), the daemon suite passed (489 tests, 13 skipped),
Node tests passed (118, one skipped) and resource synchronization checked 556
resources.

Limits:
- A squash integration leaves no ship commit in the default branch's
  history, so the ship time is the squash commit's author time, normally the
  merge time.
- The daemon still reads only the host's session folders. Sessions that a
  sandbox writes elsewhere are not counted until a `session_dirs` entry or
  the sandbox integration supplies them.
- No upstream submission has been made.

## 1.7.1.37 — 2026-11-02

Upstream package base is now `1.7.1`. The fork is synchronized through
upstream `main` commit
[`040bfae2`](https://github.com/open-gsd/gsd-path/commit/040bfae246cf033466d492544421e0874739a775)
(110 commits after the previous sync point `f46c9b72`, 86 of them without
merges; 51 after tag `v1.7.1`). The fork revision continues from 36 to 37.

Implementation:
- [`1aa0351a — synchronize upstream main`](https://github.com/drungrin/gsd-path/commit/1aa0351aa1196941830730032002c6ad53bc78fb)
  — a merge commit. Its conflict resolutions and the fork adaptations listed
  below are fork only.
- [`276f3b69 — keep a withdrawn completed repair in the repair count`](https://github.com/drungrin/gsd-path/commit/276f3b69da0508718f3649b7cedd839eb1f1412b)
  — fork only: the repair count is a fork feature.
- [`8f9737a6 — name the fork bound branch in the greenfield exception`](https://github.com/drungrin/gsd-path/commit/8f9737a605a989971b4b93373471156627a23bba)
  — fork only: a define line still named the upstream bound branch.

### Upstream changes now included

- Review (1.7.1, #535):
  - reviews validate the agreed acceptance contract and block only on a
    named unmet requirement or missing required evidence;
  - `deep` review needs an explicit user request for adversarial review
    recorded in INTENT.md; risk alone no longer authorizes it;
  - re-reviews check repaired failures and affected passes, reusing settled
    evidence.
- Patch work: the patch re-entry build checkpoint is accepted over a
  ship/active HEAD (#526), also after a resumed patch planner (#531).
- Tasks: a withdrawn task keeps a tombstone with an owner citation (#483).
- Runtime:
  - `isolation.py runtime-hotfix`, an operator-only, digest-recorded
    recovery; the guard denies agent writes under `refs/gsd-path/` (#480);
  - a runtime can name successors in its hashed manifest, and a closed
    milestone accepts helpers only from named successors (#468);
  - `--runtime-upgrade` and `--runtime-migrate` print `preflight:` warnings
    when the incoming runtime's handoff checks fail on the open milestone, and
    build recovery accepts renumbered legacy INTENT Corrections (#551).
- Multi-repo members: `members.py remove` and `members.py set-integration`
  at a milestone boundary.
- Landing and Verify:
  - non-ASCII task paths land and recover without a `core.quotePath`
    workaround (#545);
  - a parallel Verify runs in a verify sidecar reproduced from the isolate,
    so index reads match a serial round (#547);
  - a success criterion quoting a Path marker comment as inline code passes
    completion and archive (#548).
- Guards:
  - deletes inside an authorized parallel task worktree are allowed when the
    hook runs from the primary (#546);
  - non-ASCII literal paths are accepted in guarded delete and move commands;
  - the pre-commit guard accepts commits from a linked worktree after a
    milestone ships and integrates (#555).
- Status reads every origin ref with one batched `ls-remote` (#558).
- Installed `WORKFLOW.md` links are absolute (#543).
- Daemon app: language setting with Simplified Chinese, multi-repo board,
  progress, add and remove member, app release 0.1.3.

### Fork rules kept over upstream

- Patch re-entry: the fork (`e3c09203`, 1.6.2.27) and upstream fixed the
  same refusal. Both checks now apply together: the subject must be
  `build: start milestone`, the working Log must keep the committed Log with
  `patch plan approved` then `build started` (fork), and the Log must end
  with that pair inside a plan stay opened by `patch plan reopened`
  (upstream). Upstream's tests now use the real subject so their Log checks
  run. The build skill names the subject.
- Fork branch names stay. Upstream code and tests that merged without a
  textual conflict assumed upstream names:
  - the #546 guard recognized only `gsd-path-task/<id>` worktrees, so it
    never applied to fork task worktrees. It now recognizes
    `chore/task-<slug>` at `task/<slug>` and requires exactly one
    authorization ref whose name has that slug (`T001`, or `<project>-T001`
    for a member task); two such refs refuse, as task retirement does. Tests
    cover a member task, an ambiguous slug and an upstream-named branch;
  - the successor-runtime guard tests (#468) failed with the upstream bound
    branch, because the fork guard did not see a closed milestone; they and
    the runtime-hotfix, batched ls-remote, corrections-migration,
    member-remove and parallel Verify tests now use fork names.
- The member remove help names the fork's member branch prefixes.
- Upstream lets the owner withdraw a task after it landed (#483). The fork's
  repair count, which asks the redesign question after two completed
  repairs of one criterion (1.6.2.28), counted only tasks with status
  `done`, so a withdrawal lowered it without the `reconsidered:` event that
  is the only allowed reset. A withdrawn repair now counts when its
  committed history recorded `done` at or before the snapshot the count
  reads, and a `reconsidered:` event restarts it like any other.
- Installed `WORKFLOW.md` links point to this fork, not upstream, because
  the fork's contracts differ (Azure, squash, branch names).
- Merged both sides: the coordinator-migration task repo with the withdrawn
  status exemption, the archive-context helper set with the runtime-hotfix
  denial, the member `inspection` subcommand with `remove` and
  `set-integration`, and the fork installer-commit rules with the upstream
  preflight text.

Validation:
- Two cross-model review panels reviewed the staged merge.
  - muse returned "commit as is" with two minor findings, both fixed: the
    guard accepted any of several authorization refs sharing a slug, and the
    define contract named `gsd-path/M001`.
  - gpt returned "commit after listed fixes" with three findings on withdrawn
    tasks. The repair-count finding is fork specific and is fixed in
    `276f3b69`. The other two are upstream behavior and are listed under
    Limits.
- Each adapted or new guard, patch re-entry and repair-count test was checked
  to fail without its change.
- The full Python suite passed in eight parallel shards (3,735 tests, 41
  skipped, 8 minutes 19 seconds), the daemon suite passed (487 tests, 13
  skipped), Node tests passed (118, one skipped), the lock usage check passed
  and resource synchronization checked 556 resources.

Limits:
- Two upstream gaps in the new withdrawn-task feature (#483) are not fixed,
  because the code is the same upstream and they need an upstream design
  decision:
  - a wave holding a withdrawn task stops review dispatch (`wave_tasks`
    requires a proven landing for every task in the wave), and build
    completion expects withdrawn tasks in the review inventory that the wave
    gate excludes;
  - an unfinished withdrawn repair still counts as carrying its findings in
    `fix-tasks`, so no replacement repair is created.
  Both only arise when the owner withdraws a task in a wave that still needs
  review.
- Consumer projects receive the new runtime and skills at a milestone
  boundary, with a commit holding only installation output before the first
  plan approval.
- No upstream submission has been made; none of the fork commits applies
  upstream.

## 1.6.2.36 — 2026-10-29

Upstream package base remains `1.6.2`, synchronized through upstream main
`f46c9b72`. The fork revision continues from 35 to 36.

Fix:
[`a3fa9a7d — keep a shipped milestone's closed build recovery as history`](https://github.com/drungrin/gsd-path/commit/a3fa9a7d4b5f958e88d7061831ff9a4a6f6a631c)
— applies upstream: it cherry-picks cleanly onto upstream main `a8fd65c3`,
which has the same check. Upstream reaches it through a rebase-merged pull
request; the fork also reaches it through its squash integration.

### Next-milestone handoff after a squash integration

- The STATE Log keeps every milestone's history. A closed build recovery
  record of the shipped milestone still matched while STATE was `shipped`
  on the old bound branch, and its base had to be an ancestor of HEAD.
- After a squash or rebase integration, `bind-next` starts the next
  milestone's branch from a default branch that does not contain that base,
  so every state transition failed with "build recovery base is not an
  ancestor of HEAD" and the shipped milestone could not hand off.
- A shipped milestone's closed recovery is now kept as history without the
  ancestry check. Active recoveries and recoveries before shipment are still
  checked.

Validation: the new test fails without the change. Read-only against the
blocked consumer, the fixed runtime returns the closed recovery and the
pinned runtime raises the error. The full Python suite passed in eight
parallel shards (3,628 tests, 7 minutes 48 seconds), the daemon suite
passed (462 tests, 13 skipped), Node tests passed (118, one skipped), the
lock usage check passed and resource synchronization checked 556 resources.

Limits: the consumer that hit this is in the middle of its handoff, where
the new branch must stay at the recorded base, so its pin cannot change
until the next milestone's inspect. No upstream submission has been made.

## 1.6.2.35 — 2026-10-29

Upstream package base remains `1.6.2`, synchronized through upstream main
`f46c9b72`. The fork revision continues from 34 to 35.

Removal:
[`fdfa5424 — remove token usage collection and PR publication`](https://github.com/drungrin/gsd-path/commit/fdfa5424130929476579bcb00f6c0cf4a5398834)
— fork only: upstream never had this feature.

### Token usage in pull requests removed

- The fork collected Claude Code token usage during each milestone and, at
  ship, published a usage and cost summary in the PR description plus a
  model table comment. The owner now gets milestone observability from a
  separate observer, and nothing else read the collection.
- Removed `usage_collect.py`, `usage_report.py`, `usage_publish.py`, the
  offline price snapshot `usage_prices.py` and its generator `gen_prices.py`,
  with their generated skill copies and the test module.
- Removed the hooks that collected usage after `land`, `land-member`,
  `stage-review` and `collect-artifact`, after each dispatch-driver child,
  and in `pr_editorial.py prepare`, and the usage section and comment
  published by `integration.py`.
- Removed the collection instructions from the operating rules and the ship
  skill, and the usage passages from the README and WORKFLOW.
- Editorial documents prepared earlier that still carry `usage` or
  `usage_thread` load and render without them.
- The daemon's usage view reads host transcripts directly and is unchanged.

Validation: the full Python suite passed in eight parallel shards (3,627
tests, 35 fewer than before with the usage tests removed), the daemon
suite passed (462 tests, 13 skipped), Node tests passed (118, one
skipped), the lock usage check passed and resource synchronization checked
556 resources.

Limits:
- Consumer projects on the maintenance line (1.6.2.32) keep the feature
  until they move to this line at a milestone boundary; their current
  milestones still publish usage at ship, by the owner's choice.
- Local ledgers under `.git/gsd-path/usage/` and
  `.git/gsd-path/usage-publication/` in consumer repositories are no longer
  read and can be deleted.
- No upstream submission applies.

## 1.6.2.34 — 2026-10-28

Upstream package base remains `1.6.2`, synchronized through upstream main
`f46c9b72`. The fork revision continues from 33 to 34. All three changes are
contract text, delivered to consumer projects with their skills at a
milestone boundary.

Changes:

- [`f8222903 — list pinned tests read-only and stop before breaking one`](https://github.com/drungrin/gsd-path/commit/f8222903082e21b1d115389d4ab2ee8b978cc979)
  — applies upstream: it cherry-picks cleanly onto upstream main `a8fd65c3`.
- [`9d73756c — account for every test change in a task patch`](https://github.com/drungrin/gsd-path/commit/9d73756cbc7adadfb555b7115fdcea2990ed7b4d)
  — applies upstream the same way.
- [`90032fb1 — start a fresh Claude child for each published review`](https://github.com/drungrin/gsd-path/commit/90032fb15d183fe42c17eed35360926495560a65)
  — fork only: it serves the fork's `stage-review` transport.

### Pinned tests

- In one consumer milestone, four of about eleven extra coder attempts came
  from an existing test outside the task's `files` that pinned a shape the
  task changed. The coder found it only when Verify failed, once after a
  complete implementation.
- The planner now searches existing tests for assertions, fixtures and
  setup that pin each changed shape. It lists them in Approach as
  `Pinned test (read-only)`, without adding them to `files`.
- Before writing product code, the coder stops with `NEEDS-ORCHESTRATOR`
  for a pinned test its contract will break. It edits that test only after
  the orchestrator adds it to `files` and names the assertions the contract
  forces.
- Two cross-model review panels rejected listing such tests in `files`
  up front: a coder allowed to edit them could loosen a test instead of
  fixing the product. One panel recommended this detection; the other
  recommended permission with a mandatory review of the test diff. The owner
  chose detection plus that review.

### Test-change accounting in wave review

- A wave reviewer only warned on disabled tests or Verify commands that
  cannot fail. A loosened assertion or narrowed setup could pass review
  whenever the task's criteria still held, including after the orchestrator
  added a test to `files` on a plan-defect repair.
- The reviewer now accounts for every changed or deleted test file,
  assertion, fixture and setup in each task patch. Each changed or removed
  assertion, and each setup change that narrows what a test exercises, needs
  the clause that forces it: an acceptance criterion, the Interface
  contract, Approach or a recorded `Orchestrator answer`. A change without
  one is a blocking contract violation. The template's warning line now
  names disabled tests instead of weakened ones.

### Fresh child per published review

- The Claude adapter told the orchestrator to resume a child for a
  post-patch review. `stage-review` binds a transcript to the assignment the
  child started with, so a resumed cycle 1 panel child could not publish its
  cycle 2 report, and the panel ran again.
- Each review assignment published through `stage-review` now gets a fresh
  child. A review child is resumed only for a correction of its own
  assignment.

Validation: the full Python suite passed in eight parallel shards (3,662
tests, 41 skipped, 7 minutes 40 seconds), the daemon suite passed (462
tests, 13 skipped), Node tests passed (118, one skipped), the lock usage
check passed and resource synchronization checked 616 resources.

Limits:
- These are model instructions; no helper enforces them. The pinned-test
  search can miss a test, which falls back to today's block-and-repair path.
- The test-change accounting may add review findings and cycles where a
  test change is legitimate but its clause is not written down.
- Consumers on the maintenance line keep their current skills until their
  next milestone boundary.
- No upstream submission has been made.

## 1.6.2.33 — 2026-10-27

Upstream package base remains `1.6.2`, synchronized through upstream main
`f46c9b72`. The fork revision continues from 32 to 33. It contains 1.6.2.31
plus the fix that 1.6.2.32 delivered on the maintenance branch.

Fix:
[`06034a54 — accept native hand-backs recorded before their stop reason`](https://github.com/drungrin/gsd-path/commit/06034a54db02f166068bc63afe4ce34641d3a331)
— fork only, the same change as 1.6.2.32's `9a1eaee0`. See 1.6.2.32 for the
behavior and limits. The maintenance branch is merged into main with the
`ours` strategy again.

Validation: on main, the full Python suite passed (3,662 tests, 41
skipped), the daemon suite passed (462 tests, 13 skipped), Node tests passed
(118, one skipped), the lock usage check passed and resource synchronization
checked 616 resources. The full suites ran after the release commit.

Limits: the same as 1.6.2.32. No upstream submission.

## 1.6.2.32 — 2026-10-27 (maintenance)

A maintenance release on the branch `chore/runtime-1-6-2-27-maintenance`:
1.6.2.30 plus one fix. It still excludes 1.6.2.28 and the upstream
synchronization of 1.6.2.29. Upstream package base remains `1.6.2`,
synchronized through tag `v1.6.2`. The fork revision continues from 31
to 32.

Release: [`3df1c6d6 — release 1.6.2.32`](https://github.com/drungrin/gsd-path/commit/3df1c6d6e22063a196c9c32cc91f716325c86c19)
on that branch.

Fix:
[`9a1eaee0 — accept native hand-backs recorded before their stop reason`](https://github.com/drungrin/gsd-path/commit/9a1eaee080b16734f5ce7f863feae58ab3fdd058)
— fork only, like the hand-back publication it corrects. The same change is
on main as `06034a54`.

### Native Claude hand-backs

- 1.6.2.30 required `stop_reason: tool_use` on the hand-back call. That
  held for the proxied reviewers it was validated against, not for native
  Claude. Claude Code writes each content block of a message as its own
  event, often before the stream's stop reason arrives. In one native wave
  reviewer, all 55 assistant events recorded `stop_reason: null`, the
  hand-back included, so `stage-review` refused a completed review and the
  milestone stopped again.
- The hand-back call may now record `tool_use` or no stop reason. Explicit
  other reasons, such as `max_tokens` or `refusal`, are still refused.
- A hand-back must also be the only call of its message: other events with
  the same message id may hold only thinking. A report delivered beside
  another tool call is refused even when the host recorded no result for
  that call.

Two cross-model review panels reviewed the change and agreed on it, on
refusing mixed calls for reviews, and on how the blocked consumer publishes
the affected review (see Limits).

Validation: on this branch, the publication tests passed (26; the native
case and the mixed-message case fail without the change), Node tests passed
(118, one skipped) and resource synchronization checked 616 resources.
The full Python suite passed (3,450 tests, 39 skipped) and the daemon
suite passed (389 tests, 12 skipped). Against the blocked consumer's eight child transcripts, the
parser recognizes all seven hand-backs, including the native reviewer's,
and still refuses one coder that called `Bash` and the hand-back in the same
message.

Limits:
- A review whose publication base moved, because the orchestrator
  committed a block checkpoint or a runtime pin after the review, still
  cannot be staged at its original base. For the blocked consumer, the
  panels recommended a one-time owner ruling: restage the unchanged report
  at the current HEAD after proving that the commits in between touch only
  `.project/STATE.md` and `.gsd-path/runtime.json`. The report keeps its
  original `Reviewed HEAD`. `stage-review` itself was not relaxed.
- The 1.6.2.30 fix was validated only against proxied transcripts.
- No upstream submission: upstream has no `stage-review`.

## 1.6.2.31 — 2026-10-26

Upstream package base remains `1.6.2`, synchronized through upstream main
`f46c9b72` as in 1.6.2.29. The fork revision continues from 30 to 31. It
contains 1.6.2.29 plus the hand-back fix that 1.6.2.30 delivered on its
maintenance branch.

Fix:
[`00da3c45 — publish native reviews delivered through the host hand-back tool`](https://github.com/drungrin/gsd-path/commit/00da3c45abb9e76e0277fbbf59ac7f2c58af5d6d)
— fork only, the same change as 1.6.2.30's `53efed8d`. See 1.6.2.30 for the
behavior.

The maintenance branch is merged into main with the `ours` strategy, which
records its history without changing main's files.

### Runtime upgrades in the middle of a milestone

- A runtime from 1.6.2.29 on applies upstream's newer wave gate: numbered
  INTENT Corrections (`f0fd14b9`) and accounting for every task claim,
  including interface claims (`629dccae`). Reviews written under earlier
  contracts fail that gate; a replay refused four already-passed reviews of
  one consumer.
- A consumer in the middle of a milestone whose installed skills predate
  1.6.2.29 should not take a runtime from 1.6.2.29 on. Upgrade at a
  milestone boundary, together with the skills, or use a maintenance
  release. No historical review is rewritten and the gate is not relaxed.
- Not done yet: a compatibility preflight in the runtime upgrade and
  regression fixtures with earlier review contracts. The gpt panel
  recommended both.

Validation: on main with the fix, the full Python suite passed (3,660
tests, 41 skipped), the daemon suite passed (462 tests, 13 skipped), Node
tests passed (118, one skipped), the lock usage check passed and resource
synchronization checked 616 resources. After 1.6.2.30 was
installed in the blocked consumer, its runtime staged both real plan-panel
reports from a disposable clone through the hand-back path, and both passed
the plan-panel format check.

Limits: the same as 1.6.2.30. No upstream submission.

## 1.6.2.30 — 2026-10-26 (maintenance)

A maintenance release built on the branch
`chore/runtime-1-6-2-27-maintenance` from the 1.6.2.27 release
(`69d84d31`). It does not contain 1.6.2.28 or the upstream synchronization of
1.6.2.29. Upstream package base remains `1.6.2`, synchronized through tag
`v1.6.2` as in 1.6.2.27. The fork revision continues from 29 to 30.

Release: [`3090ca35 — release 1.6.2.30`](https://github.com/drungrin/gsd-path/commit/3090ca350a818500eb984e8d0f36dffbebaf2167)
on that branch.

Fix:
[`53efed8d — publish native reviews delivered through the host hand-back tool`](https://github.com/drungrin/gsd-path/commit/53efed8d60317e32f72dcaee9c19f2599f0b6c9f)
— fork only. `stage-review` is the fork's native review transport (1.6.0.6);
upstream reviewers write their report files. The same change is on main as
`00da3c45`.

### Native review publication through the host hand-back tool

- Claude Code 2.1.310 can end a child's turn with a `SubagentHandback` call
  that carries the report, instead of a final `end_turn` text message. A
  consumer running in a sandboxed host configuration got this for all six
  children of one session. Another consumer on the same version, outside
  that sandbox, still got `end_turn` for 68 of 68 children.
- `stage-review` accepted only `end_turn`, so a completed plan-review panel
  could not be published, and the milestone stopped. Here the fork's
  transport made the outcome worse than upstream's file contract.
- `stage-review` now also accepts a hand-back when:
  - the assistant event holds only the hand-back call, whose input is only
    `message`;
  - the next conversational event is the host's delivery result for that
    call: chained to it by `parentUuid` and `sourceToolAssistantUUID`, with
    `toolEndsTurn` true, no error, and `success: true`;
  - no user or assistant event follows.
- Identity, model, report-text and publication checks are unchanged, and
  staging still does not validate the report. The receipt records
  `transport: handback` and the delivery event.
- The Claude dispatch contract, the reviewer brief and `AGENTS.md` name the
  hand-back message as the report.

Why a maintenance release: two cross-model review panels recommended
accepting the hand-back in `stage-review` and delivering it without the
1.6.2.29 synchronization. A replay on a disposable clone of the blocked
consumer showed that the 1.6.2.29 runtime's wave gate refuses its four
already-passed wave reviews tested: first for unnumbered INTENT Corrections
(upstream `f0fd14b9`), then for unaccounted interface claims (upstream
`629dccae`). The 1.6.2.27 runtime accepts them.

Validation: on this branch, the publication tests passed (24, including
four new hand-back cases; the two positive cases fail without the fix), the
isolation and install modules passed, Node tests passed (118, one skipped)
and resource synchronization checked 616 resources. The full Python suite passed (3,448 tests, 39 skipped), the daemon suite
passed (389 tests, 12 skipped) and the lock usage check passed. The parser
recognized both reports of the blocked consumer's real transcripts.

Limits: a hand-back delivered without its recorded result, or followed by
more conversation, is still refused. The checks bind the report to the
recorded transcript; they cannot detect a forged transcript. No upstream
submission: upstream has no `stage-review`.

## 1.6.2.29 — 2026-10-26

Upstream package base remains `1.6.2`: upstream has not released a later
version. The fork is now synchronized through upstream `main` commit
[`f46c9b72`](https://github.com/open-gsd/gsd-path/commit/f46c9b7274b9173aeeb2487e214c54668242a933)
(167 commits after tag `v1.6.2`, 119 of them without merges). The fork
revision continues from 28 to 29.

Implementation:
[`0899c772 — synchronize upstream main`](https://github.com/drungrin/gsd-path/commit/0899c77293af567bffcaf3643ad700bfe5a2976d)
— a merge commit. Its conflict resolutions and the fork adaptations listed
below are fork only.

### Upstream changes now included

- Multi-repo members:
  - a declared Prepare command provisions member isolates;
  - a member landing that reached the default branch outside Path can be
    adopted at close (`--adopt-landed-member`), on request;
  - a member task can be reauthorized after a failed finish caused by its
    environment;
  - native member activations record the agent, pin sibling task files and
    accept repository-qualified `reads:`;
  - final-review sidecars resolve members through the primary;
  - retirement handles bound checkouts with submodules.
- Integration:
  - `archive_milestone.py integrate --adopt-published-bound` adopts a
    published bound-branch ancestor, on request;
  - rebase-merged pull-request milestones validate and recover (#450).
- Review:
  - wave reviews account for every task claim, and a claim disposition must
    cite a numbered INTENT Correction;
  - pass lines may cite a criterion by id;
  - SC headings must be in ascending order;
  - the review set is diagnosed;
  - new `evidence-manifest` and `effective-contract` views.
- Verify:
  - project Verify re-runs at the same head after a host-caused failure;
  - receipts record the actual execution directory;
  - long Windows Verify scripts run from a temporary file.
- Guards:
  - shell deletes are scoped to the repository and state roots;
  - PowerShell assignments and static member calls are gated;
  - the read-only fetch family is allowed on a closed milestone;
  - disabled Git hooks stay disabled;
  - the ship guard accepts a reviewed head behind runtime-pin commits;
  - unsupported `.project` entries are detected early.
- Runtime and routing:
  - a publish-only runtime store CLI;
  - recovery reads are cached within one ready run;
  - brief-check lookups are batched;
  - helper errors name causes and recovery hints;
  - blocked plans no longer route back into planning;
  - the patch hand-off check accepts a reopened patch plan.
- Daemon and app: gsd-pi and gsd-core project cards, framework checks and
  updates, Atlas probe data, a venv rebuild when another Python made it, and
  table layout fixes.
- Upstream `d1ba1d0d` was already ported in 1.6.2.27; the fork's text of its
  cap paragraph is kept.

### Fork rules kept over upstream

- Repair waves keep the fork rule from 1.6.2.26 instead of upstream
  `1e7ca2aa`:
  - a repair wave needs a proven source cycle and a later review cycle of its
    source wave;
  - it counts 0 review cycles of its own;
  - upstream's unused `repair_waves` set was dropped.
- Superseded review cycles stay history (fork `c1b05c14`):
  - only the last cycle must pass;
  - upstream `d2884eb8` accepts a superseded pass cycle only when it could not
    close the wave, which would refuse the fork's pass, panel repair and
    re-review flow at completion and archive;
  - that check merged without a textual conflict and was removed.
- Fork branch names stay: `feature/milestone-NNN`,
  `feature/<project>-milestone-NNN`, `chore/task-…` and `chore/verify-…`.
  - Member task reauthorization, new upstream, built its branch without the
    fork's slug (`chore/task-acme-T001` against the activation's
    `chore/task-acme-t001`), so every reauthorization was refused. It now
    uses `member_task_branch_name`.
  - The `deactivate-task` help names `chore/task-<id>`.
- Integration keeps:
  - the fork's provider abstraction (Azure Repos, squash source retention,
    external landing), with upstream's #450 rebase handling: the original
    Ship finds the pull request and proves the landing;
  - the `Source-Tag` metadata parser;
  - the member close row's `usage_publication` and `work_items`, plus
    upstream's `adopted`.
- Contracts merge both sides:
  - `reviewer-wave.md` adds upstream's inspection modes, criterion ids and
    claim accounting to the fork's citation and class-wide repair rules;
  - `task.md`, the ship skill and `docs/multi-repo-work.md` keep the fork's
    branch names, Azure work items and no-attribution wording.
- Imported upstream tests were adapted to these rules:
  - fork branch names in 12 test modules (one rebind test only passed
    because its upstream name was absent and now asserts the fork name is
    present);
  - repair-wave and superseded-cycle expectations;
  - one CI shard balance slack for the 22 fork test modules without Windows
    timings.

Validation:
- Two cross-model review panels reviewed the staged conflict resolutions.
  - Both returned "commit after listed fixes".
  - Fixed: the superseded-cycle regression, the reauthorization branch, the
    stale help text and the imported fixtures with upstream branch names.
  - Not changed, because the merged code behaves as upstream does:
    - an id-only `❌` line passes the wave gate, but `review_findings`
      reports it as an invalid artifact, so collection blocks;
    - the read-only `effective-contract` view matches SC headings more
      loosely than the gate.
- The seven imported reauthorization tests fail without the branch fix and
  pass with it.
- After the fixes, the canonical Python suite passed (3,656 tests, 41
  skipped), Node tests passed (118, one skipped), the daemon suite passed
  (462 tests, 13 skipped), the lock usage check passed and resource
  synchronization checked 616 resources.

Limits:
- Upstream's package version did not change, so the base stays `1.6.2`, and
  CI timings for fork-only test modules are not measured on Windows.
- Consumer projects receive the merged runtime through a runtime upgrade and
  the merged skills only at their next milestone boundary.
- No upstream submission has been made; none of the adaptations applies
  upstream, because they serve fork naming and fork review rules.

## 1.6.2.28 — 2026-10-26

Upstream package base remains `1.6.2`. The fork revision continues from 27 to 28.

Feature:
[`49435fd7 — count repairs of one criterion toward the redesign question`](https://github.com/drungrin/gsd-path/commit/49435fd758660257c9e902275b2f6cd7837b631e)
— fork only. It extends the fork's redesign checkpoint (`22e6dde3`), which
upstream does not have.

### Repeated repairs of one criterion

- The redesign question came only from `review_findings.py collect`, when a
  criterion had blocked two earlier canonical cycles. When the canonical
  review passed and the advisory panel reported actionable findings, the
  panel question listed `Open fix tasks from panel findings` first, and
  those repairs never counted. One consumer milestone spent 11 review
  cycles and 10 repair tasks on one criterion, 4 of those cycles from such
  panel findings.
- Repairs are now counted per criterion: completed tasks whose
  `## Review findings` carry it. The count uses the tasks as the review or
  panel publication recorded them, and spans waves and build recoveries,
  like the blocked cycles in `history`.
- `collect` also sends a fix group to `redesign` at two repairs.
- The new `review_findings.py panel-repairs` command resolves each
  actionable panel finding's references (`T001 acceptance 2`, `t001_ac2`,
  `SC3`) to the criterion text, then reports the same counts. The panel
  question then recommends `Reconsider the component approach`. A reference
  that resolves to nothing is listed and counts as unknown, never zero.
- Both commands emit a `reconsidered` event. Recorded right after a
  `Reconsider` ruling, it restarts the count for the criteria it names.
- `dispatch_driver.py round` holds a repair whose criterion two completed
  repairs already carried. It is released only by a committed redesign or
  panel ruling that opens fix tasks, recorded after those repairs completed,
  or by an approved plan that added the current copy of the task after
  them. The later ruling kind decides. Other ready tasks keep dispatching,
  and an older pinned runtime adds a receipt warning. A repair without a
  wave, without a single source cycle, or with an unresolvable reference is
  held. `build-native.md` documents these rules.

Validation: the canonical Python suite passed (3,467 tests, 39 skipped),
Node tests passed (118, one skipped), the daemon suite passed (389 tests,
12 skipped), the lock usage check passed and resource synchronization checked
616 resources. Each new test fails without the change. A read-only replay on
the consumer milestone found that `panel-repairs` would have recommended
reconsidering at wave 1 cycle 3, after two repairs, where the owner had been
asked to open a third fix task. The gpt and muse review panels:
- reviewed the design before implementation;
- reviewed the code in three rounds, finding 18 issues in the first (some
  overlapping) and 8 in the second, all fixed.

The third round listed three last fixes, made afterwards with tests and not
reviewed again:
- completion read from the task frontmatter;
- a leading reference on a mixed `Criterion:` line;
- uppercase `T001_AC1`.

Limits:
- The criterion identity is its normalized text. A reworded criterion
  starts a new count.
- A count restarts only when the orchestrator records the `reconsidered`
  event the helpers emit; without it, the count keeps running.
- Refutations are not annotated on repair attempts. A refuted group never
  enters `redesign`.
- Dispatch outside `dispatch_driver.py` follows the rule only by contract.
- For a consumer project, the runtime upgrade carries the `collect` change,
  which can raise the redesign question earlier mid-milestone. The
  `panel-repairs` step, the dispatch hold and the contract text arrive with
  the skills at the next milestone boundary.
- `.project/next` tracks are not covered by tests.
- No upstream submission has been made.

## 1.6.2.27 — 2026-10-26

Upstream package base remains `1.6.2`. The fork revision continues from 26 to 27.

Fixes:

- [`8ee4fe7b — bound absence criteria at define`](https://github.com/drungrin/gsd-path/commit/8ee4fe7b3eb94d117ebf3ffe446563893df0abf2)
  — port of upstream `d1ba1d0d` (open-gsd/gsd-path#452), upstream author
  kept. The conflict in the cap paragraph was resolved by placing the define
  recommendation after the fork's cap-lens sentence.
- [`e3c09203 — accept the patch re-entry build checkpoint while HEAD is ship/active`](https://github.com/drungrin/gsd-path/commit/e3c0920349f1cf5c194d65dfe1cbf45e39a2b26e)
  — applies upstream. Upstream `main` (ea9bb02d) has the same guard
  (`1ee6170e`) and the same patch-mode contract. The patch applies there with
  a three-way merge, and its tests pass.

### Bounded absence criteria (upstream port)

- An absence criterion must state its search space, its check and the
  result that counts as absent. Define asks for that bounded form in the edge
  round, and reviewers judge the criterion over that space only.
- At a cap cycle later than 1, a blocking group whose criterion first blocked
  in cycle 1 makes "send to define" the recommended option.
- Why it is in the fork now: a consumer milestone spent 11 review cycles on
  one criterion with an open domain. Two cross-model review panels traced the
  loop to upstream review mechanics, and found that the fork's contribution
  was missing this upstream fix.

### Patch re-entry after final review

- A final review with a failing gap opens plan patch mode. That approval
  writes no checkpoint, because build's patch re-entry commits the patch
  artifacts with its `build/active` transition (`build: start milestone`).
  Since upstream `1ee6170e`, `isolation.py checkpoint` refuses every `build:`
  subject while the STATE committed at HEAD is `ship/active`. The last commit
  before final review is `build: complete milestone` with that STATE, so the
  refusal also covered the patch re-entry, and a patch build could never
  start.
- `checkpoint` now accepts `build: start milestone` in that position, but
  only when the working STATE is `build/active` and its Log extends the
  committed Log with `patch plan approved` followed by `build started`. Every
  other `build:` subject is still refused, and so is a working STATE that
  rewrote the committed Log. The plan skill's patch-mode text names the
  exception.

Validation: the canonical Python suite passed (3,444 tests, 39 skipped),
Node tests passed (118, one skipped), the daemon suite passed (389 tests,
12 skipped), the lock usage check passed and resource synchronization checked
616 resources. The new patch re-entry acceptance test fails without the
change. Its narrowness tests (another `build:` subject, no patch approval,
`build started` before the approval, plan still open, a rewritten committed
Log) pass both before and after. A read-only replay on a consumer project
stopped at patch entry: its committed STATE is `ship/active`, and the new
check recognizes its patch re-entry. The ported cap test pins
`first_seen_cycle` and `repeat`.

Limits: the port adds contract text plus a test that pins fields `collect`
already reported, and its define recommendation stays advisory. Consumer
projects that track installed skills receive the port's define, cap and
spec-probe text only when they reinstall skills at the next milestone
boundary. A runtime upgrade carries the checkpoint fix. No upstream
submission has been made.

## 1.6.2.26 — 2026-10-26

Upstream package base remains `1.6.2`. The fork revision continues from 25 to 26.

Fix:

- [`8c97aa42 — accept repair wave headings that name one source cycle`](https://github.com/drungrin/gsd-path/commit/8c97aa42f7a185c1ef13381eb55e8fce4ca60447)
  — fork only. Upstream `1e7ca2aa`, not synchronized, accepts any wave whose
  tasks all carry `## Review findings` and checks neither a source nor a
  later review. This fork keeps both checks.

### Repair waves from earlier contracts and plan repairs

- Since 1.6.2.21, build completion and archive accept a repair wave without a
  review of its own only through the driver's exact heading
  `## Wave M — fix wave N cycle C review findings` or repair receipts that
  name its source, and only when a later review cycle of wave N exists.
  Plans written under earlier contracts named repair waves differently, for
  example `fix wave 1 cycle 3 panel finding` or `review finding`. A plan
  repair after a cycle-cap ruling wrote `(wave 1 cycle 8 cap ruling)`.
  Completion refused those waves with "review cycle waves do not match
  PLAN.md", and only a build recovery that renamed the headings could clear
  it.
- When no task of a repair wave has a receipt, the validator now also takes
  the source from the single `wave N cycle C` pair the heading names. It
  still refuses a heading with no pair, with two distinct pairs, or with a
  pair that does not name an earlier wave. Receipts keep precedence, and the
  later-review check is unchanged. `build-native.md` documents the rule.
  `build-recovery.md` gives a repair wave appended by a plan repair the
  driver's heading.

Validation: the canonical Python suite passed (3,440 tests, 39 skipped),
Node tests passed (118, one skipped), the daemon suite passed (389 tests,
12 skipped), the lock usage check passed and resource synchronization checked
616 resources. The new heading cases fail without the change. A read-only
replay used a consumer project's PLAN.md, tasks and reviews: the project was
blocked at build completion with 13 repair waves, 10 of them under older
headings. The 1.6.2.25 validator refused. The new one recognizes all 13 and
reports review cycles 11, 2 and 3 for the reviewed waves and 0 for each
repair wave. Before implementation, two cross-model review panels compared
this option with keeping the rule, adopting the upstream rule, and falling
back to it. Both chose this option.

Limits: a heading whose only pair is incidental or mistyped still binds that
source. The later-review check then proves that some later review cycle of
that wave exists, not that the review inspected the repair commit. Consumer
projects that track installed skills keep their previous `build-native.md`
and `build-recovery.md` until the next milestone boundary. The runtime
upgrade carries the validator. The `pipeline_diagnose` reporting from
upstream `1e7ca2aa` was not imported. No upstream submission has been made.

## 1.6.2.25 — 2026-10-24

Upstream package base remains `1.6.2`. The fork revision continues from 24 to 25.

Fixes:

- [`237a7f4b — split CLI JSONL records on LF only`](https://github.com/drungrin/gsd-path/commit/237a7f4b18a21502c7634f7c37f5b9434daef9e2)
  — applies upstream: the affected code is identical upstream, and the
  patch applies to upstream `main` (64ced98d) as is.
- [`9d28f7e0 — split Claude transcripts on LF only`](https://github.com/drungrin/gsd-path/commit/9d28f7e0d27409a34c6bf727404c21e6937b6214)
  — fork only.
- [`649cb4e3 — scope blocker events and refuse malformed ones on write`](https://github.com/drungrin/gsd-path/commit/649cb4e3108a500bcf77914fd5bcc82408647ffa)
  — fork only.

### Raw Unicode line separators in host JSONL

- Claude Code and Codex write U+2028, U+2029 and U+0085 unescaped inside
  JSON strings. `str.splitlines()` also breaks on them, so one event was cut
  in two. `isolation.py stage-review` then refused a review transcript as
  incomplete when its child had read a file containing one, and the report
  could not be published. The usage collector marked that usage incomplete,
  the token budget reader reported an unproven budget, and the Codex
  final-message reader could return the raw JSONL instead of the child's last
  message.
- These four readers now end a record only at LF. JSONL that the runtime
  writes itself was not affected, because `json.dumps` escapes these
  characters.

### Task-scoped `blocker` lens events

- `decision-lenses.md` gives the `blocker` gate a task or artifact scope, but
  the recording grammar accepted only the `wave N cycle C` and `member … base
  …` scopes. `pipeline_state.py transition` still wrote an event such as
  `lenses blocker T001: …`, and every later helper-enforced lens check
  refused on it. Because helpers own the append-only Log, the event could not
  be removed, so the redesign-ruling checkpoint stayed blocked.
- The grammar now accepts one token of letters, digits, `.`, `_` or `-` as
  the scope of a `blocker` event and rejects that form for every other gate.
  `transition` refuses a malformed lens event before writing it, so a lens
  event in the Log always parses. The reference documents both rules.

Validation: the canonical Python suite passed (3,435 tests, 39 skipped),
Node tests passed (118, one skipped), the daemon suite passed (389 tests,
12 skipped), the lock usage check passed and resource synchronization checked
616 resources. Each new test fails without its change. With the
upstream-applicable commit applied to upstream `main` (64ced98d), its two
tests pass. A read-only replay on a consumer project's data showed that the
review transcript the previous runtime refused now parses into 145 events
from one child that ended with a completed final response. The redesign lens
check that refused on five task-scoped `blocker` events now passes. Under the
new grammar, a scan of three consumer project logs found no lens event that
would be refused.

Limits: a lens check still refuses on any malformed lens event in the
current milestone, whatever its gate. Write-time refusal stops new ones.
Consumer projects that track installed skills keep the previous skill
copies of `dispatch_driver.py`, `token_budget.py` and `decision-lenses.md`
until their next milestone boundary. The runtime upgrade alone carries
`stage-review`, the usage collector and the lens checks. No upstream
submission has been made.

## 1.6.2.24 — 2026-10-24

Upstream package base remains `1.6.2`. The fork revision continues from 23 to 24.

Fix:
[`0dab5ee0 — let the next milestone's binding govern after bind-next`](https://github.com/drungrin/gsd-path/commit/0dab5ee019516d2cab1a1e5d08e43ada767331b7).

### Installation commits in the next milestone's inspect

- After `bind-next`, the router writes the next milestone's STATE without a
  commit. With `STATE.md` unstaged, the guard took the committed
  `shipped/done` STATE and ran the previous milestone's integration
  validator. That validator reads the working STATE, so the slugs differed.
  On a multi-repo project the member task landing proof also cannot run once
  `bind-next` retires the member branch, as the router documents. Every
  commit was refused as unverified integration, including the installation
  upgrade that 1.6.2.19 accepts before the first plan approval.
- When the committed STATE is shipped and the working STATE binds an
  unshipped bound branch that is the current branch and differs from the
  shipped one, that binding now governs the commit. Installation output passes
  before the first plan approval, and product files are still refused while
  STATE is inspect. A binding for any other branch leaves the shipped STATE
  and its integration check in place. HOOKS.md and DOCS.md describe the rule.

Validation: the canonical Python suite passed (3,428 tests, 39 skipped), Node
tests passed (118, one skipped) and resource synchronization checked 616
resources. The new guard test fails without the change. A replay on a copy
of the multi-repo coordinator that met the gap, with the shipped HEAD, the
uncommitted next-milestone binding and the staged installation, was refused by
the 1.6.2.23 guard with the reported error and accepted by this one. The same
replay with a staged product file is still refused.

Limits: the historical integration validator still cannot prove a shipped
multi-repo milestone after `bind-next` retires its member branches. The router
already treats that as expected, and the guard no longer depends on it in this
window. A project that met the gap under 1.6.2.23 can commit the pending binding
on its own first; the installation commit then passes. No upstream submission
has been made.

## 1.6.2.23 — 2026-10-24

Upstream package base remains `1.6.2`. The fork revision continues from 22 to 23.

Fixes:
[`0b9e0236 — price Claude Haiku 5.5 and name unpriced models at prepare`](https://github.com/drungrin/gsd-path/commit/0b9e0236ba9f6e97d5a4dc341c759c25d0bcdadf),
[`fba8a66c — keep PR copy free of pipeline identifiers`](https://github.com/drungrin/gsd-path/commit/fba8a66c0faed2a279f41e4570647df29870d959).

### Unpriced models in the usage report

- A multi-repo pull-request ship published two model rows as
  `Sem preço / Unpriced (<hash>)`. The offline price snapshot held five
  models and lacked `claude-haiku-5-5`, and a personal routing alias had no
  mapping. The hashed identity also hid the raw id from the ship owner, so
  nothing named the gap before publication.
- The snapshot is refreshed from models.dev as of 2026-10-24 and adds Claude
  Haiku 5.5 (with its context tier), Claude Fable 5.1 and GPT-6 Sol. Existing
  prices did not change.
- `pr_editorial.py prepare` returns `usage_unpriced_models`, the raw ids left
  without a price after alias mapping. Ship names them to the user in the
  handoff, never in published copy, so an alias or price refresh can precede
  publication. Unknown costs remain explicit.

### Self-contained PR copy

- The same ship titled the member PR `feat(m001): ...` and cited requirement,
  milestone, criterion, edge-case, pitfall and decision identifiers that only
  the coordinator's pipeline record defines. Product readers could not
  resolve them, although ship already asked for copy that works without
  coordinator access.
- `prepare` refuses a title scope that names the milestone (`m001`,
  `milestone-1`, `001`). It also refuses any cited identifier of a pipeline
  shape (`M###`, `T###`, `SC#`, `E#`, `N#`, or a two-digit or zero-padded
  catalog id such as `REQ-07`) that the `.project/` Markdown record defines.
  Milestone ids are refused even when the record lacks them. Technical terms
  such as S256, BM25, UTF-8 and SHA-256 and product paths such as
  `docs/evidence/m001` still pass. Committed publications keep loading as
  recorded, so in-flight ships are unaffected.
- Ship, operating rules, workflow, README and the editorial template state
  the rule.

Validation: on the unsplit change, the canonical Python suite passed (3,427
tests, 39 skipped), Node tests passed (118, one skipped) and resource
synchronization checked 616 resources. After the split, the first commit
passed `test_pr_editorial`, `test_usage_summary`, `test_editorial_delivery`
and `test_git_metadata_policy` (61 tests) with a clean synchronization
check. Synthetic examples then replaced the documentation and test
identifiers, and `test_pr_editorial` passed again. Replaying a real
milestone ledger with the new snapshot and alias priced every model row. The
new check, run against that milestone's two recorded publications, refused
both titles and every cited pipeline identifier.

Limits: the identifier shapes are heuristic. A three-digit catalog id that is
not zero-padded (`REQ-123`) is not detected, and a technical term with a
pipeline shape that the record also mentions (`UTF-16`) is refused and must
be reworded. Publications that are already committed or published are not
rewritten. Product code that cites pipeline identifiers in comments or test
names is out of scope. No upstream submission has been made.

## 1.6.2.22 — 2026-10-24

Upstream package base remains `1.6.2`. The fork revision continues from 21 to 22.

Fix:
[`ddcae396 — let the ship editorial helper name the archive`](https://github.com/drungrin/gsd-path/commit/ddcae39635f4c65f97dc776bc0a12af142008b19).

### Ship editorial step in archive context

- Ship step 4 runs `pr_editorial.py context` and `prepare` with
  `--archive .project/archive/<NNN>-<slug>`, the only archive form the helper
  accepts. The guard admitted archive operands only for `pipeline_state.py`
  and `archive_milestone.py`, so Claude Code refused the documented
  editorial step ("commands run in archive context must be provably
  read-only") and every pull-request ship stopped before preflight. Upstream
  main has the same gap.
- `pr_editorial.py` reads the archived evidence and writes only
  `.project/publications/`, outside the archive. A new
  `ARCHIVE_CONTEXT_HELPERS` set adds it to the pipeline helpers for the
  archive-context check alone. The closed-milestone helper set is unchanged,
  and the runtime-directory, interpreter and shell-composition rules still
  apply to it.

Validation: `tests.test_guard_hook` passed (165 tests); the new test fails
without the change. The guard-related modules `test_guard_hook_windows`,
`test_git_guard`, `test_member_sidecar_hooks`, `test_core_hook_settings`,
`test_member_writes`, `test_review_output` and `test_runtime_lifecycle`
passed (139 tests, 7 skipped), and `test_install` passed (159 tests). The
canonical full suite was not run for this revision.

Limits: found and exercised on one Claude Code project mid-ship; other hosts'
hook adapters share the same `command_denial` path but were not run live. No
upstream submission has been made.

## 1.6.2.21 — 2026-10-24

Upstream package base remains `1.6.2`. The fork revision continues from 20 to 21.

Implementation:
[`db58f1c1 — add the decision lens event grammar and freshness check`](https://github.com/drungrin/gsd-path/commit/db58f1c1f7f08878770e7afa4bb84a297452f12f),
[`2dd4e698 — add the decision lenses reference and wire it into every owner decision`](https://github.com/drungrin/gsd-path/commit/2dd4e6984a488f77640f5ee83cb2027a9ffa6296),
[`30efed29 — refuse approvals without fresh decision lenses`](https://github.com/drungrin/gsd-path/commit/30efed29de26c294a0083aaf5b553fac2044a452),
[`c7ecff76 — let approval undo accept the checkpoint's lens event`](https://github.com/drungrin/gsd-path/commit/c7ecff76e3ac1aa887715eb8013befafa1d00679),
[`2eb23140 — require lens events at build ruling and rebase admission`](https://github.com/drungrin/gsd-path/commit/2eb23140d33c42f78cbf44e185a1d8156aa9890b),
[`59000752 — route a fix-routing redesign fire to the redesign ruling`](https://github.com/drungrin/gsd-path/commit/59000752ba3ac0d6f4d37f7cfec53594a6eaa7ba),
[`1ea442c5 — name the fix-routing escalation key in the build contract`](https://github.com/drungrin/gsd-path/commit/1ea442c57e4250e10b9f5c686bbc1b01e96fe997).

Fix:
[`e3d3a796 — let completion and archive accept repair waves judged by their source wave`](https://github.com/drungrin/gsd-path/commit/e3d3a7966498575cbabe130b5f609d8d12c5948c).

### Decision lenses at owner decisions

- Owner questions named a recommended option from habit or from mechanical
  defaults: the ship question was grounded only in passing gates, plan and
  panel questions recommended from an actionable count, and fix tasks were
  routed silently until a criterion had blocked twice or the cycle cap was
  reached. Nothing asked whether a decision was about to repair a proxy,
  absorb unrelated work, or keep changing the same design assumption.
- `references/decision-lenses.md` is a self-contained reference: sixteen
  one-line questions named after laws of software, a security exception, a
  precedence order, a gate table, and a closed Log event grammar
  (`lenses <gate>[ <scope>]: fired …; blocked …; no-signal …`). Every gate
  must answer exactly its lenses; the event carries no free text. It is
  bundled with every skill that bundles the operating rules, and AGENTS.md,
  the operating rules, define, decide, roadmap, plan, build, build recovery
  and ship point to it.
- Helpers refuse a decision without a fresh lens event when the milestone
  INTENT.md (program gates: CHARTER.md) contains `Decision lenses: enforced`,
  which new INTENT and CHARTER templates carry. Milestones without the line are
  never refused. Enforced points: the INTENT, Corrections, CHARTER and
  ship-pending transitions; plan, patch, roadmap and re-slice approvals; the
  all-refuted, redesign, panel and cap ruling checkpoints; member rebase
  adoption; and `fix-tasks`. A retry or resume reuses the recorded event.
- Approval undo accepts exactly one valid lens event of the approval's own
  gate immediately before the approval event; every other STATE.md
  difference is still rejected.
- A `fix-routing` lens with a `redesign` or `fail-closed` verdict makes
  `collect` report `lens_redesign_pending`, and `fix-tasks` escalates to the
  existing redesign question even when no criterion blocked twice. Only an
  `Open fix tasks anyway` ruling recorded after the latest such event,
  committed and unchanged, releases it. Existing escalation keys keep
  precedence.
- Panel-on-pass and cycle-cap rulings now have Log events
  (`wave N cycle C panel ruling:` / `cap ruling:`) and checkpoint subjects.

### Repair waves at build completion and archive

- Upstream `ddc998bd` made a repair wave need no review of its own, because
  the next review cycle of its source wave judges it, in `build_state.py`
  and build-native.md. `review_cycle_counts`, which build completion and
  archive use, still required a review file for every PLAN wave, so
  completion stopped with "review cycle waves do not match PLAN.md" once
  every task had landed. Upstream main has the same gap.
- A wave whose tasks all carry `## Review findings` is now accepted without
  its own review only when its fix-wave heading (`## Wave M — fix wave N
  cycle C review findings`) or the repair receipts of all its tasks name one
  earlier source cycle, and that source wave was reviewed again after it.
  The source wave's last cycle must still pass. Such a wave reports 0 review
  cycles in the completion receipt and the archive manifest. Any other
  unreviewed wave fails as before. Native appended fix waves must use the
  same heading.

Validation: the canonical full Python suite passed (3,422 tests, 39 skipped)
and Node passed 118 of 119 tests with one skipped; all 616 skill resources are
synchronized and `git diff --check` passes. New tests cover the lens grammar,
every helper-enforced gate (refusal, acceptance, stale events, retries and
resumes, projects without the switch), the approval-undo proof, the
fix-routing escalation and its release ruling, and the repair-wave rule; each
behavior test fails without its change. On a project blocked at completion by
two appended fix waves, the previous validator raised "review cycle waves do
not match PLAN.md" and the new one accepted the waves as 3/2/1/0/0. Two
cross-model review panels reviewed the design twice before implementation; no
panel reviewed the code.

Limits: the lens questions are advisory judgment; the helpers prove that a
complete, fresh event was recorded, not that its answers are right. The
`decide`, `recovery` and `blocker` gates are contract-only. Owner override
records, the ship non-pass inventory, per-milestone file publication for
review families without `end_turn`, and the alignment-queue decline record
are planned for later releases. Live host scenarios were not run. No
upstream submission has been made.

## 1.6.2.20 — 2026-10-22

Upstream package base remains `1.6.2`. The fork revision continues from 19 to 20.

Implementation:
[`5f065f4a — time Claude responses and count each one once`](https://github.com/drungrin/gsd-path/commit/5f065f4a2c507a168702eb9ae844badd95e4bc8b).

### Daemon usage from Claude Code transcripts

- The dashboard showed no agent time for projects run with Claude Code. The
  session parser timed only Codex turns and left every Claude turn untimed,
  although Claude Code transcripts record timing: each response's
  `requestRef` names an `api-request` record stamped when the request was
  sent.
- Usage was overstated. Claude Code writes one record per content block of a
  response under the same `message.id`, each repeating the usage, and the
  parser counted every block as a turn. Earlier blocks can carry provisional
  usage; the last carries the final count. Subagent transcripts forked from
  one context also repeat the same responses. Upstream `1.6.2` behaves the
  same.
- A Claude response now counts once per message id, with the usage and end
  time of its last block, and once per project across transcript files. Its
  duration runs from its API request to its last block, so tool time is
  excluded; Codex durations still include it, and `daemon/README.md` states
  the difference.

Validation: the canonical full Python suite passed (3,360 tests, 39 skipped),
including two new session tests that fail without the change; the daemon
subset passed 389 tests with 12 skipped. Node passed 118 of 119 tests, with
one skipped; `git diff --check` passes and all 601 skill resources are
synchronized. On the maintainer's local session logs, one project's counted
turns fell from 21,998 to 9,959 and its priced total to about 27% of the old
figure, both matching an independent recount by message id; 99% of responses
matched an `api-request` record. No independent review panel was run.

Limits: responses without a matching `api-request` record (about 1% of the
local sample) and transcripts written before Claude Code recorded those
requests stay untimed. Claude and Codex agent time measure different spans.
Parallel subagents add their times, so agent time can exceed wall-clock time.
A response repeated across transcripts is attributed to whichever file the
daemon reads first. Cache writes are still priced at the input rate. No
upstream submission has been made.

## 1.6.2.19 — 2026-10-22

Upstream package base remains `1.6.2`. The fork revision continues from 18 to 19.

Implementation:
[`26e965c1 — accept installer commits before the first plan approval`](https://github.com/drungrin/gsd-path/commit/26e965c1218da02a65bc0e46384498b833e3f318).

### Installer commits during an unfinished milestone

- A consumer that tracks its installed skills and contracts could not land a
  runtime or skill upgrade while a milestone was unfinished. The Git guard
  refused every staged path outside `.project/`, `.gsd-path/` and host hook
  settings unless build was landing a task, while plan and roadmap approval
  refuse any worktree change outside `.project/`. The upgrade could neither
  be committed nor left uncommitted. Upstream `1.6.2` behaves the same.
- The guard now accepts a commit whose every staged path is installation
  output: GSD Path skill directories under each host's project skill root,
  `AGENTS.md`, `WORKFLOW.md`, `.claude/CLAUDE.md`, `.cursor/agents/gsd-path.md`,
  `.gsd-path/` and host hook settings. It applies only while STATE is inspect
  through plan and the milestone has no plan approval checkpoint in HEAD's
  history, so build, ship, and a plan reopened by a ship patch or build
  recovery keep their protection. Mixed commits, other skills and the
  installer's `disabled-gsd-skills` backup stay refused. A commit of only
  `.gsd-path/` and hook settings remains bookkeeping in every phase.
- A test keeps the skill roots and names in step with
  `scripts/skill-resources.json`. HOOKS.md and DOCS.md describe the rule and
  the step of moving the `disabled-gsd-skills` backup out of the worktree
  before the next approval.

Validation: the canonical full Python suite passed (3,358 tests, 39 skipped),
including four new guard tests. Node passed 118 of 119 tests, with one
skipped; lock-usage checks and `git diff --check` pass. All 601 skill
resources are synchronized. An independent GPT panel review reported four
issues: plans reopened after build would regain the exception, the Cursor
companion agent was missing, the update backup still blocked approval, and
the docs overstated the phase limit on runtime-only commits. The first two
were fixed with regression tests and the last two in the docs. All fixtures
use synthetic repositories; no consumer repository or remote was used.

Limits: a project whose Git hooks live in a tracked in-repository
`core.hooksPath` cannot commit refreshed hook files this way. The guard does
not check that staged skill files match a released bundle; it checks only
their paths. After a milestone's first plan approval, a project that tracks
skills upgrades at the next milestone boundary. A project gets this behavior
only after an explicit runtime and skill upgrade. No upstream submission has
been made.

## 1.6.2.18 — 2026-10-22

Upstream package base remains `1.6.2`. The fork revision continues from 17 to 18.

Implementation:
[`0bd37908 — keep program research durable in .project/program`](https://github.com/drungrin/gsd-path/commit/0bd37908e6f4cc790df4251aa2bc712d79ffda55).

### Durable program research

- In program flow, program-scope research wrote its manifest and evidence to
  `.project/research/`. When the first roadmap milestone had open questions,
  its own research wrote the same `RESEARCH.md` and `evidence-<dimension>.md`
  paths, overwriting the evidence the never-archived program `SYNTHESIS.md`
  cites; that milestone's ship then archived whatever remained, and later
  re-slices received the milestone manifest. Upstream `1.6.2` behaves the same.
- Program-scope research now writes `.project/program/RESEARCH.md` and
  `.project/program/evidence-<dimension>.md`, which persist beside CHARTER.md,
  ROADMAP.md and the program SYNTHESIS.md and never archive. Milestone research
  keeps `.project/research/`, where codebase maps and docs audits also stay.
  A bare `evidence-<dimension>.md` citation resolves beside its manifest.
  `check_handoffs.research_scope` selects the intent and research directory,
  and lookahead research now requires its own INTENT.md. The roadmapper reads
  the program manifest; when an earlier runtime archived program research, it
  uses the earliest archived manifest that names CHARTER.md.
- Upgraded projects: while `.project/research/RESEARCH.md` names
  `.project/CHARTER.md` and the milestone has no plan approval checkpoint yet,
  the router returns `block` naming `pipeline_state.py
  migrate-program-research --repo <root> --dry-run`. Phase or status
  transitions, roadmap and plan approvals (including deferred and patch
  approvals) and the program research gate refuse that layout; branch binding,
  Log-only transitions and moves to `blocked` stay legal. The migration
  requires the bound branch, no helper journal and real directories. It moves
  the manifest and dispatched evidence, rewrites explicit `research/`,
  `./research/` and `.project/research/` evidence paths in the manifest and
  program SYNTHESIS.md, lists other active files that still name an old path,
  appends one STATE event and removes the legacy manifest last, so a rerun
  completes an interrupted move. It refuses divergent or foreign destination
  files, and missing evidence outside running research. The next approval
  checkpoint commits the move.
- `program` joins the `.project` entry allowlist, the program-flow active-root
  allowlist for ship and abandon, and the `.gitignore` probe paths. The
  research, decide, roadmap, ship, discuss and router skills, the
  research-handoff template, WORKFLOW, README, FULL and DOCS describe the
  layout and the migration.

Validation: the canonical full Python suite passed (3,354 tests, 39 skipped),
including 22 new migration tests and new research-gate, ship and abandon
cases. Node passed 118 of 119 tests, with one skipped; lock-usage checks and
`git diff --check` pass. All 601 skill resources are synchronized. Two
independent reviews (a GPT panel reviewer and a Claude reviewer, the latter
reproducing each of its findings) reported 11 issues. Ten were fixed with
regression tests: temporaries an interrupted migration write left behind; a
symlinked research directory; migration off the bound branch, with the route
hiding the branch mismatch; dot-relative citations; the initial-binding edge
being refused; deferred and patch approvals bypassing the block, and the
window reopening on patch or build recovery; a stale legacy manifest beside a
new one (now refused by the research gate) and OS junk blocking a rerun;
missing evidence accepted after research; route and status raising on a
non-file manifest; and stale references in other files going unreported. The
undo-proof issue, and the STATE temporary part of the first, are listed under
limits. All fixtures use synthetic repositories; no consumer repository
or remote was used.

Limits: a milestone that already passed its first plan approval keeps the
legacy layout, and its ship archives program research as before. Where a
first milestone's research already overwrote program research, the runtime
cannot restore it; the originals survive only in Git history. Other files
naming an old evidence path (ROADMAP.md, INTENT.md, evidence cross-references,
discussion records) are reported, not rewritten. A researcher dispatched
before the migration writes the old path, and the research gate then
redispatches it. Like any uncommitted Log event, the migration's STATE event
keeps `pipeline_undo` from proving the next approval checkpoint. A
`.STATE.md.gsd-path-tmp` left by a killed write blocks the migration as it
blocks every transition. A project gets this behavior only after an explicit
runtime and skill upgrade. No upstream submission has been made.

## 1.6.2.17 — 2026-10-21

Upstream package base remains `1.6.2`. The fork revision continues from 16 to 17.

Implementation:
[`7771d75d — map and audit member repositories`](https://github.com/drungrin/gsd-path/commit/7771d75d4fa5f0e684191e27d72ef79b14139f09).

### Member inspection for multi-repo projects

- A coordinator that is a dedicated program repository (supported by the
  multi-repo plan) never mapped its members: `detect_project.initialize`
  runs before `MEMBERS.md` can exist, and inspect, its gates and the docs
  queue knew one repository. Inspect now maps and audits each brownfield
  member at a pinned commit, never the team's working checkout, into
  `research/evidence-codebase-<member>.md` and `research/DOCS-AUDIT-<member>.md`
  headed `Repo root`, `Member`, `Default branch` and `Member HEAD`.
  `detect_project.classify_revision` applies the brownfield rules to the
  pinned Git tree; `members.py inspection` reports each member's pin, verdict
  and evidence state read-only, without fetching.
- Pins: the active track uses the member's local `origin/<recorded default>`
  (the inspect skill fetches first); a lookahead track uses a member's bound
  branch tip when the active build locks it, else origin. A missing checkout,
  marker, origin identity or ref, or a malformed lock blocks. Evidence off the
  pin's history counts as missing; evidence behind a moved pin is stale and
  advisory at define, plan and build start.
- At `define/active` with no INTENT.md and either no CHARTER.md or a
  ROADMAP.md, the router routes brownfield members lacking evidence to
  inspect (`member inspection started`); the transition re-checks the same
  conditions, and the guard and installer status tables accept it.
  Completing inspect requires a valid pair for every brownfield member.
  `bind-initial` accepts an untracked `MEMBERS.md`, which blocked
  `init → members add → bind`.
- `workflow_run.py` runs member inspection in stages, because a member's
  mapper and auditor share one isolated checkout: `prepare-inspect` freezes
  pins and pinned docs inventories before creating sidecars, `advance-inspect`
  gates each map and starts that member's auditor at the same pin, and
  `finish-inspect` gates everything before collecting, retires the sidecars
  and transitions. Lookahead member inspection writes only `.project/next`
  and copies pending audits that receive no fresh audit. New
  `isolation.py` member inspect sidecars use `chore/verify-<coord>-inspect-<member>`
  and fail closed; project Verify names a leftover inspection and its exact
  retirement command. An in-tree Markdown alias symlink is audited through
  its target; other Markdown symlinks block.
- Each `DOCS-AUDIT*.md` is its own ruling queue keyed by (audit, Queue #):
  quick pre-approval, the router alignment offer, plan selections
  (`repo: <member>` tasks), ship and abandon carry-forward (whole files,
  manifest rows summed) and promotion (each audit's append-only history)
  cover every audit, including a removed member's queue, which is kept and
  never retargeted. The ship count and the shared ruling parser accept the
  same rulings heading. Standalone docs audit stays coordinator-only.
- Define, research and the planner receive every codebase map (the planner
  previously received none). Dispatch contracts name
  `inspect_codebase_<member>` and `inspect_docs_<member>` under the unchanged
  model-policy roles. Skills, roles, templates, WORKFLOW, README, CONTEXT,
  ADR 0002 and the multi-repo plan (slice S6) document the behavior.
  Without `MEMBERS.md`, every path is unchanged.

Validation: the canonical full Python suite passed (3,328 tests, 39 skipped).
Node passed 118 of 119 tests, with one skipped; lock-usage checks and
`git diff --check` pass. All 601 skill resources are synchronized. Two
independent reviews found six runtime defects (coverage re-run on leaving
inspect creating dead ends, sidecars leaked by a failed advance, non-ancestor
evidence labelled stale, an unhelpful Verify collision, unvalidated receipt
carry paths, Markdown alias symlinks blocking) and three queue issues (two
ruling parsers disagreeing on a padded heading, two contract wording errors).
Each was fixed with a regression test, and the reviewers' synthetic repros
then passed. All fixtures use synthetic repositories; no consumer repository
or remote was used.

Limits: a member added after INTENT.md exists, or after a lookahead track has
finished inspect, is not inspected in that milestone. If `advance-inspect` is
killed after retiring a mapper but before recording it, retire the pair and
prepare again. A lookahead inspection and ship's project Verify share the
`verify/<member>` path and must not run at the same time; the second refuses.
A Markdown symlink whose target is excluded or outside the pinned tree blocks
member docs inspection. A project gets this behavior only after an explicit
runtime and skill upgrade. No upstream submission has been made.

## 1.6.2.16 — 2026-10-21

Upstream package base remains `1.6.2`. The fork revision continues from 15 to 16.

Implementation:
[`0aa10c65 — validate GitHub squash landings`](https://github.com/drungrin/gsd-path/commit/0aa10c65c80bf05c340bca1bacea4b02188ff64e).

### GitHub squash integration parity

- GitHub pull-request integration accepts a human-completed one-parent landing
  only after proving the exact reviewed source merged cleanly into the
  landing's parent, an identical result tree, and default-branch first-parent
  ancestry. The existing PR identity and human merge-event checks still reject
  moved sources, duplicate PRs, bot completion, auto-merge, and merge queues.
  A fast-forward that already contains the original source is not reclassified
  as squash. Existing coordinator rebase proof keeps its precedence; members
  reject provable multi-commit replay shapes even when the squash tree matches.
  Unavailable replay evidence fails closed before either tag is published,
  preventing transient Git errors from changing a retry's proof classification.
- The actual landing's parent supplies GitHub's historical target, not a
  current base-ref OID or default-branch tip. Azure retains its explicit
  completion strategy and source/target/landing provenance.
- Both providers reuse the source-first annotated tag publication and existing
  close journal. The milestone tag names the landing; its canonical retained
  source tag preserves the reviewed graph after branch removal or GC.
  Historical ship recovery and next-milestone branch binding accept the same
  GitHub source-retention proof instead of requiring an Azure PR URL.
- Ship contracts, operating rules, workflow, glossary, and multi-repo ADR
  document the supported proof and its limits. Integration modes and repository
  merge settings are unchanged. PR completion remains manual.

Validation: the canonical full Python suite passed (3,179 tests, 39 skipped).
The 37 focused member/squash tests also passed. Node passed 118 of 119 tests,
with one skipped; lock-usage checks and `git diff --check` pass. All 601 skill
resources are synchronized without warnings. Independent review reproduced and
then verified fixes for multi-commit member rebases passing squash proof and
transient replay-proof failures publishing incompatible tags. Tests cover both
replay rejection and healthy retry, target advancement before and after landing,
source-first tag publication, source-branch removal, bare-remote and local GC,
archived review access, fresh clones, and next-milestone recovery. All fixtures
use synthetic local repositories and mocked provider evidence; no consumer
repository or live PR was used. An earlier full run hit its 30-minute limit;
a diagnostic stdin runner failed its multiprocessing spawn test. Neither counts
as a pass; the subsequent canonical run completed successfully.

Limits: GitHub's payload does not attest a merge strategy equivalent to Azure's
`mergeStrategy`; content and provenance cannot strictly distinguish squash from
an observationally equivalent single-commit rebase. Git 2.38+ is required for
exact merge-tree proof. Conflicts and unreviewed resolutions remain blocked.
GitHub.com is the supported GitHub PR host; provider-backed `external-landing`
remains Azure-only. No auto-merge, queue, or policy bypass is introduced.
Consumers receive this behavior only through an explicit runtime and skill
upgrade. No upstream submission has been made.

## 1.6.2.15 — 2026-10-21

Upstream package base remains `1.6.2`. The fork revision continues from 14 to 15.

Implementation:
[`22e6dde3 — preserve milestone budgets and authenticate repair anchors`](https://github.com/drungrin/gsd-path/commit/22e6dde362de399244f47cb7a3ae3e554bc84165).

### Review budget and redesign checkpoints across recoveries

- `review_findings.py collect` reads earlier wave reviews under
  `plan/build-recovery-<base>/review/`. A wave's effective cycle is the current
  cycle number plus the blocked cycles that recoveries set aside. The existing
  `max_review_cycles` limit now applies across the milestone, not separately
  in each recovery. Git-proven restorations count once; independently published
  identical reports still count separately. Missing or invalid required lenses
  remain blocked in history. A deep review still counts as one cycle, not one
  per lens. History reports the recovery,
  cycle, verdict and blocking criterion locators.
- When a current fix group's criterion blocked two earlier cycles of the same
  wave, `collect` requests a redesign ruling. `fix-tasks` escalates instead of
  writing another fix. The owner may reconsider the component approach or
  explicitly open another fix task. Only the committed current-cycle ruling,
  unchanged in the working STATE.md Log and recorded since the latest recovery,
  releases that gate. It does not override the cycle cap, structural blockers,
  or skeptic verdicts. Reviewer-line recurrence stays informational.
- When unrelated landings changed the original product before a repair base,
  `repair-evidence` first keeps its existing linked-landing check, then can
  anchor on the product an explicitly assigned current-product source review
  judged. `review --current-product` pins the combined coordinator/member
  snapshots and validates matching report metadata. Each lens needs successful
  execution and a validated, collected, hash-bound record; older recovery
  namespaces remain usable for restored reports. Manual review records the
  same scope and snapshots in its report and supplies `--review-base` and,
  when necessary, `--member-head member=sha`; flags cannot override report pins.
  An ordinary isolated-task review does not certify its collection base.
  Only linked repairs may change checked files after that snapshot. Reports
  must remain unchanged, the repair still needs its
  isolated passing Verify, and changes after its landing remain guarded.
  Receipts name `anchor: source-review` and the reviewed snapshot.
- Build, recovery, reviewer, plan and runtime contracts document these rules;
  AGENTS.md allows the earlier redesign escalation.

Validation: the full Python suite passed (3,163 tests, 39 skipped). The three
current-product scope and compatibility tests also passed after the final
older-runtime refusal was added. Node passed 118 of 119 tests, with one skipped.
All 601 resources are synchronized; lock-usage checks, the seven Git metadata
policy tests and `git diff --check` pass. An independent check reproduced six
initial defects, then verified their closure with eight targeted tests. New
controls cover independent identical publications versus Git-proven restores,
missing deep lenses, committed and recovery-scoped rulings, collected report
identity/hash/exit status, member-pin overrides, explicit current-product scope,
and provenance retained in earlier recovery namespaces. All reproductions used
synthetic temporary repositories; no consumer project or remote was used.

Limits: the redesign checkpoint groups canonical criterion locators, not
semantic guesses about root causes or advisory panel findings. History does
not include archived earlier milestones. Passing cycles set aside by a recovery
are not charged again; the current recovery retains ordinary cycle numbering.
Legacy reports without explicit current-product scope cannot supply the new
anchor; the runtime never retroactively adds reviewer claims or treats the
collection base as reviewed. Their supported fallback remains a full review
with explicitly pinned current-product scope. A project receives the new
behavior only after an explicit runtime upgrade. No consumer project has been
upgraded, and no upstream submission has been made.

## 1.6.2.14 — 2026-10-20

Upstream package base remains `1.6.2`. The fork revision continues from 13 to 14.

Implementation:
[`f9bf5cf9 — take the host id as a flag instead of a capability file field`](https://github.com/drungrin/gsd-path/commit/f9bf5cf9f441e4e4043442f140b00d4e6848899f).

### Host id as a flag for model policy

- `model_policy.py resolve` and `reassign`, and the driver commands that take
  `--model-capabilities` (`round`, the review commands, `reassign-model`), take
  `--host <id>`. The id must be one of `path_config.HOSTS`, the same list as the
  installed hosts in `skill-resources.json`. The helper refuses a missing or
  unknown id and lists the valid ones.
- A `host` field in the capability file is deprecated. If present it must equal
  `--host`. Before this change the id was free text that the orchestrating
  model wrote into that file for each project. A spelling the policy did not
  know (`claude-code` for Claude Code, whose id is `claude`) made every
  `hosts.<id>` preference silently miss, so each role fell back to `inherit`
  and children ran on the orchestrator's model.
- The old spelling `claude-code` is read as `claude` when comparing against
  old records and capability files, so those assignments resolve again and stay
  pinned. It is never accepted as `--host`.
- A driver that runs over a project runtime older than this change keeps that
  runtime's contract: the flag only fills a missing `host` field and never
  overrides a recorded one. `path_config` joins the driver's runtime activation
  order because `model_policy` now imports it when it loads.
- `model-policy.md` lists the host ids. Each host dispatch doc names its own id,
  and the shared-agents doc maps each tool branch to its id.

Validation: the full Python suite passed (3,147 tests, 39 skipped) and the Node tests
passed (118 of 119, one skipped). All 601 skill resources stayed synchronized
and `git diff --check` passed. New tests cover the flag, an unknown or missing
id, a file that names another host, the legacy spelling in files and records,
preferences keyed by the flag host, and the driver over a runtime older than
this change; removing the legacy-record comparison or the declared-host check
makes its test fail. No real project or pull request was used.

Limits: the policy yields the native selection, and the orchestrator still has
to apply it to the child tool; nothing enforces that. Existing records stay
pinned until an owner `reassign`. A project gets the stricter helper only after
its runtime is upgraded. `usage_report.py` (`claude-code`) and `token_budget.py`
(`codex-cli`) keep their own host strings, which are persisted in publications
and ledgers. No upstream submission has been made for this change.

## 1.6.2.13 — 2026-10-19

Upstream package base remains `1.6.2`. The fork revision continues from 12 to 13.

Implementation:
[`40a390e6 — rebase locked member branches by owner ruling`](https://github.com/drungrin/gsd-path/commit/40a390e6752f95d5ed045e8019bdae0d62cfcba7).

### Owner-ruled member rebase adoption

- Add `isolation.py adopt-member-rebase --repo <coordinator> --member <name>
  --onto <full SHA> --ruling '<verbatim>'`, with a read-only `--dry-run` and an
  `--abort` for an unpublished interrupted apply. When a locked member's
  recorded default branch moves, an explicit owner ruling can rebase the member
  bound branch onto it. The bound branch still never receives a merge, and the
  primary `adopt-rebase` is unchanged.
- The helper runs only with no task in flight: `plan/active`, `plan/blocked`,
  `build/blocked`, or ship while STATE.archive is null. It refuses pending
  helper journals, member task branches or authorizations, a dirty Path bound
  checkout, an unlocked or already integrated member, and an empty ruling with
  typed error codes. `--onto` must equal the live and freshly fetched
  `origin/<recorded default>`, descend from the locked base, and not contain the
  bound tip. The remote bound branch must be absent or at the local tip.
- The preview fetches into a disposable object store and simulates the replay
  with `git merge-tree --merge-base`. Apply repeats that simulation, journals,
  and cherry-picks the linear locked range on
  `chore/rebase-<project>-milestone-NNN-<member>`. It proves one replay per
  original commit with the same message, author, and ordered edits, and checks
  that the landing task contracts still hold. A conflict, empty commit, or
  failed proof rolls back without a trace. It keeps the old lineage under a
  local `refs/gsd-path/member-rebase/` ref, pushes with an exact lease (an
  absent-ref lease when unpublished), compare-and-swaps the local bound branch,
  and refreshes Path's bound checkout. If an undone archive left a pending
  member close, the helper proves the same PR is open and unmerged before
  and after the push, preserves its close row and old evidence unchanged,
  and records that prior close/source proof in the receipt. Completed,
  abandoned, ambiguous, or auto-complete PRs refuse. It then commits the new lock base with
  `.project/build/member-rebase-adoption.json`
  (`gsd-path/member-rebase-adoption/v1`) as one `.project/`-only coordinator
  commit. A rerun restarts an interrupted `prepared` replay and reuses a fully
  proven `replayed` tip. The journal records `publishing` before the push.
  From then on the adoption only rolls forward and `--abort` refuses. The
  exception is a push the remote reports as rejected while the ref keeps its
  lease, after an attempt that began at `replayed`: it returns to `replayed`
  with `publish-refused`. Every resume revalidates its targets, contracts, and
  replay. The `advanced` and `recorded` resumes also require the local and
  remote bound branch at the new tip. The receipt pins the
  coordinator task inventory, and the adoption commit binds all semantic
  receipt fields with `Receipt-SHA256`, including the verbatim ruling.
- Member landing proofs (`recover`, `verify-landed`), the coordinator-migration
  baseline and legacy projections, ship inputs, `close-members`, and repair
  evidence follow the receipt chain. A receipt whose commit map no longer
  recomputes, or whose last base differs from the lock, blocks them. Status lists
  a pending journal as `member_rebase`; undo refuses it and the adoption commit;
  diagnose names the rerun or `--abort`; member isolation and landing wait for
  it. Member pre-push now reserves `chore/rebase-<coordinator>-`.
- Refuse `define/active` without touching a surviving pending PR close; the
  normal definition/plan gates must first reach a quiet legal state. After
  adoption and repair tasks, `close-members` may rebind the preserved pending
  close only to a fresh FINAL member head. It requires fresh publication
  evidence, proves every repair commit after the adopted tip, lease-advances
  that source, and reuses the same PR. Old source/review/Verify proofs remain
  old evidence; a missing or closed PR is not silently replaced.
- Hardening from independent review:
  - The receipt authenticates through the full coordinator history, so an
    archived receipt still proves after a `--no-ff` integration and a later
    milestone branch. A changed committed receipt still refuses.
  - Commits with an explicit or non-UTF-8 encoding refuse before any
    mutation, as does a non-UTF-8 `i18n.commitEncoding`.
  - A coordinator-migrated member refuses pipeline metadata in the new default
    commits during the preview and again before publication.
  - The journal records each helper-produced replay SHA. Cleanup after a
    rejected proof or an interrupted replay removes only owned commits.

Validation:
- The full Python suite passed 3,134 tests with 39 skips. That includes 64
  synthetic member rebase adoption tests, which cover a mocked Azure adapter
  for the pending open-PR close.
- Node tests: 118 of 119 passed, with one skip.
- All 601 skill resources stayed synchronized. The lock-usage check and
  `git diff --check` passed.
- Every independent-review reproduction now refuses or verifies as intended.
- No real member, coordinator, or pull request was used.

Limits: the adoption attests identical edits, not behavior. Verify ledger entries,
final reviews, and editorial publications bound to old member commits do not
carry over: ship reruns project Verify and the member final review at the new
tip, and the publication is prepared again. Only a linear locked range is
supported; a merge commit refuses. The preview needs Git 2.40 or later. A forge
that reports a refusal of the leased force push returns the journal to
`replayed`, where `--abort` rolls it back. A push interrupted without that
report keeps `publishing`. Only a successful rerun or an owner decision closes
it. The retention ref is local, so a later proof of the
adoption needs the member checkout that keeps it; a fresh clone without the old
commits cannot recompute the receipt. A coordinator-migrated member still requires
history without pipeline metadata, including the new default-branch commits. No
upstream submission has been made for this change.

## 1.6.2.12 — 2026-10-17

Upstream package base is now `1.6.2`, synchronized through tag
[`v1.6.2`](https://github.com/open-gsd/gsd-path/releases/tag/v1.6.2), commit
[`8f705d93`](https://github.com/open-gsd/gsd-path/commit/8f705d93a44292b4e71e24ff504b47279f076826).
No later upstream commits are included. The fork revision continues from 11 to 12.

Implementation:
[`3ddf354a — synchronize upstream`](https://github.com/drungrin/gsd-path/commit/3ddf354ad1d4394ff01af3014596e7109378e767),
[`15ffb5ea — flag review findings on repair-written lines`](https://github.com/drungrin/gsd-path/commit/15ffb5eae9de811b5a81468ca1566083d2eb6e8b),
[`52a5c7e0 — keep fork maintenance rules out of the installed AGENTS.md`](https://github.com/drungrin/gsd-path/commit/52a5c7e08014bf7ddd8d68e81d2d55783e745723).

### Upstream synchronization

- Include the 1.6.2 fixes: `isolation.py checkpoint` refuses a `build:`
  subject while the STATE committed at HEAD is `ship/active`, reading quoted
  STATE values; a reused final-review view keeps the evidence-line citations
  of held-out and judgment ids; release notes keep the README What's new
  section current.
- No fork flow records a `build:` checkpoint during ship; milestone abandon
  stays a build-phase operation.
- Keep the upstream release evidence and independently versioned desktop app
  under their existing version policies. The only conflicts were the package
  version fields, resolved to the fork version.

Validation: full verification after the merge passed 3,069 Python tests with
39 skips and 118 of 119 Node tests with one skip; all 601 skill resources stayed
synchronized.

### Review recurrence signal

- Add a read-only recurrence diagnostic for wave reviews and panels. It blames
  cited evidence lines at the recorded review snapshot and marks current
  criterion findings on lines last written by a landed repair task. Original
  lines in the same file, preferences, and unproven commits do not count.
- Report missing review or member snapshots and unusable citations as explicit
  partial coverage. The diagnostic never uses current HEAD as a substitute and
  never changes a verdict, checkpoint, gate, or fix batch.
- Show recurrence in the existing build questions with an optional approach
  reconsideration. Cap and reconsideration briefs name the invariant, findings
  grouped by suspected design decision, input threat model, settled
  constraints, and alternatives; strategy changes keep the existing plan or
  define authority.
- Require approved strategy-change criteria to state class-wide rules with
  suitable class-level checks, while reviewers inspect an unlisted
  representative from the recorded evidence.

Validation: synthetic regression covers repair and original lines in one file,
moved lines after review, missing snapshots, provenance without a product file,
members, repeated calls, and non-fatal driver receipts. No project artifacts are
used as fixtures.

### Fork maintenance rules out of the installed AGENTS.md

- Move the fork's versioning, `FORK.md`, synthetic-data, and no-pull-request
  rules from `AGENTS.md`, which every consumer project installs, to the
  repository-only `CLAUDE.md`. Consumer projects keep the Git metadata rule in
  the shipped operating rules, and the runtime keeps enforcing it for generated
  branches, commits, and PR text.

Validation: install, Git metadata policy, and daemon plugin tests pass with the
shortened installed `AGENTS.md`.

## 1.6.1.11 — 2026-10-17

Upstream package base remains `1.6.1`.

- Collect Claude Code numeric usage outside landing and artifact transactions,
  retaining scoped windows, message identities, host versions and task attempts
  in a Git-common ledger. Final stream accounting replaces provisional cache
  estimates; retries, replayed blocks and copied messages are deduplicated.
- Keep collection errors, unfinished transcripts, unsupported CLI children,
  ambiguous attribution and missing prices explicit. Accounting never changes
  a product Verify verdict or blocks a proven landing.
- Generate dated offline primary-provider prices from models.dev (MIT), with
  conditional downloads and significant-change review. Apply actual per-request
  context thresholds and cache TTL prices; keep personal aliases outside Git.
- Freeze sanitized model/task/phase reports inside editorial publications so
  they survive clones without private transcripts. Publish a short description
  summary and one repeat-safe informational model table comment. Respect the
  Azure description limit and preserve human edits, work items and merge proof.
- Support explicit historical import and adoption of a matching existing table
  after checking its author. Price dates, collection windows and coverage remain
  visible in every report; monetary values are API equivalents, not invoices.

Implementation:
[`b2ac7c94 — collect usage and publish reproducible cost summaries`](https://github.com/drungrin/gsd-path/commit/b2ac7c9403f570bd17669b8af04f077201867ee0).

Validation: grouped Python regression covered 3,049 cases with 39 skips. Final
provider, accounting, runtime and full-cycle checks passed 143 tests; all 48
guard cases passed after resource synchronization. Node tests passed 116 cases
with one skip. Resource synchronization (601 resources), bytecode cleanliness,
whitespace and lock checks passed. A live historical import reproduced every
model row and the overall approved reference total. Runtime upgrade, installer
doctor, archive preflight and portable snapshot rendering passed. Publication
adopted the existing table after checking its author and values; a repeat
completed without any remote write. Archived evidence and reviewed product
heads remained unchanged; product Verify was not repeated.

Limits: collection supports Claude Code 2.x transcript usage; other hosts and
unobserved CLI children are outside its coverage. Three historical transcripts
without confirmed completion remain explicitly incomplete. The distributed
price snapshot initially contains five models; explicit refreshes can add
primary-provider models, and personal aliases remain local. Reports sum observed
messages in their recorded window; they do not assert an actual invoice. The
historical release-evidence limitation remains documented under `1.6.1.9`.
No upstream submission has been made for this change.

## 1.6.1.10 — 2026-10-17

Upstream package base remains `1.6.1`.

Implementation:
[`7d23cc2d — publish editorial PRs and synchronize delivery items`](https://github.com/drungrin/gsd-path/commit/7d23cc2d7ab7733c22e8636ca39aa3df7acdefcc),
[`58301f77 — resolve summarized PR repository metadata`](https://github.com/drungrin/gsd-path/commit/58301f77e898cf87c2a080ddac14983bd6219b88).

- Require authored editorial publications for every generated PR: a Conventional
  Commit title and self-contained problem, changes, scope, validation, and
  risks/limitations sections. Select `en` or `pt-BR` per project, independently
  of the language of internal pipeline artifacts.
- Bind publications to reviewed HEADs and immutable intent, manifest, and final
  evidence. Persist publication contracts outside archives and preserve human
  PR edits. Keep direct integration protocol messages and proof tags unchanged.
- Link only explicitly scoped Azure work items to the correct member PR, with
  acceptance snapshots and complete/partial/obsolete dispositions. After actual
  merge and tag validation, atomically apply delivery discussion and state under
  an expected work item revision. Use actual process state categories and retain
  removed-item history. No physical work item deletion is implemented.
- Keep product integration proof separate from resumable Boards synchronization.
  Service history closes the post-update crash window and prevents duplicate
  comments, repeated transitions, and automatic reversal of a human reopen.
- Extend runtime packaging, generated skill resources, ship instructions, and
  project configuration without rewriting reviewed archives.
- Resolve Azure list responses through the complete PR before validating its
  repository identity: list summaries omit the clone and web URLs. Complete
  responses still reject foreign repositories and fork sources.

Validation: grouped Python regression covered 3,014 tests with 39 skips;
corrected archive and preferences cases passed on rerun. The live-discovery
correction and delivery paths passed 81 focused tests, and the root shipment
source binding passed with all 17 editorial tests (3,016 distinct Python cases
in total). Node tests passed 116 cases with one skip. Resource synchronization,
version consistency, syntax, lock usage and whitespace checks passed. A live
project upgrade passed installer doctor and archive preflight, retained all 71
archived files unchanged, and published localized copy with four native item
links. Product verification was reused from its recorded output.

Limits: publication languages are currently `en` and `pt-BR`. Only work items
explicitly named in approved intent may be synchronized. Ambiguous process
states and changed acceptance remain pending for review. Actual work item
completion is deferred until the owner merges the PR and normal integration
proof passes; it was tested with synthetic service responses. The existing
historical release-evidence limitation remains as documented under `1.6.1.9`.
No upstream submission has been made for this change.

## 1.6.1.9 — 2026-10-16

Upstream package base remains `1.6.1`.

Implementation:
[`191b903a — move active orchestration into a dedicated repository`](https://github.com/drungrin/gsd-path/commit/191b903a97e7e95271bdc4afb2abb8e7e3d0ff4f),
[`3da09072 — ignore local coordinator tooling`](https://github.com/drungrin/gsd-path/commit/3da0907228dd71be1a05d01d88c5bb84940bebec).

- Add an explicitly approved, journaled migration from a quiet single-repo
  `ship/active` milestone to a dedicated coordinator and native code member.
  Retain original contracts, reports, command output, and task landing proofs.
- Project product commits into an independent clean graph, collapse metadata-only
  commits, preserve tree bytes and file modes, and record every commit mapping.
  Preserve both graphs remotely before rewriting only the approved backup ref
  with an exact previous-SHA lease. Refuse unrelated coordinator remote refs.
- Bind legacy task ownership to the member for fixes, regressions, superseding
  tasks, and dependency validation. Preserve the migration receipt through
  archive and require the migrated baseline in member integration proofs.
- Install native member hooks and runtime, retain ignored local product assets,
  and keep the original worktree under the coordinator Git directory. An
  unfinished migration blocks phase routing. Final review, shipping approval,
  user-completed product PRs, and integration validation remain required.

Validation: 190 focused migration, evidence, member, repair, Azure and squash
cases passed. The final migration and plan gates passed 204 cases, including
19 migration cases; the local-tooling correction passed those 19 migration
cases again. Node tests passed 116 cases with one platform skip. Generated
resource synchronization and whitespace checks passed. A live migration
preserved all 24 original task proofs, completed native cutover, and passed
installer doctor. The full Python test set passed 2,995 cases with 39 skips,
run in four groups. Archived migration receipts and original task proofs
also passed validation after moving artifacts to an archive path.

Limits: early preparation interruptions retain their staging for inspection;
publication and final cutover support retries. Rewriting Git refs does not erase
older snapshots retained by the provider in PR iterations. The upstream host
release-evidence check fails on unchanged historical `bound_branch` values;
the same failure was reproduced on revision `1.6.1.8`. Historical proof bundles
were retained. No upstream submission has been made for this change.

## 1.6.1.8 — 2026-10-16

Upstream package base remains `1.6.1`.

Implementation:
[`2c331232 — verify squash delivery and retain milestone history`](https://github.com/drungrin/gsd-path/commit/2c331232f9cc3a82a8280f7228382e5b6b72033d),
[`77559891 — report malformed retained history as unverified`](https://github.com/drungrin/gsd-path/commit/77559891372096a82646800a3f5bb42048fed772).

### Squash integration

- Accept user-completed Azure Repos squash PRs for coordinators and members,
  alongside the existing no-fast-forward merge method. PR creation no longer
  explicitly forces a non-squash completion option.
- Support Azure-backed `external-landing` without changing the locked
  integration mode. The owner still opens and completes the PR; Path discovers
  and verifies it through `az`, without requiring a canonical merge message.
  Existing Git-only external proofs keep their original contract.
- Bind the completed PR, closing user, exact reviewed source, recorded target,
  and landing. Require the target as the squash's sole parent and reproduce
  the complete integration tree with `git merge-tree`. Include concurrent
  target changes, binary content, paths, and modes; reject extra changes and
  unresolved or independently edited conflict resolutions.
- Retain the original evidence graph under an annotated
  `milestone-source/<archive>` tag, or
  `milestone-source/<project>-<archive>` for members, before publishing the
  landing tag. The landing proof records `Source-Tag`. Verify both tags and
  their publication unchanged before completion or branch retirement.
- Cover historical archive lookup, read-only status, fresh clones, interrupted
  tag publication, bind-next journal recovery, and retirement after the forge
  deletes the source branch. Reserve and authorize member source-tag pushes
  through the existing member guards.
- Keep GitHub's existing merge/rebase proof and other forges' Git-only external
  contract. Auto-complete, policy bypass, and Azure rebase remain refused.
  Regenerate the installed skill resources and document the revised closeout.

The proof follows Azure's
[merge-strategy policy](https://learn.microsoft.com/en-us/azure/devops/repos/git/branch-policies?view=azure-devops#limit-merge-types)
and [completed PR metadata](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/get-pull-request?view=azure-devops-rest-7.1).
[Git's merge-tree](https://git-scm.com/docs/git-merge-tree) computes the tree in
a disposable object store; verification does not alter repository objects,
refs, index, or worktree.

Validation: the full Python suite passed 2,974 cases with 39 platform skips.
The final malformed-tag recovery change also passed all 14 focused squash
cases. The Node suite passed 116 tests with one skip. All 501 generated resources
match their canonical sources; Python 3.9 syntax and whitespace checks passed.
Local Git regressions cover both coordinator modes, members, concurrent target
changes, source deletion, fresh clones, garbage collection, tampered trees and
metadata, interrupted publication, and next-milestone recovery.

Live read-only checks confirmed an existing Azure repository's mandatory
squash-only policy. A completed manual squash passed the provider provenance
check and reproduced its exact integration tree in a disposable bare repository.
The active consuming project's 24 existing task landings were accepted by the
new helpers without repeating its project or task commands. Its runtime and
installed contracts are upgraded explicitly for the current milestone; final
review and milestone integration remain pending their normal gates.

Limits: squash verification requires Git 2.38+. A merge requiring conflict
resolution stops for reviewed recovery rather than accepting provider metadata
alone. Both source and landing tags must remain published. Azure DevOps Server
remains outside this adapter. An active milestone still completes its normal
final review, archive approval, and user-completed PR before it can be reported
as shipped.

Upstream status: **not submitted**.

## 1.6.1.7 — 2026-10-16

Upstream package base is now `1.6.1`, synchronized through tag
[`v1.6.1`](https://github.com/open-gsd/gsd-path/releases/tag/v1.6.1), commit
[`0549f883`](https://github.com/open-gsd/gsd-path/commit/0549f88332a5001be436b953f48d034fc53cde13).
No later upstream commits are included. The fork revision continues from 6 to 7.

Implementation:
[`f137ca05 — synchronize upstream`](https://github.com/drungrin/gsd-path/commit/f137ca0530d48cbfca20b6b57890fc031cddc560),
[`c1b05c14 — enforce wave panel gates and preserve passing review history`](https://github.com/drungrin/gsd-path/commit/c1b05c14387ac499b4e1286b46da9f7eb11ddf3b),
[`3c665a80 — adapt upstream promotion fixtures`](https://github.com/drungrin/gsd-path/commit/3c665a809b59e82352932ab18e4fc21394b9ad9d),
[`c78a2103 — retain individual panel reports`](https://github.com/drungrin/gsd-path/commit/c78a2103f32f7487414bdf0d16de0c5e4839f23e).

### Upstream synchronization

- Include the 1.6.1 fixes for runtime-selected dispatcher subprocess helpers,
  absolute model-selection record paths, and promotion of lookahead artifacts
  over a retained documentation-audit carry-forward.
- Preserve the fork's Azure Repos support, conventional branches, attribution
  policy, Muse panel family, legacy recovery, and inline review publication.
- Adapt the three new promotion tests to the fork's conventional branch names.
- Keep the upstream release evidence and independently versioned desktop app
  under their existing version policies.

### Review-cycle completion

The 1.6.1 tag still allowed task dispatch after an incomplete panel and rejected
an earlier canonical `pass` when another review cycle followed it. This
contradicted the build contract's panel-driven repair flow and could stop a
milestone only when build completion or archive validation checked its history.

- Share the per-cycle panel requirement between build readiness and archive
  validation. Task readiness and later review dispatch stop with
  `review-panel-required` when a recorded cycle owes panel evidence.
- Cover blocked cycles, focused re-checks, and full/deep reviews. Accept a
  genuine detected-mode skipped receipt; refuse malformed receipts, named-mode
  skips, and simultaneous panel and skip artifacts. Off and verify-only modes
  retain their panel exemptions.
- Preserve earlier passing and blocked verdicts. A later review can follow a
  canonical pass after panel findings or product changes; the latest cycle
  still must pass every canonical lens and task. Never rewrite old verdicts.
- Accept the dispatcher's individual `wave-N.cycleC.panel.<family>.md` reports
  as auxiliary archive evidence. Preserve them without counting them as
  canonical reviews or substituting them for the merged panel. Reject unknown
  families and malformed filenames.
- Update the build contract and regenerate the installed skill resources.

Validation: the full Python suite exercised 2,958 cases with 39 skips. Its only
failures were the three newly imported promotion tests using upstream branch
names (three failed assertions and one error across their scenarios); after
adapting those fixtures, all three passed on rerun. After the final archive
compatibility change, 37 focused Python cases passed, including real local Git
worktrees that block dispatch before mutation, resume after the panel, complete
after an additional canonical pass, and retain individual reports through
completion and archive preflight. A temporary replay of the reported historical
review sequence passed without modifying its verdicts. The Node suite passed
116 tests with one skip. All 501 generated resources match their sources;
package dry run, Python 3.9 syntax, version consistency, and whitespace checks
passed. No live model inference was performed.

Limits: existing projects retain their pinned runtime until explicitly updated.
The changes do not fabricate missing panels or skipped receipts, waive required
evidence, change historical review text, or upgrade a consuming project. Older
pinned runtimes retain their own readiness behavior.

Upstream status: **not submitted**.

## 1.6.0.6 — 2026-10-15

Upstream package base remains `1.6.0`.

### Completed inline review publication

Implementation:
[`8288add3 — publish completed inline review reports`](https://github.com/drungrin/gsd-path/commit/8288add376279ceb3ad466e49fa04339d81c98df).

Claude Code's native subagent system prompt can forbid Markdown report files,
even for a write-capable reviewer assigned exactly one report. A completed
review could therefore return inline while file collection failed, causing an
unnecessary corrective dispatch before the parent manually saved the output.

- Define final-message report transport for native plan panels, wave reviews
  and lenses, wave panels, final/gap reviews, and finding skeptics. The
  independent child owns the review; the parent publishes its recorded text.
- Add `isolation.py stage-review` to extract a completed response directly
  from the bound child transcript and assignment metadata. Preserve text
  without adding fences, summaries, provenance, or credits to the report body.
- Retain a Git-common-directory receipt naming child, assignment, model,
  message ID, content hash, and parent publication. Support selecting an
  existing completed report before a later file-only refusal, without
  repeating the review. Refuse incomplete responses, identity mismatches,
  stale review bases, unsafe destinations, and unknown content replacements.
- Add `collect-artifact --expected-source` to bind collection to the captured
  report hash. Stage success does not validate the report; all existing
  format, evidence, verdict, and phase gates still apply.
- Update the host adapter, reviewer brief, plan/build/ship contracts, and
  generated distributions so a completed inline report is published directly
  instead of triggering file-publication retries or instruction forensics.

Validation: 680 focused Python cases passed, including 19 publication
regressions and six existing dispatcher review/panel/skeptic integration
cases. The Node suite passed 116 tests with one skip. All 501 generated
resources match their canonical sources. Package dry run, Python 3.9 syntax,
version/lockfile consistency, whitespace, and private-identifier checks passed.
The publication tests use synthetic transcripts and real local Git worktrees;
no live model inference was performed.

Limits: this transport is for native single-report review assignments in
Claude Code. It requires a completed child's recorded JSONL transcript and
companion assignment metadata. Other hosts, owner-supplied CLI commands,
coders, and multi-file planners retain their file-output contracts. Existing
projects must explicitly update their pinned runtime to receive the helper
and matching dispatch contracts. No live model inference is required by the
regression suite.

Upstream status: **not submitted**.

## 1.6.0.5 — 2026-10-15

Upstream package base remains `1.6.0`.

### Completed legacy artifact-collection journals

Implementation:
[`420d0749 — accept completed legacy artifact collection journals`](https://github.com/drungrin/gsd-path/commit/420d074981d802ec0d26ac1704774d7a6b484d2b).

1.6.0.4 could not read a project upgraded from a runtime that used
`gsd-path-verify/` branches. If that project had already collected inspection
artifacts, its completed collection journals failed with "invalid verify
branch". The failure blocked status, recovery, and `migrate-branch`.

- Treat a completed journal that names a legacy `gsd-path-verify/` branch as
  inert history.
- Still refuse an unfinished legacy journal. `migrate-branch` continues to
  block on it.

Validation:
- A new regression test proves a completed legacy journal is accepted and an
  unfinished one is refused.
- 456 tests across the isolation, state, git, diagnose, dispatch,
  lean-verification, and full-cycle modules passed.
- All 501 generated resources match their sources.

Upstream status: **not submitted**. This compatibility applies only to this
fork's branch names.

## 1.6.0.4 — 2026-10-15

Upstream package base remains `1.6.0`.

### Legacy bound branch migration before build

Implementation:
[`b28e951a — migrate legacy bound branches before build`](https://github.com/drungrin/gsd-path/commit/b28e951a2e27fd40389322c98d3edf168f8d5bed).

1.6.0.2 changed bound branch names. A project that upgraded its pinned runtime
while a milestone was active then failed validation on its `gsd-path/M00N`
branch, and nothing could repair it.

- Add `pipeline_state.py migrate-branch`. It renames an unpublished legacy
  `gsd-path/M00N` bound branch to `feature/milestone-NNN` and records the
  rename in STATE.md with a Log event. `--dry-run` previews the change without
  writing.
- Rename only before build. Refuse an archive transaction, any helper journal,
  a legacy task, verify, or integration branch, a target branch that already
  exists, and either name on origin. A failed `ls-remote` blocks.
- Rename the branch before writing STATE. After an interruption, a rerun finds
  the new branch checked out and completes the STATE write. A second run
  reports `already-migrated`.
- When validation meets a legacy bound branch, its error names the helper. The
  router contract previews the rename and asks the user to approve it. It
  forbids renaming the branch or editing STATE by hand.

Validation:
- Nine new tests cover preview, migration, interrupted-rename recovery,
  idempotence, each refusal, and the CLI JSON contract. They use local Git
  repositories with a bare origin.
- The full Python suite ran 2,921 tests, with 39 skipped. The Node suite passed
  116 tests, with one skip.
- All 501 generated resources match their sources. Whitespace and
  private-identifier checks passed.

Limits:
- A milestone that has entered build, or that published its branch, keeps its
  pinned runtime until it ships or is abandoned. The note under 1.6.0.2 still
  applies to it.
- Member repositories have no member branches before build, so the helper
  changes no member branches.

Upstream status: **not submitted**. This migration applies only to this fork's
branch names.

## 1.6.0.3 — 2026-10-15

Upstream package base remains `1.6.0`.

### Native Muse review panel family

Implementation:
[`ef107e70 — extend review panel family detection`](https://github.com/drungrin/gsd-path/commit/ef107e70866588f78b5988be65b46821035d19ef).

- Recognize `muse` as an independent plan and wave review family in named
  panels and detected rosters. Real Muse model IDs need no alias using
  another family. Preserve provider-qualified IDs and existing host controls.
- Cover `muse-spark-1.3`, `muse-spark-1.3-contributor`, the 1.2 variants,
  `muse-spark-1.1`, and `muse-wonder-1.0` in model-family detection.
- Prefer Standard over Contributor when both variants of the same Muse
  version are advertised. Keep explicit assignment overrides available.
  Preserve the three-family cap, independent-family checks, and inherited
  canonical reviewer selection.
- Update panel templates and user configuration examples, and distribute the
  resolver through the generated skills and packaged runtime.

Model IDs and mode differences were checked against
[Meta's model catalog](https://dev.meta.ai/docs/models) and
[pricing-mode documentation](https://dev.meta.ai/docs/pricing-rate-limits)
on 2026-10-15. Contributor permits training on submitted content; Standard
does not. Spark 1.3 Standard supports `max` reasoning; Contributor does not.

Validation: 89 focused Python tests and four Node version/install tests passed,
including real child argument delivery, unchanged inherited primary review,
named and detected rosters, provider IDs, parent exclusions, unavailable
families, and plan/wave artifact merging. All 501 generated resources match
their sources. Package dry run, version/lockfile consistency, whitespace, and
private-identifier checks passed. No live model inference was performed.

Limits: host-advertised child models determine availability. Version ordering
does not rank different Muse model lines by capability; use an explicit
assignment override to choose a line. Existing projects need a runtime update
to receive the new family, and configuration preferences do not rewrite
approved milestone contracts.

Upstream status: **not submitted**.

## 1.6.0.2 — 2026-10-15

Upstream package base remains `1.6.0`.

### Conventional branches and change-only Git metadata

Implementation:
[`f01f0662 — use conventional branches and remove attribution`](https://github.com/drungrin/gsd-path/commit/f01f06625729bcc05e3807c397b50550ad78cffd).

- Replace framework-named milestone, member, task, verification, and integration
  branches with lowercase Conventional Branch names using purpose prefixes.
  Update routing, binding, guards, recovery, and integration as one contract.
- Normalize branch descriptions separately from task identifiers. Preserve
  authorization and landing checks, including retained-branch recovery.
- Create GitHub and Azure PRs without credits or generated-by footers. Remove
  existing tool attribution on reuse without discarding human-written content.
- Reject tool-attribution commit trailers and disable client-added commit/PR
  credits and session links, with or without optional guard hooks. Member
  worktrees inherit the same attribution policy.
- Record the no-attribution requirement in the fork rules and phase contracts.
- Recognize conventional milestone branches in dashboard discovery while still
  reading branches from older pinned runtimes. Update the displayed examples.
- Keep upstream npm publication tests on a synthetic SemVer release rather
  than deriving that fixture from the fork's four-component source version.

Validation: all 127 Python test modules were exercised, totaling 2,904 cases
with 39 platform skips. The only remaining stale worktree-path expectation was
corrected, and all nine tests in that module passed on rerun. The Node suite
passed 116 tests with one skip. All 501 generated resources match their
canonical sources. Package dry run, version/lockfile consistency, whitespace,
and private-identifier checks passed. Provider tests use local Git repositories
and simulated API responses; no live PR was created.

The new branch contract applies to the updated runtime. Existing projects keep
using their pinned runtime until explicitly upgraded; finish an active milestone
before upgrading rather than renaming its branches or rewriting its archives.

Upstream status: **not submitted**.

## 1.6.0.1 — 2026-10-15

Upstream baseline: package `1.6.0`, commit
[`73bf62e3`](https://github.com/open-gsd/gsd-path/commit/73bf62e34f1adb8e9d86ae43378c8dd1200539bc).

### Azure Repos support

Implementation:
[`4b61fba6 — support Azure Repos members and pull requests`](https://github.com/drungrin/gsd-path/commit/4b61fba67e8566472175b3bdea049cb3f322118b).

- Accept existing Azure DevOps Services repositories as multi-repo members,
  through SSH or HTTPS. Decode project names for identity checks and CLI
  arguments, and detect duplicate checkouts across URL forms.
- Allow GitHub and Azure members in the same milestone. Direct integration
  continues to use Git without requiring a forge CLI.
- Use `az` with the `azure-devops` extension for Azure PR discovery, creation,
  description repair, and completion metadata. Organization, project, and
  repository are explicitly scoped instead of taken from CLI defaults.
- Wait for user completion; verify the source SHA, no-fast-forward merge,
  ancestry, and milestone tag. Reject auto-complete, policy bypass, squash,
  rebase, foreign repositories, and ambiguous PRs.
- Include the adapter in packaged skills and pinned runtimes, and accept Azure
  PR tag proofs when advancing to the next milestone.

Main implementation files: [Azure adapter](scripts/azure_repos.py),
[member validation](scripts/members.py), [integration](scripts/integration.py),
and [milestone proof](scripts/pipeline_git.py). Usage is documented in
[README.md](README.md#multi-repo-milestones).

Validation: 219 focused regression tests, 23 GitHub PR tests, 21 runtime
installation tests, pinned-runtime activation, resource synchronization, and
checks for private identifiers in the added content. Azure tests use real local
Git repositories and simulated `az` responses; no live Azure PR was created.
All test organizations, projects, repositories, and accounts are fictitious.

Limits: Azure repositories must already exist. Automatic repository creation
remains specific to GitHub. Azure DevOps Server is outside this change.

Upstream status: **not submitted**. The implementation commit is separate from
fork versioning and history, so it can be reviewed or cherry-picked on its own.

### Fork maintenance

Introduce the fourth version component, document the policy in `AGENTS.md`, and
maintain this history. These changes belong to the fork and are not part of the
Azure feature proposed for a future upstream contribution.

Fork metadata validation: 12 version-stamping and update-check tests passed.
An npm pack dry run confirmed version `1.6.0.1`, matching lockfile versions,
and inclusion of this history in the source package. Resource synchronization
and private-identifier checks passed.

## Maintaining this file

For each subsequent delivery, add its fork version and date, upstream base,
behavior changes and rationale, implementation commit links, validation, and
known limits. Track upstream submission and merge links when available. Keep
implementation commits separate from fork bookkeeping whenever practical.
