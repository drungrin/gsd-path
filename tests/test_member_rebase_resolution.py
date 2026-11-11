"""Owner-ruled member rebase adoption with a reviewed conflict resolution.

A conflicting replay is adopted only with --resolve-conflicts: every
conflicting commit is replayed favoring the member side of its conflicting
hunks (a deterministic merge each validator recomputes), the helper stops with
the replay worktree, and one reviewed, owner-ruled resolution commit limited to
the conflicting paths restores what the default branch needs there.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from scripts import build_state, integration, isolation, lean_verification, members
from tests.test_member_rebase_adoption import (
    BOUND,
    ISOLATION,
    REPLAY,
    RULING,
    TASKS,
    MemberRebaseFixture,
    git,
)

RESOLUTION_RULING = "Accept the reviewed resolution that keeps both the team and the milestone edits."
RESOLVED = "team edit\nv2\n"


class MemberRebaseResolutionFixture(MemberRebaseFixture):
    def setUp(self) -> None:
        super().setUp()
        # T001 changes app.py from v1 to v2; the team changed the same line.
        self.onto = self.advance_main("app.py", "team edit\n")

    def adopt(self, onto=None, ruling: str = RULING, resolve: bool = True, resolution_ruling=None) -> dict:
        return isolation.adopt_member_rebase(self.coordinator, "web", onto or self.onto, ruling,
                                             resolve, resolution_ruling)

    def review_path(self) -> Path:
        return self.coordinator / ".project" / isolation.member_rebase_review_path("web", self.old_tip)

    def write_review(self, tree: str, verdict: str = "Verdict: pass") -> None:
        path = self.review_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Resolution review\n\nResolution-Tree: {tree}\n{verdict}\n\nBoth sides kept.\n",
                        encoding="utf-8")

    def resolve(self, text: str = RESOLVED) -> dict:
        waiting = self.adopt()
        self.assertEqual(waiting["verdict"], "ready-for-review", waiting)
        (Path(waiting["worktree"]) / "app.py").write_text(text, encoding="utf-8")
        candidate = isolation.member_rebase_resolution(self.coordinator, "web")
        self.write_review(candidate["tree"])
        return candidate


class MemberRebaseResolutionTests(MemberRebaseResolutionFixture):
    def test_conflict_without_the_flag_is_refused_and_names_the_option(self) -> None:
        before = self.snapshot()
        with self.assertRaises(isolation.MemberRebaseError) as error:
            isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING)
        self.assertEqual(error.exception.code, "conflict")
        self.assertIn("--resolve-conflicts", str(error.exception))
        self.assertIn("app.py", str(error.exception))
        self.assertEqual(self.snapshot(), before)

    def test_preview_with_the_flag_reports_the_conflicts_read_only(self) -> None:
        before = self.snapshot()
        preview = isolation.preview_member_rebase(self.coordinator, "web", self.onto, RULING, True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(preview["verdict"], "needs-resolution")
        self.assertEqual(preview["conflicts"], [{"original": self.landings["T001"]["landing"], "paths": ["app.py"]}])
        self.assertTrue(any("--resolution-ruling" in effect for effect in preview["effects"]))

    def test_apply_stops_with_the_member_side_and_publishes_nothing(self) -> None:
        waiting = self.adopt()
        self.assertEqual(waiting["verdict"], "ready-for-review")
        worktree = Path(waiting["worktree"])
        self.assertEqual((worktree / "app.py").read_text(encoding="utf-8"), "v2\n")
        self.assertEqual((worktree / "config.py").read_text(encoding="utf-8"), "c2\n")
        self.assertEqual(waiting["paths"], ["app.py"])
        self.assertEqual(waiting["changed_paths"], [])
        self.assertEqual(waiting["tree"], git(self.member, "rev-parse", waiting["replay_tip"] + "^{tree}"))
        self.assertEqual(waiting["conflicts"][0]["sides"]["default"], self.onto)
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertFalse(self.receipt_path().exists())
        journal = json.loads(self.journal().read_text(encoding="utf-8"))
        self.assertEqual(journal["stage"], "awaiting-resolution")
        # A rerun without the resolution ruling only reports the same candidate.
        self.assertEqual(self.adopt()["tree"], waiting["tree"])
        # Other phase work sees the pending adoption.
        with self.assertRaises(isolation.IsolationError):
            isolation.require_no_member_rebase(self.coordinator, "web")

    def test_reviewed_resolution_is_committed_published_and_proved(self) -> None:
        candidate = self.resolve()
        self.assertEqual(candidate["changed_paths"], ["app.py"])
        result = self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(result["verdict"], "adopted")
        new_tip = self.bound_tip()
        self.assertEqual(result["new_tip"], new_tip)
        rebased = git(self.member, "rev-list", "--first-parent", "--reverse", f"{self.onto}..{new_tip}").split()
        self.assertEqual(len(rebased), 3)
        self.assertEqual(rebased[-1], result["resolution"]["commit"])
        self.assertTrue(git(self.member, "log", "-1", "--format=%s", new_tip).startswith(
            "fix: resolve integration conflicts with origin/main"))
        self.assertEqual(git(self.member, "show", f"{new_tip}:app.py") + "\n", RESOLVED)
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), new_tip)
        self.assertEqual((self.bound / "app.py").read_text(encoding="utf-8"), RESOLVED)
        receipt = json.loads(self.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], isolation.MEMBER_REBASE_SCHEMA_V2)
        resolution = receipt["adoptions"][0]["resolution"]
        self.assertEqual(resolution["ruling"], RESOLUTION_RULING)
        self.assertEqual(resolution["tree"], candidate["tree"])
        self.assertEqual(resolution["conflicts"], [{"original": self.landings["T001"]["landing"],
                                                    "paths": ["app.py"]}])
        self.assertEqual(receipt["adoptions"][0]["ruling"], RULING)
        head = git(self.coordinator, "rev-parse", "HEAD")
        self.assertEqual(sorted(git(self.coordinator, "diff-tree", "--no-commit-id", "--name-only", "-r", head).split()),
                         sorted([members.LOCK_PATH, isolation.MEMBER_REBASE_PATH,
                                 ".project/" + resolution["review"]["path"]]))
        self.assertIn("Resolution-Review: .project/", git(self.coordinator, "log", "-1", "--format=%B"))
        self.assertEqual(git(self.coordinator, "status", "--porcelain"), "")
        self.assertFalse(self.journal().exists())
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertFalse(Path(candidate["worktree"]).exists())
        verdicts = self.verify()
        self.assertTrue(all(row["verdict"] == "recovered" for row in verdicts), verdicts)
        self.assertEqual([row["landing"] for row in verdicts], rebased[:2])
        self.assertEqual(isolation.validate_member_rebase(self.coordinator)["status"], "verified")
        self.set_state("ship", "active")
        head = git(self.coordinator, "rev-parse", "HEAD")
        lean_verification._require_ship_inputs(self.coordinator, head)
        self.assertEqual(build_state.verify_landed_tasks(str(self.coordinator), ".project", head)
                         ["member_rebase"]["status"], "verified")
        self.assertEqual(isolation.member_mergeability(self.coordinator)["members"][0]["status"], "current")

    def test_accepting_the_member_side_needs_no_resolution_commit(self) -> None:
        waiting = self.adopt()
        self.write_review(waiting["tree"])
        result = self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertIsNone(result["resolution"]["commit"])
        rebased = git(self.member, "rev-list", "--first-parent", "--reverse", f"{self.onto}..{self.bound_tip()}").split()
        self.assertEqual(len(rebased), 2)
        self.assertEqual(git(self.member, "show", f"{self.bound_tip()}:app.py"), "v2")
        self.assertEqual(isolation.validate_member_rebase(self.coordinator)["status"], "verified")

    def test_resolution_outside_the_conflicting_paths_is_refused(self) -> None:
        waiting = self.adopt()
        (Path(waiting["worktree"]) / "config.py").write_text("hijack\n", encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as status:
            isolation.member_rebase_resolution(self.coordinator, "web")
        self.assertEqual(status.exception.code, "resolution-scope")
        self.write_review("0" * 40)
        with self.assertRaises(isolation.MemberRebaseError) as applied:
            self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(applied.exception.code, "resolution-scope")
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")

    def test_conflict_markers_are_refused(self) -> None:
        waiting = self.adopt()
        (Path(waiting["worktree"]) / "app.py").write_text("<<<<<<< ours\nteam edit\n=======\nv2\n>>>>>>> theirs\n",
                                                           encoding="utf-8")
        candidate = isolation.member_rebase_resolution(self.coordinator, "web")
        self.assertEqual((candidate["verdict"], candidate["markers"]), ("unresolved", ["app.py"]))
        self.write_review(candidate["tree"])
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(error.exception.code, "resolution-unresolved")

    def test_review_must_exist_pass_and_bind_the_exact_tree(self) -> None:
        waiting = self.adopt()
        worktree = Path(waiting["worktree"])
        (worktree / "app.py").write_text(RESOLVED, encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as missing:
            self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(missing.exception.code, "review-required")
        candidate = isolation.member_rebase_resolution(self.coordinator, "web")
        for tree, verdict in ((waiting["tree"], "Verdict: pass"), (candidate["tree"], "Verdict: fail")):
            with self.subTest(tree=tree, verdict=verdict):
                self.write_review(tree, verdict)
                with self.assertRaises(isolation.MemberRebaseError) as error:
                    self.adopt(resolution_ruling=RESOLUTION_RULING)
                self.assertEqual(error.exception.code, "review-mismatch")
        with self.assertRaises(isolation.MemberRebaseError) as empty:
            self.adopt(resolution_ruling="  ")
        self.assertEqual(empty.exception.code, "ruling-required")
        # A change after the review needs another review.
        self.write_review(candidate["tree"])
        (worktree / "app.py").write_text("team edit\nv2\nmore\n", encoding="utf-8")
        with self.assertRaises(isolation.MemberRebaseError) as stale:
            self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(stale.exception.code, "review-mismatch")
        (worktree / "app.py").write_text(RESOLVED, encoding="utf-8")
        self.assertEqual(self.adopt(resolution_ruling=RESOLUTION_RULING)["verdict"], "adopted")

    def test_resolution_ruling_without_an_awaiting_adoption_is_refused(self) -> None:
        with self.assertRaises(isolation.MemberRebaseError) as error:
            self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(error.exception.code, "arguments")
        self.assertFalse(self.journal().exists())

    def test_abort_while_awaiting_resolution_rolls_back_the_replay(self) -> None:
        waiting = self.adopt()
        (Path(waiting["worktree"]) / "app.py").write_text(RESOLVED, encoding="utf-8")
        self.assertEqual(isolation.abort_member_rebase(self.coordinator, "web")["verdict"], "aborted")
        self.assertFalse(self.journal().exists())
        self.assertFalse(Path(waiting["worktree"]).exists())
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertEqual(self.bound_tip(), self.old_tip)
        self.assertEqual(self.remote_ref(f"refs/heads/{BOUND}"), "")

    def test_interrupted_after_the_resolution_commit_resumes(self) -> None:
        self.resolve()
        real = isolation._write_member_rebase_journal

        def crash(path, journal):
            if journal.get("resolution") and journal["stage"] == "replayed":
                raise KeyboardInterrupt
            return real(path, journal)

        with mock.patch.object(isolation, "_write_member_rebase_journal", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(json.loads(self.journal().read_text())["stage"], "awaiting-resolution")
        result = self.adopt(resolution_ruling=RESOLUTION_RULING)
        self.assertEqual(result["verdict"], "adopted")
        self.assertEqual(git(self.member, "show", f"{self.bound_tip()}:app.py") + "\n", RESOLVED)

    def test_abort_after_an_unrecorded_resolution_commit_cleans_the_replay(self) -> None:
        candidate = self.resolve()
        real = isolation._write_member_rebase_journal

        def crash(path, journal):
            if journal.get("resolution"):
                raise KeyboardInterrupt
            return real(path, journal)

        with mock.patch.object(isolation, "_write_member_rebase_journal", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.adopt(resolution_ruling=RESOLUTION_RULING)
        tip = git(self.member, "rev-parse", f"refs/heads/{REPLAY}")
        self.assertNotEqual(tip, candidate["replay_tip"])
        self.assertEqual(isolation.abort_member_rebase(self.coordinator, "web")["verdict"], "aborted")
        self.assertEqual(git(self.member, "branch", "--list", REPLAY), "")
        self.assertFalse(self.journal().exists())
        self.assertEqual(self.bound_tip(), self.old_tip)

    def test_tampered_resolution_evidence_is_rejected(self) -> None:
        self.resolve()
        self.adopt(resolution_ruling=RESOLUTION_RULING)
        original = self.receipt_path().read_text(encoding="utf-8")
        tampers = {
            "tree": lambda value: value["adoptions"][0]["resolution"].update(tree="0" * 40),
            "paths": lambda value: value["adoptions"][0]["resolution"]["conflicts"][0].update(paths=["config.py"]),
            "dropped": lambda value: value["adoptions"][0].pop("resolution"),
            "schema": lambda value: value.update(schema=isolation.MEMBER_REBASE_SCHEMA),
        }
        for name, tamper in tampers.items():
            with self.subTest(name=name):
                isolation._ADOPTION_MEMO.clear()
                receipt = json.loads(original)
                tamper(receipt)
                self.receipt_path().write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                with self.assertRaises(isolation.IsolationError):
                    isolation.validate_member_rebase(self.coordinator)
        self.receipt_path().write_text(original, encoding="utf-8")
        isolation._ADOPTION_MEMO.clear()
        self.assertEqual(isolation.validate_member_rebase(self.coordinator)["status"], "verified")
        self.review_path().write_text("Verdict: pass\n", encoding="utf-8")
        with self.assertRaisesRegex(isolation.IsolationError, "recorded digest"):
            isolation.validate_member_rebase(self.coordinator)

    def test_archived_resolution_closes_through_the_new_tip(self) -> None:
        self.resolve()
        result = self.adopt(resolution_ruling=RESOLUTION_RULING)
        archive = ".project/archive/001-demo"
        destination = self.coordinator / archive
        destination.mkdir(parents=True)
        (self.coordinator / ".project/build").rename(destination / "build")
        review = destination / "review"
        review.mkdir()
        (review / "FINAL.md").write_text(
            f"# Final\n\nReviewed HEAD: {git(self.coordinator, 'rev-parse', 'HEAD')}\n"
            f"Member reviewed HEAD: web {result['new_tip']}\n", encoding="utf-8")
        self.assertEqual(isolation.validate_member_rebase(self.coordinator, archive)["status"], "verified")
        state = mock.Mock(phase="ship", status="active", archive=archive, integration="direct")
        with mock.patch.object(integration.archive_milestone, "strict_state", return_value=(state, None, None)):
            closed = integration.close_members(self.coordinator)
        self.assertEqual(closed["status"], "integrated")
        self.assertEqual(git(self.remote, "rev-parse", "main^2"), result["new_tip"])

    def test_non_content_conflict_is_still_refused(self) -> None:
        team = self.root / "team-delete"
        git(self.root, "clone", "-q", str(self.remote), str(team))
        git(team, "rm", "-q", "app.py")
        git(team, "-c", "user.name=team", "-c", "user.email=team@example.test", "commit", "-q", "-m", "drop app")
        git(team, "push", "-q", "origin", "main")
        onto = git(team, "rev-parse", "HEAD")
        with self.assertRaises(isolation.MemberRebaseError) as error:
            isolation.preview_member_rebase(self.coordinator, "web", onto, RULING, True)
        self.assertEqual(error.exception.code, "conflict")
        self.assertIn("cannot resolve", str(error.exception))

    def test_cli_resolution_flow(self) -> None:
        def run(command, *arguments):
            return subprocess.run([sys.executable, "-B", str(ISOLATION), command, "--repo", str(self.coordinator),
                                   "--member", "web", *arguments], encoding="utf-8", capture_output=True, check=False)
        preview = run("adopt-member-rebase", "--onto", self.onto, "--ruling", RULING, "--resolve-conflicts", "--dry-run")
        self.assertEqual(json.loads(preview.stdout)["verdict"], "needs-resolution", preview.stderr)
        waiting = run("adopt-member-rebase", "--onto", self.onto, "--ruling", RULING, "--resolve-conflicts")
        self.assertEqual(json.loads(waiting.stdout)["verdict"], "ready-for-review", waiting.stderr)
        (Path(json.loads(waiting.stdout)["worktree"]) / "app.py").write_text(RESOLVED, encoding="utf-8")
        status = run("member-rebase-resolution")
        self.write_review(json.loads(status.stdout)["tree"])
        applied = run("adopt-member-rebase", "--onto", self.onto, "--ruling", RULING,
                      "--resolution-ruling", RESOLUTION_RULING)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["verdict"], "adopted")


class MemberRebaseResolutionRouteTests(MemberRebaseResolutionFixture):
    def test_prepare_final_routes_a_conflict_to_the_resolving_adoption(self) -> None:
        from scripts import workflow_run

        self.set_state("ship", "active")
        head = git(self.coordinator, "rev-parse", "HEAD")
        result = workflow_run.run_workflow(self.coordinator, "prepare-final", ".project", head)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("web vs origin/main in app.py", result["reason"])
        self.assertIn("--resolve-conflicts", result["next"])


if __name__ == "__main__":
    import unittest

    unittest.main()
