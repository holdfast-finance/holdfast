# ADR 0021 PA0 spike results (Harbor)

Status: in progress (written incrementally; the final table at the top is updated last).
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
  consent, either keep restarting it (accept new URLs) or, better, run `tailscaled` directly and call
  `tailscale up --timeout=0`/`login` itself so one URL stays valid until the user signs in. The
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
