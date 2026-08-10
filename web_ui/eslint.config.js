import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default tseslint.config(
  { ignores: ["**/dist/**", "**/node_modules/**", "../assets/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // ADR-012 stage 12.3: the ADR's own CI gate ("regressions are caught
      // at PR time") - spread in directly rather than as a separate config
      // object in the array, so it composes with (rather than overrides)
      // the @typescript-eslint no-unused-vars override two lines below,
      // which every *.tsx file in this repo needs regardless of a11y rules.
      ...jsxA11y.flatConfigs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Document View redesign (stage 1): react-markdown's component
      // overrides always receive a `node` prop (its own hast node) that
      // must never be spread onto the real DOM element it's rendering -
      // the standard, widely-adopted convention for "destructured but
      // deliberately discarded" is a leading underscore, which this
      // codebase had no prior need for (nothing previously discarded a
      // destructured prop this way) and @typescript-eslint's recommended
      // preset does not ignore by default.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", destructuredArrayIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "vitest.setup.ts"],
    languageOptions: {
      globals: globals.node,
    },
  },
  {
    // ADR-019 stage 19.2: build-tooling scripts (check-bundle-size.mjs) run
    // under Node, not the browser - same globals carve-out the test files
    // above already get.
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: globals.node,
    },
  },
);
