import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { quasar, transformAssetUrls } from "@quasar/vite-plugin";

export default defineConfig({
  plugins: [
    vue({ template: { transformAssetUrls } }),
    quasar({
      sassVariables: fileURLToPath(new URL("./src/css/quasar-variables.sass", import.meta.url)),
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5186,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:5265", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:5265", changeOrigin: true },
      "/ready": { target: "http://127.0.0.1:5265", changeOrigin: true },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4186,
    strictPort: true,
  },
});
