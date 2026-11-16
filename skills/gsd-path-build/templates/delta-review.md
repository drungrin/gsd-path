<!-- PR fix lane delta section (ADR 0006). Add it to the wave review of the
     `Review depth: delta` wave, after the task sections, and record
     `Depth: delta` (no Lens line). The wave review keeps every ordinary field and
     task section. Judge the landed lane diff against the change-request items in
     PATCH-FINDINGS.md and against the baseline FINAL.md and gap reports named in
     the brief. Keep each field on one line. -->

## PR fix delta

Eligibility: <local | not-local — not-local when the diff changes a success criterion, an interface contract, or a design decision>
Risk inventory: <complete | new — new when the diff opens an integration risk the baseline gap list lacks>

<!-- One heading per fix item named in the brief, with its Locator id. -->
### Item <id>: <closed | not-closed>

- **Evidence**: <file:line or test output that shows the item closed, or what is still missing>

<!-- One heading per INTENT success criterion, in order. -->
### SC1 — <affected | unaffected>

- **Reason**: <for unaffected: why the lane diff cannot change this criterion's evidence>

<!-- For an affected criterion, replace Reason with the full final-review fields:
- **Verdict**: <met | not-met | unverifiable>
- **Surface**: <only for a Surface contract criterion>
- **Check**: `<command or walkthrough actually performed>`
- **Observed**: <what it showed>
- **Reference**: <file:line or artifact>
- **Finding**: <none, or what was found>
- **Fix direction**: <none, or the direction>
-->

<!-- One heading per baseline gap risk from 2 (gap 1 is the project Verify). -->
### Gap 2 — <affected | unaffected>

- **Reason**: <for unaffected: why the lane diff cannot change this risk>

<!-- For an affected gap, replace Reason with:
- **Gap verdict**: <pass | blocked>
- **Check**: `<command actually run>`
- **Observed**: <output or behavior>
- **Reference**: <file:line or artifact>
- **Found**: <evidence-backed result>
- **Fix direction**: <none when pass>
-->
