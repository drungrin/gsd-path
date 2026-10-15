import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import isolation, pipeline_state

ROOT = Path(__file__).resolve().parents[1]
MEMBERS = ROOT / "scripts" / "members.py"
STATE = (
    "---\npipeline: gsd-path/v2\nproject: acme\nmilestone: demo\nphase: plan\nstatus: done\n"
    "branch: feature/milestone-001\narchive: null\n---\n\n# Project State\n\n## Log\n\n- 2026-09-27 — plan — plan approved\n"
)
TASK = "---\nid: T001\ntitle: T\nwave: 1\ndeps: []\nstatus: pending\nrepo: web\nfiles:\n  - app.py\n---\n# T001\n"
BOUND = "feature/acme-milestone-001"


def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
                          cwd=repo, encoding="utf-8", errors="replace", capture_output=True, check=check)


class MemberIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        workspace = self.root / "workspace"
        environment = mock.patch.dict(os.environ, {"GSD_PATH_WORKTREE_ROOT": str(workspace)})
        environment.start()
        self.addCleanup(environment.stop)
        self.coordinator = self.root / "acme"
        (self.coordinator / ".project" / "tasks").mkdir(parents=True)
        git(self.root, "init", "-q", "-b", "feature/milestone-001", str(self.coordinator))
        (self.coordinator / ".project" / "STATE.md").write_bytes(STATE.encode("utf-8"))
        (self.coordinator / ".project" / "tasks" / "T001-t.md").write_bytes(TASK.encode("utf-8"))
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "init")
        self.member = self.root / "web"
        self.member.mkdir()
        git(self.member, "init", "-q", "-b", "main")
        (self.member / "app.py").write_bytes("v1\n".encode("utf-8"))
        git(self.member, "add", "-A")
        git(self.member, "commit", "-q", "-m", "init")
        git(self.member, "remote", "add", "origin", "https://github.com/acme/web.git")
        git(self.member, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.member, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        subprocess.run([sys.executable, str(MEMBERS), "add", "--repo", str(self.coordinator),
                        "--name", "web", "--checkout", str(self.member)],
                       encoding="utf-8", errors="replace", capture_output=True, check=True)
        pipeline_state.transition_state(
            self.coordinator,
            {"phase": "plan", "status": "done", "branch": "feature/milestone-001", "archive": None},
            {"phase": "build", "status": "active"}, "build started")
        self.workspace = workspace

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def team_checkout_untouched(self) -> None:
        self.assertEqual(git(self.member, "branch", "--show-current").stdout.strip(), "main")
        self.assertEqual(git(self.member, "status", "--porcelain", "--untracked-files=all").stdout, "")

    def test_bound_checkout_lives_in_path_workspace_on_the_bound_branch(self) -> None:
        first = isolation.member_bound_checkout(self.coordinator, "web")
        checkout = Path(first["checkout"])
        self.assertTrue(checkout.is_relative_to(self.workspace))
        self.assertEqual(git(checkout, "branch", "--show-current").stdout.strip(), BOUND)
        self.assertEqual(first["branch"], BOUND)
        self.assertEqual(isolation.member_bound_checkout(self.coordinator, "web"), first)
        self.team_checkout_untouched()

    def test_bound_checkout_ignores_a_legacy_bound_directory(self) -> None:
        legacy = self.root / "web.gsd-path" / "bound" / "acme-M001"
        legacy.mkdir(parents=True)
        result = isolation.member_bound_checkout(self.coordinator, "web")
        self.assertTrue(Path(result["checkout"]).is_relative_to(self.workspace))
        self.assertNotEqual(Path(result["checkout"]), legacy)
        self.assertEqual(git(Path(result["checkout"]), "branch", "--show-current").stdout.strip(), BOUND)

    def test_bound_checkout_refuses_unlocked_or_stale_members(self) -> None:
        with self.assertRaisesRegex(isolation.IsolationError, "not locked for this build because no task named it at build start"):
            isolation.member_bound_checkout(self.coordinator, "sdk")
        (self.coordinator / ".project" / "MEMBERS.md").unlink()
        with self.assertRaisesRegex(isolation.IsolationError, "members.py repair"):
            isolation.member_bound_checkout(self.coordinator, "web")

    def test_bound_checkout_refuses_a_member_when_the_build_lock_is_missing(self) -> None:
        (self.coordinator / ".project" / "build" / "members.json").unlink()
        with self.assertRaisesRegex(isolation.IsolationError, "not locked for this build because no task named it at build start"):
            isolation.member_bound_checkout(self.coordinator, "web")

    def test_bound_checkout_refuses_a_member_whose_marker_is_gone(self) -> None:
        common = git(self.member, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        (Path(common) / "gsd-path" / "member.json").unlink()
        with self.assertRaisesRegex(isolation.IsolationError, "members.py repair"):
            isolation.member_bound_checkout(self.coordinator, "web")

    def test_bound_checkout_explains_how_to_repair_malformed_members(self) -> None:
        path = self.coordinator / ".project" / "MEMBERS.md"
        path.write_bytes((path.read_text(encoding="utf-8") + "unexpected line\n").encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, "MEMBERS.md.*members.py repair --repo"):
            isolation.member_bound_checkout(self.coordinator, "web")

    def test_bound_checkout_explains_how_to_repair_an_unreadable_marker(self) -> None:
        common = git(self.member, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        (Path(common) / "gsd-path" / "member.json").write_bytes("invalid json\n".encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, "member marker is unreadable.*members.py repair --repo") as caught:
            isolation.member_bound_checkout(self.coordinator, "web")
        self.assertIn(str(self.coordinator), str(caught.exception))

    def test_bound_checkout_refuses_a_team_checkout_of_the_bound_branch(self) -> None:
        git(self.member, "worktree", "add", "-q", str(self.root / "team-copy"), BOUND)
        with self.assertRaises(isolation.IsolationError):
            isolation.member_bound_checkout(self.coordinator, "web")

    def test_bound_checkout_refuses_a_missing_registered_checkout(self) -> None:
        bound = Path(isolation.member_bound_checkout(self.coordinator, "web")["checkout"])
        shutil.rmtree(bound)
        with self.assertRaisesRegex(isolation.IsolationError, "bound checkout.*missing or invalid"):
            isolation.member_bound_checkout(self.coordinator, "web")
        self.assertFalse(bound.exists())

    def test_bound_checkout_refuses_an_invalid_registered_checkout(self) -> None:
        bound = Path(isolation.member_bound_checkout(self.coordinator, "web")["checkout"])
        # Rewrite in place: Git for Windows hides .git, and Windows refuses to
        # truncate-create a hidden file (write_bytes raises PermissionError).
        with (bound / ".git").open("r+b") as gitfile:
            gitfile.write("invalid gitfile\n".encode("utf-8"))
            gitfile.truncate()
        with self.assertRaisesRegex(isolation.IsolationError, "bound checkout.*missing or invalid"):
            isolation.member_bound_checkout(self.coordinator, "web")

    def test_member_task_isolates_in_a_member_sidecar_at_the_bound_tip(self) -> None:
        bound = Path(isolation.member_bound_checkout(self.coordinator, "web")["checkout"])
        (bound / "landed.py").write_bytes("earlier landing\n".encode("utf-8"))
        git(bound, "add", "-A")
        git(bound, "commit", "-q", "--no-verify", "-m", "earlier landing")
        tip = git(bound, "rev-parse", "HEAD").stdout.strip()
        result = isolation.isolate_member_task(self.coordinator, "web", "T001")
        sidecar = Path(result["worktree"])
        self.assertTrue(sidecar.is_relative_to(self.workspace))
        self.assertEqual(result["task_branch"], "chore/task-acme-t001")
        self.assertEqual(result["member_base"], tip)
        self.assertEqual(git(sidecar, "branch", "--show-current").stdout.strip(), "chore/task-acme-t001")
        self.assertEqual(git(sidecar, "rev-parse", "HEAD").stdout.strip(), tip)
        with self.assertRaisesRegex(isolation.IsolationError, "already exists"):
            isolation.isolate_member_task(self.coordinator, "web", "T001")
        self.team_checkout_untouched()

    def test_member_task_isolation_requires_a_clean_bound_checkout(self) -> None:
        bound = Path(isolation.member_bound_checkout(self.coordinator, "web")["checkout"])
        (bound / "app.py").write_bytes("dirty\n".encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, "clean"):
            isolation.isolate_member_task(self.coordinator, "web", "T001")

    def test_retire_removes_only_an_unused_member_sidecar(self) -> None:
        result = isolation.isolate_member_task(self.coordinator, "web", "T001")
        sidecar = Path(result["worktree"])
        isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertFalse(sidecar.exists())
        self.assertEqual(git(self.member, "branch", "--list", "chore/task-acme-t001").stdout, "")
        result = isolation.isolate_member_task(self.coordinator, "web", "T001")
        sidecar = Path(result["worktree"])
        (sidecar / "app.py").write_bytes("work\n".encode("utf-8"))
        git(sidecar, "commit", "-q", "--no-verify", "-am", "work")
        with self.assertRaisesRegex(isolation.IsolationError, "unlanded"):
            isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertTrue(sidecar.exists())

    def test_retire_reports_failed_task_branch_deletion(self) -> None:
        result = isolation.isolate_member_task(self.coordinator, "web", "T001")
        original_run_git = isolation.run_git

        def fail_delete(repo: Path, *arguments: str) -> subprocess.CompletedProcess:
            if arguments[:2] == ("update-ref", "-d"):
                return subprocess.CompletedProcess(arguments, 1, "", "cannot delete task ref")
            return original_run_git(repo, *arguments)

        with mock.patch.object(isolation, "run_git", side_effect=fail_delete):
            with self.assertRaisesRegex(isolation.IsolationError, "cannot delete task ref"):
                isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertFalse(Path(result["worktree"]).exists())
        self.assertNotEqual(git(self.member, "branch", "--list", "chore/task-acme-t001").stdout, "")

    def test_retire_refuses_a_sidecar_on_another_branch(self) -> None:
        result = isolation.isolate_member_task(self.coordinator, "web", "T001")
        sidecar = Path(result["worktree"])
        git(sidecar, "switch", "-q", "-c", "other")
        with self.assertRaisesRegex(isolation.IsolationError, "another branch"):
            isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertTrue(sidecar.exists())
        self.assertEqual(git(sidecar, "branch", "--show-current").stdout.strip(), "other")
        self.assertNotEqual(git(self.member, "branch", "--list", "chore/task-acme-t001").stdout, "")


if __name__ == "__main__":
    unittest.main()
