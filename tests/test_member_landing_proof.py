import unittest
from pathlib import Path

import tests.test_member_landing as landing
from scripts import isolation

TASK_FILE = landing.TASK_FILE
git = landing.git


class MemberLandingProofTests(unittest.TestCase):
    setUp = landing.MemberLandingTests.setUp
    tearDown = landing.MemberLandingTests.tearDown
    land = landing.MemberLandingTests.land
    edit = landing.MemberLandingTests.edit

    def verify(self, path=None):
        head = git(self.coordinator, "rev-parse", "HEAD")
        return isolation.verify_landed_task_files(
            self.coordinator, [path or self.coordinator / TASK_FILE], ".project/tasks", head)

    def assert_blocked(self, reason: str) -> None:
        with self.assertRaisesRegex(isolation.IsolationError, reason):
            self.verify()

    def landed(self):
        self.edit()
        return self.land()

    def test_landed_member_task_is_proven_from_record_and_member_landing(self) -> None:
        result = self.landed()
        verdicts = self.verify()["tasks"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "recovered")
        self.assertEqual(verdicts[0]["commit"], result["commit"])
        self.assertEqual(verdicts[0]["landing"], result["landing"])

    def test_reopened_landed_member_task_blocks_recovery(self) -> None:
        self.landed()
        path = self.coordinator / TASK_FILE
        path.write_bytes(path.read_text(encoding="utf-8").replace("status: done", "status: in-progress").encode("utf-8"))
        git(self.coordinator, "add", TASK_FILE)
        git(self.coordinator, "commit", "-q", "-m", "reopen task")
        report = isolation.recover(self.coordinator, self.coordinator / ".project" / "tasks")
        self.assertEqual(report["tasks"][0]["verdict"], "block")

    def test_changed_task_files_after_landing_blocks_verification(self) -> None:
        self.landed()
        path = self.coordinator / TASK_FILE
        path.write_bytes(path.read_text(encoding="utf-8").replace("  - app.py", "  - other.py").encode("utf-8"))
        git(self.coordinator, "add", TASK_FILE)
        git(self.coordinator, "commit", "-q", "-m", "change task files")
        self.assert_blocked("task artifact contract differs")

    def test_archived_member_task_is_proven_after_active_build_files_move(self) -> None:
        result = self.landed()
        archive = self.coordinator / ".project" / "archive" / "001-demo"
        archive.mkdir(parents=True)
        (self.coordinator / ".project" / "tasks").rename(archive / "tasks")
        (self.coordinator / ".project" / "build").rename(archive / "build")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "archive task and build")
        verdict = self.verify(archive / "tasks" / Path(TASK_FILE).name)["tasks"][0]
        self.assertEqual(verdict["verdict"], "recovered")
        self.assertEqual(verdict["commit"], result["commit"])

    def test_record_must_stamp_done_before_later_task_changes(self) -> None:
        self.landed()
        record_body = git(self.coordinator, "log", "-1", "--format=%b")
        git(self.coordinator, "reset", "-q", "--soft", "HEAD^")
        path = self.coordinator / TASK_FILE
        done = path.read_text(encoding="utf-8")
        path.write_bytes(done.replace("status: done", "status: in-progress").encode("utf-8"))
        git(self.coordinator, "add", TASK_FILE)
        git(self.coordinator, "commit", "-q", "-m", "T001: Change app", "-m", record_body)
        path.write_bytes(done.encode("utf-8"))
        git(self.coordinator, "add", TASK_FILE)
        git(self.coordinator, "commit", "-q", "-m", "finish task later")
        self.assert_blocked("recorded task status")

    def test_done_member_task_without_a_record_is_not_landed(self) -> None:
        self.landed()
        git(self.coordinator, "reset", "-q", "--soft", "HEAD^")
        git(self.coordinator, "commit", "-q", "-m", "chore: stamp by hand")
        self.assert_blocked("record")

    def test_record_whose_member_landing_disappeared_is_not_landed(self) -> None:
        self.landed()
        git(self.bound, "reset", "-q", "--hard", self.member_base)
        self.assert_blocked("member bound branch")

    def test_record_must_match_the_task_member_base(self) -> None:
        self.landed()
        path = self.coordinator / TASK_FILE
        text = path.read_text(encoding="utf-8")
        path.write_bytes(text.replace(f"member_base: {self.member_base}", "member_base: " + "0" * 40).encode("utf-8"))
        self.assert_blocked("member_base")

    def test_member_landing_with_undeclared_paths_is_not_landed(self) -> None:
        (self.bound / "app.py").write_bytes("v2\n".encode("utf-8"))
        (self.bound / "extra.py").write_bytes("stray\n".encode("utf-8"))
        git(self.bound, "add", "-A")
        body = isolation.member_commit_body(TASK_FILE, ["app.py", "extra.py"], self.member_base, self.base)
        git(self.bound, "commit", "-q", "--no-verify", "-m", "T001: Change app", "-m", body.strip())
        forged = git(self.bound, "rev-parse", "HEAD")
        path = self.coordinator / TASK_FILE
        text = path.read_text(encoding="utf-8")
        text = text.replace("status: pending", "status: done").replace(
            "base: null", f"base: {self.base}\nmember_base: {self.member_base}")
        path.write_bytes(text.encode("utf-8"))
        git(self.coordinator, "add", TASK_FILE)
        git(self.coordinator, "commit", "-q", "-m", "T001: Change app", "-m",
            f"Task: {TASK_FILE}\nBase: {self.base}\nMember: web {forged} {self.member_base}")
        self.assert_blocked("undeclared paths: extra.py")

    def rewrite_record(self, member_base: str, extra: bool = False) -> None:
        landing_sha = git(self.bound, "rev-parse", "HEAD")
        git(self.coordinator, "reset", "-q", "--soft", "HEAD^")
        path = self.coordinator / TASK_FILE
        text = path.read_text(encoding="utf-8")
        path.write_bytes(text.replace(f"member_base: {self.member_base}", f"member_base: {member_base}").encode("utf-8"))
        if extra:
            (self.coordinator / ".project" / "notes.md").write_bytes("x\n".encode("utf-8"))
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "T001: Change app", "-m",
            f"Task: {TASK_FILE}\nBase: {self.base}\nMember: web {landing_sha} {member_base}")

    def test_two_records_for_one_task_are_not_landed(self) -> None:
        self.landed()
        record = git(self.coordinator, "log", "-1", "--format=%b")
        path = self.coordinator / TASK_FILE
        path.write_bytes((path.read_text(encoding="utf-8") + "- again\n").encode("utf-8"))
        git(self.coordinator, "add", TASK_FILE)
        git(self.coordinator, "commit", "-q", "-m", "T001: Change app", "-m", record)
        self.assert_blocked("found 2")

    def test_record_touching_other_files_is_not_landed(self) -> None:
        self.landed()
        self.rewrite_record(self.member_base, extra=True)
        self.assert_blocked("more than the task file")

    def test_landing_must_build_on_the_recorded_member_base(self) -> None:
        self.landed()
        git(self.member, "checkout", "-q", "-b", "elsewhere", "main")
        (self.member / "side.py").write_bytes("x\n".encode("utf-8"))
        git(self.member, "add", "-A")
        git(self.member, "commit", "-q", "--no-verify", "-m", "unrelated")
        unrelated = git(self.member, "rev-parse", "HEAD")
        git(self.member, "checkout", "-q", "main")
        self.rewrite_record(unrelated)
        self.assert_blocked("does not descend from member_base")

    def recovered_verdict(self):
        report = isolation.recover(self.coordinator, self.coordinator / ".project" / "tasks")
        return next(task for task in report["tasks"] if task["task"] == TASK_FILE)

    def test_unfinished_member_task_without_isolation_has_nothing_to_recover(self) -> None:
        isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertEqual(self.recovered_verdict()["verdict"], "none")

    def test_pending_member_journal_asks_for_a_landing_retry(self) -> None:
        from unittest import mock
        self.edit()
        with mock.patch.object(isolation, "_write_member_record", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.land()
        verdict = self.recovered_verdict()
        self.assertEqual((verdict["verdict"], verdict.get("landing_retry")), ("resume", True))

    def test_unfinished_member_task_with_a_retained_sidecar_resumes(self) -> None:
        report = isolation.recover(self.coordinator, self.coordinator / ".project" / "tasks")
        verdict = next(task for task in report["tasks"] if task["task"] == TASK_FILE)
        self.assertEqual(verdict["verdict"], "resume")
        self.assertFalse(verdict.get("landing_retry"))

    def advance_bound(self) -> None:
        (self.bound / "other.py").write_bytes("earlier landing\n".encode("utf-8"))
        git(self.bound, "add", "-A")
        git(self.bound, "commit", "-q", "--no-verify", "-m", "earlier landing")

    def test_landing_on_a_moved_bound_branch_is_proven(self) -> None:
        self.advance_bound()
        result = self.landed()
        self.assertNotEqual(git(self.bound, "rev-parse", f"{result['landing']}^"), self.member_base)
        self.assertEqual(self.verify()["tasks"][0]["verdict"], "recovered")

    def test_retire_accepts_a_landed_member_task_branch(self) -> None:
        self.advance_bound()
        self.landed()
        isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertFalse(self.sidecar.exists())
        self.assertEqual(git(self.member, "branch", "--list", "chore/task-acme-t001"), "")


if __name__ == "__main__":
    unittest.main()
