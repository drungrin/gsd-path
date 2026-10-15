# Fork history

This file tracks differences in [this fork](https://github.com/drungrin/gsd-path)
from [upstream GSD Path](https://github.com/open-gsd/gsd-path), including changes
that can be proposed upstream independently.

Current fork version: **1.6.0.5**. Upstream package base: **1.6.0**.

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
