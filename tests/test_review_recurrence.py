"""review_findings.py recurrence attributes cited lines to earlier repair landings."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_findings.py"
sys.path.insert(0, str(ROOT / "scripts"))

import review_findings  # noqa: E402

PLAN = """# Plan — demo

## Config

- max_review_cycles: 3
- review_panel: named

## Wave 1 — parser

Goal: parse rows
Review depth: full
"""

TASK = """---
id: {id}
title: {title}
wave: {wave}
deps: []
status: done
{extra}{repo}files:
  - {path}
---

# {id} — {title}

## Acceptance criteria

1. Parses CSV rows
{findings}"""

REVIEW = """# Review — wave 1, cycle 2

Wave verdict: blocked
Cycle: 2
Depth: full
Tasks reviewed: 1

## T001 — Parse input: fail

- ❌ Parses CSV rows — found: drops the header, {evidence}
  fix: keep the header

Warnings (non-blocking):
- none

Contract violations (blocking):
- none
"""

PANEL = """# Wave panel

Mode: named
Wave: 1
Cycle: 2
Families: gpt
Actionable: 3
Warnings: 1
Conflicts: 0

## Actionable

- high / criterion — T001 acceptance 1
  evidence: src/parser.py:1
  found: original line
  fix: keep it
  source: gpt.md

- high / criterion — T001 acceptance 1
  evidence: parser.py:2; src/parser.py:99
  found: unusable references
  fix: cite the file
  source: gpt.md

- high / criterion — T001 acceptance 1
  evidence: src/parser.py:5
  found: manual edit
  fix: review it
  source: gpt.md

## Warnings

- medium / preference — style
  evidence: src/parser.py:3
  found: taste
  fix: optional
  source: gpt.md

## Conflicts

- none

## By family

# Panel — wave 1, cycle 2

- Family: gpt
- Model: demo

## Findings

### F1
- Severity: high
- Kind: criterion
- Criterion: T001 acceptance 1
- Evidence: src/parser.py:3
- Found: repeated in the family file
- Fix direction: ignore this copy
"""


FINDINGS = "\n## Review findings\n\n### t001_ac1\nCriterion: Parses CSV rows\n- drops rows\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          encoding="utf-8").stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "demo@example.invalid")
    git(root, "config", "user.name", "Demo")
    git(root, "config", "commit.gpgsign", "false")


def commit(root: Path, subject: str, body: str = "") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", subject, *(["-m", body] if body else []))
    return git(root, "rev-parse", "HEAD")


def landing(task_file: str, base: str, *paths: str, contract: str = "") -> str:
    lines = [f"Task: {task_file}", f"Base: {base}", *([f"Contract: {contract}"] if contract else []), "Files:"]
    return "\n".join(lines + [f"- {path}" for path in sorted(paths)])


class Project:
    """A synthetic coordinator: T001 wrote the parser, repair T002 rewrote lines 3-4."""

    def __init__(self, test):
        self.dir = tempfile.TemporaryDirectory()
        test.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "coordinator"
        repository(self.root)
        self.project = self.root / ".project"
        write(self.project / "plan/PLAN.md", PLAN)
        self.start = commit(self.root, "plan: approve")
        self.task("T001-parse-input.md", "T001", "Parse input", 1, "src/parser.py", base=self.start)
        write(self.root / "src/parser.py", "".join(f"line {n}\n" for n in range(1, 6)))
        t001 = ".project/tasks/T001-parse-input.md"
        self.t001 = commit(self.root, "T001: parse", landing(t001, self.start, t001, "src/parser.py"))
        self.task("T002-fix-wave-1-cycle-1.md", "T002", "Fix wave 1 cycle 1", 2, "src/parser.py",
                  findings=FINDINGS, base=self.t001)
        write(self.root / "src/parser.py", "line 1\nline 2\nfixed 3\nfixed 4\nline 5\n")
        t002 = ".project/tasks/T002-fix-wave-1-cycle-1.md"
        self.t002 = commit(self.root, "T002: repair", landing(t002, self.t001, t002, "src/parser.py"))
        write(self.root / "src/parser.py", "line 1\nline 2\nfixed 3\nfixed 4\nmanual 5\n")
        commit(self.root, "manual edit without a landing trailer")

    def task(self, name, task_id, title, wave, path, findings="", repo=None, base=None, member_base=None):
        extra = (f"base: {base}\n" if base else "") + (f"member_base: {member_base}\n" if member_base else "")
        write(self.project / "tasks" / name,
              TASK.format(id=task_id, title=title, wave=wave, path=path, findings=findings, extra=extra,
                          repo=f"repo: {repo}\n" if repo else ""))

    def review(self, evidence="src/parser.py:3-4", panel=True) -> str:
        write(self.project / "review/wave-1.cycle2.md", REVIEW.format(evidence=evidence))
        if panel:
            write(self.project / "review/wave-1.cycle2.panel.md", PANEL)
        return commit(self.root, "build: record wave 1 cycle 2 review")

    def run(self, *extra):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "recurrence", "--repo", str(self.root),
             "--wave", "1", "--cycle", "2", *extra],
            capture_output=True, encoding="utf-8", check=False)
        return result.returncode, json.loads(result.stdout)


class RecurrenceTest(unittest.TestCase):
    def test_repair_lines_recur_and_original_lines_in_the_same_file_do_not(self):
        project = Project(self)
        base = project.review()
        code, result = project.run("--review-base", base)
        self.assertEqual(code, 0, result)
        canonical, original, unusable, manual = result["findings"]
        self.assertEqual((canonical["source"], canonical["locator"]), ("canonical", "t001_ac1"))
        self.assertTrue(canonical["recurrent"])
        (written,) = canonical["references"][0]["written_by"]
        self.assertEqual(written, {"task": "T002", "commit": project.t002, "repair": True,
                                   "source_wave": 1, "source_cycle": 1})
        self.assertFalse(original["recurrent"])
        self.assertEqual(original["references"][0]["written_by"][0]["task"], "T001")
        self.assertEqual(unusable["references"], [])
        self.assertFalse(manual["recurrent"])
        self.assertEqual(manual["references"][0]["written_by"], [])
        self.assertEqual(result["files"], [{"path": "src/parser.py", "repo": None,
                                            "recurrent_findings": 1, "repairs_cited": ["T002"]}])

    def test_unusable_references_are_explicit_and_preferences_and_family_copies_are_ignored(self):
        project = Project(self)
        _, result = project.run("--review-base", project.review())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(sorted(item["reason"] for item in result["unattributable"]),
                         ["bare-name-or-missing-path", "line-range-outside-reviewed-file"])
        sources = [finding["criterion"] for finding in result["findings"]]
        self.assertNotIn("style", sources)
        self.assertEqual(len(result["findings"]), 4)

    def test_lines_are_read_at_the_review_base_not_the_current_head(self):
        project = Project(self)
        base = project.review(panel=False)
        write(project.root / "src/parser.py", "new 0\nnew 1\n" + (project.root / "src/parser.py").read_text())
        commit(project.root, "later edit shifts every line")
        _, result = project.run("--review-base", base)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["findings"][0]["recurrent"])
        self.assertEqual(result["findings"][0]["references"][0]["reviewed_commit"], base)

    def test_missing_review_base_is_partial_without_guessing(self):
        project = Project(self)
        project.review(panel=False)
        code, result = project.run()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["unattributable"], [{"reason": "missing-review-base"}])
        self.assertEqual(result["findings"], [])

    def test_repeated_calls_are_identical_and_write_nothing(self):
        project = Project(self)
        base = project.review()
        first = project.run("--review-base", base)
        second = project.run("--review-base", base)
        self.assertEqual(first, second)
        self.assertEqual(git(project.root, "status", "--porcelain"), "")

    def test_task_trailer_without_its_product_file_is_not_provenance(self):
        project = Project(self)
        t003 = ".project/tasks/T003-fix-wave-1-cycle-2.md"
        project.task("T003-fix-wave-1-cycle-2.md", "T003", "Fix", 2, "src/other.py",
                     findings=FINDINGS, base=project.t002)
        write(project.root / "src/parser.py", "line 1\nclaimed 2\nfixed 3\nfixed 4\nmanual 5\n")
        commit(project.root, "T003: unrelated", landing(t003, project.t002, t003, "src/other.py"))
        _, result = project.run("--review-base", project.review("src/parser.py:2", panel=False))
        self.assertEqual(result["findings"][0]["references"][0]["written_by"], [])
        self.assertFalse(result["findings"][0]["recurrent"])

    def member(self, project, record_landing=True):
        """A member whose build locked `baseline`; T004 repaired line 2 after it."""
        member = Path(project.dir.name) / "web"
        repository(member)
        write(member / "src/view.py", "one\ntwo\n")
        old = commit(member, "earlier milestone landing")
        write(member / "src/view.py", "one\ntwo\nthree\n")
        member_base = commit(member, "baseline")
        t004 = ".project/tasks/T004-fix-wave-1-cycle-1.md"
        write(member / "src/view.py", "one\nrepaired two\nthree\n")
        member_landing = commit(member, "T004: repair view", landing(t004, member_base, "src/view.py", contract="c"))
        write(member / "src/view.py", "one\nunreviewed two\nthree\n")
        commit(member, "after the review")
        write(project.project / "MEMBERS.md", f"# Members\n\n## web\nCheckout: {member}\nRemote: none\n")
        write(project.project / "build/members.json",
              json.dumps({"members": [{"name": "web", "branch": "feature/milestone-002", "base": member_base}]}))
        project.task("T004-fix-wave-1-cycle-1.md", "T004", "Fix view", 2, "src/view.py", repo="web",
                     findings=FINDINGS, member_base=member_base)
        coordinator = commit(project.root, "member registry")
        # A record from an earlier milestone stays on the first-parent history.
        commit(project.root, "T009: earlier", f"Task: .project/tasks/T009-old.md\nBase: {coordinator}\n"
                                              f"Member: web {old} {old}")
        if record_landing:
            commit(project.root, "T004: Fix view",
                   f"Task: {t004}\nBase: {coordinator}\nMember: web {member_landing} {member_base}")
        return member_base, member_landing

    def test_member_lines_use_the_recorded_member_landing(self):
        project = Project(self)
        _, member_landing = self.member(project)
        _, result = project.run("--review-base", project.review("src/view.py:2", panel=False))
        reference = result["findings"][0]["references"][0]
        self.assertEqual((reference["repo"], reference["reviewed_commit"]), ("web", member_landing))
        self.assertTrue(result["findings"][0]["recurrent"])
        self.assertEqual(result["files"][0]["repo"], "web")

    def test_an_earlier_milestone_member_record_does_not_replace_the_locked_base(self):
        project = Project(self)
        member_base, _ = self.member(project, record_landing=False)
        _, result = project.run("--review-base", project.review("src/view.py:3", panel=False))
        self.assertEqual(result["findings"][0]["references"][0]["reviewed_commit"], member_base)

    def test_a_reused_task_name_from_an_earlier_milestone_is_not_this_repair(self):
        project = Project(self)
        stale = ".project/tasks/T002-fix-wave-1-cycle-1.md"
        write(project.root / "src/parser.py", "earlier 1\nline 2\nfixed 3\nfixed 4\nmanual 5\n")
        # Same task file name, but the landing's base is not the base this task records.
        commit(project.root, "T002: earlier milestone", landing(stale, project.start, "src/parser.py"))
        _, result = project.run("--review-base", project.review("src/parser.py:1", panel=False))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["findings"][0]["references"][0]["written_by"], [])

    def test_an_archived_task_trailer_keeps_the_other_lines_of_the_citation(self):
        project = Project(self)
        archived = ".project/tasks/T009-archived-parser.md"
        write(project.root / "src/parser.py", "line 1\narchived 2\nfixed 3\nfixed 4\nmanual 5\n")
        commit(project.root, "T009: archived", landing(archived, project.start, "src/parser.py"))
        _, result = project.run("--review-base", project.review("src/parser.py:2-3", panel=False))
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["findings"][0]["recurrent"])
        self.assertEqual([item["task"] for item in result["findings"][0]["references"][0]["written_by"]],
                         ["T002"])

    def test_a_fix_direction_path_is_not_evidence(self):
        project = Project(self)
        base = project.review("src/parser.py:1\n  fix: mirror the guard at src/parser.py:3", panel=False)
        _, result = project.run("--review-base", base)
        (finding,) = result["findings"]
        self.assertFalse(finding["recurrent"])
        self.assertEqual([reference["lines"] for reference in finding["references"]], ["1"])

    def test_a_dot_slash_citation_matches_the_landing_file_list(self):
        project = Project(self)
        _, result = project.run("--review-base", project.review("./src/parser.py:3", panel=False))
        self.assertTrue(result["findings"][0]["recurrent"])
        self.assertEqual(result["files"][0]["path"], "src/parser.py")

    def test_member_without_a_snapshot_is_explicit(self):
        project = Project(self)
        write(project.project / "MEMBERS.md", "# Members\n\n## web\nCheckout: /missing\nRemote: none\n")
        _, result = project.run("--review-base", project.review(panel=False))
        self.assertEqual(result["status"], "partial")
        self.assertIn({"repo": "web", "reason": "missing-member-snapshot"}, result["unattributable"])
        self.assertIn("incomplete-membership", [item["reason"] for item in result["unattributable"]])


class EvidenceReferenceTest(unittest.TestCase):
    def test_reference_forms(self):
        found = [match.group("path", "lines") for match in review_findings.EVIDENCE_REF.finditer(
            "`src/a.py:12-16`; src/b.ts:4–9,20 and (lib/c.go:7), not http://x.example/d.py:3")]
        self.assertEqual(found, [("src/a.py", "12-16"), ("src/b.ts", "4–9,20"), ("lib/c.go", "7")])


if __name__ == "__main__":
    unittest.main()
