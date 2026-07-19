/// <reference types="vite/client" />

// Standard Vite ambient declarations. This workspace got by without them until
// composer's main.tsx needed `import.meta.env.DEV` (to gate the dev-only
// CSS-variable sheet out of production builds) and a typed CSS module import.
// Both come from vite/client; nothing here is project-specific.
