#!/usr/bin/env python3
"""Report whether a newer fork release can be installed at a milestone boundary.

Fork-only. The fork's release step writes ``~/.gsd-path/fork-release.json``
from its source checkout (``scripts/dev/consumer_upgrade.py announce``). This
helper compares that release with the router skill's VERSION stamp and the
project's ``.gsd-path/runtime.json`` pin, and checks that the project is still
inside the installer-commit window with no phase work started.

It is read-only and always exits 0 with one JSON object, so the router can run
it once after a milestone boundary transition and stop only on
``status: available``. Every other status, including ``error``, means "route
as usual".
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "gsd-path/upgrade-window/v1"
RELEASE_SCHEMA = "gsd-path/fork-release/v1"
# Phases where git_guard accepts an installer commit and no plan approval can
# exist yet for the milestone (plan itself is excluded: its approval closes
# the window, and a promoted lookahead plan may already be approved).
WINDOW_PHASES = ("inspect", "define", "research", "decide", "roadmap")
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"\d+(?:\.\d+){2,3}")
FIELD = re.compile(r"^([a-z_]+):\s*(.*)$")


def release_path():
    return Path.home() / ".gsd-path" / "fork-release.json"


def parse_version(value):
    """Return a comparable 4-tuple for ``X.Y.Z`` or ``X.Y.Z.N``, else None."""
    if not isinstance(value, str) or not VERSION.fullmatch(value.strip()):
        return None
    parts = tuple(int(part) for part in value.strip().split("."))
    return parts + (0,) * (4 - len(parts))


def read_release(path=None):
    """The announced release, or None when no announcement exists.

    Raises ValueError for an announcement that exists but is malformed.
    """
    path = path or release_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema") != RELEASE_SCHEMA
            or parse_version(data.get("version")) is None
            or not isinstance(data.get("commit"), str) or not SHA.fullmatch(data["commit"])
            or not isinstance(data.get("checkout"), str) or not Path(data["checkout"]).is_absolute()):
        raise ValueError(f"invalid fork release announcement: {path}")
    return data


def state_fields(repo):
    """Top-level ``key: value`` fields of .project/STATE.md front matter."""
    text = (repo / ".project" / "STATE.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("STATE.md has no front matter")
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        match = FIELD.match(line)
        if match:
            fields[match.group(1)] = match.group(2).split(" #", 1)[0].strip().strip('"')
    raise ValueError("STATE.md front matter is not closed")


def linked_worktrees(repo):
    """Paths of every worktree except the primary one."""
    listing = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    paths = [line[len("worktree "):] for line in listing.splitlines() if line.startswith("worktree ")]
    return paths[1:]


def installed_versions(repo, skill_dir):
    skills = runtime = None
    try:
        skills = (skill_dir / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:
        runtime = json.loads((repo / ".gsd-path" / "runtime.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        pass
    return {"skills": skills, "runtime": runtime}


def window_problem(repo):
    """Why an installer upgrade cannot happen now, or None when it can."""
    fields = state_fields(repo)
    phase, status = fields.get("phase"), fields.get("status")
    if phase not in WINDOW_PHASES or status != "active":
        return f"STATE is {phase}/{status}; the upgrade window is {'|'.join(WINDOW_PHASES)} with status active"
    worktrees = linked_worktrees(repo)
    if worktrees:
        return "linked worktrees exist, so phase work has started: " + ", ".join(worktrees)
    return None


def upgrade_command(release, repo):
    script = Path(release["checkout"]) / "scripts" / "dev" / "consumer_upgrade.py"
    return f"python3 {script} apply --project {repo}"


def check(repo, skill_dir, release_file=None):
    report = {"schema": SCHEMA, "repo": str(repo)}
    try:
        release = read_release(release_file)
        if release is None:
            return {**report, "status": "none", "reason": "no fork release announcement"}
        installed = installed_versions(repo, skill_dir)
        report.update(installed=installed, release={key: release[key] for key in ("version", "commit")})
        if all(value is None for value in installed.values()):
            return {**report, "status": "error", "reason": "no installed VERSION stamp or runtime pin"}
        target = parse_version(release["version"])
        behind = [name for name, value in installed.items()
                  if value is not None and (parse_version(value) or (0,) * 4) < target]
        if not behind:
            return {**report, "status": "current"}
        problem = window_problem(repo)
        if problem:
            return {**report, "status": "closed", "behind": behind, "reason": problem}
        return {**report, "status": "available", "behind": behind,
                "command": upgrade_command(release, repo)}
    except Exception as error:  # advisory: any failure routes as usual
        return {**report, "status": "error", "reason": f"{type(error).__name__}: {error}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent,
                        help=argparse.SUPPRESS)
    parser.add_argument("--release-file", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    report = check(arguments.repo.resolve(), arguments.skill_dir, arguments.release_file)
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
