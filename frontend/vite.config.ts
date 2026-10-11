import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { localOnlyApi } from "./dev/localOnlyApi";

export default defineConfig({
  plugins: [react(), localOnlyApi()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
        // Vite answers a failed proxy request with an empty 500; answer with a
        // 502 and an API-style detail so the UI can explain the backend is down.
        configure: (proxy) => {
          proxy.on("error", (_error, _request, response) => {
            if ("writeHead" in response && !response.headersSent) {
              response.writeHead(502, { "Content-Type": "application/json" });
              response.end(
                JSON.stringify({
                  detail: "Could not reach the server. Is the backend running?",
                }),
              );
            }
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
