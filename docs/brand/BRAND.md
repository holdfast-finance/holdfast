# Holdfast brand system

> Every account. One root. Your server.

Two brands, one product (ADR 0003, amended by ADR 0009):

- **Direction 01, Futuristic / Decentralized**: self-host / open source (README, docs, OSS site,
  default self-host app theme). Most of this file.
- **Holo** (Direction C): Holdfast Cloud, the paid hosted tier, and its marketing aimed at less
  technical people. See [Holo brand](#holo-brand-holdfast-cloud) at the end.

The source boards are in `Holdfast_Brand_Direction.pdf` and, as a self-contained interactive bundle with each direction's brand board, landing page and app dashboard (fonts embedded, OFL), `Holdfast_Brand_Direction.html`. In the PDF: pages 1–4 are Direction 01, 5–8 are
Direction B (Terminal / Brutalist, reference only) and 9–12 are Holo. Never mix 01 and Holo on one
surface. In the app they're whole themes (`data-brand`), and components use only semantic tokens.

## Logomark

A holdfast is the root that anchors kelp against the current. The mark is a single asset node, one
stem and three roots: everything you own, anchored to hardware you control.

- App icon: Signal→Deepwater gradient tile with the mark in Abyss.
- On light: the mark in Abyss on a Cloud/white tile.
- Glow (dark UI, favicon): the mark in Signal on Hull, with a soft Signal glow.
- Wordmark: "holdfast", lowercase, Space Grotesk 600.

Sources: `brand/logomark.svg`, `brand/app-icon.svg`, `brand/favicon.svg`. Raster exports in `brand/png/`: `org-avatar-500.png` (full-bleed, for the GitHub org avatar), `social-preview-1280x640.png` (repo social preview and site `og:image`), `apple-touch-icon.png`, `favicon-32.png`. The PWA icons (Phase 8.5) are generated from the same SVGs.

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

## Holo brand (Holdfast Cloud)

Light, iridescent, friendly. For the hosted edition and for marketing to people who don't run
servers. Source: brand PDF pages 9–12. Same logomark geometry, softened (round node, chunky stem).

### Holo color tokens

| Token | Fill | Text token (light) | Role |
|---|---|---|---|
| `--indigo` | `#4F46E5` | `--indigo-text` `#4338CA` | Primary buttons ("See the demo"), active nav, links, focus ring, "Watch-only" |
| `--ink` | `#12131A` | (is text) | Body text, dark buttons, code blocks, dark-mode ground |
| `--cloud` | `#F7F7FB` | | Page ground (light) |
| `--grow` | `#059669` | `--grow-text` `#065F46` | Positive, "Synced", under-pace budget |
| `--heads-up` | `#D97706` | `--heads-up-text` `#92400E` | Warning, "Relink needed", budget pace high |
| `--alert` | `#DC2626` | `--alert-text` `#B91C1C` | Negative amounts, errors, over budget |

**Holo gradient** (`--holo`): the iridescent cyan → lavender → pink → butter sweep from the board.
Use it for the secondary pill button ("Get Holdfast"), the Holo hero orb, frosted-card corner
blooms and the page-edge washes. Never put text on it below 4.5:1 (Ink on Holo passes, white
doesn't). Never use it inside dense data views.

AA (measured, same rule as Direction 01: hues are fills, colored text uses `*-text`): `--grow` as
text is only 3.5:1 on Cloud and `--heads-up` is 3.0:1, so they're never text. Text tokens on their
own 12% chip tint: indigo-text 6.2, grow-text 6.2, heads-up-text 5.9, alert-text 5.1. White on an
Indigo button is 6.3. Indigo focus ring on Cloud is 5.9.

**Holo dark** (follows the system setting): ground Ink `#12131A`, surface `#1B1C25`. Text tokens:
indigo `#A5B4FC`, grow `#34D399`, heads-up `#FBBF24`, alert `#F87171` (≥ 5.3:1 on their chips).
Light is the hero theme for Holo: marketing, screenshots and the Cloud demo use it.

### Holo typography

| Use | Face |
|---|---|
| Display & headings | **Sora 700**, wordmark "holdfast." with an Indigo period |
| Body & UI | **Manrope 400–600** |
| Money, balances, quantities, code | **DM Mono**, `tabular-nums` always |

Self-hosted via `@fontsource/*`, same as Direction 01.

### Holo UI atoms

- **Primary pill:** Indigo fill, white text ("See the demo", "Model it"). **Secondary pill:** Holo gradient, Ink text ("Get Holdfast"). **Tertiary:** white with a hairline ("Later").
- **Status chips:** same dot + label + tint pattern as 01: Synced (Grow), Relink needed (Heads-up), Watch-only (Indigo).
- **Frosted card:** white at ~80% with backdrop blur, 20px radius, a soft shadow, and one Holo bloom in a corner. Dashboard hero, Advisor card and marketing only.
- **Charts:** actuals are a cyan → indigo → pink gradient line. The forecast is a soft lavender P10–P90 band.
- **Budget bars:** per-category pastel gradients, with Heads-up when pace ≥ 90% and Alert when over.
- **Radius:** 20px cards, full pills. Generous whitespace, light washes at the page edges.
- Accounts table section headers use colored text (Banks / Investments / Crypto) from the `*-text` tokens.

### Holo voice

Warm, plain-English, a little delighted. Celebrate progress, explain jargon, never guilt anyone
about a latte. Ownership is still the headline. The rules from the main Voice section still apply
(no fear-selling, no "CPA" claims, no crypto hype). Holo allows an occasional exclamation in
celebratory moments ("Nice month!"), never in errors or warnings.

- All your money, rooted in one place.
- Own your finances. Literally.
- Footer: "Not a bank or a CPA. Your money, your data."

**Truthfulness for Cloud.** The board copy was written for self-hosting. Cloud copy must not say
"on your server" or show `curl … | sh`. Say what's true: hosted by Holdfast, encrypted, export or
delete everything any time, open source so you can move to self-hosting. Cloud crypto is watch-only
(no hardware-wallet signing), and the tax module isn't offered (PLAN 17, 18).

### Holo reference layouts

- **Cloud site hero:** "All your money, rooted in **one place.**" ("one place." in the Holo
  gradient text with an Ink fallback for AA), subhead, Indigo + Holo pills, the Holo orb with the mark and
  floating chips ("12 accounts synced", "Cold wallet · watch-only"), then a three-card frosted strip
  (net worth, next 90 days, advisor). Then a six-card features grid ("Own your finances.
  Literally."), then a dark Ink CTA panel ("Ready to put down roots?").
- **Cloud app overview:** greeting header ("Good evening"), search + "Add account" Holo pill, net
  worth frosted card with category chips, cash forecast card with backtest note, Accounts table
  grouped by Banks / Investments / Crypto, Budgets card with gradient bars, "Advisor found
  something" card.

