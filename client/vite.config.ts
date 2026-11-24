import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// The FastAPI backend serves the built files from client/dist. In development, API calls are
// proxied to the uvicorn server on port 8000.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // the pages are split by React.lazy ; the libraries go to stable vendor chunks (cached between deployments
        // when they do not change), the charting library apart : only the pages with a chart download it
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          const rel = id.split("node_modules/").pop()!;
          const pkg = rel.startsWith("@") ? rel.split("/").slice(0, 2).join("/") : rel.split("/")[0];
          // the charting library and everything only it depends on (its React helpers included) : one chunk, loaded by the
          // pages with a chart only ; nothing else may land there, or the chunks would import each other in a circle
          if (/^(recharts|recharts-scale|react-smooth|react-transition-group|victory-vendor|d3-|internmap|decimal\.js-light|eventemitter3|fast-equals|lodash|prop-types|react-is|dom-helpers|@babel\/runtime)/.test(pkg)) return "vendor-charts";
          if (/^(react|react-dom|scheduler|react-router|react-router-dom|@remix-run|@tanstack)$/.test(pkg) || pkg.startsWith("@remix-run") || pkg.startsWith("@tanstack")) return "vendor-react";
          if (pkg === "lucide-react") return "vendor-icons";
          if (pkg === "date-fns") return "vendor-dates";
          if (pkg.startsWith("@fontsource")) return undefined;
          return "vendor";
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
});
