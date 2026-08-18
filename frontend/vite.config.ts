import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

import { stubApi } from "./stub/plugin";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";
// Stub mode serves the API from fixtures inside this process. Either the stub
// answers /api or the proxy forwards it — never both.
const stub = process.env.VITE_STUB === "true";

export default defineConfig({
  plugins: [react(), ...(stub ? [stubApi()] : [])],
  server: {
    host: true,
    port: 5173,
    proxy: stub
      ? undefined
      : {
          "/api": {
            target: apiTarget,
            changeOrigin: true,
          },
        },
  },
});
