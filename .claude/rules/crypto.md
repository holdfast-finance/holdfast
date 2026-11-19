---
paths:
  - "backend/app/integrations/crypto/**"
  - "web/src/features/crypto/**"
  - "backend/tests/**/test_crypto*"
---
# Crypto + self-custody

- The server never sees private keys or seed phrases. No input anywhere accepts 12/24 words. If you're about to add a field, parser or log line that could hold one, stop.
- Store only xpubs, output descriptors, fingerprints, derivation paths and addresses. Encrypt xpubs at rest (they reveal full history).
- Signing is client-side: `Signer` interface (`get_xpub`, `display_address`, `sign_psbt`). Build QR (UR/BBQr) and file-PSBT signers first, then WebUSB. Never server-side USB/HWI.
- Spending is `FEATURE_CRYPTO_SPEND=false` by default, self-host only, and a hosted build stays watch-only permanently. Passkey re-auth before building a PSBT. Never auto-sign. Never persist signed-but-unbroadcast transactions past the session.
- Exchange keys: reject keys with trade/withdraw permissions where the API reports them, and show a blocking warning otherwise.
- Cost basis is per wallet / per exchange account (FIFO or specific ID, recorded per year). Transfer matching (withdrawal ↔ deposit) comes before any basis calculation.
- Prefer the user's own node (Bitcoin Core / electrs / Fulcrum / mempool). Public APIs require a visible privacy warning.
- `crypto-custody-auditor` must pass before merging anything in these paths.
