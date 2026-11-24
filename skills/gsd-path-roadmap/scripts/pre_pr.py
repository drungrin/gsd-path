#!/usr/bin/env python3
# gsd-path project runtime
"""Pre-PR review: an external reviewer reviews the milestone before its PR exists (ADR 0010).

The orchestrator owns the MCP calls (`revisar_branch`, `situacao`, `achados`, `responder`);
this helper owns Git, files, state, and the checks between them. The journal lives in the Git
common dir and is the transaction:

    status       initialize the milestone's sequence; report closed / due / cap-reached / pending
    publish      push each target's head to `pre-pr/...` under an exact-commit authorization
    record       bind the execution the reviewer started to the pushed head
    ended        record the terminal `situacao` and retire the published branch
    retire       retire a published branch (after the review ended, or abandoning an attempt)
    capture      bind the `achados` payload of a concluded execution, once
    rule         validate and store the owner's ruling for every captured finding
    finish       check the `responder` receipts, write the report, and block ship for a patch
    close        close the sequence (no tools, owner ruling after a failure or at the cap)
    adopt-report record surviving reports in a new journal after the journal was lost

See docs/adr/0010-pre-pr-external-review.md.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

sys.dont_write_bytecode = True

if __package__:
    from . import _common
else:
    import _common


def _sibling(name: str):
    import importlib
    return importlib.import_module(f"{__package__}.{name}" if __package__ else name)


SCHEMA = "gsd-path/pre-pr/v1"
MAX_ROUNDS = 3
COORDINATOR = "coordinator"
DECISIONS = ("corrijo", "contesto", "follow-up")
CONFIRM_SEVERITIES = ("bloqueio", "importante")
SEVERITIES = ("bloqueio", "importante", "sugestao", "nit")
RISKS = ("security", "data-loss", "correctness", "operations", "docs", "other")
REPORT_NAME = re.compile(r"^pre-pr-(?P<round>[1-9]\d*)\.md$")
FINDING_HEADING = re.compile(r"^### Finding (?P<target>[a-z][a-z0-9-]*) (?P<id>A[1-9]\d*) — (?P<title>.+)$")
LOCATOR = re.compile(r"^Finding (?P<target>[a-z][a-z0-9-]*) (?P<id>A[1-9]\d*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
CITATION = re.compile(r"(?<![\w./-])(?P<path>[\w.@+-]+(?:/[\w.@+-]+)*):(?P<line>[1-9]\d*)(?!\d)")
EVENT = "pre-PR"
TERMINAL = ("concluida", "falhou", "cancelada")
REVIEWER_LABEL = "Reviewer text"
RULING_LABEL = "Ruling text"


class PrePrError(RuntimeError):
    """Raised when the pre-PR review cannot proceed or prove its records."""


# --- small helpers -----------------------------------------------------------------

def _git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = _common.run_git(repo, *arguments)
    if check and result.returncode != 0:
        raise PrePrError((result.stderr or result.stdout).strip() or f"git {' '.join(arguments)} failed")
    return result.stdout.strip() if result.returncode == 0 else ""


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PrePrError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PrePrError(f"{path} must hold a JSON object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def quoted(value: object, label: str = REVIEWER_LABEL) -> str:
    """One line of external or authored text that no hand-off parser can misread (ADR 0010, 4.8).

    A fixed label keeps the value from being wholly a placeholder; a code span one backtick
    longer than the longest run inside hides `<x>`-shaped text from the placeholder scan.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    if not text.strip():
        return f"{label}: (empty)"
    runs = [len(run) for run in re.findall(r"`+", text)]
    fence = "`" * (max(runs, default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{label}: {fence}{pad}{text}{pad}{fence}"


def _common_dir(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def _state(repo: Path):
    return _sibling("pipeline_state").load_state(repo)[0]


def _milestone_suffix(branch: Optional[str]) -> str:
    match = re.fullmatch(r"feature/(milestone-\d{3,})", branch or "")
    if not match:
        raise PrePrError(f"the pre-PR review needs the canonical bound branch, not {branch!r}")
    return match.group(1)


def journal_dir(repo: Path, state=None) -> Path:
    state = state or _state(repo)
    return _common_dir(repo) / "gsd-path" / "pre-pr" / state.project / _milestone_suffix(state.branch)


def _journal_path(repo: Path, state=None) -> Path:
    return journal_dir(repo, state) / "journal.json"


def load_journal(repo: Path, state=None) -> Optional[dict]:
    path = _journal_path(repo, state)
    if not path.is_file():
        return None
    value = _read_json(path)
    if value.get("schema") != SCHEMA:
        raise PrePrError(f"{path} is not a pre-PR journal")
    return value


def _save(repo: Path, journal: dict, state=None) -> None:
    _write_json(_journal_path(repo, state), journal)


class _Lock:
    """Serialize pre-PR commands across the worktrees of one repository."""

    def __init__(self, repo: Path) -> None:
        directory = journal_dir(repo)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / ".lock"
        self.handle = None

    def __enter__(self) -> "_Lock":
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows has no flock
            return self
        self.handle = open(self.path, "a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise PrePrError("another pre-PR command holds the lock; retry") from error
        return self

    def __exit__(self, *exc: object) -> None:
        if self.handle is not None:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def _log_only(repo: Path, event: str) -> None:
    state = _state(repo)
    _sibling("pipeline_state").transition_state(
        repo, {"phase": state.phase, "status": state.status, "branch": state.branch, "archive": state.archive},
        {"status": state.status}, event)


def _new_journal(state) -> dict:
    return {"schema": SCHEMA, "project": state.project, "branch": state.branch, "status": "open",
            "closed_reason": None, "concluded": 0, "rounds": [], "objectives": {}, "reports": {}}


# --- targets -----------------------------------------------------------------------

def _github(checkout: Path) -> Optional[str]:
    try:
        return _sibling("integration").github_repository(checkout)
    except Exception:  # an origin that is not GitHub.com
        return None


def targets(repo: Path) -> Tuple[List[dict], List[dict]]:
    """The repositories a round reviews, and the reason each other one is skipped."""
    repo = repo.resolve()
    state = _state(repo)
    suffix = _milestone_suffix(state.branch)
    members = _sibling("members")
    lock = repo / members.LOCK_PATH
    found: List[dict] = []
    skipped: List[dict] = []
    if not lock.is_file():
        if state.integration != "pull-request":
            skipped.append({"target": COORDINATOR, "reason": f"integration is {state.integration}, not pull-request"})
        else:
            slug = _github(repo)
            if slug is None:
                skipped.append({"target": COORDINATOR, "reason": "origin is not GitHub.com"})
            else:
                found.append({"target": COORDINATOR, "repo": slug, "checkout": str(repo),
                              "branch": f"pre-pr/{suffix}", "branch_do_pr": state.branch,
                              "base": state.default_branch, "sha": _git(repo, "rev-parse", "HEAD")})
        return found, skipped
    entries = json.loads(lock.read_text(encoding="utf-8")).get("members") or []
    listed = {row["name"]: row for row in members.read_members(repo)}
    for entry in entries:
        name = str(entry["name"])
        row = listed.get(name)
        if row is None:
            skipped.append({"target": name, "reason": "not in MEMBERS.md"})
            continue
        mode = state.integration if row.get("integration") in (None, "", "default") else row.get("integration")
        if mode != "pull-request":
            skipped.append({"target": name, "reason": f"integration is {mode}, not pull-request"})
            continue
        checkout = Path(row["checkout"]).resolve()
        slug = _github(checkout)
        if slug is None:
            skipped.append({"target": name, "reason": "origin is not GitHub.com"})
            continue
        bound = str(entry["branch"])
        tip = _git(checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{bound}^{{commit}}", check=False)
        if not SHA.fullmatch(tip):
            skipped.append({"target": name, "reason": f"bound branch {bound} is missing in the member checkout"})
            continue
        found.append({"target": name, "repo": slug, "checkout": str(checkout),
                      "branch": "pre-pr/" + bound.removeprefix("feature/"), "branch_do_pr": bound,
                      "base": members.member_default_branch(repo, name), "sha": tip})
    return found, skipped


# --- sequence status ---------------------------------------------------------------

def _latest_concluded(journal: Mapping[str, object]) -> Optional[dict]:
    concluded = [row for row in journal.get("rounds") or [] if row.get("status") == "concluded"]
    return concluded[-1] if concluded else None


def _running(journal: Mapping[str, object]) -> Optional[dict]:
    rows = [row for row in journal.get("rounds") or [] if row.get("status") == "running"]
    return rows[-1] if rows else None


def _confirm_needed(round_row: Mapping[str, object]) -> bool:
    return bool(round_row.get("confirm"))


def _close(repo: Path, journal: dict, reason: str) -> dict:
    journal["status"] = "closed"
    journal["closed_reason"] = one_line(reason)
    journal["closed_logged"] = False
    _save(repo, journal)
    return _log_closed(repo, journal)


def _log_closed(repo: Path, journal: dict) -> dict:
    """Write the closure event once; a retry after an interruption writes the missing one."""
    if journal.get("status") == "closed" and journal.get("closed_logged") is False:
        _log_only(repo, f"{EVENT} closed: {journal['closed_reason']}")
        journal["closed_logged"] = True
        _save(repo, journal)
    return journal


def _ruling_text(path: Optional[Path]) -> str:
    """A readable, non-empty owner ruling, read before any side effect."""
    if path is None:
        raise PrePrError("this step needs --owner-ruling <file> with the owner's words")
    text = one_line(path.read_text(encoding="utf-8"))
    if not text:
        raise PrePrError("the owner ruling is empty")
    return text


def _pending_detail(row: Mapping[str, object]) -> List[dict]:
    return [{"target": name, "stage": target.get("stage")} for name, target in sorted((row.get("targets") or {}).items())]


def status(repo: Path, tools: bool = True) -> dict:
    """Initialize the journal and report where the milestone's pre-PR sequence stands.

    `tools=False` (the session lacks the reviewer's MCP tools) closes an idle sequence; it never
    skips findings already captured.
    """
    repo = repo.resolve()
    state = _state(repo)
    if (state.phase, state.status) != ("ship", "active"):
        raise PrePrError(f"the pre-PR review runs in ship/active, not {state.phase}/{state.status}")
    with _Lock(repo):
        journal = load_journal(repo, state)
        if journal is None:
            journal = _new_journal(state)
            _save(repo, journal, state)
        if journal["status"] == "closed":
            _log_closed(repo, journal)
            return {"schema": SCHEMA, "status": "closed", "reason": journal.get("closed_reason")}
        running = _running(journal)
        if running is not None:
            return {"schema": SCHEMA, "status": "pending", "round": running["round"],
                    "targets": _pending_detail(running),
                    "next": "resume the attempt: publish, record, ended, capture, rule, finish"}
        found, skipped = targets(repo)
        if not found:
            reason = "no eligible target: " + "; ".join(f"{row['target']} ({row['reason']})" for row in skipped)
            _close(repo, journal, reason)
            return {"schema": SCHEMA, "status": "closed", "reason": journal["closed_reason"], "skipped": skipped}
        if not tools:
            _close(repo, journal, "the reviewer's MCP tools are not available in this session")
            return {"schema": SCHEMA, "status": "closed", "reason": journal["closed_reason"]}
        latest = _latest_concluded(journal)
        if latest is not None and not _confirm_needed(latest):
            _close(repo, journal, f"round {latest['round']} left no bloqueio or importante fix to confirm")
            return {"schema": SCHEMA, "status": "closed", "reason": journal["closed_reason"]}
        if latest is not None and int(journal["concluded"]) >= MAX_ROUNDS:
            return {"schema": SCHEMA, "status": "cap-reached", "concluded": journal["concluded"],
                    "next": "ask the owner: close and continue to final review (recommended), or one more "
                            "round with publish --owner-ruling <file>"}
        return {"schema": SCHEMA, "status": "due", "round": int(journal["concluded"]) + 1,
                "first": latest is None, "targets": [_public(row) for row in found], "skipped": skipped}


def _public(target: Mapping[str, object]) -> dict:
    return {key: target[key] for key in ("target", "repo", "branch", "branch_do_pr", "base", "sha")}


# --- objective ---------------------------------------------------------------------

def _objective_text(objective: Mapping[str, str]) -> str:
    return f"{objective['title']}\n\n{objective['problem']}\n\n{objective['scope']}"


def _validate_objectives(repo: Path, value: Mapping[str, object], names: Sequence[str]) -> Dict[str, dict]:
    editorial = _sibling("pr_editorial")
    given = value.get("targets") if isinstance(value, Mapping) else None
    if not isinstance(given, Mapping) or sorted(given) != sorted(names):
        raise PrePrError("the objective file needs {\"targets\": {<target>: {title, problem, scope}}} for: "
                         + ", ".join(sorted(names)))
    language = editorial.language(repo)
    result: Dict[str, dict] = {}
    for name in names:
        row = given[name]
        if not isinstance(row, Mapping):
            raise PrePrError(f"objective for {name} must be an object")
        try:
            title = editorial.text(row.get("title"), f"{name} title")
            if not editorial.TITLE.fullmatch(title):
                raise editorial.EditorialError(f"{name} title must be a Conventional Commit subject")
            sections = {key: editorial.text(row.get(key), f"{name} {key}") for key in ("problem", "scope")}
            editorial.require_no_pipeline_identifiers(repo, title, sections.values())
        except editorial.EditorialError as error:
            raise PrePrError(str(error)) from error
        result[name] = {"title": title, **sections, "language": language}
    return result


# --- publication -------------------------------------------------------------------

def _live(checkout: Path, ref: str) -> Optional[str]:
    return _sibling("integration").live_remote_ref(checkout, ref)


def _project_of(repo: Path, target: Mapping[str, object]) -> str:
    return _state(repo).project


def _authorize(repo: Path, target: Mapping[str, object], sha: str) -> None:
    _sibling("members").authorize_push(Path(str(target["checkout"])), _project_of(repo, target),
                                       f"refs/heads/{target['branch']}", sha)


def _clear_authorization(repo: Path, target: Mapping[str, object]) -> None:
    members = _sibling("members")
    checkout = Path(str(target["checkout"]))
    ref = f"refs/heads/{target['branch']}"
    project = _project_of(repo, target)
    if _common.run_git(checkout, "rev-parse", "--verify", "--quiet",
                       members._authorization_ref(project, "push", ref)).returncode == 0:
        members.clear_authorization(checkout, project, "push", ref)


def _push(checkout: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess:
    return _common.run_git(checkout, "push", *arguments)


def _publish_target(repo: Path, journal: dict, row: dict, target: dict) -> None:
    checkout = Path(str(target["checkout"]))
    ref = f"refs/heads/{target['branch']}"
    owned = target.get("remote")  # the SHA this journal last confirmed on the remote ref
    live = _live(checkout, ref)
    if target.get("stage") == "published" and live == target["sha"]:
        return
    if live is not None and live not in (owned, target["sha"]):
        raise PrePrError(f"origin {target['branch']} holds {live}, which this pre-PR did not publish; "
                         "it is never touched (retire --adopt <sha> --owner-ruling <file> after checking it)")
    target["stage"] = "publishing"
    _save(repo, journal)
    if live != target["sha"]:
        _authorize(repo, target, target["sha"])
        pushed = _push(checkout, [f"--force-with-lease={ref}:{live or ''}", "origin", f"{target['sha']}:{ref}"])
        if pushed.returncode != 0 and _live(checkout, ref) != target["sha"]:
            raise PrePrError(f"publish {target['branch']} failed: {(pushed.stderr or pushed.stdout).strip()}")
    if _live(checkout, ref) != target["sha"]:
        raise PrePrError(f"origin {target['branch']} does not hold {target['sha']} after the push")
    target.update(stage="published", remote=target["sha"])
    _save(repo, journal)


def publish(repo: Path, objective_path: Optional[Path] = None, owner_ruling: Optional[Path] = None) -> dict:
    """Start (or resume) a round and publish every target that still needs a review."""
    repo = repo.resolve()
    state = _state(repo)
    if (state.phase, state.status) != ("ship", "active"):
        raise PrePrError(f"publish runs in ship/active, not {state.phase}/{state.status}")
    with _Lock(repo):
        journal = load_journal(repo, state)
        if journal is None or journal["status"] == "closed":
            raise PrePrError("no open pre-PR sequence; run status first")
        row = _running(journal)
        if row is None:
            latest = _latest_concluded(journal)
            if latest is not None and not _confirm_needed(latest):
                raise PrePrError("no round is due; run status")
            ruling = None
            if int(journal["concluded"]) >= MAX_ROUNDS:
                if owner_ruling is None:
                    raise PrePrError(f"{MAX_ROUNDS} rounds concluded; another round needs --owner-ruling <file>")
                ruling = _ruling_text(owner_ruling)
            found, _ = targets(repo)
            if not found:
                raise PrePrError("no eligible target; run status")
            if not journal.get("objectives"):
                if objective_path is None:
                    raise PrePrError("the first round needs --objective <file> with each target's title, "
                                     "problem, and scope")
                journal["objectives"] = _validate_objectives(repo, _read_json(objective_path),
                                                             [row["target"] for row in found])
            elif objective_path is not None:
                raise PrePrError("the objective is fixed after the first round")
            missing = sorted({row["target"] for row in found} - set(journal["objectives"]))
            if missing:
                raise PrePrError("no stored objective for: " + ", ".join(missing))
            number = int(journal["concluded"]) + 1
            previous = {name: value.get("remote") for done in journal["rounds"]
                        for name, value in (done.get("targets") or {}).items() if value.get("remote")}
            row = {"round": number, "status": "running", "head": _git(repo, "rev-parse", "HEAD"),
                   "owner_ruling": ruling, "targets": {
                       found_row["target"]: dict(found_row, stage="new", remote=previous.get(found_row["target"]))
                       for found_row in found}}
            journal["rounds"].append(row)
            _save(repo, journal)
            if ruling:
                _log_only(repo, f"{EVENT} round {number} past the cap; owner ruling: {ruling}")
        elif objective_path is not None or owner_ruling is not None:
            raise PrePrError(f"round {row['round']} is already running; resume it without new inputs")
        for name, target in sorted(row["targets"].items()):
            if target.get("stage") in ("new", "publishing", "published", "failed"):
                if target.get("stage") == "failed":
                    target.update(stage="new", execution=None, outcome=None)
                _publish_target(repo, journal, row, target)
        return {"schema": SCHEMA, "status": "published", "round": row["round"], "requests": [
            dict(_public(target), objetivo=_objective_text(journal["objectives"][name]))
            for name, target in sorted(row["targets"].items()) if target.get("stage") == "published"]}


def _target(journal: dict, name: str) -> Tuple[dict, dict]:
    row = _running(journal)
    if row is None:
        raise PrePrError("no pre-PR round is running")
    target = (row.get("targets") or {}).get(name)
    if target is None:
        raise PrePrError(f"round {row['round']} has no target {name}")
    return row, target


def _open_journal(repo: Path) -> dict:
    journal = load_journal(repo)
    if journal is None:
        raise PrePrError("no pre-PR journal for this milestone; run status")
    return journal


def record(repo: Path, name: str, execution: object, head: str) -> dict:
    repo = repo.resolve()
    with _Lock(repo):
        journal = _open_journal(repo)
        row, target = _target(journal, name)
        if target.get("stage") == "requested" and str(target.get("execution")) == str(execution):
            return {"schema": SCHEMA, "status": "requested", "target": name, "execution": target["execution"]}
        # A new request on the same branch cancels the unfinished one, so a lost execution id is
        # recovered by requesting again and recording the new id.
        if target.get("stage") not in ("published", "requested"):
            raise PrePrError(f"{name} is {target.get('stage')}; record follows publish")
        if head != target["sha"]:
            raise PrePrError(f"the reviewer started on {head}, not the pushed {target['sha']}; another push "
                             "raced the request")
        if not str(execution).strip():
            raise PrePrError("the execution id is empty")
        target.update(stage="requested", execution=str(execution))
        _save(repo, journal)
        return {"schema": SCHEMA, "status": "requested", "target": name, "execution": target["execution"]}


def _retire(repo: Path, target: dict) -> None:
    checkout = Path(str(target["checkout"]))
    ref = f"refs/heads/{target['branch']}"
    # What this attempt may own on the remote: the confirmed SHA, and the SHA an interrupted
    # publish may already have pushed before it could record the confirmation.
    owned = {sha for sha in (target.get("remote"),
                             target.get("sha") if target.get("stage") in ("publishing", "failed") else None) if sha}
    if owned:
        live = _live(checkout, ref)
        if live is not None:
            if live not in owned:
                raise PrePrError(f"origin {target['branch']} moved to {live}; it is never deleted")
            deleted = _push(checkout, [f"--force-with-lease={ref}:{live}", "origin", f":{ref}"])
            if deleted.returncode != 0 and _live(checkout, ref) is not None:
                raise PrePrError(f"retire {target['branch']} failed: {(deleted.stderr or deleted.stdout).strip()}")
    _clear_authorization(repo, target)
    target["remote"] = None


def ended(repo: Path, name: str, situation: Mapping[str, object]) -> dict:
    repo = repo.resolve()
    with _Lock(repo):
        journal = _open_journal(repo)
        row, target = _target(journal, name)
        if target.get("stage") not in ("requested", "ended", "failed"):
            raise PrePrError(f"{name} is {target.get('stage')}; ended follows record")
        if str(situation.get("execucao_id")) != str(target.get("execution")):
            raise PrePrError(f"situacao is for execution {situation.get('execucao_id')}, not {target.get('execution')}")
        outcome = situation.get("situacao")
        if situation.get("terminou") is not True or outcome not in TERMINAL:
            raise PrePrError(f"execution {target['execution']} has not ended ({outcome}); keep waiting")
        target["outcome"] = {key: situation.get(key) for key in
                             ("situacao", "veredito", "achados_incluidos", "erro", "parou_por_cota")}
        target["stage"] = "ended" if outcome == "concluida" else "failed"
        _save(repo, journal)
        _retire(repo, target)
        _save(repo, journal)
        if outcome != "concluida" and target.get("logged") != target["execution"]:
            _log_only(repo, f"{EVENT} round {row['round']}: {name} execution {target['execution']} ended "
                            f"{outcome}: {one_line(situation.get('erro') or 'no error text')}")
            target["logged"] = target["execution"]
            _save(repo, journal)
        return {"schema": SCHEMA, "status": target["stage"], "target": name, "outcome": outcome,
                "next": ("capture its achados" if outcome == "concluida" else
                         "ask the owner: close and continue (recommended), or retry with publish")}


def retire(repo: Path, name: Optional[str], abandon: bool = False, adopt: Optional[str] = None,
           owner_ruling: Optional[Path] = None) -> dict:
    """Retire a published branch; with --adopt, a live ref no journal records (owner ruling)."""
    repo = repo.resolve()
    if adopt is not None:
        if not SHA.fullmatch(adopt):
            raise PrePrError("retire --adopt needs the full SHA of the leftover ref")
        ruling = _ruling_text(owner_ruling)
        with _Lock(repo):
            found, _ = targets(repo)
            chosen = [row for row in found if name in (None, row["target"])]
            if len(chosen) != 1:
                raise PrePrError("retire --adopt names exactly one target")
            journal = load_journal(repo)
            running = _running(journal) if journal is not None else None
            if running is not None and chosen[0]["target"] in running["targets"]:
                raise PrePrError(f"round {running['round']} records {chosen[0]['target']}; retire it through the "
                                 "journal (retire --target), never by adoption")
            target = dict(chosen[0], remote=adopt, stage="adopted")
            _retire(repo, target)
            _log_only(repo, f"{EVENT}: retired a leftover {target['branch']} at {adopt}; owner ruling: {ruling}")
        return {"schema": SCHEMA, "status": "retired", "target": target["target"], "branch": target["branch"]}
    with _Lock(repo):
        journal = _open_journal(repo)
        row, target = _target(journal, str(name))
        if target.get("stage") in ("requested",):
            raise PrePrError(f"execution {target.get('execution')} has not ended; the job still fetches the branch")
        if target.get("stage") in ("new", "publishing", "published"):
            if not abandon:
                raise PrePrError(f"{name} is {target.get('stage')}; pass --abandon to drop the attempt")
            target["stage"] = "failed"
            target["outcome"] = {"situacao": "abandonada"}
        _retire(repo, target)
        _save(repo, journal)
        return {"schema": SCHEMA, "status": "retired", "target": name, "stage": target["stage"]}


# --- findings ------------------------------------------------------------------------

def _included(payload: Mapping[str, object]) -> List[dict]:
    findings = payload.get("achados")
    if not isinstance(findings, list):
        raise PrePrError("the achados payload has no achados list")
    seen = set()
    for item in findings:
        if not isinstance(item, Mapping) or not re.fullmatch(r"A[1-9]\d*", str(item.get("id") or "")):
            raise PrePrError("every finding needs an A<n> id")
        if item["id"] in seen:
            raise PrePrError(f"finding {item['id']} appears twice")
        seen.add(item["id"])
    return list(findings)


def capture(repo: Path, name: str, payload: Mapping[str, object]) -> dict:
    repo = repo.resolve()
    with _Lock(repo):
        journal = _open_journal(repo)
        row, target = _target(journal, name)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = _digest_bytes(raw.encode("utf-8"))
        if target.get("stage") in ("captured",):
            if target.get("findings_digest") != digest:
                raise PrePrError(f"{name} already captured a different payload for execution {target['execution']}")
            return {"schema": SCHEMA, "status": "captured", "target": name, "findings": len(target["findings"]["achados"])}
        if target.get("stage") != "ended":
            raise PrePrError(f"{name} is {target.get('stage')}; capture follows a concluded execution")
        if str(payload.get("execucao_id")) != str(target["execution"]):
            raise PrePrError(f"the payload is for execution {payload.get('execucao_id')}, not {target['execution']}")
        for key, expected in (("repo", target["repo"]), ("branch", target["branch"]), ("head_sha", target["sha"])):
            given = payload.get(key)
            if given is not None and str(given).lower() != str(expected).lower():
                raise PrePrError(f"the payload {key} is {given}, not {expected}")
        findings = _included(payload)
        expected_count = (target.get("outcome") or {}).get("achados_incluidos")
        if isinstance(expected_count, int) and expected_count != len(findings):
            raise PrePrError(f"situacao reported {expected_count} included findings; the payload has {len(findings)}")
        target.update(stage="captured", findings=json.loads(raw), findings_digest=digest)
        _save(repo, journal)
        return {"schema": SCHEMA, "status": "captured", "target": name, "findings": len(findings)}


def _captured(row: Mapping[str, object]) -> Dict[Tuple[str, str], dict]:
    result: Dict[Tuple[str, str], dict] = {}
    for name, target in sorted((row.get("targets") or {}).items()):
        if target.get("stage") not in ("captured",):
            raise PrePrError(f"{name} is {target.get('stage')}; every target of the round must be captured "
                             "(retry a failed one with publish, or ask the owner to close)")
        for item in target["findings"]["achados"]:
            result[(name, str(item["id"]))] = item
    return result


def _citation_error(target: Mapping[str, object], reason: str) -> Optional[str]:
    checkout = Path(str(target["checkout"]))
    for match in CITATION.finditer(reason):
        shown = _common.run_git(checkout, "show", f"{target['sha']}:{match.group('path')}")
        if shown.returncode != 0:
            continue
        if int(match.group("line")) <= len(shown.stdout.splitlines()):
            return None
    return "a contestation cites `path:line` evidence that exists at the reviewed commit"


def _validate_ruling(row: Mapping[str, object], ruling: Mapping[str, object]) -> List[dict]:
    captured = _captured(row)
    verbatim = ruling.get("verbatim")
    if captured and (not isinstance(verbatim, str) or not verbatim.strip()):
        raise PrePrError("the ruling needs the owner's answer verbatim")
    rows = ruling.get("findings")
    if not isinstance(rows, list):
        raise PrePrError("the ruling needs a findings list")
    decided: Dict[Tuple[str, str], dict] = {}
    for entry in rows:
        if not isinstance(entry, Mapping):
            raise PrePrError("each ruling row must be an object")
        key = (str(entry.get("target")), str(entry.get("id")))
        if key not in captured:
            raise PrePrError(f"the ruling names {key[0]} {key[1]}, which was not captured")
        if key in decided:
            raise PrePrError(f"{key[0]} {key[1]} has two decisions")
        decision = entry.get("decision")
        if decision not in DECISIONS:
            raise PrePrError(f"{key[0]} {key[1]} decision must be one of {', '.join(DECISIONS)}")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PrePrError(f"{key[0]} {key[1]} needs a reason")
        if decision == "contesto":
            error = _citation_error(row["targets"][key[0]], reason)
            if error:
                raise PrePrError(f"{key[0]} {key[1]}: {error}")
        if decision == "corrijo":
            direction = entry.get("fix_direction")
            if not isinstance(direction, str) or not direction.strip():
                raise PrePrError(f"{key[0]} {key[1]} is corrijo without a fix direction")
        if decision == "follow-up" and entry.get("risk") not in RISKS:
            raise PrePrError(f"{key[0]} {key[1]} risk must be one of {', '.join(RISKS)}")
        decided[key] = dict(entry)
    missing = sorted(set(captured) - set(decided))
    if missing:
        raise PrePrError("every captured finding needs one decision; missing: "
                         + ", ".join(f"{target} {identifier}" for target, identifier in missing))
    return [decided[key] for key in sorted(decided, key=lambda key: (key[0], int(key[1][1:])))]


def rule(repo: Path, ruling: Mapping[str, object]) -> dict:
    repo = repo.resolve()
    with _Lock(repo):
        journal = _open_journal(repo)
        row = _running(journal)
        if row is None:
            raise PrePrError("no pre-PR round is running")
        decisions = _validate_ruling(row, ruling)
        rendered = report_text(journal, row, decisions, str(ruling["verbatim"]))
        _preflight(rendered, row, decisions)
        if row.get("stage") == "written":
            raise PrePrError(f"round {row['round']} already wrote its report; run finish")
        row["ruling"] = {"verbatim": ruling["verbatim"], "findings": decisions}
        row["stage"] = "ruled"
        _save(repo, journal)
        counts = {decision: sum(1 for entry in decisions if entry["decision"] == decision) for decision in DECISIONS}
        return {"schema": SCHEMA, "status": "ruled", "round": row["round"], "decisions": counts,
                "next": "send every decision through responder, then finish --responses <file>"}


# --- report and hand-off -------------------------------------------------------------

def _location(item: Mapping[str, object]) -> str:
    path = item.get("arquivo")
    if not path:
        return "none"
    return quoted(f"{path}:{item['linha']}" if item.get("linha") else path)


def _record_payload(journal: Mapping[str, object], row: Mapping[str, object],
                    decisions: Sequence[Mapping[str, object]]) -> dict:
    return {"schema": SCHEMA, "round": row["round"], "head": row["head"], "owner_ruling": row.get("owner_ruling"),
            "targets": {name: {"repo": target["repo"], "branch": target["branch"],
                               "branch_do_pr": target["branch_do_pr"], "base": target["base"],
                               "head": target["sha"], "execution": target.get("execution"),
                               "verdict": (target.get("outcome") or {}).get("veredito"),
                               "objective": journal["objectives"][name],
                               "achados": target["findings"]["achados"]}
                        for name, target in sorted((row.get("targets") or {}).items())},
            "decisions": list(decisions)}


def _fenced(text: str, info: str) -> str:
    runs = [len(run) for run in re.findall(r"`{3,}", text)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return f"{fence}{info}\n{text}\n{fence}"


def report_text(journal: Mapping[str, object], row: Mapping[str, object],
                decisions: Sequence[Mapping[str, object]], verbatim: str) -> str:
    by_key = {(entry["target"], entry["id"]): entry for entry in decisions}
    counts = {decision: sum(1 for entry in decisions if entry["decision"] == decision) for decision in DECISIONS}
    members = [(name, target["sha"]) for name, target in sorted(row["targets"].items()) if name != COORDINATOR]
    lines = [f"# Pre-PR Review — Round {row['round']}", "",
             f"Reviewed HEAD: {row['head']}",
             *[f"Member reviewed HEAD: {name} {sha}" for name, sha in members],
             f"Outcome: {counts['corrijo']} to fix, {counts['contesto']} contested, {counts['follow-up']} follow-up",
             "", "## Ruling", "", quoted(verbatim, "Owner answer") if verbatim else "No finding; no question.", ""]
    for name, target in sorted(row["targets"].items()):
        objective = journal["objectives"][name]
        lines += [f"## Target {name}", "",
                  f"- **Repository**: {target['repo']}",
                  f"- **Branch**: {target['branch']} (registry {target['branch_do_pr']}, base {target['base']})",
                  f"- **Reviewed commit**: {target['sha']}",
                  f"- **Execution**: {target.get('execution')}",
                  f"- **Suggested verdict**: {(target.get('outcome') or {}).get('veredito') or 'none'}",
                  f"- **Objective**: {quoted(_objective_text(objective), 'Objective text')}", ""]
        for item in target["findings"]["achados"]:
            entry = by_key[(name, str(item["id"]))]
            severity = item.get("severidade") if item.get("severidade") in SEVERITIES else "unknown"
            direction = entry.get("fix_direction") if entry["decision"] == "corrijo" else None
            lines += [f"### Finding {name} {item['id']} — {one_line(item.get('titulo')) or 'untitled'}", "",
                      f"- **Severity**: {severity}",
                      f"- **Location**: {_location(item)}",
                      f"- **Found**: {quoted(item.get('descricao'))}",
                      f"- **Scenario**: {quoted(item.get('cenario'))}",
                      f"- **Suggestion**: {quoted(item.get('sugestao'))}",
                      f"- **Decision**: {entry['decision']}",
                      f"- **Reason**: {quoted(entry['reason'], RULING_LABEL)}",
                      f"- **Fix direction**: {quoted(direction, RULING_LABEL) if direction else 'none'}", ""]
    lines += ["## Record", "", "<!-- Machine-readable; parking and the editorial step read it. -->", "",
              _fenced(json.dumps(_record_payload(journal, row, decisions), ensure_ascii=False, indent=2,
                                 sort_keys=True), "json"), ""]
    return "\n".join(lines)


def findings_text(row: Mapping[str, object], decisions: Sequence[Mapping[str, object]], source: str) -> str:
    items = {(name, str(item["id"])): item for name, target in row["targets"].items()
             for item in target["findings"]["achados"]}
    lines = ["# Patch Findings", "", f"Reviewed HEAD: {row['head']}", "State: ship/blocked", "", "## Findings", ""]
    number = 0
    for entry in decisions:
        if entry["decision"] != "corrijo":
            continue
        number += 1
        item = items[(entry["target"], entry["id"])]
        lines += [f"### P{number:03d} — {entry['target']} {entry['id']}: {one_line(item.get('titulo')) or 'untitled'}",
                  "", f"- **Source**: `{source}`",
                  f"- **Locator**: Finding {entry['target']} {entry['id']}",
                  f"- **Evidence**: {quoted(item.get('descricao'))}",
                  f"- **Fix direction**: {quoted(entry['fix_direction'], RULING_LABEL)}", ""]
    return "\n".join(lines)


def _preflight(report: str, row: Mapping[str, object], decisions: Sequence[Mapping[str, object]]) -> None:
    """Prove that the hand-off the ruling produces passes the patch gate's field rules."""
    contracts = _sibling("check_handoffs")
    source = f".project/review/pre-pr-{row['round']}.md"
    try:
        for entry in decisions:
            if entry["decision"] != "corrijo":
                continue
            locator = f"Finding {entry['target']} {entry['id']}"
            evidence, direction = contracts._source_repair_fields(source, report, locator)
            handoff = findings_text(row, [entry], source)
            block = contracts._finding_blocks(handoff)[0][1]
            if contracts._field(block, "Evidence") != evidence or contracts._field(block, "Fix direction") != direction:
                raise PrePrError(f"{locator} would not copy verbatim into PATCH-FINDINGS.md")
    except contracts.HandoffError as error:
        raise PrePrError(f"the hand-off this ruling produces fails the patch gate: {error}") from error


def _responded(responses: Mapping[str, object], row: Mapping[str, object]) -> None:
    for name, target in sorted(row["targets"].items()):
        if not target["findings"]["achados"]:
            continue
        receipts = responses.get(name)
        receipts = receipts if isinstance(receipts, list) else [receipts]
        answered = set()
        for receipt in receipts:
            if not isinstance(receipt, Mapping) or str(receipt.get("execucao_id")) != str(target["execution"]):
                raise PrePrError(f"{name} needs the responder receipt of execution {target['execution']}")
            for entry in receipt.get("respondidos") or []:
                answered.add(str(entry.get("achado")))
        missing = sorted({str(item["id"]) for item in target["findings"]["achados"]} - answered,
                         key=lambda value: int(value[1:]))
        if missing:
            raise PrePrError(f"{name}: responder did not answer {', '.join(missing)}")


def _stale_final(repo: Path, head: str) -> List[Path]:
    """Uncommitted FINAL.md / final-gap-N.md that prepare-final wrote at the round's HEAD."""
    review = repo / ".project/review"
    stale = []
    for path in [review / "FINAL.md", *sorted(review.glob("final-gap-*.md"))]:
        if not path.is_file():
            continue
        relative = path.relative_to(repo).as_posix()
        if _common.run_git(repo, "cat-file", "-e", f"HEAD:{relative}").returncode == 0:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(rf"(?m)^Reviewed HEAD:[ \t]*{re.escape(head)}[ \t]*$", text):
            stale.append(path)
    return stale


def finish(repo: Path, responses: Mapping[str, object]) -> dict:
    repo = repo.resolve()
    with _Lock(repo):
        journal = _open_journal(repo)
        row = _running(journal)
        if row is None:
            raise PrePrError("no pre-PR round is running")
        if row.get("stage") not in ("ruled", "written"):
            if _captured(row):
                raise PrePrError("finish follows rule")
            # A round without included findings asks the owner nothing (ADR 0010, 4.6).
            row["ruling"] = {"verbatim": "", "findings": []}
            row["stage"] = "ruled"
            _save(repo, journal)
        decisions = row["ruling"]["findings"]
        _responded(responses, row)
        name = f"pre-pr-{row['round']}.md"
        source = f".project/review/{name}"
        text = report_text(journal, row, decisions, row["ruling"]["verbatim"])
        review = repo / ".project/review"
        review.mkdir(parents=True, exist_ok=True)
        fixes = [entry for entry in decisions if entry["decision"] == "corrijo"]
        if row.get("stage") == "ruled":
            row["responses"] = json.loads(json.dumps(responses))
            _common.atomic_write(review / name, text)
            journal["reports"][name] = _digest_bytes((review / name).read_bytes())
            if fixes:
                for path in _stale_final(repo, row["head"]):
                    path.unlink()
                _common.atomic_write(review / "PATCH-FINDINGS.md", findings_text(row, decisions, source))
            row["stage"] = "written"
            _save(repo, journal)
        counts = {decision: sum(1 for entry in decisions if entry["decision"] == decision) for decision in DECISIONS}
        event = (f"{EVENT} round {row['round']}: {counts['corrijo']} to fix, {counts['contesto']} contested, "
                 f"{counts['follow-up']} follow-up; ruling: {one_line(row['ruling']['verbatim'])}")
        items = {(target_name, str(item["id"])): item for target_name, target in row["targets"].items()
                 for item in target["findings"]["achados"]}
        # An unknown severity fails closed: its fix gets a confirmation round.
        row["confirm"] = any(items[(entry["target"], entry["id"])].get("severidade") not in ("sugestao", "nit")
                             for entry in fixes)
        state = _state(repo)
        if fixes:
            if (state.phase, state.status) == ("ship", "active"):
                _sibling("pipeline_state").transition_state(
                    repo, {"phase": "ship", "status": "active", "branch": state.branch, "archive": state.archive},
                    {"status": "blocked"}, event)
        elif not row.get("event_logged"):
            _log_only(repo, event)
            row["event_logged"] = True
            _save(repo, journal)
        row["status"] = "concluded"
        row["stage"] = "finished"
        journal["concluded"] = int(journal["concluded"]) + 1
        _save(repo, journal)
        if not fixes:
            _close(repo, journal, f"round {row['round']} found nothing to fix")
        return {"schema": SCHEMA, "status": "blocked" if fixes else "closed", "round": row["round"],
                "report": source, "patch_findings": ".project/review/PATCH-FINDINGS.md" if fixes else None,
                "decisions": counts}


def close(repo: Path, reason: str, owner_ruling: Optional[Path] = None) -> dict:
    """Close the sequence on the owner's ruling (after a failure, at the cap, or dropping findings).

    `status --no-tools` and a sequence without targets close on their own; every explicit close is
    the owner's decision.
    """
    repo = repo.resolve()
    ruling = _ruling_text(owner_ruling)
    with _Lock(repo):
        state = _state(repo)
        journal = load_journal(repo, state) or _new_journal(state)
        if journal["status"] == "closed":
            _log_closed(repo, journal)
            return {"schema": SCHEMA, "status": "closed", "reason": journal.get("closed_reason")}
        row = _running(journal)
        if row is not None and row.get("stage") == "written":
            raise PrePrError(f"round {row['round']} already wrote its report; run finish to conclude it")
        if row is not None:
            published = [name for name, target in row["targets"].items()
                         if target.get("remote") or target.get("stage") in ("publishing", "published", "requested")]
            if published:
                raise PrePrError("retire the published branches first: " + ", ".join(sorted(published)))
            row["status"] = "abandoned"
        _close(repo, journal, f"{one_line(reason)}; owner ruling: {ruling}")
        return {"schema": SCHEMA, "status": "closed", "reason": journal["closed_reason"]}


# --- gates and readers for other helpers ---------------------------------------------

def _report_locations(repo: Path, name: str, state) -> List[Path]:
    places = [repo / ".project/review" / name]
    if state.archive:
        places.append(repo / state.archive / "review" / name)
    return places


def require_closed(repo: Path) -> None:
    """Refuse progression past the pre-PR step while the milestone's sequence is not closed."""
    repo = repo.resolve()
    try:
        state = _state(repo)
        journal = load_journal(repo, state)
    except PrePrError:
        return  # no canonical bound branch: not a milestone the pre-PR runs for
    if journal is None:
        return
    if journal.get("status") != "closed":
        running = _running(journal)
        detail = f"round {running['round']} is in progress" if running else "the sequence is open"
        raise PrePrError(f"the pre-PR review is not closed ({detail}); run pre_pr.py status")
    for name, digest in sorted((journal.get("reports") or {}).items()):
        present = [path for path in _report_locations(repo, name, state) if path.is_file()]
        if not present:
            raise PrePrError(f"the pre-PR report {name} is missing")
        if all(_digest_bytes(path.read_bytes()) != digest for path in present):
            raise PrePrError(f"the pre-PR report {name} changed after it was written")


def admitted_report(repo: Path, name: str, data: bytes) -> bool:
    """Whether an archived review/pre-pr-<n>.md is the exact report this journal recorded."""
    if not REPORT_NAME.fullmatch(name):
        return False
    try:
        journal = load_journal(repo.resolve())
    except PrePrError:
        return False
    return journal is not None and (journal.get("reports") or {}).get(name) == _digest_bytes(data)


def patch_source_error(repo: Path, source: str, text: str) -> Optional[str]:
    """Why a pre-PR report cannot be a patch source now, or None."""
    name = PurePosixPath(source).name
    match = REPORT_NAME.fullmatch(name)
    if not match:
        return f"{source} is not a pre-PR report"
    try:
        journal = load_journal(repo.resolve())
    except PrePrError as error:
        return str(error)
    if journal is None:
        return (f"{source} has no pre-PR journal to prove that finish wrote it; after a lost journal run "
                "pre_pr.py adopt-report --owner-ruling <file>")
    rows = [row for row in journal.get("rounds") or [] if int(row["round"]) == int(match.group("round"))]
    if not rows or rows[-1].get("stage") != "finished":
        return f"{source} belongs to a pre-PR round that did not finish; run pre_pr.py finish"
    if journal.get("reports", {}).get(name) != _digest_bytes(text.encode("utf-8")):
        return f"{source} differs from the report pre_pr.py finish wrote"
    head = re.search(r"(?m)^Reviewed HEAD:[ \t]*([0-9a-f]{40})[ \t]*$", text)
    if head and _stale_final(repo.resolve(), head.group(1)):
        return ("an uncommitted FINAL.md or final-gap report from the pre-PR round's HEAD is still present; "
                "pre_pr.py finish removes it")
    return None


def corrijo_locators(text: str) -> List[str]:
    """Every `Finding <target> A<n>` the report records as corrijo, in report order."""
    value = record_of(text) or {}
    return [f"Finding {entry['target']} {entry['id']}" for entry in value.get("decisions") or []
            if entry.get("decision") == "corrijo"]


def record_of(text: str) -> Optional[dict]:
    """The machine-readable record of a report."""
    section = re.search(r"(?ms)^## Record\s*$(.*)", text)
    if not section:
        return None
    fence = re.search(r"(?ms)^(`{3,})json\n(.*?)\n\1\s*$", section.group(1))
    if not fence:
        return None
    try:
        value = json.loads(fence.group(2))
    except ValueError:
        return None
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else None


def _archived_reports(archive: Path) -> List[Tuple[int, Path]]:
    review = archive / "review"
    found = []
    for path in review.glob("pre-pr-*.md") if review.is_dir() else []:
        match = REPORT_NAME.fullmatch(path.name)
        if match and path.is_file() and not path.is_symlink():
            found.append((int(match.group("round")), path))
    return sorted(found)


def objective_for(repo: Path, archive: str, target: str) -> Optional[dict]:
    """The latest pre-PR objective of exactly this target, from the archived reports."""
    for _, path in reversed(_archived_reports(repo / archive)):
        value = record_of(path.read_text(encoding="utf-8", errors="replace"))
        if value and target in (value.get("targets") or {}):
            objective = value["targets"][target].get("objective")
            if isinstance(objective, dict):
                return objective
    return None


def follow_ups(repo: Path) -> List[dict]:
    """Every follow-up decision in the archived pre-PR reports, for the parking lot."""
    found: List[dict] = []
    root = repo / ".project/archive"
    for archive in sorted(root.iterdir()) if root.is_dir() else []:
        for _, path in _archived_reports(archive):
            value = record_of(path.read_text(encoding="utf-8", errors="replace"))
            if not value:
                continue
            targets_value = value.get("targets") or {}
            for entry in value.get("decisions") or []:
                if entry.get("decision") != "follow-up":
                    continue
                target = targets_value.get(entry.get("target")) or {}
                item = next((row for row in target.get("achados") or [] if row.get("id") == entry.get("id")), {})
                location = (f"{item['arquivo']}:{item['linha']}" if item.get("arquivo") and item.get("linha")
                            else item.get("arquivo") or "none")
                found.append({
                    "title": one_line(item.get("titulo") or entry.get("id"))[:120],
                    "class": entry.get("risk") if entry.get("risk") in RISKS else "other",
                    "source": f"{target.get('repo')}@{target.get('branch_do_pr')}#{target.get('execution')}/{entry.get('id')}",
                    "repo": entry.get("target") or COORDINATOR,
                    "location": location,
                    "found": one_line(item.get("descricao") or item.get("titulo") or entry.get("id")),
                    "ruling": one_line(entry.get("reason") or ""),
                })
    return found


def adopt_report(repo: Path, owner_ruling: Path) -> dict:
    """After the journal was lost: record the surviving reports so undo can admit them."""
    repo = repo.resolve()
    with _Lock(repo):
        state = _state(repo)
        if load_journal(repo, state) is not None:
            raise PrePrError("a pre-PR journal already exists; adopt-report recovers a lost one")
        ruling = one_line(owner_ruling.read_text(encoding="utf-8"))
        if not ruling:
            raise PrePrError("the owner ruling is empty")
        journal = _new_journal(state)
        names = set()
        for place in [repo / ".project/review", *( [repo / state.archive / "review"] if state.archive else [])]:
            for path in place.glob("pre-pr-*.md") if place.is_dir() else []:
                if REPORT_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                    journal["reports"][path.name] = _digest_bytes(path.read_bytes())
                    names.add(path.name)
        journal["rounds"] = [{"round": int(REPORT_NAME.fullmatch(name).group("round")), "status": "concluded",
                              "stage": "finished", "recovered": True, "targets": {}}
                             for name in sorted(names, key=lambda name: int(REPORT_NAME.fullmatch(name).group("round")))]
        journal["concluded"] = len(names)
        journal["status"] = "closed"
        journal["closed_reason"] = f"journal recovered from surviving reports; owner ruling: {ruling}"
        _save(repo, journal, state)
        _log_only(repo, f"{EVENT} journal recovered ({len(names)} report(s)); owner ruling: {ruling}")
        return {"schema": SCHEMA, "status": "closed", "reports": sorted(names)}


# --- CLI -------------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("status", "targets", "publish", "record", "ended", "retire", "capture",
                                           "rule", "finish", "close", "adopt-report", "require-closed"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target")
    parser.add_argument("--execution")
    parser.add_argument("--head")
    parser.add_argument("--objective", type=Path)
    parser.add_argument("--status-file", type=Path, help="ended: the situacao result as JSON")
    parser.add_argument("--findings", type=Path, help="capture: the achados result as JSON")
    parser.add_argument("--ruling", type=Path)
    parser.add_argument("--responses", type=Path, help="finish: {<target>: <responder result>}")
    parser.add_argument("--reason")
    parser.add_argument("--owner-ruling", type=Path)
    parser.add_argument("--no-tools", action="store_true", help="status: the reviewer's MCP tools are missing")
    parser.add_argument("--abandon", action="store_true")
    parser.add_argument("--adopt")
    args = parser.parse_args(argv)

    def need(*names: str) -> None:
        missing = [f"--{name.replace('_', '-')}" for name in names if getattr(args, name) in (None, "")]
        if missing:
            parser.error(f"{args.action} requires {', '.join(missing)}")

    try:
        repo = args.repo
        if args.action == "status":
            result = status(repo, tools=not args.no_tools)
        elif args.action == "targets":
            found, skipped = targets(repo)
            result = {"schema": SCHEMA, "targets": [_public(row) for row in found], "skipped": skipped}
        elif args.action == "publish":
            result = publish(repo, args.objective, args.owner_ruling)
        elif args.action == "record":
            need("target", "execution", "head")
            result = record(repo, args.target, args.execution, args.head)
        elif args.action == "ended":
            need("target", "status_file")
            result = ended(repo, args.target, _read_json(args.status_file))
        elif args.action == "retire":
            if args.adopt is None:
                need("target")
            result = retire(repo, args.target, args.abandon, args.adopt, args.owner_ruling)
        elif args.action == "capture":
            need("target", "findings")
            result = capture(repo, args.target, _read_json(args.findings))
        elif args.action == "rule":
            need("ruling")
            result = rule(repo, _read_json(args.ruling))
        elif args.action == "finish":
            result = finish(repo, _read_json(args.responses) if args.responses else {})
        elif args.action == "close":
            need("reason")
            result = close(repo, args.reason, args.owner_ruling)
        elif args.action == "adopt-report":
            need("owner_ruling")
            result = adopt_report(repo, args.owner_ruling)
        else:
            require_closed(repo)
            result = {"schema": SCHEMA, "status": "closed-or-absent"}
    except (PrePrError, OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"schema": SCHEMA, "status": "error", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
