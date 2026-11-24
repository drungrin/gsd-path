#!/usr/bin/env python3
# gsd-path project runtime
"""Parking lot: deferred work that every milestone boundary must decide.

`.project/PARKING.md` persists across milestones, like LESSONS.md. Items come from
PR change-request follow-ups (recorded PR fix rounds), from an explicit `add`, or
from review warnings the owner parks. Writes happen only in the inspect, define and
plan phases, so the approval checkpoints commit them; ship never writes the lot.

    sync     import follow-ups of recorded PR rounds and settle items the last
             milestone planned (done when a done task cites them, else open again)
    add      park items from a JSON file
    triage   record the owner's decision for items (planned, scheduled, dropped)
    check    gate errors for define approval, plan approval, or program completion
    status   the lot as JSON

Decisions: `planned` puts an item in the milestone being defined; `scheduled` names a
later milestone (a pending ROADMAP milestone in program flow); `dropped` closes it.
A security or data-loss item may be scheduled only with `accept_risk`, and the owner's
words are recorded verbatim. See docs/adr/0007-parking-lot.md.
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence

sys.dont_write_bytecode = True

if __package__:
    from . import _common
else:
    import _common


def _sibling(name: str):
    import importlib
    return importlib.import_module(f"{__package__}.{name}" if __package__ else name)


SCHEMA = "gsd-path/parking/v1"
RELATIVE = ".project/PARKING.md"
CLASSES = ("security", "data-loss", "correctness", "operations", "docs", "other")
RISK_CLASSES = ("security", "data-loss")
DECISIONS = ("planned", "scheduled", "dropped")
HEADING = re.compile(r"^### (?P<id>K\d{3}) — (?P<title>.+)$")
STATUS = re.compile(r"^(open|dropped|(?:planned|scheduled|done) M(\d{3,}))$")
CITATION = re.compile(r"\bK\d{3}\b")
WRITE_PHASES = ("inspect", "define", "research", "decide", "roadmap", "plan")
FIELDS = ("Status", "Class", "Source", "Repo", "Location", "Found", "Since")
HEADER = """# Parking Lot

<!-- Written only by parking.py. Every milestone boundary decides each open item:
     planned for the milestone being defined, scheduled for a named later milestone,
     or dropped with the owner's words. -->

## Items
"""


class ParkingError(RuntimeError):
    """Raised when the parking lot is malformed or a decision is refused."""


# --- model ---------------------------------------------------------------------------

def _one_line(text: object) -> str:
    return " ".join(str(text).split())


def parse(text: str) -> List[dict]:
    items: List[dict] = []
    current: Optional[dict] = None
    in_decisions = False
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            current = {"id": heading.group("id"), "title": heading.group("title").strip(), "decisions": []}
            items.append(current)
            in_decisions = False
            continue
        if current is None:
            continue
        field = re.match(r"^- \*\*(?P<name>[A-Za-z ]+)\*\*:\s*(?P<value>.*)$", line)
        if field:
            name = field.group("name")
            in_decisions = name == "Decisions"
            if name in FIELDS:
                current[name.lower()] = field.group("value").strip()
            elif name != "Decisions":
                raise ParkingError(f"{current['id']} has an unknown field {name}")
            continue
        if in_decisions and line.startswith("  - "):
            current["decisions"].append(line[4:].strip())
    ids = [item["id"] for item in items]
    if ids != [f"K{number:03d}" for number in range(1, len(ids) + 1)]:
        raise ParkingError("parking lot ids must be K001.. contiguous and ordered")
    for item in items:
        missing = [name for name in FIELDS if not item.get(name.lower())]
        if missing:
            raise ParkingError(f"{item['id']} is missing {', '.join(missing)}")
        if not STATUS.fullmatch(item["status"]):
            raise ParkingError(f"{item['id']} has an invalid Status: {item['status']}")
        if item["class"] not in CLASSES:
            raise ParkingError(f"{item['id']} Class must be one of {', '.join(CLASSES)}")
    return items


def render(items: Sequence[Mapping[str, object]]) -> str:
    lines = [HEADER]
    for item in items:
        lines += [f"### {item['id']} — {_one_line(item['title'])}", ""]
        lines += [f"- **{name}**: {_one_line(item[name.lower()])}" for name in FIELDS]
        lines.append("- **Decisions**:")
        lines += [f"  - {_one_line(decision)}" for decision in item["decisions"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load(repo: Path) -> List[dict]:
    path = repo / RELATIVE
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise ParkingError(f"{RELATIVE} must be a regular file")
    return parse(path.read_text(encoding="utf-8"))


def save(repo: Path, items: Sequence[Mapping[str, object]]) -> None:
    _common.atomic_write(repo / RELATIVE, render(items))


# --- milestones ----------------------------------------------------------------------

def _state(repo: Path):
    return _sibling("pipeline_state").load_state(repo)[0]


def milestone_number(repo: Path, write: bool = False) -> int:
    state = _state(repo)
    if write and state.phase not in WRITE_PHASES:
        raise ParkingError(f"the parking lot changes only in {', '.join(WRITE_PHASES)}; STATE is {state.phase}, "
                           "so the next milestone boundary records it")
    branch = str(state.branch or "")
    number = _sibling("pipeline_state")._bound_branch_number(branch)
    if number is None:
        raise ParkingError(f"the bound branch {branch!r} does not name a milestone number")
    return number


def _label(number: int) -> str:
    return f"M{number:03d}"


def _today() -> str:
    return datetime.date.today().isoformat()


def _pending_roadmap(repo: Path) -> Optional[List[int]]:
    """Pending milestone numbers in program flow; None for a single-milestone project."""
    roadmap = repo / ".project/ROADMAP.md"
    if not (repo / ".project/CHARTER.md").is_file() or not roadmap.is_file():
        return None
    blocks = _sibling("roadmap").roadmap_blocks(roadmap.read_text(encoding="utf-8"))
    return sorted(int(block["id"][1:]) for block in blocks
                  if block["fields"].get("Status", (-1, ""))[1] == "pending")


def _status(item: Mapping[str, object]) -> tuple:
    match = STATUS.fullmatch(str(item["status"]))
    word = str(item["status"]).split()[0]
    return word, int(match.group(2)) if match and match.group(2) else None


# --- sync ----------------------------------------------------------------------------

def task_rows(tasks_dir: Path) -> List[dict]:
    """id, status and Context text of each task file; plan_errors and settlement read these."""
    rows = []
    for path in sorted(tasks_dir.glob("T*.md")) if tasks_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        status = re.search(r"(?m)^status:\s*(\S+)", text)
        context = re.search(r"(?ms)^## Context\s*$(.*?)^## ", text)
        task_id = re.search(r"(?m)^id:\s*(T\d{3})", text)
        if task_id:
            rows.append({"id": task_id.group(1), "status": status.group(1) if status else "",
                         "context": context.group(1) if context else ""})
    return rows


def _citing_done_tasks(tasks_dir: Path) -> Dict[str, List[str]]:
    cited: Dict[str, List[str]] = {}
    for row in task_rows(tasks_dir):
        if row["status"] == "done":
            for item in set(CITATION.findall(row["context"])):
                cited.setdefault(item, []).append(row["id"])
    return cited


def _archive_for(repo: Path, number: int) -> Optional[Path]:
    matches = sorted((repo / ".project/archive").glob(f"{number:03d}-*")) if (repo / ".project/archive").is_dir() else []
    return matches[-1] if matches else None


def _round_follow_ups(repo: Path) -> List[dict]:
    """Follow-up items of every recorded PR round (lane or record-only) in this checkout."""
    lane = _sibling("pr_fix_lane")
    root = lane.lane_root(repo)
    found: List[dict] = []
    for record_path in sorted(root.glob("*/round-*/round.json")) if root.is_dir() else []:
        directory = record_path.parent
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            ruling = json.loads((directory / "ruling.json").read_text(encoding="utf-8"))
            snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        known = {item["id"]: item for item in snapshot.get("items", [])}
        for row in ruling.get("items", []):
            if row.get("class") != "follow-up" or row.get("id") not in known:
                continue
            item = known[row["id"]]
            found.append({"title": _one_line(row.get("title") or item["text"])[:120],
                          "class": row.get("risk") if row.get("risk") in CLASSES else "other",
                          "source": item["link"] if item.get("link") not in (None, "", "none")
                          else f"{record.get('pull_request')}#{row['id']}",
                          "repo": record.get("member") or "coordinator",
                          "location": item.get("location") or "none",
                          "found": _one_line(item["text"]),
                          "ruling": _one_line(ruling.get("verbatim") or row.get("note") or "")})
    return found


def sync(repo: Path) -> dict:
    """Import PR round follow-ups and settle items the previous milestones planned."""
    repo = repo.resolve()
    current = milestone_number(repo, write=True)
    items = load(repo)
    sources = {item["source"] for item in items}
    imported, settled, reopened = [], [], []
    rounds = [(row, "a PR change request") for row in _round_follow_ups(repo)]
    # ADR 0010: follow-ups the owner ruled in a pre-PR review, from the archived reports.
    rounds += [(row, "a pre-PR review") for row in _sibling("pre_pr").follow_ups(repo)]
    for follow_up, origin in rounds:
        if follow_up["source"] in sources:
            continue
        item = _new_item(items, follow_up, current, f"parked from {origin}: \"{follow_up['ruling']}\"")
        sources.add(item["source"])
        imported.append(item["id"])
    for item in items:
        word, number = _status(item)
        if word != "planned" or number is None or number >= current:
            continue
        archive = _archive_for(repo, number)
        tasks = _citing_done_tasks(archive / "tasks") if archive else {}
        if tasks.get(item["id"]):
            item["status"] = f"done {_label(number)}"
            item["decisions"].append(f"{_today()} ({_label(current)}) — done in {_label(number)} by "
                                     f"{', '.join(sorted(tasks[item['id']]))}")
            settled.append(item["id"])
        else:
            item["status"] = "open"
            item["decisions"].append(f"{_today()} ({_label(current)}) — reopened: no done task of "
                                     f"{_label(number)} cites it")
            reopened.append(item["id"])
    if imported or settled or reopened:
        save(repo, items)
    return {"schema": SCHEMA, "imported": imported, "settled": settled, "reopened": reopened,
            "open": [item["id"] for item in items if item["status"] == "open"]}


def _new_item(items: List[dict], values: Mapping[str, object], current: int, decision: str) -> dict:
    item = {"id": f"K{len(items) + 1:03d}", "title": _one_line(values["title"]), "status": "open",
            "class": values["class"], "source": _one_line(values["source"]),
            "repo": _one_line(values.get("repo") or "coordinator"),
            "location": _one_line(values.get("location") or "none"), "found": _one_line(values["found"]),
            "since": f"{_today()} ({_label(current)})",
            "decisions": [f"{_today()} ({_label(current)}) — {decision}"]}
    if item["class"] not in CLASSES:
        raise ParkingError(f"class must be one of {', '.join(CLASSES)}")
    for name in ("title", "source", "found"):
        if not item[name]:
            raise ParkingError(f"a parked item needs a {name}")
    items.append(item)
    return item


def add(repo: Path, source_file: Path) -> dict:
    """Park the items of a JSON file: {"verbatim": "...", "items": [{title, class, source, ...}]}."""
    repo = repo.resolve()
    current = milestone_number(repo, write=True)
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    verbatim = _one_line(payload.get("verbatim") or "")
    if not verbatim:
        raise ParkingError("parking items needs the owner's words verbatim")
    items = load(repo)
    sources = {item["source"] for item in items}
    added, skipped = [], []
    for values in payload.get("items") or []:
        if _one_line(values.get("source") or "") in sources:
            skipped.append(values.get("source"))
            continue
        item = _new_item(items, values, current, f"parked: \"{verbatim}\"")
        sources.add(item["source"])
        added.append(item["id"])
    if added:
        save(repo, items)
    return {"schema": SCHEMA, "added": added, "skipped": skipped}


# --- triage --------------------------------------------------------------------------

def triage(repo: Path, ruling_file: Path) -> dict:
    """Record decisions: {"verbatim", "items": [{"id", "decision", "milestone"?, "accept_risk"?}]}."""
    repo = repo.resolve()
    current = milestone_number(repo, write=True)
    ruling = json.loads(ruling_file.read_text(encoding="utf-8"))
    verbatim = _one_line(ruling.get("verbatim") or "")
    if not verbatim:
        raise ParkingError("a triage needs the owner's words verbatim")
    items = {item["id"]: item for item in load(repo)}
    pending = _pending_roadmap(repo)
    decided = []
    for row in ruling.get("items") or []:
        item = items.get(row.get("id"))
        if item is None:
            raise ParkingError(f"unknown parking item {row.get('id')}")
        decision = row.get("decision")
        if decision not in DECISIONS:
            raise ParkingError(f"{item['id']} decision must be one of {', '.join(DECISIONS)}")
        word, _ = _status(item)
        if word in ("done", "dropped"):
            raise ParkingError(f"{item['id']} is already {item['status']}")
        if decision == "scheduled":
            target = re.fullmatch(r"M(\d{3,})", str(row.get("milestone") or ""))
            if not target or int(target.group(1)) <= current:
                raise ParkingError(f"{item['id']} must be scheduled for a later milestone than {_label(current)}")
            number = int(target.group(1))
            if pending is not None and number not in pending:
                raise ParkingError(f"{item['id']}: {_label(number)} is not a pending ROADMAP milestone; plan it now, "
                                   "drop it, or add a milestone with a roadmap re-slice")
            if item["class"] in RISK_CLASSES and not row.get("accept_risk"):
                raise ParkingError(f"{item['id']} is a {item['class']} item: scheduling it later needs accept_risk")
            item["status"] = f"scheduled {_label(number)}"
        elif decision == "planned":
            item["status"] = f"planned {_label(current)}"
        else:
            item["status"] = "dropped"
        risk = "; accept-risk" if decision == "scheduled" and row.get("accept_risk") else ""
        item["decisions"].append(f"{_today()} ({_label(current)}) — {item['status']}{risk}: \"{verbatim}\"")
        decided.append(item["id"])
    save(repo, list(items.values()))
    return {"schema": SCHEMA, "decided": decided}


# --- gates ---------------------------------------------------------------------------

def define_errors(repo: Path) -> List[str]:
    """Every item is decided for the milestone being defined."""
    if not (repo / RELATIVE).exists():
        return []
    current = milestone_number(repo)
    pending = _pending_roadmap(repo)
    errors = []
    for item in load(repo):
        word, number = _status(item)
        if word == "open":
            errors.append(f"{item['id']} is open; decide it (planned, scheduled, or dropped)")
        elif word == "scheduled" and number is not None and number <= current:
            errors.append(f"{item['id']} was scheduled for {_label(number)}; plan it now or decide again")
        elif word == "scheduled" and pending is not None and number not in pending:
            errors.append(f"{item['id']} is scheduled for {_label(number)}, which is no longer a pending ROADMAP "
                          "milestone; plan it now, drop it, or re-slice the roadmap")
        elif word == "planned" and number is not None and number < current:
            errors.append(f"{item['id']} is still planned for {_label(number)}; run parking.py sync")
    return errors


def plan_errors(repo: Path, tasks: Sequence[Mapping[str, object]]) -> List[str]:
    """Every item planned for this milestone is closed by a task that cites it."""
    if not (repo / RELATIVE).exists():
        return []
    current = milestone_number(repo)
    items = {item["id"]: item for item in load(repo)}
    planned = {item_id for item_id, item in items.items() if _status(item) == ("planned", current)}
    cited: Dict[str, List[str]] = {}
    for task in tasks:
        if str(task.get("status") or "") == "withdrawn":
            continue
        for item_id in set(CITATION.findall(str(task.get("context") or ""))):
            cited.setdefault(item_id, []).append(str(task["id"]))
    errors = [f"{item_id} is planned for {_label(current)} but no task cites it in its Context"
              for item_id in sorted(planned - set(cited))]
    errors += [f"{', '.join(task_ids)} cite {item_id}, which is not planned for {_label(current)}"
               for item_id, task_ids in sorted(cited.items()) if item_id in items and item_id not in planned]
    return errors


def program_errors(repo: Path) -> List[str]:
    """Program completion needs every item closed and every PR round follow-up parked."""
    repo = repo.resolve()
    items = load(repo)
    errors = []
    for item in items:
        word, number = _status(item)
        if word in ("done", "dropped"):
            continue
        if word == "planned" and number is not None:
            archive = _archive_for(repo, number)
            if archive and _citing_done_tasks(archive / "tasks").get(item["id"]):
                continue
        errors.append(f"{item['id']} is {item['status']} at program completion")
    sources = {item["source"] for item in items}
    errors += [f"PR follow-up {follow_up['source']} was never parked" for follow_up in _round_follow_ups(repo)
               if follow_up["source"] not in sources]
    return errors


def status(repo: Path) -> dict:
    items = load(repo.resolve())
    return {"schema": SCHEMA, "items": [{key: item[key] for key in ("id", "title", "status", "class", "source")}
                                        for item in items]}


# --- CLI -----------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "add", "triage", "check", "status"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        if name == "add":
            command.add_argument("--items", type=Path, required=True)
        if name == "triage":
            command.add_argument("--ruling", type=Path, required=True)
        if name == "check":
            command.add_argument("--gate", choices=("define", "program"), required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            result = sync(args.repo)
        elif args.command == "add":
            result = add(args.repo, args.items)
        elif args.command == "triage":
            result = triage(args.repo, args.ruling)
        elif args.command == "check":
            errors = define_errors(args.repo.resolve()) if args.gate == "define" else program_errors(args.repo.resolve())
            result = {"schema": SCHEMA, "gate": args.gate, "errors": errors}
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 1 if errors else 0
        else:
            result = status(args.repo)
    except (ParkingError, RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    from _common import cli_receipt

    raise SystemExit(cli_receipt(main))
