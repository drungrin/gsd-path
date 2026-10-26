#!/usr/bin/env python3
# gsd-path project runtime
"""Compute the wave review finding sets for build step 7 without model reasoning.

The orchestrator names the wave and cycle. This helper reads PLAN.md Config
(`max_review_cycles`, `finding_skeptics`, `wave_budget`), the cycle's lens or
canonical review files, and every skeptic file for this wave. It groups
blocking findings by canonical criterion locator (`t001_ac2`, `sc3`,
`t001_files`, `t001_interface_contract`), collapses true duplicate
observations, selects the groups that need a skeptic, carries refutations
forward across cycles, batches fix-task groups by repo and disjoint file
scope, keeps structural blockers out of skeptic accounting, and reports
whether the cycle cap is reached. Blocked cycles that a build recovery set
aside still count toward that cap, and a criterion that keeps blocking, or that
two completed repairs already carried, asks for a redesign ruling before
another fix task, as does a recorded fix-routing decision lens that recommends
redesign or fail-closed. `panel-repairs` reports the same repair count for the
actionable findings of a panel on a passing cycle. It never decides the wave
verdict.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import posixpath
import re
import sys

# Runtime helpers must not modify their immutable installation.
sys.dont_write_bytecode = True
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

try:
    from . import _common
except ImportError:  # installed standalone helper
    import _common

DEFAULT_PROJECT_DIR = ".project"
DEFAULT_MAX_REVIEW_CYCLES = 3
CONFIG_KEYS = ("max_review_cycles", "wave_budget", "review_panel", "finding_skeptics")
CONFIG_LINE = re.compile(r"^-\s*(?P<key>[A-Za-z0-9_-]+):\s*(?P<value>.*?)\s*$")
COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
PLAN_WAVE_HEADING = re.compile(r"(?m)^## Wave (?P<wave>\d+) — (?P<title>.+)$")
TASK_FILE_NAME = re.compile(r"^(?P<id>T\d{3})-[a-z0-9][a-z0-9-]*\.md$")
WAVE_TASK_HEADING = re.compile(
    r"^## (?P<task>T\d{3}) — (?P<title>.+): (?P<verdict>pass|fail)$"
)
WAVE_SC_HEADING = re.compile(r"^### (?P<sc>SC[1-9]\d*) — (?P<rest>.+)$")
NUMBERED_ITEM = re.compile(r"^(\d+)\.\s+(\S.*)$")
SKEPTIC_FILE_NAME = re.compile(
    r"^wave-(?P<wave>[1-9]\d*)\.cycle(?P<cycle>[1-9]\d*)\.skeptic-(?P<locator>[a-z0-9_]+)\.md$"
)
SKEPTIC_OBSERVATION = re.compile(r"^### Observation (?P<n>\d+) — (?P<lens>contract|adversarial|both)$")
SKEPTIC_OBSERVATION_VERDICT = re.compile(r"^### Observation (?P<n>\d+): (?P<verdict>refuted|stands)$")
FAIL_MARKER = "- ❌ "
REVIEW_CYCLE_FILE = re.compile(
    r"^wave-(?P<wave>[1-9]\d*)\.cycle(?P<cycle>[1-9]\d*)(?:\.(?P<lens>contract|adversarial))?\.md$"
)
RECOVERY_BACKUP = re.compile(r"^build-recovery-(?P<base>[0-9a-f]{40})$")
RECOVERY_RECORD = "build recovery: "
# A fix group needs a redesign ruling when its criterion already blocked this many earlier cycles of the wave.
REDESIGN_AFTER_BLOCKS = 2
# The same holds when this many completed repair tasks already carried the criterion.
REDESIGN_AFTER_REPAIRS = REDESIGN_AFTER_BLOCKS
REDESIGN_OPEN = "Open fix tasks anyway"
RECONSIDER = "Reconsider the component approach"
CRITERION_REFERENCE = re.compile(
    r"\b[Tt](?P<task>\d{3}) acceptance (?P<ac>[1-9]\d*)\b"
    r"|\b[Tt](?P<ltask>\d{3})_[Aa][Cc](?P<lac>[1-9]\d*)\b"
    r"|\b[Ss][Cc](?P<sc>[1-9]\d*)\b"
)
WAVE_CYCLE_PAIR = re.compile(r"\bwave ([1-9]\d*) cycle ([1-9]\d*)\b", re.IGNORECASE)
PANEL_ITEM = re.compile(r"- (?P<severity>high|medium|low) / (?P<kind>[a-z-]+) — (?P<text>.+)")
BARE_REFERENCE = re.compile(
    r"(?:[Tt]\d{3} acceptance [1-9]\d*|[Tt]\d{3}_[Aa][Cc][1-9]\d*|[Ss][Cc][1-9]\d*)[.:]?"
)
PANEL_OPEN = "Open fix tasks from panel findings"
RECONSIDER_RULING = re.compile(
    r"^wave (?P<wave>\d+) cycle (?P<cycle>\d+) (?:redesign|panel) ruling: Reconsider the component approach\b")
RECONSIDERED_EVENT = re.compile(r"^wave (?P<wave>\d+) cycle (?P<cycle>\d+) reconsidered: (?P<criteria>\[.*\])$")


class ReviewFindingsError(Exception):
    """Raised when an input cannot be read as the templates define it."""


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _strip_comments(text: str) -> str:
    return COMMENT_PATTERN.sub("", text)


def _section(text: str, heading: str) -> Optional[str]:
    match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", text)
    if not match:
        return None
    rest = text[match.end() :]
    following = re.search(r"(?m)^## ", rest)
    return rest[: following.start()] if following else rest


def _read(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewFindingsError(f"{label} is not readable: {path} ({error.strerror})")


# --- PLAN.md ---------------------------------------------------------------


def parse_config(plan_text: str) -> Dict[str, object]:
    """Read and validate the PLAN.md Config keys this helper owns."""

    section = _section(_strip_comments(plan_text), "Config")
    if section is None:
        raise ReviewFindingsError("PLAN.md has no ## Config section")
    seen: Dict[str, str] = {}
    for line in section.splitlines():
        match = CONFIG_LINE.match(line.strip())
        if not match:
            continue
        key, value = match.group("key"), match.group("value")
        if key not in CONFIG_KEYS:
            raise ReviewFindingsError(
                f"PLAN.md Config key `{key}` is unknown; expected one of {', '.join(CONFIG_KEYS)}"
            )
        if key in seen:
            raise ReviewFindingsError(f"PLAN.md Config repeats `{key}`")
        seen[key] = value
    cycles_raw = seen.get("max_review_cycles", str(DEFAULT_MAX_REVIEW_CYCLES))
    if not cycles_raw.isdigit() or int(cycles_raw) < 1:
        raise ReviewFindingsError(
            f"PLAN.md Config `max_review_cycles` must be a positive integer, got `{cycles_raw}`"
        )
    skeptics = seen.get("finding_skeptics", "off")
    if skeptics not in {"off", "on"}:
        raise ReviewFindingsError(
            f"PLAN.md Config `finding_skeptics` must be `off` or `on`, got `{skeptics}`"
        )
    budget = seen.get("wave_budget", "none")
    if not budget:
        raise ReviewFindingsError("PLAN.md Config `wave_budget` must be `none` or a resource cap")
    return {
        "max_review_cycles": int(cycles_raw),
        "finding_skeptics": skeptics,
        "wave_budget": budget,
    }


def wave_depth(plan_text: str, wave: int) -> str:
    headings = list(PLAN_WAVE_HEADING.finditer(plan_text))
    for index, heading in enumerate(headings):
        if int(heading.group("wave")) != wave:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(plan_text)
        depth = re.search(r"(?m)^Review depth:\s*(\S+)", plan_text[heading.end() : end])
        if not depth or depth.group(1) not in {"full", "deep", "verify-only"}:
            raise ReviewFindingsError(f"PLAN.md Wave {wave} has an invalid Review depth")
        return depth.group(1)
    raise ReviewFindingsError(f"PLAN.md has no Wave {wave}")


# --- task files ------------------------------------------------------------


def _frontmatter_list(frontmatter: str, key: str) -> List[str]:
    inline = re.search(rf"(?m)^{key}:\s*\[(?P<body>.*)\]\s*(?:#.*)?$", frontmatter)
    if inline:
        return [part.strip().strip("\"'") for part in inline.group("body").split(",") if part.strip()]
    block = re.search(rf"(?m)^{key}:\s*(?:#.*)?$\n(?P<items>(?:[ \t]+-\s+.*\n?)*)", frontmatter)
    if not block:
        return []
    return [
        _normalize(re.sub(r"\s+#.*$", "", item.strip()[1:])).strip("\"'")
        for item in block.group("items").splitlines()
        if item.strip().startswith("-")
    ]


def _numbered_items(section: Optional[str]) -> List[str]:
    """Numbered criteria; each also carries its text without sub-bullet markers (issue #392)."""
    if section is None:
        return []
    bare = _numbered_texts(_common.without_sub_bullet_markers(section))
    return [_common.Criterion(text, other) for text, other in zip(_numbered_texts(section), bare)]


def _numbered_texts(section: str) -> List[str]:
    items: List[str] = []
    open_item = False
    for line in _strip_comments(section).splitlines():
        match = NUMBERED_ITEM.match(line.strip())
        if match:
            items.append(_normalize(match.group(2)))
            open_item = True
        elif open_item and line[:1].isspace() and line.strip():
            items[-1] = _normalize(items[-1] + " " + line.strip())
        elif line.strip():
            open_item = False
    return items


def _bullets(section: Optional[str]) -> List[str]:
    if section is None:
        return []
    return [
        _normalize(line.strip()[1:])
        for line in _strip_comments(section).splitlines()
        if line.strip().startswith("- ")
    ]


def load_wave_tasks(project: Path, wave: int) -> Dict[str, dict]:
    tasks_dir = project / "tasks"
    if not tasks_dir.is_dir():
        raise ReviewFindingsError(f"tasks directory missing: {tasks_dir}")
    tasks: Dict[str, dict] = {}
    for path in sorted(tasks_dir.iterdir()):
        named = TASK_FILE_NAME.fullmatch(path.name)
        if not named:
            continue
        text = _read(path, f"task {path.name}")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ReviewFindingsError(f"task {path.name} has no frontmatter")
        frontmatter = parts[1]
        task_wave = re.search(r"(?m)^wave:\s*(\d+)", frontmatter)
        if not task_wave or int(task_wave.group(1)) != wave:
            continue
        body = parts[2]
        member = re.search(r"(?m)^repo:[ \t]*(?P<name>[^\s#]+)", frontmatter)
        tasks[named.group("id")] = {
            "id": named.group("id"),
            "path": str(path),
            "repo": _common.task_repo_for_path(path, named.group("id"), member.group("name").strip("\"'") if member else None),
            "files": _frontmatter_list(frontmatter, "files"),
            "acceptance": _numbered_items(_section(body, "Acceptance criteria")),
            "interface": [
                item for item in _bullets(_section(body, "Interface contract")) if item != "None"
            ],
            "owned": [
                item for item in _bullets(_section(body, "Intent coverage")) if item != "None"
            ],
        }
    if not tasks:
        raise ReviewFindingsError(f"PLAN.md Wave {wave} has no task files")
    return tasks


# --- lens files ------------------------------------------------------------


def review_paths(project: Path, wave: int, cycle: int, depth: str) -> Dict[str, Path]:
    review = project / "review"
    if depth == "deep":
        return {
            lens: review / f"wave-{wave}.cycle{cycle}.{lens}.md"
            for lens in ("contract", "adversarial")
        }
    return {"canonical": review / f"wave-{wave}.cycle{cycle}.md"}


def _fail_items(lines: Sequence[str]) -> List[str]:
    """Collect `- ❌` items with their indented continuation lines."""

    items: List[str] = []
    open_item = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(FAIL_MARKER):
            items.append(stripped[len(FAIL_MARKER) :])
            open_item = True
        elif open_item and line.startswith("  ") and stripped and not stripped.startswith("- "):
            items[-1] += " " + stripped
        elif stripped.startswith("- ") or stripped.startswith("#"):
            open_item = False
    return [_normalize(item) for item in items]


def _criterion_of(observation: str) -> str:
    return _normalize(observation.split(" — ", 1)[0])


def parse_lens(text: str, lens: str, tasks: Dict[str, dict]) -> dict:
    """Return the lens verdict and every blocking observation with its locator."""

    verdict = re.search(r"(?m)^Wave verdict:\s*(\S+)", text)
    if not verdict or verdict.group(1) not in {"pass", "blocked"}:
        raise ReviewFindingsError(f"{lens} lens has no valid Wave verdict")
    lines = _strip_comments(text).splitlines()
    findings: List[dict] = []
    problems: List[str] = []
    task_headings = [
        (index, match) for index, line in enumerate(lines)
        if (match := WAVE_TASK_HEADING.fullmatch(line.strip()))
    ]
    for start, heading in task_headings:
        task_id = heading.group("task")
        end = next(
            (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
            len(lines),
        )
        if task_id not in tasks:
            problems.append(f"{lens} lens reviews {task_id}, which is not a wave task")
            continue
        task = tasks[task_id]
        block = lines[start + 1 : end]
        zones = {"criteria": [], "warnings": [], "violations": []}
        zone = "criteria"
        for line in block:
            label = line.strip().casefold()
            if label.startswith("warnings (non-blocking)"):
                zone = "warnings"
                continue
            if label.startswith("contract violations (blocking)"):
                zone = "violations"
                continue
            zones[zone].append(line)
        for observation in _fail_items(zones["criteria"]):
            # Issue #392: match as the wave gate does, then report the task's own text.
            criteria = task["acceptance"] + task["interface"]
            quoted = _common.quoted_criterion(observation, criteria)
            if quoted is None:
                problems.append(
                    f"{lens} lens {task_id} finding names no task criterion: "
                    f"{_criterion_of(observation)}"
                )
                continue
            criterion = criteria[quoted[0]]
            if quoted[0] < len(task["acceptance"]):
                locator = f"{task_id.lower()}_ac{quoted[0] + 1}"
            else:
                locator = f"{task_id.lower()}_interface_contract"
            findings.append(
                {"locator": locator, "criterion": criterion, "task": task_id,
                 "lens": lens, "observation": observation}
            )
        for observation in _fail_items(zones["violations"]):
            findings.append(
                {"locator": f"{task_id.lower()}_files", "criterion": _criterion_of(observation),
                 "task": task_id, "lens": lens, "observation": observation}
            )
    coverage = _section("\n".join(lines), "Intent coverage")
    if coverage is not None:
        coverage_lines = coverage.splitlines()
        sc_headings = [
            (index, match) for index, line in enumerate(coverage_lines)
            if (match := WAVE_SC_HEADING.fullmatch(line.strip()))
        ]
        for position, (start, heading) in enumerate(sc_headings):
            text_part, _sep, sc_verdict = heading.group("rest").rpartition(":")
            if sc_verdict.strip() != "fail":
                continue
            sc_id = heading.group("sc")
            end = sc_headings[position + 1][0] if position + 1 < len(sc_headings) else len(coverage_lines)
            observations = _fail_items(coverage_lines[start + 1 : end])
            if not observations:
                problems.append(f"{lens} lens {sc_id} fails without a ❌ observation")
                continue
            owners = [task_id for task_id, task in tasks.items() if sc_id in task["owned"]]
            for observation in observations:
                findings.append(
                    {"locator": sc_id.lower(), "criterion": _normalize(text_part),
                     "task": ", ".join(owners), "tasks": owners, "lens": lens,
                     "observation": observation}
                )
    return {"verdict": verdict.group(1), "findings": findings, "problems": problems}


# --- skeptic files ---------------------------------------------------------


def parse_skeptic(path: Path, wave: int, expected_observations: Optional[Sequence[str]] = None) -> dict:
    """Validate one skeptic file against the template; return its verdict and observations."""

    named = SKEPTIC_FILE_NAME.fullmatch(path.name)
    if not named or int(named.group("wave")) != wave:
        raise ReviewFindingsError(f"skeptic file name does not belong to wave {wave}: {path.name}")
    text = _strip_comments(_read(path, f"skeptic {path.name}"))
    locator = re.search(r"(?m)^- Criterion locator:\s*(\S+)\s*$", text)
    if not locator or locator.group(1) != named.group("locator"):
        raise ReviewFindingsError(f"{path.name} Criterion locator does not match its filename")
    verdict_section = _section(text, "Verdict")
    verdict = _normalize(verdict_section or "")
    if verdict not in {"refuted", "stands"}:
        raise ReviewFindingsError(f"{path.name} Verdict must be `refuted` or `stands`")
    observations: List[str] = []
    lines = (_section(text, "Observations") or "").splitlines()
    starts = [(index, int(match.group("n"))) for index, line in enumerate(lines)
              if (match := SKEPTIC_OBSERVATION.match(line.strip()))]
    observation_numbers = {number for _, number in starts}
    if len(observation_numbers) != len(starts):
        raise ReviewFindingsError(f"{path.name} has duplicate observation numbers")
    for position, (start, _) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        observations.append(_normalize(" ".join(lines[start + 1 : end])))
    if not observations or any(not item for item in observations):
        raise ReviewFindingsError(f"{path.name} records no observation text")
    if expected_observations is not None and not {
        _normalize(item) for item in expected_observations
    } <= set(observations):
        raise ReviewFindingsError(f"{path.name} omits dispatched observations")
    observation_verdicts = [
        (int(match.group("n")), match.group("verdict"))
        for line in (_section(text, "Observation verdicts") or "").splitlines()
        if (match := SKEPTIC_OBSERVATION_VERDICT.match(line.strip()))
    ]
    verdict_numbers = {number for number, _ in observation_verdicts}
    if len(verdict_numbers) != len(observation_verdicts) or verdict_numbers != observation_numbers:
        raise ReviewFindingsError(
            f"{path.name} must have exactly one matching verdict per observation number"
        )
    if verdict == "refuted" and any(item != "refuted" for _, item in observation_verdicts):
        raise ReviewFindingsError(
            f"{path.name} records Verdict refuted while an observation stands"
        )
    return {
        "path": str(path),
        "cycle": int(named.group("cycle")),
        "locator": named.group("locator"),
        "verdict": verdict,
        "observations": observations,
    }


def load_skeptics(project: Path, wave: int, cycle: int) -> tuple:
    """Return (skeptics by cycle then locator, structural blockers for invalid files)."""

    review = project / "review"
    by_cycle: Dict[int, Dict[str, dict]] = {}
    blockers: List[dict] = []
    if not review.is_dir():
        return by_cycle, blockers
    for path in sorted(review.iterdir()):
        named = SKEPTIC_FILE_NAME.fullmatch(path.name)
        if not named or int(named.group("wave")) != wave or int(named.group("cycle")) > cycle:
            continue
        try:
            parsed = parse_skeptic(path, wave)
        except ReviewFindingsError as error:
            blockers.append({"kind": "invalid-skeptic", "path": str(path), "detail": str(error)})
            continue
        by_cycle.setdefault(parsed["cycle"], {})[parsed["locator"]] = parsed
    return by_cycle, blockers


# --- grouping and dispositions ---------------------------------------------


def group_findings(findings: Sequence[dict]) -> List[dict]:
    """Group by locator; collapse only observations whose text is identical."""

    groups: Dict[str, dict] = {}
    for finding in findings:
        group = groups.setdefault(
            finding["locator"],
            {"locator": finding["locator"], "criterion": finding["criterion"],
             "tasks": [], "lenses": [], "observations": []},
        )
        for task_id in finding.get("tasks", [finding["task"]]):
            if task_id and task_id not in group["tasks"]:
                group["tasks"].append(task_id)
        if finding["lens"] not in group["lenses"]:
            group["lenses"].append(finding["lens"])
        if all(item["text"] != finding["observation"] for item in group["observations"]):
            group["observations"].append({"lens": finding["lens"], "text": finding["observation"]})
    return [groups[key] for key in sorted(groups)]


def _earlier_lens_locators(project: Path, wave: int, cycle: int, depth: str, tasks: Dict[str, dict]) -> Dict[str, int]:
    first_seen: Dict[str, int] = {}
    for earlier in range(1, cycle):
        for lens, path in review_paths(project, wave, earlier, depth).items():
            if not path.is_file():
                continue
            try:
                parsed = parse_lens(path.read_text(encoding="utf-8"), lens, tasks)
            except (ReviewFindingsError, OSError):
                continue
            for finding in parsed["findings"]:
                first_seen.setdefault(finding["locator"], earlier)
    return first_seen


# --- review history across build recoveries ---------------------------------


def _cycle_reviews(directory: Path, wave: int) -> Dict[int, Dict[str, str]]:
    """The canonical or lens review texts of each cycle of one wave in one review directory."""
    cycles: Dict[int, Dict[str, str]] = {}
    if not directory.is_dir():
        return cycles
    for path in sorted(directory.iterdir()):
        named = REVIEW_CYCLE_FILE.fullmatch(path.name)
        if named and int(named.group("wave")) == wave and path.is_file() and not path.is_symlink():
            cycles.setdefault(int(named.group("cycle")), {})[named.group("lens") or "canonical"] = _read(path, "review")
    return cycles


def _cycle_outcome(texts: Dict[str, str], tasks: Dict[str, dict], depth: Optional[str] = None) -> dict:
    """Missing or invalid required lenses are blocked too, even if the surviving lens passed."""
    deep = depth == "deep" or any(re.search(r"(?m)^Depth:\s*deep\s*$", text) for text in texts.values())
    expected = {"contract", "adversarial"} if deep else {"canonical"}
    verdicts, locators = [], set()
    for lens, text in texts.items():
        try:
            parsed = parse_lens(text, lens, tasks)
        except ReviewFindingsError:
            verdicts.append("blocked")
            continue
        verdicts.append(parsed["verdict"])
        if parsed["problems"]:
            verdicts.append("blocked")
        locators.update(finding["locator"] for finding in parsed["findings"])
    passed = set(texts) == expected and set(verdicts) == {"pass"}
    return {"verdict": "pass" if passed else "blocked", "locators": sorted(locators)}


def _review_origin(repo: Path, relative: str, snapshot: str, text: str) -> Optional[str]:
    """A committed publication, not a content hash: only an unchanged Git path proves restoration."""
    content = _common.run_git(repo, "show", f"{snapshot}:{relative}")
    if content.returncode or content.stdout != text:
        return None
    origin = _common.run_git(repo, "log", "-1", "--format=%H", snapshot, "--", relative)
    return origin.stdout.strip() if origin.returncode == 0 else None


def _state_log(project: Path) -> List[str]:
    state = project / "STATE.md"
    return _read(state, "STATE.md").splitlines() if state.is_file() else []


def _log_events(lines: Sequence[str]) -> List[str]:
    """Log event texts after the date and phase, split as pipeline_state._log_events splits them."""
    return [line.split(" — ", 2)[-1] for line in lines if line.startswith("- ") and " — " in line]


def _recovery_bases(project: Path) -> List[str]:
    """Build recovery bases in the order the STATE.md Log records them."""
    bases: List[str] = []
    for line in _state_log(project):
        position = line.find(RECOVERY_RECORD)
        if position < 0:
            continue
        try:
            base = json.loads(line[position + len(RECOVERY_RECORD):]).get("base")
        except (ValueError, AttributeError):
            continue
        if isinstance(base, str) and base not in bases:
            bases.append(base)
    return bases


def review_history(repo: Path, project: Path, wave: int, cycle: int, tasks: Dict[str, dict]) -> dict:
    """The earlier review cycles of a wave in this milestone, including those a build recovery set aside.

    A build recovery moves `review/` to `plan/build-recovery-<base>/review/`, and a changed wave starts
    again at cycle 1. Its blocked cycles still count: a recovery does not refill the review budget. Plan
    approval restores an unchanged wave without a new Git publication. Only those proven restorations
    count once; independently published identical reports are still separate attempts.
    """
    current = _cycle_reviews(project / "review", wave)

    def key(texts, number, snapshot):
        origins = []
        for lens, text in sorted(texts.items()):
            suffix = "" if lens == "canonical" else f".{lens}"
            relative = (project / "review" / f"wave-{wave}.cycle{number}{suffix}.md").relative_to(repo).as_posix()
            origin = _review_origin(repo, relative, snapshot, text)
            if not origin:
                return None  # Without provenance, never erase an attempt merely because its text matches.
            origins.append((relative, origin, hashlib.sha256(text.encode("utf-8")).hexdigest()))
        return tuple(origins)

    seen = {identity for number, texts in current.items() if number <= cycle
            if (identity := key(texts, number, "HEAD")) is not None}
    backups = {}
    plan = project / "plan"
    if plan.is_dir():
        for path in plan.iterdir():
            named = RECOVERY_BACKUP.fullmatch(path.name)
            if named and (path / "review").is_dir() and not path.is_symlink():
                backups[named.group("base")] = path / "review"
    order = _recovery_bases(project)
    entries: List[dict] = []
    for base in sorted(backups, key=lambda name: (order.index(name) if name in order else len(order), name)):
        for number, texts in sorted(_cycle_reviews(backups[base], wave).items()):
            identity = key(texts, number, base)
            if identity is None or identity not in seen:
                if identity is not None:
                    seen.add(identity)
                old_plan = _common.run_git(repo, "show", f"{base}:{(project / 'plan/PLAN.md').relative_to(repo).as_posix()}")
                try:
                    depth = wave_depth(old_plan.stdout, wave) if old_plan.returncode == 0 else None
                except ReviewFindingsError:
                    depth = None
                entries.append({"recovery": base, "cycle": number, **_cycle_outcome(texts, tasks, depth)})
    for number, texts in sorted(current.items()):
        if number < cycle:
            entries.append({"recovery": None, "cycle": number, **_cycle_outcome(texts, tasks)})
    prior = sum(1 for entry in entries if entry["recovery"] and entry["verdict"] == "blocked")
    return {"prior_blocked": prior, "effective_cycle": prior + cycle, "cycles": entries}


def redesign_ruling(repo: Path, project: Path, wave: int, cycle: int,
                    reset: Optional[Callable[[str], bool]] = None, kind: str = "redesign") -> Optional[str]:
    """The committed ruling on this cycle since the latest build recovery, unchanged in the working STATE.

    A recovery numbers a changed wave from cycle 1 again, so a ruling recorded before it names another review.
    ``reset`` marks other Log events that start a new ruling scope; only a ruling after the latest counts.
    ``kind`` names the ruling (`redesign`, `panel`, `cap`).
    """
    marker = f"wave {wave} cycle {cycle} {kind} ruling: "

    def last(lines):
        recovery, ruling = None, None
        for line in lines:
            if reset is not None and any(reset(event) for event in _log_events([line])):
                recovery, ruling = line, None
                continue
            event = re.match(r"^- \d{4}-\d{2}-\d{2} — (?:build|define|plan) — (.*)$", line)
            if not event:
                continue
            if event[1].startswith(RECOVERY_RECORD):
                recovery, ruling = event[1], None
            elif event[1].startswith(marker):
                ruling = event[1][len(marker):].strip()
        return recovery, ruling

    current = _state_log(project)
    recorded = _common.run_git(repo, "show", f"HEAD:{(project / 'STATE.md').relative_to(repo).as_posix()}")
    if recorded.returncode:
        return None
    committed_event = last(recorded.stdout.splitlines())
    if last(current) != committed_event:
        return None
    return committed_event[1]


def _opens_fix_tasks(ruling: Optional[str]) -> bool:
    return ruling == REDESIGN_OPEN or (ruling or "").startswith(REDESIGN_OPEN + " (")


# --- repair attempts -------------------------------------------------------
#
# Criterion identity is the normalized authoritative text. A finding names it
# verbatim (canonical reviews) or by reference (`T001 acceptance 2`,
# `t001_ac2`, `SC3`, as panels do); a repair carries it on `Criterion:` lines.
# Counts are per criterion, read as the review recorded them, and restart for
# a criterion after a committed `Reconsider` ruling on it.


def _task_files(project: Path) -> Dict[str, Path]:
    tasks_dir = project / "tasks"
    if not tasks_dir.is_dir():
        return {}
    files = {}
    for path in sorted(tasks_dir.iterdir()):
        named = TASK_FILE_NAME.fullmatch(path.name)
        if named:
            files[named.group("id")] = path
    return files


def _split_task(text: str) -> tuple:
    parts = text.split("---", 2)
    return (parts[1], parts[2]) if len(parts) == 3 else ("", text)


def _read_at(repo: Optional[Path], path: Path, commit: Optional[str]) -> Optional[str]:
    """A file as committed at ``commit``, or in the working tree when ``commit`` is None."""
    if commit is None or repo is None:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    shown = _common.run_git(repo, "show", f"{commit}:{path.relative_to(repo).as_posix()}")
    return shown.stdout if shown.returncode == 0 else None


def _task_texts_at(repo: Optional[Path], project: Path, commit: Optional[str]) -> Dict[str, tuple]:
    """Task id -> (path, text) in the working tree, or as committed at ``commit``."""
    if commit is None or repo is None:
        return {task_id: (path, _read(path, f"task {path.name}")) for task_id, path in _task_files(project).items()}
    tasks = (project / "tasks").relative_to(repo).as_posix()
    texts = {}
    for name in _common.run_git(repo, "ls-tree", "--name-only", commit, "--", f"{tasks}/").stdout.splitlines():
        named = TASK_FILE_NAME.fullmatch(posixpath.basename(name))
        if named:
            texts[named.group("id")] = (repo / name, _common.run_git(repo, "show", f"{commit}:{name}").stdout)
    return texts


class _Criteria:
    """Resolves criterion references against the task and INTENT.md texts of one snapshot."""

    def __init__(self, repo: Optional[Path], project: Path, commit: Optional[str] = None) -> None:
        self.repo, self.project, self.commit = repo, project, commit
        self._tasks: Optional[Dict[str, tuple]] = None
        self._intent: Optional[List[str]] = None

    def _items(self, match: "re.Match[str]") -> List[str]:
        if match["sc"]:
            if self._intent is None:
                text = _read_at(self.repo, self.project / "intent" / "INTENT.md", self.commit)
                self._intent = _numbered_items(_section(text, "Success criteria")) if text else []
            return self._intent
        if self._tasks is None:
            self._tasks = _task_texts_at(self.repo, self.project, self.commit)
        found = self._tasks.get("T" + (match["task"] or match["ltask"]))
        return _numbered_items(_section(_split_task(found[1])[1], "Acceptance criteria")) if found else []

    def resolve(self, source: str, identities: List[str], unresolved: List[str]) -> None:
        for match in CRITERION_REFERENCE.finditer(source):
            items, index = self._items(match), int(match["ac"] or match["lac"] or match["sc"])
            if index <= len(items):
                text = _normalize(str(items[index - 1]))
                if text not in identities:
                    identities.append(text)
            elif match.group(0) not in unresolved:
                unresolved.append(match.group(0))

    def of_finding(self, text: str, locator: Optional[str] = None, references: bool = True) -> tuple:
        """The criterion identities a finding names, and the references that resolve to none."""
        identities, unresolved = [_normalize(text)], []
        if locator:
            self.resolve(locator, identities, unresolved)
        if references:
            self.resolve(text, identities, unresolved)
        return identities, unresolved

    def carried(self, section: str) -> tuple:
        """The identities a repair's `Criterion:` lines carry; a line that is only a reference resolves."""
        identities, unresolved = [], []
        for value in re.findall(r"(?m)^Criterion:[ \t]*(.+)$", section):
            line = _normalize(value)
            if BARE_REFERENCE.fullmatch(line):
                self.resolve(line, identities, unresolved)
                continue
            if line not in identities:
                identities.append(line)
            leading = BARE_REFERENCE.match(line)
            if leading and leading.group(0)[-1:] in ":.":
                self.resolve(leading.group(0), identities, unresolved)
        return identities, unresolved


def criterion_texts(project: Path, text: str, locator: Optional[str] = None,
                    references: bool = True) -> tuple:
    """The criterion identities a finding names in the working tree, and the unresolved references."""
    return _Criteria(None, project).of_finding(text, locator, references)


def repair_tasks(repo: Optional[Path], project: Path, commit: Optional[str] = None) -> List[dict]:
    """Every task that carries `## Review findings`, with its status, wave, repo and carried criteria."""
    resolver = _Criteria(repo, project, commit)
    repairs = []
    for task_id, (path, text) in _task_texts_at(repo, project, commit).items():
        frontmatter, body = _split_task(text)
        section = _section(body, "Review findings")
        if section is None:
            continue
        status = re.search(r"(?m)^status:[ \t]*([A-Za-z-]+)", frontmatter)
        wave = re.search(r"(?m)^wave:[ \t]*(\d+)", frontmatter)
        member = re.search(r"(?m)^repo:[ \t]*(?P<name>[^\s#]+)", frontmatter)
        criteria, unresolved = resolver.carried(section)
        repairs.append({
            "task": task_id,
            "path": str(path),
            "status": status.group(1) if status else None,
            "wave": int(wave.group(1)) if wave else None,
            "repo": member.group("name").strip("\"'") if member else None,
            "criteria": criteria,
            "unresolved": unresolved,
        })
    return repairs


def _publication(repo: Path, path: Path) -> Optional[str]:
    """The commit that published a review file as it reads now; None reads the working tree.

    Plan approval restores an unchanged wave without a new publication, so a restored review keeps its
    original commit, while a new review under a reused name, or one not committed as is, reads as it is now.
    """
    if not path.is_file():
        return None
    return _review_origin(repo, path.relative_to(repo).as_posix(), "HEAD", _read(path, "review"))


def _review_commit(repo: Path, project: Path, wave: int, cycle: int) -> Optional[str]:
    """The commit that published this cycle's canonical review as it reads now."""
    for name in (f"wave-{wave}.cycle{cycle}.md", f"wave-{wave}.cycle{cycle}.contract.md"):
        path = project / "review" / name
        if path.is_file():
            return _publication(repo, path)
    return None


def _milestone_lines(lines: Sequence[str]) -> List[str]:
    """Log lines after the last shipment or abandon: the current milestone."""
    start = 0
    for index, line in enumerate(lines):
        if any(event.startswith(_common.LENS_MILESTONE_BOUNDARIES) for event in _log_events([line])):
            start = index + 1
    return list(lines[start:])


def _introducing_commit(repo: Path, relative: str, line: str, head: str = "HEAD", occurrence: int = 1) -> Optional[str]:
    """The commit whose Log first held ``occurrence`` copies of ``line``: the one that recorded that copy."""
    for commit in reversed(_common.run_git(repo, "log", "--format=%H", "-S", line, head, "--", relative).stdout.split()):
        if _common.run_git(repo, "show", f"{commit}:{relative}").stdout.splitlines().count(line) >= occurrence:
            return commit
    return None


def reconsider_event(wave: int, cycle: int, identities: Sequence[str]) -> str:
    """The Log event that names the criteria a `Reconsider` ruling covers; their repair counts restart."""
    return f"wave {wave} cycle {cycle} reconsidered: {json.dumps(sorted(set(identities)), ensure_ascii=False)}"


def _reconsider_resets(repo: Path, project: Path, commit: Optional[str]) -> List[tuple]:
    """(criteria, completed task ids) for each recorded `reconsidered` event of this milestone.

    Each event must follow its own cycle's `Reconsider` ruling; the tasks completed when it was committed
    no longer count for its criteria, in every wave.
    """
    state = (project / "STATE.md").relative_to(repo).as_posix()
    head = commit or "HEAD"
    recorded = _common.run_git(repo, "show", f"{head}:{state}")
    if recorded.returncode:
        return []
    lines = recorded.stdout.splitlines()
    milestone = len(lines) - len(_milestone_lines(lines))
    resets, reconsidered = [], set()
    for index in range(milestone, len(lines)):
        for event in _log_events([lines[index]]):
            ruling = RECONSIDER_RULING.match(event)
            if ruling:
                reconsidered.add((ruling["wave"], ruling["cycle"]))
            named = RECONSIDERED_EVENT.match(event)
            if not named or (named["wave"], named["cycle"]) not in reconsidered:
                continue
            try:
                criteria = {_normalize(item) for item in json.loads(named["criteria"]) if isinstance(item, str)}
            except ValueError:
                continue
            introduced = _introducing_commit(repo, state, lines[index], head, lines[:index + 1].count(lines[index]))
            if introduced is None:
                continue
            done = {repair["task"] for repair in repair_tasks(repo, project, introduced) if repair["status"] == "done"}
            resets.append((criteria, done))
    return resets


def repair_attempts(repo: Path, project: Path, identities: Sequence[str], exclude: Sequence[str] = (),
                    commit: Optional[str] = None) -> Dict[str, List[dict]]:
    """Per criterion identity, the completed repair tasks that carried it.

    ``commit`` reads the tasks as a recorded review saw them; None reads the working tree. Repairs completed
    before a recorded `reconsidered` event for that criterion no longer count.
    """
    resets = _reconsider_resets(repo, project, commit)
    repairs = [repair for repair in repair_tasks(repo, project, commit)
               if repair["status"] == "done" and repair["task"] not in exclude]
    counts: Dict[str, List[dict]] = {}
    for identity in identities:
        reset = set().union(*(done for criteria, done in resets if identity in criteria))
        counts[identity] = [{"task": repair["task"], "path": repair["path"], "matched": identity,
                             **({"repo": repair["repo"]} if repair["repo"] else {})}
                            for repair in repairs if identity in repair["criteria"] and repair["task"] not in reset]
    return counts


def _most_repaired(counts: Dict[str, List[dict]]) -> List[dict]:
    return max(counts.values(), key=len, default=[])


def _panel_items(text: str, heading: Optional[str] = None) -> List[dict]:
    """Merged panel findings, from one summary section or the whole summary; By family repeats them."""
    summary = text.split("\n## By family", 1)[0]
    if heading is not None:
        summary = _section(summary, heading) or ""
    items = []
    for block in re.split(r"(?m)(?=^- (?:high|medium|low) / )", summary):
        head = PANEL_ITEM.match(block)
        if not head:
            continue
        evidence = re.search(r"(?m)^  evidence: (.*)$", block)
        source = re.search(r"(?m)^  source: (.*)$", block)
        items.append({"severity": head["severity"], "kind": head["kind"], "text": head["text"].strip(),
                      "evidence": evidence.group(1) if evidence else "",
                      "source": source.group(1) if source else None})
    return items


def panel_repairs(repo: Path, project_dir: str, wave: int, cycle: int) -> dict:
    """Repair attempts behind each actionable panel finding of a cycle, for the panel-on-pass question."""
    project = repo / project_dir
    path = project / "review" / f"wave-{wave}.cycle{cycle}.panel.md"
    text = _read(path, "panel")
    count = re.search(r"(?m)^Actionable:\s*(\d+)\s*$", text)
    if not count:
        raise ReviewFindingsError("panel file has no Actionable count")
    items = _panel_items(text, "Actionable")
    if len(items) != int(count.group(1)):
        raise ReviewFindingsError(f"panel reports Actionable: {count.group(1)} but lists {len(items)} actionable findings")
    reviewed = _publication(repo, path)
    resolver = _Criteria(repo, project, reviewed)
    findings = []
    for item in items:
        identities, unresolved = resolver.of_finding(item["text"])
        counts = repair_attempts(repo, project, identities, commit=reviewed)
        findings.append({"kind": item["kind"], "criterion": item["text"], "unresolved": unresolved,
                         "repair_attempts": _most_repaired(counts),
                         "repair_criteria": [{"criterion": identity, "tasks": [entry["task"] for entry in entries]}
                                             for identity, entries in counts.items() if entries]})
    repeated = [item["criterion"] for item in findings if len(item["repair_attempts"]) >= REDESIGN_AFTER_REPAIRS]
    triggering = [entry["criterion"] for item in findings for entry in item["repair_criteria"]
                  if len(entry["tasks"]) >= REDESIGN_AFTER_REPAIRS]
    unresolved = sorted({reference for item in findings for reference in item["unresolved"]})
    ruling = redesign_ruling(repo, project, wave, cycle, kind="panel")
    return {
        "status": "ok",
        "wave": wave,
        "cycle": cycle,
        "panel": str(path),
        "actionable": len(findings),
        "findings": findings,
        "repeated": repeated,
        "reconsider_event": reconsider_event(wave, cycle, triggering) if triggering else None,
        "unresolved": unresolved,
        "ruling": ruling,
        "pending": bool(repeated or unresolved) and not _opens_panel_fixes(ruling),
    }


def _opens_panel_fixes(ruling: Optional[str]) -> bool:
    return ruling == PANEL_OPEN or (ruling or "").startswith(PANEL_OPEN + " (")


def _repair_source(project: Path, wave: int) -> Optional[tuple]:
    """The (wave, cycle) a repair answers: its wave heading's single pair, or its own reviewed wave's last cycle."""
    plan = _read(project / "plan" / "PLAN.md", "PLAN.md")
    for match in PLAN_WAVE_HEADING.finditer(plan):
        if int(match["wave"]) == wave:
            pairs = {(int(w), int(c)) for w, c in WAVE_CYCLE_PAIR.findall(match["title"])}
            if len(pairs) == 1:
                return next(iter(pairs))
    cycles = [int(found.group("cycle")) for found in (REVIEW_CYCLE_FILE.fullmatch(path.name)
              for path in (project / "review").glob(f"wave-{wave}.cycle*.md")) if found]
    return (wave, max(cycles)) if cycles else None


def _completed_at(repo: Path, path: str) -> Optional[str]:
    """The commit where the current copy of a task file's frontmatter first recorded `status: done`.

    A path an earlier task used before it was removed and added again does not lend its history, and the
    words `status: done` in the task body are not a completion.
    """
    added = _common.run_git(repo, "log", "--diff-filter=A", "--format=%H", "--", path).stdout.split()
    for commit in reversed(_common.run_git(repo, "log", "--format=%H", "--", path).stdout.split()):
        if added and not _descends(repo, added[0], commit):
            continue
        frontmatter, _ = _split_task(_common.run_git(repo, "show", f"{commit}:{path}").stdout)
        status = re.search(r"(?m)^status:[ \t]*([A-Za-z-]+)", frontmatter)
        if status and status.group(1) == "done":
            return commit
    return None


def _descends(repo: Path, ancestor: str, commit: str) -> bool:
    return _common.run_git(repo, "merge-base", "--is-ancestor", ancestor, commit).returncode == 0


def _latest_opening(repo: Path, project: Path, wave: int, cycle: int) -> Optional[str]:
    """The commit introducing the latest committed redesign or panel ruling on the cycle, when it opens fix tasks.

    Only rulings in this milestone and after the latest build recovery count, the working Log must not have
    changed it, and of the two kinds the later ruling decides.
    """
    state = (project / "STATE.md").relative_to(repo).as_posix()
    recorded = _common.run_git(repo, "show", f"HEAD:{state}")
    if recorded.returncode:
        return None
    markers = {kind: f"wave {wave} cycle {cycle} {kind} ruling: " for kind in ("redesign", "panel")}

    def last(lines: Sequence[str]) -> Optional[tuple]:
        latest = None
        for index in range(len(lines) - len(_milestone_lines(lines)), len(lines)):
            for event in _log_events([lines[index]]):
                if event.startswith(RECOVERY_RECORD):
                    latest = None
                for kind, marker in markers.items():
                    if event.startswith(marker):
                        latest = (kind, event[len(marker):].strip(), lines[index], lines[:index + 1].count(lines[index]))
        return latest

    latest = last(recorded.stdout.splitlines())
    if latest is None or last(_state_log(project)) != latest:
        return None
    kind, value, line, occurrence = latest
    opens = _opens_fix_tasks(value) if kind == "redesign" else _opens_panel_fixes(value)
    return _introducing_commit(repo, state, line, occurrence=occurrence) if opens else None


def repair_release(repo: Path, project_dir: str, task_id: str) -> Optional[str]:
    """Why a repair task must not be dispatched yet, or None.

    A repair carrying a criterion that two completed repairs already carried needs, after the later of them
    completed, either a committed redesign or panel ruling that opens fix tasks for the cycle it answers, or
    the approved plan that added it. Malformed or unresolvable repairs are held, never released.
    """
    project = repo / project_dir
    path = _task_files(project).get(task_id)
    if path is None:
        return None
    frontmatter, body = _split_task(_read(path, f"task {path.name}"))
    section = _section(body, "Review findings")
    if section is None:
        return None
    wave = re.search(r"(?m)^wave:[ \t]*(\d+)", frontmatter)
    if not wave:
        return f"repair {task_id} carries Review findings but has no wave"
    source = _repair_source(project, int(wave.group(1)))
    if source is None:
        return (f"repair {task_id}: the heading of wave {wave.group(1)} must name exactly one "
                "`wave N cycle C`, or the wave must have a review of its own")
    identities, unresolved = _Criteria(repo, project).carried(section)
    if unresolved:
        return f"repair {task_id} carries criterion references that resolve to no criterion: {', '.join(unresolved)}"
    counts = repair_attempts(repo, project, identities, exclude=(task_id,))
    repeated = [entries for entries in counts.values() if len(entries) >= REDESIGN_AFTER_REPAIRS]
    if not repeated:
        return None
    completions = [_completed_at(repo, Path(entry["path"]).relative_to(repo).as_posix())
                   for entries in repeated for entry in entries]
    trigger = [commit for commit in completions if commit]

    def after_trigger(commit: Optional[str]) -> bool:
        return (commit is not None and len(trigger) == len(completions)
                and all(_descends(repo, landed, commit) for landed in trigger))

    added = _common.run_git(repo, "log", "--diff-filter=A", "--format=%H%x09%s", "--",
                            path.relative_to(repo).as_posix()).stdout.splitlines()
    if added:
        commit, _, subject = added[0].partition("\t")
        if subject == "plan: build plan approved" and after_trigger(commit):
            return None
    if after_trigger(_latest_opening(repo, project, *source)):
        return None
    carried = ", ".join(sorted({entry["task"] for entries in repeated for entry in entries}))
    return (f"repair {task_id} carries a criterion that completed repairs {carried} already carried; "
            f"record a committed wave {source[0]} cycle {source[1]} redesign or panel ruling that opens "
            "fix tasks after them before dispatching it")


def lens_redesign(repo: Path, project: Path, wave: int, cycle: int) -> dict:
    """A recorded fix-routing lens that recommends redesign or fail-closed holds fix tasks for the redesign ruling.

    Any such `lenses fix-routing wave N cycle C` event of this milestone since the latest build recovery, in
    the working or the committed STATE.md, counts; a later lens event never withdraws it. Only an `Open fix
    tasks anyway` redesign ruling recorded after the latest such event, committed and unchanged, releases it.
    """
    scope = _common.lens_scope_wave(wave, cycle)

    def fire(event: str) -> Optional[_common.LensEvent]:
        try:
            parsed = _common.parse_lens_event(event)
        except _common.LensEventError:
            return None
        if parsed and parsed.gate == "fix-routing" and parsed.scope == scope and parsed.requests_redesign():
            return parsed
        return None

    def fires(lines: Sequence[str]) -> List[_common.LensEvent]:
        found: List[_common.LensEvent] = []
        for event in _common.milestone_log_events(_log_events(lines)):
            if event.startswith(RECOVERY_RECORD):
                found = []
            elif (parsed := fire(event)) is not None:
                found.append(parsed)
        return found

    recorded = _common.run_git(repo, "show", f"HEAD:{(project / 'STATE.md').relative_to(repo).as_posix()}")
    fired = fires(_state_log(project)) or fires(recorded.stdout.splitlines() if recorded.returncode == 0 else [])
    ruling = redesign_ruling(repo, project, wave, cycle, reset=lambda event: fire(event) is not None) if fired else None
    return {
        "lenses": [{"lens": lens, "verdict": verdict} for lens, verdict in fired[-1].fired
                   if verdict in _common.LENS_REDESIGN_VERDICTS] if fired else [],
        "pending": bool(fired) and not _opens_fix_tasks(ruling),
    }


def _fix_batches(groups: Sequence[dict], tasks: Dict[str, dict]) -> List[dict]:
    """One batch per repo and disjoint file scope: groups whose task files overlap in one repo share a batch.

    A member batch names its `repo`; a coordinator batch has no such key.
    """

    batches: List[dict] = []
    for group in groups:
        owners = [tasks.get(task_id, {}) for task_id in group["tasks"]]
        for repo in sorted({task.get("repo") or "" for task in owners}) or [""]:
            files = sorted({path for task in owners if (task.get("repo") or "") == repo
                            for path in task.get("files", [])})
            merged = {"locators": [group["locator"]], "files": files, **({"repo": repo} if repo else {})}
            keep: List[dict] = []
            for batch in batches:
                if batch.get("repo") == merged.get("repo") and _common.overlapping_paths(batch["files"], files):
                    merged["locators"] = batch["locators"] + merged["locators"]
                    merged["files"] = sorted(set(batch["files"]) | set(merged["files"]))
                else:
                    keep.append(batch)
            keep.append(merged)
            batches = keep
    return batches


def compute(
    repo: Path,
    project_dir: str,
    wave: int,
    cycle: int,
    helper_failures: Sequence[str] = (),
) -> dict:
    project = repo / project_dir
    plan_path = project / "plan" / "PLAN.md"
    plan_text = _read(plan_path, "PLAN.md")
    config = parse_config(plan_text)
    depth = wave_depth(plan_text, wave)
    tasks = load_wave_tasks(project, wave)
    structural: List[dict] = [
        {"kind": "helper-failure", "path": None, "detail": item} for item in helper_failures
    ]
    findings: List[dict] = []
    verdicts: Dict[str, Optional[str]] = {}
    for lens, path in review_paths(project, wave, cycle, depth).items():
        if not path.is_file():
            structural.append({"kind": "missing-artifact", "path": str(path), "detail": f"{lens} review file is missing"})
            verdicts[lens] = None
            continue
        try:
            parsed = parse_lens(_read(path, f"{lens} review"), lens, tasks)
        except ReviewFindingsError as error:
            structural.append({"kind": "invalid-artifact", "path": str(path), "detail": str(error)})
            verdicts[lens] = None
            continue
        verdicts[lens] = parsed["verdict"]
        findings.extend(parsed["findings"])
        for problem in parsed["problems"]:
            structural.append({"kind": "invalid-artifact", "path": str(path), "detail": problem})
        if parsed["verdict"] == "blocked" and not parsed["findings"] and not parsed["problems"]:
            structural.append({"kind": "invalid-artifact", "path": str(path), "detail": f"{lens} lens is blocked without a ❌ finding"})

    skeptics_by_cycle, skeptic_blockers = load_skeptics(project, wave, cycle)
    structural.extend(skeptic_blockers)
    panel = None
    panel_path = project / "review" / f"wave-{wave}.cycle{cycle}.panel.md"
    if panel_path.is_file():
        actionable = re.search(r"(?m)^Actionable:\s*(\d+)\s*$", _read(panel_path, "panel"))
        if not actionable:
            structural.append({"kind": "invalid-artifact", "path": str(panel_path), "detail": "panel file has no Actionable count"})
        else:
            panel = {"path": str(panel_path), "actionable": int(actionable.group(1))}

    skeptics_active = config["finding_skeptics"] == "on" and depth == "deep"
    history = review_history(repo, project, wave, cycle, tasks)
    first_seen = _earlier_lens_locators(project, wave, cycle, depth, tasks)
    for entry in history["cycles"]:
        for locator in entry["locators"]:
            first_seen.setdefault(locator, entry["cycle"])
    earlier_refuted: Dict[str, dict] = {}
    for earlier in sorted(skeptics_by_cycle):
        if earlier >= cycle:
            continue
        for locator, skeptic in skeptics_by_cycle[earlier].items():
            if skeptic["verdict"] == "refuted":
                earlier_refuted.setdefault(locator, skeptic)
    current_skeptics = skeptics_by_cycle.get(cycle, {})

    groups = group_findings(findings)
    reviewed = _review_commit(repo, project, wave, cycle)
    resolver = _Criteria(repo, project, reviewed)
    covers: Dict[str, List[str]] = {}
    for group in groups:
        locator = group["locator"]
        group["first_seen_cycle"] = first_seen.get(locator, cycle)
        group["repeat"] = locator in first_seen
        identities, _ = resolver.of_finding(group["criterion"], locator, references=False)
        counts = repair_attempts(repo, project, identities, commit=reviewed)
        group["repair_attempts"] = _most_repaired(counts)
        group["repair_criteria"] = [{"criterion": identity, "tasks": [entry["task"] for entry in entries]}
                                    for identity, entries in counts.items() if entries]
        covers[locator] = [identity for identity, entries in counts.items()
                           if len(entries) >= REDESIGN_AFTER_REPAIRS] or identities
        group["skeptic"] = None
        earlier = earlier_refuted.get(locator)
        current = current_skeptics.get(locator)
        if current is not None:
            try:
                parse_skeptic(Path(current["path"]), wave,
                              [item["text"] for item in group["observations"]])
            except ReviewFindingsError as error:
                structural.append({"kind": "invalid-skeptic", "path": current["path"], "detail": str(error)})
                current = None
        if not skeptics_active:
            group["disposition"] = "fix"
        elif earlier is not None:
            texts = {item["text"] for item in group["observations"]}
            group["skeptic"] = {"cycle": earlier["cycle"], "verdict": "refuted", "path": earlier["path"]}
            if texts <= set(earlier["observations"]):
                group["disposition"] = "refuted-earlier"
            else:
                group["disposition"] = "fix"
                group["new_evidence"] = sorted(texts - set(earlier["observations"]))
        elif current is not None:
            group["skeptic"] = {"cycle": cycle, "verdict": current["verdict"], "path": current["path"]}
            group["disposition"] = "refuted" if current["verdict"] == "refuted" else "fix"
        else:
            group["disposition"] = "needs-skeptic"

    refuted = [g["locator"] for g in groups if g["disposition"] in {"refuted", "refuted-earlier"}]
    fix_groups = [g for g in groups if g["disposition"] == "fix"]
    skeptic_groups = [g["locator"] for g in groups if g["disposition"] == "needs-skeptic"]
    lens_blocked = any(value != "pass" for value in verdicts.values())
    cap_reached = history["effective_cycle"] >= config["max_review_cycles"]
    blocked_before: Dict[str, List[dict]] = {}
    for entry in history["cycles"]:
        if entry["verdict"] == "blocked":
            for locator in entry["locators"]:
                blocked_before.setdefault(locator, []).append({"recovery": entry["recovery"], "cycle": entry["cycle"]})
    # Two fixes of the same criterion already failed: another fix task needs the user's ruling first.
    redesign = [{"locator": group["locator"], "criterion": group["criterion"],
                 "blocked_before": blocked_before.get(group["locator"], []),
                 "repair_attempts": group["repair_attempts"]}
                for group in fix_groups
                if len(blocked_before.get(group["locator"], [])) >= REDESIGN_AFTER_BLOCKS
                or len(group["repair_attempts"]) >= REDESIGN_AFTER_REPAIRS]
    ruling = redesign_ruling(repo, project, wave, cycle) if redesign else None
    lenses = lens_redesign(repo, project, wave, cycle)
    covered = [identity for entry in redesign for identity in covers[entry["locator"]]]
    return {
        "status": "ok",
        "wave": wave,
        "cycle": cycle,
        "depth": depth,
        "config": config,
        "lens_verdicts": verdicts,
        "blocked": lens_blocked or bool(structural),
        "cap_reached": cap_reached,
        "skeptics_active": skeptics_active,
        "groups": groups,
        "skeptic_groups": skeptic_groups,
        "refuted_groups": refuted,
        "carried_refutations": [
            {"locator": locator, "cycle": skeptic["cycle"], "path": skeptic["path"]}
            for locator, skeptic in sorted(earlier_refuted.items())
        ],
        "fix_groups": [g["locator"] for g in fix_groups],
        "fix_batches": _fix_batches(fix_groups, tasks),
        "structural_blockers": structural,
        "all_refuted": bool(refuted) and not fix_groups and not skeptic_groups and not structural and not cap_reached,
        "panel": panel,
        "history": history,
        "redesign": redesign,
        "reconsider_event": reconsider_event(wave, cycle, covered) if covered else None,
        "redesign_ruling": ruling,
        "redesign_pending": bool(redesign) and not _opens_fix_tasks(ruling),
        # M8: a fix-routing lens fire asks the same redesign question even when `redesign` is empty.
        "lens_redesign": lenses["lenses"],
        "lens_redesign_pending": lenses["pending"],
    }


def emit(payload: dict) -> int:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def current_product_scope(text: str) -> Optional[dict]:
    """Explicit current-product scope authored by the reviewer, never inferred from a sidecar base."""
    text = _strip_comments(text)
    if not re.search(r"(?m)^Product scope: current$", text):
        return None
    heads = re.findall(r"(?m)^Reviewed HEAD: ([0-9a-f]{40})$", text)
    members = re.findall(r"(?m)^Reviewed member: ([a-z0-9-]+) ([0-9a-f]{40})$", text)
    if len(heads) != 1 or len({name for name, _ in members}) != len(members):
        raise ReviewFindingsError("current-product review needs unique full reviewed snapshots")
    return {"base": heads[0], "member_heads": dict(members)}


def _recorded_review(repo: Path, project: Path, wave: int, cycle: int, depth: str, snapshot: str) -> Optional[dict]:
    """Collected, hash-bound lens records, including those retained before an unchanged-wave recovery."""
    if __package__:
        from . import isolation
    else:
        import isolation
    try:
        root = isolation.common_git_dir(repo) / "gsd-path/dispatch" / isolation.require_bound(repo).replace("/", "-")
    except isolation.IsolationError:
        return None
    matches = {}
    paths = review_paths(project, wave, cycle, depth)
    for lens, path in paths.items():
        relative = path.relative_to(repo).as_posix()
        expected_task = f"review_wave_{wave}_cycle_{cycle}" + ("" if lens == "canonical" else f"_{lens}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        candidates, attempted = [], False
        namespaces = [root, *sorted(root.glob("[0-9a-f]" * 40))]
        for namespace in namespaces:
            records = namespace / "reviews" / expected_task
            for record in records.glob("attempt-*/state.json"):
                state = json.loads(_read(record, "review record"))
                if state.get("base") != snapshot:
                    continue
                attempted = True
                exit_path = record.with_name("exit.json")
                execution = json.loads(_read(exit_path, "review exit")) if exit_path.is_file() else state
                if (state.get("task_id") == expected_task and state.get("wave") == wave
                        and state.get("cycle") == cycle and state.get("relative") == relative
                        and state.get("lens") == (None if lens == "canonical" else lens)
                        and state.get("outcome") == "collected" and state.get("validated") is True
                        and state.get("validated_sha256") == digest
                        and execution.get("finished_at") and execution.get("exit_code") == 0
                        and not execution.get("timed_out")):
                    candidates.append(state)
        if not candidates:
            if attempted:
                raise ReviewFindingsError("source review has no validated collected provenance")
            return None
        # An identical report from different snapshots is ambiguous, not authority to choose a newer one.
        identities = {(item.get("base"), item.get("product_scope"),
                       json.dumps(item.get("member_heads", {}), sort_keys=True)) for item in candidates}
        if len(identities) != 1:
            raise ReviewFindingsError("collected review records disagree on the reviewed snapshot")
        matches[lens] = candidates[0]
    snapshots = {(item.get("base"), item.get("product_scope"),
                  json.dumps(item.get("member_heads", {}), sort_keys=True)) for item in matches.values()}
    if len(snapshots) != 1:
        raise ReviewFindingsError("collected review lenses disagree on the reviewed snapshot")
    return next(iter(matches.values()))


def repair_evidence(repo: Path, project_dir: str, wave: int, cycle: int, task_id: str,
                    review_base: Optional[str] = None,
                    member_heads: Optional[Dict[str, str]] = None) -> dict:
    """Bind a carried finding batch to a proven, independently verified repair.

    The original landings must reach the repair base through linked repairs only. When other landings
    changed those files first, the product the source review judged is the anchor instead: the driver's
    recorded review base, or `review_base` (and `member_heads`) for a review that the driver did not run.
    """
    try:
        from . import _common, build_state, isolation
    except ImportError:  # installed standalone helper
        import _common
        import build_state
        import isolation

    try:
        repo = isolation.require_directory(repo, "repository")
        project = repo / isolation.relative_posix(project_dir)
        if not re.fullmatch(r"T\d{3}", task_id or ""):
            raise ReviewFindingsError("--task must name a repair task id")
        paths = list((project / "tasks").glob(f"{task_id}-*.md"))
        if len(paths) != 1:
            raise ReviewFindingsError("repair task must resolve to exactly one task file")
        repair_path = paths[0]
        text = _read(repair_path, "repair task")
        fields, error = isolation.task_frontmatter(text)
        if error or fields is None or fields.get("status") != "done":
            raise ReviewFindingsError(error or "repair task must be done")
        head = isolation.current_sha(repo)
        proof = isolation.verify_landed_task_files(
            repo, [repair_path], f"{project_dir}/tasks", head
        )["tasks"][0]
        if proof["verdict"] != "recovered":
            raise ReviewFindingsError("repair requires a proven landing")
        base = proof["base"]  # in the coordinator, where the source review is
        findings = _section(text, "Review findings") or ""
        headings = list(re.finditer(r"(?m)^### ([a-z0-9_]+)\s*$", findings))
        locators = [heading.group(1) for heading in headings]
        if not locators or len(set(locators)) != len(locators):
            raise ReviewFindingsError("repair needs unique carried finding locators")
        source = compute(repo, project_dir, wave, cycle)
        if source["structural_blockers"]:
            raise ReviewFindingsError("source findings have structural blockers")
        member = fields.get("repo")  # a finding in two repos has one batch per repo with the same locators
        batches = [batch for batch in source["fix_batches"]
                   if batch.get("repo") == member and set(batch["locators"]) == set(locators)]
        if len(batches) != 1:
            raise ReviewFindingsError("repair must carry one eligible source finding batch")
        # A member task's product is in the member repository: its commits, its bound branch, its Verify rows.
        where, tip = repo, []  # the coordinator product is judged in the working tree
        if member:
            where, _, lock = isolation._member_context(repo, member)
            tip = [f"refs/heads/{lock['branch']}"]

        def entry(landing: dict, path: Path, paths: List[str]) -> dict:
            """A proven landing as the receipt shows it; a member landing adds its commits in the member."""
            item = {"task": landing["task_id"], "base": landing["base"], "commit": landing["commit"], "files": paths}
            if member:
                if "landing" in landing:
                    # A landing proven through an owner-ruled member rebase reports its replayed base.
                    item.update(member_base=landing.get("member_base")
                                or isolation.task_frontmatter(_read(path, "task"))[0]["member_base"],
                                member_commit=landing["landing"])
                else:
                    if __package__:
                        from . import coordinator_migration
                    else:
                        import coordinator_migration
                    projected = coordinator_migration.legacy_proof(repo, landing["task_id"], landing["commit"], project_dir)
                    if projected is None:
                        raise ReviewFindingsError("member repair source has no native or migrated landing proof")
                    item.update(projected, evidence_origin="preserved-single-repo")
            return item

        def at(item: dict, point: str = "commit") -> str:
            return item.get(f"member_{point}", item[point])

        groups = [group for group in source["groups"] if group["locator"] in locators]
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(findings)
            block = _normalize(findings[heading.end():end])
            group = next(group for group in groups if group["locator"] == heading.group(1))
            if ("Criterion: " + group["criterion"] not in block
                    or any(item["text"] not in block for item in group["observations"])):
                raise ReviewFindingsError("repair does not carry the exact criterion and observations")
        frontmatter = text.split("---", 2)[1]
        files = _frontmatter_list(frontmatter, "files")
        sources = load_wave_tasks(project, wave)
        originals = sorted({task for group in groups for task in group["tasks"] if sources[task]["repo"] == member})
        # A repair names the files it needs. They can be fewer than the batch files, and a plan repair
        # can add a fix site that the source tasks did not declare.
        if (task_id in originals or not set(originals) <= set(_frontmatter_list(frontmatter, "deps"))
                or not files):
            raise ReviewFindingsError("repair dependencies/files do not match its source batch")
        repair = {"task_file": repair_path.relative_to(repo).as_posix(), **entry(proof, repair_path, files),
                  **({"member": member} if member else {})}

        def differ(start: str, end: List[str], paths: List[str]) -> bool:
            return bool(isolation.run_git(where, "diff", "--quiet", start, *end, "--", *paths).returncode)

        def before(start: str, end: str) -> bool:
            return not isolation.run_git(where, "merge-base", "--is-ancestor", start, end).returncode

        @functools.lru_cache(maxsize=None)
        def linked() -> tuple:
            """Landed repairs of the same findings that `deps` join to this repair, in landing order.

            A linked task is done, is in the same repo, carries a finding of this repair in its
            `## Review findings`, and depends on this repair or on a linked task, or the reverse.
            """
            tasks, carried = {task_id: (repair_path, fields)}, {}
            for path in (project / "tasks").glob("T*-*.md"):
                other = _read(path, "task")
                other_fields = isolation.task_frontmatter(other)[0] or {}
                tasks[str(other_fields.get("id"))] = (path, other_fields)
                blocks = re.split(r"(?m)^### ([a-z0-9_]+)\s*$", _section(other, "Review findings") or "")
                carried[str(other_fields.get("id"))] = {
                    (locator, _normalize(line)) for locator, block in zip(blocks[1::2], blocks[2::2])
                    for line in re.findall(r"(?m)^Criterion:(.*)$", block)}

            def traced(finding: tuple, seen: frozenset = frozenset()) -> set:
                """The finding as it started: its locator and criterion in the review of the source tasks.

                A finding on an acceptance criterion of a fix task (`fix-tasks` copies the carried criteria
                there) is the finding with the same criterion that this fix task carries. Two findings with
                the same criterion text and other locators stay two findings.
                """
                owner = re.fullmatch(r"t(\d{3})_ac\d+", finding[0])
                sources = () if not owner else {
                    item for item in carried.get(f"T{owner.group(1)}", ()) if item[1] == finding[1]} - seen
                return {origin for item in sources for origin in traced(item, seen | {finding})} or {finding}

            def origins(other: str) -> set:
                return {origin for finding in carried.get(other, ()) for origin in traced(finding)}

            repairs = {other: task for other, task in tasks.items() if other == task_id or (
                task[1].get("status") == "done" and _common.task_repo_for_path(task[0], other, task[1].get("repo")) == member
                and origins(task_id) & origins(other))}
            joined, more = set(), {task_id}
            while more:
                joined |= more
                more = {other for other, (_, other_fields) in repairs.items() if other not in joined and (
                    joined & set(other_fields.get("deps") or [])
                    or any(other in (repairs[item][1].get("deps") or []) for item in joined))}
            others = sorted(joined - {task_id})
            proofs = isolation.verify_landed_task_files(
                repo, [repairs[other][0] for other in others], f"{project_dir}/tasks", head)["tasks"] if others else []
            links = []
            for landing in proofs:
                path, other_fields = repairs[landing["task_id"]]
                if landing["verdict"] == "recovered":
                    links.append({"task_file": path.relative_to(repo).as_posix(),
                                  **entry(landing, path, list(other_fields.get("files") or []))})
            return tuple(sorted(links, key=lambda link: int(isolation.git_output(where, "rev-list", "--count", at(link)))))

        def linked_changes(start: str, end: List[str], paths: List[str]) -> Optional[List[dict]]:
            """The linked repairs whose landings are every change to `paths` from `start` to `end`, else None."""
            if not differ(start, end, paths):
                return []
            links = [link for link in linked()
                     if before(start, f"{at(link)}^") and (not end or before(at(link), end[0]))]
            for link in links:
                if differ(start, [f"{at(link)}^"], paths):
                    return None
                start = at(link)
            return None if differ(start, end, paths) else links

        original_paths = [Path(sources[task]["path"]) for task in originals]
        original_proofs = isolation.verify_landed_task_files(
            repo, original_paths, f"{project_dir}/tasks", head
        )["tasks"]

        @functools.lru_cache(maxsize=None)
        def source_review() -> Optional[tuple]:
            """The product the source review judged: (coordinator commit, commit in `where`), or None if unrecorded."""
            scopes = [current_product_scope(_read(path, "source review"))
                      for path in review_paths(project, wave, cycle, source["depth"]).values()]
            if not all(scopes):
                return None  # An ordinary review of isolated task products did not judge the collection tip.
            scope = scopes[0]
            recorded = _recorded_review(repo, project, wave, cycle, source["depth"], scope["base"])
            if any(item != scope for item in scopes):
                raise ReviewFindingsError("source review lenses disagree on current-product scope")
            if recorded is not None:
                if recorded.get("product_scope") != "current":
                    raise ReviewFindingsError("driver did not assign a current-product review")
                if {"base": recorded["base"], "member_heads": recorded.get("member_heads", {})} != scope:
                    raise ReviewFindingsError("current-product report differs from its collected assignment")
            elif not review_base:
                return None  # Native/manual publication needs its explicit recorded base too.
            git, commit = _git_reader()
            snapshot = commit(repo, scope["base"])
            if review_base and commit(repo, review_base) != snapshot:
                raise ReviewFindingsError("--review-base differs from the source review snapshot")
            if any(scope["member_heads"].get(name) != sha for name, sha in (member_heads or {}).items()):
                raise ReviewFindingsError("--member-head differs from the source review snapshot")
            for path in review_paths(project, wave, cycle, source["depth"]).values():
                if git(repo, "ls-tree", snapshot, "--", path.relative_to(repo).as_posix()):
                    raise ReviewFindingsError("source review must postdate its review base")
            if isolation.run_git(repo, "merge-base", "--is-ancestor", snapshot, base).returncode:
                raise ReviewFindingsError("review base is not in the repair base")
            if not member:
                return snapshot, snapshot
            product = scope["member_heads"].get(member)
            if product is None:
                raise ReviewFindingsError("member review snapshot is not recorded in source review")
            return snapshot, commit(where, product)

        landed, earlier, anchored = [], set(), None
        for landing in original_proofs:
            links = None
            if landing["verdict"] == "recovered":
                original = entry(landing, Path(sources[landing["task_id"]]["path"]), sources[landing["task_id"]]["files"])
                if before(at(original), at(repair, "base")):
                    links = linked_changes(at(original), [at(repair, "base")], original["files"])
                # Another landing changed these files before the repair base, for example a task of a recovery plan.
                # The source review judged the product at its base, so only the changes after it must be linked.
                snapshot = source_review() if links is None else None
                if snapshot and before(at(original), snapshot[1]) and before(snapshot[1], at(repair, "base")):
                    links = linked_changes(snapshot[1], [at(repair, "base")], original["files"])
                    if links is not None:
                        original["anchor"] = "source-review"
                        anchored = snapshot
            if links is None:
                raise ReviewFindingsError("repair base contains unlinked changes to original product")
            landed.append(original)
            earlier.update(link["task"] for link in links)
        source_paths = list(review_paths(project, wave, cycle, source["depth"]).values())
        source_paths.extend(Path(group["skeptic"]["path"]) for group in groups if group["skeptic"])
        for path in set(source_paths):
            relative = path.relative_to(repo).as_posix()
            at_base = isolation.run_git(repo, "show", f"{base}:{relative}")
            if at_base.returncode or at_base.stdout != _read(path, "source review"):
                raise ReviewFindingsError("source review must be unchanged and precede the repair")
        # A later linked repair supersedes this one: the last one is the product at the tip, and its
        # landing and its Verify are the evidence. The reviewer applies every patch of the chain, so the
        # files of every later linked repair must also change only through the landings of the chain.
        later = linked_changes(at(repair), tip, files)
        chain_files = sorted({*files, *(path for link in later or [] for path in link["files"])})
        last = later[-1] if later else repair
        verification = build_state.verify_lookup(
            str(repo), _common.task_verify_command(_read(repo / last["task_file"], "repair task")), at(last), member)
        if not verification["reuse"]:
            raise ReviewFindingsError("repair has no reusable passing Verify at its landing")
        if later is None or (later and linked_changes(at(repair), tip, chain_files) is None):
            raise ReviewFindingsError("product changed after the repair landing")
        # A parallel landing can have a parent that is newer than the repair base. The reviewer builds the
        # product from the base, so no other landing between the two can change a file of the chain.
        if differ(at(repair, "base"), [f"{at(repair)}^"], chain_files):
            raise ReviewFindingsError("product changed between the repair base and the repair landing")
        return {
            "status": "ok", "command": "repair-evidence", "head": head,
            "source": {"wave": wave, "cycle": cycle}, "groups": groups,
            "originals": landed, "repair": repair,
            **({"source_review": {"review_base": anchored[0], **({"member_head": anchored[1]} if member else {})}}
               if anchored else {}),
            **({"earlier_repairs": [link for link in linked() if link["task"] in earlier]} if earlier else {}),
            **({"later_repairs": later, "chain_files": chain_files} if later else {}),
            "verification": verification,
            "verdict": "evidence-ready; reviewer judges the original unchanged criteria",
        }
    except (isolation.IsolationError, build_state.BuildStateError, ValueError) as error:
        raise ReviewFindingsError(str(error)) from error


# --- reviewed snapshots ------------------------------------------------------


def _git_reader():
    """Read-only git queries cached for one helper call; a failed query raises ValueError."""

    @functools.lru_cache(maxsize=None)
    def git(root: Path, *args: str) -> str:
        completed = _common.run_git(root, *args)
        if completed.returncode:
            raise ValueError(completed.stderr.strip() or "git query failed")
        return completed.stdout

    def commit(root: Path, sha: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("review snapshots must be full commit SHAs")
        return git(root, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()

    return git, commit


def review_snapshots(repo: Path, project_dir: str, base: str, member_heads: Optional[Dict[str, str]],
                     git, commit) -> tuple:
    """Each repository's reviewed commit at the coordinator review `base`, and the members without one.

    Returns ([(member or None, root, commit)], [member]). A member snapshot is the supplied head, else the
    coordinator's last member landing record at `base`, else the locked build base before any landing.
    """
    if __package__:
        from . import isolation
    else:
        import isolation
    # Read membership from the coordinator snapshot, not today's member branch.
    roots = [(None, repo, base)]
    missing_members = []
    members_path = f"{project_dir}/MEMBERS.md"
    if git(repo, "ls-tree", base, "--", members_path):
        membership = git(repo, "show", f"{base}:{members_path}")
        locked = {}
        lock = f"{project_dir}/build/members.json"
        if git(repo, "ls-tree", base, "--", lock):
            for entry in json.loads(git(repo, "show", f"{base}:{lock}")).get("members", []):
                if isinstance(entry, dict) and isinstance(entry.get("base"), str):
                    locked[entry.get("name")] = entry["base"]
        # The coordinator serializes member landings, newest first on its first-parent history.
        records: Dict[str, List[str]] = {}
        for body in git(repo, "log", "--first-parent", "--format=%b%x1e", base).split("\x1e"):
            match = isolation.MEMBER_RECORD_RE.fullmatch(body.strip())
            if match:
                records.setdefault(match.group("member"), []).append(match.group("landing"))
        for name, body in re.findall(r"(?ms)^## ([a-z0-9-]+)\s*\n(.*?)(?=^## |\Z)", membership):
            checkout = re.search(r"(?m)^Checkout: (.+)$", body)
            try:
                if not checkout:
                    raise ValueError("member has no checkout")
                root = Path(checkout.group(1))
                sha = (member_heads or {}).get(name)
                if not sha and name in locked:
                    start = commit(root, locked[name])
                    # An earlier milestone's record does not descend from this build's locked base.
                    def descends(landing: str) -> bool:
                        return _common.run_git(root, "merge-base", "--is-ancestor", start, landing).returncode == 0

                    def replayed(landing: str) -> str:
                        # Only the receipt committed at this review snapshot may
                        # resolve its landings; today's working receipt is not evidence.
                        path = f"{project_dir}/{isolation.MEMBER_REBASE_RECEIPT}"
                        if not git(repo, "ls-tree", base, "--", path):
                            return landing
                        try:
                            receipt = isolation._validate_member_rebase(
                                repo, git(repo, "show", f"{base}:{path}").encode("utf-8"),
                                git(repo, "show", f"{base}:{lock}").encode("utf-8"))
                            isolation._prove_member_rebase_record(repo, receipt)
                        except isolation.IsolationError as error:
                            raise ValueError(str(error)) from error
                        for adoption in receipt["adoptions"]:
                            if adoption["member"] == name:
                                mapping = {row["original"]: row["rebased"] for row in adoption["commits"]}
                                landing = mapping.get(landing, landing)
                        return landing

                    landings = records.get(name, [])
                    sha = next((landing for landing in landings if descends(landing)), None) or next(
                        (moved for moved in map(replayed, landings) if descends(moved)), start)
                if not sha:
                    raise ValueError("member has no recorded snapshot")
                roots.append((name, root, commit(root, sha)))
            except (OSError, ValueError):
                missing_members.append(name)
    return roots, missing_members


# --- informational repair-line attribution ---------------------------------

EVIDENCE_REF = re.compile(
    r"(?<![\w/.:\\-])(?P<path>[^\s`;,():]+\.[A-Za-z0-9_]+):"
    r"(?P<lines>\d+(?:[-–]\d+)?(?:,\d+(?:[-–]\d+)?)*)"
)


def _recurrence_findings(project: Path, wave: int, cycle: int) -> List[dict]:
    plan = _read(project / "plan/PLAN.md", "plan")
    tasks = load_wave_tasks(project, wave)
    findings = []
    for lens, path in review_paths(project, wave, cycle, wave_depth(plan, wave)).items():
        parsed = parse_lens(_read(path, "review"), lens, tasks)
        for item in parsed["findings"]:
            observation = item["observation"]
            prefix = f"{item['criterion']} — "
            # The criterion can contain an em dash; the fix direction names where to change, not evidence.
            found = (observation[len(prefix):] if observation.startswith(prefix)
                     else observation.partition(" — ")[2])
            findings.append({"source": lens, "locator": item["locator"],
                             "criterion": item["criterion"],
                             "evidence": re.split(r"(?:^|\s)fix:\s", found, maxsplit=1)[0]})
    panel = project / "review" / f"wave-{wave}.cycle{cycle}.panel.md"
    if panel.is_file():
        for item in _panel_items(_read(panel, "panel")):
            if item["kind"] == "criterion":
                findings.append({"source": "panel:" + (item["source"] or "unknown"),
                                 "criterion": item["text"], "evidence": item["evidence"]})
    return findings


def recurrence(repo: Path, project_dir: str, wave: int, cycle: int,
               review_base: Optional[str] = None,
               member_heads: Optional[Dict[str, str]] = None) -> dict:
    """Describe findings on repair-written lines, never change review eligibility.

    Each repository needs a recorded review snapshot. No current HEAD/branch
    substitution is allowed: later edits can move the cited line numbers.
    """
    result = {"status": "ok", "wave": wave, "cycle": cycle, "findings": [],
              "files": [], "unattributable": []}
    if not review_base:
        return {**result, "status": "partial",
                "unattributable": [{"reason": "missing-review-base"}]}
    if __package__:
        from . import isolation
    else:
        import isolation
    project_dir = isolation.relative_posix(project_dir)
    repo = repo.resolve()
    project = repo / project_dir

    git, commit = _git_reader()
    base = commit(repo, review_base)
    result["review_base"] = base
    roots, missing_members = review_snapshots(repo, project_dir, base, member_heads, git, commit)
    for name in missing_members:
        result["unattributable"].append({"repo": name, "reason": "missing-member-snapshot"})

    @functools.lru_cache(maxsize=None)
    def origin(member: Optional[str], root: Path, sha: str, product: str) -> Optional[dict]:
        body = git(root, "show", "-s", "--format=%B", sha)
        trailers = re.findall(r"(?m)^Task: (\S+)$", body)
        landed = re.findall(r"(?m)^Base: ([0-9a-f]{40})$", body)
        files = re.search(r"(?ms)^Files:\n(?P<items>(?:- .+\n?)+)", body)
        # Landing bodies name their product files; arbitrary Task text is not provenance.
        if (len(trailers) != 1 or len(landed) != 1
                or not files or f"- {product}" not in files.group("items").splitlines()):
            return None
        path = trailers[0]
        if (not re.fullmatch(re.escape(project_dir) + r"/tasks/(T\d{3})-[a-z0-9-]+\.md", path)
                or not git(repo, "ls-tree", base, "--", path)):
            return None  # archived with an earlier milestone, or never a task here
        text = git(repo, "show", f"{base}:{path}")
        fields, _ = isolation.task_frontmatter(text)
        # Task ids and fix-task names restart each milestone: the landing must be this task's.
        if (not fields or fields.get("status") != "done"
                or fields.get("member_base" if member else "base") != landed[0]):
            return None
        parts = text.split("---", 2)
        section = _strip_comments(_section(parts[2], "Review findings") or "").strip()
        repair = bool(section and section.casefold() not in {"none", "- none"})
        item = {"task": Path(path).name.split("-", 1)[0], "commit": sha, "repair": repair}
        source = re.search(r"-fix-wave-(\d+)-cycle-(\d+)\.md$", path)
        if source:
            item.update(source_wave=int(source[1]), source_cycle=int(source[2]))
        return item

    by_file: dict = {}
    for finding in _recurrence_findings(project, wave, cycle):
        evidence = finding.pop("evidence")
        entry = {**finding, "recurrent": False, "references": []}
        result["findings"].append(entry)
        matches = list(EVIDENCE_REF.finditer(evidence))
        if not matches:
            result["unattributable"].append({"source": finding["source"], "reason": "missing-file-line"})
        for match in matches:
            cited, lines = match.group("path", "lines")
            issue = {"source": finding["source"], "reference": match.group(0)}
            try:
                if cited.startswith("/") or "\\" in cited or cited.startswith("-"):
                    raise ValueError("unsafe-path")
                # Landing bodies list normalized paths; `./a` and `a//b` must compare equal.
                path = posixpath.normpath(cited)
                if path == ".." or path.startswith("../"):
                    raise ValueError("unsafe-path")
                if path.startswith(project_dir + "/"):
                    continue
                candidates = [(name, root, tip) for name, root, tip in roots
                              if git(root, "ls-tree", tip, "--", path).startswith("100")]
                if not candidates:
                    raise ValueError("missing-path" if "/" in path else "bare-name-or-missing-path")
                if len(candidates) != 1 or missing_members:
                    raise ValueError("ambiguous-repository" if len(candidates) != 1 else "incomplete-membership")
                name, root, tip = candidates[0]
                written = {}
                for span in lines.split(","):
                    limits = re.split("[-–]", span)
                    start, end = int(limits[0]), int(limits[-1])
                    if start < 1 or end < start:
                        raise ValueError("invalid-line-range")
                    try:
                        blame = git(root, "blame", "--porcelain", "-L", f"{start},{end}", tip, "--", path)
                    except ValueError:
                        raise ValueError("line-range-outside-reviewed-file") from None
                    for sha in sorted(set(re.findall(r"(?m)^([0-9a-f]{40}) \d+ \d+(?: \d+)?$", blame))):
                        item = origin(name, root, sha, path)
                        if item:
                            written[sha] = item
                reference = {"path": path, "repo": name, "lines": lines, "reviewed_commit": tip,
                             "written_by": list(written.values())}
                entry["references"].append(reference)
                repairs = sorted({item["task"] for item in written.values() if item["repair"]})
                if repairs:
                    entry["recurrent"] = True
                    key = (name or "", path)
                    summary = by_file.setdefault(key, {"path": path, "repo": name, "recurrent_findings": 0,
                                                       "repairs_cited": set(), "findings": set()})
                    summary["repairs_cited"].update(repairs)
                    summary["findings"].add(len(result["findings"]) - 1)
            except (OSError, ValueError) as error:
                result["unattributable"].append({**issue, "reason": str(error)})
    for key in sorted(by_file):
        summary = by_file[key]
        summary["recurrent_findings"] = len(summary.pop("findings"))
        summary["repairs_cited"] = sorted(summary["repairs_cited"])
        result["files"].append(summary)
    if result["unattributable"]:
        result["status"] = "partial"
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("collect", "repair-evidence", "recurrence", "panel-repairs"))
    result.add_argument("--repo", type=Path, required=True, help="absolute repository root")
    result.add_argument("--project-dir", default=DEFAULT_PROJECT_DIR)
    result.add_argument("--wave", type=int, required=True)
    result.add_argument("--cycle", type=int, required=True)
    result.add_argument("--task", help="landed repair task for repair-evidence")
    result.add_argument("--review-base",
                        help="full coordinator commit the review judged, for recurrence or repair-evidence")
    result.add_argument("--member-head", action="append", default=[],
                        help="member=full reviewed commit for recurrence or repair-evidence; repeatable")
    result.add_argument(
        "--helper-failure",
        action="append",
        default=[],
        help="a bundled helper non-zero exit for this cycle; repeatable; recorded as a structural blocker",
    )
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.wave < 1 or arguments.cycle < 1:
            raise ReviewFindingsError("--wave and --cycle must be positive integers")
        heads = {}
        for value in arguments.member_head:
            name, separator, sha = value.partition("=")
            if not separator or not name or name in heads:
                raise ReviewFindingsError("--member-head must be unique member=sha")
            heads[name] = sha
        if arguments.command == "repair-evidence":
            return emit(repair_evidence(arguments.repo, arguments.project_dir,
                                        arguments.wave, arguments.cycle, arguments.task,
                                        arguments.review_base, heads))
        if arguments.command == "recurrence":
            return emit(recurrence(arguments.repo, arguments.project_dir, arguments.wave,
                                   arguments.cycle, arguments.review_base, heads))
        if arguments.command == "panel-repairs":
            return emit(panel_repairs(arguments.repo, arguments.project_dir, arguments.wave, arguments.cycle))
        return emit(
            compute(
                arguments.repo,
                arguments.project_dir,
                arguments.wave,
                arguments.cycle,
                arguments.helper_failure,
            )
        )
    except (ReviewFindingsError, ValueError) as error:
        json.dump({"status": "error", "error": str(error)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
