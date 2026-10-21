#!/usr/bin/env python3
# gsd-path project runtime
"""Record and validate the member repositories of a multi-repo coordinator.

The coordinator's `.project/MEMBERS.md` lists members in ship order
(docs/adr/0002-multi-repo-coordinator.md). `add` writes that file and an
untracked marker in the member's shared Git directory. The next approval
checkpoint commits MEMBERS.md with the other `.project` artifacts.
`member_role` verifies the marker from any worktree of the member.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# Runtime helpers must not modify their immutable installation.
sys.dont_write_bytecode = True
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

if __package__:
    from . import _common, pipeline_state, azure_repos
else:
    import _common
    import pipeline_state
    import azure_repos


MEMBERS_FILE = "MEMBERS.md"
MARKER_SCHEMA = "gsd-path/member/v1"
MARKER_KEYS = {"schema", "coordinator", "project", "name"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FIELDS = ("Checkout", "Remote", "Integration")
# Optional: `add` records it one time; a row without it means main.
DEFAULT_BRANCH_FIELD = "Default branch"
INTEGRATIONS = ("default", "direct", "pull-request")
GITHUB_REMOTE_RE = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/\s]+)/([^/\s]+)"
)
# Member-side Path refs carry the coordinator name, so they cannot collide with
# a member's own Path history.
MEMBER_BRANCH_FORMATS = ("feature/{}-milestone-", "chore/task-{}-", "chore/verify-{}-", "chore/integrate-{}-",
                         "chore/rebase-{}-")
MEMBER_TAG_FORMAT = "refs/tags/milestone/{}-"
# The coordinator records one authorization per (ref, object) before it
# publishes a member Path ref; member pre-push accepts only a matching one.
AUTHORIZATION_PREFIX = "refs/gsd-path/authorizations/"
LOCK_PATH = ".project/build/members.json"
LOCK_SCHEMA = "gsd-path/member-lock/v1"
HEADER = (
    "# Members\n\n"
    "<!-- Written by members.py add. One section per member repository, in\n"
    "     ship order. Fixed format; change it only through members.py. -->\n"
)


class MembersError(RuntimeError):
    pass


def _git(repo: Path, *arguments: str) -> str:
    result = _common.run_git(repo, *arguments)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "git command failed"
        raise MembersError(f"{repo}: git {' '.join(arguments)}: {detail}")
    return result.stdout.strip()


def _coordinator(repo: Path) -> tuple[Path, pipeline_state.PipelineState]:
    try:
        root = pipeline_state._repo_root(Path(repo))
        state, _, _ = pipeline_state.load_state(root)
    except pipeline_state.PipelineStateError as error:
        raise MembersError(f"coordinator: {error}") from error
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise MembersError(f"coordinator is not a Git root: {root}")
    return root, state


def _remote_identity(remote: str) -> Optional[tuple[str, ...]]:
    match = GITHUB_REMOTE_RE.fullmatch(remote)
    if match is None:
        azure = azure_repos.parse_remote(remote)
        return azure.identity if azure else None
    return match.group(1).lower(), match.group(2).removesuffix(".git").lower()


def _common_dir(checkout: Path) -> Path:
    return Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()


def _marker_path(checkout: Path) -> Path:
    directory = _common_dir(checkout) / "gsd-path"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise MembersError(f"member marker directory must be real: {directory}")
    path = directory / "member.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MembersError(
            f"member marker is not a regular file: {path}; "
            "remove it by hand, then run members.py repair --repo <coordinator>"
        )
    return path


def _read_marker(path: Path) -> Optional[dict[str, str]]:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.is_symlink() or not path.is_file():
            raise MembersError(
                f"member marker is not a regular file: {path}; "
                "remove it by hand, then run members.py repair --repo <coordinator>"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != MARKER_KEYS
                or any(not isinstance(data[key], str) or not data[key].strip() for key in MARKER_KEYS)
                or data["schema"] != MARKER_SCHEMA
                or not Path(data["coordinator"]).is_absolute()):
            raise ValueError("unexpected content")
    except (OSError, ValueError) as error:
        raise MembersError(
            f"member marker is unreadable: {path}: {error}; "
            "run members.py repair --repo <coordinator>"
        ) from error
    return data


def _write_marker(checkout: Path, coordinator: Path, project: str, name: str) -> None:
    path = _marker_path(checkout)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = {"schema": MARKER_SCHEMA, "coordinator": str(coordinator), "project": project, "name": name}
    _common.atomic_write(path, json.dumps(marker, sort_keys=True) + "\n")


def _refuse_foreign_marker(checkout: Path, coordinator: Path, project: str, name: str) -> None:
    path = _marker_path(checkout)
    try:
        marker = _read_marker(path)
    except MembersError:
        return
    if marker is not None and (marker["project"], marker["name"]) != (project, name):
        raise MembersError(f"{checkout} is already a member of {marker['project']}")
    if marker is not None:
        try:
            role = member_role(checkout)
        except MembersError:
            return
        if role is not None and role["coordinator"] != coordinator:
            raise MembersError(f"{checkout} is already a member of {role['coordinator']}")


def marker_coordinator(checkout: Path) -> Optional[Path]:
    """The coordinator a member marker names, unverified; None without a marker."""
    marker = _read_marker(_marker_path(Path(checkout)))
    return None if marker is None else Path(marker["coordinator"])


def member_role(checkout: Path) -> Optional[dict[str, object]]:
    """The verified coordinator of a member checkout, or None outside a member."""
    checkout = Path(checkout)
    marker = _read_marker(_marker_path(checkout))
    if marker is None:
        return None
    stale = MembersError(
        f"member marker for {marker['name']} is stale; "
        "run members.py repair --repo <coordinator>"
    )
    try:
        root, state = _coordinator(Path(marker["coordinator"]))
        listed = read_members(root)
        common = _common_dir(checkout)
        for member in listed:
            if member["name"] == marker["name"] and _common_dir(Path(member["checkout"])) == common:
                if state.project != marker["project"]:
                    break
                return {"coordinator": root, "project": state.project, "name": member["name"]}
    except (MembersError, OSError) as error:
        raise stale from error
    raise stale


def detect_member(checkout: Path) -> dict[str, object]:
    """Whether a repo is a member: its coordinator, name, and whether its marker is current."""
    checkout = Path(checkout).resolve()
    try:
        named = marker_coordinator(checkout)
    except MembersError as error:
        return {"member": True, "current": False, "checkout": str(checkout), "reason": str(error)}
    if named is None:
        return {"member": False, "checkout": str(checkout)}
    try:
        role = member_role(checkout)
    except MembersError as error:
        return {"member": True, "current": False, "checkout": str(checkout),
                "coordinator": str(named), "reason": str(error)}
    return {"member": True, "current": True, "checkout": str(checkout), "coordinator": str(role["coordinator"]),
            "project": role["project"], "name": role["name"]}


def read_members(coordinator: Path) -> list[dict[str, str]]:
    """Parse MEMBERS.md; an absent file means a single-repo project."""
    path = coordinator / ".project" / MEMBERS_FILE
    if not path.exists() and not path.is_symlink():
        return []
    if path.is_symlink() or not path.is_file():
        raise MembersError("MEMBERS.md must be a regular file")
    members: list[dict[str, str]] = []
    current: Optional[dict[str, str]] = None
    preamble: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            name = line[3:].strip()
            if not NAME_RE.fullmatch(name):
                raise MembersError(f"MEMBERS.md line {number}: invalid member name {name!r}")
            current = {"name": name}
            members.append(current)
            continue
        if current is None:
            preamble.append(line)
            continue
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        field = key.lower().replace(" ", "_")
        if not separator or key not in (*FIELDS, DEFAULT_BRANCH_FIELD) or field in current:
            raise MembersError(f"MEMBERS.md line {number}: unexpected line {line!r}")
        current[field] = value.strip()
    if not preamble or preamble[0] != "# Members":
        raise MembersError("MEMBERS.md format error: expected # Members")
    rest = preamble[1:]
    while rest and not rest[0]:
        rest.pop(0)
    comment = HEADER.splitlines()[2:]
    if rest[:len(comment)] == comment:
        rest = rest[len(comment):]
    if any(rest):
        raise MembersError("MEMBERS.md format error: unexpected pre-section content")
    names = [member["name"] for member in members]
    if len(set(names)) != len(names):
        raise MembersError("MEMBERS.md repeats a member name")
    for member in members:
        missing = [field for field in FIELDS if not member.get(field.lower())]
        if missing:
            raise MembersError(f"MEMBERS.md member {member['name']} lacks {', '.join(missing)}")
        if member["integration"] not in INTEGRATIONS:
            raise MembersError(
                f"MEMBERS.md member {member['name']} has invalid Integration: {member['integration']}"
            )
        if not Path(member["checkout"]).is_absolute():
            raise MembersError(f"MEMBERS.md member {member['name']} Checkout must be absolute")
        if not _common.valid_default_branch(default_branch(member)):
            raise MembersError(
                f"MEMBERS.md member {member['name']} has invalid Default branch: {member['default_branch']}"
            )
    return members


def default_branch(member: dict[str, str]) -> str:
    """The default branch a MEMBERS.md row records; a row without the field means main."""
    return member.get("default_branch", "main")


def member_default_branch(coordinator: Path, name: str) -> str:
    """The default branch MEMBERS.md records for the member `name`."""
    for member in read_members(coordinator):
        if member["name"] == name:
            return default_branch(member)
    raise MembersError(f"member {name} is not in MEMBERS.md")


def check_member(
    coordinator: Path,
    coordinator_name: str,
    checkout: Path,
    recorded: Optional[dict[str, str]] = None,
    refuse_reserved_refs: bool = True,
) -> tuple[str, str]:
    """Refuse a checkout that cannot join; return its origin URL and default branch.

    `recorded` is the member's MEMBERS.md row. Without it (`add`), the default
    branch is the local origin/HEAD, read this one time; an unset ref means main."""
    if checkout.is_symlink() or not checkout.is_dir():
        raise MembersError(f"member checkout is not a real directory: {checkout}")
    checkout = checkout.resolve()
    if coordinator in checkout.parents or checkout in coordinator.parents:
        raise MembersError(f"member checkout is nested with the coordinator: {checkout}")
    if Path(_git(checkout, "rev-parse", "--show-toplevel")).resolve() != checkout:
        raise MembersError(f"member checkout must be its Git root: {checkout}")
    if _git(checkout, "rev-parse", "--show-superproject-working-tree"):
        raise MembersError(f"member checkout is a submodule: {checkout}")
    common = Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    coordinator_common = Path(
        _git(coordinator, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    if common.resolve() == coordinator_common.resolve():
        raise MembersError("the coordinator cannot be its own member")
    if _git(checkout, "status", "--porcelain", "--untracked-files=all"):
        raise MembersError(f"member has uncommitted changes: {checkout}")
    # The configured URL, not `get-url`, which expands local insteadOf rewrites.
    remote = _git(checkout, "config", "--get", "remote.origin.url")
    if _remote_identity(remote) is None:
        raise MembersError(f"member requires a GitHub.com origin or Azure Repos origin: {remote}")
    if recorded is not None and remote != recorded["remote"]:
        raise MembersError(f"member origin changed: {recorded['remote']} -> {remote}")
    live = _common.origin_default_branch(checkout)
    default = live if recorded is None else default_branch(recorded)
    if live != default:
        raise MembersError(
            f"member remote default must be {default}, got {live}: {checkout}; "
            "run `git remote set-head origin --auto` when the local origin/HEAD is stale"
        )
    if not _common.valid_default_branch(default):
        raise MembersError(
            f"member default branch {default!r} cannot be recorded in MEMBERS.md: it is a "
            "GSD Path reserved name or it has a character that the file cannot hold"
        )
    require_origin_branch(checkout, default)
    member_state = checkout / ".project" / "STATE.md"
    if member_state.exists() or member_state.is_symlink():
        if member_state.is_symlink() or not member_state.is_file():
            raise MembersError("member STATE.md is unreadable")
        try:
            state, _, _ = pipeline_state.load_state(checkout)
        except pipeline_state.PipelineStateError as error:
            raise MembersError(f"member STATE.md is unreadable: {error}") from error
        if state.milestone is not None and (state.phase, state.status) != ("shipped", "done"):
            raise MembersError(f"member has an active milestone: {state.milestone}")
    if refuse_reserved_refs:
        prefixes = member_ref_prefixes(coordinator_name) + tuple(
            "refs/remotes/origin/" + branch.format(_common.branch_slug(coordinator_name)) for branch in MEMBER_BRANCH_FORMATS
        )
        refs = _git(checkout, "for-each-ref", "--format=%(refname)").splitlines()
        colliding = [ref for ref in refs if ref.startswith(prefixes)]
        if colliding:
            raise MembersError("member refs collide with coordinator names: " + ", ".join(colliding))
    return remote, default


MEMBER_CLOSE_SCHEMA = "gsd-path/member-close/v1"


def member_close_path(common: Path, archive_name: str) -> Path:
    """Ship's member-close journal; outside the archive so the archive inventory is unchanged."""
    return common / "gsd-path" / "member-close" / f"{archive_name}.json"


def read_member_close(common: Path, archive_name: str) -> list[dict]:
    path = member_close_path(common, archive_name)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != MEMBER_CLOSE_SCHEMA or not isinstance(data.get("members"), list):
        raise MembersError(f"member-close journal is invalid: {path}")
    return data["members"]


def milestone_member_close(common: Path, milestone: int) -> list[dict]:
    """Member-close rows of milestone M<milestone>, whatever archive name it closed under."""
    rows: list[dict] = []
    for path in sorted((common / "gsd-path" / "member-close").glob(f"{milestone:03d}-*.json")):
        rows.extend(read_member_close(common, path.stem))
    return rows


def write_member_close(common: Path, archive_name: str, rows: list[dict]) -> None:
    path = member_close_path(common, archive_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(path, json.dumps({"schema": MEMBER_CLOSE_SCHEMA, "members": rows}, indent=2) + "\n")


def require_origin_branch(checkout: Path, branch: str) -> str:
    """The commit of the member's fetched default branch `branch`."""
    baseline = _common.run_git(
        checkout, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{commit}}"
    )
    if baseline.returncode != 0:
        raise MembersError(f"member requires refs/remotes/origin/{branch}; run git fetch origin")
    return baseline.stdout.strip()


def member_ref_prefixes(project: str) -> tuple[str, ...]:
    """Ref prefixes a member repo reserves for the coordinator named `project`."""
    return tuple(
        "refs/heads/" + branch.format(_common.branch_slug(project)) for branch in MEMBER_BRANCH_FORMATS
    ) + (MEMBER_TAG_FORMAT.format(project), f"refs/tags/milestone-source/{project}-")


def _authorization_ref(project: str, kind: str, ref: str) -> str:
    if not NAME_RE.fullmatch(project) or not ref.startswith(("refs/heads/", "refs/tags/")):
        raise MembersError(f"cannot authorize {ref} for {project}")
    return f"{AUTHORIZATION_PREFIX}{project}/{kind}/{ref}"


def _authorize(checkout: Path, project: str, kind: str, ref: str, sha: str) -> None:
    authorization = _authorization_ref(project, kind, ref)
    _git(checkout, "update-ref", authorization, sha)


def clear_authorization(checkout: Path, project: str, kind: str, ref: str) -> None:
    """Remove a push or delete authorization; S4 retirement clears a milestone's own."""
    _git(checkout, "update-ref", "-d", _authorization_ref(project, kind, ref))


def authorize_push(checkout: Path, project: str, ref: str, sha: str) -> None:
    """Authorize `ref` at exactly `sha` until cleared (a commit or tag object)."""
    _authorize(checkout, project, "push", ref, sha)


def authorize_delete(checkout: Path, project: str, ref: str, expected_remote_sha: str) -> None:
    """Allow deleting remote `ref` only while it still points at `expected_remote_sha`."""
    _authorize(checkout, project, "delete", ref, expected_remote_sha)


def authorized(checkout: Path, project: str, kind: str, ref: str, sha: str) -> bool:
    if not ref.startswith(("refs/heads/", "refs/tags/")):
        return False
    result = _common.run_git(
        checkout, "rev-parse", "--verify", "--quiet", _authorization_ref(project, kind, ref)
    )
    return result.returncode == 0 and result.stdout.strip() == sha


def _task_members(coordinator: Path) -> set[str]:
    try:
        from check_task_briefs import _frontmatter
    except ImportError:  # pragma: no cover - package import used by tests
        from scripts.check_task_briefs import _frontmatter
    named = set()
    migration = coordinator / ".project/build/coordinator-migration.json"
    if migration.exists():
        if __package__:
            from . import coordinator_migration
        else:
            import coordinator_migration
        named.add(coordinator_migration.load(coordinator)["member"])
    for task in sorted((coordinator / ".project" / "tasks").glob("*.md")):
        fields, error = _frontmatter(task.read_text(encoding="utf-8"))
        if fields is None:
            raise MembersError(f"{task.name}: {error}")
        if fields.get("repo"):
            named.add(str(fields["repo"]))
    return named


def lock_build_members(coordinator: Path) -> Optional[list[dict[str, str]]]:
    """At build start, lock the members tasks name (MEMBERS.md order) and create
    each member's bound branch at its recorded default branch on origin. None when
    no task names a member."""
    root, state = _coordinator(coordinator)
    if __package__:
        from . import isolation
    else:
        import isolation
    named = _task_members(root)
    lock_path = root / LOCK_PATH
    build_dir = lock_path.parent
    if build_dir.is_symlink() or (build_dir.exists() and not build_dir.is_dir()):
        raise MembersError(f"member lock directory must be real: {build_dir}")
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise MembersError(f"member lock must be a regular file: {lock_path}")
    try:
        isolation.require_no_member_rebase(root, "")
        isolation.validate_member_rebase(root)
    except isolation.IsolationError as error:
        raise MembersError(str(error)) from error
    if not named:
        lock_path.unlink(missing_ok=True)
        return None
    listed = read_members(root)
    unknown = sorted(named - {member["name"] for member in listed})
    if unknown:
        raise MembersError("tasks name members missing from MEMBERS.md: " + ", ".join(unknown))
    ignore_error = _common.project_ignore_error(root)
    if ignore_error:
        raise MembersError(ignore_error)
    bound = _common.BOUND_BRANCH_RE.fullmatch(state.branch or "")
    if bound is None:
        raise MembersError(f"coordinator branch is not a bound branch: {state.branch}")
    branch = f"feature/{_common.branch_slug(state.project)}-milestone-{bound.group(1)}"
    previous = {}
    if lock_path.is_file():
        previous = {entry["name"]: entry for entry in json.loads(lock_path.read_text(encoding="utf-8"))["members"]}
    entries = []
    missing = []
    for member in listed:
        if member["name"] not in named:
            continue
        checkout = Path(member["checkout"])
        role = member_role(checkout)
        if role is None or role["coordinator"] != root or role["name"] != member["name"]:
            raise MembersError(f"member marker for {member['name']} is missing or stale; "
                               f"run members.py repair --repo {root}")
        remote = _git(checkout, "config", "--get", "remote.origin.url")
        if remote != member["remote"]:
            raise MembersError(f"member origin changed: {member['remote']} -> {remote}")
        default = default_branch(member)
        base = require_origin_branch(checkout, default)
        existing = _common.run_git(checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if existing.returncode == 0:
            tip = existing.stdout.strip()
            locked = previous.get(member["name"])
            # Recovery re-entry keeps the locked base; otherwise the branch must be unused.
            if locked and locked["branch"] == branch and _common.run_git(
                checkout, "merge-base", "--is-ancestor", locked["base"], tip
            ).returncode == 0:
                base = locked["base"]
            elif _common.run_git(checkout, "merge-base", "--is-ancestor", tip, base).returncode == 0:
                base = tip
            else:
                raise MembersError(f"member {member['name']} branch {branch} has commits not on origin/{default}")
        else:
            missing.append((checkout, base))
        entries.append({"name": member["name"], "branch": branch, "base": base})
    for checkout, base in missing:
        _git(checkout, "update-ref", f"refs/heads/{branch}", base, "")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(lock_path, json.dumps({"schema": LOCK_SCHEMA, "members": entries}, indent=2) + "\n")
    return entries


def render(members: Sequence[dict[str, str]]) -> str:
    sections = [
        f"## {member['name']}\n"
        f"Checkout: {member['checkout']}\n"
        f"Remote: {member['remote']}\n"
        f"Integration: {member['integration']}\n"
        # A row that never had the field keeps none, so old rows are not rewritten.
        + (f"{DEFAULT_BRANCH_FIELD}: {member['default_branch']}\n" if "default_branch" in member else "")
        for member in members
    ]
    return "\n".join([HEADER, *sections])


def add_member(repo: Path, name: str, checkout: Path, integration: str) -> list[dict[str, str]]:
    root, state = _coordinator(repo)
    if state.phase in {"build", "ship"}:
        raise MembersError("members change only at a milestone boundary, not during build or ship")
    if not NAME_RE.fullmatch(name):
        raise MembersError(f"invalid member name: {name!r}")
    members = read_members(root)
    resolved = checkout.resolve()
    remote, default = check_member(root, state.project, checkout)
    common = Path(_git(resolved, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    identity = _remote_identity(remote)
    for member in members:
        previous = Path(member["checkout"])
        if (member["name"] == name or previous == resolved
                or _remote_identity(member["remote"]) == identity
                or Path(_git(previous, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve() == common):
            raise MembersError(f"member already recorded: {member['name']}")
    _refuse_foreign_marker(resolved, root, state.project, name)
    members.append(
        {"name": name, "checkout": str(resolved), "remote": remote, "integration": integration,
         "default_branch": default}
    )
    _write_marker(resolved, root, state.project, name)
    _common.atomic_write(root / ".project" / MEMBERS_FILE, render(members))
    return members


MEMBER_CREATE_SCHEMA = "gsd-path/member-create/v1"
VISIBILITIES = ("public", "private", "internal")


def _gh(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False,
                          env={**os.environ, "GH_HOST": "github.com"})


def create_member(repo: Path, name: str, checkout: Path, integration: str, github: str,
                  visibility: str) -> list[dict[str, str]]:
    """Greenfield member: create the approved GitHub repo, clone it, then join it.

    A journal in the coordinator Git directory keeps the approved target before any
    external action; a rerun resumes each step and never creates a second repo."""
    root, state = _coordinator(repo)
    if state.phase in {"build", "ship"}:
        raise MembersError("members change only at a milestone boundary, not during build or ship")
    if not NAME_RE.fullmatch(name) or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", github):
        raise MembersError(f"invalid member name or GitHub repository: {name!r} {github!r}")
    if visibility not in VISIBILITIES:
        raise MembersError(f"visibility must be one of {', '.join(VISIBILITIES)}")
    if checkout.is_symlink():
        raise MembersError(f"member checkout path is a symlink: {checkout}")
    resolved = checkout.resolve()
    if resolved == root or root in resolved.parents or resolved in root.parents:
        raise MembersError(f"member checkout is nested with the coordinator: {resolved}")
    if not resolved.parent.is_dir():
        raise MembersError(f"member checkout parent does not exist: {resolved.parent}")
    target = {"schema": MEMBER_CREATE_SCHEMA, "name": name, "github": github, "visibility": visibility,
              "checkout": str(resolved), "integration": integration}
    common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    journal = common / "gsd-path" / "member-create" / f"{name}.json"
    url = f"https://github.com/{github}.git"
    if journal.is_file():
        recorded = json.loads(journal.read_text(encoding="utf-8"))
        if {key: recorded.get(key) for key in target} != target or recorded.get("step") not in {"create", "clone", "join"}:
            raise MembersError(f"member-create journal for {name} holds a different approved target: {journal}")
    else:
        if any(member["name"] == name for member in read_members(root)):
            raise MembersError(f"member already recorded: {name}")
    if resolved.is_symlink() or (resolved.exists() and not resolved.is_dir()):
        raise MembersError(f"member checkout path is occupied: {resolved}")
    occupied = resolved.exists() and any(resolved.iterdir())
    if occupied and Path(_common.run_git(resolved, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != resolved:
        raise MembersError(f"member checkout path is occupied: {resolved}")
    if occupied and _git(resolved, "config", "--get", "remote.origin.url") != url:
        raise MembersError(f"member checkout {resolved} is not a clone of {url}")
    if not journal.is_file():
        journal.parent.mkdir(parents=True, exist_ok=True)
        recorded = {**target, "step": "create"}
        _common.atomic_write(journal, json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    listed = [member for member in read_members(root) if member["name"] == name]
    if listed:
        if (len(listed) != 1 or {**listed[0], "default_branch": default_branch(listed[0])} != {
                    "name": name, "checkout": str(resolved), "remote": url,
                    "integration": integration, "default_branch": "main"}
                or member_role(resolved) != {"coordinator": root, "project": state.project, "name": name}):
            raise MembersError(f"member already recorded with a different checkout or marker: {name}")
        journal.unlink()
        return read_members(root)
    viewed = _gh("repo", "view", github, "--json", "visibility")
    if viewed.returncode != 0:
        if "Could not resolve to a Repository" not in f"{viewed.stdout}\n{viewed.stderr}":
            raise MembersError(f"could not inspect {github}: {(viewed.stderr or viewed.stdout).strip()}")
        if recorded["step"] != "create":
            raise MembersError(f"GitHub repository {github} disappeared after creation; refusing to create it again")
        if occupied:
            raise MembersError(f"member checkout {resolved} is a clone of a missing repository: {github}")
        created = _gh("repo", "create", github, f"--{visibility}", "--add-readme")
        if created.returncode != 0:
            raise MembersError(f"could not create {github}: {(created.stderr or created.stdout).strip()}")
        recorded["step"] = "clone"
        _common.atomic_write(journal, json.dumps(recorded, indent=2, sort_keys=True) + "\n")
        viewed = _gh("repo", "view", github, "--json", "visibility")
    if viewed.returncode != 0:
        raise MembersError(f"could not inspect {github}: {(viewed.stderr or viewed.stdout).strip()}")
    try:
        actual_visibility = json.loads(viewed.stdout)["visibility"].lower()
    except (ValueError, KeyError, AttributeError) as error:
        raise MembersError(f"could not read {github} visibility") from error
    if actual_visibility != visibility:
        raise MembersError(f"GitHub repository visibility is {actual_visibility}, not {visibility}")
    recorded["step"] = "clone"
    _common.atomic_write(journal, json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    if occupied:
        fetched = _common.run_git(resolved, "fetch", "-q", "--prune", "origin")
        if fetched.returncode != 0:
            raise MembersError(f"could not fetch member checkout {resolved}: {(fetched.stderr or fetched.stdout).strip()}")
        default = _common.run_git(resolved, "remote", "set-head", "origin", "--auto")
        if default.returncode != 0 or _git(resolved, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") != "origin/main":
            raise MembersError(f"member remote default must be main: {resolved}")
        baseline = _common.run_git(resolved, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
        if baseline.returncode != 0:
            raise MembersError(f"member checkout {resolved} has no current origin/main")
        head = _common.run_git(resolved, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        if head.returncode == 0:
            if _common.run_git(resolved, "merge-base", "--is-ancestor", "HEAD", "origin/main").returncode != 0:
                raise MembersError(f"member checkout {resolved} has history unrelated to current origin/main")
        else:
            _git(resolved, "checkout", "-B", "main", "origin/main")
    else:
        cloned = _common.run_git(resolved.parent, "clone", "-q", url, str(resolved))
        if cloned.returncode != 0:
            raise MembersError(f"could not clone {url}: {(cloned.stderr or cloned.stdout).strip()}")
    # ponytail: --create stays main-only, because a new repository uses main;
    # `add` joins an existing repository with any default branch.
    if _common.origin_default_branch(resolved) != "main":
        raise MembersError(f"member remote default must be main: {resolved}")
    recorded["step"] = "join"
    _common.atomic_write(journal, json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    joined = add_member(root, name, resolved, integration)
    journal.unlink()
    return joined


def validate_members(repo: Path) -> list[dict[str, str]]:
    root, state = _coordinator(repo)
    members = read_members(root)
    for member in members:
        checkout = Path(member["checkout"])
        check_member(root, state.project, checkout, member, refuse_reserved_refs=False)
        try:
            role = member_role(checkout)
        except MembersError as error:
            raise MembersError(f"{error}; run members.py repair --repo {root}") from error
        if role != {"coordinator": root, "project": state.project, "name": member["name"]}:
            raise MembersError(
                f"member marker for {member['name']} is missing or stale; "
                f"run members.py repair --repo {root}"
            )
    return members


def inspection_paths(name: str) -> tuple[str, str]:
    """A member's code evidence and docs audit, relative to the project directory."""
    return f"research/evidence-codebase-{name}.md", f"research/DOCS-AUDIT-{name}.md"


def _inspection_lock(root: Path) -> dict[str, dict[str, str]]:
    """The active build lock read strictly; a malformed lock never means "not locked"."""
    path = root / LOCK_PATH
    if not path.exists() and not path.is_symlink():
        return {}
    if any(part.is_symlink() for part in (path.parent, path)) or not path.is_file():
        raise MembersError(f"member lock must be a regular file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MembersError(f"member lock is unreadable: {error}") from error
    rows = data.get("members") if isinstance(data, dict) and data.get("schema") == LOCK_SCHEMA else None
    if not isinstance(rows, list):
        raise MembersError("member lock has the wrong schema")
    locked: dict[str, dict[str, str]] = {}
    for row in rows:
        if (not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("name", "branch", "base"))
                or not NAME_RE.fullmatch(row["name"]) or row["name"] in locked
                or re.fullmatch(r"[0-9a-f]{40}", row["base"]) is None or not row["branch"]):
            raise MembersError("member lock rows must name a unique member, branch, and full base SHA")
        locked[row["name"]] = row
    return locked


def inspection_pin(root: Path, member: dict[str, str], project_dir: str = ".project") -> tuple[str, str]:
    """(pin, ref) for one member's inspection; reads only local refs and never fetches.

    The active track inspects the member's local origin default. A lookahead
    track inspects a member locked by the active build at its bound tip, since
    the next milestone builds on work that may not have reached origin yet."""
    name, checkout, default = member["name"], Path(member["checkout"]), default_branch(member)
    if PurePosixPath(project_dir) == PurePosixPath(".project/next"):
        try:
            locked = _inspection_lock(root).get(name)
        except MembersError as error:
            raise MembersError(f"member {name} inspection lock is invalid: {error}") from error
        if locked is not None:
            ref = f"refs/heads/{locked['branch']}"
            resolved = _common.run_git(checkout, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
            if resolved.returncode or not resolved.stdout.strip():
                raise MembersError(f"member {name} inspection pin is unavailable: {ref}")
            return resolved.stdout.strip(), ref
    return require_origin_branch(checkout, default), f"refs/remotes/origin/{default}"


def evidence_on_pin(checkout: Path, head: str, pin: str) -> bool:
    """Whether evidence written at `head` lies on the history of the selected `pin`.

    Equal commits count; an unresolvable head does not. Scope and coverage share
    this test so that evidence they call stale is always admissible."""
    if head == pin:
        return True
    return _common.run_git(Path(checkout), "merge-base", "--is-ancestor", head, pin).returncode == 0


def _inspection_helpers():
    if __package__:
        from . import check_handoffs, detect_project
    else:
        import check_handoffs
        import detect_project
    return check_handoffs, detect_project


def _inspect_member(root: Path, project: str, project_dir: str, member: dict[str, str]) -> dict[str, object]:
    check_handoffs, detect_project = _inspection_helpers()
    name, checkout, default = member["name"], Path(member["checkout"]), default_branch(member)
    code, docs = inspection_paths(name)
    entry: dict[str, object] = {
        "name": name, "checkout": member["checkout"], "default_branch": default, "pin": None, "pin_ref": None,
        "verdict": "unverifiable", "signal": None, "reason": None, "code_evidence": code, "docs_audit": docs,
        "evidence_head": check_handoffs.member_evidence_head(root / project_dir / code), "state": "unverifiable",
    }
    try:
        if checkout.is_symlink() or not checkout.is_dir():
            raise MembersError(f"member checkout is missing: {checkout}")
        if Path(_git(checkout, "rev-parse", "--show-toplevel")).resolve() != checkout.resolve():
            raise MembersError(f"member checkout must be its Git root: {checkout}")
        role = member_role(checkout)
        if role is None:
            raise MembersError(f"member marker for {name} is missing; run members.py repair --repo {root}")
        if role != {"coordinator": root, "project": project, "name": name}:
            raise MembersError(f"member marker for {name} names another coordinator: {role['coordinator']}")
        remote = _git(checkout, "config", "--get", "remote.origin.url")
        if remote != member["remote"]:
            raise MembersError(f"member origin changed: {member['remote']} -> {remote}")
        entry["pin"], entry["pin_ref"] = inspection_pin(root, member, project_dir)
        classified = detect_project.classify_revision(checkout, entry["pin"], first_signal=True)
    except (MembersError, detect_project.DetectError, OSError) as error:
        entry["reason"] = str(error)
        return entry
    entry["verdict"] = classified["verdict"]
    entry["signal"] = (classified["signals"] or [None])[0]
    if classified["verdict"] == "greenfield":
        entry["state"] = "greenfield"
        return entry
    problems, heads = [], []
    for kind, relative in (("codebase", code), ("docs", docs)):
        path = root / project_dir / relative
        if not path.exists() and not path.is_symlink():
            problems.append(f"missing {project_dir}/{relative}")
            continue
        try:
            heads.append(check_handoffs.validate_member_evidence(
                path, name, check_handoffs.member_evidence_head(path), member["checkout"], default, kind)["Member HEAD"])
        except check_handoffs.HandoffError as error:
            problems.append(str(error))
    off_pin = sorted({head for head in heads if not evidence_on_pin(checkout, head, str(entry["pin"]))})
    # Evidence off the selected pin's history (a squash-landed bound tip, a
    # rewritten ref, an unknown commit) cannot be stale: it is missing, so the
    # route and prepare reassign it instead of coverage blocking forever.
    problems += [f"evidence Member HEAD {head} is not on {entry['pin_ref']}" for head in off_pin]
    if problems:
        entry["state"], entry["reason"] = "missing", "; ".join(problems)
    elif any(head != entry["pin"] for head in heads):
        entry["state"] = "stale"
        entry["reason"] = f"evidence Member HEAD {', '.join(sorted(set(heads)))} differs from pin {entry['pin']}"
    else:
        entry["state"] = "covered"
    return entry


def inspection_scope(coordinator: Path, project_dir: str = ".project") -> dict[str, object]:
    """Read-only inspection scope of every member at its local origin default (never fetched).

    Unlike `validate`, a dirty team checkout does not block it, and it writes
    nothing: no marker, no lock, no ref. Without MEMBERS.md nothing is computed."""
    relative = PurePosixPath(project_dir)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise MembersError(f"project directory must stay inside the coordinator: {project_dir}")
    scope: dict[str, object] = {"project_dir": str(relative), "coordinator": None, "members": []}
    path = Path(coordinator) / ".project" / MEMBERS_FILE
    if not path.exists() and not path.is_symlink():
        return scope
    root, state = _coordinator(coordinator)
    listed = read_members(root)
    _, detect_project = _inspection_helpers()
    try:
        classified = detect_project.classify_revision(root, "HEAD", first_signal=True)
    except detect_project.DetectError as error:
        raise MembersError(f"coordinator: {error}") from error
    scope["coordinator"] = {
        "checkout": str(root), "head": classified["revision"], "verdict": classified["verdict"],
        "signal": (classified["signals"] or [None])[0],
    }
    scope["members"] = [_inspect_member(root, state.project, str(relative), member) for member in listed]
    return scope


def repair_members(repo: Path) -> list[dict[str, str]]:
    """Rewrite missing or stale markers; never take over another coordinator's member."""
    root, state = _coordinator(repo)
    members = read_members(root)
    for member in members:
        checkout = Path(member["checkout"])
        check_member(root, state.project, checkout, member, refuse_reserved_refs=False)
        _refuse_foreign_marker(checkout.resolve(), root, state.project, member["name"])
    for member in members:
        checkout = Path(member["checkout"]).resolve()
        try:
            role = member_role(checkout)
        except MembersError:
            role = None
        if role != {"coordinator": root, "project": state.project, "name": member["name"]}:
            _write_marker(checkout, root, state.project, member["name"])
    return members


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="record one member repository")
    add.add_argument("--repo", required=True, type=Path, help="coordinator Git root")
    add.add_argument("--name", required=True)
    add.add_argument("--checkout", required=True, type=Path)
    add.add_argument("--integration", choices=INTEGRATIONS, default="default")
    add.add_argument("--create", metavar="OWNER/NAME",
                     help="greenfield: create this GitHub repo and clone it to --checkout first")
    add.add_argument("--visibility", choices=VISIBILITIES, help="visibility for --create")
    validate = commands.add_parser("validate", help="check MEMBERS.md and every member")
    validate.add_argument("--repo", required=True, type=Path, help="coordinator Git root")
    detect = commands.add_parser("detect", help="report whether a repo is a member, and of which coordinator")
    detect.add_argument("--checkout", required=True, type=Path, help="repository to inspect")
    repair = commands.add_parser("repair", help="rewrite missing or stale member markers")
    repair.add_argument("--repo", required=True, type=Path, help="coordinator Git root")
    inspection = commands.add_parser(
        "inspection", help="read-only member inspection scope at local origin defaults (no fetch, no writes)")
    inspection.add_argument("--repo", required=True, type=Path, help="coordinator Git root")
    inspection.add_argument("--project-dir", default=".project", help="track holding research/ (default: .project)")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "detect":
            print(json.dumps(detect_member(arguments.checkout), sort_keys=True))
            return 0
        if arguments.command == "inspection":
            print(json.dumps(inspection_scope(arguments.repo, arguments.project_dir), sort_keys=True))
            return 0
        if arguments.command == "add" and arguments.create:
            if arguments.visibility is None:
                raise MembersError("--create requires --visibility")
            members = create_member(arguments.repo, arguments.name, arguments.checkout,
                                    arguments.integration, arguments.create, arguments.visibility)
        elif arguments.command == "add":
            if arguments.visibility is not None:
                raise MembersError("--visibility requires --create")
            members = add_member(
                arguments.repo, arguments.name, arguments.checkout, arguments.integration
            )
        elif arguments.command == "repair":
            members = repair_members(arguments.repo)
        else:
            members = validate_members(arguments.repo)
    except (MembersError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"members": members}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
