import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import check_handoffs, members, pipeline_diagnose, pipeline_state
import tests.test_handoffs as handoffs

LOCK = {"schema": "gsd-path/member-lock/v1",
        "members": [{"name": "web", "branch": "feature/demo-milestone-001", "base": "a" * 40}]}


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments], cwd=repo,
                          text=True, capture_output=True, check=True).stdout.strip()


class MemberRecoveryStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        handoffs.HandoffValidationTests().write_plan_handoff(self.root)
        task = next((self.root / ".project/tasks").glob("T001-*.md"))
        task.write_text(re.sub(r"(?m)^(id: T001)$", r"\1\nrepo: web", task.read_text(encoding="utf-8"), count=1),
                        encoding="utf-8")
        self.task = task
        git(self.root, "init", "-q", "-b", "feature/milestone-001")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "plan")
        self.common = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))

    def close(self, status: str) -> None:
        members.write_member_close(self.common, "001-demo", [
            {"name": "web", "mode": "direct", "reviewed_head": "b" * 40, "status": status,
             "merge": "c" * 40 if status == "integrated" else None, "tag": "milestone/demo-001-demo"
             if status == "integrated" else None}])

    def lock(self) -> None:
        path = self.root / ".project/build/members.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(LOCK), encoding="utf-8")

    def test_plan_refuses_new_work_for_a_member_integrated_in_this_milestone(self) -> None:
        check_handoffs.validate_plan(self.root)
        self.close("pending")
        check_handoffs.validate_plan(self.root)
        self.close("integrated")
        with self.assertRaisesRegex(check_handoffs.HandoffError, "T001 .*web.*already integrated"):
            check_handoffs.validate_plan(self.root)

    def test_landed_task_for_an_integrated_member_stays_valid(self) -> None:
        self.close("integrated")
        self.task.write_text(self.task.read_text(encoding="utf-8").replace("status: pending", "status: done"),
                             encoding="utf-8")
        check_handoffs.validate_plan(self.root)

    def test_status_reports_locked_members_and_their_close(self) -> None:
        self.assertNotIn("members", pipeline_state.status_state(self.root))
        self.lock()
        self.assertEqual(pipeline_state.status_state(self.root)["members"],
                         [{"name": "web", "branch": "feature/demo-milestone-001", "close": None}])
        self.close("integrated")
        self.assertEqual(pipeline_state.status_state(self.root)["members"][0]["close"], "integrated")

    def test_lookahead_status_omits_active_build_members(self) -> None:
        self.lock()
        handoffs.HandoffValidationTests().write_state(self.root, "inspect", "active", ".project/next")
        self.assertIn("members", pipeline_state.status_state(self.root))
        self.assertNotIn("members", pipeline_state.status_state(self.root, ".project/next"))

    def test_diagnose_names_a_pending_member_close_and_its_retry(self) -> None:
        self.lock()
        self.close("pending")
        finding = next(item for item in pipeline_diagnose.diagnose(self.root)["findings"]
                       if item["id"] == "member-close")
        self.assertIn("web", finding["evidence"])
        self.assertIn("close-members", finding["retry"])

    def test_diagnose_names_a_pending_member_landing(self) -> None:
        journal = self.common / "gsd-path/member-landings/T001.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{}", encoding="utf-8")
        finding = next(item for item in pipeline_diagnose.diagnose(self.root)["findings"]
                       if item["id"] == "member-landing")
        self.assertIn("T001", finding["evidence"])


if __name__ == "__main__":
    unittest.main()
