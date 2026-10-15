# Fork history

This file tracks differences in [this fork](https://github.com/drungrin/gsd-path)
from [upstream GSD Path](https://github.com/open-gsd/gsd-path), including changes
that can be proposed upstream independently.

Current fork version: **1.6.0.1**. Upstream package base: **1.6.0**.

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
