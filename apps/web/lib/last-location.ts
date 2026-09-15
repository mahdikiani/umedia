const STORAGE_KEY = "umedia.lastLocation";

/** Dashboard routes we may restore after login / visiting `/`. */
const ALLOWED_PREFIXES = [
  "/home",
  "/files",
  "/starred",
  "/trash",
  "/storage",
  "/settings",
] as const;

const DEFAULT_LOCATION = "/home";

function isAllowedPath(path: string): boolean {
  return ALLOWED_PREFIXES.some(
    (prefix) =>
      path === prefix ||
      path.startsWith(`${prefix}/`) ||
      path.startsWith(`${prefix}?`),
  );
}

/** Last dashboard location (`pathname` + `search`), or `/home`. */
export function readLastLocation(): string {
  if (typeof window === "undefined") return DEFAULT_LOCATION;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw || !isAllowedPath(raw)) return DEFAULT_LOCATION;
    return raw;
  } catch {
    return DEFAULT_LOCATION;
  }
}

/** Persist the current dashboard URL for the next cold start / login. */
export function rememberLastLocation(pathWithSearch: string): void {
  if (typeof window === "undefined") return;
  if (!isAllowedPath(pathWithSearch)) return;
  window.localStorage.setItem(STORAGE_KEY, pathWithSearch);
}
