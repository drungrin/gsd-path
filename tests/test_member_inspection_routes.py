"""Member inspection re-entry: the define/active route, its state edge, and inspect coverage."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

from scripts import guard_hook, install, members, pipeline_state
from tests import test_discussion_records

BRANCH = "feature/milestone-001"
EVENT = "member inspection started"
REAL_RUN = subprocess.run


def git(repo: Path, *arguments: str) -> str:
    return REAL_RUN(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *arguments],
        capture_output=True, encoding="utf-8", errors="replace", check=True,
    ).stdout.strip()


def state_text(phase: str = "define", status: str = "active", branch: str = BRANCH,
               milestone: str = "null") -> str:
    return (
        "---\npipeline: gsd-path/v2\nproject: acme\n"
        f"milestone: {milestone}\nphase: {phase}\nstatus: {status}\n"
        f"branch: {branch}\narchive: null\n---\n\n"
        "# Project State\n\n## Log\n\n- 2026-10-21 — define — project initialized\n"
    )


class MemberInspectionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.coordinator = self.root / "acme"
        (self.coordinator / ".project").mkdir(parents=True)
        git(self.root, "init", "-q", "-b", "main", str(self.coordinator))
        self.state = self.coordinator / ".project" / "STATE.md"
        self.write_state()
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "init")
        git(self.coordinator, "checkout", "-q", "-b", BRANCH)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_state(self, project_dir: str = ".project", **fields: str) -> None:
        path = self.coordinator / project_dir / "STATE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(state_text(**fields).encode("utf-8"))

    def write(self, relative: str, text: str = "# Fixture\n") -> Path:
        path = self.coordinator / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def make_member(self, name: str, readme: str = "Storefront with a checkout flow.\n") -> Path:
        member = self.root / name
        member.mkdir()
        git(member, "init", "-q", "-b", "main")
        (member / "README.md").write_bytes(readme.encode("utf-8"))
        git(member, "add", "-A")
        git(member, "commit", "-q", "-m", "init")
        remote = self.root / f"{name}.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(remote))
        url = f"https://github.com/acme/{name}.git"
        git(member, "remote", "add", "origin", url)
        git(member, "config", f"url.{remote.as_uri()}.insteadOf", url)
        git(member, "push", "-q", "origin", "main")
        git(member, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        members.add_member(self.coordinator, name, member, "default")
        return member

    def add_web(self) -> None:
        self.web = self.make_member("web")
        self.pin = git(self.web, "rev-parse", "refs/remotes/origin/main")

    def write_evidence(self, project_dir: str = ".project", kinds=("codebase", "docs"),
                       member: str = "web", pin: Optional[str] = None) -> None:
        pin = pin or self.pin
        header = (f"Repo root: {self.web}\nMember: {member}\n"
                  f"Default branch: main\nMember HEAD: {pin}\n")
        if "codebase" in kinds:
            self.write(f"{project_dir}/research/evidence-codebase-web.md",
                       f"# Evidence — codebase\n\n{header}Scanned: 2026-10-21\n\n"
                       "## Map\n\n- **Stack**: Static site.\n")
        if "docs" in kinds:
            self.write(f"{project_dir}/research/DOCS-AUDIT-web.md",
                       f"# Docs Audit\n\n{header}Audited HEAD: {pin}\n\n## Summary\n")

    def remove_evidence(self, project_dir: str = ".project") -> None:
        for name in ("evidence-codebase-web.md", "DOCS-AUDIT-web.md"):
            (self.coordinator / project_dir / "research" / name).unlink(missing_ok=True)

    def advance_origin(self) -> str:
        (self.web / "app.js").write_bytes("console.log(1)\n".encode("utf-8"))
        git(self.web, "add", "-A")
        git(self.web, "commit", "-q", "-m", "app")
        git(self.web, "update-ref", "refs/remotes/origin/main", "HEAD")
        return git(self.web, "rev-parse", "HEAD")

    def route(self) -> dict:
        return pipeline_state.route_state(self.coordinator)["route"]

    def enter(self, event: str = EVENT, project_dir: str = ".project",
              expected: Optional[dict] = None, **changes: Optional[str]) -> dict:
        before = {"phase": "define", "status": "active",
                  "branch": None if project_dir != ".project" else BRANCH, "archive": None}
        return pipeline_state.transition_state(
            self.coordinator, {**before, **(expected or {})},
            {"phase": "inspect", "status": "active", **changes}, event, project_dir)

    def finish(self, project_dir: str = ".project", status: str = "done") -> dict:
        branch = BRANCH if project_dir == ".project" else None
        return pipeline_state.transition_state(
            self.coordinator, {"phase": "inspect", "status": "active", "branch": branch, "archive": None},
            {"status": status}, "inspection artifacts passed", project_dir)

    def fingerprint(self) -> dict:
        files = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.name not in {"index", "index.lock"}:
                files[str(path)] = path.read_bytes()
        return files

    def refs(self, repo: Path) -> str:
        return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")

    # No MEMBERS.md: today's flow and output.

    def test_no_members_keeps_define_route_status_and_inspect_completion(self) -> None:
        self.write(".project/research/evidence-codebase-web.md")
        with mock.patch.object(members, "inspection_scope", side_effect=AssertionError("scope")), \
                mock.patch("scripts.check_handoffs.validate_inspection_coverage",
                           side_effect=AssertionError("coverage")):
            self.assertEqual(self.route(), {"action": "run-phase", "phase": "define",
                                            "reason": "state is define/active"})
            status = pipeline_state.status_state(self.coordinator)
            self.assertEqual(status["next_skill"], "gsd-path-define")
            self.assertNotIn("members", status)
            before = self.state.read_bytes()
            with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                        "member inspection cannot start: no MEMBERS.md"):
                self.enter()
            self.assertEqual(self.state.read_bytes(), before)
            self.write_state(phase="inspect")
            result = self.finish()
            self.assertEqual(set(result), {"schema", "status", "path", "previous", "state"})
            self.assertEqual(result["state"]["status"], "done")

    # The define/active route.

    def test_brownfield_member_without_evidence_routes_to_member_inspect(self) -> None:
        self.add_web()
        self.make_member("api", "# api\n")
        expected = {"action": "run-phase", "phase": "inspect", "mode": "members", "members": ["web"],
                    "reason": "brownfield members lack inspection evidence: web"}
        self.assertEqual(self.route(), expected)
        for case, writer in (
            ("partial", lambda: self.write_evidence(kinds=("codebase",))),
            ("wrong member", lambda: self.write_evidence(member="api")),
        ):
            with self.subTest(case):
                writer()
                self.assertEqual(self.route(), expected)
                self.remove_evidence()

    def test_status_reports_member_route_without_fetch_or_writes(self) -> None:
        self.add_web()
        git(self.web, "checkout", "-q", "-b", "team-work")
        (self.web / "scratch.txt").write_bytes("team work in progress\n".encode("utf-8"))
        before, refs = self.fingerprint(), [self.refs(repo) for repo in (self.coordinator, self.web)]
        commands = []

        def recording(command, *arguments, **keywords):
            commands.append([str(part) for part in command])
            return REAL_RUN(command, *arguments, **keywords)

        with mock.patch("subprocess.run", side_effect=recording):
            status = pipeline_state.status_state(self.coordinator)
        self.assertEqual(status["route"]["phase"], "inspect")
        self.assertEqual(status["route"]["members"], ["web"])
        self.assertEqual(status["next_skill"], "gsd-path-inspect")
        self.assertEqual(status["handoff"]["next"], "Invoke $gsd-path to continue with inspect.")
        self.assertTrue(commands)
        for command in commands:
            self.assertFalse({"fetch", "pull", "push", "ls-remote", "update-ref", "worktree"} & set(command),
                             command)
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual([self.refs(repo) for repo in (self.coordinator, self.web)], refs)
        self.assertFalse((self.web / ".git" / "FETCH_HEAD").exists())
        self.assertFalse((self.coordinator / ".project" / "build").exists())

    def test_define_route_stays_when_member_reentry_does_not_apply(self) -> None:
        self.add_web()
        cases = (
            ("covered", lambda: self.write_evidence()),
            ("intent exists", lambda: self.write(".project/intent/INTENT.md", "# Intent\n\nLane: standard\n")),
            ("charter without roadmap", lambda: self.write(".project/CHARTER.md", "# Charter\n")),
        )
        for case, arrange in cases:
            with self.subTest(case):
                arrange()
                self.assertEqual(self.route(), {"action": "run-phase", "phase": "define",
                                                "reason": "state is define/active"})
                self.remove_evidence()
                for relative in ("intent/INTENT.md", "CHARTER.md"):
                    (self.coordinator / ".project" / relative).unlink(missing_ok=True)
        with self.subTest("charter with roadmap"):
            self.write(".project/CHARTER.md", "# Charter\n")
            self.write(".project/ROADMAP.md", "# Roadmap\n")
            self.assertEqual(self.route()["phase"], "inspect")
        with self.subTest("build recovery"), mock.patch(
                "scripts.build_recovery.context", return_value={"active": True, "source": "plan"}):
            route = self.route()
            self.assertEqual((route["phase"], route.get("mode")), ("define", "corrections"))

    def test_stale_member_evidence_is_advisory_not_reinspection(self) -> None:
        self.add_web()
        self.write_evidence()
        self.advance_origin()
        self.assertEqual(members.inspection_scope(self.coordinator)["members"][0]["state"], "stale")
        self.assertEqual(self.route()["phase"], "define")
        before = self.state.read_bytes()
        with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                    "member inspection cannot start: no brownfield member lacks"):
            self.enter()
        self.assertEqual(self.state.read_bytes(), before)

    def test_greenfield_members_never_route_to_inspect(self) -> None:
        self.make_member("api", "# api\n")
        self.make_member("sdk", "")
        self.assertEqual(self.route(), {"action": "run-phase", "phase": "define",
                                        "reason": "state is define/active"})
        with self.assertRaisesRegex(pipeline_state.PipelineStateError, "no brownfield member lacks"):
            self.enter()

    def test_unverifiable_member_blocks_with_its_reason(self) -> None:
        self.add_web()
        self.write_evidence()
        git(self.web, "update-ref", "-d", "refs/remotes/origin/main")
        route = self.route()
        self.assertEqual(route["action"], "block")
        self.assertEqual(route["reason"], "member web is unverifiable: member requires "
                                          "refs/remotes/origin/main; run git fetch origin")
        status = pipeline_state.status_state(self.coordinator)
        self.assertEqual((status["route"]["action"], status["next_skill"]), ("block", "gsd-path"))
        self.assertTrue(guard_hook.valid_status_payload(status, self.coordinator))
        self.assertTrue(install._valid_status_payload(status, self.coordinator))
        git(self.web, "update-ref", "refs/remotes/origin/main", self.pin)
        self.web.rename(self.root / "web-moved")
        self.assertIn(f"member web is unverifiable: member checkout is missing: {self.web}",
                      self.route()["reason"])

    def test_unborn_coordinator_blocks_instead_of_skipping_members(self) -> None:
        self.add_web()
        git(self.coordinator, "checkout", "-q", "--orphan", "feature/milestone-002")
        git(self.coordinator, "rm", "-rq", "--cached", ".")
        self.write_state(branch="feature/milestone-002")
        route = self.route()
        self.assertEqual(route["action"], "block")
        self.assertTrue(route["reason"].startswith("member inspection scope cannot be verified: coordinator:"),
                        route["reason"])

    def test_branch_binding_precedes_member_reentry(self) -> None:
        self.add_web()
        with mock.patch.object(members, "inspection_scope", side_effect=AssertionError("scope")):
            self.write_state(branch="null")
            self.assertEqual(self.route()["action"], "bind-initial")
            self.write_state()
            git(self.coordinator, "checkout", "-q", "-b", "other")
            route = self.route()
            self.assertEqual(route, {"action": "block", "reason": f"current branch other != STATE.branch {BRANCH}"})

    def test_pending_discussion_blocks_member_route_and_edge(self) -> None:
        self.add_web()
        fixture = test_discussion_records.DiscussionRecordTests()
        for owner, blocked in (("gsd-path-define", True), ("gsd-path-inspect", False)):
            with self.subTest(owner=owner):
                prepared = fixture.command(self.coordinator, "prepare")
                self.assertEqual(prepared.returncode, 0, prepared.stderr)
                payload = fixture.turn_payload(self.root, "turn.json", next_owner=owner,
                                               target_artifact=".project/intent/INTENT.md")
                appended = fixture.command(self.coordinator, "append", payload)
                self.assertEqual(appended.returncode, 0, appended.stderr)
                route = self.route()
                if blocked:
                    self.assertEqual(route["action"], "block")
                    self.assertIn("pending discussion requires its owner disposition", route["reason"])
                    self.assertIn("gsd-path-define", route["reason"])
                    before = self.state.read_bytes()
                    with self.assertRaisesRegex(pipeline_state.PipelineStateError, "pending discussion"):
                        self.enter()
                    self.assertEqual(self.state.read_bytes(), before)
                else:
                    self.assertEqual((route["action"], route["phase"]), ("run-phase", "inspect"))
                    self.assertEqual(self.enter()["state"]["phase"], "inspect")
                shutil.rmtree(self.coordinator / ".project" / "discuss")
                self.write_state()

    # The define/active -> inspect/active edge.

    def test_member_inspection_edge_enters_inspect_with_its_exact_event(self) -> None:
        self.add_web()
        result = self.enter()
        self.assertEqual((result["state"]["phase"], result["state"]["status"]), ("inspect", "active"))
        self.assertEqual(result["previous"]["phase"], "define")
        text = self.state.read_text(encoding="utf-8")
        self.assertRegex(text, r"\n- \d{4}-\d{2}-\d{2} — inspect — member inspection started\n$")
        self.assertEqual(self.route(), {"action": "run-phase", "phase": "inspect", "mode": "normal",
                                        "reason": "state is inspect/active"})
        self.assertEqual(pipeline_state.load_state(self.coordinator)[0].branch, BRANCH)

    def test_member_inspection_edge_cannot_bypass_its_preconditions(self) -> None:
        self.add_web()

        def reset() -> None:
            self.write_state()
            self.remove_evidence()
            for relative in ("intent/INTENT.md", "CHARTER.md", "next/STATE.md"):
                (self.coordinator / ".project" / relative).unlink(missing_ok=True)
            git(self.web, "update-ref", "refs/remotes/origin/main", self.pin)
            git(self.coordinator, "checkout", "-q", BRANCH)

        cases = (
            ("event", "state transition requires event: member inspection started",
             lambda: None, {"event": "inspection started"}),
            ("expected identity", "expected state does not match",
             lambda: None, {"expected": {"status": "done"}}),
            ("unexpected milestone", "transition is missing expected fields: milestone",
             lambda: None, {"milestone": "storefront"}),
            ("milestone", "illegal STATE.milestone transition", lambda: None,
             {"milestone": "storefront", "expected": {"milestone": None}}),
            ("branch", "illegal STATE.branch transition", lambda: None, {"branch": "feature/milestone-002"}),
            ("integration", "integration mode changes require configure-integration", lambda: None,
             {"integration_default": "direct", "integration": "pull-request", "integration_source": "milestone",
              "expected": {"integration_default": "direct", "integration": "direct",
                           "integration_source": "default"}}),
            ("covered", "no brownfield member lacks inspection evidence", self.write_evidence, {}),
            ("intent", "INTENT.md exists", lambda: self.write(".project/intent/INTENT.md"), {}),
            ("charter", "CHARTER.md exists without ROADMAP.md", lambda: self.write(".project/CHARTER.md"), {}),
            ("unverifiable", "member inspection cannot start: member web is unverifiable",
             lambda: git(self.web, "update-ref", "-d", "refs/remotes/origin/main"), {}),
            ("unbound", "member inspection requires a bound milestone branch",
             lambda: self.write_state(branch="null"), {"expected": {"branch": None}}),
            ("other branch", f"current branch other != STATE.branch {BRANCH}",
             lambda: git(self.coordinator, "checkout", "-q", "-B", "other"), {}),
            ("lookahead", "member inspection re-entry is legal only on the active track",
             lambda: self.write_state(".project/next", branch="null"), {"project_dir": ".project/next"}),
        )
        for case, message, arrange, arguments in cases:
            with self.subTest(case):
                reset()
                arrange()
                state_files = sorted((self.coordinator / ".project").rglob("STATE.md"))
                before = [path.read_bytes() for path in state_files]
                with self.assertRaisesRegex(pipeline_state.PipelineStateError, message):
                    self.enter(**arguments)
                self.assertEqual([path.read_bytes() for path in state_files], before)
        with self.subTest("build recovery"):
            reset()
            with mock.patch("scripts.build_recovery.context", return_value={"active": True}):
                with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                            "member inspection cannot start: build recovery is active"):
                    self.enter()

    # Inspect completion coverage.

    def test_inspect_completion_requires_member_coverage(self) -> None:
        self.add_web()
        self.write_state(phase="inspect")
        before = self.state.read_bytes()
        cases = (
            ("missing", lambda: None, r"inspect handoff failed: missing real hand-off file: .*evidence-codebase-web\.md"),
            ("partial", lambda: self.write_evidence(kinds=("codebase",)),
             r"inspect handoff failed: missing real hand-off file: .*DOCS-AUDIT-web\.md"),
            ("malformed", lambda: self.write_evidence(member="api"), "Member must be web, found api"),
            ("unverifiable", lambda: (self.write_evidence(),
                                      git(self.web, "update-ref", "-d", "refs/remotes/origin/main")),
             "inspect handoff failed: member web is unverifiable: member requires refs/remotes/origin/main"),
        )
        for case, arrange, message in cases:
            with self.subTest(case):
                arrange()
                with self.assertRaisesRegex(pipeline_state.PipelineStateError, message):
                    self.finish()
                self.assertEqual(self.state.read_bytes(), before)
                self.remove_evidence()
                git(self.web, "update-ref", "refs/remotes/origin/main", self.pin)
        self.assertEqual(self.finish(status="blocked")["state"]["status"], "blocked")
        self.write_state(phase="inspect")
        self.write_evidence()
        result = self.finish()
        self.assertEqual(result["state"]["status"], "done")
        self.assertEqual(result["member_coverage"], {
            "members": [{"name": "web", "verdict": "brownfield", "pin": self.pin, "state": "covered"}],
            "stale": []})

    def test_inspect_completion_accepts_stale_evidence_as_advisory(self) -> None:
        self.add_web()
        self.write_state(phase="inspect")
        self.write_evidence()
        moved = self.advance_origin()
        result = self.finish()
        self.assertEqual(result["member_coverage"]["stale"], ["web"])
        self.assertEqual(result["member_coverage"]["members"][0]["pin"], moved)
        done = {"phase": "inspect", "status": "done", "branch": BRANCH, "archive": None}
        entered = pipeline_state.transition_state(self.coordinator, done, {"phase": "define", "status": "active"},
                                                  "brownfield definition started")
        self.assertEqual(entered["state"]["phase"], "define")
        self.assertEqual(self.route()["phase"], "define")

    def test_leaving_inspect_done_never_reruns_member_coverage(self) -> None:
        # A member registered after an earlier inspect/done must not make define unreachable.
        self.write_state(phase="inspect", status="done")
        self.add_web()
        done = {"phase": "inspect", "status": "done", "branch": BRANCH, "archive": None}
        with mock.patch("scripts.check_handoffs.validate_inspection_coverage",
                        side_effect=AssertionError("coverage")):
            entered = pipeline_state.transition_state(
                self.coordinator, done, {"phase": "define", "status": "active"}, "brownfield definition started")
        self.assertNotIn("member_coverage", entered)
        self.assertEqual((entered["state"]["phase"], entered["state"]["status"]), ("define", "active"))
        # define/active then re-enters inspect for the member that lacks evidence.
        self.assertEqual(self.route()["members"], ["web"])
        self.assertEqual(self.enter()["state"]["phase"], "inspect")
        with self.assertRaisesRegex(pipeline_state.PipelineStateError, "inspect handoff failed"):
            self.finish()

    def test_lookahead_inspect_done_leaves_for_define_after_its_pin_lands(self) -> None:
        # Evidence pinned at a locked bound tip; the tip later squash-lands and the lock is archived.
        self.add_web()
        git(self.web, "checkout", "-q", "-b", "feature/acme-milestone-001")
        (self.web / "app.js").write_bytes("console.log(1)\n".encode("utf-8"))
        git(self.web, "add", "-A")
        git(self.web, "commit", "-q", "-m", "feature")
        tip = git(self.web, "rev-parse", "HEAD")
        git(self.web, "checkout", "-q", "main")
        self.write_state(phase="build", milestone="storefront")
        lock = self.write(".project/build/members.json", json.dumps({
            "schema": members.LOCK_SCHEMA,
            "members": [{"name": "web", "branch": "feature/acme-milestone-001", "base": self.pin}]}))
        self.write_state(".project/next", phase="inspect", branch="null")
        self.write_evidence(".project/next", pin=tip)
        self.assertEqual(self.finish(".project/next")["member_coverage"]["members"][0]["state"], "covered")
        git(self.web, "merge", "-q", "--squash", "feature/acme-milestone-001")
        git(self.web, "commit", "-q", "-m", "squash")
        git(self.web, "update-ref", "refs/remotes/origin/main", "HEAD")
        lock.unlink()
        self.assertEqual(members.inspection_scope(self.coordinator, ".project/next")["members"][0]["state"],
                         "missing")
        entered = pipeline_state.transition_state(
            self.coordinator, {"phase": "inspect", "status": "done", "branch": None, "archive": None},
            {"phase": "define", "status": "active"}, "definition started", ".project/next")
        self.assertEqual((entered["state"]["phase"], entered["state"]["status"]), ("define", "active"))

    def test_evidence_off_the_pin_history_routes_back_to_member_inspect(self) -> None:
        self.add_web()
        git(self.web, "checkout", "-q", "-b", "side")
        (self.web / "side.js").write_bytes("console.log(2)\n".encode("utf-8"))
        git(self.web, "add", "-A")
        git(self.web, "commit", "-q", "-m", "side")
        side = git(self.web, "rev-parse", "HEAD")
        git(self.web, "checkout", "-q", "main")
        for head in (side, "b" * 40):
            with self.subTest(head=head):
                self.write_evidence(pin=head)
                entry = members.inspection_scope(self.coordinator)["members"][0]
                self.assertEqual(entry["state"], "missing")
                self.assertEqual(entry["reason"], f"evidence Member HEAD {head} is not on refs/remotes/origin/main")
                self.assertEqual(self.route()["members"], ["web"])

    def test_lookahead_inspect_completion_reads_the_requested_track(self) -> None:
        self.add_web()
        self.write_state(phase="build", milestone="storefront")
        self.write_state(".project/next", phase="inspect", branch="null")
        active_before = self.state.read_bytes()
        self.write_evidence()
        with self.assertRaisesRegex(pipeline_state.PipelineStateError,
                                    r"missing real hand-off file: .*\.project/next/research/evidence-codebase-web\.md"):
            self.finish(".project/next")
        self.write_evidence(".project/next")
        result = self.finish(".project/next")
        self.assertEqual(result["state"]["status"], "done")
        self.assertEqual(result["member_coverage"]["members"][0]["state"], "covered")
        self.assertEqual(self.state.read_bytes(), active_before)
        self.assertEqual(pipeline_state.validate_state(self.coordinator, ".project/next")["state"]["phase"],
                         "inspect")

    def test_script_runtime_routes_and_enters_member_inspection(self) -> None:
        self.add_web()
        script = Path(__file__).resolve().parents[1] / "scripts" / "pipeline_state.py"

        def cli(*arguments: str) -> subprocess.CompletedProcess:
            return REAL_RUN([sys.executable, "-B", str(script), *arguments, "--repo", str(self.coordinator)],
                            capture_output=True, encoding="utf-8", errors="replace", cwd=self.root)

        routed = cli("route")
        self.assertEqual(routed.returncode, 0, routed.stderr)
        self.assertEqual(json.loads(routed.stdout)["route"]["members"], ["web"])
        expected = ("--expect-phase", "define", "--expect-status", "active",
                    "--expect-branch", BRANCH, "--expect-archive", "null",
                    "--set-phase", "inspect", "--set-status", "active")
        wrong = cli("transition", "--event", "inspection started", *expected)
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("state transition requires event: member inspection started", wrong.stderr)
        entered = cli("transition", "--event", EVENT, *expected)
        self.assertEqual(entered.returncode, 0, entered.stderr)
        self.assertEqual(json.loads(entered.stdout)["state"]["phase"], "inspect")
        finished = cli("transition", "--event", "inspection artifacts passed",
                       "--expect-phase", "inspect", "--expect-status", "active",
                       "--expect-branch", BRANCH, "--expect-archive", "null", "--set-status", "done")
        self.assertEqual(finished.returncode, 1)
        self.assertIn("inspect handoff failed: missing real hand-off file", finished.stderr)

    # Guard and installer status validation, same release.

    def test_guard_and_install_accept_the_member_inspection_route(self) -> None:
        self.assertEqual(guard_hook.STATUS_TRANSITIONS, install.STATUS_TRANSITIONS)
        self.assertIn("inspect", guard_hook.STATUS_TRANSITIONS["define"])
        self.add_web()
        status = pipeline_state.status_state(self.coordinator)
        self.assertEqual((status["route"]["phase"], status["route"]["mode"]), ("inspect", "members"))
        self.assertTrue(guard_hook.valid_status_payload(status, self.coordinator))
        self.assertTrue(install._valid_status_payload(status, self.coordinator))
        for phase, route_phase in (("define", "build"), ("research", "inspect"), ("plan", "inspect")):
            with self.subTest(state=phase, route=route_phase):
                payload = json.loads(json.dumps(status))
                payload["state"]["phase"] = phase
                payload["route"]["phase"] = route_phase
                payload["next_skill"] = f"gsd-path-{route_phase}"
                self.assertFalse(guard_hook.valid_status_payload(payload, self.coordinator))
                self.assertFalse(install._valid_status_payload(payload, self.coordinator))


if __name__ == "__main__":
    unittest.main()
