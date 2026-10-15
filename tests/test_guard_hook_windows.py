"""Windows path forms the archive guard must see through."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import guard_hook
from _platform import windows_only


def msys(path: Path) -> str:
    """The Git Bash spelling of a Windows path: C:\\a\\b -> /c/a/b."""
    text = path.as_posix()
    return f"/{text[0].lower()}{text[2:]}"


@windows_only
class WindowsGuardPathTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.repo = self.base / "repo"
        (self.repo / ".project" / "archive" / "001-demo").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def test_git_bash_paths_name_their_drive(self) -> None:
        self.assertEqual(guard_hook.native_path_text("/c/Users/me"), "C:/Users/me")
        self.assertEqual(guard_hook.native_path_text("/cygdrive/d/work"), "D:/work")
        self.assertEqual(guard_hook.native_path_text("/c"), "C:/")
        self.assertEqual(guard_hook.native_path_text("/usr/bin"), "/usr/bin")
        self.assertEqual(guard_hook.native_path_text("relative/path"), "relative/path")

    def test_deleting_an_archive_ancestor_by_git_bash_path_is_denied(self) -> None:
        for target in (self.repo / ".project", self.repo):
            with self.subTest(target=target):
                command = f"rm -rf {msys(target)}"
                self.assertIsNotNone(guard_hook.command_denial(command, [str(self.base)]))

    def test_deleting_an_unrelated_git_bash_path_is_allowed(self) -> None:
        scratch = self.base / "scratch"
        scratch.mkdir()
        self.assertIsNone(guard_hook.command_denial(f"rm -rf {msys(scratch)}", [str(self.base)]))

    def test_git_in_a_git_bash_directory_is_inspected_on_its_drive(self) -> None:
        git = ["git", "-C", str(self.repo), "-c", "user.name=Guard test",
               "-c", "user.email=guard-test@example.invalid"]
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "initial"], check=True)
        for branch in ("ordinary", "feature/milestone-001"):
            subprocess.run([*git, "branch", branch], check=True)
        prefix = f"cd {msys(self.repo)} && git"
        base = [str(self.base)]
        self.assertIsNone(guard_hook.command_denial(f"{prefix} status", base))
        self.assertIsNone(guard_hook.command_denial(f"{prefix} branch -D ordinary", base))
        self.assertEqual(
            guard_hook.command_denial(f"{prefix} branch -D feature/milestone-001", base),
            guard_hook.GIT_REASONS["branch"],
        )
        # Bash rejects two cd operands and stays where it is.
        self.assertEqual(
            guard_hook.command_denial(
                f"cd {msys(self.base)} {msys(self.repo)}; git branch -D ordinary", base
            ),
            guard_hook.GIT_REASONS["unresolved-target"].format(action="git branch -D"),
        )

    def test_a_junction_is_treated_as_a_link(self) -> None:
        import _winapi

        link = self.base / "link"
        _winapi.CreateJunction(str(self.repo), str(link))
        self.assertFalse(link.is_symlink())
        self.assertTrue(guard_hook.is_link_like(link))
        self.assertFalse(guard_hook.is_link_like(self.repo))

    def test_bundled_helper_named_by_git_bash_path_is_recognized(self) -> None:
        helper = ROOT / "scripts" / "pipeline_state.py"
        command = f"python3 -B {msys(helper)} --archive .project/archive/001-demo"
        tokens = guard_hook.shell_tokens(command)
        self.assertTrue(guard_hook.bundled_helper_invocation(command, tokens, [str(self.repo)]))

    def test_non_ascii_event_is_read_as_utf8(self) -> None:
        event = {"cwd": str(self.repo), "tool_name": "Bash",
                 "tool_input": {"command": "echo café ✅"}}
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "guard_hook.py")],
            input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
            capture_output=True, cwd=self.repo,
            env={**os.environ, "PYTHONIOENCODING": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
