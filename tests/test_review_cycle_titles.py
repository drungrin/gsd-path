"""Build completion reads earlier review cycles by task id and order, not title.

The wave gate checks each cycle's exact titles when that cycle is recorded.
A later title fix (for example quoting a title whose ` #` the frontmatter
parser read as a comment) must not invalidate an earlier, immutable cycle;
the last cycle still has to match the task files exactly.
"""

import tempfile
import unittest
from pathlib import Path

from scripts import discussion_validate
from scripts.archive_milestone import ArchiveError


class ReviewCycleTitlesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        for directory in ("intent", "plan", "tasks", "review"):
            (self.project / directory).mkdir()
        (self.project / "intent" / "INTENT.md").write_text(
            "# Intent\n\n## Success criteria\n\n1. demo works\n", encoding="utf-8")
        (self.project / "plan" / "PLAN.md").write_text(
            """# Plan

## Wave 1 — demo

| Task | Title | Deps | Files |
|------|-------|------|-------|
| T001 | demo | — | src/demo.py |

Review depth: full

## Intent coverage

| Criterion | Task | Acceptance |
|-----------|------|------------|
| SC1 | T001 | AC1 |
""",
            encoding="utf-8",
        )
        # Unquoted, so the parsed title stops before ` #7`.
        (self.project / "tasks" / "T001-demo.md").write_text(
            "---\nid: T001\ntitle: Align the docs (audit #7, #8)\nwave: 1\n---\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_review(self, cycle: int, verdict: str, title: str) -> None:
        marker = "✅" if verdict == "pass" else "❌"
        task_verdict = "pass" if verdict == "pass" else "fail"
        (self.project / "review" / f"wave-1.cycle{cycle}.md").write_text(
            f"""# Review — wave 1, cycle {cycle}

Wave verdict: {verdict}
Cycle: {cycle}
Depth: full
Tasks reviewed: 1

## T001 — {title}: {task_verdict}

- {marker} demo works — evidence recorded in tests/ExampleTest.py:14 from the Verify output.

## Intent coverage

### SC1 — demo works: {task_verdict}

- {marker} Evidence recorded in tests/ExampleTest.py:21 from the Verify output.
""",
            encoding="utf-8",
        )

    def test_an_earlier_cycle_keeps_the_title_it_was_gated_with(self) -> None:
        self.write_review(1, "blocked", "Align the docs (audit #7, #8)")
        self.write_review(2, "pass", "Align the docs (audit")

        self.assertEqual([2], discussion_validate.review_cycle_counts(self.project))

    def test_the_last_cycle_still_requires_the_exact_title(self) -> None:
        self.write_review(1, "blocked", "Align the docs (audit")
        self.write_review(2, "pass", "Align the docs (audit #7, #8)")

        with self.assertRaisesRegex(ArchiveError, "tasks and titles do not match"):
            discussion_validate.review_cycle_counts(self.project)


if __name__ == "__main__":
    unittest.main()
