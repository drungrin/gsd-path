"""Lookahead (.project/next) member inspection while the active program builds.

Same synthetic coordinator `acme` and members as the active-track workflow
tests; the active track sits at build/active with CHARTER.md and ROADMAP.md.
"""

import hashlib
import json
import unittest
from pathlib import Path
from typing import Sequence

from tests.test_member_inspect_workflow import (
    BRANCH, DEFAULT, RULING, MemberInspectFixture, audit_text, git, map_text, state_text, write,
)

NEXT = ".project/next"
LOCKED_BRANCH = "feature/acme-milestone-001"
DONE_RULING = "| 1 | fix-doc | Keep the README honest. | T001 |"


class MemberInspectLookaheadTests(MemberInspectFixture, unittest.TestCase):
    def build_lookahead(self, names: Sequence[str] = ("web",), brownfield: bool = False) -> None:
        self.build(names, brownfield=brownfield, state=state_text("build", "active"))
        project = self.coordinator / ".project"
        write(project / "CHARTER.md", "# Charter — acme\n\nProgram scope.\n")
        write(project / "ROADMAP.md", "# Roadmap — acme\n\n1. demo\n2. next\n")
        write(project / "next" / "STATE.md", state_text("inspect", "active", branch=None, milestone="next"))

    def active_tree(self) -> dict:
        project = self.coordinator / ".project"
        return {str(path.relative_to(project)): path.read_bytes() for path in sorted(project.rglob("*"))
                if path.is_file() and "next" not in path.relative_to(project).parts}

    def test_prepare_finish_member_lookahead_preserves_active_track_and_binding(self) -> None:
        self.build_lookahead(("web",))
        before = self.active_tree()
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        self.assertEqual(prepared["project_dir"], NEXT)
        self.assertEqual(prepared["base"], self.head)
        mapper = self.assignment(prepared, "inspect_codebase_web")
        self.assertEqual(mapper["output"], ".project/next/research/evidence-codebase-web.md")
        self.assertEqual(prepared["members"][0]["pin_ref"], f"refs/remotes/origin/{DEFAULT}")
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        self.assertEqual(self.assignment(advanced, "inspect_docs_web")["output"],
                         ".project/next/research/DOCS-AUDIT-web.md")
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        research = self.coordinator / NEXT / "research"
        self.assertEqual(sorted(path.name for path in research.iterdir()),
                         ["DOCS-AUDIT-web.md", "evidence-codebase-web.md"])
        self.assertEqual((self.state(NEXT)["phase"], self.state(NEXT)["status"]), ("inspect", "done"))
        self.assertIsNone(self.state(NEXT)["branch"])
        self.assertEqual((self.state()["phase"], self.state()["status"], self.state()["branch"]),
                         ("build", "active", BRANCH))
        self.assertEqual(self.active_tree(), before)
        self.assertFalse((self.coordinator / ".project" / "research").exists())
        self.assertEqual(git(self.coordinator, "rev-parse", "HEAD"), self.head)
        self.assert_no_sidecars()
        self.assert_untouched()

    def test_member_lookahead_receipt_cannot_publish_to_active_track(self) -> None:
        self.build_lookahead(("web",))
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        self.write_map(prepared, "web")
        for action in ("advance-inspect", "finish-inspect"):
            with self.subTest(action):
                self.assertEqual(self.blocked(action, receipt=prepared)["reason"],
                                 "inspection receipt does not match project directory")
        self.assertFalse((self.coordinator / ".project" / "research").exists())
        self.assertEqual(json.loads(Path(prepared["receipt_file"]).read_text())["stage"], 1)

    def test_locked_member_pins_its_bound_tip_and_others_their_origin(self) -> None:
        self.build_lookahead(("web", "api"))
        publisher = self.base / "web-bound"
        git(self.base, "clone", "-q", "-b", DEFAULT, str(self.base / "remotes" / "web.git"), str(publisher))
        write(publisher / "bound.py", "bound = True\n")
        git(publisher, "add", "-A")
        git(publisher, "commit", "-q", "-m", "landed in the active build")
        git(self.checkouts["web"], "fetch", "-q", str(publisher), f"HEAD:refs/heads/{LOCKED_BRANCH}")
        tip = git(self.checkouts["web"], "rev-parse", f"refs/heads/{LOCKED_BRANCH}")
        self.snapshots["web"] = self.snapshot("web")
        lock = write(self.coordinator / ".project" / "build" / "members.json", json.dumps({
            "schema": "gsd-path/member-lock/v1",
            "members": [{"name": "web", "branch": LOCKED_BRANCH, "base": self.pins["web"]}]}) + "\n")
        locked = lock.read_bytes()
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        pins = {member["name"]: (member["pin"], member["pin_ref"]) for member in prepared["members"]}
        self.assertEqual(pins["web"], (tip, f"refs/heads/{LOCKED_BRANCH}"))
        self.assertEqual(pins["api"], (self.pins["api"], f"refs/remotes/origin/{DEFAULT}"))
        sidecar = Path(self.assignment(prepared, "inspect_codebase_web")["member_sidecar"]["worktree"])
        self.assertEqual(git(sidecar, "rev-parse", "HEAD"), tip)
        self.assertTrue((sidecar / "bound.py").is_file())
        for name in ("web", "api"):
            self.write_map(prepared, name, pin=pins[name][0])
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        web = next(member for member in advanced["members"] if member["name"] == "web")
        self.assertEqual(Path(web["inventory_file"]).read_text(encoding="utf-8"), "README.md\n")
        for name in ("web", "api"):
            self.write_audit(advanced, name)
        self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        evidence = (self.coordinator / NEXT / "research" / "evidence-codebase-web.md").read_text(encoding="utf-8")
        self.assertIn(f"Member HEAD: {tip}", evidence)
        self.assertEqual(lock.read_bytes(), locked)
        self.assertEqual(git(self.checkouts["web"], "rev-parse", f"refs/heads/{LOCKED_BRANCH}"), tip)
        self.assertEqual(self.state(NEXT)["status"], "done")
        self.assertEqual(self.snapshot("web"), self.snapshots["web"])
        self.assertEqual(self.snapshot("api"), self.snapshots["api"])

    def test_member_lookahead_missing_bound_branch_or_checkout_blocks_before_dispatch(self) -> None:
        self.build_lookahead(("web", "api"))
        lock = write(self.coordinator / ".project" / "build" / "members.json", json.dumps({
            "schema": "gsd-path/member-lock/v1",
            "members": [{"name": "web", "branch": LOCKED_BRANCH, "base": self.pins["web"]}]}) + "\n")
        reason = self.blocked("prepare-inspect", project_dir=NEXT)["reason"]
        self.assertIn(f"member web is unverifiable: member web inspection pin is unavailable: "
                      f"refs/heads/{LOCKED_BRANCH}", reason)
        lock.write_bytes(b"{not json")
        self.assertIn("member web inspection lock is invalid",
                      self.blocked("prepare-inspect", project_dir=NEXT)["reason"])
        lock.unlink()
        self.checkouts["api"].rename(self.base / "api-moved")
        self.assertIn("member api is unverifiable: member checkout is missing",
                      self.blocked("prepare-inspect", project_dir=NEXT)["reason"])
        self.assertEqual(git(self.coordinator, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertFalse((self.coordinator / NEXT / "research").exists())

    def test_member_lookahead_passively_copies_greenfield_and_removed_queues(self) -> None:
        self.build_lookahead(("web",))
        active = self.coordinator / ".project" / "research"
        coordinator_audit = write(active / "DOCS-AUDIT.md",
                                  audit_text(str(self.coordinator), self.head, ["README.md"], (RULING,)))
        removed = write(active / "DOCS-AUDIT-old.md",
                        audit_text(str(self.base / "old-checkout"), "a" * 40, ["README.md"], (RULING,), "old"))
        write(active / "DOCS-AUDIT-done.md",
              audit_text(str(self.base / "done-checkout"), "b" * 40, ["README.md"], (DONE_RULING,), "done"))
        web_active = write(active / "DOCS-AUDIT-web.md",
                           audit_text(str(self.checkouts["web"]), self.pins["web"], ["README.md"], (RULING,), "web"))
        before = self.active_tree()
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        carries = {carry["filename"]: carry for carry in prepared["passive_carries"]}
        self.assertEqual(sorted(carries), ["DOCS-AUDIT-old.md", "DOCS-AUDIT.md"])
        for name, source in (("DOCS-AUDIT.md", coordinator_audit), ("DOCS-AUDIT-old.md", removed)):
            self.assertEqual(carries[name]["source"], f".project/research/{name}")
            self.assertEqual(carries[name]["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual((carries[name]["destination"], carries[name]["destination_sha256"],
                              carries[name]["reused"]), (f"{NEXT}/research/{name}", None, False))
        web = prepared["members"][0]
        self.assertEqual(web["prior_audit"]["source"], str(web_active))
        self.assertEqual(Path(web["changed"]["file"]).read_text(encoding="utf-8"), "")
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        self.write_audit(advanced, "web", rulings=(RULING,))
        original = removed.read_bytes()
        removed.write_bytes(original + b"\n")
        self.assertEqual(self.blocked("finish-inspect", project_dir=NEXT, receipt=advanced)["reason"],
                         "inspection carry input changed: DOCS-AUDIT-old.md")
        removed.write_bytes(original)
        self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        research = self.coordinator / NEXT / "research"
        self.assertEqual(sorted(path.name for path in research.iterdir()),
                         ["DOCS-AUDIT-old.md", "DOCS-AUDIT-web.md", "DOCS-AUDIT.md", "evidence-codebase-web.md"])
        self.assertEqual((research / "DOCS-AUDIT.md").read_bytes(), coordinator_audit.read_bytes())
        self.assertEqual((research / "DOCS-AUDIT-old.md").read_bytes(), original)
        self.assertIn(RULING, (research / "DOCS-AUDIT-web.md").read_text(encoding="utf-8"))
        self.assertEqual(self.active_tree(), before)
        self.assertEqual(self.state(NEXT)["status"], "done")
        self.assert_no_sidecars()

    def test_member_lookahead_receipt_carries_only_canonical_track_paths(self) -> None:
        self.build_lookahead(("web",))
        active = self.coordinator / ".project" / "research"
        write(active / "DOCS-AUDIT-old.md",
              audit_text(str(self.base / "old-checkout"), "a" * 40, ["README.md"], (RULING,), "old"))
        outside = write(self.base / "outside.md", "# Not an audit\n")
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        self.write_map(prepared, "web")
        receipt_file = Path(prepared["receipt_file"])
        original = receipt_file.read_bytes()
        cases = (
            ("absolute source", {"source": str(outside)}),
            ("traversing source", {"source": ".project/research/../../outside.md"}),
            ("source on another track", {"source": f"{NEXT}/research/DOCS-AUDIT-old.md"}),
            ("active destination", {"destination": ".project/research/DOCS-AUDIT-old.md"}),
            ("absolute destination", {"destination": str(self.base / "DOCS-AUDIT-old.md")}),
            ("traversing destination", {"destination": f"{NEXT}/research/../../../DOCS-AUDIT-old.md"}),
            ("non-canonical filename", {"filename": "DOCS-AUDIT-Old.md",
                                        "source": ".project/research/DOCS-AUDIT-Old.md",
                                        "destination": f"{NEXT}/research/DOCS-AUDIT-Old.md"}),
            ("non-audit filename", {"filename": "evidence-codebase-old.md",
                                    "source": ".project/research/evidence-codebase-old.md",
                                    "destination": f"{NEXT}/research/evidence-codebase-old.md"}),
        )
        for label, changes in cases:
            with self.subTest(label):
                tampered = json.loads(original)
                tampered["passive_carries"][0].update(changes)
                receipt_file.write_bytes(json.dumps(tampered).encode("utf-8"))
                filename = changes.get("filename", "DOCS-AUDIT-old.md")
                for action in ("advance-inspect", "finish-inspect"):
                    self.assertEqual(self.blocked(action, project_dir=NEXT, receipt=prepared)["reason"],
                                     f"inspection receipt has an invalid passive carry: {filename!r}")
        tampered = json.loads(original)
        tampered["passive_carries"].append(dict(tampered["passive_carries"][0]))
        receipt_file.write_bytes(json.dumps(tampered).encode("utf-8"))
        self.assertEqual(self.blocked("finish-inspect", project_dir=NEXT, receipt=prepared)["reason"],
                         "inspection receipt has an invalid passive carry: 'DOCS-AUDIT-old.md'")
        self.assertFalse((self.coordinator / NEXT / "research").exists())
        self.assertFalse((self.base / "DOCS-AUDIT-old.md").exists())
        receipt_file.write_bytes(original)
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        self.assertEqual((self.coordinator / NEXT / "research" / "DOCS-AUDIT-old.md").read_bytes(),
                         (active / "DOCS-AUDIT-old.md").read_bytes())
        self.assertEqual(outside.read_text(encoding="utf-8"), "# Not an audit\n")

    def test_active_member_receipt_refuses_passive_carries(self) -> None:
        self.build(("web",))
        write(self.coordinator / ".project" / "research" / "DOCS-AUDIT-old.md",
              audit_text(str(self.base / "old-checkout"), "a" * 40, ["README.md"], (RULING,), "old"))
        prepared = self.complete("prepare-inspect")["inspection"]
        self.assertEqual(prepared["passive_carries"], [])
        self.write_map(prepared, "web")
        receipt_file = Path(prepared["receipt_file"])
        tampered = json.loads(receipt_file.read_text(encoding="utf-8"))
        tampered["passive_carries"] = [{
            "filename": "DOCS-AUDIT-old.md", "source": ".project/research/DOCS-AUDIT-old.md",
            "destination": ".project/research/DOCS-AUDIT-old.md", "reused": False,
            "source_sha256": "0" * 64, "destination_sha256": None}]
        receipt_file.write_bytes(json.dumps(tampered).encode("utf-8"))
        self.assertEqual(self.blocked("advance-inspect", receipt=prepared)["reason"],
                         "inspection receipt has invalid passive carries")

    def test_member_lookahead_reuses_or_refuses_track_local_queues_by_their_rulings(self) -> None:
        self.build_lookahead(("web",))
        active = self.coordinator / ".project" / "research"
        removed = audit_text(str(self.base / "old-checkout"), "a" * 40, ["README.md"], (RULING,), "old")
        write(active / "DOCS-AUDIT-old.md", removed)
        write(active / "DOCS-AUDIT-web.md",
              audit_text(str(self.checkouts["web"]), self.pins["web"], ["README.md"], (RULING,), "web"))
        research = self.coordinator / NEXT / "research"
        local_old = write(research / "DOCS-AUDIT-old.md", removed.replace(RULING, ""))
        self.assertEqual(self.blocked("prepare-inspect", project_dir=NEXT)["reason"],
                         "lookahead DOCS-AUDIT-old.md does not retain the active audit rulings")
        local_old.write_bytes(removed.replace(RULING, DONE_RULING).encode("utf-8"))
        local_web = write(research / "DOCS-AUDIT-web.md", audit_text(
            str(self.checkouts["web"]), self.pins["web"], ["README.md"], (), "web"))
        self.assertEqual(self.blocked("prepare-inspect", project_dir=NEXT)["reason"],
                         "lookahead DOCS-AUDIT-web.md does not retain the active audit rulings")
        local_web.write_bytes(audit_text(str(self.checkouts["web"]), self.pins["web"], ["README.md"],
                                         (DONE_RULING,), "web").encode("utf-8"))
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        carry = prepared["passive_carries"][0]
        self.assertEqual((carry["filename"], carry["reused"]), ("DOCS-AUDIT-old.md", True))
        self.assertEqual(carry["destination_sha256"], hashlib.sha256(local_old.read_bytes()).hexdigest())
        web = prepared["members"][0]
        self.assertEqual(web["prior_audit"]["source"], str(local_web))
        self.assertEqual(web["docs_destination_sha256"], hashlib.sha256(local_web.read_bytes()).hexdigest())
        self.write_map(prepared, "web")
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        self.write_audit(advanced, "web", rulings=(DONE_RULING,))
        kept = local_old.read_bytes()
        self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        self.assertEqual(local_old.read_bytes(), kept)
        self.assertIn(DONE_RULING, local_web.read_text(encoding="utf-8"))

    def test_member_lookahead_entry_requires_active_program_build_and_its_branch(self) -> None:
        self.build_lookahead(("web",))
        project = self.coordinator / ".project"
        state = (project / "STATE.md").read_bytes()
        cases = (
            ("active plan/active", lambda: write(project / "STATE.md", state_text("plan", "active")),
             "lookahead member inspection requires active program build/active"),
            ("no ROADMAP", lambda: (project / "ROADMAP.md").rename(project / "ROADMAP.off"),
             "lookahead member inspection requires active program build/active"),
            ("next not inspect", lambda: write(project / "next" / "STATE.md",
                                               state_text("define", "active", branch=None, milestone="next")),
             "lookahead member inspection requires next inspect/active"),
            ("another branch", lambda: git(self.coordinator, "checkout", "-q", "-b", "feature/milestone-002"),
             "current branch differs from active inspection branch"),
        )
        for label, change, message in cases:
            with self.subTest(label):
                change()
                self.assertEqual(self.blocked("prepare-inspect", project_dir=NEXT)["reason"], message)
                (project / "STATE.md").write_bytes(state)
                if (project / "ROADMAP.off").exists():
                    (project / "ROADMAP.off").rename(project / "ROADMAP.md")
                write(project / "next" / "STATE.md", state_text("inspect", "active", branch=None, milestone="next"))
        self.assertEqual(git(self.coordinator, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertFalse((project / "next" / "research").exists())

    def test_member_lookahead_ref_move_does_not_change_receipt_pin(self) -> None:
        self.build_lookahead(("web",))
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        pin = prepared["members"][0]["pin"]
        moved = self.advance_origin("web")
        self.assertNotEqual(moved, pin)
        self.write_map(prepared, "web", pin=pin)
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        self.assertEqual(advanced["members"][0]["pin"], pin)
        self.write_audit(advanced, "web")
        finished = self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        self.assertEqual(finished["coverage"]["stale"], ["web"])
        evidence = (self.coordinator / NEXT / "research" / "evidence-codebase-web.md").read_text(encoding="utf-8")
        self.assertIn(f"Member HEAD: {pin}", evidence)
        self.assertEqual(self.state(NEXT)["status"], "done")

    def test_brownfield_coordinator_lookahead_pair_carries_the_active_audit(self) -> None:
        self.build_lookahead(("web",), brownfield=True)
        active = write(self.coordinator / ".project" / "research" / "DOCS-AUDIT.md",
                       audit_text(str(self.coordinator), self.head, ["README.md"], (RULING,)))
        prepared = self.complete("prepare-inspect", project_dir=NEXT)["inspection"]
        self.assertTrue(prepared["coordinator"]["assigned"])
        self.assertEqual(prepared["passive_carries"], [])
        self.assertEqual(prepared["coordinator"]["prior_audit"]["source"], str(active))
        docs = self.assignment(prepared, "inspect_docs")
        self.assertEqual(docs["output"], ".project/next/research/DOCS-AUDIT.md")
        self.write_map(prepared, "web")
        mapper = self.assignment(prepared, "inspect_codebase")
        write(Path(mapper["worktree"]) / mapper["output"], map_text(str(self.coordinator)))
        inventory = Path(prepared["inventory_file"]).read_text(encoding="utf-8").splitlines()
        write(Path(docs["worktree"]) / docs["output"],
              audit_text(str(self.coordinator), self.head, inventory, (RULING,)))
        advanced = self.complete("advance-inspect", project_dir=NEXT, receipt=prepared)["inspection"]
        self.write_audit(advanced, "web")
        self.complete("finish-inspect", project_dir=NEXT, receipt=advanced)
        research = self.coordinator / NEXT / "research"
        self.assertIn(RULING, (research / "DOCS-AUDIT.md").read_text(encoding="utf-8"))
        self.assertTrue((research / "evidence-codebase.md").is_file())
        self.assertEqual(self.state(NEXT)["status"], "done")
        self.assert_no_sidecars()

    def test_no_members_lookahead_retains_manual_workflow_refusal(self) -> None:
        coordinator = self.coordinator
        git(self.base, "init", "-q", "-b", BRANCH, str(coordinator))
        write(coordinator / "README.md", "# acme program\n\nCoordinates the acme services.\n")
        write(coordinator / ".project" / "STATE.md", state_text("build", "active"))
        write(coordinator / ".project" / "CHARTER.md", "# Charter — acme\n\nProgram scope.\n")
        write(coordinator / ".project" / "ROADMAP.md", "# Roadmap — acme\n\n1. demo\n")
        write(coordinator / NEXT / "STATE.md", state_text("inspect", "active", branch=None, milestone="next"))
        git(coordinator, "add", "-A")
        git(coordinator, "commit", "-q", "-m", "init")
        self.head = git(coordinator, "rev-parse", "HEAD")
        self.assertEqual(self.blocked("prepare-inspect", project_dir=NEXT)["reason"],
                         "initial inspection requires the active inspect track")
        receipt = {"receipt_file": str(self.base / "inspection.json")}
        self.assertEqual(self.blocked("finish-inspect", project_dir=NEXT, receipt=receipt)["reason"],
                         "finish-inspect requires the prepared active track and mapper review")
        self.assertEqual(self.blocked("advance-inspect", project_dir=NEXT, receipt=receipt)["reason"],
                         "advance-inspect requires member inspection; .project/MEMBERS.md is absent")
        self.assertFalse((self.base / "workspace").exists())


if __name__ == "__main__":
    unittest.main()
