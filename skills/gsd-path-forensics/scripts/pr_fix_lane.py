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
    regenerate rewrite an opened round's pr-review source and PATCH-FINDINGS.md from its
              recorded ruling (before plan approval)
    fall-back hand the round to the ordinary final review when the views cannot stand
              in for it; prepare-final does this itself, and a blocked final then takes
              the ordinary patch route after the landed delta waves
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
REPLY_MARKER_PREFIX = "<!-- gsd-path-pr-fix:"
# Azure thread states that still wait for the author (the others are resolved or closed).
AZURE_OPEN_THREADS = {"active", "pending"}
FINISHED_STAGES = {"viewed", "replied"}
# The lane's views could not stand in for the final review: the ordinary final review and,
# when it blocks, the ordinary patch route continue the round. The round stays active so its
# delta wave reviews still validate and `threads` still answers the PR at republication.
FELL_BACK = "fell-back"
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
    """Raised when the lane cannot enter, proceed, or prove its outputs.

    `kind` names why the views fell back (ADR 0008): new-risk, non-local, stale-product,
    content, or provenance.
    """

    def __init__(self, message: str, kind: str = "content") -> None:
        super().__init__(message)
        self.kind = kind


FALLBACK_KINDS = ("new-risk", "non-local", "stale-product", "content", "provenance")


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


def fall_back(repo: Path, reason: str, kind: Optional[str] = None) -> dict:
    """Hand the open lane round to the ordinary final review (idempotent).

    The stage becomes `fell-back`; `outcome` records `fallback` and why (ADR 0008).
    """
    repo = repo.resolve()
    with _LaneLock(repo):
        record = lane_round(repo)
        if record is None:
            raise LaneError("no PR fix lane round is open")
        if record.get("stage") not in {"opened", "built", "viewed", FELL_BACK}:
            raise LaneError(f"PR fix lane round {record['round']} is {record.get('stage')}; it cannot fall back")
        if record.get("stage") != FELL_BACK:
            record.update(stage=FELL_BACK, fallback=one_line(reason) or "ordinary final review",
                          outcome="fallback", fallback_kind=kind if kind in FALLBACK_KINDS else "manual")
            _save_round(repo, record)
    return {"schema": SCHEMA, "status": FELL_BACK, "round": record["round"], "reason": record.get("fallback")}


def _set_active(repo: Path, round_record: Mapping[str, object], status: str = "open") -> None:
    _write_json(lane_root(repo) / "active.json", {
        "archive_name": round_record["archive_name"], "branch": round_record["branch"],
        "round": round_record["round"], "status": status})


def _save_round(repo: Path, record: dict) -> None:
    directory = _round_dir(repo, str(record["archive_name"]), int(record["round"]))
    _write_json(directory / "round.json", {key: value for key, value in record.items() if key != "directory"})


# --- forge reads (GitHub and Azure DevOps) ---------------------------------------------

def _default_api(*arguments: str) -> object:
    return _sibling("integration").github_api_json(*arguments)


def _default_azure(*arguments: str) -> object:
    return _sibling("integration").azure_cli_json(*arguments)


def forge_provider(url: str) -> str:
    """`github` or `azure` for a member pull request URL; anything else takes the ordinary route."""
    if PULL_URL.fullmatch(url or ""):
        return "github"
    if _sibling("azure_repos").pull_request_parts(url or "") is not None:
        return "azure"
    raise LaneError(f"the PR fix lane supports GitHub and Azure DevOps pull requests only, not {url!r}")


def _forge_api(url: str, api: Optional[Callable[..., object]]) -> Tuple[str, Callable[..., object]]:
    provider = forge_provider(url)
    if api is not None:
        return provider, api
    return provider, (_default_azure if provider == "azure" else _default_api)


def _review_id(value: object):
    """GitHub review ids are integers; an Azure change request is a reviewer identity id."""
    return value if isinstance(value, str) else int(value)


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


def read_forge(url: str, api: Optional[Callable[..., object]] = None) -> dict:
    provider, api = _forge_api(url, api)
    if provider == "azure":
        return _read_azure(url, api)
    repository, number = pull_parts(url)
    pull = api(f"repos/{repository}/pulls/{number}")
    reviews = _pages(api(f"repos/{repository}/pulls/{number}/reviews", "--paginate", "--slurp"))
    comments = _pages(api(f"repos/{repository}/pulls/{number}/comments", "--paginate", "--slurp"))
    if not isinstance(pull, dict):
        raise LaneError("GitHub returned an unexpected pull request")
    return {"provider": "github", "repository": repository, "number": number, "pull": pull,
            "reviews": reviews, "comments": comments}


def _read_azure(url: str, call: Callable[..., object]) -> dict:
    """An Azure PR in the lane's shape: each individual reviewer's current vote is its review.

    Azure keeps one vote per reviewer instead of a review history: -5 (waiting for the
    author) and -10 (rejected) are change requests. Group reviewers only mirror a member's
    vote (`votedFor`) and are skipped. The items live in the PR threads.
    """
    azure = _sibling("azure_repos")
    repository, number = azure.pull_request_parts(url)
    try:
        pull = azure.show(repository, number, call)
        threads = azure.pull_request_threads(repository, number, call)
    except azure.AzureError as error:
        raise LaneError(str(error)) from error
    raw = pull["azure"]
    reviews = []
    for reviewer in raw.get("reviewers") or []:
        if not isinstance(reviewer, dict) or reviewer.get("isContainer"):
            continue
        vote = reviewer.get("vote")
        if not isinstance(reviewer.get("id"), str) or not isinstance(vote, int) or isinstance(vote, bool):
            continue
        state = "CHANGES_REQUESTED" if vote < 0 else "APPROVED" if vote > 0 else "NONE"
        reviews.append({"id": reviewer["id"], "state": state, "vote": vote,
                        "user": {"login": reviewer.get("uniqueName") or reviewer.get("displayName")},
                        "submitted_at": None, "commit_id": None, "body": ""})
    return {"provider": "azure", "url": url, "repository": repository.key, "number": number, "pull": pull,
            "reviews": reviews, "comments": [], "threads": threads,
            "author": (raw.get("createdBy") or {}).get("id")}


def change_requests(read: Mapping[str, object]) -> List[dict]:
    if read.get("provider") == "azure":
        return [review for review in read["reviews"] if review["state"] == "CHANGES_REQUESTED"]
    return active_change_requests(read["reviews"])


def azure_thread_items(read: Mapping[str, object], authors: Optional[set]) -> List[dict]:
    """One item per open Azure thread that waits for an answer.

    A thread waits when a counted author (a requesting reviewer, or anyone but the PR author
    when `authors` is None) wrote in it after the last lane reply. So a follow-up the lane
    answered stays out of the next round until the reviewer writes again, and a reviewer who
    reopens a resolved thread with a reply brings it back.
    """
    items: List[dict] = []
    for thread in sorted(read["threads"], key=lambda item: int(item["id"])):
        if thread.get("isDeleted") or thread.get("status") not in AZURE_OPEN_THREADS:
            continue
        comments = sorted((comment for comment in thread.get("comments") or []
                           if isinstance(comment, dict) and not comment.get("isDeleted")
                           and comment.get("commentType") == "text"),
                          key=lambda comment: int(comment.get("id") or 0))
        answered = [index for index, comment in enumerate(comments)
                    if REPLY_MARKER_PREFIX in str(comment.get("content") or "")]
        if answered:
            comments = comments[answered[-1] + 1:]
        waiting = []
        for comment in comments:
            author = (comment.get("author") or {}).get("id")
            if author == read.get("author") or (authors is not None and author not in authors):
                continue
            if one_line(comment.get("content") or ""):
                waiting.append(comment)
        if not waiting:
            continue
        context = thread.get("threadContext") or {}
        path = str(context.get("filePath") or "").lstrip("/")
        start = context.get("rightFileStart") or context.get("leftFileStart") or {}
        location = (f"{path}:{start['line']}" if start.get("line") else path) if path else "none"
        items.append({"id": f"T{thread['id']}", "kind": "inline" if path else "thread",
                      "review": (waiting[0].get("author") or {}).get("id"), "location": location,
                      # The first waiting comment makes the link unique per round: parking.py keys
                      # imported follow-ups by link, and a thread can be ruled again later.
                      "link": f"{read['url']}?discussionId={thread['id']}&commentId={waiting[0].get('id')}",
                      "text": "\n\n".join(str(comment.get("content") or "") for comment in waiting)})
    return items


def request_items(read: Mapping[str, object], requests: Sequence[dict]) -> List[dict]:
    if read.get("provider") == "azure":
        return azure_thread_items(read, {review["id"] for review in requests})
    return review_items(requests, read["comments"])


def approval_items(read: Mapping[str, object], head: str) -> Tuple[List[dict], List[dict]]:
    """Without a change request: the reviews and the unanswered remarks they left (ADR 0008)."""
    if read.get("provider") == "azure":
        items = azure_thread_items(read, None)
        known = {review["id"]: review for review in read["reviews"]}
        authors = sorted({str(item["review"]) for item in items})
        return [dict(known.get(author) or {"id": author, "state": "COMMENTED", "user": {"login": author},
                                           "submitted_at": None, "commit_id": None}, body="")
                for author in authors], items
    requests, comments = approval_comments(read["reviews"], read["comments"], head)
    return requests, review_items(requests, comments)


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
    """One item per non-empty change-request body and per inline comment that needs an answer.

    That is every top-level inline comment of an active change request, plus every reply the
    request adds to a conversation an earlier round opened (a reviewer reopening a point
    inside its old thread instead of opening a new one).
    """
    items: List[dict] = []
    ids = {int(review["id"]) for review in requests}
    top = {int(comment["id"]) for comment in comments
           if not comment.get("in_reply_to_id") and int(comment.get("pull_request_review_id") or 0) in ids}
    for review in sorted(requests, key=lambda item: int(item["id"])):
        if one_line(review.get("body") or ""):
            items.append({"id": f"R{review['id']}", "kind": "review", "review": int(review["id"]),
                          "location": "none", "link": review.get("html_url") or "none",
                          "text": review.get("body") or ""})
    for comment in sorted(comments, key=lambda item: int(item["id"])):
        if int(comment.get("pull_request_review_id") or 0) not in ids:
            continue
        if comment.get("in_reply_to_id") and int(comment["in_reply_to_id"]) in top:
            continue  # a reply inside this request's own new conversation is answered with it
        if not one_line(comment.get("body") or ""):
            continue
        line = comment.get("line") or comment.get("original_line")
        location = f"{comment.get('path')}:{line}" if comment.get("path") else "none"
        items.append({"id": f"C{comment['id']}", "kind": "inline", "review": int(comment["pull_request_review_id"]),
                      "location": location, "link": comment.get("html_url") or "none",
                      "text": comment.get("body") or ""})
    return items


def approval_comments(reviews: Sequence[dict], comments: Sequence[dict], head: str) -> Tuple[List[dict], List[dict]]:
    """Reviews on the published head without a change request whose inline comments wait for an answer.

    An approval or a plain comment review can still carry inline remarks; each top-level one
    that no lane reply answers yet is an item (ADR 0008). Review bodies are not items here.
    """
    answered = {int(comment["in_reply_to_id"]) for comment in comments
                if comment.get("in_reply_to_id") and "<!-- gsd-path-pr-fix:" in str(comment.get("body") or "")}
    reviews_on_head = [review for review in reviews
                       if review.get("commit_id") == head and review.get("state") in {"APPROVED", "COMMENTED"}]
    ids = {int(review["id"]) for review in reviews_on_head}
    waiting = [comment for comment in comments
               if int(comment.get("pull_request_review_id") or 0) in ids and not comment.get("in_reply_to_id")
               and int(comment["id"]) not in answered and one_line(comment.get("body") or "")]
    used = {int(comment["pull_request_review_id"]) for comment in waiting}
    return [dict(review, body="") for review in reviews_on_head if int(review["id"]) in used], waiting


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


def snapshot(repo: Path, api: Optional[Callable[..., object]] = None, allow_more_rounds: bool = False,
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
    if not _common.reviewed_head_covers(repo, str(final_head or ""), head) or verdict != "pass":
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
        forge_provider(str(row["pull_request"]))
        read = read_forge(str(row["pull_request"]), api)
        pull = read["pull"]
        if pull.get("state") != "open" or pull.get("merged_at"):
            raise LaneError(f"{row['pull_request']} is not open and unmerged")
        if (pull.get("head") or {}).get("sha") != row.get("reviewed_head"):
            raise LaneError(f"{row['pull_request']} head differs from the pending close's reviewed head")
        if final_members.get(str(row["name"])) != row.get("reviewed_head"):
            raise LaneError(f"FINAL.md member head for {row['name']} differs from its pending close")
        if change_requests(read):
            if selected is not None:
                raise LaneError("change requests on two member PRs; answer one PR per round")
            selected, forge = row, read
    approval = False
    if selected is None and record_only:
        # No change request: an approval or comment review may still carry unanswered inline remarks.
        for row in candidates:
            read = read_forge(str(row["pull_request"]), api)
            if approval_items(read, str(row.get("reviewed_head")))[1]:
                if selected is not None:
                    raise LaneError("unanswered review comments on two member PRs; answer one PR per round")
                selected, forge, approval = row, read, True
    if selected is None or forge is None:
        raise LaneError("no active change request on the pending member pull requests")
    if approval:
        requests, items = approval_items(forge, str(selected["reviewed_head"]))
    else:
        requests = change_requests(forge)
        items = request_items(forge, requests)
    if not items:
        raise LaneError("the change request has no item text to answer")
    snapshot_value = {
        "pull_request": selected["pull_request"], "member": selected["name"],
        "head": (forge["pull"].get("head") or {}).get("sha"),
        "body": forge["pull"].get("body") or "",
        "reviews": [{"id": _review_id(item["id"]), "user": (item.get("user") or {}).get("login"),
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
        "review_ids": sorted(_review_id(item["id"]) for item in requests),
        "snapshot_digest": _json_digest(snapshot_value), "capsule_files": files,
        "contracts": {name: _digest(archive / relative) for name, relative in
                      (("INTENT.md", "intent/INTENT.md"), ("PLAN.md", "plan/PLAN.md"))
                      if (archive / relative).is_file()},
        "gaps": [path.name for path in gaps],
        "stage": "captured", "mode": "record" if record_only else "lane", "approval": approval,
    }
    record["capsule_digest"] = _json_digest({"files": files, "head": head, "members": final_members})
    for name, relative in (("INTENT.md", "intent/INTENT.md"), ("PLAN.md", "plan/PLAN.md")):
        if (archive / relative).is_file():
            target = directory / "contracts" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((archive / relative).read_bytes())
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


def _text(value: object) -> str:
    """One line, with every `<placeholder>`-shaped token outside code spans put in backticks.

    The patch gate refuses such a token outside a code span as an unfilled template
    placeholder; review comments and owner rulings quote log formats or HTML that way.
    """
    contracts = _sibling("check_handoffs")
    text = one_line(value)
    quote = lambda chunk: contracts.PLACEHOLDER_PATTERN.sub(lambda found: f"`{found.group(0)}`", chunk)
    pieces, last = [], 0
    for span in contracts.CODE_SPAN_PATTERN.finditer(text):
        pieces += [quote(text[last:span.start()]), span.group(0)]
        last = span.end()
    pieces.append(quote(text[last:]))
    return "".join(pieces)


def source_text(record: Mapping[str, object], snap: Mapping[str, object], fixes: Sequence[dict]) -> str:
    known = {item["id"]: item for item in snap["items"]}
    lines = [f"# PR Review — round {record['round']}", "",
             f"Reviewed HEAD: {record['head']}", "Review verdict: changes-requested",
             f"Pull request: {record['pull_request']}", f"Member: {record['member']}",
             f"Snapshot: {record['snapshot_digest']}", "", "## Items", ""]
    for row in fixes:
        item = known[row["id"]]
        lines += [f"### Item {row['id']} — {_text(row['title'])}", "",
                  f"- **Kind**: {item['kind']}", f"- **Location**: {item['location']}",
                  f"- **Link**: {item['link']}", "- **Class**: fix", f"- **Group**: {_text(row['group'])}",
                  f"- **Found**: {_text(item['text'])}", f"- **Fix direction**: {_text(row['fix_direction'])}", ""]
    return "\n".join(lines).rstrip() + "\n"


def findings_text(record: Mapping[str, object], snap: Mapping[str, object], fixes: Sequence[dict]) -> str:
    known = {item["id"]: item for item in snap["items"]}
    source = f".project/review/pr-review-{record['round']}.md"
    lines = ["# Patch Findings", "", f"Reviewed HEAD: {record['head']}", "State: ship/blocked", "",
             "## Findings", ""]
    for index, row in enumerate(fixes, start=1):
        lines += [f"### P{index:03d} — {_text(row['title'])}", "",
                  f"- **Source**: `{source}`", f"- **Locator**: Item {row['id']}",
                  f"- **Evidence**: {_text(known[row['id']]['text'])}",
                  f"- **Fix direction**: {_text(row['fix_direction'])}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _preflight_handoff(record: Mapping[str, object], snap: Mapping[str, object], fixes: Sequence[dict]) -> None:
    """Read the hand-off this ruling produces the way the patch gate will, before it is locked."""
    contracts = _sibling("check_handoffs")
    source = f".project/review/pr-review-{record['round']}.md"
    source_value, findings = source_text(record, snap, fixes), findings_text(record, snap, fixes)
    try:
        for heading, block in contracts._finding_blocks(findings):
            contracts._reject_placeholder(heading, heading)
            evidence, direction = contracts._source_repair_fields(source, source_value,
                                                                  contracts._field(block, "Locator"))
            if (contracts._field(block, "Evidence"), contracts._field(block, "Fix direction")) != (evidence, direction):
                raise contracts.HandoffError(f"{heading} does not match its source item")
    except contracts.HandoffError as error:
        raise LaneError(f"the hand-off this ruling produces would fail the patch gate ({error}); "
                        "correct the ruling text and run start again (nothing was recorded)") from error


# --- continuation across the archive undo (ADR 0008) ------------------------------------

CONTINUATION_NAME = "pr-fix-continuation-{round}.md"


def _log_suffix(repo: Path) -> List[str]:
    """STATE.md Log lines added since HEAD, verbatim."""
    lines = lambda text: [line for line in text.split("\n## Log\n", 1)[-1].splitlines() if line.startswith("- ")] \
        if "\n## Log\n" in text else []
    head = lines(_git(repo, "show", "HEAD:.project/STATE.md", check=False))
    current = lines((repo / ".project/STATE.md").read_text(encoding="utf-8"))
    return current[len(head):] if current[:len(head)] == head else []


def _write_continuation(repo: Path, directory: Path, archive: str) -> None:
    """Before the archive undo: keep the Log lines and ship lessons it is about to drop.

    Written once; a resumed start after a crash keeps the first receipt.
    """
    path = directory / "continuation.json"
    if path.is_file():
        return
    lessons = _sibling("pipeline_undo")._ship_lessons(repo, archive) or []
    value = {"log": _log_suffix(repo), "lessons": lessons}
    _write_json(path, dict(value, sha256=_json_digest(value)))


def _continuation(directory: Path) -> Optional[dict]:
    path = directory / "continuation.json"
    if not path.is_file():
        return None
    value = _read_json(path)
    if _json_digest({"log": value.get("log"), "lessons": value.get("lessons")}) != value.get("sha256"):
        raise LaneError("the PR fix round's continuation receipt changed after it was written")
    return value


def _restore_continuation_log(repo: Path, directory: Path, round_number: int) -> Optional[str]:
    """After the undo: the dropped Log lines go, byte for byte, into a review file the archive keeps."""
    value = _continuation(directory)
    if not value or not value["log"]:
        return None
    relative = f".project/review/{CONTINUATION_NAME.format(round=round_number)}"
    text = (f"# PR fix round {round_number} — continuation\n\nSTATE.md Log lines recorded before the "
            "round's archive undo, verbatim:\n\n" + "\n".join(value["log"]) + "\n")
    path = repo / relative
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        _common.atomic_write(path, text)
    return f"{relative} sha256 {_digest(path)}"


def restore_lessons(repo: Path) -> dict:
    """Ship lessons step: re-append the lessons the round's archive undo dropped, each once.

    Lines already in LESSONS.md are skipped, so a rerun or a later round appends nothing twice.
    """
    repo = repo.resolve()
    record = active_round(repo)
    value = _continuation(Path(str(record["directory"]))) if record else None
    if not value or not value["lessons"]:
        return {"schema": SCHEMA, "status": "none", "appended": []}
    path = repo / ".project/LESSONS.md"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = set(current.splitlines())
    missing = [line for line in value["lessons"] if line not in present]
    if missing:
        prefix = current if not current or current.endswith("\n") else current + "\n"
        _common.atomic_write(path, prefix + "\n".join(missing) + "\n")
    return {"schema": SCHEMA, "status": "restored" if missing else "present", "appended": missing}


def start(repo: Path, ruling_path: Path) -> dict:
    """Record the ruling, undo the archive, write the hand-off, and block ship for patch mode."""
    repo = repo.resolve()
    with _LaneLock(repo):
        return _start(repo, ruling_path)


def _start(repo: Path, ruling_path: Path) -> dict:
    record = lane_round(repo)
    if record is None:
        raise LaneError("no captured lane round; run snapshot first (a record-only round uses rule)")
    directory = Path(record.pop("directory"))
    snap = _read_json(directory / "snapshot.json")
    if record["stage"] == "captured":
        ruling = _read_json(ruling_path)
        fixes = _validate_ruling(ruling, snap)
        _preflight_handoff(record, snap, fixes)
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
            _write_continuation(repo, directory, state.archive)
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
        continued = _restore_continuation_log(repo, directory, int(record["round"]))
        state = _state(repo)
        if (state.phase, state.status) == ("ship", "active"):
            _sibling("pipeline_state").transition_state(
                repo, {"phase": "ship", "status": "active", "branch": state.branch, "archive": None},
                {"status": "blocked"},
                f"{LANE_EVENT}: round {record['round']}; {record['pull_request']}; "
                f"{len(fixes)} fix item(s); ruling: {one_line(ruling['verbatim'])}"
                + (f"; continuation restored: {continued}" if continued else ""))
        # Round ownership is the membership file `bind` writes, never plan geometry (ADR 0008).
        record["membership"] = MEMBERSHIP_SCHEMA
        record["stage"] = "opened"
        _save_round(repo, record)
    return {"schema": SCHEMA, "status": record["stage"], "round": record["round"],
            "source": f".project/review/pr-review-{record['round']}.md",
            "patch_findings": ".project/review/PATCH-FINDINGS.md",
            "fixes": [{"id": row["id"], "group": row["group"], "title": one_line(row["title"])} for row in fixes]}


def regenerate(repo: Path) -> dict:
    """Rewrite the open round's pr-review source and PATCH-FINDINGS.md from its recorded ruling.

    For a round opened by an earlier runtime whose hand-off text the patch gate now refuses;
    the snapshot, the ruling and its digest stay untouched. Legal only before the round's
    plan is approved.
    """
    repo = repo.resolve()
    record = lane_round(repo)
    if record is None or record.get("stage") != "opened":
        raise LaneError("regenerate needs an opened PR fix lane round")
    state = _state(repo)
    if (state.phase, state.status) not in {("ship", "blocked"), ("plan", "active"), ("plan", "blocked")}:
        raise LaneError(f"regenerate runs before the round's plan is approved; STATE is {state.phase}/{state.status}")
    directory = Path(str(record["directory"]))
    if _digest(directory / "ruling.json") != record.get("ruling_digest"):
        raise LaneError("the recorded ruling changed after it was accepted")
    snap = _read_json(directory / "snapshot.json")
    fixes = _validate_ruling(_read_json(directory / "ruling.json"), snap)
    review = repo / ".project/review"
    source = f"pr-review-{record['round']}.md"
    written = []
    for name, text in ((source, source_text(record, snap, fixes)),
                       ("PATCH-FINDINGS.md", findings_text(record, snap, fixes))):
        path = review / name
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            _common.atomic_write(path, text)
            written.append(f".project/review/{name}")
    return {"schema": SCHEMA, "status": "regenerated", "round": record["round"], "written": written}


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

def _plan_depths(repo: Path) -> Dict[int, str]:
    plan = repo / ".project/plan/PLAN.md"
    if not plan.is_file():
        return {}
    return _sibling("check_handoffs")._plan_waves(plan.read_text(encoding="utf-8"))[0]


# --- round membership (ADR 0008) ---------------------------------------------------

MEMBERSHIP_SCHEMA = "gsd-path/pr-fix-membership/v1"
MEMBERSHIP_NAME = re.compile(r"^pr-fix-round-(?P<round>[1-9]\d*)\.json$")
REPAIR_NAME = re.compile(r"-fix-wave-(?P<wave>[1-9]\d*)-cycle-(?P<cycle>[1-9]\d*)\.md$")
OPEN_STAGES = ("opened", "built")


def membership_path(repo: Path, round_number: int) -> Path:
    return repo / ".project/plan" / f"pr-fix-round-{round_number}.json"


def _lock_path(repo: Path) -> Path:
    root = lane_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    return root


class _LaneLock:
    """Serialize lane commands across the worktrees of one repository (the Git common dir)."""

    def __init__(self, repo: Path) -> None:
        self.path = _lock_path(repo) / ".lock"
        self.handle = None

    def __enter__(self) -> "_LaneLock":
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows has no flock; lane commands run in one worktree
            return self
        self.handle = open(self.path, "a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise LaneError("another PR fix lane command holds the lane lock; retry") from error
        return self

    def __exit__(self, *exc: object) -> None:
        if self.handle is not None:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def task_contract(text: str) -> dict:
    """The task contract as approved: immutable frontmatter and the body, recorded with its length.

    Landing may change only the lifecycle fields and append to the body, so a later check
    recomputes the digest over the same frontmatter and the first `body_length` bytes.
    """
    isolation = _sibling("isolation")
    head, body = isolation.split_frontmatter(text)
    body_bytes = isolation._normalize_newlines(body).encode("utf-8")
    return {"digest": _contract_digest(_contract_frontmatter(head), body_bytes),
            "body_length": len(body_bytes)}


def _contract_frontmatter(head: Sequence[str]) -> List[str]:
    """Frontmatter without the lifecycle fields that activation and landing set (member tasks too)."""
    isolation = _sibling("isolation")
    lifecycle = set(isolation.LANDING_MUTABLE_FIELDS) | set(isolation.MEMBER_ACTIVATION_FIELDS)
    return [line for line in head if isolation._frontmatter_key(line) not in lifecycle]


def _contract_digest(frontmatter: Sequence[str], body: bytes) -> str:
    return _digest_bytes("\n".join(frontmatter).encode("utf-8") + b"\0" + body)


def contract_matches(bound: Mapping[str, object], text: str) -> bool:
    isolation = _sibling("isolation")
    try:
        head, body = isolation.split_frontmatter(text)
    except isolation.IsolationError:
        return False
    body_bytes = isolation._normalize_newlines(body).encode("utf-8")
    length = int(bound.get("body_length") or 0)
    return len(body_bytes) >= length and _contract_digest(
        _contract_frontmatter(head), body_bytes[:length]) == bound.get("digest")


def _task_files(repo: Path) -> Dict[str, dict]:
    """Every task file: id, wave, status, lane_round, Context, whether it is a review repair, and text."""
    contracts = _sibling("check_handoffs")
    tasks: Dict[str, dict] = {}
    directory = repo / ".project/tasks"
    for path in sorted(directory.glob("T*.md")) if directory.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        try:
            fields = contracts._strict_frontmatter(text, path.name)
        except contracts.HandoffError as error:
            raise LaneError(str(error)) from error
        task_id = str(fields.get("id") or "")
        if not re.fullmatch(r"T\d{3,}", task_id):
            continue
        context = re.search(r"(?ms)^## Context\s*$(.*?)^## ", text)
        raw_round = str(fields.get("lane_round") or "")
        tasks[task_id] = {
            "id": task_id, "path": path.relative_to(repo).as_posix(), "text": text,
            "wave": int(fields["wave"]) if str(fields.get("wave") or "").isdigit() else 0,
            "status": str(fields.get("status") or ""),
            "lane_round": int(raw_round) if raw_round.isdigit() else (raw_round or None),
            "repair": "\n## Review findings" in text,
            "context": context.group(1) if context else "",
        }
    return tasks


def _ruling_findings(record: Mapping[str, object]) -> List[dict]:
    """P### rows of the round in PATCH-FINDINGS order: id, item, group."""
    directory = Path(str(record["directory"]))
    snap = _read_json(directory / "snapshot.json")
    fixes = _validate_ruling(_read_json(directory / "ruling.json"), snap)
    return [{"finding": f"P{index:03d}", "item": row["id"], "group": one_line(row["group"])}
            for index, row in enumerate(fixes, start=1)]


def _initial_membership(repo: Path, record: Mapping[str, object], tasks: Mapping[str, dict]) -> dict:
    """The approved grouping and initial task contracts, computed from the ruling and task files."""
    number = int(record["round"])
    findings = _ruling_findings(record)
    known = {row["finding"] for row in findings}
    initial = [task for task in tasks.values() if task["lane_round"] == number and not task["repair"]]
    if not initial:
        raise LaneError(f"no task carries `lane_round: {number}`; add it to each task that closes a P### item")
    closed_by: Dict[str, str] = {}
    for task in initial:
        cited = sorted(set(re.findall(r"\bP\d{3}\b", task["context"])))
        if not cited:
            raise LaneError(f"{task['id']} must cite the P### items it closes in its Context")
        for finding in cited:
            if finding not in known:
                raise LaneError(f"{task['id']} cites unknown finding {finding}")
            if finding in closed_by:
                raise LaneError(f"{finding} is closed by both {closed_by[finding]} and {task['id']}")
            closed_by[finding] = task["id"]
    missing = [row["finding"] for row in findings if row["finding"] not in closed_by]
    if missing:
        raise LaneError("every lane finding needs one task; uncovered: " + ", ".join(missing))
    groups: Dict[str, dict] = {}
    for row in findings:
        group = groups.setdefault(row["group"], {"group": row["group"], "items": [], "findings": [],
                                                 "task": closed_by[row["finding"]]})
        if group["task"] != closed_by[row["finding"]]:
            raise LaneError(f"ruling group {row['group']!r} is split across {group['task']} and "
                            f"{closed_by[row['finding']]}; one task closes a group")
        group["items"].append(row["item"])
        group["findings"].append(row["finding"])
    return {
        "schema": MEMBERSHIP_SCHEMA, "round": number, "pull_request": record["pull_request"],
        "snapshot_digest": record["snapshot_digest"], "ruling_digest": record["ruling_digest"],
        "gaps": sorted(int(GAP_NAME.fullmatch(name).group("number")) for name in record.get("gaps") or []),
        "groups": list(groups.values()),
        "tasks": [{"id": task["id"], "wave": task["wave"], "contract": task_contract(task["text"])}
                  for task in sorted(initial, key=lambda task: task["id"])],
    }


def _approval_digest(membership: Mapping[str, object]) -> str:
    """What the patch plan approval covers: the membership without its later repairs."""
    return _json_digest({key: value for key, value in membership.items() if key != "repairs"})


def read_membership(repo: Path, round_number: int) -> Optional[dict]:
    path = membership_path(repo, round_number)
    if not path.is_file():
        return None
    value = _read_json(path)
    if value.get("schema") != MEMBERSHIP_SCHEMA or value.get("round") != round_number:
        raise LaneError(f"{path.relative_to(repo).as_posix()} is not the membership of PR fix round {round_number}")
    return value


def _approval_receipt(record: Mapping[str, object]) -> Optional[dict]:
    path = Path(str(record["directory"])) / "approval.json"
    return _read_json(path) if path.is_file() else None


def _legacy_refusal(record: Mapping[str, object]) -> str:
    return (f"PR fix lane round {record['round']} was opened by an earlier runtime without an explicit "
            "membership; run `pr_fix_lane.py fall-back --repo <root> --reason \"<why>\"` and continue "
            "on the ordinary route")


def bind(repo: Path, source: Optional[str] = None) -> dict:
    """Write the open round's membership file from the ruling, PATCH-FINDINGS.md, and the task files.

    Idempotent. After the patch plan approval the initial part is frozen; only review repairs
    (tasks with `lane_round` and `## Review findings`) are appended, each with its source review.
    """
    repo = repo.resolve()
    with _LaneLock(repo):
        record = lane_round(repo)
        if record is None or record.get("stage") not in OPEN_STAGES:
            raise LaneError("bind needs an opened or built PR fix lane round")
        if record.get("membership") != MEMBERSHIP_SCHEMA:
            raise LaneError(_legacy_refusal(record))
        number = int(record["round"])
        tasks = _task_files(repo)
        computed = _initial_membership(repo, record, tasks)
        existing = read_membership(repo, number)
        bound = {row["id"]: row["contract"] for row in (existing or {}).get("tasks") or []}
        for row in computed["tasks"]:
            # A landed task keeps its bound contract: landing only appends to the body.
            if row["id"] in bound and contract_matches(bound[row["id"]], tasks[row["id"]]["text"]):
                row["contract"] = bound[row["id"]]
        approval = _approval_receipt(record)
        if approval is not None and approval.get("membership_digest") != _approval_digest(computed):
            raise LaneError(f"the lane tasks of round {number} changed after the patch plan approval; "
                            "a changed lane plan needs a new patch plan approval")
        repairs = list((existing or {}).get("repairs") or [])
        recorded = {row["id"] for row in repairs}
        for row in repairs:
            task = tasks.get(row["id"])
            if task is None or not contract_matches(row["contract"], task["text"]):
                raise LaneError(f"repair {row['id']} of round {number} changed after it was bound")
        initial_ids = {row["id"] for row in computed["tasks"]}
        for task in sorted(tasks.values(), key=lambda task: task["id"]):
            if task["lane_round"] != number or not task["repair"] or task["id"] in recorded:
                continue
            if approval is None:
                raise LaneError(f"repair {task['id']} needs the round's approved patch plan first")
            named = REPAIR_NAME.search(task["path"])
            review = (f".project/review/wave-{named.group('wave')}.cycle{named.group('cycle')}.md"
                      if named else source)
            if not review:
                raise LaneError(f"repair {task['id']} needs --source <review> naming the review that opened it")
            review_path = repo / review
            if not review_path.is_file():
                raise LaneError(f"repair {task['id']} source review {review} is missing")
            repairs.append({"id": task["id"], "wave": task["wave"], "contract": task_contract(task["text"]),
                            "source": review, "source_sha256": _digest(review_path)})
        stray = sorted(task["id"] for task in tasks.values() if task["lane_round"] not in (None, "")
                       and task["lane_round"] == number and task["id"] not in initial_ids
                       and task["id"] not in {row["id"] for row in repairs})
        if stray:
            raise LaneError("tasks carry the round but are not members: " + ", ".join(stray))
        value = dict(computed, repairs=repairs)
        path = membership_path(repo, number)
        text = _canonical_json(value) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            _common.atomic_write(path, text)
        return {"schema": SCHEMA, "status": "bound", "round": number,
                "membership": path.relative_to(repo).as_posix(), "digest": _digest(path),
                "tasks": [row["id"] for row in value["tasks"]], "repairs": [row["id"] for row in repairs]}


def record_patch_approval(repo: Path) -> Optional[dict]:
    """Called by the patch plan approval, under the state lock, before STATE is written.

    Records the approved membership digest in the round directory (inside the Git common
    dir). Build entry compares the membership file against it.
    """
    record = lane_round(repo)
    if record is None or record.get("stage") not in OPEN_STAGES or record.get("membership") != MEMBERSHIP_SCHEMA:
        return None
    membership = read_membership(repo, int(record["round"]))
    if membership is None:
        raise LaneError(f"PR fix round {record['round']} has no membership file; run "
                        "`pr_fix_lane.py bind --repo <root>` before the approval")
    receipt = {"kind": "patch", "round": record["round"], "membership_digest": _approval_digest(membership)}
    _write_json(Path(str(record["directory"])) / "approval.json", receipt)
    return receipt


def require_approved_membership(repo: Path) -> None:
    """Build entry: the membership must be the one the patch plan approval recorded."""
    record = lane_round(repo)
    if record is None or record.get("stage") not in OPEN_STAGES:
        return
    if record.get("membership") != MEMBERSHIP_SCHEMA:
        raise LaneError(_legacy_refusal(record))
    membership = read_membership(repo, int(record["round"]))
    approval = _approval_receipt(record)
    if membership is None or approval is None:
        raise LaneError(f"PR fix round {record['round']} has no approved membership; bind it and approve the patch plan")
    if approval.get("membership_digest") != _approval_digest(membership):
        raise LaneError(f"the membership of PR fix round {record['round']} differs from the approved one; "
                        "a changed lane plan needs a new patch plan approval")


def _round_records(repo: Path) -> Dict[int, dict]:
    """Membership files in .project/plan, by round."""
    directory = repo / ".project/plan"
    found: Dict[int, dict] = {}
    for path in sorted(directory.glob("pr-fix-round-*.json")) if directory.is_dir() else []:
        match = MEMBERSHIP_NAME.fullmatch(path.name)
        if match:
            found[int(match.group("round"))] = read_membership(repo, int(match.group("round"))) or {}
    return found


def member_ids(membership: Mapping[str, object]) -> List[str]:
    return [row["id"] for row in membership.get("tasks") or []] + [row["id"] for row in membership.get("repairs") or []]


def lane_plan_errors(repo: Path, waves: Mapping[int, str], tasks: Sequence[Mapping[str, object]]) -> List[str]:
    """Plan-gate checks for PR fix lane rounds, read from their membership files (ADR 0008).

    The open round's members must match its membership in both directions and keep their
    bound contracts; delta waves owned by no open round are landed history.
    """
    delta = sorted(number for number, depth in waves.items() if depth == "delta")
    files = _task_files(repo)
    fields = {task_id: task["lane_round"] for task_id, task in files.items() if task["lane_round"] not in (None, "")}
    if not delta and not fields:
        return []
    errors: List[str] = []
    record = lane_round(repo)
    by_wave: Dict[int, List[Mapping[str, object]]] = {}
    for task in tasks:
        by_wave.setdefault(int(task.get("wave") or 0), []).append(task)
    unfinished = lambda task: str(task.get("status") or "") not in ("done", "withdrawn")
    open_round = (int(record["round"]) if record is not None and record.get("stage") in (*OPEN_STAGES, "viewed")
                  else None)
    fell_back = record is not None and record.get("stage") == FELL_BACK
    if open_round is not None and record.get("membership") != MEMBERSHIP_SCHEMA:
        return [_legacy_refusal(record)]
    for task_id, value in sorted(fields.items()):
        if not isinstance(value, int):
            errors.append(f"{task_id} lane_round must be a round number")
        elif value != open_round and not (fell_back and value == int(record["round"])) and \
                unfinished(files[task_id]):
            errors.append(f"{task_id} belongs to closed PR fix round {value} and is {files[task_id]['status']}")
    members: List[str] = []
    if open_round is not None:
        membership = read_membership(repo, open_round)
        if membership is None:
            return errors + [f"PR fix round {open_round} has no membership file; run "
                             "`pr_fix_lane.py bind --repo <root>`"]
        members = member_ids(membership)
        if (membership.get("ruling_digest"), membership.get("snapshot_digest")) != \
                (record.get("ruling_digest"), record.get("snapshot_digest")):
            errors.append(f"the membership of round {open_round} is not bound to its recorded ruling")
        findings = (repo / ".project/review/PATCH-FINDINGS.md")
        ids = re.findall(r"(?m)^### (P\d{3}) — ", findings.read_text(encoding="utf-8")) if findings.is_file() else []
        bound = [finding for group in membership.get("groups") or [] for finding in group["findings"]]
        if sorted(ids) != sorted(bound):
            errors.append("PATCH-FINDINGS.md and the round membership name different P### items")
        carriers = sorted(task_id for task_id, value in fields.items() if value == open_round)
        if carriers != sorted(members):
            missing = sorted(set(members) - set(carriers))
            extra = sorted(set(carriers) - set(members))
            if missing:
                errors.append(f"membership tasks without `lane_round: {open_round}`: " + ", ".join(missing))
            if extra:
                errors.append(f"tasks with `lane_round: {open_round}` outside the membership (run bind): "
                              + ", ".join(extra))
        for row in [*(membership.get("tasks") or []), *(membership.get("repairs") or [])]:
            task = files.get(row["id"])
            if task is None:
                errors.append(f"membership task {row['id']} has no task file")
            elif not contract_matches(row["contract"], task["text"]):
                errors.append(f"{row['id']} changed after it was bound; a changed lane plan needs a new approval")
            elif task["wave"] != row["wave"]:
                errors.append(f"{row['id']} moved from wave {row['wave']} to wave {task['wave']}")
        member_waves = sorted({files[task_id]["wave"] for task_id in members if task_id in files})
        for wave in member_waves:
            if waves.get(wave) != "delta":
                errors.append(f"wave {wave} of PR fix round {open_round} must have Review depth: delta")
            others = [str(task["id"]) for task in by_wave.get(wave, []) if str(task["id"]) not in members]
            if others:
                errors.append(f"wave {wave} of PR fix round {open_round} holds non-member tasks: " + ", ".join(others))
        if member_waves and any(wave > min(member_waves) and wave not in member_waves for wave in waves):
            errors.append("the PR fix lane waves must be the last waves of the plan")
    for wave in delta:
        for task in by_wave.get(wave, []):
            if str(task["id"]) in members or not unfinished(task):
                continue
            if fell_back:
                errors.append(f"{task['id']} in delta wave {wave} is {task.get('status')}; after a PR fix lane "
                              "fallback the delta waves are history: move it to a standard wave "
                              "(`Review depth: full`) through the ordinary plan route")
            elif fields.get(str(task["id"])) is None:
                errors.append(f"{task['id']} in delta wave {wave} is {task.get('status')}; Review depth: delta "
                              "is legal only for a member task of an open PR fix lane round")
    return errors


# --- delta review ------------------------------------------------------------------

def _delta_section(text: str) -> Optional[str]:
    match = re.search(rf"(?m)^## {re.escape(DELTA_SECTION)}\s*$", text)
    if not match:
        return None
    body = text[match.end():]
    following = re.search(r"(?m)^## ", body)
    return body[: following.start() if following else len(body)]


def _blocks(section: str) -> List[Tuple[str, str]]:
    """The `###` blocks of a section, in order; a repeated heading identity is kept for refusal."""
    matches = list(re.finditer(r"(?m)^### (.+)$", section))
    return [(match.group(1).strip(), section[match.end(): (matches[index + 1].start() if index + 1 < len(matches)
                                                            else len(section))])
            for index, match in enumerate(matches)]


def _bullet(block: str, field: str) -> Optional[str]:
    values = _common.find_review_bullet_field_values(block.splitlines(), field)
    return values[0].strip() if len(values) == 1 else None


def _single_line(section: str, label: str) -> Optional[str]:
    values = re.findall(rf"(?m)^{re.escape(label)}[ \t]*(\S.*?)[ \t]*$", section)
    if len(values) > 1:
        raise LaneError(f"the delta review repeats `{label}`")
    return values[0] if values else None


def parse_delta(text: str, criteria: Sequence[str], gaps: Sequence[int], item_ids: Sequence[str]) -> dict:
    """Parse and check the `## PR fix delta` section of a delta wave review.

    Every item, criterion, and gap appears exactly once; a closed item cites its evidence.
    The cumulative fields (Round tasks, Membership, Product HEAD, Member product HEAD) are
    returned as found; the wave gate decides where they are required.
    """
    section = _delta_section(text)
    if section is None:
        raise LaneError(f"a delta wave review needs a `## {DELTA_SECTION}` section")
    eligibility = _single_line(section, "Eligibility:")
    inventory = _single_line(section, "Risk inventory:")
    if eligibility not in {"local", "not-local"}:
        raise LaneError("the delta review needs `Eligibility: local` or `Eligibility: not-local`")
    if inventory not in {"complete", "new"}:
        raise LaneError("the delta review needs `Risk inventory: complete` or `Risk inventory: new`")
    contracts = _sibling("check_handoffs")
    items: Dict[str, str] = {}
    criterion_rows: Dict[str, dict] = {}
    gap_rows: Dict[int, dict] = {}
    for heading, block in _blocks(section):
        item = re.fullmatch(r"Item ([RCT]\d+): (closed|not-closed)", heading)
        criterion = re.fullmatch(r"(SC[1-9]\d*) — (affected|unaffected)", heading)
        gap = re.fullmatch(r"Gap ([1-9]\d*) — (affected|unaffected)", heading)
        if item:
            if item.group(1) in items:
                raise LaneError(f"the delta review reports item {item.group(1)} twice")
            items[item.group(1)] = item.group(2)
            evidence = _bullet(block, "Evidence")
            if item.group(2) == "closed":
                if not evidence or evidence.casefold() == "none" or contracts.PLACEHOLDER_PATTERN.search(
                        contracts.CODE_SPAN_PATTERN.sub("", evidence)):
                    raise LaneError(f"closed item {item.group(1)} needs its Evidence")
        elif criterion:
            if criterion.group(1) in criterion_rows:
                raise LaneError(f"the delta review maps {criterion.group(1)} twice")
            criterion_rows[criterion.group(1)] = {"impact": criterion.group(2), "block": block}
        elif gap:
            if int(gap.group(1)) in gap_rows:
                raise LaneError(f"the delta review maps gap {gap.group(1)} twice")
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
    round_tasks = _single_line(section, "Round tasks:")
    member_heads: Dict[str, str] = {}
    for name, sha in re.findall(r"(?m)^Member product HEAD:[ \t]*(\S+)[ \t]+([0-9a-f]{40})[ \t]*$", section):
        if name in member_heads:
            raise LaneError(f"the delta review names member {name} twice")
        member_heads[name] = sha
    return {"eligibility": eligibility, "inventory": inventory, "items": items,
            "criteria": criterion_rows, "gaps": gap_rows,
            "round_tasks": [task.strip() for task in round_tasks.split(",")] if round_tasks else None,
            "membership": _single_line(section, "Membership:"),
            "product_head": _single_line(section, "Product HEAD:"), "member_heads": member_heads}


def _delta_coverage_error(repo: Path, delta_path: Path) -> Optional[str]:
    """Why the delta review no longer covers the product that ships, or None.

    The review judged the product as committed when it was recorded. A later member rebase
    adoption, a new member base, or another landing changes that product, so its
    affected/unaffected map cannot stand in for the final review any more.
    """
    relative = delta_path.relative_to(repo).as_posix()
    recorded = _git(repo, "log", "-1", "--format=%H", "--", relative, check=False)
    if not recorded:
        return f"{relative} is not committed; record the wave review before the views"
    changed = set(_git(repo, "diff", "--name-only", recorded, "HEAD").splitlines())
    isolation = _sibling("isolation")
    members = _sibling("members")
    moved = sorted(changed & {isolation.MEMBER_REBASE_PATH, members.LOCK_PATH})
    if moved:
        return (f"the member product changed after {relative} was recorded ({', '.join(moved)}); "
                "use the ordinary final review")
    for path in sorted(changed):
        if not re.fullmatch(r"\.project/tasks/T\d{3}[^/]*\.md", path) or not (repo / path).is_file():
            continue
        status_now = re.search(r"(?m)^status:\s*(\S+)", (repo / path).read_text(encoding="utf-8"))
        before = _git(repo, "show", f"{recorded}:{path}", check=False)
        status_then = re.search(r"(?m)^status:\s*(\S+)", before)
        if status_now and status_now.group(1) == "done" and (not status_then or status_then.group(1) != "done"):
            return f"{path} landed after {relative} was recorded; use the ordinary final review"
    return None


def delta_report(repo: Path, wave: int) -> Tuple[Path, str]:
    """The latest cycle of the delta wave review."""
    review = repo / ".project/review"
    cycles = sorted(((int(match.group(1)), path) for path in review.glob(f"wave-{wave}.cycle*.md")
                     if (match := re.fullmatch(rf"wave-{wave}\.cycle([1-9]\d*)\.md", path.name))))
    if not cycles:
        raise LaneError(f"the lane wave {wave} has no review yet")
    path = cycles[-1][1]
    return path, path.read_text(encoding="utf-8")


def _round_record(repo: Path, round_number: int) -> Optional[dict]:
    """The recorded round of this milestone's archive, open or closed."""
    pointer = lane_root(repo) / "active.json"
    if not pointer.is_file():
        return None
    directory = _round_dir(repo, str(_read_json(pointer)["archive_name"]), round_number)
    return dict(_read_json(directory / "round.json"), directory=str(directory)) \
        if (directory / "round.json").is_file() else None


def _owner_of_wave(repo: Path, wave: int) -> Optional[Tuple[dict, dict]]:
    """The round whose membership holds a task in this wave, with that membership."""
    for number, membership in sorted(_round_records(repo).items()):
        if any(row["wave"] == wave for row in [*(membership.get("tasks") or []), *(membership.get("repairs") or [])]):
            record = _round_record(repo, number)
            return (record, membership) if record is not None else None
    return None


def coordinator_equivalent(repo: Path, reviewed: str, head: str) -> bool:
    """Whether every coordinator commit after the reviewed one is lane bookkeeping (ADR 0008).

    Allowed: non-merge commits that change only `.project/` outside INTENT.md, PLAN.md, and
    the round membership files, or installer paths. The generic installer-only
    `_common.reviewed_head_covers` keeps its contract for its other callers.
    """
    if not SHA.fullmatch(reviewed or ""):
        return False
    if reviewed == head:
        return True
    ancestry = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", reviewed, head],
                              capture_output=True)
    if ancestry.returncode != 0:
        return False
    for commit in _git(repo, "rev-list", f"{reviewed}..{head}").split():
        parents = _git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) != 1:
            return False
        paths = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if not paths:
            return False
        for path in paths:
            if _common.installer_path(path):
                continue
            if not path.startswith(".project/") or path.startswith(".project/intent/") \
                    or path == ".project/plan/PLAN.md" or MEMBERSHIP_NAME.fullmatch(PurePosixPath(path).name):
                return False
    return True


def _findings_map(repo: Path) -> Dict[str, str]:
    findings_text_value = (repo / ".project/review/PATCH-FINDINGS.md").read_text(encoding="utf-8")
    return dict(re.findall(r"(?m)^### (P\d{3}) — .*\n\n- \*\*Source\*\*: .*\n- \*\*Locator\*\*: Item (\S+)",
                           findings_text_value))


def render_views(repo: Path, record: Mapping[str, object], membership: Mapping[str, object], delta: dict,
                 delta_relative: str, head: str, members: Mapping[str, str]) -> Tuple[str, Dict[int, str]]:
    """FINAL.md and the gap reports from 2 that the round's capsule and delta produce."""
    directory = Path(str(record["directory"]))
    capsule = directory / "capsule/review"
    ruling = _read_json(directory / "ruling.json")
    task_of = {finding: str(group["task"]) for group in membership["groups"] for finding in group["findings"]}
    final = build_final_view((capsule / "FINAL.md").read_text(encoding="utf-8"), delta, head, members,
                             record, ruling, delta_relative, _findings_map(repo), task_of)
    gaps = {number: build_gap_view((capsule / f"final-gap-{number}.md").read_text(encoding="utf-8"),
                                   delta["gaps"][number], head, members, record, delta_relative)
            for number in membership["gaps"] if number > 1}
    return final, gaps


def _cumulative_errors(repo: Path, membership: Mapping[str, object], delta: dict) -> List[str]:
    """The round's cumulative assessment must name every member and the reviewed product vector."""
    errors = []
    members = member_ids(membership)
    if delta["round_tasks"] != members:
        errors.append("the cumulative delta review must list `Round tasks: " + ", ".join(members) + "`")
    files = _task_files(repo)
    unfinished = [task_id for task_id in members if (files.get(task_id) or {}).get("status") != "done"]
    if unfinished:
        errors.append("the cumulative delta review runs after every member landed; pending: " + ", ".join(unfinished))
    digest = _digest(membership_path(repo, int(membership["round"])))
    if delta["membership"] != digest:
        errors.append(f"the cumulative delta review must record `Membership: {digest}`")
    head = delta["product_head"] or ""
    if not SHA.fullmatch(head) or not coordinator_equivalent(repo, head, _git(repo, "rev-parse", "HEAD")):
        errors.append("the cumulative delta review must record the coordinator commit it judged as `Product HEAD`")
    tips = _sibling("check_handoffs")._member_tips(repo)
    if delta["member_heads"] != tips:
        expected = "; ".join(f"Member product HEAD: {name} {sha}" for name, sha in sorted(tips.items()))
        errors.append(f"the cumulative delta review must record the member tips it judged ({expected})")
    return errors


def delta_errors(repo: Path, text: str, wave: Optional[int] = None, relative: Optional[str] = None) -> List[str]:
    """Wave-gate checks for a passing delta review against the round that owns its wave.

    The round's first lane wave carries the cumulative assessment: it must name every member
    task and the reviewed product vector, and the views it produces must pass the final
    gate's content rules (gap 1, the project Verify, is deferred to ship).
    """
    owner = _owner_of_wave(repo, wave) if wave is not None else None
    if owner is None:
        record = lane_round(repo)
        if record is None:
            return ["a delta wave review needs an open PR fix lane round"]
        if record.get("membership") == MEMBERSHIP_SCHEMA:
            return [f"delta wave {wave} belongs to no PR fix round membership; run `pr_fix_lane.py bind`"]
        membership = None  # a round opened by an earlier runtime keeps the earlier check
    else:
        record, membership = owner
    directory = Path(str(record["directory"]))
    contracts = _sibling("check_handoffs")
    criteria = list(contracts._success_criteria((repo / ".project/intent/INTENT.md").read_text(encoding="utf-8")))
    gaps = [int(GAP_NAME.fullmatch(name).group("number")) for name in record.get("gaps", [])
            if GAP_NAME.fullmatch(name) and int(GAP_NAME.fullmatch(name).group("number")) > 1]
    try:
        delta = parse_delta(text, criteria, gaps, list(record.get("fixes") or []))
    except LaneError as error:
        return [str(error)]
    capsule = directory / "capsule/review"
    if not (capsule / "FINAL.md").is_file():
        return ["the lane capsule lost its FINAL.md"]
    if membership is None or wave != min(row["wave"] for row in membership["tasks"]):
        return []
    if membership.get("gaps") != sorted(int(GAP_NAME.fullmatch(name).group("number"))
                                        for name in record.get("gaps") or []):
        return ["the round membership and its capsule name different gap reports"]
    if record.get("stage") not in OPEN_STAGES:
        return []  # a closed round's wave: its views were proven at its own ship
    errors = _cumulative_errors(repo, membership, delta)
    if errors:
        return errors
    if delta["eligibility"] != "local" or delta["inventory"] != "complete":
        return []  # the designed fallback; the views are not attempted
    try:
        final, gap_texts = render_views(repo, record, membership, delta, relative or "delta review",
                                        str(delta["product_head"]), delta["member_heads"])
        gap_one = _replace_header((capsule / "final-gap-1.md").read_text(encoding="utf-8"),
                                  str(delta["product_head"]), delta["member_heads"])
        contracts.validate_final_content(contracts.final_contract(repo), final, {1: gap_one, **gap_texts},
                                         deferred=(1,))
    except (LaneError, contracts.HandoffError) as error:
        return [f"the views this delta review produces would fail the final gate: {error}"]
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
                if value is None and field == "Surface":
                    # The criterion's surface does not change with the lane diff: inherit the
                    # baseline's, so a delta review that omits it still yields a valid FINAL.md.
                    value = _bullet(block, "Surface")
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


def _lane_members(repo: Path, record: Mapping[str, object]) -> Tuple[dict, int, Dict[str, dict]]:
    """The round's membership, its first lane wave, and its member task files (ADR 0008)."""
    membership = read_membership(repo, int(record["round"]))
    if membership is None:
        raise LaneError(f"PR fix round {record['round']} has no membership file")
    files = _task_files(repo)
    tasks = {task_id: files[task_id] for task_id in member_ids(membership) if task_id in files}
    if len(tasks) != len(member_ids(membership)) or not membership.get("tasks"):
        raise LaneError("a member task of the round has no task file")
    return membership, min(row["wave"] for row in membership["tasks"]), tasks


def views(repo: Path, expected_head: str) -> dict:
    """Generate FINAL.md and gap views for the active lane round; refuse when anything is unproven."""
    repo = repo.resolve()
    with _LaneLock(repo):
        return _views(repo, expected_head)


def _views(repo: Path, expected_head: str) -> dict:
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
    membership, wave, tasks = _lane_members(repo, record)
    if not tasks or any(task["status"] != "done" for task in tasks.values()):
        raise LaneError("every lane task must be done before the views")
    proof = _sibling("build_state").verify_landed_tasks(str(repo), ".project", head)
    landings = {row["task"]["id"]: row.get("landed_commit") for row in proof.get("tasks") or []
                if isinstance(row.get("task"), dict) and row["task"].get("id") in tasks}
    if set(landings) != set(tasks) or not all(landings.values()):
        raise LaneError("a lane task is not proven landed", "provenance")
    delta_path, delta_text = delta_report(repo, wave)
    stale = _delta_coverage_error(repo, delta_path)
    if stale:
        raise LaneError(stale, "stale-product")
    if contracts._line_value(delta_text, "Wave verdict:") != "pass" or \
            contracts._line_value(delta_text, "Depth:") != "delta":
        raise LaneError(f"{delta_path.name} is not a passing delta review")
    criteria = list(contracts._success_criteria((repo / ".project/intent/INTENT.md").read_text(encoding="utf-8")))
    capsule = directory / "capsule/review"
    capsule_gaps = sorted(int(GAP_NAME.fullmatch(path.name).group("number"))
                          for path in capsule.iterdir() if GAP_NAME.fullmatch(path.name))
    if capsule_gaps != membership["gaps"]:
        raise LaneError("the round capsule and its membership name different gap reports", "provenance")
    delta = parse_delta(delta_text, criteria, [number for number in capsule_gaps if number > 1],
                        list(record.get("fixes") or []))
    if delta["eligibility"] != "local":
        raise LaneError("the delta review found a non-local change; use the ordinary final review", "non-local")
    if delta["inventory"] != "complete":
        raise LaneError("the delta review found a new integration risk; use the ordinary final review", "new-risk")
    if any(value != "closed" for value in delta["items"].values()):
        raise LaneError("a lane item is not closed")
    cumulative = _cumulative_errors(repo, membership, delta)
    if cumulative:
        raise LaneError("; ".join(cumulative), "stale-product")
    if delta["member_heads"] != members:
        raise LaneError("the member tips changed after the delta review", "stale-product")
    contract_error = _contract_drift(repo, record, directory, membership)
    if contract_error:
        raise LaneError(contract_error, "provenance")
    relative_delta = delta_path.relative_to(repo).as_posix()
    final, gap_texts = render_views(repo, dict(record, directory=str(directory)), membership, delta,
                                    relative_delta, head, members)
    outputs: Dict[str, str] = {}
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
    for path in sorted((directory / "capsule/publications").glob("*")):
        if path.is_file():
            target = preserved / "publications" / path.name
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
        "capsule_files": record["capsule_files"],
        "membership": {"path": membership_path(repo, int(record["round"])).relative_to(repo).as_posix(),
                       "sha256": _digest(membership_path(repo, int(record["round"])))},
        "product": {"head": delta["product_head"], "members": delta["member_heads"]},
        "previous": _previous_provenance(review, int(record["round"])),
    }
    _write_json(preserved / "provenance.json", provenance)
    record.update(stage="viewed", outcome="views")
    _save_round(repo, record)
    return {"schema": SCHEMA, "status": "complete", "round": record["round"], "head": head,
            "outputs": sorted(outputs), "provenance": f".project/review/pr-fix-{record['round']}/provenance.json"}


def _plan_sections(text: str) -> Dict[str, str]:
    """PLAN.md `## ` sections by heading line; a lane wave is one section."""
    matches = list(re.finditer(r"(?m)^## .*$", text))
    return {match.group(0).strip(): text[match.end(): matches[index + 1].start() if index + 1 < len(matches)
                                         else len(text)].strip()
            for index, match in enumerate(matches)}


def _contract_drift(repo: Path, record: Mapping[str, object], directory: Path,
                    membership: Mapping[str, object]) -> Optional[str]:
    """Why INTENT.md or PLAN.md differ from the round's baseline beyond its own lane waves, or None."""
    contracts = record.get("contracts") or {}
    intent = repo / ".project/intent/INTENT.md"
    if "INTENT.md" in contracts and (not intent.is_file() or _digest(intent) != contracts["INTENT.md"]):
        return "INTENT.md changed after the round's baseline; use the ordinary final review"
    baseline = directory / "contracts/PLAN.md"
    if not baseline.is_file():
        return None  # a round captured by an earlier runtime kept only the digest
    waves = {row["wave"] for row in [*(membership.get("tasks") or []), *(membership.get("repairs") or [])]}
    own = lambda heading: (match := re.match(r"## Wave (\d+)\b", heading)) is not None and int(match.group(1)) in waves
    current = {heading: body for heading, body in _plan_sections((repo / ".project/plan/PLAN.md").read_text(
        encoding="utf-8")).items() if not own(heading)}
    if current != _plan_sections(baseline.read_text(encoding="utf-8")):
        return "PLAN.md changed outside the round's lane waves; use the ordinary final review"
    return None


def _previous_provenance(review: Path, round_number: int) -> Optional[dict]:
    earlier = sorted((int(match.group("round")), path) for path in review.iterdir()
                     if path.is_dir() and (match := ROUND_DIR.fullmatch(path.name))
                     and int(match.group("round")) < round_number and (path / "provenance.json").is_file())
    if not earlier:
        return None
    number, path = earlier[-1]
    return {"round": number, "sha256": _digest(path / "provenance.json")}


def _validate_capsule(review_dir: Path, preserved: Path, provenance: Mapping[str, object]) -> None:
    """Recompute the round's capsule digest from the archived baseline files."""
    files = provenance.get("capsule_files")
    if files is None:
        return  # recorded by an earlier runtime: read-only compatibility, nothing to backfill
    for key, digest in files.items():
        kind, _, relative = key.partition("/")
        if kind == "review":
            path = (review_dir / relative if ROUND_DIR.fullmatch(relative.split("/")[0])
                    else preserved / "baseline" / relative)
        elif kind == "publications":
            path = preserved / "publications" / relative
        else:
            raise LaneError(f"unknown capsule entry {key} in {preserved.name}")
        if not path.is_file() or _digest(path) != digest:
            raise LaneError(f"{preserved.name} baseline {relative} differs from its capsule")
    recomputed = _json_digest({"files": files, "head": provenance["baseline_head"],
                               "members": provenance["baseline_members"]})
    if recomputed != provenance["capsule_digest"]:
        raise LaneError(f"{preserved.name} capsule digest does not match its baseline files")


def validate_views(repo: Path, review_dir: Path) -> None:
    """Re-prove the newest round's provenance from files alone (active or archived review/).

    Earlier rounds are walked through their recorded pointers: each capsule is recomputed
    from the archived baseline files. Records from earlier runtimes are read as they are.
    """
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
    if "membership" in provenance:
        path = review_dir.parent / "plan" / PurePosixPath(provenance["membership"]["path"]).name
        if not path.is_file() or _digest(path) != provenance["membership"]["sha256"]:
            raise LaneError(f"{provenance['membership']['path']} differs from its PR fix lane provenance")
        if sorted(member_ids(_read_json(path))) != sorted(provenance["tasks"]):
            raise LaneError("the landed tasks of the PR fix round differ from its membership")
    current, record = newest, provenance
    while True:
        _validate_capsule(review_dir, current, record)
        pointer = record.get("previous")
        if not pointer:
            break
        current = review_dir / f"pr-fix-{pointer['round']}"
        if not (current / "provenance.json").is_file() or _digest(current / "provenance.json") != pointer["sha256"]:
            raise LaneError(f"pr-fix-{pointer['round']}/provenance.json differs from the next round's pointer")
        record = _read_json(current / "provenance.json")


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
    """The conversation holding the comment, whether it opened the thread or replied in it."""
    for thread in threads:
        nodes = (thread.get("comments") or {}).get("nodes") or []
        if any(node.get("databaseId") == comment_id for node in nodes):
            return thread
    return None


def post_threads(plan: Mapping[str, object], api: Optional[Callable[..., object]] = None) -> dict:
    """Answer every review item of a round in one COMMENT review, then resolve the fixed threads.

    `plan` holds pull_request, head (the published head the replies cite), round, language, and
    items with id (R<review>|C<comment>|T<Azure thread>), kind, class (fix|redesign|follow-up), note and optional
    link. Fixed items get a reply and a resolved thread; follow-up items get a reply and stay open;
    the review body lists every item, so body-only items are answered too. Markers make a rerun
    skip what was already posted, and a pending review a crash left behind is replaced.
    """
    provider, api = _forge_api(str(plan["pull_request"]), api)
    if provider == "azure":
        return _post_azure_threads(plan, api)
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
        lines = _summary_lines(plan, text, head)
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


def _summary_lines(plan: Mapping[str, object], text: Mapping[str, str], head: str) -> List[str]:
    lines = [text["summary"].format(round=plan["round"], head=head[:12]), ""]
    for item in plan["items"]:
        status = text["later"] if item["class"] == "follow-up" else text["fixed"]
        label = f"[{item['id']}]({item['link']})" if item.get("link") and item["link"] != "none" else item["id"]
        lines.append(f"- {label}: {status} — {one_line(item['note'])}")
    return lines


def _post_azure_threads(plan: Mapping[str, object], call: Callable[..., object]) -> dict:
    """Azure: reply inside each item's thread, set the fixed ones to `fixed`, post one summary thread.

    Azure has no review that batches replies, so each reply is its own comment and its marker
    makes a rerun skip it. The summary is a general thread created `closed`, so the
    comment-resolution policy never waits on it. A follow-up thread stays active.
    """
    azure = _sibling("azure_repos")
    repository, number = azure.pull_request_parts(str(plan["pull_request"]))
    head = str(plan["head"])
    text = REPLY_TEXT.get(str(plan.get("language") or "en"), REPLY_TEXT["en"])
    summary_marker = REPLY_MARKER.format(key=f"{plan['round']}/review/{head}")
    replied, skipped, resolved, missing = [], [], [], []
    try:
        pull = azure.show(repository, number, call)
        if (pull.get("head") or {}).get("sha") != head:
            raise LaneError(f"the PR shows {str((pull.get('head') or {}).get('sha'))[:12]}, not the round's "
                            f"published head {head[:12]}; publish it through close-members first")
        index = {int(thread["id"]): thread for thread in azure.pull_request_threads(repository, number, call)}
        done = any(summary_marker in str(comment.get("content") or "")
                   for thread in index.values() for comment in thread["comments"] if isinstance(comment, dict))
        items = list(plan["items"])
        for item in items:
            identifier = str(item["id"])
            thread = index.get(int(identifier[1:])) if identifier.startswith("T") else None
            if thread is None or thread.get("isDeleted"):
                missing.append(identifier)
                continue
            marker = REPLY_MARKER.format(key=f"{plan['round']}/{identifier}/{head}")
            comments = [comment for comment in thread["comments"] if isinstance(comment, dict)]
            if any(marker in str(comment.get("content") or "") for comment in comments):
                skipped.append(identifier)
                continue
            first = min((int(comment["id"]) for comment in comments if isinstance(comment.get("id"), int)), default=0)
            kind = "follow-up" if item["class"] == "follow-up" else "fix"
            body = text[kind].format(head=head[:12], note=one_line(item["note"])) + f"\n\n{marker}"
            azure.reply_to_thread(repository, number, int(identifier[1:]), first, body, call)
            replied.append(identifier)
        if not done:
            azure.create_thread(repository, number, "\n".join(_summary_lines(plan, text, head)) +
                                f"\n\n{summary_marker}", "closed", call)
        for item in items:
            identifier = str(item["id"])
            if item["class"] == "follow-up" or identifier in missing:
                continue
            thread = index[int(identifier[1:])]
            if thread.get("status") in AZURE_OPEN_THREADS:
                azure.set_thread_status(repository, number, int(identifier[1:]), "fixed", call)
                resolved.append(identifier)
    except azure.AzureError as error:
        raise LaneError(str(error)) from error
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
    if not head or (head == record["members"].get(str(record["member"])) and not record.get("approval")):
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


def threads(repo: Path, api: Optional[Callable[..., object]] = None) -> dict:
    """Answer and resolve the round's PR conversations after close-members published the new head."""
    repo = repo.resolve()
    plan = round_plan(repo)
    record = plan.pop("_record")
    result = post_threads(plan, api)
    record["stage"] = "replied"
    _save_round(repo, record)
    return result


def ship_check(repo: Path, api: Optional[Callable[..., object]] = None, override: Optional[Path] = None) -> dict:
    """Refuse shipment while a change request is still active on the lane's PR."""
    repo = repo.resolve()
    record = active_round(repo)
    if record is None:
        return {"schema": SCHEMA, "status": "not-applicable"}
    if record.get("stage") == "captured":
        raise LaneError(f"PR fix round {record['round']} captured items that were never ruled; record the "
                        "owner's ruling (`rule --ruling <file>` for a record-only round) and answer them first")
    read = read_forge(str(record["pull_request"]), api)
    requests = change_requests(read)
    if not requests:
        _set_active(repo, record, status="closed")
        return {"schema": SCHEMA, "status": "clear"}
    head = (read["pull"].get("head") or {}).get("sha")
    outstanding = sorted(_review_id(item["id"]) for item in requests)
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
    for name in ("snapshot", "start", "rule", "regenerate", "bind", "views", "fall-back", "threads", "ship-check",
                 "restore-lessons", "status"):
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
        if name == "bind":
            command.add_argument("--source", help="the review that opened a repair task outside fix-tasks")
        if name == "fall-back":
            command.add_argument("--reason", default="",
                                 help="why the ordinary final review replaces the lane views")
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
        elif args.command == "regenerate":
            result = regenerate(args.repo)
        elif args.command == "bind":
            result = bind(args.repo, args.source)
        elif args.command == "restore-lessons":
            result = restore_lessons(args.repo)
        elif args.command == "fall-back":
            result = fall_back(args.repo, args.reason)
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
