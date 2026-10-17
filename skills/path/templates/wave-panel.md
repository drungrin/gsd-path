# Panel — wave <N>, cycle <C>

<!-- Written by one review-panel child. Advisory only: the canonical reviewer
     owns Wave verdict. Kind criterion may be presented to the user when
     Severity is high; preference stays a warning. -->

- Family: <claude | gpt | grok | composer | gemini | deepseek | kimi | qwen | muse>
- Model: <exact advertised slug>
- Depth: <full | adversarial>

## Findings

<!-- One ### F# block per finding, or a single `- none` line. Evidence cites
     repo-relative source paths with a line or range, separated by `;`.
     If attribution is unresolvable, state why as a warning, not a reason to
     reject the finding. Preferences never count toward recurrence. -->

### F1
- Severity: high
- Kind: criterion
- Criterion: T001 acceptance 1
- Evidence: src/app.py:12-16; src/rules.py:7
- Found: <what exists instead>
- Fix direction: <concrete coder direction>

## Summary

<one paragraph; do not edit code, tasks, or the canonical wave review>
