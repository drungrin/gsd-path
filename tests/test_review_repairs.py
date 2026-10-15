"""Repair evidence must follow real task landings, not a shared branch tip."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import _common, build_state, isolation
from tests.test_build_state import run_git
from tests.test_review_findings import PLAN, TASK


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/review_findings.py"


class RepairEvidenceTests(unittest.TestCase):
    original_task = {}  # task() overrides for T001; set before calling setUp again

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        run_git(self.repo, "init", "-q")
        run_git(self.repo, "config", "user.name", "Test")
        run_git(self.repo, "config", "user.email", "test@example.com")
        run_git(self.repo, "switch", "-q", "-c", "feature/milestone-001")
        self.project = self.repo / ".project"
        for directory in ("tasks", "plan", "review"):
            (self.project / directory).mkdir(parents=True, exist_ok=True)
        (self.project / "plan/PLAN.md").write_bytes(
            (PLAN.format(cycles=3, skeptics="off", depth="full")
            + "\n## Wave 2 — repair\nGoal: repair the observed failure\nReview depth: full\n").encode("utf-8")
        )
        (self.repo / "count.py").write_bytes("print('initial')\n".encode("utf-8"))
        self.original_file = self.task("T001", "Original", 1, "", "", **self.original_task)
        self.commit("fixture")
        self.original = self.land("T001", "Original", self.original_file, "bad")
        self.review = self.project / "review/wave-1.cycle1.md"
        self.review.write_bytes("""# Review — wave 1, cycle 1
Wave verdict: blocked
Cycle: 1
Depth: full
Tasks reviewed: 1
## T001 — Original: fail
- ❌ Prints good — found: prints bad, count.py:1
  fix: print good
""".encode("utf-8"))
        self.findings = """## Review findings

### t001_ac1
Criterion: Prints good
Observation 1 — canonical:
Prints good — found: prints bad, count.py:1 fix: print good

"""
        self.repair_file = self.task("T002", "Repair", 2, "T001", self.findings)

    def task(self, task_id, title, wave, deps, findings, files="  - count.py", owned="None"):
        path = self.project / "tasks" / f"{task_id}-task.md"
        text = TASK.format(id=task_id, title=title, files=files,
                           interface="None", owned=owned, ac1="Prints good",
                           ac2="Exits successfully")
        text = text.replace("wave: 1", f"wave: {wave}").replace("deps: []", f"deps: [{deps}]")
        text = text.replace("status: done", "status: pending")
        text = text.replace("python3 -m unittest tests.test_demo", "python3 -B count.py")
        path.write_bytes(text.replace("## Log", findings + "## Log").encode("utf-8"))
        return path.relative_to(self.repo).as_posix()

    def commit(self, message):
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-qm", message)
        return run_git(self.repo, "rev-parse", "HEAD")

    def land(self, task_id, title, task_file, output, target="count.py"):
        base = run_git(self.repo, "rev-parse", "HEAD")
        isolation.activate_task(self.repo, base, task_id, "coder", task_file, None)
        for name in target.split(","):
            (self.repo / name).write_bytes(f"print({output!r})\n".encode("utf-8"))
        result = subprocess.run([sys.executable, "-B", "count.py"], cwd=self.repo,
                                capture_output=True, encoding="utf-8", errors="replace", check=True)
        allowed = isolation.task_frontmatter((self.repo / task_file).read_text(encoding="utf-8"))[0]["files"]
        commit = isolation.land(self.repo, self.repo, base, task_id, title, task_file, allowed)["commit"]
        build_state.verify_record(str(self.repo), "python3 -B count.py", commit, "pass",
                                  {"stdout": result.stdout, "stderr": result.stderr,
                                   "exit_code": result.returncode})
        return commit

    def finish_repair(self):
        self.commit("plan repair")
        return self.land("T002", "Repair", self.repair_file, "good")

    def repair(self, task_id, deps, output, findings=None, files="  - count.py", target="count.py"):
        """Plan and land one more repair task; return its landing commit."""
        task_file = self.task(task_id, "Repair", 2, deps, self.findings if findings is None else findings, files=files)
        self.commit(f"plan {task_id}")
        return self.land(task_id, "Repair", task_file, output, target)

    def cli(self, task="T002", cycle=1):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "repair-evidence", "--repo", str(self.repo),
             "--wave", "1", "--cycle", str(cycle), "--task", task],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        return result, json.loads(result.stdout) if result.stdout else {}

    def test_proven_repair_resolves_to_its_isolated_product_without_reverification(self):
        repair = self.finish_repair()
        before = run_git(self.repo, "status", "--porcelain")
        ledger = (self.repo / _common.VERIFY_LEDGER_PATH).read_bytes()
        result, evidence = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr or evidence)
        self.assertEqual(evidence["repair"]["commit"], repair)
        self.assertEqual(evidence["originals"][0]["commit"], self.original)
        self.assertEqual(evidence["groups"][0]["locator"], "t001_ac1")
        self.assertEqual(evidence["verification"]["entry"]["execution"]["stdout"], "good\n")
        # One repair right after its review: the receipt has no key of a repair chain or of a member.
        self.assertEqual(sorted(evidence), ["command", "groups", "head", "originals", "repair", "source", "status",
                                            "verdict", "verification"])
        self.assertEqual(evidence["repair"], {"task": "T002", "task_file": self.repair_file, "commit": repair,
                                              "base": run_git(self.repo, "rev-parse", f"{repair}^"), "files": ["count.py"]})
        self.assertEqual(evidence["originals"], [{"task": "T001", "commit": self.original, "files": ["count.py"],
                                                  "base": run_git(self.repo, "rev-parse", f"{self.original}^")}])
        self.assertEqual(run_git(self.repo, "status", "--porcelain"), before)
        self.assertEqual((self.repo / _common.VERIFY_LEDGER_PATH).read_bytes(), ledger)

    def test_rejects_unlanded_repair(self):
        self.commit("pending repair")
        result, evidence = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("done", evidence["error"])

    def test_rejects_unrelated_missing_or_changed_evidence(self):
        expected_errors = {
            "dependency": "dependencies/files",
            "finding": "eligible source finding batch",
            "intervening": "unlinked changes",
            "ledger": "passing Verify",
            "report": "exact criterion and observations",
            "task": "task",
            "later-product": "product changed after",
        }
        for fault, error in expected_errors.items():
            with self.subTest(fault=fault):
                # Each case needs its own real history; setUp's fixture is replaced.
                self.setUp()
                path = self.repo / self.repair_file
                if fault == "dependency":
                    path.write_bytes(path.read_text(encoding="utf-8").replace("deps: [T001]", "deps: []").encode("utf-8"))
                elif fault == "finding":
                    path.write_bytes(path.read_text(encoding="utf-8").replace("### t001_ac1", "### t001_ac9").encode("utf-8"))
                elif fault == "intervening":
                    (self.repo / "count.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                self.finish_repair()
                if fault == "ledger":
                    (self.repo / _common.VERIFY_LEDGER_PATH).unlink()
                elif fault == "report":
                    self.review.write_bytes(self.review.read_text(encoding="utf-8").replace("prints bad", "prints worse").encode("utf-8"))
                elif fault == "task":
                    path.write_bytes(path.read_text(encoding="utf-8").replace("Prints good", "Anything passes").encode("utf-8"))
                elif fault == "later-product":
                    (self.repo / "count.py").write_bytes("print('later unrelated change')\n".encode("utf-8"))
                result, evidence = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(evidence.get("status"), "error", result.stderr or evidence)
                self.assertIn(error, evidence["error"])

    def test_repair_files_need_not_stay_in_the_batch_but_are_never_none(self):
        # A repair with no files lands only its task file, so it leaves the product at "bad".
        for files, target, output, error in (("  - count.py", "count.py", "good", None),
                                             ("  - count.py\n  - outside.py", "count.py", "good", None),
                                             ("  - outside.py", "outside.py", "good", None),
                                             ("", "count.py", "bad", "dependencies/files")):
            with self.subTest(files=files):
                self.original_task = {"files": "  - count.py\n  - extra.py"}
                self.setUp()
                repair = self.repair("T002", "T001", output, files=files, target=target)
                result, evidence = self.cli()
                if error:
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(error, evidence["error"])
                else:
                    self.assertEqual(result.returncode, 0, result.stderr or evidence)
                    self.assertEqual(evidence["repair"], {"task": "T002", "task_file": self.repair_file,
                                                          "base": evidence["repair"]["base"], "commit": repair,
                                                          "files": [line.strip(" -") for line in files.splitlines()]})
                    self.assertEqual(evidence["originals"][0]["files"], ["count.py", "extra.py"])

    def test_repair_outside_the_batch_still_needs_the_unchanged_original_product(self):
        expected_errors = {"before": "unlinked changes", "after": "product changed after"}
        for fault, error in expected_errors.items():
            with self.subTest(fault=fault):
                self.setUp()
                if fault == "before":  # another change to the original task's file, in the repair base
                    (self.repo / "count.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                self.repair("T002", "T001", "good", files="  - outside.py", target="outside.py")
                if fault == "after":  # another change to the repair's own file, after its landing
                    (self.repo / "outside.py").write_bytes("print('later')\n".encode("utf-8"))
                result, evidence = self.cli()
                self.assertEqual(result.returncode, 2, evidence)
                self.assertIn(error, evidence["error"])

    def test_change_to_another_file_does_not_block_the_receipt(self):
        (self.repo / "other.py").write_bytes("print('before the repair base')\n".encode("utf-8"))
        repair = self.finish_repair()
        (self.repo / "other.py").write_bytes("print('after the repair landing')\n".encode("utf-8"))
        self.commit("later work in another file")
        result, evidence = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr or evidence)
        self.assertEqual(evidence["repair"]["commit"], repair)

    CYCLE_2 = """# Review — wave 1, cycle 2
Wave verdict: blocked
Cycle: 2
Depth: full
Tasks reviewed: 1
## T001 — Original: fail
- ❌ Prints good — found: prints good too late, count.py:1
  fix: print better
"""
    REPEAT = """## Review findings

### t001_ac1
Criterion: Prints good
- Prints good — found: prints good too late, count.py:1 fix: print better

"""
    FINDING = "## Review findings\n\n### {locator}\nCriterion: {criterion}\n- {criterion} — found: wrong\n\n"
    # Another criterion; the same criterion text in a finding of another task; a finding on a fix task itself.
    OTHER = FINDING.format(locator="t002_ac2", criterion="Exits successfully")
    SAME_TEXT = FINDING.format(locator="t009_ac1", criterion="Prints good")
    FIX_OF_FIX = FINDING.replace("{criterion}", "Prints good")

    def repeat_repair(self, deps="T001, T002"):
        """The cycle 2 review repeats the finding; T003 repairs it on top of the landed T002."""
        (self.project / "review/wave-1.cycle2.md").write_bytes(self.CYCLE_2.encode("utf-8"))
        return self.repair("T003", deps, "better", findings=self.REPEAT)

    def test_repeat_repair_gets_a_receipt_on_top_of_the_earlier_repair_it_depends_on(self):
        first = self.finish_repair()
        second = self.repeat_repair()
        result, evidence = self.cli("T003", cycle=2)
        self.assertEqual(result.returncode, 0, result.stderr or evidence)
        self.assertEqual(evidence["repair"]["commit"], second)
        self.assertEqual(evidence["originals"][0]["commit"], self.original)
        self.assertEqual(evidence["earlier_repairs"],
                         [{"task": "T002", "task_file": self.repair_file, "commit": first, "files": ["count.py"],
                           "base": run_git(self.repo, "rev-parse", f"{first}^")}])
        self.assertNotIn("later_repairs", evidence)
        self.assertEqual((evidence["verification"]["entry"]["commit"],
                          evidence["verification"]["entry"]["execution"]["stdout"]), (second, "better\n"))

    def test_repeat_repair_gets_no_receipt_when_its_base_has_other_changes(self):
        for fault in ("no dependency", "other finding", "same text", "change before", "change after"):
            with self.subTest(fault=fault):
                self.setUp()
                if fault in ("other finding", "same text"):  # T002 is a repair of another finding
                    self.repair_file = self.task("T002", "Repair", 2, "T001",
                                                 self.OTHER if fault == "other finding" else self.SAME_TEXT)
                elif fault == "change before":  # before the linked repair T002; not the landing of a linked repair
                    (self.repo / "count.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                self.finish_repair()
                if fault == "change after":  # after the linked repair T002
                    (self.repo / "count.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                self.repeat_repair(deps="T001" if fault == "no dependency" else "T001, T002")
                result, evidence = self.cli("T003", cycle=2)
                self.assertEqual(result.returncode, 2, evidence)
                self.assertIn("unlinked changes", evidence["error"])

    def test_superseded_repair_gets_a_receipt_from_the_later_repairs_that_depend_on_it(self):
        first = self.finish_repair()
        second = self.repair("T003", "T002", "better")
        third = self.repair("T004", "T003", "best")
        result, evidence = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr or evidence)
        self.assertEqual(evidence["repair"]["commit"], first)
        self.assertEqual([(item["task"], item["commit"], item["files"]) for item in evidence["later_repairs"]],
                         [("T003", second, ["count.py"]), ("T004", third, ["count.py"])])
        self.assertNotIn("earlier_repairs", evidence)
        self.assertEqual((evidence["verification"]["entry"]["commit"],
                          evidence["verification"]["entry"]["execution"]["stdout"]), (third, "best\n"))

    def later_chain(self, unrelated=None):
        """T002 changes count.py; the linked T003 changes count.py and late.py; the linked T004 changes count.py.

        As `fix-tasks` writes a fix of a fix, T003 carries a finding on a criterion of T002, and T004 one of T003.
        `unrelated` names the moment when another task changes late.py, the file that only T003 names.
        """
        def other_task(moment):
            if moment == unrelated:
                (self.repo / "late.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                self.commit("work of another task")

        self.finish_repair()
        other_task("before T003")
        self.repair("T003", "T002", "better", findings=self.FIX_OF_FIX.format(locator="t002_ac1"),
                    files="  - count.py\n  - late.py", target="count.py,late.py")
        self.repair("T004", "T003", "best", findings=self.FIX_OF_FIX.format(locator="t003_ac1"))
        other_task("after T004")

    def test_superseded_repair_receipt_names_the_files_of_every_later_linked_repair(self):
        self.later_chain()
        result, evidence = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr or evidence)
        self.assertEqual([item["task"] for item in evidence["later_repairs"]], ["T003", "T004"])
        self.assertEqual(evidence["chain_files"], ["count.py", "late.py"])

    def test_superseded_repair_gets_no_receipt_when_a_file_of_a_middle_linked_repair_changed(self):
        for moment in ("before T003", "after T004"):
            with self.subTest(moment=moment):
                self.setUp()
                self.later_chain(unrelated=moment)
                result, evidence = self.cli()
                self.assertEqual(result.returncode, 2, evidence)
                self.assertIn("product changed after", evidence["error"])

    def test_superseded_repair_that_landed_on_a_newer_parent_needs_the_chain_files_unchanged_from_its_base(self):
        # T002 starts at its base in its own isolate. Another task lands before T002 does, so the parent of
        # the T002 landing is not its base. The reviewer builds the product from that base.
        for other, error in (("late.py", "between the repair base and the repair landing"), ("other.py", None)):
            with self.subTest(other=other):
                self.setUp()
                base = self.commit("plan repair")
                sidecars = tempfile.TemporaryDirectory()
                self.addCleanup(sidecars.cleanup)
                with mock.patch.dict(os.environ, {"GSD_PATH_WORKTREE_ROOT": sidecars.name}):
                    isolated = isolation.isolate_task(self.repo, base, "T002", 2)
                    worktree = Path(isolated["worktree"])
                    isolation.activate_task(worktree, base, "T002", "coder", self.repair_file, isolated["task_branch"])
                    (worktree / "count.py").write_bytes("print('good')\n".encode("utf-8"))
                    (self.repo / other).write_bytes("print('parallel task')\n".encode("utf-8"))
                    self.commit("parallel task")
                    repair = isolation.land(self.repo, worktree, base, "T002", "Repair", self.repair_file, ["count.py"])["commit"]
                    isolation.retire(self.repo, worktree, isolated["task_branch"], False)
                self.assertNotEqual(run_git(self.repo, "rev-parse", f"{repair}^"), base)
                self.repair("T003", "T002", "better", files="  - count.py\n  - late.py", target="count.py,late.py")
                result, evidence = self.cli()
                if error:
                    self.assertEqual(result.returncode, 2, evidence)
                    self.assertIn(error, evidence["error"])
                else:
                    self.assertEqual(result.returncode, 0, result.stderr or evidence)
                    self.assertEqual((evidence["repair"]["base"], evidence["repair"]["commit"], evidence["chain_files"]),
                                     (base, repair, ["count.py", "late.py"]))

    def test_superseded_repair_gets_no_receipt_from_an_unlinked_or_unverified_later_change(self):
        expected_errors = {"no dependency": "product changed after", "other finding": "product changed after",
                           "same text": "product changed after",
                           "no findings": "product changed after", "other change": "product changed after",
                           "other file": "product changed after", "no verify": "passing Verify"}
        for fault, error in expected_errors.items():
            with self.subTest(fault=fault):
                self.setUp()
                self.finish_repair()
                ledger = (self.repo / _common.VERIFY_LEDGER_PATH).read_bytes()
                self.repair("T003", "T001" if fault == "no dependency" else "T002", "better",
                            findings={"other finding": self.OTHER, "same text": self.SAME_TEXT,
                                      "no findings": ""}.get(fault),
                            files="  - count.py\n  - late.py" if fault == "other file" else "  - count.py")
                if fault == "other change":  # after the last linked repair
                    (self.repo / "count.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                elif fault == "other file":  # the last linked repair's own file changes after its Verify
                    (self.repo / "late.py").write_bytes("print('unrelated')\n".encode("utf-8"))
                    self.commit("later work")
                elif fault == "no verify":  # the Verify of the last linked repair is not in the ledger
                    (self.repo / _common.VERIFY_LEDGER_PATH).write_bytes(ledger)
                result, evidence = self.cli()
                self.assertEqual(result.returncode, 2, evidence)
                self.assertIn(error, evidence["error"])

    def test_repair_of_a_finding_in_two_repos_binds_to_the_batch_of_its_own_repo(self):
        self.original_task = {"owned": "SC1"}
        self.setUp()
        member = self.repo / self.task("T003", "Member", 1, "", "", owned="SC1")
        member.write_bytes(member.read_text(encoding="utf-8").replace("files:\n", "repo: web\nfiles:\n", 1).encode("utf-8"))
        self.review.write_bytes("""# Review — wave 1, cycle 1
Wave verdict: blocked
Cycle: 1
Depth: full
Tasks reviewed: 2
## T001 — Original: pass
- ✅ Prints good — recorded Verify
## Intent coverage
### SC1 — Every repo prints good: fail
- ❌ Every repo prints good — found: prints bad, count.py:1
""".encode("utf-8"))
        findings = "## Review findings\n\n### sc1\nCriterion: Every repo prints good\n" \
                   "- Every repo prints good — found: prints bad, count.py:1\n\n"
        self.repair_file = self.task("T002", "Repair", 2, "T001", findings)
        self.finish_repair()
        result, evidence = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr or evidence)
        self.assertEqual(evidence["groups"][0]["tasks"], ["T001", "T003"])
        self.assertEqual([item["task"] for item in evidence["originals"]], ["T001"])
