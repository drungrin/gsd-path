# Fork history

This file tracks differences in [this fork](https://github.com/drungrin/gsd-path)
from [upstream GSD Path](https://github.com/open-gsd/gsd-path), including changes
that can be proposed upstream independently.

Current fork version: **1.6.2.25**. Upstream package base: **1.6.2**.

## Version policy

Versions use `X.Y.Z.N`. The first three components follow the upstream version
most recently synchronized into this fork. The fourth component is a monotonically
increasing fork revision. Local features and fixes increment only that revision;
an upstream sync changes the first three components without resetting it.

This is a fork version format, not npm SemVer. Install the fork from its source
checkout. The upstream npm release workflow, conventional-commit bump helper,
and npm release notes remain specific to the upstream package. A Git push does
not publish an npm package or create a release tag. Keep upstream release
records in `CHANGELOG.md`; record fork changes here.

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
