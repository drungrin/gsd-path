"""review_findings.py computes the build step 7 finding sets from disk."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_findings.py"
sys.path.insert(0, str(ROOT / "scripts"))

import _common  # noqa: E402
import review_findings  # noqa: E402

PLAN = """# Plan — demo

Project verify: `python3 -m unittest`

## Config

- max_review_cycles: {cycles}
- wave_budget: none
- review_panel: off
- finding_skeptics: {skeptics}

## Wave 1 — risk burn-down

Goal: prove the parser
Review depth: {depth}
"""

TASK = """---
id: {id}
title: {title}
wave: 1
deps: []
status: done
agent: null
base: null
worktree: null
task_branch: null
files:
{files}
---

# {id} — {title}

## Interface contract

- {interface}

## Intent coverage

- {owned}

## Acceptance criteria

1. {ac1}
2. {ac2}

## Verify

```bash
python3 -m unittest tests.test_demo
```

## Log

- 2026-09-01 — created by planner
"""

LENS = """# Review — wave 1, cycle {cycle}

Wave verdict: {verdict}
Cycle: {cycle}
Depth: {depth}
{lens_line}Tasks reviewed: 2

## T001 — Parse input: {t001}

{t001_body}
Warnings (non-blocking):
- none

Contract violations (blocking):
{t001_violations}

## T002 — Render output: pass

- ✅ Renders a table — verified render.py:10

## Intent coverage

### SC1 — Parser accepts CSV: {sc1}
{sc1_body}
"""

SKEPTIC = """# Skeptic — wave 1, cycle {cycle}

- Criterion: Parses CSV rows
- Criterion locator: {locator}
- Lenses: contract

## Observations

{observations}
## Observation verdicts

{observation_verdicts}
## Verdict

{verdict}

## Evidence

Re-ran the verify in the sidecar.
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def lens_text(cycle=1, depth="deep", lens="contract", verdict="blocked", t001_fails=None,
              violations=("none",), sc1_fails=None):
    t001_fails = list(t001_fails or [])
    sc1_fails = list(sc1_fails or [])
    t001_body = "\n".join(
        [f"- ❌ {item}\n  fix: correct it" for item in t001_fails]
        or ["- ✅ Parses CSV rows — verified parser.py:5"]
    )
    sc1_body = "\n".join([f"- ❌ {item}" for item in sc1_fails] or ["- ✅ verified parser.py:5"])
    return LENS.format(
        cycle=cycle,
        verdict=verdict,
        depth=depth,
        lens_line=f"Lens: {lens}\n" if depth == "deep" else "",
        t001="fail" if t001_fails or violations != ("none",) else "pass",
        t001_body=t001_body,
        t001_violations="\n".join(f"- ❌ {item}" if item != "none" else "- none" for item in violations),
        sc1="fail" if sc1_fails else "pass",
        sc1_body=sc1_body,
    )


def skeptic_text(locator, cycle, verdict, observations):
    blocks = "\n".join(
        f"### Observation {n} — contract\n\n{text}\n" for n, text in enumerate(observations, 1)
    )
    verdicts = "\n".join(
        f"### Observation {n}: {verdict}\n\nchecked\n" for n in range(1, len(observations) + 1)
    )
    return SKEPTIC.format(
        cycle=cycle, locator=locator, verdict=verdict, observations=blocks,
        observation_verdicts=verdicts,
    )


class Fixture:
    def __init__(self, test, cycles=3, skeptics="on", depth="deep"):
        self.dir = tempfile.TemporaryDirectory()
        test.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.project = self.root / ".project"
        write(self.project / "plan" / "PLAN.md", PLAN.format(cycles=cycles, skeptics=skeptics, depth=depth))
        write(
            self.project / "tasks" / "T001-parse-input.md",
            TASK.format(id="T001", title="Parse input", files="  - src/parser.py", interface="None",
                        owned="SC1", ac1="Parses CSV rows", ac2="Rejects malformed rows"),
        )
        write(
            self.project / "tasks" / "T002-render-output.md",
            TASK.format(id="T002", title="Render output", files="  - src/render.py", interface="None",
                        owned="None", ac1="Renders a table", ac2="Handles empty input"),
        )

    def lens(self, lens, cycle=1, **kwargs):
        name = f"wave-1.cycle{cycle}.{lens}.md" if lens in {"contract", "adversarial"} else f"wave-1.cycle{cycle}.md"
        write(self.project / "review" / name, lens_text(cycle=cycle, lens=lens, **kwargs))

    def skeptic(self, locator, cycle, verdict, observations):
        write(
            self.project / "review" / f"wave-1.cycle{cycle}.skeptic-{locator}.md",
            skeptic_text(locator, cycle, verdict, observations),
        )

    def run(self, cycle=1, extra=()):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "collect", "--repo", str(self.root),
             "--wave", "1", "--cycle", str(cycle), *extra],
            capture_output=True, encoding="utf-8", errors="replace", check=False,
        )
        return result.returncode, json.loads(result.stdout)


class GroupingTest(unittest.TestCase):
    def test_groups_by_locator_and_keeps_distinct_observations(self):
        fixture = Fixture(self)
        fixture.lens("contract", t001_fails=["Rejects malformed rows — found: accepts them, parser.py:9"],
                     sc1_fails=["Parser accepts CSV — found: TSV only, parser.py:3"])
        fixture.lens("adversarial", t001_fails=["Rejects malformed rows — found: crashes on quotes, parser.py:12"],
                     violations=["task changed path outside its files list — src/render.py edited"])
        code, result = fixture.run()
        self.assertEqual(code, 0, result)
        by_locator = {group["locator"]: group for group in result["groups"]}
        self.assertEqual(sorted(by_locator), ["sc1", "t001_ac2", "t001_files"])
        ac2 = by_locator["t001_ac2"]
        self.assertEqual(ac2["lenses"], ["contract", "adversarial"])
        self.assertEqual(len(ac2["observations"]), 2)
        self.assertEqual(ac2["criterion"], "Rejects malformed rows")
        self.assertEqual(by_locator["sc1"]["tasks"], ["T001"])
        self.assertEqual(result["skeptic_groups"], ["sc1", "t001_ac2", "t001_files"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["cap_reached"])

    def test_true_duplicates_collapse(self):
        fixture = Fixture(self)
        same = "Rejects malformed rows — found: accepts them, parser.py:9"
        fixture.lens("contract", t001_fails=[same])
        fixture.lens("adversarial", t001_fails=[same])
        _, result = fixture.run()
        (group,) = result["groups"]
        self.assertEqual(len(group["observations"]), 1)
        self.assertEqual(group["lenses"], ["contract", "adversarial"])

    def test_unlocatable_finding_is_structural(self):
        fixture = Fixture(self)
        fixture.lens("contract", t001_fails=["Some paraphrase — found: nothing, x.py:1"])
        fixture.lens("adversarial", verdict="pass")
        _, result = fixture.run()
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["structural_blockers"][0]["kind"], "invalid-artifact")
        self.assertIn("names no task criterion", result["structural_blockers"][0]["detail"])

    def test_sub_bullet_criterion_quoted_without_list_markers_keeps_its_locator(self):
        # Issue #392: a wrapped criterion is stored with the `- ` markers of its
        # sub-bullets. A quote without them is the same criterion, not a
        # structural blocker.
        fixture = Fixture(self, skeptics="off")
        write(
            fixture.project / "tasks" / "T001-parse-input.md",
            TASK.format(id="T001", title="Parse input", files="  - src/parser.py",
                        interface="None", owned="SC1", ac1="Parses CSV rows",
                        ac2="Rejects malformed rows:\n   - reports the row number\n   - keeps the earlier rows"),
        )
        stored = "Rejects malformed rows: - reports the row number - keeps the earlier rows"
        for quote in (stored, "Rejects malformed rows: reports the row number keeps the earlier rows"):
            with self.subTest(quote=quote):
                fixture.lens("contract", t001_fails=[f"{quote} — found: drops them, parser.py:9"])
                fixture.lens("adversarial", verdict="pass")
                _, result = fixture.run()
                self.assertEqual(result["structural_blockers"], [])
                (group,) = result["groups"]
                self.assertEqual((group["locator"], group["criterion"]), ("t001_ac2", stored))
                self.assertEqual(result["fix_groups"], ["t001_ac2"])
        for quote in (
            "Rejects malformed rows: reports the row number keeps the later rows",  # changed word
            "Rejects malformed rows:",  # sub-bullets dropped
            "Rejects malformed rows: keeps the earlier rows reports the row number",  # reordered
            "Rejects malformed rows- reports the row number keeps the earlier rows",  # changed punctuation
        ):
            with self.subTest(quote=quote):
                fixture.lens("contract", t001_fails=[f"{quote} — found: drops them, parser.py:9"])
                _, result = fixture.run()
                self.assertEqual(result["groups"], [])
                self.assertIn("names no task criterion", result["structural_blockers"][0]["detail"])

    def test_quote_of_a_criterion_that_contains_an_em_dash(self):
        # The wave gate tries each ` — ` as the end of the quote; this reader
        # must find the same criterion.
        fixture = Fixture(self, skeptics="off")
        stored = "Rejects malformed rows — and keeps the earlier rows"
        write(
            fixture.project / "tasks" / "T001-parse-input.md",
            TASK.format(id="T001", title="Parse input", files="  - src/parser.py",
                        interface="None", owned="SC1", ac1="Parses CSV rows", ac2=stored),
        )
        fixture.lens("contract", t001_fails=[f"{stored} — found: drops them, parser.py:9"])
        fixture.lens("adversarial", verdict="pass")
        _, result = fixture.run()
        self.assertEqual(result["structural_blockers"], [])
        (group,) = result["groups"]
        self.assertEqual((group["locator"], group["criterion"]), ("t001_ac2", stored))

        fixture.lens(
            "contract",
            t001_fails=["Rejects malformed rows — and keeps the later rows — found: drops them, parser.py:9"],
        )
        _, result = fixture.run()
        self.assertEqual(result["groups"], [])
        self.assertIn("names no task criterion", result["structural_blockers"][0]["detail"])

    def test_exact_quote_wins_over_a_marker_free_reading_of_an_earlier_criterion(self):
        fixture = Fixture(self, skeptics="off")
        fixture.lens("adversarial", verdict="pass")
        for ac1, ac2, quote in (
            ("Returns\n   - 5 items", "Returns 5 items", "Returns 5 items"),
            ("Prints hello", "Prints hello — and exits 0", "Prints hello — and exits 0"),
        ):
            with self.subTest(quote=quote):
                write(
                    fixture.project / "tasks" / "T001-parse-input.md",
                    TASK.format(id="T001", title="Parse input", files="  - src/parser.py",
                                interface="None", owned="SC1", ac1=ac1, ac2=ac2),
                )
                fixture.lens("contract", t001_fails=[f"{quote} — found: 4, parser.py:9"])
                _, result = fixture.run()
                self.assertEqual(result["structural_blockers"], [])
                (group,) = result["groups"]
                self.assertEqual((group["locator"], group["criterion"]), ("t001_ac2", ac2))

    def test_sub_bullets_join_at_any_indentation(self):
        # The wave gate joins any indented line to its criterion; this reader
        # must store the same criterion.
        fixture = Fixture(self, skeptics="off")
        fixture.lens("adversarial", verdict="pass")
        stored = "Prints all of: - alpha - beta"
        for indent in ("  ", "\t", "   "):
            for quote in (stored, "Prints all of: alpha beta"):
                with self.subTest(indent=indent, quote=quote):
                    write(
                        fixture.project / "tasks" / "T001-parse-input.md",
                        TASK.format(id="T001", title="Parse input", files="  - src/parser.py",
                                    interface="None", owned="SC1", ac1="Parses CSV rows",
                                    ac2=f"Prints all of:\n{indent}- alpha\n{indent}- beta"),
                    )
                    fixture.lens("contract", t001_fails=[f"{quote} — found: only alpha, parser.py:9"])
                    _, result = fixture.run()
                    self.assertEqual(result["structural_blockers"], [])
                    (group,) = result["groups"]
                    self.assertEqual((group["locator"], group["criterion"]), ("t001_ac2", stored))

    def test_hyphen_inside_a_criterion_line_is_compared(self):
        # Only the `- ` markers of sub-bullet lines are optional. Two criteria
        # that differ by a minus sign are different criteria.
        fixture = Fixture(self, skeptics="off")
        write(
            fixture.project / "tasks" / "T001-parse-input.md",
            TASK.format(id="T001", title="Parse input", files="  - src/parser.py",
                        interface="None", owned="SC1", ac1="Render `a b`", ac2="Render `a - b`"),
        )
        for quote, locator in (("Render `a - b`", "t001_ac2"), ("Render `a b`", "t001_ac1")):
            with self.subTest(quote=quote):
                fixture.lens("contract", t001_fails=[f"{quote} — found: wrong output, parser.py:9"])
                fixture.lens("adversarial", verdict="pass")
                _, result = fixture.run()
                self.assertEqual(result["structural_blockers"], [])
                (group,) = result["groups"]
                self.assertEqual((group["locator"], group["criterion"]), (locator, quote))
        write(
            fixture.project / "tasks" / "T001-parse-input.md",
            TASK.format(id="T001", title="Parse input", files="  - src/parser.py",
                        interface="None", owned="SC1", ac1="Parses CSV rows",
                        ac2="Returns a - b:\n   - for integers\n   - for floats"),
        )
        fixture.lens("contract", t001_fails=["Returns a b: for integers for floats — found: a + b, parser.py:9"])
        _, result = fixture.run()
        self.assertEqual(result["groups"], [])
        self.assertIn("names no task criterion", result["structural_blockers"][0]["detail"])
        fixture.lens("contract", t001_fails=["Returns a - b: for integers for floats — found: a + b, parser.py:9"])
        _, result = fixture.run()
        self.assertEqual(result["structural_blockers"], [])
        self.assertEqual(result["groups"][0]["locator"], "t001_ac2")


class FixBatchTest(unittest.TestCase):
    def test_a_declared_directory_and_a_path_below_it_share_a_fix_batch(self):
        # Fix batches become same-wave repair tasks, so they follow the plan overlap rule.
        groups = [{"locator": f"t00{n}_ac1", "tasks": [f"T00{n}"]} for n in (1, 2, 3)]
        tasks = {
            "T001": {"files": ["fixtures/set"]},
            "T002": {"files": ["fixtures/set/a.json"]},
            "T003": {"files": ["fixtures/setx.json"]},
        }
        self.assertEqual(
            review_findings._fix_batches(groups, tasks),
            [
                {"locators": ["t001_ac1", "t002_ac1"],
                 "files": ["fixtures/set", "fixtures/set/a.json"]},
                {"locators": ["t003_ac1"], "files": ["fixtures/setx.json"]},
            ],
        )


class SkepticSelectionTest(unittest.TestCase):
    def test_skeptics_off_sends_groups_to_fix(self):
        fixture = Fixture(self, skeptics="off")
        fixture.lens("contract", t001_fails=["Parses CSV rows — found: no, parser.py:1"])
        fixture.lens("adversarial", verdict="pass")
        _, result = fixture.run()
        self.assertFalse(result["skeptics_active"])
        self.assertEqual(result["skeptic_groups"], [])
        self.assertEqual(result["fix_groups"], ["t001_ac1"])
        self.assertEqual(result["fix_batches"], [{"locators": ["t001_ac1"], "files": ["src/parser.py"]}])

    def test_skeptics_on_but_full_depth_stays_off(self):
        fixture = Fixture(self, depth="full")
        fixture.lens("canonical", depth="full", t001_fails=["Parses CSV rows — found: no, parser.py:1"])
        _, result = fixture.run()
        self.assertFalse(result["skeptics_active"])
        self.assertEqual(result["fix_groups"], ["t001_ac1"])

    def test_current_cycle_skeptic_verdicts(self):
        fixture = Fixture(self)
        obs_a = "Parses CSV rows — found: no, parser.py:1 fix: correct it"
        obs_b = "Rejects malformed rows — found: accepts them, parser.py:9 fix: correct it"
        fixture.lens("contract", t001_fails=[obs_a.replace(" fix: correct it", ""),
                                              obs_b.replace(" fix: correct it", "")])
        fixture.lens("adversarial", verdict="pass")
        fixture.skeptic("t001_ac1", 1, "refuted", [obs_a])
        fixture.skeptic("t001_ac2", 1, "stands", [obs_b])
        _, result = fixture.run()
        self.assertEqual(result["refuted_groups"], ["t001_ac1"])
        self.assertEqual(result["fix_groups"], ["t001_ac2"])
        self.assertEqual(result["skeptic_groups"], [])
        self.assertFalse(result["all_refuted"])

    def test_partial_refutation_cannot_hide_an_unexamined_observation(self):
        fixture = Fixture(self)
        first = "Parses CSV rows — found: no, parser.py:1 fix: correct it"
        second = "Parses CSV rows — found: drops rows, parser.py:9 fix: correct it"
        fixture.lens("contract", t001_fails=[first.replace(" fix: correct it", "")])
        fixture.lens("adversarial", t001_fails=[second.replace(" fix: correct it", "")])
        fixture.skeptic("t001_ac1", 1, "refuted", [first])
        _, result = fixture.run()
        self.assertEqual(result["refuted_groups"], [])
        self.assertEqual(result["skeptic_groups"], ["t001_ac1"])
        self.assertFalse(result["all_refuted"])
        self.assertEqual(result["structural_blockers"][0]["kind"], "invalid-skeptic")
        fixture.skeptic("t001_ac1", 1, "refuted", [first, second])
        _, result = fixture.run()
        self.assertTrue(result["all_refuted"], result)

    def test_invalid_observation_numbers_cannot_refute_a_group(self):
        fixture = Fixture(self)
        first = "Parses CSV rows — found: no, parser.py:1 fix: correct it"
        second = "Parses CSV rows — found: drops rows, parser.py:9 fix: correct it"
        fixture.lens("contract", t001_fails=[first.replace(" fix: correct it", "")])
        fixture.lens("adversarial", t001_fails=[second.replace(" fix: correct it", "")])
        path = fixture.project / "review/wave-1.cycle1.skeptic-t001_ac1.md"
        valid = skeptic_text("t001_ac1", 1, "refuted", [first, second])
        for label, old, new in (
            ("duplicate observation", "Observation 2 —", "Observation 1 —"),
            ("duplicate verdict", "Observation 2: refuted", "Observation 1: refuted"),
            ("unknown verdict", "Observation 2: refuted", "Observation 3: refuted"),
            ("missing verdict", "### Observation 2: refuted", ""),
            ("aliased observation", "Observation 2 —", "Observation 01 —"),
            ("aliased verdict", "Observation 2: refuted", "Observation 01: refuted"),
        ):
            with self.subTest(label=label):
                write(path, valid.replace(old, new))
                with self.assertRaises(review_findings.ReviewFindingsError):
                    review_findings.parse_skeptic(path, 1)
                _, result = fixture.run()
                self.assertEqual(result["refuted_groups"], [])
                self.assertEqual(result["skeptic_groups"], ["t001_ac1"])
                self.assertFalse(result["all_refuted"])
                self.assertEqual(result["structural_blockers"][0]["kind"], "invalid-skeptic")

    def test_verdicts_match_observation_numbers_independent_of_order(self):
        fixture = Fixture(self)
        path = fixture.project / "review/wave-1.cycle1.skeptic-t001_ac1.md"
        text = skeptic_text("t001_ac1", 1, "refuted", ["first observation", "second observation"])
        text = text.replace("Observation 1: refuted", "Observation 02: refuted")
        text = text.replace("Observation 2: refuted", "Observation 1: refuted")
        write(path, text)
        parsed = review_findings.parse_skeptic(path, 1)
        self.assertEqual(parsed["verdict"], "refuted")
        self.assertEqual(parsed["observations"], ["first observation", "second observation"])

    def test_all_refuted_branch(self):
        fixture = Fixture(self)
        obs = "Parses CSV rows — found: no, parser.py:1 fix: correct it"
        fixture.lens("contract", t001_fails=[obs.replace(" fix: correct it", "")])
        fixture.lens("adversarial", verdict="pass")
        fixture.skeptic("t001_ac1", 1, "refuted", [obs])
        _, result = fixture.run()
        self.assertTrue(result["all_refuted"])


class CarryForwardTest(unittest.TestCase):
    def test_earlier_refutation_with_unchanged_evidence_is_carried(self):
        fixture = Fixture(self)
        obs = "Parses CSV rows — found: no, parser.py:1 fix: correct it"
        fixture.lens("contract", cycle=1, t001_fails=[obs.replace(" fix: correct it", "")])
        fixture.lens("adversarial", cycle=1, verdict="pass")
        fixture.skeptic("t001_ac1", 1, "refuted", [obs])
        fixture.lens("contract", cycle=2, t001_fails=[obs.replace(" fix: correct it", "")])
        fixture.lens("adversarial", cycle=2, verdict="pass")
        _, result = fixture.run(cycle=2)
        (group,) = result["groups"]
        self.assertEqual(group["disposition"], "refuted-earlier")
        self.assertEqual(group["skeptic"]["cycle"], 1)
        self.assertTrue(group["repeat"])
        self.assertEqual(result["refuted_groups"], ["t001_ac1"])
        self.assertEqual(result["skeptic_groups"], [])
        self.assertEqual(result["carried_refutations"][0]["locator"], "t001_ac1")

    def test_earlier_refutation_with_new_evidence_goes_to_fix_without_skeptic(self):
        fixture = Fixture(self)
        old = "Parses CSV rows — found: no, parser.py:1 fix: correct it"
        fixture.lens("contract", cycle=1, t001_fails=[old.replace(" fix: correct it", "")])
        fixture.lens("adversarial", cycle=1, verdict="pass")
        fixture.skeptic("t001_ac1", 1, "refuted", [old])
        fixture.lens("contract", cycle=2, t001_fails=["Parses CSV rows — found: drops header, parser.py:4"])
        fixture.lens("adversarial", cycle=2, verdict="pass")
        _, result = fixture.run(cycle=2)
        (group,) = result["groups"]
        self.assertEqual(group["disposition"], "fix")
        self.assertEqual(len(group["new_evidence"]), 1)
        self.assertEqual(result["skeptic_groups"], [])
        self.assertEqual(result["fix_groups"], ["t001_ac1"])


class StructuralTest(unittest.TestCase):
    def test_missing_lens_and_helper_failure_bypass_skeptics(self):
        fixture = Fixture(self)
        fixture.lens("contract", t001_fails=["Parses CSV rows — found: no, parser.py:1"])
        _, result = fixture.run(extra=["--helper-failure", "check_handoffs.py wave exit 2: Depth mismatch"])
        kinds = sorted(item["kind"] for item in result["structural_blockers"])
        self.assertEqual(kinds, ["helper-failure", "missing-artifact"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["all_refuted"])
        self.assertEqual(result["skeptic_groups"], ["t001_ac1"])

    def test_invalid_skeptic_file_is_structural_not_refuted(self):
        fixture = Fixture(self)
        fixture.lens("contract", t001_fails=["Parses CSV rows — found: no, parser.py:1"])
        fixture.lens("adversarial", verdict="pass")
        write(fixture.project / "review" / "wave-1.cycle1.skeptic-t001_ac1.md",
              "# Skeptic\n\n- Criterion locator: t001_ac1\n\n## Verdict\n\nmaybe\n")
        _, result = fixture.run()
        self.assertEqual(result["structural_blockers"][0]["kind"], "invalid-skeptic")
        self.assertEqual(result["skeptic_groups"], ["t001_ac1"])
        self.assertEqual(result["refuted_groups"], [])


class CapAndConfigTest(unittest.TestCase):
    def test_cap_reached_at_max_review_cycles(self):
        fixture = Fixture(self, cycles=2)
        fixture.lens("contract", cycle=2, t001_fails=["Parses CSV rows — found: no, parser.py:1"])
        fixture.lens("adversarial", cycle=2, verdict="pass")
        _, result = fixture.run(cycle=2)
        self.assertTrue(result["cap_reached"])
        self.assertEqual(result["config"]["max_review_cycles"], 2)
        _, earlier = Fixture(self, cycles=2).run(cycle=1)
        self.assertFalse(earlier["cap_reached"])

    def test_config_typo_is_rejected_by_key(self):
        fixture = Fixture(self)
        plan = fixture.project / "plan" / "PLAN.md"
        plan.write_bytes(plan.read_text(encoding="utf-8").replace("finding_skeptics: on", "finding_sceptics: on").encode("utf-8"))
        code, result = fixture.run()
        self.assertEqual(code, 2)
        self.assertIn("finding_sceptics", result["error"])

    def test_config_bad_values_name_the_key(self):
        for key, bad in (("max_review_cycles", "three"), ("finding_skeptics", "yes"), ("wave_budget", "")):
            with self.subTest(key=key):
                text = PLAN.format(cycles=3, skeptics="on", depth="deep")
                text = text.replace(
                    {"max_review_cycles": "max_review_cycles: 3", "finding_skeptics": "finding_skeptics: on",
                     "wave_budget": "wave_budget: none"}[key],
                    f"{key}: {bad}",
                )
                with self.assertRaises(review_findings.ReviewFindingsError) as raised:
                    review_findings.parse_config(text)
                self.assertIn(key, str(raised.exception))

    def test_config_defaults_when_keys_absent(self):
        config = review_findings.parse_config("## Config\n\n- review_panel: off\n\n## Wave 1 — x\n")
        self.assertEqual(config, {"max_review_cycles": 3, "finding_skeptics": "off", "wave_budget": "none"})


A_BASE, B_BASE = "a" * 40, "b" * 40
PARSES = "Parses CSV rows — found: {}, parser.py:1"
REJECTS = "Rejects malformed rows — found: {}, parser.py:9"


class RecoveryHistoryTest(unittest.TestCase):
    """A build recovery moves review/ aside and restarts at cycle 1; the budget and the criterion history stay."""

    def fixture(self, cycles=3, skeptics="off", depth="full"):
        return Fixture(self, cycles=cycles, skeptics=skeptics, depth=depth)

    def full(self, fixture, directory, cycle, **kwargs):
        write(directory / f"wave-1.cycle{cycle}.md", lens_text(cycle=cycle, depth="full", lens="canonical", **kwargs))

    def backup(self, fixture, base):
        return fixture.project / "plan" / f"build-recovery-{base}" / "review"

    def state(self, fixture, *events, commit=True):
        write(fixture.project / "STATE.md",
              "# Project State\n\n## Log\n\n" + "".join(f"- 2026-10-21 — build — {event}\n" for event in events))
        if commit:
            def git(*args):
                subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
                               cwd=fixture.root, check=True, capture_output=True)
            if not (fixture.root / ".git").is_dir():
                git("init", "-q")
            git("add", ".project/STATE.md")
            git("commit", "-qm", "record ruling", "--allow-empty")

    @staticmethod
    def recovery(base):
        return 'build recovery: {"base": "%s", "branch": "feature/milestone-001", "kind": "define", "source": "build"}' % base

    def test_blocked_cycles_a_recovery_set_aside_count_toward_the_cap(self):
        fixture = self.fixture(cycles=3)
        self.full(fixture, self.backup(fixture, A_BASE), 1, t001_fails=[PARSES.format("no")])
        self.full(fixture, self.backup(fixture, A_BASE), 2, verdict="pass")
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("still no")])
        _, result = fixture.run()
        self.assertEqual((result["history"]["prior_blocked"], result["history"]["effective_cycle"]), (1, 2))
        self.assertFalse(result["cap_reached"])
        self.full(fixture, self.backup(fixture, B_BASE), 1, t001_fails=[REJECTS.format("accepts them")])
        # STATE.md records B before A, so the history lists B first.
        self.state(fixture, self.recovery(B_BASE), self.recovery(A_BASE))
        code, result = fixture.run()
        self.assertEqual(code, 0, result)
        self.assertTrue(result["cap_reached"])
        self.assertEqual(result["history"], {"prior_blocked": 2, "effective_cycle": 3, "cycles": [
            {"recovery": B_BASE, "cycle": 1, "verdict": "blocked", "locators": ["t001_ac2"]},
            {"recovery": A_BASE, "cycle": 1, "verdict": "blocked", "locators": ["t001_ac1"]},
            {"recovery": A_BASE, "cycle": 2, "verdict": "pass", "locators": []},
        ]})

    def git(self, fixture, *args):
        return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
                              cwd=fixture.root, check=True, capture_output=True, text=True).stdout.strip()

    def test_a_restored_review_counts_once(self):
        fixture = self.fixture(cycles=5)
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("no")])
        self.git(fixture, "init", "-q")
        self.git(fixture, "add", ".")
        self.git(fixture, "commit", "-qm", "publish original review")
        first = self.git(fixture, "rev-parse", "HEAD")
        self.git(fixture, "commit", "--allow-empty", "-qm", "restore unchanged review without republishing")
        second = self.git(fixture, "rev-parse", "HEAD")
        for base in (first, second):
            self.full(fixture, self.backup(fixture, base), 1, t001_fails=[PARSES.format("no")])
        self.full(fixture, fixture.project / "review", 2, t001_fails=[PARSES.format("still no")])
        _, result = fixture.run(cycle=2)
        self.assertEqual(result["history"], {"prior_blocked": 0, "effective_cycle": 2, "cycles": [
            {"recovery": None, "cycle": 1, "verdict": "blocked", "locators": ["t001_ac1"]}]})
        shutil.rmtree(fixture.project / "review")
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("still no")])
        self.assertEqual(fixture.run()[1]["history"]["prior_blocked"], 1)

    def test_identical_independently_published_cycles_still_reach_both_gates(self):
        fixture = self.fixture(cycles=3)
        self.git(fixture, "init", "-q")
        for index in range(2):
            self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("no")])
            self.git(fixture, "add", ".")
            self.git(fixture, "commit", "-qm", f"independent review {index}")
            base = self.git(fixture, "rev-parse", "HEAD")
            self.full(fixture, self.backup(fixture, base), 1, t001_fails=[PARSES.format("no")])
            shutil.rmtree(fixture.project / "review")
            self.git(fixture, "add", ".")
            self.git(fixture, "commit", "-qm", "recovery resets changed wave")
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("no")])
        _, result = fixture.run()
        self.assertEqual(result["history"]["effective_cycle"], 3)
        self.assertTrue(result["cap_reached"])
        self.assertTrue(result["redesign_pending"])

    def test_missing_historical_deep_lens_stays_blocked(self):
        fixture = self.fixture(cycles=2)
        write(self.backup(fixture, A_BASE) / "wave-1.cycle1.contract.md", lens_text(verdict="pass"))
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("no")])
        _, result = fixture.run()
        self.assertTrue(result["cap_reached"])
        self.assertEqual(result["history"]["cycles"][0]["verdict"], "blocked")

    def test_no_backup_keeps_the_cycle_number_as_the_cap_count(self):
        fixture = self.fixture(cycles=2)
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("no")])
        self.full(fixture, fixture.project / "review", 2, t001_fails=[PARSES.format("still no")])
        _, result = fixture.run(cycle=2)
        self.assertEqual((result["history"]["prior_blocked"], result["cap_reached"]), (0, True))
        self.assertEqual(result["redesign"], [])
        self.assertFalse(result["redesign_pending"])

    def criterion_blocked_three_times(self, cycles=5):
        """t001_ac1 blocked before the recovery and in cycles 1 and 2 after it; t001_ac2 blocks only now."""
        fixture = self.fixture(cycles=cycles)
        self.full(fixture, self.backup(fixture, A_BASE), 1, t001_fails=[PARSES.format("no")])
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("still no")])
        self.full(fixture, fixture.project / "review", 2,
                  t001_fails=[PARSES.format("not yet"), REJECTS.format("accepts them")])
        return fixture

    def test_a_criterion_that_blocked_two_earlier_cycles_asks_for_a_redesign_ruling(self):
        fixture = self.criterion_blocked_three_times()
        _, first = fixture.run(cycle=1)
        self.assertEqual((first["redesign"], first["redesign_pending"]), ([], False))
        code, result = fixture.run(cycle=2)
        self.assertEqual(code, 0, result)
        self.assertFalse(result["cap_reached"])
        self.assertEqual(result["fix_groups"], ["t001_ac1", "t001_ac2"])
        self.assertEqual(result["redesign"], [{"locator": "t001_ac1", "criterion": "Parses CSV rows", "blocked_before": [
            {"recovery": A_BASE, "cycle": 1}, {"recovery": None, "cycle": 1}]}])
        self.assertEqual((result["redesign_ruling"], result["redesign_pending"]), (None, True))

    def test_only_the_open_ruling_of_this_cycle_since_the_last_recovery_releases_fix_tasks(self):
        fixture = self.criterion_blocked_three_times()
        open_ruling = "wave 1 cycle 2 redesign ruling: Open fix tasks anyway (recommended — the counterexamples are real)"
        for events, ruling, pending in (
            (["wave 1 cycle 2 redesign ruling: Reconsider the component approach"],
             "Reconsider the component approach", True),
            (["wave 1 cycle 1 redesign ruling: Open fix tasks anyway"], None, True),
            ([open_ruling, self.recovery(A_BASE)], None, True),
            ([self.recovery(A_BASE), "wave 1 cycle 2 redesign ruling: Reconsider the component approach", open_ruling],
             open_ruling.split(": ", 1)[1], False),
        ):
            with self.subTest(events=events):
                self.state(fixture, *events)
                _, result = fixture.run(cycle=2)
                self.assertEqual((result["redesign_ruling"], result["redesign_pending"]), (ruling, pending))
                self.assertEqual(len(result["redesign"]), 1)
        self.state(fixture, self.recovery(A_BASE), open_ruling, commit=False)
        # The same choice was the last committed fixture: change it before the pending-ruling assertion.
        self.state(fixture, self.recovery(A_BASE), "wave 1 cycle 2 redesign ruling: Reconsider the component approach")
        self.state(fixture, self.recovery(A_BASE), open_ruling, commit=False)
        _, result = fixture.run(cycle=2)
        self.assertEqual((result["redesign_ruling"], result["redesign_pending"]), (None, True))
        self.state(fixture, self.recovery(A_BASE), "wave 1 cycle 2 redesign ruling: Open fix tasks anyway-never")
        self.assertTrue(fixture.run(cycle=2)[1]["redesign_pending"])

    def test_a_group_that_is_not_a_fix_group_never_asks_for_a_redesign(self):
        fixture = self.fixture(cycles=5, skeptics="on", depth="deep")
        observation = PARSES.format("not yet")
        for directory, cycle, text in ((self.backup(fixture, A_BASE), 1, PARSES.format("no")),
                                       (fixture.project / "review", 1, PARSES.format("still no")),
                                       (fixture.project / "review", 2, observation)):
            write(directory / f"wave-1.cycle{cycle}.contract.md", lens_text(cycle=cycle, t001_fails=[text]))
            write(directory / f"wave-1.cycle{cycle}.adversarial.md",
                  lens_text(cycle=cycle, lens="adversarial", verdict="pass"))
        _, waiting = fixture.run(cycle=2)
        self.assertEqual((waiting["skeptic_groups"], waiting["redesign"]), (["t001_ac1"], []))
        self.assertEqual([entry["locators"] for entry in waiting["history"]["cycles"]], [["t001_ac1"], ["t001_ac1"]])
        fixture.skeptic("t001_ac1", 2, "refuted", [observation + " fix: correct it"])
        _, refuted = fixture.run(cycle=2)
        self.assertEqual((refuted["refuted_groups"], refuted["redesign"], refuted["redesign_pending"]),
                         (["t001_ac1"], [], False))
        self.assertTrue(refuted["all_refuted"])


def lens_event(gate, cycle=1, fired=()):
    """A decision lens event for wave 1: ``fired`` lenses with their verdicts, every other lens quiet."""
    verdicts = dict(fired)
    return _common.format_lens_event(gate, _common.lens_scope_wave(1, cycle), fired=list(fired),
                                     no_signal=[lens for lens in _common.LENS_GATES[gate] if lens not in verdicts])


FIRE = lens_event("fix-routing", fired=[("L12", "redesign")])


class LensRedesignTest(unittest.TestCase):
    """M8: a recorded fix-routing lens that recommends redesign holds fix tasks for the redesign ruling."""

    fixture = RecoveryHistoryTest.fixture
    full = RecoveryHistoryTest.full
    backup = RecoveryHistoryTest.backup
    state = RecoveryHistoryTest.state
    recovery = staticmethod(RecoveryHistoryTest.recovery)
    criterion_blocked_three_times = RecoveryHistoryTest.criterion_blocked_three_times

    def blocked_once(self):
        """Cycle 1 blocks t001_ac1 for the first time: a fix group without a recurrence-derived redesign."""
        fixture = self.fixture(cycles=5)
        self.full(fixture, fixture.project / "review", 1, t001_fails=[PARSES.format("no")])
        return fixture

    def test_a_recorded_redesign_or_fail_closed_fire_asks_even_without_recurrence(self):
        fixture = self.blocked_once()
        _, before = fixture.run()
        self.assertEqual((before["fix_groups"], before["redesign"], before["lens_redesign"],
                          before["lens_redesign_pending"]), (["t001_ac1"], [], [], False))
        for fired, pending in (
            ((), False),
            ((("L12", "keep"), ("L3", "narrow")), False),
            ((("L12", "redesign"),), True),
            ((("L3", "fail-closed"), ("L6", "add-option")), True),
        ):
            with self.subTest(fired=fired):
                self.state(fixture, "build started", lens_event("fix-routing", fired=fired))
                code, result = fixture.run()
                self.assertEqual(code, 0, result)
                self.assertEqual((result["redesign"], result["redesign_pending"]), ([], False))
                self.assertEqual(result["lens_redesign_pending"], pending)
                self.assertEqual(result["lens_redesign"], [{"lens": lens, "verdict": verdict} for lens, verdict in fired
                                                           if verdict in ("redesign", "fail-closed")])
        # The working file counts before its checkpoint, and a committed fire still counts once dropped from it.
        self.state(fixture, "build started")
        self.state(fixture, "build started", FIRE, commit=False)
        self.assertTrue(fixture.run()[1]["lens_redesign_pending"])
        self.state(fixture, "build started", FIRE)
        self.state(fixture, "build started", commit=False)
        self.assertTrue(fixture.run()[1]["lens_redesign_pending"])

    def test_only_a_committed_open_ruling_after_the_latest_fire_releases_fix_tasks(self):
        fixture = self.blocked_once()
        open_ruling = "wave 1 cycle 1 redesign ruling: Open fix tasks anyway"
        reconsider = "wave 1 cycle 1 redesign ruling: Reconsider the component approach"
        quiet = lens_event("fix-routing")
        for name, events, pending in (
            ("open", (FIRE, open_ruling), False),
            ("open with a recommendation", (FIRE, open_ruling + " (recommended — the counterexamples are real)"), False),
            ("open, then a quiet lens", (FIRE, open_ruling, quiet), False),
            ("fire before the latest recovery", (FIRE, self.recovery(A_BASE)), False),
            ("fire in an earlier milestone", (FIRE, "archive preflight passed; shipment recorded"), False),
            ("fire for another cycle", (lens_event("fix-routing", cycle=2, fired=[("L12", "redesign")]),), False),
            ("fire at another gate", (lens_event("redesign", fired=[("L12", "redesign")]),), False),
            ("reconsider", (FIRE, reconsider), True),
            ("missing", (FIRE,), True),
            ("a later quiet lens does not withdraw the fire", (FIRE, quiet), True),
            ("open for another cycle", (FIRE, "wave 1 cycle 2 redesign ruling: Open fix tasks anyway"), True),
            ("open before the fire", (open_ruling, FIRE), True),
            ("open before the latest fire", (FIRE, open_ruling, FIRE), True),
            ("open before the latest recovery", (FIRE, open_ruling, self.recovery(A_BASE), FIRE), True),
            ("not the open choice", (FIRE, open_ruling + "-never"), True),
        ):
            with self.subTest(name):
                self.state(fixture, *events)
                self.assertEqual(fixture.run()[1]["lens_redesign_pending"], pending)
        for committed, working in (((FIRE,), (FIRE, open_ruling)),
                                   ((FIRE, reconsider), (FIRE, open_ruling)),
                                   ((FIRE, open_ruling), (FIRE, reconsider)),
                                   ((FIRE, open_ruling), (FIRE, open_ruling, FIRE))):
            with self.subTest(committed=committed, working=working):
                self.state(fixture, *committed)
                self.state(fixture, *working, commit=False)
                self.assertTrue(fixture.run()[1]["lens_redesign_pending"])
        self.state(fixture, FIRE, open_ruling)
        self.assertEqual([fixture.run()[1]["lens_redesign_pending"] for _ in range(2)], [False, False])

    def test_lens_events_leave_the_redesign_ruling_and_recovery_history_unchanged(self):
        fixture = self.criterion_blocked_three_times()
        open_ruling = "wave 1 cycle 2 redesign ruling: Open fix tasks anyway"
        self.state(fixture, self.recovery(A_BASE), open_ruling)
        _, plain = fixture.run(cycle=2)
        self.state(fixture, lens_event("recovery", cycle=1), self.recovery(A_BASE), lens_event("redesign", cycle=2),
                   open_ruling, lens_event("fix-routing", cycle=2, fired=[("L12", "redesign")]),
                   lens_event("cap", cycle=2, fired=[("L16", "fail-closed")]))
        _, lensed = fixture.run(cycle=2)
        self.assertEqual(review_findings._recovery_bases(fixture.project), [A_BASE])
        self.assertEqual(review_findings.redesign_ruling(fixture.root, fixture.project, 1, 2), "Open fix tasks anyway")
        for key in ("history", "cap_reached", "redesign", "redesign_ruling", "redesign_pending", "fix_batches"):
            self.assertEqual(lensed[key], plain[key], key)
        self.assertFalse(lensed["redesign_pending"])
        # The fix-routing fire after that ruling asks the redesign question again on its own.
        self.assertEqual((plain["lens_redesign_pending"], lensed["lens_redesign_pending"]), (False, True))


class FixBatchRepoTest(unittest.TestCase):
    """SC1 fails for its owners T001 and T002, and T001 also fails its own criterion."""

    def batches(self, t001_repo, t002_repo, t002_files):
        fixture = Fixture(self, skeptics="off")
        for name, task_id, files, repo in (("T001-parse-input.md", "T001", "src/parser.py", t001_repo),
                                           ("T002-render-output.md", "T002", t002_files, t002_repo)):
            text = TASK.format(id=task_id, title="Task", files=f"  - {files}", interface="None", owned="SC1",
                               ac1="Parses CSV rows", ac2="Rejects malformed rows")
            write(fixture.project / "tasks" / name,
                  text.replace("files:\n", f"repo: {repo}\nfiles:\n", 1) if repo else text)
        fixture.lens("contract", t001_fails=["Parses CSV rows — found: no, parser.py:1"],
                     sc1_fails=["Parser accepts CSV — found: TSV only, parser.py:3"])
        fixture.lens("adversarial", verdict="pass")
        code, result = fixture.run()
        self.assertEqual(code, 0, result)
        self.assertEqual({group["locator"]: group["tasks"] for group in result["groups"]},
                         {"sc1": ["T001", "T002"], "t001_ac1": ["T001"]})
        return result["fix_batches"]

    def test_same_relative_path_in_two_repos_gives_one_batch_per_repo(self):
        self.assertEqual(self.batches(None, "web", "src/parser.py"), [
            {"locators": ["sc1"], "files": ["src/parser.py"], "repo": "web"},
            {"locators": ["sc1", "t001_ac1"], "files": ["src/parser.py"]},
        ])

    def test_file_overlap_merges_batches_inside_a_member(self):
        self.assertEqual(self.batches("web", '"web"', "src/render.py"), [
            {"locators": ["sc1", "t001_ac1"], "files": ["src/parser.py", "src/render.py"], "repo": "web"},
        ])

    def test_coordinator_batches_keep_their_shape(self):
        self.assertEqual(self.batches(None, None, "src/render.py"), [
            {"locators": ["sc1", "t001_ac1"], "files": ["src/parser.py", "src/render.py"]},
        ])


if __name__ == "__main__":
    unittest.main()
