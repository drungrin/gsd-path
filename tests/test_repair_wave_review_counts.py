"""A repair-only wave is judged by its source wave's next review cycle, not by a review of its own."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts import discussion_validate
from scripts.archive_milestone import ArchiveError


class RepairWaveReviewCountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        for directory in ("intent", "plan", "tasks", "review"):
            (self.project / directory).mkdir()
        (self.project / "intent" / "INTENT.md").write_text(
            "# Intent\n\n## Success criteria\n\n1. demo works\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_plan(self, repair_heading: str = "## Wave 2 — fix wave 1 cycle 1 review findings",
                   extra_repair_row: str = "") -> None:
        (self.project / "plan" / "PLAN.md").write_text(
            f"""# Plan

## Wave 1 — demo

| Task | Title | Deps | Files |
|------|-------|------|-------|
| T001 | demo | — | src/demo.py |

Review depth: full

{repair_heading}

| Task | Title | Deps | Files |
|------|-------|------|-------|
| T002 | repair demo | T001 | src/demo.py |
{extra_repair_row}
Review depth: full

## Intent coverage

| Criterion | Task | Acceptance |
|-----------|------|------------|
| SC1 | T001 | AC1 |
""",
            encoding="utf-8",
        )

    def write_tasks(self, repair_findings: bool = True) -> None:
        (self.project / "tasks" / "T001-demo.md").write_text(
            "---\nid: T001\ntitle: demo\nwave: 1\n---\n", encoding="utf-8")
        findings = "\n## Review findings\n\n### t001_ac1\n\nCriterion: demo works\n" if repair_findings else ""
        (self.project / "tasks" / "T002-repair-demo.md").write_text(
            f"---\nid: T002\ntitle: repair demo\nwave: 2\n---\n{findings}", encoding="utf-8")

    def write_review(self, wave: int, cycle: int, verdict: str, task: str = "T001 — demo") -> None:
        marker = "✅" if verdict == "pass" else "❌"
        task_verdict = "pass" if verdict == "pass" else "fail"
        coverage = "" if wave != 1 else f"""
## Intent coverage

### SC1 — demo works: {task_verdict}

- {marker} Evidence recorded in tests/ExampleTest.py:21 from the Verify output.
"""
        (self.project / "review" / f"wave-{wave}.cycle{cycle}.md").write_text(
            f"""# Review — wave {wave}, cycle {cycle}

Wave verdict: {verdict}
Cycle: {cycle}
Depth: full
Tasks reviewed: 1

## {task}: {task_verdict}

- {marker} demo works — evidence recorded in tests/ExampleTest.py:14 from the Verify output.
{coverage}""",
            encoding="utf-8",
        )

    def write_receipt(self, wave: int, cycle: int, task: str) -> None:
        payload = {"command": "repair-evidence", "status": "ok",
                   "source": {"wave": wave, "cycle": cycle}, "repair": {"task": task}}
        (self.project / "review" / f"wave-{wave}.cycle{cycle}.repair-{task}.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def counts(self):
        return discussion_validate.review_cycle_counts(self.project)

    def test_repair_wave_is_judged_by_the_next_source_cycle(self) -> None:
        self.write_plan()
        self.write_tasks()
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        self.assertEqual([2, 0], self.counts())

    def test_source_wave_must_be_reviewed_after_the_findings_cycle(self) -> None:
        self.write_plan()
        self.write_tasks()
        self.write_review(1, 1, "pass")
        with self.assertRaisesRegex(ArchiveError, "repair wave 2 requires a review cycle of wave 1 after cycle 1"):
            self.counts()

    def test_source_wave_last_cycle_must_still_pass(self) -> None:
        self.write_plan()
        self.write_tasks()
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "blocked")
        with self.assertRaisesRegex(ArchiveError, "last review cycle for wave 1 did not pass"):
            self.counts()

    def test_wave_with_a_task_without_review_findings_needs_its_own_review(self) -> None:
        self.write_plan()
        self.write_tasks(repair_findings=False)
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        with self.assertRaisesRegex(ArchiveError, "review cycle waves do not match PLAN.md"):
            self.counts()

    def test_ordinary_wave_without_review_still_fails(self) -> None:
        self.write_plan("## Wave 2 — more features")
        self.write_tasks()
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        with self.assertRaisesRegex(ArchiveError, "review cycle waves do not match PLAN.md"):
            self.counts()

    def test_heading_naming_a_later_or_same_wave_is_not_a_source(self) -> None:
        for heading in ("## Wave 2 — fix wave 2 cycle 1 review findings",
                        "## Wave 2 — fix wave 3 cycle 1 review findings"):
            with self.subTest(heading=heading):
                self.write_plan(heading)
                self.write_tasks()
                self.write_review(1, 1, "blocked")
                self.write_review(1, 2, "pass")
                with self.assertRaisesRegex(ArchiveError, "review cycle waves do not match PLAN.md"):
                    self.counts()

    def test_repair_wave_with_its_own_review_is_validated_as_before(self) -> None:
        self.write_plan()
        self.write_tasks()
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        self.write_review(2, 1, "pass", task="T002 — repair demo")
        self.assertEqual([2, 1], self.counts())

    def test_repair_receipts_name_the_source_without_the_driver_heading(self) -> None:
        self.write_plan("## Wave 2 — repairs")
        self.write_tasks()
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        self.write_receipt(1, 1, "T002")
        self.assertEqual([2, 0], self.counts())

    def test_legacy_heading_names_its_source_with_one_wave_cycle_pair(self) -> None:
        for heading in ("## Wave 2 — fix wave 1 cycle 1 panel finding",
                        "## Wave 2 — fix wave 1 cycle 1 review finding",
                        "## Wave 2 — redesign the demo check (wave 1 cycle 1 cap ruling)",
                        "## Wave 2 — Fix Wave 1 Cycle 1 findings",
                        "## Wave 2 — fix wave 1 cycle 1, again wave 1 cycle 1"):
            with self.subTest(heading=heading):
                self.write_plan(heading)
                self.write_tasks()
                self.write_review(1, 1, "blocked")
                self.write_review(1, 2, "pass")
                self.assertEqual([2, 0], self.counts())

    def test_legacy_heading_without_exactly_one_earlier_pair_is_not_a_source(self) -> None:
        for heading in ("## Wave 2 — fix wave 1 cycle 1 and wave 1 cycle 2 findings",
                        "## Wave 2 — fix the review findings",
                        "## Wave 2 — fix rewave 1 cycle 1 and wave 1 cycle 1x",
                        "## Wave 2 — fix wave 2 cycle 1 panel finding"):
            with self.subTest(heading=heading):
                self.write_plan(heading)
                self.write_tasks()
                self.write_review(1, 1, "blocked")
                self.write_review(1, 2, "pass")
                with self.assertRaisesRegex(ArchiveError, "review cycle waves do not match PLAN.md"):
                    self.counts()

    def test_legacy_heading_source_still_needs_a_later_review(self) -> None:
        self.write_plan("## Wave 2 — fix wave 1 cycle 1 panel finding")
        self.write_tasks()
        self.write_review(1, 1, "pass")
        with self.assertRaisesRegex(ArchiveError, "repair wave 2 requires a review cycle of wave 1 after cycle 1"):
            self.counts()

    def test_receipts_outrank_the_heading_pair(self) -> None:
        self.write_plan("## Wave 2 — fix wave 1 cycle 2 panel finding")
        self.write_tasks()
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        self.write_receipt(1, 1, "T002")
        self.assertEqual([2, 0], self.counts())

    def test_partial_receipts_do_not_fall_back_to_the_heading_pair(self) -> None:
        self.write_plan("## Wave 2 — fix wave 1 cycle 1 panel findings",
                        "| T003 | repair demo again | T001 | src/demo.py |\n")
        self.write_tasks()
        (self.project / "tasks" / "T003-repair-demo-again.md").write_text(
            "---\nid: T003\ntitle: repair demo again\nwave: 2\n---\n\n## Review findings\n\n"
            "### t001_ac1\n\nCriterion: demo works\n", encoding="utf-8")
        self.write_review(1, 1, "blocked")
        self.write_review(1, 2, "pass")
        self.write_receipt(1, 1, "T002")
        with self.assertRaisesRegex(ArchiveError, "review cycle waves do not match PLAN.md"):
            self.counts()

    def test_manifest_counts_accept_a_judged_repair_wave(self) -> None:
        pattern = discussion_validate.MANIFEST_WAVES_PATTERN
        self.assertIsNotNone(pattern.fullmatch("5  Tasks: 20 done / 20 total  Review cycles used: 3/2/1/0/0"))
        self.assertIsNotNone(pattern.fullmatch("1  Tasks: 1 done / 1 total  Review cycles used: 1  Attested: 1"))
        for invalid in ("2  Tasks: 2 done / 2 total  Review cycles used: 2/01",
                        "2  Tasks: 2 done / 2 total  Review cycles used: 2/-1",
                        "2  Tasks: 2 done / 2 total  Review cycles used: "):
            with self.subTest(invalid=invalid):
                self.assertIsNone(pattern.fullmatch(invalid))


if __name__ == "__main__":
    unittest.main()
