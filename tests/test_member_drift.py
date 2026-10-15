import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_task_briefs, pipeline_git, state_checkpoint, state_promote
from tests.test_pipeline_state import PLAN_WAVE, roadmap_text, run_git, state_text, task_text

MEMBERS = Path(__file__).resolve().parents[1] / "scripts" / "members.py"


class MemberDriftTests(unittest.TestCase):
    """Lookahead member tasks are checked against the member merge the shipped milestone made."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.member = base / "web"
        run_git(base, "init", "-b", "main", str(self.member))
        for key, value in (("user.name", "t"), ("user.email", "t@t")):
            run_git(self.member, "config", key, value)
        (self.member / "app.py").write_text("approved = True\n", encoding="utf-8")
        (self.member / "other.py").write_text("other = True\n", encoding="utf-8")
        run_git(self.member, "add", "-A")
        run_git(self.member, "commit", "-m", "member base")
        run_git(self.member, "remote", "add", "origin", "https://github.com/acme/web.git")
        run_git(self.member, "update-ref", "refs/remotes/origin/main", "HEAD")
        run_git(self.member, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.repo = base / "repo"
        run_git(base, "init", "-b", "main", str(self.repo))
        for key, value in (("user.name", "t"), ("user.email", "t@t")):
            run_git(self.repo, "config", key, value)
        (self.repo / "README.md").write_text("coordinator\n", encoding="utf-8")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-m", "base")
        run_git(self.repo, "switch", "-c", "feature/milestone-001")
        project = self.repo / ".project"
        (project / "next" / "tasks").mkdir(parents=True)
        (project / "next" / "plan").mkdir()
        (project / "next" / "review").mkdir()
        (project / "STATE.md").write_text(state_text(milestone="first", phase="plan", status="active",
                                                     branch="feature/milestone-001"), encoding="utf-8")
        joined = subprocess.run([sys.executable, str(MEMBERS), "add", "--repo", str(self.repo), "--name", "web",
                                 "--checkout", str(self.member)], text=True, capture_output=True)
        self.assertEqual(joined.returncode, 0, joined.stderr)
        (project / "STATE.md").write_text(state_text(milestone="first", phase="build", status="active",
                                                     branch="feature/milestone-001"), encoding="utf-8")
        (project / "ROADMAP.md").write_text(roadmap_text(), encoding="utf-8")
        (project / "next" / "STATE.md").write_text(state_text(status="active"), encoding="utf-8")
        (project / "next" / "tasks" / "T001-change-app.md").write_text(
            task_text().replace("T002", "T001").replace("files:", "repo: web\nfiles:", 1), encoding="utf-8")
        (project / "next" / "plan" / "PLAN.md").write_text(
            PLAN_WAVE.format(title="second").replace("T002", "T001"), encoding="utf-8")
        (project / "next" / "review" / "PLAN-PANEL.md").write_text("# Plan review panel\n\nStatus: ready\n",
                                                                    encoding="utf-8")
        self.approval_base = run_git(self.member, "rev-parse", "HEAD").stdout.strip()

    def approve(self, *, done: bool = False) -> None:
        if done:
            task = self.repo / ".project" / "next" / "tasks" / "T001-change-app.md"
            task.write_text(
                task.read_text(encoding="utf-8")
                .replace("status: pending", "status: done")
                .replace("agent: null", "agent: coder")
                .replace("base: null", f"base: {self.approval_base}"),
                encoding="utf-8",
            )
        state_checkpoint.checkpoint_approval(self.repo, "plan", run_git(self.repo, "rev-parse", "HEAD").stdout.strip(),
                                             ".project/next")

    def ship(self, changed: str, *, rename_from: str = "") -> str:
        """The shipped milestone merged a member change to `changed` into the member's main."""
        run_git(self.member, "switch", "-c", "feature/demo-milestone-001")
        if rename_from:
            run_git(self.member, "mv", rename_from, changed)
        else:
            (self.member / changed).write_text("changed = True\n", encoding="utf-8")
        run_git(self.member, "commit", "-am", "member landing")
        reviewed = run_git(self.member, "rev-parse", "HEAD").stdout.strip()
        run_git(self.member, "switch", "main")
        run_git(self.member, "merge", "--no-ff", "-m", "integrate", "feature/demo-milestone-001")
        merge = run_git(self.member, "rev-parse", "HEAD").stdout.strip()
        project = self.repo / ".project"
        (project / "STATE.md").write_text(state_text(milestone="first", phase="shipped", status="done",
                                                     branch="feature/milestone-001", archive=".project/archive/001-first/"),
                                          encoding="utf-8")
        run_git(self.repo, "add", "-A")
        rows = [{"name": "web", "reviewed_head": reviewed, "merge": merge, "tag": "milestone/demo-001-first"}]
        run_git(self.repo, "commit", "-m", "ship: M001 — first", "-m",
                pipeline_git.ship_commit_body(".project/archive/001-first", "d" * 40, rows))
        run_git(self.repo, "switch", "main")
        run_git(self.repo, "merge", "--no-ff", "feature/milestone-001", "-m", "integrate: M001 — merge feature/milestone-001 into main")
        integrate = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()
        run_git(self.repo, "tag", "-a", "-m", "milestone 001-first", "milestone/001-first", integrate)
        tag = run_git(self.repo, "rev-parse", "refs/tags/milestone/001-first").stdout.strip()
        run_git(self.repo, "update-ref", "refs/remotes/origin/tags/milestone/001-first", tag)
        run_git(self.repo, "update-ref", "refs/remotes/origin/main", integrate)
        run_git(self.repo, "switch", "-c", "feature/milestone-002")
        return integrate

    def test_approval_records_the_member_base_the_brief_was_checked_at(self) -> None:
        self.approve()
        common = Path(run_git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        recorded = json.loads((common / "gsd-path" / "lookahead-member-bases" / "second.json").read_text())
        self.assertEqual(recorded["bases"], {"web": self.approval_base})

    def test_approval_records_lint_base_when_member_ref_moves(self) -> None:
        original = check_task_briefs._member_bases
        ref_moved = False

        def drifting_resolver(root: Path):
            locate = original(root)

            def tracked(name: str):
                nonlocal ref_moved
                located = locate(name)
                if not ref_moved:
                    (self.member / "app.py").write_text("changed = True\n", encoding="utf-8")
                    run_git(self.member, "commit", "-am", "move member main")
                    run_git(self.member, "update-ref", "refs/remotes/origin/main", "HEAD")
                    ref_moved = True
                return located

            return tracked

        with patch.object(check_task_briefs, "_member_bases", side_effect=drifting_resolver):
            self.approve()
        self.assertTrue(ref_moved)
        moved_base = run_git(self.member, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        self.assertNotEqual(moved_base, self.approval_base)
        common = Path(run_git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        recorded = json.loads((common / "gsd-path" / "lookahead-member-bases" / "second.json").read_text())
        self.assertEqual(recorded["bases"], {"web": self.approval_base})

    def test_member_change_to_a_declared_path_flags_the_task(self) -> None:
        self.approve()
        integrate = self.ship("app.py")
        result = state_promote.promote_next(self.repo, "second", "feature/milestone-002", integrate)
        self.assertEqual(result["drift"]["class"], "changed")
        self.assertEqual(result["drift"]["task_ids"], ["T001"])
        self.assertIn("web:app.py", result["drift"]["changed_paths"])
        again = state_promote.promote_next(self.repo, "second", "feature/milestone-002", integrate)
        self.assertEqual(again["drift"], result["drift"])

    def test_member_change_elsewhere_keeps_the_plan_clean(self) -> None:
        self.approve()
        integrate = self.ship("other.py")
        result = state_promote.promote_next(self.repo, "second", "feature/milestone-002", integrate)
        self.assertEqual(result["drift"]["class"], "clean")

    def test_missing_member_bases_stay_unverifiable(self) -> None:
        self.approve()
        common = Path(run_git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        (common / "gsd-path" / "lookahead-member-bases" / "second.json").unlink()
        integrate = self.ship("other.py")
        result = state_promote.promote_next(self.repo, "second", "feature/milestone-002", integrate)
        self.assertEqual(result["drift"]["class"], "unverifiable")

    def test_done_member_task_needs_no_recorded_base_at_promotion(self) -> None:
        self.approve(done=True)
        common = Path(run_git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        self.assertFalse((common / "gsd-path" / "lookahead-member-bases" / "second.json").exists())
        integrate = self.ship("app.py")
        result = state_promote.promote_next(self.repo, "second", "feature/milestone-002", integrate)
        self.assertEqual(result["drift"]["class"], "clean")
        self.assertEqual(result["drift"]["task_ids"], [])
        self.assertEqual(result["drift"]["changed_paths"], [])

    def test_member_rename_of_declared_path_flags_the_task(self) -> None:
        self.approve()
        integrate = self.ship("renamed.py", rename_from="app.py")
        result = state_promote.promote_next(self.repo, "second", "feature/milestone-002", integrate)
        self.assertEqual(result["drift"]["class"], "changed")
        self.assertEqual(result["drift"]["task_ids"], ["T001"])
        self.assertIn("web:app.py", result["drift"]["changed_paths"])


if __name__ == "__main__":
    unittest.main()
