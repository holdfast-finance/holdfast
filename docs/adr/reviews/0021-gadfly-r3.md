# Gadfly critique: ADR 0021 revision 3

- Target: `docs/adr/0021-zero-config-private-access.md` at `bb18f004424b7d520ca22b90ef5b24cda6a7b093`
  (branch `docs/adr-zero-config-access`, PR #51)
- Inputs: `0021-pa0-spike.md`, my earlier `0021-gadfly.md`, CLAUDE.md hard rules 9 and 12, and the current
  `docker-compose.yml`, `backend/app/security/client_ip.py`, `ratelimit.py`, `services/auth.py` and `services/setup.py`
- Question: is this safe to merge as a **Proposed** record? It doesn't have to be ready to build.
- Verdict: **REWORK (small).** The work is text only, about one run, and needs no new spike. The design is
  sound. The problem is that three statements contradict each other or are asserted without a U-number,
  and two of them sit on the rule 12 and lockout paths. A Proposed ADR can carry open assumptions, but it
  can't carry a state machine that contradicts itself.

Severity: **blocking** means fix it before merge. **Should** means fix it in this revision if it's cheap,
otherwise give it a U-number. **Minor** means fix it while building.

## Blocking

**B1. The wrapper's state machine contradicts itself (§1 "Off until the owner turns it on", phases 3 and 4).**
Phase 3 says the wrapper "`exec`s `containerboot`". After an `exec` there's no wrapper process left, yet
the same section says "if `containerboot` exits, the wrapper returns to phase 2", and phase 4 says the
wrapper notices the flag is gone and "stops `containerboot`". Nothing would be watching the flag, so
Turn off (§6 Revocation, step-up) would do nothing until the next restart. That's a revocation control
that silently fails.
Fix: in phase 3, `containerboot` runs as a child (`& wait`). The wrapper watches the flag, traps and
forwards `SIGTERM`, and compose sets `init: true`. Drop "about 40 lines" from Consequences or re-estimate it.

**B2. "`containerboot` exited" is treated as "needs sign-in". That re-logs-in during an outage, which §9
forbids (§1 phase 3, §9 "Control-plane outage", §9 "Key expiry").** This is unmarked and the spike didn't
run it:
- Check 12 restarted the node during an outage and saw `Running` with 0 peers. It didn't record whether
  `containerboot` exited at its 60 s `tailscale up` deadline. If it does exit, the wrapper drops to phase 2
  and runs an interactive `tailscale login`.
- So a Tailscale outage plus a reboot turns into a fresh login URL. The same can happen with HTTPS off,
  if a failed `TerminateTLS` cert fetch makes `containerboot` exit.
- On a node that already has a profile, `tailscale login` may create a **new profile and node identity**
  rather than re-authenticate. That gives `holdfast-1`, or a different tailnet if the wrong browser account
  completes it. It also contradicts §9's "never offers re-auth there, because a new login during an
  outage could switch tailnets".
- "Key expiry ends up here" (phase 3) is also unverified.
- And how phase 0 decides "signed-in state" before `tailscaled` runs isn't specified. A logged-out state
  file from phase 4 isn't a signed-in state.

Fix:
- Enter phase 2 only when `BackendState == NeedsLogin` **and** control is reachable (the U-11 signal).
  Otherwise back off and retry phase 3.
- Use `tailscale up` with no timeout for re-auth, not `login`.
- Add U-12: "`containerboot` exit behaviour on outage, expiry and cert failure; `login` vs `up` on an
  existing profile".

**B3. The forged-IP accepted risk understates both the path and the impact (§1 "Accepted risk", §4, §9
threat model).** The ADR names one forgery path, a PROXY header sent to Caddy's tailnet listener. There's
a wider one already: `TRUSTED_PROXIES` defaults to the whole `frontend` /24
(`docker-compose.yml:219-224`). `web` sits on `frontend`, so it can call `api:8000` directly with any
`X-Forwarded-For` and `X-Holdfast-Ingress`. That bypasses Caddy, the 1 MB cap and the ingress tagging.
What depends on the client IP:
- **Passkey login and setup are limited by IP scope only** (`auth.py:335`, `setup.py:74`). Forged IPs give
  a fresh 20-attempt budget each time. Forging the phone's tailnet address gives a **targeted lockout**,
  escalating up to 1 h (`ratelimit.py` `MAX_LOCK_SECONDS`).
- **Audit IPs** can be forged (repudiation).
- On the local ingress, every PC client shares one bucket (the Docker gateway). Any host process, or a LAN
  neighbour below the Docker floor, can lock the owner out of passkey login on the "never changes"
  localhost floor.

Fix:
- State all three impacts in §1.
- Take `web` off any network that `api` shares with Caddy: `api` and Caddy get their own internal network.
- Narrow `TRUSTED_PROXIES` to Caddy. It's unpinned, so resolve the `caddy` name at startup or use a
  Caddy-injected shared secret.
- Move this from PA-D10 to PA1, because PA1 is what puts phones' IPs into the buckets.
- Give the local ingress a separate, higher threshold, or an account-plus-ingress scope.
- Correct §4's "A per-account rate-limit scope already exists for password and TOTP": passkey and setup
  have none.

**B4. Rule 12: "the sidecar accepts no other serve config" is overstated (§2 checks, §8 "can't publish
it").** The read-only mount protects only the **file**. Serve config lives in `tailscaled` state and can be
written through LocalAPI by root or the daemon's UID. That means the wrapper itself, or any
`docker compose exec tailscale tailscale funnel 443` a stranger copies from a Tailscale tutorial. The
persisted config also survives into phase 2's bare `tailscaled`. PA0 4 showed only that **`api`** can't
write. My earlier F2 point, that the real Funnel control is the tailnet `funnel` nodeAttr, is still
docs-only (VERIFY). Fix:
- Add a runtime tripwire. `GET /localapi/v0/serve-config` is readable (PA0 4), so the status client reads
  it. On any difference from the committed config, or any `AllowFunnel`, it removes the flag, alerts and
  audits.
- CI greps the wrapper for `funnel`, `serve`, `set`, `--operator` and `--advertise`.
- Reword §2 and §8 to "no shipped path writes serve config; a runtime check catches any that does".

## Should

**S1. The UID rule leaves out the operator, and CI only covers compose (§6, §1 CI list, Consequences).**
- PA0 4 caveat 1 lists three UIDs with write access: root, the daemon's UID **and the configured
  operator**. §6 lists two, and the `TS_EXTRA_ARGS` denylist doesn't forbid `--operator`. Fix: add it to
  the denylist and the wrapper grep.
- CI can't see `docker compose exec -u 0 api …` or a non-numeric `user:`. Fix: require numeric `user:`.
  `api` also asserts at startup that `getuid() != 0` and `!= stat(socket dir).st_uid`, then sends one
  denied probe (wrong-method `POST logout`, which must return `403`). If any check fails, it refuses to
  use the socket. That verifies the gate at runtime and covers U-4 (rootless Docker) without a spike.

**S2. Rule 9: Let's Encrypt isn't named (Context "Hard rule 9", §9 egress list).** PA0 7 found
`acme-account.key.pem` in the state volume. `tailscaled` runs its own ACME account against Let's Encrypt,
which is a third party, not "Tailscale's certificate services", and it publishes to CT. Fix: name ISRG/Let's
Encrypt ACME as an external dependency and add it to the egress list. `tailscale-init` and the two networks
are covered (§1). One small thing: `chmod 0700` has to run before `chown`, otherwise it needs `CAP_FOWNER`.

**S3. Acceptance gating deadlocks with rule 9 (§12, §13 PA1).** §12 says the ADR becomes Accepted only
when **all** of U-1 to U-11 pass. U-6 to U-10 need owner hardware, and that queue already stalls (F5). The
ADR doesn't say whether PA1, the new container, may merge while the ADR is still Proposed. Fix: split the
acceptance. "Accepted for the sidecar overlay" needs U-1 to U-5, U-11 and U-12. The browser and tailnet
items (U-6 to U-10) gate only PA-D2, PA-D3 and PA-D8. Say explicitly that PA1 doesn't merge before the
sidecar acceptance.

**S4. Refusing below the Docker floor is the right call, but the ADR doesn't define its blast radius
(Consequences, §7(a)).**
- **Keep the refusal.** Below the floor the plain-HTTP login is exposed to the LAN.
- **Name the upgrade path:** Docker's apt repository, linked from the docs and printed by the installer.
- **Name who has no path at all:** Synology, QNAP, Unraid and TrueNAS ship vendor Docker builds that users
  can't upgrade. Send them to the manual path explicitly. Raspberry Pi OS follows Debian.
- **Never let the refusal block data egress.** It should stop `up`, not `recover`, `backup` or `export`,
  so that Docker regressing after an OS reinstall can't strand data.
- **Admit that a numeric compare can't see distro backports.** It refuses a patched `docker.io`. Record
  that as accepted.
- **Ask whether the floor applies beyond zero-config.** The 28.0.0 fix also covers direct routing to a
  container on its published ports, so it applies to the prod and manual compose too. Say whether it does.

**S5. The outage messaging is only half there (§9).** The PC can still reach the app on localhost, and the
ADR says so. But the phone's offline shell says "Is Tailscale on?", which blames the user during a
Tailscale outage. Fix: change the copy to "Can't reach Holdfast. Your Tailscale connection or Tailscale's
service may be down." Also add a Docker daemon restart (for example, unattended upgrades of `docker-ce`) to
the list of things that restart the sidecar.

**S6. The "Turn on HTTPS" stop has no in-app exit (§9 "HTTPS off", U-9).** If serving needs a sidecar
restart after HTTPS is turned on, `api` has no lever except the flag, and toggling the flag logs the node
**out**. Fix: the wrapper restarts its `containerboot` child when `CertDomains` goes from empty to set. That
needs B1's child model. Otherwise state the host command that's required.

## Minor

- The §8 "organization domain" heuristic for the typed confirmation isn't defined against what
  `CurrentTailnet.Name` actually contains. Define it in PA-D3.
- `AuthURL` is a bearer capability: whoever completes it owns the node's tailnet membership. Show it only
  to an owner session with step-up.

## Earlier objections

Nothing was dropped silently. F2's nodeAttr point is still docs-only, so B4 covers it again. My earlier
S7 fix said to record the forged-IP risk, and B3 says the recorded version is incomplete. The rest are
answered in §10.

## Private details

Clean. Only placeholders appear (`tail0000.ts.net`, `100.x.x.x`, `100.64.0.0/10`). `192.200.0.0/24` in the
spike file is Tailscale's public control range.

## Weakest assumption

**B2:** that "`containerboot` exited" means "needs sign-in". It's unlisted, the spike didn't run it, and
it turns an outage or a cert failure into a re-login that can change the node's identity or tailnet.
