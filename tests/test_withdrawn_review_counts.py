"""Build completion and the archive read a wave review the way the wave gate does (#483).

A withdrawn tombstone with an owner citation produced no live work: the wave
gate excludes it from the reviewed tasks and from the wave's owned criteria,
so the completion validator must not require it either.
"""

import tempfile
import unittest
from pathlib import Path

from scripts import discussion_validate
from scripts.archive_milestone import ArchiveError

CITATION = '- Withdrawn — 2026-10-26: "owner rules this out of scope"\n'


class WithdrawnReviewCountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        for directory in ("intent", "plan", "tasks", "review"):
            (self.project / directory).mkdir()
        (self.project / "intent" / "INTENT.md").write_text(
            "# Intent\n\n## Success criteria\n\n1. demo works\n2. report works\n",
            encoding="utf-8",
        )
        (self.project / "plan" / "PLAN.md").write_text(
            """# Plan

## Wave 1 — demo

| Task | Title | Deps | Files |
|------|-------|------|-------|
| T001 | demo | — | src/demo.py |
| T002 | report | — | src/report.py |

Review depth: full

## Wave 2 — report again

| Task | Title | Deps | Files |
|------|-------|------|-------|
| T003 | report again | T001 | src/report.py |

Review depth: full

## Intent coverage

| Criterion | Task | Acceptance |
|-----------|------|------------|
| SC1 | T001 | AC1 |
| SC2 | T002 | AC2 |
| SC2 | T003 | AC3 |
""",
            encoding="utf-8",
        )
        self.write_task("T001", "demo", 1)
        self.write_task("T003", "report again", 2)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_task(self, task_id: str, title: str, wave: int, status: str = "done",
                   log: str = "") -> None:
        slug = title.replace(" ", "-")
        (self.project / "tasks" / f"{task_id}-{slug}.md").write_text(
            f"---\nid: {task_id}\ntitle: {title}\nwave: {wave}\nstatus: {status}\n---\n\n"
            f"## Log\n\n- 2026-10-25 — created\n{log}",
            encoding="utf-8",
        )

    def write_review(self, wave: int, tasks, criteria) -> None:
        sections = "".join(
            f"## {task_id} — {title}: pass\n\n"
            f"- ✅ {title} works — evidence recorded in tests/ExampleTest.py:14 "
            "from the Verify output.\n\n"
            for task_id, title in tasks
        )
        coverage = "".join(
            f"### {sc} — {text}: pass\n\n"
            "- ✅ Evidence recorded in tests/ExampleTest.py:21 from the Verify output.\n\n"
            for sc, text in criteria
        )
        (self.project / "review" / f"wave-{wave}.cycle1.md").write_text(
            f"""# Review — wave {wave}, cycle 1

Wave verdict: pass
Cycle: 1
Depth: full
Tasks reviewed: {len(tasks)}

{sections}## Intent coverage

{coverage}""",
            encoding="utf-8",
        )

    def write_reviews_without_t002(self) -> None:
        self.write_review(1, [("T001", "demo")], [("SC1", "demo works")])
        self.write_review(2, [("T003", "report again")], [("SC2", "report works")])

    def test_completion_skips_a_cited_withdrawn_tombstone(self) -> None:
        self.write_task("T002", "report", 1, status="withdrawn", log=CITATION)
        self.write_reviews_without_t002()

        self.assertEqual([1, 1], discussion_validate.review_cycle_counts(self.project))

    def test_an_uncited_withdrawn_tombstone_still_blocks_completion(self) -> None:
        self.write_task("T002", "report", 1, status="withdrawn")
        self.write_reviews_without_t002()

        with self.assertRaisesRegex(ArchiveError, "T002 is withdrawn without an owner citation"):
            discussion_validate.review_cycle_counts(self.project)

    def test_a_live_task_missing_from_the_review_still_blocks_completion(self) -> None:
        self.write_task("T002", "report", 1)
        self.write_reviews_without_t002()

        with self.assertRaisesRegex(ArchiveError, "do not match its wave task files"):
            discussion_validate.review_cycle_counts(self.project)


if __name__ == "__main__":
    unittest.main()
