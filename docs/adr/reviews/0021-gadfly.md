# Gadfly critique: ADR 0021 zero-config private access

- Target: `docs/adr/0021-zero-config-private-access.md` at `4f0c6ee` (branch `docs/adr-zero-config-access`)
- Date: 2026-11-21
- Location note: there is no prior ADR-review convention. The only earlier Gadfly critique is
  `docs/planning/roundtable/08-gadfly-challenge.md`, and this file follows its format (ranked attacks,
  each with a fix).
- Verdict: **reject as written**. Use the alternative in section 9 (a stock sidecar, the app on
  localhost first, credentials valid for more than one RP id), scheduled in Phase 8.5. Two small
  stories run now: a verification spike (S0) and the PROXY fix for the owner's own host (S1).

Severity: **fatal** means the design or the plan fails as stated. **Serious** means it ships with a
real user-visible or security cost. **Minor** means fix it while building.

## 1. Fatal: the case for Go and tsnet undercuts itself

**F1. The ADR's own manual path removes the reason for tsnet.** Section 4 fixes the owner's host with
`tailscale serve --tls-terminated-tcp 443 --proxy-protocol 2 tcp://…`. If that works on the host, it
also works in the stock `tailscale/tailscale` container. Containerboot reads a read-only
`TS_SERVE_CONFIG` JSON with `${TS_CERT_DOMAIN}` substitution, runs in userspace by default, and needs
no TUN device and no `NET_ADMIN`. That gives three of tsnet's selling points for free: the real
client IP, userspace networking and native cert renewal. If Serve does *not* do this, the manual-path
fix fails too. Either way, the one VERIFY that decides tsnet versus stock sits in PA8 (docs), at the
back of the epic. It should be the first thing checked.

**F2. The Funnel argument doesn't hold up.**
- In the stock sidecar, nothing outside that container can reach the LocalAPI socket unless we
  share it out. "Anything that can reach the LocalAPI" therefore means code execution inside the
  sidecar.
- tsnet also serves the LocalAPI, in process (`Server.LocalClient`). Section 8 admits that a
  compromised `access` can turn on Funnel.
- So "Funnel impossible in code" (the Alternatives table) only protects against *our own*
  misconfiguration. A CI test that rejects any `AllowFunnel` key in the committed serve JSON catches
  that equally well.
- The real Funnel control in both designs is the tailnet policy `funnel` nodeAttr. The ADR leaves
  that to a docs page.

**F3. Nobody owns the cost.** "Under 600 lines" counts only our glue. The binary statically links all
of `tailscale.com`: wireguard-go, gVisor netstack and the full control client.
- **Every Tailscale security bulletin needs a release from us.** That means a Go module bump,
  rebuild, multi-arch build, cosign signature and GHCR push, and then every install running
  `holdfast update`. With the stock image it's a Dependabot bump of a digest.
- **tsnet makes no API stability promise.** The ADR cites `client/local`, the package that replaced
  `client/tailscale`, which is evidence of churn. `tailscale.com` also raises its `go` directive to
  the newest toolchain quickly.
- **No Go reviewer exists in the roster.** Bulwark's checks are written for Python and TypeScript.

Fix: if tsnet survives the spike, the ADR names a CVE owner (Harbor), a response target (for
example, a release within 72 hours of a Tailscale bulletin) and a Go-capable review gate before PA1.
*What would change my mind:* the spike shows that Serve can't emit PROXY v2 from the container, or
that the login URL and status can't reach the wizard without sharing the LocalAPI socket into
another container.

## 2. Fatal: the plan pushes back the Q1 finish line

**F4. The size is larger than Phase 5.** PA1–PA10 is 8 M stories and 2 S stories. At the roundtable
calibration of about 300k tokens per story including review, that's about 3M tokens, roughly 1.5
times Phase 5's whole budget of about 2.0M (`ROADMAP.md`). `STATUS.md` still shows Phase 1.5 as not
started. PLAN already places private access in Phase 8.5, and ROADMAP says side lanes are
preemptible. Nothing in the owner's ask is a Q1 requirement.

**F5. PA10 can't finish without the owner, and two of the three host OSes get no CI.** PA10 needs
owner-provided macOS and Windows hosts, iOS and Android phones, and a second tailnet. The Phase 0
Windows item from 2026-11-19 is still open, which shows these items stall. GitHub's macOS runners
have no Docker, and its Windows runners run only Windows containers. The Docker Desktop paths, two
of the three rows in the step table, therefore run nowhere except owner hardware.

Fix: do S0 and S1 now, and move everything else to Phase 8.5 (section 9).

## 3. Serious: the step count is optimistic, and households aren't counted

**S1. The real minimum.**
- **Irreducible on Linux with Docker (7):** run the installer; sign in to Tailscale and approve;
  turn on HTTPS; phone: install Tailscale; phone: sign in and allow the VPN; phone: open Holdfast;
  create a passkey.
- **Self-inflicted, and removable:**
  - The separate "click Connect" step. `holdfast open` can open the login URL itself.
  - Add to Home Screen. It isn't needed to "open it on my phone", so make it optional.
  - Installing pwsh 7 on Windows. Rule 10 is about dev scripts. A 5.1-compatible bootstrap is a
    product decision, not a rule violation to avoid.
- **Undercounted:** "turn off key expiry" is listed as recommended. Skip it and the phone goes dark
  at day 180, when nobody remembers the setup. It's required, so the honest Linux number is 8.
  macOS adds 2 and Windows adds 2 to 3 plus a likely reboot.
- **Missing entirely:** every *other household member*. Holdfast is household software with
  per-person visibility (ADR 0008). Each member has to accept a tailnet invite (with their own
  identity-provider account), install Tailscale, sign in and accept the Holdfast invite. That's
  about 4 steps per person, and the free plan's user cap (VERIFY) limits household size.

**S2. Dismissed too fast: localhost first.** `http://localhost` is a secure context, so WebAuthn works
there with RP id `localhost`. The installer can open the *app* on the PC. The user creates the
household and a passkey there (or password + TOTP) and has a working product after **1 step past
Docker**, without ever hearing the word Tailscale. "Use it on your phone" then becomes a separate,
optional flow of about 6 steps.

The ADR already adds `webauthn_credential.rp_id`. Use that column to allow a *set* of RP ids
(`localhost` plus the tailnet name) instead of pinning one. The phone's passkey is added from a
signed-in PC session (pairing QR, step-up). The LAN + local-CA option was rightly rejected. This is
a different option, and the ADR didn't consider it.

To verify:
- Safari's handling of `Secure` cookies on `http://localhost`.
- Chrome on Linux has no platform authenticator, so password + TOTP or a hybrid (phone) passkey
  covers that case.

*What would change my mind:* the owner says the phone is the *only* surface and the PC is headless.

## 4. Serious: lockout and recovery

**S3. A pinned single RP id turns ordinary events into lockouts.** In each of these the RP id
changes:
- Docker Desktop's "Clean / Purge data", a common troubleshooting tip.
- `docker compose down -v`.
- The documented restore to a new PC, which excludes `access_state`, done *without* first removing
  the old machine in the admin console. The new node comes up as `holdfast-1`.
- The tailnet is deleted, or the owner's identity-provider account is closed. A new tailnet means a
  new `tailXXXX`, and "rename back" is impossible.

The default user created only a passkey in step 8: no password, no TOTP. Recovery step 2 doesn't
apply to them, so every path ends at `holdfast recover`. That still works while the host and Docker
work, so this isn't permanent data loss. It does make the host CLI the *only* recovery path for a
user the ADR assumes can't use a terminal. With localhost first (S2), the `localhost` credential
never moves and is always available as the recovery path. Fix: adopt S2, or at minimum make enrolling
TOTP or a second factor part of the wizard.

**S4. Reconnect can silently move the node to a different tailnet.** After key expiry, **Reconnect**
opens a login URL. If the browser is signed in to a different identity-provider account (a second
Google account, a work Microsoft account), the node joins *another* tailnet. The phone loses access,
and the recovery token then "rebinds first", following exactly the name that section 3 refuses to
follow automatically. Fix:
- Bind the tailnet identity (tailnet name or ID) at first connect.
- Have `access` or the CLI refuse a reconnect into a different tailnet without a typed
  confirmation.
- The recovery token must not rebind across tailnets.

**S5. The console token is weaker than the recovery token.** `holdfast open` puts the one-hour token
in `http://127.0.0.1:47800/console#…`. Fragments are kept in browser history, and history sync
uploads them to the browser vendor. Fix:
- Make the token single-use. Exchange it on first load for an in-memory value and
  `history.replaceState` the fragment away.
- Shorten the TTL to 10 minutes.
- Show a "hijack check" only after the exchange.

QR 2 shows the setup token on screen. That's fine if it's single-use, because whoever finishes setup
first owns the instance. State that explicitly.

## 5. Serious: security regressions and new failure modes

**S6. The employer or shared tailnet.**
- Step 3 signs in with whatever identity the browser holds. On a PC whose browser is signed in to a
  work Google Workspace or Entra account, the node joins the *employer's* tailnet. Its admins can
  see it, reach it, rename it (forcing `host_mismatch`), and see flow logs on enterprise plans.
  CLAUDE.md's "never on employer" rule is invisible to strangers.
- In a family tailnet, the default allow-all policy exposes `holdfast:443` to every member, and any
  admin can grant Funnel.
- "Confirm the signed-in account" won't stop a non-technical user from clicking OK.

Fix:
- Show the tailnet name and member count.
- Hard-warn, with a typed confirmation, when the tailnet has more than one user or a non-consumer
  login domain.
- Ship the ACL snippet that limits `holdfast:443` to named users.

**S7. Pinned Docker subnets will fail installs.** The `edge` `/29` and `accessctl` networks use
pinned addresses. Pinned ranges collide with home LANs, corporate VPNs and other compose stacks
("Pool overlaps"). For a non-technical user that's a failure with no way forward. Fix: trust by
filesystem instead. `access` writes PROXY v2 to a unix socket on a volume shared only with Caddy
(Caddy can bind `unix//…`). With the stock sidecar, which needs a TCP target, use an unpinned
`internal` network and accept that a compromised `web` container could forge audit IPs. Record that
as accepted risk.

**S8. The Docker version floor adds steps.** The fix for LAN hosts reaching `127.0.0.1`-published
ports is in Docker 28. Distro packages lag: Debian 12's `docker.io` is 20.10 (VERIFY). The installer
either refuses on stock distros, which adds the "install Docker CE from Docker's repo" step to the
Linux row, or it ships the console exposed. The step table assumes neither.

**S9. A control-plane outage plus a reboot may mean no access.** Section 7 says `access` "keeps
serving" through a control-plane outage. That holds only if it never restarts. If a restarted node
can't fetch its netmap without control (VERIFY), a PC reboot or `holdfast update` during a
Tailscale outage cuts off the phone. The Headscale "exit path" isn't zero-config: it needs a public
coordination server and an owned domain. Fix: state honestly that the default experience depends on
Tailscale, and drop "exit path" from the vendor-risk column.

**Minor items.**
- In access mode Caddy receives plaintext on `:8080` and has no site name. It has to reject any
  `Host` other than `bound_host` (421), or invite and alert links can be built from an
  attacker-supplied host.
- `/api/console/*` needs an explicit no-CORS test.
- The peer-count-by-OS feature enumerates every tailnet device, including work laptops on a shared
  tailnet. Drop it and keep "My phone is connected".
- The rewrite of hard rule 12 should merge through the owner digest. It shouldn't be applied on
  acceptance (roundtable ruling C1 / attack 3).

## 6. VERIFY items ranked by blast radius

| Rank | Claim | If false | When |
|---|---|---|---|
| 1 | Serve `--tls-terminated-tcp` + `--proxy-protocol 2`, set through containerboot `TS_SERVE_CONFIG` | Decides tsnet vs stock (F1). Also breaks the manual-path fix | **S0, before accepting** |
| 2 | Re-auth after expiry keeps the node name and IP. Collision-suffix behaviour after state loss | A pinned RP id breaks every 180 days or on any reinstall | **S0** |
| 3 | HTTPS certs need an admin toggle. `QueryFeature` consent link works from tsnet or the container | Step count and the "one click" claim | **S0** |
| 4 | Free plan user and device caps, and terms | Household members (S1). Whole decision | **S0** (read only) |
| 5 | Docker version that fixes 127.0.0.1-publish LAN reachability, compared with distro packages and Docker Desktop | Console exposure or an extra Linux step (S8) | before PA9 |
| 6 | A restarted node works without control | Outage story (S9) | before docs |
| 7 | tsnet log upload can be disabled | Telemetry rule; blocks PA1 only if tsnet is chosen | PA1 |
| 8 | Default policy grants Funnel | Severity of F2. Both designs need the docs fix | PA8 |
| 9 | Caddy `proxy_protocol` `allow` / `fallback_policy` | Fixable by config | PA2 |

The rest (PSL, licence, IdP list, firewall list, Tailnet Lock states, caddy-tailscale) are
low-blast-radius or near-certain. Ranks 1–4 need only a test tailnet and one phone. Owner-queue item
2 is therefore a **prerequisite for S0**, not just for PA10.

## 7. Vendor dependence

- The licence (BSD-3) is fine for AGPL with either design.
- The real risk is product risk. The free Personal plan's terms and user cap have changed before.
  If they change again, *every* default install degrades at once, and nobody has a migration path.
- Fix:
  - Record a named trigger in the ADR, for example "Personal plan drops below 3 users or requires
    payment".
  - Keep S2's localhost path as the always-works floor, so a vendor change removes only phone
    access, never the app.

## 8. Smallest slice worth building now

- **S0 (S, Harbor + Sounding, about 1 run):** spike on a throwaway tailnet. Verify ranks 1–4 with
  the stock container and record the results in the ADR. No product code.
- **S1 (S, Harbor):** a Caddy PROXY listener on loopback plus the owner's host-Serve command. This
  fixes the collapsed-IP bug on the one install that exists today. The `client-ip-check.md` row
  goes with it.
- **Phase 8.5:** everything else, rescoped per section 9. PA6 (recovery), PA9 (installer) and PA10
  (acceptance) stay. PA1 and PA4 shrink to a compose service plus host CLI verbs (`holdfast
  connect` reads `AuthURL` and `Self.DNSName` through `docker compose exec`, so there's no
  `accessctl` network or control token).

## 9. Recommended decision

**Reject in favour of: "stock sidecar, localhost first, multiple RP ids", built in Phase 8.5.**

1. The app opens on `http://localhost` at install. That's the first passkey and the permanent
   recovery path.
2. Phone access is optional. It uses a digest-pinned `tailscale/tailscale` container in userspace
   with a read-only `TS_SERVE_CONFIG` (TLS-terminated TCP + PROXY v2 to Caddy). The LocalAPI socket
   is never shared, and a CI test rejects `AllowFunnel`.
3. `webauthn_credential.rp_id` is allowed from a set of RP ids, not pinned. The phone passkey is
   added from a signed-in PC session.
4. The tailnet identity is bound at first connect. Crossing tailnets needs a typed confirmation, and
   `holdfast recover` can't rebind across them.
5. The step table counts key expiry as required, includes per-member onboarding and drops the pwsh 7
   step through a bootstrap.

I'd **accept tsnet** only if S0 shows rank 1 is false and the ADR adds a named CVE owner, a
response target and a Go review gate.
