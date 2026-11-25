# 0042. Alerts are evaluated per person, delivered in-app and by email, with minimal email content by default

- Status: Accepted (Keel, 2026-11-24)
- Date: 2026-11-24
- Deciders: Lattice (architect); Keel

## Context
PLAN 8.8 lists alert kinds and delivers them "by email and push (ntfy)". Three forces apply:

- **Visibility.** A household-wide job that sees every account would tell Sam about a large charge
  on Alex's private card. That breaks ADR 0008 as surely as a leaky query.
- **Data leaving the instance.** An email passes through whatever SMTP relay the household
  configured. The security rules treat data leaving the instance as sensitive: exports need
  re-auth, and telemetry carries no financial data.
- **ntfy is an external service.** Hard rule 9 requires an ADR, and the Ledger memo and ROADMAP B4
  both say so. A public ntfy server also exposes the topic name and the message.

## Decision
1. **Per-person evaluation.** The job `insights.refresh` opens one `scoped_session(household,
   person)` for each person with a login. It evaluates that person's rules only over what that
   person can see, and stores events with `person_id`. The `alert_event` read policy also requires
   the event's account (if any) to still be detail-visible, so a later visibility change hides old
   events too (ADR 0026 point 2).
2. **Cooldown and dedupe in the database.** Each candidate carries a deterministic `dedupe_key`.
   A kind is either one-shot (once per key, forever) or has a cooldown (once per key per window).
   The job runs under the ADR 0016 per-household lock, so evaluation is serialised.
3. **Channels:**
   - Every event is always in-app.
   - Email is opt-in per person and goes to that person's login email. The SMTP relay is set in
     `.env`. STARTTLS (or implicit TLS on 465) is required, and there is no plaintext mode.
   - **No ntfy in Phase 5.** It needs its own ADR first (self-hosted container or a public server,
     topic secrecy, payload content).
4. **Minimal email content by default.** The default email says "You have N new alerts in
   Holdfast" with a link to the instance, and carries no amounts, merchants, account names or
   masks. A person may opt into `full` detail (alert titles, no masks ever). That needs a fresh
   re-auth (`Principal.reauth_fresh`, as for exports), because it sends financial data off the
   instance.
5. **No history replay.** On a person's first evaluation, the watermark is set to "now".
   Transaction-level kinds only consider rows created after the watermark and posted within the
   last 7 days, so importing five years of statements doesn't send five years of alerts.

## Alternatives considered
- **A household-wide evaluation, with an audience filter on delivery:** this keeps the privacy
  rule in application code, the place it's most easily missed.
- **Full email content by default:** friendlier, but it pushes financial data through a third-party
  relay without the person choosing it.
- **ntfy now, using the `NTFY_*` settings already in config:** hard rule 9.
- **`ALERT_EMAIL` as the single recipient:** that gives one inbox for two people with private
  accounts. The variable stays in config but unused (Phase 5 contract OQ-13).

## Consequences
- Each person's rules and preferences are separate rows. Defaults live in
  `app/domain/alerts.py`, so a missing row means the default.
- Email delivery runs after commit and is retried by the next run (3 attempts at most). Failures
  are logged by exception class only, and `SMTP_PASS` never reaches a log.
- A later push channel adds a column value and a transport, not a new evaluation path.
- Credit alerts (a new inquiry or tradeline) arrive with Phase 6 as new kinds on the same engine.
