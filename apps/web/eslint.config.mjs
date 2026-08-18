import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextCoreWebVitals,
  ...nextTypeScript,
  globalIgnores([".next/**", "next-env.d.ts"]),
  {
    // Vendored via `shadcn add` and regenerated wholesale on every
    // `shadcn add`/upgrade -- hand-editing to satisfy our own lint rules
    // would just be overwritten (or drift from upstream) next time.
    files: ["components/ui/**", "hooks/use-mobile.ts"],
    rules: {
      "react-hooks/set-state-in-effect": "off",
    },
  },
]);
