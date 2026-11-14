from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import _common

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def parse(line: str) -> dict:
    if not line.startswith(_common.RECEIPT_PREFIX):
        raise AssertionError(f"not a receipt line: {line!r}")
    return json.loads(line[len(_common.RECEIPT_PREFIX):])


def receipt_lines(stderr: str) -> list:
    return [parse(line) for line in stderr.splitlines() if line.startswith(_common.RECEIPT_PREFIX)]


def clean_environment(**extra: str) -> dict:
    environment = {key: value for key, value in os.environ.items()
                   if key not in (_common.RECEIPT_NESTED, _common.RECEIPT_SWITCH)}
    environment.update(extra)
    return environment


class ReceiptLineTest(unittest.TestCase):
    def test_landing_carries_task_and_commit(self) -> None:
        output = json.dumps({"blocked": [], "landed": [{"task": "T002", "commit": "a" * 40}], "status": "landed"})
        receipt = parse(_common.receipt_line("dispatch_driver.py", ["finish", "--repo", "/r", "--task-id", "T002"], 0, output))
        self.assertEqual(receipt, {"cmd": "finish", "commit": "a" * 40, "exit": 0, "outcome": "ok",
                                   "script": "dispatch_driver.py", "status": "landed", "task": "T002"})

    def test_blocked_result_fails_even_with_exit_zero(self) -> None:
        output = json.dumps({"blocked": [{"task": "T003", "reason": "Verify failed"}], "landed": []})
        receipt = parse(_common.receipt_line("dispatch_driver.py", ["round", "--wave", "2"], 0, output))
        self.assertEqual((receipt["outcome"], receipt["reason"], receipt["wave"]), ("fail", "Verify failed", 2))
        receipt = parse(_common.receipt_line("model_policy.py", ["resolve"], 0, json.dumps({"status": "blocked", "reason": "model unavailable"})))
        self.assertEqual((receipt["outcome"], receipt["status"], receipt["reason"]), ("fail", "blocked", "model unavailable"))

    def test_question_and_blocking_route_warn(self) -> None:
        question = parse(_common.receipt_line("dispatch_driver.py", ["finish"], 0, json.dumps({"blocked": [], "landed": [], "status": "question"})))
        self.assertEqual(question["outcome"], "warn")
        route = {"schema": "gsd-path/route/v1", "route": {"action": "block", "reason": "build is blocked"}}
        receipt = parse(_common.receipt_line("pipeline_state.py", ["route"], 0, json.dumps(route)))
        self.assertEqual((receipt["outcome"], receipt["status"], receipt["reason"]), ("warn", "block", "build is blocked"))

    def test_nonzero_exit_fails_with_the_last_error_line(self) -> None:
        receipt = parse(_common.receipt_line("check_handoffs.py", ["plan"], 1, "", "note\nhandoff validation failed: PLAN.md misses SC3\n"))
        self.assertEqual(receipt, {"cmd": "plan", "exit": 1, "outcome": "fail", "reason": "handoff validation failed: PLAN.md misses SC3",
                                   "script": "check_handoffs.py", "status": "failed"})

    def test_reads_the_last_object_line_and_keeps_one_line(self) -> None:
        output = 'progress\n{"status": "committed", "commit": "b' + "0" * 39 + '"}\n'
        receipt = parse(_common.receipt_line("isolation.py", ["checkpoint"], 0, output))
        self.assertEqual((receipt["status"], receipt["commit"]), ("committed", "b" + "0" * 39))
        line = _common.receipt_line("x.py", [], 1, json.dumps({"status": "failed", "reason": "a\nb‮" + "c" * 400}))
        self.assertEqual(line.count("\n"), 0)
        self.assertLessEqual(len(parse(line)["reason"]), 300)
        self.assertNotIn("‮", line)

    def test_unreadable_output_leaves_the_exit_code(self) -> None:
        receipt = parse(_common.receipt_line("task_context.py", ["--repo", "/r"], 0, "not json {"))
        self.assertEqual(receipt, {"cmd": "", "exit": 0, "outcome": "ok", "script": "task_context.py", "status": "ok"})


class CliReceiptTest(unittest.TestCase):
    def run_main(self, main, argv=("pipeline_state.py", "route"), **environment: str):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", list(argv)), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(sys, "stderr", stderr), mock.patch.dict(os.environ, clean_environment(**environment), clear=True):
            try:
                returned = _common.cli_receipt(main)
            except SystemExit as stop:
                returned = stop
            nested = os.environ.get(_common.RECEIPT_NESTED)
        return returned, stdout.getvalue(), stderr.getvalue(), nested

    def test_returns_main_result_and_writes_one_line_after_output(self) -> None:
        def main() -> int:
            print(json.dumps({"status": "transitioned"}))
            return 0
        returned, stdout, stderr, nested = self.run_main(main)
        self.assertEqual(returned, 0)
        self.assertEqual(stdout, '{"status": "transitioned"}\n')
        self.assertEqual(receipt_lines(stderr), [{"cmd": "route", "exit": 0, "outcome": "ok", "script": "pipeline_state.py", "status": "transitioned"}])
        self.assertEqual(nested, "1")

    def test_system_exit_passes_through_with_its_code(self) -> None:
        def main() -> int:
            print("error: bad input", file=sys.stderr)
            raise SystemExit(2)
        returned, _, stderr, _ = self.run_main(main)
        self.assertIsInstance(returned, SystemExit)
        self.assertEqual(returned.code, 2)
        self.assertEqual(receipt_lines(stderr)[0]["reason"], "error: bad input")

    def test_typed_refusal_on_stderr_stays_pure_json(self) -> None:
        def main() -> int:
            print(json.dumps({"code": "ruling-required", "verdict": "refused"}), file=sys.stderr)
            return 1
        returned, _, stderr, _ = self.run_main(main, argv=("isolation.py", "adopt-member-rebase"))
        self.assertEqual(returned, 1)
        self.assertEqual(json.loads(stderr)["code"], "ruling-required")

    def test_nested_switched_off_and_internal_calls_write_nothing(self) -> None:
        main = lambda: 0  # noqa: E731
        self.assertEqual(self.run_main(main, **{_common.RECEIPT_NESTED: "1"})[2], "")
        self.assertEqual(self.run_main(main, **{_common.RECEIPT_SWITCH: "off"})[2], "")
        returned, _, stderr, nested = self.run_main(main, argv=("dispatch_driver.py", "_child", "--state", "s"), **{_common.RECEIPT_NESTED: "1"})
        self.assertEqual((returned, stderr, nested), (0, "", None))


class HelperProcessTest(unittest.TestCase):
    def test_helper_writes_the_line_on_stderr_and_keeps_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(("git", "init", "-q", str(repo)), check=True)
            result = subprocess.run(
                (sys.executable, "-B", str(SCRIPTS / "check_handoffs.py"), "intent", "--repo", str(repo)),
                capture_output=True, encoding="utf-8", env=clean_environment(), check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn(_common.RECEIPT_PREFIX, result.stdout)
            [receipt] = receipt_lines(result.stderr)
            self.assertEqual((receipt["script"], receipt["cmd"], receipt["outcome"], receipt["exit"]), ("check_handoffs.py", "intent", "fail", 1))
            self.assertIn("handoff validation failed", receipt["reason"])

            quiet = subprocess.run(
                (sys.executable, "-B", str(SCRIPTS / "check_handoffs.py"), "intent", "--repo", str(repo)),
                capture_output=True, encoding="utf-8", env=clean_environment(**{_common.RECEIPT_NESTED: "1"}), check=False,
            )
            self.assertEqual((quiet.returncode, receipt_lines(quiet.stderr)), (1, []))


if __name__ == "__main__":
    unittest.main()
