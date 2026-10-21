/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OHBE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
