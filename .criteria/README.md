# Acceptance Criteria

Contract directory between Codex (author) and Claude Code (implementer).

## File format

One file per feature: `.criteria/<short-slug>.md`

```markdown
# <Feature name>

## Context
<1-3 sentences: why this exists, what problem it solves>

## Scope
- In: <bullet list of what's included>
- Out: <bullet list of explicitly excluded>

## Criteria

### AC-1: <short name>
- **Given** <precondition>
- **When** <action>
- **Then** <observable outcome>

### AC-2: ...
```

## Rules

- Criteria must be observable (testable from outside the system) — no "code should be clean".
- One AC = one test (ideally). If an AC needs multiple tests, split it.
- Codex writes these. Claude reads and implements. Neither crosses the line.
- When Claude ships, the PR description references AC-N for each commit/change.
