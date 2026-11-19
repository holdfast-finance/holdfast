# Holdfast brand system

> Every account. One root. Your server.

Chosen direction: **Direction 01, Futuristic / Decentralized** (ADR 0003). The source boards are
in `Holdfast_Brand_Direction.pdf`: pages 1–4 are Direction 01, 5–8 are Direction B (Terminal /
Brutalist) and 9–12 are Direction C (Holo). B and C are kept for reference. Don't mix them in.

## Logomark

A holdfast is the root that anchors kelp against the current. The mark is a single asset node, one
stem and three roots: everything you own, anchored to hardware you control.

- App icon: Signal→Deepwater gradient tile with the mark in Abyss.
- On light: the mark in Abyss on a Cloud/white tile.
- Glow (dark UI, favicon): the mark in Signal on Hull, with a soft Signal glow.
- Wordmark: "holdfast", lowercase, Space Grotesk 600.

SVG sources go in `brand/` (created in Phase 0). The PWA icons are generated from them.

## Color tokens

| Token | Hex | Role |
|---|---|---|
| `--signal` | `#3DF5C8` | Primary, CTAs, positive, "Synced", focus ring, chart actuals |
| `--deepwater` | `#7B61FF` | Secondary, forecast bands/projections, "Watch-only", AI accents |
| `--abyss` | `#07080C` | Page ground (dark) |
| `--hull` | `#0E1118` | Surfaces / cards (dark) |
| `--flare` | `#FFB547` | Warning, "Relink needed", budget pace high |
| `--breach` | `#FF5D7A` | Negative amounts, errors, over-budget |

Derived (define as CSS variables, not ad hoc):

| Token | Value | Use |
|---|---|---|
| `--hull-raised` | Hull + 4% white | Hover, popovers |
| `--hairline` | white @ 8% | Borders, table rules |
| `--text` | `#E8ECF4` | Primary text on dark |
| `--text-muted` | `#8A93A6` | Labels, captions |
| `--glass` | Hull @ 70% + 12px backdrop blur + hairline | Glass cards (Dashboard only) |

**Light theme** (follows the system setting, per PLAN 11.1): ground `#F7F8FB`, surface `#FFFFFF`,
text `#0E1118`. Light fills: Signal `#0FB894`, Deepwater `#5B41E0`. Dark is the hero theme:
marketing, screenshots and the demo use it.

### Fill vs. text tokens (AA rule, Phase 0 review)

Brand hues are **fills**: dots, bars, buttons, chart lines and chip tints. Colored **text** always
uses the separate `*-text` tokens. Most fills fail 4.5:1 when used as text, especially on white.

| Text token | Light | Dark | Worst measured contrast |
|---|---|---|---|
| `--signal-text` | `#09745D` | `#3DF5C8` | 4.83 (light chip) |
| `--flare-text` | `#8B5E1B` | `#FFB547` | 4.86 (light chip) |
| `--breach-text` | `#BF2945` | `#FF5D7A` | 4.72 (light chip) |
| `--deepwater-text` | `#5B41E0` | `#8B74FF` | 4.67 (dark chip) |
| `--focus-ring` (≥ 3:1) | `#0B8A6F` | `#3DF5C8` | 4.05 |

The source of truth is `web/src/lib/tokens.ts`, and `web/src/index.css` mirrors it. The test
`web/src/tokens.node.test.ts` fails the build if any pair drops below 4.5:1 (3:1 for the ring).
Never darken the Signal **fill** to fix text: Abyss text on a Signal button is 7.9:1 or better.

Map tokens into shadcn's CSS variables (`--primary` = signal, `--secondary` = deepwater,
`--destructive` = breach, `--background` = abyss, `--card` = hull, `--ring` = signal) in
`web/src/index.css`, and expose them as Tailwind v4 `@theme` colors.

## Typography

| Use | Face | Notes |
|---|---|---|
| Display & headings | **Space Grotesk 600** | Tight tracking (-0.02em) on large sizes |
| Body & UI | **Inter 400/500** | |
| All money, balances, quantities, codes, CLI | **JetBrains Mono** | `font-variant-numeric: tabular-nums` always |

Self-host all fonts (`@fontsource/*`). The app never calls Google Fonts.

## UI atoms

- **Primary button:** Signal fill, Abyss text, soft Signal glow on hover. Example: "Self-host now".
- **Secondary button:** Hull fill, hairline border, Text. Example: "View demo".
- **Status chips:** a dot (fill token) plus a label (`*-text` token) on a tinted pill. `● Synced` (Signal), `● Relink needed` (Flare), `● Watch-only` (Deepwater), `● Error` (Breach). Never color alone.
- **Code block:** Abyss fill, hairline border, mono, with the command keyword in Signal (`docker compose up -d holdfast`).
- **Glass card:** the `--glass` recipe with a subtle Signal→Deepwater radial in one corner. Use it on the Dashboard hero and the Advisor card only.
- **Charts:** actuals are a Signal line with a soft area fill. Forecasts are a dashed Deepwater P50 with a Deepwater 20% P10–P90 band. Gridlines are hairline. A "Today" marker sits at the actual/forecast join.
- **Budget bars:** Signal under pace, Flare when pace is high (≥ 90% or projected > 110%), Breach when over. Deepwater is for subscriptions.
- **Radius:** 12px on cards, 10px on buttons, full on chips.

## Voice

"Hold fast." Builder-to-builder: short, confident, a little cypherpunk. Talk about ownership,
not features. Never fear-sell.

- Every account. One root.
- Your money. Your keys. Your server.
- Self-custody for your whole financial life.
- Footer line: "Not a bank. Not a CPA. Your software, your data."

Don't use exclamation marks in product UI, urgency or scare copy about breaches, the word "CPA"
as a claim, or crypto-bro hype.

## Reference layouts (from the board)

- **Site hero:** "Every account. / One root. **Your server.**" (the last phrase in Signal), a subhead, a
  primary and a secondary CTA, a one-line install command, and below it a three-card glass strip (net
  worth, 90-day forecast, Advisor).
- **App overview:** a left sidebar (Overview, Transactions, Budgets, Forecast, Investments, Crypto,
  Advisor, Settings), the net worth headline in mono, sync chips at top right ("9 of 10 accounts synced",
  "1 relink needed"), a 90-day forecast card, an Advisor card, an Accounts table with status chips, and a
  Budgets card with pace bars.
