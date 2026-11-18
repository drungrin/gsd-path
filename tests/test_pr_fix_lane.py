"""PR fix lane (ADR 0006): entry, capsule, ruling, undo, hand-off, delta parsing and views."""

import json
import re
import subprocess
import tempfile
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from scripts import check_handoffs, members, pipeline_state, pr_fix_lane
import tests.test_member_integration as member_integration

git = member_integration.git
ARCHIVE = member_integration.ARCHIVE
PULL = "https://github.com/acme/web/pull/7"


def review(identifier, state, login="rev", submitted="2026-11-16T03:00:00Z", body="", commit="x"):
    return {"id": identifier, "state": state, "user": {"login": login}, "submitted_at": submitted,
            "body": body, "commit_id": commit, "html_url": f"{PULL}#pullrequestreview-{identifier}"}


def comment(identifier, review_id, body, path="src/a.py", line=3, reply_to=None):
    return {"id": identifier, "pull_request_review_id": review_id, "body": body, "path": path,
            "line": line, "in_reply_to_id": reply_to, "html_url": f"{PULL}#discussion_r{identifier}"}


class ReviewStateTests(unittest.TestCase):
    def test_latest_decisive_review_per_reviewer_decides(self) -> None:
        reviews = [review(1, "CHANGES_REQUESTED", "a", "2026-11-16T01:00:00Z"),
                   review(2, "COMMENTED", "a", "2026-11-16T02:00:00Z"),
                   review(3, "APPROVED", "b", "2026-11-16T01:00:00Z"),
                   review(4, "CHANGES_REQUESTED", "b", "2026-11-16T03:00:00Z"),
                   review(5, "CHANGES_REQUESTED", "c", "2026-11-16T01:00:00Z"),
                   review(6, "APPROVED", "c", "2026-11-16T04:00:00Z")]
        self.assertEqual(sorted(item["id"] for item in pr_fix_lane.active_change_requests(reviews)), [1, 4])

    def test_items_are_request_bodies_and_top_level_inline_comments(self) -> None:
        requests = [review(11, "CHANGES_REQUESTED", body="Fix the scope.\n\nDetails inline.")]
        comments = [comment(21, 11, "Missing **scope** check"), comment(22, 11, "reply", reply_to=21),
                    comment(23, 99, "other review"), comment(24, 11, "   ")]
        items = pr_fix_lane.review_items(requests, comments)
        self.assertEqual([item["id"] for item in items], ["R11", "C21"])
        self.assertEqual(items[1]["location"], "src/a.py:3")
        self.assertEqual(pr_fix_lane.one_line(items[0]["text"]), "Fix the scope. Details inline.")

    def test_a_reply_review_neither_clears_a_request_nor_adds_an_item(self) -> None:
        # GitHub files each inline reply under a new COMMENTED review by the replier (seen live).
        requests = [review(11, "CHANGES_REQUESTED", "rev", body="Fix it.")]
        reviews = requests + [review(12, "COMMENTED", "author", "2026-11-16T04:00:00Z")]
        comments = [comment(21, 11, "Missing scope check"), comment(22, 12, "Fixed in abc.", reply_to=21)]
        self.assertEqual([item["id"] for item in pr_fix_lane.active_change_requests(reviews)], [11])
        self.assertEqual([item["id"] for item in pr_fix_lane.review_items(requests, comments)], ["R11", "C21"])

    def test_a_reply_that_reopens_an_earlier_conversation_is_an_item(self) -> None:
        # Round 2: the reviewer answers inside the round-1 thread instead of opening a new one.
        requests = [review(31, "CHANGES_REQUESTED", body="Segunda rodada.")]
        comments = [comment(21, 11, "Falta escopo"), comment(22, 12, "Corrigi em abc.", reply_to=21),
                    comment(23, 31, "Ainda falta o prompt=login", reply_to=21),
                    comment(24, 31, "Novo ponto"), comment(25, 31, "detalhe do novo ponto", reply_to=24)]
        items = pr_fix_lane.review_items(requests, comments)
        self.assertEqual([item["id"] for item in items], ["R31", "C23", "C24"])
        self.assertEqual(pr_fix_lane.one_line(items[1]["text"]), "Ainda falta o prompt=login")

    def test_only_github_pull_requests(self) -> None:
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "GitHub pull requests only"):
            pr_fix_lane.pull_parts("https://dev.azure.com/o/p/_git/r/pullrequest/3")


RULING = {"verbatim": "Corrigir C21 e R11; o resto fica.",
          "items": [{"id": "R11", "class": "fix", "group": "G1", "title": "Exigir o escopo",
                     "fix_direction": "Declarar escopo na operação"},
                    {"id": "C21", "class": "fix", "group": "G1", "title": "Checar o escopo",
                     "fix_direction": "Recusar token sem escopo"}]}


class HandoffTextTests(unittest.TestCase):
    record = {"round": 1, "head": "a" * 40, "pull_request": PULL, "member": "web", "snapshot_digest": "d" * 64}
    snap = {"items": [{"id": "R11", "kind": "review", "location": "none", "link": "l1", "text": "Fix\nthe scope."},
                      {"id": "C21", "kind": "inline", "location": "src/a.py:3", "link": "l2",
                       "text": "Missing scope check"}]}

    def test_findings_copy_the_source_fields_the_patch_gate_reads(self) -> None:
        fixes = pr_fix_lane._validate_ruling(RULING, self.snap)
        source = pr_fix_lane.source_text(self.record, self.snap, fixes)
        findings = pr_fix_lane.findings_text(self.record, self.snap, fixes)
        for heading, block in check_handoffs._finding_blocks(findings):
            locator = check_handoffs._field(block, "Locator")
            evidence, direction = check_handoffs._source_repair_fields(
                ".project/review/pr-review-1.md", source, locator)
            self.assertEqual(check_handoffs._field(block, "Evidence"), evidence)
            self.assertEqual(check_handoffs._field(block, "Fix direction"), direction)
        self.assertIn("- **Found**: Fix the scope.", source)

    def test_placeholder_shaped_text_is_quoted_for_the_patch_gate(self) -> None:
        ruling = json.loads(json.dumps(RULING))
        ruling["items"][0]["fix_direction"] = "Logar 'prazo-estourado sistema=<codigo>' e manter `<ok>` como está"
        snap = json.loads(json.dumps(self.snap))
        snap["items"][1]["text"] = "O <div> sem fechamento quebra a tela; use `<span>`."
        fixes = pr_fix_lane._validate_ruling(ruling, snap)
        source = pr_fix_lane.source_text(self.record, snap, fixes)
        findings = pr_fix_lane.findings_text(self.record, snap, fixes)
        self.assertIn("Logar 'prazo-estourado sistema=`<codigo>`' e manter `<ok>` como está", findings)
        self.assertIn("O `<div>` sem fechamento quebra a tela; use `<span>`.", source)
        for heading, block in check_handoffs._finding_blocks(findings):
            for field in ("Evidence", "Fix direction"):
                check_handoffs._reject_placeholder(check_handoffs._field(block, field), field)
            evidence, direction = check_handoffs._source_repair_fields(
                ".project/review/pr-review-1.md", source, check_handoffs._field(block, "Locator"))
            self.assertEqual(check_handoffs._field(block, "Evidence"), evidence)
            self.assertEqual(check_handoffs._field(block, "Fix direction"), direction)

    def test_start_refuses_a_ruling_whose_hand_off_the_patch_gate_would_refuse(self) -> None:
        ruling = json.loads(json.dumps(RULING))
        ruling["items"][0]["fix_direction"] = "<codigo>"  # only a placeholder: the gate refuses it
        fixes = pr_fix_lane._validate_ruling(ruling, self.snap)
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "would fail the patch gate .*nothing was recorded"):
            pr_fix_lane._preflight_handoff(self.record, self.snap, fixes)
        pr_fix_lane._preflight_handoff(self.record, self.snap, pr_fix_lane._validate_ruling(RULING, self.snap))

    def test_ruling_must_classify_every_item_and_refuses_redesign(self) -> None:
        partial = dict(RULING, items=RULING["items"][:1])
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "missing C21"):
            pr_fix_lane._validate_ruling(partial, self.snap)
        redesign = json.loads(json.dumps(RULING))
        redesign["items"][1]["class"] = "redesign"
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "ordinary route"):
            pr_fix_lane._validate_ruling(redesign, self.snap)
        no_direction = json.loads(json.dumps(RULING))
        no_direction["items"][0]["fix_direction"] = " "
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "fix_direction"):
            pr_fix_lane._validate_ruling(no_direction, self.snap)


DELTA = """Wave verdict: pass
Cycle: 1
Depth: delta
Tasks reviewed: 1

## T003 — Exigir o escopo: pass

- ✅ T003/AC1 — test passes

## PR fix delta

Eligibility: local
Risk inventory: complete

### Item R11: closed

- **Evidence**: src/a.py:3

### Item C21: closed

- **Evidence**: tests/test_a.py:9

### SC1 — unaffected

- **Reason**: the diff touches only the scope check

### SC2 — affected

- **Verdict**: met
- **Check**: `python3 -m unittest test_a`
- **Observed**: 4 tests OK
- **Reference**: tests/test_a.py:9
- **Finding**: none
- **Fix direction**: none

### Gap 2 — unaffected

- **Reason**: no delivery code changed
"""

FINAL = """# Final Review — demo

Reviewed HEAD: {head}
Member reviewed HEAD: web {member}
Overall verdict: pass

## Success criteria

### SC1 — The demo command prints hello.

- **Verdict**: met
- **Check**: `make one`
- **Observed**: ok
- **Reference**: a.py:1
- **Finding**: none
- **Fix direction**: none

### SC2 — The demo test suite is green.

- **Verdict**: met
- **Check**: `make two`
- **Observed**: ok
- **Reference**: b.py:1
- **Finding**: none
- **Fix direction**: none
"""

GAP = """# Gap Review — 2: delivery

Reviewed HEAD: {head}
Member reviewed HEAD: web {member}
Gap verdict: pass
Risk: delivery
Waves checked: 1, 2

## Checked evidence

- **Check**: `make gap`
- **Observed**: ok
- **Reference**: c.py:1

## Finding

- **Found**: delivery holds
- **Fix direction**: none
"""


class DeltaViewTests(unittest.TestCase):
    def test_delta_maps_every_item_criterion_and_gap(self) -> None:
        delta = pr_fix_lane.parse_delta(DELTA, ["SC1", "SC2"], [2], ["R11", "C21"])
        self.assertEqual(delta["eligibility"], "local")
        self.assertEqual(delta["criteria"]["SC1"]["reason"], "the diff touches only the scope check")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "every success criterion"):
            pr_fix_lane.parse_delta(DELTA, ["SC1", "SC2", "SC3"], [2], ["R11", "C21"])
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "every fix item"):
            pr_fix_lane.parse_delta(DELTA, ["SC1", "SC2"], [2], ["R11"])
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "gap risk"):
            pr_fix_lane.parse_delta(DELTA, ["SC1", "SC2"], [2, 3], ["R11", "C21"])
        unreasoned = DELTA.replace("- **Reason**: no delivery code changed", "- **Reason**: none")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "without a Reason"):
            pr_fix_lane.parse_delta(unreasoned, ["SC1", "SC2"], [2], ["R11", "C21"])

    def test_a_duplicate_or_contradictory_conclusion_is_refused(self) -> None:
        args = (["SC1", "SC2"], [2], ["R11", "C21"])
        cases = {
            "item R11 twice": DELTA.replace("### Item C21: closed", "### Item R11: not-closed\n\n- **Evidence**: no"
                                            "\n\n### Item C21: closed"),
            "maps SC1 twice": DELTA.replace("### SC2 — affected", "### SC1 — affected\n\n### SC2 — affected"),
            "maps gap 2 twice": DELTA + "\n### Gap 2 — affected\n\n- **Gap verdict**: pass\n",
            "repeats `Eligibility:`": DELTA.replace("Risk inventory:", "Eligibility: not-local\nRisk inventory:"),
        }
        for message, text in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(pr_fix_lane.LaneError, re.escape(message)):
                    pr_fix_lane.parse_delta(text, *args)

    def test_a_closed_item_needs_real_evidence(self) -> None:
        args = (["SC1", "SC2"], [2], ["R11", "C21"])
        for evidence in ("- **Evidence**: none", "- **Evidence**: <file:line>", ""):
            with self.subTest(evidence=evidence):
                text = DELTA.replace("- **Evidence**: src/a.py:3", evidence)
                with self.assertRaisesRegex(pr_fix_lane.LaneError, "closed item R11 needs its Evidence"):
                    pr_fix_lane.parse_delta(text, *args)
        quoted = DELTA.replace("- **Evidence**: src/a.py:3", "- **Evidence**: logs `<codigo>` at src/a.py:3")
        self.assertEqual(pr_fix_lane.parse_delta(quoted, *args)["items"]["R11"], "closed")

    def test_the_cumulative_fields_are_parsed_once(self) -> None:
        fields = ("Round tasks: T003, T005\nMembership: " + "a" * 64 + "\nProduct HEAD: " + "b" * 40
                  + "\nMember product HEAD: web " + "c" * 40 + "\n")
        text = DELTA.replace("Eligibility: local", fields + "Eligibility: local")
        delta = pr_fix_lane.parse_delta(text, ["SC1", "SC2"], [2], ["R11", "C21"])
        self.assertEqual((delta["round_tasks"], delta["membership"], delta["product_head"], delta["member_heads"]),
                         (["T003", "T005"], "a" * 64, "b" * 40, {"web": "c" * 40}))
        twice = text.replace("Eligibility: local", "Member product HEAD: web " + "d" * 40 + "\nEligibility: local")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "names member web twice"):
            pr_fix_lane.parse_delta(twice, ["SC1", "SC2"], [2], ["R11", "C21"])

    def test_coordinator_equivalence_accepts_only_lane_bookkeeping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            run = lambda *args: subprocess.run(["git", "-C", temporary, "-c", "user.name=t",
                                                "-c", "user.email=t@example.invalid", *args],
                                               check=True, capture_output=True, text=True).stdout.strip()
            run("init", "-q")
            for path in (".project/review/a.md", ".project/intent/INTENT.md", ".project/plan/PLAN.md", "src.py"):
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text("x\n", encoding="utf-8")
            run("add", "-A")
            run("commit", "-qm", "base")
            reviewed = run("rev-parse", "HEAD")
            commit = lambda path, text: ((repo / path).write_text(text, encoding="utf-8"),
                                         run("commit", "-qam", path), run("rev-parse", "HEAD"))[-1]
            bookkeeping = commit(".project/review/a.md", "review recorded\n")
            self.assertTrue(pr_fix_lane.coordinator_equivalent(repo, reviewed, bookkeeping))
            self.assertTrue(pr_fix_lane.coordinator_equivalent(repo, reviewed, reviewed))
            for path in (".project/intent/INTENT.md", ".project/plan/PLAN.md", "src.py"):
                with self.subTest(path=path):
                    changed = commit(path, f"changed {path}\n")
                    self.assertFalse(pr_fix_lane.coordinator_equivalent(repo, reviewed, changed))
                    run("reset", "-q", "--hard", bookkeeping)
            self.assertFalse(pr_fix_lane.coordinator_equivalent(repo, bookkeeping, reviewed))  # not an ancestor
            self.assertFalse(pr_fix_lane.coordinator_equivalent(repo, "not-a-sha", bookkeeping))

    def test_views_inherit_unaffected_blocks_and_replace_affected_ones(self) -> None:
        old, new, old_member, new_member = "a" * 40, "b" * 40, "c" * 40, "d" * 40
        delta = pr_fix_lane.parse_delta(DELTA, ["SC1", "SC2"], [2], ["R11", "C21"])
        record = {"round": 1, "head": old, "pull_request": PULL, "member": "web",
                  "members": {"web": old_member}, "review_ids": [11]}
        final = pr_fix_lane.build_final_view(
            FINAL.format(head=old, member=old_member), delta, new, {"web": new_member}, record, RULING,
            ".project/review/wave-3.cycle1.md", {"P001": "R11", "P002": "C21"}, {"P001": "T003", "P002": "T003"})
        self.assertIn(f"Reviewed HEAD: {new}\nMember reviewed HEAD: web {new_member}\n", final)
        self.assertNotIn(old, final.split("## PR fix rounds")[0].replace(old[:12], ""))
        self.assertIn("- **Reference**: a.py:1 (inherited from aaaaaaaaaaaa by PR fix round 1; "
                      ".project/review/wave-3.cycle1.md: unaffected — the diff touches only the scope check)", final)
        self.assertIn("- **Check**: `python3 -m unittest test_a`", final)
        self.assertNotIn("`make two`", final)
        self.assertIn("## PR fix rounds\n\n### Round 1", final)
        self.assertIn("- **P002**: Item C21 → T003", final)
        self.assertEqual(final.count("### SC"), 2)
        gap = pr_fix_lane.build_gap_view(GAP.format(head=old, member=old_member), delta["gaps"][2], new,
                                         {"web": new_member}, record, ".project/review/wave-3.cycle1.md")
        self.assertIn(f"Reviewed HEAD: {new}\nMember reviewed HEAD: web {new_member}\n", gap)
        self.assertIn("- **Reference**: c.py:1 (inherited from aaaaaaaaaaaa", gap)

    def test_an_affected_surface_criterion_keeps_its_surface(self) -> None:
        old, new = "a" * 40, "b" * 40
        baseline = FINAL.format(head=old, member="c" * 40).replace(
            "- **Verdict**: met\n- **Check**: `make two`", "- **Verdict**: met\n- **Surface**: MCP tools\n- **Check**: `make two`")
        record = {"round": 1, "head": old, "pull_request": PULL, "member": "web",
                  "members": {"web": "c" * 40}, "review_ids": [11]}
        build = lambda text: pr_fix_lane.build_final_view(
            baseline, pr_fix_lane.parse_delta(text, ["SC1", "SC2"], [2], ["R11", "C21"]), new, {"web": "d" * 40},
            record, RULING, "x", {}, {})
        # A delta review that omits the Surface inherits the baseline's.
        self.assertIn("- **Verdict**: met\n- **Surface**: MCP tools\n- **Check**: `python3 -m unittest test_a`",
                      build(DELTA))
        named = DELTA.replace("- **Verdict**: met\n", "- **Verdict**: met\n- **Surface**: MCP tools (reviewed)\n")
        self.assertIn("- **Surface**: MCP tools (reviewed)", build(named))

    def test_an_affected_criterion_that_is_not_met_is_refused(self) -> None:
        failing = DELTA.replace("- **Verdict**: met", "- **Verdict**: not-met")
        delta = pr_fix_lane.parse_delta(failing, ["SC1", "SC2"], [2], ["R11", "C21"])
        record = {"round": 1, "head": "a" * 40, "pull_request": PULL, "member": "web",
                  "members": {"web": "c" * 40}, "review_ids": [11]}
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "not met"):
            pr_fix_lane.build_final_view(FINAL.format(head="a" * 40, member="c" * 40), delta, "b" * 40,
                                         {"web": "d" * 40}, record, RULING, "x", {}, {})


class LaneEntryTests(unittest.TestCase):
    fixture = member_integration.MemberIntegrationTests.fixture
    remote_ref = member_integration.MemberIntegrationTests.remote_ref

    def setUp(self) -> None:
        member_integration.MemberIntegrationTests.setUp(self)
        home = self.root.parent / "home"
        (home / ".gsd-path").mkdir(parents=True)
        (home / ".gsd-path/config.json").write_text('{"pr_fix_lane": "on"}\n', encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.prepare_archive()
        self.pull = {"state": "open", "merged_at": None, "head": {"sha": self.member_tip}, "body": "Descrição"}
        self.reviews = [review(11, "CHANGES_REQUESTED", body="Fix the scope.")]
        self.comments = [comment(21, 11, "Missing scope check")]

    def prepare_archive(self) -> None:
        project = self.root / ".project"
        archive = self.root / ARCHIVE
        archive.mkdir(parents=True)
        for name in ("intent", "plan", "build", "tasks"):
            if (project / name).exists():
                shutil.move(str(project / name), archive / name)
        review_dir = archive / "review"
        if (project / "review").exists():
            shutil.move(str(project / "review"), review_dir)
        review_dir.mkdir(exist_ok=True)
        (review_dir / "FINAL.md").write_text(FINAL.format(head=self.head, member=self.member_tip), encoding="utf-8")
        (review_dir / "final-gap-1.md").write_text(
            GAP.format(head=self.head, member=self.member_tip).replace("2: delivery", "1: project Verify")
            .replace("Risk: delivery", "Risk: project Verify"), encoding="utf-8")
        (review_dir / "final-gap-2.md").write_text(GAP.format(head=self.head, member=self.member_tip),
                                                   encoding="utf-8")
        state = project / "STATE.md"
        state.write_bytes(state.read_text(encoding="utf-8").replace("archive: null", f"archive: {ARCHIVE}")
                          .encode("utf-8"))
        common = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        members.write_member_close(common, "001-demo", [{
            "name": "web", "mode": "pull-request", "reviewed_head": self.member_tip, "status": "pending",
            "merge": None, "tag": None, "pull_request": PULL}])

    def api(self, *arguments):
        path = arguments[0]
        if path.endswith("/reviews"):
            return [self.reviews]
        if path.endswith("/comments"):
            return [self.comments]
        return self.pull

    def snapshot(self):
        return pr_fix_lane.snapshot(self.root, api=self.api)

    def test_snapshot_captures_the_baseline_without_touching_the_project(self) -> None:
        before = git(self.root, "status", "--porcelain")
        result = self.snapshot()
        self.assertEqual(git(self.root, "status", "--porcelain"), before)
        self.assertEqual([item["id"] for item in result["items"]], ["R11", "C21"])
        directory = Path(result["directory"])
        self.assertEqual((directory / "capsule/review/FINAL.md").read_text(encoding="utf-8"),
                         FINAL.format(head=self.head, member=self.member_tip))
        self.assertTrue((directory / "capsule/review/final-gap-2.md").is_file())
        self.assertEqual(pr_fix_lane.active_round(self.root)["stage"], "captured")
        self.assertEqual(self.snapshot()["directory"], result["directory"])

    def test_entry_refuses_without_the_setting_a_request_or_a_matching_head(self) -> None:
        with mock.patch.object(pr_fix_lane, "setting_enabled", return_value=False):
            with self.assertRaisesRegex(pr_fix_lane.LaneError, "lane is off"):
                self.snapshot()
        self.reviews = [review(11, "APPROVED")]
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "no active change request"):
            self.snapshot()
        self.reviews = [review(11, "CHANGES_REQUESTED", body="x")]
        self.pull = dict(self.pull, head={"sha": "f" * 40})
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "head differs"):
            self.snapshot()
        self.pull = dict(self.pull, head={"sha": self.member_tip}, merged_at="2026-11-16")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "not open and unmerged"):
            self.snapshot()
        self.assertIsNone(pr_fix_lane.active_round(self.root))

    def test_start_undoes_the_archive_writes_the_handoff_and_blocks_ship(self) -> None:
        self.snapshot()
        ruling = self.root.parent / "ruling.json"
        ruling.write_text(json.dumps(RULING), encoding="utf-8")
        result = pr_fix_lane.start(self.root, ruling)
        self.assertEqual(result["status"], "opened")
        state = pipeline_state.load_state(self.root)[0]
        self.assertEqual((state.phase, state.status, state.archive), ("ship", "blocked", None))
        self.assertFalse((self.root / ARCHIVE).exists())
        self.assertIn(f"{pr_fix_lane.LANE_EVENT}: round 1",
                      (self.root / ".project/STATE.md").read_text(encoding="utf-8"))
        summary = check_handoffs.validate_patch_findings(self.root)
        self.assertEqual(summary["sources"] if "sources" in summary else None, summary.get("sources"))
        source = self.root / ".project/review/pr-review-1.md"
        source.write_text(source.read_text(encoding="utf-8").replace("Recusar", "Aceitar"), encoding="utf-8")
        with self.assertRaisesRegex(check_handoffs.HandoffError, "differs from the lane"):
            check_handoffs.validate_patch_findings(self.root)
        # A rerun after completion changes nothing.
        source.write_text(pr_fix_lane.source_text(
            {key: value for key, value in pr_fix_lane.active_round(self.root).items()},
            json.loads((Path(pr_fix_lane.active_round(self.root)["directory"]) / "snapshot.json")
                       .read_text(encoding="utf-8")),
            RULING["items"]), encoding="utf-8")
        self.assertEqual(pr_fix_lane.start(self.root, ruling)["status"], "opened")

    def test_start_resumes_after_a_crash_between_undo_and_handoff(self) -> None:
        self.snapshot()
        ruling = self.root.parent / "ruling.json"
        ruling.write_text(json.dumps(RULING), encoding="utf-8")
        real = pr_fix_lane._save_round
        calls = []

        def crash(repo, record):
            calls.append(record["stage"])
            if record["stage"] == "undone":
                raise RuntimeError("crash after undo")
            real(repo, record)

        with mock.patch.object(pr_fix_lane, "_save_round", side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, "crash after undo"):
                pr_fix_lane.start(self.root, ruling)
        self.assertEqual(pr_fix_lane.active_round(self.root)["stage"], "ruled")
        self.assertIsNone(pipeline_state.load_state(self.root)[0].archive)
        result = pr_fix_lane.start(self.root, ruling)
        self.assertEqual(result["status"], "opened")
        self.assertEqual(pipeline_state.load_state(self.root)[0].status, "blocked")


if __name__ == "__main__":
    unittest.main()


class LaneGateTests(unittest.TestCase):
    def test_only_an_unfinished_lane_round_falls_back(self) -> None:
        saved = []
        # A retry of prepare-final may find a viewed round whose views no longer stand.
        for stage, allowed in (("opened", True), ("built", True), ("fell-back", True),
                               ("viewed", True), ("replied", False)):
            record = {"stage": stage, "round": 1, "mode": "lane"}
            with mock.patch.object(pr_fix_lane, "active_round", return_value=dict(record)), \
                    mock.patch.object(pr_fix_lane, "_save_round", side_effect=lambda repo, value: saved.append(value)):
                if allowed:
                    self.assertEqual(pr_fix_lane.fall_back(Path("."), "new risk")["status"], "fell-back")
                else:
                    with self.assertRaisesRegex(pr_fix_lane.LaneError, "cannot fall back"):
                        pr_fix_lane.fall_back(Path("."), "new risk")
        self.assertEqual([value["fallback"] for value in saved], ["new risk", "new risk", "new risk"])

    def test_a_delta_review_stops_covering_a_product_that_changed_after_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            run = lambda *args: subprocess.run(["git", "-C", temporary, "-c", "user.name=t",
                                                "-c", "user.email=t@example.invalid", *args],
                                               check=True, capture_output=True)
            run("init", "-q")
            tasks, review = repo / ".project/tasks", repo / ".project/review"
            tasks.mkdir(parents=True)
            review.mkdir(parents=True)
            (tasks / "T001-a.md").write_text("---\nid: T001\nstatus: done\n---\n", encoding="utf-8")
            (tasks / "T002-b.md").write_text("---\nid: T002\nstatus: pending\n---\n", encoding="utf-8")
            delta = review / "wave-2.cycle1.md"
            delta.write_text("# Review\n", encoding="utf-8")
            (repo / ".project/build").mkdir()
            (repo / ".project/build/members.json").write_text("{}", encoding="utf-8")
            run("add", "-A")
            run("commit", "-q", "-m", "build: record wave 2 review")
            self.assertIsNone(pr_fix_lane._delta_coverage_error(repo, delta))
            (tasks / "T001-a.md").write_text("---\nid: T001\nstatus: done\n---\nlog\n", encoding="utf-8")
            run("commit", "-qam", "bookkeeping")
            self.assertIsNone(pr_fix_lane._delta_coverage_error(repo, delta))
            (tasks / "T002-b.md").write_text("---\nid: T002\nstatus: done\n---\n", encoding="utf-8")
            run("commit", "-qam", "T002 landed")
            self.assertRegex(pr_fix_lane._delta_coverage_error(repo, delta), "T002-b.md landed after")
            run("reset", "-q", "--hard", "HEAD~1")
            (repo / ".project/build/members.json").write_text('{"base": "x"}', encoding="utf-8")
            run("commit", "-qam", "adopt rebased member")
            self.assertRegex(pr_fix_lane._delta_coverage_error(repo, delta), "member product changed")
            uncommitted = review / "wave-3.cycle1.md"
            uncommitted.write_text("# Review\n", encoding="utf-8")
            self.assertRegex(pr_fix_lane._delta_coverage_error(repo, uncommitted), "is not committed")

    def test_a_delta_cycle_owes_no_panel_and_refuses_a_stray_receipt(self) -> None:
        from scripts import archive_milestone
        with tempfile.TemporaryDirectory() as temporary:
            reviews = Path(temporary)
            archive_milestone.require_wave_panel_evidence(reviews, 2, 1, "delta", "named")
            with self.assertRaises(archive_milestone.ArchiveError):
                archive_milestone.require_wave_panel_evidence(reviews, 2, 1, "full", "named")

    def test_the_setting_is_on_or_off_and_never_locked_by_ship(self) -> None:
        from scripts import path_config
        path_config.validate_value("pr_fix_lane", "on")
        with self.assertRaisesRegex(ValueError, "on or off"):
            path_config.validate_value("pr_fix_lane", "yes")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".gsd-path").mkdir()
            (home / ".gsd-path/config.json").write_text('{"pr_fix_lane": "on"}', encoding="utf-8")
            repo = home / "repo"
            (repo / ".project").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                self.assertTrue(pr_fix_lane.setting_enabled(repo))
                (repo / ".project/config.json").write_text('{"pr_fix_lane": "off"}', encoding="utf-8")
                self.assertFalse(pr_fix_lane.setting_enabled(repo))

TASK_FILE = """---
id: {task_id}
title: {title}
wave: {wave}
deps: []
status: {status}
agent: null
base: null
worktree: null
task_branch: null
{lane}files:
  - src/{task_id}.py
---

# {task_id} — {title}

## Context

{context}

## Acceptance criteria

1. Done.
{findings}
## Log
"""


class LaneMembershipTests(unittest.TestCase):
    """ADR 0008: round ownership is the bound membership file, never plan geometry."""

    WAVES = {1: "full", 2: "full", 3: "delta"}

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        for name in ("tasks", "review", "plan"):
            (self.root / ".project" / name).mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.directory = Path(temporary.name) / "round"
        self.directory.mkdir()
        snap = {"items": [{"id": "I1"}, {"id": "I2"}, {"id": "I3"}]}
        ruling = {"verbatim": "Fix them.", "items": [
            {"id": "I1", "class": "fix", "title": "Scope check", "fix_direction": "check it", "group": "scope"},
            {"id": "I2", "class": "fix", "title": "Scope message", "fix_direction": "say it", "group": "scope"},
            {"id": "I3", "class": "fix", "title": "Docs", "fix_direction": "write it", "group": "docs"}]}
        (self.directory / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        (self.directory / "ruling.json").write_text(json.dumps(ruling), encoding="utf-8")
        (self.root / ".project/review/PATCH-FINDINGS.md").write_text(
            "### P001 — Scope check\n\n### P002 — Scope message\n\n### P003 — Docs\n", encoding="utf-8")
        self.record = {"round": 2, "stage": "opened", "mode": "lane", "membership": pr_fix_lane.MEMBERSHIP_SCHEMA,
                       "directory": str(self.directory), "pull_request": PULL, "snapshot_digest": "s" * 64,
                       "ruling_digest": "r" * 64, "gaps": ["final-gap-1.md", "final-gap-2.md"]}
        patcher = mock.patch.object(pr_fix_lane, "lane_round", side_effect=lambda repo: dict(self.record))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write_task("T001", 1, "done", context="Original work.")
        self.write_task("T005", 3, "pending", lane=2, context="Closes P001 and P002.")
        self.write_task("T006", 3, "pending", lane=2, context="Closes P003.")

    def write_task(self, task_id, wave, status, lane=None, context="", findings="", suffix="") -> Path:
        path = self.root / f".project/tasks/{task_id}-{suffix or 'task'}.md"
        path.write_text(TASK_FILE.format(task_id=task_id, title=f"Task {task_id}", wave=wave, status=status,
                                         lane=f"lane_round: {lane}\n" if lane is not None else "",
                                         context=context, findings=findings), encoding="utf-8")
        return path

    def plan_tasks(self):
        return [{"id": task["id"], "wave": task["wave"], "status": task["status"], "context": task["context"]}
                for task in pr_fix_lane._task_files(self.root).values()]

    def gate(self, waves=None):
        return pr_fix_lane.lane_plan_errors(self.root, waves or self.WAVES, self.plan_tasks())

    def test_bind_records_the_grouping_and_contracts_and_is_idempotent(self) -> None:
        result = pr_fix_lane.bind(self.root)
        path = pr_fix_lane.membership_path(self.root, 2)
        first = path.read_bytes()
        self.assertEqual((result["tasks"], result["repairs"]), (["T005", "T006"], []))
        membership = json.loads(first)
        self.assertEqual([(group["group"], group["findings"], group["task"]) for group in membership["groups"]],
                         [("scope", ["P001", "P002"], "T005"), ("docs", ["P003"], "T006")])
        self.assertEqual(membership["gaps"], [1, 2])
        pr_fix_lane.bind(self.root)
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(self.gate(), [])

    def test_bind_refuses_a_group_split_across_tasks_and_uncovered_findings(self) -> None:
        self.write_task("T005", 3, "pending", lane=2, context="Closes P001.")
        self.write_task("T006", 3, "pending", lane=2, context="Closes P002 and P003.")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "group 'scope' is split across T005 and T006"):
            pr_fix_lane.bind(self.root)
        self.write_task("T006", 3, "pending", lane=2, context="Closes nothing yet, see P009.")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "unknown finding P009"):
            pr_fix_lane.bind(self.root)

    def test_membership_and_task_fields_must_agree_in_both_directions(self) -> None:
        pr_fix_lane.bind(self.root)
        self.write_task("T007", 3, "pending", lane=2, context="Closes P003 too.")
        self.assertIn("tasks with `lane_round: 2` outside the membership (run bind): T007", self.gate())
        (self.root / ".project/tasks/T007-task.md").unlink()
        self.write_task("T006", 3, "pending", context="Closes P003.")
        errors = self.gate()
        self.assertIn("membership tasks without `lane_round: 2`: T006", errors)

    def test_a_contract_change_refuses_but_an_appended_log_does_not(self) -> None:
        pr_fix_lane.bind(self.root)
        path = self.root / ".project/tasks/T005-task.md"
        path.write_text(path.read_text(encoding="utf-8").replace("status: pending", "status: done")
                        + "- 2026-11-18 — landed\n", encoding="utf-8")
        self.assertEqual(self.gate(), [])
        path.write_text(path.read_text(encoding="utf-8").replace("1. Done.", "1. Done twice."), encoding="utf-8")
        self.assertIn("T005 changed after it was bound; a changed lane plan needs a new approval", self.gate())

    def test_lane_waves_hold_only_members_and_come_last(self) -> None:
        pr_fix_lane.bind(self.root)
        self.write_task("T008", 3, "pending", context="Unrelated.")
        self.assertIn("wave 3 of PR fix round 2 holds non-member tasks: T008", self.gate())
        (self.root / ".project/tasks/T008-task.md").unlink()
        self.assertIn("the PR fix lane waves must be the last waves of the plan",
                      self.gate({1: "full", 2: "full", 3: "delta", 4: "full"}))

    def test_the_approval_freezes_the_membership_and_build_entry_checks_it(self) -> None:
        pr_fix_lane.bind(self.root)
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "no approved membership"):
            pr_fix_lane.require_approved_membership(self.root)
        pr_fix_lane.record_patch_approval(self.root)
        pr_fix_lane.require_approved_membership(self.root)
        # A regrouping without a new approval is refused by bind.
        self.write_task("T006", 3, "pending", lane=2, context="Closes P003, reworded.")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "changed after the patch plan approval"):
            pr_fix_lane.bind(self.root)
        # A paired change of a task and the membership file is refused at build entry.
        path = pr_fix_lane.membership_path(self.root, 2)
        membership = json.loads(path.read_text(encoding="utf-8"))
        membership["tasks"][1]["contract"] = pr_fix_lane.task_contract(
            (self.root / ".project/tasks/T006-task.md").read_text(encoding="utf-8"))
        path.write_text(json.dumps(membership), encoding="utf-8")
        self.assertEqual(self.gate(), [])
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "differs from the approved one"):
            pr_fix_lane.require_approved_membership(self.root)

    def test_a_review_repair_is_appended_with_its_source_review(self) -> None:
        pr_fix_lane.bind(self.root)
        review = self.root / ".project/review/wave-3.cycle1.md"
        review.write_text("# Wave 3 review\nWave verdict: blocked\n", encoding="utf-8")
        self.write_task("T009", 4, "pending", lane=2, context="Repair.", suffix="fix-wave-3-cycle-1",
                        findings="\n## Review findings\n\n### F1\nCriterion: x\n- fix it\n")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "needs the round's approved patch plan"):
            pr_fix_lane.bind(self.root)
        pr_fix_lane.record_patch_approval(self.root)
        result = pr_fix_lane.bind(self.root)
        self.assertEqual(result["repairs"], ["T009"])
        repair = json.loads(pr_fix_lane.membership_path(self.root, 2).read_text(encoding="utf-8"))["repairs"][0]
        self.assertEqual((repair["source"], repair["wave"]), (".project/review/wave-3.cycle1.md", 4))
        pr_fix_lane.require_approved_membership(self.root)  # repairs do not change the approved part
        self.assertEqual(self.gate({1: "full", 2: "full", 3: "delta", 4: "delta"}), [])

    def test_an_open_legacy_round_is_refused_with_the_fall_back_command(self) -> None:
        del self.record["membership"]
        errors = self.gate()
        self.assertEqual(len(errors), 1)
        self.assertIn('pr_fix_lane.py fall-back --repo <root> --reason "<why>"', errors[0])
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "earlier runtime"):
            pr_fix_lane.bind(self.root)
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "earlier runtime"):
            pr_fix_lane.require_approved_membership(self.root)

    def test_after_a_fall_back_unfinished_delta_work_moves_to_ordinary_depth(self) -> None:
        # A legacy round: its delta tasks carry no lane_round.
        del self.record["membership"]
        self.record["stage"] = "fell-back"
        self.write_task("T005", 3, "done", context="Closes P001 and P002.")
        self.write_task("T006", 3, "pending", context="Closes P003.")
        self.assertEqual(self.gate(), ["T006 in delta wave 3 is pending; after a PR fix lane fallback the delta "
                                       "waves are history: move it to a standard wave (`Review depth: full`) "
                                       "through the ordinary plan route"])
        self.write_task("T006", 4, "pending", context="Closes P003.")
        self.assertEqual(self.gate({1: "full", 2: "full", 3: "delta", 4: "full"}), [])
        self.write_task("T006", 3, "done", context="Closes P003.")
        self.assertEqual(self.gate(), [])

    def test_a_closed_round_member_must_be_finished(self) -> None:
        pr_fix_lane.bind(self.root)
        self.write_task("T002", 2, "pending", lane=1, context="Closes P001 of round 1.")
        self.assertIn("T002 belongs to closed PR fix round 1 and is pending", self.gate())
        self.write_task("T002", 2, "done", lane=1, context="Closes P001 of round 1.")
        self.assertEqual(self.gate(), [])

    def test_regression_exemption_is_limited_to_members_of_an_open_round(self) -> None:
        from scripts import build_state
        pr_fix_lane.bind(self.root)
        member, other = mock.Mock(task_id="T005"), mock.Mock(task_id="T001")
        self.assertTrue(build_state._pr_fix_lane_task(self.root, member, ".project"))
        self.assertFalse(build_state._pr_fix_lane_task(self.root, other, ".project"))
        self.record["stage"] = "fell-back"
        self.assertFalse(build_state._pr_fix_lane_task(self.root, member, ".project"))


class FakeThreads:
    """The GitHub GraphQL surface post_threads uses: thread index, pending review, replies, resolve."""

    def __init__(self, head, comments, viewer="author"):
        self.head, self.viewer = head, viewer
        self.reviews, self.calls, self.counter = [], [], 0
        self.threads = [{"id": f"T{comment['id']}", "isResolved": False,
                         "comments": {"nodes": [{"databaseId": comment["id"], "body": comment["body"]}]}}
                        for comment in comments]

    def _id(self, prefix):
        self.counter += 1
        return f"{prefix}{self.counter}"

    def api(self, *arguments):
        self.calls.append(arguments)
        assert arguments[0] == "graphql", arguments
        values = dict(argument.split("=", 1) for argument in arguments[1:] if "=" in argument)
        query = values["query"]
        if "reviewThreads" in query:
            return {"data": {"viewer": {"login": self.viewer}, "repository": {"pullRequest": {
                "id": "PR1", "headRefOid": self.head,
                "reviews": {"nodes": [dict(review) for review in self.reviews]},
                "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                  "nodes": json.loads(json.dumps(self.threads))}}}}}
        if "deletePullRequestReview" in query:
            self.reviews = [review for review in self.reviews if review["id"] != values["id"]]
            return {"data": {"deletePullRequestReview": {"pullRequestReview": {"id": values["id"]}}}}
        if "addPullRequestReview(" in query:
            review = {"id": self._id("V"), "state": "PENDING", "body": "", "author": {"login": self.viewer},
                      "replies": []}
            self.reviews.append(review)
            return {"data": {"addPullRequestReview": {"pullRequestReview": {"id": review["id"]}}}}
        if "addPullRequestReviewThreadReply" in query:
            review = next(item for item in self.reviews if item["id"] == values["review"])
            review["replies"].append((values["thread"], values["body"]))
            return {"data": {"addPullRequestReviewThreadReply": {"comment": {"id": self._id("K")}}}}
        if "submitPullRequestReview" in query:
            review = next(item for item in self.reviews if item["id"] == values["review"])
            review.update(state="COMMENTED", body=values["body"])
            for thread_id, body in review["replies"]:
                thread = next(item for item in self.threads if item["id"] == thread_id)
                thread["comments"]["nodes"].append({"databaseId": 0, "body": body})
            return {"data": {"submitPullRequestReview": {"pullRequestReview": {"id": review["id"]}}}}
        if "resolveReviewThread" in query:
            next(item for item in self.threads if item["id"] == values["id"])["isResolved"] = True
            return {"data": {"resolveReviewThread": {"thread": {"id": values["id"], "isResolved": True}}}}
        raise AssertionError(query)


class PostThreadsTests(unittest.TestCase):
    head = "e" * 40
    plan = {"pull_request": PULL, "head": "e" * 40, "round": 1, "language": "pt-BR", "items": [
        {"id": "R11", "kind": "review", "class": "fix", "note": "Declarei o escopo", "link": "l1"},
        {"id": "C21", "kind": "inline", "class": "fix", "note": "Recusar token sem escopo", "link": "l2"},
        {"id": "C22", "kind": "inline", "class": "follow-up", "note": "Fica para o M003", "link": "l3"}]}

    def fake(self):
        return FakeThreads(self.head, [{"id": 21, "body": "Falta escopo"}, {"id": 22, "body": "Lento"}])

    def test_a_reply_item_is_answered_and_resolved_in_its_conversation(self) -> None:
        fake = self.fake()
        fake.threads[0]["comments"]["nodes"] += [{"databaseId": 30, "body": "Corrigi em abc."},
                                                 {"databaseId": 31, "body": "Ainda falta o prompt=login"}]
        plan = dict(self.plan, round=2, items=[
            {"id": "C31", "kind": "inline", "class": "fix", "note": "prompt=login em todo pedido", "link": "l4"}])
        result = pr_fix_lane.post_threads(plan, fake.api)
        self.assertEqual((result["replied"], result["resolved"], result["missing_threads"]), (["C31"], ["C31"], []))
        replies = dict(next(review for review in fake.reviews if review["state"] == "COMMENTED")["replies"])
        self.assertTrue(replies["T21"].startswith("Corrigi em `eeeeeeeeeeee`: prompt=login em todo pedido"))
        self.assertEqual([thread["isResolved"] for thread in fake.threads], [True, False])

    def test_one_review_answers_every_item_and_resolves_only_the_fixes(self) -> None:
        fake = self.fake()
        result = pr_fix_lane.post_threads(self.plan, fake.api)
        self.assertEqual((result["summary"], result["replied"], result["resolved"]), ("posted", ["C21", "C22"], ["C21"]))
        submitted = [review for review in fake.reviews if review["state"] == "COMMENTED"]
        self.assertEqual(len(submitted), 1)
        body = submitted[0]["body"]
        self.assertIn("Rodada 1 de correção do PR, publicada em `eeeeeeeeeeee`.", body)
        self.assertIn("- [R11](l1): corrigido — Declarei o escopo", body)
        self.assertIn("- [C22](l3): fica para depois — Fica para o M003", body)
        replies = dict(submitted[0]["replies"])
        self.assertTrue(replies["T21"].startswith("Corrigi em `eeeeeeeeeeee`: Recusar token sem escopo"))
        self.assertTrue(replies["T22"].startswith("Deixei para depois: Fica para o M003"))
        self.assertEqual([thread["isResolved"] for thread in fake.threads], [True, False])
        again = pr_fix_lane.post_threads(self.plan, fake.api)
        self.assertEqual((again["summary"], again["replied"], again["resolved"]), ("present", [], []))
        self.assertEqual(len([review for review in fake.reviews if review["state"] == "COMMENTED"]), 1)

    def test_a_pending_review_left_by_a_crash_is_replaced(self) -> None:
        fake = self.fake()
        fake.reviews.append({"id": "OLD", "state": "PENDING", "body": "", "author": {"login": "author"},
                             "replies": [("T21", "half")]})
        pr_fix_lane.post_threads(self.plan, fake.api)
        self.assertNotIn("OLD", [review["id"] for review in fake.reviews])
        self.assertEqual(len(fake.reviews), 1)

    def test_replies_wait_for_the_published_head(self) -> None:
        fake = self.fake()
        fake.head = "f" * 40
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "publish it through close-members"):
            pr_fix_lane.post_threads(self.plan, fake.api)
        self.assertEqual(fake.reviews, [])

    def test_english_text_and_a_missing_thread_is_reported(self) -> None:
        fake = FakeThreads(self.head, [{"id": 22, "body": "Lento"}])
        plan = dict(self.plan, language="en")
        result = pr_fix_lane.post_threads(plan, fake.api)
        self.assertEqual(result["missing_threads"], ["C21"])
        self.assertIn("PR fix round 1, published in", fake.reviews[0]["body"])


class RecordOnlyRoundTests(LaneEntryTests):
    def test_record_only_needs_no_setting_and_never_opens_the_lane(self) -> None:
        with mock.patch.object(pr_fix_lane, "setting_enabled", return_value=False):
            result = pr_fix_lane.snapshot(self.root, api=self.api, record_only=True)
        self.assertEqual(result["mode"], "record")
        self.assertIsNone(pr_fix_lane.lane_round(self.root))
        ruling = self.root.parent / "ruling.json"
        ruling.write_text(json.dumps({"verbatim": "Corrigir R11; C21 fica.", "items": [
            {"id": "R11", "class": "redesign", "fix_direction": "Refazer a guarda"},
            {"id": "C21", "class": "follow-up", "note": "Próximo milestone"}]}), encoding="utf-8")
        self.assertEqual(pr_fix_lane.rule(self.root, ruling)["fixes"], ["R11"])
        self.assertEqual(pr_fix_lane.active_round(self.root)["stage"], "ruled")
        self.assertEqual(pipeline_state.load_state(self.root)[0].archive, ARCHIVE)
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "record-only round uses rule"):
            pr_fix_lane.start(self.root, ruling)
        bad = self.root.parent / "bad.json"
        bad.write_text(json.dumps({"verbatim": "x", "items": [{"id": "R11", "class": "follow-up", "note": "n"},
                                                               {"id": "C21", "class": "fix", "fix_direction": "y"}]}),
                       encoding="utf-8")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "different ruling"):
            pr_fix_lane.rule(self.root, bad)
