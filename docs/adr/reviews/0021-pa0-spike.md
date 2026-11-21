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
