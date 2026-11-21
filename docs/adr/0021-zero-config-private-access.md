# 0021. Zero-config private access: localhost first, phone through a stock Tailscale sidecar

- Status: Proposed (revision 2)
- Date: 2026-11-21
- Deciders: Lattice (architect), Keel. The owner asked for it (paraphrased: people who download
  Holdfast shouldn't have to understand reverse proxies or tunnels).
- Reviews: Gadfly, `docs/adr/reviews/0021-gadfly.md` (it rejected revision 1). §10 answers it point
  by point. Bulwark threat-models the design before PA1.
- Revision 2 changes:
  - The custom Go/tsnet `access` service is replaced by the stock `tailscale/tailscale` container.
  - The app works on the PC at `http://localhost` straight after install. Phone access becomes an
    optional second step inside the app.
  - Credentials can belong to more than one accepted RP id, replacing the single pinned RP id.
  - The story list is cut to a verification spike plus the smallest Linux slice. Everything else is
    deferred to Phase 8.5.

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
- **Hard rule 9:** this ADR covers Tailscale's coordination, DERP and certificate services, plus the
  upstream `tailscale/tailscale` image, as dependencies of the optional phone path.
- **Hard rule 12:** nothing public.
- **Hard rule 13:** the owner's tailnet name is personal. It never appears in the repo. Examples use
  `holdfast.tail0000.ts.net`.
- **ADR 0013:** no paid service for the project.
- **ADR 0009:** a relay run by Holdfast belongs to Holdfast Cloud.
- **ADR 0010:** today's RP id is `APP_HOST`.
- **ADR 0018:** compose plus GHCR is the supported install.
- **Roadmap:** this is **Phase 8.5 work**. It must not displace the Q1 finish line (Phase 5,
  `docs/planning/ROADMAP.md`).

Every claim marked **VERIFY** is unchecked. The items the decision depends on are in the PA0 spike
(§9) and are checked before the ADR is accepted.

## Decision

### 1. Two ingresses, one Caddy

```
PC browser ──▶ http://localhost:47800 ──▶ caddy "local" site (plain HTTP, host loopback only)
                                              │
phone ──WireGuard──▶ tailscale (stock image, userspace)        ├──▶ api / web (frontend, unchanged)
                     serve: :443 TLS-terminated TCP            │
                     + PROXY v2 ──(unix socket | edge net)──▶ caddy "tailnet" site
```

- **Local ingress (always on).** Caddy publishes one host port, `127.0.0.1:47800`. It's a
  plain-HTTP site that answers only `Host: localhost:47800` and redirects `127.0.0.1:47800` to it,
  because WebAuthn doesn't allow an IP literal as the RP id.
- **Tailnet ingress (off until the owner turns it on).** A digest-pinned **stock
  `tailscale/tailscale`** container in userspace mode (no TUN device, no `NET_ADMIN`) runs Tailscale
  Serve from a **committed, read-only `TS_SERVE_CONFIG`**: one TCP handler on `443`, TLS terminated
  with the node's own `${TS_CERT_DOMAIN}` certificate, forwarded to Caddy's tailnet site with a
  PROXY v2 header (VERIFY, PA0 item 1).
  - The forward target is a **unix socket** on a volume that only `tailscale` and `caddy` mount, if
    Serve can forward TCP to one (PA0 item 2). That way nothing is trusted by IP.
  - Otherwise it's an **unpinned** internal network `edge` whose only members are `tailscale` and
    `caddy`. The accepted risk is that a compromised `frontend` container could connect to Caddy's
    tailnet listener and forge client IPs. That affects rate-limit buckets and audit rows, not
    authentication.
  - No new pinned subnet either way (Gadfly S7).
- **Caddy tags the ingress.** Each site overwrites `X-Holdfast-Ingress: local|tailnet` and
  `X-Forwarded-For {remote_host}`, and strips any client-sent copies. The API trusts both headers
  only from `TRUSTED_PROXIES`, as today. Caddy's security headers, CSP and 1 MB body cap apply to
  both sites. HSTS goes on the tailnet site only.

**What the container is allowed to do,** enforced by CI:
- `scripts/check-serve-config.py` parses the committed JSON and fails unless:
  - the only top-level key is `TCP`
  - the only port is `"443"`
  - the handler has only `TerminateTLS: "${TS_CERT_DOMAIN}"`, `TCPForward` set to the Caddy target,
    and `ProxyProtocol: 2`
  - there is no `Web`, `AllowFunnel`, `Services` or `Foreground` key anywhere
- `check-compose-ports.py` extends to the `tailscale` service. It fails unless:
  - no `ports`, `TS_USERSPACE=true` and `TS_ACCEPT_DNS=false`
  - `TS_SERVE_CONFIG` is mounted read-only
  - `TS_TAILSCALED_EXTRA_ARGS` contains `--no-logs-no-support` (VERIFY, PA0 item 5) and
    `TS_EXTRA_ARGS` contains no `--advertise-*`, `--exit-node*` or `--ssh`
  - the LocalAPI socket is shared with no service except `api`, read-only (below)

**Off until the owner turns it on.** Without consent, the sidecar must not contact Tailscale at all.
An unauthenticated node already registers with Tailscale's control plane to get a login URL.
- The compose `entrypoint` wraps the stock `containerboot` in about 10 lines of `sh`. It waits for a
  flag file, `phone-access/enabled`, on a volume that `api` can write and `tailscale` mounts
  read-only, then starts `containerboot`.
- When the flag disappears, the wrapper runs `tailscale logout` and goes idle.
- That one bit is the app's only write channel to the sidecar. CI runs shellcheck on the wrapper and
  a compose test.

**Status for the in-app wizard.** The app needs the login URL, `Self.DNSName`, `CurrentTailnet`,
MagicDNS and certificate state, key expiry and the user count. It gets them by sharing the sidecar's
LocalAPI socket into `api`, which runs as a non-root UID. LocalAPI grants only **read** access to a
non-root, non-operator peer (VERIFY, PA0 item 4).
- Required on read access: status works, and `up`, `login`, `logout`, `cert`, `serve`, `funnel` and
  `set` are all denied.
- **If PA0 shows that write access is reachable, the socket is never shared.** The fallback is the
  host CLI: `holdfast phone` runs `docker compose exec tailscale tailscale status --json`, opens the
  login URL, and hands the FQDN and tailnet identity to `app.cli`. That costs one terminal step and
  makes the wizard partly out-of-app.
- Either way, the app never holds a Tailscale write capability or an API key.

**tsnet** (revision 1's choice) is the last fallback. It's used only if PA0 shows that Serve can't
emit PROXY v2 from the stock container *and* the XFF fallback (§4) fails. If that happens, Gadfly's
conditions apply first: Harbor named as CVE owner, a release within 72 hours of a Tailscale security
bulletin, and a Go-capable review gate.

### 2. Rule 12, restated (merges through the owner digest, not on acceptance)

Proposed CLAUDE.md wording. Keel puts it in a PR that the weekly digest lists for veto. It isn't
applied automatically when this ADR is accepted.

> 12. **Networking:** Holdfast is never reachable from the public internet. The default stack
> publishes only Caddy, on `127.0.0.1`, for the PC's own browser. Phone access, when the owner turns
> it on, comes only through the bundled Tailscale container, whose committed serve config allows
> tailnet `:443` and nothing else (no Funnel). The manual path binds Caddy to the Tailscale IP only.
> Never add a public port, port-forward, Funnel or public tunnel. `make ports` and the serve-config
> test check this. The service worker caches the app shell only, never API responses.

The checks:
- `make ports` covers default, access and prod. In default and access, only `caddy` publishes, on
  `127.0.0.1:47800`. In prod, `TAILSCALE_BIND_IP` must be in `100.64.0.0/10` or
  `fd7a:115c:a1e0::/48`.
- `check-serve-config.py`, as listed in §1.
- **The Docker floor.** Older Docker Engine releases let hosts on the same LAN reach
  `127.0.0.1`-published ports. The installer refuses an Engine older than the fixed release
  (believed to be 28.0, VERIFY: https://docs.docker.com/engine/release-notes/28/) and names the fix.
  On a stock distro package that's one extra Linux step (Gadfly S8). With the bug, the plain-HTTP
  localhost site would be reachable from the LAN, so the floor is what makes "loopback only" true.
- Sounding's release probe: from a device that isn't on the tailnet, the FQDN doesn't resolve, and
  the host's LAN and public addresses refuse `47800` and `443`.

### 3. Localhost first: what Caddy and TLS do, and why it's safe

**There's no TLS on the local ingress.** `http://localhost` counts as a secure context ("potentially
trustworthy origin"), so WebAuthn and `Secure` cookies work there in the major browsers (VERIFY per
browser, PA0 item 10). Safari's `Secure`-cookie handling on `http://localhost` is the known doubt.

The alternatives are both worse:
- `tls internal` on `https://localhost` shows a certificate warning, and Chrome disables WebAuthn on
  a certificate error.
- Trusting Caddy's local CA in the OS store means keeping a CA key on disk that can mint a
  certificate for any domain.

If PA0 shows Safari rejects `Secure` cookies on `http://localhost`, there are two options. Either
the session cookie for the **local origin only** drops `Secure` (the traffic never leaves loopback,
and the cookie is host-only with no `Domain`), or the docs tell macOS users to use Chrome or Firefox
for the PC app. Bulwark picks.

**The first run happens entirely on the PC:**
1. The installer opens `http://localhost:47800/setup#t=<setup token>`.
2. The page exchanges the token on load for an in-memory value and calls `history.replaceState` to
   remove the fragment.
3. The token is single-use with a 10-minute TTL. That fixes Gadfly S5 for setup, recovery and
   pairing alike.
4. The owner creates the household and a passkey with RP id `localhost`. Password + TOTP is the
   offered alternative: Chrome on Linux has no platform authenticator, and a hybrid (phone)
   passkey works there.

The app is fully usable at that point, and Tailscale hasn't been mentioned.

**Why rule 12 still holds:**
- The only host port is loopback, and the Docker floor (§2) closes LAN reachability.
- Anything that can reach the port is already running code on the PC.
- The localhost site never serves the tailnet's pairing endpoint. Setup and recovery are served
  **only** on the local ingress: the API refuses `/setup` and `/recover` unless
  `X-Holdfast-Ingress: local`. So recovery requires presence at the PC.

### 4. Real client identity

- **Tailnet ingress.** The source of truth is the TCP peer that Serve accepted on the tailnet. The
  tunnel is WireGuard, authenticated by the sending node's key, so a peer can't present another
  peer's address.
  - Serve writes that address into a PROXY v2 header, ahead of any client byte. Client bytes arrive
    inside the TLS stream, after the header, and Caddy parses them as HTTP, never as PROXY.
  - Caddy's `proxy_protocol` wrapper on the tailnet listener rejects connections without the header
    (VERIFY option names, PA1: https://caddyserver.com/docs/caddyfile/options#listener-wrappers).
  - Only the unix socket (filesystem trust) or the two-member `edge` network can reach that
    listener. Every container drops all capabilities, so none can spoof a source address.
  - Caddy then overwrites `X-Forwarded-For`, and the API's resolver is unchanged.
- **Fallback if Serve's PROXY v2 can't be relied on** (this VERIFY now leads PA0):
  1. Serve in HTTPS-proxy mode, with the forwarded header Serve sets, *only if* PA0 shows Serve
     **overwrites** a client-supplied `X-Forwarded-For` instead of appending to it. The header is
     then trusted only from the sidecar.
  2. If Serve appends, use TLS-terminated TCP without PROXY. Tailnet clients then share one
     rate-limit bucket (today's problem, recorded as accepted risk), and audit rows carry no device
     IP. A per-account rate-limit scope already exists for password and TOTP.
  3. tsnet, under the conditions in §1.
- **Local ingress.** The peer is the Docker gateway or the userland proxy for every client. That's
  correct: there's one client, the PC.

### 5. More than one accepted RP id

**Model:**
- `instance_access` is a new instance-level table. It has no household data, so no `household_id`,
  and it joins the identity-table exemption in the RLS lint with that reason.
- It holds the accepted origins: always `http://localhost:47800` with RP id `localhost`, plus zero
  or more `https://<fqdn>` with RP id `<fqdn>`, each with `added_at` and `added_by`.
- It binds the tailnet identity (`CurrentTailnet` name and MagicDNS suffix) at first connect.
- `webauthn_credential` gains `rp_id`, which is NOT NULL and backfilled from `APP_HOST`.
- Manual mode (`APP_HOST` set in `.env`) keeps today's single-origin behaviour.

**Ceremony rules:**
- **Picking the RP id.** For options requests, the API picks the RP id from the ingress. Local
  ingress gives `localhost`. Tailnet ingress gives the accepted FQDN equal to `Host`. Any other
  `Host` gets `421 misdirected_request` (Gadfly minor), so invite and alert links are always built
  from an accepted origin.
- **Verifying.** `clientDataJSON.origin` must exactly equal (scheme, host and port) an accepted
  origin. `rpIdHash` must equal the hash of the RP id mapped to that origin. Assertions are checked
  only against credentials whose stored `rp_id` matches.
- **Who adds an origin.**
  - `localhost` is written at install.
  - A tailnet FQDN is added only when an **owner session with step-up** asks, and only with the
    value **read from the sidecar's own status** (`Self.DNSName`), never with a request header.
  - In the CLI fallback, `app.cli` writes it on the host.
- **Who removes an origin.** The owner, with step-up. Removing an origin disables its credentials
  but doesn't delete them, so renaming back revives them.

**Why this is safe:**
1. An authenticator binds each credential to exactly one RP id. Accepting several RP ids never
   makes a credential valid somewhere else. The phone passkey (FQDN) and the PC passkey
   (`localhost`) are separate credentials on one account.
2. The accepted set is server-side, written only by step-up or by the host. A forged `Host` or an
   attacker-run origin can't add itself.
3. The exact origin check (including the port) defeats a local phishing page on another
   `localhost` port. Its assertions carry its own origin and get rejected, even though the RP id
   `localhost` is shared by every localhost app.
4. Phishing resistance per credential is unchanged. Sessions are per origin (host-only cookies).

**What "survives a rename" honestly means.** When the tailnet or machine name changes, the phone's
old-FQDN credential *can't* move. That's WebAuthn's origin binding working as designed. Related
Origin Requests don't help, because the old name stops resolving. What survives is the **account**:
1. The owner signs in on the PC at `localhost`, which never changes.
2. The owner adds the new FQDN (step-up).
3. The owner re-pairs the phone: two phone steps, scan the QR and create a passkey.

The stale credential stays listed so a rename back revives it.

**Pairing the phone.** It starts from a signed-in PC session with step-up:
1. The PC mints a single-use, 10-minute pairing token and shows it as QR 2:
   `https://<fqdn>/pair#<token>`. The phone page does the exchange-and-`replaceState` dance.
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

### 6. Secrets and state

- **The sidecar.** Node state lives in the `tailscale_state` named volume (`TS_STATE_DIR`).
  - Mode `0700`, read-only root filesystem, `/tmp` on tmpfs, `no-new-privileges`, and every
    capability dropped that userspace mode allows. It runs as non-root if the image supports that
    in userspace (VERIFY, PA0 item 5). If not, it runs as root inside the container with no
    capabilities, which is recorded as accepted.
  - Other volumes: `phone-access` (api writes, sidecar reads) and the LocalAPI socket volume, shared
    read-only into `api` only if PA0 item 4 passes.
- **Never logged by Holdfast:** node and machine keys, the certificate key, the login URL, auth keys,
  and setup, pairing and recovery tokens.
  - Upstream `containerboot` prints the login URL to the container's stdout. That stays in
    `docker logs` on the host, where it's readable only with the trust anchor above. Holdfast never
    ships logs anywhere.
  - Tailscale log upload is off (`--no-logs-no-support`, VERIFY).
- **Backups exclude `tailscale_state`.** A node key in a backup is a liability, and restoring it next
  to a live node duplicates the identity. After a restore, the owner signs in at `localhost` (the
  app works at once), turns phone access back on and re-pairs. The new node may come up as
  `holdfast-1` or in a new tailnet, which is handled by the rules in §5.
- **Revocation.**
  - **Phone access:** Settings → Phone access → Turn off (step-up). That removes the flag, so the
    wrapper logs the node out and idles. The page also deep-links the admin console's machine page
    so the owner can remove the machine.
  - **A lost phone:** remove it in the admin console, then remove its passkey in Holdfast. Removing
    a credential already revokes the user's other sessions (ADR 0010).

### 7. Honest minimum step counts

**(a) Using it on the PC.** It's the same Holdfast on every OS. Only the Docker prerequisite
differs.

| | Linux | macOS | Windows |
|---|---|---|---|
| Docker | 0 if Engine is at the floor already. Otherwise 1 (Docker CE from Docker's repo) | 2: install Docker Desktop, open it and accept its terms | 2: install Docker Desktop (it may enable WSL2 and need a **reboot**), open it and accept its terms |
| Shell | 0 | 0 | 1: install PowerShell 7 (`winget install Microsoft.PowerShell`). This step goes away if rule 10 is amended to allow a 5.1 bootstrap (§10, Gadfly S1) |
| Holdfast | 2: run the one-line installer (the browser opens on setup), then create the household + passkey | 2 | 2 |
| **Total** | **2 to 3** | **4** | **5** (4 with the bootstrap) |

**(b) Adding a phone.** This runs in the app from a signed-in PC and is the same on every OS:
1. Settings → **Connect your phone** → step-up → **Turn on** (consent to contact Tailscale).
2. Sign in to or create a Tailscale account in the browser that opens, and approve `holdfast`.
3. **Turn on HTTPS** (and MagicDNS if it's off) on the admin page the wizard links. This is likely
   required on a new tailnet (VERIFY, PA0 item 7). The wizard shows the certificate-transparency
   disclosure first: the FQDN gets published.
4. Phone: scan QR 1, install Tailscale. Skip if it's already installed.
5. Phone: sign in with the same account and allow the VPN configuration.
6. Phone: scan QR 2 to open the pairing page.
7. PC: **Approve** the phone (compare the code).
8. Phone: create the passkey.
9. Turn off key expiry for `holdfast` (one click on the linked admin page). This counts as
   required: without it, phone access goes dark at day 180 (VERIFY).

That's **9 steps**, or 8 with Tailscale already on the phone. Add to Home Screen and always-on VPN
are optional extras. A typical first-time total is **Linux 11–12, macOS 13, Windows 14** (13 with
the bootstrap).

**(c) Each extra household member.**
- **PC only** (a shared computer): **2 steps.** The owner creates an invite, and the member opens
  it at `localhost` and creates a passkey.
- **Their own phone: 6 steps.**
  - The owner: **shares the `holdfast` machine** with the member's Tailscale account (1), and later
    sends the Holdfast invite (5).
  - The member: accepts the share with their own identity provider account (2), installs Tailscale
    on the phone (3), signs in and allows the VPN (4), and opens the invite on the phone and creates
    a passkey (6).

  Node sharing is the recommended route, not inviting the member into the tailnet. A shared-in user
  reaches only the Holdfast machine, not the owner's other devices. Whether shared-in users count
  toward the free plan's user cap needs checking (VERIFY, PA0 item 8:
  https://tailscale.com/kb/1084/sharing, https://tailscale.com/pricing).

### 8. Employer and shared tailnets

**Who can reach the app.** Every device the tailnet policy allows. On the default policy that's
every member's device (VERIFY: https://tailscale.com/kb/1192/acl-samples), plus shared-in users for
this machine. Passkeys are still required. Reaching the login page isn't access.

**What tailnet admins can do.** They can see the machine and its metadata, rename it (which forces a
re-pair), remove or expire it, and see flow logs on plans that have them (VERIFY). They **can't**
read app traffic: TLS terminates inside our container with a key that never leaves it. They **can't**
publish it by granting Funnel in policy, because our serve config has no `AllowFunnel` and the
container accepts no other serve config.

**The posture we recommend and enforce:**
- **A personal tailnet only.** Before step 2, the wizard says plainly: "Don't connect Holdfast to a
  work or school Tailscale network. Its admins can see and reach this machine."
- **A hard stop when the tailnet looks shared.** After sign-in, the API reads the tailnet name and
  the number of distinct users visible in status. If there's more than one user, or the tailnet name
  is an organization domain rather than a personal login, it requires a **typed confirmation** of
  the tailnet name. A non-technical user can't just click OK past it (Gadfly S6). In the CLI
  fallback, `holdfast phone` asks the same question.
- **The docs ship a policy snippet** that grants `holdfast:443` only to named users (VERIFY syntax:
  https://tailscale.com/kb/1337/policy-syntax), and they show how to remove the default `funnel`
  attribute (VERIFY: https://tailscale.com/kb/1223/funnel).
- **No peer enumeration in the UI.** The "count phones by OS" idea is dropped (Gadfly minor). The API
  reads `Self`, `CurrentTailnet` and the user count only.

### 9. Failure behaviour, vendor dependence, threat model

**Failure behaviour.**
- **The app on the PC never depends on Tailscale.** Phone access does. If Tailscale's control plane
  is down, existing phone sessions probably keep working from cached state. A restarted node may not
  come back until control returns (VERIFY, PA0 item 12). The docs say so plainly.
- **Key expiry:** banners at 30, 7 and 1 days, plus one-click re-auth from the PC.
- **MagicDNS or HTTPS off:** detected from status and deep-linked. **Manual: one click each.** The
  app can't flip tailnet settings without an admin API key, and it won't hold one.
- **Phone VPN off:** the PWA's cached shell shows "Can't reach Holdfast. Is Tailscale on?"

**Vendor trigger.** Lattice reopens this ADR if any of these happens:
- the free Personal plan drops below 3 users,
- it starts requiring payment,
- the terms forbid this use,
- the image or Serve's PROXY support goes away.

The localhost floor means a vendor change removes phone access, never the app. Headscale isn't
offered as a zero-config exit path: it needs a public coordination server and a domain. It stays a
manual option.

**Threat-model delta for Bulwark.**
- **New pieces:**
  - an upstream container (pinned digest, userspace, no published port)
  - egress from that container only, while phone access is on: control plane, DERP, STUN and peer
    UDP (VERIFY list: https://tailscale.com/kb/1082/firewall-ports)
  - a one-bit flag volume
  - possibly a read-only LocalAPI socket in `api`
  - the plain-HTTP loopback site
  - multiple RP ids
- **A compromised sidecar** can: read app traffic in plaintext and relay it, because TLS terminates
  there; enable Funnel from inside; forge PROXY headers to Caddy; join another tailnet. It **can't**
  reach `db` or `redis`, `.env`, the document volume or the Docker socket.
- **A compromised `api` with the read-only socket** additionally learns the tailnet's device
  inventory. It's already the most privileged component, so this is a privacy delta, not a control
  delta, provided the read-only check holds. That's why it's a PA0 gate.

## Alternatives considered

| Option | Verdict |
|---|---|
| **Stock sidecar, userspace, TLS-terminated TCP + PROXY v2 (chosen)** | Gives the real client IP, native certificate renewal and no TUN device, with upstream's security releases arriving as a digest bump. PA0 gates it |
| Custom Go/tsnet `access` service (revision 1) | Its Funnel argument protected only against our own misconfiguration (the CI test covers that). It moves every Tailscale security fix onto our release train, and the roster has no Go reviewer. Kept as fallback 3 (§4) |
| Stock sidecar, kernel mode, Caddy sharing its netns | Needs `NET_ADMIN` and `/dev/net/tun`, breaks rootless Docker, and a restart of the namespace owner strands Caddy. Rejected |
| Serve HTTPS proxy + `X-Forwarded-For` | Fallback 1, and only if Serve overwrites the header (PA0 item 3) |
| Caddy's built-in ts.net cert getter (socket into Caddy), or the `caddy-tailscale` plugin | Puts node control in the HTTP parser. Rejected |
| Host Tailscale + Serve, automated by the installer | Depends on the host's version and rights and on any serve config the user already has. **Kept as the documented manual path**, using the same Caddy PROXY listener published on loopback (deferred PA-D9) |
| Single pinned RP id + recovery token (revision 1) | Ordinary events become lockouts that only a terminal can fix (Gadfly S3). Rejected for multiple RP ids |
| https://localhost with a local CA | A browser warning, or a CA key on disk that can mint any certificate. Rejected for plain HTTP on loopback |
| Headscale / self-run coordination | Needs a public endpoint and a domain. Manual option only |
| Cloudflare Tunnel + Access | Public hostname, and Cloudflare sees plaintext. Rejected (rule 12) |
| Relay run by Holdfast | Costs money, gives us metadata and creates legal exposure. Holdfast Cloud only (ADR 0009) |
| WireGuard direct | Needs a router port-forward. Rejected (rule 12) |
| LAN-only mDNS + local CA | No access on LTE, and a CA-trust ordeal on the phone. Rejected |
| Funnel | Public. Forbidden by the serve-config test |

## Consequences

- **Easier:**
  - the app works on the PC two steps after Docker
  - the phone is an optional nine-step flow inside the app
  - a vendor change can't take the app down
  - client IPs are correct on the tailnet path
  - rule 12 gets mechanical checks for the default install
- **Harder:**
  - two ingresses and a per-request RP id in auth code
  - an instance-level table and `rp_id` (migration token)
  - Safari's `Secure` cookie handling on localhost may force a per-origin cookie rule
  - the Docker floor adds a step on stock Linux distros
  - macOS and Windows rows have no CI. GitHub's macOS runners have no Docker, and its Windows
    runners run only Windows containers. Those rows rest on owner hardware (Gadfly F5, owner queue)
  - the frontend subnet that's already pinned in `docker-compose.yml` is an existing collision risk,
    unpinned in deferred PA-D10
- **Docs:** PLAN 12.1 and Phase 8.5 change in the next Section 20 amendment (Beacon).
  `client-ip-check.md` gains a tailnet row.

## Owner queue (money, accounts, legal)

1. **A test Tailscale account and tailnet for PA0**, separate from the personal tailnet. The owner
   either signs the test nodes in interactively or puts a one-off auth key in a file outside the
   repo. This is a **prerequisite for PA0**, not just for final acceptance.
2. **Owner devices for PA0 items 7 and 10 and for deferred acceptance:** a macOS host (Safari and
   Chrome), a Windows host, one iPhone and one Android phone.
3. **Steering users to Tailscale's free plan** for the optional phone path. Each household's own
   account and terms. Holdfast holds no account and no API key.
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
   recommendation.
3. Local port `47800`: check for clashes with common self-host apps.
4. Default machine name `holdfast` (disclosed in CT logs, changeable at install) versus a neutral
   name.

## 10. Response to Gadfly (`docs/adr/reviews/0021-gadfly.md`)

| Item | Response |
|---|---|
| F1 the manual path removes the reason for tsnet | **Accepted.** The stock sidecar with the serve config is the decision, and the decisive VERIFY leads PA0 |
| F2 the Funnel argument | **Accepted.** A CI serve-config test replaces "impossible in code". Also, a Funnel grant in tailnet policy can't publish anything without `AllowFunnel` in our config |
| F3 Go cost has no owner | **Accepted.** If tsnet ever returns as fallback 3, its conditions are Gadfly's |
| F4 the size displaces Phase 5 | **Accepted.** Phase 8.5. Only PA0 (S) and PA1 (M) are specified. The rest is deferred |
| F5 owner-dependent acceptance, no CI for Docker Desktop | **Accepted.** In Consequences and the owner queue |
| S1 step counts / households | **Mostly accepted:** key expiry is counted, Add to Home Screen is optional, per-member costs are counted. **Two partial rejections.** (1) The **Turn on** click in the phone flow stays. It's the consent to start talking to a third party, not decoration. (2) The pwsh 7 step stays counted until rule 10 is amended through the digest. A 5.1 bootstrap is a fair product call, but it changes a hard rule, and agents don't change hard rules by ADR |
| S2 localhost first | **Accepted** (§3). `http://localhost`, not a local CA |
| S3 pinned RP id lockouts | **Accepted** (§5). Multiple RP ids, with localhost as the floor that never changes |
| S4 reconnect into another tailnet | **Accepted.** Tailnet identity is bound, adopting another one needs a typed confirmation, and recovery never crosses tailnets |
| S5 token in the fragment / history | **Accepted** for every token: exchange on load, `replaceState`, single use, 10 minutes. QR 2 also needs PC approval |
| S6 employer / shared tailnets | **Accepted** (§8), with a typed confirmation and no peer enumeration |
| S7 pinned subnets | **Accepted.** A unix socket if Serve supports it, otherwise an unpinned `edge` network with the accepted risk recorded. The existing frontend pin is deferred |
| S8 Docker floor | **Accepted.** Counted in the Linux row |
| S9 outage / "exit path" | **Accepted.** Stated honestly. Headscale isn't called a zero-config exit. The vendor trigger is named |
| S1 (story) PROXY fix on the owner's host | **Partly rejected.** It isn't scheduled ahead of Phase 8.5, because Keel's schedule ruling wins and the problem affects one install's audit rows, not security. PA1 builds the same Caddy PROXY listener, so the host-Serve variant (deferred PA-D9) is small once PA1 lands |
| Minors (Host 421, no-CORS test, drop peer-by-OS, rule 12 via the digest) | **All accepted** |
| Rank 1–4 VERIFYs before acceptance | **Accepted.** PA0 is a precondition for status Accepted |

## 11. Stories

Schedule: Phase 8.5 (Q2, `ROADMAP.md`). Nothing here displaces Phase 5. PA0 has no product code,
so it may run as a preemptible stretch-lane item once owner-queue items 1 and 2 are available. Keel
decides. The IDs use `PA` because `A1` and `S1` already exist in the roadmap.

### PA0: verification spike (S, Harbor + Sounding, about 1 run, no product code)

The spike runs on a throwaway tailnet, using stock containers on a dev machine. A second userspace
Tailscale container can act as a tailnet client, so most items need no phone. The results go into
this ADR as pass/fail with the docs and version checked. They never include tailnet names or IPs
(ADR 0019 style reporting).

| # | Check | Pass means | Needs owner's tailnet / devices |
|---|---|---|---|
| 1 | `TS_SERVE_CONFIG` TCP `443` with `TerminateTLS` + `TCPForward` + `ProxyProtocol: 2` in the stock image. Exact field names and the minimum version. A Caddy test listener logs the PROXY source | Header present, source = the client node's tailnet IP | **Test tailnet** (owner queue 1) |
| 2 | Same as item 1 with `TCPForward` to a `unix:` socket | Works, or documented as unsupported (then the `edge` network) | Test tailnet |
| 3 | HTTPS-proxy mode: the client sends a forged `X-Forwarded-For` | Know whether Serve overwrites or appends (fallback 1 valid only if it overwrites) | Test tailnet |
| 4 | LocalAPI socket shared into a UID-1000 container: `status --json` works (AuthURL, Self.DNSName, CurrentTailnet, CertDomains, KeyExpiry, users); `up`, `login`, `logout`, `cert`, `serve`, `funnel` and `set` all denied. Repeat under rootless Docker | All writes denied in both modes. Otherwise the socket is never shared and the CLI fallback is used | Local only (status needs control contact, no account) |
| 5 | Wrapper entrypoint on the stock image: flag gating, `logout` on removal, `--no-logs-no-support` honoured (watch egress hosts), cap-drop set and non-root feasibility in userspace | No control contact before the flag, no log upload | Local + test tailnet |
| 6 | Name stability: forced key expiry, then re-auth keeps the name and IP. Lost state with the old node still present gives a suffix. Old node removed, then reconnect reclaims the name | Behaviour recorded (multiple RP ids make the outcomes recoverable, not fatal) | **Test tailnet admin console** |
| 7 | HTTPS certificates and MagicDNS defaults on a **fresh** tailnet. Whether a consent link (Serve's enable URL / `QueryFeature`) exists and works from the container | Step 3 of (b) confirmed as one click, or the count corrected | **Fresh test account** |
| 8 | Free Personal plan: user and device caps, whether shared-in users count, terms on this use. Read only: https://tailscale.com/pricing, https://tailscale.com/kb/1084/sharing | Caps recorded, vendor trigger not already met | None |
| 9 | tsnet not needed: follows from items 1, 3 and 4 | Items 1 and 4 pass, so tsnet is off the table | None |
| 10 | `http://localhost:<port>`: WebAuthn create and get plus a `Secure` session cookie in Chrome and Firefox (Linux; virtual authenticator acceptable), Safari and Chrome (macOS), Edge (Windows) | Works, or the Safari fallback is chosen (open question 2) | **Owner macOS + Windows hosts** |
| 11 | Docker floor: the release that fixes 127.0.0.1-publish LAN reachability, and the versions in Debian 12/13, Ubuntu 24.04, Fedora 43 and current Docker Desktop | Floor and Linux step count confirmed | None (docs + local) |
| 12 | Restart with control-plane hosts blocked: does the node serve peers from cached state | Outage wording confirmed | Test tailnet |

ADR 0021 moves to Accepted only after items 1, 4, 6, 7, 8 and 10 are resolved. Item 2 changes the
transport, items 3 and 12 change fallbacks and wording, and item 11 changes a count.

### PA1: smallest Linux slice (M, Harbor; Bulwark reviews)

- Scope:
  - `infra/docker-compose.access.yml` with the stock sidecar (pinned digest, userspace, read-only
    serve config, flag-gated wrapper)
  - Caddy's tailnet site (PROXY v2 listener on the transport PA0 picked, ingress header, XFF
    overwrite)
  - `scripts/check-serve-config.py`
  - the `check-compose-ports.py` extensions, plus `make up-access`
  - `APP_HOST` still set by hand from `tailscale status`, so there's a single RP id until PA-D1
    ships
- Acceptance evidence:
  - CI: the serve-config test rejects fixtures containing `AllowFunnel`, `Web` or a second port
  - `make ports` is green
  - on a Linux host, a phone on the test tailnet registers a passkey at the FQDN, and `audit_log.ip`
    is the phone's tailnet address (Sounding reports counts only)
  - a device off the tailnet can't resolve the name or connect

### Deferred to Phase 8.5 (rough sizes, not yet specified)

| ID | Story | Size |
|---|---|---|
| PA-D1 | Multiple RP ids: `instance_access`, `webauthn_credential.rp_id`, per-ingress RP selection, exact origin checks, Host 421, no-CORS test (Tide, migration token, Bulwark) | M |
| PA-D2 | Localhost-first default stack: Caddy local site on `127.0.0.1:47800`, setup on local ingress only, token exchange + `replaceState`, Safari cookie rule (Harbor + Tide) | S |
| PA-D3 | In-app "Connect your phone": flag write, read-only status client (or CLI fallback), tailnet binding + typed confirmations, shared-tailnet warning, pairing QR + PC approval (Tide + Prism) | M |
| PA-D4 | `holdfast recover` (local ingress only) (Tide, Bulwark) | S |
| PA-D5 | Settings → Phone access, expiry banners, PWA offline hint (Prism) | S |
| PA-D6 | Installer `install.sh` / `install.ps1`, `holdfast` wrapper, Docker floor, digest-pinned compose (Harbor) | M |
| PA-D7 | Docs: per-OS guide with the §7 counts, node sharing for members, ACL snippet, Funnel attribute removal, backup/restore, revocation, manual paths (Beacon) | S |
| PA-D8 | Real-device acceptance on Linux, macOS and Windows hosts, iOS and Android (Sounding; owner devices) | M |
| PA-D9 | Host-Serve manual path: loopback-published PROXY listener + documented `tailscale serve` command (fixes the owner's host) (Harbor) | S |
| PA-D10 | Unpin the `frontend` subnet and rework the `TRUSTED_PROXIES` default (Harbor, Bulwark) | S |

Deferred total: about 4 M + 6 S, plus PA0 (S) and PA1 (M).

## VERIFY index (beyond PA0)

| Claim | Where to check |
|---|---|
| Tailscale client licence BSD-3-Clause (upstream image, AGPL-compatible) | https://github.com/tailscale/tailscale/blob/main/LICENSE |
| Default policy is allow-all among members; flow logs by plan | https://tailscale.com/kb/1192/acl-samples, https://tailscale.com/pricing |
| Default `funnel` node attribute in new tailnets | https://tailscale.com/kb/1223/funnel |
| Key expiry 180 days by default | https://tailscale.com/kb/1028/key-expiry |
| CT publication of the FQDN when HTTPS is on | https://tailscale.com/kb/1153/enabling-https |
| Policy grant syntax for the docs snippet | https://tailscale.com/kb/1337/policy-syntax |
| Firewall and egress list | https://tailscale.com/kb/1082/firewall-ports |
| Caddy `proxy_protocol` wrapper options | https://caddyserver.com/docs/caddyfile/options#listener-wrappers |
| Docker Desktop terms | https://www.docker.com/legal/docker-subscription-service-agreement/ |
