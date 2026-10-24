"""Decision lens events: grammar, gate completeness, freshness, and enforcement switch."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import _common  # noqa: E402
import review_findings  # noqa: E402


def every_quiet(gate, scope=""):
    return _common.format_lens_event(gate, scope, no_signal=_common.LENS_GATES[gate])


class LensEventGrammarTest(unittest.TestCase):
    def test_every_gate_round_trips_with_all_its_lenses_quiet(self):
        for gate, lenses in _common.LENS_GATES.items():
            with self.subTest(gate=gate):
                event = every_quiet(gate)
                parsed = _common.parse_lens_event(event)
                self.assertEqual((gate, "", (), (), lenses), tuple(parsed))

    def test_every_lens_belongs_to_at_least_one_gate(self):
        mapped = {lens for lenses in _common.LENS_GATES.values() for lens in lenses}
        self.assertEqual(set(_common.LENS_IDS), mapped)

    def test_fired_blocked_and_scoped_event_round_trips(self):
        event = _common.format_lens_event(
            "redesign", _common.lens_scope_wave(2, 3),
            fired=[("L12", "redesign"), ("L3", "fail-closed")], blocked=["L5"],
            no_signal=["L2", "L4", "L6"])
        self.assertEqual(
            "lenses redesign wave 2 cycle 3: fired L12=redesign, L3=fail-closed; "
            "blocked L5; no-signal L2, L4, L6", event)
        parsed = _common.parse_lens_event(event)
        self.assertTrue(parsed.requests_redesign())
        self.assertTrue(parsed.requests_redesign(["L12"]))
        self.assertFalse(parsed.requests_redesign(["L2"]))

    def test_member_scope_keeps_twelve_hex_digits(self):
        scope = _common.lens_scope_member("gitops-apps", "0123456789abcdef0123")
        event = _common.format_lens_event("rebase", scope, no_signal=["L10", "L14"])
        self.assertEqual("member gitops-apps base 0123456789ab", _common.parse_lens_event(event).scope)

    def test_non_lens_events_are_not_parsed(self):
        for event in ("plan approved", "build recovery: {}", "lens policy changed"):
            with self.subTest(event=event):
                self.assertIsNone(_common.parse_lens_event(event))

    def test_malformed_events_are_refused(self):
        cases = {
            "unknown gate": "lenses deploy: fired none; blocked none; no-signal L1",
            "unknown verdict": "lenses ship: fired L1=maybe; blocked none; no-signal L3",
            "unknown lens": "lenses ship: fired none; blocked none; no-signal L1, L3, L17",
            "missing lens": "lenses ship: fired none; blocked none; no-signal L1",
            "extra lens": "lenses ship: fired none; blocked none; no-signal L1, L3, L4",
            "duplicate": "lenses ship: fired L1=keep; blocked L1; no-signal L3",
            "free text": "lenses ship: fired none; blocked none; no-signal L1, L3 because ship",
            "bad scope": "lenses panel wave two: fired none; blocked none; no-signal L3, L5, L6, L8, L11",
            "clause order": "lenses ship: no-signal L1, L3; fired none; blocked none",
        }
        for name, event in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(_common.LensEventError):
                    _common.parse_lens_event(event)

    def test_format_refuses_an_incomplete_event(self):
        with self.assertRaises(_common.LensEventError):
            _common.format_lens_event("plan", no_signal=["L2"])

    def test_lens_events_never_carry_a_recovery_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            lens = every_quiet("recovery")
            (project / "STATE.md").write_text(
                "# Project State\n\n## Log\n\n"
                f"- 2026-01-02 — build — {lens}\n"
                '- 2026-01-02 — define — build recovery: {"base": "' + "a" * 40 + '"}\n',
                encoding="utf-8")
            self.assertEqual(["a" * 40], review_findings._recovery_bases(project))


class LensRequirementTest(unittest.TestCase):
    def test_missing_event_is_refused_with_the_event_to_record(self):
        message = _common.lens_requirement(["build started"], "plan", consumed=("plan approved",))
        self.assertIn("`lenses plan: …`", message)

    def test_event_after_the_previous_decision_is_fresh(self):
        events = ["plan approved", every_quiet("plan")]
        self.assertIsNone(_common.lens_requirement(events, "plan", consumed=("plan approved",)))

    def test_event_consumed_by_the_previous_decision_is_stale(self):
        events = [every_quiet("plan"), "plan approved", "build plan repair requested"]
        self.assertIsNotNone(_common.lens_requirement(events, "plan", consumed=("plan approved",)))

    def test_scope_must_match(self):
        events = [every_quiet("cap", _common.lens_scope_wave(1, 3))]
        self.assertIsNotNone(_common.lens_requirement(events, "cap", _common.lens_scope_wave(2, 3)))
        self.assertIsNone(_common.lens_requirement(events, "cap", _common.lens_scope_wave(1, 3)))

    def test_recovery_marker_starts_a_new_epoch_when_asked(self):
        scope = _common.lens_scope_wave(1, 1)
        events = [every_quiet("fix-routing", scope), 'build recovery: {"base": "b"}']
        self.assertIsNone(_common.lens_requirement(events, "fix-routing", scope))
        self.assertIsNotNone(_common.lens_requirement(events, "fix-routing", scope, after_recovery=True))

    def test_previous_milestone_events_do_not_count(self):
        events = [every_quiet("define"), "archive preflight passed; shipment recorded", "intent drafted"]
        self.assertIsNotNone(_common.lens_requirement(events, "define"))
        events = [every_quiet("define"), "milestone abandoned: demo; archive: a; ruling: stop"]
        self.assertIsNotNone(_common.lens_requirement(events, "define"))

    def test_malformed_event_for_the_gate_is_reported(self):
        events = ["lenses plan: fired none; blocked none; no-signal L2"]
        self.assertIn("must answer exactly", _common.lens_requirement(events, "plan"))

    def test_latest_event_skips_malformed_and_respects_recovery(self):
        scope = _common.lens_scope_wave(1, 2)
        fired = _common.format_lens_event(
            "fix-routing", scope, fired=[("L12", "redesign")],
            no_signal=["L2", "L3", "L5", "L6", "L11"])
        events = [fired, "lenses fix-routing wave 1 cycle 2: bad"]
        self.assertTrue(_common.latest_lens_event(events, "fix-routing", scope).requests_redesign())
        events.append('build recovery: {"base": "c"}')
        self.assertIsNone(_common.latest_lens_event(events, "fix-routing", scope, after_recovery=True))


class LensEnforcementSwitchTest(unittest.TestCase):
    def test_only_enforced_turns_lenses_on(self):
        self.assertTrue(_common.lens_enforced("# Intent\n\nDecision lenses: enforced   <!-- note -->\n"))
        self.assertFalse(_common.lens_enforced("# Intent\n\nDecision lenses: off\n"))
        self.assertFalse(_common.lens_enforced("# Intent\n\nFinding skeptics: off\n"))
        self.assertFalse(_common.lens_enforced(None))

    def test_program_gates_read_the_charter_and_others_the_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / ".project"
            (project / "intent").mkdir(parents=True)
            (project / "intent" / "INTENT.md").write_text("Decision lenses: enforced\n", encoding="utf-8")
            self.assertTrue(_common.lens_enforced_for(project, "plan"))
            self.assertFalse(_common.lens_enforced_for(project, "roadmap"))
            (project / "CHARTER.md").write_text("Decision lenses: enforced\n", encoding="utf-8")
            self.assertTrue(_common.lens_enforced_for(project, "roadmap"))
            lookahead = project / "next"
            lookahead.mkdir()
            self.assertTrue(_common.lens_enforced_for(lookahead, "charter"))
            self.assertFalse(_common.lens_enforced_for(lookahead, "plan"))


if __name__ == "__main__":
    unittest.main()
