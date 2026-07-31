import { defineConfig } from "vite";

export default defineConfig({
  resolve: {
    alias: {
      "@": "/src"
    }
  },
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  }
});
