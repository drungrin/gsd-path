"""Fork release announcement, boundary upgrade window, and consumer upgrade."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import upgrade_window

SOURCE = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40


def load_consumer_upgrade():
    spec = importlib.util.spec_from_file_location(
        "consumer_upgrade", SOURCE / "scripts" / "dev" / "consumer_upgrade.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          encoding="utf-8", errors="replace", check=True).stdout.strip()


def state_text(phase="inspect", status="active"):
    return ("---\npipeline: gsd-path/v2\nproject: demo\nmilestone: null     # set by define\n"
            f"phase: {phase}       # v2 tokens\nstatus: {status}      # active | done\n"
            "branch: null\narchive: null\n---\n\n# Project State\n")


class UpgradeWindowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / "project"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "init")
        (self.repo / ".project").mkdir()
        (self.repo / ".project" / "STATE.md").write_text(state_text(), encoding="utf-8")
        self.skill = self.repo / ".claude" / "skills" / "gsd-path"
        self.skill.mkdir(parents=True)
        self.install("1.7.1.47", "1.7.1.47")

    def install(self, skills, runtime):
        (self.skill / "VERSION").write_text(skills + "\n", encoding="utf-8")
        (self.repo / ".gsd-path").mkdir(exist_ok=True)
        (self.repo / ".gsd-path" / "runtime.json").write_text(
            json.dumps({"version": runtime}), encoding="utf-8")

    def announce(self, version="1.7.1.48", **changes):
        release = {"schema": upgrade_window.RELEASE_SCHEMA, "version": version, "commit": COMMIT,
                   "checkout": str(self.root / "fork"), **changes}
        path = self.home / ".gsd-path" / "fork-release.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(release), encoding="utf-8")

    def check(self):
        return upgrade_window.check(self.repo, self.skill)

    def test_without_announcement_routes_as_usual(self):
        self.assertEqual(self.check()["status"], "none")

    def test_newer_release_at_boundary_is_available_with_command(self):
        self.announce()
        report = self.check()
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["behind"], ["skills", "runtime"])
        self.assertEqual(report["release"], {"version": "1.7.1.48", "commit": COMMIT})
        self.assertEqual(report["command"], f"python3 {self.root / 'fork' / 'scripts' / 'dev' / 'consumer_upgrade.py'}"
                                            f" apply --project {self.repo}")

    def test_installed_release_is_current(self):
        self.announce("1.7.1.47")
        self.assertEqual(self.check()["status"], "current")

    def test_a_stale_runtime_alone_is_behind(self):
        self.install("1.7.1.48", "1.7.1.40")
        self.announce()
        report = self.check()
        self.assertEqual((report["status"], report["behind"]), ("available", ["runtime"]))

    def test_upstream_three_part_version_compares_below_fork_revision(self):
        self.install("1.7.1", "1.7.1")
        self.announce("1.7.1.1")
        self.assertEqual(self.check()["status"], "available")

    def test_phase_outside_the_window_is_closed(self):
        self.announce()
        for phase, status in (("plan", "active"), ("build", "active"), ("inspect", "blocked"), ("shipped", "done")):
            (self.repo / ".project" / "STATE.md").write_text(state_text(phase, status), encoding="utf-8")
            self.assertEqual(self.check()["status"], "closed", phase)

    def test_started_phase_work_closes_the_window(self):
        self.announce()
        git(self.repo, "worktree", "add", "-q", "--detach", str(self.root / "sidecar"))
        report = self.check()
        self.assertEqual(report["status"], "closed")
        self.assertIn(str(self.root / "sidecar"), report["reason"])

    def test_malformed_announcement_is_an_error_not_a_stop(self):
        self.announce(commit="not-a-sha")
        self.assertEqual(self.check()["status"], "error")

    def test_cli_always_exits_zero(self):
        self.announce()
        (self.repo / ".project" / "STATE.md").unlink()
        result = subprocess.run([sys.executable, "-B", str(SOURCE / "scripts" / "upgrade_window.py"),
                                 "--repo", str(self.repo), "--skill-dir", str(self.skill)],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "error")


@unittest.skipUnless(shutil.which("node") and (SOURCE / ".git").exists(), "needs node and a Git checkout")
class ConsumerUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        cls.home = cls.root / "home"
        cls.home.mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.home), "USERPROFILE": str(cls.home)})
        environment.start()
        cls.addClassCleanup(environment.stop)
        cls.fork = cls.root / "fork"
        subprocess.run(["git", "clone", "-q", "--shared", str(SOURCE), str(cls.fork)], check=True)
        git(cls.fork, "config", "user.name", "Fork test")
        git(cls.fork, "config", "user.email", "fork@example.invalid")
        cls.template = cls.root / "consumer"
        cls.template.mkdir()
        git(cls.template, "init", "-q", "-b", "main")
        git(cls.template, "config", "user.name", "Consumer test")
        git(cls.template, "config", "user.email", "consumer@example.invalid")
        (cls.template / "README.md").write_text("demo\n", encoding="utf-8")
        subprocess.run(["node", str(cls.fork / "scripts" / "install.mjs"), "--claude", "--claude-root",
                        str(cls.template / ".claude" / "skills"), "--project", str(cls.template)],
                       capture_output=True, check=True)
        git(cls.template, "add", "-A")
        git(cls.template, "commit", "-q", "-m", "chore: install gsd-path")
        (cls.template / ".project").mkdir()
        (cls.template / ".project" / "STATE.md").write_text(state_text(), encoding="utf-8")
        package = cls.fork / "package.json"
        manifest = json.loads(package.read_text(encoding="utf-8"))
        parts = manifest["version"].split(".")
        cls.version = ".".join(parts[:3] + [str(int(parts[3]) + 1 if len(parts) == 4 else 1)])
        package.write_text(package.read_text(encoding="utf-8").replace(
            f'"version": "{manifest["version"]}"', f'"version": "{cls.version}"', 1), encoding="utf-8")
        git(cls.fork, "commit", "-q", "-am", f"chore(fork): release {cls.version}")
        cls.commit = git(cls.fork, "rev-parse", "HEAD")
        cls.module = load_consumer_upgrade()
        cls.module.ROOT = cls.fork

    def setUp(self):
        self.project = self.root / f"project-{self._testMethodName}"
        shutil.copytree(self.template, self.project, symlinks=True)
        self.addCleanup(shutil.rmtree, self.project, True)
        with mock.patch("sys.stdout"):
            self.assertEqual(self.module.main(["announce"]), 0)

    def apply(self, *extra):
        with mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr:
            code = self.module.main(["apply", "--project", str(self.project), *extra])
        written = "".join(call.args[0] for call in stdout.write.call_args_list)
        errors = "".join(call.args[0] for call in stderr.write.call_args_list)
        return code, (json.loads(written) if written else None), errors

    def backups(self):
        return sorted((self.home / ".gsd-path" / "preserved" / self.project.name).glob("upgrade-*"))

    def test_announce_records_the_release_commit(self):
        release = json.loads((self.home / ".gsd-path" / "fork-release.json").read_text(encoding="utf-8"))
        self.assertEqual((release["version"], release["commit"], release["checkout"]),
                         (self.version, self.commit, str(self.fork)))

    def test_announce_refuses_a_dirty_checkout(self):
        (self.fork / "scratch.txt").write_text("x\n", encoding="utf-8")
        self.addCleanup((self.fork / "scratch.txt").unlink)
        with mock.patch("sys.stderr"):
            self.assertEqual(self.module.main(["announce", "--dry-run"]), 1)

    def test_apply_refuses_outside_the_window_without_changes(self):
        (self.project / ".project" / "STATE.md").write_text(state_text("build"), encoding="utf-8")
        head = git(self.project, "rev-parse", "HEAD")
        code, _, errors = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("build/active", errors)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), head)
        self.assertEqual(self.backups(), [])

    def test_apply_refuses_dirty_paths_outside_project_state(self):
        (self.project / "AGENTS.md").write_text("owner edit\n", encoding="utf-8")
        code, _, errors = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("AGENTS.md", errors)
        self.assertEqual(self.backups(), [])

    def test_apply_upgrades_from_the_announced_commit_and_commits_installation_paths(self):
        unreleased = self.fork / "skills" / "gsd-path" / "SKILL.md"
        unreleased.write_text(unreleased.read_text(encoding="utf-8") + "\nUNRELEASED WORK\n", encoding="utf-8")
        self.addCleanup(git, self.fork, "checkout", "--", "skills/gsd-path/SKILL.md")
        code, report, errors = self.apply()
        self.assertEqual(code, 0, errors)
        self.assertEqual(report["status"], "upgraded")
        self.assertEqual(git(self.project, "log", "-1", "--format=%s"),
                         f"chore(gsd-path): upgrade runtime and skills to {self.version}")
        for skill in ("gsd-path", "path"):
            self.assertEqual((self.project / ".claude" / "skills" / skill / "VERSION")
                             .read_text(encoding="utf-8").strip(), self.version)
        self.assertNotIn("UNRELEASED WORK", (self.project / ".claude" / "skills" / "gsd-path" / "SKILL.md")
                         .read_text(encoding="utf-8"))
        pin = json.loads((self.project / ".gsd-path" / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual((pin["version"], pin["provenance"]["patch_ref"]), (self.version, self.commit))
        committed = git(self.project, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertTrue(committed)
        self.assertFalse([path for path in committed if path.startswith(".project/")])
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=all"),
                         "?? .project/STATE.md")
        [backup] = self.backups()
        self.assertTrue((backup / "install-paths.tar").is_file())
        self.assertFalse((self.project / ".claude" / "disabled-gsd-skills").exists())
        window = upgrade_window.check(self.project, self.project / ".claude" / "skills" / "gsd-path")
        self.assertEqual(window["status"], "current")
        code, report, errors = self.apply()
        self.assertEqual((code, report["status"]), (0, "current"), errors)


if __name__ == "__main__":
    unittest.main()
