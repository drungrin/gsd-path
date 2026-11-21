"""CLI review-panel members (fork only): configuration, confinement, receipts, and failures."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
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

    def run_member(self, mode: str, version: str = "0.166.0") -> subprocess.CompletedProcess:
        final = self.base / "attempt/final.md"
        final.parent.mkdir(exist_ok=True)
        (self.home / "fake-codex").write_text(mode)
        if final.exists():
            final.unlink()
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", HOME=str(self.home),
                   SANDBOX_NAME="test-sandbox", FAKE_CODEX_VERSION=version)
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

    def test_blocks_with_a_coded_last_stderr_line(self) -> None:
        for mode, version, code in (("login", "0.166.0", "login"), ("wrong-nonce", "0.166.0", "no-access-receipt"),
                                    ("no-inspected", "0.166.0", "no-access-receipt"),
                                    ("empty", "0.166.0", "empty-output"), ("ok", "0.199.0", "cli-version")):
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


if __name__ == "__main__":
    unittest.main()
