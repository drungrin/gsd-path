@AGENTS.md

## Fork maintenance

Repository-only. `AGENTS.md` is installed into every consumer project, so rules
for maintaining this fork live here instead.

- The fork uses `X.Y.Z.N` versions. `X.Y.Z` is the version of the upstream
  source most recently synchronized into the fork; local changes never bump
  those three components. `N` is this fork's revision and increases for each
  fork release. Never reset or decrement `N`, including after an upstream sync.
  For example: `1.6.0.1`, `1.6.0.2`, then `1.7.0.3` after synchronizing
  upstream `1.7.0`.
- Keep `package.json`, both root version fields in `package-lock.json`, the
  README fork notice, and `FORK.md` consistent. Do not change the separately
  versioned desktop app or rewrite historical upstream release evidence.
  Update fork revision metadata directly: upstream `npm version` and
  `release:bump` assume three-component SemVer and must not decide fork bumps.
- Maintain `FORK.md` as the history of differences from upstream. Every fork
  delivery records its fork version, upstream base, behavior changes, relevant
  implementation commit links, validation, and known limits. Record upstream
  submission or merge links when they exist, so changes can later be
  contributed without including unrelated fork bookkeeping. Never invent a
  commit hash or claim an upstream submission that did not happen.
- Always split a fork change by upstream applicability. Before committing,
  check the affected code against the latest `upstream/main`. A fix or
  feature that also applies to code present upstream goes in its own commit
  with its tests, free of fork-only code and fork bookkeeping, so it can be
  proposed upstream as is. Fork-only code goes in separate commits. `FORK.md`
  names which implementation commits apply upstream.
- When a session analysis, consumer run, or fork change shows that a fork
  decision makes the outcome worse than upstream behavior (more blocks,
  rulings, failures, review cycles, or cost), tell the user plainly with the
  evidence, even when not asked, so they can decide whether to revert it.
  Never revert a fork decision on your own.
- This repository is public. Tests, fixtures, and documentation use synthetic
  organizations, projects, repository names, and accounts. Never copy private
  identifiers, credentials, personal filesystem paths, or content from a
  consumer project into tracked files, commit messages, or `FORK.md`.
- This fork has a single maintainer and takes no pull requests. Do not open,
  draft, or propose one. Integrate finished branches into `main` locally, and
  push only when asked. Consumer projects keep their `pull-request`
  integration mode.
- Branch names, commits, and PR text follow the Git metadata rule in
  `skills/gsd-path/references/operating-rules.md`. The runtime enforces it for
  everything it generates or publishes; keep that enforcement and its tests in
  `tests/test_git_metadata_policy.py` when changing branch, commit, or PR code.

## Agent skills

Repository-only: `docs/agents/` configures this source repository and is not installed into consumer projects.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
