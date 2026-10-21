"""D5: every DOCS-AUDIT carry-forward queue survives ship, abandon, and promotion.

Synthetic coordinator `demo` with member audits `web` and `api`; `cli` has no
pending ruling, and `legacy` stands for a member no longer in MEMBERS.md.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import _common, archive_milestone, discussion_validate, pipeline_state, state_promote
from tests import test_archive_milestone as archive_tests
from tests import test_pipeline_state as pipeline_tests
from tests._platform import requires_symlink

ARCHIVE_SCRIPT = archive_tests.ARCHIVE_SCRIPT
PROJECT_ROOT = archive_tests.PROJECT_ROOT
run_git = pipeline_tests.run_git
SHA = "a" * 40


def audit(member=None, *rows):
    """A docs audit; member audits carry the four member header lines."""
    header = (
        f"Repo root: ../{member}\nMember: {member}\nDefault branch: trunk\nMember HEAD: {SHA}\n"
        if member else "Repo root: .\n"
    )
    table = "".join(f"| {' | '.join(row)} |\n" for row in rows)
    return (
        f"# Docs Audit\n\n{header}Audited: 2026-10-21\nAudited HEAD: {SHA}\n\n## User rulings\n\n"
        "| Queue # | Ruling | User's words | Planned |\n|---------|--------|--------------|---------|\n"
        f"{table}\n## Remediation queue\n\n| # | Doc | Claim | Verdict | Status | Fix |\n|---|---|---|---|---|---|\n"
        "| 9 | README.md | x | stale | open | no |\n"
    )


BASE = audit(None, ("1", "fix-doc", '"keep this queued"', "no"))
WEB = audit("web", ("1", "fix-doc", '"web \\| docs"', "no"), ("2", "fix-code", '"done"', "T001"))
API = audit("api", ("1", "fix-doc", '"api one"', "no"), ("2", "fix-code", '"api two"', "no"))
CLI = audit("cli", ("1", "accept-drift", '"fine"', "n/a (accept-drift)"))
LEGACY = audit("legacy", ("1", "fix-doc", '"removed member"', "no"))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def tree(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    } if root.is_dir() else {}


class DocsAuditHelperTests(unittest.TestCase):
    def test_ship_count_and_shared_parser_agree_on_a_padded_rulings_heading(self) -> None:
        # The docs gate accepts trailing blanks after a heading; a pending ruling must still carry.
        with tempfile.TemporaryDirectory() as tmp:
            research = Path(tmp) / "research"
            write(research / "DOCS-AUDIT-web.md", WEB.replace("## User rulings\n", "## User rulings  \n"))
            pending = [row for row in _common.docs_audit_ruling_rows(
                (research / "DOCS-AUDIT-web.md").read_text(encoding="utf-8")) if row[3] == "no"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(archive_milestone.pending_audits(research),
                             [(research / "DOCS-AUDIT-web.md", 1)])

    def test_files_list_base_first_then_member_names_without_a_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research = Path(tmp) / "research"
            self.assertEqual(_common.docs_audit_files(research), [])
            for name in ("DOCS-AUDIT-web.md", "DOCS-AUDIT-a-b.md", "DOCS-AUDIT-a.md", "DOCS-AUDIT.md",
                         "evidence-codebase-web.md", "SYNTHESIS.md", "DOCS-AUDIT.prior.md",
                         ".DOCS-AUDIT-web.md.gsd-path-tmp"):
                write(research / name, "x\n")
            self.assertEqual(
                [path.name for path in _common.docs_audit_files(research)],
                ["DOCS-AUDIT.md", "DOCS-AUDIT-a.md", "DOCS-AUDIT-a-b.md", "DOCS-AUDIT-web.md"],
            )

    def test_files_reject_malformed_names_and_unsafe_paths(self) -> None:
        for name in ("DOCS-AUDIT-Web.md", "docs-audit.md", "DOCS-AUDIT-.md", "DOCS-AUDIT-web_x.md",
                     "DOCS-AUDIT-web.MD"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                write(Path(tmp) / name, "x\n")
                with self.assertRaisesRegex(ValueError, "malformed docs audit filename"):
                    _common.docs_audit_files(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "DOCS-AUDIT-web.md").mkdir()
            with self.assertRaisesRegex(ValueError, "must be a regular file"):
                _common.docs_audit_files(Path(tmp))
            write(Path(tmp) / "file", "x\n")
            with self.assertRaisesRegex(ValueError, "must be a real directory"):
                _common.docs_audit_files(Path(tmp) / "file")

    @requires_symlink
    def test_files_reject_symlinked_audits_and_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            write(outside / "DOCS-AUDIT-web.md", WEB)
            research = Path(tmp) / "research"
            research.mkdir()
            (research / "DOCS-AUDIT-web.md").symlink_to(outside / "DOCS-AUDIT-web.md")
            with self.assertRaisesRegex(ValueError, "must be a regular file"):
                _common.docs_audit_files(research)
            (Path(tmp) / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "must be a real directory"):
                _common.docs_audit_files(Path(tmp) / "linked")

    def test_ruling_rows_split_escaped_pipes_and_number_per_audit(self) -> None:
        self.assertEqual(
            _common.docs_audit_ruling_rows(WEB),
            [["1", "fix-doc", '"web | docs"', "no"], ["2", "fix-code", '"done"', "T001"]],
        )
        self.assertEqual(_common.docs_audit_ruling_rows(BASE)[0][0], "1")
        self.assertEqual(_common.docs_audit_ruling_rows(API)[0][0], "1")
        self.assertEqual(_common.docs_audit_ruling_rows("# Docs Audit\n"), [])
        with self.assertRaisesRegex(ValueError, "4 cells"):
            _common.docs_audit_ruling_rows(BASE.replace('| 1 | fix-doc | "keep this queued" | no |', "| 1 | no |"))
        with self.assertRaisesRegex(ValueError, "repeats section"):
            _common.docs_audit_ruling_rows(BASE + "\n## User rulings\n")

    def test_carry_names_keep_the_coordinator_wording(self) -> None:
        self.assertEqual(archive_milestone.carry_temp_name("DOCS-AUDIT.md"), archive_milestone.CARRY_TEMP_NAME)
        self.assertEqual(archive_milestone.carry_temp_name("DOCS-AUDIT-web.md"), ".DOCS-AUDIT-web.md.gsd-path-tmp")
        self.assertEqual(archive_milestone.carry_label("DOCS-AUDIT.md"), "DOCS-AUDIT")
        self.assertEqual(archive_milestone.carry_label("DOCS-AUDIT-web.md"), "DOCS-AUDIT-web.md")


class MemberAuditArchiveTests(unittest.TestCase):
    # Reuse the archive fixtures without re-running that module's tests here.
    run_command = archive_tests.ArchiveMilestoneTests.run_command
    git = archive_tests.ArchiveMilestoneTests.git
    make_repo = archive_tests.ArchiveMilestoneTests.make_repo
    make_build_repo = archive_tests.ArchiveMilestoneTests.make_build_repo
    prepare_archive = archive_tests.ArchiveMilestoneTests.prepare_archive
    preflight = archive_tests.ArchiveMilestoneTests.preflight
    render_manifest = archive_tests.ArchiveMilestoneTests.render_manifest
    mark_shipped = archive_tests.ArchiveMilestoneTests.mark_shipped
    commit_ship = archive_tests.ArchiveMilestoneTests.commit_ship
    run_abandon = archive_tests.ArchiveMilestoneTests.run_abandon

    def archive_command(self, command: str, repo: Path, *extra: str):
        return self.run_command(sys.executable, str(ARCHIVE_SCRIPT), command, "--repo", str(repo), *extra,
                                cwd=PROJECT_ROOT)

    def member_repo(self, root: Path, base: str = BASE) -> Path:
        self.make_repo(root)
        research = root / ".project" / "research"
        write(research / "DOCS-AUDIT.md", base)
        if base != BASE:
            self.git(root, "add", ".project/research/DOCS-AUDIT.md")
            self.git(root, "commit", "-qm", "fixture: base audit rulings")
            archive_tests.ArchiveMilestoneTests.restamp_final_review(self, root)
        for name, text in (("DOCS-AUDIT-web.md", WEB), ("DOCS-AUDIT-api.md", API), ("DOCS-AUDIT-cli.md", CLI),
                           ("evidence-codebase-web.md", "# Codebase — web\n")):
            write(research / name, text)
        return research

    def ship(self, repo: Path, archive: Path) -> None:
        self.mark_shipped(repo)
        self.git(repo, "add", ".project")
        ship = self.commit_ship(repo, archive)
        self.assertEqual(ship.returncode, 0, ship.stderr)

    def test_multi_audit_ship_carries_pending_set_and_manifest_counts_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            research = self.member_repo(repo)
            prepared = self.archive_command("prepare", repo, "--slug", "demo")
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertEqual(json.loads(prepared.stdout)["carried_forward"], 4)
            archive = repo / ".project" / "archive" / "001-demo"
            self.assertEqual(sorted(tree(research)), ["DOCS-AUDIT-api.md", "DOCS-AUDIT-web.md", "DOCS-AUDIT.md"])
            for name, content in tree(research).items():
                self.assertEqual(content, (archive / "research" / name).read_bytes())
            self.assertTrue((archive / "research" / "DOCS-AUDIT-cli.md").is_file())
            self.assertTrue((archive / "research" / "evidence-codebase-web.md").is_file())

            (archive / "MANIFEST.md").write_bytes("stale\n".encode("utf-8"))
            rendered = self.render_manifest(repo)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            manifest = (archive / "MANIFEST.md").read_text(encoding="utf-8")
            self.assertIn("Carried forward: 4 DOCS-AUDIT ruling(s)", manifest)
            self.assertEqual(discussion_validate.manifest_carry_text(archive), "4 DOCS-AUDIT ruling(s)")
            (archive / "MANIFEST.md").write_bytes(
                manifest.replace("Carried forward: 4", "Carried forward: 1").encode("utf-8"))
            stale = self.preflight(repo)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("manifest carried-forward count does not match", stale.stderr)
            (archive / "MANIFEST.md").write_bytes(manifest.encode("utf-8"))
            checked = self.preflight(repo)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)["carried_forward"], 4)

            self.ship(repo, archive)
            archived = tree(archive)
            validated = self.archive_command("validate", repo)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(tree(archive), archived)
            for name in ("DOCS-AUDIT-api.md", "DOCS-AUDIT-web.md"):
                self.assertEqual(
                    self.git(repo, "show", f"HEAD:.project/research/{name}").stdout,
                    (archive / "research" / name).read_text(encoding="utf-8"),
                )

    def test_prepare_resumes_partial_multi_audit_carry_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            research = self.member_repo(repo)
            archive = self.prepare_archive(repo)
            expected = tree(research)
            archived = tree(archive)
            # Crash after copying DOCS-AUDIT.md and web, while api was being written.
            (research / "DOCS-AUDIT-api.md").unlink()
            write(research / ".DOCS-AUDIT-api.md.gsd-path-tmp", "partial\n")

            retry = self.archive_command("prepare", repo, "--slug", "demo")

            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)["carried_forward"], 4)
            self.assertEqual(tree(research), expected)
            self.assertFalse((research / ".DOCS-AUDIT-api.md.gsd-path-tmp").exists())
            self.assertEqual(tree(archive), archived)

    def test_prepare_rejects_extra_or_altered_active_audit(self) -> None:
        cases = {
            "altered": ("DOCS-AUDIT-web.md", WEB.replace('"done"', '"changed"')),
            "not-pending": ("DOCS-AUDIT-cli.md", CLI),
            "unknown-temporary": (".DOCS-AUDIT-ghost.md.gsd-path-tmp", "partial\n"),
            "unrelated": ("notes.md", "x\n"),
        }
        for label, (name, text) in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                research = self.member_repo(repo)
                archive = self.prepare_archive(repo)
                archived = tree(archive)
                write(research / name, text)

                retry = self.archive_command("prepare", repo, "--slug", "demo")

                self.assertNotEqual(retry.returncode, 0)
                self.assertIn("archive collision", retry.stderr)
                self.assertEqual((research / name).read_bytes(), text.encode("utf-8"))
                self.assertEqual(tree(archive), archived)

    def test_clean_root_requires_the_complete_exact_carry_set(self) -> None:
        cases = {
            "missing-member": (lambda research: (research / "DOCS-AUDIT-api.md").unlink(),
                               "pending DOCS-AUDIT-api.md rulings lack an exact active carry-forward copy"),
            "altered-member": (lambda research: write(research / "DOCS-AUDIT-web.md", WEB + "\n"),
                               "pending DOCS-AUDIT-web.md rulings lack an exact active carry-forward copy"),
            "extra-file": (lambda research: write(research / "notes.md", "x\n"),
                           "pending DOCS-AUDIT rulings lack an exact active carry-forward copy"),
        }
        for label, (mutate, message) in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                research = self.member_repo(repo)
                self.prepare_archive(repo)
                self.assertEqual(self.render_manifest(repo).returncode, 0)
                mutate(research)

                checked = self.preflight(repo)

                self.assertNotEqual(checked.returncode, 0)
                self.assertIn(message, checked.stderr)

    def test_member_queue_survives_registry_removal(self) -> None:
        settled = audit(None, ("1", "fix-doc", '"keep this queued"', "T001"))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            research = self.member_repo(repo, base=settled)
            for name in ("DOCS-AUDIT-web.md", "DOCS-AUDIT-api.md"):
                (research / name).unlink()
            write(research / "DOCS-AUDIT-legacy.md", LEGACY)
            self.assertFalse((repo / ".project" / "MEMBERS.md").exists())

            prepared = self.archive_command("prepare", repo, "--slug", "demo")

            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertEqual(json.loads(prepared.stdout)["carried_forward"], 1)
            self.assertEqual(sorted(tree(research)), ["DOCS-AUDIT-legacy.md"])
            self.assertEqual(tree(research)["DOCS-AUDIT-legacy.md"], LEGACY.encode("utf-8"))
            self.assertEqual(self.render_manifest(repo).returncode, 0)
            archive = repo / ".project" / "archive" / "001-demo"
            self.assertIn("Carried forward: 1 DOCS-AUDIT ruling(s)", (archive / "MANIFEST.md").read_text(encoding="utf-8"))
            checked = self.preflight(repo)
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_ignored_later_member_carry_fails_preflight_and_committed_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.member_repo(repo)
            carry_path = ".project/research/DOCS-AUDIT-web.md"
            # DOCS-AUDIT.md is tracked; the later web copy is ignored.
            self.assertEqual(self.git(repo, "ls-files", "--error-unmatch", ".project/research/DOCS-AUDIT.md").returncode, 0)
            (repo / ".git" / "info" / "exclude").write_bytes(f"/{carry_path}\n".encode("utf-8"))
            archive = self.prepare_archive(repo)
            self.assertEqual(self.render_manifest(repo).returncode, 0)

            checked = self.preflight(repo)

            self.assertNotEqual(checked.returncode, 0)
            self.assertIn(f"ignored active carry-forward is not tracked and would be omitted: {carry_path}",
                          checked.stderr)
            self.ship(repo, archive)
            validated = self.archive_command("validate", repo)
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("committed carry-forward", validated.stderr)

    def test_committed_later_member_carry_with_different_bytes_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            research = self.member_repo(repo)
            archive = self.prepare_archive(repo)
            self.assertEqual(self.render_manifest(repo).returncode, 0)
            active = research / "DOCS-AUDIT-api.md"
            active.write_bytes("different committed queue\n".encode("utf-8"))
            self.ship(repo, archive)
            active.write_bytes((archive / "research" / "DOCS-AUDIT-api.md").read_bytes())
            self.git(repo, "update-index", "--assume-unchanged", ".project/research/DOCS-AUDIT-api.md")

            validated = self.archive_command("validate", repo)

            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("committed carry-forward differs from the archived DOCS-AUDIT-api.md", validated.stderr)

    @requires_symlink
    def test_member_carry_rejects_symlinked_audit_before_moving_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            research = self.member_repo(repo)
            outside = Path(tmp) / "outside.md"
            outside.write_bytes(WEB.encode("utf-8"))
            (research / "DOCS-AUDIT-web.md").unlink()
            (research / "DOCS-AUDIT-web.md").symlink_to(outside)
            state = (repo / ".project" / "STATE.md").read_bytes()

            prepared = self.archive_command("prepare", repo, "--slug", "demo")

            self.assertNotEqual(prepared.returncode, 0)
            self.assertIn("docs audit must be a regular file", prepared.stderr)
            self.assertEqual((repo / ".project" / "STATE.md").read_bytes(), state)
            self.assertFalse((repo / ".project" / "archive" / "001-demo").exists())
            self.assertTrue((research / "SYNTHESIS.md").is_file())

    def test_abandon_and_completed_retry_carry_every_pending_member_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.make_build_repo(repo)
            research = repo / ".project" / "research"
            for name, text in (("DOCS-AUDIT.md", BASE), ("DOCS-AUDIT-web.md", WEB), ("DOCS-AUDIT-api.md", API),
                               ("DOCS-AUDIT-cli.md", CLI), ("evidence-codebase-api.md", "# Codebase — api\n")):
                write(research / name, text)
            self.git(repo, "add", ".project")
            self.assertEqual(self.git(repo, "commit", "-qm", "fixture: member audits").returncode, 0)

            first = self.run_abandon(repo)
            retry = self.run_abandon(repo)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["carried_forward"], 4)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)["status"], "already-complete")
            self.assertEqual(json.loads(retry.stdout)["carried_forward"], 4)
            archive = repo / ".project" / "archive" / "001-demo"
            self.assertEqual(sorted(tree(research)), ["DOCS-AUDIT-api.md", "DOCS-AUDIT-web.md", "DOCS-AUDIT.md"])
            for name, content in tree(research).items():
                self.assertEqual(content, (archive / "research" / name).read_bytes())
                self.assertEqual(self.git(repo, "show", f"HEAD:.project/research/{name}").stdout,
                                 content.decode("utf-8"))
            self.assertTrue((archive / "research" / "DOCS-AUDIT-cli.md").is_file())


class MemberAuditPromotionTests(unittest.TestCase):
    _promotion_repo = pipeline_tests.PipelineStateTests._promotion_repo

    ARCHIVED = {"DOCS-AUDIT.md": BASE, "DOCS-AUDIT-web.md": WEB, "DOCS-AUDIT-api.md": API,
                "DOCS-AUDIT-cli.md": CLI}
    CARRIED = ("DOCS-AUDIT.md", "DOCS-AUDIT-web.md", "DOCS-AUDIT-api.md")

    def promotion_repo(self, tmp: str, lookahead, archived=None, carried=None):
        """A shipped milestone whose carry set sits in the promotion base commit."""
        archived = self.ARCHIVED if archived is None else archived
        carried = self.CARRIED if carried is None else carried
        repo, landing = self._promotion_repo(tmp, drift=False)
        project = repo / ".project"
        for name, text in archived.items():
            write(project / "archive" / "001-first" / "research" / name, text)
        write(project / "archive" / "001-first" / "research" / "evidence-codebase-web.md", "# Codebase — web\n")
        for name in carried:
            write(project / "research" / name, archived[name])
        if lookahead is not None:
            for name, text in lookahead.items():
                write(project / "next" / "research" / name, text)
        run_git(repo, "add", "-A", "--", ".project")
        run_git(repo, "commit", "-m", "fixture: shipped carry-forward set")
        base = run_git(repo, "rev-parse", "HEAD").stdout.strip()
        run_git(repo, "update-ref", "refs/remotes/origin/main", base)
        return repo, base, landing

    def promote(self, repo: Path, base: str, landing: str):
        return state_promote.promote_next(repo, "second", "feature/milestone-002", base, landing)

    def kept_lookahead(self):
        return {
            "DOCS-AUDIT.md": BASE.replace('"keep this queued" | no', '"keep this queued" | T003'),
            "DOCS-AUDIT-web.md": WEB.replace(
                '| "done" | T001 |\n', '| "done" | T001 |\n| 3 | fix-doc | "new web" | no |\n'),
            "DOCS-AUDIT-api.md": API.replace('"api one" | no', '"api one" | T004'),
            "evidence-codebase-api.md": "# Codebase — api\n",
        }

    def test_promote_member_audits_preserves_each_ruling_history(self) -> None:
        lookahead = self.kept_lookahead()
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, landing = self.promotion_repo(tmp, lookahead)
            archive = repo / ".project" / "archive" / "001-first"
            archived = tree(archive)

            result = self.promote(repo, base, landing)
            repeat = self.promote(repo, base, landing)

            self.assertEqual(result["status"], "promoted")
            self.assertEqual(repeat["status"], "already-complete")
            self.assertEqual(tree(repo / ".project" / "research"),
                             {name: text.encode("utf-8") for name, text in lookahead.items()})
            self.assertEqual(tree(archive), archived)
            self.assertEqual(run_git(repo, "status", "--porcelain").stdout, "")

    def test_promote_missing_member_counterpart_blocks_before_mutation(self) -> None:
        lookahead = self.kept_lookahead()
        del lookahead["DOCS-AUDIT-api.md"]
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, landing = self.promotion_repo(tmp, lookahead)
            before = tree(repo / ".project")

            with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                        "lookahead DOCS-AUDIT-api.md is missing for pending carry-forward"):
                self.promote(repo, base, landing)

            self.assertEqual(tree(repo / ".project"), before)
            self.assertFalse(pipeline_state._git_path(repo, "gsd-path-promote-next.json").exists())

    def test_promote_one_member_dropped_or_reordered_ruling_keeps_all_sources(self) -> None:
        dropped = WEB.replace('| 2 | fix-code | "done" | T001 |\n', "")
        reordered = WEB.replace(
            '| 1 | fix-doc | "web \\| docs" | no |\n| 2 | fix-code | "done" | T001 |\n',
            '| 2 | fix-code | "done" | T001 |\n| 1 | fix-doc | "web \\| docs" | no |\n',
        )
        self.assertNotEqual(reordered, WEB)
        for label, text, number in (("dropped", dropped, "2"), ("reordered", reordered, "1")):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                lookahead = self.kept_lookahead()
                lookahead["DOCS-AUDIT-web.md"] = text
                repo, base, landing = self.promotion_repo(tmp, lookahead)
                before = tree(repo / ".project")

                with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                            f"lookahead DOCS-AUDIT-web.md does not keep carry-forward ruling #{number}"):
                    self.promote(repo, base, landing)

                self.assertEqual(tree(repo / ".project"), before)

    def test_promote_rejects_rewritten_words_or_illegal_planned_value(self) -> None:
        cases = {
            "rewritten-words": (API.replace('"api one"', '"api uno"'),
                                "does not keep carry-forward ruling #1"),
            "done": (API.replace('"api one" | no', '"api one" | done'), "does not keep carry-forward ruling #1"),
            "accept-drift": (API.replace('"api one" | no', '"api one" | n/a (accept-drift)'),
                             "does not keep carry-forward ruling #1"),
            "reused-number": (API.replace('"api two" | no |\n', '"api two" | no |\n| 2 | fix-doc | "again" | no |\n'),
                              "reuses carry-forward ruling #2"),
            "other-member": (API.replace("Member: api", "Member: web"), "belongs to a different audit identity"),
        }
        for label, (text, message) in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                lookahead = self.kept_lookahead()
                lookahead["DOCS-AUDIT-api.md"] = text
                repo, base, landing = self.promotion_repo(tmp, lookahead)
                before = tree(repo / ".project")

                with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                            f"lookahead DOCS-AUDIT-api.md {message}"):
                    self.promote(repo, base, landing)

                self.assertEqual(tree(repo / ".project"), before)

    def test_promote_retains_carry_when_no_source_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, landing = self.promotion_repo(tmp, None)
            carried = tree(repo / ".project" / "research")

            result = self.promote(repo, base, landing)
            repeat = self.promote(repo, base, landing)

            self.assertEqual(result["status"], "promoted")
            self.assertEqual(repeat["status"], "already-complete")
            self.assertEqual(tree(repo / ".project" / "research"), carried)

    def test_promote_passive_removed_member_audit_and_repeat_is_complete(self) -> None:
        archived = {"DOCS-AUDIT-legacy.md": LEGACY, "DOCS-AUDIT-cli.md": CLI}
        lookahead = {"DOCS-AUDIT-legacy.md": LEGACY, "DOCS-AUDIT-api.md": API,
                     "evidence-codebase-api.md": "# Codebase — api\n"}
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, landing = self.promotion_repo(
                tmp, lookahead, archived=archived, carried=("DOCS-AUDIT-legacy.md",))
            self.assertFalse((repo / ".project" / "MEMBERS.md").exists())

            result = self.promote(repo, base, landing)
            repeat = self.promote(repo, base, landing)

            self.assertEqual(result["status"], "promoted")
            self.assertEqual(repeat["status"], "already-complete")
            changed = run_git(repo, "diff", "--name-only", base, "HEAD").stdout.splitlines()
            self.assertNotIn(".project/research/DOCS-AUDIT-legacy.md", changed)
            self.assertIn(".project/research/DOCS-AUDIT-api.md", changed)
            self.assertEqual(tree(repo / ".project" / "research"),
                             {name: text.encode("utf-8") for name, text in lookahead.items()})

    def test_promote_member_carry_recovery_rejects_source_or_destination_drift(self) -> None:
        journal_name = "gsd-path-promote-next.json"
        with self.subTest(case="destination drift before removal"), tempfile.TemporaryDirectory() as tmp:
            repo, base, landing = self.promotion_repo(tmp, self.kept_lookahead())
            with mock.patch.object(state_promote, "_resume_track_moves", side_effect=RuntimeError("crash")):
                with self.assertRaises(RuntimeError):
                    self.promote(repo, base, landing)
            active = repo / ".project" / "research" / "DOCS-AUDIT-api.md"
            write(active, API + "\n")

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "carry-forward drifted during promotion"):
                self.promote(repo, base, landing)
            self.assertEqual(active.read_text(encoding="utf-8"), API + "\n")
            self.assertTrue((repo / ".project" / "next" / "research").is_dir())

        with self.subTest(case="source drift before removal"), tempfile.TemporaryDirectory() as tmp:
            repo, base, landing = self.promotion_repo(tmp, self.kept_lookahead())
            carried = tree(repo / ".project" / "research")
            with mock.patch.object(state_promote, "_resume_track_moves", side_effect=RuntimeError("crash")):
                with self.assertRaises(RuntimeError):
                    self.promote(repo, base, landing)
            write(repo / ".project" / "next" / "research" / "DOCS-AUDIT-web.md", WEB)

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "promotion track drifted: research"):
                self.promote(repo, base, landing)
            # The carried set is not deleted when the source cannot be proved.
            self.assertEqual(tree(repo / ".project" / "research"), carried)

        with self.subTest(case="source drift after removal"), tempfile.TemporaryDirectory() as tmp:
            lookahead = self.kept_lookahead()
            repo, base, landing = self.promotion_repo(tmp, lookahead)
            source = repo / ".project" / "next" / "research"
            rename = Path.rename

            def interrupted(path, target):
                if path == source:
                    raise OSError("crash before research rename")
                return rename(path, target)

            with mock.patch.object(Path, "rename", interrupted):
                with self.assertRaises(OSError):
                    self.promote(repo, base, landing)
            journal = pipeline_state._git_path(repo, journal_name)
            self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["stage"], "removed:carry-forward")
            self.assertFalse((repo / ".project" / "research").exists())
            write(source / "DOCS-AUDIT-web.md", WEB)

            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "promotion track drifted: research"):
                self.promote(repo, base, landing)
            self.assertFalse((repo / ".project" / "research").exists())

            write(source / "DOCS-AUDIT-web.md", lookahead["DOCS-AUDIT-web.md"])
            result = self.promote(repo, base, landing)
            self.assertEqual(result["status"], "promoted")
            self.assertEqual(tree(repo / ".project" / "research"),
                             {name: text.encode("utf-8") for name, text in lookahead.items()})
            self.assertEqual(
                run_git(repo, "show", "HEAD:.project/archive/001-first/research/DOCS-AUDIT-web.md").stdout, WEB)


if __name__ == "__main__":
    unittest.main()
