#!/usr/bin/env python3
# gsd-path project runtime
"""PR fix lane: answer a change request on an open member PR without a full milestone cycle.

The lane keeps the ordinary frame (uncommitted-archive undo, archive transaction,
pending-close rebind and exact-lease source advance) and replaces its middle:

    snapshot  entry checks, forge snapshot, and a baseline capsule (before undo)
    start     record the owner's ruling, undo the archive, write the pr-review
              source and PATCH-FINDINGS.md, and block ship so patch mode opens
    views     after the delta wave and the project Verify, generate FINAL.md and
              the gap reports as views of the capsule plus the delta review
    rule      record the ruling of a record-only round (the ordinary route keeps its frame)
    threads   after republication, answer every item in one review and resolve the fixed threads
    ship-check  refuse shipment while a change request is still active

See docs/adr/0006-pr-review-fix-lane.md.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

sys.dont_write_bytecode = True

if __package__:
    from . import _common
else:
    import _common


def _sibling(name: str):
    import importlib
    return importlib.import_module(f"{__package__}.{name}" if __package__ else name)


SCHEMA = "gsd-path/pr-fix-lane/v1"
SETTING = "pr_fix_lane"
LANE_EVENT = "PR fix lane opened"
MAX_ROUNDS = 3
SOURCE_NAME = re.compile(r"^pr-review-(?P<round>[1-9]\d*)\.md$")
ROUND_DIR = re.compile(r"^pr-fix-(?P<round>[1-9]\d*)$")
GAP_NAME = re.compile(r"^final-gap-(?P<number>[1-9]\d*)\.md$")
PULL_URL = re.compile(r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>[1-9]\d*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
CLASSES = ("fix", "follow-up", "redesign")
DECISIVE_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
REPLY_MARKER = "<!-- gsd-path-pr-fix: {key} -->"
FINISHED_STAGES = {"viewed", "replied"}
REPLY_TEXT = {
    "en": {"fix": "Fixed in `{head}`: {note}", "follow-up": "Left for a later change: {note}",
           "summary": "PR fix round {round}, published in `{head}`.",
           "fixed": "fixed", "later": "left for later"},
    "pt-BR": {"fix": "Corrigi em `{head}`: {note}", "follow-up": "Deixei para depois: {note}",
              "summary": "Rodada {round} de correção do PR, publicada em `{head}`.",
              "fixed": "corrigido", "later": "fica para depois"},
}
DELTA_SECTION = "PR fix delta"


class LaneError(RuntimeError):
    """Raised when the lane cannot enter, proceed, or prove its outputs."""


# --- small helpers -----------------------------------------------------------------

def _git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True,
                            encoding="utf-8", errors="surrogateescape")
    if check and result.returncode != 0:
        raise LaneError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_digest(value: object) -> str:
    return _digest_bytes(_canonical_json(value).encode("utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(path, _canonical_json(value))


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LaneError(f"{path} must hold a JSON object")
    return value


def one_line(text: str) -> str:
    """The single-line form of a review item used in Markdown fields."""
    return " ".join(str(text).split())


def _common_dir(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def lane_root(repo: Path) -> Path:
    return _common_dir(repo) / "gsd-path" / "pr-fix"


def _archive_name(archive: str) -> str:
    return PurePosixPath(archive.rstrip("/")).name


def _round_dir(repo: Path, archive_name: str, round_number: int) -> Path:
    return lane_root(repo) / archive_name / f"round-{round_number}"


def _state(repo: Path):
    return _sibling("pipeline_state").load_state(repo)[0]


def setting_enabled(repo: Path) -> bool:
    """The project setting wins; otherwise the user default; the lane is off by default."""
    for path in (repo / ".project/config.json", Path.home() / ".gsd-path/config.json"):
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return False
        if isinstance(value, dict) and SETTING in value:
            return value[SETTING] == "on"
    return False


# --- the active round --------------------------------------------------------------

def active_round(repo: Path) -> Optional[dict]:
    """The lane round bound to this milestone's branch, if one is open; else None."""
    pointer = lane_root(repo) / "active.json"
    if not pointer.is_file():
        return None
    try:
        record = _read_json(pointer)
        state = _state(repo)
    except (LaneError, ValueError, OSError, RuntimeError):
        return None
    if record.get("branch") != state.branch or record.get("status") == "closed":
        return None
    directory = _round_dir(repo, str(record["archive_name"]), int(record["round"]))
    if not (directory / "round.json").is_file():
        return None
    return dict(_read_json(directory / "round.json"), directory=str(directory))


def lane_round(repo: Path) -> Optional[dict]:
    """The open round when it runs the lane; a record-only round never changes the plan or build."""
    record = active_round(repo)
    return record if record is not None and record.get("mode", "lane") == "lane" else None


def _set_active(repo: Path, round_record: Mapping[str, object], status: str = "open") -> None:
    _write_json(lane_root(repo) / "active.json", {
        "archive_name": round_record["archive_name"], "branch": round_record["branch"],
        "round": round_record["round"], "status": status})


def _save_round(repo: Path, record: dict) -> None:
    directory = _round_dir(repo, str(record["archive_name"]), int(record["round"]))
    _write_json(directory / "round.json", {key: value for key, value in record.items() if key != "directory"})


# --- forge reads (GitHub) ----------------------------------------------------------

def _default_api(*arguments: str) -> object:
    return _sibling("integration").github_api_json(*arguments)


def _pages(value: object) -> list:
    if isinstance(value, list) and all(isinstance(page, list) for page in value):
        return [item for page in value for item in page]
    if isinstance(value, list):
        return value
    raise LaneError("GitHub returned an unexpected list shape")


def pull_parts(url: str) -> Tuple[str, int]:
    match = PULL_URL.fullmatch(url or "")
    if not match:
        raise LaneError(f"the PR fix lane supports GitHub pull requests only, not {url!r}")
    return match.group("repo"), int(match.group("number"))


def read_forge(url: str, api: Callable[..., object] = _default_api) -> dict:
    repository, number = pull_parts(url)
    pull = api(f"repos/{repository}/pulls/{number}")
    reviews = _pages(api(f"repos/{repository}/pulls/{number}/reviews", "--paginate", "--slurp"))
    comments = _pages(api(f"repos/{repository}/pulls/{number}/comments", "--paginate", "--slurp"))
    if not isinstance(pull, dict):
        raise LaneError("GitHub returned an unexpected pull request")
    return {"repository": repository, "number": number, "pull": pull, "reviews": reviews,
            "comments": comments}


def active_change_requests(reviews: Sequence[dict]) -> List[dict]:
    """The latest decisive review per reviewer, when that review requests changes."""
    latest: Dict[str, dict] = {}
    for review in sorted(reviews, key=lambda item: (str(item.get("submitted_at") or ""), int(item.get("id") or 0))):
        if review.get("state") not in DECISIVE_REVIEW_STATES:
            continue
        login = str((review.get("user") or {}).get("login") or "")
        latest[login] = review
    return [review for review in latest.values() if review.get("state") == "CHANGES_REQUESTED"]


def review_items(requests: Sequence[dict], comments: Sequence[dict]) -> List[dict]:
    """One item per non-empty change-request body and per top-level inline comment."""
    items: List[dict] = []
    ids = {int(review["id"]) for review in requests}
    for review in sorted(requests, key=lambda item: int(item["id"])):
        if one_line(review.get("body") or ""):
            items.append({"id": f"R{review['id']}", "kind": "review", "review": int(review["id"]),
                          "location": "none", "link": review.get("html_url") or "none",
                          "text": review.get("body") or ""})
    for comment in sorted(comments, key=lambda item: int(item["id"])):
        if comment.get("in_reply_to_id") or int(comment.get("pull_request_review_id") or 0) not in ids:
            continue
        if not one_line(comment.get("body") or ""):
            continue
        line = comment.get("line") or comment.get("original_line")
        location = f"{comment.get('path')}:{line}" if comment.get("path") else "none"
        items.append({"id": f"C{comment['id']}", "kind": "inline", "review": int(comment["pull_request_review_id"]),
                      "location": location, "link": comment.get("html_url") or "none",
                      "text": comment.get("body") or ""})
    return items


# --- entry and capsule -------------------------------------------------------------

def _archived_review(repo: Path, archive: str) -> Path:
    return repo / archive.rstrip("/") / "review"


def _pending_rows(repo: Path, archive_name: str) -> List[dict]:
    rows = _sibling("members").read_member_close(_common_dir(repo), archive_name)
    return [row for row in rows if row.get("status") == "pending"]


def _final_header_heads(text: str) -> Tuple[str, Dict[str, str], str]:
    contracts = _sibling("check_handoffs")
    head = contracts._reviewed_head(text, "FINAL.md")
    members = contracts.member_reviewed_heads(text, "FINAL.md")
    verdict = contracts._line_value(text, "Overall verdict:")
    return head, members, verdict


def _count_rounds(review: Path) -> int:
    return sum(1 for path in review.iterdir() if path.is_dir() and ROUND_DIR.fullmatch(path.name)) \
        if review.is_dir() else 0


def snapshot(repo: Path, api: Callable[..., object] = _default_api, allow_more_rounds: bool = False,
             record_only: bool = False) -> dict:
    """Check entry, read the change request, and write the baseline capsule (no mutation of .project/).

    With record_only the round only records the items and the ruling so the ordinary route can
    answer and resolve the PR conversations at republication; it never enters the lane.
    """
    repo = repo.resolve()
    if not record_only and not setting_enabled(repo):
        raise LaneError("the PR fix lane is off; set pr_fix_lane to on in Path settings or use the ordinary route "
                        "(snapshot --record-only still records the items for the PR conversations)")
    state = _state(repo)
    if (state.phase, state.status) != ("ship", "active") or not state.archive:
        raise LaneError("the PR fix lane starts from ship/active with a prepared archive")
    existing = active_round(repo)
    if existing and existing.get("archive_name") == _archive_name(state.archive):
        if existing.get("stage") == "captured":
            return _summary(existing)  # the same capture, retried before the ruling
        if existing.get("stage") not in FINISHED_STAGES:
            raise LaneError(f"PR fix lane round {existing['round']} is still in progress ({existing['stage']})")
        # The previous round published its views; a new change request opens the next round.
        _set_active(repo, existing, status="closed")
    archive_name = _archive_name(state.archive)
    review = _archived_review(repo, state.archive)
    final_text = (review / "FINAL.md").read_text(encoding="utf-8")
    head = _git(repo, "rev-parse", "HEAD")
    final_head, final_members, verdict = _final_header_heads(final_text)
    if final_head != head or verdict != "pass":
        raise LaneError("the prepared FINAL.md must pass at the current HEAD before the lane can inherit it")
    gaps = sorted((path for path in review.iterdir() if GAP_NAME.fullmatch(path.name)),
                  key=lambda path: int(GAP_NAME.fullmatch(path.name).group("number")))
    contracts = _sibling("check_handoffs")
    for gap in gaps:
        text = gap.read_text(encoding="utf-8")
        if contracts._line_value(text, "Gap verdict:") != "pass":
            raise LaneError(f"{gap.name} does not pass; the lane inherits only passing gaps")
    if not final_members:
        raise LaneError("the PR fix lane is for member pull-request integration; use the ordinary route")
    pending = _pending_rows(repo, archive_name)
    candidates = [row for row in pending if row.get("mode") == "pull-request" and row.get("pull_request")]
    if not candidates:
        raise LaneError("no pending member pull request; use the ordinary route")
    rounds = max(_count_rounds(review), _recorded_rounds(repo, archive_name)) + 1
    if rounds > MAX_ROUNDS and not allow_more_rounds and not record_only:
        raise LaneError(f"the lane already ran {MAX_ROUNDS} rounds; use the ordinary route, "
                        "or rerun snapshot with --allow-more-rounds after an explicit owner ruling")
    selected = None
    forge = None
    for row in candidates:
        pull_parts(str(row["pull_request"]))
        read = read_forge(str(row["pull_request"]), api)
        pull = read["pull"]
        if pull.get("state") != "open" or pull.get("merged_at"):
            raise LaneError(f"{row['pull_request']} is not open and unmerged")
        if (pull.get("head") or {}).get("sha") != row.get("reviewed_head"):
            raise LaneError(f"{row['pull_request']} head differs from the pending close's reviewed head")
        if final_members.get(str(row["name"])) != row.get("reviewed_head"):
            raise LaneError(f"FINAL.md member head for {row['name']} differs from its pending close")
        if active_change_requests(read["reviews"]):
            if selected is not None:
                raise LaneError("change requests on two member PRs; answer one PR per round")
            selected, forge = row, read
    if selected is None or forge is None:
        raise LaneError("no active change request on the pending member pull requests")
    requests = active_change_requests(forge["reviews"])
    items = review_items(requests, forge["comments"])
    if not items:
        raise LaneError("the change request has no item text to answer")
    snapshot_value = {
        "pull_request": selected["pull_request"], "member": selected["name"],
        "head": (forge["pull"].get("head") or {}).get("sha"),
        "body": forge["pull"].get("body") or "",
        "reviews": [{"id": int(item["id"]), "user": (item.get("user") or {}).get("login"),
                     "state": item.get("state"), "submitted_at": item.get("submitted_at"),
                     "commit_id": item.get("commit_id"), "body": item.get("body") or ""}
                    for item in requests],
        "items": items,
    }
    directory = _round_dir(repo, archive_name, rounds)
    if directory.exists() and (directory / "round.json").is_file():
        record = _read_json(directory / "round.json")
        if record.get("snapshot_digest") != _json_digest(snapshot_value):
            raise LaneError(f"{directory} holds a different capsule; resolve it before a new snapshot")
        return _summary(dict(record, directory=str(directory)))
    capsule = directory / "capsule"
    files: Dict[str, str] = {}
    for path in sorted(review.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(review).as_posix()
            if relative == "FINAL.md" or GAP_NAME.fullmatch(relative) or ROUND_DIR.fullmatch(relative.split("/")[0]):
                target = capsule / "review" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                files[f"review/{relative}"] = _digest(path)
    publications = repo / ".project/publications" / archive_name
    if publications.is_dir():
        for path in sorted(publications.iterdir()):
            if path.is_file() and not path.is_symlink():
                target = capsule / "publications" / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                files[f"publications/{path.name}"] = _digest(path)
    _write_json(directory / "snapshot.json", snapshot_value)
    archive = repo / state.archive.rstrip("/")
    record = {
        "schema": SCHEMA, "round": rounds, "archive": state.archive.rstrip("/"), "archive_name": archive_name,
        "branch": state.branch, "head": head, "members": final_members,
        "pull_request": selected["pull_request"], "member": selected["name"],
        "review_ids": sorted(int(item["id"]) for item in requests),
        "snapshot_digest": _json_digest(snapshot_value), "capsule_files": files,
        "contracts": {name: _digest(archive / relative) for name, relative in
                      (("INTENT.md", "intent/INTENT.md"), ("PLAN.md", "plan/PLAN.md"))
                      if (archive / relative).is_file()},
        "gaps": [path.name for path in gaps],
        "stage": "captured", "mode": "record" if record_only else "lane",
    }
    record["capsule_digest"] = _json_digest({"files": files, "head": head, "members": final_members})
    _save_round(repo, record)
    _set_active(repo, record)
    return _summary(dict(record, directory=str(directory)))


def _recorded_rounds(repo: Path, archive_name: str) -> int:
    """Rounds already recorded for this archive; record-only rounds leave no pr-fix directory."""
    root = lane_root(repo) / archive_name
    return sum(1 for path in root.glob("round-*/round.json")) if root.is_dir() else 0


def _summary(record: Mapping[str, object]) -> dict:
    directory = Path(str(record["directory"]))
    snap = _read_json(directory / "snapshot.json")
    return {"schema": SCHEMA, "status": record.get("stage"), "round": record["round"],
            "mode": record.get("mode", "lane"),
            "pull_request": record["pull_request"], "member": record["member"],
            "directory": str(directory),
            "items": [{key: item[key] for key in ("id", "kind", "location", "link")} | {"text": one_line(item["text"])}
                      for item in snap["items"]]}


# --- ruling and start ---------------------------------------------------------------

def _validate_ruling(ruling: Mapping[str, object], snap: Mapping[str, object], lane: bool = True) -> List[dict]:
    if not isinstance(ruling.get("verbatim"), str) or not ruling["verbatim"].strip():
        raise LaneError("the ruling must carry the owner's answer verbatim")
    known = {item["id"]: item for item in snap["items"]}
    rows = ruling.get("items")
    if not isinstance(rows, list) or not rows:
        raise LaneError("the ruling must classify every item")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in known:
            raise LaneError(f"ruling names an unknown item: {row!r}")
        if row["id"] in seen:
            raise LaneError(f"ruling repeats item {row['id']}")
        seen.add(row["id"])
        if row.get("class") not in CLASSES:
            raise LaneError(f"item {row['id']} class must be one of {', '.join(CLASSES)}")
        needed = {"fix": ("title", "fix_direction", "group") if lane else ("fix_direction",),
                  "redesign": ("fix_direction",), "follow-up": ("note",)}[row["class"]]
        for field in needed:
            if not isinstance(row.get(field), str) or not one_line(row[field]):
                raise LaneError(f"{row['class']} item {row['id']} needs a {field}")
    if seen != set(known):
        missing = ", ".join(sorted(set(known) - seen))
        raise LaneError(f"the ruling must classify every item; missing {missing}")
    if lane and any(row["class"] == "redesign" for row in rows):
        raise LaneError("a selected item is a redesign; the round uses the ordinary route")
    fixes = [row for row in rows if row["class"] in ("fix", "redesign")]
    if lane and not fixes:
        raise LaneError("the ruling selects no fix item")
    return fixes


def source_text(record: Mapping[str, object], snap: Mapping[str, object], fixes: Sequence[dict]) -> str:
    known = {item["id"]: item for item in snap["items"]}
    lines = [f"# PR Review — round {record['round']}", "",
             f"Reviewed HEAD: {record['head']}", "Review verdict: changes-requested",
             f"Pull request: {record['pull_request']}", f"Member: {record['member']}",
             f"Snapshot: {record['snapshot_digest']}", "", "## Items", ""]
    for row in fixes:
        item = known[row["id"]]
        lines += [f"### Item {row['id']} — {one_line(row['title'])}", "",
                  f"- **Kind**: {item['kind']}", f"- **Location**: {item['location']}",
                  f"- **Link**: {item['link']}", "- **Class**: fix", f"- **Group**: {one_line(row['group'])}",
                  f"- **Found**: {one_line(item['text'])}", f"- **Fix direction**: {one_line(row['fix_direction'])}", ""]
    return "\n".join(lines).rstrip() + "\n"


def findings_text(record: Mapping[str, object], snap: Mapping[str, object], fixes: Sequence[dict]) -> str:
    known = {item["id"]: item for item in snap["items"]}
    source = f".project/review/pr-review-{record['round']}.md"
    lines = ["# Patch Findings", "", f"Reviewed HEAD: {record['head']}", "State: ship/blocked", "",
             "## Findings", ""]
    for index, row in enumerate(fixes, start=1):
        lines += [f"### P{index:03d} — {one_line(row['title'])}", "",
                  f"- **Source**: `{source}`", f"- **Locator**: Item {row['id']}",
                  f"- **Evidence**: {one_line(known[row['id']]['text'])}",
                  f"- **Fix direction**: {one_line(row['fix_direction'])}", ""]
    return "\n".join(lines).rstrip() + "\n"


def start(repo: Path, ruling_path: Path) -> dict:
    """Record the ruling, undo the archive, write the hand-off, and block ship for patch mode."""
    repo = repo.resolve()
    record = lane_round(repo)
    if record is None:
        raise LaneError("no captured lane round; run snapshot first (a record-only round uses rule)")
    directory = Path(record.pop("directory"))
    snap = _read_json(directory / "snapshot.json")
    if record["stage"] == "captured":
        ruling = _read_json(ruling_path)
        fixes = _validate_ruling(ruling, snap)
        _write_json(directory / "ruling.json", ruling)
        record.update(stage="ruled", ruling_digest=_digest(directory / "ruling.json"),
                      fixes=[row["id"] for row in fixes])
        _save_round(repo, record)
    ruling = _read_json(directory / "ruling.json")
    if _digest(directory / "ruling.json") != record.get("ruling_digest"):
        raise LaneError("the recorded ruling changed after it was accepted")
    fixes = _validate_ruling(ruling, snap)
    state = _state(repo)
    head = _git(repo, "rev-parse", "HEAD")
    if head != record["head"]:
        raise LaneError("HEAD moved after the capsule was written; the lane cannot continue")
    if record["stage"] == "ruled":
        if state.archive:
            undo = _sibling("pipeline_undo")
            previewed = undo.preview(repo)
            if previewed["target"].get("kind") != "uncommitted-archive":
                reasons = "; ".join(previewed["target"].get("blocked") or []) or "no uncommitted archive"
                raise LaneError(f"the prepared archive cannot be undone safely ({reasons}); use the ordinary route")
            undo.apply_undo(repo, "uncommitted-archive", previewed["target"]["head"])
            state = _state(repo)
        if state.archive or (state.phase, state.status) != ("ship", "active"):
            raise LaneError("the archive undo did not return ship/active without an archive")
        record["stage"] = "undone"
        _save_round(repo, record)
    if record["stage"] == "undone":
        review = repo / ".project/review"
        review.mkdir(parents=True, exist_ok=True)
        _common.atomic_write(review / f"pr-review-{record['round']}.md", source_text(record, snap, fixes))
        _common.atomic_write(review / "PATCH-FINDINGS.md", findings_text(record, snap, fixes))
        state = _state(repo)
        if (state.phase, state.status) == ("ship", "active"):
            _sibling("pipeline_state").transition_state(
                repo, {"phase": "ship", "status": "active", "branch": state.branch, "archive": None},
                {"status": "blocked"},
                f"{LANE_EVENT}: round {record['round']}; {record['pull_request']}; "
                f"{len(fixes)} fix item(s); ruling: {one_line(ruling['verbatim'])}")
        record["stage"] = "opened"
        _save_round(repo, record)
    return {"schema": SCHEMA, "status": record["stage"], "round": record["round"],
            "source": f".project/review/pr-review-{record['round']}.md",
            "patch_findings": ".project/review/PATCH-FINDINGS.md",
            "fixes": [{"id": row["id"], "group": row["group"], "title": one_line(row["title"])} for row in fixes]}


def require_source(repo: Path, relative: str, text: str) -> None:
    """A pr-review patch source must be exactly what the lane wrote for the active round."""
    match = SOURCE_NAME.fullmatch(PurePosixPath(relative).name)
    record = lane_round(repo)
    if not match or record is None or int(match.group("round")) != int(record["round"]):
        raise LaneError(f"{relative} is not the pr-review source of an active PR fix lane round")
    directory = Path(str(record["directory"]))
    snap = _read_json(directory / "snapshot.json")
    ruling = _read_json(directory / "ruling.json")
    if _digest(directory / "ruling.json") != record.get("ruling_digest"):
        raise LaneError("the recorded ruling changed after it was accepted")
    expected = source_text(record, snap, _validate_ruling(ruling, snap))
    if text != expected:
        raise LaneError(f"{relative} differs from the lane's recorded snapshot and ruling")


# --- lane plan ---------------------------------------------------------------------

def lane_plan_errors(repo: Path, waves: Mapping[int, str], tasks: Sequence[Mapping[str, object]]) -> List[str]:
    """Plan-gate checks for a `Review depth: delta` wave."""
    delta = sorted(number for number, depth in waves.items() if depth == "delta")
    if not delta:
        return []
    errors: List[str] = []
    record = lane_round(repo)
    if record is None or record.get("stage") not in {"opened", "built", "viewed"}:
        return ["Review depth: delta is legal only in an open PR fix lane round"]
    if delta != list(range(delta[0], max(waves) + 1)):
        errors.append("the PR fix lane waves must be the last waves of the plan")
    wave = delta[0]  # later delta waves are its repair waves (they inherit its depth)
    findings = (repo / ".project/review/PATCH-FINDINGS.md")
    ids = re.findall(r"(?m)^### (P\d{3}) — ", findings.read_text(encoding="utf-8")) if findings.is_file() else []
    covered: Dict[str, str] = {}
    for task in tasks:
        if int(task.get("wave") or 0) != wave:
            continue
        cited = set(re.findall(r"\bP\d{3}\b", str(task.get("context") or "")))
        if not cited:
            errors.append(f"{task['id']} in the lane wave must cite the P### items it closes in its Context")
        for finding in cited:
            if finding not in ids:
                errors.append(f"{task['id']} cites unknown finding {finding}")
            elif finding in covered:
                errors.append(f"{finding} is closed by both {covered[finding]} and {task['id']}")
            else:
                covered[finding] = str(task["id"])
    missing = [finding for finding in ids if finding not in covered]
    if missing:
        errors.append("every lane finding needs one task; uncovered: " + ", ".join(missing))
    return errors


# --- delta review ------------------------------------------------------------------

def _delta_section(text: str) -> Optional[str]:
    match = re.search(rf"(?m)^## {re.escape(DELTA_SECTION)}\s*$", text)
    if not match:
        return None
    body = text[match.end():]
    following = re.search(r"(?m)^## ", body)
    return body[: following.start() if following else len(body)]


def _blocks(section: str) -> Dict[str, str]:
    matches = list(re.finditer(r"(?m)^### (.+)$", section))
    return {match.group(1).strip(): section[match.end(): (matches[index + 1].start() if index + 1 < len(matches)
                                                          else len(section))]
            for index, match in enumerate(matches)}


def _bullet(block: str, field: str) -> Optional[str]:
    values = _common.find_review_bullet_field_values(block.splitlines(), field)
    return values[0].strip() if len(values) == 1 else None


def parse_delta(text: str, criteria: Sequence[str], gaps: Sequence[int], item_ids: Sequence[str]) -> dict:
    """Parse and check the `## PR fix delta` section of a delta wave review."""
    section = _delta_section(text)
    if section is None:
        raise LaneError(f"a delta wave review needs a `## {DELTA_SECTION}` section")
    eligibility = re.search(r"(?m)^Eligibility:\s*(\S+)\s*$", section)
    inventory = re.search(r"(?m)^Risk inventory:\s*(\S+)\s*$", section)
    if not eligibility or eligibility.group(1) not in {"local", "not-local"}:
        raise LaneError("the delta review needs `Eligibility: local` or `Eligibility: not-local`")
    if not inventory or inventory.group(1) not in {"complete", "new"}:
        raise LaneError("the delta review needs `Risk inventory: complete` or `Risk inventory: new`")
    blocks = _blocks(section)
    items: Dict[str, str] = {}
    criterion_rows: Dict[str, dict] = {}
    gap_rows: Dict[int, dict] = {}
    for heading, block in blocks.items():
        item = re.fullmatch(r"Item ([RC]\d+): (closed|not-closed)", heading)
        criterion = re.fullmatch(r"(SC[1-9]\d*) — (affected|unaffected)", heading)
        gap = re.fullmatch(r"Gap ([1-9]\d*) — (affected|unaffected)", heading)
        if item:
            items[item.group(1)] = item.group(2)
        elif criterion:
            criterion_rows[criterion.group(1)] = {"impact": criterion.group(2), "block": block}
        elif gap:
            gap_rows[int(gap.group(1))] = {"impact": gap.group(2), "block": block}
        else:
            raise LaneError(f"unexpected delta heading: ### {heading}")
    if sorted(items) != sorted(item_ids):
        raise LaneError("the delta review must report every fix item once: " + ", ".join(sorted(item_ids)))
    if set(criterion_rows) != set(criteria):
        raise LaneError("the delta review must map every success criterion")
    if set(gap_rows) != set(gaps):
        raise LaneError("the delta review must map every numbered gap risk from 2")
    for key, row in list(criterion_rows.items()) + [(f"Gap {k}", v) for k, v in gap_rows.items()]:
        if row["impact"] == "unaffected":
            reason = _bullet(row["block"], "Reason")
            if not reason or reason.casefold() == "none":
                raise LaneError(f"{key} is unaffected without a Reason")
            row["reason"] = one_line(reason)
    return {"eligibility": eligibility.group(1), "inventory": inventory.group(1), "items": items,
            "criteria": criterion_rows, "gaps": gap_rows}


def delta_report(repo: Path, wave: int) -> Tuple[Path, str]:
    """The latest cycle of the delta wave review."""
    review = repo / ".project/review"
    cycles = sorted(((int(match.group(1)), path) for path in review.glob(f"wave-{wave}.cycle*.md")
                     if (match := re.fullmatch(rf"wave-{wave}\.cycle([1-9]\d*)\.md", path.name))))
    if not cycles:
        raise LaneError(f"the lane wave {wave} has no review yet")
    path = cycles[-1][1]
    return path, path.read_text(encoding="utf-8")


def delta_errors(repo: Path, text: str) -> List[str]:
    """Wave-gate checks for a delta review against the active round."""
    record = lane_round(repo)
    if record is None:
        return ["a delta wave review needs an open PR fix lane round"]
    directory = Path(str(record["directory"]))
    capsule = directory / "capsule/review"
    contracts = _sibling("check_handoffs")
    criteria = list(contracts._success_criteria((repo / ".project/intent/INTENT.md").read_text(encoding="utf-8")))
    gaps = [int(GAP_NAME.fullmatch(name).group("number")) for name in record.get("gaps", [])
            if GAP_NAME.fullmatch(name) and int(GAP_NAME.fullmatch(name).group("number")) > 1]
    try:
        parse_delta(text, criteria, gaps, list(record.get("fixes") or []))
    except LaneError as error:
        return [str(error)]
    if not (capsule / "FINAL.md").is_file():
        return ["the lane capsule lost its FINAL.md"]
    return []


# --- views -------------------------------------------------------------------------

def _replace_header(text: str, head: str, members: Mapping[str, str]) -> str:
    header, separator, rest = text.partition("\n## ")
    lines = [line for line in header.splitlines() if not line.startswith("Member reviewed HEAD:")]
    out: List[str] = []
    for line in lines:
        if line.startswith("Reviewed HEAD:"):
            out.append(f"Reviewed HEAD: {head}")
            out += [f"Member reviewed HEAD: {name} {sha}" for name, sha in sorted(members.items())]
        else:
            out.append(line)
    return "\n".join(out) + (separator + rest if separator else "\n")


def _inherit_reference(block: str, note: str) -> str:
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^- \*\*Reference\*\*:", line):
            lines[index] = line.rstrip() + f" {note}"
            return "\n".join(lines) + ("\n" if block.endswith("\n") else "")
    raise LaneError("an inherited block has no Reference field")


def _criterion_blocks(final_text: str) -> Tuple[List[Tuple["re.Match[str]", str]], int, int]:
    section = re.search(r"(?m)^## Success criteria\s*$", final_text)
    if not section:
        raise LaneError("FINAL.md has no Success criteria section")
    start = section.end()
    following = re.search(r"(?m)^## ", final_text[start:])
    end = start + following.start() if following else len(final_text)
    matches = list(re.finditer(r"(?m)^### (SC[1-9]\d*) — .+$", final_text[start:end]))
    return [(match, final_text[start + match.end(): start + (matches[index + 1].start() if index + 1 < len(matches)
                                                                else end - start)])
            for index, match in enumerate(matches)], start, end


def build_final_view(capsule_final: str, delta: dict, head: str, members: Mapping[str, str],
                     record: Mapping[str, object], ruling: Mapping[str, object], delta_relative: str,
                     findings: Mapping[str, str], tasks: Mapping[str, str]) -> str:
    blocks, start, end = _criterion_blocks(capsule_final)
    short = str(record["head"])[:12]
    pieces = [capsule_final[:start]]
    cursor = start
    for match, block in blocks:
        criterion = match.group(1)
        row = delta["criteria"][criterion]
        block_start = start + match.start()
        pieces.append(capsule_final[cursor:block_start])
        pieces.append(match.group(0))
        if row["impact"] == "unaffected":
            pieces.append(_inherit_reference(block, f"(inherited from {short} by PR fix round {record['round']}; "
                                                    f"{delta_relative}: unaffected — {row['reason']})"))
        else:
            fields = ["Verdict", "Surface", "Check", "Observed", "Reference", "Finding", "Fix direction"]
            lines = [""]
            for field in fields:
                value = _bullet(row["block"], field)
                if value is None and field != "Surface":
                    raise LaneError(f"affected {criterion} needs a {field} field in the delta review")
                if value is not None:
                    lines.append(f"- **{field}**: {value}")
            if (_bullet(row["block"], "Verdict") or "") != "met":
                raise LaneError(f"affected {criterion} is not met; the lane cannot ship it")
            pieces.append("\n".join(lines) + "\n\n")
        cursor = start + match.end() + len(block)
    pieces.append(capsule_final[cursor:])
    text = _replace_header("".join(pieces), head, members)
    entry = [f"### Round {record['round']}", "",
             f"- **Pull request**: {record['pull_request']} ({record['member']}) at "
             f"{record['members'].get(record['member'], 'unknown')} → {members.get(str(record['member']), 'unknown')}",
             f"- **Review**: {', '.join(str(value) for value in record['review_ids'])}",
             f"- **Ruling**: {one_line(ruling['verbatim'])}",
             f"- **Delta review**: `{delta_relative}`",
             f"- **Provenance**: `.project/review/pr-fix-{record['round']}/provenance.json`"]
    entry += [f"- **{finding}**: Item {item} → {tasks.get(finding, 'unassigned')}" for finding, item in findings.items()]
    rounds_heading = re.search(r"(?m)^## PR fix rounds\s*$", text)
    addition = "\n".join(entry) + "\n"
    if rounds_heading:
        text = text.rstrip("\n") + "\n\n" + addition
    else:
        text = text.rstrip("\n") + "\n\n## PR fix rounds\n\n" + addition
    return text


def build_gap_view(capsule_gap: str, row: dict, head: str, members: Mapping[str, str],
                   record: Mapping[str, object], delta_relative: str) -> str:
    text = _replace_header(capsule_gap, head, members)
    short = str(record["head"])[:12]
    if row["impact"] == "unaffected":
        section = re.search(r"(?m)^## Checked evidence\s*$", text)
        if not section:
            raise LaneError("gap review has no Checked evidence section")
        following = re.search(r"(?m)^## ", text[section.end():])
        end = section.end() + (following.start() if following else len(text) - section.end())
        body = _inherit_reference(text[section.end():end],
                                  f"(inherited from {short} by PR fix round {record['round']}; "
                                  f"{delta_relative}: unaffected — {row['reason']})")
        return text[:section.end()] + body + text[end:]
    values = {field: _bullet(row["block"], field) for field in
              ("Gap verdict", "Check", "Observed", "Reference", "Found", "Fix direction")}
    missing = [field for field, value in values.items() if value is None]
    if missing:
        raise LaneError("an affected gap needs " + ", ".join(missing) + " in the delta review")
    if values["Gap verdict"] != "pass":
        raise LaneError("an affected gap does not pass; the lane cannot ship it")
    header = text.split("\n## ", 1)[0]
    header = re.sub(r"(?m)^Gap verdict:.*$", "Gap verdict: pass", header)
    return (header.rstrip("\n") + "\n\n## Checked evidence\n\n"
            f"- **Check**: {values['Check']}\n- **Observed**: {values['Observed']}\n"
            f"- **Reference**: {values['Reference']}\n\n## Finding\n\n"
            f"- **Found**: {values['Found']}\n- **Fix direction**: {values['Fix direction']}\n")


def _lane_waves(repo: Path) -> List[int]:
    """The lane wave first, then its repair waves; all carry `Review depth: delta`."""
    plan = (repo / ".project/plan/PLAN.md").read_text(encoding="utf-8")
    waves, _ = _sibling("check_handoffs")._plan_waves(plan)
    delta = sorted(number for number, depth in waves.items() if depth == "delta")
    if not delta:
        raise LaneError("the plan has no PR fix lane wave")
    return delta


def _lane_tasks(repo: Path, lane_waves: Sequence[int]) -> Dict[str, dict]:
    tasks: Dict[str, dict] = {}
    for path in sorted((repo / ".project/tasks").glob("T*.md")):
        text = path.read_text(encoding="utf-8")
        wave_match = re.search(r"(?m)^wave:\s*(\d+)", text)
        status = re.search(r"(?m)^status:\s*(\S+)", text)
        task_id = re.search(r"(?m)^id:\s*(T\d{3})", text)
        if not wave_match or int(wave_match.group(1)) not in lane_waves or not task_id:
            continue
        context = re.search(r"(?ms)^## Context\s*$(.*?)^## ", text)
        tasks[task_id.group(1)] = {"path": path.relative_to(repo).as_posix(),
                                   "status": status.group(1) if status else None,
                                   "context": context.group(1) if context else ""}
    return tasks


def views(repo: Path, expected_head: str) -> dict:
    """Generate FINAL.md and gap views for the active lane round; refuse when anything is unproven."""
    repo = repo.resolve()
    record = lane_round(repo)
    if record is None:
        raise LaneError("no PR fix lane round is open")
    directory = Path(str(record.pop("directory")))
    head = _git(repo, "rev-parse", "HEAD")
    if head != expected_head:
        raise LaneError("primary HEAD changed")
    state = _state(repo)
    if (state.phase, state.status) != ("ship", "active") or state.archive:
        raise LaneError("lane views are generated at ship/active before the archive is prepared")
    contracts = _sibling("check_handoffs")
    members = contracts._member_tips(repo)
    review = repo / ".project/review"
    gap_one = (review / "final-gap-1.md").read_text(encoding="utf-8")
    if contracts._reviewed_head(gap_one, "final-gap-1.md") != head \
            or contracts.member_reviewed_heads(gap_one, "final-gap-1.md") != members \
            or contracts._line_value(gap_one, "Gap verdict:") != "pass":
        raise LaneError("the project Verify gap must pass at the current head and member tips")
    lane_waves = _lane_waves(repo)
    wave = lane_waves[0]
    tasks = _lane_tasks(repo, lane_waves)
    if not tasks or any(task["status"] != "done" for task in tasks.values()):
        raise LaneError("every lane task must be done before the views")
    proof = _sibling("build_state").verify_landed_tasks(str(repo), ".project", head)
    landings = {row["task"]["id"]: row.get("landed_commit") for row in proof.get("tasks") or []
                if isinstance(row.get("task"), dict) and row["task"].get("id") in tasks}
    if set(landings) != set(tasks) or not all(landings.values()):
        raise LaneError("a lane task is not proven landed")
    delta_path, delta_text = delta_report(repo, wave)
    if contracts._line_value(delta_text, "Wave verdict:") != "pass" or \
            contracts._line_value(delta_text, "Depth:") != "delta":
        raise LaneError(f"{delta_path.name} is not a passing delta review")
    criteria = list(contracts._success_criteria((repo / ".project/intent/INTENT.md").read_text(encoding="utf-8")))
    capsule = directory / "capsule/review"
    capsule_gaps = sorted(int(GAP_NAME.fullmatch(path.name).group("number"))
                          for path in capsule.iterdir() if GAP_NAME.fullmatch(path.name))
    delta = parse_delta(delta_text, criteria, [number for number in capsule_gaps if number > 1],
                        list(record.get("fixes") or []))
    if delta["eligibility"] != "local":
        raise LaneError("the delta review found a non-local change; use the ordinary final review")
    if delta["inventory"] != "complete":
        raise LaneError("the delta review found a new integration risk; use the ordinary final review")
    if any(value != "closed" for value in delta["items"].values()):
        raise LaneError("a lane item is not closed")
    findings_text_value = (review / "PATCH-FINDINGS.md").read_text(encoding="utf-8")
    findings = dict(re.findall(r"(?m)^### (P\d{3}) — .*\n\n- \*\*Source\*\*: .*\n- \*\*Locator\*\*: Item (\S+)",
                               findings_text_value))
    task_of: Dict[str, str] = {}
    for task_id, task in tasks.items():
        for finding in re.findall(r"\bP\d{3}\b", task["context"]):
            task_of[finding] = task_id
    ruling = _read_json(directory / "ruling.json")
    relative_delta = delta_path.relative_to(repo).as_posix()
    final = build_final_view((capsule / "FINAL.md").read_text(encoding="utf-8"), delta, head, members,
                             record, ruling, relative_delta, findings, task_of)
    outputs: Dict[str, str] = {}
    gap_texts = {number: build_gap_view((capsule / f"final-gap-{number}.md").read_text(encoding="utf-8"),
                                        delta["gaps"][number], head, members, record, relative_delta)
                 for number in capsule_gaps if number > 1}
    # Write the views, validate them as an ordinary final, and restore the prior files on refusal.
    before = {path.name: path.read_bytes() for path in review.iterdir()
              if path.is_file() and (path.name == "FINAL.md" or GAP_NAME.fullmatch(path.name))
              and path.name != "final-gap-1.md"}
    try:
        for name in before:
            if name != "FINAL.md" and int(GAP_NAME.fullmatch(name).group("number")) not in gap_texts:
                (review / name).unlink()
        for number, text in gap_texts.items():
            _common.atomic_write(review / f"final-gap-{number}.md", text)
        _common.atomic_write(review / "FINAL.md", final)
        contracts.validate_final(repo)
    except Exception:
        for path in review.iterdir():
            if path.name not in before and (path.name == "FINAL.md" or (
                    GAP_NAME.fullmatch(path.name) and path.name != "final-gap-1.md")):
                path.unlink()
        for name, data in before.items():
            (review / name).write_bytes(data)
        raise
    preserved = review / f"pr-fix-{record['round']}"
    for path in sorted((directory / "capsule/review").rglob("*")):
        if path.is_file():
            relative = path.relative_to(directory / "capsule/review").as_posix()
            if ROUND_DIR.fullmatch(relative.split("/")[0]):
                target = review / relative
            else:
                target = preserved / "baseline" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    for name in ("snapshot.json", "ruling.json"):
        (preserved / name).write_bytes((directory / name).read_bytes())
    for path in sorted(review.iterdir()):
        if path.name == "FINAL.md" or GAP_NAME.fullmatch(path.name):
            outputs[path.name] = _digest(path)
    provenance = {
        "schema": SCHEMA, "round": record["round"], "pull_request": record["pull_request"],
        "member": record["member"], "baseline_head": record["head"], "baseline_members": record["members"],
        "head": head, "members": members, "capsule_digest": record["capsule_digest"],
        "snapshot_digest": record["snapshot_digest"], "ruling_digest": record["ruling_digest"],
        "tasks": {task_id: landings[task_id] for task_id in sorted(tasks)},
        "delta_review": {"path": relative_delta, "sha256": _digest(delta_path)},
        "pr_review": {"path": f".project/review/pr-review-{record['round']}.md",
                      "sha256": _digest(review / f"pr-review-{record['round']}.md")},
        "outputs": outputs,
    }
    _write_json(preserved / "provenance.json", provenance)
    record["stage"] = "viewed"
    _save_round(repo, record)
    return {"schema": SCHEMA, "status": "complete", "round": record["round"], "head": head,
            "outputs": sorted(outputs), "provenance": f".project/review/pr-fix-{record['round']}/provenance.json"}


def validate_views(repo: Path, review_dir: Path) -> None:
    """Re-prove the newest round's provenance from files alone (active or archived review/)."""
    rounds = sorted((int(match.group("round")), path) for path in review_dir.iterdir()
                    if path.is_dir() and (match := ROUND_DIR.fullmatch(path.name))) if review_dir.is_dir() else []
    if not rounds:
        return
    _, newest = rounds[-1]
    provenance = _read_json(newest / "provenance.json")
    for name, digest in provenance["outputs"].items():
        path = review_dir / name
        if not path.is_file() or _digest(path) != digest:
            raise LaneError(f"{name} differs from its PR fix lane provenance")
    for key in ("delta_review", "pr_review"):
        path = review_dir / PurePosixPath(provenance[key]["path"]).name
        if not path.is_file() or _digest(path) != provenance[key]["sha256"]:
            raise LaneError(f"{provenance[key]['path']} differs from its PR fix lane provenance")
    if _digest(newest / "ruling.json") != provenance["ruling_digest"]:
        raise LaneError("the archived ruling differs from its provenance")
    if _json_digest(_read_json(newest / "snapshot.json")) != provenance["snapshot_digest"]:
        raise LaneError("the archived snapshot differs from its provenance")
    final_text = (review_dir / "FINAL.md").read_text(encoding="utf-8")
    head, members, verdict = _final_header_heads(final_text)
    if head != provenance["head"] or members != provenance["members"] or verdict != "pass":
        raise LaneError("FINAL.md heads differ from the PR fix lane provenance")


# --- after publication -------------------------------------------------------------

def _reply_key(record: Mapping[str, object], item: str, head: str) -> str:
    return f"{record['round']}/{item}/{head}"


THREADS_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String){
  viewer{login}
  repository(owner:$owner,name:$name){pullRequest(number:$number){
    id headRefOid
    reviews(last:100){nodes{id state body author{login}}}
    reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}
      nodes{id isResolved comments(first:100){nodes{databaseId body}}}}
  }}
}"""


def _graphql(api: Callable[..., object], query: str, **variables: object) -> dict:
    arguments = ["graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        if value is None:
            continue
        arguments += ["-F" if isinstance(value, int) else "-f", f"{name}={value}"]
    result = api(*arguments)
    if not isinstance(result, dict) or result.get("errors") or not isinstance(result.get("data"), dict):
        raise LaneError(f"GitHub GraphQL refused the request: {result.get('errors') if isinstance(result, dict) else result}")
    return result["data"]


def _pull_threads(api: Callable[..., object], repository: str, number: int) -> dict:
    owner, name = repository.split("/", 1)
    threads, cursor, pull, viewer = [], None, None, None
    while True:
        data = _graphql(api, THREADS_QUERY, owner=owner, name=name, number=number, after=cursor)
        viewer = (data.get("viewer") or {}).get("login")
        pull = data["repository"]["pullRequest"]
        page = pull["reviewThreads"]
        threads += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return {"viewer": viewer, "id": pull["id"], "head": pull["headRefOid"], "reviews": pull["reviews"]["nodes"],
            "threads": threads}


def _thread_of(threads: Sequence[dict], comment_id: int) -> Optional[dict]:
    for thread in threads:
        nodes = (thread.get("comments") or {}).get("nodes") or []
        if nodes and nodes[0].get("databaseId") == comment_id:
            return thread
    return None


def post_threads(plan: Mapping[str, object], api: Callable[..., object] = _default_api) -> dict:
    """Answer every review item of a round in one COMMENT review, then resolve the fixed threads.

    `plan` holds pull_request, head (the published head the replies cite), round, language, and
    items with id (R<review>|C<comment>), kind, class (fix|redesign|follow-up), note and optional
    link. Fixed items get a reply and a resolved thread; follow-up items get a reply and stay open;
    the review body lists every item, so body-only items are answered too. Markers make a rerun
    skip what was already posted, and a pending review a crash left behind is replaced.
    """
    repository, number = pull_parts(str(plan["pull_request"]))
    head = str(plan["head"])
    text = REPLY_TEXT.get(str(plan.get("language") or "en"), REPLY_TEXT["en"])
    index = _pull_threads(api, repository, number)
    if index["head"] != head:
        raise LaneError(f"the PR shows {str(index['head'])[:12]}, not the round's published head {head[:12]}; "
                        "publish it through close-members first")
    summary_marker = REPLY_MARKER.format(key=f"{plan['round']}/review/{head}")
    submitted = [review for review in index["reviews"] if review.get("state") != "PENDING"]
    done = any(summary_marker in str(review.get("body") or "") for review in submitted)
    replied, skipped, resolved, missing = [], [], [], []
    items = list(plan["items"])
    if not done:
        for review in index["reviews"]:
            if review.get("state") == "PENDING" and (review.get("author") or {}).get("login") == index["viewer"]:
                _graphql(api, "mutation($id:ID!){deletePullRequestReview(input:{pullRequestReviewId:$id})"
                              "{pullRequestReview{id}}}", id=review["id"])
        pending = _graphql(api, "mutation($pr:ID!){addPullRequestReview(input:{pullRequestId:$pr})"
                                "{pullRequestReview{id}}}", pr=index["id"])["addPullRequestReview"]
        review_id = pending["pullRequestReview"]["id"]
        for item in items:
            if item.get("kind") != "inline":
                continue
            thread = _thread_of(index["threads"], int(str(item["id"])[1:]))
            if thread is None:
                missing.append(item["id"])
                continue
            marker = REPLY_MARKER.format(key=f"{plan['round']}/{item['id']}/{head}")
            bodies = "\n".join(str(node.get("body") or "") for node in thread["comments"]["nodes"])
            if marker in bodies:
                skipped.append(item["id"])
                continue
            kind = "follow-up" if item["class"] == "follow-up" else "fix"
            body = text[kind].format(head=head[:12], note=one_line(item["note"])) + f"\n\n{marker}"
            _graphql(api, "mutation($review:ID!,$thread:ID!,$body:String!){addPullRequestReviewThreadReply("
                          "input:{pullRequestReviewId:$review,pullRequestReviewThreadId:$thread,body:$body})"
                          "{comment{id}}}", review=review_id, thread=thread["id"], body=body)
            replied.append(item["id"])
        lines = [text["summary"].format(round=plan["round"], head=head[:12]), ""]
        for item in items:
            status = text["later"] if item["class"] == "follow-up" else text["fixed"]
            label = f"[{item['id']}]({item['link']})" if item.get("link") and item["link"] != "none" else item["id"]
            lines.append(f"- {label}: {status} — {one_line(item['note'])}")
        _graphql(api, "mutation($review:ID!,$body:String!){submitPullRequestReview(input:"
                      "{pullRequestReviewId:$review,event:COMMENT,body:$body}){pullRequestReview{id}}}",
                 review=review_id, body="\n".join(lines) + f"\n\n{summary_marker}")
    for item in items:
        if item.get("kind") != "inline" or item["class"] == "follow-up":
            continue
        thread = _thread_of(index["threads"], int(str(item["id"])[1:]))
        if thread is None or thread.get("isResolved"):
            continue
        _graphql(api, "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}",
                 id=thread["id"])
        resolved.append(item["id"])
    return {"schema": SCHEMA, "status": "complete", "summary": "present" if done else "posted",
            "replied": replied, "skipped": skipped, "resolved": resolved, "missing_threads": missing}


def rule(repo: Path, ruling_path: Path) -> dict:
    """Record the owner's ruling for a record-only round; the ordinary route does the rest."""
    repo = repo.resolve()
    record = active_round(repo)
    if record is None or record.get("mode") != "record":
        raise LaneError("rule records a record-only round; a lane round records its ruling with start")
    directory = Path(record.pop("directory"))
    snap = _read_json(directory / "snapshot.json")
    ruling = _read_json(ruling_path)
    fixes = _validate_ruling(ruling, snap, lane=False)
    if record["stage"] == "captured":
        _write_json(directory / "ruling.json", ruling)
        record.update(stage="ruled", ruling_digest=_digest(directory / "ruling.json"),
                      fixes=[row["id"] for row in fixes])
        _save_round(repo, record)
    elif _digest(directory / "ruling.json") != record.get("ruling_digest") or \
            _read_json(directory / "ruling.json") != ruling:
        raise LaneError("this round already recorded a different ruling")
    return {"schema": SCHEMA, "status": record["stage"], "round": record["round"],
            "fixes": [row["id"] for row in fixes]}


def round_plan(repo: Path) -> dict:
    """The thread plan of the open round, once its head is published to the PR."""
    record = active_round(repo)
    if record is None or record.get("stage") in (None, "captured"):
        raise LaneError("no ruled PR fix round is open")
    directory = Path(str(record.pop("directory")))
    snap = _read_json(directory / "snapshot.json")
    ruling = _read_json(directory / "ruling.json")
    state = _state(repo)
    archive = repo / str(state.archive or "").rstrip("/")
    if not state.archive or not (archive / "review/FINAL.md").is_file():
        raise LaneError("thread replies run after the archive is prepared again and close-members published it")
    _, members, _ = _final_header_heads((archive / "review/FINAL.md").read_text(encoding="utf-8"))
    head = members.get(str(record["member"]))
    if not head or head == record["members"].get(str(record["member"])):
        raise LaneError("the prepared archive does not name a new member head for this round")
    if record.get("mode", "lane") == "lane":
        validate_views(repo, archive / "review")
    publication = repo / ".project/publications" / str(record["archive_name"]) / f"{record['member']}.json"
    try:
        language = _read_json(publication).get("language", "en") if publication.is_file() else "en"
    except (LaneError, ValueError):
        language = "en"
    known = {item["id"]: item for item in snap["items"]}
    items = [{"id": row["id"], "kind": known[row["id"]]["kind"], "class": row["class"],
              "link": known[row["id"]].get("link"),
              "note": row.get("fix_direction") if row["class"] != "follow-up" else row.get("note")}
             for row in ruling["items"]]
    return {"pull_request": record["pull_request"], "head": head, "round": record["round"],
            "language": language, "items": items, "_record": record}


def threads(repo: Path, api: Callable[..., object] = _default_api) -> dict:
    """Answer and resolve the round's PR conversations after close-members published the new head."""
    repo = repo.resolve()
    plan = round_plan(repo)
    record = plan.pop("_record")
    result = post_threads(plan, api)
    record["stage"] = "replied"
    _save_round(repo, record)
    return result


def ship_check(repo: Path, api: Callable[..., object] = _default_api, override: Optional[Path] = None) -> dict:
    """Refuse shipment while a change request is still active on the lane's PR."""
    repo = repo.resolve()
    record = active_round(repo)
    if record is None:
        return {"schema": SCHEMA, "status": "not-applicable"}
    read = read_forge(str(record["pull_request"]), api)
    requests = active_change_requests(read["reviews"])
    if not requests:
        _set_active(repo, record, status="closed")
        return {"schema": SCHEMA, "status": "clear"}
    head = (read["pull"].get("head") or {}).get("sha")
    outstanding = sorted(int(item["id"]) for item in requests)
    if override is not None:
        ruling = _read_json(override)
        if ruling.get("head") == head and sorted(ruling.get("reviews") or []) == outstanding and \
                isinstance(ruling.get("verbatim"), str) and ruling["verbatim"].strip():
            _set_active(repo, record, status="closed")
            return {"schema": SCHEMA, "status": "overridden", "head": head, "reviews": outstanding}
        raise LaneError("the shipment override does not name the published head and the exact outstanding reviews")
    raise LaneError("a change request is still active on " + str(record["pull_request"]) +
                    "; wait for the reviewer or record an explicit owner override")


# --- CLI ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "start", "rule", "views", "threads", "ship-check", "status"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, required=name != "threads")
        if name == "snapshot":
            command.add_argument("--allow-more-rounds", action="store_true")
            command.add_argument("--record-only", action="store_true",
                                 help="record the items for the PR conversations without entering the lane")
        if name == "rule":
            command.add_argument("--ruling", type=Path, required=True)
        if name == "threads":
            command.add_argument("--plan", type=Path,
                                 help="explicit thread plan for a one-off answer outside a recorded round")
        if name == "start":
            command.add_argument("--ruling", type=Path, required=True)
        if name == "views":
            command.add_argument("--expected-head", required=True)
        if name == "ship-check":
            command.add_argument("--override", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(args.repo, allow_more_rounds=args.allow_more_rounds, record_only=args.record_only)
        elif args.command == "rule":
            result = rule(args.repo, args.ruling)
        elif args.command == "threads":
            if args.plan is not None:
                result = post_threads(_read_json(args.plan))
            elif args.repo is not None:
                result = threads(args.repo)
            else:
                raise LaneError("threads needs --repo (a recorded round) or --plan (a one-off)")
        elif args.command == "start":
            result = start(args.repo, args.ruling)
        elif args.command == "views":
            result = views(args.repo, args.expected_head)
        elif args.command == "ship-check":
            result = ship_check(args.repo, override=args.override)
        else:
            current = active_round(args.repo.resolve())
            result = {"schema": SCHEMA, "status": "none"} if current is None else \
                {"schema": SCHEMA, "status": current.get("stage"), "round": current["round"],
                 "pull_request": current["pull_request"]}
    except (LaneError, RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    from _common import cli_receipt

    raise SystemExit(cli_receipt(main))
