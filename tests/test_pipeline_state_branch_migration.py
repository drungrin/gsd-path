import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import pipeline_state
from tests.test_pipeline_state import run_git, state_text

PIPELINE_STATE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pipeline_state.py"
LEGACY = "gsd-path/M001"
TARGET = "feature/milestone-001"


def make_legacy_repo(tmp: str, *, phase: str = "plan", status: str = "active") -> tuple[Path, Path]:
    origin = Path(tmp) / "origin.git"
    repo = Path(tmp) / "repo"
    run_git(Path(tmp), "init", "--bare", "-b", "main", str(origin))
    run_git(Path(tmp), "init", "-b", "main", str(repo))
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "user.email", "test@example.com")
    (repo / "product.txt").write_bytes(b"base\n")
    run_git(repo, "add", "product.txt")
    run_git(repo, "commit", "-m", "base")
    run_git(repo, "remote", "add", "origin", str(origin))
    run_git(repo, "push", "-u", "origin", "main")
    run_git(repo, "switch", "--no-track", "-c", LEGACY)
    project = repo / ".project"
    project.mkdir()
    (project / "STATE.md").write_bytes(
        state_text(milestone="guards", phase=phase, status=status, branch=LEGACY).encode("utf-8")
    )
    return origin, repo


def current_branch(repo: Path) -> str:
    return run_git(repo, "branch", "--show-current").stdout.strip()


class LegacyBranchMigrationTests(unittest.TestCase):
    def test_validate_names_the_migration_for_a_legacy_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "migrate-branch"):
                pipeline_state.validate_state(repo)

    def test_preview_then_migrate_renames_branch_and_records_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            before = (repo / ".project" / "STATE.md").read_bytes()

            preview = pipeline_state.migrate_branch(repo, dry_run=True)

            self.assertEqual(preview["status"], "preview")
            self.assertEqual((preview["from"], preview["to"]), (LEGACY, TARGET))
            self.assertEqual(current_branch(repo), LEGACY)
            self.assertEqual((repo / ".project" / "STATE.md").read_bytes(), before)

            result = pipeline_state.migrate_branch(repo)

            self.assertEqual(result["status"], "migrated")
            self.assertFalse(result["resumed"])
            self.assertEqual(current_branch(repo), TARGET)
            self.assertEqual(
                run_git(repo, "branch", "--list", LEGACY).stdout.strip(), ""
            )
            validated = pipeline_state.validate_state(repo)
            self.assertEqual(validated["state"]["branch"], TARGET)
            self.assertEqual(validated["state"]["phase"], "plan")
            log = (repo / ".project" / "STATE.md").read_text(encoding="utf-8")
            self.assertTrue(
                log.rstrip().endswith(f"— plan — bound branch renamed from {LEGACY} to {TARGET}")
            )

            again = pipeline_state.migrate_branch(repo)
            self.assertEqual(again["status"], "already-migrated")

    def test_rerun_completes_state_after_interrupted_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            run_git(repo, "branch", "-m", LEGACY, TARGET)

            result = pipeline_state.migrate_branch(repo)

            self.assertEqual(result["status"], "migrated")
            self.assertTrue(result["resumed"])
            self.assertEqual(pipeline_state.validate_state(repo)["state"]["branch"], TARGET)

    def test_refuses_a_published_legacy_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            run_git(repo, "push", "origin", LEGACY)

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "unpublished"):
                pipeline_state.migrate_branch(repo)
            self.assertEqual(current_branch(repo), LEGACY)

    def test_refuses_after_build_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp, phase="build", status="active")

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "before build"):
                pipeline_state.migrate_branch(repo)
            self.assertEqual(current_branch(repo), LEGACY)

    def test_refuses_with_a_legacy_task_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            run_git(repo, "branch", "gsd-path-task/T001")

            with self.assertRaisesRegex(
                pipeline_state.PipelineStateError, "refs/heads/gsd-path-task/T001"
            ):
                pipeline_state.migrate_branch(repo)
            self.assertEqual(current_branch(repo), LEGACY)

    def test_refuses_when_target_branch_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            run_git(repo, "branch", TARGET, "main")

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "already exists"):
                pipeline_state.migrate_branch(repo)
            self.assertEqual(current_branch(repo), LEGACY)

    def test_refuses_when_another_branch_is_checked_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            run_git(repo, "switch", "main")

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "current branch"):
                pipeline_state.migrate_branch(repo)

    def test_cli_reports_preview_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, repo = make_legacy_repo(tmp)
            result = subprocess.run(
                [sys.executable, "-B", str(PIPELINE_STATE_SCRIPT), "migrate-branch",
                 "--repo", str(repo), "--dry-run"],
                capture_output=True, encoding="utf-8", errors="replace",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "gsd-path/branch-migration/v1")
            self.assertEqual(payload["status"], "preview")
            self.assertEqual(current_branch(repo), LEGACY)


if __name__ == "__main__":
    unittest.main()
