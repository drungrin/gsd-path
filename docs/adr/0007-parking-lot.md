# Deferred work goes to a parking lot that every milestone boundary must decide

Status: accepted (2026-11-16). Fork-only. Upstream `main` (`d5a30f5f`) has no
store for deferred work across milestones; its closest relative is the
docs-audit queue, whose `planned: no` rulings are offered once at planning.

## Context

The PR fix lane ([ADR 0006](0006-pr-review-fix-lane.md)) lets the owner mark a
change-request item `follow-up`: the PR conversation gets a reply ("left for a
later change") and stays open. Nothing in the pipeline carried that item
forward. The next milestone was defined from the roadmap entry alone, so a
follow-up survived only in the PR thread and in the owner's memory. On one
consumer PR, eleven of seventeen review items were answered that way,
including a server-side request forgery bypass and two data-loss paths.

A passive backlog does not fix this. A list that nobody must read before
moving on is the same as no list. The earlier GSD parking lot (`## Backlog`
entries with manual promotion) had that problem. A single "parking-lot
milestone" at the end of the roadmap has it too: until then, a security item
waits behind every planned milestone, and the end of the program is the moment
with the least appetite for leftovers.

## Decision

Keep `.project/PARKING.md`, written only by `scripts/parking.py`, as project
metadata that persists across milestones like LESSONS.md. It never moves into
an archive.

- **Capture.** `sync` imports every `follow-up` of a recorded PR round (lane or
  record-only) from the round capsules in the git common directory, once per
  source link. `add` parks items from a JSON file with the owner's words.
  Each item has an id `K###`, a class (`security`, `data-loss`,
  `correctness`, `operations`, `docs`, `other`), its source link, repository,
  location, the finding text, and a decision log quoting the owner verbatim.
- **Triage at every boundary.** Define syncs the lot and presents every open or
  due item in the intent confirmation. The owner decides each one: `planned`
  for this milestone, `scheduled` for a named later milestone (a `pending`
  ROADMAP entry in program flow), or `dropped`. Scheduling a `security` or
  `data-loss` item later needs an explicit `accept_risk`.
- **Gates.** The intent approval transition refuses an open item, an item
  scheduled for this milestone or an earlier one, and an item scheduled for a
  milestone that is no longer pending. The build-start transition runs the same
  check, which covers a promoted lookahead milestone, whose define ran on a
  track that never writes the active lot. The plan gate requires every item
  planned for this milestone to be cited (`K###`) in the Context of a
  non-withdrawn task, and refuses citations of items not planned here.
- **Settlement.** At the next boundary, `sync` marks a planned item `done` when a
  done task of its milestone's archive cites it. Otherwise, it opens the item
  again with a logged reason.
- **Safety net.** In program flow, scheduling is limited to pending ROADMAP
  milestones. At the last milestone, the remaining items must be planned or
  dropped, or a roadmap re-slice adds a final parking-lot milestone. When every
  entry is terminal, the router runs `parking.py check --gate program` and
  reports any item that is still unresolved, together with any follow-up from
  the last PR that was never parked.
- **Write window.** Writes are legal only from inspect to plan. The plan approval
  checkpoint commits `.project/` in full, so the lot rides in that commit.
  Ship never writes it, and the ship commit allowlist stays unchanged.

## Consequences

- A follow-up can no longer disappear between milestones without the owner's
  recorded words, and a deferred security or data-loss item carries an
  explicit risk acceptance.
- Every boundary adds one question when the lot has open or due items, and
  none when it is empty. A project without PARKING.md behaves as before.
- Items found during a build or in a review cannot be written until the next
  boundary. Review warnings still go into their review files; the owner parks
  them with `add` at the next define.
- A follow-up from the PR of the program's last milestone has no later
  boundary. It is reported at program completion instead of being enforced.
