# 0021. Zero-config private access: localhost first, phone through a stock Tailscale sidecar

- Status: **Proposed (revision 4)**. Acceptance comes in two stages (§12): "Accepted for the
  sidecar overlay" once Harbor's items pass, then "Accepted" once the owner checks pass. Neither has
  happened yet.
- Date: 2026-11-21
- Deciders: Lattice (architect), Keel. The owner asked for it (paraphrased: people who download
  Holdfast shouldn't have to understand reverse proxies or tunnels).
- Reviews:
  - Gadfly, `docs/adr/reviews/0021-gadfly.md` (it rejected revision 1). §10 answers it point by point.
  - Harbor's PA0 spike, `docs/adr/reviews/0021-pa0-spike.md`. §11 maps every check to what it changed
    here.
  - Gadfly on revision 3, `docs/adr/reviews/0021-gadfly-r3.md` (REWORK, small). §10a answers it.
  - Bulwark threat-models the design before PA1.
- Revision 4 changes (from Gadfly's review of revision 3; text only, no new spike):
  - **Wrapper:** it supervises `containerboot` as a child instead of `exec`ing it, so turning phone
    access off takes effect while the container runs (B1).
  - **Phase choice:** the wrapper decides from `BackendState` and control reachability, never from
    `containerboot` exiting. It never starts a sign-in during an outage. New U-12 (B2).
  - **Forged client IPs:** the risk is stated at full strength, and trust is narrowed to Caddy alone
    on a network `web` isn't on. Local-ingress rate limits get their own scope. This moves into PA1
    (B3).
  - **Funnel:** a runtime serve-config tripwire turns phone access off if Funnel or any unexpected
    listener appears. The "accepts no other serve config" claim is gone (B4).
  - **Also:** the UID rule now includes the operator UID and adds a startup self-check (S1). Let's
    Encrypt is named under rule 9 (S2). Acceptance is split so PA1 isn't deadlocked (S3). The Docker
    floor's blast radius is defined (S4). Plus the outage copy (S5), the HTTPS-on restart (S6) and
    two minors.
- Revision 3 changes (from the PA0 spike):
  - **Transport.** Serve can't send PROXY v2 to a unix socket (check 2 FAIL). The upstream is a TCP
    target on an unpinned, internal `edge` network with two members. The unix-socket option is gone.
  - **Sign-in.** The stock `containerboot` exits after 60 s if nobody signs in (check 5). The start
    wrapper now owns the sign-in phase and hands over to `containerboot` only once the node is
    signed in.
  - **UID rule.** LocalAPI write access depends on the client's UID: root and the daemon's own UID
    get write access, and root can read the certificate private key (checks 4 and 5). `api` must be
    non-root, with a UID different from the sidecar's. CI enforces both.
  - **Docker floor** is 28.3.3, not 28.0 (check 11). Stock Debian 12 and 13 are below it.
  - **Outage behaviour** is now stated firmly (check 12): a running sidecar keeps serving, but a
    sidecar restarted during a control-plane outage doesn't serve until control returns.
  - **HTTPS detection** reads `CertDomains` from status instead of assuming HTTPS is on (check 7).
  - **Dropped:** tsnet (check 9) and the host-CLI status fallback (check 4). The free-plan figure is
    corrected from 3 users to 6 (check 8).
- Revision 2 changes (kept): the stock `tailscale/tailscale` container replaced revision 1's custom
  Go/tsnet service; the app works at `http://localhost` straight after install, and phone access is
  an optional in-app step; credentials can belong to more than one accepted RP id; scope is cut to
  the spike plus the smallest Linux slice.

## Context

PLAN 12.1 and Phase 8.5 describe private access for someone who already knows networking:
- Tailscale on the host
- an owned domain and a Cloudflare DNS-01 token
- Caddy bound to the Tailscale IP
- ufw rules and tailnet ACLs

`infra/docker-compose.prod.yml` implements that path.

The owner's machine works today because of a manual setup: Tailscale on the host, `tailscale serve`
(tailnet only, real `*.ts.net` certificate) in front of Caddy on `127.0.0.1:443`, `APP_HOST` set to
the ts.net name, and a phone passkey registered against that name. Serve reconnects to Caddy from
loopback, so Caddy sees one peer for every client. Per-IP rate limits and audit IPs collapse
(`docs/ops/client-ip-check.md`, ADR 0010).

The owner wants strangers to get the same result without knowing any of that.

Constraints:
- **Hard rule 9:** this ADR covers these external dependencies of the optional phone path:
  - Tailscale's coordination and DERP services
  - the upstream `tailscale/tailscale` image
  - **Let's Encrypt (ISRG) ACME.** `tailscaled` keeps its own ACME account (`acme-account.key.pem`
    in the state volume, PA0 7) and gets the node's certificate from Let's Encrypt, a third party
    distinct from Tailscale.
  - the public **Certificate Transparency** logs that each issued certificate, and so the node's
    FQDN, is published to

  §1 lists the compose services this adds. None of them is a new external service or a new image.
- **Hard rule 12:** nothing public. No service publishes a port except Caddy on `127.0.0.1`, and
  Funnel is never enabled.
- **Hard rule 13:** the owner's tailnet name is personal and never appears in the repo. Examples
  use `holdfast.tail0000.ts.net`, and addresses are written as `100.x.x.x`.
- **ADR 0013:** no paid service for the project.
- **ADR 0009:** a relay run by Holdfast belongs to Holdfast Cloud.
- **ADR 0010:** today's RP id is `APP_HOST`.
- **ADR 0018:** compose plus GHCR is the supported install.
- **Roadmap:** this is **Phase 8.5 work**. It must not displace the Q1 finish line (Phase 5,
  `docs/planning/ROADMAP.md`).

How claims are marked:
- **Verified (PA0 n)** means the spike checked it on `tailscale/tailscale` v1.104.7 and Docker
  29.9.3.
- **Unverified (U-n)** means an assumption is still open. §12 names the exact check for each one and
  who can run it.
- **VERIFY** (in the index at the end) means a docs fact the design doesn't hinge on.

## Decision

### 1. Two ingresses, one Caddy

```
PC browser ──▶ http://localhost:47800 ──▶ caddy "local" site (plain HTTP, host loopback only)
                                              │
phone ──WireGuard──▶ tailscale (stock image, userspace)          ├──▶ web  (over `frontend`: caddy + web)
                     serve: :443 TLS-terminated TCP              ├──▶ api  (over `app`: caddy + api, internal)
                     + PROXY v2 ──TCP over `edge` network──▶ caddy "tailnet" site
                     egress via `ts-egress` network only
```

- **Local ingress (always on).** Caddy publishes one host port, `127.0.0.1:47800`. It's a
  plain-HTTP site that answers only `Host: localhost:47800` and redirects `127.0.0.1:47800` to it,
  because WebAuthn doesn't allow an IP literal as the RP id.
- **Tailnet ingress (off until the owner turns it on).** A digest-pinned **stock
  `tailscale/tailscale`** container runs in userspace mode, with no TUN device and no `NET_ADMIN`.
  It runs Tailscale Serve from a **committed, read-only `TS_SERVE_CONFIG`**: one TCP handler on
  `443`, TLS terminated with the node's own `${TS_CERT_DOMAIN}` certificate, and forwarded to Caddy's
  tailnet listener with a PROXY v2 header. **Verified (PA0 1):** the field names are
  `TCP."443".{TCPForward, TerminateTLS, ProxyProtocol}`. The header's source is the client node's
  tailnet address, and containerboot substitutes `${TS_CERT_DOMAIN}`. PA1 pins a current stable
  digest (the spike used v1.104.7).
- **The forward target is TCP, on the `edge` network.** This replaces revision 2's unix socket.
  - **Verified (PA0 2):** with `ProxyProtocol: 2`, a `unix:` target drops every TLS connection
    before the handshake completes. Serve accepts the config without any error, so the failure is
    silent. Without PROXY the socket works but loses the client address, which is the reason for
    the whole exercise.
  - `edge` is a compose network with `internal: true` and **no pinned subnet** (Gadfly S7). Its only
    members are `tailscale` and `caddy`.
  - The sidecar reaches Tailscale through a second unpinned network, `ts-egress`, which is not
    internal and has the sidecar as its only member. Caddy, `api` and the rest have no route through
    it.
- **Serve also answers on the node's tailnet IPs** (PA0 1: `serve status` lists both). Those
  requests carry an IP literal as `Host`, so they get `421` (§5).
- **Caddy tags the ingress and owns the forwarded headers.** Each site:
  - overwrites `X-Holdfast-Ingress: local|tailnet` and `X-Forwarded-For {remote_host}`
  - strips any client-sent `X-Real-IP`, `Forwarded` and `Tailscale-*` headers. In Serve's HTTPS mode
    `X-Real-IP` passes through forged and Serve adds `Tailscale-User-*` (PA0 3). Inside a
    TLS-terminated TCP stream every header is client-written.

  Caddy's security headers, CSP and 1 MB body cap apply to both sites, and HSTS goes on the tailnet
  site only.

**Forged client identity: the risk at full strength, and how trust is narrowed (Gadfly r3 B3).**

Today `TRUSTED_PROXIES` defaults to the whole `frontend` /24. `web` sits on `frontend`, so any code
running in `web` can call `api:8000` directly with any `X-Forwarded-For` and `X-Holdfast-Ingress`.
That skips Caddy, the body cap and the ingress tag. A second path opens with this ADR: any container
on a network Caddy shares could send a forged PROXY header to Caddy's tailnet listener. Gadfly r3
lists what a forged client IP buys, citing `client_ip.py`, `ratelimit.py`, `auth.py` and `setup.py`:
- **Unlimited guessing, and targeted lockout.** Passkey login and setup are rate-limited by IP scope
  only. Each forged IP gets a fresh 20-attempt budget. Forging one phone's tailnet address locks
  that phone out, escalating up to 1 hour (`MAX_LOCK_SECONDS`).
- **Repudiation.** Audit rows carry whatever IP was forged.
- **Lockout of the localhost floor.** On the local ingress every PC client shares one bucket (the
  Docker gateway). Any process on the host, or a LAN neighbour on a Docker below the floor (§2), can
  lock the owner out of passkey login at `localhost`, the one origin that never changes.

None of these forges authentication. They do turn a compromised `web` into a lockout tool.
**PA1 narrows the trust, not PA-D10,** because PA1 is what puts phone IPs into the buckets:
1. **`api` gets its own internal network, `app`, whose only members are `caddy` and `api`.**
   - `web` leaves every network that `api` is on. If `web` calls `api` server-side today, those
     calls go through Caddy instead, so they carry Caddy's tags.
   - `app` is unpinned, like every other network here.
2. **`api` trusts forwarded headers only from Caddy's own address.** `TRUSTED_PROXIES` drops the /24
   default. In the bundled stacks `api` resolves the `caddy` name on `app` at startup, and again
   (rate-limited) when a request arrives from a peer it doesn't know. Headers from any other peer
   are ignored, and the TCP peer is the client IP. On a two-member network that's belt and braces.
   - Manual mode can still set an explicit `TRUSTED_PROXIES`, but `check-compose-ports.py` rejects a
     range wider than a single address in the bundled compose files.
3. **Caddy's tailnet listener accepts connections only from the sidecar.** In order of preference:
   - (a) bind it to Caddy's `edge` address by name, if Caddy supports that (U-5)
   - (b) Caddy's start script resolves `tailscale` on `edge` and sets the `proxy_protocol` `allow`
     list to that single address. It reloads Caddy when the address changes.

   PA1 doesn't merge until (a) or (b) is in place and verified (U-5). After that, only a compromised
   sidecar can forge tailnet client IPs, and it can already do worse (§9).
4. **Rate-limit scopes stop depending on the IP alone.**
   - The local ingress gets its own bucket family: a higher threshold and a shorter maximum lock
     than the tailnet buckets, so one shared bucket can't hold the owner out for an hour.
   - Passkey login adds a scope keyed on the asserted credential id and the ingress. Setup adds a
     scope keyed on the setup token.
   - The credential and setup scopes land in PA1 (Tide's part of it). The local family lands in
     PA-D2 with the local ingress. Tide sets the numbers with Bulwark, in settings rather than code
     constants.
   - §4 now says correctly that only password and TOTP have a per-account scope today.

**Compose services this adds** (hard rule 9; none is a new external service or image):
- `tailscale`: the stock image, pinned by digest.
- `tailscale-init`: a one-shot service on **the same pinned image**. It has no network and exits
  after it sets ownership on the three volumes below, so it needs only `CAP_CHOWN`. It runs `chmod`
  **before** `chown`, while root still owns each path: after the `chown`, changing the mode would
  need `CAP_FOWNER`. It exists because a non-root sidecar can't chown its own state volume (PA0 5).
  It's dropped if the sidecar has to run as root (U-3).
- Volumes:
  - `tailscale_state`: owned by the sidecar's UID, mode `0700`
  - `tailscale_sock`: the LocalAPI socket directory, owned by the sidecar's UID
  - `phone_access`: the flag, owned by the `api` UID
- Networks: `edge`, `ts-egress` and `app` (B3 above).

**What the containers are allowed to do,** enforced by CI:
- `scripts/check-serve-config.py` parses the committed JSON and fails unless:
  - the only top-level key is `TCP`
  - the only port is `"443"`
  - the handler has exactly `TerminateTLS: "${TS_CERT_DOMAIN}"`, `TCPForward` set to Caddy's
    `edge` target (a `host:port`, never `unix:`), and `ProxyProtocol: 2`
  - there is no `Web`, `HTTPS`, `AllowFunnel`, `Services` or `Foreground` key anywhere
- `check-compose-ports.py` extends to the new services. It fails unless:
  - `tailscale` and `tailscale-init` have no `ports`
  - `tailscale` has `TS_USERSPACE=true` and `TS_ACCEPT_DNS=false`, and `TS_SERVE_CONFIG` is mounted
    read-only
  - `TS_TAILSCALED_EXTRA_ARGS` contains `--no-logs-no-support` (**verified, PA0 5:** the log-upload
    lookups stop)
  - neither `TS_EXTRA_ARGS` nor `TS_TAILSCALED_EXTRA_ARGS` contains `--advertise-*`,
    `--exit-node*`, `--ssh` or `--operator`
  - `tailscale` sets `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges`, a `/tmp` tmpfs and
    `init: true` (**verified, PA0 5:** the hardening is supported)
  - `edge` and `app` are `internal: true`, and no network has an `ipam` subnet
  - `TRUSTED_PROXIES` in the bundled files is unset (resolve `caddy`) or a single address (B3)
  - **the UID rule** (§6) holds:
    - `api` and `tailscale` set `user:` to **numeric** UIDs, both non-zero (or `0` for the sidecar
      only if U-3 forces it), and the two differ
    - only `api` mounts `tailscale_sock`, and read-only
- **The wrapper's command allowlist.** A CI test parses every `tailscale` invocation in the wrapper.
  It fails unless each one is `status`, `up`, `logout` or `serve status`. It also fails on the
  tokens `funnel`, `set`, `--operator` or `--advertise` anywhere in the file, and on any `serve`
  subcommand other than `status`.

**Off until the owner turns it on, and the wrapper owns sign-in.** Without consent the sidecar must
not contact Tailscale at all. The default entrypoint reaches the control plane within about a
second (PA0 5).

**The wrapper is a supervisor, never an `exec`** (Gadfly r3 B1). The compose `entrypoint` is a
POSIX `sh` wrapper, re-estimated at about 100 lines. It's checked by shellcheck, the command
allowlist above, and a CI compose test that drives every transition below with a fake `tailscale`.
- It stays PID 2 under compose's `init: true` (`tini` as PID 1, which reaps zombies and passes
  signals on).
- `tailscaled` or `containerboot` always runs as its **child** (`… & wait`), so the wrapper keeps
  running and keeps watching the flag in every phase.
- It traps `SIGTERM` and `SIGINT`, forwards them to the child, waits for it, and exits.

It runs a loop that re-reads the flag and the node's state every 5 seconds:
1. **Idle** (no flag). No child runs.
   - **Verified (PA0 5):** with no flag, a 30 s capture showed no packets from the node.
   - `api` writes the flag, and the sidecar mounts the volume read-only.
2. **Decide** (flag present, no child running, no tripwire latch). The wrapper starts a bare `tailscaled` child with
   the same arguments, waits for its socket, and reads `BackendState` and `Health` from
   `tailscale status --json`. The decision comes **only from that state, never from how a previous
   child exited** (B2):
   - `Running` or `Starting`: stop the bare child and go to **Serving**.
   - `NeedsLogin` **and** control reachable (the U-11 signal): go to **Sign-in**.
   - `NeedsLogin` with control unreachable, `NoState`, or anything else: stop the child, back off
     (30 s doubling to 10 minutes) and decide again. **The wrapper never starts a sign-in it can't
     finish against a reachable control plane.** That's what keeps an outage plus a reboot from
     turning into a re-login (§9).
3. **Sign-in.** On the same bare `tailscaled`, the wrapper runs `tailscale up` with the hostname,
   `--accept-dns=false` and no timeout. It uses `up`, not `login`, so that on a node that already
   has a profile it re-authenticates the existing profile instead of adding one (U-12). One login
   URL stays valid until someone completes it (U-1).
   - **Verified (PA0 5):** it doesn't use `containerboot` here, because `containerboot` gives up
     after 60 s (`context deadline exceeded`, exit 1) and a restart policy mints a new URL every
     minute.
   - When `BackendState` reaches `Running`, the wrapper stops the bare child and goes to
     **Serving**.
   - The resulting tailnet is checked against the bound one (§5).
4. **Serving.** The wrapper runs `containerboot` as a child on the signed-in state. `containerboot`
   applies the committed `TS_SERVE_CONFIG`.
   - **Verified (PA0 6):** a restart on kept state keeps the name and address.
   - **Verified (PA0 1):** containerboot applies the serve config.
   - The hand-over is U-1.
   - **If the child exits for any reason**, the wrapper backs off and goes back to **Decide**. An
     exit is never read as "needs sign-in". What `containerboot` does during an outage, on key
     expiry and on a failed certificate fetch is U-12.
   - **HTTPS turned on later (Gadfly r3 S6).** If `CertDomains` goes from empty to listing the node's
     name, the wrapper restarts its `containerboot` child once. That's a restart, not a logout. If
     U-9 shows serving starts without it, the restart is harmless.
   - **Serve-config tripwire, wrapper side (B4).** After `containerboot` applies the config, and
     on every loop, the wrapper reads `tailscale serve status --json` and compares it with the
     committed config (after `${TS_CERT_DOMAIN}` substitution). On any difference it runs **Turn
     off** and latches (below).
5. **Turn off** (flag removed while a child runs, or a tripwire fires).
   - The wrapper runs `tailscale logout` while `tailscaled` is still running, then sends `SIGTERM` to
     the child, waits, and goes to **Idle**.
   - **Turn-off takes effect within one loop (≤ 5 s) with the container still running.** The PC UI
     shows "Turning off…" until status reports the node logged out or the socket gone. It confirms
     from status, never from its own flag write.
   - **Verified (PA0 5):** logout works on a node that isn't signed in. U-2 is that it deregisters a
     signed-in one.
   - **Latch.** After a tripwire, the wrapper stays in **Idle** even if the flag is still present.
     It leaves the latch only once it has seen the flag removed and created again, which is the
     owner turning phone access off and on. The latch lives in the wrapper's memory and in a marker
     on the state volume, so a container restart doesn't clear it.

Turning phone access back on after Turn off is a fresh sign-in. The machine name may then change,
and §5 handles that. The flag bit in `phone_access` is the app's only write channel to the sidecar.

If the pinned image offers a supported way to lengthen `containerboot`'s sign-in wait, PA1 may use it
instead of the bare-`tailscaled` sign-in. PA0 didn't look for one, and the decide rule stays the same
either way.

**Status for the in-app wizard.** `api` connects to the sidecar's LocalAPI socket, which is mounted
read-only (a read-only mount can still connect), and calls `GET /localapi/v0/status`.
- **Verified (PA0 4):** a non-root client whose UID differs from the daemon's can read `BackendState`,
  `AuthURL`, `Self.DNSName`, `Self.KeyExpiry`, `CertDomains`,
  `CurrentTailnet{Name, MagicDNSSuffix, MagicDNSEnabled}`, `User` and `Health`.
- It gets `403` on `logout`, `start`, `login-interactive`, `reset-auth`, `set-dns`,
  `set-expiry-sooner`, `debug`, `profiles/`, `check-prefs`, `id-token` and `cert/<fqdn>`. Writes to
  `serve-config` and to `prefs` also get `403`, so `api` can't change serve, turn on Funnel, or run
  `up` or `set`.
- **The status client reads only** `BackendState`, `Health`, `AuthURL`, `Self.DNSName`,
  `Self.KeyExpiry`, `CertDomains`, `CurrentTailnet` and the **count** of entries in `User`. It
  parses the response in memory and discards the rest.
- It never logs, stores or returns the raw response. Read access also exposes `Peer` (every other
  device's hostname, OS and last-seen time) and `/file-targets` (PA0 4).
- **`AuthURL` is a bearer capability.** Whoever completes it decides the node's tailnet membership,
  so the API returns it only to an owner session with fresh step-up (Gadfly r3 minor).
- Besides `status`, it calls only `GET /localapi/v0/serve-config` (the tripwire below) and the
  one denied probe in the §6 startup self-check.
- The app never holds a Tailscale write capability or an API key.

**Serve-config tripwire, API side (Gadfly r3 B4).** The read-only mount protects only the **file**.
Serve config lives in `tailscaled`'s state, and anyone with write access can change it through
LocalAPI: root in the container, the daemon's UID (which includes the wrapper) or an operator. A
copied `docker compose exec tailscale tailscale funnel 443` from a tutorial would do exactly that.
So the running config is checked, not only the committed one. While the flag is present, `api`
reads `GET /localapi/v0/serve-config` (readable, PA0 4) every 30 seconds. **It fails closed** when
any of these happens:
- the config differs from the committed one (after `${TS_CERT_DOMAIN}` substitution)
- it contains `AllowFunnel` or any key or listener the committed config doesn't have
- three reads in a row fail while the node reports `Running`

When it does:
1. `api` removes the flag, which makes the wrapper log out within one loop.
2. It writes an audit row and raises an owner alert.
3. The PC shows "Phone access is off. Tailscale had a serve setting Holdfast didn't make (Funnel or
   an extra listener). Remove it before turning phone access back on."
4. It doesn't re-enable on its own.

The wrapper's own check (Serving, above) is the second, independent half, so a tampered config is
caught even while `api` is down.

The window is bounded. Normally it's one wrapper loop (≤ 5 s), whether the change came at start or
at runtime. If the wrapper's own check is what's broken, it's 30 s (`api`'s poll). Funnel also needs the tailnet policy's `funnel` attribute. The
docs say to remove it (§8), and that's still the first line of defence (VERIFY).

### 2. Rule 12, restated (merges through the owner digest, not on acceptance)

This is the proposed CLAUDE.md wording. Keel puts it in a PR that the weekly digest lists for veto,
so it isn't applied automatically when this ADR is accepted.

> 12. **Networking:** Holdfast is never reachable from the public internet. The default stack
> publishes only Caddy, on `127.0.0.1`, for the PC's own browser. Phone access, when the owner turns
> it on, comes only through the bundled Tailscale container, whose committed serve config allows
> tailnet `:443` and nothing else (no Funnel). The manual path binds Caddy to the Tailscale IP only.
> Never add a public port, port-forward, Funnel or public tunnel. `make ports`, the serve-config
> test and a runtime serve-config check enforce this; the runtime check turns phone access off
> rather than serve anything unexpected. The service worker caches the app shell only, never API
> responses.

The checks:
- `make ports` covers the default, access and prod stacks. In default and access, only `caddy`
  publishes, on `127.0.0.1:47800`. In prod, `TAILSCALE_BIND_IP` must be in `100.64.0.0/10` or
  `fd7a:115c:a1e0::/48`.
- **Serve config (B4).** No shipped path writes serve config, and a runtime check catches any that
  does:
  - `check-serve-config.py` (§1) keeps a Funnel key out of every committed config.
  - The read-only mount keeps the file from changing.
  - `api` can't write serve config (PA0 4), and the wrapper's command allowlist has no `serve` or
    `funnel` write.
  - Root in the sidecar, the daemon's UID or an operator still could. The two runtime tripwires
    (§1) catch that and **fail closed**: phone access turns off, and the owner is told why.

  Every failure path ends with the node logged out, never with it serving. That includes a tripwire
  read failing, `api` down (the wrapper's own check still runs), a crashing wrapper (`containerboot`
  is its child, so `tini` takes both down) and an outage (the wrapper backs off instead of signing
  in).
- **The Docker floor is 28.3.3** (verified, PA0 11). Older Engines let LAN neighbours reach
  `127.0.0.1`-published ports:
  - 28.0.0 fixed this (moby/moby#49325). The same release fixed remote hosts routing straight to a
    container's published ports.
  - 28.3.3 fixed a regression where a firewalld reload reopened it (CVE-2025-54388,
    moby/moby#50506).

  Below the floor the plain-HTTP localhost site would be reachable from the LAN, so the floor is what
  makes "loopback only" true. **The floor applies to every stack Holdfast ships** (default, access
  and prod), because the routing fix protects the prod stack's Tailscale-IP bind too. The manual
  path below the floor is covered in Consequences, along with what the product does there.
- Sounding's release probe: from a device that isn't on the tailnet, the FQDN doesn't resolve, and
  the host's LAN and public addresses refuse `47800` and `443`.

### 3. Localhost first: what Caddy and TLS do, and why it's safe

**There's no TLS on the local ingress.** `http://localhost` is a secure context ("potentially
trustworthy origin"), so WebAuthn and `Secure` cookies work there.
- **Verified (PA0 10):** in Chromium on Linux it's a secure context, `Secure` cookies are stored and
  sent, and WebAuthn create and get with RP id `localhost` pass (virtual authenticator).
- **Verified (PA0 10):** in Firefox on Linux it's a secure context and `Secure` cookies work.
- **U-6:** WebAuthn in Firefox with a real authenticator.
- **U-7:** Safari and Chrome on macOS, and Edge on Windows. Safari's `Secure` cookie handling on
  `http://localhost` is the known doubt.

The alternatives are both worse:
- `tls internal` on `https://localhost` shows a certificate warning, and Chrome disables WebAuthn on
  a certificate error.
- Trusting Caddy's local CA in the OS store means keeping a CA key on disk that can mint a
  certificate for any domain.

If U-7 shows Safari rejects `Secure` cookies on `http://localhost`, Bulwark picks one of two options:
- the session cookie for the **local origin only** drops `Secure`. The traffic never leaves
  loopback, and the cookie is host-only with no `Domain`.
- the docs tell macOS users to use Chrome or Firefox for the PC app.

**The first run happens entirely on the PC:**
1. The installer opens `http://localhost:47800/setup#t=<setup token>`.
2. The page exchanges the token on load for an in-memory value and calls `history.replaceState` to
   remove the fragment.
3. The token is single-use with a 10-minute TTL. That fixes Gadfly S5 for setup, recovery and
   pairing alike.
4. The owner creates the household and a passkey with RP id `localhost`. Password + TOTP is the
   offered alternative. Chrome on Linux has no platform authenticator, and a hybrid (phone) passkey
   works there.

The app is fully usable at that point, and Tailscale hasn't been mentioned.

**Why rule 12 still holds:**
- The only host port is loopback, and the Docker floor (§2) closes LAN reachability.
- Anything that can reach the port is already running code on the PC.
- Setup and recovery are served **only** on the local ingress: the API refuses `/setup` and
  `/recover` unless `X-Holdfast-Ingress: local`. So recovery requires presence at the PC.
- The ingress tag is a routing rule, not an authenticator. A container on `frontend` could reach
  Caddy's local listener inside the Docker network. The real gate is the single-use token, which
  only the host CLI mints.

### 4. Real client identity

**Tailnet ingress (primary).** The source of truth is the TCP peer that Serve accepted on the
tailnet. The tunnel is WireGuard, authenticated by the sending node's key, so a peer can't present
another peer's address.
- Serve writes that address into a PROXY v2 header, ahead of any client byte. **Verified (PA0 1):**
  `src` is the client's tailnet address and `dst` is the node's address. HTTP follows in plaintext
  after the header.
- Client bytes arrive inside the TLS stream, after the header, and Caddy parses them as HTTP, never
  as PROXY.
- Caddy's `proxy_protocol` listener wrapper on the tailnet listener rejects connections that have no
  header. The option names are U-5:
  https://caddyserver.com/docs/caddyfile/options#listener-wrappers.
- Only the sidecar may reach that listener, by bind address or by a single-address `allow` list
  (§1, B3, U-5). Every container drops all capabilities, so none can spoof a source address at the
  IP layer.
- Caddy then overwrites `X-Forwarded-For`. The API's resolver trusts it only from Caddy's own
  address on `app` (§1).

**Fallback 1, if a future Serve breaks PROXY v2:** Serve's HTTPS-proxy mode, trusting its
`X-Forwarded-For` from the sidecar only.
- **Verified (PA0 3):** Serve **overwrites** a client-supplied `X-Forwarded-For` with the client's
  tailnet address. It doesn't append.
- It also overwrites `X-Forwarded-Proto` and `X-Forwarded-Host`, drops `Forwarded`, and passes
  `X-Real-IP` through unchanged.
- Caddy's header stripping (§1) already covers this mode.
- Using it would need a second committed serve config shape, which the serve-config test would have
  to allow. This ADR would be amended first.

**Fallback 2:** TLS-terminated TCP without PROXY. Tailnet clients then share one rate-limit bucket
(today's problem, recorded as accepted risk), and audit rows carry no device IP. Today only password
and TOTP have a per-account rate-limit scope. Passkey login and setup are IP-scoped only, so this
fallback relies on the credential and setup scopes in §1 (B3).

**tsnet is off the table** (PA0 9: checks 1, 3 and 4 pass). It returns only through the vendor
trigger in §9, under Gadfly's conditions: Harbor named as CVE owner, a release within 72 hours of a
Tailscale security bulletin, and a Go-capable review gate.

**Local ingress.** The peer is the Docker gateway or the userland proxy for every client. That's
correct: there's one client, the PC.

### 5. More than one accepted RP id

**Model:**
- `instance_access` is a new instance-level table. It has no household data, so no `household_id`,
  and it joins the identity-table exemption in the RLS lint with that reason.
- It holds the accepted origins: always `http://localhost:47800` with RP id `localhost`, plus zero
  or more `https://<fqdn>` with RP id `<fqdn>`. Each has `added_at` and `added_by`.
- It binds the tailnet identity at first connect: the `CurrentTailnet` name and MagicDNS suffix.
- `webauthn_credential` gains `rp_id`, which is NOT NULL and backfilled from `APP_HOST`.
- Manual mode (`APP_HOST` set in `.env`) keeps today's single-origin behaviour.

**Ceremony rules:**
- **Picking the RP id.** For options requests, the API picks the RP id from the ingress:
  - Local ingress gives `localhost`.
  - Tailnet ingress gives the accepted FQDN equal to `Host`.
  - Any other `Host` gets `421 misdirected_request`, including the node's tailnet IP literals (§1)
    (Gadfly minor). Invite and alert links are therefore always built from an accepted origin.
- **Verifying.** `clientDataJSON.origin` must exactly equal (scheme, host and port) an accepted
  origin. `rpIdHash` must equal the hash of the RP id mapped to that origin. Assertions are checked
  only against credentials whose stored `rp_id` matches.
- **Who adds an origin.**
  - `localhost` is written at install.
  - A tailnet FQDN is added only when an **owner session with step-up** asks. The value is
    `Self.DNSName` (without the trailing dot) **read from the sidecar's own status**, never a request
    header. It's added only if `CertDomains` contains that name (§9).
- **Who removes an origin.** The owner, with step-up. Removing an origin disables its credentials
  but doesn't delete them, so renaming back revives them.

**Why this is safe:**
1. An authenticator binds each credential to exactly one RP id, so accepting several RP ids never
   makes a credential valid somewhere else. The phone passkey (FQDN) and the PC passkey
   (`localhost`) are separate credentials on one account.
2. The accepted set is server-side, written only by step-up or by the host. A forged `Host` or an
   attacker-run origin can't add itself.
3. The exact origin check (including the port) defeats a local phishing page on another `localhost`
   port. Every localhost app shares the RP id `localhost`, but that page's assertions carry its own
   origin and get rejected.
4. Phishing resistance per credential is unchanged. Sessions are per origin (host-only cookies).

**What "survives a rename" honestly means.** When the tailnet or machine name changes, the phone's
old-FQDN credential *can't* move. That's WebAuthn's origin binding working as designed. Related
Origin Requests don't help, because the old name stops resolving. What survives is the **account**:
1. The owner signs in on the PC at `localhost`, which never changes.
2. The owner adds the new FQDN (step-up).
3. The owner re-pairs the phone: two phone steps, scan the QR and create a passkey.

The stale credential stays listed so a rename back revives it.

**When the name changes** (verified only for the restart case):
- **Verified (PA0 6):** a restart with kept state keeps the name and address.
- **U-8:** re-auth after key expiry, lost state, and a lost state after the old machine is removed.
  Tailscale's docs say a clash gives `<hostname>-1`, and the name keeps that suffix after the clash
  is resolved (https://tailscale.com/kb/1098/machine-names). Either way, the flow above makes the
  result a re-pair, not a lockout.

**Pairing the phone.** It starts from a signed-in PC session with step-up:
1. The PC mints a single-use, 10-minute pairing token and shows it as QR 2:
   `https://<fqdn>/pair#<token>`. The phone page does the same exchange-and-`replaceState` dance.
2. The phone shows a 4-digit code, and the **PC must approve it** ("Approve this phone? Code
   4821").
3. Only after approval can the phone register a passkey on the owner's account.

That approval step means a screenshot of QR 2, or another member of a shared tailnet, can't attach a
credential on its own.

**Tailnet binding (Gadfly S4).** The sidecar may come back (after a key expiry, say) in a tailnet
other than the bound one, usually because the browser was signed in to a different identity
provider account. When that happens:
- The API answers every tailnet-ingress request with "Holdfast is on a different Tailscale network".
- The PC shows the old and new tailnet names and asks for a **typed confirmation** before it adopts
  the new one.
- The recovery path can't rebind across tailnets.

**Recovery for a fully locked-out owner.** That means the PC passkey and TOTP are both lost, for
example after an OS reinstall that kept the Docker volumes, or a restore to a new PC. `holdfast
recover` on the host runs `docker compose exec api python -m app.cli recover`:
- It mints a single-use, 10-minute token, printed to the CLI's own stdout and never to the API log.
- It opens `http://localhost:47800/recover#…` (exchange and `replaceState`).
- It allows one new `localhost` passkey for the owner.
- It revokes every session and records audit and alert entries.
- It's served on the local ingress only.

The trust anchor is the same as the setup token's: control of the host's Docker daemon, which can
already read the database.

### 6. Secrets, state and the UID rule

**The UID rule** (verified, PA0 4 and 5). LocalAPI decides write access from the connecting
process's container UID:
- Root gets full control, including the certificate **private key** through `cert/<fqdn>`.
- The daemon's own UID gets write access: `tailscale set` as the daemon's UID succeeded, and as
  another UID it was denied.
- **The configured operator UID** (`--operator`) gets write access too. PA0 4 caveat 1 names it
  from upstream behaviour (Gadfly r3 S1).
- Any other UID gets read only.

So:
- **No operator is ever configured.** CI rejects `--operator` in `TS_EXTRA_ARGS`,
  `TS_TAILSCALED_EXTRA_ARGS` and the wrapper (§1).
- `api` runs as a fixed, **numeric, non-zero** UID that is **different from the sidecar's** UID.
  PA1 confirms the `api` image's runtime UID and changes it if it's 0 today.
- Only `api` mounts `tailscale_sock`, read-only. `holdfast recover` and the other `app.cli` commands
  run as `api`'s UID, and the `holdfast` wrapper never passes `-u` to `exec`.
- CI asserts the compose half (§1).

**Startup self-check (S1).** CI can't see an ad hoc `docker compose exec -u 0`. So before the status
client first uses the socket, `api` checks:
- `getuid() != 0`
- `getuid()` differs from the owner UID of the socket directory, which is the sidecar's UID
- one deliberately wrong-method `POST /localapi/v0/logout` returns **`403`**. A `400` or `405`
  means writes are permitted (PA0 4's probe method), so it changes nothing either way.

If any check fails, `api` refuses to use the socket for the life of the process, audits it, and the
PC shows "Phone access status is unavailable: unsafe container setup". It doesn't fall back.

An operator who runs a root `exec` by hand into a container that mounts the socket has full node
control. That's the same trust anchor as the Docker daemon (§5), and it isn't defended further.

**U-4, rootless Docker.** The rule is stated in container UIDs, and peer credentials should still be
seen inside the user namespace under rootless Docker, so the same rule should hold. The spike
reasoned this but didn't run it. **The startup self-check now verifies it on every install**, rootless
or not, and fails closed. So U-4 no longer gates acceptance, though Harbor still records it once.

**The sidecar.**
- It runs as a fixed non-root UID, with these all verified (PA0 5):
  - every capability dropped
  - a read-only root filesystem
  - `/tmp` on tmpfs
  - `no-new-privileges`
  - `TS_SOCKET` in `tailscale_sock`
- Its state lives in `tailscale_state`, mode `0700`. `tailscale-init` sets the ownership, because
  the non-root sidecar can't.
- **U-3:** Serve (cert fetch, `TerminateTLS` and PROXY v2) working as non-root. If it fails, the
  sidecar runs as UID 0 with no capabilities, `tailscale-init` is dropped, and this is recorded as
  accepted. The UID rule still holds, because `api` is non-root.

**What the state volume holds.** It holds the node and machine keys, the ACME account key, and the
certificate and its key under `certs/` (PA0 7).

**Never logged by Holdfast:** node and machine keys, the certificate key, the login URL, auth keys,
setup, pairing and recovery tokens, and the raw LocalAPI status.
- Upstream prints the login URL to the container's stdout. That stays in `docker logs` on the host,
  readable only with the trust anchor above. Holdfast never ships logs anywhere.
- Tailscale's log upload is off (`--no-logs-no-support`, verified, PA0 5).

**Backups exclude `tailscale_state`.** A node key in a backup is a liability, and restoring it next
to a live node duplicates the identity. After a restore, the owner signs in at `localhost` (the app
works at once), turns phone access back on and re-pairs. The new node may come up as `holdfast-1` or
in a new tailnet. §5 covers both.

**Revocation.**
- **Phone access:** Settings → Phone access → Turn off (step-up). That removes the flag. Within one
  wrapper loop (≤ 5 s), with the container still running, the wrapper logs the node out, stops its
  child and idles (§1; U-2 for a signed-in node). The page reports "Off" only once status confirms
  it. It also deep-links the admin console's machine page so the owner can remove the machine.
- **A lost phone:** remove it in the admin console, then remove its passkey in Holdfast. Removing a
  credential already revokes the user's other sessions (ADR 0010).

### 7. Honest minimum step counts

**(a) Using it on the PC.** It's the same Holdfast on every OS. Only the Docker prerequisite
differs.

| | Linux | macOS | Windows |
|---|---|---|---|
| Docker | 0 if Engine ≥ 28.3.3 already (Fedora 43, Ubuntu 24.04 with `noble-updates`). 1 on Debian 12 or 13, and on Raspberry Pi OS, which follows Debian: their stock `docker.io` (20.10.24, 26.1.5) is below the floor, so install Docker CE from Docker's repo (PA0 11). NAS vendor builds have no zero-config path (Consequences) | 2: install Docker Desktop (4.100.0 bundles 29.9.3), open it and accept its terms | 2: install Docker Desktop (it may enable WSL2 and need a **reboot**), open it and accept its terms |
| Shell | 0 | 0 | 1: install PowerShell 7 (`winget install Microsoft.PowerShell`). This step goes away if rule 10 is amended to allow a 5.1 bootstrap (§10, Gadfly S1) |
| Holdfast | 2: run the one-line installer (the browser opens on setup), then create the household + passkey | 2 | 2 |
| **Total** | **2 to 3** | **4** | **5** (4 with the bootstrap) |

**(b) Adding a phone.** This runs in the app from a signed-in PC and is the same on every OS:
1. Settings → **Connect your phone** → step-up → **Turn on** (consent to contact Tailscale).
2. Sign in to or create a Tailscale account in the browser that opens, and approve `holdfast`.
3. **Turn on HTTPS** (and MagicDNS if it's off) on the admin page the wizard links. The wizard shows
   this step only when status says it's needed (§9), after the certificate-transparency disclosure.
   It's counted as required because a fresh tailnet probably has it off (U-9).
4. Phone: scan QR 1, install Tailscale. Skip if it's already installed.
5. Phone: sign in with the same account and allow the VPN configuration.
6. Phone: scan QR 2 to open the pairing page.
7. PC: **Approve** the phone (compare the code).
8. Phone: create the passkey.
9. Turn off key expiry for `holdfast` (one click on the linked admin page). This counts as required:
   the default expiry is 180 days, and after it connections stop until re-auth
   (https://tailscale.com/kb/1028/key-expiry, read in PA0 6).

That's **9 steps**, or 8 with Tailscale already on the phone. Add to Home Screen and always-on VPN
are optional extras. A typical first-time total is **Linux 11–12, macOS 13, Windows 14** (13 with
the bootstrap).

**(c) Each extra household member.**
- **PC only** (a shared computer): **2 steps.** The owner creates an invite, and the member opens it
  at `localhost` and creates a passkey.
- **Their own phone: 6 steps.**
  - The owner: **shares the `holdfast` machine** with the member's Tailscale account (1), and later
    sends the Holdfast invite (5).
  - The member: accepts the share with their own identity provider account (2), installs Tailscale
    on the phone (3), signs in and allows the VPN (4), and opens the invite on the phone and creates
    a passkey (6).

  Node sharing is the recommended route, not inviting the member into the tailnet. A shared-in user
  reaches only the Holdfast machine, not the owner's other devices.
  - The free Personal plan allows **6 users** and unlimited user devices (verified from the docs,
    PA0 8: https://tailscale.com/kb/1154/free-plan, https://tailscale.com/pricing).
  - Whether shared-in users count toward the 6 isn't stated anywhere. The free-plan page lists node
    sharing separately, so they probably don't (U-10).
  - The sidecar never gets a tag, because a tagged device would count against the tagged-resource
    cap.

### 8. Employer and shared tailnets

**Who can reach the app.** Every device the tailnet policy allows. On the default policy that's
every member's device (VERIFY: https://tailscale.com/kb/1192/acl-samples), plus users the machine is
shared with. Passkeys are still required: reaching the login page isn't access.

**What tailnet admins can do.**
- They **can** see the machine and its metadata, rename it (which forces a re-pair), remove or
  expire it, and see flow logs on plans that have them (VERIFY).
- They **can't** read app traffic. TLS terminates inside our container with a key that never leaves
  it.
- Granting Funnel in policy **doesn't publish it by itself.** Something on the node still has to
  write a Funnel serve config. No shipped path does (§2): the committed config has no
  `AllowFunnel`, the file is read-only, `api` can't write serve config (PA0 4) and the wrapper's
  allowlist has no serve write.
- If someone with root in the sidecar does write one, the runtime tripwires turn phone access off
  within 5 s (30 s at worst) and tell the owner (§1). Removing the policy's `funnel` attribute (below) closes even
  that window.

**The posture we recommend and enforce:**
- **A personal tailnet only.** Before step 2, the wizard says plainly: "Don't connect Holdfast to a
  work or school Tailscale network. Its admins can see and reach this machine."
- **The Personal plan is non-commercial** (PA0 8). The docs say so, and this path isn't offered for
  business use or Holdfast Cloud.
- **A hard stop when the tailnet looks shared.** After sign-in, the API reads the tailnet name and
  the count of `User` entries in status. It requires a **typed confirmation** of the tailnet name if
  there's more than one user, or if the tailnet name is an organization domain rather than a
  personal login. A non-technical user can't just click OK past it (Gadfly S6).
  - PA-D3 defines the organization-domain test against what `CurrentTailnet.Name` actually contains
    for personal and organization tailnets, recorded from the test tailnet. It doesn't guess
    (Gadfly r3 minor).
- **The docs ship a policy snippet** that grants `holdfast:443` only to named users (VERIFY syntax:
  https://tailscale.com/kb/1337/policy-syntax). They also show how to remove the default `funnel`
  attribute (VERIFY: https://tailscale.com/kb/1223/funnel).
- **No peer enumeration in the UI.** The "count phones by OS" idea is dropped (Gadfly minor). The
  status client discards `Peer` (§1).

### 9. Failure behaviour, vendor dependence, threat model

**The app on the PC never depends on Tailscale.** Phone access does.

**Control-plane outage** (verified, PA0 12):
- **A running sidecar keeps serving.** Phones that were connected kept working for the roughly
  7 minutes tested, with control unreachable.
- **A sidecar that restarts during the outage doesn't serve.** It loads a cached netmap with 0
  peers, so it knows no clients. Phone access is down until the control plane returns, and it
  resumes within about 20 s after that.
- Status during that state is misleading: `BackendState` is `Running`, and `Health` says "You are
  logged out … network is unreachable". The app shows it as "Tailscale's coordination service is
  unreachable. Phone access comes back when it does." It never shows it as signed out and never
  offers re-auth there, because a new login during an outage could switch tailnets (§5). The exact
  detection signal is U-11.
- **The wrapper doesn't sign in during an outage either.** Its decide step enters sign-in only on
  `NeedsLogin` with control reachable. Otherwise it backs off and decides again (§1, B2). If
  `containerboot` exits during the outage (U-12), the wrapper re-decides instead of starting a
  login. When control returns, the next decide finds `Running` and serving resumes.
- **What restarts the sidecar:**
  - a PC reboot
  - a Docker daemon restart, including unattended upgrades of `docker-ce` or `moby-engine`
    (Gadfly r3 S5)
  - `holdfast update` when the sidecar's digest changes. It recreates the sidecar only then, which
    is compose's default, and it warns first if status shows control unreachable.
- The docs say this plainly: any of those during a Tailscale outage takes phone access down until
  the outage ends.

**HTTPS off on the tailnet** (PA0 7).
- The wizard keys off status:
  - `CertDomains` empty, or missing `Self.DNSName`, means HTTPS certificates are off.
  - `CurrentTailnet.MagicDNSEnabled: false` means MagicDNS is off.
  - It never calls `cert` to probe.
- With HTTPS off, the household sees step 3 of §7(b) and nothing after it:
  - "HTTPS is off on your Tailscale network. Your phone needs it for a real certificate. Turning it
    on publishes this machine's name (for example `holdfast.tail0000.ts.net`) in public certificate
    logs."
  - **Open Tailscale DNS settings** and **Check again** buttons.
  - QR 2 and pairing stay hidden, and no FQDN is added to `instance_access` until `CertDomains`
    contains the name.
- If HTTPS is turned off after pairing, Settings → Phone access shows the same banner. The phone
  can't complete TLS, and the PWA's cached shell shows "Can't reach Holdfast".
- **Verified (PA0 7):** with HTTPS on, `TerminateTLS` fetched a real certificate with no `cert` step,
  and status listed it in `CertDomains`.
- **U-9:** that `CertDomains` is empty when HTTPS is off. The upstream CLI makes the same inference
  (`cmd/tailscale/cli/cert.go`), but nobody observed it. Also open: the default on a fresh tailnet,
  and whether serving starts without a container restart once HTTPS is turned on. The control
  plane's own error text isn't needed, because the design doesn't parse it.
- **The exit from this stop is in the app (Gadfly r3 S6).** Once the owner turns HTTPS on and clicks
  **Check again**, `api` sees `CertDomains` list the name. The wrapper sees it too, and restarts its
  `containerboot` child once (§1, Serving). That's not a logout, so no host command is needed and
  the flag isn't toggled.

**Other failures:**
- **Key expiry:** banners at 30, 7 and 1 days. After expiry the node reports `NeedsLogin`. With
  control reachable, the wrapper re-authenticates the existing profile with `tailscale up` (U-12),
  and the PC shows **Reconnect** with the login URL to an owner session with step-up only (§1). The
  tailnet binding in §5 applies to the result.
- **MagicDNS or HTTPS off:** detected from status and deep-linked. **Manual: one click each.** The
  app can't flip tailnet settings without an admin API key, and it won't hold one.
- **Phone can't connect:** the PWA's cached shell shows "Can't reach Holdfast. Your Tailscale
  connection or Tailscale's service may be down." It doesn't blame the user during an outage
  (Gadfly r3 S5).
- **Tripwire fired:** phone access is off, and the PC says why (§1). The PC app is unaffected.

**Vendor trigger.** Lattice reopens this ADR if any of these happens:
- the free Personal plan's user cap drops below the 6 measured in PA0 8, or it starts requiring
  payment
- the terms forbid this use
- the stock image goes away, or Serve's TLS-terminated TCP or PROXY v2 support goes away (fallback 1
  in §4, then tsnet under Gadfly's conditions)

The localhost floor means a vendor change removes phone access, never the app. Headscale isn't
offered as a zero-config exit path: it needs a public coordination server and a domain. It stays a
manual option.

**Threat-model delta for Bulwark.**
- **New pieces:**
  - the upstream container (pinned digest, userspace, no published port) and its one-shot init
  - egress from that container only, over `ts-egress`, while phone access is on: control plane,
    DERP, STUN, peer UDP and **Let's Encrypt's ACME endpoints** (VERIFY list:
    https://tailscale.com/kb/1082/firewall-ports)
  - certificate issuance publishing the FQDN to CT logs
  - the `edge` and `app` networks
  - a one-bit flag volume
  - a read-only LocalAPI socket in non-root `api`, with a startup self-check and the serve-config
    tripwire
  - the plain-HTTP loopback site
  - multiple RP ids
- **A compromised sidecar** can:
  - read app traffic in plaintext and relay it, because TLS terminates there
  - enable Funnel from inside, and **lie to the tripwire**, because LocalAPI is the sidecar's own
    answer. The tripwire defends against misconfiguration and copied commands, not against a
    compromised sidecar. The policy `funnel` attribute removal (§8) is the control there.
  - forge PROXY headers to Caddy
  - join another tailnet

  It **can't** reach `db` or `redis`, `.env`, the document volume or the Docker socket.
- **A compromised `api`** additionally learns the tailnet's device inventory through the read-only
  socket (verified, PA0 4). It's already the most privileged component, so this is a privacy delta,
  not a control delta, as long as the UID rule holds. If `api` ran as root, the delta would become
  full node control and the certificate key. That's why the UID rule is a CI check, not a
  convention.
- **A compromised `web`** today can forge client IPs straight to `api`, which means unlimited
  passkey and setup guessing across forged IPs, targeted lockouts of up to 1 hour, and forged audit
  IPs (§1, B3). After PA1 it isn't on `app`, `api` ignores forwarded headers from it, and the
  tailnet listener accepts only the sidecar.
  - It can still reach Caddy's local listener inside the Docker network, so its requests are tagged
    `local` and hit the local buckets. The local family's shorter lock bounds that, and setup and
    recovery still need a host-minted token.

## Alternatives considered

| Option | Verdict |
|---|---|
| **Stock sidecar, userspace, TLS-terminated TCP + PROXY v2 over an internal TCP network (chosen)** | Gives the real client IP (PA0 1), native certificate renewal and no TUN device. Upstream's security releases arrive as a digest bump |
| The same, with a unix-socket upstream (revision 2's preference) | Trust by filesystem instead of network. **Refuted by PA0 2:** PROXY v2 to `unix:` drops the TLS connection silently, and without PROXY the client address is lost |
| Wrap `containerboot` as is for sign-in | **Refuted by PA0 5:** it exits after 60 s with no sign-in, and a restart policy mints a new URL every minute. The wrapper owns sign-in |
| Wrapper `exec`s `containerboot` (revision 3) | **Rejected (Gadfly r3 B1):** after the `exec` nothing watches the flag, so Turn off would do nothing until a restart. The wrapper supervises it as a child under `init: true` |
| Treat a `containerboot` exit as "needs sign-in" (revision 3) | **Rejected (Gadfly r3 B2):** an outage restart or a failed cert fetch would start a login, and possibly a new node identity. The decision comes from `BackendState` and control reachability |
| Trust `TRUSTED_PROXIES` as the `frontend` /24 (today) | **Rejected (Gadfly r3 B3):** `web` could forge client IPs to `api`. `api` trusts only Caddy, over a two-member `app` network |
| Host CLI for status (`docker compose exec tailscale tailscale status`) instead of a shared socket | Not needed: PA0 4 shows read-only access for a distinct non-root UID. It stays the contingency if the §6 startup self-check fails on a class of hosts (for example rootless, U-4). It costs one terminal step |
| Custom Go/tsnet `access` service (revision 1) | Its Funnel argument protected only against our own misconfiguration, which the CI test covers. It moves every Tailscale security fix onto our release train, and the roster has no Go reviewer. **Off the table per PA0 9.** It returns only through the vendor trigger |
| Stock sidecar, kernel mode, Caddy sharing its netns | Needs `NET_ADMIN` and `/dev/net/tun`, breaks rootless Docker, and a restart of the namespace owner strands Caddy. Rejected |
| Serve HTTPS proxy + `X-Forwarded-For` | Valid (PA0 3: Serve overwrites, doesn't append). Kept as fallback 1. Not primary, because it puts an HTTP proxy in the sidecar and adds `Tailscale-User-*` headers Caddy must strip |
| Caddy's built-in ts.net cert getter (socket into Caddy), or the `caddy-tailscale` plugin | Puts node control in the HTTP parser. Rejected |
| Host Tailscale + Serve, automated by the installer | Depends on the host's version and rights and on any serve config the user already has. **Kept as the documented manual path**, using the same Caddy PROXY listener published on loopback (deferred PA-D9) |
| Single pinned RP id + recovery token (revision 1) | Ordinary events become lockouts that only a terminal can fix (Gadfly S3). Rejected for multiple RP ids |
| https://localhost with a local CA | A browser warning, or a CA key on disk that can mint any certificate. Rejected for plain HTTP on loopback |
| Headscale / self-run coordination | Needs a public endpoint and a domain. Manual option only |
| Cloudflare Tunnel + Access | Public hostname, and Cloudflare sees plaintext. Rejected (rule 12) |
| Relay run by Holdfast | Costs money, gives us metadata and creates legal exposure. Holdfast Cloud only (ADR 0009) |
| WireGuard direct | Needs a router port-forward. Rejected (rule 12) |
| LAN-only mDNS + local CA | No access on LTE, and a CA-trust ordeal on the phone. Rejected |
| Funnel | Public. Forbidden by the serve-config test and the wrapper allowlist, and caught at runtime by the tripwires |

## Consequences

Stated plainly:
- **Minimum Docker Engine is 28.3.3, for every stack Holdfast ships** (§2).
  - **The check.** The installer and the `holdfast` wrapper compare `docker version`'s server version
    numerically.
  - **Below the floor they refuse `up` and `update`.** They exit non-zero and print the upgrade path:
    - Docker CE from Docker's own apt repository on Debian 12 and 13 and Raspberry Pi OS
      (https://docs.docker.com/engine/install/debian/, linked from the docs too)
    - a Docker Desktop update on macOS and Windows
  - There's no override flag in the zero-config path.
  - **The refusal never blocks getting data out.** `holdfast backup` and `holdfast export` run as
    one-off containers that publish no port, so they work below the floor.
  - **`holdfast recover` also works below the floor,** after a warning. It starts only `db`, `api` and
    Caddy's local site for the recovery token's 10-minute window, then stops Caddy again. The accepted
    exposure is the plain-HTTP login page reachable from the LAN for that window. The single-use token
    stays in the PC's own fragment.
  - So a Docker that regresses after an OS reinstall can't strand the household's data.
  - **Who meets the floor:** Ubuntu 24.04 with `noble-updates` (on by default), Fedora 43 and current
    Docker Desktop. Stock Debian 12 and 13 don't.
  - **Accepted: a numeric compare can't see distro backports.** A `docker.io` that a distro patched for
    CVE-2025-54388 without changing its version is still refused. The fix is the same Docker CE step.
  - **NAS boxes have no zero-config path.** Synology, QNAP, Unraid and TrueNAS ship vendor Docker builds
    that users can't upgrade. Below the floor the installer says so by name and points to the manual
    path. The manual path's docs require a host firewall that drops forwarded and LAN traffic to the
    Docker networks. Where the box can't do that, Holdfast isn't supported on it, and the docs say so
    plainly.
  - Someone who runs `docker compose up` by hand bypasses the check. The docs state the floor and
    why: below it, LAN neighbours can reach the plain-HTTP localhost site.
- **During a Tailscale control-plane outage**, the PC app works as normal. Phone access keeps working
  while the sidecar stays up. If the sidecar restarts during the outage, phone access is down until
  the outage ends. That covers a PC reboot, a Docker daemon restart or an update that changes the
  sidecar's digest. The app says so, the wrapper backs off instead of signing in, and nobody is
  offered re-auth.
- **The UID rule:** `api` is never UID 0 and never shares the sidecar's UID, and no operator UID is
  ever configured. Only `api` mounts the LocalAPI socket, read-only, and its status client keeps
  only `Self`, `CurrentTailnet`, `CertDomains`, `Health` and a user count.
  - CI enforces the compose half.
  - `api`'s startup self-check enforces it at runtime and fails closed.
  - Code review and a unit test enforce the status-client half.
- **Funnel or an unexpected listener at runtime** turns phone access off and tells the owner. It's
  never served.
- **When HTTPS is off on the household's tailnet**, the phone wizard stops at "Turn on HTTPS". It
  shows the certificate-log disclosure, a link to Tailscale's DNS settings and a re-check button.
  Nothing is paired and no tailnet origin is added until status lists the certificate domain.

Also:
- **Easier:**
  - the app works on the PC two steps after Docker
  - the phone is an optional nine-step flow inside the app
  - a vendor change can't take the app down
  - client IPs are correct on the tailnet path
  - rule 12 gets mechanical checks for the default install
- **Harder:**
  - two ingresses and a per-request RP id in auth code
  - an instance-level table and `rp_id` (migration token)
  - a supervising wrapper with a state machine, flag watch, signal forwarding and a tripwire
    (re-estimated at about 100 lines of `sh`, with a CI transition test), plus an init service for
    volume ownership
  - a network split (`app`) and a narrower `TRUSTED_PROXIES` pulled forward into PA1
  - Safari's `Secure` cookie handling on localhost may force a per-origin cookie rule
  - the Docker floor adds a step on Debian, refuses patched distro packages, and excludes NAS vendor
    builds from zero-config
  - macOS and Windows rows have no CI. GitHub's macOS runners have no Docker, and its Windows
    runners run only Windows containers. Those rows rest on owner hardware (Gadfly F5, owner queue)
  - the frontend subnet that's already pinned in `docker-compose.yml` is an existing collision risk,
    unpinned in deferred PA-D10
- **Docs:**
  - PLAN 12.1 and Phase 8.5 change in the next Section 20 amendment (Beacon).
  - `client-ip-check.md` gains a tailnet row.
  - The user docs gain the outage, Docker-floor and non-commercial notes.

## Owner queue (money, accounts, legal)

1. **The test Tailscale account and tailnet** for the owner checks in §12. It must be separate from
   the personal tailnet, and check U-9 needs a **fresh** account.
2. **Owner devices** for U-6, U-7 and deferred acceptance: a macOS host (Safari and Chrome), a
   Windows host (Edge), a hardware authenticator or phone for Firefox, one iPhone and one Android
   phone.
3. **Steering users to Tailscale's free plan** for the optional phone path. Each household uses its
   own account and accepts its own terms, including the non-commercial clause. Holdfast holds no
   account and no API key.
4. **Docker Desktop licence** wording in the macOS and Windows docs (VERIFY:
   https://www.docker.com/legal/docker-subscription-service-agreement/), and whether to also name
   Podman Desktop or Rancher Desktop.
5. **Install script URL:** recommended as a GitHub Release asset (no new service). Using
   `holdfastfinance.app` would involve the domain account.
6. Code signing for a future native launcher (money). Deferred, not needed for scripts.

## Open questions (Keel / Bulwark)

1. Rule 10 versus a Windows PowerShell 5.1 bootstrap. This is a CLAUDE.md change, so it goes
   through the digest. It saves one Windows step.
2. Safari fallback (§3): a non-`Secure` host-only cookie on the local origin, or a browser
   recommendation. U-7 decides whether it's needed.
3. Local port `47800`: check for clashes with common self-host apps.
4. Default machine name `holdfast` (disclosed in CT logs, changeable at install) versus a neutral
   name.

## 10. Response to Gadfly (`docs/adr/reviews/0021-gadfly.md`)

| Item | Response |
|---|---|
| F1 the manual path removes the reason for tsnet | **Accepted, and confirmed by PA0 1 and 9.** The stock sidecar is the decision |
| F2 the Funnel argument | **Accepted.** A CI serve-config test replaces "impossible in code". PA0 4 adds that `api` can't write serve config either |
| F3 Go cost has no owner | **Accepted.** tsnet is off the table, and if it returns, Gadfly's conditions apply |
| F4 the size displaces Phase 5 | **Accepted.** Phase 8.5. Only PA0 (S, done) and PA1 (M) are specified. The rest is deferred |
| F5 owner-dependent acceptance, no CI for Docker Desktop | **Accepted.** In Consequences, the owner queue and §12 |
| S1 step counts / households | **Mostly accepted.** Key expiry is counted, Add to Home Screen is optional, and per-member costs are counted. **Two partial rejections:** (1) the **Turn on** click stays, because it's the consent to start talking to a third party; (2) the pwsh 7 step stays counted until rule 10 is amended through the digest |
| S2 localhost first | **Accepted** (§3). PA0 10 confirms it for Chromium and for Firefox cookies |
| S3 pinned RP id lockouts | **Accepted** (§5). Multiple RP ids, with localhost as the floor that never changes |
| S4 reconnect into another tailnet | **Accepted.** Tailnet identity is bound, adopting another one needs a typed confirmation, and recovery never crosses tailnets. The outage UI doesn't offer re-auth (§9) |
| S5 token in the fragment / history | **Accepted** for every token: exchange on load, `replaceState`, single use, 10 minutes. QR 2 also needs PC approval |
| S6 employer / shared tailnets | **Accepted** (§8), with a typed confirmation and no peer enumeration |
| S7 pinned subnets / trust by filesystem | **Accepted in part.** No pinned subnets anywhere. Trust by filesystem was tried and refuted (PA0 2), so it's an unpinned internal `edge` network, with the forged-IP risk recorded (§1) |
| S8 Docker floor | **Accepted.** It's 28.3.3 (PA0 11), and the Debian step is counted |
| S9 outage / "exit path" | **Accepted and now measured** (PA0 12). Headscale isn't called a zero-config exit. The vendor trigger is named |
| S1 (story) PROXY fix on the owner's host | **Partly rejected.** It isn't scheduled ahead of Phase 8.5, because Keel's schedule ruling wins and the problem affects one install's audit rows, not security. PA1 builds the same Caddy PROXY listener, so PA-D9 is small afterwards |
| §9 rec. 2 "the LocalAPI socket is never shared" | **Rejected, on evidence.** PA0 4 shows a read-only share to a distinct non-root UID can't write. The cost is the device-inventory privacy delta, contained by the filtered status client. The host-CLI route stays the contingency (Alternatives) |
| Minors (Host 421, no-CORS test, drop peer-by-OS, rule 12 via the digest) | **All accepted.** The 421 also covers tailnet IP literals |
| Rank 1–4 VERIFYs before acceptance | **Accepted.** Ranks 1 and 4 resolved in PA0. Rank 2 is U-8 and rank 3 is U-9, both owner checks |

## 10a. Response to Gadfly on revision 3 (`docs/adr/reviews/0021-gadfly-r3.md`)

| Item | Response | Where |
|---|---|---|
| B1 wrapper `exec`s `containerboot`, so nothing watches the flag | **Accepted.** `containerboot` and the bare `tailscaled` always run as children under `init: true`. The wrapper loops every 5 s, forwards signals and turns off within one loop with the container running. Re-estimated at about 100 lines with a CI transition test | §1 wrapper, §6 Revocation, Consequences |
| B2 an exit is treated as "needs sign-in" | **Accepted.** A decide step reads `BackendState` and control reachability. It signs in only on `NeedsLogin` with control reachable, and otherwise backs off. It uses `tailscale up`, not `login`. U-12 is added and gates the overlay | §1 Decide, §9 outage and key expiry, §12 |
| B3 the forged-IP risk is understated | **Accepted.** All three impacts are stated. `app` network (caddy + api), `TRUSTED_PROXIES` narrowed to Caddy's address, sidecar-only tailnet listener, credential and setup scopes, all in PA1. A local bucket family in PA-D2. §4's per-account claim corrected | §1, §4, §9 threat model, §13 |
| B4 "accepts no other serve config" is overstated | **Accepted.** Reworded to "no shipped path writes serve config; a runtime check catches any that does". Two tripwires (`api` every 30 s, wrapper every loop) fail closed and latch. A wrapper command allowlist in CI. A compromised sidecar can lie to the tripwire, which is stated | §1, §2, §8, §9 |
| S1 operator UID; CI sees compose only | **Accepted.** Operator is in the rule and forbidden in args and the wrapper. `user:` must be numeric. `api` runs a startup self-check (UID ≠ 0, ≠ socket owner, wrong-method `logout` → `403`) and fails closed, which also covers U-4 per install | §1 CI, §6 |
| S2 Let's Encrypt isn't named | **Accepted.** Named under rule 9, with CT. ACME is in the egress list. `chmod` runs before `chown` in `tailscale-init` | Context, §1, §9 |
| S3 the acceptance gate deadlocks with PA1 | **Accepted.** Two stages: "Accepted for the sidecar overlay" needs only Harbor's items and gates PA1. The owner items gate PA-D2, PA-D3 and PA-D8. PA1's branch produces the evidence, the ADR status change merges first, then PA1 | §12, §13 |
| S4 the floor's blast radius | **Accepted.** Refusal kept, for `up` and `update` only. Upgrade path named and printed. `backup` and `export` never blocked, and `recover` works with a bounded window. NAS vendor builds named, with no zero-config path. Raspberry Pi OS follows Debian. Distro backports accepted as refused. The floor applies to every shipped stack | §2, §7(a), Consequences |
| S5 outage copy blames the user; daemon restarts missing | **Accepted.** New offline copy. Docker daemon restarts and unattended upgrades are listed as sidecar restarts | §9 |
| S6 "Turn on HTTPS" has no in-app exit | **Accepted.** The wrapper restarts its `containerboot` child once when `CertDomains` becomes set, so there's no logout and no host command | §1 Serving, §9 |
| Minor: organization-domain heuristic | **Accepted.** PA-D3 defines it from recorded `CurrentTailnet.Name` values | §8 |
| Minor: `AuthURL` is a bearer capability | **Accepted.** Returned only to an owner session with fresh step-up | §1, §9 |

## 11. PA0 results and what each one changed

The full evidence is in `docs/adr/reviews/0021-pa0-spike.md` (Harbor, 2026-11-21, stock image
v1.104.7, Docker 29.9.3, Fedora 43).

| # | Result | Change to this ADR |
|---|---|---|
| 1 | PASS: PROXY v2 to a TCP upstream, `src` = client tailnet address | Primary design confirmed; field names recorded (§1) |
| 2 | FAIL: PROXY v2 to `unix:` drops TLS silently | Unix socket removed. TCP over the unpinned internal `edge` network, with `ts-egress` for the sidecar. Serve-config test forbids `unix:` (§1) |
| 3 | PASS: HTTPS mode overwrites `X-Forwarded-For`, passes `X-Real-IP`, adds `Tailscale-User-*` | Fallback 1 confirmed. Caddy strips `X-Real-IP`, `Forwarded`, `Tailscale-*` on both sites (§1, §4) |
| 4 | PASS with caveats: read-only for a non-root distinct UID; root gets full control and the cert key; read exposes `Peer` | UID rule (§6) plus CI. Filtered status client (§1). Host-CLI fallback demoted to contingency. Rootless reasoning is U-4 |
| 5 | PASS plus finding: flag gating, `--no-logs-no-support`, cap-drop, read-only root, non-root all work; containerboot exits at 60 s; daemon's own UID can write | A wrapper that owns sign-in (§1), made a supervising state machine in revision 4. `tailscale-init` for volume ownership. Same-UID rule. U-1, U-2, U-3, U-12 |
| 6 | PARTIAL: restart keeps name and address | Recorded. U-8 (owner) for re-auth, lost state, removal |
| 7 | PARTIAL: cert fetched automatically and listed in `CertDomains` | Wizard keys off `CertDomains` and `MagicDNSEnabled`, never probes `cert` (§9). U-9 (owner) |
| 8 | PASS: 6 users, unlimited user devices, non-commercial; shared-in counting unstated | Figure corrected to 6. Vendor trigger re-based. No tag. Non-commercial in docs. U-10 |
| 9 | tsnet not needed | tsnet removed from the fallback chain (§4) |
| 10 | PARTIAL: Chromium pass, Firefox cookies pass | U-6, U-7 (owner) |
| 11 | Floor 28.3.3; Debian 12 and 13 below | Floor and Linux row updated. Behaviour below the floor (Consequences) |
| 12 | Running node keeps serving; restarted node doesn't until control returns | Outage behaviour stated firmly, outage UI, update rule (§9). U-11 |

## 12. Unverified assumptions and the acceptance gate (why this stays Proposed)

**Acceptance comes in two stages, so PA1 isn't deadlocked on owner hardware (Gadfly r3 S3):**
1. **Accepted for the sidecar overlay.** Needs U-1, U-2, U-3, U-5, U-11, U-12 and U-13. All of them
   are Harbor's and need only the test tailnet.
   - This stage gates **PA1**. PA1 doesn't merge while the ADR is only Proposed.
   - So there's no deadlock: Harbor runs the checks against **PA1's branch** and records the results
     as a PA0 addendum. Keel merges the ADR status change first, then PA1.
   - U-4 is recorded but doesn't gate, because the §6 startup self-check verifies it on every
     install and fails closed.
2. **Accepted.** Additionally needs U-6 to U-10, the owner rows. They gate only the stories that
   depend on them:
   - U-6 and U-7 gate PA-D2 (the localhost-first stack)
   - U-8, U-9 and U-10 gate PA-D3 (the phone wizard)
   - all of them gate PA-D8 (real-device acceptance)

A row counts as closed when it's verified, or when its fallback is chosen and written here.

| ID | Assumption | Exact check | Who |
|---|---|---|---|
| U-1 | The bare-`tailscaled` sign-in (`tailscale up`, no timeout) keeps one login URL valid past 60 s, and handing over to a `containerboot` child on the signed-in state applies `TS_SERVE_CONFIG` with no new sign-in | On the test tailnet: start the wrapper with a fresh state volume, create the flag, wait 3 minutes, check `AuthURL` hasn't changed, sign in, then confirm `serve status` shows the TCP handler and check 1's PROXY probe passes | Harbor |
| U-2 | Removing the flag logs out a **signed-in** node within one loop, with the container still running, and the node drops off the tailnet | Signed-in node: remove the flag. Within 10 s, confirm `BackendState` is `NeedsLogin` (or the socket is gone), the container is still up, and the node is absent from another peer's `tailscale status` | Harbor |
| U-3 | Serve works with the sidecar non-root: the cert fetch writes to the state volume, and `TerminateTLS` plus PROXY v2 work | Repeat checks 1 and 7 with `--user` set to the sidecar UID and volumes set up by `tailscale-init` (`chmod` then `chown`) | Harbor |
| U-4 | The UID rule holds under rootless Docker (non-gating: the §6 self-check covers it per install) | Repeat check 4 (UID-1000 client denied writes, UID-0 client allowed) on a rootless daemon | Harbor |
| U-5 | Caddy's `proxy_protocol` wrapper rejects connections without the header, and the tailnet listener accepts only the sidecar, by bind address (a) or by a single-address `allow` (b) | PA1: a Caddy test with and without the header, and a connection attempt from `web` to the tailnet listener that must be refused. Record which of (a) and (b) is used | Harbor (PA1) |
| U-6 | Firefox WebAuthn create and get on `http://localhost` | **Owner check 3** | Owner |
| U-7 | Safari and Chrome (macOS) and Edge (Windows): secure context, `Secure` cookie, WebAuthn on `http://localhost` | **Owner check 3.** A Playwright WebKit run on a host with WebKit's libraries is a useful pre-check (Sounding) but doesn't replace Safari | Owner |
| U-8 | Re-auth after key expiry, lost state and old-node removal give recoverable name outcomes | **Owner check 1** | Owner |
| U-9 | On a fresh tailnet, HTTPS (and maybe MagicDNS) is off, turning it on is one click, `CertDomains` is empty while it's off, and serving starts afterwards without a restart | **Owner check 2** | Owner |
| U-10 | Shared-in users don't count toward the 6-user cap | **Owner check 4** (optional, low blast radius: it changes the household-size note only) | Owner |
| U-11 | The status fields that distinguish "control unreachable" from "signed out". Check 12 saw `Running` plus a `Health` message. The wrapper's decide step and PA-D5 both match on them | Repeat check 12's restart-during-outage on a signed-in node **and** on a `NeedsLogin` node. Record the exact `BackendState` and `Health` entries for each | Harbor |
| U-12 | (a) During an outage restart, the wrapper never runs `tailscale up`, whatever `containerboot` does. (b) `tailscale up` on a node with an existing profile re-authenticates **that** profile (same name, address and tailnet) instead of creating a new one. (c) What re-enabling after Turn off produces. (d) Whether `containerboot` exits on a failed certificate fetch (informational: the decide rule is state-based either way) | (a) Signed-in node: block egress as in check 12, restart, watch for 10 minutes. Record `containerboot` exits and codes, and confirm from the wrapper log that it only decides and backs off. Restore egress and confirm serving resumes with no login. (b) Force expiry with LocalAPI `set-expiry-sooner` as the daemon's UID (or owner check 1(a)), then let the wrapper re-authenticate. Confirm one profile (`tailscale switch --list`) and the same name and address. (c) Turn off, then on, and record the name. (d) Owner check 2, before HTTPS is enabled | Harbor ((d): owner) |
| U-13 | The tripwires don't false-trip: the serve config read back through `GET /localapi/v0/serve-config` and `tailscale serve status --json` (which must exist in the pinned version) compare equal to the committed config after substitution and normalisation. They do trip on an extra listener | After check 1's setup, diff both read-backs against the substituted committed file. Then, as root in the test sidecar, add a **tailnet-only** second TCP listener (never Funnel, rule 12) and confirm both tripwires turn phone access off and latch. The `AllowFunnel` case is a unit test on a fixture only | Harbor |

### Owner checks

Run these on the test account from owner-queue item 1, never the personal tailnet. Steps 1 and 2
come from the spike file. Report the results as behaviour, with no tailnet names or `100.x.x.x`
addresses.

1. **Name stability (check 6, U-8).** Use the spike node `holdfast-spike`.
   - (a) In the admin console, open Machines → `holdfast-spike`. If key expiry is disabled, turn it
     back on, then click **Expire key now**. In the container, run `tailscale up` and sign in.
     Record whether the name and IP stayed the same, and whether `tailscale switch --list` shows
     one profile or two (U-12 (b)).
   - (b) Stop the node and run `docker volume rm hfspike_ts_state`. Start a new node with the same
     hostname while the old machine is still listed. Expect `holdfast-spike-1`.
   - (c) Delete the old machine in the console and recreate the node with the same hostname. Record
     whether it reclaims `holdfast-spike`.
2. **HTTPS on a fresh tailnet (check 7, U-9).**
   - Create a new test Tailscale account and sign a node in to it. Record whether MagicDNS and HTTPS
     are on by default.
   - Before enabling HTTPS: record `CertDomains` from `tailscale status --json` and the output of
     `tailscale cert <fqdn>`. Also record whether Serve shows an enable URL.
   - With HTTPS still off, start the sidecar with the committed serve config. Record whether
     `containerboot` exits, and its `BackendState` (U-12 (d)).
   - Enable HTTPS on the admin DNS page and count the clicks, including the CT acknowledgement.
   - Repeat both reads, then curl the node's FQDN from a peer and record whether it serves without a
     container restart.
3. **Browsers on `http://localhost` (check 10, U-6, U-7).** Use Harbor's loopback test page (a
   loopback-only server that sets a `Secure` cookie and a `Secure; HttpOnly; SameSite=Strict` cookie,
   then calls `navigator.credentials.create` and `get` with `rp.id = "localhost"`). In Safari and
   Chrome on macOS, Edge on Windows, and Firefox with a real authenticator (security key or phone
   hybrid), record:
   - `isSecureContext`
   - whether both cookies come back in the `Cookie` header
   - whether create and get succeed
4. **Shared-in users (check 8 caveat, U-10; optional).** Share the test node with a second Tailscale
   account. Record whether the test tailnet's user count in the admin console goes up.

## 13. Stories

Schedule: Phase 8.5 (Q2, `ROADMAP.md`). Nothing here displaces Phase 5. The IDs use `PA` because
`A1` and `S1` already exist in the roadmap.

### PA0: verification spike (S, Harbor + Sounding): done, with an addendum outstanding

The results are in `docs/adr/reviews/0021-pa0-spike.md` and are summarized in §11. Still open:
- Harbor's addendum: U-1 to U-5 and U-11 to U-13. They need only the test tailnet, and most run
  against PA1's branch.
- The owner checks: U-6 to U-10 and U-12 (d).

### PA1: smallest Linux slice (M, Harbor + Tide for the auth scopes; Bulwark reviews)

**Merge rule:** PA1 merges only after the ADR is "Accepted for the sidecar overlay" (§12).

- Scope:
  - `infra/docker-compose.access.yml`:
    - the stock sidecar: pinned digest, userspace, numeric non-root UID, `init: true`, hardening as
      in §1, read-only serve config
    - the supervising wrapper (§1) and its command allowlist test
    - `tailscale-init`
    - the `edge`, `ts-egress` and `app` networks, unpinned
  - **Client-identity narrowing (B3, pulled forward from PA-D10):**
    - `web` off every network `api` is on
    - `TRUSTED_PROXIES` resolved to Caddy's address only
    - the credential and setup rate-limit scopes
  - Caddy's tailnet listener on `edge`:
    - sidecar-only, by bind address or a single-address `allow` (U-5)
    - PROXY v2 wrapper that rejects connections without the header
    - ingress header and XFF overwrite
    - stripping of `X-Real-IP`, `Forwarded` and `Tailscale-*`
  - `api`:
    - a fixed numeric non-zero UID distinct from the sidecar's
    - the startup self-check (§6)
    - the serve-config tripwire and the filtered status client (§1)
  - `scripts/check-serve-config.py`
  - the `check-compose-ports.py` extensions: ports, hardening, unpinned networks, UID rule, numeric
    `user:`, no `--operator`, socket mount and `TRUSTED_PROXIES` width. Plus `make up-access`.
  - `APP_HOST` still set by hand from `tailscale status`, so there's a single RP id until PA-D1
    ships
- Acceptance evidence:
  - CI:
    - The serve-config test rejects fixtures that contain `AllowFunnel`, `Web`, `HTTPS`, a `unix:`
      target or a second port.
    - The compose test rejects `api` as UID 0, as the sidecar's UID or as a non-numeric `user:`. It
      also rejects `--operator`, a pinned subnet, and `web` on `app`.
    - The wrapper transition test covers every state in §1, including "exit while control is
      unreachable does not sign in" and "flag removed while serving".
  - `make ports` is green
  - U-1, U-2, U-3, U-5, U-11, U-12 (a–c) and U-13 verified, and recorded in the PA0 addendum before
    merge
  - a request from `web` straight to `api` with a forged `X-Forwarded-For` is recorded with `web`'s
    own address
  - on a Linux host, a phone on the test tailnet registers a passkey at the FQDN, and `audit_log.ip`
    is the phone's tailnet address (Sounding reports counts only)
  - a device off the tailnet can't resolve the name or connect

### Deferred to Phase 8.5 (rough sizes, not yet specified)

| ID | Story | Size |
|---|---|---|
| PA-D1 | Multiple RP ids: `instance_access`, `webauthn_credential.rp_id`, per-ingress RP selection, exact origin checks, Host 421 (including IP literals), no-CORS test (Tide, migration token, Bulwark) | M |
| PA-D2 | Localhost-first default stack: Caddy local site on `127.0.0.1:47800`, setup on local ingress only, token exchange + `replaceState`, Safari cookie rule, local-ingress rate-limit family (Harbor + Tide; gated by U-6, U-7) | S |
| PA-D3 | In-app "Connect your phone" (gated by U-8, U-9, U-10): flag write, step-up-only `AuthURL`, organization-domain test from recorded `CurrentTailnet.Name` values, `CertDomains` / MagicDNS gating, tailnet binding + typed confirmations, shared-tailnet warning, pairing QR + PC approval (Tide + Prism) | M |
| PA-D4 | `holdfast recover` (local ingress only, runs as `api`'s UID) (Tide, Bulwark) | S |
| PA-D5 | Settings → Phone access, expiry banners, HTTPS-off, control-unreachable and tripwire states (U-11), PWA offline copy (Prism) | S |
| PA-D6 | **Superseded by ADR 0037 and `docs/planning/INSTALL-PLAN.md` (IN7, IN10, IN15 and IN-A2); PA-D2 is pulled into that plan as IN-A1.** Original scope: Installer `install.sh` / `install.ps1`, `holdfast` wrapper, Docker floor 28.3.3 with numeric compare, refusal of `up`/`update` only, printed upgrade path, NAS detection message, `backup`/`export`/`recover` below the floor, update rule for the sidecar, digest-pinned compose (Harbor) | M |
| PA-D7 | Docs: per-OS guide with the §7 counts, Debian Docker step, node sharing for members, ACL snippet, Funnel attribute removal, outage behaviour, non-commercial note, backup/restore, revocation, manual paths (Beacon) | S |
| PA-D8 | Real-device acceptance on Linux, macOS and Windows hosts, iOS and Android (Sounding; owner devices) | M |
| PA-D9 | Host-Serve manual path: loopback-published PROXY listener + documented `tailscale serve` command (fixes the owner's host) (Harbor) | S |
| PA-D10 | Unpin the `frontend` subnet (the `TRUSTED_PROXIES` rework moved to PA1) (Harbor, Bulwark) | S |

Deferred total: about 4 M + 6 S, plus PA1 (M).

## VERIFY index (docs facts the design doesn't hinge on)

| Claim | Where to check |
|---|---|
| Tailscale client licence BSD-3-Clause (upstream image, AGPL-compatible) | https://github.com/tailscale/tailscale/blob/main/LICENSE |
| Default policy is allow-all among members; flow logs by plan | https://tailscale.com/kb/1192/acl-samples, https://tailscale.com/pricing |
| Default `funnel` node attribute in new tailnets | https://tailscale.com/kb/1223/funnel |
| CT publication of the FQDN when HTTPS is on | https://tailscale.com/kb/1153/enabling-https (read in PA0 7; the wizard copy quotes it) |
| Policy grant syntax for the docs snippet | https://tailscale.com/kb/1337/policy-syntax |
| Firewall and egress list | https://tailscale.com/kb/1082/firewall-ports |
| Client-side netmap caching guarantees (beyond PA0 12's measurement) | Tailscale's concept page on netmap caching |
| Docker Desktop terms | https://www.docker.com/legal/docker-subscription-service-agreement/ |

Read during PA0 and no longer VERIFY: free-plan caps and terms (kb/1154, pricing), key expiry of
180 days (kb/1028), machine-name suffixing (kb/1098) and the Docker 28.0.0 and 28.3.3 release notes.
