---
name: gsd-path-inspect
description: Inspect an existing codebase and its documentation to establish brownfield ground truth before intent is defined. Use only when the user explicitly invokes $gsd-path-inspect or an active $gsd-path router explicitly routes to this phase.
---

Before executing project helpers, read [runtime selection](references/runtime-selection.md)
and [operating rules](references/operating-rules.md).

# GSD Path Inspect Phase

Establish ground truth about an existing project before intent is defined.
Detect, scan, and audit first; recommend and question only after the evidence
is on disk. Never question a user about a codebase the pipeline has not read.

Routing instructions below are caller handoffs under the AGENTS.md handoff
rule; never invoke an explicit-only sibling skill yourself.

Before phase work and again before completion, apply the AGENTS.md
pending-answer rule with the bundled `scripts/discussion_records.py`; a
follow-up owned by inspect is resolved through its artifact gate, or the
phase blocks.

## Preconditions

If STATE.md is missing, run the bundled
`python3 <absolute-bundled-script> initialize --repo <absolute-root>
--template <absolute-state-template> --require-git` helper (`scripts/detect_project.py`) and
follow its returned JSON `verdict` / `route`. This is the only no-state
boundary; do not run `classify` first or classify from a directory listing or
conversation. Handle `route: setup-repository` before the write gate: preserve
the folder unchanged and return to `$gsd-path` for repository setup; no state
was written. If the command exits nonzero, returns `error`, or returns
`wrote_state: false`, report the error and block without routing or claiming
STATE.md was written.
- `owned` — continue under the existing-state rules below.
- `orphan` — return to `$gsd-path` for orphaned-state recovery instead of
  initializing or overwriting it.
- `greenfield` — the helper writes STATE.md at `define/active`; skip inspection
  and route to `$gsd-path-define` from this returned verdict.
- `brownfield` — require `wrote_state: true`, then continue with the helper's
  STATE.md at `inspect/active`.
If `.project/STATE.md` exists, run `python3 <absolute pipeline_state.py>
validate --repo <absolute root> [--project-dir .project/next]`; a non-zero
result returns to `$gsd-path` for ownership checking. Legal entry is
`inspect/active|blocked`, or `define/active` on the `.project` track when an
active router routes `inspect` with `mode: members`; `inspect/done` routes to
define, and any later phase stops. For that `mode: members` entry, first run
`python3 <absolute pipeline_state.py> transition --repo <absolute root>
--event "member inspection started" --expect-phase define --expect-status
active --expect-milestone <current milestone or null> --expect-branch
<current branch> --expect-archive null --set-phase inspect --set-status
active`; it refuses unless the route's preconditions still hold, and its
returned `inspect/active` state is the entry for Member inspection below.
When an active router supplies `.project/next/`, require its STATE.md
to be a regular non-symlink file and apply these rules to that track state.
When an existing state records a bound branch (single-milestone restart
or program next-milestone), require the current symbolic branch to match and
preserve both `branch` and `milestone`. Do not re-enter `inspect/done` in the
same milestone; a later milestone's `inspect/active` is a new scan.

## Process

When `.project/MEMBERS.md` exists, use [Member inspection](#member-inspection)
on either track instead of this section's single-repo flow.

For an initial `inspect/active` inspection on the `.project` track, with neither output
artifact present and a clean Git product at the recorded HEAD, run the bundled
`python3 <absolute workflow_run.py> prepare-inspect --repo <absolute root>
--expected-head <recorded HEAD>` first. It performs the inventory freeze and
both sidecar preparations from steps 1–2 and writes complete assignment briefs.
Its state validation and pending-answer check satisfy those entry checks;
reuse their results at this boundary.
Use its returned `inspection.inventory_file` for the gate. Before dispatch, read
and apply the [runtime dispatch contract](references/dispatch.md), including its
host-specific context isolation. Dispatch each returned logical `task_name` in a fresh context with its `brief_file` path and the current
user constraints, including configured budgets. The child reads that file;
the parent does not read or rewrite the roles, templates, or generated briefs.
When both children return, review the mapper's Map and Findings against its
template as in step 3. After that review passes, run the bundled
`python3 <absolute workflow_run.py> finish-inspect --repo <absolute root>
--expected-head <recorded HEAD> --inspection <returned receipt_file>
--mapper-reviewed`. This runs the docs gate, checks the audit baseline,
collects and retires both sidecars, checks pending discussion, and records
`inspect/done` through the canonical transition. Do not repeat those operations.
Present step 4's ground truth and use step 5's caller handoff. A failure uses
step 3's failure contract and the returned step evidence; do not blindly rerun
the completion command or repeat already proven steps.
Prior evidence, lookahead, or a dirty/non-Git product uses steps 1–2 below.
Preparation never changes phase state; neither command dispatches agents.

1. Before creating or changing `.project/` Markdown, freeze the helper's exact
   stdout from `python3 <absolute check_docs_audit.py> --repo <absolute root>
   --emit-inventory` in a temporary file. Do not rediscover or edit that
   inventory. If STATE.md is now missing, restart Preconditions and route from
   the new `initialize` result; never continue from an ignored result. Record
   the SHA-256 of each existing assigned destination and preserve the existing
   DOCS-AUDIT.md in a temporary prior-audit file; when it records an
   `Audited HEAD` Git resolves as an ancestor of HEAD, freeze the changed set
   as `$gsd-path-docs-audit` step 1 describes. Preserve an existing
   router-bound branch and milestone.
2. Dispatch two independent agents in parallel, following the local
   [runtime dispatch contract](references/dispatch.md) and its deterministic
   task-name rules:
   - **Codebase mapper** — role
     [codebase-mapper](references/codebase-mapper.md), template
     [codebase](templates/codebase.md), output
     `.project/research/evidence-codebase.md` (`.project/next/research/` in
     Lookahead mode), task name `inspect_codebase`.
   - **Docs auditor** — role
     [docs-auditor](references/docs-auditor.md), template
     [docs-audit](templates/docs-audit.md), output
     `.project/research/DOCS-AUDIT.md` (same next/ prefix in Lookahead mode),
     task name `inspect_docs`.
   Give each the absolute repo root and exclusion rule. Give the auditor the
   exact frozen inventory, the changed set when one exists, and `alignment
   mode: false`; it audits only that
   list and never rediscovers paths. The frozen inventory travels inside the
   dispatch brief; never persist it as a `.project/` sidecar file. Pass an existing DOCS-AUDIT.md separately
   as carry-forward input so its `## User rulings` and `planned` values remain
   verbatim. When Git has a resolvable HEAD and no non-`.project` worktree
   changes, the orchestrator creates a verify sidecar for each agent with
   `python3 <absolute isolation.py> isolate-verify --repo <absolute primary>
   --base <HEAD> --name inspect-codebase` and `--name inspect-docs`, and
   includes its path and revision for project commands; expected new pipeline
   artifacts do not make product code dirty. Each agent writes only its
   assigned output under that sidecar and keeps it there for the gates in step
   3; its `Repo root` header still names the absolute primary. Otherwise no
   project command may run. The docs-auditor brief carries current HEAD as the
   audit baseline only when its verify sidecar was created at HEAD; otherwise
   it carries `none`. The auditor writes that baseline as `Audited HEAD`.
3. Gate both artifacts against their templates: the codebase evidence needs
   a filled `## Map` plus findings as observed — no quota, but an empty
   findings section must say why; the docs audit must pass the bundled
   `python3 <absolute check_docs_audit.py> --repo <docs sidecar> --primary
   <absolute primary> --audit <track-relative DOCS-AUDIT.md> --inventory <frozen inventory file>`, where
   the audit path is `.project/research/DOCS-AUDIT.md` normally and
   `.project/next/research/DOCS-AUDIT.md` in Lookahead mode; add
   `--prior-audit <temporary prior-audit file>` when one was preserved and
   `--changed <temporary changed-set file>` when one was frozen
   (disjoint `## Doc:` sections and `## Descriptive docs` equal to the frozen
   inventory, every claim a valid verdict with evidence, Summary counts and
   remediation queue consistent). After each artifact passes, collect it with
   `python3 <absolute isolation.py> collect-artifact --repo <absolute primary>
   --source <returned worktree> --base <recorded HEAD> --branch <returned
   branch> --source-path <assigned track-relative path> --destination-path
   <assigned track-relative path>`, adding `--expected-destination <recorded
   prior SHA-256>` when that destination existed. Require the returned base,
   branch, source, and destination to match. Only then retire with `python3
   <absolute isolation.py> retire --repo <absolute primary> --worktree
   <returned worktree> --branch <returned branch>` without `--force`.
   Redispatch one complete corrected brief under the same logical task
   name, following the runtime dispatch contract. If it still fails, run
   `pipeline_state.py transition` with expected `inspect/active`, the exact
   current branch and archive values, `--set-status blocked`, and an event
   naming the failed artifact gate. Present **Outcome** with the failed gate, **Review**
   linking each malformed output that exists or STATE.md when an output is
   missing, and **Next** naming the one correction or user decision required;
   then stop.
4. Present the ground truth to the user, brief — a summary, not a dump:
   - what the project is (stack, architecture, entry points, maturity);
   - what demonstrably works (verified claims, passing verifies);
   - drift: what the docs claim that the code contradicts, and what exists
     with no documentation at all;
   - the mapper's open questions about apparent intent.
   Lead with the outcome, then provide absolute-path Markdown links to the
   track's `research/evidence-codebase.md` and `research/DOCS-AUDIT.md`
   (`.project/research/` normally, `.project/next/research/` in Lookahead
   mode), then state that define is next.
5. Run `python3 <absolute pipeline_state.py> transition --repo <absolute root>
   [--project-dir .project/next]
   --event "inspection artifacts passed" --expect-phase inspect --expect-status
   active --expect-milestone <current milestone or null> --expect-branch
   <current branch or null> --expect-archive <current archive or null>
   --set-phase inspect --set-status done`. This helper is the only ordinary
   STATE mutation; require its returned state to be `inspect/done`.
   Use the executable phase handoff in AGENTS.md.

## Member inspection

A coordinator with `.project/MEMBERS.md` inspects every brownfield member
repository from its pinned tree, never from the team's working checkout. The
bundled `workflow_run.py` owns scope, sidecars, gates, collection, retirement,
and the phase transition in three commands; add `--project-dir .project/next`
to each in Lookahead mode. None of them dispatches agents or fetches.

1. **Fetch.** For each member in MEMBERS.md, run `git -C <Checkout> fetch
   origin`. The runtime pins the member at its local
   `refs/remotes/origin/<Default branch>` and never fetches; a failed fetch
   blocks with its output.
2. **Prepare.** Run `python3 <absolute workflow_run.py> prepare-inspect --repo
   <absolute root> --expected-head <coordinator HEAD>`. It checks the track
   state, branch, HEAD, and pending discussion, reads `members.py inspection`,
   and blocks before any sidecar on an unverifiable member (missing checkout,
   marker, or pin ref). A greenfield coordinator gets no pair; a brownfield one
   gets `inspect_codebase` and `inspect_docs` while its map is absent, with an
   existing coordinator audit as carry-forward input. Each brownfield member
   whose evidence is missing gets `inspect_codebase_<member>` and
   `inspect_docs_<member>` (normalized; colliding names block). Covered and
   stale members are skipped and listed in `inspection.skipped`; stale
   evidence is advisory. Evidence whose `Member HEAD` is not on the pin ref's
   history counts as missing and is reassigned. It freezes pins, pinned member
   docs inventories, destination hashes, prior member audits, and their pinned
   changed sets outside `.project/`, so a pinned tree the gate cannot
   inventory blocks before any sidecar exists, then creates the stage-1
   sidecars: the coordinator pair, and for each member an artifact
   sidecar plus its read-only member checkout at the pin, the sibling
   `../<member>`.
3. **Stage 1.** Apply the [runtime dispatch contract](references/dispatch.md)
   and dispatch every logical task named in `inspection.dispatch` in a fresh
   context with its assignment's `brief_file`. A member child runs commands
   only in its member checkout and writes only the absolute artifact path in
   its brief, with the member header lines the brief lists.
4. **Advance.** When every stage-1 child returns, review each mapper's Map and
   Findings as in step 3 of the single-repo flow; member maps are final now.
   Then run `python3 <absolute workflow_run.py> advance-inspect --repo
   <absolute root> --expected-head <coordinator HEAD> --inspection <returned
   receipt_file> --mapper-reviewed`. It gates every member map header at its
   recorded pin before changing any sidecar, retires each mapper's member
   checkout while keeping its artifact, creates the docs auditor's sidecars at
   the same pin with the inventory frozen at prepare, and returns the stage-2
   tasks in `inspection.dispatch`. An auditor pair it creates but cannot record
   is retired before it fails, so a rerun recreates it. Skip this step when
   the receipt has no member assignment.
5. **Stage 2.** Dispatch those auditors as in stage 1. Each brief carries
   `Audited HEAD` equal to the pin, `Alignment mode: no`, the frozen
   inventory file, and any prior audit and changed set.
6. **Finish.** When every child returns, run `python3 <absolute
   workflow_run.py> finish-inspect --repo <absolute root> --expected-head
   <coordinator HEAD> --inspection <returned receipt_file> --mapper-reviewed`.
   It gates every artifact before collecting any (coordinator audit as in
   step 3; member maps at their pins; member audits with
   `check_docs_audit.py --member` against the frozen inventory, prior, and
   changed set), collects each with its expected destination hash, retires
   every sidecar, runs `check_handoffs.py member-evidence` coverage, and
   records `inspect/done` with `inspection artifacts passed`. Do not repeat
   those operations.
7. **Failures.** A blocked result names the failed step; its evidence is in
   `steps`. Redispatch one complete corrected brief under the same logical
   task name and rerun the same command; advance and finish resume from the
   receipt. If it still fails, apply step 3's blocked-transition and
   Outcome/Review/Next contract. To abandon a preparation, retire each
   assignment with `python3 <absolute isolation.py> retire-member-inspect
   --repo <absolute root> --member <member> --name <sidecar name>` (it works
   without the receipt) and the coordinator pair with `isolation.py retire`.
   `project runtime upgrade required` means the selected project runtime
   predates member inspection: stop and report it; do not inspect by hand.
8. **Report.** Present step 4's ground truth for the coordinator and each
   inspected member, linking every collected
   `research/evidence-codebase-<member>.md` and
   `research/DOCS-AUDIT-<member>.md` with its `Member HEAD` pin. List skipped
   members with their state, stale ones as advisory, and any passive carries.
   Then use the executable phase handoff in AGENTS.md.

## Lookahead mode

Entered only when an active router supplies the lookahead track root
`.project/next/` while the active STATE.md is `build/active` in program
flow. Evaluate every state and artifact precondition against the track:
`.project/next/STATE.md` is the state file and the outputs are
`.project/next/research/evidence-codebase.md` and
`.project/next/research/DOCS-AUDIT.md`. Freeze the inventory from the
repository root as usual. Carry forward the active
`.project/research/DOCS-AUDIT.md` when it exists so its `## User rulings`
and `planned` values remain verbatim; otherwise carry forward a track-local
audit if present. Never write an active-path artifact.

With `.project/MEMBERS.md`, Lookahead mode runs Member inspection with
`--project-dir .project/next`. It requires the active STATE.md at
`build/active` with CHARTER.md and ROADMAP.md, the current branch equal to
the active STATE.md branch, and `--expected-head` equal to the coordinator
HEAD. Outputs go to `.project/next/research/`. A member that the active build
locked in `.project/build/members.json` is pinned at its bound branch tip;
any other member at its local origin default. A fresh audit carries forward
the track's own audit when it keeps every active ruling; otherwise preparation
blocks. Finish copies each active audit that still has a `Planned: no` ruling
and receives no fresh audit (a greenfield coordinator, a greenfield or
removed member) byte for byte into `.project/next/research/`; an existing
copy is kept only when it keeps those rulings. It records `inspect/done` on
the lookahead track only and creates no member lock, bound branch, or push.
Without MEMBERS.md, the workflow commands refuse lookahead; use steps 1–5.

## Rules

- Scanning is read-only. Inspection changes nothing outside `.project/`.
- Project commands run only in agent-specific disposable verification
  worktrees at a recorded clean revision; otherwise checks use static evidence
  or are `unverifiable`. They never run in the source worktree.
- Report reality, not judgment: "tests exist but 3 fail" — never "test
  hygiene is poor". The user may know exactly why those 3 fail.
- Doc-vs-code conflicts are surfaced, never auto-resolved; whether the doc
  or the code is wrong is the user's ruling, captured while defining intent.
- Inspection evidence feeds the whole pipeline: researchers treat
  `evidence-codebase.md` as a fifth standard dimension when it exists, the
  planner must match its conventions, and reviewers may cite it.
