#!/usr/bin/env python3
"""Run canonical workflow helpers with fixed arguments and fail-stop receipts."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

try:
    from scripts import _common, isolation
except ImportError:  # bundled copy inside a skill's scripts directory
    import _common
    import isolation


class StepFailed(RuntimeError):
    pass


def _table_rows(body):
    """Cells of a Markdown table's data rows (header and separator skipped)."""
    lines = [line.strip() for line in (body or "").splitlines() if line.strip().startswith("|")]
    return [[cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip("|"))] for line in lines[2:]]

INSPECTION_SPECS = (("inspect_codebase", "codebase-mapper", "codebase", "evidence-codebase.md"),
                    ("inspect_docs", "docs-auditor", "docs-audit", "DOCS-AUDIT.md"))

# Member inspection runs only when the coordinator registers members; without
# .project/MEMBERS.md every inspect action keeps its single-repo behavior.
MEMBERS_FILE = ".project/MEMBERS.md"
MEMBER_INSPECTION_ACTIONS = ("prepare-inspect", "advance-inspect", "finish-inspect")
MEMBER_RECEIPT_SCHEMA = "gsd-path/member-inspection/v1"
MEMBER_HEADER_FIELDS = ("Repo root", "Member", "Default branch", "Member HEAD")
EXCLUSIONS = "Exclude .git, node_modules, .project/archive, and installed GSD Path and path skill bundles.\n"


def _logical_task(prefix: str, member: str) -> str:
    """A member's logical task name, normalized as the runtime dispatch contract requires."""
    return f"{prefix}_{re.sub(r'[^a-z0-9]+', '_', member.lower())}"


def _sha256(path: Path):
    """The SHA-256 of a regular file, or None when nothing exists at the path."""
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not path.is_file():
        raise StepFailed(f"inspection path is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")


def _member_header(text: str) -> dict:
    """The member header values above the first section, each only when written exactly once."""
    header = re.sub(r"(?s)<!--.*?-->", "", text).split("\n## ", 1)[0]
    values = {}
    for field in MEMBER_HEADER_FIELDS:
        found = [value.strip().strip("`").strip()
                 for value in re.findall(rf"(?m)^{re.escape(field)}:[ \t]*(.*)$", header)]
        values[field] = found[0] if len(found) == 1 else None
    return values


def _pending_rulings(text: str) -> bool:
    return any(row[3] == "no" for row in _common.docs_audit_ruling_rows(text))


def _retains_rulings(filename: str, prior_text: str, kept_text: str) -> bool:
    """`kept_text` keeps the audit identity and every ruling of `prior_text`, in order.

    Only a pending `no` may have become a T### since; new rows may follow."""
    if filename != _common.DOCS_AUDIT_FILENAME:
        prior, kept = _member_header(prior_text), _member_header(kept_text)
        if (prior["Member"], prior["Repo root"]) != (kept["Member"], kept["Repo root"]):
            return False
    try:
        prior_rows = _common.docs_audit_ruling_rows(prior_text)
        kept_rows = _common.docs_audit_ruling_rows(kept_text)
    except ValueError:
        return False
    for index, cells in enumerate(prior_rows):
        other = kept_rows[index] if index < len(kept_rows) else None
        if other is None or other[:3] != cells[:3] or (
                other[3] != cells[3] and not (cells[3] == "no" and re.fullmatch(r"T\d{3}", other[3]))):
            return False
    return True


class _MemberInspection:
    """Member-aware inspect: scope, staged sidecars, gates, collection, and the inspect/done transition.

    Stage 1 maps every assigned member at its frozen pin (beside the coordinator
    pair when that pair is due); stage 2 audits each member's docs at the same
    pin, because one member admits one pinned sidecar at a time. Every runtime
    decision runs through the selected runtime's helpers; nothing here fetches,
    locks, binds, or pushes."""

    def __init__(self, repo: Path, project_dir: str, expected_head: str, step, helpers: Path,
                 resources: Path, steps: list):
        self.repo = repo
        self.project_dir = project_dir
        self.expected_head = expected_head
        self.step = step
        self.helpers = helpers
        self.resources = resources
        self.steps = steps
        self.lookahead = project_dir == ".project/next"
        self.track = repo / project_dir
        self.research = self.track / "research"

    # Entry, scope, and capability.

    def require_runtime(self) -> None:
        """Block clearly when the selected runtime predates member inspection."""
        # (capability, command, text its help must show); one release ships all of them together.
        probes = (("members.py inspection", ("members.py", "inspection", "--help"), "--project-dir"),
                  ("isolation.py retire-member-inspect", ("isolation.py", "retire-member-inspect", "--help"),
                   "--keep-artifact"),
                  ("check_handoffs.py member-evidence", ("check_handoffs.py", "member-evidence", "--help"),
                   "--member"),
                  ("check_docs_audit.py --member", ("check_docs_audit.py", "--help"), "--member"))
        missing = []
        for label, (script, *arguments), needle in probes:
            completed = subprocess.run([sys.executable, "-B", str(self.helpers / script), *arguments],
                                       cwd=self.repo, capture_output=True, encoding="utf-8", errors="replace")
            if completed.returncode or needle not in completed.stdout:
                missing.append(label)
        self.steps.append({"script": "workflow_run.py", "capability": "member-inspection",
                           "runtime": str(self.helpers), "missing": missing})
        if missing:
            raise StepFailed("project runtime upgrade required: the selected runtime lacks member inspection ("
                             + ", ".join(missing) + ")")

    def require_entry(self) -> dict:
        """The track state after proving its phase, branch, program context, HEAD, and discussion."""
        common = ["--repo", str(self.repo), "--project-dir", self.project_dir]
        state = self.step("pipeline_state.py", "validate", *common)["state"]
        if (state["phase"], state["status"]) != ("inspect", "active"):
            raise StepFailed("lookahead member inspection requires next inspect/active" if self.lookahead
                             else "initial inspection requires the active inspect track")
        branch = _git(self.repo, "branch", "--show-current").stdout.strip()
        if self.lookahead:
            active = self.step("pipeline_state.py", "validate", "--repo", str(self.repo),
                               "--project-dir", ".project")["state"]
            program = all((self.repo / ".project" / name).is_file() and not (self.repo / ".project" / name).is_symlink()
                          for name in ("CHARTER.md", "ROADMAP.md"))
            if (active["phase"], active["status"]) != ("build", "active") or not program:
                raise StepFailed("lookahead member inspection requires active program build/active")
            if not active["branch"] or branch != active["branch"]:
                raise StepFailed("current branch differs from active inspection branch")
        elif branch != state["branch"]:
            raise StepFailed("current branch differs from the recorded inspection branch")
        head = _git(self.repo, "rev-parse", "--verify", "HEAD").stdout.strip()
        if head != self.expected_head:
            raise StepFailed("coordinator HEAD differs from --expected-head")
        if self.step("discussion_records.py", "pending", "--repo", str(self.repo))["pending"]:
            raise StepFailed("pending discussion requires its owner disposition")
        return state

    def scope(self) -> dict:
        scope = self.step("members.py", "inspection", "--repo", str(self.repo), "--project-dir", self.project_dir)
        unverifiable = [entry for entry in scope["members"] if entry["verdict"] == "unverifiable"]
        if unverifiable:
            raise StepFailed("; ".join(f"member {entry['name']} is unverifiable: {entry['reason']}"
                                       for entry in unverifiable))
        if scope["coordinator"] is None:
            raise StepFailed("member inspection requires .project/MEMBERS.md")
        if scope["coordinator"]["head"] != self.expected_head:
            raise StepFailed("coordinator HEAD differs from --expected-head")
        return scope

    # Frozen inputs.

    def read_audit(self, path: Path) -> str:
        if path.is_symlink() or not path.is_file():
            raise StepFailed(f"docs audit must be a regular file: {path}")
        return path.read_text(encoding="utf-8")

    def prior_audit(self, filename: str):
        """The audit whose rulings a fresh audit must carry: the track's own, else (lookahead) the active one."""
        local = self.research / filename
        active = self.repo / ".project/research" / filename
        if os.path.lexists(local):
            if self.lookahead and os.path.lexists(active) and not _retains_rulings(
                    filename, self.read_audit(active), self.read_audit(local)):
                raise StepFailed(f"lookahead {filename} does not retain the active audit rulings")
            return local
        if self.lookahead and os.path.lexists(active):
            return active
        return None

    def freeze(self, staging: Path, label: str, source: Path) -> dict:
        frozen = staging / label
        text = self.read_audit(source)
        frozen.write_bytes(text.encode("utf-8"))
        return {"source": str(source), "file": str(frozen), "sha256": _sha256(frozen)}

    def member_prior(self, staging: Path, member: dict):
        """Freeze a member's prior audit and, when its Member HEAD precedes the pin, the pinned changed set."""
        filename = Path(member["docs_audit"]).name
        source = self.prior_audit(filename)
        if source is None:
            return None, None
        prior = self.freeze(staging, f"prior-{filename}", source)
        header = _member_header(Path(prior["file"]).read_text(encoding="utf-8"))
        expected = {"Repo root": member["checkout"], "Member": member["name"],
                    "Default branch": member["default_branch"]}
        head = header["Member HEAD"] or ""
        if any(header[field] != value for field, value in expected.items()) or not re.fullmatch(r"[0-9a-f]{40}", head):
            raise StepFailed(f"prior audit {filename} belongs to a different member identity")
        checkout = Path(member["checkout"])
        if _git(checkout, "merge-base", "--is-ancestor", head, member["pin"]).returncode:
            return prior, None
        listed = subprocess.run(["git", "diff", "--no-ext-diff", "--name-only", "--no-renames", "-z",
                                 head, member["pin"]], cwd=checkout, capture_output=True, check=True)
        changed = staging / f"changed-{member['name']}.txt"
        paths = sorted(path.decode("utf-8") for path in listed.stdout.split(b"\0") if path)
        changed.write_bytes("".join(f"{path}\n" for path in paths).encode("utf-8"))
        return prior, {"file": str(changed), "sha256": _sha256(changed)}

    def member_inventory(self, staging: Path, member: dict) -> dict:
        """Freeze a member's docs inventory at its pin before any sidecar exists.

        A pinned tree the gate cannot inventory blocks preparation, so no
        sidecar is ever created for an audit that could not start. Every later
        load verifies the recorded hash, and finish rechecks the frozen list
        against the same pinned tree."""
        listed = self.step("check_docs_audit.py", "--repo", str(self.repo), "--member", member["name"],
                           "--pin", member["pin"], "--project-dir", self.project_dir, "--emit-inventory",
                           raw=True)
        frozen = staging / f"inventory-{member['name']}.txt"
        frozen.write_bytes(listed.encode("utf-8"))
        return {"file": str(frozen), "sha256": _sha256(frozen)}

    def passive_carries(self, fresh: set) -> list:
        """Active pending audits the lookahead track must hold although no fresh audit replaces them."""
        carries = []
        for source in _common.docs_audit_files(self.repo / ".project/research"):
            text = self.read_audit(source)
            if source.name in fresh or not _pending_rulings(text):
                continue
            destination = self.research / source.name
            carry = {"filename": source.name, "source": f".project/research/{source.name}",
                     "destination": f"{self.project_dir}/research/{source.name}",
                     "source_sha256": _sha256(source), "destination_sha256": _sha256(destination),
                     "reused": os.path.lexists(destination)}
            if carry["reused"] and not _retains_rulings(source.name, text, self.read_audit(destination)):
                raise StepFailed(f"lookahead {source.name} does not retain the active audit rulings")
            carries.append(carry)
        return carries

    # Briefs.

    def role(self, role: str, template: str) -> tuple:
        paths = (self.resources / "references" / f"{role}.md", self.resources / "templates" / f"{template}.md")
        for path in paths:
            if not path.is_file():
                raise StepFailed(f"missing inspection resource: {path}")
        return paths

    @staticmethod
    def prior_lines(prior, changed) -> str:
        if prior is None:
            return "No prior audit or changed set.\n"
        lines = (f"Prior audit, carry-forward input (keep its ## User rulings and Planned values verbatim): "
                 f"{prior['file']}\n")
        if changed is None:
            return lines + "No changed set: re-audit every inventoried doc.\n"
        return lines + f"Changed set since the prior audit's Member HEAD: {changed['file']}\n"

    def coordinator_brief(self, task_name: str, role_path: Path, template_path: Path, isolated: dict,
                          output: str, inventory: str, prior) -> str:
        brief = (f"Logical task: {task_name}\nRead and follow role: {role_path}\n"
                 f"Template: {template_path}\nRepository and command cwd: {isolated['worktree']}\n"
                 f"Recorded baseline and Audited HEAD: {isolated['base']}\n"
                 f"Write only: {Path(isolated['worktree']) / output}\n"
                 f"Write header Repo root: {self.repo}\n"
                 "Use only this supplied sidecar for project commands; do not create worktrees.\n"
                 "The parent owns gates, collection, retirement, and phase state.\n" + EXCLUSIONS)
        if task_name == "inspect_docs":
            brief += ("Alignment mode: false. " + self.prior_lines(prior, None)
                      + "Frozen inventory follows; use it exactly, without rediscovery:\n" + inventory)
        return brief

    def member_brief(self, task_name: str, role_path: Path, template_path: Path, member: dict,
                     isolated: dict, output: str) -> str:
        sidecar = isolated["member_sidecar"]
        return (f"Logical task: {task_name}\nRead and follow role: {role_path}\nTemplate: {template_path}\n"
                f"Member: {member['name']}\n"
                f"Member code, read-only at the pin, and command cwd: {sidecar['worktree']}\n"
                f"It is the artifact sidecar's sibling ../{member['name']}, checked out at Member HEAD "
                f"{member['pin']}.\n"
                f"Never read, run commands in, or change the team checkout {member['checkout']}.\n"
                f"Artifact sidecar: {isolated['worktree']}\n"
                f"Write only: {Path(isolated['worktree']) / output}\n"
                "Write these header lines, each exactly once:\n"
                f"Repo root: {member['checkout']}\nMember: {member['name']}\n"
                f"Default branch: {member['default_branch']}\nMember HEAD: {member['pin']}\n"
                "Use only the supplied sidecars for project commands; do not create worktrees.\n"
                "The parent owns gates, collection, retirement, and phase state.\n" + EXCLUSIONS)

    # Receipts.

    def write(self, receipt: dict) -> None:
        _common.atomic_write(Path(receipt["receipt_file"]), json.dumps(receipt, sort_keys=True) + "\n")

    def load(self, inspection: Path) -> dict:
        prepared = json.loads(inspection.read_text(encoding="utf-8"))
        if not isinstance(prepared, dict) or prepared.get("schema") != MEMBER_RECEIPT_SCHEMA:
            raise StepFailed("inspection receipt is not a member inspection receipt")
        if prepared["repo"] != str(self.repo) or prepared["base"] != self.expected_head:
            raise StepFailed("inspection receipt does not match repository and expected HEAD")
        if prepared["project_dir"] != self.project_dir:
            raise StepFailed("inspection receipt does not match project directory")
        if Path(prepared["receipt_file"]).resolve() != inspection.resolve():
            raise StepFailed("inspection receipt does not name its own file")
        research = f"{self.project_dir}/research/"
        expected = {spec[0]: research + spec[3] for spec in INSPECTION_SPECS}
        pins = {}
        for member in prepared["members"]:
            expected[member["code_task"]] = research + f"evidence-codebase-{member['name']}.md"
            expected[member["docs_task"]] = research + f"DOCS-AUDIT-{member['name']}.md"
            pins[member["code_task"]] = pins[member["docs_task"]] = (member["name"], member["pin"])
            if not member["inventory_file"] or not member["inventory_sha256"]:
                raise StepFailed(f"inspection receipt lacks the frozen inventory of member {member['name']}")
        self.check_carries(prepared["passive_carries"])
        names = [assignment["task_name"] for assignment in prepared["assignments"]]
        if len(names) != len(set(names)):
            raise StepFailed("inspection receipt repeats an assignment")
        for assignment in prepared["assignments"]:
            if (assignment["base"] != self.expected_head
                    or assignment["output"] != expected.get(assignment["task_name"])):
                raise StepFailed("inspection assignment changed its baseline or output")
            pinned = pins.get(assignment["task_name"])
            if pinned is not None and (assignment["member"], assignment["pin"]) != pinned:
                raise StepFailed(f"inspection assignment changed its member pin: {assignment['task_name']}")
        for frozen in self.frozen_inputs(prepared):
            if _sha256(Path(frozen["file"])) != frozen["sha256"]:
                raise StepFailed(f"frozen inspection input changed: {frozen['file']}")
        return prepared

    def check_carries(self, carries) -> None:
        """Each passive carry copies .project/research/<audit> to this track's research/<audit>.

        Both paths are rebuilt from one canonical audit filename, so a receipt can
        neither read nor publish anywhere else; only lookahead receipts carry."""
        if not isinstance(carries, list) or (carries and not self.lookahead):
            raise StepFailed("inspection receipt has invalid passive carries")
        seen = set()
        for carry in carries:
            filename = carry.get("filename") if isinstance(carry, dict) else None
            try:
                canonical = isinstance(filename, str) and _common.docs_audit_names([filename]) == [filename]
            except ValueError:
                canonical = False
            valid = canonical and filename not in seen and (
                carry.get("source") == f".project/research/{filename}"
                and carry.get("destination") == f"{self.project_dir}/research/{filename}"
                and isinstance(carry.get("reused"), bool)
                and isinstance(carry.get("source_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", carry["source_sha256"]) is not None
                and (carry.get("destination_sha256") is None) == (not carry["reused"]))
            if valid:
                for relative in (carry["source"], carry["destination"]):
                    path = PurePosixPath(relative)
                    valid = valid and not path.is_absolute() and ".." not in path.parts
            if not valid:
                raise StepFailed(f"inspection receipt has an invalid passive carry: {filename!r}")
            seen.add(filename)

    @staticmethod
    def frozen_inputs(receipt: dict) -> list:
        frozen = []
        if receipt["inventory_file"]:
            frozen.append({"file": receipt["inventory_file"], "sha256": receipt["inventory_sha256"]})
        if receipt["coordinator"]["prior_audit"]:
            frozen.append(receipt["coordinator"]["prior_audit"])
        for member in receipt["members"]:
            frozen += [item for item in (member["prior_audit"], member["changed"]) if item]
            if member["inventory_file"]:
                frozen.append({"file": member["inventory_file"], "sha256": member["inventory_sha256"]})
        return frozen

    # Actions.

    def prepare(self) -> dict:
        self.require_entry()
        scope = self.scope()
        coordinator = scope["coordinator"]
        code = self.research / INSPECTION_SPECS[0][3]
        audit = self.research / INSPECTION_SPECS[1][3]
        pair = False
        if coordinator["verdict"] == "brownfield":
            # A brownfield coordinator keeps its pair until its map exists; a map without an audit is not ours to redo.
            pair = not os.path.lexists(code)
            if not pair and not os.path.lexists(audit):
                raise StepFailed("prior inspection evidence requires the re-inspection contract")
        assigned = [entry for entry in scope["members"]
                    if entry["verdict"] == "brownfield" and entry["state"] == "missing"]
        skipped = [{key: entry[key] for key in ("name", "verdict", "state", "reason", "pin", "evidence_head")}
                   for entry in scope["members"] if entry not in assigned]
        tasks = [spec[0] for spec in INSPECTION_SPECS] if pair else []
        names = [task.replace("_", "-") for task in tasks]
        for entry in assigned:
            for kind in ("codebase", "docs"):
                tasks.append(_logical_task(f"inspect_{kind}", entry["name"]))
                names.append(f"inspect-{kind}-{entry['name']}")
        if len(set(tasks)) != len(tasks):
            raise StepFailed("member inspection logical task names collide: " + ", ".join(
                sorted({task for task in tasks if tasks.count(task) > 1})))
        isolation.check_member_inspect_layout([entry["name"] for entry in assigned], names)
        roles = {spec[0]: self.role(spec[1], spec[2]) for spec in INSPECTION_SPECS}

        staging = Path(tempfile.mkdtemp(prefix="gsd-path-inspect-"))
        created = []
        try:
            receipt = self.prepare_staged(staging, coordinator, pair, assigned, skipped, roles, created)
        except BaseException:
            self.rollback(created)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return receipt

    def prepare_staged(self, staging: Path, coordinator: dict, pair: bool, assigned: list, skipped: list,
                       roles: dict, created: list) -> dict:
        audit = self.research / INSPECTION_SPECS[1][3]
        receipt_file = staging / "inspection.json"
        coordinator_prior = None
        inventory = inventory_file = None
        if pair:
            source = self.prior_audit(audit.name)
            if source is not None:
                coordinator_prior = self.freeze(staging, "prior-DOCS-AUDIT.md", source)
            inventory = self.step("check_docs_audit.py", "--repo", str(self.repo), "--emit-inventory", raw=True)
            inventory_file = staging / "inventory.txt"
            inventory_file.write_bytes(inventory.encode("utf-8"))
        members = []
        for entry in assigned:
            prior, changed = self.member_prior(staging, entry)
            inventory_member = self.member_inventory(staging, entry)
            members.append({
                "name": entry["name"], "checkout": entry["checkout"], "default_branch": entry["default_branch"],
                "pin": entry["pin"], "pin_ref": entry["pin_ref"],
                "code_task": _logical_task("inspect_codebase", entry["name"]),
                "docs_task": _logical_task("inspect_docs", entry["name"]),
                "code_output": f"{self.project_dir}/{entry['code_evidence']}",
                "docs_output": f"{self.project_dir}/{entry['docs_audit']}",
                "code_destination_sha256": _sha256(self.track / entry["code_evidence"]),
                "docs_destination_sha256": _sha256(self.track / entry["docs_audit"]),
                "prior_audit": prior, "changed": changed, "code_sha256": None,
                "inventory_file": inventory_member["file"], "inventory_sha256": inventory_member["sha256"],
                "mapper_retired": False, "advanced": False,
            })
        fresh = ({audit.name} if pair else set()) | {Path(entry["docs_audit"]).name for entry in assigned}
        carries = self.passive_carries(fresh) if self.lookahead else []

        receipt = {
            "schema": MEMBER_RECEIPT_SCHEMA, "mode": "members", "repo": str(self.repo),
            "base": self.expected_head, "project_dir": self.project_dir, "receipt_file": str(receipt_file),
            "stage": 1, "dispatch": [], "assignments": [], "collected": {}, "retired": [],
            "coordinator": {"verdict": coordinator["verdict"], "signal": coordinator["signal"],
                            "head": coordinator["head"], "assigned": pair, "prior_audit": coordinator_prior,
                            "destination_sha256": {spec[3]: _sha256(self.research / spec[3])
                                                   for spec in INSPECTION_SPECS} if pair else {}},
            "inventory_file": str(inventory_file) if inventory_file else None,
            "inventory_sha256": _sha256(inventory_file) if inventory_file else None,
            "members": members, "skipped": skipped, "passive_carries": carries,
        }
        if pair:
            for task_name, _, _, filename in INSPECTION_SPECS:
                isolated = self.step("isolation.py", "isolate-verify", "--repo", str(self.repo),
                                     "--base", self.expected_head, "--name", task_name.replace("_", "-"))
                created.append(("coordinator", isolated))
                role_path, template_path = roles[task_name]
                output = f"{self.project_dir}/research/{filename}"
                brief_file = staging / f"{task_name}.md"
                brief_file.write_bytes(self.coordinator_brief(
                    task_name, role_path, template_path, isolated, output, inventory,
                    coordinator_prior).encode("utf-8"))
                receipt["assignments"].append({
                    **isolated, "task_name": task_name, "kind": "coordinator", "stage": 1,
                    "role": str(role_path), "template": str(template_path), "output": output,
                    "brief_file": str(brief_file),
                    "destination_sha256": receipt["coordinator"]["destination_sha256"][filename]})
        for member in members:
            isolated = self.isolate(member, "codebase")
            created.append(("member", isolated))
            receipt["assignments"].append(self.member_assignment(staging, member, "codebase", isolated, roles))
        receipt["dispatch"] = [assignment["task_name"] for assignment in receipt["assignments"]]
        self.write(receipt)
        return receipt

    def isolate(self, member: dict, kind: str) -> dict:
        return self.step("isolation.py", "isolate-member-inspect", "--repo", str(self.repo),
                         "--member", member["name"], "--name", f"inspect-{kind}-{member['name']}",
                         "--base", self.expected_head, "--pin", member["pin"])

    def member_assignment(self, staging: Path, member: dict, kind: str, isolated: dict, roles: dict) -> dict:
        task_name = member["code_task"] if kind == "codebase" else member["docs_task"]
        role_path, template_path = roles["inspect_codebase" if kind == "codebase" else "inspect_docs"]
        output = member["code_output"] if kind == "codebase" else member["docs_output"]
        brief = self.member_brief(task_name, role_path, template_path, member, isolated, output)
        if kind == "docs":
            inventory = Path(member["inventory_file"]).read_text(encoding="utf-8")
            brief += (f"Audited HEAD: {member['pin']}\nAlignment mode: no\n"
                      "Doc paths and evidence are member-relative; queue numbers belong to this audit alone.\n"
                      + self.prior_lines(member["prior_audit"], member["changed"])
                      + f"Frozen inventory file: {member['inventory_file']}\n"
                      "Frozen inventory follows; use it exactly, without rediscovery:\n" + inventory)
        brief_file = staging / f"{task_name}.md"
        brief_file.write_bytes(brief.encode("utf-8"))
        return {**isolated, "task_name": task_name, "kind": "member", "stage": 1 if kind == "codebase" else 2,
                "role": str(role_path), "template": str(template_path), "output": output,
                "brief_file": str(brief_file),
                "destination_sha256": member["code_destination_sha256" if kind == "codebase"
                                             else "docs_destination_sha256"]}

    def rollback(self, created: list) -> None:
        """Retire sidecars this call created but never recorded; failures stay in the receipt steps."""
        for kind, isolated in reversed(created):
            try:
                if kind == "member":
                    self.step("isolation.py", "retire-member-inspect", "--repo", str(self.repo),
                              "--member", isolated["member"], "--name", isolated["name"],
                              "--base", isolated["base"], "--pin", isolated["pin"])
                else:
                    self.step("isolation.py", "retire", "--repo", str(self.repo),
                              "--worktree", isolated["worktree"], "--branch", isolated["branch"])
            except StepFailed:
                continue

    def advance(self, inspection: Path) -> dict:
        receipt = self.load(inspection)
        self.require_entry()
        assignments = {assignment["task_name"]: assignment for assignment in receipt["assignments"]}
        members = receipt["members"]
        if not members:
            raise StepFailed("inspection receipt has no member assignment to advance")
        if receipt["stage"] == 2:
            # A repeated call proves the audit sidecars it already created and returns them unchanged.
            for member in members:
                self.check(assignments[member["docs_task"]])
            return receipt
        # Gate every member map before any member sidecar changes hands.
        gated = {}
        for member in members:
            if member["advanced"]:
                continue
            mapper = assignments[member["code_task"]]
            output = Path(mapper["worktree"]) / mapper["output"]
            if not output.is_file() or output.is_symlink():
                raise StepFailed(f"member {member['name']} mapper output is missing: {output}")
            if not member["mapper_retired"]:
                self.check(mapper)
            self.gate_map(member, mapper)
            gated[member["name"]] = _sha256(output)
            if member["mapper_retired"] and gated[member["name"]] != member["code_sha256"]:
                raise StepFailed(f"member {member['name']} mapper output changed during advance-inspect")
        roles = {spec[0]: self.role(spec[1], spec[2]) for spec in INSPECTION_SPECS}
        staging = Path(receipt["receipt_file"]).parent
        for member in members:
            if member["advanced"]:
                continue
            mapper = assignments[member["code_task"]]
            if _sha256(Path(mapper["worktree"]) / mapper["output"]) != gated[member["name"]]:
                raise StepFailed(f"member {member['name']} mapper output changed during advance-inspect")
            if member["mapper_retired"]:
                # An earlier call retired the mapper but recorded no auditor. A docs pair
                # it created before stopping was never dispatched; retire it (the helper
                # refuses a touched or foreign sidecar) so the auditor can be recreated.
                self.retire_unrecorded_auditor(member)
            else:
                member["code_sha256"] = gated[member["name"]]
                self.step("isolation.py", "retire-member-inspect", "--repo", str(self.repo),
                          "--member", member["name"], "--name", mapper["name"], "--base", mapper["base"],
                          "--pin", mapper["pin"], "--keep-artifact")
                member["mapper_retired"] = True
                self.write(receipt)
            isolated = self.isolate(member, "docs")
            try:
                assignment = self.member_assignment(staging, member, "docs", isolated, roles)
                receipt["assignments"].append(assignment)
                member["advanced"] = True
                self.write(receipt)
            except BaseException:
                # The receipt does not record this pair, so nothing may hold it: retire it.
                self.rollback([("member", isolated)])
                raise
        receipt["stage"] = 2
        receipt["dispatch"] = [member["docs_task"] for member in members]
        self.write(receipt)
        return receipt

    def retire_unrecorded_auditor(self, member: dict) -> None:
        self.step("isolation.py", "retire-member-inspect", "--repo", str(self.repo),
                  "--member", member["name"], "--name", f"inspect-docs-{member['name']}",
                  "--base", self.expected_head, "--pin", member["pin"])

    def check(self, assignment: dict) -> None:
        self.step("isolation.py", "check-member-inspect", "--repo", str(self.repo),
                  "--member", assignment["member"], "--name", assignment["name"],
                  "--base", assignment["base"], "--pin", assignment["pin"])

    def gate_map(self, member: dict, mapper: dict) -> None:
        self.step("check_handoffs.py", "member-evidence", "--repo", str(self.repo),
                  "--project-dir", self.project_dir, "--member", member["name"], "--kind", "codebase",
                  "--path", str(Path(mapper["worktree"]) / mapper["output"]), "--pin", member["pin"])

    def finish(self, inspection: Path) -> dict:
        receipt = self.load(inspection)
        state = self.require_entry()
        members = receipt["members"]
        if members and receipt["stage"] != 2:
            raise StepFailed("member inspection requires advance-inspect before finish-inspect")
        expected_tasks = ({spec[0] for spec in INSPECTION_SPECS} if receipt["coordinator"]["assigned"] else set())
        expected_tasks |= {member[key] for member in members for key in ("code_task", "docs_task")}
        assignments = {assignment["task_name"]: assignment for assignment in receipt["assignments"]}
        if set(assignments) != expected_tasks:
            raise StepFailed("inspection receipt must name every prepared assignment")
        if members:
            scope = self.scope()
            registered = {entry["name"]: entry for entry in scope["members"]}
            for member in members:
                entry = registered.get(member["name"])
                if entry is None or (entry["checkout"], entry["default_branch"]) != (
                        member["checkout"], member["default_branch"]):
                    raise StepFailed(f"inspection scope changed: member {member['name']}")
        for carry in receipt["passive_carries"]:
            source = self.repo / carry["source"]
            destination = self.repo / carry["destination"]
            if _sha256(source) != carry["source_sha256"]:
                raise StepFailed(f"inspection carry input changed: {carry['filename']}")
            if _sha256(destination) not in ({carry["destination_sha256"]} if carry["reused"]
                                            else {None, carry["source_sha256"]}):
                raise StepFailed(f"inspection carry input changed: {carry['filename']}")
        by_member = {member["code_task"]: member for member in members}
        by_member.update({member["docs_task"]: member for member in members})
        order = receipt["assignments"]
        gated = {}
        # Gate every artifact before collecting any of them.
        for assignment in order:
            task_name = assignment["task_name"]
            if task_name in receipt["collected"]:
                continue
            source = Path(assignment["worktree"]) / assignment["output"]
            if not source.is_file() or source.is_symlink():
                raise StepFailed(f"inspection output is missing: {source}")
            member = by_member.get(task_name)
            if member is None and task_name == "inspect_docs":
                self.gate_coordinator_audit(receipt, assignment)
            elif member is not None and task_name == member["code_task"]:
                self.gate_map(member, assignment)
                if _sha256(source) != member["code_sha256"]:
                    raise StepFailed(f"member {member['name']} mapper output changed after advance-inspect")
            elif member is not None:
                self.check(assignment)
                self.gate_member_audit(member, assignment)
            gated[task_name] = _sha256(source)
        for assignment in order:
            task_name = assignment["task_name"]
            destination = self.repo / assignment["output"]
            if task_name in receipt["collected"]:
                if _sha256(destination) != receipt["collected"][task_name]:
                    raise StepFailed(f"inspection output changed after collection: {assignment['output']}")
            else:
                collect = ["--repo", str(self.repo), "--source", assignment["worktree"],
                           "--base", self.expected_head, "--branch", assignment["branch"],
                           "--source-path", assignment["output"], "--destination-path", assignment["output"],
                           "--expected-source", gated[task_name]]
                if assignment["destination_sha256"]:
                    collect += ["--expected-destination", assignment["destination_sha256"]]
                self.step("isolation.py", "collect-artifact", *collect)
                receipt["collected"][task_name] = gated[task_name]
                self.write(receipt)
            if task_name not in receipt["retired"]:
                self.retire(assignment)
                receipt["retired"].append(task_name)
                self.write(receipt)
        for carry in receipt["passive_carries"]:
            destination = self.repo / carry["destination"]
            if not carry["reused"] and _sha256(destination) is None:
                _common.atomic_write(destination, (self.repo / carry["source"]).read_bytes().decode("utf-8"))
            published = carry["destination_sha256"] if carry["reused"] else carry["source_sha256"]
            if _sha256(destination) != published:
                raise StepFailed(f"inspection carry publication differs from its receipt: {carry['filename']}")
        coverage = self.step("check_handoffs.py", "member-evidence", "--repo", str(self.repo),
                             "--project-dir", self.project_dir)
        expected = [part for key, value in state.items()
                    for part in (f"--expect-{key}", "null" if value is None else str(value))]
        self.step("pipeline_state.py", "transition", "--repo", str(self.repo), "--project-dir", self.project_dir,
                  *expected, "--set-phase", "inspect", "--set-status", "done",
                  "--event", "inspection artifacts passed")
        return {"inspection": receipt, "coverage": coverage}

    def gate_coordinator_audit(self, receipt: dict, docs: dict) -> None:
        gate = ["--repo", docs["worktree"], "--primary", str(self.repo), "--audit", docs["output"],
                "--inventory", receipt["inventory_file"]]
        if receipt["coordinator"]["prior_audit"]:
            gate += ["--prior-audit", receipt["coordinator"]["prior_audit"]["file"]]
        self.step("check_docs_audit.py", *gate)
        text = (Path(docs["worktree"]) / docs["output"]).read_text(encoding="utf-8")
        if f"Audited HEAD: {self.expected_head}" not in text.splitlines():
            raise StepFailed("audit does not name the supplied inspection baseline")

    def gate_member_audit(self, member: dict, auditor: dict) -> None:
        gate = ["--repo", auditor["worktree"], "--primary", str(self.repo), "--member", member["name"],
                "--pin", member["pin"], "--project-dir", self.project_dir, "--audit", member["docs_output"],
                "--inventory", member["inventory_file"]]
        if member["prior_audit"]:
            gate += ["--prior-audit", member["prior_audit"]["file"]]
            if member["changed"]:
                gate += ["--changed", member["changed"]["file"]]
        self.step("check_docs_audit.py", *gate)

    def retire(self, assignment: dict) -> None:
        if assignment["kind"] == "coordinator":
            self.step("isolation.py", "retire", "--repo", str(self.repo), "--worktree", assignment["worktree"],
                      "--branch", assignment["branch"])
        else:
            self.step("isolation.py", "retire-member-inspect", "--repo", str(self.repo),
                      "--member", assignment["member"], "--name", assignment["name"],
                      "--base", assignment["base"], "--pin", assignment["pin"])


def _task_member(repo: Path, project_dir: str, task_id: str):
    """The `repo:` member a task names, or None for a coordinator task."""
    for path in sorted((repo / project_dir / "tasks").glob(f"{task_id}-*.md")):
        fields, error = isolation.task_frontmatter(path.read_text(encoding="utf-8"))
        if error:
            raise StepFailed(error)
        return fields.get("repo")
    return None


def run_workflow(repo: Path, action: str, project_dir: str, expected_head: str = None,
                 task_id: str = None, round_size: int = None, inspection: Path = None,
                 mapper_reviewed: bool = False, kind: str = None) -> dict:
    steps = []
    outputs = {}
    scripts = Path(__file__).resolve().parent
    helpers = scripts

    def step(script, *arguments, raw=False):
        command = [sys.executable, "-B", str(helpers / script), *arguments]
        completed = subprocess.run(command, cwd=repo, capture_output=True, encoding="utf-8", errors="replace")
        receipt = {"script": script, "command": command, "exit_code": completed.returncode,
                   "stdout": completed.stdout, "stderr": completed.stderr}
        steps.append(receipt)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        if completed.returncode:
            raise StepFailed(script)
        if raw:
            return completed.stdout
        try:
            receipt["result"] = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise StepFailed(f"{script} did not return JSON") from error
        return receipt["result"]

    common = ["--repo", str(repo), "--project-dir", project_dir]
    next_step = "$gsd-path-forensics"

    def gate_plan():
        if step("discussion_records.py", "pending", "--repo", str(repo))["pending"]:
            raise StepFailed("pending discussion requires its owner disposition")
        step("check_handoffs.py", "plan", *common)
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"],
                              cwd=repo, capture_output=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
        step("check_task_briefs.py", "--repo", str(repo), "--base", head,
             "--tasks-dir", f"{project_dir}/tasks", "--project-dir", project_dir)
        panel = ["validate-plan", "--plan", str(repo / project_dir / "plan/PLAN.md"),
                 "--intent", str(repo / project_dir / "intent/INTENT.md")]
        charter = repo / ".project/CHARTER.md"
        if charter.exists():
            panel += ["--charter", str(charter)]
        return step("review_panel.py", *panel)

    try:
        if os.path.lexists(repo / ".gsd-path/runtime.json"):
            resolved = subprocess.run(
                [sys.executable, "-B", str(repo / ".gsd-path/status_runtime.py"),
                 "--repo", str(repo), "--runtime-path"],
                cwd=repo, capture_output=True, encoding="utf-8", errors="replace")
            if resolved.returncode:
                sys.stderr.write(resolved.stderr)
                raise StepFailed(resolved.stderr.strip())
            helpers = Path(resolved.stdout.strip())
        if action in MEMBER_INSPECTION_ACTIONS and os.path.lexists(repo / MEMBERS_FILE):
            if action == "finish-inspect" and (not mapper_reviewed or inspection is None):
                raise StepFailed("finish-inspect requires the prepared active track and mapper review")
            if action == "advance-inspect" and (not mapper_reviewed or inspection is None):
                raise StepFailed("advance-inspect requires the prepared member inspection and mapper review")
            resources = scripts.parent
            if not (resources / "references").is_dir():
                resources = resources / "skills/gsd-path"
            members = _MemberInspection(repo, project_dir, expected_head, step, helpers, resources, steps)
            members.require_runtime()
            if action == "prepare-inspect":
                outputs["inspection"] = members.prepare()
            elif action == "advance-inspect":
                outputs["inspection"] = members.advance(inspection)
            else:
                outputs.update(members.finish(inspection))
        elif action == "advance-inspect":
            raise StepFailed("advance-inspect requires member inspection; .project/MEMBERS.md is absent")
        elif action == "route":
            step("pipeline_state.py", "route", *common)
        elif action == "prepare-inspect":
            state = step("pipeline_state.py", "validate", *common)["state"]
            if project_dir != ".project" or state["phase"] != "inspect" or state["status"] != "active":
                raise StepFailed("initial inspection requires the active inspect track")
            specs = INSPECTION_SPECS
            if any((repo / project_dir / "research" / spec[3]).exists() or
                   (repo / project_dir / "research" / spec[3]).is_symlink() for spec in specs):
                raise StepFailed("prior inspection evidence requires the re-inspection contract")
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                                    capture_output=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
            if branch != state["branch"]:
                raise StepFailed("current branch differs from the recorded inspection branch")
            pending = step("discussion_records.py", "pending", "--repo", str(repo))
            if pending["pending"]:
                raise StepFailed("pending discussion requires its owner disposition")
            inventory = step("check_docs_audit.py", "--repo", str(repo), "--emit-inventory", raw=True)
            resources = scripts.parent
            if not (resources / "references").is_dir():
                resources = resources / "skills/gsd-path"
            for _, role, template, _ in specs:
                for path in (resources / "references" / f"{role}.md", resources / "templates" / f"{template}.md"):
                    if not path.is_file():
                        raise StepFailed(f"missing inspection resource: {path}")
            staging = Path(tempfile.mkdtemp(prefix="gsd-path-inspect-"))
            inventory_file = staging / "inventory.txt"
            inventory_file.write_bytes(inventory.encode("utf-8"))
            assignments = []
            for task_name, role, template, filename in specs:
                isolated = step("isolation.py", "isolate-verify", "--repo", str(repo),
                                "--base", expected_head, "--name", task_name.replace("_", "-"))
                role_path = resources / "references" / f"{role}.md"
                template_path = resources / "templates" / f"{template}.md"
                output = f"{project_dir}/research/{filename}"
                brief_file = staging / f"{task_name}.md"
                brief = (f"Logical task: {task_name}\nRead and follow role: {role_path}\n"
                         f"Template: {template_path}\nRepository and command cwd: {isolated['worktree']}\n"
                         f"Recorded baseline and Audited HEAD: {isolated['base']}\n"
                         f"Write only: {Path(isolated['worktree']) / output}\n"
                         f"Write header Repo root: {repo}\n"
                         "Use only this supplied sidecar for project commands; do not create worktrees.\n"
                         "The parent owns gates, collection, retirement, and phase state.\n"
                         "Exclude .git, node_modules, .project/archive, and installed GSD Path and path skill bundles.\n")
                if task_name == "inspect_docs":
                    brief += ("Alignment mode: false. No prior audit or changed set.\n"
                              "Frozen inventory follows; use it exactly, without rediscovery:\n" + inventory)
                brief_file.write_bytes(brief.encode("utf-8"))
                assignments.append({**isolated, "task_name": task_name, "role": str(role_path),
                                    "template": str(template_path), "output": output,
                                    "brief_file": str(brief_file)})
            receipt_file = staging / "inspection.json"
            outputs["inspection"] = {"repo": str(repo), "base": expected_head,
                                     "inventory_file": str(inventory_file),
                                     "inventory_sha256": hashlib.sha256(inventory_file.read_bytes()).hexdigest(),
                                     "assignments": assignments, "receipt_file": str(receipt_file)}
            _common.atomic_write(receipt_file, json.dumps(outputs["inspection"], sort_keys=True) + "\n")
        elif action == "finish-inspect":
            if not mapper_reviewed or inspection is None or project_dir != ".project":
                raise StepFailed("finish-inspect requires the prepared active track and mapper review")
            prepared = json.loads(inspection.read_text(encoding="utf-8"))
            if prepared["repo"] != str(repo) or prepared["base"] != expected_head:
                raise StepFailed("inspection receipt does not match repository and expected HEAD")
            assignments = {a["task_name"]: a for a in prepared["assignments"]}
            if len(prepared["assignments"]) != len(INSPECTION_SPECS) or set(assignments) != {s[0] for s in INSPECTION_SPECS}:
                raise StepFailed("inspection receipt must name both original assignments")
            for task_name, _, _, filename in INSPECTION_SPECS:
                assignment = assignments[task_name]
                if assignment["base"] != expected_head or assignment["output"] != f".project/research/{filename}":
                    raise StepFailed("inspection assignment changed its baseline or output")
            inventory_file = Path(prepared["inventory_file"])
            if hashlib.sha256(inventory_file.read_bytes()).hexdigest() != prepared["inventory_sha256"]:
                raise StepFailed("frozen inspection inventory changed")
            state = step("pipeline_state.py", "validate", *common)["state"]
            if state["phase"] != "inspect" or state["status"] != "active":
                raise StepFailed("inspection completion requires inspect/active")
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                                    capture_output=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
            if state["branch"] != branch:
                raise StepFailed("current branch differs from the recorded inspection branch")
            pending = step("discussion_records.py", "pending", "--repo", str(repo))
            if pending["pending"]:
                raise StepFailed("pending discussion requires its owner disposition")
            docs = assignments["inspect_docs"]
            step("check_docs_audit.py", "--repo", docs["worktree"], "--primary", str(repo),
                 "--audit", docs["output"], "--inventory", str(inventory_file))
            if f"Audited HEAD: {expected_head}" not in (Path(docs["worktree"]) / docs["output"]).read_text(encoding="utf-8").splitlines():
                raise StepFailed("audit does not name the supplied inspection baseline")
            for task_name, _, _, _ in INSPECTION_SPECS:
                assignment = assignments[task_name]
                step("isolation.py", "collect-artifact", "--repo", str(repo),
                     "--source", assignment["worktree"], "--base", expected_head,
                     "--branch", assignment["branch"], "--source-path", assignment["output"],
                     "--destination-path", assignment["output"])
                step("isolation.py", "retire", "--repo", str(repo), "--worktree", assignment["worktree"],
                     "--branch", assignment["branch"])
            expected = [part for key, value in state.items()
                        for part in (f"--expect-{key}", "null" if value is None else str(value))]
            step("pipeline_state.py", "transition", *common, *expected,
                 "--set-phase", "inspect", "--set-status", "done", "--event", "inspection artifacts passed")
        elif action == "lint-round":
            head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"],
                                  cwd=repo, capture_output=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
            step("check_task_briefs.py", "--repo", str(repo), "--base", head,
                 "--tasks-dir", f"{project_dir}/tasks", "--project-dir", project_dir)
            step("check_handoffs.py", "plan", *common)
        elif action == "prepare-task" and _task_member(repo, project_dir, task_id):
            # A member task works in a sidecar of its member; its Verify runs there at finish.
            step("isolation.py", "isolate-member-task", "--repo", str(repo),
                 "--member", _task_member(repo, project_dir, task_id), "--task-id", task_id)
        elif action == "prepare-task":
            task = step("isolation.py", "isolate-task", "--repo", str(repo),
                        "--base", expected_head, "--task-id", task_id,
                        "--round-size", str(round_size))
            if task["mode"] == "serial":
                name = f"task-{task_id.lower()}-verify"
                stale = isolation.sidecar_root(repo, "verify", name)
                if os.path.lexists(stale / ".git"):
                    # An earlier prepare-task left this sidecar. Only one that is proven untouched is replaced.
                    steps.append({"script": "isolation.py", "result": isolation.retire_stale_verify(
                        repo, stale, isolation.verify_branch_name(name))})
                step("isolation.py", "isolate-verify", "--repo", str(repo),
                     "--base", expected_head, "--name", name)
        elif action == "build-evidence":
            proof = step("build_state.py", "verify-landed", *common, "--head", expected_head)
            # The landing proof has one canonical home; lean final-review reuse allows only this path.
            evidence = repo / project_dir / "build" / "evidence.json"
            _common.atomic_write(evidence, json.dumps(proof, indent=2, sort_keys=True) + "\n")
            steps.append({"script": "workflow_run.py", "evidence": str(evidence)})
        elif action == "prepare-final":
            if project_dir != ".project":
                raise StepFailed("final review requires the active milestone")
            pending = step("discussion_records.py", "pending", "--repo", str(repo))
            if pending["pending"]:
                raise StepFailed("pending discussion requires its owner disposition")
            step("build_state.py", "verify-landed", *common, "--head", expected_head)
            step("lean_verification.py", "--repo", str(repo), "--expected-head", expected_head)
        elif action == "preauthorize":
            # A block here is not a failure: the gate asks its owner as usual.
            next_step = "ask the owner at this gate"
            if project_dir != ".project" or kind not in {"intent", "plan"}:
                raise StepFailed("pre-approval covers only intent or plan on the active track")
            routed = step("pipeline_state.py", "route", *common)
            if routed["route"].get("action") != "run-phase" or "recovery" in routed:
                raise StepFailed("pre-approval needs an ordinary phase route")
            if step("discussion_records.py", "pending", "--repo", str(repo))["pending"]:
                raise StepFailed("pending discussion requires its owner disposition")
            track = repo / project_dir
            intent = (track / "intent/INTENT.md").read_text(encoding="utf-8")
            lane = re.search(r"(?m)^Lane:\s*(.+)$", intent)
            if not lane or lane.group(1).split("<!--", 1)[0].strip().strip("`") != "quick":
                raise StepFailed("pre-approval covers only the quick lane")
            intent = re.sub(r"(?s)<!--.*?-->", "", intent)
            if re.search(r"(?m)^\s*-\s*\[(RESEARCH|NEEDS-USER)\]", _common.section_body(intent, "Open questions") or ""):
                raise StepFailed("INTENT.md has open RESEARCH or NEEDS-USER questions")
            # Member audits, including a removed member's carried queue, rule independently.
            for audit_path in _common.docs_audit_files(track / "research"):
                audit = audit_path.read_text(encoding="utf-8")
                queue = _table_rows(_common.section_body(audit, "Remediation queue"))
                ruled = set()
                for row in _table_rows(_common.section_body(audit, "User rulings")):
                    if len(row) != 4 or row[1] not in {"fix-code", "fix-doc", "accept-drift"}:
                        continue
                    words = row[2].strip().strip('`"').strip()
                    if words and words.casefold() != "none" and not re.fullmatch(r"<[^>]*>", words):
                        ruled.add(row[0])
                if any(len(row) == 6 and row[4] == "NEEDS-USER" and row[0] not in ruled for row in queue):
                    raise StepFailed(f"{audit_path.name} has NEEDS-USER rows without a user ruling")
            if kind == "intent":
                # Malformed or incomplete spec-probe tables are not a pre-approvable draft.
                step("check_handoffs.py", "intent", *common)
            if kind == "plan":
                if gate_plan()["mode"] != "off":
                    raise StepFailed("pre-approval requires review_panel off")
                plan = re.sub(r"(?s)<!--.*?-->", "", (track / "plan/PLAN.md").read_text(encoding="utf-8"))
                config = _common.section_body(plan, "Config") or ""
                skeptics = re.search(r"(?m)^[ \t]*-[ \t]*finding_skeptics:[ \t]*(\S+)", config)
                if skeptics and skeptics.group(1) != "off":
                    raise StepFailed("pre-approval requires finding_skeptics off")
                # Quick-lane limit from project policy (gsd-path-plan Quick mode): one wave, at most two tasks.
                if (len(re.findall(r"(?m)^## Wave \d+ — ", plan)) != 1
                        or len(list((track / "tasks").glob("T[0-9][0-9][0-9]-*.md"))) > 2):
                    raise StepFailed("pre-approval requires one wave and at most two tasks")
            step("pipeline_state.py", "pre-approve", "--repo", str(repo), "--use", kind)
        else:
            gate_plan()
            if action == "approve-plan":
                step("pipeline_state.py", "approve", *common, "--kind", "plan",
                     "--expected-head", expected_head)
    except (StepFailed, isolation.IsolationError, OSError, subprocess.CalledProcessError,
            ValueError, KeyError, TypeError) as error:
        return {"status": "blocked", "reason": str(error), "steps": steps,
                "next": next_step}
    return {"status": "complete", "steps": steps, **outputs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("route", "gate-plan", "approve-plan", "build-evidence", "lint-round", "prepare-task", "prepare-final", "prepare-inspect", "advance-inspect", "finish-inspect", "preauthorize"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project-dir", choices=(".project", ".project/next"), default=".project")
    parser.add_argument("--expected-head")
    parser.add_argument("--task-id")
    parser.add_argument("--round-size", type=int)
    parser.add_argument("--inspection", type=Path)
    parser.add_argument("--mapper-reviewed", action="store_true")
    parser.add_argument("--kind", choices=("intent", "plan"))
    arguments = parser.parse_args(argv)
    if arguments.action in {"approve-plan", "build-evidence", "prepare-task", "prepare-final", "prepare-inspect", "advance-inspect", "finish-inspect"} and not arguments.expected_head:
        parser.error(f"{arguments.action} requires --expected-head")
    if arguments.action == "prepare-task" and (not arguments.task_id or arguments.round_size is None):
        parser.error("prepare-task requires --task-id and --round-size")
    if arguments.action == "preauthorize" and not arguments.kind:
        parser.error("preauthorize requires --kind")
    result = run_workflow(arguments.repo.resolve(), arguments.action,
                          arguments.project_dir, arguments.expected_head,
                          arguments.task_id, arguments.round_size, arguments.inspection,
                          arguments.mapper_reviewed, arguments.kind)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
