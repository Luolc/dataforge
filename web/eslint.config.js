import js from "@eslint/js";
import eslintReact from "@eslint-react/eslint-plugin";
import vitest from "@vitest/eslint-plugin";
import betterTailwind from "eslint-plugin-better-tailwindcss";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import reactHooks from "eslint-plugin-react-hooks";
import { reactRefresh } from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist"]),
  {
    linterOptions: { reportUnusedDisableDirectives: "error" },
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat.recommended,
      eslintReact.configs["strict-type-checked"],
      eslintReact.configs["disable-conflict-eslint-plugin-react-hooks"],
      jsxA11y.configs.strict,
      reactRefresh.configs.vite(),
      betterTailwind.configs.correctness,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    settings: {
      "better-tailwindcss": {
        entryPoint: "src/ui/theme.css",
        // .md, the Markdown styles, is a class of @layer components there.
        detectComponentClasses: true,
      },
    },
    rules: {
      // @eslint-react's conflict preset turns off the react-hooks rules it
      // replaces; it has no rule of its own for these three.
      "react-hooks/globals": "error",
      "react-hooks/immutability": "error",
      "react-hooks/refs": "error",
      // A deliverable kind's View, Count and Line are components declared
      // once in src/views/registry.tsx and looked up by kind while
      // rendering, which this rule reports at every lookup.
      // no-nested-component-definitions still reports a component declared
      // inside another.
      "@eslint-react/static-components": "off",
      // Every list keyed by its index is a read-only record drawn in its
      // own order and never reordered or filtered in place: a file's lines,
      // a session's messages, an attempt's runs, where the index is the
      // run's number.
      "@eslint-react/no-array-index-key": "off",
      "@typescript-eslint/switch-exhaustiveness-check": [
        "error",
        { considerDefaultExhaustiveForUnions: false },
      ],
      // A condition must be a boolean: a test that 0 or "" fails reads as a
      // test for a missing value.
      "@typescript-eslint/strict-boolean-expressions": [
        "error",
        { allowNumber: false, allowString: false },
      ],
      "@typescript-eslint/no-unsafe-type-assertion": "error",
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true },
      ],
    },
  },
  {
    // Node, not the browser, runs these.
    files: ["vite.config.ts", "fixtures/**"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["**/*.test.{ts,tsx}"],
    extends: [vitest.configs.recommended],
  },
  {
    files: ["eslint.config.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
]);
