"""PR fix lane end to end (ADR 0006): a member milestone ships to an open GitHub PR, the
reviewer requests changes, and the lane answers with one delta wave instead of a second
complete final review. Every gate runs for real against temp repos with bare remotes and
the guard hooks installed; only the GitHub API is a fake.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_state, check_handoffs, integration, isolation, lean_verification, pipeline_state
from scripts import pr_fix_lane
from tests._editorial_fixtures import publication_fixture
from tests.test_pr_fix_lane import FakeThreads
import tests.test_member_full_cycle as cycle

BRANCH = cycle.BRANCH
SLUG = cycle.SLUG
TODAY = cycle.TODAY
BOUND = "feature/acme-milestone-001"
PULL = "https://github.com/acme/web/pull/7"

LANE_TASK = """---
id: T002
title: refuse a missing scope
wave: 2
deps: []
status: pending
agent: null
base: null
worktree: null
task_branch: null
lane_round: {lane_round}
repo: web
files:
  - lib.py
---

# T002 — refuse a missing scope

## Context

Closes P001 and P002 from the PR change request: the library must refuse a call without a scope.

## Approach

- Add `require_scope` to `lib.py` without changing `value`.

## Interface contract

None

## Intent coverage

- None

## Acceptance criteria

1. `lib.require_scope(None)` raises `ValueError`.

## Verify

```bash
python3 -c "import lib
try:
    lib.require_scope(None)
except ValueError:
    pass
else:
    raise SystemExit(1)" && python3 -m py_compile lib.py
```

## Log

- {today} — created by the orchestrator from the PR fix lane ruling
"""

DELTA_REVIEW = """# Review — wave 2, cycle 1

Wave verdict: pass
Cycle: 1
Depth: delta
Tasks reviewed: 1

## T002 — refuse a missing scope: pass

- ✅ T002/AC1 — the focused Verify raised ValueError for a missing scope

## PR fix delta

{cumulative}
Eligibility: local
Risk inventory: complete

### Item R11: closed

- **Evidence**: web lib.py require_scope

### Item C21: closed

- **Evidence**: web lib.py require_scope raises ValueError

### SC1 — unaffected

- **Reason**: require_scope leaves value at 2 and the project Verify still asserts it
"""

BLOCKED_DELTA = """# Review — wave 2, cycle 1

Wave verdict: blocked
Cycle: 1
Depth: delta
Tasks reviewed: 1

## T002 — refuse a missing scope: fail

- ❌ `lib.require_scope(None)` raises `ValueError`. — found: the error message does not say the scope is required, lib.py:5
  fix: say that the scope is required
"""

RULING = {"verbatim": "Corrigir os dois itens nesta PR.",
          "items": [{"id": "R11", "class": "fix", "group": "G1", "title": "Exigir o escopo",
                     "fix_direction": "Recusar chamada sem escopo"},
                    {"id": "C21", "class": "fix", "group": "G1", "title": "Checar o escopo na chamada",
                     "fix_direction": "Levantar ValueError sem escopo"}]}


class FakeGitHub:
    """The GitHub REST and GraphQL calls the member PR close and the lane make."""

    def __init__(self, test):
        self.test = test
        self.pulls, self.reviews, self.comments, self.replies, self.patches = [], [], [], [], []
        self.threads = None

    def bound_tip(self):
        return self.test.remote_ref("refs/heads/" + BOUND)

    def _sync(self):
        for pull in self.pulls:
            if pull["state"] == "open":
                pull["head"]["sha"] = self.bound_tip()

    def run_command(self, *arguments):
        def reply(payload):
            return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")
        if arguments[:2] == ("gh", "auth"):
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if arguments[:2] != ("gh", "api"):
            raise AssertionError(f"unexpected command: {arguments}")
        rest = arguments[4:] if arguments[2] == "--hostname" else arguments[2:]
        return reply(self.api(*rest))

    def api(self, *arguments):
        raw = tuple(arguments)
        self._sync()
        path = arguments[0] if arguments else ""
        if "graphql" in raw and "mergeQueue" not in next(a for a in raw if str(a).startswith("query=")):
            if self.threads is None:
                self.threads = FakeThreads(self.bound_tip(), [comment for comment in self.comments
                                                             if not comment.get("in_reply_to_id")])
            self.threads.head = self.bound_tip()
            return self.threads.api(*raw)
        if "graphql" in raw:
            merged = [pull["merge_commit_sha"] for pull in self.pulls if pull["merged_at"]]
            nodes = [{"__typename": "MergedEvent", "actor": {"__typename": "User", "login": "owner"},
                      "commit": {"oid": merged[0]}}] if merged else []
            return {"data": {"repository": {"pullRequest": {
                "baseRefOid": self.test.remote_ref("refs/heads/main"),
                "mergeQueue": {"nodes": []}, "autoMerge": {"nodes": []}, "mergeAction": {"nodes": nodes}}}}}
        if path.endswith("/replies"):
            body = next(argument.removeprefix("body=") for argument in raw if str(argument).startswith("body="))
            self.replies.append((path, body))
            self.comments.append({"id": 900 + len(self.replies), "pull_request_review_id": None, "body": body,
                                  "in_reply_to_id": int(path.split("/")[-2])})
            return {"id": 900 + len(self.replies)}
        if path.endswith("/reviews"):
            return [list(self.reviews)]
        if path.endswith("/comments"):
            return [list(self.comments)]
        if re.fullmatch(r"repos/acme/web/pulls/7", path):
            if "PATCH" in raw:
                self.patches.append(raw)
                self.pulls[0]["body"] = next(argument.removeprefix("body=") for argument in raw
                                             if str(argument).startswith("body="))
            return self.pulls[0]
        if "POST" in raw:
            base = next(argument.removeprefix("base=") for argument in raw if str(argument).startswith("base="))
            self.pulls.append({"number": 7, "state": "open", "html_url": PULL, "merged_at": None,
                               "title": next(a.removeprefix("title=") for a in raw if str(a).startswith("title=")),
                               "merge_commit_sha": None,
                               "base": {"ref": base, "sha": self.test.remote_ref("refs/heads/" + base)},
                               "body": next(a.removeprefix("body=") for a in raw if str(a).startswith("body=")),
                               "head": {"ref": BOUND, "sha": self.bound_tip(), "repo": {"full_name": "acme/web"}}})
            return self.pulls[-1]
        base = next((argument.removeprefix("base=") for argument in raw if str(argument).startswith("base=")), None)
        return [[pull for pull in self.pulls if pull["base"]["ref"] == base]]


class PrFixLaneCycleTests(unittest.TestCase):
    write = cycle.MemberFullCycleTests.write
    state = cycle.MemberFullCycleTests.state
    commit = cycle.MemberFullCycleTests.commit
    gate = cycle.MemberFullCycleTests.gate
    transition = cycle.MemberFullCycleTests.transition

    def remote_ref(self, ref):
        return cycle.git(self.member_origin, "rev-parse", "--verify", "--quiet", ref).stdout.strip()

    def test_change_request_is_answered_by_one_delta_wave(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.run_lane(Path(temporary).resolve())

    def test_a_second_change_request_opens_round_two_on_the_published_head(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.run_lane(Path(temporary).resolve(), second_round=True)

    def test_a_non_local_delta_falls_back_to_the_ordinary_final_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.run_lane(Path(temporary).resolve(), eligibility="not-local")

    def test_a_repaired_round_reaches_views_through_its_cumulative_cycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.run_lane(Path(temporary).resolve(), repair=True)

    def run_lane(self, base, eligibility="local", second_round=False, repair=False):
        self.default_line = ""
        environment = mock.patch.dict(os.environ, {
            "GSD_PATH_WORKTREE_ROOT": str(base / "workspace"), "HOME": str(base / "home"),
            "GIT_AUTHOR_NAME": "Cycle", "GIT_AUTHOR_EMAIL": "cycle@example.invalid",
            "GIT_COMMITTER_NAME": "Cycle", "GIT_COMMITTER_EMAIL": "cycle@example.invalid"})
        environment.start()
        self.addCleanup(environment.stop)
        (base / "home/.gsd-path").mkdir(parents=True)
        (base / "home/.gsd-path/config.json").write_text('{"pr_fix_lane": "on"}\n', encoding="utf-8")
        git = cycle.git
        repo = self.repo = base / "acme"
        member = self.member = base / "web"
        origin = base / "acme-origin.git"
        self.member_origin = member_origin = base / "web-origin.git"
        for bare in (origin, member_origin):
            self.assertEqual(git(base, "init", "--bare", "-q", "-b", "main", str(bare)).returncode, 0)
        git(base, "init", "-q", "-b", "main", str(member))
        self.write("lib.py", "value = 1\n", member)
        git(member, "add", "-A")
        git(member, "commit", "-q", "-m", "web: existing library")
        git(member, "remote", "add", "origin", "https://github.com/acme/web.git")
        self.assertEqual(git(member, "push", "-q", str(member_origin), "main").returncode, 0)
        git(member, "fetch", "-q", str(member_origin), "main:refs/remotes/origin/main")
        git(member, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(member, "config", f"url.{member_origin}.insteadOf", "https://github.com/acme/web.git")

        git(base, "init", "-q", "-b", "main", str(repo))
        self.write("README.md", "acme program\n")
        self.commit("fixture: coordinator")
        hooks = subprocess.run(["node", str(cycle.SCRIPTS / "install.mjs"), "--claude", "--local", "--project",
                                str(repo), "--hooks", "--no-color"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(hooks.returncode, 0, hooks.stdout + hooks.stderr)
        self.commit("router: install guard hooks")
        git(repo, "remote", "add", "origin", str(origin))
        self.assertEqual(git(repo, "push", "-q", "-u", "origin", "main").returncode, 0)
        git(repo, "remote", "set-head", "origin", "--auto")
        git(repo, "switch", "-q", "-c", BRANCH)
        self.state("plan", "active")
        self.write(".project/intent/INTENT.md", cycle.INTENT)
        self.write(".project/research/RESEARCH.md", cycle.RESEARCH)
        self.write(".project/research/evidence-domain.md", cycle.EVIDENCE)
        self.write(".project/research/SYNTHESIS.md", cycle.SYNTHESIS)
        self.commit("define: intent, research, and synthesis")
        self.gate("members.py", "add", "--repo", str(repo), "--name", "web", "--checkout", str(member),
                  "--integration", "pull-request")
        installed = subprocess.run([cycle.sys.executable, str(cycle.SCRIPTS / "install.py"), "--member-of",
                                    str(repo), "--project", str(member)], capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        self.write(".project/plan/PLAN.md", cycle.PLAN)
        self.write(".project/tasks/T001-raise.md", cycle.TASK.format(today=TODAY))
        self.gate("check_handoffs.py", "plan", "--repo", str(repo))
        plan_head = self.commit("plan: draft")
        approval = self.gate("pipeline_state.py", "approve", "--repo", str(repo), "--kind", "plan",
                             "--expected-head", plan_head)

        # --- wave 1 lands the member task; ship opens the PR
        self.transition("build started", "plan", "done", set_phase="build", set_status="active")
        start = isolation.checkpoint(repo, approval["commit"], "build: start", "Why: build started", [".project"])
        first = self.land("T001", "lib.py", "value = 2\n", start["commit"])
        self.write(".project/review/wave-1.cycle1.md", cycle.WAVE_REVIEW)
        self.gate("check_handoffs.py", "wave", "--repo", str(repo), "--review", ".project/review/wave-1.cycle1.md")
        wave = isolation.checkpoint(repo, first["commit"], "build: wave 1 reviewed",
                                    f"Why: wave review passed\nWave: 1\nTasks: T001\nBase: {first['commit']}",
                                    [".project"])
        self.transition("build done; final review pending", "build", "active", set_phase="ship",
                        set_status="active")
        reviewed = isolation.checkpoint(repo, wave["commit"], "build: final review",
                                        "Why: build done; final review pending", [".project"])["commit"]
        self.assertTrue(lean_verification.verify_project(repo, reviewed)["passed"])
        self.write(".project/review/FINAL.md", cycle.FINAL.format(head=reviewed, member=first["landing"]))
        self.gate("check_handoffs.py", "final", "--repo", str(repo))
        prepared = self.archive()
        self.fake = FakeGitHub(self)
        publication_fixture(repo, prepared, "web", first["landing"], "https://github.com/acme/web.git")
        with _Patched(self.fake):
            waiting = integration.close_members(repo)
        self.assertEqual((waiting["status"], waiting["pull_request"]), ("awaiting-merge", PULL))
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), first["landing"])

        # --- the reviewer requests changes
        self.fake.reviews.append({"id": 11, "state": "CHANGES_REQUESTED", "user": {"login": "rev"},
                                  "submitted_at": "2026-11-16T03:00:00Z", "body": "Exija o escopo.",
                                  "commit_id": first["landing"], "html_url": PULL + "#pullrequestreview-11"})
        self.fake.comments.append({"id": 21, "pull_request_review_id": 11, "body": "Falta checar o escopo.",
                                   "path": "lib.py", "line": 1, "in_reply_to_id": None,
                                   "html_url": PULL + "#discussion_r21"})
        snapshot = pr_fix_lane.snapshot(repo, api=self.fake.api)
        self.assertEqual([item["id"] for item in snapshot["items"]], ["R11", "C21"])
        ruling = base / "ruling.json"
        ruling.write_text(json.dumps(RULING), encoding="utf-8")
        opened = pr_fix_lane.start(repo, ruling)
        self.assertEqual(opened["status"], "opened")
        self.assertEqual(pipeline_state.load_state(repo)[0].status, "blocked")
        self.gate("check_handoffs.py", "patch", "--repo", str(repo))
        # A hand-off an earlier runtime wrote is rewritten from the recorded ruling.
        self.assertEqual(pr_fix_lane.regenerate(repo)["written"], [])
        stale = repo / ".project/review/pr-review-1.md"
        stale.write_text(stale.read_text(encoding="utf-8").replace("Falta checar", "Falta  checar"), encoding="utf-8")
        self.assertEqual(pr_fix_lane.regenerate(repo)["written"], [".project/review/pr-review-1.md"])
        self.gate("check_handoffs.py", "patch", "--repo", str(repo))

        # --- patch mode: the orchestrator appends the delta wave; no planner, no panel
        self.transition("patch plan reopened", "ship", "blocked", set_phase="plan", set_status="active")
        plan = (repo / ".project/plan/PLAN.md").read_text(encoding="utf-8")
        plan = plan.replace("## Intent coverage", "## Wave 2 — PR fix lane\n\nGoal: answer the PR change request\n"
                                                  "Review depth: delta\n\n## Intent coverage", 1)
        self.write(".project/plan/PLAN.md", plan)
        self.write(".project/tasks/T002-scope.md", LANE_TASK.format(today=TODAY, lane_round=1))
        unbound = subprocess.run([cycle.sys.executable, str(cycle.SCRIPTS / "check_handoffs.py"), "plan",
                                  "--repo", str(repo)], capture_output=True, text=True)
        self.assertIn("run `pr_fix_lane.py bind --repo <root>`", unbound.stderr + unbound.stdout)
        bound = self.gate("pr_fix_lane.py", "bind", "--repo", str(repo))
        self.assertEqual((bound["tasks"], bound["membership"]), (["T002"], ".project/plan/pr-fix-round-1.json"))
        self.gate("check_handoffs.py", "plan", "--repo", str(repo))
        self.gate("pipeline_state.py", "approve", "--repo", str(repo), "--kind", "plan", "--patch")
        pr_fix_lane.require_approved_membership(repo)
        self.transition("build started", "plan", "done", set_phase="build", set_status="active")
        restart = isolation.checkpoint(repo, git(repo, "rev-parse", "HEAD").stdout.strip(), "build: start milestone",
                                       "Why: patch wave for the PR change request", [".project"])
        own = "python3 -c 'import lib'"
        self.assertEqual(build_state.regression_verifies(repo, "T002", own), [])
        second = self.land("T002", "lib.py",
                           "value = 2\n\n\ndef require_scope(scope):\n    if scope is None:\n"
                           "        raise ValueError('scope required')\n    return scope\n", restart["commit"])
        cycle_name = "wave-2.cycle1.md"
        if not repair and not second_round and eligibility == "local":
            # A delta whose views would fail the final gate is refused inside the wave cycle.
            thin = (DELTA_REVIEW.replace("{cumulative}", self.cumulative(repo, 1, ["T002"]))
                    .replace("### SC1 — unaffected\n\n- **Reason**: require_scope leaves value at 2 and the "
                             "project Verify still asserts it\n",
                             "### SC1 — affected\n\n- **Verdict**: met\n- **Check**: `python3 -c 'import lib'`\n"
                             "- **Observed**: none\n- **Reference**: lib.py:1\n- **Finding**: none\n"
                             "- **Fix direction**: none\n"))
            self.assertIn("### SC1 — affected", thin)
            self.write(".project/review/wave-2.cycle1.md", thin)
            refused = subprocess.run([cycle.sys.executable, str(cycle.SCRIPTS / "check_handoffs.py"), "wave",
                                      "--repo", str(repo), "--review", ".project/review/wave-2.cycle1.md"],
                                     capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("would fail the final gate: FINAL.md SC1 lacks an Observed result",
                          refused.stderr + refused.stdout)
        if repair:
            second, cycle_name = self.repair_round(repo, second), "wave-2.cycle2.md"
        self.write(f".project/review/{cycle_name}",
                   DELTA_REVIEW.replace("{cumulative}", self.cumulative(
                       repo, 1, ["T002", "T003"] if repair else ["T002"]))
                   .replace("Eligibility: local", f"Eligibility: {eligibility}")
                   .replace("cycle 1\n\nWave verdict", f"cycle {2 if repair else 1}\n\nWave verdict")
                   .replace("Cycle: 1", f"Cycle: {2 if repair else 1}"))
        self.gate("check_handoffs.py", "wave", "--repo", str(repo), "--review", f".project/review/{cycle_name}")
        wave2 = isolation.checkpoint(repo, second["commit"], "build: wave 2 reviewed",
                                     f"Why: delta review passed\nWave: 2\nTasks: T002\nBase: {second['commit']}",
                                     [".project"])
        self.transition("build done; final review pending", "build", "active", set_phase="ship",
                        set_status="active")
        reviewed2 = isolation.checkpoint(repo, wave2["commit"], "build: final review",
                                         "Why: build done; final review pending", [".project"])["commit"]

        # --- ship: project Verify, then generated views instead of reviewers
        final = lean_verification.prepare_final(repo, reviewed2)
        if eligibility != "local":
            self.assertEqual((final["next"], final["final"]["lane"]), ("review-final", "fallback"), final)
            self.assertIn("non-local", final["final"]["reason"])
            self.assertFalse((repo / ".project/review/FINAL.md").exists())
            self.patch_after_fallback(repo)
            return
        self.assertEqual(final["next"], "final-gate", final)
        self.assertEqual(final["final"]["lane"], "views")
        self.gate("check_handoffs.py", "final", "--repo", str(repo))
        text = (repo / ".project/review/FINAL.md").read_text(encoding="utf-8")
        self.assertIn(f"Member reviewed HEAD: web {second['landing']}", text)
        self.assertIn("inherited from", text)
        self.assertIn("## PR fix rounds", text)
        prepared2 = self.archive()
        self.assertTrue((repo / prepared2 / "review/pr-fix-1/provenance.json").is_file())
        self.assertTrue((repo / prepared2 / "review/pr-fix-1/baseline/FINAL.md").is_file())
        publication_fixture(repo, prepared2, "web", second["landing"], "https://github.com/acme/web.git")
        with _Patched(self.fake):
            waiting = integration.close_members(repo)
        self.assertEqual((waiting["status"], waiting["pull_request"]), ("awaiting-merge", PULL))
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), second["landing"])
        self.assertEqual(len([pull for pull in self.fake.pulls]), 1)

        answered = pr_fix_lane.threads(repo, api=self.fake.api)
        self.assertEqual((answered["replied"], answered["resolved"]), (["C21"], ["C21"]))
        self.assertEqual(pr_fix_lane.post_threads(
            {key: value for key, value in pr_fix_lane.round_plan(repo).items() if key != "_record"},
            self.fake.api)["summary"], "present")
        review = [item for item in self.fake.threads.reviews if item["state"] == "COMMENTED"]
        self.assertEqual(len(review), 1)
        self.assertIn("- [R11]", review[0]["body"])
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "still active"):
            pr_fix_lane.ship_check(repo, api=self.fake.api)
        if second_round:
            self.fake.reviews.append({"id": 13, "state": "CHANGES_REQUESTED", "user": {"login": "rev"},
                                      "submitted_at": "2026-11-16T05:00:00Z", "body": "Documente o escopo.",
                                      "commit_id": second["landing"], "html_url": PULL + "#pullrequestreview-13"})
            # The owner forces a runtime upgrade while the PR waits: an installer-only commit
            # after the reviewed head must not block the next round.
            skill = next((repo / ".claude/skills").glob("*/SKILL.md"))
            skill.write_text(skill.read_text(encoding="utf-8") + "\n<!-- upgraded -->\n", encoding="utf-8")
            forced = git(repo, "commit", "-q", "--no-verify", "-m", "chore(gsd-path): upgrade runtime and skills",
                         "--", str(skill.relative_to(repo)))
            self.assertEqual(forced.returncode, 0, forced.stderr)
            # The reviewer also reopens the round-1 conversation inside its old thread.
            self.fake.comments.append({"id": 23, "pull_request_review_id": 13, "body": "Ainda falta documentar.",
                                       "path": "lib.py", "line": 1, "in_reply_to_id": 21,
                                       "html_url": PULL + "#discussion_r23"})
            again = pr_fix_lane.snapshot(repo, api=self.fake.api)
            self.assertEqual((again["round"], [item["id"] for item in again["items"]]), (2, ["R13", "C23"]))
            capsule = Path(again["directory"]) / "capsule/review"
            self.assertTrue((capsule / "pr-fix-1/provenance.json").is_file())
            self.assertIn("### Round 1", (capsule / "FINAL.md").read_text(encoding="utf-8"))
            ruling2 = base / "ruling-2.json"
            ruling2.write_text(json.dumps({"verbatim": "Documentar.", "items": [
                {"id": "R13", "class": "fix", "group": "G1", "title": "Documentar o escopo",
                 "fix_direction": "Explicar require_scope no docstring"},
                {"id": "C23", "class": "fix", "group": "G1", "title": "Documentar na conversa antiga",
                 "fix_direction": "O mesmo docstring responde a conversa reaberta"}]}), encoding="utf-8")
            self.assertEqual(pr_fix_lane.start(repo, ruling2)["status"], "opened")
            self.gate("check_handoffs.py", "patch", "--repo", str(repo))
            self.assertTrue((repo / ".project/review/pr-review-2.md").is_file())
            self.assertFalse((repo / prepared2).exists())
            self.assertEqual(pr_fix_lane.lane_round(repo)["membership"], pr_fix_lane.MEMBERSHIP_SCHEMA)
            self.second_round_cycle(repo, second["landing"])
            return
        self.fake.reviews.append({"id": 12, "state": "APPROVED", "user": {"login": "rev"},
                                  "submitted_at": "2026-11-16T05:00:00Z", "body": "", "commit_id": second["landing"]})
        self.assertEqual(pr_fix_lane.ship_check(repo, api=self.fake.api)["status"], "clear")

        # --- the owner merges; ship closes the member and commits the archive with the lane record
        merge = self.github_merge(base)
        with _Patched(self.fake):
            closed = integration.close_members(repo)
        self.assertEqual(closed["status"], "integrated")
        self.assertEqual(closed["members"][0]["merge"], merge)
        self.assertEqual(closed["members"][0]["reviewed_head"], second["landing"])
        self.gate("pipeline_state.py", "record-shipment", "--repo", str(repo), "--archive", prepared2,
                  "--event", "archive preflight passed; shipment recorded")
        git(repo, "add", "-A")
        ship = git(repo, "commit", "-q", "-m", f"ship: M001 — {SLUG}", "-m", closed["body"].rstrip("\n"))
        self.assertEqual(ship.returncode, 0, ship.stderr)
        with _Patched(self.fake):
            shipped = self.gate("archive_milestone.py", "validate", "--repo", str(repo))
        self.assertEqual(shipped["members"][0]["merge"], merge)
        pr_fix_lane.validate_views(repo, repo / prepared2 / "review")

    def second_round_cycle(self, repo, published):
        """Round 2 plans, builds and reviews its own delta wave; round 1's delta wave is history."""
        git = cycle.git
        self.transition("patch plan reopened", "ship", "blocked", set_phase="plan", set_status="active")
        plan = (repo / ".project/plan/PLAN.md").read_text(encoding="utf-8")
        plan = plan.replace("## Intent coverage", "## Wave 3 — PR fix lane round 2\n\nGoal: answer the second "
                                                  "change request\nReview depth: delta\n\n## Intent coverage", 1)
        self.write(".project/plan/PLAN.md", plan)
        task = LANE_TASK.format(today=TODAY, lane_round=2).replace("id: T002", "id: T003").replace("wave: 2", "wave: 3") \
            .replace("# T002", "# T003").replace("Closes P001 and P002 from the PR change request",
                                                 "Closes P001 and P002 from the second PR change request")
        self.write(".project/tasks/T003-doc.md", task)
        # Round 1's member T002 stays history: its round is closed and its task is done.
        self.assertEqual(self.gate("pr_fix_lane.py", "bind", "--repo", str(repo))["tasks"], ["T003"])
        self.gate("check_handoffs.py", "plan", "--repo", str(repo))
        self.gate("pipeline_state.py", "approve", "--repo", str(repo), "--kind", "plan", "--patch")
        pr_fix_lane.require_approved_membership(repo)
        self.transition("build started", "plan", "done", set_phase="build", set_status="active")
        restart = isolation.checkpoint(repo, git(repo, "rev-parse", "HEAD").stdout.strip(), "build: start milestone",
                                       "Why: patch wave for the second PR change request", [".project"])
        third = self.land("T003", "lib.py",
                          "value = 2\n\n\ndef require_scope(scope):\n    \"\"\"Refuse a call without a scope.\"\"\"\n"
                          "    if scope is None:\n        raise ValueError('scope required')\n    return scope\n",
                          restart["commit"])
        review = (DELTA_REVIEW.replace("{cumulative}", self.cumulative(repo, 2, ["T003"]))
                  .replace("wave 2", "wave 3").replace("T002", "T003")
                  .replace("### Item R11: closed\n\n- **Evidence**: web lib.py require_scope\n\n", "")
                  .replace("### Item C21: closed", "### Item C23: closed\n\n- **Evidence**: web lib.py docstring"
                                                    "\n\n### Item R13: closed"))
        self.write(".project/review/wave-3.cycle1.md", review)
        self.gate("check_handoffs.py", "wave", "--repo", str(repo), "--review", ".project/review/wave-3.cycle1.md")
        wave3 = isolation.checkpoint(repo, third["commit"], "build: wave 3 reviewed",
                                     f"Why: delta review passed\nWave: 3\nTasks: T003\nBase: {third['commit']}",
                                     [".project"])
        self.transition("build done; final review pending", "build", "active", set_phase="ship",
                        set_status="active")
        reviewed3 = isolation.checkpoint(repo, wave3["commit"], "build: final review",
                                         "Why: build done; final review pending", [".project"])["commit"]
        final = lean_verification.prepare_final(repo, reviewed3)
        self.assertEqual((final["next"], final["final"]["lane"]), ("final-gate", "views"), final)
        self.gate("check_handoffs.py", "final", "--repo", str(repo))
        text = (repo / ".project/review/FINAL.md").read_text(encoding="utf-8")
        self.assertIn(f"Member reviewed HEAD: web {third['landing']}", text)
        self.assertNotEqual(third["landing"], published)

    def patch_after_fallback(self, repo):
        """A blocked ordinary final review after a lane fallback takes the ordinary patch route."""
        record = pr_fix_lane.lane_round(repo)
        self.assertEqual(record["stage"], pr_fix_lane.FELL_BACK)
        self.assertIn("non-local", record["fallback"])
        # The landed delta wave still validates against its round.
        self.state("build", "active")
        self.gate("check_handoffs.py", "wave", "--repo", str(repo), "--review", ".project/review/wave-2.cycle1.md")
        self.state("plan", "active")
        plan = (repo / ".project/plan/PLAN.md").read_text(encoding="utf-8")
        standard = ("## Wave 3 — final gap repair\n\nGoal: close the blocked final gap\nReview depth: full\n\n"
                    "## Intent coverage")
        self.write(".project/plan/PLAN.md", plan.replace("## Intent coverage", standard, 1))
        task = LANE_TASK.format(today=TODAY, lane_round=1).replace("id: T002", "id: T003").replace("wave: 2", "wave: 3") \
            .replace("# T002", "# T003").replace("Closes P001 and P002 from the PR change request",
                                                 "Closes the final gap 3 finding").replace("lane_round: 1\n", "")
        self.write(".project/tasks/T003-gap.md", task)
        self.gate("check_handoffs.py", "plan", "--repo", str(repo))
        # New work never joins a delta wave once the round fell back.
        self.write(".project/tasks/T003-gap.md", task.replace("wave: 3", "wave: 2").replace("lib.py", "scope.py")
                   .replace("import lib", "import scope").replace("lib.require_scope", "scope.require_scope"))
        refused = subprocess.run([cycle.sys.executable, str(cycle.SCRIPTS / "check_handoffs.py"), "plan",
                                  "--repo", str(repo)], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("delta waves are history: move it to a standard wave", refused.stderr + refused.stdout)
        # The round can still answer the PR once the ordinary route republishes.
        self.assertEqual(pr_fix_lane.fall_back(repo, "again")["status"], pr_fix_lane.FELL_BACK)

    def repair_round(self, repo, landed):
        """A blocked delta cycle, fix-tasks, and the landed repair: the repair joins the round."""
        self.write(".project/review/wave-2.cycle1.md", BLOCKED_DELTA)
        self.gate("check_handoffs.py", "wave", "--repo", str(repo), "--review", ".project/review/wave-2.cycle1.md")
        blocked = isolation.checkpoint(repo, landed["commit"], "build: record wave 2 cycle 1 review",
                                       "Why: persist the blocked wave review verdict\nWave: 2", [".project"])
        created = self.gate("dispatch_driver.py", "fix-tasks", "--repo", str(repo), "--wave", "2", "--cycle", "1")
        self.assertEqual([item["task"] for item in created["created"]], ["T003"], created)
        repair_path = repo / created["created"][0]["path"]
        self.assertIn("lane_round: 1\n", repair_path.read_text(encoding="utf-8"))
        self.assertEqual(created["lane"]["repairs"], ["T003"])
        repairs = json.loads(pr_fix_lane.membership_path(repo, 1).read_text(encoding="utf-8"))["repairs"]
        self.assertEqual((repairs[0]["id"], repairs[0]["source"]), ("T003", ".project/review/wave-2.cycle1.md"))
        pr_fix_lane.require_approved_membership(repo)  # the approved part is unchanged
        self.gate("check_handoffs.py", "plan", "--repo", str(repo))
        head = cycle.git(repo, "rev-parse", "HEAD").stdout.strip()
        if any(path.startswith(".project/") for path in isolation.uncommitted_paths(repo)):
            head = isolation.checkpoint(repo, head, "build: record fix tasks",
                                        "Why: fix tasks for wave 2 cycle 1", [".project"])["commit"]
        repaired = self.land("T003", "lib.py",
                             "value = 2\n\n\ndef require_scope(scope):\n    if scope is None:\n"
                             "        raise ValueError('scope is required')\n    return scope\n", head)
        # A cumulative cycle that forgets the repair is refused at the wave gate.
        self.write(".project/review/wave-2.cycle2.md",
                   DELTA_REVIEW.replace("{cumulative}", self.cumulative(repo, 1, ["T002"]))
                   .replace("cycle 1\n\nWave verdict", "cycle 2\n\nWave verdict").replace("Cycle: 1", "Cycle: 2"))
        refused = subprocess.run([cycle.sys.executable, str(cycle.SCRIPTS / "check_handoffs.py"), "wave", "--repo",
                                  str(repo), "--review", ".project/review/wave-2.cycle2.md"],
                                 capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("must list `Round tasks: T002, T003`", refused.stderr + refused.stdout)
        return repaired

    def cumulative(self, repo, round_number, tasks):
        """The cumulative fields the delta brief hands the reviewer for the round's first lane wave."""
        membership = pr_fix_lane.membership_path(repo, round_number)
        tips = check_handoffs._member_tips(repo)
        return "\n".join([f"Round tasks: {', '.join(tasks)}", f"Membership: {pr_fix_lane._digest(membership)}",
                          f"Product HEAD: {cycle.git(repo, 'rev-parse', 'HEAD').stdout.strip()}",
                          *[f"Member product HEAD: {name} {sha}" for name, sha in sorted(tips.items())]]) + "\n"

    def land(self, task_id, path, content, coordinator_base):
        repo = self.repo
        task = next(str(item.relative_to(repo)) for item in (repo / ".project/tasks").glob(f"{task_id}-*.md"))
        isolated = isolation.isolate_member_task(repo, "web", task_id)
        isolation.activate_member_task(repo, "web", task_id, "cycle-coder", task, coordinator_base)
        self.write(path, content, Path(isolated["worktree"]))
        title = re.search(r"(?m)^title:\s*(.+)$", (repo / task).read_text(encoding="utf-8")).group(1).strip()
        landed = isolation.land_member(repo, "web", task_id, title, task, coordinator_base,
                                       isolated["member_base"])
        isolation.retire_member_task(repo, "web", task_id)
        return landed

    def github_merge(self, base):
        """Merge the PR on the member remote the way the GitHub merge button does."""
        clone = base / "github"
        cycle.git(base, "clone", "-q", str(self.member_origin), str(clone))
        cycle.git(clone, "merge", "-q", "--no-ff", "-m", "Merge pull request #7", "origin/" + BOUND)
        cycle.git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")
        merge = cycle.git(clone, "rev-parse", "HEAD").stdout.strip()
        self.fake.pulls[0].update(state="closed", merged_at="2026-11-16T06:00:00Z", merge_commit_sha=merge)
        return merge

    def archive(self):
        prepared = self.gate("archive_milestone.py", "prepare", "--repo", str(self.repo), "--slug", SLUG)
        self.gate("archive_milestone.py", "render-manifest", "--repo", str(self.repo))
        self.gate("archive_milestone.py", "preflight", "--repo", str(self.repo))
        return prepared["archive"]


class _Patched:
    def __init__(self, fake):
        self.patches = [mock.patch.object(integration, "github_repository", return_value="acme/web"),
                        mock.patch.object(integration, "run_command", side_effect=fake.run_command)]

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self.patches):
            patch.stop()
        return False


if __name__ == "__main__":
    unittest.main()
