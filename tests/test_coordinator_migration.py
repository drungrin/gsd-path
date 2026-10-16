import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import _common, build_state, check_handoffs, coordinator_migration as migration, isolation, members, pipeline_state, review_findings
from tests.test_build_state import task_text
from tests import test_handoffs


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


class CoordinatorMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.coordinator, self.code = [self.root / n for n in ("legacy", "orchestration", "code")]
        self.source.mkdir()
        self.origin, self.audit = self.root / "product.git", self.root / "orchestration.git"
        for repo in (self.origin, self.audit):
            git(self.root, "init", "--bare", "-q", "-b", "main", str(repo))
        git(self.source, "init", "-q", "-b", "main")
        git(self.source, "config", "user.name", "Test User")
        git(self.source, "config", "user.email", "test@example.test")
        self.product_url = "https://dev.azure.com/example-org/Example/_git/product"
        self.audit_url = "https://dev.azure.com/example-org/Example/_git/orchestration"
        for bare, url in ((self.origin, self.product_url), (self.audit, self.audit_url)):
            git(self.source, "config", "url." + str(bare) + ".insteadOf", url)
        git(self.source, "remote", "add", "origin", self.product_url)
        (self.source / "app.py").write_text("print(1)\n")
        (self.source / ".gitignore").write_text("node_modules/\n.env\n.gsd-path/\n.claude/\nAGENTS.md\nWORKFLOW.md\n")
        self.commit("base")
        git(self.source, "push", "-q", "origin", "main")
        git(self.source, "remote", "set-head", "origin", "--auto")
        git(self.source, "checkout", "-q", "-b", "feature/milestone-001")
        for name in ("plan", "tasks", "intent", "research", "review", "build"):
            (self.source / ".project" / name).mkdir(parents=True, exist_ok=True)
        (self.source / ".project/STATE.md").write_text("---\npipeline: gsd-path/v2\nproject: legacy\nmilestone: demo\nphase: ship\nstatus: active\nbranch: feature/milestone-001\narchive: null\nintegration_default: external-landing\nintegration: external-landing\nintegration_source: default\n---\n\n# State\n\n## Log\n")
        (self.source / ".project/plan/PLAN.md").write_text("# Plan\nProject verify: `python app.py`\n\n## Wave 1 — change\n\nReview depth: full\n")
        (self.source / ".project/intent/INTENT.md").write_text("# Intent\n\n## Success criteria\n\n1. **SC1 — output.** The app prints two.\n")
        (self.source / ".project/research/SYNTHESIS.md").write_text("# Synthesis\n\nUse the existing app.\n")
        self.task_path = ".project/tasks/T001-change.md"
        self.task = self.source / self.task_path
        self.task.write_text(task_text("T001", "Change output", 1, (), ("app.py",)))
        self.base = self.commit("plan: approve")
        (self.source / "app.py").write_text("print(2)\n")
        self.assertEqual(subprocess.check_output(["python3", "app.py"], cwd=self.source, text=True).strip(), "2")
        self.task.write_text(task_text("T001", "Change output", 1, (), ("app.py",), status="done", agent="worker", base=self.base,
                                       log=("created", "Verify passed for the task snapshot.")))
        body = isolation.task_commit_body(self.task_path, [self.task_path, "app.py"], self.base)
        git(self.source, "add", ".")
        git(self.source, "commit", "-q", "-m", "T001: Change output", "-m", body)
        self.head = git(self.source, "rev-parse", "HEAD")
        git(self.source, "push", "-q", "origin", "HEAD:refs/heads/chore/demo-review")
        self.spec = migration.preview(self.source, self.coordinator, self.code, self.audit_url,
                                       "sample-orchestrator", "chore/demo-review", "Approved migration and backup rewrite.", self.head)

    def commit(self, subject):
        git(self.source, "add", ".")
        git(self.source, "commit", "-q", "-m", subject)
        return git(self.source, "rev-parse", "HEAD")

    def prepared(self):
        return migration.prepare(self.spec)

    def completed_marker(self):
        path = migration._journal(self.coordinator)
        data = json.loads(path.read_text())
        data["stage"] = "complete"
        migration._write(path, data)

    def test_preview_is_read_only_and_exact(self):
        self.assertFalse(self.coordinator.exists())
        self.assertFalse(self.code.exists())
        self.assertEqual(self.spec["source_head"], self.head)
        self.assertEqual(git(self.source, "status", "--porcelain"), "")

    def test_projection_preserves_original_proof_and_clean_product_history(self):
        prepared = self.prepared()
        receipt = migration.load(self.coordinator)
        self.assertEqual(git(self.coordinator, "show", self.head + ":app.py"), "print(2)")
        self.assertFalse((self.coordinator / "app.py").exists())
        self.assertEqual((self.coordinator / self.task_path).read_bytes(), self.task.read_bytes())
        self.assertEqual(git(self.code, "show", receipt["export_head"] + ":app.py"), "print(2)")
        self.assertEqual(git(self.code, "log", "--format=%H", prepared["bound"], "--", ".project"), "")
        self.assertEqual(migration.validate(self.coordinator, allow_pending=True)["status"], "verified")
        self.assertEqual(pipeline_state.route_state(self.coordinator)["route"]["action"], "block")
        self.completed_marker()
        proof = build_state.verify_landed_tasks(str(self.coordinator), ".project", prepared["coordinator_head"])
        self.assertEqual(proof["tasks"][0]["landed_commit"], self.head)
        self.assertEqual(proof["migration"]["tasks"], 1)

    def test_publish_preserves_first_then_rewrites_only_exact_backup(self):
        prepared = self.prepared()
        main = git(self.origin, "rev-parse", "main")
        result = migration.publish(self.coordinator)
        self.assertEqual(result["stage"], "published")
        self.assertEqual(git(self.origin, "rev-parse", "main"), main)
        self.assertEqual(git(self.origin, "rev-parse", "chore/demo-review"), prepared["receipt"]["export_head"])
        self.assertEqual(git(self.audit, "rev-parse", prepared["receipt"]["retention_tag"] + "^{}"), prepared["coordinator_head"])
        self.assertEqual(migration.publish(self.coordinator)["stage"], "published")

    def test_advanced_backup_is_not_overwritten(self):
        prepared = self.prepared()
        tree = git(self.origin, "rev-parse", self.head + "^{tree}")
        other = git(self.origin, "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "commit-tree", tree, "-p", self.head, "-m", "chore: later backup work")
        git(self.origin, "update-ref", "refs/heads/chore/demo-review", other, self.head)
        with self.assertRaisesRegex(migration.MigrationError, "backup branch advanced"):
            migration.publish(self.coordinator)
        self.assertEqual(git(self.origin, "rev-parse", "chore/demo-review"), other)

    def test_nonempty_coordinator_remote_is_never_adopted(self):
        self.prepared()
        git(self.coordinator, "push", "-q", "origin", self.head + ":refs/heads/feature/existing")
        with self.assertRaisesRegex(migration.MigrationError, "unowned ref"):
            migration.publish(self.coordinator)
        self.assertEqual(git(self.origin, "rev-parse", "chore/demo-review"), self.head)
        self.assertEqual(git(self.audit, "for-each-ref", "--format=%(refname)"), "refs/heads/feature/existing")

    def test_preparation_rechecks_original_snapshot_before_creating_targets(self):
        (self.source / "app.py").write_text("print(3)\n")
        with self.assertRaisesRegex(migration.MigrationError, "uncommitted changes"):
            self.prepared()
        self.assertFalse(self.coordinator.exists())
        self.assertFalse(self.code.exists())

    def test_projection_tampering_is_rejected(self):
        self.prepared()
        path = self.coordinator / ".project" / migration.RECEIPT
        receipt = json.loads(path.read_text())
        receipt["mapping"][self.head] = receipt["source_base"]
        migration._write(path, receipt)
        with self.assertRaisesRegex(migration.MigrationError, "tree differs"):
            migration.validate(self.coordinator, allow_pending=True)

    def test_original_task_and_review_output_cannot_be_rewritten(self):
        self.prepared()
        task = self.coordinator / self.task_path
        task.write_text(task.read_text().replace("Verify passed", "Verify failed"))
        with self.assertRaisesRegex(migration.MigrationError, "rewritten"):
            migration.validate(self.coordinator, allow_pending=True)

    def test_receipt_cannot_waive_original_artifact_integrity(self):
        self.prepared()
        path = self.coordinator / ".project" / migration.RECEIPT
        receipt = json.loads(path.read_text())
        receipt["artifacts"].pop(self.task_path)
        migration._write(path, receipt)
        with self.assertRaisesRegex(migration.MigrationError, "inventory differs"):
            migration.validate(self.coordinator, allow_pending=True)

    def test_legacy_task_is_assigned_to_code_for_repairs_and_regressions(self):
        self.prepared()
        self.completed_marker()
        tasks = review_findings.load_wave_tasks(self.coordinator / ".project", 1)
        self.assertEqual(tasks["T001"]["repo"], "code")
        task = build_state._parse_task(self.coordinator / self.task_path, self.task_path)
        self.assertEqual(task.repo, "code")
        projection = migration.legacy_proof(self.coordinator, "T001", self.head)
        self.assertEqual(projection["member_commit"], migration.load(self.coordinator)["export_head"])
        self.assertEqual(members._task_members(self.coordinator), {"code"})

    def test_member_patch_can_supersede_and_depend_on_a_migrated_task(self):
        self.prepared()
        fixture = self.root / "task-contracts"
        helper = test_handoffs.HandoffValidationTests()
        helper.write_coverage_task(fixture, "T001", "- None", files="app.py")
        helper.write_coverage_task(fixture, "T002", "- None", files="app.py", wave=2, deps="[T001]")
        tasks = {task: (fixture / f".project/tasks/{task}-demo.md").read_text() for task in ("T001", "T002")}
        tasks["T001"] = tasks["T001"].replace("status: pending", "status: done")
        tasks["T002"] = tasks["T002"].replace("files:\n", "repo: code\nsupersedes: [T001]\nfiles:\n")
        dependencies = check_handoffs._validate_task_graph({1: "full", 2: "full"}, tasks,
                                                           initial=False, repo=self.coordinator)
        self.assertEqual(dependencies["T002"], {"app.py"})

    def test_incomplete_migration_blocks_normal_verification(self):
        prepared = self.prepared()
        with self.assertRaisesRegex(build_state.BuildStateError, "incomplete"):
            build_state.verify_landed_tasks(str(self.coordinator), ".project", prepared["coordinator_head"])

    def test_prepare_is_idempotent_after_success(self):
        first = self.prepared()
        self.assertEqual(self.prepared(), first)

    def test_refuses_missing_ruling_existing_target_or_final_review(self):
        args = (self.source, self.coordinator, self.code, self.audit_url, "sample-orchestrator", "chore/demo-review")
        with self.assertRaisesRegex(migration.MigrationError, "owner ruling"):
            migration.preview(*args, "", self.head)
        self.code.mkdir()
        with self.assertRaisesRegex(migration.MigrationError, "already exists"):
            migration.preview(*args, "Approved.", self.head)
        self.code.rmdir()
        (self.source / ".project/review/FINAL.md").write_text("# Existing final review\n")
        with self.assertRaisesRegex(migration.MigrationError, "precede final-review"):
            migration.preview(*args, "Approved.", self.head)

    def test_failure_to_publish_preservation_never_rewrites_product_backup(self):
        self.prepared()
        original = migration._git
        def fail_tag(repo, *args, **kwargs):
            if repo == self.coordinator and args[0] == "push" and "refs/tags/" in args[-1]:
                raise migration.MigrationError("preservation unavailable")
            return original(repo, *args, **kwargs)
        with mock.patch.object(migration, "_git", side_effect=fail_tag):
            with self.assertRaisesRegex(migration.MigrationError, "preservation unavailable"):
                migration.publish(self.coordinator)
        self.assertEqual(git(self.origin, "rev-parse", "chore/demo-review"), self.head)
        self.assertEqual(migration.publish(self.coordinator)["stage"], "published")

    def test_publication_resumes_after_clean_backup_push_before_final_journal_write(self):
        self.prepared()
        original = migration._write
        def crash(path, value):
            if value.get("stage") == "published":
                raise migration.MigrationError("journal interrupted")
            return original(path, value)
        with mock.patch.object(migration, "_write", side_effect=crash):
            with self.assertRaisesRegex(migration.MigrationError, "journal interrupted"):
                migration.publish(self.coordinator)
        self.assertEqual(migration.publish(self.coordinator)["stage"], "published")

    def test_preserved_graph_survives_fresh_clone_and_gc(self):
        prepared = self.prepared()
        migration.publish(self.coordinator)
        clone = self.root / "restored"
        git(self.root, "clone", "-q", "--no-local", str(self.audit), str(clone))
        tag = prepared["receipt"]["retention_tag"]
        git(clone, "fetch", "-q", "origin", "refs/tags/" + tag)
        git(clone, "checkout", "-q", "-b", "feature/milestone-001", "refs/tags/" + tag)
        git(clone, "reflog", "expire", "--expire=now", "--all")
        git(clone, "gc", "--prune=now")
        self.assertEqual(migration.validate(clone)["status"], "verified")

    def test_migration_does_not_allow_success_criterion_changes(self):
        self.prepared()
        path = self.coordinator / ".project/intent/INTENT.md"
        path.write_text(path.read_text().replace("The app prints two.", "Anything passes."))
        with self.assertRaisesRegex(migration.MigrationError, "success criteria"):
            migration.validate(self.coordinator, allow_pending=True)

    def test_finalization_preserves_local_assets_and_installs_native_bindings(self):
        self.prepared()
        migration.publish(self.coordinator)
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules/local.txt").write_text("local dependency\n")
        (self.source / ".env").write_text("EXAMPLE_VALUE=local\n")
        result = migration.finalize(self.coordinator, Path(__file__).resolve().parents[1])
        self.assertEqual(result["stage"], "complete")
        self.assertFalse(self.source.exists())
        self.assertTrue(Path(result["source_backup"]).is_dir())
        self.assertEqual((self.code / "node_modules/local.txt").read_text(), "local dependency\n")
        self.assertFalse((self.code / ".project").exists())
        self.assertTrue((self.coordinator / ".gsd-path/runtime.json").is_file())
        self.assertEqual(pipeline_state.route_state(self.coordinator)["route"]["action"], "run-phase")
        self.assertEqual(migration.finalize(self.coordinator, Path(__file__).resolve().parents[1])["stage"], "complete")


if __name__ == "__main__":
    unittest.main()
