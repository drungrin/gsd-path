import argparse
import json
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

import tests.test_member_landing as landing
from scripts import dispatch_driver, isolation

TASK_FILE = landing.TASK_FILE
git = landing.git
AUTH = "refs/gsd-path/task-authorizations/acme-T001"
MANAGED = {
    ".claude/settings.json": {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR/.gsd-path/guard_hook.py"'}]}]}},
    ".cursor/hooks.json": {"version": 1, "hooks": {"preToolUse": [
        {"command": 'python3 ".gsd-path/guard_hook.py"', "matcher": ".*", "failClosed": True}]}},
}
# Claude reads .claude/settings.local.json on top of the project's .claude/settings.json.
SIDECAR = {".claude/settings.json": ".claude/settings.local.json", ".cursor/hooks.json": ".cursor/hooks.json"}
LOCAL = ".claude/settings.local.json"


class MemberSidecarHookTests(unittest.TestCase):
    setUp_landing = landing.MemberLandingTests.setUp
    tearDown = landing.MemberLandingTests.tearDown

    def setUp(self) -> None:
        self.setUp_landing()
        # The fixture activates T001; start over from an isolated, unactivated task.
        isolation.retire_member_task(self.coordinator, "web", "T001")
        for relative, content in MANAGED.items():
            path = self.coordinator / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(json.dumps(content).encode("utf-8"))
        git(self.coordinator, "add", "-A")
        git(self.coordinator, "commit", "-q", "-m", "install host guards")
        self.base = git(self.coordinator, "rev-parse", "HEAD")
        self.sidecar = Path(isolation.isolate_member_task(self.coordinator, "web", "T001")["worktree"])

    def activate(self):
        return isolation.activate_member_task(self.coordinator, "web", "T001", "coder", TASK_FILE, self.base)

    def guard_commands(self, relative: str) -> list:
        data = json.loads((self.sidecar / relative).read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse" if "PreToolUse" in data["hooks"] else "preToolUse"]
        return [hook["command"] for entry in entries for hook in entry.get("hooks", [entry])]

    def test_sidecar_gets_host_hooks_that_call_the_coordinator_guard(self) -> None:
        self.activate()
        guard = str(self.coordinator / ".gsd-path" / "guard_hook.py")
        for relative in MANAGED:
            commands = self.guard_commands(SIDECAR[relative])
            self.assertEqual(len(commands), 1)
            self.assertEqual(shlex.split(commands[0]), [sys.executable, guard])
        self.assertFalse((self.sidecar / ".codex" / "hooks.json").exists())
        self.assertFalse((self.sidecar / ".claude" / "settings.json").exists())
        self.assertEqual(git(self.sidecar, "status", "--porcelain", "--untracked-files=all"), "")
        isolation.retire_member_task(self.coordinator, "web", "T001")
        self.assertFalse(self.sidecar.exists())

    def track_member_file(self, relative: str, content: str) -> None:
        isolation.retire_member_task(self.coordinator, "web", "T001")
        bound = Path(isolation.member_bound_checkout(self.coordinator, "web")["checkout"])
        (bound / relative).parent.mkdir(parents=True, exist_ok=True)
        (bound / relative).write_bytes(content.encode("utf-8"))
        git(bound, "add", "-f", "-A")
        git(bound, "commit", "-q", "--no-verify", "-m", "team settings")
        self.sidecar = Path(isolation.isolate_member_task(self.coordinator, "web", "T001")["worktree"])

    def test_member_that_tracks_claude_settings_keeps_them_beside_the_guard(self) -> None:
        team = '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node team.mjs"}]}]}}'
        self.track_member_file(".claude/settings.json", team)
        self.activate()
        self.assertEqual((self.sidecar / ".claude" / "settings.json").read_text(encoding="utf-8"), team)
        self.assertEqual(shlex.split(self.guard_commands(LOCAL)[0]),
                         [sys.executable, str(self.coordinator / ".gsd-path" / "guard_hook.py")])
        self.assertEqual(git(self.sidecar, "status", "--porcelain", "--untracked-files=all"), "")

    def test_member_that_tracks_claude_local_settings_refuses_activation(self) -> None:
        self.track_member_file(LOCAL, '{"team": true}')
        with self.assertRaisesRegex(isolation.IsolationError, LOCAL):
            self.activate()
        self.assertEqual((self.sidecar / LOCAL).read_text(encoding="utf-8"), '{"team": true}')
        self.assertFalse((self.sidecar / ".gsd-path-coordinator").exists())
        self.assertEqual(git(self.member, "rev-parse", "--verify", "--quiet", AUTH, check=False), "")

    def test_untracked_local_host_config_is_never_overwritten(self) -> None:
        local = self.sidecar / ".cursor" / "hooks.json"
        local.parent.mkdir()
        local.write_bytes('{"mine": true}'.encode("utf-8"))
        exclude = Path(git(self.member, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_bytes("/.cursor/\n".encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, ".cursor/hooks.json"):
            self.activate()
        self.assertEqual(local.read_text(encoding="utf-8"), '{"mine": true}')
        self.assertEqual(git(self.member, "rev-parse", "--verify", "--quiet", AUTH, check=False), "")

    def test_plain_untracked_host_config_names_collision_before_cleanliness(self) -> None:
        local = self.sidecar / LOCAL
        local.parent.mkdir()
        local.write_bytes('{"mine": true}'.encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, LOCAL):
            self.activate()
        self.assertEqual(local.read_text(encoding="utf-8"), '{"mine": true}')
        self.assertFalse((self.sidecar / ".cursor" / "hooks.json").exists())
        self.assertFalse((self.sidecar / ".gsd-path-coordinator").exists())
        self.assertEqual(git(self.member, "rev-parse", "--verify", "--quiet", AUTH, check=False), "")

    def test_reactivation_keeps_the_generated_hooks(self) -> None:
        self.activate()
        before = (self.sidecar / LOCAL).read_bytes()
        self.activate()
        self.assertEqual((self.sidecar / LOCAL).read_bytes(), before)

    def test_member_launch_restores_removed_hook_and_refuses_changed_hook(self) -> None:
        self.activate()
        hook = self.sidecar / LOCAL
        hook.unlink()
        state = {"task_id": "T001", "wave": 1, "mode": "member", "member": "web",
                 "worktree": str(self.sidecar)}
        round_ = dispatch_driver.Round(self.coordinator, argparse.Namespace(project_dir=".project", wave=1))

        def check_hook(*_args):
            self.assertEqual(shlex.split(self.guard_commands(LOCAL)[0]),
                             [sys.executable, str(self.coordinator / ".gsd-path" / "guard_hook.py")])
            return state

        with mock.patch.object(dispatch_driver, "spawn", side_effect=check_hook) as launch:
            round_.launch(state)
            self.assertEqual(launch.call_count, 1)
            hook.write_bytes('{"mine": true}'.encode("utf-8"))
            with self.assertRaisesRegex(isolation.IsolationError, LOCAL):
                round_.launch(state)
            self.assertEqual(launch.call_count, 1)

    def test_posix_hook_quotes_coordinator_path_with_dollar_sign(self) -> None:
        coordinator = self.root / "$HOME-coordinator"
        installed = coordinator / ".claude" / "settings.json"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(json.dumps(MANAGED[".claude/settings.json"]).encode("utf-8"))
        config = json.loads(isolation._member_hook_configs(coordinator)[LOCAL])
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(shlex.split(command),
                         [sys.executable, str(coordinator / ".gsd-path" / "guard_hook.py")])

    def test_codex_hook_has_absolute_windows_guard_command(self) -> None:
        installed = self.coordinator / ".codex" / "hooks.json"
        installed.parent.mkdir(parents=True)
        installed.write_bytes('{"guard": "guard_hook.py"}'.encode("utf-8"))
        self.activate()
        config = json.loads((self.sidecar / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(shlex.split(command["command"]),
                         [sys.executable, str(self.coordinator / ".gsd-path" / "guard_hook.py")])
        self.assertEqual(shlex.split(command["commandWindows"]),
                         ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                          f"& '{sys.executable}' '{self.coordinator / '.gsd-path' / 'guard_hook.py'}'"])

    def test_non_utf8_host_config_collision_names_its_path(self) -> None:
        hook = self.sidecar / ".cursor" / "hooks.json"
        hook.parent.mkdir()
        hook.write_bytes(b"\xff")
        exclude = Path(git(self.member, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_bytes("/.cursor/\n".encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, ".cursor/hooks.json"):
            self.activate()
        self.assertEqual(hook.read_bytes(), b"\xff")
        self.assertFalse((self.sidecar / ".gsd-path-coordinator").exists())
        self.assertEqual(git(self.member, "rev-parse", "--verify", "--quiet", AUTH, check=False), "")


if __name__ == "__main__":
    unittest.main()
