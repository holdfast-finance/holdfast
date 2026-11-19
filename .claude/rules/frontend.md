---
paths:
  - "web/**"
  - "site/**"
---
# Frontend

- React + Vite + TS strict, Tailwind v4, shadcn/ui base (`pnpm dlx shadcn@latest add <c>`). Code-generated shadcn components live in `web/src/components/ui/`. Edit them freely, since we own them.
- Brand: use the CSS variables and Tailwind tokens defined from `docs/brand/BRAND.md` (`--signal`, `--deepwater`, `--abyss`, `--hull`, `--flare`, `--breach`). No raw hex in components.
- Type: Space Grotesk 600 for display/headings, Inter 400/500 for UI/body, JetBrains Mono with `font-variant-numeric: tabular-nums` for **all** money, balances, quantities and codes. Self-host the fonts (no Google Fonts CDN in the app, for privacy).
- Status chips: Synced = Signal, Relink needed = Flare, Watch-only = Deepwater, Error/negative = Breach. Always pair color with a text label.
- Motion: Magic UI / Aceternity only in `web/src/components/magic/`, only on Dashboard and login. Respect `prefers-reduced-motion`.
- Mobile: bottom tab bar (Dashboard, Transactions, Budgets, Advisor, More), sidebar on desktop. Use Sheet/Drawer instead of modals on small screens, test at 375px, and keep touch targets ≥ 44px.
- PWA: the service worker caches the app shell only. Never cache `/api/*`.
- Demo mode (`VITE_DEMO=1`) uses seeded fake data and no backend, for the public site's static demo. Placeholders like `$[NET WORTH]` are fine in marketing mockups.
- Accessibility: WCAG AA contrast against Abyss/Hull, full keyboard navigation, visible focus rings in Signal.
