"""Parking lot (ADR 0007): capture, triage, settlement and the boundary gates."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import check_handoffs, parking, pipeline_state, pr_fix_lane
import tests.test_handoffs as handoff_fixtures
from tests.test_pipeline_state import ENFORCED_INTENT, current_fields, record_lens, state_text

PULL = "https://github.com/acme/web/pull/7"


def make_repo(tmp: str, number: int = 3, phase: str = "define", status: str = "active",
              roadmap: "list[tuple[int, str]] | None" = None) -> Path:
    repo = Path(tmp)
    subprocess.run(("git", "init", "-q", "-b", f"feature/milestone-{number:03d}", str(repo)), check=True)
    project = repo / ".project"
    project.mkdir()
    (project / "STATE.md").write_text(state_text(
        milestone="third", phase=phase, status=status, branch=f"feature/milestone-{number:03d}"), encoding="utf-8")
    if roadmap is not None:
        (project / "CHARTER.md").write_text("# Charter\n", encoding="utf-8")
        blocks = "".join(f"### M{entry:03d} — m{entry}\n\nStatus: {value}\nArchive: null\n\n"
                         for entry, value in roadmap)
        (project / "ROADMAP.md").write_text(f"# Roadmap\n\n## Milestones\n\n{blocks}", encoding="utf-8")
    return repo


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def park(repo: Path, *items: dict) -> list:
    payload = {"verbatim": "Eu quero todos os itens.", "items": list(items)}
    return parking.add(repo, write_json(repo / "items.json", payload))["added"]


def item(source: str, klass: str = "correctness", title: str = "Validate the flag") -> dict:
    return {"title": title, "class": klass, "source": source, "repo": "web", "location": "src/a.py:3",
            "found": "The flag is accepted without validation."}


def decide(repo: Path, *rows: dict, verbatim: str = "Planeje K001 agora.") -> dict:
    return parking.triage(repo, write_json(repo / "ruling.json", {"verbatim": verbatim, "items": list(rows)}))


def task(identifier: str, status: str, context: str) -> str:
    return (f"---\nid: {identifier}\ntitle: t\nwave: 1\ndeps: []\nstatus: {status}\n---\n\n# {identifier}\n\n"
            f"## Context\n\n{context}\n\n## Approach\n\nx\n")


class ModelTests(unittest.TestCase):
    def test_render_and_parse_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            self.assertEqual(park(repo, item(f"{PULL}#discussion_r1"), item(f"{PULL}#discussion_r2", "security")),
                             ["K001", "K002"])
            items = parking.load(repo)
            self.assertEqual([(row["id"], row["status"], row["class"]) for row in items],
                             [("K001", "open", "correctness"), ("K002", "open", "security")])
            self.assertIn('parked: "Eu quero todos os itens."', items[0]["decisions"][0])
            self.assertEqual(parking.render(items), (repo / parking.RELATIVE).read_text(encoding="utf-8"))

    def test_add_skips_a_source_already_parked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"))
            self.assertEqual(park(repo, item("s1"), item("s2")), ["K002"])

    def test_malformed_lot_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"))
            path = repo / parking.RELATIVE
            path.write_text(path.read_text(encoding="utf-8").replace("Status**: open", "Status**: later"),
                            encoding="utf-8")
            with self.assertRaisesRegex(parking.ParkingError, "invalid Status"):
                parking.load(repo)

    def test_writes_wait_for_a_boundary_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, phase="build")
            with self.assertRaisesRegex(parking.ParkingError, "STATE is build"):
                park(repo, item("s1"))
            self.assertFalse((repo / parking.RELATIVE).exists())


class TriageTests(unittest.TestCase):
    def test_decisions_record_the_owner_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"), item("s2"), item("s3"))
            decide(repo, {"id": "K001", "decision": "planned"}, {"id": "K002", "decision": "scheduled",
                                                               "milestone": "M005"},
                   {"id": "K003", "decision": "dropped"}, verbatim="K001 agora, K002 no M005, K003 não.")
            items = parking.load(repo)
            self.assertEqual([row["status"] for row in items], ["planned M003", "scheduled M005", "dropped"])
            self.assertTrue(items[1]["decisions"][-1].endswith('scheduled M005: "K001 agora, K002 no M005, K003 não."'))
            with self.assertRaisesRegex(parking.ParkingError, "already dropped"):
                decide(repo, {"id": "K003", "decision": "planned"})

    def test_risk_items_need_accept_risk_to_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1", "security"), item("s2", "data-loss"))
            for row in ("K001", "K002"):
                with self.assertRaisesRegex(parking.ParkingError, "needs accept_risk"):
                    decide(repo, {"id": row, "decision": "scheduled", "milestone": "M004"})
            decide(repo, {"id": "K001", "decision": "scheduled", "milestone": "M004", "accept_risk": True})
            self.assertIn("accept-risk", parking.load(repo)[0]["decisions"][-1])

    def test_scheduling_needs_a_later_pending_roadmap_milestone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, roadmap=[(1, "shipped"), (2, "shipped"), (3, "active"), (4, "pending")])
            park(repo, item("s1"))
            with self.assertRaisesRegex(parking.ParkingError, "later milestone"):
                decide(repo, {"id": "K001", "decision": "scheduled", "milestone": "M003"})
            with self.assertRaisesRegex(parking.ParkingError, "M005 is not a pending ROADMAP milestone"):
                decide(repo, {"id": "K001", "decision": "scheduled", "milestone": "M005"})
            decide(repo, {"id": "K001", "decision": "scheduled", "milestone": "M004"})


class GateTests(unittest.TestCase):
    def test_define_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, number=4, roadmap=[(3, "shipped"), (4, "active"), (5, "pending")])
            self.assertEqual(parking.define_errors(repo), [])
            park(repo, item("s1"), item("s2"))
            self.assertEqual(len(parking.define_errors(repo)), 2)
            decide(repo, {"id": "K001", "decision": "planned"},
                   {"id": "K002", "decision": "scheduled", "milestone": "M005"})
            self.assertEqual(parking.define_errors(repo), [])
            # The roadmap re-slice removed M005: the scheduled item must be decided again.
            roadmap = repo / ".project/ROADMAP.md"
            roadmap.write_text(roadmap.read_text(encoding="utf-8").replace("Status: pending", "Status: abandoned"),
                               encoding="utf-8")
            self.assertRegex(" ".join(parking.define_errors(repo)), "K002 .* no longer a pending ROADMAP")

    def test_define_gate_catches_a_due_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"))
            decide(repo, {"id": "K001", "decision": "scheduled", "milestone": "M004"})
            state = repo / ".project/STATE.md"
            subprocess.run(("git", "-C", tmp, "checkout", "-q", "-b", "feature/milestone-004"), check=True)
            state.write_text(state.read_text(encoding="utf-8").replace("milestone-003", "milestone-004"),
                             encoding="utf-8")
            self.assertRegex(parking.define_errors(repo)[0], "K001 was scheduled for M004")

    def test_plan_gate_needs_a_citing_task_both_ways(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"), item("s2"))
            decide(repo, {"id": "K001", "decision": "planned"}, {"id": "K002", "decision": "dropped"})
            self.assertEqual(parking.plan_errors(repo, [{"id": "T001", "status": "pending", "context": "x"}]),
                             ["K001 is planned for M003 but no task cites it in its Context"])
            withdrawn = [{"id": "T001", "status": "withdrawn", "context": "Closes K001."}]
            self.assertEqual(len(parking.plan_errors(repo, withdrawn)), 1)
            self.assertEqual(parking.plan_errors(repo, [{"id": "T001", "status": "done", "context": "Closes K001."}]), [])
            self.assertEqual(parking.plan_errors(repo, [{"id": "T001", "status": "pending", "context": "K001 K002"}]),
                             ["T001 cite K002, which is not planned for M003"])

    def test_validate_plan_reports_the_parking_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = handoff_fixtures.HandoffValidationTests()
            fixture.write_plan_handoff(root)
            subprocess.run(("git", "init", "-q", "-b", "feature/milestone-001", tmp), check=True)
            park(root, item("s1"))
            decide(root, {"id": "K001", "decision": "planned"})
            with self.assertRaisesRegex(check_handoffs.HandoffError, "parking lot: K001 is planned for M001"):
                check_handoffs.validate_plan(root)
            task = next((root / ".project/tasks").glob("T001-*.md"))
            text = task.read_text(encoding="utf-8")
            task.write_text(text.replace("## Context\n", "## Context\n\nCloses K001.\n", 1), encoding="utf-8")
            self.assertEqual(check_handoffs.validate_plan(root)["tasks"], 2)

    def test_define_approval_refuses_undecided_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            (repo / ".project/intent").mkdir()
            (repo / ".project/intent/INTENT.md").write_text(ENFORCED_INTENT, encoding="utf-8")
            park(repo, item("s1"))
            record_lens(repo, "define")
            approve = lambda: pipeline_state.transition_state(
                repo, current_fields(repo), {"status": "done"}, "milestone intent approved")
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "parking lot: K001 is open"):
                approve()
            decide(repo, {"id": "K001", "decision": "dropped"}, verbatim="Pode descartar.")
            self.assertEqual(approve()["state"]["status"], "done")


    def test_build_start_refuses_an_undecided_or_uncited_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, phase="plan")
            park(repo, item("s1"))
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "parking lot: K001 is open"):
                pipeline_state._require_parking_decided(repo, building=True)
            decide(repo, {"id": "K001", "decision": "planned"})
            with self.assertRaisesRegex(pipeline_state.PipelineStateError, "K001 is planned for M003 but no task"):
                pipeline_state._require_parking_decided(repo, building=True)
            (repo / ".project/tasks").mkdir()
            (repo / ".project/tasks/T001-t.md").write_text(task("T001", "pending", "Closes K001."), encoding="utf-8")
            pipeline_state._require_parking_decided(repo, building=True)


class SettlementTests(unittest.TestCase):
    def archive(self, repo: Path, number: int, *tasks: str) -> None:
        directory = repo / f".project/archive/{number:03d}-m{number}/tasks"
        directory.mkdir(parents=True)
        for index, text in enumerate(tasks, start=1):
            (directory / f"T{index:03d}-t.md").write_text(text, encoding="utf-8")

    def next_milestone(self, repo: Path, number: int) -> None:
        subprocess.run(("git", "-C", str(repo), "checkout", "-q", "-b", f"feature/milestone-{number:03d}"), check=True)
        state = repo / ".project/STATE.md"
        text = state.read_text(encoding="utf-8")
        state.write_text(text.replace(f"milestone-{number - 1:03d}", f"milestone-{number:03d}"), encoding="utf-8")

    def test_sync_settles_planned_items_from_the_archived_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"), item("s2"))
            decide(repo, {"id": "K001", "decision": "planned"}, {"id": "K002", "decision": "planned"})
            self.archive(repo, 3, task("T001", "done", "Closes K001."), task("T002", "withdrawn", "Closes K002."))
            self.next_milestone(repo, 4)
            result = parking.sync(repo)
            self.assertEqual((result["settled"], result["reopened"], result["open"]), (["K001"], ["K002"], ["K002"]))
            items = parking.load(repo)
            self.assertEqual(items[0]["status"], "done M003")
            self.assertIn("done in M003 by T001", items[0]["decisions"][-1])

    def test_sync_imports_pr_round_follow_ups_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            directory = pr_fix_lane.lane_root(repo) / "002-m2" / "round-1"
            directory.mkdir(parents=True)
            write_json(directory / "round.json", {"round": 1, "pull_request": PULL, "member": "web"})
            write_json(directory / "snapshot.json", {"items": [
                {"id": "C21", "kind": "inline", "location": "src/a.py:3", "link": f"{PULL}#discussion_r21",
                 "text": "Validate the **flag**."},
                {"id": "R11", "kind": "review", "location": "none", "link": "none", "text": "Indexes later."},
                {"id": "C22", "kind": "inline", "location": "src/b.py:1", "link": f"{PULL}#discussion_r22",
                 "text": "Fixed already."}]})
            write_json(directory / "ruling.json", {"verbatim": "C22 agora; o resto depois.", "items": [
                {"id": "C21", "class": "follow-up", "note": "later", "risk": "security"},
                {"id": "R11", "class": "follow-up", "note": "later"},
                {"id": "C22", "class": "fix", "fix_direction": "x"}]})
            self.assertEqual(parking.sync(repo)["imported"], ["K001", "K002"])
            self.assertEqual(parking.sync(repo)["imported"], [])
            items = parking.load(repo)
            self.assertEqual([(row["class"], row["source"]) for row in items],
                             [("security", f"{PULL}#discussion_r21"), ("other", f"{PULL}#R11")])
            self.assertIn('"C22 agora; o resto depois."', items[0]["decisions"][0])

    def test_program_completion_needs_every_item_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            park(repo, item("s1"), item("s2"), item("s3"))
            decide(repo, {"id": "K001", "decision": "planned"}, {"id": "K002", "decision": "dropped"},
                   {"id": "K003", "decision": "scheduled", "milestone": "M004"})
            self.archive(repo, 3, task("T001", "done", "Closes K001."))
            self.assertEqual(parking.program_errors(repo), ["K003 is scheduled M004 at program completion"])


if __name__ == "__main__":
    unittest.main()
