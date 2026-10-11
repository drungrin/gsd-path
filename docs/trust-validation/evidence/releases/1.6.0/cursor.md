---
schema: gsd-path/live-evidence/v1
host: cursor
package: 1.6.0
pipeline: gsd-path/v2
candidate: 2aad06114ebf7d4b88ba70d3de63627e4c238c2f
verdict: pass
child_spawn: pass
state: pass
task_verify: pass
wave_review: pass
final_review: pass
archive: pass
integration: pass
guard_tier: native-fail-closed
---

# Live milestone evidence — cursor

Full quick-lane milestone run by the pinned candidate on a fresh fixture; evaluator-driven owner gates. Run directory: `/Users/jeremymcspadden/orca/evaluations/gsd-path-release-1.6.0-2aad0611/cursor`.

## Environment

- Host and CLI version: Cursor CLI 2026.10.01-e373342
- Operator: local release evaluator
- Date: 2026-10-11
- Fixture repository: /Users/jeremymcspadden/orca/evaluations/gsd-path-release-1.6.0-2aad0611/cursor/quick/repo
- Child-agent API used: Task (description build_t001)

## Evidence

- Install command and result: cursor/install.json
- Router invocation and state artifact: cursor/router.json
- Child spawn output: cursor/child-spawn.json
- Task branch, worktree, and landing commit: cursor/task-landing.json
- Task Verify command and result: cursor/task-verify.json
- Wave and final review artifacts: cursor/reviews.json
- Archive validation output: cursor/archive.json
- Integration merge and milestone tag: cursor/integration.json
- Remaining `git worktree list` output: cursor/worktrees.json
- Native guard and Git-hook results: cursor/guards.json

Fixture Git bundle: cursor/fixture.bundle (all refs, including origin/main and the annotated milestone tag).
