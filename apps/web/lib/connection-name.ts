/** Connection names are also S3 bucket names in the public S3 API, so
 * they follow S3 bucket naming rules (no dots: they would break
 * `{bucket}.{endpoint}` virtual-host addressing). Mirrors
 * apps/media `apps/provider_connections/names.py`; the server is the
 * authority, this only gives instant feedback in forms. */

const MIN = 3;
const MAX = 63;
const FORBIDDEN_PREFIXES = ["xn--", "sthree-", "amzn-s3-demo-"];
const FORBIDDEN_SUFFIXES = ["-s3alias", "--ol-s3", "--x-s3", "--table-s3"];
const RESERVED = new Set(["umedia", "api", "s3", "www"]);
const FALLBACK = "storage";

/** HTML `pattern` for the name input (character set + edges only). */
export const CONNECTION_NAME_PATTERN = "[a-z0-9](?:[a-z0-9\\-]*[a-z0-9])?";

/** The rule `name` breaks, or `null` when it is a valid name. */
export function connectionNameError(name: string): string | null {
  if (name.length < MIN || name.length > MAX) {
    return `Name must be between ${MIN} and ${MAX} characters`;
  }
  if (!/^[a-z0-9-]+$/.test(name)) {
    return "Name may contain only lowercase letters, numbers, and hyphens";
  }
  if (name.startsWith("-") || name.endsWith("-")) {
    return "Name must start and end with a letter or number";
  }
  const prefix = FORBIDDEN_PREFIXES.find((item) => name.startsWith(item));
  if (prefix) return `Name cannot start with '${prefix}'`;
  if (name.includes("--")) return "Name cannot contain consecutive hyphens";
  const suffix = FORBIDDEN_SUFFIXES.find((item) => name.endsWith(item));
  if (suffix) return `Name cannot end with '${suffix}'`;
  if (RESERVED.has(name)) return `'${name}' is a reserved name`;
  return null;
}

/** A valid default name derived from a label like "Microsoft OneDrive",
 * skipping names in `taken` (`sftp` -> `sftp-2` -> ...). */
export function suggestConnectionName(raw: string, taken: string[] = []): string {
  let slug = raw.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  for (const prefix of FORBIDDEN_PREFIXES) {
    if (slug.startsWith(prefix)) slug = slug.slice(prefix.length);
  }
  slug = slug.slice(0, MAX).replace(/-+$/, "");
  for (const suffix of FORBIDDEN_SUFFIXES) {
    if (slug.endsWith(suffix)) slug = slug.slice(0, -suffix.length);
  }
  if (!slug) slug = FALLBACK;
  else if (slug.length < MIN || RESERVED.has(slug)) {
    slug = `${slug}-${FALLBACK}`.slice(0, MAX);
  }
  const used = new Set(taken);
  if (!used.has(slug)) return slug;
  for (let counter = 2; ; counter += 1) {
    const tail = `-${counter}`;
    const candidate = `${slug.slice(0, MAX - tail.length).replace(/-+$/, "")}${tail}`;
    if (!used.has(candidate)) return candidate;
  }
}
