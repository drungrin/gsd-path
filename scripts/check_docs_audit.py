#!/usr/bin/env python3
# gsd-path project runtime
"""Gate a GSD Path docs audit (.project/research/DOCS-AUDIT.md) against its contract.

Checks the rules the docs-audit contract states in prose: the header lines,
a Summary table whose counts match the body, one `## Doc:` section per doc
with testable claims where every claim row carries a valid type, a valid
verdict, and real evidence, a `## Descriptive docs` list for claimless docs,
the two sets disjoint and together equal to the frozen inventory, and a
remediation queue with one classified row per non-verified claim.
Queue numbers are unique, positive and strictly increasing, not necessarily
contiguous. With `--prior-audit`, persisting rows keep their number and new
rows use a number above every prior queue number and User rulings Queue #.
Numbers stay bound to their original (doc, claim); never reuse a retired one.
`## User rulings` is fixed-format durable memory. Pass the pre-rewrite audit
with `--prior-audit FILE`; every prior row, including its Planned value, must
remain an exact ordered prefix.

A re-audit may carry a prior `verified` row forward verbatim with its Evidence
prefixed `unchanged: `. Pass the paths that differ from the prior audit's
`Audited HEAD` with `--changed FILE` (one path per line); a carried row must
repeat a prior verified non-command row whose doc and evidence paths are all
outside that set. Without both `--prior-audit` and `--changed`, carried rows
are rejected.

The frozen inventory travels in the dispatch brief; pass it with
`--inventory FILE` (one POSIX path per line, `-` for stdin). Without it the
gate derives the inventory from tracked Markdown files, excluding `.git`,
`node_modules`, `.project/archive/**`, installed `*/skills/gsd-path*` and
`*/skills/path` bundles,
the audit itself, and — unless the
audit declares `Alignment mode: yes` — everything else under `.project/`.

`Repo root` names the primary repository. When the audit is gated inside a
disposable sidecar, pass that sidecar as `--repo` and the primary as
`--primary`; the header then stays valid after the sidecar is retired.

Member mode (`--member NAME --pin SHA`, optional `--project-dir .project` or
`.project/next`) gates `<project-dir>/research/DOCS-AUDIT-<name>.md` in the
coordinator artifact tree `--repo`; `--primary` (default `--repo`) still names
the coordinator whose MEMBERS.md registers the member. The member header
(Repo root = MEMBERS Checkout, Member, Default branch, Member HEAD = pin) and
`Audited HEAD` must name the frozen pin, and `Alignment mode` must be no. The
inventory is the Markdown of the pinned commit tree, read from the member's
object database (never its working tree, index, HEAD, or untracked files); an
explicit `--inventory` must equal it. A `--prior-audit` must belong to the same
member, and `--changed` must equal the diff from its Member HEAD to the pin.
"""

import argparse
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
sys.dont_write_bytecode = True
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

DEFAULT_AUDIT = ".project/research/DOCS-AUDIT.md"
TYPES = ("command", "feature", "structure", "status", "config", "integration")
VERDICTS = ("verified", "stale", "aspirational", "unverifiable")
CLASSES = ("fix-doc", "fix-code", "NEEDS-USER")
RULINGS = ("fix-code", "fix-doc", "accept-drift")
SUMMARY_ROWS = VERDICTS + ("descriptive docs (no testable claims)",)

HEADER_PATTERN = re.compile(r"^(Repo root|Audited|Audited HEAD|Alignment mode): (.+)$", re.M)
HEAD_PATTERN = re.compile(r"[0-9a-f]{40}|none")
CARRIED = "unchanged: "  # Evidence prefix of a row carried forward from the prior audit
SECTION_PATTERN = re.compile(r"^## (.+?)\s*$", re.M)
PLACEHOLDER_PATTERN = re.compile(r"^<[^>]*>$")  # a cell left as the template placeholder
PIN_PATTERN = re.compile(r"[0-9a-f]{40}")
MEMBER_PROJECT_DIRS = (".project", ".project/next")
REGULAR_BLOB_MODES = (b"100644", b"100755")


class AuditError(RuntimeError):
    """Raised when the audit is absent or violates its contract."""


def _cells(line: str) -> Optional[List[str]]:
    if not line.startswith("|"):
        return None
    cells = [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
    if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
        return None  # separator row
    return cells


def _sections(text: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    matches = list(SECTION_PATTERN.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group(1)
        if title in sections:
            raise AuditError(f"duplicate section: ## {title}")
        sections[title] = text[match.end() : end]
    return sections


def _table_rows(body: str, width: int, label: str) -> List[List[str]]:
    rows = []
    for line in body.splitlines():
        cells = _cells(line)
        if cells is None:
            continue
        if len(cells) != width:
            raise AuditError(f"{label}: expected {width} columns, found {len(cells)}: {line.strip()}")
        rows.append(cells)
    if not rows:
        raise AuditError(f"{label}: missing table")
    header, *rows = rows
    return rows


def _remediation_queue_rows(body: str, label: str, required: bool = False) -> List[List[str]]:
    if not required and "|" not in body:
        return []
    return _table_rows(body, 6, label)


def _not_placeholder(value: str, label: str) -> None:
    normalized = value.strip().strip("`\"").strip()
    if (
        not normalized
        or normalized.casefold() == "none"
        or PLACEHOLDER_PATTERN.match(normalized)
    ):
        raise AuditError(f"{label}: placeholder or empty value: {value!r}")


def _installed_skill(parts: Sequence[str]) -> bool:
    """Exclude installed GSD Path bundles, including the path router alias."""
    return any(
        parts[index] == "skills" and
        (parts[index + 1].startswith("gsd-path") or parts[index + 1] == "path")
        for index in range(len(parts) - 2)
    )


def derive_inventory(repo: Path, audit_relative: str, alignment: bool) -> List[str]:
    listed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.md",
            "**/*.md",
        ],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        raise AuditError("cannot derive inventory: git ls-files failed; pass --inventory")
    paths = set()
    for raw in listed.stdout.decode("utf-8").split("\0"):
        if not raw or _excluded(raw, audit_relative, alignment):
            continue
        paths.add(raw)
    return sorted(paths)


def _excluded(raw: str, audit_relative: str, alignment: bool) -> bool:
    """The inventory exclusions shared by checkout and pinned-revision discovery."""
    if raw == audit_relative:
        return True
    parts = raw.split("/")
    if parts[0] in (".git", "node_modules") or "node_modules" in parts:
        return True
    if _installed_skill(parts):
        return True
    if raw.startswith(".project/archive/"):
        return True
    return raw.startswith(".project/") and not alignment


def _member_helpers():
    """Slice-1 member identity, evidence, and Git object helpers; member mode only."""
    try:
        if __package__:
            from . import check_handoffs, detect_project, members
        else:
            import check_handoffs
            import detect_project
            import members
    except ImportError as error:
        raise AuditError(
            f"member mode requires the bundled members, check_handoffs, and detect_project helpers: {error}"
        ) from error
    return check_handoffs, detect_project, members


def derive_revision_inventory(
    checkout: Path,
    pin: str,
    audit_relative: str,
    alignment: bool = False,
    member: Optional[str] = None,
) -> List[str]:
    """Markdown in the commit tree at `pin`, read only from the checkout's object database.

    Never consults the working tree, index, HEAD, or untracked files. A
    Markdown symlink whose target, resolved from the link's directory, is an
    inventoried regular Markdown blob of the same tree (`CLAUDE.md ->
    AGENTS.md`) is an alias: the target is audited and the link is left out.
    Any other inventoried Markdown entry that is not a regular blob blocks;
    an empty inventory is valid."""
    who = f"member {member}" if member else "revision"
    if not PIN_PATTERN.fullmatch(pin):
        raise AuditError(f"cannot derive {who} inventory: pin must be a full 40-character commit SHA, not {pin!r}")
    _, detect_project, _ = _member_helpers()
    try:
        listing = detect_project.revision_git(
            Path(checkout), "ls-tree", "-r", "-z", "--full-tree", f"{pin}^{{commit}}"
        )
    except detect_project.DetectError as error:
        raise AuditError(f"cannot derive {who} inventory at {pin}: git ls-tree failed") from error
    entries: Dict[str, tuple] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded = record.split(b"\t", 1)
            mode, kind, sha = metadata.split(b" ")
        except ValueError as error:
            raise AuditError(f"cannot derive {who} inventory at {pin}: git ls-tree returned malformed data") from error
        if not encoded.endswith(b".md"):
            continue
        try:
            raw = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AuditError(f"cannot derive {who} inventory at {pin}: non-UTF-8 Markdown path {encoded!r}") from error
        if any(part in ("", ".", "..") for part in raw.split("/")):
            raise AuditError(f"cannot derive {who} inventory at {pin}: unsafe tree path {raw!r}")
        if _excluded(raw, audit_relative, alignment):
            continue
        entries[raw] = (mode, kind, sha)

    def regular(path: str) -> bool:
        found = entries.get(path)
        return found is not None and found[1] == b"blob" and found[0] in REGULAR_BLOB_MODES

    def alias_target(path: str, sha: bytes) -> Optional[str]:
        """The in-tree regular Markdown blob a symlink names, or None."""
        try:
            target = detect_project.revision_git(Path(checkout), "cat-file", "blob", sha.decode("ascii"))
            text = target.decode("utf-8")
        except (detect_project.DetectError, UnicodeDecodeError):
            return None
        if not text or text.startswith("/") or "\0" in text or "\\" in text:
            return None
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), text))
        if resolved in (".", "..") or resolved.startswith("../") or not resolved.endswith(".md"):
            return None
        return resolved if regular(resolved) else None

    paths = []
    for raw, (mode, kind, sha) in entries.items():
        if regular(raw):
            paths.append(raw)
        elif not (kind == b"blob" and mode == b"120000" and alias_target(raw, sha)):
            raise AuditError(f"{who} pinned Markdown is not a regular blob: {raw}")
    return sorted(paths)


def _user_rulings(body: str, label: str) -> List[List[str]]:
    rows = _table_rows(body, 4, label)
    seen = set()
    for number, ruling, words, planned in rows:
        if not number.isdigit() or int(number) < 1:
            raise AuditError(f"{label}: Queue # must be a positive integer: {number}")
        if number in seen:
            raise AuditError(f"{label}: repeated Queue #: {number}")
        seen.add(number)
        if ruling not in RULINGS:
            raise AuditError(f"{label}: invalid ruling: {ruling}")
        _not_placeholder(words, f"{label} #{number} User's words")
        if ruling == "accept-drift":
            if planned != "n/a (accept-drift)":
                raise AuditError(
                    f"{label} #{number}: accept-drift must be planned n/a (accept-drift)"
                )
        elif planned != "no" and not re.fullmatch(r"T\d{3}", planned):
            raise AuditError(
                f"{label} #{number}: Planned must be no or a T### task id"
            )
    return rows


def _doc_claims(sections: Dict[str, str]) -> Dict[str, List[Dict[str, str]]]:
    docs: Dict[str, List[Dict[str, str]]] = {}
    for title, body in sections.items():
        if not title.startswith("Doc: "):
            continue
        path = title[len("Doc: ") :].strip().strip("`")
        _not_placeholder(path, "Doc section path")
        if path in docs:
            raise AuditError(f"duplicate normalized Doc section: {path}")
        claims = []
        claim_rows = _table_rows(body, 4, f"Doc: {path}")
        if not claim_rows:
            raise AuditError(f"Doc: {path}: requires at least one claim row")
        for claim, kind, verdict, evidence in claim_rows:
            if kind not in TYPES:
                raise AuditError(f"Doc: {path}: invalid claim type: {kind}")
            if verdict not in VERDICTS:
                raise AuditError(f"Doc: {path}: invalid verdict: {verdict}")
            _not_placeholder(claim, f"Doc: {path}: claim")
            _not_placeholder(evidence, f"Doc: {path}: evidence for {claim}")
            claims.append({"claim": claim, "type": kind, "verdict": verdict, "evidence": evidence})
        docs[path] = claims
    return docs


def _evidence_names_path(evidence: str, path: str) -> bool:
    parts = path.split("/")
    return path in evidence or any(
        "/".join(parts[:end]) + "/" in evidence
        for end in range(1, len(parts))
    )


def _check_carried(
    docs: Dict[str, List[Dict[str, str]]],
    prior_sections: Optional[Dict[str, str]],
    changed: Optional[Sequence[str]],
) -> None:
    carried = [
        (path, claim)
        for path, claims in docs.items()
        for claim in claims
        if claim["evidence"].startswith(CARRIED)
    ]
    if not carried:
        return
    if changed is None:
        raise AuditError("carried (unchanged:) rows require --prior-audit and --changed")
    prior = {
        (path, claim["claim"], claim["type"]): claim["evidence"].removeprefix(CARRIED)
        for path, claims in _doc_claims(prior_sections or {}).items()
        for claim in claims
        if claim["verdict"] == "verified"
    }
    for path, claim in carried:
        label = f"Doc: {path}: carried row {claim['claim']}"
        evidence = claim["evidence"].removeprefix(CARRIED)
        if claim["verdict"] != "verified" or claim["type"] == "command":
            raise AuditError(f"{label}: only verified non-command claims carry forward")
        if prior.get((path, claim["claim"], claim["type"])) != evidence:
            raise AuditError(f"{label}: does not repeat a prior verified row")
        # Evidence is free text, so a substring match errs toward re-verifying.
        moved = sorted(
            changed_path
            for changed_path in changed
            if changed_path == path
            or _evidence_names_path(evidence, changed_path)
        )
        if moved:
            raise AuditError(f"{label}: doc or evidence changed since the prior audit: {', '.join(moved)}")


def _queue_number(number: str, label: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise AuditError(f"{label}: queue numbers must be positive integers")
    return int(number)


def _check_queue_number_history(
    queue: Sequence[Sequence[str]],
    prior_sections: Dict[str, str],
) -> None:
    if "Remediation queue" not in prior_sections:
        raise AuditError("prior audit is missing section: ## Remediation queue")
    if "User rulings" not in prior_sections:
        raise AuditError("prior audit is missing section: ## User rulings")

    prior_queue = _remediation_queue_rows(
        prior_sections["Remediation queue"], "Prior remediation queue"
    )
    prior_items_by_number: Dict[int, tuple] = {}
    prior_numbers_by_item: Dict[tuple, int] = {}
    for number, doc, claim, *_ in prior_queue:
        numeric_number = _queue_number(number, "Prior remediation queue")
        identity = (doc, claim)
        prior_items_by_number[numeric_number] = identity
        prior_numbers_by_item[identity] = numeric_number

    prior_rulings = _user_rulings(
        prior_sections["User rulings"], "Prior user rulings"
    )
    prior_ruling_numbers = {int(row[0]) for row in prior_rulings}
    prior_max = max(
        [*prior_items_by_number, *prior_ruling_numbers],
        default=0,
    )

    for number, doc, claim, *_ in queue:
        numeric_number = _queue_number(number, "Remediation queue")
        identity = (doc, claim)
        prior_number = prior_numbers_by_item.get(identity)
        if numeric_number in prior_ruling_numbers and prior_items_by_number.get(numeric_number) != identity:
            raise AuditError(
                f"Remediation queue #{number}: Queue # {number} is already retired by a prior "
                "User rulings row for a different item; give new items a number above every "
                "prior queue number and User rulings Queue #"
            )
        if prior_number is not None and numeric_number != prior_number:
            raise AuditError(
                f"Remediation queue #{number}: persisting item must keep its prior queue "
                f"number #{prior_number}"
            )
        if prior_number is None and numeric_number <= prior_max:
            raise AuditError(
                f"Remediation queue #{number}: new items must use a number above every prior "
                f"queue number and User rulings Queue # (maximum {prior_max})"
            )


def _audit_header(text: str) -> Dict[str, str]:
    header_rows = HEADER_PATTERN.findall(text)
    header = {}
    for key in ("Repo root", "Audited", "Audited HEAD", "Alignment mode"):
        values = [value for found_key, value in header_rows if found_key == key]
        if len(values) != 1:
            raise AuditError(f"header must contain exactly one {key} line")
        header[key] = values[0]
    return header


def _header_alignment(header: Dict[str, str]) -> bool:
    try:
        date.fromisoformat(header["Audited"])
    except ValueError as error:
        raise AuditError("Audited must be an ISO date") from error
    if not HEAD_PATTERN.fullmatch(header["Audited HEAD"]):
        raise AuditError("Audited HEAD must be a 40-hex commit or none")
    alignment_value = header["Alignment mode"].split("—")[0].strip().lower()
    if alignment_value not in ("yes", "no"):
        raise AuditError(f"Alignment mode must be yes or no, found: {header['Alignment mode']}")
    return alignment_value == "yes"


def validate(
    repo: Path,
    audit_relative: str,
    inventory: Optional[Sequence[str]],
    prior_text: Optional[str] = None,
    changed: Optional[Sequence[str]] = None,
    primary: Optional[Path] = None,
) -> Dict[str, object]:
    audit = repo / audit_relative
    if not audit.is_file() or audit.is_symlink():
        raise AuditError(f"missing real audit file: {audit_relative}")
    text = audit.read_text(encoding="utf-8")

    header = _audit_header(text)
    declared_root = Path(header["Repo root"])
    if not declared_root.is_absolute() or declared_root.resolve() != (primary or repo).resolve():
        raise AuditError("Repo root does not match --primary (default --repo)")
    alignment = _header_alignment(header)
    return _validate_body(
        text,
        alignment,
        lambda: sorted(inventory) if inventory is not None else derive_inventory(repo, audit_relative, alignment),
        audit_relative,
        prior_text,
        changed,
    )


def _validate_body(
    text: str,
    alignment: bool,
    expected_inventory: Callable[[], List[str]],
    audit_relative: str,
    prior_text: Optional[str],
    changed: Optional[Sequence[str]],
) -> Dict[str, object]:
    sections = _sections(text)
    for required in ("Summary", "Descriptive docs", "User rulings", "Remediation queue"):
        if required not in sections:
            raise AuditError(f"missing section: ## {required}")

    summary = {}
    for verdict, count in _table_rows(sections["Summary"], 2, "Summary"):
        if verdict not in SUMMARY_ROWS:
            raise AuditError(f"Summary: unknown row: {verdict}")
        if not count.isdigit():
            raise AuditError(f"Summary: count for {verdict} is not an integer: {count!r}")
        summary[verdict] = int(count)
    missing = [row for row in SUMMARY_ROWS if row not in summary]
    if missing:
        raise AuditError(f"Summary: missing rows: {', '.join(missing)}")

    docs = _doc_claims(sections)
    tallies = {verdict: 0 for verdict in VERDICTS}
    for claims in docs.values():
        for claim in claims:
            tallies[claim["verdict"]] += 1

    descriptive = []
    for line in sections["Descriptive docs"].splitlines():
        line = line.strip()
        if line.startswith("- "):
            path = line[2:].strip().strip("`")
            _not_placeholder(path, "Descriptive docs entry")
            if path in descriptive:
                raise AuditError(f"Descriptive docs: listed twice: {path}")
            descriptive.append(path)

    overlap = sorted(set(docs) & set(descriptive))
    if overlap:
        raise AuditError(f"docs listed both as claim sections and descriptive: {', '.join(overlap)}")

    expected = expected_inventory()
    covered = sorted(set(docs) | set(descriptive))
    if covered != expected:
        unaudited = sorted(set(expected) - set(covered))
        extra = sorted(set(covered) - set(expected))
        detail = []
        if unaudited:
            detail.append(f"not audited: {', '.join(unaudited)}")
        if extra:
            detail.append(f"outside inventory: {', '.join(extra)}")
        raise AuditError("audit coverage does not equal the frozen inventory; " + "; ".join(detail))

    for verdict in VERDICTS:
        if summary[verdict] != tallies[verdict]:
            raise AuditError(f"Summary: {verdict} count {summary[verdict]} does not match {tallies[verdict]} claim rows")
    if summary["descriptive docs (no testable claims)"] != len(descriptive):
        raise AuditError(
            f"Summary: descriptive count {summary['descriptive docs (no testable claims)']} "
            f"does not match {len(descriptive)} listed docs"
        )

    rulings = _user_rulings(sections["User rulings"], "User rulings")
    prior_sections = _sections(prior_text) if prior_text is not None else None
    if prior_sections is not None:
        if "User rulings" not in prior_sections:
            raise AuditError("prior audit is missing section: ## User rulings")
        prior_rulings = _user_rulings(
            prior_sections["User rulings"], "Prior user rulings"
        )
        if rulings[: len(prior_rulings)] != prior_rulings:
            raise AuditError(
                "User rulings must preserve every prior row and Planned value in order"
            )
    _check_carried(docs, prior_sections, changed)

    queue_body = sections["Remediation queue"]
    non_verified = sum(tallies[verdict] for verdict in VERDICTS if verdict != "verified")
    queue = _remediation_queue_rows(
        queue_body, "Remediation queue", required=bool(non_verified)
    )
    expected_queue = sorted(
        (doc, claim["claim"], claim["verdict"])
        for doc, claims in docs.items()
        for claim in claims
        if claim["verdict"] != "verified"
    )
    for number, doc, claim, verdict, klass, action in queue:
        if verdict not in VERDICTS or verdict == "verified":
            raise AuditError(f"Remediation queue #{number}: verdict must be non-verified, found: {verdict}")
        if klass not in CLASSES:
            raise AuditError(f"Remediation queue #{number}: invalid class: {klass}")
        if doc not in docs:
            raise AuditError(f"Remediation queue #{number}: unknown doc: {doc}")
        _not_placeholder(action, f"Remediation queue #{number}: action")
    if len(queue) != non_verified:
        raise AuditError(f"Remediation queue has {len(queue)} rows for {non_verified} non-verified claims")
    numbers = [_queue_number(row[0], "Remediation queue") for row in queue]
    if any(later <= earlier for earlier, later in zip(numbers, numbers[1:])):
        raise AuditError(
            "Remediation queue numbers must be unique and strictly increasing"
        )
    if prior_sections is not None:
        _check_queue_number_history(queue, prior_sections)
    actual_queue = sorted((doc, claim, verdict) for _, doc, claim, verdict, _, _ in queue)
    if actual_queue != expected_queue:
        raise AuditError(
            "Remediation queue must bind each non-verified doc, claim, and verdict exactly once"
        )

    return {
        "audit": audit_relative,
        "alignment": alignment,
        "docs": sorted(docs),
        "descriptive": descriptive,
        "claims": sum(len(claims) for claims in docs.values()),
        "verdicts": tallies,
        "queue": len(queue),
        "rulings": len(rulings),
    }


class MemberContext(NamedTuple):
    """A registered member's identity and the frozen pin its audit names."""

    name: str
    checkout: str  # the literal MEMBERS.md Checkout
    default_branch: str
    pin: str
    project_dir: str


def member_audit_path(member: str, project_dir: str = ".project") -> str:
    """The canonical track-scoped audit path of `member`, relative to the coordinator."""
    _, _, members = _member_helpers()
    if not members.NAME_RE.fullmatch(member):
        raise AuditError(f"invalid member name: {member!r}")
    if project_dir not in MEMBER_PROJECT_DIRS:
        raise AuditError(f"--project-dir must be one of {', '.join(MEMBER_PROJECT_DIRS)}, not {project_dir!r}")
    return f"{project_dir}/{members.inspection_paths(member)[1]}"


def _member_audit_context(
    repo: Path, primary: Optional[Path], project_dir: str, member: str, pin: str
) -> MemberContext:
    """Resolve `member` from the coordinator registry and prove `pin` is a commit it holds.

    Uses slice 1's inspection scope for checkout, marker, and origin identity.
    The pin need not equal a tracking ref that moved after it was frozen."""
    member_audit_path(member, project_dir)
    if not PIN_PATTERN.fullmatch(pin):
        raise AuditError(f"--pin must be a full 40-character commit SHA, not {pin!r}")
    _, detect_project, members = _member_helpers()
    try:
        scope = members.inspection_scope(Path(primary or repo), project_dir)
    except members.MembersError as error:
        raise AuditError(str(error)) from error
    entry = next((item for item in scope["members"] if item["name"] == member), None)
    if entry is None:
        raise AuditError(f"member {member} is not in MEMBERS.md")
    if entry["verdict"] == "unverifiable":
        raise AuditError(f"member {member} is unverifiable: {entry['reason']}")
    checkout = str(entry["checkout"])
    try:
        resolved = detect_project.revision_git(
            Path(checkout), "rev-parse", "--verify", "--quiet", f"{pin}^{{commit}}"
        ).decode("ascii").strip()
    except (detect_project.DetectError, UnicodeDecodeError):
        resolved = None
    if resolved != pin:
        raise AuditError(f"member {member} pin {pin} is not a commit in {checkout}")
    return MemberContext(member, checkout, str(entry["default_branch"]), pin, project_dir)


def _real_audit_file(repo: Path, audit_relative: str) -> Path:
    """The audit under `repo`, refusing a symlinked or non-directory ancestor."""
    current = repo
    parts = PurePosixPath(audit_relative).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            status = os.lstat(current)
        except FileNotFoundError:
            raise AuditError(f"missing real audit file: {audit_relative}") from None
        except OSError as error:
            raise AuditError(f"cannot inspect {'/'.join(parts[:index + 1])}: {error}") from error
        last = index == len(parts) - 1
        if last and not stat.S_ISREG(status.st_mode):
            raise AuditError(f"missing real audit file: {audit_relative}")
        if not last and not stat.S_ISDIR(status.st_mode):
            raise AuditError(f"audit ancestor must be a real directory: {'/'.join(parts[:index + 1])}")
    return current


def _prior_member_head(prior_text: str, context: MemberContext) -> str:
    check_handoffs, _, _ = _member_helpers()
    found = check_handoffs.member_evidence_header(prior_text)
    values = {field: entries[0] for field, entries in found.items() if len(entries) == 1}
    expected = {"Repo root": context.checkout, "Member": context.name, "Default branch": context.default_branch}
    if len(values) != len(found) or any(values[field] != value for field, value in expected.items()):
        raise AuditError("prior audit belongs to a different member identity")
    if not PIN_PATTERN.fullmatch(values["Member HEAD"]):
        raise AuditError(
            f"prior audit Member HEAD must be the full 40-character commit SHA, not {values['Member HEAD'] or 'empty'}"
        )
    return values["Member HEAD"]


def _require_member_changed(context: MemberContext, prior_head: str, changed: Sequence[str]) -> None:
    """`changed` must equal the commit diff from the prior Member HEAD to the pin, never a team diff."""
    _, detect_project, _ = _member_helpers()
    checkout = Path(context.checkout)
    try:
        detect_project.revision_git(checkout, "merge-base", "--is-ancestor", prior_head, context.pin)
    except detect_project.DetectError as error:
        raise AuditError(
            f"member {context.name} --changed requires prior Member HEAD {prior_head} to be a resolvable "
            f"ancestor of the pin {context.pin}; omit --changed and re-audit every doc"
        ) from error
    try:
        listing = detect_project.revision_git(
            checkout, "diff", "--no-ext-diff", "--name-only", "--no-renames", "-z", prior_head, context.pin
        )
        expected = sorted(path.decode("utf-8") for path in listing.split(b"\0") if path)
    except (detect_project.DetectError, UnicodeDecodeError) as error:
        raise AuditError(f"cannot diff member {context.name} from {prior_head} to {context.pin}: {error}") from error
    supplied = sorted(changed)
    if supplied != expected:
        detail = []
        missing = sorted(set(expected) - set(supplied))
        extra = sorted(set(supplied) - set(expected))
        if missing:
            detail.append(f"missing: {', '.join(missing)}")
        if extra:
            detail.append(f"extra: {', '.join(extra)}")
        if len(set(supplied)) != len(supplied):
            detail.append("repeated paths")
        raise AuditError(
            f"member {context.name} --changed does not equal the pinned prior-to-current diff; " + "; ".join(detail)
        )


def validate_member(
    repo: Path,
    member: str,
    pin: str,
    inventory: Optional[Sequence[str]],
    prior_text: Optional[str] = None,
    changed: Optional[Sequence[str]] = None,
    primary: Optional[Path] = None,
    project_dir: str = ".project",
    audit_relative: Optional[str] = None,
) -> Dict[str, object]:
    """Gate a member docs audit read from the coordinator artifact tree `repo` at a frozen pin."""
    canonical = member_audit_path(member, project_dir)
    if audit_relative is not None and audit_relative != canonical:
        raise AuditError(f"member {member} audit must be {canonical}")
    context = _member_audit_context(repo, primary, project_dir, member, pin)
    audit = _real_audit_file(repo, canonical)
    try:
        text = audit.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AuditError(f"cannot read {canonical}: {error}") from error

    header = _audit_header(text)
    check_handoffs, _, _ = _member_helpers()
    try:
        check_handoffs.validate_member_evidence(
            audit, context.name, context.pin, context.checkout, context.default_branch, "docs"
        )
    except check_handoffs.HandoffError as error:
        raise AuditError(str(error)) from error
    if _header_alignment(header):
        raise AuditError(f"member {member} audit does not support alignment mode")
    if header["Audited HEAD"] != context.pin:
        raise AuditError(f"{PurePosixPath(canonical).name} Audited HEAD must equal Member HEAD {context.pin}")

    pinned = derive_revision_inventory(Path(context.checkout), context.pin, canonical, False, member)
    if inventory is not None and sorted(inventory) != pinned:
        detail = []
        missing = sorted(set(pinned) - set(inventory))
        extra = sorted(set(inventory) - set(pinned))
        if missing:
            detail.append(f"missing from --inventory: {', '.join(missing)}")
        if extra:
            detail.append(f"not in the pinned tree: {', '.join(extra)}")
        if len(set(inventory)) != len(inventory):
            detail.append("repeated paths")
        raise AuditError(f"member {member} pinned inventory differs from --inventory; " + "; ".join(detail))

    if prior_text is not None:
        prior_head = _prior_member_head(prior_text, context)
        if changed is not None:
            _require_member_changed(context, prior_head, changed)
    result = _validate_body(text, False, lambda: pinned, canonical, prior_text, changed)
    result["member"] = {
        "name": context.name,
        "checkout": context.checkout,
        "default_branch": context.default_branch,
        "pin": context.pin,
        "project_dir": context.project_dir,
    }
    return result


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argument_parser.add_argument("--repo", type=Path, required=True)
    argument_parser.add_argument(
        "--audit",
        help=f"relative audit path (default: {DEFAULT_AUDIT}; member mode: "
        "<project-dir>/research/DOCS-AUDIT-<member>.md)",
    )
    argument_parser.add_argument("--inventory", help="file with one inventoried POSIX path per line, or - for stdin")
    argument_parser.add_argument(
        "--primary",
        type=Path,
        help="primary repository the Repo root header must name; in member mode, the coordinator "
        "whose MEMBERS.md registers --member (default: --repo)",
    )
    argument_parser.add_argument(
        "--prior-audit",
        type=Path,
        help="pre-rewrite audit whose User rulings must carry forward",
    )
    argument_parser.add_argument(
        "--changed",
        help="file with one path changed since the prior audit's Audited HEAD per line; permits unchanged: rows",
    )
    argument_parser.add_argument(
        "--emit-inventory",
        action="store_true",
        help="print the frozen tracked and untracked Markdown inventory, one path per line",
    )
    argument_parser.add_argument(
        "--alignment",
        action="store_true",
        help="include active .project Markdown when emitting an alignment inventory",
    )
    argument_parser.add_argument("--member", help="MEMBERS.md member whose docs audit to gate (requires --pin)")
    argument_parser.add_argument("--pin", help="frozen 40-hex member commit the audit names (requires --member)")
    argument_parser.add_argument(
        "--project-dir",
        help="member audit track: .project or .project/next (default: .project; requires --member)",
    )
    return argument_parser


def _path_lines(name: str) -> List[str]:
    source = sys.stdin if name == "-" else open(name, encoding="utf-8")
    with source:
        return [line.strip() for line in source if line.strip()]


def _member_main(arguments: argparse.Namespace) -> int:
    def failed(message: str) -> int:
        print(f"docs audit validation failed: {message}", file=sys.stderr)
        return 1

    if arguments.member is None:
        return failed("--pin requires --member" if arguments.pin is not None else "--project-dir requires --member")
    if arguments.pin is None:
        return failed("--member requires --pin")
    project_dir = arguments.project_dir if arguments.project_dir is not None else ".project"
    repo = arguments.repo.resolve()
    try:
        canonical = member_audit_path(arguments.member, project_dir)
        if arguments.alignment:
            raise AuditError(f"member {arguments.member} audit does not support alignment mode")
        if arguments.audit is not None and arguments.audit != canonical:
            raise AuditError(f"member {arguments.member} audit must be {canonical}")
        if arguments.emit_inventory:
            if arguments.inventory is not None or arguments.prior_audit is not None or arguments.changed is not None:
                raise AuditError("--emit-inventory cannot use validation inputs")
            context = _member_audit_context(repo, arguments.primary, project_dir, arguments.member, arguments.pin)
            inventory = derive_revision_inventory(
                Path(context.checkout), context.pin, canonical, False, context.name
            )
            sys.stdout.write("".join(f"{path}\n" for path in inventory))  # an empty tree emits nothing
            return 0
        inventory = _path_lines(arguments.inventory) if arguments.inventory is not None else None
        prior_text = None
        if arguments.prior_audit is not None:
            if arguments.prior_audit.is_symlink() or not arguments.prior_audit.is_file():
                raise AuditError("--prior-audit must be a real file")
            prior_text = arguments.prior_audit.read_text(encoding="utf-8")
        changed = _path_lines(arguments.changed) if arguments.changed is not None else None
        result = validate_member(
            repo, arguments.member, arguments.pin, inventory, prior_text, changed,
            arguments.primary, project_dir, canonical,
        )
    except (AuditError, OSError, UnicodeDecodeError) as error:
        return failed(str(error))
    print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.changed is not None and arguments.prior_audit is None:
        print("docs audit validation failed: --changed requires --prior-audit", file=sys.stderr)
        return 1
    if arguments.member is not None or arguments.pin is not None or arguments.project_dir is not None:
        return _member_main(arguments)
    if arguments.audit is None:
        arguments.audit = DEFAULT_AUDIT
    if arguments.emit_inventory:
        if arguments.inventory is not None or arguments.prior_audit is not None or arguments.changed is not None:
            print(
                "docs audit validation failed: --emit-inventory cannot use validation inputs",
                file=sys.stderr,
            )
            return 1
        try:
            inventory = derive_inventory(
                arguments.repo.resolve(), arguments.audit, arguments.alignment
            )
        except AuditError as error:
            print(f"docs audit validation failed: {error}", file=sys.stderr)
            return 1
        print("\n".join(inventory))
        return 0
    inventory = _path_lines(arguments.inventory) if arguments.inventory is not None else None
    prior_text = None
    if arguments.prior_audit is not None:
        if arguments.prior_audit.is_symlink() or not arguments.prior_audit.is_file():
            print(
                "docs audit validation failed: --prior-audit must be a real file",
                file=sys.stderr,
            )
            return 1
        prior_text = arguments.prior_audit.read_text(encoding="utf-8")
    changed = _path_lines(arguments.changed) if arguments.changed is not None else None
    try:
        result = validate(
            arguments.repo.resolve(), arguments.audit, inventory, prior_text, changed,
            arguments.primary,
        )
    except AuditError as error:
        print(f"docs audit validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
