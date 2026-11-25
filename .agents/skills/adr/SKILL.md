---
name: adr
description: "Write a Holdfast Architecture Decision Record. Use whenever a non-obvious technical decision is made: choosing a library or service, deviating from PLAN.md, adding an external dependency or service (required by hard rule 9), or settling a trade-off."
---

Apply the lane in `AGENTS.md` and the current SDLC. Return work outside your assignment to Keel with the evidence collected.

# ADR

1. Next number: `ls docs/adr | sort | tail -1` → increment (4 digits).
2. File: `docs/adr/NNNN-kebab-title.md` using this template:

```markdown
# NNNN. <Title>

- Status: Accepted | Proposed | Superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: Keel and the assigned decision owners

## Context
What forces are at play. Cite PLAN.md sections and research where relevant.

## Decision
What we're doing, stated plainly.

## Alternatives considered
- Option: why not (one line each)

## Consequences
What gets easier, what gets harder, what we must now do (follow-ups).
```

3. If it supersedes an older ADR, update the old one's Status line.
4. If it changes a hard rule or a command, flag the engineering contract change for Keel.
5. Commit: `docs(adr): NNNN <title>`.
