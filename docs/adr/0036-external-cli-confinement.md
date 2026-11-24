# 0036. Other vendors' coding CLIs run only through the confined firstchair wrapper

- Status: Accepted (amended by 0039)
- Date: 2026-11-21
- Deciders: Keel; Bulwark (reviewed the control in PR #64 and PR #72)
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
nothing proves there was none for Codex. The gap was in the control, not in a worker's conduct.
PR #64 closed the paths known at the time.

Bulwark's review of this ADR found two more system-wide sockets that PR #64 left reachable:
- `/run/tailscale/tailscaled.sock` is mode `0666`, and the owner is the Tailscale operator. A
  worker could have run `tailscale serve` or `tailscale funnel` to publish the stack, or
  `tailscale file cp` to send data off the machine.
- `/run/dbus/system_bus_socket` is mode `0666`, which exposes whatever polkit actions the
  owner's session may take.

Keel checked and found no misuse. `tailscale serve status` shows only the owner's two
tailnet-only entries, funnel is off, and the `tailscaled` journal shows no serve, funnel or file
activity. PR #72 hides both sockets. It also hides the per-user runtime directory by allowlist,
because a denylist there had already missed entries.

## Decision
1. **One entry point.** On a machine that holds real data, every Codex or Grok worker starts
   through `scripts/fc-delegate.sh`, which wraps `firstchair delegate run`. Running the raw CLIs,
   running `firstchair delegate run` directly, and running PR #8's `external-agent-launch.sh` are
   all out.
   *(Amended by 0039: this point now covers headless workers. Live sessions the owner opens and
   controls may run unconfined. Headless Grok write workers stay refused, and the wrapper enforces
   that.)*
2. **What the wrapper does** (as of PR #72). It adds `--confine`, then a `--hide` for each of
   these that exists:
   - the private data directory (`HOLDFAST_PRIVATE_DIR`, default
     `$XDG_DATA_HOME/holdfast-private`);
   - container sockets: `/run/docker.sock`, `/var/run/docker.sock`, `/run/podman`;
   - the Tailscale daemon: `/run/tailscale` and `/var/run/tailscale`, which hold
     `tailscaled.sock`;
   - the system bus: `/run/dbus` and `/var/run/dbus`, which hold `system_bus_socket`;
   - **every entry of the per-user runtime directory** (`/run/user/<uid>`) except an allowlist,
     which today is just `fnm_multishells`, the Node shims the Codex CLI starts through. That
     covers the session bus, the systemd user manager, the accessibility bus, the keyring, the GPG
     and SSH agents, the Wayland, PipeWire and Pulse sockets, the X11 cookie, the Git credential
     and editor IPC sockets, and any per-user container socket. The list is computed at launch
     from what exists then. An entry created after the worker starts isn't hidden from it;
   - the lead's scratch directory, `/tmp/claude-<uid>`.

   It also removes `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `DBUS_SESSION_BUS_ADDRESS`,
   `SSH_AUTH_SOCK`, `GPG_AGENT_INFO` and `DOCKER_HOST` from the worker's environment. A caller
   can't pass its own `--confine` or `--hide`, so it can't trim the list. Adding to the runtime
   allowlist widens a worker's view, so it needs a protected-path PR reviewed by Bulwark.
3. **It fails closed.** Nothing starts if bubblewrap is missing, if the private directory isn't
   set up (`none` is the explicit opt-out for a machine with no real data), or if a dry run of
   the same arguments doesn't report `"confined": true`. `scripts/tests/test_fc_delegate.py`
   covers each refusal and checks that the hide list is complete.
4. **Codex writes, Grok reads.** Grok write workers are refused, and the wrapper never passes
   `--allow-grok-write`. Workers never commit, push or merge. Keel applies the diff, tests it
   under point 5, commits with the agent's trailer, scans and lands the PR. A Codex or Grok
   review is a second opinion and never satisfies `review-gate`.
5. **Review before anything runs locally.** A worker's diff is vendor-written code. Running it
   on the host executes it as the owner, outside bubblewrap. That covers `make test`, `make
   lint`, `uv sync`, `pnpm install`, a migration, a container build, and even `git commit`,
   which runs `.githooks/pre-commit`. Until worker diffs can be tested inside the confined view,
   or only in CI:
   - Keel reads every changed line of every file a local run executes or that configures one,
     before any local run or commit. That means `conftest.py` and every test file, `Makefile`,
     `backend/pyproject.toml`, `uv.lock`, `web/package.json`, `pnpm-lock.yaml` (install and
     lifecycle scripts), `.npmrc`, the vitest and Playwright configs, `.githooks/`, `scripts/`,
     `backend/migrations/env.py` and every migration, Dockerfiles and compose files. It also
     means the agent tooling config: `.claude/` (hooks, settings, agents), `.codex/`, `.grok/`
     and `.agents/`. A hook change runs unsandboxed on Keel's next Edit or Write, with no `make`
     at all, so that review comes before the diff is even applied to a Keel checkout.
   - A test run imports every changed module under `backend/app/` and `web/src/`, so any diff
     there counts as executed code too. The review covers every changed line in those trees
     before a local run.
   - Keel never runs `make test`, `make test-locked`, `uv sync` or `pnpm install` on a worker
     diff that hasn't had that review. If a diff touches those files and Keel can't review it
     fully in the window, it goes to CI only, as a draft PR. Keel pushes it, the worker doesn't.
6. **Only `delegate` can confine.** `conduct run` has no `--confine` or `--hide`. Until it does,
   a score may use only the `claude` and `self` seats. Fan out Codex and Grok as several
   background `delegate run` jobs through the wrapper instead. The same applies to
   `firstchair run` with a `codex` or `grok` provider or reviewer, since it doesn't go through
   bubblewrap either.
7. **The target hasn't changed.** It's still the separate OS user without the `docker` group,
   plus a read-only Git host identity that pushes only to a fork, as PR #8 laid out
   (`docs/process/EXTERNAL-AGENTS-SETUP.md` on that branch holds the owner steps). The wrapper
   keeps working under that user. **Trigger:** this must be in place before Holdfast Cloud, or as
   soon as any worker is found to have used a socket, a loopback service or a credential it was
   not given. If that happens, Codex and Grok dispatch stops until it's in place.

## Why not wait for the separate OS user
The owner wants Codex and Grok in use now. Waiting would keep both idle for an unknown time on
an owner-queue item. With PR #72, the wrapper closes every path we have found from a worker to
real data or to a privileged local service. The first version missed two, the Tailscale and
system-bus sockets, so this is a list of what we've found, not a proof that nothing is left.
That's why the runtime directory is now an allowlist, and why point 7 has a trigger. What we know
is still open is listed below. Each item is either bounded by another control (point 5 for local
runs) or needs the target to close.

## Threat model
- **In scope:** a worker that follows a hostile instruction (prompt injection through a file,
  an issue, a dependency or a tool result), or one that just runs a command with side effects it
  shouldn't have. Either way it uses the lead's identity to read real data, credentials or
  another checkout, to reach the container daemon (root-equivalent), to reach the Tailscale
  daemon or the system bus, to start code outside the sandbox through the session bus or the
  systemd user manager, or to push. Also in scope: a worker that plants code in its diff for the
  owner to run later, outside the sandbox (point 5).
- **Out of scope:** a malicious vendor binary that sets out to break bubblewrap itself, and a
  kernel exploit. Only a separate user or a separate machine bounds those.
- **What can leave the machine:** whatever the worker can read or run inside its view. That's
  the clean worktree, the command output and the test logs. The worker shares the host's network
  namespace, so it can send anything in that view to any internet host, not only its vendor's
  API. Under ADR 0019 that view must hold no real data, and the hide list is what keeps the
  private directory out of it.

## Residual risks
These stay open until the target in point 7 exists:

- **Running a worker's diff locally.** This is the largest risk, because it skips every other
  control. `make test`, `uv sync` or `pnpm install` on a worker's diff runs vendor-written
  code as the owner, outside bubblewrap. That code runs with the `docker` group, can read the
  `.env` passwords, the private data directory, the keyring and the `gh` credentials, and can
  reach the Tailscale daemon. The paths are `conftest.py` and tests, `Makefile`, install hooks
  in `pyproject.toml`, `uv.lock` or `package.json`, `.githooks/`, `migrations/env.py` and
  Dockerfiles. A single prompt-injected `conftest.py` would beat the wrapper outright. Point 5's
  line-by-line review is the only control until worker diffs are tested inside the confined view
  or only in CI, and it relies on Keel reading carefully.
- **Same UID and same groups, `docker` included.** Every container socket path we know of is
  hidden. But the worker's process still has the `docker` group. Any daemon path the list misses
  is root-equivalent. The same goes for any file only the lead's UID can read that `--confine`
  doesn't cover, and any world-writable system socket that isn't on the list. Tailscale and the
  system bus were both that kind of socket.
- **The whole internet.** The shared network namespace lets a worker send anything in its view
  to any host. The view holds no real data, but it does hold the repo, the worker's own vendor
  session and anything a worker command prints.
- **Loopback TCP ports.** The worker shares the host's loopback. Since PR #66 the dev Redis
  requires a password (it lives in the hidden `.env`). A worker without it can't reach the job
  queue. Even with it, the damage is bounded:
  the runner treats the Redis payload as untrusted, claims only `job_run` rows that already
  exist, and runs the payload stored in `job_run` (`backend/app/jobs/runner.py`, ADR 0016). So a
  worker could drop, replay or delay jobs, but it couldn't inject one. The dev Postgres is behind
  a password, and that password lives in the hidden main checkout's `.env`, not in the worker's
  worktree.
- **Abstract Unix sockets.** These live in the network namespace, not the filesystem, so
  `--hide` can't reach them. The X server is one. Bulwark measured it: without the X cookie, the
  X server refused a confined client. So today the cookie hide is what closes X. That would no
  longer hold if the server were set to admit the same local user without a cookie.
- **Sockets still reachable from the confine, reviewed.** These remain visible and were judged
  acceptable for now:
  - `/run/pcscd/pcscd.comm`, which reaches smart cards and a YubiKey;
  - avahi (mDNS), snapd, Kerberos KCM and gssproxy, cups, systemd's varlink sockets, and
    `/run/ssh-unix-local/socket`;
  - libvirt, which is reachable but was tested: polkit refused it.

  Any of them moves to the hide list if a use turns up, and finding a worker using one is the
  trigger in point 7.
- **Grok read workers have a shell with permissions bypassed.** Removing its two edit tools
  doesn't stop it writing through the shell. "Read" for Grok is a role, not a capability.
- **Codex's own sandbox is only partly verified.** Its filesystem side was checked: under
  `workspace-write`, writes to `$HOME` and `fnm_multishells` were refused (tested by Bulwark during its review of PR #72, 2026-11-22). Loopback and Unix
  sockets remain untested, so we don't count it as a layer there.
- **Some rules are only in the prompt.** "Do not commit, push or merge" and the lane limits are
  instructions. Hidden credential stores and Git prompt sockets make a push unlikely to
  authenticate. But no Git host identity limits a credential a worker might find. ADR 0013's
  "prevention" for external agents relies on PR #8's Read-role identity, which doesn't exist yet.
  Until it does, protecting `main` against these workers comes down to detection (`main-guard`)
  and to Keel being the only one who commits their work.
- **Codex leaves no command log.** After the fact we can show what Grok did, but not what Codex
  did.

## Recommendation for the owner (not a decision)
Consider clearing the Tailscale operator (for example `sudo tailscale set --operator=`; check the
flag against the installed version). Then `serve`,
`funnel` and `file cp` would need root even from the owner's own shell. That would close the
Tailscale path for a worker's local-run code too, which the wrapper can't reach. The cost is
typing `sudo` for those commands. It's an owner-queue item, because it changes how the owner's
own account works.

## Alternatives considered
- **Keep dispatch suspended until the separate user exists (PR #8).** It's the stronger
  boundary, but the owner declined to wait, and the known paths can be closed now.
- **Use `--confine` alone.** That's what ran in the incident. It hides no sockets and leaves the
  session bus and systemd user manager open, and either one can start code outside the sandbox.
- **Give the worker its own network namespace (`--unshare-net` plus a proxy to the vendor
  API).** That would close loopback, abstract sockets and arbitrary egress. But `firstchair`
  doesn't support it today, and building it would mean maintaining a proxy. We'll take it up if
  the target slips.
- **Containers for the workers.** That needs Docker access to start them, which is the very
  privilege we're keeping away from workers. It also adds a service under hard rule 9.

## Consequences
- Codex and Grok are usable today with real data on the machine, under a stated and tested
  control. Every dispatch goes through one script that CI tests.
- `docs/process/FIRSTCHAIR.md` (PR #64) is the operating reference. Its rules 1 to 3 match this
  ADR. Three things need bringing in line with it. The "Picking a command" row for `run
  --provider codex --reviewer grok` has to carry point 6's limit. Rules 4 and 6, which say
  "runs the suite" and "run `make test-locked` and `make lint` on the applied diff", need point
  5's review step first.
- Worker throughput is now limited by review: a diff that touches executed or config files
  costs Keel a careful read before any local run (point 5).
- PR #8 is superseded. Its process doc stays useful as the owner steps for the target. Its ADR
  0012 and `external-agent-launch.sh` should not merge as written.
- Every entry in the per-user runtime directory at launch is hidden unless it's on the
  allowlist, so a new desktop service there needs no change. A new
  system-wide socket that a non-root user can write to (under `/run`, `/var/run` or elsewhere)
  still needs its own entry in the wrapper and its test, via a protected-path PR reviewed by
  Bulwark.
- After any worker run that looks unusual, check for new systemd user units, container activity,
  `tailscale serve status` and the `tailscaled` journal, and Grok's session log. Treat any
  finding as the trigger in point 7.
