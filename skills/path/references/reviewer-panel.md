## Plan panel mode

Use only plan-panel.md. Review `.project/plan/PLAN.md` and every task file
against INTENT.md and SYNTHESIS.md. Do not edit those files. Write one
finding block per issue, or `- none`. Mark `Kind: criterion` only when the
finding names a written plan/task criterion; alternate designs are
`Kind: preference`. This output is advisory. The structural plan gate and
the user's approval remain the only plan gates.

## Wave panel mode

Use only wave-panel.md. Apply every Wave mode evidence rule (recorded SHAs,
isolated patch, recorded Verify) when the brief supplies a verify sidecar.
On a `deep` wave the brief is the adversarial lens only. Record findings
the same way as plan panel mode. Do not write a Wave verdict and do not
edit the canonical wave-review file. The canonical reviewer remains the only
pass/fail.

## Muse family

Use `muse` in a named panel (for example, `gpt,muse`) or let `detected`
include it when the host advertises a Muse model. Model IDs such as
`muse-spark-1.3`, `muse-spark-1.3-contributor`, `muse-spark-1.2`,
`muse-spark-1.2-contributor`, and `muse-spark-1.1` belong to the same family.
`muse-wonder-1.0` is also recognized as `muse`; pin the exact advertised ID
with an assignment override to choose a model line rather than version order.
Provider-qualified IDs such as `opencode-go/muse-spark-1.3-contributor` retain
their exact advertised spelling. No alias using another family is required.

The resolver selects the newest advertised version and prefers Standard over
Contributor when both variants of that version are advertised. Contributor
remains selectable when it is the only advertised variant or when explicitly
selected through the existing assignment model override. The host still owns
availability and supported effort levels; never infer access from this list.
The three-family cap and exclusions for the parent and canonical reviewer
also apply to Muse.

[Meta's model catalog](https://dev.meta.ai/docs/models) lists the exact IDs.
Standard content is not used for training; Contributor content may be used
for training in exchange for lower pricing. Spark 1.3 Standard supports `max`
reasoning, while its Contributor variant does not. Read the current catalog
and host capabilities before choosing a mode or effort level.
