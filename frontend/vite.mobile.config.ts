import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  root: fileURLToPath(new URL("./mobile-native", import.meta.url)),
  plugins: [react()],
  server: { port: 5174, strictPort: true },
  build: { outDir: "../dist-mobile", emptyOutDir: true },
});
