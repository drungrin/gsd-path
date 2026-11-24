# Review-panel members through vendor CLIs in Docker Sandboxes

Status: accepted (2026-11-21), implemented in fork 1.7.1.68, after a two-model review panel
on the design and a qualification of the three CLIs inside a sandbox.

Fork-only for the CLI members, the confinement, and the absence ruling. The per-family captured
transport and the driver's plan-panel action apply upstream.

## Context

A review panel runs one member per model family. On the Claude host, the fork ran every member as
a native Claude Code subagent whose model alias a local proxy routed to the other vendor. The
parent then published the member's final message by reading the subagent transcript
(`isolation.py stage-review`, fork only).

That decision produced a series of symptom fixes:

- 1.6.2.30 to 1.6.2.33: the host's `SubagentHandback` ending, first accepted only for proxied
  transcripts, then for native ones.
- 1.6.2.34: a resumed panel child could not publish the next cycle's report.
- 1.7.1.42: a final text with no stop reason. In a survey of 899 child transcripts, 17 of the
  18 cases came from a proxied muse member and none from a native Claude model.

A later sweep of 758 panel members over 16 days, on runtimes from 1.7.1.42 on, found:

| | gpt members | muse members |
|---|---|---|
| Published | 93.8% | 83.9% |
| Ended without a stop reason | 2% | 22% |
| Published only by the 1.7.1.42 rule | 5 | 53 |

Publication refusals continued about once a day across consumers. Many of them came from the
parent's side of the transcript transport: republishing over a staged file, a guard refusal, or a
model identity it passed wrong.

## Decision

1. **A family can run in its vendor's headless CLI.** The `review_panel_cli` setting maps
   `gpt` to Codex, `muse` to Muse Code, and `gemini` to Antigravity (`agy`), with a model of that
   family. It is set per project, with an optional user default. A family without a profile stays
   native.
2. **The driver owns the report.** `dispatch_driver.py panel` and the new `plan-panel` take a
   command per family. The driver captures the member's final message and writes the family file.
   The member needs no write access, and no transcript is read.
3. **Confinement by mount, not by flags.** `panel_cli.py run` wraps each CLI in bubblewrap, with
   the whole filesystem read-only. Only a private scratch directory, the CLI's own state, and a
   wave panel's verify sidecar are writable. Other CLIs' state and the Claude credentials are
   hidden, and only an allowlisted environment reaches the member.
4. **Docker Sandboxes only.** The setup was built and qualified for the sandboxes Paseo runs
   orchestrators in. Outside one, `panel_cli.py` refuses.
5. **A member must prove it read.** The brief asks for an access receipt: a nonce read from a
   file, plus an `Inspected:` list of repository paths that must exist. A report of `- none` is
   valid only with that receipt.
6. **A failed member blocks, and the owner rules.** The failure carries a stable code. The owner
   either waits and reruns with `--retry-family`, or continues with `--accept-absent` and a
   verbatim `--ruling`, which the merged panel records. A failed CLI member is never replaced by
   a native child.
7. **CLI versions are recorded, not pinned.** Each member's receipt records the installed version.
   Until 1.7.1.73 each profile accepted only the versions qualified in a sandbox; the owner
   dropped that check, so a version that breaks a member fails as any other member failure.

## Evidence for the confinement

The qualification ran in a disposable sandbox with a fresh login of each CLI, on a synthetic
repository with a planted defect, a nonce file, and a writable mount standing in for a shared
runtime directory:

- With their bypass flags, Codex and Muse Code wrote to the shared mount and to the repository.
- Inside the bubblewrap confinement, all three CLIs read the repository, found the planted
  defect, and wrote nothing.
- Codex's own `read-only` sandbox works inside a sandbox VM and inside the outer confinement. A
  wave member uses `workspace-write` on its sidecar, with `/tmp` excluded: without the exclusion
  Codex's sandbox could not start under the read-only `/tmp`, the member read nothing, and the
  missing access receipt blocked it instead of passing an empty report.
- Muse Code reads without `--yolo`. Its `--yolo` also trusts the workspace and loads the reviewed
  repository's `.mcp.json`, so the profile does not use it.
- Antigravity in headless mode exits 0 with no output when it denies a tool. The profile runs it
  with its bypass and `--mode plan` inside the confinement, and an empty report is a failure.
- Without a login, each CLI exits nonzero with a recognizable message.

## Consequences

- Each consumer sandbox needs the three CLIs installed and logged in, one login per sandbox, in
  the VM's home. `panel_cli.py doctor` prints the missing `sbx exec` commands. A recreated
  sandbox loses them.
- A CLI upgrade is not checked in advance; a break shows up as a member failure.
- The model that answered remains the CLI's claim. Codex's event stream does not name it.
- The sandbox network stays open; the confinement covers files, not egress.
- The native panel path and `stage-review` remain for families without a profile.

## Alternatives considered

- **Keep patching the transcript transport.** Rejected by both panel members: it is the
  recurring decision.
- **Let the CLI write its own report file.** Rejected: it gives write access to the least
  trusted writer, over the artifact the merge trusts.
- **Free-form command templates in the owner's configuration.** Rejected in favor of pinned
  profiles: failure classification and the receipt depend on each CLI's output, and that logic
  would move into untested configuration.

## Revision: the paseo-sbx plugin manifest (fork 1.7.1.69)

The paseo-sbx plugin now installs, updates, and logs in the CLIs inside sandboxes, and writes a
read-only manifest (`PASEO_SBX_CLI_MANIFEST`) with each CLI's state folders and credential files,
the folders a member must read, and the folders that hold credentials. Provisioning, login scope,
and credential sharing are the plugin's and the operator's; the runtime checks none of them.

- **Confinement with a manifest.** The credential roots are hidden; read roots inside them are
  mounted back read-only. The member's state is an empty tmpfs, and only its own credential files
  are bound writable. A renewed token persists; configuration a member plants does not.
- **The manifest is not trusted.** It decides what becomes writable, so each binding is checked:
  a real regular file outside the repository, the sidecar, the runtime, and the read roots, bound
  inside that CLI's state. An unknown major version is refused.
- **Without a manifest** the defect found in review is closed by pattern: Paseo's credential
  mounts (`homes`, `cli-auth`, the session's Claude folder) are hidden, read from the mount table.
  A general allowlist of readable paths remains a later hardening.
- **New failure codes.** `not-mounted` for the plugin shim's exit 78, `credential-busy` when a
  renewal cannot replace the bound credential file, and `manifest`.
- **Evidence.** On the host, with the real bubblewrap, a member reads and renews its own bound
  credential, cannot read another CLI's credential through its home path or its source, and its
  planted configuration does not survive. Replacing a bound file by rename fails with `EBUSY`;
  Codex renewal under the binding remains unqualified and fails closed.

