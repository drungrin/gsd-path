"""Member inspection sidecars: a pinned, read-only member sibling of each artifact sidecar."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import isolation, members

ROOT = Path(__file__).resolve().parents[1]
ISOLATION = ROOT / "scripts" / "isolation.py"
STATE = (
    "---\npipeline: gsd-path/v2\nproject: acme\nmilestone: demo\nphase: inspect\nstatus: active\n"
    "branch: feature/milestone-001\narchive: null\n---\n\n# Project State\n\n## Log\n\n"
    "- 2026-10-21 — inspect — member inspection started\n"
)
PURPOSE_BRANCH = re.compile(r"^(feature|feat|bugfix|fix|hotfix|release|chore)/[a-z0-9]+(?:[-.][a-z0-9]+)*$")


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments], cwd=repo,
                          encoding="utf-8", errors="replace", capture_output=True, check=check).stdout.strip()


class MemberInspectTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {"GSD_PATH_WORKTREE_ROOT": str(self.base / "workspace")})
        environment.start()
        self.addCleanup(environment.stop)
        self.coordinator = self.base / "acme"
        (self.coordinator / ".project").mkdir(parents=True)
        git(self.base, "init", "-q", "-b", "feature/milestone-001", str(self.coordinator))
        git(self.coordinator, "config", "gc.autoDetach", "false")
        (self.coordinator / ".project" / "STATE.md").write_bytes(STATE.encode("utf-8"))
        (self.coordinator / "README.md").write_bytes(b"program\n")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "init")
        self.checkouts = {name: self.member_repository(name) for name in ("web", "api")}
        for name, checkout in self.checkouts.items():
            members.add_member(self.coordinator, name, checkout, "default")
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "record members")
        self.head = git(self.coordinator, "rev-parse", "HEAD")
        self.pins = {name: git(checkout, "rev-parse", "refs/remotes/origin/main")
                     for name, checkout in self.checkouts.items()}
        for checkout in self.checkouts.values():
            # The team moves on and leaves its tree dirty; inspection reads only the pin.
            (checkout / "README.md").write_bytes(b"team work\n")
            git(checkout, "commit", "-q", "-am", "team work")
            (checkout / "app.py").write_bytes(b"dirty = True\n")
            (checkout / "notes.txt").write_bytes(b"untracked\n")
        self.snapshots = {name: self.snapshot(name) for name in self.checkouts}

    def member_repository(self, name: str) -> Path:
        bare = self.base / "remotes" / f"{name}.git"
        git(self.base, "init", "-q", "--bare", "-b", "main", str(bare))
        checkout = self.base / f"{name}-checkout"
        checkout.mkdir()
        git(checkout, "init", "-q", "-b", "main")
        git(checkout, "config", "gc.autoDetach", "false")
        (checkout / "README.md").write_bytes(f"{name}\n".encode("utf-8"))
        (checkout / "app.py").write_bytes(b"pinned = True\n")
        (checkout / ".gitignore").write_bytes(b"*.log\n")
        git(checkout, "add", "-A")
        git(checkout, "commit", "-q", "-m", "init")
        remote = f"https://github.com/acme/{name}.git"
        git(checkout, "remote", "add", "origin", remote)
        git(checkout, "config", f"url.{bare}.insteadOf", remote)
        git(checkout, "push", "-q", "origin", "main")
        git(checkout, "fetch", "-q", "origin")
        git(checkout, "remote", "set-head", "origin", "main")
        return checkout

    def marker(self, name: str) -> Path:
        common = git(self.checkouts[name], "rev-parse", "--path-format=absolute", "--git-common-dir")
        return Path(common) / "gsd-path" / "member.json"

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
            "marker": self.marker(name).read_bytes(),
            "worktrees": git(checkout, "worktree", "list", "--porcelain").count("worktree "),
        }

    def assert_untouched(self, *names: str) -> None:
        for name in names or tuple(self.checkouts):
            self.assertEqual(self.snapshot(name), self.snapshots[name], name)
        self.assertFalse((self.coordinator / members.LOCK_PATH).exists())
        self.assertEqual(git(self.coordinator, "for-each-ref", "--format=%(refname)", "refs/heads"),
                         "refs/heads/feature/milestone-001")
        self.assertEqual(git(self.coordinator, "worktree", "list", "--porcelain").count("worktree "), 1)

    def isolate(self, member: str = "web", name: str = "inspect-codebase-web") -> dict:
        return isolation.isolate_member_inspect(self.coordinator, member, self.head, self.pins[member], name)

    def retire(self, record: dict, **options) -> dict:
        return isolation.retire_member_inspect(self.coordinator, record["member"], record["name"],
                                               record["base"], record["pin"], **options)

    def test_inspect_branch_is_conventional_and_distinct_from_project_verify(self) -> None:
        branch = isolation.member_inspect_branch_name("acme", "web")
        self.assertEqual(branch, "chore/verify-acme-inspect-web")
        self.assertRegex(branch, PURPOSE_BRANCH)
        self.assertNotRegex(branch, r"(?i)gsd|claude|codex|copilot|cursor|opencode")
        self.assertNotEqual(branch, isolation.verify_branch_name("acme-web"))
        self.assertTrue(any(f"refs/heads/{branch}".startswith(prefix)
                            for prefix in members.member_ref_prefixes("acme")))

    def test_isolate_reads_exact_pin_beside_artifact_without_lock_or_team_changes(self) -> None:
        record = self.isolate()
        sidecar = record["member_sidecar"]
        artifact, worktree = Path(record["worktree"]), Path(sidecar["worktree"])
        self.assertEqual((record["member"], record["name"], record["pin"], record["base"]),
                         ("web", "inspect-codebase-web", self.pins["web"], self.head))
        self.assertEqual(record["branch"], "chore/verify-inspect-codebase-web")
        self.assertEqual((record["kind"], record["mode"], record["bound_branch"]),
                         ("verify", "sidecar", "feature/milestone-001"))
        self.assertEqual(sidecar, {"branch": "chore/verify-acme-inspect-web",
                                   "checkout": str(self.checkouts["web"]), "tip": self.pins["web"],
                                   "worktree": str(artifact.parent / "web")})
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), self.pins["web"])
        self.assertEqual(git(worktree, "branch", "--show-current"), sidecar["branch"])
        self.assertEqual((worktree / "README.md").read_bytes(), b"web\n")
        self.assertEqual((worktree / "app.py").read_bytes(), b"pinned = True\n")
        self.assertFalse((worktree / "notes.txt").exists())
        self.assertEqual(git(artifact, "rev-parse", "HEAD"), self.head)
        self.assertEqual(isolation.check_member_inspect(self.coordinator, "web", self.head,
                                                        self.pins["web"], "inspect-codebase-web"), record)
        # The child writes evidence only into the coordinator artifact sidecar.
        output = ".project/research/evidence-codebase-web.md"
        (artifact / output).parent.mkdir(parents=True)
        (artifact / output).write_bytes(b"# Evidence\n")
        isolation.retire_member_inspect(self.coordinator, "web", record["name"], record["base"],
                                        record["pin"], keep_artifact=True)
        self.assertFalse(worktree.exists())
        isolation.collect_artifact(self.coordinator, artifact, self.head, record["branch"], output, output)
        retired = self.retire(record)
        self.assertEqual(retired["member_sidecar"]["reason"], "already-absent")
        self.assertEqual(retired["artifact"]["reason"], "removed")
        self.assertFalse(artifact.exists())
        self.assertEqual((self.coordinator / output).read_bytes(), b"# Evidence\n")
        (self.coordinator / output).unlink()
        self.assert_untouched()

    def test_pin_must_be_an_exact_member_commit(self) -> None:
        for pin, message in ((self.pins["web"][:12], "full 40-character SHA"),
                             ("f" * 40, "not a commit"),
                             (self.pins["api"], "not a commit")):
            with self.subTest(pin=pin):
                with self.assertRaisesRegex(isolation.IsolationError, message):
                    isolation.isolate_member_inspect(self.coordinator, "web", self.head, pin, "inspect-codebase-web")
        with self.assertRaisesRegex(isolation.IsolationError, "recorded base"):
            isolation.isolate_member_inspect(self.coordinator, "web", git(self.coordinator, "rev-parse", "HEAD~1"),
                                             self.pins["web"], "inspect-codebase-web")
        self.assert_untouched()

    def test_member_identity_is_checked_and_dirty_team_tree_is_ignored(self) -> None:
        self.assertNotEqual(self.snapshots["web"]["status"], "")
        marker = self.marker("web")
        original = marker.read_bytes()
        role = json.loads(original)
        marker.write_bytes(json.dumps({**role, "name": "api"}).encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, "member marker for (web|api) is (missing or )?stale"):
            self.isolate()
        marker.write_bytes(original)
        with self.assertRaisesRegex(isolation.IsolationError, "member docs is not in MEMBERS.md"):
            isolation.isolate_member_inspect(self.coordinator, "docs", self.head, self.pins["web"], "inspect-docs")
        self.assert_untouched()

    def test_moved_member_head_is_rejected_and_preserved(self) -> None:
        record = self.isolate()
        worktree = Path(record["member_sidecar"]["worktree"])
        git(worktree, "commit", "-q", "--allow-empty", "-m", "unexpected")
        with self.assertRaisesRegex(isolation.IsolationError, "moved after its creation"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])
        for pin in (record["pin"], None):
            with self.assertRaisesRegex(isolation.IsolationError, "moved after its creation; preserving it"):
                isolation.retire_member_inspect(self.coordinator, "web", record["name"], record["base"], pin)
        self.assertTrue(worktree.is_dir())
        self.assertTrue(Path(record["worktree"]).is_dir())
        with self.assertRaisesRegex(isolation.IsolationError, "already held by inspect-codebase-web"):
            self.isolate()

    def test_switched_member_branch_is_rejected_and_preserved(self) -> None:
        record = self.isolate()
        worktree = Path(record["member_sidecar"]["worktree"])
        git(worktree, "switch", "-q", "-c", "elsewhere")
        with self.assertRaisesRegex(isolation.IsolationError, "inspect sidecar branch changed"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])
        with self.assertRaisesRegex(isolation.IsolationError, "branch changed; preserving it"):
            self.retire(record)
        self.assertEqual(git(worktree, "branch", "--show-current"), "elsewhere")
        self.assertEqual(git(self.checkouts["web"], "rev-parse", record["member_sidecar"]["branch"]),
                         self.pins["web"])

    def test_moved_coordinator_base_or_artifact_head_is_rejected(self) -> None:
        record = self.isolate()
        artifact = Path(record["worktree"])
        git(artifact, "commit", "-q", "--allow-empty", "-m", "unexpected")
        with self.assertRaisesRegex(isolation.IsolationError, "inspect artifact sidecar inspect-codebase-web HEAD"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])
        git(artifact, "reset", "-q", "--hard", self.head)
        isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])
        team = self.snapshots["web"]["head"]
        with self.assertRaisesRegex(isolation.IsolationError, f"pinned at {self.pins['web']}, not {team}"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, team, record["name"])
        git(self.coordinator, "commit", "-q", "--allow-empty", "-m", "moved")
        with self.assertRaisesRegex(isolation.IsolationError, "coordinator HEAD to equal the recorded base"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])

    def test_same_member_mapper_and_auditor_run_one_after_the_other(self) -> None:
        mapper = self.isolate("web", "inspect-codebase-web")
        mapper_worktree = Path(mapper["member_sidecar"]["worktree"])
        with self.assertRaisesRegex(isolation.IsolationError, "already held by inspect-codebase-web"):
            self.isolate("web", "inspect-docs-web")
        self.assertFalse(isolation.sidecar_root(self.coordinator, "verify", "inspect-docs-web").exists())
        self.assertEqual(git(self.coordinator, "branch", "--list", "chore/verify-inspect-docs-web"), "")
        # The auditor's retirement never takes the mapper's live checkout.
        refused = isolation.retire_member_inspect(self.coordinator, "web", "inspect-docs-web")
        self.assertEqual((refused["member_sidecar"]["reason"], refused["member_sidecar"]["owner"]),
                         ("not-owned", "inspect-codebase-web"))
        self.assertEqual(refused["artifact"]["reason"], "already-absent")
        with self.assertRaisesRegex(isolation.IsolationError, "artifact sidecar inspect-docs-web is missing"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], "inspect-docs-web")
        self.assertTrue(mapper_worktree.is_dir())
        output = Path(mapper["worktree"]) / ".project/research/evidence-codebase-web.md"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"# Evidence\n")
        released = self.retire(mapper, keep_artifact=True)
        self.assertEqual((released["member_sidecar"]["reason"], released["artifact"]["reason"]), ("removed", "kept"))
        auditor = self.isolate("web", "inspect-docs-web")
        self.assertEqual(auditor["member_sidecar"]["worktree"], str(mapper_worktree))
        self.assertEqual(auditor["member_sidecar"]["branch"], mapper["member_sidecar"]["branch"])
        self.assertEqual(git(mapper_worktree, "rev-parse", "HEAD"), self.pins["web"])
        self.assertEqual(output.read_bytes(), b"# Evidence\n")
        isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], "inspect-docs-web")
        # The mapper's own retirement leaves the auditor's checkout alone and keeps uncollected evidence.
        with self.assertRaisesRegex(isolation.IsolationError, "uncommitted files; preserving it"):
            self.retire(mapper)
        self.assertTrue(output.is_file())
        self.assertTrue(mapper_worktree.is_dir())
        self.assertEqual(self.retire(auditor)["member_sidecar"]["reason"], "removed")
        output.unlink()
        self.assertEqual(self.retire(mapper)["artifact"]["reason"], "removed")
        self.assert_untouched()

    def test_distinct_members_hold_independent_siblings(self) -> None:
        web = self.isolate("web", "inspect-codebase-web")
        api = self.isolate("api", "inspect-codebase-api")
        folder = Path(web["worktree"]).parent
        self.assertEqual({Path(record[key]).parent for record in (web, api) for key in ("worktree",)}, {folder})
        self.assertEqual(Path(web["member_sidecar"]["worktree"]), folder / "web")
        self.assertEqual(Path(api["member_sidecar"]["worktree"]), folder / "api")
        self.assertEqual(api["member_sidecar"]["branch"], "chore/verify-acme-inspect-api")
        self.assertEqual(git(Path(api["member_sidecar"]["worktree"]), "rev-parse", "HEAD"), self.pins["api"])
        self.retire(web)
        isolation.check_member_inspect(self.coordinator, "api", self.head, self.pins["api"], "inspect-codebase-api")
        docs = self.isolate("web", "inspect-docs-web")
        self.retire(api)
        self.retire(docs)
        self.assert_untouched()

    def test_colliding_paths_and_foreign_branches_are_refused_untouched(self) -> None:
        folder = isolation.sidecar_root(self.coordinator, "verify", "inspect-codebase-web").parent
        stray = folder / "web"
        stray.mkdir(parents=True)
        (stray / "keep.txt").write_bytes(b"keep\n")
        with self.assertRaisesRegex(isolation.IsolationError, "inspect path is already in use"):
            self.isolate()
        self.assertEqual((stray / "keep.txt").read_bytes(), b"keep\n")
        (stray / "keep.txt").unlink()
        stray.rmdir()
        # Project Verify's member sibling shares the ../web folder but never its branch.
        verify = isolation.verify_branch_name("acme-web")
        git(self.checkouts["web"], "worktree", "add", "-q", "-b", verify, str(stray), self.pins["web"])
        with self.assertRaisesRegex(isolation.IsolationError, f"in use by refs/heads/{verify}"):
            self.isolate()
        self.assertEqual(isolation.retire_member_inspect(self.coordinator, "web", "inspect-codebase-web")
                         ["member_sidecar"]["reason"], "already-absent")
        self.assertEqual(git(stray, "branch", "--show-current"), verify)
        git(self.checkouts["web"], "worktree", "remove", str(stray))
        git(self.checkouts["web"], "branch", "-D", verify)
        foreign = isolation.member_inspect_branch_name("acme", "web")
        git(self.checkouts["web"], "branch", foreign, self.pins["web"])
        with self.assertRaisesRegex(isolation.IsolationError, "has no inspect creation record; preserving it"):
            self.isolate()
        retired = isolation.retire_member_inspect(self.coordinator, "web", "inspect-codebase-web")
        self.assertEqual((retired["member_sidecar"]["reason"], retired["member_sidecar"]["owner"]),
                         ("not-owned", None))
        self.assertEqual(git(self.checkouts["web"], "rev-parse", foreign), self.pins["web"])
        git(self.checkouts["web"], "branch", "-D", foreign)
        artifact = isolation.sidecar_root(self.coordinator, "verify", "inspect-codebase-web")
        artifact.mkdir(parents=True)
        with self.assertRaisesRegex(isolation.IsolationError, "worktree path already exists"):
            self.isolate()
        with self.assertRaisesRegex(isolation.IsolationError, "not an owned sidecar"):
            isolation.retire_member_inspect(self.coordinator, "web", "inspect-codebase-web")
        artifact.rmdir()
        self.assertEqual(git(self.checkouts["web"], "branch", "--list", foreign), "")
        self.assert_untouched()

    def test_layout_collisions_are_refused_before_any_sidecar(self) -> None:
        names = ["inspect-codebase", "inspect-docs", "inspect-codebase-web", "inspect-docs-web"]
        isolation.check_member_inspect_layout(["web", "api"], names)
        for member_names, sidecar_names, message in (
                (["inspect-docs"], names, "member inspect-docs collides with coordinator sidecar folder"),
                (["project-verify"], names, "member project-verify collides"),
                (["web", "web"], names, "member web is named twice"),
                (["web"], ["inspect-docs-web", "inspect_docs_web"], "collides with sidecar folder inspect-docs-web"),
                (["web"], ["project-verify"], "collides with sidecar folder project-verify"),
                (["web"], ["../web"], "invalid inspect sidecar name")):
            with self.subTest(members=member_names, names=sidecar_names):
                with self.assertRaisesRegex(isolation.IsolationError, message):
                    isolation.check_member_inspect_layout(member_names, sidecar_names)
        with self.assertRaisesRegex(isolation.IsolationError, "member web collides"):
            isolation.isolate_member_inspect(self.coordinator, "web", self.head, self.pins["web"], "web")
        first = self.isolate("web", "inspect-codebase-shared")
        with self.assertRaisesRegex(isolation.IsolationError, "already exists"):
            self.isolate("api", "inspect-codebase-shared")
        self.assertEqual(git(self.checkouts["api"], "worktree", "list", "--porcelain").count("worktree "), 1)
        self.retire(first)
        self.assert_untouched()

    def test_unknown_member_changes_are_kept_and_ignored_output_is_discarded(self) -> None:
        record = self.isolate()
        worktree = Path(record["member_sidecar"]["worktree"])
        (worktree / "run.log").write_bytes(b"command output\n")
        isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])
        for path, data in (("app.py", b"edited\n"), ("scratch.txt", b"new\n")):
            with self.subTest(path=path):
                existed = (worktree / path).exists()
                (worktree / path).write_bytes(data)
                with self.assertRaisesRegex(isolation.IsolationError, f"unknown changes: {path}"):
                    isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"],
                                                   record["name"])
                with self.assertRaisesRegex(isolation.IsolationError, f"unknown changes; preserving it: {path}"):
                    self.retire(record)
                self.assertEqual((worktree / path).read_bytes(), data)
                self.assertTrue(Path(record["worktree"]).is_dir())
                if existed:
                    git(worktree, "checkout", "--", path)
                else:
                    (worktree / path).unlink()
        self.assertEqual(self.retire(record)["member_sidecar"]["reason"], "removed")
        self.assertFalse(worktree.exists())
        self.assert_untouched()

    def test_failed_member_creation_rolls_back_the_artifact_sidecar(self) -> None:
        original = isolation.create_named_worktree

        def fail_member(primary, branch, destination, base, bound=None, reflog=None):
            if reflog is not None:
                raise isolation.IsolationError("member worktree add failed")
            return original(primary, branch, destination, base, bound, reflog)

        with mock.patch.object(isolation, "create_named_worktree", side_effect=fail_member):
            with self.assertRaisesRegex(isolation.IsolationError, "member worktree add failed"):
                self.isolate()
        self.assertFalse(isolation.sidecar_root(self.coordinator, "verify", "inspect-codebase-web").exists())
        self.assert_untouched()
        record = self.isolate()
        self.retire(record)
        self.assert_untouched()

    def test_lost_receipt_retires_from_creation_records_and_retry_recreates(self) -> None:
        record = self.isolate()
        with self.assertRaisesRegex(isolation.IsolationError, "already held by inspect-codebase-web"):
            self.isolate()
        with self.assertRaisesRegex(isolation.IsolationError, "pinned at"):
            isolation.retire_member_inspect(self.coordinator, "web", record["name"], pin=self.pins["api"])
        self.assertTrue(Path(record["member_sidecar"]["worktree"]).is_dir())
        retired = isolation.retire_member_inspect(self.coordinator, "web", record["name"])
        self.assertEqual((retired["member_sidecar"]["reason"], retired["member_sidecar"]["pin"]),
                         ("removed", self.pins["web"]))
        self.assertEqual(retired["artifact"]["reason"], "removed")
        again = isolation.retire_member_inspect(self.coordinator, "web", record["name"])
        self.assertEqual((again["member_sidecar"]["reason"], again["artifact"]["reason"]),
                         ("already-absent", "already-absent"))
        self.assert_untouched()
        retry = self.isolate()
        self.assertEqual(retry, record)
        self.retire(retry)
        self.assert_untouched()

    def test_missing_member_folder_is_recovered_from_its_registration(self) -> None:
        record = self.isolate()
        worktree = Path(record["member_sidecar"]["worktree"])
        subprocess.run(["rm", "-rf", str(worktree)], check=True)
        with self.assertRaisesRegex(isolation.IsolationError, "missing or not a real directory"):
            isolation.check_member_inspect(self.coordinator, "web", self.head, self.pins["web"], record["name"])
        self.assertEqual(self.retire(record)["member_sidecar"]["reason"], "removed")
        self.assert_untouched()

    def test_project_verify_names_a_leftover_inspection_and_its_exact_retirement(self) -> None:
        record = self.isolate()
        inspected = Path(record["member_sidecar"]["worktree"])
        git(self.checkouts["web"], "branch", "feature/acme-milestone-001", self.pins["web"])
        lock = self.coordinator / members.LOCK_PATH
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(json.dumps({"schema": members.LOCK_SCHEMA, "members": [
            {"name": "web", "branch": "feature/acme-milestone-001", "base": self.pins["web"]}]}).encode("utf-8"))
        sidecar = isolation.sidecar_root(self.coordinator, "verify", "project-verify")
        self.assertEqual(sidecar.parent / "web", inspected)
        command = " ".join(shlex.quote(part) for part in (
            "python3", "-B", str(ISOLATION), "retire-member-inspect", "--repo", str(self.coordinator),
            "--member", "web", "--name", "inspect-codebase-web"))
        expected = (f"member web is still held by inspection inspect-codebase-web on "
                    f"chore/verify-acme-inspect-web at {inspected}; finish that inspection, or retire it with: "
                    f"{command}")
        with self.assertRaises(isolation.IsolationError) as raised:
            isolation.isolate_member_verify(self.coordinator, sidecar)
        self.assertEqual(str(raised.exception), expected)
        # Nothing is removed or created: the inspection keeps its sibling, Verify made no branch.
        self.assertEqual(git(inspected, "rev-parse", "HEAD"), self.pins["web"])
        self.assertTrue(Path(record["worktree"]).is_dir())
        self.assertEqual(git(self.checkouts["web"], "branch", "--list", "chore/verify-acme-web"), "")
        retired = subprocess.run(shlex.split(command), cwd=self.coordinator, encoding="utf-8",
                                 errors="replace", capture_output=True)
        self.assertEqual(retired.returncode, 0, retired.stderr)
        self.assertFalse(inspected.exists())
        created = isolation.isolate_member_verify(self.coordinator, sidecar)
        self.assertEqual(created["web"]["worktree"], str(inspected))
        isolation.retire_member_verify(created)

    def test_cli_round_trip_and_error_convention(self) -> None:
        def run(*arguments: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-B", str(ISOLATION), *arguments], cwd=self.coordinator,
                                  encoding="utf-8", errors="replace", capture_output=True)

        common = ["--repo", str(self.coordinator), "--member", "web", "--name", "inspect-docs-web"]
        pinned = [*common, "--base", self.head, "--pin", self.pins["web"]]
        created = run("isolate-member-inspect", *pinned)
        self.assertEqual(created.returncode, 0, created.stderr)
        record = json.loads(created.stdout)
        self.assertEqual(record["member_sidecar"]["branch"], "chore/verify-acme-inspect-web")
        checked = run("check-member-inspect", *pinned)
        self.assertEqual((checked.returncode, json.loads(checked.stdout)), (0, record), checked.stderr)
        refused = run("isolate-member-inspect", *pinned)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(refused.stdout, "")
        self.assertIn("already held by inspect-docs-web", refused.stderr)
        self.assertNotIn("Traceback", refused.stderr)
        kept = run("retire-member-inspect", *pinned, "--keep-artifact")
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertEqual(json.loads(kept.stdout)["artifact"]["reason"], "kept")
        retired = run("retire-member-inspect", *common)
        self.assertEqual(retired.returncode, 0, retired.stderr)
        self.assertEqual(json.loads(retired.stdout)["artifact"]["reason"], "removed")
        missing = run("isolate-member-inspect", *common)
        self.assertEqual(missing.returncode, 2)
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
