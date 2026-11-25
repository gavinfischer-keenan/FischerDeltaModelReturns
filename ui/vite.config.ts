import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Served under /fischer-delta/ (specification section 9.3).
export default defineConfig({
  base: "./",
  plugins: [svelte()],
  worker: { format: "es" },
  // On-screen words live in /text, outside this folder (specification 4.2).
  server: { fs: { allow: [".."] } },
  build: { target: "es2022", outDir: "dist", emptyOutDir: true },
});
