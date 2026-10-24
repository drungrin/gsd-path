# Decision lenses

Sixteen one-line questions, each named after a law of software, applied at
the owner decisions below before the recommended option is named. They
change only an existing decision. This file is self-contained; nothing else
needs to be loaded.

## Contract

1. A lens runs only at a gate in the table below, once per evidence scope,
   on the facts that changed since its last answer. "No signal" is a valid
   answer.
2. A lens adds no report, dispatch, research assignment or question. Its only
   effect is on the decision being asked: the recommended option, its
   one-line reason, or one added option. Pre-authorized approvals still
   record the lens event and ask nothing.
3. Verdicts: `keep`, `narrow`, `defer-next-milestone`, `fail-closed`,
   `redesign`, `bounded-measurement`, `add-option`.
4. A recommendation that changes INTENT.md, the review panel, or the plan
   states its recovery cost first: an INTENT.md change sends every wave back
   to review; a `review_panel` change invalidates all wave evidence.
5. **Security exception.** Absence of an observed occurrence never clears a
   reachable violation of an approved security, isolation, credential,
   data-loss or irreversible-state invariant. A lens recommendation that
   would drop, defer or bypass such a finding is recorded as `blocked` and
   does not change the decision.
6. **One exception to rule 2.** A `redesign` or `fail-closed` verdict at the
   `fix-routing` gate invokes the existing redesign ruling (build-native
   step 7) before fix tasks are created. No other gate adds a question.

## Precedence

When lenses disagree at one decision:

1. the security exception;
2. the authority order in `AGENTS.md`: no lens waives a CHARTER.md or
   INTENT.md criterion or veto; a contract change goes through define
   Corrections with its cost stated;
3. evidence lenses: L3, L4, L5, L7, L11;
4. approach lenses: L6, L12, L13, L14, L16;
5. scope and budget lenses: L1, L2, L8, L9, L10, L15.

A higher class sets the recommendation. A lower class may not remove an item
a higher class keeps. Within one class, recommend the option that changes
fewer approved contracts and offer the other.

## Lenses

| Lens | Question |
|---|---|
| L1 Gall | What is the smallest independently shippable slice that keeps required invariants, does it work integrated per evidence already in hand, and which addition is optional? Never defer failures required to complete an approved shipment. |
| L2 Parkinson | Which owner-approved budget applies (review cycles, waves), how is usage counted, and what stops when it runs out? Exhaustion escalates; it never certifies. |
| L3 Goodhart | Is this criterion a proxy or a required invariant? What reachable failure does this finding show within the input domain the product must support? |
| L4 Kerckhoffs | What can the adversary read, submit or modify, and who controls enforcement? Does correctness depend on hiding the mechanism? |
| L5 Postel | Which forms are supported; are unknown forms rejected, tolerated or escalated; where is that written; which existing valid inputs must stay accepted? |
| L6 Greenspun | Are we implementing language semantics? Can a bounded representation or existing mechanism meet the same invariant with less total work? |
| L7 Hyrum | Which preserved observable behaviors does this change alter on purpose, and do those tests protect semantics or incidental output (exact counts, messages, order)? |
| L8 Linus | What unique, decision-relevant findings has this panel produced on this risk class, and is another pass justified? |
| L9 Zawinski | Is this addition necessary for the approved outcome, or an unrelated responsibility? Name the dependency and its owner. |
| L10 Conway | Who owns the moving interface, what exact base is pinned, and what evidence requires adopting the new base now? |
| L11 Shirky | Does the recommendation follow from this finding's consequence and severity, rather than from its presence? |
| L12 Kernighan | Can the invariant, the supported input domain and the unknown-input behavior be stated compactly? Are repeated repairs changing the same design assumption? |
| L13 Leaky abstractions | What bounded lower-layer diagnosis is needed, and can an already-authorized fallback resolve it at one seam without changing product contracts? |
| L14 Boring technology | What new operational dependency is this, what existing alternative was considered, and what is the bounded fallback? |
| L15 Hofstadter | What comparable actuals already exist (archive manifests, logs), including verify and recovery time? If none, say so; no measurement project. |
| L16 Knuth | What measured cost (verify queue, full-suite reruns) justifies this verification design, and where is the measured bottleneck? |

## Gates

Each gate answers exactly its lenses. L5 and L6 marked `*` answer
`no-signal` unless the finding concerns a guard, validator, scanner or
analyzer.

| Gate | Decision | Scope | Lenses | Enforced by |
|---|---|---|---|---|
| `define` | INTENT.md approval | milestone | L1 L2 L3 L8 L10 | `pipeline_state.py transition` |
| `charter` | CHARTER.md approval | program | L1 L2 L3 L9 | `pipeline_state.py transition` |
| `corrections` | INTENT.md Corrections approval after build recovery | milestone, recovery | L3 L7 L9 | `pipeline_state.py transition` |
| `decide` | `NEEDS-USER` ruling in decide | ruling | L4 L5 L6 | contract |
| `roadmap` | roadmap approval or re-slice | program | L1 L2 L9 L10 L15 | `pipeline_state.py approve` |
| `plan` | plan approval, including build repair | milestone, recovery | L2 L6 L7 L8 L11 L12 L15 L16 | `pipeline_state.py approve` |
| `patch` | patch-mode plan approval | milestone | L1 L7 L9 | `pipeline_state.py approve --patch` |
| `recovery` | entry into build recovery | recovery | L1 L2 L9 L10 | contract |
| `panel` | panel ruling: canonical pass with actionable panel findings, or blocked panel result | `wave N cycle C` | L3 L5* L6* L8 L11 | panel-ruling checkpoint |
| `refuted` | all-refuted ruling | `wave N cycle C` | L3 L8 | skeptic-ruling checkpoint |
| `redesign` | redesign ruling | `wave N cycle C` | L2 L3 L4 L5* L6 L12 | redesign-ruling checkpoint |
| `cap` | cycle-cap ruling | `wave N cycle C` | L2 L3 L5* L16 | cap-ruling checkpoint |
| `fix-routing` | before `dispatch_driver.py fix-tasks` | `wave N cycle C` | L2 L3 L5* L6 L11 L12 | `dispatch_driver.py fix-tasks` |
| `rebase` | member rebase adoption | `member <m> base <sha12>` | L10 L14 | `isolation.py adopt-member-rebase` |
| `ship` | archive-and-ship question | milestone | L1 L3 | `pipeline_state.py transition` |
| `blocker` | an owner ruling already required for a blocked task, helper failure or publication block | task or artifact | L13 L14 L16 | contract |

Helpers enforce a gate only when the milestone INTENT.md (program gates:
CHARTER.md) contains `Decision lenses: enforced`. A milestone without the line
is never refused, but the contract still applies.

## Recording

Before naming the recommended option, record one Log event with the log-only
`pipeline_state.py transition` the phase already uses: every `--expect-*`
and `--set-*` field equal to the current position, `--project-dir` for the
routed track, and the event text, for example:

```text
lenses redesign wave 2 cycle 3: fired L12=redesign, L3=fail-closed; blocked none; no-signal L2, L4, L5, L6
```

Grammar: `lenses <gate>[ <scope>]: fired <Ln=verdict, …|none>; blocked
<Ln, …|none>; no-signal <Ln, …|none>`. Every lens of the gate appears exactly
once. There is no free text: reasons go into the question's recommendation,
never into the event. The event must come after the previous decision of the
same gate and scope and, for build gates, after the latest build recovery.
A retry or resume of the same decision reuses the recorded event.

In the question, cite the lenses that changed the recommendation in its
one-line reason, for example `(recommended) — L12: three repairs changed the
same assumption`.
