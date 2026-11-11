#!/usr/bin/env python3
"""Announce a fork release and install it into consumer projects (fork-only).

  announce                Record the checkout's release commit in
                          ~/.gsd-path/fork-release.json. Run it on the
                          `chore(fork): release X.Y.Z.N` commit.
  apply --project PATH    Upgrade one Claude consumer to the announced release
                          at a milestone boundary: backup, runtime upgrade with
                          provenance, skills update, checks, and one commit of
                          installation paths only.

`apply` installs from a `git archive` of the announced commit, never from the
working checkout, so unreleased work cannot reach a consumer. It refuses before
any change unless the consumer is inside the window that
scripts/upgrade_window.py reports: STATE at inspect|define|research|decide|roadmap
with status active, no linked worktree, nothing staged, and no dirty path outside
.project/.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import git_guard  # noqa: E402
import upgrade_window  # noqa: E402

RELEASE_SUBJECT = "chore(fork): release {version}"
COMMIT_SUBJECT = "chore(gsd-path): upgrade runtime and skills to {version}"
FORK_VERSION = re.compile(r"\d+\.\d+\.\d+\.\d+")
# Hosts other than Claude are not handled: every current consumer is Claude-only,
# and a wrong host root would half-upgrade the project.
OTHER_SKILL_ROOTS = tuple(root for root in git_guard.INSTALL_SKILL_ROOTS if root != ".claude/skills")
BACKUP_PATHS = (".claude/skills", ".gsd-path", "AGENTS.md", "WORKFLOW.md", ".claude/CLAUDE.md",
                ".claude/settings.json")


class UpgradeError(Exception):
    pass


def git(repo, *args, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise UpgradeError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout


def changed_paths(repo):
    """(staged, every changed path) from porcelain v1, renames counted on both sides."""
    staged, paths = [], []
    entries = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if code[0] not in " ?":
            staged.append(path)
        if code[0] in "RC":
            paths.append(entries[index])
            index += 1
    return staged, paths


def https_source(url):
    match = re.fullmatch(r"(?:git@|ssh://git@)github\.com[:/](.+?)(?:\.git)?/?", url.strip())
    if match:
        return f"https://github.com/{match.group(1)}"
    return re.sub(r"\.git/?$", "", url.strip())


def announce(arguments):
    staged, paths = changed_paths(ROOT)
    if paths:
        raise UpgradeError("the fork checkout has uncommitted changes: " + ", ".join(sorted(set(paths))[:10]))
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    if not FORK_VERSION.fullmatch(version):
        raise UpgradeError(f"package.json version {version!r} is not a fork X.Y.Z.N version")
    subject = git(ROOT, "log", "-1", "--format=%s").strip()
    expected = RELEASE_SUBJECT.format(version=version)
    if subject != expected:
        raise UpgradeError(f"HEAD is {subject!r}, not the release commit {expected!r}")
    commit = git(ROOT, "rev-parse", "HEAD").strip()
    published = bool(git(ROOT, "branch", "-r", "--contains", commit, "--list", "origin/*", check=False).strip())
    release = {
        "schema": upgrade_window.RELEASE_SCHEMA,
        "version": version,
        "commit": commit,
        "checkout": str(ROOT),
        "source": https_source(git(ROOT, "remote", "get-url", "origin").strip()),
        "announced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    target = upgrade_window.release_path()
    if not arguments.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    return {"status": "dry-run" if arguments.dry_run else "announced", "file": str(target),
            "published": published, **release}


def consumer_status(project):
    """The consumer's own status runtime JSON (its stdout may carry progress lines)."""
    result = subprocess.run([sys.executable, "-B", str(project / ".gsd-path" / "status_runtime.py"),
                             "--repo", str(project)], capture_output=True, encoding="utf-8", errors="replace")
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if result.returncode != 0 or not lines:
        raise UpgradeError(f"status runtime failed: {(result.stderr or result.stdout).strip()[-500:]}")
    return json.loads(lines[-1])


def preflight(project, release):
    if not (project / ".project" / "STATE.md").is_file():
        raise UpgradeError(f"{project} has no .project/STATE.md")
    top = Path(git(project, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != project:
        raise UpgradeError(f"{project} is not the root of its Git checkout ({top})")
    skill_dir = project / ".claude" / "skills" / "gsd-path"
    if not (skill_dir / "VERSION").is_file():
        raise UpgradeError(f"no Claude project install at {skill_dir}")
    others = [root for root in OTHER_SKILL_ROOTS if (project / root / "gsd-path").exists()]
    if others:
        raise UpgradeError("only Claude project installs are supported; also found " + ", ".join(others))
    if git(ROOT, "cat-file", "-t", release["commit"], check=False).strip() != "commit":
        raise UpgradeError(f"release commit {release['commit']} is missing from {ROOT}")
    installed = upgrade_window.installed_versions(project, skill_dir)
    target = upgrade_window.parse_version(release["version"])
    behind = [name for name, value in installed.items()
              if (upgrade_window.parse_version(value) or (0,) * 4) < target]
    status = consumer_status(project)
    state = status.get("state") or {}
    problem = upgrade_window.window_problem(project)
    if problem:
        raise UpgradeError(problem)
    if state.get("phase") not in upgrade_window.WINDOW_PHASES or state.get("status") != "active":
        raise UpgradeError(f"status runtime reports {state.get('phase')}/{state.get('status')}")
    staged, paths = changed_paths(project)
    if staged:
        raise UpgradeError("the consumer index has staged changes: " + ", ".join(staged))
    outside = sorted({path for path in paths if not path.startswith(".project/")})
    if outside:
        raise UpgradeError("the consumer has uncommitted changes outside .project/: " + ", ".join(outside))
    return {"installed": installed, "behind": behind, "state": state, "status": status,
            "head": git(project, "rev-parse", "HEAD").strip()}


def backup(project, release, checked):
    stamp = time.strftime("%Y%m%dT%H%M%S")
    directory = Path.home() / ".gsd-path" / "preserved" / project.name / f"upgrade-{release['version']}-{stamp}"
    directory.mkdir(parents=True)
    (directory / "head.txt").write_text(checked["head"] + "\n", encoding="utf-8")
    (directory / "status.json").write_text(json.dumps(checked["status"], indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    runtime = project / ".gsd-path" / "runtime.json"
    if runtime.is_file():
        shutil.copy2(runtime, directory / "runtime.json.before")
    with tarfile.open(directory / "install-paths.tar", "w") as archive:
        for name in BACKUP_PATHS:
            if (project / name).exists():
                archive.add(project / name, arcname=name)
    return directory


def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise UpgradeError(f"{' '.join(map(str, command[:3]))} failed:\n{(result.stdout + result.stderr).strip()[-2000:]}")
    return result.stdout


def verify(project, release):
    problems = []
    for skill in ("gsd-path", "path"):
        stamp = project / ".claude" / "skills" / skill / "VERSION"
        found = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else None
        if found != release["version"]:
            problems.append(f".claude/skills/{skill}/VERSION is {found!r}")
    pin = json.loads((project / ".gsd-path" / "runtime.json").read_text(encoding="utf-8"))
    if pin.get("version") != release["version"]:
        problems.append(f"runtime.json version is {pin.get('version')!r}")
    if (pin.get("provenance") or {}).get("patch_ref") != release["commit"]:
        problems.append(f"runtime.json provenance patch_ref is {(pin.get('provenance') or {}).get('patch_ref')!r}")
    _, paths = changed_paths(project)
    install = sorted({path for path in paths if not path.startswith(".project/")})
    foreign = [path for path in install if not git_guard.installation_path(path)]
    if foreign:
        problems.append("the install changed non-installation paths: " + ", ".join(foreign))
    return problems, install


def apply(arguments):
    project = arguments.project.resolve()
    release = upgrade_window.read_release()
    if release is None:
        raise UpgradeError(f"no release announcement at {upgrade_window.release_path()}; run announce first")
    checked = preflight(project, release)
    report = {"project": str(project), "release": release["version"], "commit": release["commit"],
              "before": checked["installed"], "state": f"{checked['state'].get('phase')}/{checked['state'].get('status')}"}
    if not checked["behind"]:
        return {**report, "status": "current"}
    if arguments.dry_run:
        return {**report, "status": "dry-run", "behind": checked["behind"]}
    directory = backup(project, release, checked)
    report["backup"] = str(directory)
    with tempfile.TemporaryDirectory(prefix="gsd-path-release-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        archive = Path(temporary) / "release.tar"
        git(ROOT, "archive", "--format=tar", "-o", str(archive), release["commit"])
        with tarfile.open(archive) as bundle:
            if hasattr(tarfile, "data_filter"):
                bundle.extractall(source, filter="data")
            else:
                bundle.extractall(source)
        phase = checked["state"].get("phase")
        run([sys.executable, "-B", str(source / "scripts" / "install.py"), "--project", str(project),
             "--runtime-upgrade", "--runtime-provenance-source", release["source"],
             "--runtime-provenance-patch", release["commit"],
             "--runtime-provenance-note", f"fork {release['version']}: milestone boundary, {phase}/active"],
            cwd=project)
        run(["node", str(source / "scripts" / "install.mjs"), "--update", "--claude",
             "--claude-root", str(project / ".claude" / "skills"), "--project", str(project)], cwd=project)
    disabled = project / ".claude" / "disabled-gsd-skills"
    if disabled.exists():
        shutil.move(str(disabled), str(directory / "disabled-gsd-skills"))
    problems, install = verify(project, release)
    if problems:
        raise UpgradeError("installed but not committed; restore from " + str(directory)
                           + "/install-paths.tar if needed:\n- " + "\n- ".join(problems))
    git(project, "add", "-A", "--", *install)
    subject = COMMIT_SUBJECT.format(version=release["version"])
    result = subprocess.run(["git", "-C", str(project), "commit", "-m", subject],
                            capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise UpgradeError("installed and staged but the commit failed (backup " + str(directory) + "):\n"
                           + (result.stdout + result.stderr).strip()[-2000:])
    return {**report, "status": "upgraded", "paths": len(install),
            "upgrade_commit": git(project, "rev-parse", "HEAD").strip(),
            "next": "restart the project's agent session, then continue with /path"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    announce_parser = commands.add_parser("announce")
    announce_parser.add_argument("--dry-run", action="store_true")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--project", required=True, type=Path)
    apply_parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        report = announce(arguments) if arguments.command == "announce" else apply(arguments)
    except (UpgradeError, OSError, ValueError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
