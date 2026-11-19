/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `1` turns on the in-browser MSW mock API. Honoured by `pnpm dev` only, never by builds. */
  readonly VITE_MOCK_API?: string
  /** Default brand theme: `signal` (Direction 01, default) or `holo` (Holdfast Cloud). */
  readonly VITE_BRAND?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
