---
paths:
  - "web/**"
  - "site/**"
---
# Frontend

- React + Vite + TS strict, Tailwind v4, shadcn/ui base (`pnpm dlx shadcn@latest add <c>`). Code-generated shadcn components live in `web/src/components/ui/`. Edit them freely, since we own them.
- Brand: components use **semantic** tokens only (`primary`, `secondary`, `success`, `warning`, `destructive` and their `*-text` forms, `muted-foreground`, `card`, `border`, `input`, `ring`, `chart-actual`, `chart-forecast`, `font-mono`). Brand hues (`--signal`, `--deepwater`, …) are mapped to those in `web/src/index.css` / `lib/tokens.ts` only, so the Holo theme can swap in (ADR 0009). No raw hex or brand-hue class names in components; `src/brand.node.test.ts` enforces this.
- Type (Direction 01): Space Grotesk 600 for display/headings, Inter 400/500 for UI/body, the `font-mono` token (JetBrains Mono; DM Mono under Holo) with `font-variant-numeric: tabular-nums` for **all** money, balances, quantities and codes. Self-host the fonts (no Google Fonts CDN in the app, for privacy).
- Status chips: Synced = success, Relink needed = warning, Watch-only = secondary, Error/negative = destructive (Signal/Flare/Deepwater/Breach under Direction 01). Always pair color with a text label.
- Motion: Magic UI / Aceternity only in `web/src/components/magic/`, only on Dashboard and login. Respect `prefers-reduced-motion`.
- Mobile: bottom tab bar (Dashboard, Transactions, Budgets, Advisor, More), sidebar on desktop. Use Sheet/Drawer instead of modals on small screens, test at 375px, and keep touch targets ≥ 44px.
- PWA: the service worker caches the app shell only. Never cache `/api/*`.
- Demo mode (`VITE_DEMO=1`) uses seeded fake data and no backend, for the public site's static demo. Placeholders like `$[NET WORTH]` are fine in marketing mockups.
- Accessibility: WCAG AA contrast against Abyss/Hull, full keyboard navigation, visible focus rings (the `ring` token). Form-control borders use `--input` (≥ 3:1).
