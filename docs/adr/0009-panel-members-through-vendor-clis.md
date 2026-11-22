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
7. **CLI versions are pinned.** Each profile accepts only the versions qualified in a sandbox.

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
- Upgrading a CLI requires qualifying the new version.
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
