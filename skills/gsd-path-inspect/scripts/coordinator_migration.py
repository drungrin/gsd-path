#!/usr/bin/env python3
# gsd-path project runtime
"""Journaled single-repo to dedicated-coordinator migration at quiet ship.

Original commits and recorded verification stay in the coordinator. The code
member receives a projection of the original product history, with an explicit
commit map. No recorded command is relabelled as an execution in the new repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.dont_write_bytecode = True
if __package__:
    from . import _common
else:
    import _common

SCHEMA = "gsd-path/coordinator-migration/v1"
RECEIPT = "build/coordinator-migration.json"
JOURNAL = "gsd-path/coordinator-migration.json"
METADATA = frozenset({".project", ".planning", ".gsd-path", ".claude", ".codex", ".agents",
                      "AGENTS.md", "WORKFLOW.md", "CLAUDE.md"})
SHA = re.compile(r"[0-9a-f]{40}")


class MigrationError(ValueError):
    pass


def _git(repo: Path, *args: str, data: Optional[bytes] = None, env: Optional[dict] = None) -> bytes:
    run = subprocess.run(_common.git_command(repo, *args), input=data, capture_output=True, env=env)
    if run.returncode:
        raise MigrationError(run.stderr.decode("utf-8", "replace").strip() or "git " + " ".join(args) + " failed")
    return run.stdout


def _text(repo: Path, *args: str) -> str:
    return _git(repo, *args).decode("utf-8", "surrogateescape").strip()


def _modules():
    if __package__:
        from . import build_state, isolation, members, pipeline_state
    else:
        import build_state, isolation, members, pipeline_state
    return build_state, isolation, members, pipeline_state


def _write(path: Path, data: dict) -> None:
    _common.atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _journal(repo: Path) -> Path:
    return Path(_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / JOURNAL


def load(repo: Path, project_dir: str = ".project") -> Optional[dict]:
    path = repo / project_dir / RECEIPT
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents if parent != repo and repo in parent.parents):
        raise MigrationError("coordinator migration receipt must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise MigrationError("unreadable coordinator migration receipt") from error
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or not isinstance(value.get("mapping"), dict) or not isinstance(value.get("tasks"), dict)
            or not isinstance(value.get("member"), str) or not value.get("ruling")):
        raise MigrationError("invalid coordinator migration receipt")
    return value


def effective_repo(repo: Path, task_id: str, declared: Optional[str], project_dir: str = ".project") -> Optional[str]:
    if declared:
        return declared
    receipt = load(repo, project_dir)
    return receipt["member"] if receipt and task_id in receipt["tasks"] else None


def projection(repo: Path, revision: str, metadata: bool = False, write: bool = False) -> str:
    """Hash a top-level tree projection; validation never writes Git objects."""
    raw = bytearray()
    for entry in _git(repo, "ls-tree", "-z", revision).split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, _, oid = header.split(b" ", 2)
        selected = name.decode("utf-8", "surrogateescape") in METADATA
        if selected != metadata:
            continue
        raw.extend(mode.lstrip(b"0") + b" " + name + b"\0" + bytes.fromhex(oid.decode()))
    result = hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if write:
        written = _git(repo, "hash-object", "-w", "-t", "tree", "--stdin", data=bytes(raw)).decode().strip()
        if written != result:
            raise MigrationError("tree projection hash mismatch")
    return result


def _history(repo: Path, base: str, head: str) -> list[str]:
    rows = _text(repo, "rev-list", "--first-parent", "--reverse", "--parents", f"{base}..{head}").splitlines()
    previous, result = base, []
    for line in rows:
        fields = line.split()
        if len(fields) != 2 or fields[1] != previous:
            raise MigrationError("migration requires linear milestone history")
        result.append(fields[0])
        previous = fields[0]
    if previous != head:
        raise MigrationError("source base does not lead to the milestone head")
    return result


def _remote_ref(repo: Path, remote: str, ref: str) -> Optional[str]:
    rows = _text(repo, "ls-remote", remote, ref).splitlines()
    matches = [row.split("\t")[0] for row in rows if row.split("\t")[-1] == ref]
    if len(matches) > 1:
        raise MigrationError("ambiguous remote ref: " + ref)
    return matches[0] if matches else None


def _require_clean_history(repo: Path, refs: list[str]) -> None:
    if not refs:
        return
    found = _text(repo, "log", "--format=%H", *refs, "--", *sorted(METADATA))
    if found:
        raise MigrationError("product history contains pipeline metadata: " + found.splitlines()[0])


def _audit_remote(coordinator: Path, spec: dict) -> dict:
    """Audit in the evidence repo, never import mixed backup commits into code."""
    _git(coordinator, "fetch", "--quiet", "--prune", "--no-tags", spec["source_origin"],
         "+refs/heads/*:refs/migration-audit/heads/*", "+refs/tags/*:refs/migration-audit/tags/*")
    refs = _text(coordinator, "for-each-ref", "--format=%(refname) %(objectname)", "refs/migration-audit/").splitlines()
    audited = {}
    clean = []
    for row in refs:
        name, oid = row.split(" ", 1)
        original = name.replace("refs/migration-audit/", "refs/", 1)
        audited[original] = oid
        if original != spec["backup_ref"]:
            clean.append(name)
    _require_clean_history(coordinator, clean)
    return audited


def preview(source: Path, coordinator: Path, code: Path, remote: str, project: str,
            backup_branch: str, ruling: str, expected_head: str, extra_members: Optional[list[dict]] = None) -> dict:
    source, coordinator, code = source.resolve(), coordinator.absolute(), code.absolute()
    build_state, isolation, members, state_module = _modules()
    state, state_text, _ = state_module.load_state(source)
    if (state.phase, state.status, state.archive) != ("ship", "active", None):
        raise MigrationError("migration requires ship/active before archive preparation")
    if (source / ".project/MEMBERS.md").exists() or (source / ".project/next").exists():
        raise MigrationError("migration requires a single-repo milestone without lookahead")
    if (source / ".project/review/FINAL.md").exists() or list((source / ".project/review").glob("final-gap-*.md")):
        raise MigrationError("migration must precede final-review execution")
    if not ruling.strip() or not SHA.fullmatch(expected_head):
        raise MigrationError("migration requires an owner ruling and exact expected head")
    if _text(source, "rev-parse", "HEAD") != expected_head or _text(source, "branch", "--show-current") != state.branch:
        raise MigrationError("source branch or head differs from the approved snapshot")
    dirty = isolation.uncommitted_paths(source)
    if dirty - {".project/STATE.md"}:
        raise MigrationError("source has uncommitted changes outside STATE.md")
    if state_module._pending_discussion_block(source, "ship"):
        raise MigrationError("source has pending discussion rulings")
    status = state_module.status_state(source)
    if any(value for value in status["journals"].values()):
        raise MigrationError("source has an unfinished transaction")
    worktrees = _text(source, "worktree", "list", "--porcelain").split("\n\n")
    if len(worktrees) != 1:
        raise MigrationError("source has retained worktrees")
    if source.parent != coordinator.parent or source.parent != code.parent or coordinator == code:
        raise MigrationError("source, coordinator and code must be distinct sibling checkouts")
    if coordinator.exists() or code.exists():
        raise MigrationError("migration target already exists")
    if not members.NAME_RE.fullmatch(project):
        raise MigrationError("invalid coordinator project name")
    proof = build_state.verify_landed_tasks(str(source), ".project", expected_head)
    origin = _text(source, "config", "--get", "remote.origin.url")
    cached_default = f"origin/{state.default_branch}"
    base = _text(source, "merge-base", expected_head, cached_default)
    _history(source, base, expected_head)
    live_main = _remote_ref(source, "origin", "refs/heads/" + state.default_branch)
    backup_ref = "refs/heads/" + backup_branch
    if not backup_branch.startswith("chore/"):
        raise MigrationError("only an explicitly named chore backup branch may be rewritten")
    backup = _remote_ref(source, "origin", backup_ref)
    if not live_main or not backup:
        raise MigrationError("source main or approved backup branch is missing")
    listed = []
    for item in extra_members or []:
        if not members.NAME_RE.fullmatch(item["name"]) or item["name"] == "code" or any(row["name"] == item["name"] for row in listed):
            raise MigrationError("invalid or duplicate migration member name")
        checkout = Path(item["checkout"]).resolve()
        if checkout in {source, coordinator, code}:
            raise MigrationError("duplicate migration member")
        member_remote, default = members.check_member(source, project, checkout)
        listed.append({"name": item["name"], "checkout": str(checkout), "remote": member_remote,
                       "default_branch": default, "integration": "pull-request"})
    return {"schema": SCHEMA, "status": "preview", "source": str(source), "source_head": expected_head,
            "source_base": base, "source_state": state_text, "source_origin": origin,
            "source_project": state.project, "coordinator": str(coordinator), "code": str(code),
            "coordinator_remote": remote, "project": project, "branch": state.branch,
            "default_branch": state.default_branch, "live_main": live_main,
            "backup_ref": backup_ref, "backup_old": backup, "ruling": ruling,
            "extra_members": listed, "tasks": {r["task"]["id"]: {"commit": r["landed_commit"],
                         "base": r["task"]["base"], "path": r["task"]["task_file"]} for r in proof["tasks"]}}


def _commit(repo: Path, tree: str, parent: Optional[str], subject: str, env: Optional[dict] = None,
            *, extra_parent: Optional[str] = None) -> str:
    return _git(repo, "commit-tree", tree, *(["-p", parent] if parent else []),
                *(["-p", extra_parent] if extra_parent else []), "-m", subject,
                env=env).decode().strip()


def _copy_rewrites(source: Path, destination: Path) -> None:
    result = subprocess.run(_common.git_command(source, "config", "--get-regexp", r"^url\..*\.insteadof$"),
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        name, value = line.split(" ", 1)
        _git(destination, "config", name, value)


def prepare(spec: dict) -> dict:
    """Create independent local projections. The original checkout is untouched."""
    coordinator, code, source = (Path(spec[key]) for key in ("coordinator", "code", "source"))
    if coordinator.exists():
        journal_path = _journal(coordinator)
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if journal["spec"] != spec:
                raise MigrationError("existing migration journal names a different snapshot")
            if journal["stage"] in {"prepared", "preserved", "published", "complete"}:
                validate(coordinator, allow_pending=True)
                return journal
        raise MigrationError("incomplete preparation retained; inspect its journal before recovery")
    _source_unchanged(spec)
    coordinator.mkdir()
    # An ownership marker precedes even Git initialization.
    marker = coordinator / ".coordinator-migration.json"
    _write(marker, {"schema": SCHEMA, "spec": spec, "stage": "initializing"})
    _git(coordinator, "init", "-q", "-b", spec["branch"])
    journal = {"schema": SCHEMA, "spec": spec, "stage": "preparing"}
    _write(_journal(coordinator), journal)
    marker.unlink()
    _copy_rewrites(source, coordinator)
    for key in ("user.name", "user.email"):
        _git(coordinator, "config", key, _text(source, "config", "--get", key))
    _git(coordinator, "remote", "add", "origin", spec["coordinator_remote"])
    _git(coordinator, "fetch", "--quiet", "--no-tags", str(source), spec["source_head"])
    journal["audited_product_refs"] = _audit_remote(coordinator, spec)
    _git(coordinator, "update-ref", "refs/heads/" + spec["branch"], spec["source_head"], "")
    _git(coordinator, "read-tree", projection(coordinator, spec["source_head"], metadata=True, write=True))
    _git(coordinator, "checkout-index", "-a")
    # This is a new Git database, not an alternate object store tied to source.
    transport = _text(source, "remote", "get-url", "origin")
    _git(coordinator, "clone", "--quiet", "--single-branch", "--branch", spec["default_branch"],
         "--no-tags", transport, str(code))
    _git(code, "remote", "set-url", "origin", spec["source_origin"])
    _copy_rewrites(source, code)
    for key in ("user.name", "user.email"):
        _git(code, "config", key, _text(source, "config", "--get", key))
    if _text(code, "rev-parse", "HEAD") != spec["live_main"]:
        raise MigrationError("product main advanced after preview; preserve staging and preview again")
    _require_clean_history(code, ["HEAD"])
    if _text(code, "merge-base", spec["source_base"], "HEAD") != spec["source_base"]:
        raise MigrationError("source base is not in the clean product main history")
    chain = [spec["source_base"], *_history(coordinator, spec["source_base"], spec["source_head"])]
    trees = {old: projection(coordinator, old, write=True) for old in chain}
    # Transfer trees and blobs only. Original mixed commits never enter code.
    packed = _git(coordinator, "pack-objects", "--stdout", "--revs", data=("\n".join(sorted(set(trees.values()))) + "\n").encode())
    _git(code, "unpack-objects", "-q", data=packed)
    mapping = {chain[0]: chain[0]}
    for old, previous in zip(chain[1:], chain):
        parent = mapping[previous]
        if trees[old] == _text(code, "rev-parse", parent + "^{tree}"):
            mapping[old] = parent
            continue
        fields = _git(coordinator, "show", "-s", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%s", old).decode().strip().split("\0")
        environment = os.environ.copy()
        for key, value in zip(("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"), fields[:6]):
            environment[key] = value
        subject = _common.without_tool_attribution(fields[6]).strip()
        if not subject:
            raise MigrationError("source product commit has no attribution-free subject")
        mapping[old] = _commit(code, trees[old], parent, subject, environment)
    bound = f"feature/{_common.branch_slug(spec['project'])}-milestone-{spec['branch'].rsplit('-', 1)[1]}"
    _git(code, "update-ref", "refs/heads/" + bound, mapping[spec["source_head"]], "")
    receipt = {"schema": SCHEMA, "ruling": spec["ruling"], "source_head": spec["source_head"],
               "source_base": spec["source_base"], "source_project": spec["source_project"],
               "source_path": spec["source"], "member": "code", "mapping": mapping,
               "tasks": spec["tasks"], "product_tree": trees[spec["source_head"]],
               "export_head": mapping[spec["source_head"]], "retention_tag": "migration/" + spec["branch"].split('/')[-1] + "-" + spec["source_head"][:12]}
    _, _, members, state_module = _modules()
    state_text = state_module._set_frontmatter(spec["source_state"], {"project": spec["project"],
                    "integration_default": "direct", "integration": "direct", "integration_source": "default"})
    state_text = state_module._append_event(state_text, "ship", "owner-approved coordinator migration; ruling verbatim: " + spec["ruling"])
    (coordinator / ".project/STATE.md").write_text(state_text, encoding="utf-8")
    listed = [{"name": "code", "checkout": str(code), "remote": spec["source_origin"],
               "default_branch": spec["default_branch"], "integration": "pull-request"}, *spec["extra_members"]]
    (coordinator / ".project/MEMBERS.md").write_text(members.render(listed), encoding="utf-8")
    _write(coordinator / ".project/build/members.json", {"schema": members.LOCK_SCHEMA,
         "members": [{"name": "code", "branch": bound, "base": spec["source_base"]}]})
    _write(coordinator / ".project" / RECEIPT, receipt)
    plan = coordinator / ".project/plan/PLAN.md"
    plan_text = plan.read_text(encoding="utf-8")
    command = re.search(r"(?m)^Project verify:[ \t]*`([^`\n]+)`[ \t]*$", plan_text)
    if command is None:
        raise MigrationError("PLAN.md must have one inline Project verify command")
    receipt["original_verify"] = command.group(1)
    plan_text = plan_text[:command.start()] + "Project verify: `cd ../code && (" + command.group(1) + ")`" + plan_text[command.end():]
    plan_text += ("\n## Coordinator migration\n\nOriginal completed task contracts and verification output are preserved. "
                  "Their product paths and surface commands belong to member `code`; new product repair tasks use `repo: code`. "
                  "The commit projection and owner ruling are recorded in `build/coordinator-migration.json`.\n")
    plan.write_text(plan_text, encoding="utf-8")
    for relative in ("intent/INTENT.md", "research/SYNTHESIS.md"):
        path = coordinator / ".project" / relative
        content = path.read_text(encoding="utf-8")
        content += ("\n## Coordinator migration ruling\n\nOwner ruling, verbatim: " + spec["ruling"] + "\n\n"
                    "This approved repository-layout correction supersedes the earlier single-repo closeout and deferred member setup. "
                    "Success-criterion text and product scope are unchanged. Product paths refer to member `code`. "
                    "The coordinator owns planning, evidence and archive; only code participates in this milestone. "
                    "Code closes through a user-completed PR under the existing branch policy; the new coordinator uses direct integration. "
                    "Published code history must contain no pipeline metadata; its original mixed history is retained in the coordinator. "
                    "Only the recorded backup branch may be rewritten, under its exact previous-SHA lease.\n")
        path.write_text(content, encoding="utf-8")
    receipt["artifacts"] = {}
    for path in sorted((coordinator / ".project").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(coordinator).as_posix()
        if relative.startswith((".project/tasks/", ".project/review/")) or relative == ".project/build/verify-ledger.jsonl":
            receipt["artifacts"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write(coordinator / ".project" / RECEIPT, receipt)
    # Retain the clean projection graph in the coordinator as well. Validation
    # stays possible after the code member's source branch has been retired.
    packed = _git(code, "pack-objects", "--stdout", "--revs", data=(receipt["export_head"] + "\n^" + spec["source_base"] + "\n").encode())
    _git(coordinator, "unpack-objects", "-q", data=packed)
    _git(coordinator, "add", ".project")
    checkpoint_tree = _text(coordinator, "write-tree")
    checkpoint = _commit(coordinator, checkpoint_tree, spec["source_head"],
                         "chore: move milestone orchestration into its own repository",
                         extra_parent=receipt["export_head"])
    _git(coordinator, "update-ref", "refs/heads/" + spec["branch"], checkpoint, spec["source_head"])
    # Empty current tree; common ancestry preserves the original milestone's
    # first-parent proof without putting product files on coordinator main.
    empty = _git(coordinator, "mktree", data=b"").decode().strip()
    bootstrap = _commit(coordinator, empty, spec["source_base"], "chore: initialize orchestration repository")
    _git(coordinator, "update-ref", "refs/heads/" + spec["default_branch"], bootstrap, "")
    code_tag = receipt["retention_tag"].replace("migration/", "migration-code/", 1)
    for name, target in ((receipt["retention_tag"], checkpoint), (code_tag, receipt["export_head"])):
        _git(coordinator, "tag", "-a", name, target, "-m", "Preserved migration evidence\n\nSource: " + spec["source_head"])
    journal.update(receipt=receipt, bound=bound, coordinator_head=checkpoint, bootstrap=bootstrap,
                   code_tag=code_tag, stage="prepared")
    _write(_journal(coordinator), journal)
    validate(coordinator, allow_pending=True)
    return journal


def validate(repo: Path, project_dir: str = ".project", *, allow_pending: bool = False) -> Optional[dict]:
    """Verify original artifacts and every product projection without mutation."""
    receipt = load(repo, project_dir)
    if receipt is None:
        return None
    journal_path = _journal(repo)
    if journal_path.is_symlink():
        raise MigrationError("migration journal must not be a symlink")
    if journal_path.exists() and not allow_pending:
        transaction = json.loads(journal_path.read_text())
        if transaction.get("stage") != "complete":
            raise MigrationError("coordinator migration is incomplete; finish its journal before phase work")
    base, source = receipt.get("source_base"), receipt.get("source_head")
    if not isinstance(base, str) or not isinstance(source, str) or not SHA.fullmatch(base) or not SHA.fullmatch(source):
        raise MigrationError("migration source commits are invalid")
    chain = [base, *_history(repo, base, source)]
    if set(chain) != set(receipt["mapping"]):
        raise MigrationError("migration projection map does not cover the original history exactly")
    mapping = receipt["mapping"]
    if mapping[base] != base:
        raise MigrationError("migration changed the shared clean product base")
    for old, previous in zip(chain[1:], chain):
        projected, parent = mapping[old], mapping[previous]
        if not isinstance(projected, str) or not SHA.fullmatch(projected):
            raise MigrationError("migration projection contains an invalid commit")
        tree = projection(repo, old)
        if _text(repo, "rev-parse", projected + "^{tree}") != tree:
            raise MigrationError("projected product tree differs from the original: " + old)
        if projected != parent:
            parents = _text(repo, "rev-list", "--parents", "-n", "1", projected).split()
            if parents != [projected, parent]:
                raise MigrationError("product projection changed the original dependency order")
        elif tree != projection(repo, previous):
            raise MigrationError("product projection dropped a source change")
    if receipt.get("export_head") != mapping[source] or receipt.get("product_tree") != projection(repo, source):
        raise MigrationError("migration export identity differs from its source")
    _require_clean_history(repo, [receipt["export_head"]])
    location = repo / project_dir
    inventory = _text(repo, "ls-tree", "-r", "--name-only", source, "--",
                      ".project/tasks", ".project/review", ".project/build/verify-ledger.jsonl").splitlines()
    if set(inventory) != set(receipt.get("artifacts", {})):
        raise MigrationError("migration artifact inventory differs from the original history")
    for relative, expected in receipt.get("artifacts", {}).items():
        parts = Path(relative).parts
        if not parts or parts[0] != ".project" or ".." in parts:
            raise MigrationError("migration artifact path escapes .project")
        original = _git(repo, "show", source + ":" + relative)
        if hashlib.sha256(original).hexdigest() != expected:
            raise MigrationError("migration artifact digest differs from the original history: " + relative)
        path = location.joinpath(*parts[1:])
        if path.is_symlink() or not path.is_file():
            raise MigrationError("preserved migration artifact is missing: " + relative)
        contents = path.read_bytes()
        if relative.endswith("verify-ledger.jsonl"):
            if not contents.startswith(original):
                raise MigrationError("original verification ledger was rewritten")
        elif hashlib.sha256(contents).hexdigest() != expected:
            if not relative.startswith(".project/tasks/") or not contents.startswith(original):
                raise MigrationError("original task or review evidence was rewritten: " + relative)
    for task_id, task in receipt["tasks"].items():
        if not re.fullmatch(r"T\d{3}", task_id) or task.get("commit") not in mapping or task.get("base") not in mapping:
            raise MigrationError("migration task has no complete projection: " + task_id)
    _, isolation, _, _ = _modules()
    paths = [location / "tasks" / Path(task["path"]).name for task in receipt["tasks"].values()]
    proofs = isolation.verify_landed_task_files(repo, paths, ".project/tasks", source)["tasks"]
    actual = {task["task_id"]: {"commit": task["commit"], "base": task["base"],
                              "path": task["task"]} for task in proofs}
    if actual != receipt["tasks"]:
        raise MigrationError("migration task map differs from the original proven landings")
    if __package__:
        from . import check_handoffs
    else:
        import check_handoffs
    original_intent = _git(repo, "show", source + ":.project/intent/INTENT.md").decode("utf-8")
    current_intent = (location / "intent/INTENT.md").read_text(encoding="utf-8")
    if check_handoffs._success_criteria(original_intent) != check_handoffs._success_criteria(current_intent):
        raise MigrationError("migration changed the approved success criteria")
    return {"schema": SCHEMA, "source_head": source, "export_head": receipt["export_head"],
            "member": receipt["member"], "tasks": len(receipt["tasks"]), "status": "verified"}


def legacy_proof(repo: Path, task_id: str, commit: str, project_dir: str = ".project") -> Optional[dict]:
    receipt = load(repo, project_dir)
    task = receipt["tasks"].get(task_id) if receipt else None
    if not task:
        return None
    if task["commit"] != commit:
        raise MigrationError("original task landing differs from its migration receipt")
    return {"member_base": receipt["mapping"][task["base"]], "member_commit": receipt["mapping"][commit]}


def require_member_baseline(coordinator: Path, member: str, checkout: Path, reviewed: str, project_dir: str) -> None:
    receipt = load(coordinator, project_dir)
    if receipt and member == receipt["member"]:
        if _text(checkout, "merge-base", receipt["export_head"], reviewed) != receipt["export_head"]:
            raise MigrationError("member reviewed head does not preserve the migrated product baseline")
        _require_clean_history(checkout, [reviewed])


def _source_unchanged(spec: dict) -> None:
    source = Path(spec["source"])
    if (_text(source, "rev-parse", "HEAD") != spec["source_head"]
            or (source / ".project/STATE.md").read_text(encoding="utf-8") != spec["source_state"]):
        raise MigrationError("original source changed after the migration preview")
    _, isolation, _, _ = _modules()
    if isolation.uncommitted_paths(source) - {".project/STATE.md"}:
        raise MigrationError("original source has new uncommitted changes")


def publish(coordinator: Path) -> dict:
    """Publish preservation first; only then rewrite the exact backup ref."""
    journal = json.loads(_journal(coordinator).read_text())
    if journal.get("schema") != SCHEMA or journal.get("stage") not in {"prepared", "preserved", "published", "complete"}:
        raise MigrationError("migration is not prepared for publication")
    if journal["stage"] == "complete":
        return journal
    spec, receipt = journal["spec"], journal["receipt"]
    _source_unchanged(spec)
    validate(coordinator, allow_pending=True)
    code = Path(spec["code"])
    if _text(coordinator, "rev-parse", "HEAD") != journal["coordinator_head"]:
        raise MigrationError("coordinator changed after preparation")
    if _text(code, "rev-parse", "refs/heads/" + journal["bound"]) != receipt["export_head"]:
        raise MigrationError("code projection changed before publication")
    journal["audited_product_refs"] = _audit_remote(coordinator, spec)
    refs = [("refs/heads/" + spec["default_branch"], journal["bootstrap"]),
            ("refs/tags/" + receipt["retention_tag"], _text(coordinator, "rev-parse", "refs/tags/" + receipt["retention_tag"])),
            ("refs/tags/" + journal["code_tag"], _text(coordinator, "rev-parse", "refs/tags/" + journal["code_tag"]))]
    owned = dict(refs)
    for line in _text(coordinator, "ls-remote", "--heads", "--tags", "origin").splitlines():
        oid, name = line.split("\t")
        if name.endswith("^{}"):
            continue
        if owned.get(name) != oid:
            raise MigrationError("coordinator remote contains an unowned ref: " + name)
    for ref, expected in refs:
        current = _remote_ref(coordinator, "origin", ref)
        if current not in {None, expected}:
            raise MigrationError("coordinator remote ref already has different contents: " + ref)
        if current is None:
            _git(coordinator, "push", f"--force-with-lease={ref}:", "origin", f"{ref}:{ref}")
        if _remote_ref(coordinator, "origin", ref) != expected:
            raise MigrationError("coordinator preservation ref was not published exactly")
    remote_head = _text(coordinator, "ls-remote", "--symref", "origin", "HEAD")
    expected_head = "ref: refs/heads/" + spec["default_branch"] + "\tHEAD"
    if expected_head not in remote_head.splitlines():
        if __package__:
            from . import azure_repos, integration
        else:
            import azure_repos, integration
        azure = azure_repos.parse_remote(spec["coordinator_remote"])
        if azure is None:
            raise MigrationError("coordinator remote default branch must be set to " + spec["default_branch"])
        integration.azure_cli_json("repos", "update", "--repository", azure.name,
                                   "--default-branch", "refs/heads/" + spec["default_branch"], *azure.scope)
        if expected_head not in _text(coordinator, "ls-remote", "--symref", "origin", "HEAD").splitlines():
            raise MigrationError("coordinator remote default branch was not updated")
    _git(coordinator, "fetch", "--quiet", "origin")
    _git(coordinator, "remote", "set-head", "origin", "--auto")
    journal["stage"] = "preserved"
    _write(_journal(coordinator), journal)
    current = _remote_ref(code, "origin", spec["backup_ref"])
    if current not in {spec["backup_old"], receipt["export_head"]}:
        raise MigrationError("backup branch advanced; refusing to overwrite unapproved work")
    if current != receipt["export_head"]:
        _git(code, "push", f"--force-with-lease={spec['backup_ref']}:{spec['backup_old']}", "origin",
             f"{receipt['export_head']}:{spec['backup_ref']}")
    if _remote_ref(code, "origin", spec["backup_ref"]) != receipt["export_head"]:
        raise MigrationError("backup branch does not name the clean product export")
    _git(code, "config", "--replace-all", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    _git(code, "fetch", "--quiet", "--prune", "--tags", "origin")
    product_refs = _text(code, "for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/origin/", "refs/tags/").splitlines()
    _require_clean_history(code, product_refs)
    journal["stage"] = "published"
    _write(_journal(coordinator), journal)
    return journal


def _move_product_extras(source: Path, code: Path, journal: dict, journal_path: Path) -> None:
    """Move ignored dependencies and local files without reading their contents."""
    tracked = set(_git(source, "ls-files", "-z").decode("utf-8", "surrogateescape").split("\0"))
    prefixes = {str(Path(p).parent) for p in tracked}
    prefixes |= {str(parent) for p in tracked for parent in Path(p).parents}
    moves = journal.setdefault("local_moves", [])
    for move in moves:
        if move["done"]:
            continue
        old, new = Path(move["source"]), Path(move["target"])
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        elif old.exists() or not new.exists():
            raise MigrationError("interrupted local-file move has ambiguous contents")
        move["done"] = True
        _write(journal_path, journal)
    def transfer(path: Path):
        relative = path.relative_to(source).as_posix()
        target = code / relative
        if relative in tracked:
            return
        if path.is_dir() and not path.is_symlink() and relative in prefixes:
            for child in path.iterdir():
                transfer(child)
            return
        if target.exists() or target.is_symlink():
            raise MigrationError("local product file collides with the clean checkout: " + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        moves.append({"source": str(path), "target": str(target), "done": False})
        _write(journal_path, journal)
        path.rename(target)
        moves[-1]["done"] = True
        _write(journal_path, journal)
    for path in source.iterdir():
        if path.name != ".git" and path.name not in METADATA:
            transfer(path)


def finalize(coordinator: Path, package_root: Path) -> dict:
    """Install supported bindings, retain the old worktree, and complete cutover."""
    path = _journal(coordinator)
    journal = json.loads(path.read_text())
    if journal.get("schema") != SCHEMA or journal.get("stage") not in {"published", "finalizing", "complete"}:
        raise MigrationError("publish preservation and the clean backup before finalizing")
    if journal["stage"] == "complete":
        validate(coordinator)
        return journal
    spec, receipt = journal["spec"], journal["receipt"]
    source, code = Path(spec["source"]), Path(spec["code"])
    if journal["stage"] == "published":
        _source_unchanged(spec)
        journal["stage"] = "finalizing"
        _write(path, journal)
    # Keep old local configuration as local configuration; never stage it.
    for name in sorted(METADATA - {".project", ".planning"}):
        original, destination = source / name, coordinator / name
        if original.exists() and not destination.exists():
            if original.is_symlink():
                raise MigrationError("managed configuration must not be a symlink: " + name)
            if original.is_dir():
                shutil.copytree(original, destination)
            else:
                shutil.copy2(original, destination)
    if (source / ".planning").exists():
        shutil.copytree(source / ".planning", coordinator / ".planning", dirs_exist_ok=True)
    # The installer preserves custom SOPs. Refresh a known canonical older SOP
    # while retaining its exact bytes in the transaction backup.
    workflow, canonical = coordinator / "WORKFLOW.md", package_root / "WORKFLOW.md"
    if workflow.is_file() and canonical.is_file() and workflow.read_bytes() != canonical.read_bytes():
        history = subprocess.run(_common.git_command(package_root, "log", "-n", "30", "--format=%H", "--", "WORKFLOW.md"),
                                 capture_output=True, text=True)
        if history.returncode == 0:
            for revision in history.stdout.splitlines():
                original = _git(package_root, "show", revision + ":WORKFLOW.md")
                if original == workflow.read_bytes():
                    backup = path.parent / "workflow-before-migration.md"
                    if not backup.exists():
                        backup.write_bytes(original)
                    shutil.copy2(canonical, workflow)
                    break
    installer = package_root / "scripts/install.py"
    actions = []
    if (coordinator / ".gsd-path/runtime.json").exists():
        actions.append(("--runtime-upgrade", "--project", str(coordinator)))
    installed_skills = (coordinator / ".claude/skills/gsd-path/SKILL.md").exists()
    actions.append(("--claude", "--local", "--project", str(coordinator),
                    *(("--update",) if installed_skills else ()), "--hooks"))
    for arguments in actions:
        run = subprocess.run([sys.executable, "-B", str(installer), *arguments, "--source-root", str(package_root)],
                             cwd=coordinator, capture_output=True, text=True)
        if run.returncode:
            raise MigrationError(run.stdout + run.stderr)
    _, isolation, members, _ = _modules()
    for item in [dict(name="code", checkout=str(code)), *spec["extra_members"]]:
        members._write_marker(Path(item["checkout"]), coordinator, spec["project"], item["name"])
        run = subprocess.run([sys.executable, "-B", str(installer), "--project", item["checkout"],
                              "--member-of", str(coordinator), "--source-root", str(package_root)],
                             capture_output=True, text=True)
        if run.returncode:
            raise MigrationError(run.stdout + run.stderr)
    bound = isolation.member_bound_checkout(coordinator, "code")
    journal["code_bound_checkout"] = bound["checkout"]
    _write(path, journal)
    if source.exists():
        _move_product_extras(source, code, journal, path)
        backup = path.parent / "original-worktree"
        if backup.exists():
            raise MigrationError("original-worktree backup already exists")
        source.rename(backup)
        journal["source_backup"] = str(backup)
        _write(path, journal)
    elif (path.parent / "original-worktree").exists():
        journal["source_backup"] = str(path.parent / "original-worktree")
    else:
        raise MigrationError("original source and its retained worktree are both missing")
    validate(coordinator, allow_pending=True)
    journal["stage"] = "complete"
    _write(path, journal)
    return journal


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "prepare", "publish", "finalize", "validate"))
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--coordinator", type=Path, required=True)
    parser.add_argument("--code", type=Path)
    parser.add_argument("--coordinator-remote")
    parser.add_argument("--project")
    parser.add_argument("--backup-branch")
    parser.add_argument("--ruling")
    parser.add_argument("--expected-head")
    parser.add_argument("--member", action="append", default=[], help="name=absolute-checkout")
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        if args.action in {"preview", "prepare"}:
            if not all((args.repo, args.code, args.coordinator_remote, args.project, args.backup_branch, args.ruling, args.expected_head)):
                parser.error("preview and prepare require the exact source, targets, remote, backup branch and owner ruling")
            extra = [dict(zip(("name", "checkout"), entry.split("=", 1))) for entry in args.member]
            existing = _journal(args.coordinator) if (args.coordinator / ".git").exists() else None
            if args.action == "prepare" and existing is not None and existing.is_file():
                transaction = json.loads(existing.read_text())
                spec = transaction["spec"]
                expected = (str(args.repo.resolve()), str(args.coordinator.absolute()), str(args.code.absolute()),
                            args.coordinator_remote, args.project, "refs/heads/" + args.backup_branch,
                            args.ruling, args.expected_head)
                recorded = tuple(spec[key] for key in ("source", "coordinator", "code", "coordinator_remote", "project", "backup_ref", "ruling", "source_head"))
                if expected != recorded:
                    raise MigrationError("retry differs from the recorded migration")
            else:
                spec = preview(args.repo, args.coordinator, args.code, args.coordinator_remote, args.project,
                               args.backup_branch, args.ruling, args.expected_head, extra)
            result = spec if args.action == "preview" else prepare(spec)
        elif args.action == "publish":
            result = publish(args.coordinator.resolve())
        elif args.action == "finalize":
            result = finalize(args.coordinator.resolve(), args.package_root.resolve())
        else:
            result = validate(args.coordinator.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (MigrationError, OSError, ValueError) as error:
        print("migration error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

