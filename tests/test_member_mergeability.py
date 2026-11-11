"""Member mergeability: a member bound branch that conflicts with its live
default branch blocks final review before any landing proof, project Verify,
or reviewer runs. The probe fetches into a disposable object store only.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import isolation, members, workflow_run

ROOT = Path(__file__).resolve().parents[1]
ISOLATION = ROOT / "scripts" / "isolation.py"
URL = "https://github.com/acme/web.git"
BOUND = "feature/acme-milestone-001"
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
STATE = (
    "---\npipeline: gsd-path/v2\nproject: acme\nmilestone: demo\nphase: plan\nstatus: done\n"
    "branch: feature/milestone-001\narchive: null\n---\n\n# Project State\n\n## Log\n\n"
    "- 2026-11-11 — ship — build done; final review pending\n"
)


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *arguments], encoding="utf-8", errors="replace",
                          capture_output=True, check=check).stdout.strip()


class MemberMergeabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, IDENTITY)
        environment.start()
        self.addCleanup(environment.stop)
        self.remote = self.root / "web.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.member = self.root / "web"
        self.member.mkdir()
        git(self.member, "init", "-q", "-b", "main")
        (self.member / "app.py").write_text("v1\n", encoding="utf-8")
        git(self.member, "add", "-A")
        git(self.member, "commit", "-q", "-m", "init")
        git(self.member, "config", f"url.{self.remote}.insteadOf", URL)
        git(self.member, "remote", "add", "origin", URL)
        git(self.member, "push", "-q", "origin", "main")
        git(self.member, "fetch", "-q", "origin")
        git(self.member, "remote", "set-head", "origin", "--auto")
        base = git(self.member, "rev-parse", "main")
        self.coordinator = self.root / "acme"
        git(self.root, "init", "-q", "-b", "feature/milestone-001", str(self.coordinator))
        (self.coordinator / ".project").mkdir()
        (self.coordinator / ".project" / "STATE.md").write_text(STATE, encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "plan: approve")
        members.add_member(self.coordinator, "web", self.member, "default")
        git(self.member, "branch", BOUND, base)
        lock = self.coordinator / members.LOCK_PATH
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"schema": members.LOCK_SCHEMA,
                                    "members": [{"name": "web", "branch": BOUND, "base": base}]}, indent=2),
                        encoding="utf-8")
        state = self.coordinator / ".project" / "STATE.md"
        state.write_text(state.read_text(encoding="utf-8").replace("phase: plan\nstatus: done",
                                                                     "phase: ship\nstatus: active"), encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "build: lock members")

    def commit_member(self, name: str, text: str) -> str:
        worktree = self.root / "bound"
        if not worktree.exists():
            git(self.member, "worktree", "add", "-q", str(worktree), BOUND)
        (worktree / name).write_text(text, encoding="utf-8")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "-m", f"change {name}")
        return git(worktree, "rev-parse", "HEAD")

    def advance_main(self, name: str, text: str) -> str:
        team = self.root / f"team-{len(list(self.root.glob('team-*')))}"
        git(self.root, "clone", "-q", str(self.remote), str(team))
        (team / name).write_text(text, encoding="utf-8")
        git(team, "add", "-A")
        git(team, "commit", "-q", "-m", f"team {name}")
        git(team, "push", "-q", "origin", "main")
        return git(team, "rev-parse", "HEAD")

    def snapshot(self) -> dict:
        return {
            "refs": git(self.member, "for-each-ref", "--format=%(refname) %(objectname)"),
            "objects": git(self.member, "count-objects", "-v"),
            "status": git(self.member, "status", "--porcelain", "--untracked-files=all"),
            "fetch_head": (Path(git(self.member, "rev-parse", "--absolute-git-dir")) / "FETCH_HEAD").exists(),
            "coordinator": git(self.coordinator, "status", "--porcelain", "--untracked-files=all"),
        }

    def probe(self) -> dict:
        result = subprocess.run([sys.executable, "-B", str(ISOLATION), "member-mergeability", "--repo",
                                 str(self.coordinator)], encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_states_from_current_to_conflict_without_touching_the_member(self) -> None:
        tip = self.commit_member("app.py", "v2\n")
        self.assertEqual(self.probe()["members"][0]["status"], "current")
        onto = self.advance_main("team.txt", "team\n")
        before = self.snapshot()
        clean = self.probe()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(clean["verdict"], "clean")
        self.assertEqual({key: clean["members"][0][key] for key in ("member", "status", "tip", "default")},
                         {"member": "web", "status": "behind", "tip": tip, "default": onto})
        self.advance_main("app.py", "team edit\n")
        before = self.snapshot()
        conflict = self.probe()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(conflict["verdict"], "conflict")
        self.assertEqual((conflict["members"][0]["status"], conflict["members"][0]["conflicts"]),
                         ("conflict", ["app.py"]))

    def test_unreachable_origin_and_missing_lock(self) -> None:
        self.commit_member("app.py", "v2\n")
        git(self.member, "config", "--unset", f"url.{self.remote}.insteadOf")
        git(self.member, "remote", "set-url", "origin", str(self.root / "absent.git"))
        result = isolation.member_mergeability(self.coordinator)
        self.assertEqual((result["verdict"], result["members"][0]["status"]), ("unavailable", "unavailable"))
        (self.coordinator / members.LOCK_PATH).unlink()
        self.assertEqual(isolation.member_mergeability(self.coordinator), {"verdict": "none", "members": []})

    def test_prepare_final_blocks_a_conflict_before_landing_proof_or_verify(self) -> None:
        self.commit_member("app.py", "v2\n")
        self.advance_main("app.py", "team edit\n")
        head = git(self.coordinator, "rev-parse", "HEAD")
        with mock.patch.object(workflow_run.subprocess, "run", wraps=subprocess.run) as spy:
            result = workflow_run.run_workflow(self.coordinator, "prepare-final", ".project", head)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("web vs origin/main in app.py", result["reason"])
        self.assertIn("ship/blocked", result["next"])
        scripts = [Path(str(call.args[0][2])).name for call in spy.call_args_list
                   if call.args and isinstance(call.args[0], list) and len(call.args[0]) > 2]
        self.assertIn("isolation.py", scripts)
        self.assertNotIn("build_state.py", scripts)
        self.assertNotIn("lean_verification.py", scripts)

    def test_prepare_final_continues_past_a_clean_or_unchecked_member(self) -> None:
        self.commit_member("app.py", "v2\n")
        self.advance_main("team.txt", "team\n")
        head = git(self.coordinator, "rev-parse", "HEAD")
        result = workflow_run.run_workflow(self.coordinator, "prepare-final", ".project", head)
        # Later steps fail in this synthetic milestone; only reaching them matters here.
        probe = next(step for step in result["steps"] if step["script"] == "isolation.py")
        self.assertEqual(probe["result"]["verdict"], "clean")
        self.assertIn("build_state.py", [step["script"] for step in result["steps"]])
        real = subprocess.run

        def refuse(command, *arguments, **options):
            # An older selected runtime without the command fails like any other probe failure.
            if isinstance(command, list) and "member-mergeability" in command:
                return subprocess.CompletedProcess(command, 2, "", "invalid choice: 'member-mergeability'")
            return real(command, *arguments, **options)

        with mock.patch.object(workflow_run.subprocess, "run", side_effect=refuse):
            unchecked = workflow_run.run_workflow(self.coordinator, "prepare-final", ".project", head)
        self.assertTrue(next(step for step in unchecked["steps"] if step["script"] == "isolation.py")["advisory"])
        self.assertIn("build_state.py", [step["script"] for step in unchecked["steps"]])


if __name__ == "__main__":
    unittest.main()
