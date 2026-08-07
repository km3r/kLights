import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built into ui/dist, which engine/server.py serves directly. `npm run dev`
// runs on its own port and proxies the WebSocket to the engine, so hot reload
// works without the engine needing to know about Vite.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    host: true,               // reachable from a phone on the same network
    proxy: { "/ws": { target: "ws://127.0.0.1:8765", ws: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
