import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import check_handoffs, isolation, pipeline_state, state_checkpoint
from tests._platform import requires_symlink
from tests.test_pipeline_state import roadmap_text, run_git, state_text
from tests.test_task_briefs import PLAN_WAVE, TASK_TEMPLATE

PIPELINE_STATE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pipeline_state.py"
BRANCH = "feature/milestone-001"
QUESTION = "[RESEARCH] Which domain applies?"
CHARTER = f"# Charter — demo\n\n## Open questions\n- {QUESTION}\n"
EVENT = "program research moved to .project/program"


def manifest(research_dir: str, intent: str = ".project/CHARTER.md") -> str:
    return f"""# Research Handoff

Phase: research
Status: complete
Intent: `{intent}`

## Dispatch

<!-- The settled map .project/research/evidence-codebase-code.md stays with inspect. -->
- `domain` — dispatched → `{research_dir}/evidence-domain.md` — assigned question
- `stack` — dispatched → `{research_dir}/evidence-stack.md` — assigned question
- `pitfalls` — skipped → none — no risk was assigned
- `similar` — skipped → none — no comparison is needed

## Question assignments

- `{QUESTION}` → `domain`, `stack`
"""


def evidence(dimension: str) -> str:
    return f"""# Evidence — {dimension}

Dimension: {dimension}
Questions assigned: Which domain applies?

## Finding: {dimension} finding

- **Claim**: The {dimension} claim is falsifiable.
- **Source**: https://example.test/{dimension}
- **Confidence**: high
- **Why it matters here**: It affects the program charter.

## Assigned questions — answers

- Which domain applies? → The evidence answers this with the cited source.
"""


SYNTHESIS = """# Synthesis

<!-- Evidence: research/evidence-domain.md, .project/research/evidence-stack.md,
     plus the brownfield map research/evidence-codebase-code.md. -->

## Decisions

- **Evidence**: evidence-stack.md § stack finding; see research/evidence-domain.md.
- [Domain finding](./research/evidence-domain.md#domain-finding) links relatively.
- Archived copy: .project/archive/001-first/research/evidence-domain.md stays.
- Already moved: program/evidence-stack.md stays.
"""

MIGRATED_SYNTHESIS = """# Synthesis

<!-- Evidence: program/evidence-domain.md, .project/program/evidence-stack.md,
     plus the brownfield map research/evidence-codebase-code.md. -->

## Decisions

- **Evidence**: evidence-stack.md § stack finding; see program/evidence-domain.md.
- [Domain finding](./program/evidence-domain.md#domain-finding) links relatively.
- Archived copy: .project/archive/001-first/research/evidence-domain.md stays.
- Already moved: program/evidence-stack.md stays.
"""


def make_program_repo(
    tmp: str,
    *,
    phase: str = "define",
    status: str = "done",
    milestone: str = "first",
    commit: bool = True,
) -> Path:
    repo = Path(tmp) / "repo"
    run_git(Path(tmp), "init", "-b", "main", str(repo))
    run_git(repo, "config", "user.name", "GSD Path Test")
    run_git(repo, "config", "user.email", "test@example.com")
    (repo / "product.txt").write_bytes(b"base\n")
    run_git(repo, "add", "product.txt")
    run_git(repo, "commit", "-m", "base")
    run_git(repo, "switch", "-c", BRANCH)
    project = repo / ".project"
    research = project / "research"
    research.mkdir(parents=True)
    (project / "STATE.md").write_bytes(
        state_text(milestone=milestone, phase=phase, status=status, branch=BRANCH).encode("utf-8")
    )
    (project / "CHARTER.md").write_bytes(CHARTER.encode("utf-8"))
    (project / "SYNTHESIS.md").write_bytes(SYNTHESIS.encode("utf-8"))
    (research / "RESEARCH.md").write_bytes(manifest(".project/research").encode("utf-8"))
    for dimension in ("domain", "stack"):
        (research / f"evidence-{dimension}.md").write_bytes(evidence(dimension).encode("utf-8"))
    (research / "evidence-codebase-code.md").write_bytes(b"# Codebase map\n")
    if commit:
        run_git(repo, "add", ".project")
        run_git(repo, "commit", "-m", "roadmap: program roadmap approved")
    return repo


def write_plan(project: Path) -> None:
    (project / "tasks").mkdir(exist_ok=True)
    (project / "tasks" / "T001-new.md").write_bytes(
        TASK_TEMPLATE.format(
            task_id="T001", files_block="  - newpkg/app.py",
            context="Create the requested module.", approach="Implement the module.",
            contract="- None", verify="python3 newpkg/app.py",
        ).encode("utf-8"),
    )
    (project / "plan").mkdir(exist_ok=True)
    (project / "plan" / "PLAN.md").write_bytes(PLAN_WAVE.format(title="first").encode("utf-8"))


class ProgramResearchMigrationTests(unittest.TestCase):
    def test_route_and_transition_block_until_program_research_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            state = repo / ".project" / "STATE.md"
            before = state.read_bytes()

            route = pipeline_state.route_state(repo)["route"]
            self.assertEqual(route["action"], "block")
            self.assertIn(f"migrate-program-research --repo {repo.resolve()} --dry-run", route["reason"])
            handoff = pipeline_state.status_state(repo)["handoff"]
            self.assertIn("migrate-program-research", handoff["next"])
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "migrate-program-research"):
                pipeline_state.transition_state(
                    repo,
                    {"phase": "define", "status": "done", "branch": BRANCH, "archive": None},
                    {"phase": "research", "status": "active"},
                    "research started",
                )
            self.assertEqual(state.read_bytes(), before)

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="research", status="active", milestone="null")
            expected = {"phase": "research", "status": "active", "branch": BRANCH, "archive": None}
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "migrate-program-research"):
                pipeline_state.transition_state(repo, expected, {"status": "done"}, "research complete")
            # A phase may still record that it is blocked.
            pipeline_state.transition_state(
                repo, expected, {"status": "blocked"}, "waiting for the program research migration"
            )

    def test_preview_reports_the_move_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            project = repo / ".project"
            before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}

            preview = pipeline_state.migrate_program_research(repo, dry_run=True)

            self.assertEqual(preview["status"], "preview")
            self.assertEqual(
                preview["moved"],
                [
                    {"from": ".project/research/evidence-domain.md", "to": ".project/program/evidence-domain.md"},
                    {"from": ".project/research/evidence-stack.md", "to": ".project/program/evidence-stack.md"},
                ],
            )
            self.assertEqual(preview["synthesis_references"], 4)
            self.assertEqual(preview["missing"], [])
            self.assertFalse((project / "program").exists())
            self.assertEqual(
                {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}, before
            )

    def test_migration_moves_program_research_and_rewrites_only_its_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            project = repo / ".project"

            result = pipeline_state.migrate_program_research(repo)

            self.assertEqual(result["status"], "migrated")
            program = project / "program"
            # The comment naming the codebase map keeps its research/ path.
            self.assertEqual(
                (program / "RESEARCH.md").read_text(encoding="utf-8"), manifest(".project/program")
            )
            for dimension in ("domain", "stack"):
                self.assertEqual(
                    (program / f"evidence-{dimension}.md").read_text(encoding="utf-8"), evidence(dimension)
                )
                self.assertFalse((project / "research" / f"evidence-{dimension}.md").exists())
            self.assertFalse((project / "research" / "RESEARCH.md").exists())
            self.assertTrue((project / "research" / "evidence-codebase-code.md").is_file())
            self.assertEqual((project / "SYNTHESIS.md").read_text(encoding="utf-8"), MIGRATED_SYNTHESIS)
            log = (project / "STATE.md").read_text(encoding="utf-8")
            self.assertTrue(log.rstrip().endswith(f"— define — {EVENT}"))
            self.assertNotEqual(pipeline_state.route_state(repo)["route"]["action"], "block")

            again = pipeline_state.migrate_program_research(repo)
            self.assertEqual(again["status"], "already-migrated")
            self.assertEqual((project / "STATE.md").read_text(encoding="utf-8").count(EVENT), 1)

    def test_migrated_program_research_passes_its_gate_beside_milestone_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="research", status="active", milestone="null")
            pipeline_state.migrate_program_research(repo)
            program_files = {
                path: path.read_bytes() for path in (repo / ".project" / "program").iterdir()
            }

            self.assertEqual(check_handoffs.validate_research(repo)["dispatched"], ["domain", "stack"])

            project = repo / ".project"
            (project / "intent").mkdir()
            (project / "intent" / "INTENT.md").write_bytes(
                f"# Intent\n\nLane: milestone\n\n## Open questions\n- {QUESTION}\n".encode("utf-8")
            )
            (project / "research" / "RESEARCH.md").write_bytes(
                manifest(".project/research", ".project/intent/INTENT.md").encode("utf-8")
            )
            for dimension in ("domain", "stack"):
                (project / "research" / f"evidence-{dimension}.md").write_bytes(
                    evidence(dimension).replace("finding", "milestone finding").encode("utf-8")
                )

            self.assertEqual(check_handoffs.validate_research(repo)["dispatched"], ["domain", "stack"])
            self.assertEqual(
                {path: path.read_bytes() for path in (project / "program").iterdir()}, program_files
            )
            self.assertEqual(pipeline_state.route_state(repo)["route"]["action"], "run-phase")

    def test_migration_during_research_leaves_missing_evidence_to_the_new_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="research", status="active", milestone="null", commit=False)
            project = repo / ".project"
            (project / "research" / "evidence-stack.md").unlink()

            result = pipeline_state.migrate_program_research(repo)

            self.assertEqual(result["missing"], [".project/program/evidence-stack.md"])
            with self.assertRaisesRegex(check_handoffs.HandoffError, "evidence-stack.md"):
                check_handoffs.validate_research(repo)
            (project / "program" / "evidence-stack.md").write_bytes(evidence("stack").encode("utf-8"))
            self.assertEqual(check_handoffs.validate_research(repo)["dispatched"], ["domain", "stack"])

    def test_rerun_completes_an_interrupted_migration_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            project = repo / ".project"
            program = project / "program"
            program.mkdir()
            (project / "research" / "evidence-domain.md").rename(program / "evidence-domain.md")
            (program / "evidence-stack.md").write_bytes(
                (project / "research" / "evidence-stack.md").read_bytes()
            )
            state = project / "STATE.md"
            state.write_bytes(
                state.read_bytes() + f"- 2026-10-22 — define — {EVENT}\n".encode("utf-8")
            )

            result = pipeline_state.migrate_program_research(repo)

            self.assertEqual(result["status"], "migrated")
            self.assertEqual(result["moved"], [])
            self.assertEqual(result["deduplicated"], [".project/research/evidence-stack.md"])
            self.assertFalse((project / "research" / "evidence-stack.md").exists())
            self.assertFalse((project / "research" / "RESEARCH.md").exists())
            self.assertEqual(state.read_text(encoding="utf-8").count(EVENT), 1)
            self.assertEqual((project / "SYNTHESIS.md").read_text(encoding="utf-8"), MIGRATED_SYNTHESIS)

    def test_migration_refuses_divergent_or_foreign_destination_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            program = repo / ".project" / "program"
            program.mkdir()
            (program / "evidence-domain.md").write_bytes(b"# Different evidence\n")
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "differs at both paths"):
                pipeline_state.migrate_program_research(repo)
            (program / "evidence-domain.md").unlink()
            (program / "notes.md").write_bytes(b"# Notes\n")
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "unexpected files: notes.md"):
                pipeline_state.migrate_program_research(repo)
            self.assertTrue((repo / ".project" / "research" / "RESEARCH.md").is_file())

    def test_migration_discards_its_own_interrupted_temporaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            project = repo / ".project"
            (project / "program").mkdir()
            (project / "program" / ".RESEARCH.md.gsd-path-tmp").write_bytes(b"# partial")
            (project / ".SYNTHESIS.md.gsd-path-tmp").write_bytes(b"# partial")

            pipeline_state.migrate_program_research(repo)

            self.assertEqual(
                (project / "program" / "RESEARCH.md").read_text(encoding="utf-8"),
                manifest(".project/program"),
            )
            self.assertEqual((project / "SYNTHESIS.md").read_text(encoding="utf-8"), MIGRATED_SYNTHESIS)
            self.assertFalse((project / "program" / ".RESEARCH.md.gsd-path-tmp").exists())
            self.assertFalse((project / ".SYNTHESIS.md.gsd-path-tmp").exists())

    def test_migration_requires_the_bound_branch_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            run_git(repo, "switch", "-c", "feature/elsewhere")
            project = repo / ".project"
            before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}

            route = pipeline_state.route_state(repo)["route"]
            self.assertEqual(route["action"], "block")
            self.assertIn("STATE.branch", route["reason"])
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "requires the bound branch"):
                pipeline_state.migrate_program_research(repo)
            self.assertEqual(
                {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}, before
            )

    @requires_symlink
    def test_migration_refuses_a_symlinked_research_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, commit=False)
            outside = Path(tmp) / "outside"
            (repo / ".project" / "research").rename(outside)
            (repo / ".project" / "research").symlink_to(outside, target_is_directory=True)
            before = {path: path.read_bytes() for path in outside.iterdir()}

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "research path must be a real directory"):
                pipeline_state.migrate_program_research(repo)
            self.assertEqual({path: path.read_bytes() for path in outside.iterdir()}, before)
            self.assertFalse((repo / ".project" / "program").exists())

    def test_migration_is_closed_once_the_plan_is_approved(self) -> None:
        for phase, status in (("plan", "done"), ("build", "active")):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                repo = make_program_repo(tmp, phase=phase, status=status)
                with self.assertRaisesRegex(
                    pipeline_state.PipelineStateError, f"first plan approval; STATE.md is {phase}/{status}"
                ):
                    pipeline_state.migrate_program_research(repo)
                self.assertNotIn(
                    "migrate-program-research",
                    json.dumps(pipeline_state.route_state(repo)["route"]),
                )

    def test_a_plan_reopened_after_its_approval_keeps_the_window_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="plan", status="active")
            run_git(
                repo, "commit", "--allow-empty", "-m", "plan: build plan approved",
                "-m", "Why: approved plan checkpoint\nMilestone: first",
            )

            self.assertNotIn("migrate-program-research", json.dumps(pipeline_state.route_state(repo)["route"]))
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "first plan approval"):
                pipeline_state.migrate_program_research(repo)

    def test_initial_binding_precedes_the_block_and_stays_legal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="roadmap", status="done")
            state = repo / ".project" / "STATE.md"
            state.write_bytes(state.read_text(encoding="utf-8").replace(f"branch: {BRANCH}", "branch: null").encode("utf-8"))

            self.assertEqual(pipeline_state.route_state(repo)["route"]["action"], "bind-initial")
            pipeline_state.transition_state(
                repo,
                {"phase": "roadmap", "status": "done", "branch": None, "archive": None},
                {"branch": BRANCH},
                "router bound initial milestone",
            )
            self.assertIn("migrate-program-research", pipeline_state.route_state(repo)["route"]["reason"])
            self.assertEqual(pipeline_state.migrate_program_research(repo)["status"], "migrated")

    def test_deferred_approval_refuses_the_legacy_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="plan", status="active")
            project = repo / ".project"
            write_plan(project)
            (project / "REPOSITORY.md").write_bytes(b"Kind: new-github\nRemote: https://github.com/o/r\n")
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "migrate-program-research"):
                state_checkpoint.defer_approval(repo, "plan")
            self.assertEqual(pipeline_state.load_state(repo)[0].status, "active")

            pipeline_state.migrate_program_research(repo)
            self.assertEqual(state_checkpoint.defer_approval(repo, "plan")["status"], "approved")

    def test_missing_evidence_is_refused_once_research_is_done(self) -> None:
        for phase, status in (("research", "done"), ("roadmap", "active")):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                repo = make_program_repo(tmp, phase=phase, status=status, milestone="null")
                (repo / ".project" / "research" / "evidence-stack.md").unlink()
                with self.assertRaisesRegex(
                    pipeline_state.PipelineStateError, "missing evidence: .project/program/evidence-stack.md"
                ):
                    pipeline_state.migrate_program_research(repo)
                self.assertTrue((repo / ".project" / "research" / "RESEARCH.md").is_file())

    def test_route_and_status_ignore_a_non_file_research_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, commit=False)
            manifest_path = repo / ".project" / "research" / "RESEARCH.md"
            manifest_path.unlink()
            manifest_path.mkdir()

            self.assertNotIn("migrate-program-research", json.dumps(pipeline_state.route_state(repo)["route"]))
            pipeline_state.status_state(repo)

    def test_preview_lists_other_files_that_still_name_old_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            project = repo / ".project"
            (project / "ROADMAP.md").write_bytes(b"# Roadmap\n\nRisk: see research/evidence-stack.md.\n")
            stack = project / "research" / "evidence-stack.md"
            stack.write_bytes(stack.read_bytes() + b"\nSee also .project/research/evidence-domain.md.\n")
            (project / "archive" / "001-first").mkdir(parents=True)
            (project / "archive" / "001-first" / "NOTES.md").write_bytes(b"research/evidence-stack.md\n")

            preview = pipeline_state.migrate_program_research(repo, dry_run=True)

            self.assertEqual(
                preview["unrewritten_references"],
                [
                    {"path": ".project/ROADMAP.md", "references": 1},
                    {"path": ".project/program/evidence-stack.md", "references": 1},
                ],
            )
            self.assertEqual(
                pipeline_state.migrate_program_research(repo)["unrewritten_references"],
                preview["unrewritten_references"],
            )
            self.assertIn(b"research/evidence-stack.md", (project / "ROADMAP.md").read_bytes())

    def test_ignored_os_junk_in_the_program_directory_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            (repo / ".gitignore").write_bytes(b".DS_Store\n")
            (repo / ".project" / "program").mkdir()
            (repo / ".project" / "program" / ".DS_Store").write_bytes(b"junk")
            self.assertEqual(pipeline_state.migrate_program_research(repo)["status"], "migrated")

    def test_migration_requires_a_legacy_program_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            manifest_path = repo / ".project" / "research" / "RESEARCH.md"
            manifest_path.write_bytes(
                manifest(".project/research", ".project/intent/INTENT.md").encode("utf-8")
            )
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "no program research to migrate"):
                pipeline_state.migrate_program_research(repo)

    def test_cli_previews_and_migrates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp)
            command = [sys.executable, "-B", str(PIPELINE_STATE_SCRIPT), "migrate-program-research", "--repo", str(repo)]
            preview = subprocess.run([*command, "--dry-run"], capture_output=True, encoding="utf-8", errors="replace")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(json.loads(preview.stdout)["status"], "preview")
            migrated = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertEqual(json.loads(migrated.stdout)["status"], "migrated")

    def test_roadmap_approval_refuses_legacy_layout_then_commits_the_moved_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="roadmap", status="active", milestone="null", commit=False)
            project = repo / ".project"
            (project / "ROADMAP.md").write_bytes(
                roadmap_text().replace("Status: shipped", "Status: pending", 1).encode("utf-8")
            )
            head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "migrate-program-research"):
                state_checkpoint.checkpoint_approval(repo, "roadmap", head, selected_milestone="first")

            pipeline_state.migrate_program_research(repo)
            state_checkpoint.checkpoint_approval(repo, "roadmap", head, selected_milestone="first")

            tree = run_git(repo, "ls-tree", "-r", "--name-only", "HEAD", ".project").stdout.split()
            self.assertIn(".project/program/RESEARCH.md", tree)
            self.assertIn(".project/program/evidence-stack.md", tree)
            self.assertNotIn(".project/research/RESEARCH.md", tree)
            self.assertIn("program", isolation.PROJECT_ENTRIES)

    def test_plan_approval_commits_a_migration_of_committed_program_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_program_repo(tmp, phase="plan", status="active")
            write_plan(repo / ".project")
            head = run_git(repo, "rev-parse", "HEAD").stdout.strip()

            pipeline_state.migrate_program_research(repo)
            state_checkpoint.checkpoint_approval(repo, "plan", head)

            self.assertEqual(run_git(repo, "status", "--porcelain").stdout, "")
            tree = run_git(repo, "ls-tree", "-r", "--name-only", "HEAD", ".project").stdout.split()
            self.assertIn(".project/program/evidence-domain.md", tree)
            self.assertNotIn(".project/research/evidence-domain.md", tree)
            self.assertIn(".project/research/evidence-codebase-code.md", tree)


if __name__ == "__main__":
    unittest.main()
