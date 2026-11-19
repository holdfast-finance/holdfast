# Security policy

Holdfast is pre-alpha. Don't run it with real financial data on an internet-exposed host.

## Reporting a vulnerability
Please report privately, not in a public issue. Use GitHub's "Report a vulnerability" (private advisory) on this repository once it's public. We aim to acknowledge reports within 72 hours.

## Design commitments
- No public ports: access is through Tailscale (or your own WireGuard).
- Bank tokens, exchange keys and xpubs are encrypted at rest. SSNs and full account numbers are never stored.
- The server never holds crypto private keys or seed phrases.
- AI providers receive redacted data through read-only tools. Every call is visible to the user.
- No telemetry by default.
