import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL(".", import.meta.url).pathname } },
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "https://drive.uln.me" } },
    setupFiles: ["./tests/bun-test-setup.js"],
  },
});
