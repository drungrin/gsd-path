# CLI panel members (Docker Sandboxes only)

Fork-only. Built for the Docker Sandboxes (`sbx`) Paseo runs GSD Path orchestrators in. A panel
family configured in `review_panel_cli` runs in its vendor's headless CLI instead of a native
child: `gpt` in Codex, `muse` in Muse Code, `gemini` in Antigravity (`agy`). The dispatch driver
captures each member's final report and writes the family file; the member needs no write
access. Coders, canonical reviewers, and skeptics are unchanged. Outside a sandbox the runtime
refuses, and the panel stays native.

## With the paseo-sbx plugin

When the paseo-sbx plugin provisions the CLIs, it sets `PASEO_SBX_CLI_MANIFEST` to a read-only JSON
manifest. Provisioning, login scope, and credential sharing stay the plugin's and the operator's;
the runtime checks none of them. It reads the manifest only to confine a member:

- the manifest's credential roots are hidden, and its read roots inside them are mounted back
  read-only;
- the member's CLI state is an empty tmpfs, and only that CLI's credential files are bound
  writable, so a renewed token persists and nothing else the member writes does;
- every credential binding is checked first: the source must be a real regular file outside the
  repository, the sidecar, the runtime, and the read roots, and the target must sit inside that
  CLI's state.

Without a manifest, Paseo's credential mounts (`homes`, `cli-auth`, the session's Claude folder)
are hidden by pattern, and the member's CLI state is bound writable as before. A manifest whose
major version this runtime does not read is refused (`manifest`).

## When it applies

Run `python3 -B <absolute panel_cli.py> commands --repo <absolute root> --kind <wave|plan>
--output <absolute family-commands.json>` from the active skill's bundled scripts. Write the file
outside `.project/`, in the session's work folder. An empty `families` list means no CLI members:
use the native panel procedure unchanged. Otherwise:

- Run `python3 -B <absolute panel_cli.py> doctor --repo <absolute root>` before the first panel
  of the phase. `ok: false` is a member failure for the families it names: stop as below and
  show its `fixes`. With the plugin they point to its workspace panel and to the CLI's login
  command in the Sandbox terminal; without it, to the exact `sbx exec` commands the owner runs on
  the host. The plugin's login scope is reported, never checked.
- Pass `--advertised` the command's `advertised` slugs plus the host-advertised slugs of any
  family without a CLI profile. Such a family stays native.
- Model policy does not apply to a CLI family: its model and effort come from
  `review_panel_cli`. Native families keep the dispatch model-policy contract.

## Wave panel

Run the driver's panel action even when the rest of the build is native:

```text
python3 <absolute dispatch_driver.py> panel --repo <absolute primary> --wave <N> --cycle <C> \
  --family-commands <absolute family-commands.json> --advertised <slugs> \
  --parent-slug <current model slug when known> --exclude-family <canonical-reviewer family> \
  [--child-command '<owner command>'] [--wait <owner seconds>]
```

Repeat `--exclude-family` for every known parent and canonical-reviewer family. Pass
`--child-command` only in a driver build, with the same command the review used. Act on
`status`:

- `pass`: the driver merged `wave-N.cycleC.panel.md` and made the on-pass checkpoint. Continue
  as after a passing panel.
- `native-pending`: for each `native` entry, run that family's native child as the native panel
  procedure says, collect its report at the entry's `relative` path, then rerun the same command
  with `--native-collected <family>`.
- `in-flight`: rerun the same command.
- `blocked`: a member failure, below. Any other blocked reason follows the driver's receipt.

## Plan panel

```text
python3 <absolute dispatch_driver.py> plan-panel --repo <absolute root> --track <track, relative> \
  --family-commands <absolute family-commands.json> --advertised <slugs> \
  --parent-slug <current model slug when known> [--wait <owner seconds>]
```

- `off`: as in the plan skill.
- `skipped`: the driver wrote `<track>/review/PLAN-PANEL.skipped.json` and removed a stale
  `PLAN-PANEL.md`. Make the plan skill's unchanged-state transition and continue.
- `pass`: `<track>/review/PLAN-PANEL.md` is merged. Continue to the approval question.
- `native-pending`: dispatch each `native` family natively, validate its report, and rerun with
  `--native-report <family>=<absolute report path>`.
- `blocked`: a member failure, below.

## Member failure

A failed member's blocked `reason` reads `child exited 3: panel-cli: <code>: <detail>`. The codes
are: `not-sandboxed`, `no-bwrap`, `manifest`, `cli-missing`, `cli-version`, `not-mounted` (the
plugin's shim found the CLI's state unmounted), `login`, `credential-busy` (a renewal could not
replace the bound credential file), `quota`, `exit-nonzero`, `tool-denied`, `empty-output`,
`no-access-receipt`, `malformed-report`, plus `child timed out`. Stop and present **Outcome** (family, code, detail), **Review** (the member's
`panel-cli/raw.md` and `stderr` in its dispatch attempt), and **Next** with two options:

1. Wait: fix the cause and rerun the same command with `--retry-family <family>`. For `login`,
   `cli-missing`, `cli-version`, `not-mounted`, and `credential-busy`, recommend this option: the
   fix is one `doctor` command, a plugin setting, or one run of the CLI in the Sandbox terminal.
2. Continue without it: rerun with `--accept-absent <family> --ruling '<the owner's words>'`. The
   merged panel lists the family as absent with the ruling.

For `quota`, a timeout, and the other codes, choosing between delay and an independent review is
the owner's call: offer both without a recommendation. Never accept an absence without the
owner's ruling, and never replace a failed CLI member with a native child.

## Limits

- A CLI that renews its token by replacing the credential file cannot do so on a single-file
  binding; the member blocks with `credential-busy` and the owner renews outside the panel.
  Codex renewal under the binding is not qualified.
- The plugin's CLIs do not include a panel profile for `claude`.

- The model that answered is the CLI's claim; Codex's event stream does not name it.
- The sandbox network stays open; confinement covers files, not egress.
- Each profile accepts only the CLI versions qualified in a sandbox; another version blocks with
  `cli-version` until it is qualified.
