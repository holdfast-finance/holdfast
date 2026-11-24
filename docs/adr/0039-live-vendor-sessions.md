# 0039. Live Codex and Grok sessions on the owner's host

- Status: Accepted
- Date: 2026-11-24
- Deciders: Owner; Keel
- Amends: ADR 0019 (who may reach real data), ADR 0036 (scope of the confinement rule)

## Context

ADR 0019 lets Holdfast's own Claude agents read the owner's real statements locally and keeps
them away from every other vendor's tool. ADR 0036 enforces that by starting every Codex or
Grok worker through `scripts/fc-delegate.sh`, which hides the private data directory.

On 2026-11-24 the owner set up the triad (`docs/process/TRIAD.md`): Claude (Keel), Codex (Anvil)
and Grok (Helm) each lead a team and build full-stack toward 1.0. The owner runs Anvil and Helm
as live sessions in their own terminals and controls them directly. Those sessions are not
started through the wrapper, so they can reach this machine's files. The owner's reasoning:
Claude's sessions can already reach the same files, and the content Claude reads goes to its
vendor exactly as Codex's and Grok's reads go to theirs. The line in ADR 0019 was a trust
choice between vendors, not a technical boundary, and the owner is entitled to move it.

## Decision

1. **Live sessions are allowed.** Codex and Grok sessions that the owner opens and controls in
   a terminal on this host may run unconfined. The owner accepts that they can reach the private
   data directory, as Claude's sessions can.
2. **Reach is not use.** No session from any vendor opens the private data directory unless a
   story's brief needs it. Every triad story to date uses synthetic fixtures. Everything else in
   ADR 0019 stands: real data and anything derived from it never enter a commit, PR, issue, log,
   fixture, build context, release, or the bus (`docs/comms/bus/`).
3. **Headless workers stay confined.** Any Codex or Grok job that Keel launches unattended still
   starts through `scripts/fc-delegate.sh` (ADR 0036, FIRSTCHAIR rules 1–6). ADR 0036 now covers
   headless workers only.
4. **Both live sessions build.** Anvil and Helm may commit on their own branches with their own
   trailers and open PRs. The "Codex writes, Grok reads" rule applies to headless jobs only, and
   a headless Grok write job still needs Keel's explicit brief. Only Keel merges, tags and
   releases. Keel owns design, frontend and architecture decisions.

## Alternatives considered

- Keep every vendor session behind the wrapper: the owner rejected it, because it blocks the
  live, owner-steered collaboration the triad needs.
- Move real data to a second OS user now: the right long-term boundary (ADR 0036's residual
  risk), but it doesn't block this decision. It stays a follow-up.

## Consequences

- Anvil and Helm can work interactively, with the owner steering, at full speed.
- Three vendors can now reach the owner's real data, not one, so the blast radius of a bad
  session grows. The mitigation is behavioral (item 2) plus the existing scans. Scans run
  before every push: gitleaks, `scripts/check-private-data.sh` and the private `scan_rev.py`.
- `CLAUDE.md` and `.claude/rules/security.md` are updated in the same commit.
- Follow-up: a separate OS user that owns the private data directory, so that no coding session
  from any vendor can read it without an explicit grant.
