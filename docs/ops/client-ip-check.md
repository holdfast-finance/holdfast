# Check that Holdfast sees real client IPs

Per-IP rate limits (passkey login, setup, invite accept) and the audit log key on the client
address. The API trusts `X-Forwarded-For` only from Caddy (`TRUSTED_PROXIES`, default loopback +
`172.30.10.0/24`), and Caddy overwrites that header with the TCP peer it saw (`{remote_host}`).
So the API is only as right as Caddy: if Docker hides the real peer from Caddy, every client
shares one rate-limit bucket and one lockout. See ADR 0010.

Run this once after the first deploy on a host, and again after changing Docker, Tailscale or
firewall setup. It takes a minute.

## 1. Make a request from another tailnet device

From a phone or laptop on the tailnet (not the server itself), open the app and sign in, or just
try a wrong password once. Note that device's Tailscale address (`tailscale ip -4` on it, or the
admin console). It's in `100.64.0.0/10` (or `fd7a:115c:a1e0::/48` for IPv6).

## 2. Read what the audit log recorded

On the server, from the repo root:

```sh
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT at, action, host(ip) FROM audit_log ORDER BY at DESC LIMIT 10;"'
```

(Add `-f docker-compose.yml -f infra/docker-compose.prod.yml` after `docker compose` on a prod
stack. The container's superuser bypasses RLS, so you see every household's rows: keep the output
on the server.)

## 3. Read the result

| `ip` shows | Meaning | Action |
|---|---|---|
| The device's `100.x.y.z` address | Correct. | Done. |
| `172.30.10.x` | The API didn't trust Caddy, or Caddy's header was dropped. | Check `TRUSTED_PROXIES` in `.env` (unset is fine; a custom value must still include the frontend subnet). |
| `172.17.0.1`, `172.18.0.1` or another docker gateway | Caddy saw Docker's gateway, not the client. Usually the userland proxy (`userland-proxy`) or rootless Docker (slirp4netns/pasta port forwarding). | See below. |
| `127.0.0.1` / `::1` | Something on the host is proxying into Caddy (e.g. `tailscale serve`). | Point Tailscale at the published port directly, or make that proxy pass the peer through. |

### Fixing a gateway address

- **Rootful Docker:** set `"userland-proxy": false` in `/etc/docker/daemon.json` and restart
  Docker, so published ports go through iptables DNAT and keep the source address.
- **Rootless Docker:** the default port driver rewrites the source. Use
  `DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER=slirp4netns` with
  `DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=true`, or run Docker rootful.
- **Never** fix this by widening `TRUSTED_PROXIES` to the gateway: that would trust a header any
  local process could send.

Repeat steps 1–2 after the fix. The new rows should carry the device's tailnet address.

The backend test `tests/api/test_security_lows.py::test_audit_log_records_the_forwarded_client_ip_from_a_trusted_proxy`
covers the API half (trusted peer + `X-Forwarded-For` lands in `audit_log.ip`). This check covers
the half a test can't: what Caddy sees on your host.
