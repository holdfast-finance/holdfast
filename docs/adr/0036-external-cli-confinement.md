# 0036. Other vendors' coding CLIs run only through the confined firstchair wrapper

- Status: Accepted
- Date: 2026-11-21
- Deciders: Keel; Bulwark (reviewed the control in PR #64)
- Supersedes: ADR 0012 "Coding agents from other vendors" as proposed in PR #8. That ADR was
  never merged, so there is no 0012 file on `main`. The number stays unused so the references in
  ADR 0013 and ADR 0018 still point at PR #8.

## Context
The owner wants Codex (Anvil) and Grok (Helm) building and reviewing now, while Claude windows
are short. PR #8 proposed the control for that: a separate `holdfast-agents` OS user with no
`docker` group and a `0700` home boundary, a GitHub machine user with the **Read** role that
pushes only to its fork, and `scripts/external-agent-launch.sh` refusing to start without both.
Keel suspended dispatch on 2026-11-20 until that isolation existed. Neither the OS user nor the
machine user exists yet. Creating them is an owner-queue item (accounts).

ADR 0019 then raised the stakes. The owner's real statements now live on this machine in a
private directory outside the repo, and point 3 of that ADR says real data never reaches another
vendor's coding CLI.

Meanwhile Keel started driving both CLIs through `firstchair` (`docs/process/FIRSTCHAIR.md`, PR
#64). Its `delegate run --confine` runs a worker in a bubblewrap filesystem view. That view hides
the project's other checkouts, common secret stores and the other providers' files. It runs under
the lead's own UID and groups, and it hides no sockets.

**The incident.** On 2026-11-21 several Codex and Grok jobs ran with `--confine` and the private
data directory hidden, but with the session bus, the systemd user manager, the container sockets,
the keyring and the X server still reachable. Afterwards Keel checked what it could. Grok's session
logs showed no Docker, database or keyring commands. Codex keeps no command log, so there was
nothing to check. No transient or linked systemd user units appeared. Nothing shows misuse, and
nothing proves there was none for Codex. The gap was in the control, not in a worker's conduct,
and PR #64 closed the known paths.

## Decision
1. **One entry point.** On a machine that holds real data, every Codex or Grok worker starts
   through `scripts/fc-delegate.sh`, which wraps `firstchair delegate run`. Running the raw CLIs,
   running `firstchair delegate run` directly, and running PR #8's `external-agent-launch.sh` are
   all out.
2. **What the wrapper does.** It adds `--confine`, then a `--hide` for each of these that exists:
   - the private data directory (`HOLDFAST_PRIVATE_DIR`, default
     `$XDG_DATA_HOME/holdfast-private`);
   - container sockets: `/run/docker.sock`, `/var/run/docker.sock`, `/run/podman`, and the
     runtime directory's `docker.sock`, `docker`, `podman` and `libpod`;
   - the session bus (`bus`), the systemd user manager (`systemd`) and the accessibility bus
     (`at-spi`);
   - keyring and agent sockets: `keyring`, `gnupg`, `gcr`, `cc-socks`;
   - audio: `pipewire-0`, `pulse`;
   - the X11 cookie (`.mutter-Xwaylandauth.*`);
   - Git credential and editor IPC sockets: `vscode-git-*.sock`, `vscode-ipc-*.sock`,
     `ssh-askpass-*.sock`;
   - the lead's scratch directory, `/tmp/claude-<uid>`.

   It also removes `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `DBUS_SESSION_BUS_ADDRESS`,
   `SSH_AUTH_SOCK`, `GPG_AGENT_INFO` and `DOCKER_HOST` from the worker's environment. A caller
   can't pass its own `--confine` or `--hide`, so it can't trim the list.
3. **It fails closed.** Nothing starts if bubblewrap is missing, if the private directory isn't
   set up (`none` is the explicit opt-out for a machine with no real data), or if a dry run of
   the same arguments doesn't report `"confined": true`. `scripts/tests/test_fc_delegate.py`
   covers each refusal and checks that the hide list is complete.
4. **Codex writes, Grok reads.** Grok write workers are refused, and the wrapper never passes
   `--allow-grok-write`. Workers never commit, push or merge. Keel applies the diff, runs the
   suite, commits with the agent's trailer, scans and lands the PR. A Codex or Grok review is a
   second opinion and never satisfies `review-gate`.
5. **Only `delegate` can confine.** `conduct run` has no `--confine` or `--hide`. Until it does,
   a score may use only the `claude` and `self` seats. Fan out Codex and Grok as several
   background `delegate run` jobs through the wrapper instead. The same applies to
   `firstchair run` with a `codex` or `grok` provider or reviewer, since it doesn't go through
   bubblewrap either.
6. **The target hasn't changed.** It's still the separate OS user without the `docker` group,
   plus a read-only Git host identity that pushes only to a fork, as PR #8 laid out
   (`docs/process/EXTERNAL-AGENTS-SETUP.md` on that branch holds the owner steps). The wrapper
   keeps working under that user. **Trigger:** this must be in place before Holdfast Cloud, or as
   soon as any worker is found to have used a socket, a loopback service or a credential it was
   not given. If that happens, Codex and Grok dispatch stops until it's in place.

## Why not wait for the separate OS user
The owner wants Codex and Grok in use now. Waiting would keep both idle for an unknown time on
an owner-queue item. The wrapper closes every path we know of that leads from a worker to real
data or to a privileged local service. What it leaves open is listed below and is either
low-value to an attacker or needs the target to close.

## Threat model
- **In scope:** a worker that follows a hostile instruction (prompt injection through a file,
  an issue, a dependency or a tool result), or one that just runs a command with side effects it
  shouldn't have. Either way it uses the lead's identity to read real data, credentials or
  another checkout, to reach the container daemon (root-equivalent), to start code outside the
  sandbox through the session bus or the systemd user manager, or to push.
- **Out of scope:** a malicious vendor binary that sets out to break bubblewrap itself, and a
  kernel exploit. Only a separate user or a separate machine bounds those.
- **What reaches the vendor:** whatever the worker reads or runs inside its view. That's the
  clean worktree, the command output and the test logs. Under ADR 0019 that set must hold no real
  data, and the hide list is what keeps the private directory out of it.

## Residual risks
These stay open until the target in point 6 exists:

- **Same UID and same groups, `docker` included.** Every container socket path we know of is
  hidden. But the worker's process still has the `docker` group. Any daemon path the list misses
  is root-equivalent, and so is any file only the lead's UID can read that `--confine` doesn't
  cover.
- **Loopback TCP ports.** The worker shares the host's network namespace, because it needs the
  network to reach its vendor. The dev Redis listens on loopback with no auth, so a worker could
  read or write the job queue. The dev Postgres is behind a password, and that password lives in
  the hidden main checkout's `.env`, not in the worker's worktree.
- **Abstract Unix sockets.** These live in the network namespace, not the filesystem, so
  `--hide` can't reach them. The X server is one. Hiding the cookie doesn't help if the server
  admits the same local user without one, which is a common desktop default.
- **Grok read workers have a shell with permissions bypassed.** Removing its two edit tools
  doesn't stop it writing through the shell. "Read" for Grok is a role, not a capability.
- **Codex's own sandbox is unverified on loopback and Unix sockets.** We haven't tested what it
  allows there, so we don't count it as a layer.
- **Some rules are only in the prompt.** "Do not commit, push or merge" and the lane limits are
  instructions. Hidden credential stores and Git prompt sockets make a push unlikely to
  authenticate. But no Git host identity limits a credential a worker might find. ADR 0013's
  "prevention" for external agents relies on PR #8's Read-role identity, which doesn't exist yet.
  Until it does, protecting `main` against these workers comes down to detection (`main-guard`)
  and to Keel being the only one who commits their work.
- **Codex leaves no command log.** After the fact we can show what Grok did, but not what Codex
  did.

## Alternatives considered
- **Keep dispatch suspended until the separate user exists (PR #8).** It's the stronger
  boundary, but the owner declined to wait, and the known paths can be closed now.
- **Use `--confine` alone.** That's what ran in the incident. It hides no sockets and leaves the
  session bus and systemd user manager open, and either one can start code outside the sandbox.
- **Give the worker its own network namespace (`--unshare-net` plus a proxy to the vendor
  API).** That would close loopback and abstract sockets. But `firstchair` doesn't support it
  today, and building it would mean maintaining a proxy. We'll take it up if the target slips.
- **Containers for the workers.** That needs Docker access to start them, which is the very
  privilege we're keeping away from workers. It also adds a service under hard rule 9.

## Consequences
- Codex and Grok are usable today with real data on the machine, under a stated and tested
  control. Every dispatch goes through one script that CI tests.
- `docs/process/FIRSTCHAIR.md` (PR #64) is the operating reference. Its rules 1 to 3 match this
  ADR. Its "Picking a command" row for `run --provider codex --reviewer grok` has to carry point
  5's limit.
- PR #8 is superseded. Its process doc stays useful as the owner steps for the target. Its ADR
  0012 and `external-agent-launch.sh` should not merge as written.
- Any new socket, desktop service or credential helper on this machine needs a matching entry in
  the wrapper's hide list and in its test, via a protected-path PR reviewed by Bulwark.
- After any worker run that looks unusual, check for new systemd user units, container activity
  and Grok's session log. Treat any finding as the trigger in point 6.
