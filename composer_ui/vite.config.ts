import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../assets/composer",
    emptyOutDir: true,
    sourcemap: false,
  },
});
