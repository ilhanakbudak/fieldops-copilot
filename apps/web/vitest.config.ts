import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Component tests for the screens where getting it wrong is expensive.
 *
 * Not every screen — a test that asserts a heading renders is a test that fails
 * when somebody improves the wording. These cover the administration screen,
 * where the interesting behaviour is *refusals*: the guards that stop an
 * administrator locking themselves, or everyone, out.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.tsx"],
    css: {
      // CSS Modules resolve to identity proxies, so a test can assert on a
      // class name without the build pipeline being involved.
      modules: { classNameStrategy: "non-scoped" },
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
