/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_FIXTURES?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
