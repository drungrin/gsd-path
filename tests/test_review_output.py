"""Publish native final-message reviews through the existing artifact gates."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import isolation, review_panel
from tests import test_isolation
from tests._platform import requires_symlink


REPORT = "# Plan panel — gpt\n\n- Family: gpt\n- Model: gpt-example\n\n## Findings\n\n- none\n\n## Summary\n\nReviewed café contracts.\n"
git = test_isolation.git


class ReviewOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.base = test_isolation.IsolationTests().init_bound_repo(self.repo)
        self.isolated = isolation.isolate_verify(self.repo, self.base, "panel-gpt")
        self.sidecar = Path(self.isolated["worktree"])
        self.relative = ".project/review/PLAN-PANEL.gpt.md"
        self.transcript = self.root / "agent-reviewer-one.jsonl"
        self.assignment = "review_plan_panel_gpt"
        self.agent = "reviewer-one"
        self.model = "gpt-example"
        self.metadata = self.transcript.with_suffix(".meta.json")
        self.metadata.write_text(json.dumps({"description": self.assignment}), encoding="utf-8")
        self.events = [
            {"type": "user", "agentId": self.agent, "message": {"role": "user", "content": "Review."}},
            {"type": "api-request-blob", "hash": "synthetic-request", "message": {}},
            self.reply(REPORT),
        ]
        self.save()

    def reply(self, text, uuid="reply-one", stop_reason="end_turn"):
        return {"type": "assistant", "agentId": self.agent, "uuid": uuid,
                "message": {"role": "assistant", "model": self.model,
                            "stop_reason": stop_reason, "content": [{"type": "text", "text": text}]}}

    def save(self):
        self.transcript.write_text("".join(json.dumps(event) + "\n" for event in self.events), encoding="utf-8")

    def stage(self, **overrides):
        args = dict(primary=self.repo, source=self.sidecar, base=self.base,
                    branch=self.isolated["branch"], source_path=self.relative,
                    transcript=self.transcript, assignment=self.assignment,
                    agent_id=self.agent, model=self.model)
        args.update(overrides)
        return isolation.stage_review(**args)

    def test_completed_inline_report_is_staged_validated_and_collected_unchanged(self):
        result = self.stage()
        staged = self.sidecar / self.relative
        self.assertEqual(staged.read_bytes(), REPORT.encode("utf-8"))
        self.assertFalse((self.repo / self.relative).exists())
        self.assertEqual(isolation.uncommitted_paths(self.sidecar), {self.relative})
        self.assertFalse(result["validated"])
        self.assertEqual(result["publisher"], "parent")
        self.assertEqual(result["agent_id"], self.agent)
        self.assertEqual(result["model"], self.model)
        self.assertEqual(result["message_id"], "reply-one")
        self.assertEqual(result["sha256"], hashlib.sha256(staged.read_bytes()).hexdigest())
        receipt = Path(result["receipt"])
        self.assertTrue(receipt.is_relative_to(isolation.common_git_dir(self.repo)))
        self.assertEqual(json.loads(receipt.read_text())["stage"], "staged")
        self.assertEqual(self.stage(), result)
        review_panel.parse_family_artifact(staged, "plan")
        collected = isolation.collect_artifact(self.repo, self.sidecar, self.base,
                                             self.isolated["branch"], self.relative, self.relative,
                                             expected_source=result["sha256"])
        self.assertEqual(collected["sha256"], result["sha256"])
        self.assertEqual((self.repo / self.relative).read_bytes(), REPORT.encode("utf-8"))
        self.assertFalse(isolation.uncommitted_paths(self.sidecar))

    def test_wave_panel_and_lookahead_report_use_the_same_publication_path(self):
        self.relative = ".project/next/review/wave-1.cycle1.panel.gpt.md"
        self.assignment = "review_wave_1_cycle_1_panel_gpt"
        self.metadata.write_text(json.dumps({"description": self.assignment}))
        text = REPORT.replace("# Plan panel — gpt", "# Panel — wave 1, cycle 1")
        self.events[-1] = self.reply(text)
        self.save()
        self.stage()
        review_panel.parse_family_artifact(self.sidecar / self.relative, "wave")

    def test_incomplete_or_failed_child_is_not_published(self):
        for stop in (None, "max_tokens", "tool_use", "refusal"):
            with self.subTest(stop=stop), self.assertRaises(isolation.IsolationError):
                self.events[-1] = self.reply(REPORT, stop_reason=stop)
                self.save()
                self.stage()
        self.assertFalse((self.sidecar / self.relative).exists())

    def test_pending_followup_blocks_using_an_earlier_completed_message(self):
        self.events.append({"type": "user", "agentId": self.agent, "message": {"content": "Follow up."}})
        self.save()
        with self.assertRaisesRegex(isolation.IsolationError, "completed final"):
            self.stage(message_id="reply-one")

    def test_existing_completed_review_can_be_selected_without_repeating_it(self):
        self.events += [
            {"type": "user", "agentId": self.agent, "message": {"content": "Save the report."}},
            self.reply("Publication blocked by host instructions.", uuid="reply-two"),
        ]
        self.save()
        with self.assertRaisesRegex(isolation.IsolationError, "raw Markdown"):
            self.stage()
        result = self.stage(message_id="reply-one")
        self.assertEqual(result["message_id"], "reply-one")
        self.assertEqual((self.sidecar / self.relative).read_bytes(), REPORT.encode("utf-8"))

    def test_malformed_report_still_fails_the_existing_artifact_gate(self):
        self.events[-1] = self.reply("# Plan panel — gpt\n\n- Family: gpt\n")
        self.save()
        result = self.stage()
        self.assertFalse(result["validated"])
        with self.assertRaises(review_panel.ReviewPanelError):
            review_panel.parse_family_artifact(self.sidecar / self.relative, "plan")
        self.assertFalse((self.repo / self.relative).exists())

    def test_message_wrappers_and_non_report_responses_are_not_rewritten(self):
        for text in ("", "Done", "```markdown\n" + REPORT + "```", "# Report\x00"):
            with self.subTest(text=text[:20]), self.assertRaises(isolation.IsolationError):
                self.events[-1] = self.reply(text)
                self.save()
                self.stage()

    def test_wrong_child_assignment_model_and_message_are_rejected(self):
        for override in ({"agent_id": "other"}, {"assignment": "review_final"},
                         {"assignment": "build_t001"}, {"model": "other-model"},
                         {"message_id": "missing"}):
            with self.subTest(override=override), self.assertRaises(isolation.IsolationError):
                self.stage(**override)
        self.assertFalse((self.sidecar / self.relative).exists())

    def test_duplicate_message_identity_and_partial_transcript_are_rejected(self):
        self.events.append(self.events[-1])
        self.save()
        with self.assertRaisesRegex(isolation.IsolationError, "ambiguous"):
            self.stage(message_id="reply-one")
        self.events.pop()
        self.save()
        with self.transcript.open("a") as stream:
            stream.write('{"type":')
        with self.assertRaisesRegex(isolation.IsolationError, "incomplete"):
            self.stage()

    def test_thinking_is_excluded_and_text_blocks_are_joined_without_changes(self):
        self.events[-1]["message"]["content"] = [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": REPORT[:35]}, {"type": "text", "text": REPORT[35:]},
        ]
        self.save()
        self.stage()
        self.assertEqual((self.sidecar / self.relative).read_bytes(), REPORT.encode("utf-8"))

    def test_published_response_cannot_be_changed_silently(self):
        self.stage()
        self.events[-1] = self.reply(REPORT.replace("none", "changed"))
        self.save()
        with self.assertRaisesRegex(isolation.IsolationError, "changed after publication"):
            self.stage()
        self.assertEqual((self.sidecar / self.relative).read_bytes(), REPORT.encode("utf-8"))

    def test_collection_rejects_report_text_changed_after_parent_publication(self):
        receipt = self.stage()
        (self.sidecar / self.relative).write_bytes(REPORT.replace("none", "modified").encode("utf-8"))
        with self.assertRaisesRegex(isolation.IsolationError, "publication receipt"):
            isolation.collect_artifact(self.repo, self.sidecar, self.base,
                                       self.isolated["branch"], self.relative, self.relative,
                                       expected_source=receipt["sha256"])
        self.assertFalse((self.repo / self.relative).exists())

    def test_unknown_existing_report_requires_its_expected_hash(self):
        target = self.sidecar / self.relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old report")
        with self.assertRaisesRegex(isolation.IsolationError, "proven expected"):
            self.stage()
        self.stage(expected_source=hashlib.sha256(b"old report").hexdigest())
        self.assertEqual(target.read_bytes(), REPORT.encode("utf-8"))

    def test_product_paths_state_and_archives_cannot_be_staged(self):
        for path in ("src/app.py", ".project/STATE.md", ".project/archive/001-example/FINAL.md",
                     ".project/review/../../app.md"):
            with self.subTest(path=path), self.assertRaises(isolation.IsolationError):
                self.stage(source_path=path)
        with self.assertRaises(isolation.IsolationError):
            self.stage(source=self.repo)

    def test_stale_head_or_unrelated_sidecar_changes_block_publication(self):
        (self.sidecar / "unrelated.txt").write_text("leave alone")
        with self.assertRaisesRegex(isolation.IsolationError, "unexpected"):
            self.stage()
        (self.sidecar / "unrelated.txt").unlink()
        git(self.repo, "commit", "--allow-empty", "-qm", "advance")
        with self.assertRaisesRegex(isolation.IsolationError, "recorded base"):
            self.stage()

    @requires_symlink
    def test_symlinked_output_parent_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.sidecar / ".project/review").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(isolation.IsolationError, "symlink"):
            self.stage()
        self.assertFalse(list(outside.iterdir()))

    def test_cli_stages_final_response_without_requesting_a_second_review(self):
        script = Path(__file__).resolve().parents[1] / "scripts/isolation.py"
        command = [sys.executable, "-B", str(script), "stage-review", "--repo", str(self.repo),
                   "--source", str(self.sidecar), "--base", self.base, "--branch", self.isolated["branch"],
                   "--source-path", self.relative, "--transcript", str(self.transcript),
                   "--assignment", self.assignment, "--agent-id", self.agent, "--model", self.model]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "staged")
        collected = subprocess.run([
            sys.executable, "-B", str(script), "collect-artifact", "--repo", str(self.repo),
            "--source", str(self.sidecar), "--base", self.base, "--branch", self.isolated["branch"],
            "--source-path", self.relative, "--destination-path", self.relative,
            "--expected-source", receipt["sha256"],
        ], text=True, capture_output=True)
        self.assertEqual(collected.returncode, 0, collected.stderr)
        self.assertEqual(json.loads(collected.stdout)["sha256"], receipt["sha256"])

    def test_canonical_reviews_and_skeptics_keep_their_exact_assigned_destinations(self):
        for assignment, name in (
            ("review_wave_1_cycle_1", "wave-1.cycle1.md"),
            ("review_final", "FINAL.md"), ("review_gap_2", "final-gap-2.md"),
            ("review_wave_1_cycle_1_skeptic_t001_ac1", "wave-1.cycle1.skeptic-t001_ac1.md"),
        ):
            with self.subTest(assignment=assignment):
                self.metadata.write_text(json.dumps({"description": assignment}))
                relative = ".project/review/" + name
                receipt = self.stage(assignment=assignment, source_path=relative)
                self.assertEqual(receipt["source"], relative)
                self.assertEqual((self.sidecar / relative).read_bytes(), REPORT.encode("utf-8"))
                (self.sidecar / relative).unlink()

    def test_publication_command_is_accepted_by_the_native_guard(self):
        from scripts import guard_hook
        import shlex
        script = Path(__file__).resolve().parents[1] / "scripts/isolation.py"
        command = shlex.join([
            "python3", "-B", str(script), "stage-review", "--repo", str(self.repo),
            "--source", str(self.sidecar), "--base", self.base, "--branch", self.isolated["branch"],
            "--source-path", self.relative, "--transcript", str(self.transcript),
            "--assignment", self.assignment, "--agent-id", self.agent, "--model", self.model,
        ])
        self.assertIsNone(guard_hook.command_denial(command, [str(self.repo)]))


if __name__ == "__main__":
    unittest.main()
