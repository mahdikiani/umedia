/** Thin client for apps/media's `/api/v1` — see docs/05-api-design.md. */

export type AuthUser = {
  uid: string;
  email: string;
  roles: string[];
  name?: string | null;
};

export type UserSummary = {
  uid: string;
  email: string;
  name: string | null;
  roles: string[];
  is_active: boolean;
};

export type AccessKey = {
  uid: string;
  access_key_id: string;
  label: string;
  is_active: boolean;
  created_at: string;
};

export type AccessKeyCreated = AccessKey & { secret_access_key: string };

export type S3EndpointInfo = {
  endpoint: string;
  region: string;
  bucket: string;
  force_path_style: boolean;
};

export type AuthState = {
  configured: boolean;
  authenticated: boolean;
  user?: AuthUser | null;
};

export type ProviderField = {
  key: string;
  label: string;
  input_type: string;
  required: boolean;
  secret: boolean;
  placeholder: string | null;
};

export type ProviderType = {
  id: string;
  name: string;
  description: string;
  adapter: string;
  status: "available" | "beta" | "planned";
  capabilities: string[];
  fields: ProviderField[];
  // "oauth"/"session" sub-flow endpoints aren't built yet -- every
  // provider type is still created through the plain "token" form
  // regardless of this value. See plugins/manifest.py's own field.
  connect_flow: "token" | "oauth" | "session";
};

export type ProviderConnection = {
  uid: string;
  provider_type: string;
  name: string;
  status: string;
  enabled: boolean;
  import_existing: boolean;
  mirror_structure: boolean;
  created_at: string;
  last_tested_at: string | null;
  last_error: string | null;
};

export type MediaFileItem = {
  uid: string;
  owner_id: string;
  provider_connection_id: string | null;
  storage_object_id?: string | null;
  type: string;
  name: string;
  parent_id: string | null;
  metadata: Record<string, unknown>;
  content_type: string;
  size: number;
  status: "processing" | "completed" | "failed";
  error: string | null;
  public_permission: "none" | "read";
  starred: boolean;
  permissions: { user_id: string; permission: number }[];
  workspace_id: string | null;
  access_at: string;
  /** Set while the file sits in the trash (soft-deleted); null when live. */
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ResourceItem = MediaFileItem;

/** Offset/limit envelope every backend list endpoint returns —
 * `total` counts the full result set, not just this slice. */
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

/** One backend page per fetch — matches the API's default `limit`. */
export const LIST_PAGE_SIZE = 50;

export type StorageObjectItem = {
  uid: string;
  provider_connection_id: string;
  content_reference: string;
  provider_parent_ref: string | null;
  type: string;
  name: string;
  content_hash: string | null;
  content_type: string;
  size: number;
  metadata: Record<string, unknown>;
  status: string;
  last_seen_at: string;
};

export type VolumeStats = {
  used_bytes: number;
  file_count: number;
  folder_count: number;
};

export class ApiError extends Error {}

/** The backend's error body is inconsistent about `message`'s shape:
 * apps/media's own custom errors (ResourceNotFoundError, etc.) send a
 * plain string, but usso/fastapi_mongo_base's own built-in errors
 * (auth failures, request-validation errors) send `{en, fa}`. Passing
 * that object straight to `Error(...)` silently stringifies it to the
 * useless `"[object Object]"` -- the actual bug behind what looked like
 * a display glitch.
 *
 * `detail` is preferred when present: several of apps/media's own errors
 * (`ProviderConnectionError`, `ResourceNotFoundError`, ...) pair a fixed,
 * generic `message` ("Could not connect to the storage provider") with
 * the *actual* specific reason in `detail` ("root_path must be inside
 * /data/storage") -- showing only `message` in those cases means the
 * admin can never see why a request actually failed. `detail` is always
 * plain English (never localized like `message` can be), which is still
 * strictly better than a generic string in the wrong cases. */
export function extractErrorMessage(body: unknown): string {
  if (!body || typeof body !== "object") return "Something went wrong";
  const { message, detail } = body as Record<string, unknown>;

  if (typeof detail === "string" && detail) return detail;

  if (typeof message === "string" && message) return message;

  if (message && typeof message === "object") {
    const localized = message as Record<string, unknown>;
    const lang = typeof document !== "undefined" ? document.documentElement.lang : "en";
    const candidates = [localized[lang], localized.en, ...Object.values(localized)];
    const found = candidates.find((value) => typeof value === "string" && value);
    if (typeof found === "string") return found;
  }

  return "Something went wrong";
}

/** JSON request/response -- auth, /providers list/delete, and anywhere
 * else the body isn't a file upload. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new ApiError(extractErrorMessage(error));
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

/** `multipart/form-data` request -- uploads and library-folder creation
 * use individual `Form()` fields. MediaFile metadata changes use JSON
 * `PATCH /files/{uid}` through `api()` instead. Never set Content-Type
 * manually here; the browser fills in the multipart boundary itself. */
export async function apiForm<T>(
  path: string,
  method: "POST" | "PUT",
  form: FormData,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: "include",
    method,
    body: form,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new ApiError(extractErrorMessage(error));
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

/** A single trailing path segment for file URLs -- cosmetic for browsers
 * / download names; servers resolve by `uid` only. Slashes are flattened
 * so the segment never looks like a nested path. */
export function fileUrlFilename(name: string): string {
  const cleaned = name.replace(/[/\\]+/g, "_").trim() || "file";
  return encodeURIComponent(cleaned);
}

/** The absolute-ish path of a MediaFile's own byte stream -- used as a
 * plain `<a href>` (same-origin, cookie-authenticated) rather than
 * through `api()`, since the browser should stream/download it directly
 * instead of the app buffering it in memory first. Optional `filename`
 * is appended as a dummy trailing segment for nicer downloads. */
export function fileContentUrl(uid: string, filename?: string): string {
  const base = `/api/v1/files/${uid}/content`;
  return filename ? `${base}/${fileUrlFilename(filename)}` : base;
}

export function resourceContentUrl(uid: string, filename?: string): string {
  return fileContentUrl(uid, filename);
}

/** Prefix a backend-issued path-absolute path (e.g. `/api/v1/f/...`)
 * with the site origin so it can be shared outside the app.
 * Already-absolute URLs (scheme present) are returned unchanged so a
 * future mint that returns a full SigV4 URL is not double-prefixed. */
export function absoluteUrl(path: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(path)) {
    return path;
  }
  return `${typeof window === "undefined" ? "" : window.location.origin}${path}`;
}

/** Permanent public share URL. Trailing `filename` is cosmetic. */
export function publicLinkUrl(uid: string, filename?: string): string {
  const path = filename
    ? `/api/v1/f/${uid}/${fileUrlFilename(filename)}`
    : `/api/v1/f/${uid}`;
  return absoluteUrl(path);
}

/** `POST /files/{uid}/temporary-link` response — a SigV4-presigned S3
 * GET URL for `{uid}/{filename}`: `url` is path-absolute and already
 * carries the `X-Amz-*` query params; `key_id` is the public id of the
 * signing access key (never the secret). */
export type TemporaryLink = {
  url: string;
  key_id: string;
  expires: number;
  expires_at: string;
};

export function createTemporaryLink(
  uid: string,
  expiresInSeconds: number,
): Promise<TemporaryLink> {
  return api<TemporaryLink>(`/files/${uid}/temporary-link`, {
    method: "POST",
    body: JSON.stringify({ expires_in: expiresInSeconds }),
  });
}
