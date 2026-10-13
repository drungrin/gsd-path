import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

from gsd_daemon import discovery

STATE_V2 = """---
pipeline: gsd-path/v2
project: demo
milestone: null
phase: define
status: active
branch: null
archive: null
---

# Project State
"""

STATE_NO_MARKER = """---
pipeline: something/else
project: demo
phase: define
status: active
---
"""


def make_project(root: Path, state: str = STATE_V2) -> Path:
    project_dir = root / ".project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "STATE.md").write_bytes(state.encode("utf-8"))
    return root


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name) / "work"
        self.parent.mkdir()

    def scan(self, **kwargs) -> list:
        return discovery.scan([str(self.parent)], **kwargs)

    def test_finds_project_at_depth_one(self) -> None:
        project = make_project(self.parent / "alpha")
        self.assertEqual(self.scan(), [str(project)])

    def test_finds_projects_at_depth(self) -> None:
        deep = make_project(self.parent / "a" / "b" / "c" / "deep-proj")
        shallow = make_project(self.parent / "shallow")
        self.assertEqual(self.scan(), [str(deep), str(shallow)])

    def test_parent_itself_can_be_project(self) -> None:
        make_project(self.parent)
        self.assertEqual(self.scan(), [str(self.parent)])

    def test_requires_v2_marker(self) -> None:
        make_project(self.parent / "legacy", state=STATE_NO_MARKER)
        make_project(self.parent / "no-frontmatter", state="# no frontmatter\n")
        make_project(self.parent / "valid")
        self.assertEqual(self.scan(), [str(self.parent / "valid")])

    def test_max_depth(self) -> None:
        make_project(self.parent / "a" / "b" / "c" / "d" / "too-deep")
        self.assertEqual(self.scan(max_depth=4), [])
        self.assertEqual(self.scan(max_depth=5), [str(self.parent / "a" / "b" / "c" / "d" / "too-deep")])

    def test_excludes_exact_and_prefix(self) -> None:
        keep = make_project(self.parent / "keep")
        excluded_root = make_project(self.parent / "archive")
        nested = make_project(self.parent / "archive" / "nested")
        results = self.scan(excludes=[str(excluded_root)])
        self.assertEqual(results, [str(keep)])
        self.assertNotIn(str(nested), results)

    def test_no_descend_below_project_root(self) -> None:
        outer = make_project(self.parent / "outer")
        make_project(self.parent / "outer" / "inner")
        self.assertEqual(self.scan(), [str(outer)])

    def test_skips_hidden_and_vendor_dirs(self) -> None:
        make_project(self.parent / ".hidden" / "proj")
        make_project(self.parent / "pkg" / "node_modules" / "proj")
        make_project(self.parent / "env" / ".venv" / "proj")
        make_project(self.parent / "src" / "__pycache__" / "proj")
        make_project(self.parent / "repo" / ".git" / "proj")
        visible = make_project(self.parent / "visible")
        self.assertEqual(self.scan(), [str(visible)])

    def test_missing_and_excluded_parents_ignored(self) -> None:
        results = discovery.scan(
            [str(self.parent / "does-not-exist"), str(self.parent)],
            excludes=[str(self.parent)],
        )
        self.assertEqual(results, [])

    def test_state_md_without_project_dir_is_not_project(self) -> None:
        stray = self.parent / "stray"
        stray.mkdir()
        (stray / "STATE.md").write_bytes(STATE_V2.encode("utf-8"))
        self.assertEqual(self.scan(), [])

    def test_lists_installed_project_with_no_state_file(self) -> None:
        idle = self.parent / "idle"
        (idle / ".gsd-path").mkdir(parents=True)
        (idle / ".gsd-path" / "status_runtime.py").write_bytes(b"")
        (self.parent / "plain").mkdir()
        self.assertEqual(self.scan(), [str(idle)])

    def test_installed_folder_does_not_hide_project_below_it(self) -> None:
        (self.parent / ".gsd-path").mkdir()
        (self.parent / ".gsd-path" / "status_runtime.py").write_bytes(b"")
        project = make_project(self.parent / "alpha")
        self.assertEqual(self.scan(), [str(project)])


class WorktreeDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name) / "work"
        self.parent.mkdir()

    def _git(self, *args: str, cwd: Path) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
        )

    def make_repo_with_worktree(self, nested: bool = False) -> tuple:
        main = self.parent / "repo"
        main.mkdir()
        self._git("init", cwd=main)
        make_project(main / "app" if nested else main)
        self._git("add", "-A", cwd=main)
        self._git("commit", "-m", "init", cwd=main)
        linked = self.parent / "repo-linked"
        self._git("worktree", "add", str(linked), cwd=main)
        return main, linked

    def test_status_keeps_worktree_and_main_project_identity(self):
        from gsd_daemon.probe import probe_project
        from gsd_daemon.model import ProjectStatus
        main, linked = self.make_repo_with_worktree()
        data = probe_project(linked, enrich=False).to_dict()
        self.assertEqual(Path(data["project_root"]).resolve(), main.resolve())
        self.assertEqual(Path(data["worktree_root"]).resolve(), linked.resolve())
        self.assertEqual(data.get("repository"), main.name)
        self.assertEqual(data["root"], str(linked))
        self.assertEqual(data["project"], "demo")
        self.assertEqual(ProjectStatus.from_dict(data).to_dict(), data)
        main_data = probe_project(main, enrich=False).to_dict()
        self.assertEqual(Path(main_data["project_root"]).resolve(), main.resolve())
        self.assertIsNone(main_data["worktree_root"])

    def test_nested_project_uses_project_folder_and_checkout_worktree(self):
        from gsd_daemon.probe import probe_project
        main, linked = self.make_repo_with_worktree(nested=True)
        main_data = probe_project(main / "app", enrich=False).to_dict()
        self.assertEqual(main_data["root"], str(main / "app"))
        self.assertEqual(Path(main_data["project_root"]).resolve(), (main / "app").resolve())
        self.assertIsNone(main_data["worktree_root"])
        self.assertEqual(main_data["repository"], main.name)
        linked_data = probe_project(linked / "app", enrich=False).to_dict()
        self.assertEqual(linked_data["root"], str(linked / "app"))
        self.assertEqual(Path(linked_data["project_root"]).resolve(), (main / "app").resolve())
        self.assertEqual(Path(linked_data["worktree_root"]).resolve(), linked.resolve())
        self.assertEqual(linked_data["repository"], main.name)
        self._git("checkout", "--detach", cwd=linked)
        detached_data = probe_project(linked / "app", enrich=False).to_dict()
        self.assertEqual(Path(detached_data["project_root"]).resolve(), (main / "app").resolve())
        self.assertEqual(Path(detached_data["worktree_root"]).resolve(), linked.resolve())
        self.assertEqual(detached_data["git"]["branch"], "HEAD")

    def test_non_git_project_has_no_invented_repository(self):
        from gsd_daemon.probe import probe_project
        root = make_project(self.parent / "plain")
        data = probe_project(root, enrich=False).to_dict()
        self.assertIsNone(data.get("repository"))
        self.assertEqual(data.get("project_root"), str(root))
        self.assertIsNone(data["worktree_root"])

    def test_bare_backed_linked_project_has_no_main_checkout(self):
        from gsd_daemon.gitinfo import project_identity
        seed = self.parent / "seed"
        seed.mkdir()
        self._git("init", cwd=seed)
        project = seed / "app"
        project.mkdir()
        (project / "README.md").write_text("project\n")
        self._git("add", "-A", cwd=seed)
        self._git("commit", "-m", "init", cwd=seed)
        bare = self.parent / "repository.git"
        self._git("clone", "--bare", str(seed), str(bare), cwd=self.parent)
        linked = self.parent / "preview"
        self._git("worktree", "add", "--detach", str(linked), cwd=bare)
        identity = project_identity(linked / "app")
        self.assertEqual(identity["project_root"], str(linked / "app"))
        self.assertIsNotNone(identity["worktree_root"])
        self.assertEqual(Path(identity["worktree_root"]).resolve(), linked.resolve())
        self.assertEqual(identity["repository"], bare.name)

    def test_linked_worktree_deduped_to_main_checkout(self) -> None:
        main, linked = self.make_repo_with_worktree()
        self.assertTrue((main / ".git").is_dir())
        self.assertTrue((linked / ".git").is_file())
        self.assertEqual(discovery.scan([str(self.parent)]), [str(main)])

    def test_unrelated_projects_not_deduped(self) -> None:
        main, _linked = self.make_repo_with_worktree()
        other = make_project(self.parent / "other")
        results = discovery.scan([str(self.parent)])
        self.assertEqual(results, [str(other), str(main)])

    def test_managed_sidecar_resolves_primary_outside_watched_root(self) -> None:
        main, primary = self.make_repo_with_worktree()
        self._git("branch", "-m", "gsd-path/M001", cwd=primary)
        managed = self.parent / ".gsd-path" / "projects"
        sidecar = managed / "repository" / "workspace" / "verify" / "review"
        self._git("worktree", "add", "-b", "gsd-path-verify/review", str(sidecar), cwd=main)
        self.assertEqual([Path(p).resolve() for p in discovery.scan([str(managed)])], [primary.resolve()])
        self.assertEqual([Path(p).resolve() for p in discovery.scan([str(self.parent), str(managed)])], [primary.resolve()])
        self.assertNotIn(primary.resolve(), [Path(p).resolve() for p in discovery.scan([str(managed)], excludes=[str(primary)])])

    def test_git_failure_treats_each_root_as_own_group(self) -> None:
        main, linked = self.make_repo_with_worktree()
        with mock.patch("subprocess.run", side_effect=OSError("git missing")):
            results = discovery.scan([str(self.parent)])
        self.assertEqual(results, [str(main), str(linked)])


if __name__ == "__main__":
    unittest.main()
