import { defineConfig } from "vite";

/**
 * The office is served by `client/serve.py` on this machine, same origin, no
 * token. `npm run dev` is a different origin, so it forwards /api to the local
 * server instead: without this the dev page would talk to Vite about a world
 * Vite has never heard of, and fail in a way that looks like the server is down.
 */
export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8790",
        changeOrigin: false,
      },
    },
  },
});
