#!/usr/bin/env python3
# gsd-path project runtime
"""Review-panel members through vendor CLIs, inside Paseo's Docker Sandboxes (fork only).

Built for the Docker Sandboxes (`sbx`) that Paseo runs GSD Path orchestrators in. When the paseo-sbx
plugin provisions the CLIs, its manifest (PASEO_SBX_CLI_MANIFEST) names each CLI's state, credential
files, and the credential roots to hide. Each member runs
in its vendor's headless CLI (Codex, Muse Code, Antigravity) inside a bubblewrap mount namespace
where everything is read-only except a private scratch directory, the CLI's own state, and, for a
wave panel, the member's verify sidecar. Outside a sandbox the runtime refuses.

  commands  print the dispatch driver's --family-commands for the configured families
  run       run one member on the brief from stdin and write its checked report to --final
  doctor    check the sandbox, bubblewrap, and each configured CLI's version and login
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.dont_write_bytecode = True

FAILURE_EXIT = 3
FAMILIES_BY_PROFILE = {"codex": "gpt", "muse": "muse", "agy": "gemini"}
CONFIG_FIELDS = ("profile", "model", "effort")

# The installed CLI version is recorded in the member's receipt, never checked: a version that
# breaks the member fails as any other member failure (owner decision, 1.7.1.73).
PROFILES: Dict[str, Dict[str, object]] = {
    "codex": {
        "binary": "codex",
        "state": (".codex",),
        "output": "file",
        "env": {},
        "login": ("codex", "login", "status"),
    },
    "muse": {
        "binary": "muse",
        "state": (".config/muse", ".local/share/muse"),
        "output": "stdout",
        "env": {"MUSE_NO_AUTO_UPDATE": "1"},
        "login": ("muse", "usage", "--json"),
    },
    "agy": {
        "binary": "agy",
        "state": (".gemini",),
        "output": "stdout",
        "env": {},
        "login": ("agy", "models"),
    },
}

# Credential files of other tools that a member never needs to read.
HIDDEN_FILES = (".claude/.credentials.json",)

# The paseo-sbx plugin's CLI manifest: provisioning stays the plugin's and the operator's; the
# runtime only reads where each CLI keeps its state and credential, and what to hide.
MANIFEST_ENV = "PASEO_SBX_CLI_MANIFEST"
MANIFEST_VERSION = 1
SHIM_UNMOUNTED_EXIT = 78
SHIM_PREFIX = "paseo-sbx-cli: "
# Without a manifest, Paseo's host-path mounts that hold other credentials are hidden by pattern.
DEFAULT_CREDENTIAL_MOUNT = re.compile(r"/\.local/state/paseo-sbx/(homes|cli-auth|claude/[^/]+)$")

# Only these variables reach a member: no tokens, keys, or the parent's agent settings.
PASSED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TZ",
              "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
              "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
              "SANDBOX_NAME", "SANDBOX_ID", "SANDBOX_VM_ID")

LOGIN = re.compile(r"\b(401|unauthori[sz]ed|missing (meta )?credentials|not (signed|logged) in|"
                   r"authentication (required|expired|failed|timed out)|login (required|expired)|"
                   r"token (expired|revoked)|invalid_grant|refresh_token_reused|run `?\w+ login`?)\b", re.I)
QUOTA = re.compile(r"(usage limit|rate limit|weekly limit|hit your (weekly |daily )?limit|quota|"
                   r"rate_limit_reached|insufficient_quota|too many requests|\b429\b)", re.I)
DENIED = re.compile(r"auto-denied|cannot prompt for", re.I)
# A credential bound as a single file cannot be replaced by rename (EBUSY): a CLI that renews its token
# that way fails inside the member, and the operator renews it outside the panel.
BUSY = re.compile(r"os error 16|resource busy|EBUSY", re.I)
SECRET = re.compile(r"(eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,})|((sk|rt|ya29|ghp|gho|1//)[-\w.]{16,})")


class PanelCliError(RuntimeError):
    """A member failure with a stable code; the driver blocks the family with the message."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code


# --- configuration -----------------------------------------------------------


def validate_config(value: object, source: str = "review_panel_cli", partial: bool = False) -> Dict[str, Dict[str, str]]:
    """{family: {"profile": codex|muse|agy, "model": slug, "effort": level?}}.

    A partial check accepts an entry still missing its profile or model (settings are written one
    field at a time); running a panel needs every entry complete."""
    if __package__:
        from . import review_panel
    else:
        import review_panel
    if not isinstance(value, dict):
        raise ValueError(f"{source} must map families to CLI profiles")
    if len(value) > review_panel.MAX_FAMILIES:
        raise ValueError(f"{source} names at most {review_panel.MAX_FAMILIES} families")
    for family, entry in value.items():
        if family not in review_panel.KNOWN_FAMILIES:
            raise ValueError(f"{source}.{family} is not a known panel family")
        if not isinstance(entry, dict) or set(entry) - set(CONFIG_FIELDS):
            raise ValueError(f"{source}.{family} allows only {', '.join(CONFIG_FIELDS)}")
        for field, text in entry.items():
            if not isinstance(text, str) or not text or text != text.strip() or any(c.isspace() for c in text):
                raise ValueError(f"{source}.{family}.{field} requires a nonempty word")
        if partial and not {"profile", "model"} <= set(entry):
            if "profile" in entry and entry["profile"] not in PROFILES:
                raise ValueError(f"{source}.{family}.profile must be one of {', '.join(PROFILES)}")
            if "model" in entry and review_panel.family_of_slug(entry["model"]) != family:
                raise ValueError(f"{source}.{family}.model {entry['model']} is not a {family} model")
            continue
        if entry.get("profile") not in PROFILES:
            raise ValueError(f"{source}.{family}.profile must be one of {', '.join(PROFILES)}")
        if "model" not in entry:
            raise ValueError(f"{source}.{family}.model is required")
        if FAMILIES_BY_PROFILE[entry["profile"]] != family:
            raise ValueError(f"{source}.{family}: profile {entry['profile']} serves the "
                             f"{FAMILIES_BY_PROFILE[entry['profile']]} family")
        if review_panel.family_of_slug(entry["model"]) != family:
            raise ValueError(f"{source}.{family}.model {entry['model']} is not a {family} model")
    return value


def load_config(repo: Path) -> Dict[str, Dict[str, str]]:
    """The project's review_panel_cli, else the user default; empty when neither sets it."""
    if __package__:
        from . import path_config
    else:
        import path_config
    project = path_config.preferences(repo / ".project/config.json")
    if "review_panel_cli" in project:
        return project["review_panel_cli"]
    return path_config.user_defaults().get("review_panel_cli", {})


# --- environment checks ------------------------------------------------------


def in_sandbox() -> bool:
    return bool(os.environ.get("PASEO_SBX_OUT") or os.environ.get("SANDBOX_NAME")) or Path("/run/sandbox").exists()


def require_sandbox() -> None:
    if not in_sandbox():
        raise PanelCliError("not-sandboxed", "CLI panel members run only inside Paseo's Docker Sandboxes (sbx)")
    if shutil.which("bwrap") is None:
        raise PanelCliError("no-bwrap", "bubblewrap (bwrap) is not installed in this sandbox")


def cli_version(profile: str, manifest: Optional[Dict[str, object]] = None) -> str:
    spec = PROFILES[profile]
    if manifest is not None and profile not in manifest["clis"]:  # type: ignore[operator]
        raise PanelCliError("cli-missing", f"{profile} is not enabled in this sandbox. {fix_hint(manifest)}".strip())
    binary = shutil.which(str(spec["binary"]))
    if binary is None:
        where = fix_hint(manifest) if manifest is not None else "install it in this sandbox"
        raise PanelCliError("cli-missing", f"{spec['binary']} is not on PATH; {where}")
    env = dict(os.environ, **spec["env"])  # type: ignore[arg-type]
    completed = subprocess.run([binary, "--version"], capture_output=True, encoding="utf-8",
                               errors="replace", env=env, timeout=60, stdin=subprocess.DEVNULL)
    shim_unmounted(completed.returncode, completed.stderr, fix_hint(manifest))
    found = re.findall(r"\d+\.\d+\.\d+", completed.stdout + completed.stderr)
    if not found:
        return "unknown"
    return found[-1] if profile == "agy" else found[0]


# --- the plugin's manifest ------------------------------------------------------


def _absolute(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value:
        raise PanelCliError("manifest", f"{label} must be an absolute path")
    return Path(value)


def load_manifest() -> Optional[Dict[str, object]]:
    """The plugin's CLI manifest, checked for shape; None when the plugin did not provide one."""
    location = os.environ.get(MANIFEST_ENV)
    if not location:
        return None
    try:
        data = json.loads(Path(location).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PanelCliError("manifest", f"{MANIFEST_ENV} names an unreadable manifest: {error}") from error
    version = data.get("version") if isinstance(data, dict) else None
    if version != MANIFEST_VERSION or isinstance(version, bool):
        raise PanelCliError("manifest", f"paseo-sbx CLI manifest version {version!r} is not supported; "
                                        f"this runtime reads version {MANIFEST_VERSION}")
    if not isinstance(data.get("clis"), dict):
        raise PanelCliError("manifest", "the manifest's clis must be an object")
    for key in ("read_roots", "credential_roots"):
        if not isinstance(data.get(key, []), list):
            raise PanelCliError("manifest", f"the manifest's {key} must be a list")
        for item in data.get(key, []):
            _absolute(item, key)
    if not isinstance(data.get("fix_hint", ""), str):
        raise PanelCliError("manifest", "the manifest's fix_hint must be text")
    for name, entry in data["clis"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("state"), list) or not entry["state"] \
                or not isinstance(entry.get("credentials", []), list):
            raise PanelCliError("manifest", f"manifest CLI {name} needs a state list and a credentials list")
        for item in entry["state"]:
            _absolute(item, f"{name} state")
        for item in entry.get("credentials", []):
            if not isinstance(item, dict):
                raise PanelCliError("manifest", f"manifest CLI {name} has a malformed credential")
            _absolute(item.get("path"), f"{name} credential path")
            _absolute(item.get("source"), f"{name} credential source")
    return data


def fix_hint(manifest: Optional[Dict[str, object]]) -> str:
    return str((manifest or {}).get("fix_hint") or "")


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def member_credentials(manifest: Dict[str, object], profile: str, forbidden: Sequence[Path]) -> List[tuple]:
    """(source, path) pairs the member may write: each a real regular file bound into its own state.

    The manifest decides what becomes writable inside the member, so nothing in it is trusted: a
    source must be a regular file with no symlink on its path, outside the repository, the sidecar,
    the runtime, and the read roots, and its target must sit inside one of the CLI's state folders."""
    entry = manifest["clis"].get(profile)  # type: ignore[union-attr]
    if entry is None:
        raise PanelCliError("cli-missing", f"{profile} is not enabled in this sandbox. {fix_hint(manifest)}".strip())
    states = [Path(item) for item in entry["state"]]
    home = Path.home()
    for state in states:
        if home not in state.parents:
            raise PanelCliError("manifest", f"{profile} state {state} is not inside {home}")
    blocked = [*forbidden, *(Path(item) for item in manifest.get("read_roots", []))]  # type: ignore[union-attr]
    pairs = []
    for item in entry.get("credentials", []):
        source, path = Path(item["source"]), Path(item["path"])
        if not any(state in path.parents for state in states):
            raise PanelCliError("manifest", f"{profile} credential {path} is not inside its state folders")
        if os.path.realpath(source) != str(source) or not source.is_file():
            raise PanelCliError("manifest", f"{profile} credential source {source} is not a real regular file")
        if _inside(source, blocked):
            raise PanelCliError("manifest", f"{profile} credential source {source} is inside a protected folder")
        pairs.append((source, path))
    return pairs


def default_credential_roots(mountinfo: Optional[str] = None) -> List[Path]:
    """Without a manifest: Paseo's mounted credential folders, read from the mount table."""
    roots = set()
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            mountinfo = ""
    lines = mountinfo.splitlines()
    for line in lines:
        fields = line.split()
        if len(fields) > 4:
            point = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
            if DEFAULT_CREDENTIAL_MOUNT.search(point):
                roots.add(Path(point))
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    if config and Path(config).is_dir():
        roots.add(Path(config))
    return sorted(root for root in roots if root.is_dir())


# --- one member ----------------------------------------------------------------


def receipt_instructions(nonce_file: Path, profile: str) -> str:
    lines = [
        "",
        "## Access receipt (required)",
        "",
        f"Before you finish, read the file {nonce_file}. This section overrides the terminal-result "
        "line above: end your final message with exactly these lines after the report, and nothing "
        "after them:",
        "",
        "Inspected:",
        "- <each repository-relative path you read, one per line>",
        "Access receipt: <the exact text of that file>",
        "",
        "The report before these lines must follow the template. Cite files by repository-relative "
        "paths, never absolute paths or file:// links.",
    ]
    if profile == "agy":
        lines.append("Do not create plans or wait for approval: this is a read-only review; answer now.")
    return "\n".join(lines) + "\n"


def confine(argv: Sequence[str], profile: str, scratch: Path, workdir: Path, writable_workdir: bool,
            manifest: Optional[Dict[str, object]] = None, credentials: Sequence[tuple] = (),
            repo: Optional[Path] = None) -> List[str]:
    """Everything read-only except the scratch, this CLI's state or credential, and an optional workdir.

    With the plugin's manifest the member's state is an empty tmpfs and only its credential files
    are bound writable, so nothing it writes outlives the run except a renewed token. Credential
    roots are hidden in both modes; bubblewrap resolves bind sources in the original root, so a
    credential under a hidden root still binds.

    /tmp is private: the native Codex sandbox writes its mount markers there (qualified in a
    sandbox), and the VM's /tmp may hold other sessions' files. The repository, the workdir, and
    the scratch are mounted back over it, parents first."""
    home = Path.home()
    command = ["bwrap", "--ro-bind", "/", "/", "--unshare-pid", "--die-with-parent",
               "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    if repo is not None:
        command += ["--ro-bind", str(repo), str(repo)]
    roots = ([Path(item) for item in manifest.get("credential_roots", []) if Path(item).is_dir()]  # type: ignore[union-attr]
             if manifest is not None else default_credential_roots())
    for root in roots:
        command += ["--tmpfs", str(root)]
    if manifest is not None:
        for item in manifest.get("read_roots", []):  # type: ignore[union-attr]
            if Path(item).exists() and _inside(Path(item), roots):
                command += ["--ro-bind", str(item), str(item)]
        for item in manifest["clis"][profile]["state"]:  # type: ignore[index]
            Path(item).mkdir(parents=True, exist_ok=True)
            command += ["--tmpfs", str(item)]
        for source, path in credentials:
            command += ["--bind", str(source), str(path)]
    else:
        for relative in PROFILES[profile]["state"]:  # type: ignore[union-attr]
            path = home / str(relative)
            path.mkdir(parents=True, exist_ok=True)
            command += ["--bind", str(path), str(path)]
    for other, spec in PROFILES.items():
        if other == profile:
            continue
        listed = ((manifest or {}).get("clis") or {}).get(other, {}).get("state")  # type: ignore[union-attr]
        for state in (listed or [str(home / str(relative)) for relative in spec["state"]]):  # type: ignore[union-attr]
            if Path(state).is_dir():
                command += ["--tmpfs", str(state)]
    for relative in HIDDEN_FILES:
        if (home / relative).is_file():
            command += ["--ro-bind", "/dev/null", str(home / relative)]
    command += ["--bind" if writable_workdir else "--ro-bind", str(workdir), str(workdir),
                "--bind", str(scratch), str(scratch)]
    return [*command, "--chdir", str(workdir), "--", *argv]


def cli_argv(profile: str, model: str, effort: Optional[str], workdir: Path, brief: Path, brief_text: str,
             raw: Path, writable_workdir: bool = False) -> List[str]:
    if profile == "codex":
        extra = ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
        # Codex's own sandbox inside the outer one: a wave member may patch its sidecar (the workspace).
        # workspace-write also claims /tmp, which the outer confinement keeps read-only; without the
        # exclusion every command fails (qualified in a sandbox). TMPDIR points at the scratch.
        mode = ["-s", "workspace-write", "-c", "sandbox_workspace_write.exclude_slash_tmp=true"] \
            if writable_workdir else ["-s", "read-only"]
        return ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-C", str(workdir), *mode,
                "-m", model, *extra, "-o", str(raw), "--json", "-"]
    if profile == "muse":
        extra = ["--reasoning-effort", effort] if effort else []
        return ["muse", "exec", "--workspace", str(workdir), "--model", model, *extra,
                "--prompt-file", str(brief)]
    extra = ["--effort", effort] if effort else []
    return ["agy", "-p", brief_text, "--model", model, *extra, "--mode", "plan", "--output-format", "text",
            "--dangerously-skip-permissions"]


def member_env(profile: str, scratch: Path) -> Dict[str, str]:
    env = {key: os.environ[key] for key in PASSED_ENV if key in os.environ}
    env.update(PROFILES[profile]["env"])  # type: ignore[arg-type]
    (scratch / "tmp").mkdir(exist_ok=True)
    env["TMPDIR"] = str(scratch / "tmp")
    return env


ERROR_LINE = re.compile(r"error|fail|denied|does not exist|not found|invalid|unauthori|limit|timed out|"
                        r"credential|refused", re.I)


def masked_tail(text: str, lines: int = 4) -> str:
    """The lines that explain a failure, preferring error lines over a CLI's warnings, masked."""
    every = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [line for line in every if ERROR_LINE.search(line)]
    tail = errors[-2:] if errors else every[-lines:]
    return SECRET.sub("****", " | ".join(tail))[:500]


def split_receipt(text: str, nonce: str, repo: Path) -> tuple:
    """(report, inspected paths) from a final message that ends with the access receipt."""
    lines = text.rstrip().splitlines()
    if not lines or not re.fullmatch(r"Access receipt:\s*(\S+)\s*", lines[-1].strip()):
        raise PanelCliError("no-access-receipt", "the report does not end with its access receipt")
    if re.fullmatch(r"Access receipt:\s*(\S+)\s*", lines[-1].strip()).group(1).strip("`") != nonce:
        raise PanelCliError("no-access-receipt", "the access receipt does not match the file it names")
    start = max((index for index, line in enumerate(lines[:-1]) if line.strip() == "Inspected:"), default=None)
    if start is None:
        raise PanelCliError("no-access-receipt", "the report has no Inspected: list")
    inspected = [line.strip()[2:].strip().strip("`") for line in lines[start + 1:-1] if line.strip().startswith("- ")]
    existing = [path for path in inspected
                if path and not path.startswith(("/", "file:")) and ".." not in Path(path).parts
                and (repo / path).exists()]
    if not existing:
        raise PanelCliError("no-access-receipt", "Inspected: names no repository file that exists")
    report = "\n".join(lines[:start]).rstrip() + "\n"
    if not report.startswith("# "):
        raise PanelCliError("malformed-report", "the report does not start with the template heading")
    return report, existing


def shim_unmounted(code: Optional[int], stderr: str, hint: str) -> None:
    """The plugin's shim exits 78 with a `paseo-sbx-cli: ` line when the CLI's state is not mounted."""
    if code == SHIM_UNMOUNTED_EXIT and SHIM_PREFIX in stderr:
        lines = [line.strip() for line in stderr.splitlines() if line.strip().startswith(SHIM_PREFIX)]
        raise PanelCliError("not-mounted", f"{SECRET.sub('****', lines[-1])[:300]} {hint}".strip())


def classify(profile: str, code: Optional[int], raw: str, stderr: str, hint: str = "") -> None:
    """Raise the member failure its exit, output, and messages show; return when the run is usable."""
    shim_unmounted(code, stderr, hint)
    signals = f"{raw[-2000:]}\n{stderr[-4000:]}"
    if code != 0 and BUSY.search(signals) and re.search(r"auth|token|credential", signals, re.I):
        raise PanelCliError("credential-busy", f"{profile} could not replace its bound credential file while "
                                               f"renewing it; run {profile} once in the Sandbox terminal to renew "
                                               f"the login: {masked_tail(stderr or raw)}")
    if code != 0:
        if LOGIN.search(signals):
            raise PanelCliError("login", f"{profile} is not signed in: {masked_tail(stderr or raw)}")
        if QUOTA.search(signals):
            raise PanelCliError("quota", f"{profile} reports a usage limit: {masked_tail(stderr or raw)}")
        raise PanelCliError("exit-nonzero", f"{profile} exited {code}: {masked_tail(stderr or raw)}")
    if profile == "agy" and DENIED.search(stderr):
        raise PanelCliError("tool-denied", f"agy denied a tool in headless mode: {masked_tail(stderr)}")
    if not raw.strip():
        raise PanelCliError("empty-output", f"{profile} returned no report: {masked_tail(stderr)}")
    if len(raw) < 600 and QUOTA.search(raw):
        raise PanelCliError("quota", f"{profile} returned a usage-limit message instead of a report")


def run_member(arguments: argparse.Namespace, brief_text: str) -> Dict[str, object]:
    require_sandbox()
    profile = arguments.profile
    manifest = load_manifest()
    version = cli_version(profile, manifest)
    final = Path(arguments.final).resolve()
    scratch = final.parent / "panel-cli"
    scratch.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(12)
    nonce_file = scratch / "ACCESS-RECEIPT.txt"
    nonce_file.write_text(nonce + "\n", encoding="utf-8")
    full_brief = brief_text.rstrip() + "\n" + receipt_instructions(nonce_file, profile)
    brief = scratch / "brief.md"
    brief.write_text(full_brief, encoding="utf-8")
    raw_file = scratch / "raw.md"
    workdir = Path.cwd().resolve()
    repo = Path(arguments.repo).resolve()
    credentials = (member_credentials(manifest, profile, [repo, workdir, Path(__file__).resolve().parent,
                                                          Path.home() / ".gsd-path"])
                   if manifest is not None else [])
    argv = confine(cli_argv(profile, arguments.model, arguments.effort, workdir, brief, full_brief, raw_file,
                            arguments.writable_workdir),
                   profile, scratch, workdir, arguments.writable_workdir, manifest, credentials, repo)
    (scratch / "command.json").write_text(json.dumps([part if part != full_brief else "<brief>" for part in argv],
                                                     indent=1) + "\n", encoding="utf-8")
    with open(scratch / "stdout", "wb") as out, open(scratch / "stderr", "wb") as err:
        completed = subprocess.run(argv, stdout=out, stderr=err, env=member_env(profile, scratch),
                                   input=full_brief.encode("utf-8") if profile == "codex" else None,
                                   stdin=None if profile == "codex" else subprocess.DEVNULL)
    stdout = (scratch / "stdout").read_text(encoding="utf-8", errors="replace")
    stderr = (scratch / "stderr").read_text(encoding="utf-8", errors="replace")
    raw = raw_file.read_text(encoding="utf-8", errors="replace") if PROFILES[profile]["output"] == "file" \
        and raw_file.is_file() else (stdout if PROFILES[profile]["output"] == "stdout" else "")
    if PROFILES[profile]["output"] == "stdout":
        raw_file.write_text(raw, encoding="utf-8")
    classify(profile, completed.returncode, raw, stderr if profile != "codex" else stderr + stdout,
             fix_hint(manifest))
    report, inspected = split_receipt(raw, nonce, Path(arguments.repo).resolve())
    final.write_text(report, encoding="utf-8")
    receipt = {"profile": profile, "version": version, "model": arguments.model, "effort": arguments.effort,
               "inspected": inspected, "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
               "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
               "brief_sha256": hashlib.sha256(full_brief.encode("utf-8")).hexdigest(),
               "reported_model": reported_model(profile, stdout)}
    (scratch / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def reported_model(profile: str, stdout: str) -> Optional[str]:
    """The model the CLI says answered, when its output names one; a claim, not a proof."""
    if profile != "codex":
        return None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        model = event.get("model") if isinstance(event, dict) else None
        if isinstance(model, str):
            return model
    return None


# --- commands and doctor -------------------------------------------------------


def family_commands(repo: Path, kind: str) -> Dict[str, object]:
    config = validate_config(load_config(repo))
    script = Path(__file__).resolve()
    commands = {}
    for family, entry in sorted(config.items()):
        parts = [sys.executable, "-B", str(script), "run", "--profile", entry["profile"],
                 "--model", "{model}", "--repo", str(repo), "--final", "{final}"]
        if entry.get("effort"):
            parts += ["--effort", entry["effort"]]
        if kind == "wave":
            parts.append("--writable-workdir")
        commands[family] = {"command": " ".join(shlex.quote(part) if part not in ("{model}", "{final}") else part
                                                for part in parts), "output": "file"}
    return {"family_commands": commands,
            "advertised": ",".join(entry["model"] for _, entry in sorted(config.items())),
            "families": sorted(config)}


def doctor(repo: Path) -> Dict[str, object]:
    report: Dict[str, object] = {"sandbox": in_sandbox(), "sandbox_name": os.environ.get("SANDBOX_NAME"),
                                 "bwrap": None, "families": {}, "fixes": []}
    name = os.environ.get("SANDBOX_NAME") or "<sandbox>"
    try:
        manifest = load_manifest()
    except PanelCliError as error:
        report["manifest_error"] = str(error)
        manifest = None
    if manifest is not None:
        # Provisioning is the plugin's and the operator's: the scope is reported, never checked.
        report["manifest"] = {"scope": manifest.get("scope"), "clis": sorted(manifest["clis"])}  # type: ignore[arg-type]
    if shutil.which("bwrap"):
        probe = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-pid", "--dev", "/dev", "--proc", "/proc",
                                "true"], capture_output=True, encoding="utf-8", errors="replace")
        report["bwrap"] = "ok" if probe.returncode == 0 else masked_tail(probe.stderr)
    else:
        report["bwrap"] = "missing"
    try:
        config = validate_config(load_config(repo))
    except ValueError as error:
        report["config_error"] = str(error)
        config = {}
    for family, entry in sorted(config.items()):
        profile, item = entry["profile"], {"profile": entry["profile"], "model": entry["model"]}
        try:
            item["version"] = cli_version(profile, manifest)
            spec = PROFILES[profile]
            probe = subprocess.run(list(spec["login"]), capture_output=True, encoding="utf-8",  # type: ignore[arg-type]
                                   errors="replace", timeout=45, stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, **spec["env"]))  # type: ignore[arg-type]
            signals = probe.stdout + probe.stderr
            item["login"] = "ok" if probe.returncode == 0 and not LOGIN.search(signals) else "missing"
        except subprocess.TimeoutExpired:
            item["login"] = "missing"
        except PanelCliError as error:
            item["error"] = str(error)
        if item.get("login") == "missing" and manifest is not None:
            login = manifest["clis"][profile].get("login") or LOGIN_COMMANDS[profile]  # type: ignore[index]
            report["fixes"].append(f"{profile}: run `{login}` in the workspace's Sandbox terminal. "
                                   f"{fix_hint(manifest)}".strip())
        elif item.get("login") == "missing":
            report["fixes"].append(f"XDG_STATE_HOME=~/.local/state/paseo-sbx/sbx sbx exec -it {name} "
                                   f"{LOGIN_COMMANDS[profile]}")
        elif "error" in item:
            report["fixes"].append(f"{profile}: {item['error']}")
        report["families"][family] = item
    report["ok"] = (report["sandbox"] and report["bwrap"] == "ok" and not report.get("config_error")
                    and not report.get("manifest_error")
                    and all(item.get("login") == "ok" for item in report["families"].values()))
    return report


LOGIN_COMMANDS = {"codex": "codex login --device-auth", "muse": "env MUSE_NO_AUTO_UPDATE=1 muse login",
                  "agy": "agy  # sign in from its interface, then quit"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("commands", help="print --family-commands for the configured families")
    listing.add_argument("--repo", type=Path, required=True)
    listing.add_argument("--kind", choices=("wave", "plan"), required=True)
    listing.add_argument("--output", type=Path, help="also write the family commands JSON here")
    member = commands.add_parser("run", help="run one panel member on the brief from stdin")
    member.add_argument("--profile", choices=tuple(PROFILES), required=True)
    member.add_argument("--model", required=True)
    member.add_argument("--effort")
    member.add_argument("--repo", type=Path, required=True)
    member.add_argument("--final", type=Path, required=True)
    member.add_argument("--writable-workdir", action="store_true",
                        help="let the member write its working directory (a wave panel's verify sidecar)")
    check = commands.add_parser("doctor", help="check the sandbox and each configured CLI")
    check.add_argument("--repo", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "commands":
            result = family_commands(arguments.repo.resolve(), arguments.kind)
            if arguments.output:
                arguments.output.write_text(json.dumps(result["family_commands"], indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if arguments.command == "doctor":
            result = doctor(arguments.repo.resolve())
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        receipt = run_member(arguments, sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except PanelCliError as error:
        print(f"panel-cli: {error}", file=sys.stderr)
        return FAILURE_EXIT
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"panel-cli: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
