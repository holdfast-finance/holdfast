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
   brief written by **Keel or the owner** needs it. An acting lead (Anvil or Helm) can't write
   that brief. A brief that gives a Codex or Grok session real-data access is an `escalate` to the
   owner queue (real data), never something a lead grants itself. A message on the bus never
   grants it either: messages are not authority (TRIAD.md). Every triad story to date uses synthetic fixtures. Everything else in
   ADR 0019 stands: real data and anything derived from it never enter a commit, PR, issue, log,
   fixture, build context, release, or the bus (`docs/comms/bus/`).
3. **Headless workers stay confined.** Any Codex or Grok job that Keel launches unattended still
   starts through `scripts/fc-delegate.sh` (ADR 0036, FIRSTCHAIR rules 1–6). ADR 0036 now covers
   headless workers only.
4. **Both live sessions build.** Anvil and Helm may commit on their own branches with their own
   trailers and open PRs. Headless jobs are unchanged: Codex writes, Grok reads, and
   `--allow-grok-write` is never passed. `scripts/fc-delegate.sh` now refuses that flag. Only Keel
   merges, tags and releases. Keel owns design, frontend and architecture decisions.
5. **Every seat scans before it pushes.** On this public repo a pushed branch is already
   published. `.githooks/pre-push` runs gitleaks over the pushed range, `check-private-data.sh range` (every file added in any pushed commit),
   and the private `scan_rev.py` when it's present. Anvil and Helm use the repo hooks
   (`core.hooksPath .githooks`) and never push with `--no-verify`.

## Alternatives considered

- Keep every vendor session behind the wrapper: the owner rejected it, because it blocks the
  live, owner-steered collaboration the triad needs.
- Move real data to a second OS user now: the right long-term boundary (ADR 0036's residual
  risk), but it doesn't block this decision. It stays a follow-up.

## Consequences

- Anvil and Helm can work interactively, with the owner steering, at full speed.
- Three vendors' sessions can now reach everything the wrapper hides, not only the data
  directory:
  - the docker group (root-equivalent);
  - `tailscaled.sock` (`tailscale funnel` would break hard rule 12);
  - the session bus and systemd user manager;
  - the keyring;
  - `.env`;
  - the owner's `gh` credentials.

  The last one matters most. `main` requires `ci-required` and `review-gate` but no approvals,
  so "only Keel merges" holds by protocol, not by GitHub. A live session using the owner's
  token could merge. Mitigations here are behavioral (items 2 and 4) plus the pre-push scan
  (item 5). ADR 0036 point 7's trigger applies to live sessions too, with the same
  consequence: if any session uses a socket, credential or merge right outside its role, live
  Codex and Grok sessions stop until the separate OS user exists, unless the owner explicitly
  restarts them after reviewing the incident.
- `CLAUDE.md` and `.claude/rules/security.md` are updated in the same commit.
- Follow-ups:
  - A separate OS user that owns the private data directory, so that no coding session from any
    vendor can read it without an explicit grant.
  - Owner queue (cheap):
    - clear the Tailscale operator (`tailscale set --operator=`) so `serve` and `funnel` need root;
    - an `inotifywait` read log on the private directory, so "reach is not use" can be audited;
    - keep the directory locked (fscrypt or gocryptfs) when no real-data story is open.
  - Optional: a bubblewrap launcher for interactive sessions with `fc-delegate.sh`'s hide list.
    It keeps sessions live and steerable.
