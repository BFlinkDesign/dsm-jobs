// @ts-check
import { defineConfig, envField } from "astro/config";

export default defineConfig({
  base: "/dsm-jobs/",
  outDir: "../web",
  env: {
    schema: {
      SENTRY_DSN: envField.string({
        context: "server",
        access: "public",
        optional: true,
      }),
    },
  },
  build: {
    assets: "_astro",
    inlineStylesheets: "auto",
  },
  vite: {
    build: {
      cssMinify: true,
      target: "es2020",
    },
  },
});
