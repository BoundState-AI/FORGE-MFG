/**
 * Module:   ESLint Config
 * Purpose:  Lint rules for the FORGE-MFG frontend.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this config: We use the legacy .eslintrc (supported by ESLint 8.57)
 * rather than flat config because the v8 CLI still defaults to legacy.
 * Rules are intentionally light — TypeScript already catches most drift,
 * and the existing tsc --noEmit gate is authoritative for type safety.
 * ESLint fills the gap on unused imports, hook deps, and stylistic drift.
 */
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'react-refresh'],
  ignorePatterns: [
    'dist',
    'node_modules',
    'src/api/generated/**',
    'src/api/openapi.json',
    '*.cjs',
  ],
  rules: {
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
    /* TypeScript's own unused-vars is already on; disable the JS one. */
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': [
      'warn',
      {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      },
    ],
    /* Explicit any is sometimes needed for openapi-fetch typed operation
       parameters and for generated-schema escape hatches.  Warn, don't error. */
    '@typescript-eslint/no-explicit-any': 'warn',
    /* The codebase uses both type and interface; keep consistent style
       without hard failures. */
    '@typescript-eslint/consistent-type-definitions': 'off',
    /* We intentionally use require() inside this file and vite.config.
       Disable the restriction — we control both boundaries. */
    '@typescript-eslint/no-var-requires': 'off',
  },
};
