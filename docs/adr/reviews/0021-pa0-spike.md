# ADR 0021 PA0 spike results (Harbor)

Status: complete except where marked NOT RUN. Two runs: the first was interrupted by a host restart.
Date: 2026-11-21. Names are placeholders: `<node>.<tailnet>.ts.net`, `100.x.x.x`. No real tailnet name,
address, login name or avatar URL appears here (ADR 0019 style).

## Environment

- Host: Fedora 43, Docker Engine 29.9.3, host tailscale 1.104.7 (read-only `tailscale status`/`ip`
  only; host serve config untouched).
- Test node: `tailscale/tailscale:stable` = tailscaled v1.104.7 (Go 1.27.4), container `hfspike-ts`,
  userspace networking, `--cap-drop ALL`, `--read-only`, `no-new-privileges`, `/tmp` tmpfs, no
  published ports, state in volume `hfspike_ts_state`, socket in volume `hfspike_sock`.
- Dummy upstream: `hfspike-up` (`python:3.12-alpine`, `--cap-drop ALL`, read-only), a 70-line asyncio
  echo that parses an optional PROXY v2 header and returns peer address, PROXY fields and request
  headers as JSON. Listens on `0.0.0.0:8080` and on the unix socket `/run/hfup/up.sock`.
- Everything on the user-defined bridge `hfspike-net`. Tests run from the host (same tailnet) with
  `curl https://<node>.<tailnet>.ts.net/` (real Let's Encrypt cert, no `-k`).
- Serve configs are files mounted read-only at `/config` and selected with `TS_SERVE_CONFIG`
  (containerboot substitutes `${TS_CERT_DOMAIN}`).

## Summary

| # | Check | Result |
|---|---|---|
| 1 | Serve TCP + PROXY v2, TCP upstream, real peer address | PASS |
| 2 | Same to a unix socket | FAIL with PROXY v2 (unix forwarding without PROXY works) |
| 3 | Serve HTTPS proxy and forged `X-Forwarded-For` | PASS: overwritten, not appended |
| 4 | LocalAPI shared read-only into non-root container | PASS, with UID and inventory caveats |
| 5 | Wrapper gating, no log upload, cap-drop, non-root | PASS, plus the 60 s sign-in exit finding |
| 6 | Name and IP stability | PARTIAL: restart keeps name and IP; re-auth, lost state, removal NOT RUN (owner) |
| 7 | HTTPS certificate and MagicDNS | PARTIAL: cert obtained and serving; default-off NOT OBSERVABLE here |
| 8 | Free-plan limits | PASS (figures recorded; shared-in counting not stated by the docs) |
| 9 | tsnet needed? | NO, from 1, 3 and 4 |
| 10 | WebAuthn and `Secure` cookie on `http://localhost` | Chromium PASS; Firefox cookie/secure-context PASS, WebAuthn NOT RUN; WebKit NOT RUN; Safari, Edge NOT RUN (owner) |
| 11 | Docker floor | PASS: 28.0.0 (28.3.3 recommended) |
| 12 | Restart with control plane unreachable | Serving continues without restart; FAILS after restart |

## Results

### 1. TLS-terminated TCP + PROXY v2 to a TCP upstream: PASS

Config (declarative, stock image, no CLI):

```json
{"TCP":{"443":{"TCPForward":"hfspike-up:8080","TerminateTLS":"${TS_CERT_DOMAIN}","ProxyProtocol":2}}}
```

Upstream saw TCP peer = the sidecar's container IP, and a PROXY v2 header (`ver_cmd 0x21`, family
`0x11` = TCP4, len 12) with `src` = the host's tailnet IPv4 and source port, `dst` = the node's tailnet
IPv4 and the forward port. Checked programmatically: `src == host tailnet IP` and
`dst == node tailnet IP`. HTTP then arrived as plaintext after the header (TLS terminated by Serve).
`tailscale serve status` renders it as "TLS-terminated TCP, tailnet only" and lists the node name and both
tailnet IPs on :443 (so the same handler is reachable by IP, but without a valid cert for the IP).
Field names: `TCP.<port>.{TCPForward,TerminateTLS,ProxyProtocol}`; it works in v1.104.7.
Minimum version not bisected (not needed: the product pins a digest of a current stable).

Meaning: the primary design (§1/§4) works with the stock image. The real tailnet peer address is
available to Caddy through PROXY v2.

### 2. Same to a unix-socket upstream: FAIL for PROXY v2 (forwarding itself works)

- `TCPForward: "unix:/run/hfup/up.sock"` with `TerminateTLS` and no `ProxyProtocol`: PASS, requests
  reach the unix listener (peer address empty, as expected for unix).
- The same with `"ProxyProtocol": 2`: the TLS connection is dropped before the handshake completes
  (`curl: (35) Send failure: Broken pipe`, `openssl s_client` shows no cipher negotiated). The
  upstream accepts a connection and reads 0 bytes. `tailscale serve status` accepts the config and
  shows `unix:/run/hfup/up.sock`, so the failure is silent apart from the client error.

Meaning: a unix socket and PROXY v2 cannot be combined in this version. A unix socket without PROXY
would lose the client IP, which is the point of the exercise. The ADR's preferred transport (unix
socket) is out; the fallback (a TCP target on the unpinned two-member `edge` network) is the design.

### 3. Serve HTTPS (web) proxy and forged headers: PASS (overwrites; fallback 1 is valid)

Config: `{"TCP":{"443":{"HTTPS":true}},"Web":{"${TS_CERT_DOMAIN}:443":{"Handlers":{"/":{"Proxy":"http://hfspike-up:8080"}}}}}`.
Client sent `X-Forwarded-For: 6.6.6.6`, `X-Forwarded-Proto: ftp`, `X-Forwarded-Host: evil.example`,
`Forwarded: for=7.7.7.7`, `X-Real-IP: 8.8.8.8`, `Tailscale-User-Login: forged@example.com`.
Upstream saw:

| Header | Result |
|---|---|
| `X-Forwarded-For` | **Overwritten** with the client's tailnet IP only (the forged `6.6.6.6` is gone, not appended) |
| `X-Forwarded-Proto` / `X-Forwarded-Host` | Overwritten (`https`, the node FQDN) |
| `Forwarded` | Dropped |
| `X-Real-Ip` | **Passed through unchanged** (client-forgeable; never trust it) |
| `Tailscale-User-Login`, `Tailscale-User-Name`, `Tailscale-User-Profile-Pic` | Added by Serve for the authenticated tailnet user; the forged login was replaced by the real one |
| `Tailscale-Headers-Info` | Added (docs link) |

The upstream TCP peer is the sidecar container; there is no PROXY header in this mode.
Meaning: fallback 1 (§4) is valid: Caddy may trust `X-Forwarded-For` from the sidecar only, and must
still overwrite `X-Real-Ip` and strip `Tailscale-User-*` unless it deliberately uses them. The
`Tailscale-User-*` headers are a possible identity hint, not used by the design; they identify a
tailnet user, not a Holdfast user. Note a shared-in user's login shows up here too.

### 4. LocalAPI socket shared with a non-root container: PASS (read-only), with three caveats

Test: container `python:3.12-alpine` as `--user 1000:1000`, `--cap-drop ALL`, `--read-only`,
`--network none`, socket volume mounted `:ro` (the socket is still connectable read-only), speaking
HTTP over the unix socket (`Host: local-tailscaled.sock`, `Sec-Tailscale: localapi`). Sidecar's
tailscaled runs as root in the container.

- Readable (200): `GET /status` with `BackendState`, `AuthURL` (empty when logged in),
  `Self.DNSName`, `Self.KeyExpiry`, `CertDomains`, `CurrentTailnet{Name,MagicDNSSuffix,MagicDNSEnabled,StableID}`,
  `User`, `Health`; `GET /prefs`; `GET /serve-config`.
- Denied (403): `logout`, `start`, `login-interactive`, `reset-auth`, `set-dns`, `set-expiry-sooner`,
  `debug`, `profiles/`, `check-prefs`, `id-token`, `cert/<fqdn>` (so no certificate or key is
  readable), `POST /serve-config` with the unchanged config (so serve and Funnel cannot change), and
  `PATCH /prefs` with an empty mask (so `set`/`up` cannot). Destructive calls were probed with the
  wrong HTTP method (403 means denied before method validation, 400/405 means permitted) and no-op
  bodies, so the spike node was never logged out.
- Same probes as `--user 0:0`: `logout` and `start` return 400 "want POST", `reset-auth` 405, `cert`
  returns the **private key**, serve-config and prefs writes return 200. Root in the client container
  = full control.

Caveats for the design:
1. **Non-root is the entire gate.** The `api` container must never run as UID 0. Also the sidecar and
   `api` must not share a UID: tailscaled gives write to root, its own UID and the configured
   operator (LocalAPI peer-credential check; from upstream behaviour, not independently verified for
   the same-UID case here). Pin `api` to a UID that differs from the sidecar's user.
2. **Read access leaks the peer inventory.** The same read-only client sees `Peer` (3 other devices
   on the test tailnet with hostnames, OS, last-seen) and `GET /file-targets` returns device names.
   The ADR already lists "learns the tailnet's device inventory" as the privacy delta; the API client
   must read only `Self`, `CurrentTailnet`, `CertDomains` and `User` count and must not log or store
   the raw status.
3. **Rootless Docker (reasoned, not installed or run).** `rootlesskit` exists on the host but was not
   enabled. Under rootless Docker the daemon runs in a user namespace; container UIDs map to subuids,
   and SO_PEERCRED is translated into the reader's user namespace, so tailscaled still sees `api` as
   UID 1000 (non-root) and a root-in-container client as UID 0 (write). The decision rule is therefore
   the same: container UID, not host UID, is what counts. The sidecar's "root" in rootless Docker is
   the unprivileged host user, which is a plus. Not verified empirically.

Meaning: sharing the socket read-only into a non-root `api` is acceptable and the CLI fallback is not
needed, provided the three conditions hold (non-root, distinct UID, status filtered). CI should assert
`user:` is set and non-zero on `api` and that the volume mount is `:ro`.

### 5. Start wrapper, log upload, caps, non-root: PASS (all supported by the stock image), plus one finding

Fresh, unauthenticated state volume each time, container shares a pause container's network
namespace so a `tcpdump` container (`NET_RAW`, own container) sees all of its packets.

- **Finding: the stock entrypoint exits after 60 s when sign-in is not completed.**
  `containerboot` runs `tailscale up`, prints the login URL (`To authenticate, visit: ...`) and, with no
  `TS_AUTHKEY` and nobody signing in, dies with `failed to auth tailscale: tailscale up failed:
  context deadline exceeded` (exit 1) at +60 s. With a restart policy this loops and mints a new login
  URL each time. The wrapper must therefore (a) not run `containerboot` until consent, and (b) after
  consent, either keep restarting it (accept new URLs) or run `tailscaled` directly and call
  `tailscale up`/`login` itself so one URL stays valid until the user signs in (untested here beyond
  the earlier direct-`tailscaled` run). The
  earlier hfspike run used direct `tailscaled` for this reason. The wrapper must also forward SIGTERM.
- **Flag gating: PASS.** Wrapper (`sh`, ~15 lines, `--entrypoint /bin/sh`, flag on a read-only
  volume, `containerboot` started only after `/run/flag/enabled` exists): with no flag, 30 s of
  capture showed zero packets from the node (only two unrelated link-local mDNS/router-solicit packets
  from the shared namespace); within the first second after `touch enabled`, `containerboot` started
  and TCP 443 connections to Tailscale's control range (`192.200.0.0/24`) began. Default entrypoint
  contacts the control plane immediately (DNS lookup of the control host, TCP 443, login URL after
  about 1 s).
- **Logout on flag removal: PASS.** Removing the flag made the wrapper call
  `tailscale logout` (`POST /localapi/v0/logout` in the node log) and idle. The test node was not logged
  in, so this exercised the path but not the control-plane deregistration of a logged-in node; the
  wrapper must also kill `containerboot` before idling (done in the spike script).
- **`--no-logs-no-support`: PASS (supported via `TS_TAILSCALED_EXTRA_ARGS`).** tailscaled logs
  "You have disabled logging. Tailscale will not be able to provide support." and, in 60 s with the
  flag, the node made 2 DNS lookups (control plane A and AAAA); the same node without the flag made 4
  (the extra pair is the log-upload host). Observation is by DNS response counts because the container
  resolver is on loopback; hostnames were not decoded.
- **All capabilities dropped: PASS** (`--cap-drop ALL`; harmless warnings about UDP buffer sizes and
  the missing TPM device).
- **Read-only root filesystem: PASS** with `/tmp` tmpfs and a state volume; one harmless warning
  (`failed to symlink socket: mkdir /var/run/tailscale`).
- **Non-root: PASS in userspace mode** with `--user 1000:1000`, a state volume pre-chowned to that UID
  (mode 0700) and `TS_SOCKET` under a writable path: `containerboot` and `tailscaled` ran as UID 1000
  and reached the login-URL state. Caveat to design: the state volume must be pre-created with the
  right owner (an init step or `chown` in the wrapper is impossible once non-root). Serve as non-root
  was not tested.
- **Same-UID caveat confirmed (feeds check 4):** on the non-root node, `tailscale set` as UID 1000
  (the daemon's own UID) succeeded; as UID 2000 it was denied (`checkprefs access denied`). So if the
  sidecar is made non-root, `api` must run as a different UID.

Meaning: the wrapper design in §1 is feasible with stock parts, but it must not wrap `containerboot`
naively for the sign-in phase (60 s exit). Hardening: all of cap-drop, read-only root, no-new-privileges
and no log upload are supported; non-root is feasible but forces a UID plan.

### 6. Name and address stability: PARTIAL

- **Restart with state kept: PASS.** `docker restart` of the node (and the first host-restart
  recovery of the earlier run) kept the same FQDN and the same tailnet IPv4. State volume reuse also meant
  the owner approved the node only once.
- **Re-auth after key expiry, lost state, old node removed: NOT RUN.** They need the owner's admin
  console. Docs read (https://tailscale.com/kb/1098/machine-names, retrieved 2026-11-21): when a device
  with the same name already exists, a new machine gets `<hostname>-1`, and keeps that name even after
  the conflict is resolved. Key expiry (https://tailscale.com/kb/1028/key-expiry): default 180 days;
  after expiry connections stop until re-authentication.
  Exact owner steps: (a) admin console, Machines, `holdfast-spike`, Disable key expiry off then
  "Expire key now", then on the container run `tailscale up` and sign in: record name and IP;
  (b) stop the node, `docker volume rm hfspike_ts_state`, start a new node with the same hostname while
  the old machine is still listed: expect `holdfast-spike-1`; (c) delete the old machine in the console,
  recreate with the same hostname: record whether it reclaims `holdfast-spike`.
Meaning: unchanged; multiple RP ids already make these outcomes recoverable.

### 7. HTTPS certificates and MagicDNS: PARTIAL

- The node obtained a real certificate: `TerminateTLS` in the serve config made tailscaled fetch a
  Let's Encrypt certificate itself (files `<fqdn>.crt` and `.key` appeared in the state volume
  `certs/`, plus `acme-account.key.pem`), and the host's curl validated it without `-k`. `tailscale cert
  <fqdn>` does the same on demand. Status reports `CertDomains` (one entry) and
  `CurrentTailnet.MagicDNSEnabled: true`.
- **Default-off cannot be observed.** This tailnet already had MagicDNS and HTTPS on, so the "step 3
  is one click" count is unverified. Docs (https://tailscale.com/kb/1153/enabling-https): admin console
  DNS page, enable MagicDNS, "Enable HTTPS", acknowledge that machine names are published to the
  Certificate Transparency ledger, then run `tailscale cert` on each machine.
- **How the product detects it without a failing call:** read-only status has `CertDomains`. Empty
  means HTTPS is off. The upstream CLI itself prints "HTTPS cert support is not enabled/configured for your
  tailnet." in that case (`cmd/tailscale/cli/cert.go`, v1.104.7). The only server error text captured
  here is for a wrong domain: `500 Internal Server Error: invalid domain "<x>"; must be one of ["<node>.<tailnet>.ts.net"]`.
  The control-plane message for HTTPS-disabled was not captured (needs a fresh tailnet): NOT RUN, owner
  action: a new test account, run `tailscale cert <fqdn>` before and after enabling HTTPS.
- Serve's own enable-URL / `QueryFeature` flow was not exercised for the same reason.
Meaning: the wizard should key off `CertDomains` and `MagicDNSEnabled`, deep-link the DNS admin page,
and not call `cert` to probe.

### 8. Free-plan limits: PASS (retrieved 2026-11-21)

- https://tailscale.com/kb/1154/free-plan: "The Personal plan permits 6 free users in a single
  Tailscale network ... You can also share devices with other users with node sharing."
- https://tailscale.com/pricing, Personal: "$0 Free forever", "Unlimited user devices", "Up to 6 users",
  "Up to 3 ACL groups", "Up to 50 tagged resources to start". Tagged resources are devices owned by a tag
  (servers, routers); a node signed in by a user, as ours is, is a user device and unlimited.
- Same page: the Personal plan "is only suitable for non-commercial use"; a tailnet on a public identity
  domain (Gmail, Apple, personal GitHub) is treated as personal use, a custom domain as business with a trial.
- Shared-in users: neither https://tailscale.com/kb/1084/sharing nor the pricing page states whether they
  count toward the 6 users. The free-plan page lists node sharing as separate from the 6 users, and a
  sharee belongs to their own tailnet, so it is likely they do not count. Not stated explicitly.
Meaning: the vendor trigger "drops below 3 users" is not met (6 today). Update the ADR's wording from
"3" to the measured 6. The non-commercial clause matters for Holdfast Cloud and for any business use,
and should be in the docs. Do not give the sidecar a tag (it would become a counted tagged resource).

### 9. Is tsnet needed? NO

Items 1 (PROXY v2 to a TCP upstream works), 3 (fallback 1 valid) and 4 (read-only LocalAPI share works)
all pass. tsnet is off the table. The only transport change is that the unix socket is gone (item 2):
use the unpinned two-member `edge` network.

### 10. `http://localhost:<port>`: WebAuthn and Secure cookie: PARTIAL

Test page served by a loopback-only Python server on a random port (bound to 127.0.0.1), sending
`Set-Cookie: sess=...; Secure` (and a second `Secure; HttpOnly; SameSite=Strict` cookie), then calling
`navigator.credentials.create` and `get` with `rp.id = "localhost"`. Playwright-core 1.58.2.

| Browser | Secure context | `Secure` cookie stored and sent back | WebAuthn create + get |
|---|---|---|---|
| Chromium 145.0.7632.6 (Linux) | true | PASS (both cookies stored, `Cookie` header seen by server) | PASS with a CDP virtual authenticator (ctap2, internal, resident key) |
| Firefox 146.0.1 (Linux) | true | PASS (same) | NOT RUN: no virtual authenticator available through Playwright; `PublicKeyCredential` exists and the call was refused by the platform, which says nothing about the origin |
| WebKit (Playwright build, Safari approximation) | NOT RUN | NOT RUN | NOT RUN: host libraries missing (libicu74, libjpeg-turbo8) and no sudo |
| Safari (macOS), Edge (Windows) | NOT RUN | NOT RUN | NOT RUN: owner's macOS and Windows hosts |

Note: Chromium needed focus emulation (`Emulation.setFocusEmulationEnabled`) for WebAuthn in headless
mode. Meaning: the localhost-first design (§3) holds for Chromium and Firefox's cookie behaviour. The
Safari question (open question 2) stays open until a macOS run.

### 11. Docker floor: PASS (retrieved 2026-11-21)

- https://docs.docker.com/engine/release-notes/28/: **28.0.0 (2025-02-19)**: "Fix a security issue that
  was allowing remote hosts to connect directly to a container on its published ports" and "Fix a security
  issue that was allowing neighbor hosts to connect to ports mapped on a loopback address"
  (moby/moby#49325). Not present in the 27 release notes.
- **28.3.3 (2025-07-29)** fixes a regression: after a firewalld reload, published ports meant for loopback
  only could be reached from the local network (CVE-2025-54388, moby/moby#50506). So the real floor is
  **28.3.3**, not 28.0.
- Shipped versions (package pages, retrieved 2026-11-21): Debian 12 `docker.io` 20.10.24 (below floor);
  Debian 13 `docker.io` 26.1.5 (below floor); Ubuntu 24.04 `docker.io` 29.1.3 in noble-updates (above floor;
  the release pocket is older); Fedora 43 `moby-engine` 29.9.3 (above); Docker Desktop 4.100.0
  (2026-11-19) bundling Engine 29.9.3 (above). This host: Docker CE 29.9.3.
Meaning: Debian 12 and 13 need Docker's own repository (the extra Linux step stays); Ubuntu, Fedora and
Desktop meet the floor. Set the installer floor to 28.3.3 and compare numerically.

### 12. Control plane unreachable: PARTIAL, two different answers

Method: the node moved to an `--internal` Docker network (no egress; the host still reaches it over the
bridge). DNS and TCP to the control plane then fail ("network is unreachable"). The upstream stays reachable.

- **Already connected, no restart: serving continues.** Probes at +25, +50, +75 s all returned 200.
  Later probes showed one stall (a probe around +5.5 min timed out) after the network attachment changed
  the container's address; the path recovered by itself within about 20 s through direct discovery. All
  later probes (+6 to about +7 min) returned 200. So the claim is tested for about 7 minutes only, and
  the stall is an artefact of re-addressing the container, which a firewall-only block would not cause.
- **Restart during the outage: serving does NOT resume.** After `docker restart`, tailscaled logged
  `loaded netmap from disk cache; 0 peers`; 11 probes over about 2.5 minutes all failed, in two separate
  restarts. Status shows `Running` with the health message "You are logged out ... fetch control key ...
  network is unreachable", which is misleading. The cached netmap holds no peers, so no client is known.
- Recovery: once egress returned the node served again within 20 s, and a restart with egress kept the
  same name and IP.
Meaning: the ADR's wording ("probably keep working; a restarted node may not come back") is right and can
be stated firmly: existing sessions survive a control-plane outage as long as the sidecar is not
restarted; a restart during an outage means phone access is down until the control plane returns.
Docs: "Client-side netmap caching" is a Tailscale concept page; its exact guarantees were not read.

## Findings that most change the design

1. **Unix socket plus PROXY v2 does not work** (item 2). Use the `edge` network with a TCP target.
2. **The stock entrypoint exits after 60 s without sign-in** (item 5). The wrapper must own the sign-in
   phase and must not just wrap `containerboot`.
3. **LocalAPI write access is by container UID**, and the same UID as the daemon has write access
   (items 4 and 5). `api` must be non-root and a different UID, and read-only access still exposes the
   peer inventory.

Runner-ups: Debian 12 and 13 are below the Docker floor and the floor is 28.3.3; the free plan allows 6
users and is non-commercial; Serve's HTTPS mode overwrites `X-Forwarded-For`, so fallback 1 is real.

## Commands and versions

`tailscale/tailscale:stable` digest `sha256:72c1e19e0aedca96b8533bbb0b4368564410ff20b17e53b804eb6c1f04a31be4`
(tailscaled 1.104.7, Go 1.27.4); `python:3.12-alpine`; a local `hfspike-tcpdump` image (alpine +
tcpdump) for packet capture; Docker CE 29.9.3 on Fedora 43; Playwright-core 1.58.2. Containerboot env used:
`TS_USERSPACE=true TS_STATE_DIR=/var/lib/tailscale TS_SOCKET=... TS_HOSTNAME=holdfast-spike
TS_ACCEPT_DNS=false TS_SERVE_CONFIG=/config/<file> TS_TAILSCALED_EXTRA_ARGS=--no-logs-no-support`.
