import js from "@eslint/js";
import globals from "globals";
import prettier from "eslint-config-prettier";

// Local plugin enforcing kebab-case filenames for frontend source modules.
// Keeps snake_case out of the frontend (it is reserved for the Python backend
// and BIDS data files). Single-word names (app.js, dom.js) and digits
// (i18n.js) are allowed.
const filenamesPlugin = {
  rules: {
    "kebab-case": {
      meta: { type: "problem", schema: [] },
      create(context) {
        return {
          Program(node) {
            const filename = context.filename;
            if (!filename) return;
            const stem = filename.split("/").pop().replace(/\.js$/, "");
            if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(stem)) {
              context.report({
                node,
                message:
                  "Filename '{{name}}.js' must be kebab-case (lowercase letters, digits and hyphens only).",
                data: { name: stem },
              });
            }
          },
        };
      },
    },
  },
};

export default [
  { ignores: ["node_modules/**", "dist/**"] },
  js.configs.recommended,
  prettier,
  {
    files: ["src/**/*.js"],
    plugins: { filenames: filenamesPlugin },
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.browser },
    },
    rules: {
      // Catches things like the two-statement import from the same module.
      "no-duplicate-imports": "error",
      // Underscore-prefixed args/vars are intentionally unused (DI placeholders).
      "no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // Frontend source files use kebab-case filenames.
      "filenames/kebab-case": "error",
    },
  },
  {
    files: ["tests/**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.node },
    },
  },
];
