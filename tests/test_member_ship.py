import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import archive_milestone, integration, pipeline_git
from scripts.archive_milestone import ArchiveError
import tests.test_archive_milestone as archive_tests

MEMBER_HEAD = "b" * 40
TAG = "milestone/demo-001-demo"


class MemberShipTests(unittest.TestCase):
    """A coordinator ship commit names each closed member; validators bind those lines."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.helper = archive_tests.ArchiveMilestoneTests()
        base = Path(temporary.name).resolve()
        self.repo = base / "primary"
        self.repo.mkdir()
        self.helper.make_publishable_bound_repo(self.repo, base / "origin.git")
        self.archive = self.helper.prepare_archive(self.repo)
        (self.archive / "build").mkdir(exist_ok=True)
        (self.archive / "build/members.json").write_text(json.dumps({
            "schema": "gsd-path/member-lock/v1",
            "members": [{"name": "web", "branch": "feature/demo-milestone-001", "base": "a" * 40}]}), encoding="utf-8")
        review = self.archive / "review"
        for path in [review / "FINAL.md", *sorted(review.glob("final-gap-*.md"))]:
            text = path.read_text(encoding="utf-8")
            end = text.index("\n", text.index("Reviewed HEAD:"))
            path.write_text(text[:end] + f"\nMember reviewed HEAD: web {MEMBER_HEAD}" + text[end:], encoding="utf-8")
        self.assertEqual(self.helper.render_manifest(self.repo).returncode, 0)
        self.assertEqual(self.helper.preflight(self.repo).returncode, 0)
        self.reviewed = self.git("rev-parse", "HEAD")
        self.helper.mark_shipped(self.repo)
        self.git("add", ".project")

    def git(self, *arguments: str) -> str:
        return self.helper.git(self.repo, *arguments).stdout.strip()

    def ship(self, rows) -> str:
        body = pipeline_git.ship_commit_body(self.archive.relative_to(self.repo).as_posix(), self.reviewed, rows)
        # The guard's member-close journal check has its own test; commit the body directly.
        self.git("commit", "-q", "--no-verify", "-m", pipeline_git.ship_subject(self.archive.name), "-m", body)
        return self.git("rev-parse", "HEAD")

    def row(self, **changes) -> dict:
        return {"name": "web", "reviewed_head": MEMBER_HEAD, "merge": "c" * 40, "tag": TAG, **changes}

    def test_validate_binds_member_lines_and_returns_them(self) -> None:
        self.ship([self.row()])
        self.assertEqual(archive_milestone.validate(self.repo)["members"], [self.row()])

    def test_validate_refuses_a_member_ship_without_member_lines(self) -> None:
        self.ship([])
        with self.assertRaisesRegex(ArchiveError, "member"):
            archive_milestone.validate(self.repo)

    def test_validate_refuses_a_member_head_that_differs_from_final(self) -> None:
        self.ship([self.row(reviewed_head="d" * 40)])
        with self.assertRaisesRegex(ArchiveError, "member"):
            archive_milestone.validate(self.repo)

    def test_validate_refuses_an_unlocked_member(self) -> None:
        self.ship([self.row(name="api")])
        with self.assertRaisesRegex(ArchiveError, "member"):
            archive_milestone.validate(self.repo)

    def test_validate_integrated_checks_each_member_origin(self) -> None:
        self.ship([self.row()])
        proven = {"member": "web", "merge": "c" * 40, "tag": TAG, "tag_object": "e" * 40}
        with mock.patch.object(integration, "validate_member_integrated", return_value=proven) as checked:
            result = integration.integrate(self.repo, "demo")
            checked.assert_called_with(self.repo.resolve(), "web", self.archive.relative_to(self.repo).as_posix(),
                                       MEMBER_HEAD, refresh=True)
            checked.reset_mock()
            integration.validate_integrated(self.repo, "demo", refresh=False)
            checked.assert_called_once_with(self.repo.resolve(), "web", self.archive.relative_to(self.repo).as_posix(),
                                            MEMBER_HEAD, refresh=False)
        self.assertEqual(result["members"], [self.row()])
        with mock.patch.object(integration, "validate_member_integrated", return_value={**proven, "merge": "f" * 40}):
            with self.assertRaisesRegex(ArchiveError, "member web"):
                integration.validate_integrated(self.repo, "demo")


if __name__ == "__main__":
    unittest.main()
