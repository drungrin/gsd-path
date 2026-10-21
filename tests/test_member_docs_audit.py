"""Member docs audits: frozen-pin inventory, member headers, and prior/changed binding.

Every fixture is a genuine temporary coordinator with members registered through
`members.py add`, each with a bare origin remote. Identifiers are synthetic.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from tests._platform import requires_symlink

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_docs_audit.py"
MEMBERS = ROOT / "scripts" / "members.py"

STATE = """---
pipeline: gsd-path/v2
project: acme
milestone: demo
phase: inspect
status: active
branch: feature/milestone-001
archive: null
---
"""

GIT_ENV = {
    key: value for key, value in os.environ.items()
    if not key.startswith("GIT_") or key in ("GIT_CONFIG_NOSYSTEM",)
}
GIT_ENV.update({
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
})

Claim = Tuple[str, str, str, str, str]  # doc, claim, type, verdict, evidence


def git(repo: Path, *arguments: str, env: Optional[Dict[str, str]] = None, stdin: Optional[str] = None) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *arguments], cwd=repo, input=stdin,
        encoding="utf-8", errors="replace", capture_output=True, check=True, env=env or GIT_ENV,
    ).stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def member_audit(
    checkout: Path,
    name: str,
    default: str,
    pin: str,
    claims: Sequence[Claim] = (),
    descriptive: Sequence[str] = (),
    rulings: Sequence[str] = (),
    queue: Optional[Sequence[Tuple[int, str, str, str]]] = None,
    audited_head: Optional[str] = None,
    alignment: str = "no",
) -> str:
    """A member audit in the fixed docs-audit format; queue entries are (#, doc, claim, verdict)."""
    tallies = {"verified": 0, "stale": 0, "aspirational": 0, "unverifiable": 0}
    by_doc: Dict[str, list] = {}
    for doc, claim, kind, verdict, evidence in claims:
        tallies[verdict] += 1
        by_doc.setdefault(doc, []).append(f"| {claim} | {kind} | {verdict} | {evidence} |")
    if queue is None:
        unverified = [(doc, claim, verdict) for doc, claim, _, verdict, _ in claims if verdict != "verified"]
        queue = [(number, *item) for number, item in enumerate(unverified, 1)]
    lines = [
        "# Docs Audit", "",
        f"Repo root: {checkout}",
        f"Member: {name}",
        f"Default branch: {default}",
        f"Member HEAD: {pin}",
        "Audited: 2026-10-21",
        f"Audited HEAD: {audited_head or pin}",
        f"Alignment mode: {alignment}", "",
        "## Summary", "", "| Verdict | Count |", "|---------|-------|",
        *(f"| {verdict} | {count} |" for verdict, count in tallies.items()),
        f"| descriptive docs (no testable claims) | {len(descriptive)} |", "",
        "Worst drift: none found.", "",
    ]
    for doc, rows in by_doc.items():
        lines += [f"## Doc: {doc}", "", "| Claim | Type | Verdict | Evidence |",
                  "|-------|------|---------|----------|", *rows, ""]
    lines += ["## Descriptive docs", "", *(f"- {doc}" for doc in descriptive), ""]
    lines += ["## User rulings", "", "| Queue # | Ruling | User's words | Planned |",
              "|---------|--------|--------------|---------|", *rulings, ""]
    lines += ["## Remediation queue", "", "| # | Doc | Claim | Verdict | Class | Suggested action |",
              "|---|-----|-------|---------|-------|------------------|"]
    lines += [f"| {number} | {doc} | {claim} | {verdict} | fix-doc | edit the doc |"
              for number, doc, claim, verdict in queue]
    return "\n".join(lines) + "\n"


class MemberDocsAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.coordinator = self.root / "acme"
        self.coordinator.mkdir()
        git(self.coordinator, "init", "-q", "-b", "main")
        write(self.coordinator / ".project" / "STATE.md", STATE)
        write(self.coordinator / "README.md", "# acme program\n")
        commit_all(self.coordinator, "init")
        self.web = self.make_member("web", "trunk", {
            "README.md": "# web\n\nRun `make` to build.\n",
            "docs/guide.md": "# Guide\n",
            "docs/old.md": "# Old\n",
            "src/app.py": "print(1)\n",
        })
        self.pin = git(self.web, "rev-parse", "refs/remotes/origin/trunk")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_member(self, name: str, default: str, files: Dict[str, str]) -> Path:
        checkout = self.root / name
        checkout.mkdir()
        git(checkout, "init", "-q", "-b", default)
        for relative, text in files.items():
            write(checkout / relative, text)
        commit_all(checkout, "init")
        remote = self.root / "remotes" / f"{name}.git"
        remote.parent.mkdir(exist_ok=True)
        git(self.root, "init", "-q", "--bare", "-b", default, str(remote))
        url = f"https://github.com/acme/{name}.git"
        git(checkout, "remote", "add", "origin", url)
        git(checkout, "config", f"url.{remote.as_uri()}.insteadOf", url)
        git(checkout, "push", "-q", "origin", f"HEAD:refs/heads/{default}")
        git(checkout, "fetch", "-q", "origin")
        git(checkout, "remote", "set-head", "origin", default)
        added = subprocess.run(
            [sys.executable, "-B", str(MEMBERS), "add", "--repo", str(self.coordinator),
             "--name", name, "--checkout", str(checkout)],
            encoding="utf-8", errors="replace", capture_output=True, check=False, env=GIT_ENV,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        return checkout

    def advance_origin(self, member: Path, default: str, relative: str, text: str) -> str:
        """Commit on the remote default and fetch it, leaving the team branch where it is."""
        clone = self.root / f"clone-{member.name}-{relative.replace('/', '-')}"
        git(self.root, "clone", "-q", "-b", default, str(self.root / "remotes" / f"{member.name}.git"), str(clone))
        write(clone / relative, text)
        commit_all(clone, f"change {relative}")
        git(clone, "push", "-q", "origin", f"HEAD:refs/heads/{default}")
        git(member, "fetch", "-q", "origin")
        return git(member, "rev-parse", f"refs/remotes/origin/{default}")

    def plumbed_commit(self, member: Path, entries: Dict[str, Tuple[str, str]],
                       parent: Optional[str] = None) -> str:
        """A commit built in the member object database without touching its index or worktree.

        `entries` maps a path to (mode, blob text), or to ("160000", commit SHA) for a gitlink."""
        index = self.root / f"index-{len(list(self.root.glob('index-*')))}"
        env = {**GIT_ENV, "GIT_INDEX_FILE": str(index)}
        for path, (mode, content) in entries.items():
            sha = content if mode == "160000" else git(member, "hash-object", "-w", "--stdin", stdin=content)
            git(member, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}", env=env)
        tree = git(member, "write-tree", env=env)
        return git(member, "commit-tree", tree, *(("-p", parent) if parent else ()), "-m", "plumbed")

    def write_audit(self, text: str, name: str = "web", project_dir: str = ".project",
                    repo: Optional[Path] = None) -> Path:
        path = (repo or self.coordinator) / project_dir / "research" / f"DOCS-AUDIT-{name}.md"
        write(path, text)
        return path

    def lines_file(self, label: str, lines: Sequence[str]) -> Path:
        path = self.root / "frozen" / f"{label}-{len(list((self.root / 'frozen').glob('*')))}"
        write(path, "".join(f"{line}\n" for line in lines))
        return path

    def run_gate(self, *arguments: str, repo: Optional[Path] = None) -> Tuple[int, str, str]:
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--repo", str(repo or self.coordinator), *arguments],
            encoding="utf-8", errors="replace", capture_output=True, check=False,
            env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return result.returncode, result.stdout, result.stderr

    def web_audit(self, pin: Optional[str] = None, **overrides) -> str:
        options = {
            "claims": [("README.md", '"Run `make` to build"', "command", "verified", "`make` → exit 0")],
            "descriptive": ["docs/guide.md", "docs/old.md"],
        }
        options.update(overrides)
        return member_audit(self.web, "web", "trunk", pin or self.pin, **options)

    def assert_blocked(self, outcome: Tuple[int, str, str], expected: str) -> None:
        code, output, error = outcome
        self.assertEqual(code, 1, output)
        self.assertIn("docs audit validation failed: ", error)
        self.assertIn(expected, error)

    def snapshot(self, *repos: Path) -> list:
        return [(git(repo, "status", "--porcelain", "--untracked-files=all"),
                 git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
                 git(repo, "rev-parse", "HEAD")) for repo in repos]

    def test_inventory_uses_pin_not_team_head_index_or_untracked_files(self) -> None:
        git(self.web, "rm", "-q", "docs/old.md")
        write(self.web / "docs" / "ahead.md", "# Ahead of origin\n")
        commit_all(self.web, "team work ahead of origin")
        write(self.web / "docs" / "staged.md", "# Staged\n")
        git(self.web, "add", "docs/staged.md")
        write(self.web / "NOTES.md", "# Untracked team notes\n")
        (self.web / "docs" / "guide.md").unlink()
        write(self.web / "README.md", "# web, edited in the team checkout\n")
        refused = subprocess.run([sys.executable, "-B", str(MEMBERS), "validate", "--repo", str(self.coordinator)],
                                 encoding="utf-8", capture_output=True, check=False, env=GIT_ENV)
        self.assertIn("uncommitted changes", refused.stderr)
        self.write_audit(self.web_audit())
        before = self.snapshot(self.web, self.coordinator)

        code, output, error = self.run_gate("--member", "web", "--pin", self.pin, "--emit-inventory")
        self.assertEqual(code, 0, error)
        self.assertEqual(output, "README.md\ndocs/guide.md\ndocs/old.md\n")

        frozen = self.lines_file("inventory", output.splitlines())
        for extra in ((), ("--inventory", str(frozen))):
            with self.subTest(extra=extra):
                code, output, error = self.run_gate("--member", "web", "--pin", self.pin, *extra)
                self.assertEqual(code, 0, error)
                result = json.loads(output)
                self.assertEqual(result["descriptive"], ["docs/guide.md", "docs/old.md"])
                self.assertEqual(result["audit"], ".project/research/DOCS-AUDIT-web.md")
                self.assertEqual(result["member"], {
                    "name": "web", "checkout": str(self.web), "default_branch": "trunk",
                    "pin": self.pin, "project_dir": ".project"})
        self.assertEqual(self.snapshot(self.web, self.coordinator), before)

    def test_inventory_honors_exclusions_and_empty_tree(self) -> None:
        excluded = self.plumbed_commit(self.web, {
            "README.md": ("100644", "# web\n"),
            "docs/path.md": ("100644", "# path\n"),
            ".agents/skills/pathology/SKILL.md": ("100644", "# kept\n"),
            "tools/run.md": ("100755", "# executable but regular\n"),
            "node_modules/x/README.md": ("100644", "x\n"),
            "lib/node_modules/y/README.md": ("100644", "y\n"),
            "node_modules/link.md": ("120000", "../README.md"),
            ".project/intent/INTENT.md": ("100644", "# member Path history\n"),
            ".project/research/DOCS-AUDIT-web.md": ("100644", "# own audit\n"),
            ".project/archive/001-old/MANIFEST.md": ("100644", "# archive\n"),
            ".claude/skills/gsd-path/SKILL.md": ("100644", "# installed\n"),
            ".agents/skills/path/SKILL.md": ("100644", "# router alias\n"),
            "docs/UPPER.MD": ("100644", "# not lowercase\n"),
        })
        code, output, error = self.run_gate("--member", "web", "--pin", excluded, "--emit-inventory")
        self.assertEqual(code, 0, error)
        self.assertEqual(output.splitlines(),
                         [".agents/skills/pathology/SKILL.md", "README.md", "docs/path.md", "tools/run.md"])

        empty = self.plumbed_commit(self.web, {"src/app.py": ("100644", "print(1)\n")})
        code, output, error = self.run_gate("--member", "web", "--pin", empty, "--emit-inventory")
        self.assertEqual(code, 0, error)
        self.assertEqual(output, "")
        self.write_audit(member_audit(self.web, "web", "trunk", empty))
        for extra in ((), ("--inventory", str(self.lines_file("empty", [])))):
            with self.subTest(extra=extra):
                code, output, error = self.run_gate("--member", "web", "--pin", empty, *extra)
                self.assertEqual(code, 0, error)
                self.assertEqual((json.loads(output)["docs"], json.loads(output)["descriptive"]), ([], []))

    def test_inventory_audits_an_in_tree_markdown_alias_through_its_target(self) -> None:
        pin = self.plumbed_commit(self.web, {
            "AGENTS.md": ("100644", "# Agents\n"),
            "CLAUDE.md": ("120000", "AGENTS.md"),
            "README.md": ("100644", "# web\n"),
            "docs/link.md": ("120000", "../README.md"),
            "docs/dot.md": ("120000", "./../docs/../AGENTS.md"),
            "node_modules/x/link.md": ("120000", "../../../outside.md"),
        })
        code, output, error = self.run_gate("--member", "web", "--pin", pin, "--emit-inventory")
        self.assertEqual(code, 0, error)
        self.assertEqual(output, "AGENTS.md\nREADME.md\n")
        self.write_audit(member_audit(self.web, "web", "trunk", pin, descriptive=["AGENTS.md", "README.md"]))
        code, output, error = self.run_gate("--member", "web", "--pin", pin,
                                            "--inventory", str(self.lines_file("alias", ["AGENTS.md", "README.md"])))
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["descriptive"], ["AGENTS.md", "README.md"])
        code, _, error = self.run_gate("--member", "web", "--pin", pin, "--inventory", str(
            self.lines_file("alias-listed", ["AGENTS.md", "CLAUDE.md", "README.md"])))
        self.assertEqual(code, 1)
        self.assertIn("member web pinned inventory differs from --inventory", error)

    def test_inventory_rejects_pinned_markdown_symlink_and_gitlink(self) -> None:
        regular = {"README.md": ("100644", "# web\n"), "notes.txt": ("100644", "plain\n"),
                   ".project/intent/INTENT.md": ("100644", "# member Path history\n")}
        cases = {
            "outside.md": {"outside.md": ("120000", "../README.md")},
            "docs/escape.md": {"docs/escape.md": ("120000", "../../web/README.md")},
            "absolute.md": {"absolute.md": ("120000", "/etc/README.md")},
            "missing.md": {"missing.md": ("120000", "docs/absent.md")},
            "text.md": {"text.md": ("120000", "notes.txt")},
            "chain.md": {"chain.md": ("120000", "hop.md"), "hop.md": ("120000", "README.md")},
            "hidden.md": {"hidden.md": ("120000", ".project/intent/INTENT.md")},
            "vendor.md": {"vendor.md": ("160000", self.pin)},
        }
        cases = {path: self.plumbed_commit(self.web, {**regular, **entries}) for path, entries in cases.items()}
        for path, pin in cases.items():
            with self.subTest(path=path):
                expected = f"member web pinned Markdown is not a regular blob: {path}"
                self.assert_blocked(self.run_gate("--member", "web", "--pin", pin, "--emit-inventory"), expected)
                self.write_audit(member_audit(self.web, "web", "trunk", pin, descriptive=["README.md"]))
                self.assert_blocked(self.run_gate("--member", "web", "--pin", pin), expected)

    @requires_symlink
    def test_team_checkout_symlink_is_not_inventoried_from_the_worktree(self) -> None:
        (self.web / "LINKED.md").symlink_to(self.web / "README.md")
        code, output, error = self.run_gate("--member", "web", "--pin", self.pin, "--emit-inventory")
        self.assertEqual(code, 0, error)
        self.assertNotIn("LINKED.md", output.splitlines())

    def test_member_gate_uses_frozen_pin_after_origin_moves(self) -> None:
        self.write_audit(self.web_audit())
        moved = self.advance_origin(self.web, "trunk", "docs/new.md", "# New\n")
        self.assertNotEqual(moved, self.pin)
        code, output, error = self.run_gate("--member", "web", "--pin", self.pin)
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["member"]["pin"], self.pin)

        self.assert_blocked(self.run_gate("--member", "web", "--pin", moved), f"is not the member pin {moved}")
        code, output, error = self.run_gate("--member", "web", "--pin", moved, "--emit-inventory")
        self.assertEqual(code, 0, error)
        self.assertIn("docs/new.md", output.splitlines())
        unknown = "0" * 40
        self.assert_blocked(self.run_gate("--member", "web", "--pin", unknown),
                            f"member web pin {unknown} is not a commit in {self.web}")
        tree = git(self.web, "rev-parse", f"{self.pin}^{{tree}}")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", tree, "--emit-inventory"),
                            f"member web pin {tree} is not a commit")

    def test_member_headers_are_unique_and_bound_to_registry_and_pin(self) -> None:
        valid = self.web_audit()
        member_head = f"Member HEAD: {self.pin}"
        audited_head = f"Audited HEAD: {self.pin}"
        cases = (
            (valid.replace(f"Repo root: {self.web}", f"Repo root: {self.coordinator}"),
             f"Repo root must be {self.web}"),
            (valid.replace("Member: web", "Member: api"), "Member must be web, found api"),
            (valid.replace("Default branch: trunk", "Default branch: main"), "Default branch must be trunk"),
            (valid.replace(member_head + "\n", ""), "header must name Member HEAD: exactly once"),
            (valid.replace(member_head, f"{member_head}\n{member_head}"), "header must name Member HEAD: exactly once"),
            (valid.replace("Member: web", "<!-- Member: web -->"), "header must name Member: exactly once"),
            (valid.replace("Alignment mode: no\n", f"Alignment mode: no\n\n```text\n{member_head}\n```\n"),
             "header must name Member HEAD: exactly once"),
            (valid.replace(member_head, f"Member HEAD: {self.pin[:12]}"), "full 40-character commit SHA"),
            (valid.replace(f"Repo root: {self.web}", f"Repo root: {self.web}\nRepo root: {self.web}"),
             "header must contain exactly one Repo root line"),
            (valid.replace(audited_head, f"{audited_head}\n{audited_head}"),
             "header must contain exactly one Audited HEAD line"),
            (valid.replace(audited_head, "Audited HEAD: none"),
             f"DOCS-AUDIT-web.md Audited HEAD must equal Member HEAD {self.pin}"),
            (valid.replace(audited_head, f"Audited HEAD: {'1' * 40}"),
             f"DOCS-AUDIT-web.md Audited HEAD must equal Member HEAD {self.pin}"),
            (valid.replace("Alignment mode: no", "Alignment mode: yes"), "member web audit does not support alignment mode"),
            (valid.replace("Audited: 2026-10-21", "Audited: today"), "Audited must be an ISO date"),
            (valid.replace("## Summary", "## Overview"), "missing section: ## Summary"),
            (valid.replace("- docs/old.md\n", ""), "not audited: docs/old.md"),
            (valid.replace("| verified | 1 |", "| verified | 2 |"), "verified count 2 does not match 1"),
        )
        for text, expected in cases:
            with self.subTest(expected=expected):
                self.write_audit(text)
                self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin), expected)
        self.write_audit(valid)
        self.assertEqual(self.run_gate("--member", "web", "--pin", self.pin)[0], 0)
        (self.coordinator / ".project/research/DOCS-AUDIT-web.md").write_bytes(b"Repo root: \xff\n")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin), "cannot read")

    def test_member_cli_requires_pin_and_canonical_track_path(self) -> None:
        self.write_audit(self.web_audit())
        self.write_audit(self.web_audit(), project_dir=".project/next")
        canonical = ".project/research/DOCS-AUDIT-web.md"
        cases = (
            (("--member", "web"), "--member requires --pin"),
            (("--pin", self.pin), "--pin requires --member"),
            (("--project-dir", ".project/next"), "--project-dir requires --member"),
            (("--member", "web", "--pin", self.pin, "--project-dir", "../elsewhere"), "--project-dir must be one of"),
            (("--member", "web", "--pin", self.pin, "--project-dir", ".project/archive"), "--project-dir must be one of"),
            (("--member", "../web", "--pin", self.pin), "invalid member name"),
            (("--member", "Web", "--pin", self.pin), "invalid member name"),
            (("--member", "web", "--pin", self.pin[:7]), "--pin must be a full 40-character commit SHA"),
            (("--member", "web", "--pin", self.pin, "--audit", ".project/research/DOCS-AUDIT.md"),
             f"member web audit must be {canonical}"),
            (("--member", "web", "--pin", self.pin, "--audit", ".project/research/DOCS-AUDIT-api.md"),
             f"member web audit must be {canonical}"),
            (("--member", "web", "--pin", self.pin, "--audit", ".project/research/../research/DOCS-AUDIT-web.md"),
             f"member web audit must be {canonical}"),
            (("--member", "web", "--pin", self.pin, "--audit", str(self.coordinator / canonical)),
             f"member web audit must be {canonical}"),
            (("--member", "web", "--pin", self.pin, "--project-dir", ".project/next", "--audit", canonical),
             "member web audit must be .project/next/research/DOCS-AUDIT-web.md"),
            (("--member", "web", "--pin", self.pin, "--alignment", "--emit-inventory"),
             "member web audit does not support alignment mode"),
            (("--member", "web", "--pin", self.pin, "--emit-inventory", "--inventory", "-"),
             "--emit-inventory cannot use validation inputs"),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assert_blocked(self.run_gate(*arguments), expected)
        code, _, error = self.run_gate("--member", "web", "--pin", self.pin, "--audit", canonical)
        self.assertEqual(code, 0, error)

    @requires_symlink
    def test_member_audit_rejects_symlinked_audit_or_ancestor(self) -> None:
        elsewhere = self.root / "elsewhere"
        write(elsewhere / "DOCS-AUDIT-web.md", self.web_audit())
        research = self.coordinator / ".project" / "research"
        research.symlink_to(elsewhere, target_is_directory=True)
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin),
                            "audit ancestor must be a real directory: .project/research")
        research.unlink()
        research.mkdir()
        (research / "DOCS-AUDIT-web.md").symlink_to(elsewhere / "DOCS-AUDIT-web.md")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin),
                            "missing real audit file: .project/research/DOCS-AUDIT-web.md")
        (self.coordinator / ".project" / "next").symlink_to(self.coordinator / ".project", target_is_directory=True)
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin, "--project-dir", ".project/next"),
                            "audit ancestor must be a real directory: .project/next")

    def test_member_registry_marker_and_sidecar_identity(self) -> None:
        sidecar = self.root / "acme-inspect-sidecar"
        self.write_audit(self.web_audit(), repo=sidecar)
        self.assertFalse((self.coordinator / ".project/research/DOCS-AUDIT-web.md").exists())
        code, output, error = self.run_gate("--member", "web", "--pin", self.pin, "--primary",
                                            str(self.coordinator), repo=sidecar)
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["member"]["checkout"], str(self.web))
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin, repo=sidecar),
                            "member web is not in MEMBERS.md")
        self.assert_blocked(self.run_gate("--member", "sdk", "--pin", self.pin), "member sdk is not in MEMBERS.md")

        marker = Path(git(self.web, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "gsd-path" / "member.json"
        original = marker.read_bytes()
        foreign = {**json.loads(original.decode("utf-8")), "coordinator": str(self.root / "other")}
        marker.write_bytes(json.dumps(foreign).encode("utf-8"))
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin, "--primary",
                                          str(self.coordinator), repo=sidecar),
                            "member web is unverifiable: member marker for web is stale")
        marker.unlink()
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin, "--emit-inventory"),
                            "member web is unverifiable: member marker for web is missing")
        marker.write_bytes(original)
        git(self.web, "remote", "set-url", "origin", "https://github.com/acme/elsewhere.git")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin),
                            "member web is unverifiable: member origin changed")
        git(self.web, "remote", "set-url", "origin", "https://github.com/acme/web.git")
        git(self.web, "update-ref", "-d", "refs/remotes/origin/trunk")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin),
                            "member web is unverifiable: member requires refs/remotes/origin/trunk")
        git(self.web, "fetch", "-q", "origin")
        self.web.rename(self.root / "web-moved")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin),
                            f"member web is unverifiable: member checkout is missing: {self.web}")

    def test_member_explicit_inventory_must_equal_pinned_inventory(self) -> None:
        write(self.web / "NOTES.md", "# Untracked team notes\n")
        self.write_audit(self.web_audit())
        pinned = ["README.md", "docs/guide.md", "docs/old.md"]
        cases = (
            (pinned + ["NOTES.md"], "not in the pinned tree: NOTES.md"),
            (pinned[:-1], "missing from --inventory: docs/old.md"),
            (pinned + ["README.md"], "repeated paths"),
            ([], "missing from --inventory: README.md, docs/guide.md, docs/old.md"),
        )
        for lines, detail in cases:
            with self.subTest(lines=lines):
                outcome = self.run_gate("--member", "web", "--pin", self.pin,
                                        "--inventory", str(self.lines_file("inventory", lines)))
                self.assert_blocked(outcome, "member web pinned inventory differs from --inventory")
                self.assertIn(detail, outcome[2])

    def test_member_prior_identity_and_changed_set_are_pinned(self) -> None:
        api = self.make_member("api", "main", {"README.md": "# api\n"})
        api_pin = git(api, "rev-parse", "refs/remotes/origin/main")
        fresh = ("README.md", '"Run `make` to build"', "command", "verified", "`make` → exit 0")
        structure = ("docs/guide.md", '"Sources live in src/"', "structure", "verified", "src/app.py:1")
        stale = ("docs/old.md", '"Ships a CLI"', "feature", "stale", "no CLI entry in src/app.py")
        ruling = '| 1 | fix-doc | "drop the CLI promise" | no |'
        prior_text = self.web_audit(claims=[fresh, structure, stale], descriptive=[], rulings=[ruling])
        prior = self.root / "frozen" / "prior.md"
        write(prior, prior_text)

        moved = self.advance_origin(self.web, "trunk", "docs/new.md", "# New\n")
        write(self.web / "UNTRACKED.md", "# team only\n")
        carried = (structure[0], structure[1], structure[2], structure[3], "unchanged: " + structure[4])
        current = self.web_audit(moved, claims=[fresh, carried, stale], descriptive=["docs/new.md"], rulings=[ruling])
        self.write_audit(current)
        changed = self.lines_file("changed", ["docs/new.md"])
        code, output, error = self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior),
                                            "--changed", str(changed))
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["rulings"], 1)

        cases = (
            (["docs/new.md", "UNTRACKED.md"], "extra: UNTRACKED.md"),
            ([], "missing: docs/new.md"),
            (["docs/new.md", "src/app.py"], "extra: src/app.py"),
        )
        for lines, detail in cases:
            with self.subTest(changed=lines):
                outcome = self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior),
                                        "--changed", str(self.lines_file("changed", lines)))
                self.assert_blocked(outcome, "member web --changed does not equal the pinned prior-to-current diff")
                self.assertIn(detail, outcome[2])

        moved_code = self.advance_origin(self.web, "trunk", "src/app.py", "print(2)\n")
        self.write_audit(self.web_audit(moved_code, claims=[fresh, carried, stale], descriptive=["docs/new.md"],
                                        rulings=[ruling]))
        self.assert_blocked(self.run_gate("--member", "web", "--pin", moved_code, "--prior-audit", str(prior),
                                          "--changed", str(self.lines_file("changed", ["docs/new.md", "src/app.py"]))),
                            "changed since the prior audit: src/app.py")
        self.write_audit(current)

        identities = (
            member_audit(api, "api", "main", api_pin, descriptive=["README.md"], rulings=[ruling]),
            member_audit(self.root / "web-replacement", "web", "trunk", self.pin, rulings=[ruling]),
            prior_text.replace("Member: web\n", ""),
            prior_text.replace("Default branch: trunk", "Default branch: main"),
        )
        for text in identities:
            with self.subTest(prior=text.splitlines()[2:6]):
                write(prior, text)
                self.assert_blocked(self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior)),
                                    "prior audit belongs to a different member identity")
        write(prior, prior_text)

        self.write_audit(self.web_audit(moved, claims=[fresh, carried, stale], descriptive=["docs/new.md"]))
        self.assert_blocked(self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior),
                                          "--changed", str(changed)),
                            "User rulings must preserve every prior row")

        unrelated = self.plumbed_commit(self.web, {"README.md": ("100644", "# rewritten history\n")})
        write(prior, prior_text.replace(self.pin, unrelated))
        self.write_audit(current)
        self.assert_blocked(self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior),
                                          "--changed", str(changed)),
                            f"--changed requires prior Member HEAD {unrelated} to be a resolvable ancestor")
        self.assert_blocked(self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior)),
                            "carried (unchanged:) rows require --prior-audit and --changed")
        fresh_structure = self.web_audit(moved, claims=[fresh, structure, stale], descriptive=["docs/new.md"],
                                         rulings=[ruling, '| 2 | accept-drift | "fine" | n/a (accept-drift) |'])
        self.write_audit(fresh_structure)
        code, output, error = self.run_gate("--member", "web", "--pin", moved, "--prior-audit", str(prior))
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["rulings"], 2)

    def test_same_queue_number_is_independent_across_members(self) -> None:
        api = self.make_member("api", "main", {"README.md": "# api\n"})
        api_pin = git(api, "rev-parse", "refs/remotes/origin/main")
        web_text = self.web_audit(
            claims=[("README.md", '"Run `make` to build"', "command", "stale", "`make` → no rule")],
            rulings=['| 1 | fix-code | "add the make rule" | no |'])
        api_text = member_audit(
            api, "api", "main", api_pin,
            claims=[("README.md", '"Serves /health"', "feature", "aspirational", "no route in the tree")],
            rulings=['| 1 | accept-drift | "keep it" | n/a (accept-drift) |'])
        self.write_audit(web_text)
        self.write_audit(api_text, name="api")
        for name, pin in (("web", self.pin), ("api", api_pin)):
            with self.subTest(member=name):
                code, output, error = self.run_gate("--member", name, "--pin", pin)
                self.assertEqual(code, 0, error)
                self.assertEqual((json.loads(output)["queue"], json.loads(output)["rulings"]), (1, 1))
        api_prior = self.root / "frozen" / "api-prior.md"
        write(api_prior, api_text)
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin, "--prior-audit", str(api_prior)),
                            "prior audit belongs to a different member identity")
        self.assert_blocked(self.run_gate("--member", "api", "--pin", self.pin),
                            f"member api pin {self.pin} is not a commit in {api}")

    def test_lookahead_audit_path_is_track_scoped(self) -> None:
        active = self.write_audit(self.web_audit(rulings=['| 1 | fix-doc | "later" | no |'],
                                                 claims=[("README.md", '"Run `make` to build"', "command", "stale",
                                                          "`make` → no rule")]))
        self.assert_blocked(self.run_gate("--member", "web", "--pin", self.pin, "--project-dir", ".project/next"),
                            "missing real audit file: .project/next/research/DOCS-AUDIT-web.md")
        moved = self.advance_origin(self.web, "trunk", "docs/new.md", "# New\n")
        lookahead = self.web_audit(moved, claims=[("README.md", '"Run `make` to build"', "command", "stale",
                                                   "`make` → no rule")],
                                   descriptive=["docs/guide.md", "docs/new.md", "docs/old.md"],
                                   rulings=['| 1 | fix-doc | "later" | no |'])
        self.write_audit(lookahead, project_dir=".project/next")
        code, output, error = self.run_gate("--member", "web", "--pin", moved, "--project-dir", ".project/next",
                                            "--prior-audit", str(active))
        self.assertEqual(code, 0, error)
        result = json.loads(output)
        self.assertEqual((result["audit"], result["member"]["project_dir"]),
                         (".project/next/research/DOCS-AUDIT-web.md", ".project/next"))
        self.assert_blocked(self.run_gate("--member", "web", "--pin", moved), f"is not the member pin {moved}")

    def test_coordinator_mode_is_unchanged_beside_a_member_registry(self) -> None:
        coordinator_audit = (
            f"# Docs Audit\n\nRepo root: {self.coordinator}\nAudited: 2026-10-21\nAudited HEAD: none\n"
            "Alignment mode: no\n\n## Summary\n\n| Verdict | Count |\n|---------|-------|\n| verified | 0 |\n"
            "| stale | 0 |\n| aspirational | 0 |\n| unverifiable | 0 |\n"
            "| descriptive docs (no testable claims) | 1 |\n\n## Descriptive docs\n\n- README.md\n\n"
            "## User rulings\n\n| Queue # | Ruling | User's words | Planned |\n"
            "|---------|--------|--------------|---------|\n\n## Remediation queue\n\nNone.\n"
        )
        write(self.coordinator / ".project/research/DOCS-AUDIT.md", coordinator_audit)
        code, output, error = self.run_gate()
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output), {
            "alignment": False, "audit": ".project/research/DOCS-AUDIT.md", "claims": 0, "descriptive": ["README.md"],
            "docs": [], "queue": 0, "rulings": 0,
            "verdicts": {"aspirational": 0, "stale": 0, "unverifiable": 0, "verified": 0}})
        code, output, error = self.run_gate("--emit-inventory")
        self.assertEqual((code, output.splitlines()), (0, ["README.md"]), error)


if __name__ == "__main__":
    unittest.main()
