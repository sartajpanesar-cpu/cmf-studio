import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Served by watchd at http://127.0.0.1:8765/ui/ ; `npm run dev` proxies the API to a running watchd.
export default defineConfig({
  base: "/ui/",
  plugins: [svelte()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    proxy: Object.fromEntries(
      ["/status", "/notify", "/faces", "/sync", "/samples", "/daily", "/jobs", "/events", "/insight", "/reparse"].map((p) => [
        p,
        "http://127.0.0.1:8765",
      ]),
    ),
  },
});
