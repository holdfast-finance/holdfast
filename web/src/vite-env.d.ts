/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `1` turns on the in-browser MSW mock API. Honoured by `pnpm dev` only, never by builds. */
  readonly VITE_MOCK_API?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
