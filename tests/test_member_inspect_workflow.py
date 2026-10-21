"""Member-aware inspect workflow: prepare -> advance -> finish over a real coordinator and members.

Children are stubbed by writing their outputs into the artifact sidecars the
runtime prepared. Coordinator `acme` and members `web`/`api`/`site` are
synthetic, each member with a bare origin whose default branch is `trunk`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Sequence
from unittest import mock

from scripts import isolation, pipeline_state
from scripts import members as members_module

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "workflow_run.py"
BRANCH = "feature/milestone-001"
DEFAULT = "trunk"
RULING = "| 1 | fix-doc | Keep the README honest. | no |"


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
         *arguments], cwd=repo, encoding="utf-8", errors="replace", capture_output=True, check=check,
    ).stdout.strip()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def state_text(phase: str = "inspect", status: str = "active", branch: Optional[str] = BRANCH,
               milestone: str = "demo") -> str:
    return (
        "---\npipeline: gsd-path/v2\nproject: acme\n"
        f"milestone: {milestone}\nphase: {phase}\nstatus: {status}\n"
        f"branch: {branch or 'null'}\narchive: null\n---\n\n"
        f"# Project State\n\n## Log\n\n- 2026-10-21 — {phase} — handoff ready\n"
    )


def audit_text(root: str, head: str, inventory: Sequence[str], rulings: Sequence[str] = (),
               member: Optional[str] = None, pin: Optional[str] = None, default: str = DEFAULT) -> str:
    """A valid descriptive-only docs audit; member audits add the four member header lines."""
    header = [f"Repo root: {root}"]
    if member is not None:
        header += [f"Member: {member}", f"Default branch: {default}", f"Member HEAD: {pin or head}"]
    lines = [
        "# Docs Audit", "", *header, "Audited: 2026-10-21", f"Audited HEAD: {head}", "Alignment mode: no", "",
        "## Summary", "", "| Verdict | Count |", "|---------|-------|", "| verified | 0 |", "| stale | 0 |",
        "| aspirational | 0 |", "| unverifiable | 0 |",
        f"| descriptive docs (no testable claims) | {len(inventory)} |", "", "Worst drift: none found.", "",
        "## Descriptive docs", "", *(f"- {doc}" for doc in inventory), "",
        "## User rulings", "", "| Queue # | Ruling | User's words | Planned |",
        "|---------|--------|--------------|---------|", *rulings, "",
        "## Remediation queue", "", "| # | Doc | Claim | Verdict | Class | Suggested action |",
        "|---|-----|-------|---------|-------|------------------|", "",
    ]
    return "\n".join(lines)


def map_text(root: str, member: Optional[str] = None, pin: Optional[str] = None, default: str = DEFAULT) -> str:
    header = f"Repo root: {root}\n"
    if member is not None:
        header += f"Member: {member}\nDefault branch: {default}\nMember HEAD: {pin}\n"
    return (f"# Evidence — codebase\n\n{header}Scanned: 2026-10-21\n\n## Map\n\n- **Stack**: Python service.\n\n"
            "## Findings\n\nNone: no surprises.\n")


class MemberInspectFixture:
    """A coordinator `acme` with members whose teams moved on and left dirty trees."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {"GSD_PATH_WORKTREE_ROOT": str(self.base / "workspace")})
        environment.start()
        self.addCleanup(environment.stop)
        self.coordinator = self.base / "acme"
        self.checkouts = {}
        self.pins = {}

    def build(self, names: Sequence[str] = ("web",), brownfield: bool = False,
              state: Optional[str] = None) -> None:
        coordinator = self.coordinator
        git(self.base, "init", "-q", "-b", BRANCH, str(coordinator))
        git(coordinator, "config", "gc.autoDetach", "false")
        write(coordinator / "README.md", "# acme program\n\nCoordinates the acme services.\n" if brownfield
              else "# acme\n")
        if brownfield:
            write(coordinator / "src" / "app.py", "print('acme')\n")
        write(coordinator / ".project" / "STATE.md", state_text("define", "active"))
        git(coordinator, "add", "-A")
        git(coordinator, "commit", "-q", "-m", "init")
        for name in names:
            self.checkouts[name] = self.member_repository(name)
            members_module.add_member(coordinator, name, self.checkouts[name], "default")
        write(coordinator / ".project" / "STATE.md", state or state_text())
        git(coordinator, "add", "-A")
        git(coordinator, "commit", "-q", "-m", "record members")
        self.head = git(coordinator, "rev-parse", "HEAD")
        self.pins = {name: git(checkout, "rev-parse", f"refs/remotes/origin/{DEFAULT}")
                     for name, checkout in self.checkouts.items()}
        for checkout in self.checkouts.values():
            # The team moves on and leaves its tree dirty; inspection reads only the pin.
            write(checkout / "TEAM.md", "# Team\n\nWork in progress on the team checkout.\n")
            git(checkout, "add", "-A")
            git(checkout, "commit", "-q", "-m", "team work")
            write(checkout / "app.py", "dirty = True\n")
            write(checkout / "notes.txt", "untracked\n")
        self.snapshots = {name: self.snapshot(name) for name in self.checkouts}

    def member_repository(self, name: str) -> Path:
        bare = self.base / "remotes" / f"{name}.git"
        git(self.base, "init", "-q", "--bare", "-b", DEFAULT, str(bare))
        checkout = self.base / f"{name}-checkout"
        checkout.mkdir()
        git(checkout, "init", "-q", "-b", DEFAULT)
        git(checkout, "config", "gc.autoDetach", "false")
        write(checkout / "README.md", f"# {name}\n\nThe {name} service.\n")
        write(checkout / "app.py", "pinned = True\n")
        write(checkout / ".gitignore", "*.log\n")
        git(checkout, "add", "-A")
        git(checkout, "commit", "-q", "-m", "init")
        remote = f"https://github.com/acme/{name}.git"
        git(checkout, "remote", "add", "origin", remote)
        git(checkout, "config", f"url.{bare}.insteadOf", remote)
        git(checkout, "push", "-q", "origin", DEFAULT)
        git(checkout, "fetch", "-q", "origin")
        git(checkout, "remote", "set-head", "origin", DEFAULT)
        return checkout

    def advance_origin(self, name: str, relative: str = "later.py") -> str:
        """Publish a new commit on the member origin and fetch it, as the inspect skill would."""
        bare = self.base / "remotes" / f"{name}.git"
        clone = self.base / f"{name}-publisher"
        if not clone.exists():
            git(self.base, "clone", "-q", "-b", DEFAULT, str(bare), str(clone))
        write(clone / relative, "later = True\n")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "later")
        git(clone, "push", "-q", "origin", DEFAULT)
        git(self.checkouts[name], "fetch", "-q", "origin")
        return git(self.checkouts[name], "rev-parse", f"refs/remotes/origin/{DEFAULT}")

    def publish_tree(self, name: str, files: dict, links: dict) -> str:
        """Commit regular files and symlink entries on the member origin, then fetch the new pin.

        Symlinks are written with Git plumbing, so no host symlink support is needed."""
        bare = self.base / "remotes" / f"{name}.git"
        clone = self.base / f"{name}-publisher"
        if not clone.exists():
            git(self.base, "clone", "-q", "-b", DEFAULT, str(bare), str(clone))
        for relative, text in files.items():
            write(clone / relative, text)
            git(clone, "add", relative)
        for relative, target in links.items():
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=clone, input=target,
                                  capture_output=True, encoding="utf-8", check=True).stdout.strip()
            git(clone, "update-index", "--add", "--cacheinfo", f"120000,{blob},{relative}")
        git(clone, "commit", "-q", "-m", "publish tree")
        git(clone, "push", "-q", "origin", DEFAULT)
        git(self.checkouts[name], "fetch", "-q", "origin")
        self.pins[name] = git(self.checkouts[name], "rev-parse", f"refs/remotes/origin/{DEFAULT}")
        self.snapshots[name] = self.snapshot(name)
        return self.pins[name]

    # Observation.

    def snapshot(self, name: str) -> dict:
        checkout = self.checkouts[name]
        return {
            "head": git(checkout, "rev-parse", "HEAD"),
            "branch": git(checkout, "branch", "--show-current"),
            "status": git(checkout, "status", "--porcelain", "--untracked-files=all"),
            "app": (checkout / "app.py").read_bytes(),
            "refs": git(checkout, "for-each-ref", "--format=%(refname) %(objectname)"),
            "remote": git(self.base / "remotes" / f"{name}.git", "for-each-ref",
                          "--format=%(refname) %(objectname)"),
            "worktrees": git(checkout, "worktree", "list", "--porcelain").count("worktree "),
        }

    def assert_untouched(self, *names: str) -> None:
        for name in names or tuple(self.checkouts):
            self.assertEqual(self.snapshot(name), self.snapshots[name], name)
        self.assertFalse(os.path.lexists(self.coordinator / members_module.LOCK_PATH))

    def assert_no_sidecars(self) -> None:
        self.assertEqual(git(self.coordinator, "for-each-ref", "--format=%(refname)", "refs/heads"),
                         f"refs/heads/{BRANCH}")
        self.assertEqual(git(self.coordinator, "worktree", "list", "--porcelain").count("worktree "), 1)
        for checkout in self.checkouts.values():
            self.assertEqual(git(checkout, "worktree", "list", "--porcelain").count("worktree "), 1)
            self.assertNotIn("inspect", git(checkout, "for-each-ref", "--format=%(refname)", "refs/heads"))

    def state(self, project_dir: str = ".project") -> dict:
        return pipeline_state.validate_state(self.coordinator, project_dir)["state"]

    # Commands and stubbed children.

    def run_cli(self, action: str, *arguments: str, project_dir: Optional[str] = None,
                receipt: Optional[dict] = None, head: Optional[str] = None, reviewed: bool = True):
        command = [sys.executable, "-B", str(SCRIPT), action, "--repo", str(self.coordinator),
                   "--expected-head", head or self.head]
        if project_dir:
            command += ["--project-dir", project_dir]
        if receipt is not None:
            command += ["--inspection", receipt["receipt_file"]]
            if reviewed:
                command.append("--mapper-reviewed")
        completed = subprocess.run(command + list(arguments), capture_output=True, encoding="utf-8",
                                   errors="replace")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            self.fail(f"{action} printed no JSON: {completed.stdout!r} {completed.stderr!r}")
        return completed, payload

    def complete(self, action: str, **options) -> dict:
        completed, payload = self.run_cli(action, **options)
        self.assertEqual(payload["status"], "complete", f"{payload.get('reason')} {completed.stderr}")
        self.assertEqual(completed.returncode, 0)
        return payload

    def blocked(self, action: str, **options) -> dict:
        completed, payload = self.run_cli(action, **options)
        self.assertEqual(payload["status"], "blocked", completed.stdout)
        self.assertNotEqual(completed.returncode, 0)
        return payload

    @staticmethod
    def assignment(receipt: dict, task_name: str) -> dict:
        return next(item for item in receipt["assignments"] if item["task_name"] == task_name)

    def write_map(self, receipt: dict, name: str, pin: Optional[str] = None) -> Path:
        mapper = self.assignment(receipt, f"inspect_codebase_{name}")
        return write(Path(mapper["worktree"]) / mapper["output"],
                     map_text(str(self.checkouts[name]), name, pin or self.pins[name]))

    def write_audit(self, receipt: dict, name: str, pin: Optional[str] = None,
                    inventory: Optional[Sequence[str]] = None, rulings: Sequence[str] = (),
                    audited: Optional[str] = None) -> Path:
        auditor = self.assignment(receipt, f"inspect_docs_{name}")
        member = next(item for item in receipt["members"] if item["name"] == name)
        if inventory is None:
            inventory = Path(member["inventory_file"]).read_text(encoding="utf-8").splitlines()
        pin = pin or member["pin"]
        return write(Path(auditor["worktree"]) / auditor["output"],
                     audit_text(str(self.checkouts[name]), audited or pin, inventory, rulings, name, pin))

    def write_coordinator_pair(self, receipt: dict) -> None:
        mapper = self.assignment(receipt, "inspect_codebase")
        write(Path(mapper["worktree"]) / mapper["output"], map_text(str(self.coordinator)))
        docs = self.assignment(receipt, "inspect_docs")
        inventory = Path(receipt["inventory_file"]).read_text(encoding="utf-8").splitlines()
        write(Path(docs["worktree"]) / docs["output"], audit_text(str(self.coordinator), self.head, inventory))


class MemberInspectWorkflowTests(MemberInspectFixture, unittest.TestCase):
    def test_greenfield_coordinator_dispatches_only_member_work_and_finishes_clean(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertEqual(prepared["dispatch"], ["inspect_codebase_web"])
        self.assertFalse(prepared["coordinator"]["assigned"])
        self.assertEqual(prepared["coordinator"]["verdict"], "greenfield")
        self.assertIsNone(prepared["inventory_file"])
        self.assertEqual(prepared["stage"], 1)
        self.assertEqual(prepared["project_dir"], ".project")
        self.assertFalse(str(Path(prepared["receipt_file"])).startswith(str(self.coordinator / ".project")))
        mapper = self.assignment(prepared, "inspect_codebase_web")
        sidecar = Path(mapper["member_sidecar"]["worktree"])
        self.assertEqual(sidecar, Path(mapper["worktree"]).parent / "web")
        self.assertEqual(git(sidecar, "rev-parse", "HEAD"), self.pins["web"])
        self.assertFalse((sidecar / "TEAM.md").exists())
        self.assertEqual((sidecar / "app.py").read_bytes(), b"pinned = True\n")
        self.assertEqual(mapper["output"], ".project/research/evidence-codebase-web.md")
        brief = Path(mapper["brief_file"]).read_text(encoding="utf-8")
        for line in (f"Member code, read-only at the pin, and command cwd: {sidecar}",
                     "sibling ../web", f"Write only: {Path(mapper['worktree']) / mapper['output']}",
                     f"Repo root: {self.checkouts['web']}\n", "Member: web\n", f"Default branch: {DEFAULT}\n",
                     f"Member HEAD: {self.pins['web']}\n", "codebase-mapper.md", "templates/codebase.md"):
            self.assertIn(line, brief)
        self.assertEqual(git(self.coordinator, "worktree", "list", "--porcelain").count("worktree "), 2)

        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.assertEqual((advanced["stage"], advanced["dispatch"]), (2, ["inspect_docs_web"]))
        auditor = self.assignment(advanced, "inspect_docs_web")
        self.assertEqual(Path(auditor["member_sidecar"]["worktree"]), sidecar)
        self.assertEqual(git(sidecar, "rev-parse", "HEAD"), self.pins["web"])
        self.assertTrue(Path(mapper["worktree"]).is_dir(), "the mapper artifact is kept for collection")
        member = advanced["members"][0]
        self.assertEqual(Path(member["inventory_file"]).read_text(encoding="utf-8"), "README.md\n")
        brief = Path(auditor["brief_file"]).read_text(encoding="utf-8")
        for line in (f"Audited HEAD: {self.pins['web']}\n", "Alignment mode: no\n",
                     f"Frozen inventory file: {member['inventory_file']}\n", "No prior audit or changed set.\n",
                     "docs-auditor.md", "templates/docs-audit.md"):
            self.assertIn(line, brief)
        self.assertNotIn("TEAM.md", brief)

        self.write_audit(advanced, "web")
        finished = self.complete("finish-inspect", receipt=advanced)
        research = self.coordinator / ".project" / "research"
        self.assertIn(f"Member HEAD: {self.pins['web']}", (research / "evidence-codebase-web.md").read_text())
        self.assertIn(f"Audited HEAD: {self.pins['web']}", (research / "DOCS-AUDIT-web.md").read_text())
        self.assertFalse((research / "evidence-codebase.md").exists())
        self.assertEqual(finished["coverage"]["stale"], [])
        self.assertEqual((self.state()["phase"], self.state()["status"]), ("inspect", "done"))
        self.assertFalse(Path(mapper["worktree"]).exists())
        self.assertFalse(Path(auditor["worktree"]).exists())
        self.assertFalse(sidecar.exists())
        self.assertEqual(git(self.coordinator, "rev-parse", "HEAD"), self.head)
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_brownfield_coordinator_keeps_its_pair_beside_member_work(self) -> None:
        self.build(("web",), brownfield=True)
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertTrue(prepared["coordinator"]["assigned"])
        self.assertEqual(prepared["dispatch"], ["inspect_codebase", "inspect_docs", "inspect_codebase_web"])
        self.assertEqual(Path(prepared["inventory_file"]).read_text(encoding="utf-8"), "README.md\n")
        docs = self.assignment(prepared, "inspect_docs")
        self.assertIn(f"Write header Repo root: {self.coordinator}\n",
                      Path(docs["brief_file"]).read_text(encoding="utf-8"))
        self.write_coordinator_pair(prepared)
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", receipt=advanced)
        research = self.coordinator / ".project" / "research"
        self.assertEqual(sorted(path.name for path in research.iterdir()),
                         ["DOCS-AUDIT-web.md", "DOCS-AUDIT.md", "evidence-codebase-web.md", "evidence-codebase.md"])
        self.assertEqual((self.state()["phase"], self.state()["status"]), ("inspect", "done"))
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_brownfield_coordinator_with_its_pair_present_is_not_reinspected(self) -> None:
        self.build(("web",), brownfield=True)
        research = self.coordinator / ".project" / "research"
        write(research / "evidence-codebase.md", map_text(str(self.coordinator)))
        self.assertIn("re-inspection contract", self.blocked("prepare-inspect")["reason"])
        self.assert_no_sidecars()
        write(research / "DOCS-AUDIT.md", audit_text(str(self.coordinator), self.head, ["README.md"]))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertFalse(prepared["coordinator"]["assigned"])
        self.assertEqual(prepared["dispatch"], ["inspect_codebase_web"])

    def test_brownfield_coordinator_carried_audit_is_the_prior_of_its_pair(self) -> None:
        self.build(("web",), brownfield=True)
        carried = write(self.coordinator / ".project" / "research" / "DOCS-AUDIT.md",
                        audit_text(str(self.coordinator), self.head, ["README.md"], (RULING,)))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertTrue(prepared["coordinator"]["assigned"])
        self.assertEqual(prepared["coordinator"]["prior_audit"]["source"], str(carried))
        docs = self.assignment(prepared, "inspect_docs")
        self.assertIsNotNone(docs["destination_sha256"])
        brief = Path(docs["brief_file"]).read_text(encoding="utf-8")
        self.assertIn(f"Prior audit, carry-forward input (keep its ## User rulings and Planned values verbatim): "
                      f"{prepared['coordinator']['prior_audit']['file']}", brief)
        self.assertIn("No changed set: re-audit every inventoried doc.", brief)

    def test_covered_and_stale_members_are_skipped_and_nothing_due_finishes_directly(self) -> None:
        self.build(("web", "api"))
        research = self.coordinator / ".project" / "research"
        for name in ("web", "api"):
            write(research / f"evidence-codebase-{name}.md", map_text(str(self.checkouts[name]), name,
                                                                   self.pins[name]))
            write(research / f"DOCS-AUDIT-{name}.md", audit_text(str(self.checkouts[name]), self.pins[name],
                                                               ["README.md"], (), name))
        moved = self.advance_origin("api")
        self.snapshots["api"] = self.snapshot("api")
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertEqual((prepared["assignments"], prepared["dispatch"], prepared["members"]), ([], [], []))
        skipped = {item["name"]: item for item in prepared["skipped"]}
        self.assertEqual(skipped["web"]["state"], "covered")
        self.assertEqual((skipped["api"]["state"], skipped["api"]["pin"]), ("stale", moved))
        self.assertIn("differs from pin", skipped["api"]["reason"])
        self.assertEqual(self.blocked("advance-inspect", receipt=prepared)["reason"],
                         "inspection receipt has no member assignment to advance")
        finished = self.complete("finish-inspect", receipt=prepared)
        self.assertEqual(finished["coverage"]["stale"], ["api"])
        self.assertEqual((self.state()["phase"], self.state()["status"]), ("inspect", "done"))
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_only_missing_members_are_assigned(self) -> None:
        self.build(("web", "api"))
        research = self.coordinator / ".project" / "research"
        write(research / "evidence-codebase-web.md", map_text(str(self.checkouts["web"]), "web", self.pins["web"]))
        write(research / "DOCS-AUDIT-web.md", audit_text(str(self.checkouts["web"]), self.pins["web"],
                                                         ["README.md"], (), "web"))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertEqual(prepared["dispatch"], ["inspect_codebase_api"])
        self.assertEqual([item["name"] for item in prepared["skipped"]], ["web"])

    def test_unverifiable_member_blocks_before_any_sidecar(self) -> None:
        self.build(("web", "api"))
        git(self.checkouts["api"], "update-ref", "-d", f"refs/remotes/origin/{DEFAULT}")
        self.snapshots["api"] = self.snapshot("api")
        state = (self.coordinator / ".project" / "STATE.md").read_bytes()
        reason = self.blocked("prepare-inspect")["reason"]
        self.assertIn("member api is unverifiable", reason)
        self.assertIn("git fetch origin", reason)
        self.assert_no_sidecars()
        self.assertEqual((self.coordinator / ".project" / "STATE.md").read_bytes(), state)
        self.assert_untouched()

    def test_colliding_logical_task_names_are_refused_before_sidecars(self) -> None:
        self.build(("a-b", "a--b"))
        reason = self.blocked("prepare-inspect")["reason"]
        self.assertIn("logical task names collide: inspect_codebase_a_b, inspect_docs_a_b", reason)
        self.assert_no_sidecars()

    def test_advance_before_mapper_output_and_finish_before_advance_block(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        receipt = Path(prepared["receipt_file"]).read_bytes()
        self.assertIn("mapper output is missing", self.blocked("advance-inspect", receipt=prepared)["reason"])
        self.assertIn("mapper review", self.blocked("advance-inspect", receipt=prepared, reviewed=False)["reason"])
        self.write_map(prepared, "web")
        self.assertIn("requires advance-inspect before finish-inspect",
                      self.blocked("finish-inspect", receipt=prepared)["reason"])
        self.assertEqual(Path(prepared["receipt_file"]).read_bytes(), receipt)
        self.assertFalse((self.coordinator / ".project" / "research").exists())
        sidecar = Path(self.assignment(prepared, "inspect_codebase_web")["member_sidecar"]["worktree"])
        self.assertEqual(git(sidecar, "rev-parse", "HEAD"), self.pins["web"])
        self.assertEqual(self.state()["status"], "active")

    def test_header_pin_and_inventory_mismatches_block_before_collection(self) -> None:
        self.build(("web", "api"))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        self.write_map(prepared, "api", pin=self.pins["web"])
        reason = self.blocked("advance-inspect", receipt=prepared)["reason"]
        self.assertEqual(reason, "check_handoffs.py")
        # Nothing changed hands: web's map passed but api's did not, so both mappers keep their sidecars.
        self.assertEqual(json.loads(Path(prepared["receipt_file"]).read_text())["stage"], 1)
        for name in ("web", "api"):
            sidecar = Path(self.assignment(prepared, f"inspect_codebase_{name}")["member_sidecar"]["worktree"])
            self.assertTrue(sidecar.is_dir())
        self.write_map(prepared, "api")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.assertEqual(advanced["dispatch"], ["inspect_docs_web", "inspect_docs_api"])
        research = self.coordinator / ".project" / "research"
        cases = (
            ("wrong Member HEAD", {"pin": self.pins["api"]}, "check_docs_audit.py"),
            ("Audited HEAD not the pin", {"audited": self.head}, "check_docs_audit.py"),
            ("inventory differs", {"inventory": ["README.md", "TEAM.md"]}, "check_docs_audit.py"),
        )
        self.write_audit(advanced, "api")
        for label, options, script in cases:
            with self.subTest(label):
                self.write_audit(advanced, "web", **options)
                self.assertEqual(self.blocked("finish-inspect", receipt=advanced)["reason"], script)
                self.assertFalse(research.exists(), "no artifact is collected before every gate passes")
        self.write_map(advanced, "web", pin=self.pins["api"])
        self.write_audit(advanced, "web")
        self.assertEqual(self.blocked("finish-inspect", receipt=advanced)["reason"], "check_handoffs.py")
        self.write_map(advanced, "web")
        self.complete("finish-inspect", receipt=advanced)
        self.assertEqual(sorted(path.name for path in research.iterdir()),
                         ["DOCS-AUDIT-api.md", "DOCS-AUDIT-web.md", "evidence-codebase-api.md",
                          "evidence-codebase-web.md"])
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_changed_mapper_output_after_advance_blocks(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        mapping = self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        mapping.write_bytes(mapping.read_bytes() + b"\nAdded after the gate.\n")
        self.write_audit(advanced, "web")
        self.assertIn("changed after advance-inspect", self.blocked("finish-inspect", receipt=advanced)["reason"])

    def test_receipt_with_a_changed_member_pin_or_input_is_refused(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        receipt_file = Path(prepared["receipt_file"])
        original = receipt_file.read_bytes()
        tampered = json.loads(original)
        tampered["assignments"][0]["pin"] = "f" * 40
        receipt_file.write_bytes(json.dumps(tampered).encode("utf-8"))
        self.assertEqual(self.blocked("advance-inspect", receipt=prepared)["reason"],
                         "inspection assignment changed its member pin: inspect_codebase_web")
        receipt_file.write_bytes(original)
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        inventory = Path(advanced["members"][0]["inventory_file"])
        inventory.write_bytes(b"README.md\nTEAM.md\n")
        self.assertEqual(self.blocked("finish-inspect", receipt=advanced)["reason"],
                         f"frozen inspection input changed: {inventory}")

    def test_repeated_advance_returns_the_same_stage_two_receipt(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        again = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.assertEqual(again, advanced)

    def test_failed_preparation_retires_what_it_created_and_drops_its_staging(self) -> None:
        self.build(("web", "api"))
        obstacle = isolation.sidecar_root(self.coordinator, "verify", "inspect-codebase-api", pin=True)
        write(obstacle / "keep.txt", "not a sidecar\n")
        receipts = self.base / "receipts"
        receipts.mkdir()
        with mock.patch.dict(os.environ, {"TMPDIR": str(receipts)}):
            payload = self.blocked("prepare-inspect")
        self.assertEqual(payload["reason"], "isolation.py")
        retired = [step for step in payload["steps"] if "retire-member-inspect" in step.get("command", [])]
        self.assertEqual(len(retired), 1)
        self.assertEqual(list(receipts.iterdir()), [])
        self.assertEqual((obstacle / "keep.txt").read_text(encoding="utf-8"), "not a sidecar\n")
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_interrupted_advance_resumes_after_its_mapper_sibling_retired(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        obstacle = isolation.sidecar_root(self.coordinator, "verify", "inspect-docs-web", pin=True)
        write(obstacle / "keep.txt", "not a sidecar\n")
        self.assertEqual(self.blocked("advance-inspect", receipt=prepared)["reason"], "isolation.py")
        interrupted = json.loads(Path(prepared["receipt_file"]).read_text(encoding="utf-8"))
        self.assertEqual((interrupted["stage"], interrupted["members"][0]["mapper_retired"]), (1, True))
        shutil.rmtree(obstacle)
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.assertEqual(advanced["dispatch"], ["inspect_docs_web"])
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", receipt=advanced)
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_unauditable_pinned_tree_blocks_prepare_before_any_sidecar(self) -> None:
        self.build(("web", "api"))
        self.publish_tree("api", {}, {"LINK.md": "../outside.md"})
        receipts = self.base / "receipts"
        receipts.mkdir()
        with mock.patch.dict(os.environ, {"TMPDIR": str(receipts)}):
            payload = self.blocked("prepare-inspect")
        self.assertEqual(payload["reason"], "check_docs_audit.py")
        self.assertIn("member api pinned Markdown is not a regular blob: LINK.md", payload["steps"][-1]["stderr"])
        self.assertFalse([step for step in payload["steps"] if "isolation.py" in step.get("script", "")])
        self.assertEqual(list(receipts.iterdir()), [])
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_prepare_freezes_member_inventories_before_dispatch(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        member = prepared["members"][0]
        self.assertEqual(Path(member["inventory_file"]).read_text(encoding="utf-8"), "README.md\n")
        self.write_map(prepared, "web")
        payload = self.complete("advance-inspect", receipt=prepared)
        self.assertEqual(payload["inspection"]["members"][0]["inventory_file"], member["inventory_file"])
        self.assertFalse([step for step in payload["steps"] if step.get("script") == "check_docs_audit.py"])
        receipt_file = Path(prepared["receipt_file"])
        tampered = json.loads(receipt_file.read_text(encoding="utf-8"))
        tampered["members"][0]["inventory_file"] = None
        receipt_file.write_bytes(json.dumps(tampered).encode("utf-8"))
        self.assertEqual(self.blocked("finish-inspect", receipt=prepared)["reason"],
                         "inspection receipt lacks the frozen inventory of member web")

    def test_failed_advance_retires_the_auditor_pair_it_did_not_record(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        mapper = self.assignment(prepared, "inspect_codebase_web")
        brief = Path(prepared["receipt_file"]).parent / "inspect_docs_web.md"
        brief.mkdir()
        payload = self.blocked("advance-inspect", receipt=prepared)
        self.assertIn("inspect_docs_web.md", payload["reason"])
        retired = [step for step in payload["steps"] if "retire-member-inspect" in step.get("command", [])
                   and "inspect-docs-web" in step["command"]]
        self.assertEqual([step["exit_code"] for step in retired], [0])
        interrupted = json.loads(Path(prepared["receipt_file"]).read_text(encoding="utf-8"))
        self.assertEqual((interrupted["stage"], interrupted["members"][0]["mapper_retired"],
                          interrupted["members"][0]["advanced"]), (1, True, False))
        self.assertTrue(Path(mapper["worktree"]).is_dir(), "the mapper artifact is kept for collection")
        self.assertFalse(isolation.sidecar_root(self.coordinator, "verify", "inspect-docs-web").exists())
        self.assertNotIn("inspect", git(self.checkouts["web"], "for-each-ref", "--format=%(refname)", "refs/heads"))
        self.assertEqual(git(self.checkouts["web"], "worktree", "list", "--porcelain").count("worktree "), 1)
        brief.rmdir()
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.assertEqual(advanced["dispatch"], ["inspect_docs_web"])
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", receipt=advanced)
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_advance_resumes_over_an_auditor_pair_a_stopped_call_left(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        obstacle = isolation.sidecar_root(self.coordinator, "verify", "inspect-docs-web", pin=True)
        write(obstacle / "keep.txt", "not a sidecar\n")
        self.assertEqual(self.blocked("advance-inspect", receipt=prepared)["reason"], "isolation.py")
        shutil.rmtree(obstacle)
        # A call stopped after creating the auditor pair but before recording it.
        isolation.isolate_member_inspect(self.coordinator, "web", self.head, self.pins["web"], "inspect-docs-web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.assertEqual(advanced["dispatch"], ["inspect_docs_web"])
        auditor = self.assignment(advanced, "inspect_docs_web")
        self.assertEqual(git(Path(auditor["member_sidecar"]["worktree"]), "rev-parse", "HEAD"), self.pins["web"])
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", receipt=advanced)
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_markdown_alias_symlink_is_audited_through_its_target(self) -> None:
        self.build(("web",))
        self.publish_tree("web", {"AGENTS.md": "# Agents\n\nRules.\n"}, {"CLAUDE.md": "AGENTS.md"})
        prepared = self.complete("prepare-inspect")["inspection"]
        member = prepared["members"][0]
        self.assertEqual(member["pin"], self.pins["web"])
        self.assertEqual(Path(member["inventory_file"]).read_text(encoding="utf-8"), "AGENTS.md\nREADME.md\n")
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", receipt=advanced)
        self.assertEqual((self.state()["phase"], self.state()["status"]), ("inspect", "done"))
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_member_evidence_off_the_pin_history_is_reinspected_with_its_rulings(self) -> None:
        self.build(("web",))
        research = self.coordinator / ".project" / "research"
        team_head = self.snapshots["web"]["head"]  # the team's unpushed commit, never on origin
        write(research / "evidence-codebase-web.md", map_text(str(self.checkouts["web"]), "web", team_head))
        prior = write(research / "DOCS-AUDIT-web.md", audit_text(
            str(self.checkouts["web"]), team_head, ["README.md", "TEAM.md"], (RULING,), "web"))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertEqual(prepared["dispatch"], ["inspect_codebase_web"])
        member = prepared["members"][0]
        self.assertEqual((member["prior_audit"]["source"], member["changed"]), (str(prior), None))
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        brief = Path(self.assignment(advanced, "inspect_docs_web")["brief_file"]).read_text(encoding="utf-8")
        self.assertIn("No changed set: re-audit every inventoried doc.", brief)
        self.write_audit(advanced, "web", rulings=(RULING,))
        finished = self.complete("finish-inspect", receipt=advanced)
        self.assertEqual(finished["coverage"]["members"][0]["state"], "covered")
        collected = (research / "DOCS-AUDIT-web.md").read_text(encoding="utf-8")
        self.assertIn(f"Member HEAD: {self.pins['web']}", collected)
        self.assertIn(RULING, collected)
        self.assertIn(f"Member HEAD: {self.pins['web']}",
                      (research / "evidence-codebase-web.md").read_text(encoding="utf-8"))
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_prior_member_audit_carries_rulings_with_its_pinned_changed_set(self) -> None:
        self.build(("web",))
        research = self.coordinator / ".project" / "research"
        old_pin = self.pins["web"]
        prior = write(research / "DOCS-AUDIT-web.md",
                      audit_text(str(self.checkouts["web"]), old_pin, ["README.md"], (RULING,), "web"))
        self.pins["web"] = self.advance_origin("web", "docs/GUIDE.md")
        self.snapshots["web"] = self.snapshot("web")
        prepared = self.complete("prepare-inspect")["inspection"]
        member = prepared["members"][0]
        self.assertEqual(member["pin"], self.pins["web"])
        self.assertEqual(member["prior_audit"]["source"], str(prior))
        self.assertEqual(Path(member["changed"]["file"]).read_text(encoding="utf-8"), "docs/GUIDE.md\n")
        self.assertEqual(member["docs_destination_sha256"], member["prior_audit"]["sha256"])
        self.assertIsNone(member["code_destination_sha256"])
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        brief = Path(self.assignment(advanced, "inspect_docs_web")["brief_file"]).read_text(encoding="utf-8")
        self.assertIn(f"Prior audit, carry-forward input", brief)
        self.assertIn(f"Changed set since the prior audit's Member HEAD: {member['changed']['file']}", brief)
        self.assertEqual(Path(advanced["members"][0]["inventory_file"]).read_text(encoding="utf-8"),
                         "README.md\ndocs/GUIDE.md\n")
        self.write_audit(advanced, "web")
        self.assertEqual(self.blocked("finish-inspect", receipt=advanced)["reason"], "check_docs_audit.py")
        self.write_audit(advanced, "web", rulings=(RULING,))
        self.complete("finish-inspect", receipt=advanced)
        collected = (research / "DOCS-AUDIT-web.md").read_text(encoding="utf-8")
        self.assertIn(RULING, collected)
        self.assertIn(f"Member HEAD: {self.pins['web']}", collected)
        self.assert_no_sidecars()

    def test_prior_audit_of_another_member_identity_blocks_before_sidecars(self) -> None:
        self.build(("web",))
        write(self.coordinator / ".project" / "research" / "DOCS-AUDIT-web.md",
              audit_text(str(self.base / "elsewhere"), self.pins["web"], ["README.md"], (RULING,), "web"))
        self.assertIn("belongs to a different member identity", self.blocked("prepare-inspect")["reason"])
        self.assert_no_sidecars()

    def test_destination_changed_after_prepare_blocks_collection(self) -> None:
        self.build(("web",))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", receipt=prepared)["inspection"]
        self.write_audit(advanced, "web")
        intruder = write(self.coordinator / ".project" / "research" / "evidence-codebase-web.md", "# Intruder\n")
        self.assertEqual(self.blocked("finish-inspect", receipt=advanced)["reason"], "isolation.py")
        self.assertEqual(intruder.read_text(encoding="utf-8"), "# Intruder\n")
        self.assertEqual(self.state()["status"], "active")

    def test_capability_block_when_selected_runtime_predates_member_inspection(self) -> None:
        self.build(("web",))
        runtime = self.base / "runtime"
        shutil.copytree(ROOT / "scripts", runtime, ignore=shutil.ignore_patterns("__pycache__", "dev"))
        write(runtime / "members.py", (
            "import argparse\nparser = argparse.ArgumentParser()\n"
            "parser.add_argument('command', choices=('add', 'validate', 'detect', 'repair'))\n"
            "parser.parse_args()\n"))
        write(self.coordinator / ".gsd-path" / "runtime.json", '{"digest": "test"}\n')
        write(self.coordinator / ".gsd-path" / "status_runtime.py", f"print({str(runtime)!r})\n")
        payload = self.blocked("prepare-inspect")
        self.assertTrue(payload["reason"].startswith("project runtime upgrade required"), payload["reason"])
        self.assertIn("members.py inspection", payload["reason"])
        self.assertNotIn("isolation.py", payload["reason"])
        self.assert_no_sidecars()

    def test_no_members_keeps_single_repo_receipt_and_refuses_advance(self) -> None:
        coordinator = self.coordinator
        git(self.base, "init", "-q", "-b", BRANCH, str(coordinator))
        write(coordinator / "README.md", "# acme program\n\nCoordinates the acme services.\n")
        write(coordinator / ".project" / "STATE.md", state_text())
        git(coordinator, "add", "-A")
        git(coordinator, "commit", "-q", "-m", "init")
        self.head = git(coordinator, "rev-parse", "HEAD")
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertEqual(set(prepared), {"repo", "base", "inventory_file", "inventory_sha256", "assignments",
                                         "receipt_file"})
        self.assertEqual([item["task_name"] for item in prepared["assignments"]], ["inspect_codebase", "inspect_docs"])
        self.assertEqual(self.blocked("advance-inspect", receipt=prepared)["reason"],
                         "advance-inspect requires member inspection; .project/MEMBERS.md is absent")
        self.write_coordinator_pair(prepared)
        self.complete("finish-inspect", receipt=prepared)
        self.assertEqual((self.state()["phase"], self.state()["status"]), ("inspect", "done"))


if __name__ == "__main__":
    unittest.main()
