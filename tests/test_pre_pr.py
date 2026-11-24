"""Pre-PR review (ADR 0010): publication under the installed hooks, rounds, hand-off, and gates."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import archive_milestone, check_handoffs, members, pipeline_undo, pr_editorial, pre_pr

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "git_guard.py"
STATE = """---
pipeline: gsd-path/v2
project: demo
milestone: demo
phase: {phase}
status: {status}
branch: feature/milestone-001
archive: null
integration_default: {integration}
integration: {integration}
integration_source: default
---

# Project State

## Log
- 2026-11-24 — build — build complete
"""


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments], cwd=repo,
                          encoding="utf-8", errors="replace", capture_output=True, check=check).stdout.strip()


def install_pre_push(repo: Path) -> None:
    hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{GUARD}" pre-push "$@"\n', encoding="utf-8")
    hook.chmod(0o755)


def situation(execution, included, outcome="concluida"):
    return {"execucao_id": execution, "situacao": outcome, "terminou": True, "veredito": "REQUEST_CHANGES",
            "achados_incluidos": included, "erro": None if outcome == "concluida" else "quota",
            "parou_por_cota": False}


def finding(identifier, severity, description, path="hello.py", line=1):
    return {"id": identifier, "item": "R" + identifier[1:], "severidade": severity, "arquivo": path, "linha": line,
            "titulo": f"Title {identifier}", "descricao": description, "cenario": "", "sugestao": "Fix it",
            "suggestion": None}


class SingleRepoFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        home = base / "home"
        (home / ".gsd-path").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote = base / "remote.git"
        git(base, "init", "-q", "--bare", str(self.remote))
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.root = base / "demo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "url." + str(self.remote) + ".insteadOf", "https://github.com/acme/demo.git")
        git(self.root, "remote", "add", "origin", "https://github.com/acme/demo.git")
        (self.root / "hello.py").write_text("print('hello')\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        git(self.root, "push", "-q", "origin", "main")
        git(self.root, "fetch", "-q", "origin")
        git(self.root, "remote", "set-head", "origin", "main")
        git(self.root, "checkout", "-qb", "feature/milestone-001")
        (self.root / "hello.py").write_text("print('hello, world')\n", encoding="utf-8")
        self.write_state("ship", "active")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "build complete")
        self.head = git(self.root, "rev-parse", "HEAD")
        install_pre_push(self.root)
        self.files = base / "files"
        self.files.mkdir()

    def write_state(self, phase: str, status: str, integration: str = "pull-request") -> None:
        path = self.root / ".project/STATE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STATE.format(phase=phase, status=status, integration=integration), encoding="utf-8")

    def file(self, name: str, value: object) -> Path:
        path = self.files / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def remote_ref(self, ref: str) -> str:
        return git(self.remote, "rev-parse", "--verify", "--quiet", ref, check=False)

    def objective(self) -> Path:
        return self.file("objective.json", {"targets": {"coordinator": {
            "title": "feat(greeting): greet the whole world",
            "problem": "The greeting names nobody.", "scope": "Only the greeting text changes."}}})

    def run_round(self, execution, findings, decisions, objective=True) -> dict:
        published = pre_pr.publish(self.root, self.objective() if objective else None)
        request = published["requests"][0]
        self.assertEqual(request["branch"], "pre-pr/milestone-001")
        self.assertEqual(request["branch_do_pr"], "feature/milestone-001")
        head = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), head)
        pre_pr.record(self.root, "coordinator", execution, head)
        pre_pr.ended(self.root, "coordinator", situation(execution, len(findings)))
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), "")
        self.assertFalse(members.authorized(self.root, "demo", "push", "refs/heads/pre-pr/milestone-001", head))
        pre_pr.capture(self.root, "coordinator", {"execucao_id": execution, "repo": "acme/demo",
                                                  "branch": "pre-pr/milestone-001", "head_sha": head,
                                                  "achados": findings})
        pre_pr.rule(self.root, {"verbatim": "ok, as proposed", "findings": decisions})
        return pre_pr.finish(self.root, {"coordinator": {"execucao_id": execution, "respondidos": [
            {"achado": row["id"], "item": "R" + row["id"][1:], "situacao": "aberto"} for row in findings]}})


class SingleRepoTests(SingleRepoFixture):
    def test_the_installed_hook_refuses_the_bound_name_but_accepts_the_authorized_pre_pr_branch(self) -> None:
        refused = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/feature/milestone-001"], cwd=self.root,
                                 capture_output=True, encoding="utf-8")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("ship commit", refused.stderr)
        self.assertEqual(pre_pr.status(self.root)["status"], "due")
        pre_pr.publish(self.root, self.objective())
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), self.head)

    def test_a_round_with_a_fix_blocks_ship_with_a_valid_patch_hand_off(self) -> None:
        self.assertEqual(pre_pr.status(self.root)["status"], "due")
        result = self.run_round(1302, [finding("A1", "importante", "<codigo> fica sem `escape` | ok\nlinha 2"),
                                       finding("A2", "nit", "Prefira f-strings")],
                                [{"target": "coordinator", "id": "A1", "decision": "corrijo", "reason": "Real bug.",
                                  "fix_direction": "Escape `<codigo>` before printing."},
                                 {"target": "coordinator", "id": "A2", "decision": "contesto",
                                  "reason": "hello.py:1 already uses the project style."}])
        self.assertEqual(result["status"], "blocked")
        state = (self.root / ".project/STATE.md").read_text(encoding="utf-8")
        self.assertIn("status: blocked", state)
        self.assertIn("pre-PR round 1: 1 to fix, 1 contested, 0 follow-up", state)
        summary = check_handoffs.validate_patch_findings(self.root)
        self.assertEqual(summary["findings"], ["P001"])
        self.assertEqual(summary["sources"], [".project/review/pre-pr-1.md"])
        with self.assertRaisesRegex(pre_pr.PrePrError, "not closed"):
            pre_pr.require_closed(self.root)
        with self.assertRaisesRegex(archive_milestone.ArchiveError, "not closed"):
            archive_milestone.require_pre_pr_closed(self.root)
        self.assertEqual(check_handoffs.main(["final", "--repo", str(self.root)]), 1)
        record = pre_pr.record_of((self.root / ".project/review/pre-pr-1.md").read_text(encoding="utf-8"))
        self.assertEqual(record["targets"]["coordinator"]["objective"]["title"], "feat(greeting): greet the whole world")

        # The patch lands; ship re-enters, and the important fix gets a confirmation round.
        (self.root / "hello.py").write_text("print('hello, escaped world')\n", encoding="utf-8")
        self.write_state("ship", "active")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "patch landed")
        due = pre_pr.status(self.root)
        self.assertEqual((due["status"], due["round"], due["first"]), ("due", 2, False))
        with self.assertRaisesRegex(pre_pr.PrePrError, "fixed after the first round"):
            pre_pr.publish(self.root, self.objective())
        second = self.run_round(1308, [], [], objective=False)
        self.assertEqual(second["status"], "closed")
        self.assertEqual(pre_pr.status(self.root)["status"], "closed")
        pre_pr.require_closed(self.root)

    def test_a_round_without_a_fix_closes_the_sequence(self) -> None:
        pre_pr.status(self.root)
        result = self.run_round(7, [finding("A1", "sugestao", "Rename it")],
                                [{"target": "coordinator", "id": "A1", "decision": "follow-up",
                                  "reason": "Out of this change.", "risk": "docs"}])
        self.assertEqual(result["status"], "closed")
        self.assertFalse((self.root / ".project/review/PATCH-FINDINGS.md").exists())
        state = (self.root / ".project/STATE.md").read_text(encoding="utf-8")
        self.assertIn("status: active", state)
        self.assertIn("pre-PR closed: round 1 found nothing to fix", state)
        pre_pr.require_closed(self.root)
        (self.root / ".project/review/pre-pr-1.md").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(pre_pr.PrePrError, "changed after it was written"):
            pre_pr.require_closed(self.root)

    def test_the_cap_is_enforced_by_publish(self) -> None:
        pre_pr.status(self.root)
        journal = pre_pr.load_journal(self.root)
        journal["concluded"] = 3
        journal["objectives"] = {"coordinator": {"title": "feat(x): y", "problem": "p", "scope": "s", "language": "en"}}
        journal["rounds"] = [{"round": number, "status": "concluded", "stage": "finished", "confirm": True,
                              "head": self.head, "targets": {}} for number in (1, 2, 3)]
        pre_pr._save(self.root, journal)
        self.assertEqual(pre_pr.status(self.root)["status"], "cap-reached")
        with self.assertRaisesRegex(pre_pr.PrePrError, "needs --owner-ruling"):
            pre_pr.publish(self.root)
        ruling = self.files / "ruling.txt"
        ruling.write_text("One more round.\n", encoding="utf-8")
        self.assertEqual(pre_pr.publish(self.root, owner_ruling=ruling)["round"], 4)
        self.assertIn("past the cap; owner ruling: One more round.",
                      (self.root / ".project/STATE.md").read_text(encoding="utf-8"))

    def test_publish_never_touches_a_ref_it_did_not_publish(self) -> None:
        git(self.root, "push", "-q", "--no-verify", "origin", "main:refs/heads/pre-pr/milestone-001")
        pre_pr.status(self.root)
        with self.assertRaisesRegex(pre_pr.PrePrError, "did not publish"):
            pre_pr.publish(self.root, self.objective())

    def test_retire_waits_for_the_running_job(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        pre_pr.record(self.root, "coordinator", 5, self.head)
        with self.assertRaisesRegex(pre_pr.PrePrError, "still fetches the branch"):
            pre_pr.retire(self.root, "coordinator")
        with self.assertRaisesRegex(pre_pr.PrePrError, "has not ended"):
            pre_pr.ended(self.root, "coordinator", dict(situation(5, 0), terminou=False, situacao="executando"))
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), self.head)
        # A lost execution id: a new request cancels the old one and is recorded instead.
        pre_pr.record(self.root, "coordinator", 6, self.head)
        pre_pr.ended(self.root, "coordinator", situation(6, 0, outcome="falhou"))
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), "")
        self.assertIn("ended falhou: quota", (self.root / ".project/STATE.md").read_text(encoding="utf-8"))

    def test_capture_and_rule_refuse_what_would_drop_a_finding(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        pre_pr.record(self.root, "coordinator", 9, self.head)
        pre_pr.ended(self.root, "coordinator", situation(9, 2))
        payload = {"execucao_id": 9, "achados": [finding("A1", "bloqueio", "Broken")]}
        with self.assertRaisesRegex(pre_pr.PrePrError, "reported 2 included findings"):
            pre_pr.capture(self.root, "coordinator", payload)
        payload["achados"].append(finding("A2", "nit", "Style"))
        pre_pr.capture(self.root, "coordinator", payload)
        with self.assertRaisesRegex(pre_pr.PrePrError, "different payload"):
            pre_pr.capture(self.root, "coordinator", dict(payload, achados=payload["achados"][:1]))
        corrijo = {"target": "coordinator", "id": "A1", "decision": "corrijo", "reason": "Yes.", "fix_direction": "Fix."}
        with self.assertRaisesRegex(pre_pr.PrePrError, "missing: coordinator A2"):
            pre_pr.rule(self.root, {"verbatim": "ok", "findings": [corrijo]})
        for reason in ("It is fine.", "missing.py:1 shows it", "hello.py:99 shows it"):
            with self.assertRaisesRegex(pre_pr.PrePrError, "path:line"):
                pre_pr.rule(self.root, {"verbatim": "ok", "findings": [corrijo, {
                    "target": "coordinator", "id": "A2", "decision": "contesto", "reason": reason}]})
        with self.assertRaisesRegex(pre_pr.PrePrError, "did not answer A2"):
            pre_pr.rule(self.root, {"verbatim": "ok", "findings": [corrijo, {
                "target": "coordinator", "id": "A2", "decision": "follow-up", "reason": "Later.",
                "risk": "other"}]})
            pre_pr.finish(self.root, {"coordinator": {"execucao_id": 9, "respondidos": [{"achado": "A1"}]}})

    def test_finish_removes_the_stale_final_written_at_the_round_head(self) -> None:
        review = self.root / ".project/review"
        review.mkdir(parents=True)
        (review / "FINAL.md").write_text(f"# Final Review\n\nReviewed HEAD: {self.head}\n", encoding="utf-8")
        (review / "final-gap-1.md").write_text(f"# Gap Review — 1: project Verify\n\nReviewed HEAD: {self.head}\n",
                                               encoding="utf-8")
        pre_pr.status(self.root)
        self.run_round(11, [finding("A1", "importante", "Broken")],
                       [{"target": "coordinator", "id": "A1", "decision": "corrijo", "reason": "Yes.",
                         "fix_direction": "Fix it."}])
        self.assertFalse((review / "FINAL.md").exists())
        self.assertFalse((review / "final-gap-1.md").exists())
        (review / "FINAL.md").write_text(f"# Final Review\n\nReviewed HEAD: {self.head}\n", encoding="utf-8")
        with self.assertRaisesRegex(check_handoffs.HandoffError, "pre_pr.py finish removes it"):
            check_handoffs.validate_patch_findings(self.root)

    def ruling(self, text: str = "The owner decided.") -> Path:
        path = self.files / "owner.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_empty_round_asks_nothing_and_closes(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        pre_pr.record(self.root, "coordinator", 21, self.head)
        pre_pr.ended(self.root, "coordinator", situation(21, 0))
        pre_pr.capture(self.root, "coordinator", {"execucao_id": 21, "achados": []})
        self.assertEqual(pre_pr.finish(self.root, {})["status"], "closed")
        self.assertIn("No finding; no question.", (self.root / ".project/review/pre-pr-1.md").read_text(encoding="utf-8"))
        pre_pr.require_closed(self.root)

    def test_the_patch_gate_refuses_a_hand_off_that_drops_a_corrijo(self) -> None:
        pre_pr.status(self.root)
        fix = {"decision": "corrijo", "reason": "Yes.", "fix_direction": "Fix it."}
        self.run_round(31, [finding("A1", "importante", "One"), finding("A2", "nit", "Two")],
                       [dict(fix, target="coordinator", id="A1"), dict(fix, target="coordinator", id="A2")])
        check_handoffs.validate_patch_findings(self.root)
        handoff = self.root / ".project/review/PATCH-FINDINGS.md"
        text = handoff.read_text(encoding="utf-8")
        handoff.write_text(text[:text.index("### P002")], encoding="utf-8")
        with self.assertRaisesRegex(check_handoffs.HandoffError, "every corrijo finding"):
            check_handoffs.validate_patch_findings(self.root)

    def test_close_always_needs_the_owner_ruling_and_logs_once(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        pre_pr.record(self.root, "coordinator", 41, self.head)
        pre_pr.ended(self.root, "coordinator", situation(41, 0, outcome="falhou"))
        with self.assertRaisesRegex(pre_pr.PrePrError, "--owner-ruling"):
            pre_pr.close(self.root, "reviewer down")
        with self.assertRaisesRegex(pre_pr.PrePrError, "empty"):
            pre_pr.close(self.root, "reviewer down", self.ruling("  "))
        with mock.patch.object(pre_pr, "_log_only", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                pre_pr.close(self.root, "reviewer down", self.ruling())
        self.assertEqual(pre_pr.status(self.root)["status"], "closed")
        self.assertEqual(pre_pr.status(self.root)["status"], "closed")
        state = (self.root / ".project/STATE.md").read_text(encoding="utf-8")
        self.assertEqual(state.count("pre-PR closed: reviewer down; owner ruling: The owner decided."), 1)

    def test_adoption_never_retires_a_recorded_attempt_and_reads_the_ruling_first(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        pre_pr.record(self.root, "coordinator", 51, self.head)
        with self.assertRaisesRegex(pre_pr.PrePrError, "--owner-ruling"):
            pre_pr.retire(self.root, "coordinator", adopt=self.head)
        with self.assertRaisesRegex(pre_pr.PrePrError, "never by adoption"):
            pre_pr.retire(self.root, "coordinator", adopt=self.head, owner_ruling=self.ruling())
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), self.head)

    def test_abandoning_an_interrupted_publish_deletes_what_it_pushed(self) -> None:
        pre_pr.status(self.root)
        original = pre_pr._save
        calls = {"n": 0}

        def interrupted(repo, journal, state=None):
            row = pre_pr._running(journal)
            target = row["targets"]["coordinator"] if row else {}
            if target.get("stage") == "published":
                raise RuntimeError("interrupted after the push")
            return original(repo, journal, state)

        with mock.patch.object(pre_pr, "_save", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                pre_pr.publish(self.root, self.objective())
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), self.head)
        pre_pr.retire(self.root, "coordinator", abandon=True)
        self.assertEqual(self.remote_ref("refs/heads/pre-pr/milestone-001"), "")
        self.assertFalse(members.authorized(self.root, "demo", "push", "refs/heads/pre-pr/milestone-001", self.head))

    def test_bindings_refuse_a_raced_head_another_execution_and_a_foreign_payload(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        with self.assertRaisesRegex(pre_pr.PrePrError, "raced the request"):
            pre_pr.record(self.root, "coordinator", 61, "b" * 40)
        pre_pr.record(self.root, "coordinator", 61, self.head)
        with self.assertRaisesRegex(pre_pr.PrePrError, "not 61"):
            pre_pr.ended(self.root, "coordinator", situation(62, 0))
        pre_pr.ended(self.root, "coordinator", situation(61, 0))
        with self.assertRaisesRegex(pre_pr.PrePrError, "head_sha"):
            pre_pr.capture(self.root, "coordinator", {"execucao_id": 61, "head_sha": "b" * 40, "achados": []})
        with self.assertRaisesRegex(pre_pr.PrePrError, "not 61"):
            pre_pr.capture(self.root, "coordinator", {"execucao_id": 62, "achados": []})

    def test_missing_tools_never_close_a_running_round(self) -> None:
        pre_pr.status(self.root)
        pre_pr.publish(self.root, self.objective())
        self.assertEqual(pre_pr.status(self.root, tools=False)["status"], "pending")

    def test_no_tools_or_no_target_closes_with_the_reason(self) -> None:
        self.assertEqual(pre_pr.status(self.root, tools=False)["status"], "closed")
        self.assertIn("pre-PR closed: the reviewer's MCP tools are not available",
                      (self.root / ".project/STATE.md").read_text(encoding="utf-8"))

    def test_direct_integration_has_no_target(self) -> None:
        self.write_state("ship", "active", integration="direct")
        result = pre_pr.status(self.root)
        self.assertEqual(result["status"], "closed")
        self.assertIn("integration is direct", result["reason"])


class RenderingTests(unittest.TestCase):
    """Reviewer text never strands the patch hand-off (the 1.7.1.65 defect class)."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        git(self.root, "init", "-q", "-b", "feature/milestone-001")
        (self.root / ".project/review").mkdir(parents=True)
        (self.root / ".project/STATE.md").write_text(
            STATE.format(phase="ship", status="blocked", integration="pull-request"), encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "state")

    def target(self, name: str, items) -> dict:
        return {"repo": f"acme/{name}", "branch": f"pre-pr/{name}", "branch_do_pr": f"feature/{name}",
                "base": "main", "sha": "a" * 40, "execution": name, "outcome": {"veredito": "COMMENT"},
                "findings": {"achados": items}}

    def hand_off(self, row, decisions, objectives) -> str:
        report = pre_pr.report_text({"objectives": objectives}, row, decisions, "ok")
        path = self.root / ".project/review/pre-pr-1.md"
        path.write_text(report, encoding="utf-8")
        (self.root / ".project/review/PATCH-FINDINGS.md").write_text(
            pre_pr.findings_text(row, decisions, ".project/review/pre-pr-1.md"), encoding="utf-8")
        journal = pre_pr._new_journal(pre_pr._state(self.root))
        journal["rounds"] = [{"round": 1, "status": "concluded", "stage": "finished", "targets": {}}]
        journal["reports"] = {"pre-pr-1.md": pre_pr._digest_bytes(path.read_bytes())}
        pre_pr._save(self.root, journal)
        return report

    def check(self, text: str) -> None:
        item = finding("A1", "importante", text)
        item["cenario"] = text
        row = {"round": 1, "head": "a" * 40, "targets": {"coordinator": self.target("coordinator", [item])}}
        decisions = [{"target": "coordinator", "id": "A1", "decision": "corrijo", "reason": text or "r",
                      "fix_direction": text or "f"}]
        report = self.hand_off(row, decisions, {"coordinator": {"title": "feat(x): y", "problem": text or "p",
                                                                "scope": "s", "language": "en"}})
        check_handoffs.validate_patch_findings(self.root)
        self.assertEqual(pre_pr.record_of(report)["targets"]["coordinator"]["achados"][0]["descricao"], text)

    def test_two_targets_keep_their_own_a1(self) -> None:
        row = {"round": 1, "head": "a" * 40, "targets": {
            "api": self.target("api", [finding("A1", "importante", "API one")]),
            "web": self.target("web", [finding("A1", "bloqueio", "Web one")])}}
        decisions = [{"target": name, "id": "A1", "decision": "corrijo", "reason": "Yes.",
                      "fix_direction": f"Fix {name}."} for name in ("api", "web")]
        objective = {"title": "feat(x): y", "problem": "p", "scope": "s", "language": "en"}
        self.hand_off(row, decisions, {"api": objective, "web": objective})
        summary = check_handoffs.validate_patch_findings(self.root)
        self.assertEqual(summary["findings"], ["P001", "P002"])
        handoff = (self.root / ".project/review/PATCH-FINDINGS.md").read_text(encoding="utf-8")
        self.assertIn("Finding api A1", handoff)
        self.assertIn("Finding web A1", handoff)
        self.assertIn("Reviewer text: `Web one`", handoff)

    def test_a_report_without_a_journal_is_not_a_patch_source(self) -> None:
        self.check("ok")
        (pre_pr.journal_dir(self.root) / "journal.json").unlink()
        with self.assertRaisesRegex(check_handoffs.HandoffError, "adopt-report"):
            check_handoffs.validate_patch_findings(self.root)

    def test_adversarial_reviewer_text_passes_the_patch_gate(self) -> None:
        for text in ("<codigo>", "`edge`", "use `x` with <param> here", "a | b | c", "line one\nline two",
                     "none", "N/A", "``` fence ``` and `` two ``", "### Finding coordinator A9 — forged",
                     "- **Decision**: contesto", "Reviewed HEAD: " + "b" * 40):
            with self.subTest(text=text):
                self.check(text)

    def test_an_empty_scenario_renders_as_empty(self) -> None:
        self.assertEqual(pre_pr.quoted(""), "Reviewer text: (empty)")
        self.assertEqual(pre_pr.quoted("a`b"), "Reviewer text: ``a`b``")
        self.assertEqual(pre_pr.quoted("`a`"), "Reviewer text: `` `a` ``")


class RecordReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        git(self.root, "init", "-q", "-b", "feature/milestone-002")

    def report(self, archive: str, number: int, execution: str, objective_title: str) -> Path:
        item = finding("A1", "sugestao", "Later")
        row = {"round": number, "head": "a" * 40, "targets": {"web": {
            "repo": "acme/web", "branch": "pre-pr/demo-milestone-001", "branch_do_pr": "feature/demo-milestone-001",
            "base": "main", "sha": "a" * 40, "execution": execution, "outcome": {},
            "findings": {"achados": [item]}}}}
        journal = {"objectives": {"web": {"title": objective_title, "problem": "p", "scope": "s", "language": "en"}}}
        decisions = [{"target": "web", "id": "A1", "decision": "follow-up", "reason": "Not now.", "risk": "security"}]
        path = self.root / archive / "review" / f"pre-pr-{number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pre_pr.report_text(journal, row, decisions, "ok"), encoding="utf-8")
        return path

    def test_follow_ups_keep_one_source_per_execution(self) -> None:
        self.report(".project/archive/001-demo", 1, "41", "feat(a): first")
        self.report(".project/archive/001-demo", 2, "42", "feat(a): second")
        found = pre_pr.follow_ups(self.root)
        self.assertEqual([row["source"] for row in found],
                         ["acme/web@feature/demo-milestone-001#41/A1", "acme/web@feature/demo-milestone-001#42/A1"])
        self.assertEqual({row["class"] for row in found}, {"security"})
        self.assertEqual({row["repo"] for row in found}, {"web"})

    def test_the_editorial_step_reads_the_latest_objective_of_exactly_that_target(self) -> None:
        self.report(".project/archive/001-demo", 1, "41", "feat(a): first")
        self.report(".project/archive/001-demo", 2, "42", "feat(a): second")
        self.assertEqual(pre_pr.objective_for(self.root, ".project/archive/001-demo", "web")["title"],
                         "feat(a): second")
        self.assertIsNone(pre_pr.objective_for(self.root, ".project/archive/001-demo", "coordinator"))


class EditorialObjectiveTests(unittest.TestCase):
    objective = {"title": "feat(a): b", "problem": "Problem.", "scope": "Scope.", "language": "en"}

    def value(self, **changes):
        value = {"title": "feat(a): b", "language": "en",
                 "sections": {"problem": "Problem.", "changes": "c", "scope": "Scope.", "validation": "v",
                              "risks": "r"}}
        value.update(changes)
        return value

    def test_the_copy_repeats_the_reviewed_objective(self) -> None:
        value = self.value()
        pr_editorial.require_objective(value, self.objective)
        self.assertEqual(value["pre_pr_objective"], self.objective)
        pr_editorial.require_objective(self.value(), None)

    def test_a_difference_or_a_translation_needs_a_disclosed_reason(self) -> None:
        for value in (self.value(title="feat(a): c"), self.value(language="pt-BR")):
            with self.assertRaisesRegex(pr_editorial.EditorialError, "objective_change"):
                pr_editorial.require_objective(value, self.objective)
        value = self.value(language="pt-BR", objective_change="The owner switched the PR language.")
        pr_editorial.require_objective(value, self.objective)
        self.assertEqual(value["objective_change"], "The owner switched the PR language.")


class UndoAdmissionTests(SingleRepoFixture):
    def test_undo_admits_and_restores_only_the_recorded_report(self) -> None:
        pre_pr.status(self.root)
        self.run_round(3, [], [])
        report = self.root / ".project/review/pre-pr-1.md"
        archive = ".project/archive/001-demo"
        archived = self.root / archive / "review" / "pre-pr-1.md"
        archived.parent.mkdir(parents=True)
        report.replace(archived)
        (archived.parent / "pre-pr-2.md").write_text("unknown\n", encoding="utf-8")
        admitted = pipeline_undo._admitted_pre_pr_reports(self.root, archive)
        self.assertEqual(sorted(admitted), ["pre-pr-1.md"])
        pipeline_undo._remove_untracked_tree(self.root, archive)
        pipeline_undo._restore_pre_pr_reports(self.root, admitted)
        pre_pr.require_closed(self.root)

    def test_adopt_report_recovers_a_lost_journal(self) -> None:
        pre_pr.status(self.root)
        self.run_round(3, [], [])
        directory = pre_pr.journal_dir(self.root)
        (directory / "journal.json").unlink()
        ruling = self.files / "ruling.txt"
        ruling.write_text("The journal was lost in a fresh clone.\n", encoding="utf-8")
        self.assertEqual(pre_pr.adopt_report(self.root, ruling)["reports"], ["pre-pr-1.md"])
        self.assertTrue(pre_pr.admitted_report(self.root, "pre-pr-1.md",
                                               (self.root / ".project/review/pre-pr-1.md").read_bytes()))


class MemberPublicationTests(unittest.TestCase):
    """A member's pre-PR branch passes the member's installed pre-push hook."""

    def setUp(self) -> None:
        import tests.test_member_integration as member_integration
        member_integration.MemberIntegrationTests.setUp(self)
        for repo in (self.root, self.member):
            git(repo, "config", "user.name", "t")
            git(repo, "config", "user.email", "t@t")
        listed = self.root / ".project/MEMBERS.md"
        listed.write_text(listed.read_text(encoding="utf-8").replace("Integration: default",
                                                                    "Integration: pull-request"), encoding="utf-8")
        home = self.root.parent / "home"
        (home / ".gsd-path").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        install_pre_push(self.member)

    fixture = __import__("tests.test_member_integration", fromlist=["x"]).MemberIntegrationTests.fixture

    def test_member_target_publishes_and_retires_under_the_member_hook(self) -> None:
        found, skipped = pre_pr.targets(self.root)
        self.assertEqual([row["target"] for row in found], ["web"])
        self.assertEqual(found[0]["branch"], "pre-pr/demo-milestone-001")
        self.assertEqual(found[0]["branch_do_pr"], "feature/demo-milestone-001")
        self.assertEqual(found[0]["repo"], "acme/web")
        refused = subprocess.run(["git", "push", "origin", f"{self.member_tip}:refs/heads/pre-pr/demo-milestone-001"],
                                 cwd=self.member, capture_output=True, encoding="utf-8")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(pre_pr.status(self.root)["status"], "due")
        objective = self.root.parent / "objective.json"
        objective.write_text(json.dumps({"targets": {"web": {
            "title": "feat(web): ship the landing", "problem": "Nothing lands.", "scope": "The web member."}}}),
            encoding="utf-8")
        pre_pr.publish(self.root, objective)
        ref = "refs/heads/pre-pr/demo-milestone-001"
        remote = lambda: git(self.remote, "rev-parse", "--verify", "--quiet", ref, check=False)
        self.assertEqual(remote(), self.member_tip)
        pre_pr.record(self.root, "web", 1, self.member_tip)
        pre_pr.ended(self.root, "web", situation(1, 0))
        self.assertEqual(remote(), "")


if __name__ == "__main__":
    unittest.main()
