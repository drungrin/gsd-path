import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import guard_hook
import test_guard_hook
from tests._platform import requires_symlink

MEMBERS = ROOT / "scripts" / "members.py"
STATE = (
    "---\npipeline: gsd-path/v2\nproject: {project}\nmilestone: demo\nphase: plan\n"
    "status: active\nbranch: feature/milestone-001\narchive: null\n---\n"
)


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
                   cwd=repo, encoding="utf-8", errors="replace", capture_output=True, check=True)


def make_repo(path: Path, project: str, remote: str = "") -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / ".project").mkdir()
    (path / ".project" / "STATE.md").write_bytes(STATE.format(project=project).encode("utf-8"))
    (path / "app.py").write_bytes("print('v1')\n".encode("utf-8"))
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    if remote:
        git(path, "remote", "add", "origin", remote)
        git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return path


def join(coordinator: Path, member: Path, name: str) -> None:
    subprocess.run([sys.executable, str(MEMBERS), "add", "--repo", str(coordinator),
                    "--name", name, "--checkout", str(member)],
                   encoding="utf-8", errors="replace", capture_output=True, check=True)


class MemberWriteTests(unittest.TestCase):
    status = test_guard_hook.GuardHookTests.status

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        self.coordinator = make_repo(root / "acme", "acme")
        self.member = make_repo(root / "web", "web", "https://github.com/acme/web.git")
        (self.member / ".project" / "STATE.md").unlink()
        git(self.member, "commit", "-q", "-am", "leave path")
        join(self.coordinator, self.member, "web")
        self.root = root

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def guard(self, target: Path, phase: str = "plan"):
        with (
            mock.patch.object(guard_hook, "repository_root", return_value=self.coordinator),
            mock.patch.object(guard_hook, "project_status",
                              return_value=self.status(self.coordinator, phase=phase)),
        ):
            return test_guard_hook.run_guard({"tool_name": "Edit", "tool_input": {"file_path": str(target)}})

    def assert_denied(self, target: Path, reason: str, phase: str = "plan") -> None:
        status, output, error = self.guard(target, phase)
        self.assertEqual(status, 2, error)
        self.assertIn(reason, json.loads(output)["reason"])

    def assert_allowed(self, target: Path, phase: str = "plan") -> None:
        status, output, error = self.guard(target, phase)
        self.assertEqual((status, output), (0, ""), error)

    def test_member_write_follows_the_coordinator_phase(self) -> None:
        self.assert_denied(self.member / "app.py", "GSD Path is plan")
        self.assert_denied(self.member / "src" / "new.py", "GSD Path is plan")
        self.assert_allowed(self.member / "app.py", phase="build")

    def test_member_linked_worktree_follows_the_coordinator_phase(self) -> None:
        linked = self.root / "web-linked"
        git(self.member, "worktree", "add", "-q", "-b", "side", str(linked))
        self.assert_denied(linked / "app.py", "GSD Path is plan")

    def test_repos_outside_this_coordinator_stay_external(self) -> None:
        plain = make_repo(self.root / "plain", "plain")
        self.assert_allowed(plain / "app.py")
        other = make_repo(self.root / "other", "other")
        foreign = make_repo(self.root / "sdk", "sdk", "https://github.com/acme/sdk.git")
        (foreign / ".project" / "STATE.md").unlink()
        git(foreign, "commit", "-q", "-am", "leave path")
        join(other, foreign, "sdk")
        self.assert_allowed(foreign / "app.py")

    def test_foreign_member_stays_external_when_listed_checkout_is_gone(self) -> None:
        self.member.rename(self.root / "web-gone")
        other = make_repo(self.root / "other", "other")
        foreign = make_repo(self.root / "sdk", "sdk", "https://github.com/acme/sdk.git")
        (foreign / ".project" / "STATE.md").unlink()
        git(foreign, "commit", "-q", "-am", "leave path")
        join(other, foreign, "sdk")
        self.assert_allowed(foreign / "app.py")

    def test_stale_member_of_this_coordinator_fails_closed(self) -> None:
        (self.coordinator / ".project" / "MEMBERS.md").unlink()
        self.assert_denied(self.member / "app.py", "members.py repair", phase="build")

    def test_moved_coordinator_refuses_stale_member_marker(self) -> None:
        moved = self.root / "acme-moved"
        self.coordinator.rename(moved)
        self.coordinator = moved
        for phase in ("plan", "build"):
            self.assert_denied(self.member / "app.py", "members.py repair", phase=phase)

    @requires_symlink
    def test_coordinator_symlink_refuses_stale_member_marker(self) -> None:
        (self.coordinator / ".project" / "MEMBERS.md").unlink()
        link = self.coordinator / "member-app.py"
        link.symlink_to(self.member / "app.py")
        self.assert_denied(link, "members.py repair", phase="build")

    def test_member_git_inspection_failure_refuses_write(self) -> None:
        real_run = subprocess.run

        def failing_inspection(command, *args, **kwargs):
            if "--git-common-dir" in command and command[0] == "git":
                return subprocess.CompletedProcess(command, 128, "", "fatal: broken git metadata")
            return real_run(command, *args, **kwargs)

        with mock.patch.object(guard_hook.subprocess, "run", side_effect=failing_inspection):
            self.assert_denied(self.member / "app.py", "members.py repair")


if __name__ == "__main__":
    unittest.main()
