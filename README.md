<p align="center"><img src="brand/app-icon.svg" width="96" height="96" alt="holdfast logo"></p>

# holdfast

**Every account. One root. Your server.**

Holdfast is an open-source, self-hosted personal finance app. Banks, cards, 401(k), HSA,
brokerage and crypto live in one place, with sync health you can see, full history imported from
statements, honest forecasts that publish their own error, and a private AI advisor that runs on
the model you choose (Claude, a local Ollama model, or any OpenAI-compatible endpoint).

> Status: **v0.0.0-alpha, a foundation release.** Auth, households and the data model work. Bank sync, import, budgets, forecasts, credit, tax, the AI advisor and crypto are not built yet. See [release notes](docs/releases/v0.0.0-alpha.md) and [`docs/STATUS.md`](docs/STATUS.md).

## Why

- **One app, not three.** Budgets, investments, net worth and crypto together, with real US bank sync (Plaid with your own free keys, or SimpleFIN).
- **Sync you can trust.** Per-account status, stale-data warnings, one-click relink and duplicate cleanup.
- **Your whole history.** Statement PDFs reconciled to the penny, plus Mint/YNAB/Monarch/Copilot importers.
- **Private AI.** Redacted, read-only tools, every call visible.
- **Self-custody.** Hardware-wallet watch-only tracking, and optional Bitcoin signing on your own device. The server never sees your keys.
- **Taxes, year-round** (optional self-host module): relief scanner, crypto basis per wallet, and a filing-ready package you file yourself.

## Self-host

The alpha runs as a dev stack with synthetic data only. Don't use real financial data yet.

```sh
make keys && make up && make migrate   # Windows: ./scripts/dev.ps1 keys | up | migrate
```

Then open https://localhost (self-signed in dev). There is no upgrade path before beta. Artifacts,
checksums and limits are in the [release notes](docs/releases/v0.0.0-alpha.md).

Private access over Tailscale and PWA install are planned, not shipped.

## Docs

- [PLAN.md](PLAN.md): full build spec
- [docs/STATUS.md](docs/STATUS.md): phase progress
- [docs/adr/](docs/adr/): architecture decisions
- [docs/brand/BRAND.md](docs/brand/BRAND.md): brand system
- [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md)

## License

[AGPL-3.0](LICENSE). Not a bank. Not a CPA. Your software, your data.
