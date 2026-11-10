"""Archive undo owns the uncommitted editorial copy pr_editorial wrote for that archive."""

import tempfile
import unittest
from pathlib import Path

from scripts import pipeline_state, pipeline_undo
from tests.test_pipeline_undo import init_repo, run_git, state_text


ARCHIVE = ".project/archive/001-first"


def prepared(root: Path) -> Path:
    repo = init_repo(root)
    project = repo / ".project"
    intent = project / "intent"
    intent.mkdir(parents=True)
    (project / "STATE.md").write_bytes(state_text(phase="ship", status="active").encode("utf-8"))
    (intent / "INTENT.md").write_bytes("# Intent — first\n".encode("utf-8"))
    older = project / "publications" / "000-zero" / "web.json"
    older.parent.mkdir(parents=True)
    older.write_text('{"recorded": true}\n', encoding="utf-8")
    run_git(repo, "add", ".project")
    run_git(repo, "commit", "-m", "fixture: ship active")
    archived_intent = repo / ARCHIVE / "intent"
    archived_intent.mkdir(parents=True)
    (intent / "INTENT.md").replace(archived_intent / "INTENT.md")
    intent.rmdir()
    (project / "STATE.md").write_bytes(
        state_text(phase="ship", status="active", archive=f"{ARCHIVE}/").encode("utf-8"))
    return repo


class PublicationUndoTests(unittest.TestCase):
    def test_this_archives_uncommitted_publication_is_deleted_with_the_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = prepared(Path(tmp))
            copy = repo / ".project/publications/001-first/web.json"
            copy.parent.mkdir()
            copy.write_text('{"member": "web"}\n', encoding="utf-8")
            previewed = pipeline_undo.preview(repo)
            self.assertEqual(previewed["target"]["kind"], "uncommitted-archive")
            self.assertIn("delete untracked .project/publications/001-first", previewed["target"]["effects"])
            pipeline_undo.apply_undo(repo, "uncommitted-archive", previewed["target"]["head"])
            self.assertFalse(copy.parent.exists())
            self.assertTrue((repo / ".project/publications/000-zero/web.json").is_file())
            self.assertIsNone(pipeline_state.load_state(repo)[0].archive)

    def test_other_publication_changes_block_without_mutation(self) -> None:
        cases = {
            "another archive": ".project/publications/002-other/web.json",
            "nested path": ".project/publications/001-first/extra/web.json",
            "not json": ".project/publications/001-first/web.txt",
        }
        for name, relative in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                repo = prepared(Path(tmp))
                path = repo / relative
                path.parent.mkdir(parents=True)
                path.write_text("{}\n", encoding="utf-8")
                previewed = pipeline_undo.preview(repo)
                self.assertIsNone(previewed["target"]["kind"])
                self.assertIn("unowned", previewed["target"]["blocked"][0])
                self.assertTrue(path.is_file())

    def test_an_edited_committed_publication_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = prepared(Path(tmp))
            older = repo / ".project/publications/000-zero/web.json"
            older.write_text('{"recorded": false}\n', encoding="utf-8")
            previewed = pipeline_undo.preview(repo)
            self.assertIsNone(previewed["target"]["kind"])
            self.assertEqual(older.read_text(encoding="utf-8"), '{"recorded": false}\n')


if __name__ == "__main__":
    unittest.main()
