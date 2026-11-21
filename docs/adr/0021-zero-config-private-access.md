# 0021. Zero-config private access: a bundled tsnet `access` service and a phone onboarding wizard

- Status: Proposed
- Date: 2026-11-21
- Deciders: Lattice (architect), Keel; owner asked for it (paraphrased: people who download Holdfast
  shouldn't have to understand reverse proxies or tunnels). Bulwark and Gadfly review before the
  first build. Owner-queue items are listed at the end.

## Context

PLAN 12.1 and Phase 8.5 describe private access for someone who already knows networking: Tailscale
on the host, an owned domain, a Cloudflare DNS-01 token, Caddy bound to the Tailscale IP, ufw rules
and tailnet ACLs. `infra/docker-compose.prod.yml` implements that path. Most of the people PLAN 20.1
calls the beachhead can follow it, but a household that only wants "Holdfast on my PC, open it on my
phone" can't.

The owner's machine works today because of a manual setup: Tailscale on the host, `tailscale serve`
(tailnet only, real `*.ts.net` certificate) in front of Caddy on `127.0.0.1:443`, `APP_HOST` set to
the ts.net name, `default_sni` in the dev Caddyfile, and a passkey registered on the phone against
that name. It has one known problem. Serve terminates TLS and reconnects to Caddy from loopback, so
Caddy sees `127.0.0.1` as the peer for every client. Per-IP rate limits and audit IPs collapse into
one bucket (`docs/ops/client-ip-check.md`, ADR 0010).

Constraints that shape the design:
- **Hard rule 12:** no public port, no port-forward. Caddy binds to the Tailscale IP only.
- **Hard rule 9:** a new external service needs an ADR. This ADR covers Tailscale's coordination,
  DERP relay and certificate services as a dependency of the default install.
- **Hard rule 13:** no personal assumptions. The owner's tailnet name is personal data. It never
  appears in code, fixtures or docs. Examples use `holdfast.tail0000.ts.net`.
- **ADR 0013:** no paid plan or service for the project. Users bring their own free accounts, as
  they already do for Plaid and AI (PLAN 17.1).
- **ADR 0009:** a relay run by Holdfast belongs to Holdfast Cloud, not to self-host.
- **ADR 0010:** the WebAuthn RP id is `APP_HOST` and the expected origin is `https://APP_HOST`. A
  passkey is bound to its RP id when it's registered.
- **ADR 0018:** the supported install is compose with images from GHCR. PLAN 17.6 asks for a
  one-command installer. No native launcher exists yet.
- **Rule 10:** Windows scripts target PowerShell 7.

Claims about Tailscale behaviour, limits, pricing and licences are marked **VERIFY** with the page to
check. They aren't stated as fact. Tailscale changes these often. Any VERIFY item that turns out
false reopens the parts of this ADR that depend on it (the list at the end shows which).

## Decision

### 1. Mechanism: a small Holdfast `access` service built on tsnet

The default install gains one container, **`access`**. It is a Holdfast-built Go binary that embeds
Tailscale's **tsnet** library (userspace networking, so no TUN device, no `NET_ADMIN` and no host
Tailscale). It's the only component that touches the tailnet, and it does five things:

1. **Joins the tailnet** as the machine `holdfast`, which the installer can change. With no auth
   key, this uses Tailscale's interactive login URL. The service surfaces the URL to the console and
   never logs it.
2. **Listens on the tailnet at `:443` only**, through `tsnet.Server.ListenTLS`. TLS uses the node's
   own `*.ts.net` certificate, which tsnet fetches and renews (VERIFY:
   https://pkg.go.dev/tailscale.com/tsnet). The only ALPN is `http/1.1`.
3. **Forwards each decrypted connection to Caddy** over a dedicated internal network (`edge`). Each
   connection starts with a **PROXY protocol v2** header that carries the tailnet peer address from
   `conn.RemoteAddr()`. Caddy keeps every header, CSP, body cap and route it has today
   (`Caddyfile.access`, which is derived from `Caddyfile.prod`).
4. **Reports status** on a control endpoint that only the API can reach (§5). Status covers:
   backend state, FQDN, MagicDNS and HTTPS availability, key expiry, the login URL while one is
   pending, the signed-in account's login name, and a count of online peers by OS (no peer names).
5. **Logs out** when the owner disconnects (step-up required).

It has no code path to Funnel, `ServeConfig`, exit nodes, subnet routes, SSH or the web client. A
source test fails the build if any of those identifiers appear (`ListenFunnel`, `SetServeConfig`,
`RunWebClient`, `AdvertiseRoutes`, `ExitNode`). The never-public invariant lives in one place: this
binary.

```
phone (Wi-Fi/LTE) ──WireGuard (Tailscale)──▶ access  (tsnet, :443 on tailnet, ts.net cert)
                                               │  plaintext over `edge` (internal), PROXY v2
                                               ▼
                                             caddy  (bind <edge ip>:8080, proxy_protocol allow edge/29)
                                               │  X-Forwarded-For {remote_host}  (overwrite)
                                               ▼
                                             api / web  (frontend network, unchanged)

PC browser ──▶ 127.0.0.1:47800 ──▶ caddy console site (setup + connect only, token-gated)
```

**Why tsnet and not the stock Tailscale image plus `tailscale serve` (Keel's starting position):**
the sidecar idea stands, but the stock image puts a full `tailscaled`, its CLI and the LocalAPI in
the container. Anything that can reach the LocalAPI can turn on Funnel. With tsnet, one process holds
the node key and exposes only the verbs listed above. It also gets the real peer address natively,
renews certificates natively (no copying cert files into Caddy) and reads the login URL from the
in-process client instead of parsing logs. The comparison is in *Alternatives*.

**Kernel versus userspace:** userspace. Inbound connections are all the service needs. Userspace
runs unchanged on Docker Desktop for Windows and macOS, on rootless Docker, on Podman and on NAS
boxes. Kernel mode needs `/dev/net/tun` plus `NET_ADMIN`, and it breaks under rootless Docker.

**Telemetry:** tsnet uploads logs to Tailscale's log service by default. `access` disables that with
`TS_NO_LOGS_NO_SUPPORT=true` or the tsnet equivalent (VERIFY:
https://tailscale.com/kb/1011/log-mesh-traffic). This follows the "no telemetry by default" rule
in `.claude/rules/security.md`.

**Exit path if Tailscale's terms change:** tsnet accepts a `ControlURL`, so `ACCESS_CONTROL_URL`
can point the same binary at a self-run Headscale. That path has no `*.ts.net` certificates, so it
pairs with the manual domain + DNS-01 path. The manual paths (PLAN 12.1, and host Tailscale + Serve)
stay supported and documented.

### 2. Rule 12, restated so it stays true and testable

Under this design Caddy no longer binds a Tailscale IP. In the default stack Caddy binds an internal
Docker network, and the tailnet listener lives in `access`. The rule's intent (nothing reachable
from the public internet) holds. Its letter has to change. Proposed wording for CLAUDE.md, for
Keel to apply when this ADR is accepted (flagged in the weekly digest so the owner can veto it):

> 12. **Networking:** Holdfast is reachable only over the owner's private network, never from the
> public internet. The default stack publishes one host port, the setup console on `127.0.0.1`,
> and takes tailnet traffic only through the `access` service, which never opens Funnel or any
> public listener. The manual path binds Caddy to the Tailscale IP only. Never add a public port,
> port-forward, Funnel or public tunnel. `make ports` checks the compose side. The service worker
> caches the app shell only, never API responses.

The mechanical checks, all in `make ports` / CI:
- `scripts/check-compose-ports.py --access`: only `caddy` publishes. The host IP is `127.0.0.1`
  and the port is the console port. `access` publishes nothing. `access` is on exactly
  `{access_egress, edge, accessctl}` and `caddy` on `{frontend, edge}`. `edge` and `accessctl` are
  `internal: true`, and `api` is not on `edge`.
- `--prod` (manual path) gains a range check: `TAILSCALE_BIND_IP` must be in `100.64.0.0/10` or
  `fd7a:115c:a1e0::/48`, not just "not a wildcard".
- `go test ./...` in `infra/access` includes the forbidden-identifier source test from §1.
- Runtime acceptance (Sounding, once per release on a real tailnet): from a device that isn't on
  the tailnet, the FQDN doesn't resolve, and a connection to the host's LAN and public addresses on
  443 and 47800 is refused.

### 3. Hostname lifecycle

- **The bound host.** A new single-row, instance-level table `instance_access` stores `bound_host`,
  `bound_at`, `mode` (`access` | `manual`) and `previous_host`. It holds no household data, so it
  has no `household_id`. It joins the identity-table exemption list in the RLS lint (ADR 0008/0010
  precedent), with that reason recorded. `webauthn_credential` gains `rp_id` (NOT NULL, backfilled
  from `APP_HOST`).
- **Precedence.** If `APP_HOST` is set in `.env`, the instance is in manual mode, and that value is
  the RP id, as today. If it's unset, the instance is in access mode, and the RP id is `bound_host`.
  The API resolves it per request (`app/services/access.py`) instead of once at startup.
- **First run, before a name exists.** There's no RP id, so passkey and TOTP registration and
  `POST /setup` answer `409 access_not_ready`. Only the loopback console works. When `access`
  reports a name and a working certificate, the console shows the phone QR. The first successful
  owner passkey registration at that origin writes `bound_host` in the same transaction.
  `bound_host` is never written from a request's `Host` header. It's written only from the `access`
  status the API fetched itself, and only when the two match.
- **The name changes** (machine renamed, tailnet renamed, or a collision suffix such as
  `holdfast-1`; VERIFY: https://tailscale.com/kb/1098/machine-names,
  https://tailscale.com/kb/1217/tailnet-name). The API never re-binds by itself, because silently
  following the name would hand the RP id to whatever the sidecar reports. When the current FQDN
  differs from `bound_host`, the console and the API's health show `host_mismatch`, and the app at
  the new name shows "Holdfast moved" with the recovery options. The old name no longer resolves.
  Recovery, cheapest first:
  1. Rename the machine back in the Tailscale admin console. Nothing else changes.
  2. If the owner has TOTP, sign in at the new name with password + TOTP (TOTP isn't bound to an RP
     id) and run **Rebind**, which requires step-up.
  3. Otherwise use **host recovery** (below).
  A rebind sets `previous_host`, makes the new name `bound_host`, marks credentials with the old
  `rp_id` stale (shown, not deleted, so the owner can rename back), revokes every session, writes
  an audit entry and sends an alert on every configured channel.
- **Locked-out owner.** The existing setup token isn't enough. It's minted only while setup is
  pending (`app/services/setup.py`), and a locked-out owner's instance is already complete. The fix
  is a new **recovery token**:
  - Only `holdfast recover` on the host can mint it. That command runs
    `docker compose exec api python -m app.cli recover`.
  - It prints to the CLI's own stdout, never to the API log.
  - It's 32 random bytes. Redis stores only its sha256, with a 15-minute TTL and GETDEL single use.
  - It authorizes one thing: registering one passkey for the named owner at the current bound host,
    or rebinding first when there's a mismatch.
  - Using it revokes every session and sends an alert.

  This doesn't weaken auth. It has the same trust anchor as the setup token: control of the host's
  Docker daemon, which can already read the database. It's never reachable over the network
  without a token minted on the host.

### 4. Real client identity behind the proxy

The source of truth is the **TCP peer address that `access` sees on its tailnet listener**. That
address arrives through a WireGuard session authenticated by the sending node's key, and the data
plane drops packets whose source isn't in that peer's allowed IPs. A tailnet device can't claim
another device's address. A subnet router can only present addresses it's authorized to route.

- `access` writes the address into a PROXY v2 header. The header is the first bytes it sends on its
  own connection to Caddy, before any client byte. Anything a client sends arrives later, inside the
  TLS stream, and Caddy reads it as HTTP, never as PROXY.
- Caddy's `proxy_protocol` listener wrapper accepts the header only from `edge` (a `/29` whose only
  members are `access` and `caddy`, with pinned addresses) and rejects connections without it
  (`fallback_policy reject`; VERIFY the option names:
  https://caddyserver.com/docs/caddyfile/options#listener-wrappers). Every container drops all
  capabilities, so none has `NET_RAW` or `NET_ADMIN` to forge a source address on a bridge.
- Caddy then overwrites `X-Forwarded-For` with `{remote_host}`, as today. The API keeps
  `TRUSTED_PROXIES` unchanged (loopback plus the frontend subnet) and the same right-most resolver.
- The loopback console site gets no PROXY header and is a separate bind. Its peer is the Docker
  gateway, which is fine for a token-gated, host-only console.

This closes the collapsed-IP problem. `docs/ops/client-ip-check.md` gets an access-mode row: the
expected value is the phone's `100.x.y.z`. A later enhancement (not in scope) is to add the peer's
WhoIs device name as a PROXY v2 TLV for nicer audit lines.

For the manual host-Serve path, the docs replace Serve's HTTPS-terminating proxy with
`tailscale serve --tls-terminated-tcp 443 --proxy-protocol 2 tcp://127.0.0.1:<loopback port>`,
which points at a loopback-published Caddy PROXY listener. Both flags exist in client 1.104, but
check their semantics and the minimum version (VERIFY: https://tailscale.com/kb/1242/tailscale-serve).
That fixes the owner's current setup without moving to the sidecar.

### 5. Secrets and state

- **Node state:** node key, machine key and the certificate cache live in the `access_state` named
  volume, mounted at `/var/lib/holdfast-access`.
  - The container runs as a fixed non-root UID on a distroless static image.
  - The directory is mode `0700` and its files `0600`.
  - The container has a read-only root filesystem, `/tmp` on tmpfs, `cap_drop: ALL` and
    `no-new-privileges`.
- **Control channel:** `access` listens on its pinned `accessctl` address only. `accessctl` is an
  internal network whose only members are `api` and `access`. Every request needs the bearer token
  `ACCESS_CONTROL_TOKEN`:
  - `make keys` or the installer generates it. Under ADR 0015 it's a secret, passed only to `api`
    and `access`.
  - The endpoints are `GET /v1/status`, `POST /v1/login` and `POST /v1/logout`. Nothing else
    exists.
  - `access` never calls the API.
- **Egress:** `access` reaches the internet only through its own non-internal network,
  `access_egress`, and no other service joins it.
- **Never logged anywhere** (`access`, API or Caddy), each with a redaction test:
  - node and machine keys
  - the certificate private key
  - the interactive login URL, because whoever opens it first can claim the node for their own
    tailnet
  - auth keys, if a user supplies one
  - `ACCESS_CONTROL_TOKEN`
  - console, setup and recovery tokens (the existing deliberate setup-token WARNING line stays for
    manual installs)

  tsnet's `Logf`/`UserLogf` go through a filter that drops URL-bearing lines.
- **Backups exclude `access_state`.** Node keys in a backup are a liability. Restoring a node
  identity while the old machine still runs produces two nodes with one key. Restore on a new host
  goes like this: remove the old machine in the admin console, run `holdfast open`, reconnect, and
  name the new node the same (`holdfast`). The FQDN, and with it the RP id, comes back unchanged, so
  the passkeys keep working. If the name comes back with a suffix, the §3 recovery order applies.
- **Revocation:**
  - **The server:** Settings → Access → Disconnect (owner, step-up), which logs out and wipes
    `access_state`. Or remove the machine in the Tailscale admin console. Either takes the node off
    the tailnet.
  - **A lost phone:** remove it in the admin console, then remove its passkey in Holdfast. Removing
    a credential already revokes the other sessions (ADR 0010). The passkey stays the second
    factor, so a phone that's still on the tailnet isn't enough to get in.

### 6. First run, end to end

**Installer** (ADR 0018 packaging; Harbor). One command:
`curl -fsSL <GitHub Release asset URL>/install.sh | sh` on Linux and macOS, or the pwsh 7
`install.ps1` on Windows. The scripts are release assets, so no new hosting service is involved.
The installer:
1. checks for Docker and Compose and names the fix if either is missing (it doesn't install Docker
   silently);
2. creates the install directory;
3. writes `.env` with fresh secrets (the `make keys` logic) and leaves `APP_HOST` blank (access mode);
4. writes a digest-pinned `compose.yaml`;
5. pulls multi-arch images (`holdfast-api`, `-web`, `-caddy`, **`-access`**);
6. runs `up` and then `migrate`;
7. installs a `holdfast` wrapper (`open`, `status`, `recover`, `update`, `stop`, `uninstall`);
8. runs `holdfast open`.

`holdfast open` mints a console token through `app.cli`: one hour, host-only, held in the URL
fragment and never in a cookie. It mints a setup token too if setup is pending. Then it opens the
browser at `http://127.0.0.1:47800/console#…`.

**In-app console wizard** (Prism + Tide). It's served only on the loopback site and authorizes every
call with the console token in an `Authorization` header (no CSRF surface). Caddy answers only
`Host: 127.0.0.1:47800` and `localhost:47800` there (DNS-rebinding guard). The wizard steps:
1. Connect Tailscale.
2. Confirm the signed-in account, which catches a login URL that someone else used.
3. Fix tailnet settings, with deep links when they're needed.
4. Phone QR 1: get Tailscale. It's `https://tailscale.com/download`, so no Holdfast-hosted page.
5. Auto-advance when `access` reports a new online iOS or Android peer, or the user clicks "My
   phone is connected".
6. Phone QR 2: `https://<fqdn>/setup#<setup token>`.
7. Done: install the app, turn on always-on VPN, optionally add this PC.

A single QR can't do Keel's whole "install Tailscale → open Holdfast" flow. The ts.net URL doesn't
resolve on the phone until it's on the tailnet, so the wizard uses two QRs.

**User steps.** "Required" means the user has to do it. Conditional steps depend on the tailnet's
state.

| # | Linux | macOS | Windows |
|---|---|---|---|
| P | (if missing) install Docker Engine + Compose plugin | P1 install Docker Desktop; P2 open it, accept its terms, wait for "running" | P1 install Docker Desktop (it may enable WSL2 and need a **reboot**); P2 open it, accept its terms; P3 install PowerShell 7 (`winget install Microsoft.PowerShell`) |
| 1 | Paste the one-line installer into a terminal | same (Terminal) | same (pwsh 7) |
| 2 | Browser opens the console: click **Connect Tailscale** | same | same |
| 3 | Sign in to or create a Tailscale account and approve the device | same | same |
| 4 | Click **Turn on HTTPS** (and MagicDNS if it's off) and confirm in the Tailscale admin page: *likely required on a new tailnet* (VERIFY) | same | same |
| 5 | Phone: scan QR 1, install Tailscale | same | same |
| 6 | Phone: open Tailscale, sign in with the same account, allow the VPN configuration | same | same |
| 7 | Phone: scan QR 2, Holdfast setup opens | same | same |
| 8 | Phone: household name + email, then **Create passkey** (Face ID / fingerprint) | same | same |
| 9 | Phone: Add to Home Screen (iOS: Share menu) or Install (Android) | same | same |
| rec. | Console: **Turn off key expiry** for `holdfast` (one click in the admin page) | same | same |

**Counts.** These assume step 4 is required, and P counts only when it applies.

| OS | Prerequisites | Required steps | Total | With the recommended key-expiry step |
|---|---|---|---|---|
| Linux, Docker already installed | 0 | 9 | 9 | 10 |
| Linux, Docker missing | 1 | 9 | 10 | 11 |
| macOS | 2 | 9 | 11 | 12 |
| Windows | 3, plus a possible reboot | 9 | 12 | 13 |

Using Holdfast on the PC itself takes two more steps: install Tailscale on the PC and sign in. The
PC then uses the same name and passkey (or a second passkey after step-up). That step is optional
because the phone is the goal.

**Where users get stuck:**
- Docker Desktop: virtualization is off in the BIOS, WSL2 fails to install, or the licence prompt
  confuses them.
- pwsh 7 on Windows. Rule 10 adds this step (open question for Keel below).
- Creating a Tailscale account, which needs an identity provider account (VERIFY:
  https://tailscale.com/kb/1013/sso-providers).
- The HTTPS toggle in the admin page, and its certificate-transparency warning.
- The phone's VPN permission prompt.
- Scanning QR 2 before the phone is on the tailnet. The browser shows a DNS error, not a Holdfast
  page, which is why the wizard waits on auto-advance.
- iOS Add to Home Screen is hidden in the Share menu.
- Existing tailnets: device approval or Tailnet Lock leaves the node waiting. The console shows the
  state and a deep link (VERIFY: https://tailscale.com/kb/1099/device-approval,
  https://tailscale.com/kb/1226/tailnet-lock). Custom ACLs that block phone → `holdfast:443` can't
  be detected from the server. The docs show the one ACL line to add.

### 7. Failure and offline behaviour

| Situation | What happens | What Holdfast does |
|---|---|---|
| Tailscale control plane down | Existing peers usually keep talking with their cached network map. New logins, new devices and certificate renewal fail (VERIFY). | `access` keeps serving with its cached cert. Status shows `control_unreachable`. The console and Settings warn when the cert has fewer than 14 days left. |
| `access` container down | The phone can't connect. The data is untouched. | `restart: unless-stopped`, plus a healthcheck. `holdfast status` shows it. The PWA's cached shell detects the failed `/api` calls and shows "Can't reach your Holdfast. Is Tailscale on, on both devices?" |
| Node key expiry (default 180 days; VERIFY: https://tailscale.com/kb/1028/key-expiry) | The node drops off the tailnet until it re-authenticates. Whether the same IP and name survive needs checking (VERIFY, same page). | Banners at 30, 7 and 1 days, using the key expiry from status. The console deep-links "Turn off key expiry". After expiry, `holdfast open` → **Reconnect** (about 3 steps). This can't be automated without a Tailscale API token, which Holdfast deliberately doesn't ask for. Tagged nodes have no expiry, but tags need policy edits (VERIFY). |
| Phone not signed in, or VPN off | The FQDN doesn't resolve on the phone. | The shell hint above. The docs recommend iOS VPN On Demand and Android Always-on VPN. |
| MagicDNS off | The name doesn't resolve for any peer. | Detected from status. Console deep link to the admin DNS page. **Manual: one click by the user.** |
| HTTPS certificates off | `ListenTLS` handshakes fail, so there's no cert. | Detected from status (no cert domains). Console shows **Turn on HTTPS** with the CT disclosure. **Manual: one click.** If the LocalAPI feature-consent URL (`QueryFeature`) is available to tsnet, the console uses that one-click link instead of the generic admin page (VERIFY: https://github.com/tailscale/tailscale/blob/main/client/local/local.go). Either way the user has to click. Holdfast can't flip tailnet settings without an admin API key, and it won't hold one. |
| Tailnet or machine renamed | The FQDN changes. | §3 `host_mismatch`, with the recovery order. |
| No internet at home | Peers on the same LAN may still connect with cached state (VERIFY). | No claim in user docs until Sounding has tested it. |

### 8. Threat model delta (for Bulwark)

- **New container:** `access` (Go, distroless, non-root, no capabilities, read-only). New networks:
  `edge` and `accessctl` (internal) and `access_egress`.
- **New egress** from `access` only: the Tailscale control plane, DERP relays (TCP 443), STUN
  (UDP 3478), direct peer UDP, and Tailscale's certificate flow. The firewall list to confirm is at
  https://tailscale.com/kb/1082/firewall-ports (VERIFY). Log upload is off.
- **New host port:** `127.0.0.1:47800` (console). It's token-gated, has a Host allowlist, uses no
  cookies, and serves only setup, connect, status and recovery verbs. Older Docker releases let
  hosts on the same LAN reach `127.0.0.1`-published ports by routing to them. The installer requires
  a Docker version with that fixed (VERIFY: https://docs.docker.com/engine/release-notes/28/).
- **A compromised `access` can:**
  - read all app traffic in plaintext and relay it, because it holds the TLS key and terminates
    TLS. That means session theft and live use of the app.
  - join the node to another tailnet;
  - enable Funnel through its in-process client, if the tailnet policy grants the Funnel attribute.
    New tailnets' default policy may grant it to members (VERIFY: https://tailscale.com/kb/1223/funnel).
    The docs show how to remove that attribute.
  - reach Caddy's PROXY listener and forge client IPs for rate limits and audit;
  - reach the API's port on `accessctl`, without trusted-proxy status, so it would only be an
    unauthenticated client.

  It **cannot** reach `db` or `redis` (the internal `backend` network), the document volume, `.env`
  or the Docker socket.
- **Mitigations:**
  - minimal verbs and the forbidden-identifier test
  - pinned Go module versions with Dependabot `gomod`, plus `govulncheck` in CI
  - the image built in CI only, by digest (ADR 0018), and cosign-signed before beta
  - the passkey stays the second factor
  - the console's signed-in-account confirmation, which catches a hijacked login URL
  - audit entries for connect, disconnect, rebind and recovery
- **Metadata Tailscale sees:** node names, tailnet IPs, public endpoints, OS and client versions,
  and online times. It can't decrypt WireGuard traffic. DERP relays see only ciphertext.
  **Certificate transparency publishes the FQDN** (`holdfast.tailXXXX.ts.net`) to public logs. Mark
  this VERIFY (https://tailscale.com/kb/1153/enabling-https). The wizard discloses it before the
  HTTPS step, and the installer offers a neutral machine name.

## Alternatives considered

The criteria are: user steps after Docker (a non-technical user), public exposure, who sees traffic
or metadata, cost to the project, licence, Docker Desktop on Windows/macOS/Linux, a certificate valid
for passkeys and PWA install on iOS and Android, and what breaks when the vendor changes terms.

| Option | Steps | Public exposure | Sees traffic / metadata | Project cost | Licence | Docker Desktop W/M/L | Passkey + PWA cert | Vendor risk | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **tsnet `access` service (chosen)** | 9 | None. Tailnet only, Funnel impossible in code | Tailscale: metadata. CT: FQDN. Content: nobody | $0 (users' free plan; VERIFY https://tailscale.com/pricing) | BSD-3-Clause (VERIFY https://github.com/tailscale/tailscale/blob/main/LICENSE), AGPL-compatible | Same everywhere (userspace) | Let's Encrypt `*.ts.net`, valid on iOS and Android. `ts.net` on the PSL keeps tailnets apart (VERIFY https://publicsuffix.org/list/public_suffix_list.dat) | Free tier or terms change: `ACCESS_CONTROL_URL` → Headscale plus the manual domain path | **Default** |
| Stock `tailscale/tailscale` sidecar, kernel mode, Caddy sharing its netns | 9 | None, if config is right | Same | $0 | BSD-3 | Needs `/dev/net/tun` + `NET_ADMIN`. Breaks rootless. Docker Desktop untested | Valid | Same | Rejected. More privilege, and a full LocalAPI plus CLI in the container can enable Funnel. If the netns owner restarts, Caddy loses networking until it's recreated |
| Stock sidecar, userspace, `tailscale serve` HTTPS → Caddy | 9 | None, if serve config is right | Same | $0 | BSD-3 | Works | Valid | Same | Rejected. Real IP depends on how Serve handles `X-Forwarded-For` (append or overwrite; VERIFY https://tailscale.com/kb/1312/serve). Funnel is one config line away. The login URL has to be scraped from logs or reached through a broker that holds the socket |
| Stock sidecar + Caddy's built-in ts.net cert getter (socket shared into Caddy) | 9 | None | Same | $0 | BSD-3 / Apache-2.0 | Works | Valid | Same | Rejected. Caddy, the component that parses HTTP, would hold the full LocalAPI |
| `tailscale/caddy-tailscale` plugin (Caddy embeds tsnet) | 9 | None | Same | $0 | VERIFY https://github.com/tailscale/caddy-tailscale | Works | Valid | Plugin maturity | Rejected for now. It puts the node key in the HTTP parser and has no status or login-URL surface for the wizard. Revisit if it matures |
| Host Tailscale + `tailscale serve` (today's manual setup), automated by the installer | ~9, plus host CLI and `sudo`/operator setup on Linux | None | Same | $0 | BSD-3 | Depends on the host app variant (the macOS App Store build differs) | Valid | Same | **Kept as a documented manual path** with the PROXY fix (§4). Not the default: it depends on the host's version, rights and existing serve config, and the RP id follows the PC's hostname |
| Headscale or other self-run coordination | Many (a public coordination endpoint, custom server URL in the phone apps) | Coordination server must be public | The user | $0 or the user's VPS | BSD-3 | Works | No ts.net certs, so an owned domain + DNS-01 | Self-run | Advanced exit path only (`ACCESS_CONTROL_URL`) |
| Cloudflare Tunnel + Access | ~8, plus buy a domain and move DNS to Cloudflare | **Public hostname** | **Cloudflare sees plaintext** at its edge (PLAN 12.1) | $0 for the free plan, but a domain costs money | cloudflared Apache-2.0 | Works | Valid | Free plan terms | Rejected. Breaks rule 12's intent and the privacy claim. A webhook-only tunnel (PLAN 7.4) would need its own ADR |
| Relay run by Holdfast | ~5 | Public relay | **Holdfast** sees metadata and maybe traffic | Paid infrastructure (ADR 0013 no) plus legal exposure (PLAN 17.3) | Ours | Works | Valid | Ours | Rejected for self-host. Belongs to Holdfast Cloud (ADR 0009) |
| WireGuard direct | Router port-forward, keys, QR | **Public UDP port**. Breaks behind CGNAT | Nobody | $0 | GPL-2.0 kernel / MIT tools | Port forwards from Docker Desktop are fiddly | Needs a domain or local CA | None | Rejected. A port-forward violates rule 12 |
| LAN only, mDNS + local CA | Install and *fully trust* a root CA profile on iOS (about 5 Settings screens), on Android, and on the PC | None | Nobody | $0 | n/a | mDNS from containers is unreliable on Docker Desktop | Works only with a trusted local CA. Android apps ignore user CAs | None | Rejected as default. Doesn't work on LTE, and trusting a CA is too many steps. Possible later "home-only" mode |
| Tailscale Funnel | Few | **Public** | Tailscale relay | $0 | BSD-3 | Works | Valid | Same | Rejected by rule 12. Forbidden in code |

Why a separate `edge` network and not Caddy sharing `access`'s network namespace: the shared
namespace would allow a `127.0.0.1` PROXY allowlist. But when the namespace owner restarts, Docker
leaves dependent containers without networking. The dedicated `/29` gives the same trust by
membership without that fragility.

Why Go: tsnet exists only as a Go library, and a broker that talked to `tailscaled`'s LocalAPI from
Python would depend on an HTTP surface Tailscale doesn't promise to keep stable. The binary stays
small (target under 600 lines) and lives in `infra/access/`. This amends ADR 0004's stack by one
narrowly scoped language. Keel confirms (open question 1).

## Consequences

- **Easier:** a stranger gets a real certificate, passkeys and an installable PWA on their phone
  without a domain, a DNS token or knowing what a reverse proxy is. Per-IP limits and audit IPs
  become correct in the default stack. Rule 12 gets a mechanical check for the default install
  instead of a doc convention.
- **Harder:**
  - another language and toolchain in CI (a path-gated Go job; ADR 0013 minutes)
  - another image to build, sign and mirror (ADR 0018 adds `holdfast-access`, multi-arch
    amd64/arm64)
  - an instance-level table, and `rp_id` on credentials (migration token)
  - the RP id resolved per request
  - a loopback console surface for Bulwark to review
  - the manual-path docs have to stay maintained
  - Tailscale is a dependency for the default experience. If its free plan or terms change badly,
    the default degrades to the manual paths until a replacement ADR lands
- **Docs and rules:** Keel applies CLAUDE.md rule 12 (§2) when this ADR is accepted. Beacon updates
  PLAN 12.1 / Phase 8.5 through the next amendment (Section 20.x), plus `client-ip-check.md` and the
  install guide. `docs/STATUS.md` owner-queue items about the domain and DNS token become optional
  (manual path only).
- **Product voice:** self-host copy stays Direction 01. The wizard never says "VPN-free" or "no
  third party". It names Tailscale plainly and says what Tailscale can and can't see.

## Open questions (Keel / Bulwark / Gadfly, not owner)

1. Go in the stack (ADR 0004 amendment). Recommended yes, scoped to `infra/access/`.
2. Windows bootstrap versus rule 10. Either require pwsh 7 (one extra step, recommended for alpha),
   or allow a 5.1-compatible stub that installs pwsh 7 and re-launches. The stub is a rule-10
   deviation and would need a CLAUDE.md edit.
3. Default machine name: `holdfast` (recognizable in the admin console and the URL bar) or a neutral
   name, given that CT logs publish it. Recommended: `holdfast`, disclosed and changeable at install.
4. Whether `access` should send the peer's WhoIs device name as a PROXY v2 TLV for audit (later).
5. Console port `47800`: confirm it doesn't clash with common self-host apps.

## Owner queue (money, accounts, legal)

1. **Tailscale as the recommended default** for users. Each household creates its own Tailscale
   account and accepts Tailscale's terms, and Holdfast holds no account for them. The docs state
   this. Owner confirms they're comfortable steering users to a third-party vendor's free plan.
2. **A test tailnet for acceptance.** Sounding needs a free Tailscale account that isn't the owner's
   personal tailnet, plus one iOS and one Android device on it, for PA10 (account).
3. **Docker Desktop licence** for the macOS and Windows recommendation. The free tier's terms for
   personal and small-business use apply to users (VERIFY:
   https://www.docker.com/legal/docker-subscription-service-agreement/ and
   https://www.docker.com/pricing/faq/). Owner decides whether the docs also name Podman Desktop or
   Rancher Desktop (both Apache-2.0) (legal).
4. **Code signing / notarization** (money). It's needed only if a native launcher replaces the
   scripts later: Apple Developer Program for notarization and an Authenticode certificate for
   SmartScreen. Not needed for this ADR's script installer. Deferred.
5. **Install script URL.** Recommended: GitHub Release assets, with no new service or domain. Serving
   it from `holdfastfinance.app` would involve the owner's domain account (account).

## VERIFY list (check before the story that depends on it starts)

| Claim | Check | Blocks |
|---|---|---|
| Personal free plan limits (users, devices) and terms | https://tailscale.com/pricing | Decision as a whole |
| tsnet licence BSD-3-Clause | https://github.com/tailscale/tailscale/blob/main/LICENSE | PA1 |
| tsnet `ListenTLS`, `ControlURL`, `Store`/`Dir`, `UserLogf`, in-process LocalClient (status, login URL, logout) | https://pkg.go.dev/tailscale.com/tsnet | PA1 |
| Disabling log upload in tsnet | https://tailscale.com/kb/1011/log-mesh-traffic | PA1 |
| HTTPS certs off by default, enabled in the admin console, CT publication, renewal window | https://tailscale.com/kb/1153/enabling-https | PA1, PA5 |
| MagicDNS on by default for new tailnets | https://tailscale.com/kb/1081/magicdns | PA5 |
| Feature-consent URL (`QueryFeature`) reachable from tsnet | https://github.com/tailscale/tailscale/blob/main/client/local/local.go | PA5 |
| Key expiry 180 days; per-machine disable; tagged nodes exempt; re-auth keeps IP and name | https://tailscale.com/kb/1028/key-expiry | PA7 |
| Machine-name collision suffix and rename behaviour; tailnet rename | https://tailscale.com/kb/1098/machine-names, https://tailscale.com/kb/1217/tailnet-name | PA3, PA6 |
| Default policy grants the Funnel attribute | https://tailscale.com/kb/1223/funnel | PA8 docs |
| Device approval / Tailnet Lock node states | https://tailscale.com/kb/1099/device-approval, https://tailscale.com/kb/1226/tailnet-lock | PA5 |
| Firewall and egress list | https://tailscale.com/kb/1082/firewall-ports | PA1 |
| Sign-up identity providers | https://tailscale.com/kb/1013/sso-providers | PA8 docs |
| `ts.net` on the Public Suffix List | https://publicsuffix.org/list/public_suffix_list.dat | PA3 |
| Serve `--tls-terminated-tcp` + `--proxy-protocol` semantics and minimum version (manual path); Serve `X-Forwarded-For` handling | https://tailscale.com/kb/1242/tailscale-serve, https://tailscale.com/kb/1312/serve | PA8 |
| Caddy `proxy_protocol` wrapper `allow` / `fallback_policy` | https://caddyserver.com/docs/caddyfile/options#listener-wrappers | PA2 |
| Docker 127.0.0.1-publish LAN reachability fix version | https://docs.docker.com/engine/release-notes/28/ | PA9 |
| Docker Desktop licence terms | https://www.docker.com/legal/docker-subscription-service-agreement/ | Owner queue 3 |
| `caddy-tailscale` licence and maturity (rejected alternative) | https://github.com/tailscale/caddy-tailscale | none |

## Story breakdown (epic PA, "private access")

The IDs use `PA`, not `A`. `A1` is already the redaction story in `docs/planning/ROADMAP.md`. Every
story is S or M (SDLC §1). Gadfly challenges the epic and Bulwark threat-models it before PA1
starts. PA1 and PA2 deliver the first working Linux path (with `APP_HOST` set by hand), and the
owner's own host can switch over at that point. PA3 to PA5 make it zero-config.

| ID | Story | Owner | Size | Depends on | Acceptance evidence |
|---|---|---|---|---|---|
| PA1 | `infra/access/` Go module: tsnet node (userspace, state volume, log upload off, filtered logs), `ListenTLS :443` → PROXY v2 → upstream, CLI `holdfast-access login` / `status` printing the login URL and a terminal QR to the operator's terminal only. Forbidden-identifier test. Distroless image. CI Go job (vet, test, `govulncheck`), path-gated, plus Dependabot `gomod` | Harbor (Bulwark reviews) | M | ADR accepted, Gadfly + Bulwark pass | `go test` green, including PROXY header bytes and forbidden-identifier tests. Image runs as non-root on a read-only root filesystem. Redaction test shows no login URL in log output |
| PA2 | `infra/docker-compose.access.yml` (networks `edge`/`accessctl`/`access_egress`, pinned IPs), `Caddyfile.access` (edge bind, `proxy_protocol` allow `/29`, XFF overwrite, prod headers), `check-compose-ports.py --access` plus the `--prod` CGNAT-range check, `make up-access`, rule-12 wording applied to CLAUDE.md by Keel | Harbor (Bulwark reviews) | M | PA1 | **First working Linux path** with `APP_HOST` set by hand from `holdfast-access status`: phone on the tailnet registers a passkey, and `audit_log.ip` is the phone's `100.x`. `make ports` green. A device off the tailnet can't resolve or connect (Sounding, counts only) |
| PA3 | Host binding: migration for `instance_access` and `webauthn_credential.rp_id` (backfill), RLS-lint exemption with reason, `app/services/access.py` resolver (env > bound > none), per-request RP id in passkeys, `409 access_not_ready`, bind at first owner passkey | Tide (Ledger + Bulwark review). Holds the migration token | M | PA2 | Tests: unbound instance refuses registration; bind happens only from fetched status that matches the `Host` header; manual mode unchanged; up/down/up migration; credentials filtered by `rp_id` at login |
| PA4 | `access` control API (`/v1/status`, `/v1/login`, `/v1/logout`) on `accessctl` with a bearer token, `ACCESS_CONTROL_TOKEN` in `.env.example` and `gen-keys`, API client plus `/api/access/status` (owner) and `/api/access/disconnect` (owner + step-up) | Harbor (Go side), Tide (API side), Bulwark reviews | M | PA1, PA3 | Contract tests with a fake `access`; wrong or missing token → 401; disconnect requires step-up and writes an audit entry; peer summary carries no names |
| PA5 | Loopback console: Caddy console site (Host allowlist, `/console` + `/api/console/*` only), `app.cli open` (console + setup tokens, fragment URL), web console pages (connect, confirm account, settings deep links for HTTPS/MagicDNS/approval, QR 1, auto-advance, QR 2) | Tide (API), Prism (web) | M | PA4 | e2e against a fake `access` covering the whole wizard; a rebinding test (wrong `Host` → 421); no cookies set; 375px layout; brand review |
| PA6 | Recovery and rebind: `app.cli recover` token (15-minute TTL, single use, stdout only), rebind with step-up, `host_mismatch` state and the "Holdfast moved" page, stale-credential display, session revocation, alerts | Tide (Bulwark reviews) | M | PA3, PA5 | Tests: token single use and expiry, nothing written to the API log, all sessions revoked, audit plus alert. A name change is never followed automatically |
| PA7 | Settings → Access (status, cert and key-expiry banners at 30/7/1 days, Disconnect, "use on this PC" guidance) and the PWA shell's offline hint "Is Tailscale on?" | Prism | S | PA4 | Vitest plus e2e with a fake `access`; the SW test still asserts no `/api` caching |
| PA8 | Docs: install guide per OS with the step table and stuck-point fixes; manual paths (domain DNS-01, host Serve with PROXY); backup/restore without `access_state`; revocation; Funnel-attribute removal; `client-ip-check.md` access-mode row; PLAN Section 20.x amendment draft | Beacon (Keel reviews) | S | PA5, PA6 | Link check green; every Tailscale claim either cites the checked page or stays marked VERIFY |
| PA9 | Installer: `install.sh` / `install.ps1` (pwsh 7), digest-pinned `compose.yaml`, `holdfast` wrapper verbs, Docker version floor, multi-arch `holdfast-access` in the release workflow (ADR 0018 addition), CI installer smoke on Linux | Harbor (Bulwark reviews) | M | PA5, PA6 | CI: a fresh Linux VM install reaches the console with a fake `access`. Release dry run lists four image digests. Installer refuses to overwrite an existing `.env` |
| PA10 | Acceptance on real devices: Linux, macOS and Windows hosts; iOS and Android phones; counts of steps taken against the table above; stuck-point log; key-expiry simulation; off-tailnet probe | Sounding | M | PA7, PA8, PA9, owner queue 2 | `docs/phases/` acceptance note with counts and pass rates only (no tailnet names, no IPs) |
