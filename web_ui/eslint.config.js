import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
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
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
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
);
