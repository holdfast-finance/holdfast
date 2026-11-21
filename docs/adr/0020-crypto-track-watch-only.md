# 0020. Crypto Track (watch-only): chain data, libraries, schema

- Status: Proposed (Cipher and Bulwark review pending)
- Date: 2026-11-21
- Deciders: Tide (crypto lane); Keel, Lattice, Cipher and Bulwark to confirm

## Context
PLAN 18 puts crypto next to bank, card and retirement accounts in one net worth. Phase 10a (Track) is
watch-only: Bitcoin xpubs and descriptors, EVM addresses, exchange balances, price history. Hard rule 11
(and `.claude/rules/crypto.md`) says the server never accepts, stores, logs or transmits a seed phrase or
a private key. Hard rule 9 says a new external service needs an ADR. PLAN 18.3 prefers the user's own node
and calls public APIs a privacy cost, because the provider learns the user's addresses. Phase 10a also
must not depend on `FEATURE_CRYPTO_SPEND`, which stays false. Spending (PSBT, signing) is Phase 10c.

## Decision

### 1. Public keys only, enforced in code, tests and the database
- One pure guard (`app.domain.crypto.guard`) runs before any parser, validator or free-text field that
  can hold an xpub, descriptor, address or label. It rejects extended private keys (`xprv`, `yprv`, `zprv`,
  `Yprv`, `Zprv`, `tprv`, `uprv`, `vprv`, `Uprv`, `Vprv`, and any `...prv` + base58 body, also inside a
  descriptor), WIF keys, 64-hex private keys, and BIP39-looking word lists (English list, plus any run of
  eleven or more alphabetic words). The error is a fixed string and never echoes the input.
- A test walks `backend/app/**/crypto*` and `app/integrations/crypto/**` and fails on identifiers that name
  secret material (`mnemonic`, `seed`, `xprv`, `private_key`, `wif`) used as an input, parameter or field.
- `crypto_wallet.is_watch_only` carries `CHECK (is_watch_only)`: the database cannot hold a spend wallet
  until a 10c migration deliberately relaxes it. `exchange_account.permissions` may not say `trade` or
  `withdraw` is true (CHECK), so a trade-capable key cannot be recorded as safe.
- Spending, PSBT construction, signing, `Signer`, and any dependency on `FEATURE_CRYPTO_SPEND` are out
  of scope for this ADR.

### 2. Chain data: your own node first, nothing public by default
| Chain | Supported source (10a) | Notes |
|---|---|---|
| Bitcoin | **Electrum protocol** server you run (electrs or Fulcrum), over TCP or TLS | `blockchain.scripthash.get_history`, `get_balance`. Own small async client: the protocol is newline-delimited JSON-RPC, so no new library |
| EVM (Ethereum, Base, Arbitrum, Optimism, Polygon) | **JSON-RPC endpoint you run** (HTTP) | Balance by `eth_getBalance`. ERC-20 history by `eth_getLogs` on the indexed `Transfer` topics. Native history needs `trace_filter` (Erigon, Reth, Nethermind); a node without it reports native history as unavailable instead of guessing |

- A `ChainSource` port (balances and transaction history for a set of addresses) sits in
  `app.integrations.crypto.chains`. The real adapters are constructed **only** from an endpoint the
  household configured. There is no default URL in code, `.env.example` or tests.
- Endpoint config lives in `household_setting` under `crypto.endpoint.<chain>` as an encrypted value (an
  endpoint URL can carry an API key). No code path reads a public endpoint unless
  `allow_public = true` is set for that chain, and the API/UI must show the privacy warning ("this provider
  will learn every address in this wallet") before saving it. Public fallbacks themselves (Esplora,
  mempool.space, an Etherscan-compatible API) are **deferred**.
- **Deferred sources:** Bitcoin Core RPC, self-hosted mempool/Esplora over HTTP, Blockscout, Solana and
  other chains (plugin connectors, PLAN 18.3). Adding one is an adapter behind the same port.
- Tests use an in-memory fake `ChainSource` and recorded, sanitized JSON. The test network guard stays on.

### 3. Libraries
| Need | Choice | Licence | Why |
|---|---|---|---|
| Bitcoin: BIP32 public derivation, xpub/ypub/zpub, descriptors (`wpkh`, `sh(wpkh)`, `tr`, `wsh(sortedmulti)`), address encoding | **embit** 0.8.0 | MIT | Zero runtime dependencies, parses and checksums descriptors, covers all four script types, public-key only code path for our use. Hand-rolling EC math or Bech32m is the thing to avoid |
| EVM EIP-55 checksum (needs Keccak-256; `hashlib.sha3_256` is NIST SHA-3, not Keccak) | **pycryptodome** | BSD-2-Clause / public domain | Maintained, widely audited, one hash call. Alternative `eth-utils` pulls `eth-typing`, `eth-hash`, `cytoolz` for one function |
| Exchanges (later slice) | **ccxt** | MIT | See below |

AGPL-3.0 can include MIT and BSD code. **embit's maintenance risk:** the last release is 0.8.0 (2024-05-30).
The behaviour we use (BIP32, BIP84/86 vectors, descriptor grammar) is frozen, we test against published BIP49,
BIP84 and BIP86 vectors, and all use is confined to `app/domain/crypto/bitcoin.py` behind our own value types, so
replacing it with `bip-utils` (MIT, releases to 2026-10, but eight runtime dependencies including ed25519,
sr25519 and TON packages) is a one-module change. Revisit if a vulnerability or a new script type lands.

**CCXT** (version 4.5.93, released 2026-11-17, about 1,600 releases since 2017, MIT): maintained and
licence-compatible. Costs: it is large, and it pins exact transitive versions (`typing_extensions==4.16.0`,
`cffi==2.0.0`, `aiohttp<3.15`, `cryptography>=50`, `uvloop`, `orjson`, `coincurve`), which can conflict with our
own pins. It is **not added in this run**. When the exchange slice lands it goes into an optional extra
(`exchanges`) so a bank-only install is not forced to carry it, behind an adapter that exposes only
`fetch_balance`, `fetch_my_trades`, `fetch_deposits` and `fetch_withdrawals`, after a read-only permission
check (hard rule 11, PLAN 18.0).

### 4. Price history
Prices are a port (`PriceSource`) with a recorded daily close per asset, date and source, stored in
`price_history`. The first adapter will go through CCXT's public (keyless) OHLC endpoints, so there is no new
SDK, and a lookup reveals only an asset ticker and a date, never an address or a balance. It is **off until
the household enables it** and no URL is called by default. The exact venue is chosen when that slice is built
and its pagination limits are checked against recorded fixtures. CSV import of closes is the fallback. A
second, different provider is not added without another ADR.

### 5. Schema (migration `0007_crypto_track`)
Tables: `crypto_wallet`, `crypto_address`, `crypto_tx`, `exchange_account`, `price_history`. Lots and
disposals are Phase 10b.

- Every table has `household_id`, ENABLE + FORCE RLS and the standard `household_isolation` policy, and
  composite foreign keys that include `household_id` (ADR 0008).
- **Per-person visibility** follows `account`: `crypto_wallet` and `exchange_account` carry `owner_person_id`
  (NULL = joint) and `visibility`, with RESTRICTIVE read and write policies. `crypto_address` and `crypto_tx`
  inherit through EXISTS on their wallet or exchange account (`summary_only` viewers see the wallet row, not
  its addresses or transactions).
- **Quantities are `NUMERIC(38,18)`.** 18 decimals is what an EVM wei needs, and the 20 integer digits cover
  every real balance. The money rule's `NUMERIC(28,10)` cannot hold 18 decimals. Bitcoin (8 decimals) fits.
  A value that does not fit is rejected in the service with an error, not rounded. Prices are
  **`NUMERIC(28,12)`**: sub-cent tokens lose too much at `NUMERIC(18,8)` once multiplied by a large quantity.
  Both are new numeric kinds and `money.md` should be amended to list them (Keel).
- **Sign:** `crypto_tx.quantity` is signed (inflow positive, outflow negative, hard rule 1), with
  `direction` as a CHECKed redundancy. Fees are non-negative magnitudes (`fee_qty`), like
  `investment_txn.fees`.
- **Encrypted at rest:** `crypto_wallet.descriptor_enc` holds the canonical xpub or descriptor (it reveals
  the wallet's full history) and `exchange_account.{api_key,api_secret,passphrase}_enc` the exchange credential.
  A separate `identity_hash` (SHA-256 of the canonical descriptor or address) gives a per-household unique key
  without decrypting, because Fernet ciphertexts are not comparable. Addresses are stored in plaintext because
  they are looked up and sent to the chain source; they are public data, and the same history is visible in
  `crypto_tx` regardless.
- **Idempotent transactions:** `UNIQUE (wallet_id, chain, tx_hash, address, direction, asset,
  asset_contract, event_index)`. `event_index` (output index or log index) and the asset are part of the key
  because one transaction can pay the same address twice or move two tokens. Exchange rows are keyed by
  `(exchange_account_id, external_id)`.
- **Reconciliation, the crypto form of hard rule 6:** a sync checks that the sum of a wallet's signed
  quantities per asset equals the chain source's reported balance (fees included) and otherwise sets the
  wallet `status = 'needs_review'`. An unreconciled wallet is never silently accepted.
- `price_history` is **household-scoped** (primary key `household_id, asset, date, source`), not global
  reference data. A global table would let one household write closes every other household's basis rests on.
  The cost is duplicated rows.
- **Ciphertext today is a bare Fernet token** (`app.security.crypto.encrypt`), because the `hf1` envelope of
  ADR 0015 is not on `main` yet. All writes go through one helper so the switch is one place. ADR 0015
  section 2 lists exactly three rotated columns: the four new columns above must be added to the rotator
  (a Harbor / protected-path change) before a key rotation is run against a database that has crypto data.

### 6. What the services guarantee
Create a wallet (validate, derive addresses to the gap limit, encrypt, audit-log), sync through a
`ChainSource` (Decimal end to end, idempotent upsert, reconcile), list and delete. Audit entries record the
wallet id, kind, chain and counts, never a key, descriptor or address list.

### Deferred (recorded, not forgotten)
Lots, disposals and 1099-DA (10b); `Signer`, PSBT and spend descriptors (10c); EVM send (10d); Bitcoin Core
RPC, Esplora/mempool, Blockscout and public fallbacks; Solana; CCXT adapter; transfer matching; crypto in
`v_net_worth_daily`; price adapter; CSV importers; `signer_type`; the web feature; rotator support for the new
ciphertext columns; the `hf1` envelope.

## Alternatives considered
- `bip-utils` for derivation: maintained, but no descriptors and a heavy native/ed25519/TON dependency tree for a BTC-only job.
- `bitcoinlib` (GPL-3.0), `python-bitcointx` (LGPL, needs libsecp256k1): licence or packaging cost for no gain.
- Hand-rolled BIP32/Bech32m/Taproot tweak: exactly the code we should not own.
- A default public Esplora or RPC endpoint "so it works out of the box": leaks every address to a third party. Rejected; opt-in only.
- Global `price_history`: simpler and smaller, but it is a shared write target across households.
- One `xpub_enc` plus a plaintext `descriptor` (PLAN 18.5 as drawn): a descriptor contains the xpub, so the plaintext column would defeat the encryption.
- `NUMERIC(28,10)` for crypto quantities (current money rule): drops 8 of 18 EVM decimals. A silent rounding is the failure to avoid.

## Consequences
- 10a ships no network call by default; a household with no node configured has watch-only addresses and manual entry only.
- EVM native history needs a trace-capable node or a later indexer adapter. The port reports "unavailable", which the UI must say plainly.
- `embit` and `pycryptodome` join the runtime dependencies (`uv.lock` changes); `httpx` moves from dev to runtime for the EVM adapter.
- Follow-ups: amend `.claude/rules/money.md`; Harbor adds the four ciphertext columns to `make rotate-keys` and the `hf1` envelope; Cipher audits the guard and the no-key-material test.
