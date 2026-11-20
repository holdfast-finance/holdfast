---
name: adr
description: >
  Write a Holdfast Architecture Decision Record. Use whenever a non-obvious
  technical decision is made: choosing a library or service, deviating from
  PLAN.md, adding an external dependency or service (hard rule 9), or settling
  a trade-off. Trigger phrases: "write an ADR", "record this decision", /adr.
---

# ADR

1. Next number: list `docs/adr/`, sort, and increment the four-digit prefix.
2. Write `docs/adr/NNNN-kebab-title.md`:

```markdown
# NNNN. <Title>

- Status: Accepted | Proposed | Superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: <Agent> (Holdfast). The owner sees it in the weekly digest.

## Context
What forces are at play. Cite PLAN.md sections and research where relevant.

## Decision
What we are doing, stated plainly.

## Alternatives considered
- Option: why not (one line each)

## Consequences
What gets easier, what gets harder, and the follow-ups.
```

3. If this supersedes an older ADR, update that ADR's Status line.
4. If it changes a hard rule or a command, update `CLAUDE.md` in the same change. `.claude/` and `CLAUDE.md` are protected paths: a separate Bulwark run must sign the head SHA before Keel merges.
5. Commit: `docs(adr): NNNN <title>`, attributed to the agent who decided.
