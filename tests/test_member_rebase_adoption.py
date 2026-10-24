"""Owner-ruled member rebase adoption.

The helper replays a locked member bound branch onto the member's freshly
fetched default branch in its own named sidecar, proves every replayed landing,
publishes with a lease, and records the new lock base with a receipt that
member landing proofs, ship checks, and migrated milestones read.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import (
    _common,
    build_state,
    coordinator_migration as migration,
    integration,
    isolation,
    lean_verification,
    members,
    pipeline_diagnose,
    pipeline_state,
    pipeline_undo,
)
from tests import test_coordinator_migration as migration_fixture
from tests.test_member_landing import TASK

ROOT = Path(__file__).resolve().parents[1]
ISOLATION = ROOT / "scripts" / "isolation.py"
GUARD = ROOT / "scripts" / "git_guard.py"
URL = "https://github.com/acme/web.git"
BOUND = "feature/acme-milestone-001"
REPLAY = "chore/rebase-acme-milestone-001-web"
RULING = "Rebase the web branch onto the current main and keep every landing."
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
STATE = (
    "---\npipeline: gsd-path/v2\nproject: acme\nmilestone: demo\nphase: plan\nstatus: done\n"
    "branch: feature/milestone-001\narchive: null\n---\n\n# Project State\n\n## Log\n\n"
    "- 2026-10-19 — plan — plan approved\n"
)
TASKS = {
    "T001": (".project/tasks/T001-change.md", "Change app", "app.py", "v2\n"),
    "T002": (".project/tasks/T002-config.md", "Change config", "config.py", "c2\n"),
}


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *arguments], encoding="utf-8", errors="replace",
                          capture_output=True, check=check).stdout.strip()


def task_text(task_id: str) -> str:
    _, title, name, _ = TASKS[task_id]
    return TASK.replace("T001", task_id).replace("Change app", title).replace("app.py", name)


class MemberRebaseFixture(unittest.TestCase):
    """A coordinator milestone with two member landings and an advanced member main."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {
            **IDENTITY, "GSD_PATH_WORKTREE_ROOT": str(self.root / "workspace")})
        environment.start()
        self.addCleanup(environment.stop)
        # Receipt proofs are memoized per process; each test starts fresh.
        isolation._ADOPTION_MEMO.clear()
        self.addCleanup(isolation._ADOPTION_MEMO.clear)
        self.default = getattr(self, "member_default", "main")
        self.remote = self.root / "web.git"
        git(self.root, "init", "-q", "--bare", "-b", self.default, str(self.remote))
        self.member = self.root / "web"
        self.member.mkdir()
        git(self.member, "init", "-q", "-b", self.default)
        (self.member / "app.py").write_text("v1\n", encoding="utf-8")
        (self.member / "config.py").write_text("c1\n", encoding="utf-8")
        git(self.member, "add", "-A")
        git(self.member, "commit", "-q", "-m", "init")
        git(self.member, "config", f"url.{self.remote}.insteadOf", URL)
        git(self.member, "remote", "add", "origin", URL)
        git(self.member, "push", "-q", "origin", self.default)
        git(self.member, "fetch", "-q", "origin")
        git(self.member, "remote", "set-head", "origin", "--auto")
        self.locked_base = git(self.member, "rev-parse", self.default)
        self.coordinator = self.root / "acme"
        (self.coordinator / ".project" / "tasks").mkdir(parents=True)
        git(self.root, "init", "-q", "-b", "feature/milestone-001", str(self.coordinator))
        (self.coordinator / ".project" / "STATE.md").write_text(STATE, encoding="utf-8")
        for task_id, (path, *_rest) in TASKS.items():
            (self.coordinator / path).write_text(task_text(task_id), encoding="utf-8")
        (self.coordinator / ".project" / "plan").mkdir()
        (self.coordinator / ".project" / "plan" / "PLAN.md").write_text(
            "# Plan\nProject verify: `true`\n\n## Wave 1 — change\n\nReview depth: full\n", encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "plan: approve")
        members.add_member(self.coordinator, "web", self.member, "default")
        pipeline_state.transition_state(
            self.coordinator,
            {"phase": "plan", "status": "done", "branch": "feature/milestone-001", "archive": None},
            {"phase": "build", "status": "active"}, "build started")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "build: start milestone")
        self.landings = {task_id: self.land(task_id) for task_id in TASKS}
        self.old_tip = self.bound_tip()
        self.set_state("build", "blocked")
        self.onto = self.advance_main("team.txt", "team work\n")

    # -- fixture helpers --------------------------------------------------

    def land(self, task_id: str) -> dict:
        path, title, name, text = TASKS[task_id]
        base = git(self.coordinator, "rev-parse", "HEAD")
        isolated = isolation.isolate_member_task(self.coordinator, "web", task_id)
        self.bound = Path(isolated["bound_checkout"])
        isolation.activate_member_task(self.coordinator, "web", task_id, "coder", path, base)
        (Path(isolated["worktree"]) / name).write_text(text, encoding="utf-8")
        landed = isolation.land_member(self.coordinator, "web", task_id, title, path, base,
                                       isolated["member_base"])
        isolation.retire_member_task(self.coordinator, "web", task_id)
        return landed

    def set_state(self, phase: str, status: str, archive: str = "null") -> None:
        path = self.coordinator / ".project" / "STATE.md"
        text = path.read_text(encoding="utf-8")
        for key, value in (("phase", phase), ("status", status), ("archive", archive)):
            text = re.sub(rf"(?m)^{key}: \S+", f"{key}: {value}", text, count=1)
        path.write_text(text, encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", f"test: {phase}/{status}")

    def advance_main(self, name: str, text: str) -> str:
        team = self.root / f"team-{len(list(self.root.glob('team-*')))}"
        git(self.root, "clone", "-q", str(self.remote), str(team))
        (team / name).parent.mkdir(parents=True, exist_ok=True)
        (team / name).write_text(text, encoding="utf-8")
        git(team, "add", "-A")
        git(team, "-c", "user.name=team", "-c", "user.email=team@example.test", "commit", "-q", "-m", f"team {name}")
        git(team, "push", "-q", "origin", self.default)
        return git(team, "rev-parse", "HEAD")

    def bound_tip(self) -> str:
        return git(self.member, "rev-parse", f"refs/heads/{BOUND}")

    def remote_ref(self, ref: str) -> str:
        return git(self.remote, "rev-parse", "--verify", "--quiet", ref, check=False)

    def adopt(self, onto=None, ruling: str = RULING) -> dict:
        return isolation.adopt_member_rebase(self.coordinator, "web", onto or self.onto, ruling)

    def lock(self) -> dict:
        return json.loads((self.coordinator / members.LOCK_PATH).read_text(encoding="utf-8"))

    def receipt_path(self) -> Path:
        return self.coordinator / isolation.MEMBER_REBASE_PATH

    def journal(self) -> Path:
        return isolation._member_rebase_journal_path(self.coordinator, "web")

    def verify(self, root: Path = None) -> list:
        root = root or self.coordinator / ".project"
        paths = [root / "tasks" / Path(path).name for path, *_ in TASKS.values()]
        head = git(self.coordinator, "rev-parse", "HEAD")
        return isolation.verify_landed_task_files(self.coordinator, paths, ".project/tasks", head)["tasks"]

    def snapshot(self) -> dict:
        common = Path(git(self.coordinator, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        return {
            "member_refs": git(self.member, "for-each-ref", "--format=%(refname) %(objectname)"),
            "worktrees": git(self.member, "worktree", "list", "--porcelain"),
            "objects": git(self.member, "count-objects", "-v"),
            "remote": git(self.remote, "for-each-ref", "--format=%(refname) %(objectname)"),
            "head": git(self.coordinator, "rev-parse", "HEAD"),
            "status": git(self.coordinator, "status", "--porcelain", "--untracked-files=all"),
            "bound_status": git(self.bound, "status", "--porcelain", "--untracked-files=all"),
            "journals": sorted(str(path) for path in (common / "gsd-path").rglob("*") if "member-rebase" in str(path)),
            "lock": (self.coordinator / members.LOCK_PATH).read_text(encoding="utf-8"),
            "workspace": sorted(str(path) for path in (self.root / "workspace").rglob("*")
                                if path.is_dir() and "rebase" in path.name),
        }


class MemberRebaseAdoptionTests(MemberRebaseFixture):
    def assert_adopted(self, result: dict) -> None:
        new_tip = self.bound_tip()
        self.assertEqual(result["verdict"], "adopted")
        self.assertEqual((result["old_tip"], result["new_base"], result["new_tip"]), (self.old_tip, self.onto, new_tip))
        rebased = git(self.member, "rev-list", "--first-parent", "--reverse", f"{self.onto}..{new_tip}").split()
        self.assertEqual(len(rebased), 2)
        self.assertEqual(git(self.member, "rev-parse", f"{rebased[0]}^"), self.onto)
        for original, replay in zip((self.landings["T001"]["landing"], self.landings["T002"]["landing"]), rebased):
            self.assertEqual(git(self.member, "log", "-1", "--format=%B%n%an%n%ae%n%ad", original),
                             git(self.member, "log", "-1", "--format=%B%n%an%n%ae%n%ad", replay))
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), new_tip)
        self.assertEqual(git(self.member, "rev-parse", f"refs/remotes/origin/{BOUND}"), new_tip)
        self.assertEqual(self.lock()["members"][0]["base"], self.onto)
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], "gsd-path/member-rebase-adoption/v1")
        self.assertEqual(len(receipt["adoptions"]), 1)
        adoption = receipt["adoptions"][0]
        self.assertEqual(adoption["ruling"], RULING)
        self.assertEqual([(row["original"], row["rebased"]) for row in adoption["commits"]],
                         list(zip((self.landings["T001"]["landing"], self.landings["T002"]["landing"]), rebased)))
        self.assertEqual([row["task"] for row in adoption["commits"]], [TASKS["T001"][0], TASKS["T002"][0]])
        # One .project/-only coordinator commit records the lock and the receipt.
        head = git(self.coordinator, "rev-parse", "HEAD")
        self.assertEqual(result["coordinator_commit"], head)
        self.assertEqual(sorted(git(self.coordinator, "diff-tree", "--no-commit-id", "--name-only", "-r", head).split()),
                         sorted([members.LOCK_PATH, isolation.MEMBER_REBASE_PATH]))
        message = git(self.coordinator, "log", "-1", "--format=%B")
        self.assertTrue(message.startswith(f"chore: adopt rebased member web onto {self.onto[:12]}\n"))
        self.assertIsNone(re.search(r"(?i)co-authored|generated|claude|gsd", message))
        # The bound checkout follows the branch, the old lineage stays reachable, nothing is left behind.
        self.assertEqual(git(self.bound, "rev-parse", "HEAD"), new_tip)
        self.assertEqual(git(self.bound, "status", "--porcelain"), "")
        self.assertEqual((self.bound / "team.txt").read_text(encoding="utf-8"), "team work\n")
        self.assertEqual(git(self.member, "rev-parse", result["retention_ref"]), self.old_tip)
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertFalse(self.journal().exists())
        self.assertTrue(members.authorized(self.member, "acme", "push", f"refs/heads/{BOUND}", new_tip))
        self.assertEqual(git(self.coordinator, "status", "--porcelain"), "")

    def assert_recovered_through_receipt(self) -> None:
        verdicts = self.verify()
        rebased = git(self.member, "rev-list", "--first-parent", "--reverse", f"{self.onto}..{self.bound_tip()}").split()
        for verdict, task_id, landing in zip(verdicts, TASKS, rebased):
            self.assertEqual(verdict["verdict"], "recovered", verdict)
            self.assertEqual(verdict["landing"], landing)
            self.assertEqual(verdict["recorded_landing"], self.landings[task_id]["landing"])
            self.assertEqual(verdict["provenance"], "owner-ruled-member-rebase")

    def test_landings_on_the_old_lineage_block_without_an_adoption(self) -> None:
        git(self.member, "fetch", "-q", "origin")
        git(self.bound, "reset", "-q", "--hard", self.onto)
        verdicts = isolation.recover(self.coordinator, Path(".project/tasks"))["tasks"]
        self.assertTrue(all(task["verdict"] == "block" for task in verdicts))
        git(self.bound, "reset", "-q", "--hard", self.old_tip)

    def test_apply_rebases_publishes_records_and_proves_landings(self) -> None:
        with self.assertRaisesRegex(isolation.IsolationError, "not present"):
            isolation._require_onto(self.member, {"onto": self.onto, "old_base": self.locked_base,
                                                  "old_tip": self.old_tip, "member": "web",
                                                  "default_branch": "main"})
        self.assert_adopted(self.adopt())
        self.assert_recovered_through_receipt()
        proof = build_state.verify_landed_tasks(str(self.coordinator), ".project",
                                                git(self.coordinator, "rev-parse", "HEAD"))
        self.assertEqual(proof["member_rebase"]["adoptions"][0]["commits"], 2)
        self.assertEqual(isolation.validate_member_rebase(self.coordinator)["status"], "verified")

    def test_unpublished_branch_uses_an_absent_lease_and_published_branch_an_exact_lease(self) -> None:
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")
        result = self.adopt()
        self.assertEqual(result["published"]["lease"], "absent")
        self.assertIsNone(json.loads(self.receipt_path().read_text())["adoptions"][0]["published_from"])
        # A second adoption leases the published first replay.
        second_onto = self.advance_main("later.txt", "later\n")
        first_tip = self.bound_tip()
        second = self.adopt(second_onto)
        self.assertEqual(second["published"]["lease"], first_tip)
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual([item["old_base"] for item in receipt["adoptions"]], [self.locked_base, self.onto])
        self.assertEqual(receipt["adoptions"][1]["published_from"], first_tip)
        # The chain maps the recorded landings through both replays.
        verdicts = self.verify()
        self.assertTrue(all(item["verdict"] == "recovered" for item in verdicts), verdicts)
        self.assertEqual(verdicts[1]["landing"], self.bound_tip())

    def test_dry_run_is_read_only_and_reports_the_plan(self) -> None:
        before = self.snapshot()
        preview = isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        self.assertEqual(self.snapshot(), before)
        # The disposable fetch left no object behind in the member repository.
        self.assertNotEqual(subprocess.run(["git", "-C", str(self.member), "cat-file", "-e", self.onto],
                                           capture_output=True).returncode, 0)
        self.assertEqual(preview["verdict"], "ready")
        self.assertEqual(preview["status"], "preview")
        self.assertEqual((preview["old_base"], preview["old_tip"], preview["onto"]),
                         (self.locked_base, self.old_tip, self.onto))
        self.assertEqual([row["original"] for row in preview["commits"]],
                         [self.landings["T001"]["landing"], self.landings["T002"]["landing"]])
        self.assertEqual((preview["lease"], preview["replay_branch"]), ("absent", REPLAY))

    def test_cli_preview_apply_and_typed_refusal(self) -> None:
        def run(*arguments):
            return subprocess.run([sys.executable, "-B", str(ISOLATION), "adopt-member-rebase",
                                   "--repo", str(self.coordinator), "--member", "web", *arguments],
                                  encoding="utf-8", capture_output=True, check=False)
        refused = run("--onto", self.onto, "--ruling", "  ")
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stderr)["code"], "ruling-required")
        preview = run("--onto", self.onto, "--ruling", RULING, "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["verdict"], "ready")
        applied = run("--onto", self.onto, "--ruling", RULING)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assert_adopted(json.loads(applied.stdout))
        again = run("--onto", self.onto, "--ruling", RULING)
        self.assertEqual(json.loads(again.stderr)["code"], "already-based")

    def test_conflict_is_refused_before_any_mutation(self) -> None:
        self.onto = self.advance_main("app.py", "team edit\n")
        before = self.snapshot()
        with self.assertRaises(isolation.MemberRebaseError) as preview:
            isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        self.assertEqual(preview.exception.code, "conflict")
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(isolation.MemberRebaseError) as applied:
            self.adopt()
        self.assertEqual(applied.exception.code, "conflict")
        after = self.snapshot()
        # Only the fresh fetch moved the remote-tracking default branch.
        self.assertEqual({key: value for key, value in after.items() if key not in {"member_refs", "objects"}},
                         {key: value for key, value in before.items() if key not in {"member_refs", "objects"}})
        fetched = ("refs/remotes/origin/main ", "refs/remotes/origin/HEAD ")
        refs = [line for line in after["member_refs"].splitlines() if not line.startswith(fetched)]
        self.assertEqual(refs, [line for line in before["member_refs"].splitlines() if not line.startswith(fetched)])

    def test_conflicting_real_replay_rolls_back_without_trace(self) -> None:
        self.onto = self.advance_main("app.py", "team edit\n")
        git(self.member, "fetch", "-q", "origin")
        before = self.snapshot()
        with mock.patch.object(isolation, "_simulate_member_replay"):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                self.adopt()
        self.assertEqual(error.exception.code, "conflict")
        after = self.snapshot()
        self.assertEqual({key: value for key, value in after.items() if key != "objects"},
                         {key: value for key, value in before.items() if key != "objects"})
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertFalse(self.journal().exists())
        self.assertFalse(self.receipt_path().exists())

    def test_wrong_onto_moved_remote_and_non_descendant_are_refused(self) -> None:
        cases = []
        with self.assertRaises(isolation.MemberRebaseError) as stale:
            self.adopt(self.locked_base)
        cases.append(stale.exception.code)
        # The remote moved between the preview and the apply.
        isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        moved_main = self.advance_main("other.txt", "other\n")
        with self.assertRaises(isolation.MemberRebaseError) as raced:
            self.adopt()
        cases.append(raced.exception.code)
        # Someone else published the member bound branch.
        git(self.member, "push", "-q", "origin", f"{self.locked_base}:refs/heads/{BOUND}")
        with self.assertRaises(isolation.MemberRebaseError) as remote:
            self.adopt(moved_main)
        cases.append(remote.exception.code)
        git(self.remote, "update-ref", "-d", f"refs/heads/{BOUND}")
        # The default branch was rewritten so it no longer descends from the locked base.
        orphan = self.root / "orphan"
        git(self.root, "init", "-q", "-b", "main", str(orphan))
        (orphan / "x.txt").write_text("x\n", encoding="utf-8")
        git(orphan, "add", "-A")
        git(orphan, "commit", "-q", "-m", "unrelated")
        git(orphan, "push", "-q", "--force", str(self.remote), "main")
        unrelated = git(orphan, "rev-parse", "HEAD")
        with self.assertRaises(isolation.MemberRebaseError) as foreign:
            self.adopt(unrelated)
        cases.append(foreign.exception.code)
        self.assertEqual(cases, ["onto-mismatch", "onto-mismatch", "remote-moved", "onto-not-descendant"])
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertFalse(self.receipt_path().exists())

    def test_illegal_states_and_busy_member_are_refused(self) -> None:
        for phase, status, archive in (("build", "active", "null"), ("define", "active", "null"),
                                       ("ship", "active", ".project/archive/001-demo")):
            self.set_state(phase, status, archive)
            with self.assertRaises(isolation.MemberRebaseError) as error:
                isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
            self.assertEqual(error.exception.code, "illegal-state", (phase, status))
        for phase, status in (("plan", "active"), ("plan", "blocked"), ("ship", "blocked"), ("ship", "active")):
            self.set_state(phase, status)
            preview = isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
            self.assertEqual(preview["verdict"], "ready", (phase, status))
        self.set_state("build", "blocked")
        refusals = {}
        branch = "chore/task-acme-t009"
        git(self.member, "branch", branch, self.old_tip)
        with self.assertRaises(isolation.MemberRebaseError) as busy:
            isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        refusals["task"] = busy.exception.code
        git(self.member, "branch", "-D", branch)
        (self.bound / "app.py").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as dirty:
            isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        refusals["dirty"] = dirty.exception.code
        git(self.bound, "checkout", "--", "app.py")
        landings = Path(git(self.coordinator, "rev-parse", "--path-format=absolute", "--git-common-dir")) / \
            "gsd-path" / "member-landings"
        landings.mkdir(parents=True, exist_ok=True)
        (landings / "T009.json").write_text(json.dumps({"member": "web"}), encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as journal:
            isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        refusals["journal"] = journal.exception.code
        (landings / "T009.json").unlink()
        with self.assertRaises(isolation.MemberRebaseError) as unknown:
            isolation.preview_member_rebase(self.coordinator, "api", self.onto, RULING)
        refusals["unlocked"] = unknown.exception.code
        for call in (lambda: isolation.adopt_member_rebase(self.coordinator, "../web", self.onto, RULING),
                     lambda: isolation.abort_member_rebase(self.coordinator, "../web")):
            with self.assertRaises(isolation.MemberRebaseError) as invalid:
                call()
            self.assertEqual(invalid.exception.code, "member-invalid")
        self.assertEqual(refusals, {"task": "task-in-flight", "dirty": "bound-dirty",
                                    "journal": "journal-pending", "unlocked": "member-not-locked"})

    def test_tampered_receipt_is_rejected_by_landing_proof_and_ship_checks(self) -> None:
        self.adopt()
        original = self.receipt_path().read_text(encoding="utf-8")
        lock = (self.coordinator / members.LOCK_PATH).read_text(encoding="utf-8")
        tampers = {
            "rebased": lambda receipt: receipt["adoptions"][0]["commits"][0].update(rebased=self.old_tip),
            "ruling": lambda receipt: receipt["adoptions"][0].update(ruling=" "),
            "field": lambda receipt: receipt["adoptions"][0].update(extra=True),
            "chain": lambda receipt: receipt["adoptions"][0].update(old_base=self.onto),
        }
        for name, tamper in tampers.items():
            with self.subTest(name=name):
                receipt = json.loads(original)
                tamper(receipt)
                self.receipt_path().write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                with self.assertRaisesRegex(isolation.IsolationError,
                                            "T001: member rebase adoption receipt is invalid.*T002: member rebase"):
                    self.verify()
                verdicts = isolation.recover(self.coordinator, Path(".project/tasks"))["tasks"]
                self.assertTrue(all(item["verdict"] == "block" for item in verdicts), verdicts)
                with self.assertRaises(isolation.IsolationError):
                    isolation.validate_member_rebase(self.coordinator)
        self.receipt_path().write_text(original, encoding="utf-8")
        # A lock that forgets the adopted base no longer matches the receipt.
        (self.coordinator / members.LOCK_PATH).write_text(lock.replace(self.onto, self.locked_base), encoding="utf-8")
        with self.assertRaisesRegex(isolation.IsolationError, "lock does not record the adopted base"):
            isolation.validate_member_rebase(self.coordinator)
        (self.coordinator / members.LOCK_PATH).write_text(lock, encoding="utf-8")
        # Ship refuses a committed tampered receipt.
        receipt = json.loads(original)
        receipt["adoptions"][0]["commits"][1]["subject"] = "T002: Something else"
        self.receipt_path().write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "chore: tamper")
        self.set_state("ship", "active")
        head = git(self.coordinator, "rev-parse", "HEAD")
        with self.assertRaisesRegex(isolation.IsolationError, "commit map"):
            lean_verification._require_ship_inputs(self.coordinator, head)
        with self.assertRaises(build_state.BuildStateError):
            build_state.verify_landed_tasks(str(self.coordinator), ".project", head)

    def test_ruling_is_preserved_verbatim_including_whitespace_and_newlines(self) -> None:
        ruling = "  Aprovar o rebase.\nManter todos os pousos.  \n"
        self.adopt(ruling=ruling)
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["adoptions"][0]["ruling"], ruling)
        snapshot = receipt["adoptions"][0]
        self.assertEqual(snapshot["task_contracts"],
                         isolation._member_rebase_contracts(self.coordinator, snapshot["contract_head"]))

    def test_empty_or_dropped_commit_is_refused_without_changes(self) -> None:
        # The default branch gained exactly the first member edit, but not the second.
        self.onto = self.advance_main("app.py", "v2\n")
        before = self.snapshot()
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "empty-commit")
        self.assertEqual(self.snapshot(), before)

    def test_lease_is_pinned_even_if_the_absent_remote_appears_at_the_old_tip(self) -> None:
        real = isolation._publish_member_rebase

        def raced(journal):
            git(self.member, "push", "-q", "origin", f"{self.old_tip}:refs/heads/{BOUND}")
            return real(journal)

        with mock.patch.object(isolation, "_publish_member_rebase", side_effect=raced):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                self.adopt()
        self.assertEqual(error.exception.code, "remote-moved")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), self.old_tip)
        self.assertFalse(self.journal().exists())
        self.assertFalse(self.receipt_path().exists())

    def test_editing_a_nonempty_ruling_fails_even_after_an_unowned_commit(self) -> None:
        self.adopt()
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        receipt["adoptions"][0]["ruling"] = "A different, unapproved ruling."
        self.receipt_path().write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(isolation.IsolationError, "committed owner adoption record"):
            self.verify()
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "chore: change receipt by hand")
        with self.assertRaisesRegex(isolation.IsolationError, "canonical owner adoption commit"):
            isolation.validate_member_rebase(self.coordinator)

    def test_another_locked_members_native_activation_blocks_adoption(self) -> None:
        self.set_state("plan", "active")
        other = self.root / "api"
        git(self.root, "clone", "-q", str(self.remote), str(other))
        git(other, "remote", "set-url", "origin", "https://github.com/acme/api.git")
        members.add_member(self.coordinator, "api", other, "default")
        lock = self.lock()
        lock["members"].append({"name": "api", "branch": BOUND,
                                "base": git(other, "rev-parse", "HEAD")})
        (self.coordinator / members.LOCK_PATH).write_text(json.dumps(lock, indent=2), encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "plan: include api")
        git(other, "branch", "chore/task-acme-t003")
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "task-in-flight")
        self.assertIn("chore/task-acme-t003", str(error.exception))

    def test_live_dispatch_pid_refuses_read_only(self) -> None:
        root = isolation.common_git_dir(self.coordinator) / "gsd-path/dispatch/feature-milestone-001"
        record = root / "reviews/test/attempt-1/state.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"pid": os.getpid(), "finished_at": None}), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "task-in-flight")
        self.assertEqual(self.snapshot(), before)

    def test_valid_receipt_cannot_authorize_resetting_the_member_to_its_old_lineage(self) -> None:
        self.adopt()
        git(self.bound, "reset", "-q", "--hard", self.old_tip)
        with self.assertRaisesRegex(isolation.IsolationError, "adopted tip"):
            isolation.validate_member_rebase(self.coordinator)
        with self.assertRaisesRegex(members.MembersError, "adopted tip"):
            members.lock_build_members(self.coordinator)

    def test_ship_inputs_accept_a_committed_valid_receipt(self) -> None:
        self.adopt()
        self.set_state("ship", "active")
        head = git(self.coordinator, "rev-parse", "HEAD")
        lean_verification._require_ship_inputs(self.coordinator, head)
        proof = build_state.verify_landed_tasks(str(self.coordinator), ".project", head)
        self.assertEqual(proof["member_rebase"]["status"], "verified")

    def test_archived_receipt_authenticates_after_no_ff_integration_and_next_branch(self) -> None:
        self.adopt()
        archive = self.coordinator / ".project/archive/001-demo"
        archive.mkdir(parents=True)
        for name in ("tasks", "build"):
            (self.coordinator / ".project" / name).rename(archive / name)
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "ship: archive synthetic milestone")
        self.assertEqual(isolation.validate_member_rebase(self.coordinator, ".project/archive/001-demo")["status"],
                         "verified")
        root = git(self.coordinator, "rev-list", "--max-parents=0", "HEAD")
        git(self.coordinator, "checkout", "-q", "-b", "main", root)
        git(self.coordinator, "merge", "-q", "--no-ff", "feature/milestone-001", "-m", "integrate: synthetic milestone")
        git(self.coordinator, "checkout", "-q", "-b", "feature/milestone-002")
        self.assertEqual(isolation.validate_member_rebase(self.coordinator, ".project/archive/001-demo")["status"],
                         "verified")
        # Authentication remains mandatory on the preserved second-parent lineage.
        receipt_path = archive / isolation.MEMBER_REBASE_RECEIPT
        receipt = json.loads(receipt_path.read_text())
        receipt["adoptions"][0]["ruling"] = "An unapproved replacement ruling."
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "chore: tamper synthetic receipt")
        with self.assertRaisesRegex(isolation.IsolationError, "recorded owner adoption commit"):
            isolation.validate_member_rebase(self.coordinator, ".project/archive/001-demo")

    def test_non_migrated_members_can_keep_their_own_pipeline_metadata(self) -> None:
        self.onto = self.advance_main(".project/team.txt", "member team metadata\n")
        self.adopt()
        self.assertTrue(all(row["verdict"] == "recovered" for row in self.verify()))
        self.assertEqual((self.bound / ".project/team.txt").read_text(), "member team metadata\n")

    def test_archived_receipt_still_proves_archived_member_tasks(self) -> None:
        self.adopt()
        archive = self.coordinator / ".project" / "archive" / "001-demo"
        archive.mkdir(parents=True)
        (self.coordinator / ".project" / "tasks").rename(archive / "tasks")
        (self.coordinator / ".project" / "build").rename(archive / "build")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "archive tasks and build")
        verdicts = self.verify(archive)
        self.assertTrue(all(item["verdict"] == "recovered" for item in verdicts), verdicts)
        self.assertEqual(isolation.validate_member_rebase(self.coordinator, ".project/archive/001-demo")["status"],
                         "verified")

    def test_pending_member_close_row_is_preserved_and_integrated_member_is_refused(self) -> None:
        common = Path(git(self.coordinator, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        row = {"name": "web", "mode": "pull-request", "reviewed_head": self.old_tip, "status": "pending",
               "merge": None, "tag": None, "pull_request": "https://github.com/acme/web/pull/7"}
        members.write_member_close(common, "001-demo", [{**row, "status": "integrated"}])
        with self.assertRaises(isolation.MemberRebaseError) as integrated:
            isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        self.assertEqual(integrated.exception.code, "integrated")
        members.write_member_close(common, "001-demo", [row])
        git(self.member, "push", "-q", "origin", f"{self.old_tip}:refs/heads/{BOUND}")
        proof = {"url": row["pull_request"], "number": 7, "source": self.old_tip, "target": "main"}
        with mock.patch.object(isolation, "_prove_open_member_pull_request", return_value=proof):
            preview = isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
            self.assertEqual(preview["preserve_close"], {"archive": "001-demo", "row": row, "pull_request": proof})
            result = self.adopt()
        self.assertEqual(result["preserved_close"]["row"], row)
        self.assertEqual(members.read_member_close(common, "001-demo"), [row])
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["adoptions"][0]["prior_close"],
                         {"archive": "001-demo", "row": row, "pull_request": proof})

    def test_uncommitted_state_log_stays_out_of_the_adoption_commit(self) -> None:
        state = self.coordinator / ".project" / "STATE.md"
        state.write_text(state.read_text(encoding="utf-8") + "- 2026-10-19 — build — owner ruling recorded\n",
                         encoding="utf-8")
        git(self.coordinator, "add", ".project/STATE.md")
        result = self.adopt()
        changed = git(self.coordinator, "diff-tree", "--no-commit-id", "--name-only", "-r", result["coordinator_commit"])
        self.assertEqual(sorted(changed.split()), sorted([members.LOCK_PATH, isolation.MEMBER_REBASE_PATH]))
        self.assertEqual(git(self.coordinator, "status", "--porcelain"), "M  .project/STATE.md")

    def test_build_restart_keeps_the_adopted_base_and_later_landings_prove(self) -> None:
        self.adopt()
        self.assertEqual(members.lock_build_members(self.coordinator)[0]["base"], self.onto)
        self.assertEqual(git(self.coordinator, "status", "--porcelain"), "")
        extra = {"T003": (".project/tasks/T003-docs.md", "Add docs", "docs.txt", "docs\n")}
        with mock.patch.dict(TASKS, extra):
            (self.coordinator / extra["T003"][0]).write_text(task_text("T003"), encoding="utf-8")
            self.set_state("build", "active")
            landed = self.land("T003")
            self.assertEqual(git(self.member, "rev-parse", f"{landed['landing']}^"), git(self.member, "rev-parse",
                                                                                             f"{self.bound_tip()}^"))
            verdicts = self.verify()
        self.assertTrue(all(item["verdict"] == "recovered" for item in verdicts), verdicts)
        self.assertEqual([item.get("provenance") for item in verdicts],
                         ["owner-ruled-member-rebase", "owner-ruled-member-rebase", None])
        self.assertEqual(verdicts[2]["landing"], self.bound_tip())

    @unittest.skipIf(os.name == "nt", "POSIX shell hooks")
    def test_member_guard_accepts_the_helper_push_and_reserves_the_replay_prefix(self) -> None:
        hooks = self.root / "hooks"
        hooks.mkdir()
        for name, arguments in (("pre-push", '"$@"'), ("pre-commit", ""), ("commit-msg", '"$1"')):
            hook = hooks / name
            hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" -B "{GUARD}" {name} {arguments}\n', encoding="utf-8")
            hook.chmod(0o755)
        git(self.member, "config", "core.hooksPath", str(hooks))
        refused = subprocess.run(["git", "-C", str(self.member), "push", "origin", f"{self.old_tip}:refs/heads/{REPLAY}"],
                                 encoding="utf-8", capture_output=True, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("not authorized", refused.stderr)
        self.assert_adopted(self.adopt())
        self.assertEqual(self.remote_ref(f"refs/heads/{REPLAY}"), "")

    def test_status_undo_diagnose_and_member_work_see_a_pending_adoption(self) -> None:
        with mock.patch.object(isolation, "_advance_member_bound", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.adopt()
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "published")
        status = pipeline_state.status_state(self.coordinator)
        self.assertEqual(status["journals"]["member_rebase"], str(self.journal()))
        self.assertEqual(status["route"]["action"], "block")
        undo = pipeline_undo.preview(self.coordinator)
        self.assertIn("member_rebase", " ".join(undo["target"]["blocked"]))
        findings = [item for item in pipeline_diagnose.diagnose(self.coordinator)["findings"]
                    if item["id"] == "member-rebase"]
        self.assertEqual(len(findings), 1)
        self.assertIn("stage published", findings[0]["evidence"])
        with self.assertRaisesRegex(isolation.IsolationError, "pending rebase adoption"):
            isolation.isolate_member_task(self.coordinator, "web", "T003")
        with self.assertRaises(isolation.MemberRebaseError) as abort:
            isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(abort.exception.code, "published")
        self.assert_adopted(self.adopt())
        undo = pipeline_undo.preview(self.coordinator)
        self.assertIn("owner-ruled member rebase adoption", " ".join(undo["target"]["blocked"]))


class EncodedMemberRebaseTests(MemberRebaseFixture):
    def land(self, task_id: str) -> dict:
        git(self.member, "config", "i18n.commitEncoding", "ISO-8859-1")
        return super().land(task_id)

    def test_explicit_commit_encoding_is_refused_before_mutation(self) -> None:
        self.assertTrue(all(row["verdict"] == "recovered" for row in self.verify()))
        git(self.member, "config", "--unset", "i18n.commitEncoding")
        before = self.snapshot()
        for operation in (lambda: isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING),
                          self.adopt):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                operation()
            self.assertEqual(error.exception.code, "unsupported-identity")
            self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.journal().exists())
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertEqual(git(self.member, "config", "--get", "i18n.commitEncoding", check=False), "")


class PendingOpenMemberCloseTests(MemberRebaseFixture):
    """An undone archive does not erase the live PR or its old evidence."""
    azure_url = "https://dev.azure.com/example-org/Example/_git/web"

    def setUp(self) -> None:
        super().setUp()
        git(self.member, "remote", "set-url", "origin", self.azure_url)
        git(self.member, "config", f"url.{self.remote}.insteadOf", self.azure_url)
        listed = members.read_members(self.coordinator)
        listed[0].update(remote=self.azure_url, integration="pull-request")
        (self.coordinator / ".project/MEMBERS.md").write_text(members.render(listed), encoding="utf-8")
        git(self.member, "push", "-q", "origin", f"{self.old_tip}:refs/heads/{BOUND}")
        self.pr_url = self.azure_url + "/pullrequest/7"
        self.pr = {
            "pullRequestId": 7, "status": "active", "title": "feat(web): reviewed work",
            "sourceRefName": "refs/heads/" + BOUND, "targetRefName": "refs/heads/main",
            "description": f"Member: web\nReviewed-HEAD: {self.old_tip}",
            "repository": {"name": "web", "project": {"name": "Example"}, "remoteUrl": self.azure_url},
            # Azure may keep this old merge iteration while the source ref moves.
            "lastMergeSourceCommit": {"commitId": self.old_tip}, "lastMergeCommit": None,
            "autoCompleteSetBy": None, "completionOptions": {"squashMerge": False},
            "comments": ["Keep this original review and its evidence."],
        }
        self.old_row = {"name": "web", "mode": "pull-request", "reviewed_head": self.old_tip,
                        "status": "pending", "merge": None, "tag": None, "pull_request": self.pr_url}
        common = isolation.common_git_dir(self.coordinator)
        members.write_member_close(common, "001-demo", [self.old_row])
        self.close_path = members.member_close_path(common, "001-demo")
        self.close_before = self.close_path.read_bytes()
        review = self.coordinator / ".project/review"
        review.mkdir()
        self.old_gap = review / "final-gap-1.md"
        self.old_gap.write_text(f"Member reviewed HEAD: web {self.old_tip}\nOld Verify evidence.\n", encoding="utf-8")
        self.gap_before = self.old_gap.read_bytes()
        self.verify_command = f'{sys.executable} -c "print(123)"'
        completed = subprocess.run([sys.executable, "-c", "print(123)"], capture_output=True, text=True, check=True)
        build_state.verify_record(str(self.coordinator), self.verify_command, self.old_tip, "pass", member="web",
                                  execution={"exit_code": 0, "stdout": completed.stdout, "stderr": completed.stderr})
        self.ledger = self.coordinator / ".project/build/verify-ledger.jsonl"
        self.ledger_before = self.ledger.read_bytes()
        self.set_state("define", "active")
        self.calls = []
        adapter = mock.patch.object(integration, "azure_cli_json", side_effect=self.az)
        adapter.start()
        self.addCleanup(adapter.stop)

    def az(self, *arguments):
        self.calls.append(arguments)
        if arguments[:3] == ("repos", "ref", "list"):
            return [{"name": "refs/heads/" + BOUND, "objectId": self.remote_ref("refs/heads/" + BOUND)}]
        if arguments[:3] == ("repos", "pr", "list"):
            return [json.loads(json.dumps(self.pr))]
        if arguments[:3] == ("repos", "pr", "show"):
            return json.loads(json.dumps(self.pr))
        if arguments[:3] == ("repos", "pr", "update"):
            for flag, key in (("--description", "description"), ("--title", "title")):
                if flag in arguments:
                    self.pr[key] = arguments[arguments.index(flag) + 1]
            return json.loads(json.dumps(self.pr))
        self.fail(f"unexpected Azure write or request: {arguments}")

    def enter_plan_and_adopt(self) -> dict:
        self.set_state("plan", "active")
        return self.adopt()

    def repaired_archive(self) -> tuple[str, str]:
        extra = {"T003": (".project/tasks/T003-repair.md", "Repair member", "repair.py", "repaired\n")}
        with mock.patch.dict(TASKS, extra):
            (self.coordinator / extra["T003"][0]).write_text(task_text("T003"), encoding="utf-8")
            self.set_state("build", "active")
            repair = self.land("T003")
        self.set_state("ship", "active")
        archive = ".project/archive/001-demo"
        root = self.coordinator / archive
        root.mkdir(parents=True)
        for name in ("build", "tasks", "review", "plan"):
            (self.coordinator / ".project" / name).rename(root / name)
        (root / "review/FINAL.md").write_text(
            f"Reviewed HEAD: {git(self.coordinator, 'rev-parse', 'HEAD')}\n"
            f"Member reviewed HEAD: web {repair['landing']}\n", encoding="utf-8")
        return archive, repair["landing"]

    def close(self, archive: str) -> dict:
        state = mock.Mock(phase="ship", status="active", archive=archive, integration="direct")
        with mock.patch.object(integration.archive_milestone, "strict_state", return_value=(state, None, None)):
            return integration.close_members(self.coordinator)

    def test_define_refuses_without_clearing_the_pending_live_close(self) -> None:
        before = self.snapshot()
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "illegal-state")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.close_path.read_bytes(), self.close_before)
        self.assertEqual(self.calls, [])
        findings = pipeline_diagnose.diagnose(self.coordinator)["findings"]
        pending = next(row for row in findings if row["id"] == "member-close")
        self.assertIn("preserve the pending PR close", pending["retry"])
        self.assertIn("adopt-member-rebase --dry-run", pending["retry"])

    def test_plan_rebase_preserves_the_existing_open_azure_pr_and_old_evidence(self) -> None:
        result = self.enter_plan_and_adopt()
        self.assertEqual(result["preserved_close"]["row"], self.old_row)
        self.assertEqual(result["preserved_close"]["pull_request"]["source"], self.old_tip)
        self.assertEqual(self.close_path.read_bytes(), self.close_before)
        self.assertEqual(self.old_gap.read_bytes(), self.gap_before)
        self.assertEqual(self.ledger.read_bytes(), self.ledger_before)
        self.assertEqual(self.pr["comments"], ["Keep this original review and its evidence."])
        self.assertEqual(self.pr["status"], "active")
        self.assertTrue(all(call[:3] in (("repos", "pr", "list"), ("repos", "pr", "show"),
                                       ("repos", "ref", "list")) for call in self.calls))
        self.assertEqual(self.pr["lastMergeSourceCommit"]["commitId"], self.old_tip)
        self.assertFalse(build_state.verify_lookup(str(self.coordinator), self.verify_command,
                                                   result["new_tip"], "web")["reuse"])
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["adoptions"][0]["prior_close"]["row"], self.old_row)

    def test_repair_and_fresh_review_rebind_and_lease_advance_the_same_pr(self) -> None:
        from tests._editorial_fixtures import publication_fixture
        result = self.enter_plan_and_adopt()
        archive, repaired = self.repaired_archive()
        final_path = self.coordinator / archive / "review/FINAL.md"
        fresh_final = final_path.read_text(encoding="utf-8")
        final_path.write_text(fresh_final.replace(repaired, self.old_tip), encoding="utf-8")
        with self.assertRaisesRegex(integration.ArchiveError, "adopted tip"):
            self.close(archive)
        self.assertEqual(self.close_path.read_bytes(), self.close_before)
        final_path.write_text(fresh_final, encoding="utf-8")
        # The old source is not silently labelled as reviewed at the repaired tip.
        with self.assertRaises(integration.ArchiveError):
            self.close(archive)  # no new editorial publication exists yet
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), result["new_tip"])
        publication_fixture(self.coordinator, archive, "web", repaired, self.azure_url)
        with mock.patch.object(integration, "_push_member_ref", wraps=integration._push_member_ref) as publish:
            waiting = self.close(archive)
            self.assertEqual(waiting["status"], "awaiting-merge")
            self.assertEqual(waiting["pull_request"], self.pr_url)
            self.assertEqual(self.close(archive)["pull_request"], self.pr_url)
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(publish.call_args.args[-1], result["new_tip"])
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), repaired)
        self.assertEqual(self.remote_ref("refs/heads/main"), self.onto)
        closed = members.read_member_close(isolation.common_git_dir(self.coordinator), "001-demo")[0]
        self.assertEqual((closed["reviewed_head"], closed["status"], closed["pull_request"]),
                         (repaired, "pending", self.pr_url))
        receipt = json.loads((self.coordinator / archive / isolation.MEMBER_REBASE_RECEIPT).read_text())
        self.assertEqual(receipt["adoptions"][0]["prior_close"]["row"], self.old_row)
        self.assertEqual(self.pr["lastMergeSourceCommit"]["commitId"], self.old_tip)
        self.assertFalse(any(call[:3] == ("repos", "pr", "create") for call in self.calls))
        self.assertFalse(build_state.verify_lookup(str(self.coordinator), self.verify_command, repaired, "web")["reuse"])

    def test_provider_failure_after_push_keeps_the_pending_close_and_resumes(self) -> None:
        self.set_state("plan", "active")
        real = self.az
        failed = False

        def unavailable(*arguments):
            nonlocal failed
            if not failed and self.remote_ref("refs/heads/" + BOUND) != self.old_tip:
                failed = True
                raise integration.ArchiveError("temporary provider failure")
            return real(*arguments)

        with mock.patch.object(integration, "azure_cli_json", side_effect=unavailable):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                self.adopt()
        self.assertEqual(error.exception.code, "pull-request")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertEqual(self.close_path.read_bytes(), self.close_before)
        self.assertEqual(json.loads(self.journal().read_text())["stage"], "publishing")
        with self.assertRaises(isolation.MemberRebaseError) as abort:
            isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(abort.exception.code, "published")
        result = self.adopt()
        self.assertEqual(result["new_tip"], self.remote_ref("refs/heads/" + BOUND))
        self.assertEqual(self.close_path.read_bytes(), self.close_before)

    def test_unproven_tail_cannot_be_published_as_a_repair(self) -> None:
        from tests._editorial_fixtures import publication_fixture
        result = self.enter_plan_and_adopt()
        archive, _ = self.repaired_archive()
        (self.bound / "unowned.py").write_text("not a task landing\n", encoding="utf-8")
        git(self.bound, "add", "-A")
        git(self.bound, "commit", "-q", "-m", "chore: unowned change")
        tip = self.bound_tip()
        (self.coordinator / archive / "review/FINAL.md").write_text(
            f"Reviewed HEAD: {git(self.coordinator, 'rev-parse', 'HEAD')}\n"
            f"Member reviewed HEAD: web {tip}\n", encoding="utf-8")
        publication_fixture(self.coordinator, archive, "web", tip, self.azure_url)
        with self.assertRaisesRegex(integration.ArchiveError, "unproven repair commit"):
            self.close(archive)
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), result["new_tip"])
        self.assertEqual(self.pr["status"], "active")

    def test_completed_abandoned_and_auto_complete_prs_refuse_without_mutation(self) -> None:
        self.set_state("plan", "active")
        for status, auto in (("abandoned", None), ("completed", None), ("active", {"id": "owner"})):
            with self.subTest(status=status, auto=auto):
                self.pr.update(status=status, autoCompleteSetBy=auto,
                               closedDate="2026-10-19T00:00:00Z", mergeStatus="succeeded",
                               lastMergeCommit={"commitId": "a" * 40})
                before = self.snapshot()
                with self.assertRaises(isolation.MemberRebaseError) as error:
                    self.adopt()
                self.assertEqual(error.exception.code, "pull-request")
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.close_path.read_bytes(), self.close_before)


class MemberRebaseLensTests(MemberRebaseFixture):
    """With decision lenses enforced, a new adoption needs the rebase lens event of its member and target base."""

    assert_adopted = MemberRebaseAdoptionTests.assert_adopted

    def setUp(self) -> None:
        super().setUp()
        intent = self.coordinator / ".project" / "intent" / "INTENT.md"
        intent.parent.mkdir(parents=True, exist_ok=True)
        intent.write_text("# Intent — demo\n\nDecision lenses: enforced\n", encoding="utf-8")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "test: enforce decision lenses")

    def record_lens(self, base: str) -> str:
        event = _common.format_lens_event("rebase", _common.lens_scope_member("web", base),
                                          fired=[("L10", "keep")], no_signal=["L14"])
        state = self.coordinator / ".project" / "STATE.md"
        state.write_text(state.read_text(encoding="utf-8") + f"- 2026-10-24 — build — {event}\n", encoding="utf-8")
        git(self.coordinator, "commit", "-q", "-am", "test: record rebase lenses")
        return event

    def test_new_adoption_needs_the_lens_event_before_any_mutation(self) -> None:
        before = self.snapshot()
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "lens-required")
        self.assertIn(f"`lenses rebase member web base {self.onto[:12]}: …`", str(error.exception))
        self.assertEqual(self.snapshot(), before)
        # The preview stays an unchecked read-only report.
        self.assertEqual(isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)["verdict"],
                         "ready")
        self.record_lens(self.locked_base)  # the lens answered for another target base
        with self.assertRaises(isolation.MemberRebaseError) as stale:
            self.adopt()
        self.assertEqual(stale.exception.code, "lens-required")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertFalse(self.journal().exists())
        self.record_lens(self.onto)
        self.assert_adopted(self.adopt())

    def test_resuming_an_admitted_adoption_does_not_check_the_lens_again(self) -> None:
        event = self.record_lens(self.onto)
        with mock.patch.object(isolation, "_advance_member_bound", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.adopt()
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "published")
        state = self.coordinator / ".project" / "STATE.md"
        state.write_text(state.read_text(encoding="utf-8").replace(f"- 2026-10-24 — build — {event}\n", ""),
                         encoding="utf-8")
        git(self.coordinator, "commit", "-q", "-am", "test: drop the rebase lens event")
        self.assert_adopted(self.adopt())


class MemberRebaseMasterTests(MemberRebaseFixture):
    member_default = "master"

    def test_recorded_non_main_default_is_fetched_replayed_and_published(self) -> None:
        preview = isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        self.assertEqual(preview["default_branch"], "master")
        result = self.adopt()
        MemberRebaseAdoptionTests.assert_adopted(self, result)
        self.assertEqual(result["default_branch"], "master")
        self.assertEqual(git(self.member, "rev-parse", "origin/master"), self.onto)
        self.assertTrue(all(row["verdict"] == "recovered" for row in self.verify()))


class MemberRebaseResumeTests(MemberRebaseFixture):
    """An interrupted apply rolls back before publication and rolls forward after it."""

    def crash_at(self, name: str, **kwargs) -> None:
        with mock.patch.object(isolation, name, **kwargs):
            with self.assertRaises((RuntimeError, SystemExit)):
                self.adopt()

    def finish(self) -> None:
        self.assert_adopted(self.adopt())
        self.assert_recovered_through_receipt()

    assert_adopted = MemberRebaseAdoptionTests.assert_adopted
    assert_recovered_through_receipt = MemberRebaseAdoptionTests.assert_recovered_through_receipt

    def test_killed_replay_is_rolled_back_then_restarted(self) -> None:
        def partial(journal):
            # Leave the real first-pick checkpoint, without changing the planned inventory.
            destination = Path(journal["replay_worktree"])
            isolation.create_named_worktree(self.member, journal["replay_branch"], destination,
                                             journal["onto"], bound=journal["branch"])
            git(destination, "cherry-pick", journal["commits"][0]["original"])
            journal["produced_commits"].append(git(destination, "rev-parse", "HEAD"))
            isolation._write_member_rebase_journal(self.journal(), journal)
            raise SystemExit("killed")

        with mock.patch.object(isolation, "_rollback_member_rebase"):
            self.crash_at("_replay_member_rebase", side_effect=partial)
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "prepared")
        self.assertEqual(self.bound_tip(), self.old_tip)
        with self.assertRaises(isolation.MemberRebaseError) as other:
            self.adopt(ruling="another ruling")
        self.assertEqual(other.exception.code, "journal-pending")
        self.finish()

    def test_abort_rolls_back_a_replayed_adoption(self) -> None:
        self.crash_at("_publish_member_rebase", side_effect=RuntimeError("network"))
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "replayed")
        self.assertTrue(git(self.member, "branch", "--list", REPLAY))
        result = isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(result["verdict"], "aborted")
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertEqual(git(self.member, "for-each-ref", "refs/gsd-path/member-rebase/"), "")
        self.assertFalse(self.journal().exists())
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertEqual(isolation.abort_member_rebase(self.coordinator, "web")["verdict"], "none")
        self.finish()

    def test_tampered_resume_map_is_rejected_before_publication(self) -> None:
        self.crash_at("_publish_member_rebase", side_effect=RuntimeError("network"))
        path = self.journal()
        journal = json.loads(path.read_text(encoding="utf-8"))
        journal["commits"][0]["rebased"] = self.old_tip
        path.write_text(json.dumps(journal), encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "journal-invalid")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")

    def test_task_contract_change_while_replayed_blocks_before_push(self) -> None:
        self.crash_at("_publish_member_rebase", side_effect=RuntimeError("network"))
        task = self.coordinator / TASKS["T001"][0]
        task.write_text(task.read_text(encoding="utf-8").replace("app.py", "other.py"), encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "task-contract")
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")

    def test_journal_precedes_even_the_actual_member_fetch(self) -> None:
        real = isolation._fetch_into

        def checked(checkout, default, env):
            if env is None:
                self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "prepared")
            return real(checkout, default, env)

        with mock.patch.object(isolation, "_fetch_into", side_effect=checked):
            self.finish()

    def test_resume_after_replay_before_publication(self) -> None:
        self.crash_at("_publish_member_rebase", side_effect=RuntimeError("network"))
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")
        self.finish()

    def test_resume_after_push_before_its_journal_stage(self) -> None:
        real = isolation._write_member_rebase_journal

        def interrupted(path, journal):
            if journal["stage"] == "published":
                raise RuntimeError("crash")
            return real(path, journal)

        self.crash_at("_write_member_rebase_journal", side_effect=interrupted)
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "publishing")
        self.assertNotEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")
        self.finish()

    def test_resume_after_publication(self) -> None:
        self.crash_at("_advance_member_bound", side_effect=RuntimeError("crash"))
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.finish()

    def test_resume_after_local_advance(self) -> None:
        self.crash_at("_record_member_rebase", side_effect=RuntimeError("crash"))
        self.assertNotEqual(self.bound_tip(), self.old_tip)
        self.assertFalse(self.receipt_path().exists())
        self.finish()

    def test_resume_after_the_coordinator_commit(self) -> None:
        real = isolation._write_member_rebase_journal

        def interrupted(path, journal):
            if journal["stage"] == "recorded":
                raise RuntimeError("crash")
            return real(path, journal)

        self.crash_at("_write_member_rebase_journal", side_effect=interrupted)
        recorded = git(self.coordinator, "rev-parse", "HEAD")
        self.finish()
        self.assertEqual(git(self.coordinator, "rev-parse", "HEAD"), recorded)

    def test_resume_recorded_stage_after_cleanup_interruption(self) -> None:
        self.crash_at("_remove_member_rebase_journal", side_effect=RuntimeError("crash"))
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "recorded")
        recorded = git(self.coordinator, "rev-parse", "HEAD")
        self.finish()
        self.assertEqual(git(self.coordinator, "rev-parse", "HEAD"), recorded)

    def test_resume_after_lock_write_before_receipt_write(self) -> None:
        real = isolation._common.atomic_write

        def interrupted(path, content):
            if Path(path) == self.receipt_path():
                raise RuntimeError("crash")
            return real(path, content)

        with mock.patch.object(isolation._common, "atomic_write", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.adopt()
        self.assertEqual(self.lock()["members"][0]["base"], self.onto)
        self.assertFalse(self.receipt_path().exists())
        self.finish()

    def test_lost_push_ack_then_remote_advance_never_rolls_back_evidence(self) -> None:
        real = isolation._write_member_rebase_journal

        def interrupted(path, journal):
            if journal["stage"] == "published":
                raise RuntimeError("lost acknowledgment")
            return real(path, journal)

        self.crash_at("_write_member_rebase_journal", side_effect=interrupted)
        saved = json.loads(self.journal().read_text())
        self.assertEqual(saved["stage"], "publishing")
        tip = saved["new_tip"]
        advanced = git(self.remote, "commit-tree", tip + "^{tree}", "-p", tip, "-m", "external source advance")
        git(self.remote, "update-ref", "refs/heads/" + BOUND, advanced, tip)
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "remote-moved")
        self.assertEqual(json.loads(self.journal().read_text()), saved)
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertFalse(self.receipt_path().exists())
        self.assertEqual(git(self.member, "rev-parse", REPLAY), tip)
        self.assertEqual(git(self.member, "rev-parse", saved["retention_ref"]), self.old_tip)
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), advanced)
        with self.assertRaises(isolation.MemberRebaseError) as abort:
            isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(abort.exception.code, "published")
        # Even an absent current source cannot prove the earlier push never happened.
        git(self.remote, "update-ref", "-d", "refs/heads/" + BOUND, advanced)
        with self.assertRaises(isolation.MemberRebaseError):
            isolation.abort_member_rebase(self.coordinator, "web")
        self.assertTrue(self.journal().exists())
        self.assertEqual(git(self.member, "rev-parse", REPLAY), tip)

    def test_publication_intent_is_durable_before_git_push_is_sent(self) -> None:
        real = isolation.run_git

        def interrupted(repo, *arguments, **kwargs):
            if arguments[:1] == ("push",):
                self.assertEqual(json.loads(self.journal().read_text())["stage"], "publishing")
                raise RuntimeError("before push invocation")
            return real(repo, *arguments, **kwargs)

        self.crash_at("run_git", side_effect=interrupted)
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), "")
        with self.assertRaises(isolation.MemberRebaseError):
            isolation.abort_member_rebase(self.coordinator, "web")
        self.finish()

    def refuse_forced_updates(self) -> None:
        """Publish the bound branch, then deny rewrites as a forge branch policy does."""
        git(self.remote, "fetch", "-q", str(self.member), f"refs/heads/{BOUND}:refs/heads/{BOUND}")
        git(self.remote, "config", "receive.denyNonFastForwards", "true")

    def test_forge_refusal_of_the_leased_push_stays_abortable(self) -> None:
        self.refuse_forced_updates()
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "publish-refused")
        saved = json.loads(self.journal().read_text(encoding="utf-8"))
        self.assertEqual(saved["stage"], "replayed")
        self.assertNotIn("published_from", saved)
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), self.old_tip)
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertFalse(self.receipt_path().exists())
        self.assertEqual(isolation.abort_member_rebase(self.coordinator, "web")["verdict"], "aborted")
        self.assertFalse(self.journal().exists())
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertIsNone(isolation._optional_ref(self.member, saved["retention_ref"]))
        self.assertEqual(isolation._optional_ref(self.member, saved["authorization_ref"]),
                         saved["authorization_before"])
        git(self.remote, "config", "--unset", "receive.denyNonFastForwards")
        self.finish()

    def test_rerun_after_a_lifted_forge_refusal_reuses_the_replay(self) -> None:
        self.refuse_forced_updates()
        with self.assertRaises(isolation.MemberRebaseError):
            self.adopt()
        tip = json.loads(self.journal().read_text(encoding="utf-8"))["new_tip"]
        git(self.remote, "config", "--unset", "receive.denyNonFastForwards")
        self.finish()
        self.assertEqual(self.bound_tip(), tip)

    def test_unconfirmed_push_failure_keeps_publication_intent(self) -> None:
        real = isolation.run_git

        def hung_up(repo, *arguments, **kwargs):
            if arguments[:1] == ("push",):
                # No per-ref report: the remote may still apply the update.
                return subprocess.CompletedProcess(["git", *arguments], 128, "",
                                                   "fatal: the remote end hung up unexpectedly")
            return real(repo, *arguments, **kwargs)

        with mock.patch.object(isolation, "run_git", side_effect=hung_up):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                self.adopt()
        self.assertEqual(error.exception.code, "publish-failed")
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "publishing")
        with self.assertRaises(isolation.MemberRebaseError) as abort:
            isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(abort.exception.code, "published")
        self.finish()

    def test_refusal_after_an_unconfirmed_attempt_keeps_publication_intent(self) -> None:
        self.refuse_forced_updates()
        real = isolation.run_git

        def interrupted(repo, *arguments, **kwargs):
            if arguments[:1] == ("push",):
                raise RuntimeError("interrupted around the first push")
            return real(repo, *arguments, **kwargs)

        self.crash_at("run_git", side_effect=interrupted)
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "publishing")
        # A later reported refusal cannot prove the earlier attempt never landed.
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "publish-failed")
        self.assertEqual(json.loads(self.journal().read_text(encoding="utf-8"))["stage"], "publishing")
        with self.assertRaises(isolation.MemberRebaseError) as abort:
            isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(abort.exception.code, "published")
        git(self.remote, "config", "--unset", "receive.denyNonFastForwards")
        self.finish()

    def test_push_refusal_requires_the_remote_report_for_the_exact_ref(self) -> None:
        ref = f"refs/heads/{BOUND}"
        rejected = f"To {URL}\n!\t{self.old_tip}:{ref}\t[remote rejected] (TF401027: permission)\nDone\n"
        self.assertTrue(isolation._member_push_refused(rejected, ref))
        self.assertTrue(isolation._member_push_refused(rejected.replace("[remote rejected]", "[rejected]"), ref))
        self.assertFalse(isolation._member_push_refused(rejected.replace("Done\n", ""), ref))
        self.assertFalse(isolation._member_push_refused(
            rejected.replace("[remote rejected] (TF401027: permission)", "[remote failure] (no report)"), ref))
        self.assertFalse(isolation._member_push_refused(rejected.replace(ref, ref + "-other"), ref))
        self.assertFalse(isolation._member_push_refused("", ref))

    def assert_stage_drift_preserves_transaction(self, stage: str, where: str) -> None:
        method = "_record_member_rebase" if stage == "advanced" else "_remove_member_rebase_journal"
        self.crash_at(method, side_effect=RuntimeError("stage interruption"))
        saved = json.loads(self.journal().read_text())
        self.assertEqual(saved["stage"], stage)
        if where == "local":
            git(self.bound, "reset", "-q", "--hard", self.old_tip)
        else:
            tip = saved["new_tip"]
            advanced = git(self.remote, "commit-tree", tip + "^{tree}", "-p", tip, "-m", "external source advance")
            git(self.remote, "update-ref", "refs/heads/" + BOUND, advanced, tip)
        before = git(self.coordinator, "rev-parse", "HEAD")
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt()
        self.assertEqual(error.exception.code, "bound-moved" if where == "local" else "remote-moved")
        self.assertEqual(json.loads(self.journal().read_text()), saved)
        self.assertEqual(git(self.coordinator, "rev-parse", "HEAD"), before)
        self.assertEqual(self.receipt_path().exists(), stage == "recorded")

    def test_advanced_resume_refuses_local_ref_drift(self) -> None:
        self.assert_stage_drift_preserves_transaction("advanced", "local")

    def test_recorded_resume_refuses_local_ref_drift(self) -> None:
        self.assert_stage_drift_preserves_transaction("recorded", "local")

    def test_advanced_resume_refuses_remote_ref_drift(self) -> None:
        self.assert_stage_drift_preserves_transaction("advanced", "remote")

    def test_recorded_resume_refuses_remote_ref_drift(self) -> None:
        self.assert_stage_drift_preserves_transaction("recorded", "remote")

    def test_failed_replay_identity_is_cleaned_using_recorded_produced_shas(self) -> None:
        real = isolation._replay_member_rebase

        def changed_encoding(journal):
            git(self.member, "config", "i18n.commitEncoding", "ISO-8859-1")
            return real(journal)

        with mock.patch.object(isolation, "_replay_member_rebase", side_effect=changed_encoding):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                self.adopt()
        self.assertEqual(error.exception.code, "proof-failed")
        self.assertFalse(self.journal().exists())
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertEqual(self.remote_ref("refs/heads/" + BOUND), "")

    def test_abort_can_clean_a_semantically_rejected_but_owned_replay(self) -> None:
        real = isolation._replay_member_rebase

        def changed_encoding(journal):
            git(self.member, "config", "i18n.commitEncoding", "ISO-8859-1")
            return real(journal)

        with mock.patch.object(isolation, "_replay_member_rebase", side_effect=changed_encoding), \
                mock.patch.object(isolation, "_rollback_member_rebase", side_effect=RuntimeError("cleanup interruption")):
            with self.assertRaises(RuntimeError):
                self.adopt()
        saved = json.loads(self.journal().read_text())
        self.assertEqual(saved["stage"], "prepared")
        self.assertEqual(saved["produced_commits"][-1], git(self.member, "rev-parse", REPLAY))
        result = isolation.abort_member_rebase(self.coordinator, "web")
        self.assertEqual(result["verdict"], "aborted")
        self.assertFalse(self.journal().exists())
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertEqual(self.bound_tip(), self.old_tip)

    def test_bound_checkout_left_at_the_old_tree_is_refreshed(self) -> None:
        real = isolation._advance_member_bound

        def ref_only(journal):
            git(self.member, "update-ref", f"refs/heads/{BOUND}", journal["new_tip"], journal["old_tip"])
            raise RuntimeError("crash")

        self.crash_at("_advance_member_bound", side_effect=ref_only)
        self.assertNotEqual(git(self.bound, "status", "--porcelain"), "")
        self.assertIs(isolation._advance_member_bound, real)
        self.finish()


class MigratedMemberRebaseTests(unittest.TestCase):
    """A coordinator-migrated milestone stays provable after its member is rebased."""

    setUp_migration = migration_fixture.CoordinatorMigrationTests.setUp
    commit = migration_fixture.CoordinatorMigrationTests.commit

    def setUp(self) -> None:
        environment = mock.patch.dict(os.environ, {**IDENTITY})
        environment.start()
        self.addCleanup(environment.stop)
        isolation._ADOPTION_MEMO.clear()
        self.addCleanup(isolation._ADOPTION_MEMO.clear)
        self.setUp_migration()
        os.environ["GSD_PATH_WORKTREE_ROOT"] = str(self.root / "workspace")
        prepared = migration.prepare(self.spec)
        path = migration._journal(self.coordinator)
        journal = json.loads(path.read_text())
        journal["stage"] = "complete"
        migration._write(path, journal)
        members._write_marker(self.code, self.coordinator, "sample-orchestrator", "code")
        self.export = prepared["receipt"]["export_head"]
        self.branch = prepared["bound"]
        team = self.root / "team"
        git = migration_fixture.git
        git(self.root, "clone", "-q", str(self.origin), str(team))
        (team / "team.txt").write_text("team\n")
        git(team, "-c", "user.name=team", "-c", "user.email=team@example.test", "add", "-A")
        git(team, "-c", "user.name=team", "-c", "user.email=team@example.test", "commit", "-q", "-m", "team work")
        git(team, "push", "-q", "origin", "main")
        self.onto = git(team, "rev-parse", "HEAD")

    def test_migrated_default_with_pipeline_metadata_refuses_before_publication(self) -> None:
        self.assertEqual(migration.validate(self.coordinator)["status"], "verified")
        team = self.root / "team"
        (team / ".project").mkdir()
        (team / ".project/team.txt").write_text("member team metadata\n", encoding="utf-8")
        git(team, "add", "-A")
        git(team, "commit", "-q", "-m", "chore: team pipeline metadata")
        git(team, "push", "-q", "origin", "main")
        onto = git(team, "rev-parse", "HEAD")
        before = (git(self.code, "for-each-ref", "--format=%(refname) %(objectname)"),
                  git(self.code, "count-objects", "-v"), git(self.coordinator, "status", "--porcelain"))
        for operation in (isolation.preview_member_rebase, isolation.adopt_member_rebase):
            with self.assertRaises(isolation.MemberRebaseError) as error:
                operation(self.coordinator, "code", onto, RULING)
            self.assertEqual(error.exception.code, "migration-history")
            self.assertEqual((git(self.code, "for-each-ref", "--format=%(refname) %(objectname)"),
                              git(self.code, "count-objects", "-v"),
                              git(self.coordinator, "status", "--porcelain")), before)
        self.assertFalse(isolation._member_rebase_journal_path(self.coordinator, "code").exists())
        self.assertEqual(migration.validate(self.coordinator)["status"], "verified")
        self.assertEqual(git(self.code, "rev-parse", self.branch), self.export)

    def test_migrated_legacy_tasks_and_baseline_follow_the_adoption(self) -> None:
        git = migration_fixture.git
        result = isolation.adopt_member_rebase(self.coordinator, "code", self.onto,
                                               "Rebase code onto the current main.")
        new_tip = git(self.code, "rev-parse", f"refs/heads/{self.branch}")
        self.assertEqual((result["old_tip"], result["new_tip"]), (self.export, new_tip))
        self.assertEqual(git(self.code, "rev-parse", f"{new_tip}^"), self.onto)
        self.assertEqual(git(self.code, "show", f"{new_tip}:app.py"), "print(2)")
        head = git(self.coordinator, "rev-parse", "HEAD")
        proof = build_state.verify_landed_tasks(str(self.coordinator), ".project", head)
        self.assertEqual(proof["tasks"][0]["landed_commit"], self.head)
        self.assertEqual(proof["migration"]["status"], "verified")
        self.assertEqual(proof["member_rebase"]["adoptions"][0]["new_tip"], new_tip)
        self.assertEqual(migration.validate(self.coordinator)["export_head"], self.export)
        migration.require_member_baseline(self.coordinator, "code", self.code, new_tip, ".project")
        with self.assertRaisesRegex(migration.MigrationError, "baseline"):
            migration.require_member_baseline(self.coordinator, "code", self.code, self.export, ".project")
        projected = migration.legacy_proof(self.coordinator, "T001", self.head)
        self.assertEqual(projected["member_commit"], new_tip)
        self.assertEqual(projected["member_base"], self.onto)
        # A tampered receipt cannot stand in for the baseline either.
        receipt_path = self.coordinator / isolation.MEMBER_REBASE_PATH
        receipt = json.loads(receipt_path.read_text())
        receipt["adoptions"][0]["new_tip"] = self.export
        receipt_path.write_text(json.dumps(receipt, indent=2))
        with self.assertRaisesRegex(migration.MigrationError, "receipt is invalid"):
            migration.require_member_baseline(self.coordinator, "code", self.code, new_tip, ".project")
        recovered = isolation.recover(self.coordinator, Path(".project/tasks"))
        self.assertEqual(recovered["verdict"], "block")
        self.assertIn("receipt is invalid", recovered["reason"])
        with self.assertRaises(isolation.IsolationError):
            isolation.verify_landed_task_files(self.coordinator, [self.coordinator / self.task_path],
                                               ".project/tasks", head)
        receipt_path.unlink()
        self.assertEqual(isolation.recover(self.coordinator, Path(".project/tasks"))["verdict"], "block")


class MemberRebaseCloseTests(MemberRebaseFixture):
    def test_close_members_uses_the_new_tip_and_archives_the_receipt(self) -> None:
        result = self.adopt()
        archive = ".project/archive/001-demo"
        destination = self.coordinator / archive
        destination.mkdir(parents=True)
        (self.coordinator / ".project/build").rename(destination / "build")
        review = destination / "review"
        review.mkdir()
        (review / "FINAL.md").write_text(
            f"# Final\n\nReviewed HEAD: {git(self.coordinator, 'rev-parse', 'HEAD')}\n"
            f"Member reviewed HEAD: web {result['new_tip']}\n", encoding="utf-8")
        inventory = integration.archive_milestone.archive_file_inventory(destination)
        self.assertIn(isolation.MEMBER_REBASE_RECEIPT, inventory)
        state = mock.Mock(phase="ship", status="active", archive=archive, integration="direct")
        with mock.patch.object(integration.archive_milestone, "strict_state", return_value=(state, None, None)):
            closed = integration.close_members(self.coordinator)
        self.assertEqual(closed["status"], "integrated")
        self.assertEqual(closed["members"][0]["reviewed_head"], result["new_tip"])
        self.assertIn(f"Member-Reviewed-HEAD: web@{result['new_tip']}", closed["body"])
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), result["new_tip"])
        self.assertEqual(git(self.remote, "rev-parse", "main^2"), result["new_tip"])

    def test_close_members_refuses_a_tampered_archived_receipt(self) -> None:
        self.adopt()
        archive = ".project/archive/001-demo"
        (self.coordinator / archive).mkdir(parents=True)
        (self.coordinator / ".project" / "build").rename(self.coordinator / archive / "build")
        review = self.coordinator / archive / "review"
        review.mkdir()
        (review / "FINAL.md").write_text(f"# Final\n\nReviewed HEAD: {'a' * 40}\n"
                                         f"Member reviewed HEAD: web {self.bound_tip()}\n", encoding="utf-8")
        receipt = self.coordinator / archive / isolation.MEMBER_REBASE_RECEIPT
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["adoptions"][0]["new_tip"] = self.old_tip
        receipt.write_text(json.dumps(data), encoding="utf-8")
        state = mock.Mock(phase="ship", status="active", archive=archive, integration="direct")
        with mock.patch.object(integration.archive_milestone, "strict_state", return_value=(state, None, None)):
            with self.assertRaisesRegex(integration.ArchiveError, "member rebase adoption receipt is invalid"):
                integration.close_members(self.coordinator)


if __name__ == "__main__":
    unittest.main()
