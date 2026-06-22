// @ts-check
import { defineConfig } from "astro/config";

export default defineConfig({
  base: "/dsm-jobs/",
  outDir: "../web",
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
