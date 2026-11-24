"""CLI review-panel members (fork only): configuration, confinement, receipts, and failures."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from scripts import panel_cli
from tests.test_pipeline_state import state_text

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/panel_cli.py"
CONFIG = {"gpt": {"profile": "codex", "model": "gpt-6-astra", "effort": "high"},
          "muse": {"profile": "muse", "model": "muse-wonder-1.0"},
          "gemini": {"profile": "agy", "model": "gemini-4-neon-high"}}

# Runs the command after `--`, in the directory --chdir names: enough to test the wrapper without
# a mount namespace.
FAKE_BWRAP = textwrap.dedent(
    """
    import os, sys
    args = sys.argv[1:]
    if "--chdir" in args:
        os.chdir(args[args.index("--chdir") + 1])
    command = args[args.index("--") + 1:] if "--" in args else ["true"]
    os.execvp(command[0], command)
    """
)

# A Codex stand-in: --version, then `exec ... -o <raw> --json -` with the brief on stdin. The file
# $HOME/fake-codex picks the behaviour (the member environment passes no test variables): ok,
# wrong-nonce, no-inspected, login, empty.
FAKE_CODEX = textwrap.dedent(
    """
    import json, os, re, sys
    from pathlib import Path
    if sys.argv[1:] == ["--version"]:
        print("codex-cli " + os.environ.get("FAKE_CODEX_VERSION", "0.166.0"))
        sys.exit(0)
    mode = (Path.home() / "fake-codex").read_text().strip()
    brief = sys.stdin.read()
    raw = Path(sys.argv[sys.argv.index("-o") + 1])
    if mode == "login":
        print(json.dumps({"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized"}}))
        sys.exit(1)
    nonce_file = re.search(r"read the file (\\S+)\\. This section", brief).group(1)
    nonce = Path(nonce_file).read_text().strip()
    if mode == "wrong-nonce":
        nonce = "0" * 24
    inspected = "Inspected:\\n- missing/file.py\\n" if mode == "no-inspected" else "Inspected:\\n- src/app.py\\n"
    report = ("# Panel — wave 1, cycle 1\\n\\n- Family: gpt\\n- Model: gpt-6-astra\\n- Depth: full\\n\\n"
              "## Findings\\n\\n- none\\n\\n## Summary\\n\\nNo findings.\\n\\n")
    raw.write_text("" if mode == "empty" else report + inspected + f"Access receipt: {nonce}\\n")
    print(json.dumps({"type": "turn.completed"}))
    """
)


class ConfigTests(unittest.TestCase):
    def test_accepts_one_profile_per_family_and_refuses_mismatches(self) -> None:
        self.assertEqual(panel_cli.validate_config(CONFIG), CONFIG)
        for bad, message in (
                ({"gpt": {"profile": "muse", "model": "gpt-6-astra"}}, "serves the muse family"),
                ({"gpt": {"profile": "codex", "model": "muse-wonder-1.0"}}, "is not a gpt model"),
                ({"gpt": {"profile": "codex"}}, "model is required"),
                ({"gpt": {"profile": "codex", "model": "gpt-6-astra", "extra": "x"}}, "allows only"),
                ({"unknown": {"profile": "codex", "model": "gpt-6-astra"}}, "not a known panel family"),
                ({"gpt": {"profile": "other", "model": "gpt-6-astra"}}, "profile must be one of")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                panel_cli.validate_config(bad)

    def test_partial_entries_pass_only_the_partial_check(self) -> None:
        self.assertEqual(panel_cli.validate_config({"gpt": {"profile": "codex"}}, partial=True),
                         {"gpt": {"profile": "codex"}})
        with self.assertRaisesRegex(ValueError, "profile must be one of"):
            panel_cli.validate_config({"gpt": {"profile": "nope"}}, partial=True)


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        (self.repo / "src").mkdir()
        (self.repo / "src/app.py").write_text("x = 1\n")
        self.report = "# Panel — wave 1, cycle 1\n\n## Findings\n\n- none\n"

    def test_strips_the_receipt_and_keeps_the_report_exactly(self) -> None:
        text = self.report + "\nInspected:\n- src/app.py\n- `/etc/passwd`\nAccess receipt: abc\n"
        report, inspected = panel_cli.split_receipt(text, "abc", self.repo)
        self.assertEqual(report, self.report)
        self.assertEqual(inspected, ["src/app.py"])

    def test_refuses_a_missing_wrong_or_unproven_receipt(self) -> None:
        for text, nonce, code in (
                (self.report, "abc", "no-access-receipt"),
                (self.report + "Inspected:\n- src/app.py\nAccess receipt: other\n", "abc", "no-access-receipt"),
                (self.report + "Access receipt: abc\n", "abc", "no-access-receipt"),
                (self.report + "Inspected:\n- ../outside.py\n- missing.py\nAccess receipt: abc\n", "abc",
                 "no-access-receipt"),
                ("Report\nInspected:\n- src/app.py\nAccess receipt: abc\n", "abc", "malformed-report")):
            with self.subTest(text=text[-40:]):
                with self.assertRaises(panel_cli.PanelCliError) as caught:
                    panel_cli.split_receipt(text, nonce, self.repo)
                self.assertEqual(caught.exception.code, code)


class ClassifyTests(unittest.TestCase):
    def code(self, *args) -> str:
        with self.assertRaises(panel_cli.PanelCliError) as caught:
            panel_cli.classify(*args)
        return caught.exception.code

    def test_names_each_failure(self) -> None:
        self.assertEqual(self.code("codex", 1, "", '{"type":"turn.failed","error":"401 Unauthorized"}'), "login")
        self.assertEqual(self.code("muse", 1, "", "missing meta credentials: run `muse login`"), "login")
        self.assertEqual(self.code("agy", 1, "", "Error: authentication timed out."), "login")
        self.assertEqual(self.code("muse", 1, "", "You've hit your weekly limit"), "quota")
        self.assertEqual(self.code("muse", 2, "", "boom"), "exit-nonzero")
        self.assertEqual(self.code("agy", 0, "", "a tool required the read_file permission that headless mode "
                                                 "cannot prompt for, so it was auto-denied"), "tool-denied")
        self.assertEqual(self.code("codex", 0, "  \n", ""), "empty-output")
        self.assertEqual(self.code("muse", 0, "You've hit your weekly limit.", ""), "quota")
        panel_cli.classify("codex", 0, "# Panel — wave 1, cycle 1\n" + "x" * 700 + " quota", "")

    def test_reasons_prefer_error_lines_over_warnings(self) -> None:
        stderr = ("muse: warning: rules file skipped\nmuse: workspace trust: untrusted\n"
                  "agent loop failed: model `muse-x` does not exist or you lack access\nmuse: bye\n")
        self.assertEqual(panel_cli.masked_tail(stderr),
                         "agent loop failed: model `muse-x` does not exist or you lack access")
        self.assertEqual(panel_cli.masked_tail("one\ntwo\n"), "one | two")

    def test_codex_may_write_only_a_wave_sidecar(self) -> None:
        wave = panel_cli.cli_argv("codex", "gpt-6-astra", None, Path("/w"), Path("/b"), "x", Path("/r"), True)
        plan = panel_cli.cli_argv("codex", "gpt-6-astra", None, Path("/w"), Path("/b"), "x", Path("/r"), False)
        self.assertEqual(wave[wave.index("-s") + 1], "workspace-write")
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", wave)
        self.assertEqual(plan[plan.index("-s") + 1], "read-only")
        muse = panel_cli.cli_argv("muse", "muse-wonder-1.0", "high", Path("/w"), Path("/b"), "x", Path("/r"), True)
        self.assertNotIn("--yolo", muse)

    def test_masks_secrets_in_reasons(self) -> None:
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", panel_cli.masked_tail("token sk-abcdefghijklmnopqrstuvwxyz"))


class EnvironmentTests(unittest.TestCase):
    def test_refuses_outside_a_sandbox(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(panel_cli, "Path") as fake_path:
            fake_path.return_value.exists.return_value = False
            with self.assertRaises(panel_cli.PanelCliError) as caught:
                panel_cli.require_sandbox()
        self.assertEqual(caught.exception.code, "not-sandboxed")

    def test_confines_everything_but_scratch_state_and_the_wave_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            (home / ".config/muse").mkdir(parents=True)
            (home / ".claude").mkdir()
            (home / ".claude/.credentials.json").write_text("{}")
            with patch.object(panel_cli.Path, "home", return_value=home):
                argv = panel_cli.confine(["codex", "exec"], "codex", home / "scratch", home / "sidecar", True)
                plan = panel_cli.confine(["codex", "exec"], "codex", home / "scratch", home / "repo", False)
        joined = " ".join(argv)
        self.assertEqual(argv[:4], ["bwrap", "--ro-bind", "/", "/"])
        self.assertIn(f"--bind {home / '.codex'} {home / '.codex'}", joined)
        self.assertIn(f"--tmpfs {home / '.config/muse'}", joined)
        self.assertIn(f"--ro-bind /dev/null {home / '.claude/.credentials.json'}", joined)
        self.assertIn(f"--bind {home / 'sidecar'} {home / 'sidecar'}", joined)
        self.assertNotIn(f"--bind {home / 'repo'}", " ".join(plan))
        self.assertEqual(argv[-3:], ["--", "codex", "exec"])

    def test_member_environment_passes_no_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, \
                patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "x", "OPENAI_API_KEY": "y", "HTTPS_PROXY": "p"}):
            env = panel_cli.member_env("muse", Path(scratch))
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["HTTPS_PROXY"], "p")
        self.assertEqual(env["MUSE_NO_AUTO_UPDATE"], "1")


class MemberRunTests(unittest.TestCase):
    """`run` end to end with a pass-through bwrap and a Codex stand-in."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name, source in (("bwrap", FAKE_BWRAP), ("codex", FAKE_CODEX)):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n{source}")
            path.chmod(0o755)
        self.repo = self.base / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src/app.py").write_text("x = 1\n")
        self.home = self.base / "home"
        self.home.mkdir()

    def run_member(self, mode: str, version: str = "0.166.0", manifest: Optional[dict] = None) -> subprocess.CompletedProcess:
        final = self.base / "attempt/final.md"
        final.parent.mkdir(exist_ok=True)
        (self.home / "fake-codex").write_text(mode)
        if final.exists():
            final.unlink()
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", HOME=str(self.home),
                   SANDBOX_NAME="test-sandbox", FAKE_CODEX_VERSION=version)
        env.pop(panel_cli.MANIFEST_ENV, None)
        if manifest is not None:
            path = self.base / "manifest.json"
            path.write_text(json.dumps(manifest))
            env[panel_cli.MANIFEST_ENV] = str(path)
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "run", "--profile", "codex", "--model", "gpt-6-astra",
                               "--repo", str(self.repo), "--final", str(final)],
                              input="Brief: review wave 1.\n", capture_output=True, encoding="utf-8",
                              cwd=self.repo, env=env)

    def test_writes_the_checked_report_and_a_receipt(self) -> None:
        completed = self.run_member("ok")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        final = (self.base / "attempt/final.md").read_text()
        self.assertTrue(final.startswith("# Panel — wave 1, cycle 1"))
        self.assertNotIn("Access receipt", final)
        receipt = json.loads((self.base / "attempt/panel-cli/receipt.json").read_text())
        self.assertEqual(receipt["inspected"], ["src/app.py"])
        self.assertEqual(receipt["version"], "0.166.0")
        brief = (self.base / "attempt/panel-cli/brief.md").read_text()
        self.assertIn("Brief: review wave 1.", brief)
        self.assertIn("## Access receipt (required)", brief)

    def test_runs_with_the_plugin_manifest_and_refuses_a_cli_it_does_not_list(self) -> None:
        source = self.base / "cli-auth/codex/auth.json"
        source.parent.mkdir(parents=True)
        source.write_text("{}")
        manifest = {"version": 1, "scope": "sandbox", "fix_hint": "Turn it on in the plugin.",
                    "clis": {"codex": {"version": "0.166.0", "state": [str(self.home / ".codex")],
                                       "credentials": [{"path": str(self.home / ".codex/auth.json"),
                                                        "source": str(source)}]}},
                    "read_roots": [], "credential_roots": [str(self.base / "cli-auth")]}
        completed = self.run_member("ok", manifest=manifest)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        command = json.loads((self.base / "attempt/panel-cli/command.json").read_text())
        self.assertIn(f"--bind {source} {self.home / '.codex/auth.json'}", " ".join(command))
        missing = self.run_member("ok", manifest={**manifest, "clis": {}})
        self.assertEqual(missing.returncode, panel_cli.FAILURE_EXIT)
        self.assertIn("panel-cli: cli-missing: codex is not enabled in this sandbox. Turn it on in the plugin.",
                      missing.stderr)

    def test_any_cli_version_runs_and_is_recorded(self) -> None:
        completed = self.run_member("ok", "0.199.0")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads((self.base / "attempt/panel-cli/receipt.json").read_text())
        self.assertEqual(receipt["version"], "0.199.0")

    def test_blocks_with_a_coded_last_stderr_line(self) -> None:
        for mode, version, code in (("login", "0.166.0", "login"), ("wrong-nonce", "0.166.0", "no-access-receipt"),
                                    ("no-inspected", "0.166.0", "no-access-receipt"),
                                    ("empty", "0.166.0", "empty-output")):
            with self.subTest(mode=mode, version=version):
                completed = self.run_member(mode, version)
                self.assertEqual(completed.returncode, panel_cli.FAILURE_EXIT, completed.stderr)
                last = completed.stderr.strip().splitlines()[-1]
                self.assertTrue(last.startswith(f"panel-cli: {code}: "), last)
                self.assertFalse((self.base / "attempt/final.md").exists())


class CommandsAndSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "feature/milestone-001", str(self.repo)], check=True, capture_output=True)
        (self.repo / ".project").mkdir()
        (self.repo / ".project/STATE.md").write_bytes(
            state_text(phase="define", status="active", branch="feature/milestone-001").encode("utf-8"))
        env = patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def config(self, *args: str, ok: bool = True) -> dict:
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/path_config.py"), *args,
                                    "--repo", str(self.repo)], capture_output=True, encoding="utf-8")
        self.assertEqual(completed.returncode == 0, ok, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def test_settings_are_written_field_by_field_and_the_project_wins(self) -> None:
        self.config("set", "review_panel_cli.gpt.profile", "codex", "--scope", "user")
        self.config("set", "review_panel_cli.gpt.model", "gpt-6-astra", "--scope", "user")
        refused = self.config("set", "review_panel_cli.muse.model", "gpt-6-astra", "--scope", "user", ok=False)
        self.assertIn("is not a muse model", refused["error"])
        shown = self.config("show", "--scope", "user")
        self.assertEqual(shown["settings"]["review_panel_cli"]["value"],
                         {"gpt": {"profile": "codex", "model": "gpt-6-astra"}})
        self.assertEqual(panel_cli.load_config(self.repo), {"gpt": {"profile": "codex", "model": "gpt-6-astra"}})
        (self.repo / ".project/config.json").write_text(json.dumps({"review_panel_cli": {
            "muse": {"profile": "muse", "model": "muse-wonder-1.0"}}}))
        self.assertEqual(panel_cli.load_config(self.repo), {"muse": {"profile": "muse", "model": "muse-wonder-1.0"}})
        self.config("reset", "review_panel_cli.gpt", "--scope", "user")
        self.assertNotIn("review_panel_cli", json.loads((self.home / ".gsd-path/config.json").read_text()))

    def test_commands_name_model_and_final_slots_and_a_writable_wave_sidecar(self) -> None:
        (self.repo / ".project/config.json").write_text(json.dumps({"review_panel_cli": CONFIG}))
        output = self.home / "commands.json"
        completed = subprocess.run([sys.executable, "-B", str(SCRIPT), "commands", "--repo", str(self.repo),
                                    "--kind", "wave", "--output", str(output)], capture_output=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["families"], ["gemini", "gpt", "muse"])
        self.assertEqual(result["advertised"], "gemini-4-neon-high,gpt-6-astra,muse-wonder-1.0")
        written = json.loads(output.read_text())
        self.assertEqual(set(written), {"gemini", "gpt", "muse"})
        for spec in written.values():
            self.assertEqual(spec["output"], "file")
            self.assertIn("--model {model}", spec["command"])
            self.assertIn("--final {final}", spec["command"])
            self.assertIn("--writable-workdir", spec["command"])
        plan = panel_cli.family_commands(self.repo, "plan")["family_commands"]
        self.assertNotIn("--writable-workdir", plan["gpt"]["command"])
        self.assertIn("--effort high", plan["gpt"]["command"])


def manifest_layout(base: Path) -> dict:
    """A plugin-style layout: per-CLI state in the home, credentials under hidden roots."""
    home, roots = base / "home", base / "roots"
    for path in (home / ".codex", home / ".config/muse", home / ".local/share/muse", home / ".gemini",
                 roots / "cli-auth/codex", roots / "cli-auth/muse", roots / "claude-sbx/cli-bin"):
        path.mkdir(parents=True, exist_ok=True)
    (roots / "cli-auth/codex/auth.json").write_text("codex-secret\n")
    (roots / "cli-auth/muse/auth.json").write_text("muse-token\n")
    (roots / "claude-sbx/.credentials.json").write_text("claude-secret\n")
    (roots / "claude-sbx/cli-bin/shim").write_text("shim\n")
    return {
        "version": 1, "sandbox": "sbx-test", "scope": "global",
        "clis": {
            "codex": {"version": "0.166.0", "state": [str(home / ".codex")],
                      "credentials": [{"path": str(home / ".codex/auth.json"),
                                       "source": str(roots / "cli-auth/codex/auth.json")}],
                      "login": "codex login --device-auth"},
            "muse": {"version": "1.4.25-R9361.1", "state": [str(home / ".config/muse"), str(home / ".local/share/muse")],
                     "credentials": [{"path": str(home / ".config/muse/auth.json"),
                                      "source": str(roots / "cli-auth/muse/auth.json")}],
                     "login": "muse login"},
        },
        "read_roots": [str(roots / "claude-sbx/cli-bin")],
        "credential_roots": [str(roots / "cli-auth"), str(roots / "claude-sbx")],
        "fix_hint": "Turn the CLI on in the plugin.",
    }


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.manifest = manifest_layout(self.base)
        self.home = self.base / "home"
        self.repo = self.base / "repo"
        self.repo.mkdir()
        home = patch.object(panel_cli.Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)

    def write(self, data: dict) -> Path:
        path = self.base / "manifest.json"
        path.write_text(json.dumps(data))
        return path

    def load(self, data: dict) -> dict:
        with patch.dict(os.environ, {panel_cli.MANIFEST_ENV: str(self.write(data))}):
            return panel_cli.load_manifest()

    def code(self, call) -> str:
        with self.assertRaises(panel_cli.PanelCliError) as caught:
            call()
        return caught.exception.code

    def test_absent_manifest_keeps_the_current_behaviour(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(panel_cli.load_manifest())

    def test_refuses_an_unknown_version_or_malformed_paths(self) -> None:
        self.assertEqual(self.load(self.manifest)["scope"], "global")
        for change in ({"version": 2}, {"version": True}, {"credential_roots": ["relative/path"]},
                       {"clis": {"codex": {"state": []}}}, {"read_roots": "x"}):
            with self.subTest(change=change):
                self.assertEqual(self.code(lambda: self.load({**self.manifest, **change})), "manifest")
        broken = self.base / "broken.json"
        broken.write_text("{")
        with patch.dict(os.environ, {panel_cli.MANIFEST_ENV: str(broken)}):
            self.assertEqual(self.code(panel_cli.load_manifest), "manifest")

    def test_credentials_must_be_real_files_bound_inside_the_cli_state(self) -> None:
        forbidden = [self.repo]
        pairs = panel_cli.member_credentials(self.manifest, "muse", forbidden)
        self.assertEqual(pairs, [(self.base / "roots/cli-auth/muse/auth.json", self.home / ".config/muse/auth.json")])
        link = self.base / "roots/cli-auth/muse/link.json"
        link.symlink_to(self.base / "roots/cli-auth/muse/auth.json")
        (self.repo / "file.txt").write_text("x")
        cases = {
            "path outside state": {"path": str(self.home / ".bashrc"), "source": str(self.base / "roots/cli-auth/muse/auth.json")},
            "symlink source": {"path": str(self.home / ".config/muse/auth.json"), "source": str(link)},
            "source in repo": {"path": str(self.home / ".config/muse/auth.json"), "source": str(self.repo / "file.txt")},
            "source in read root": {"path": str(self.home / ".config/muse/auth.json"),
                                    "source": str(self.base / "roots/claude-sbx/cli-bin/shim")},
            "missing source": {"path": str(self.home / ".config/muse/auth.json"), "source": str(self.base / "nope")},
        }
        for name, credential in cases.items():
            with self.subTest(name=name):
                data = json.loads(json.dumps(self.manifest))
                data["clis"]["muse"]["credentials"] = [credential]
                self.assertEqual(self.code(lambda: panel_cli.member_credentials(data, "muse", forbidden)), "manifest")
        outside = json.loads(json.dumps(self.manifest))
        outside["clis"]["muse"]["state"] = ["/etc/muse"]
        self.assertEqual(self.code(lambda: panel_cli.member_credentials(outside, "muse", forbidden)), "manifest")
        with self.assertRaises(panel_cli.PanelCliError) as caught:
            panel_cli.member_credentials(self.manifest, "agy", forbidden)
        self.assertEqual(caught.exception.code, "cli-missing")
        self.assertIn("Turn the CLI on in the plugin.", str(caught.exception))

    def test_confines_with_hidden_roots_an_empty_state_and_the_member_credential(self) -> None:
        pairs = panel_cli.member_credentials(self.manifest, "muse", [self.repo])
        argv = panel_cli.confine(["muse", "exec"], "muse", self.base / "scratch", self.repo, False, self.manifest, pairs)
        joined = " ".join(argv)
        roots = self.base / "roots"
        self.assertIn(f"--tmpfs {roots / 'cli-auth'}", joined)
        self.assertIn(f"--tmpfs {roots / 'claude-sbx'}", joined)
        self.assertIn(f"--ro-bind {roots / 'claude-sbx/cli-bin'} {roots / 'claude-sbx/cli-bin'}", joined)
        self.assertIn(f"--tmpfs {self.home / '.config/muse'}", joined)
        self.assertIn(f"--bind {roots / 'cli-auth/muse/auth.json'} {self.home / '.config/muse/auth.json'}", joined)
        self.assertIn(f"--tmpfs {self.home / '.codex'}", joined)
        self.assertNotIn(f"--bind {self.home / '.config/muse'} ", joined)
        repo = panel_cli.confine(["muse"], "muse", self.repo / ".git/scratch", self.repo, False, self.manifest, pairs,
                                 self.repo)
        self.assertEqual(repo[repo.index("--tmpfs") + 1], "/tmp")
        order = [repo.index(f"--ro-bind"), repo.index(str(self.repo)), repo.index(str(self.repo / ".git/scratch"))]
        self.assertEqual(order, sorted(order))
        self.assertEqual(repo[-7:-4], ["--bind", str(self.repo / ".git/scratch"), str(self.repo / ".git/scratch")])

    def test_without_a_manifest_paseo_credential_mounts_are_hidden_by_pattern(self) -> None:
        state = self.base / "host/.local/state/paseo-sbx"
        for name in ("homes", "cli-auth", "claude/paseo-x-1234", "bin"):
            (state / name).mkdir(parents=True)
        config = self.base / "claude-config"
        config.mkdir()
        mountinfo = "\n".join(f"36 25 0:32 / {state / name} rw - virtiofs x rw"
                               for name in ("homes", "cli-auth", "claude/paseo-x-1234", "bin"))
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(config)}):
            roots = panel_cli.default_credential_roots(mountinfo)
        self.assertEqual(set(roots), {state / "homes", state / "cli-auth", state / "claude/paseo-x-1234", config})

    def test_shim_exit_78_is_not_mounted_with_the_hint(self) -> None:
        stderr = "warming up\npaseo-sbx-cli: /home/agent/.codex is not a mount point\n"
        with self.assertRaises(panel_cli.PanelCliError) as caught:
            panel_cli.classify("codex", 78, "", stderr, "Turn the CLI on in the plugin.")
        self.assertEqual(caught.exception.code, "not-mounted")
        self.assertIn("is not a mount point Turn the CLI on in the plugin.", str(caught.exception))
        with self.assertRaises(panel_cli.PanelCliError) as other:
            panel_cli.classify("codex", 78, "", "plain failure\n")
        self.assertEqual(other.exception.code, "exit-nonzero")

    def test_a_renewal_that_cannot_replace_the_bound_credential_is_credential_busy(self) -> None:
        stderr = "ERROR failed to persist OAuth tokens: Device or resource busy (os error 16)\n"
        with self.assertRaises(panel_cli.PanelCliError) as caught:
            panel_cli.classify("codex", 1, "", stderr)
        self.assertEqual(caught.exception.code, "credential-busy")
        self.assertIn("run codex once in the Sandbox terminal", str(caught.exception))

    def test_doctor_points_a_missing_cli_to_the_plugin_and_reports_the_scope(self) -> None:
        with patch.dict(os.environ, {panel_cli.MANIFEST_ENV: str(self.write(self.manifest)), "SANDBOX_NAME": "sbx-test"}), \
                patch.object(panel_cli, "load_config",
                             return_value={"gemini": {"profile": "agy", "model": "gemini-4-neon-high"}}):
            report = panel_cli.doctor(self.repo)
        self.assertEqual(report["manifest"], {"scope": "global", "clis": ["codex", "muse"]})
        self.assertIn("cli-missing", report["families"]["gemini"]["error"])
        self.assertTrue(any("Turn the CLI on in the plugin." in fix for fix in report["fixes"]))
        self.assertFalse(report["ok"])


@unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is not installed")
class ConfinementIntegrationTests(unittest.TestCase):
    """The real bubblewrap: own credential readable and renewable, others unreadable, state discarded."""

    def test_member_reads_and_renews_only_its_own_credential(self) -> None:
        # Outside /tmp, which the confinement replaces with a private tmpfs: the roots must be hidden
        # by their own tmpfs, not by the private /tmp.
        parent = "/var/tmp" if os.access("/var/tmp", os.W_OK) else None
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            base = Path(temporary).resolve()
            manifest = manifest_layout(base)
            home, roots = base / "home", base / "roots"
            (base / "scratch").mkdir()
            (base / "repo").mkdir()
            probe = textwrap.dedent(f"""
                printf 'own=%s\\n' "$(cat {home}/.config/muse/auth.json 2>/dev/null)"
                printf 'codex-home=%s\\n' "$(cat {home}/.codex/auth.json 2>/dev/null)"
                printf 'codex-source=%s\\n' "$(cat {roots}/cli-auth/codex/auth.json 2>/dev/null)"
                printf 'claude=%s\\n' "$(cat {roots}/claude-sbx/.credentials.json 2>/dev/null)"
                printf 'shim=%s\\n' "$(cat {roots}/claude-sbx/cli-bin/shim 2>/dev/null)"
                echo renewed > {home}/.config/muse/auth.json
                echo planted > {home}/.config/muse/hooks.json && echo planted-ok
            """)
            with patch.object(panel_cli.Path, "home", return_value=home):
                pairs = panel_cli.member_credentials(manifest, "muse", [base / "repo"])
                argv = panel_cli.confine(["sh", "-c", probe], "muse", base / "scratch", base / "repo", False,
                                         manifest, pairs, base / "repo")
            completed = subprocess.run(argv, capture_output=True, encoding="utf-8")
            if completed.returncode != 0 and "Operation not permitted" in completed.stderr:
                self.skipTest("unprivileged user namespaces are unavailable")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
            self.assertEqual(lines["own"], "muse-token")
            self.assertEqual((lines["codex-home"], lines["codex-source"], lines["claude"]), ("", "", ""))
            self.assertEqual(lines["shim"], "shim")
            self.assertIn("planted-ok", completed.stdout)
            self.assertEqual((roots / "cli-auth/muse/auth.json").read_text(), "renewed\n")
            self.assertFalse((home / ".config/muse/hooks.json").exists())


if __name__ == "__main__":
    unittest.main()
