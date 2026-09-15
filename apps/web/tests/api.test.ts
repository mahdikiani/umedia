import { afterEach, describe, expect, it, vi } from "vitest";

import { addToTemporary, extractErrorMessage, fileContentUrl, fileDownloadUrl } from "@/lib/api";

describe("MediaFile URLs", () => {
  it("targets the live files content endpoint, with optional filename", () => {
    expect(fileContentUrl("file-1")).toBe("/api/v1/files/file-1/content");
    expect(fileContentUrl("file-1", "photo.jpg")).toBe(
      "/api/v1/files/file-1/content/photo.jpg",
    );
    expect(fileContentUrl("file-1", "a/b.txt")).toBe(
      "/api/v1/files/file-1/content/a_b.txt",
    );
  });

  it("adds download=1 only when requested", () => {
    expect(fileDownloadUrl("file-1", "notes.md")).toBe(
      "/api/v1/files/file-1/content/notes.md?download=1",
    );
    expect(fileContentUrl("file-1", "notes.md", { download: true })).toBe(
      "/api/v1/files/file-1/content/notes.md?download=1",
    );
  });
});

describe("Temporary pointers", () => {
  it("does not request the API when no ids are provided", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await addToTemporary([]);

    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("extractErrorMessage", () => {
  it("prefers detail over a generic wrapper message", () => {
    // apps/provider_connections/services.py's ProviderConnectionError:
    // a fixed generic `message` paired with the actual reason in
    // `detail` -- reported live as "Could not connect to the storage
    // provider" with no way to see the real ("root_path must be inside
    // /data/storage") cause.
    const body = {
      message: "Could not connect to the storage provider",
      error_code: "provider_connection_failed",
      detail: "root_path must be inside /data/storage",
    };
    expect(extractErrorMessage(body)).toBe("root_path must be inside /data/storage");
  });

  it("falls back to a string message when there is no detail", () => {
    expect(extractErrorMessage({ message: "Resource not found" })).toBe(
      "Resource not found",
    );
  });

  it("picks the current-language string out of a bilingual {en, fa} message", () => {
    const body = {
      message: { en: "Invalid credentials.", fa: "اطلاعات ورود صحیح نیست." },
      detail: null,
    };
    expect(extractErrorMessage(body)).toBe("Invalid credentials.");
  });

  it("never returns [object Object] or similar for a malformed/empty body", () => {
    expect(extractErrorMessage(null)).toBe("Something went wrong");
    expect(extractErrorMessage({})).toBe("Something went wrong");
    expect(extractErrorMessage({ message: {}, detail: null })).toBe(
      "Something went wrong",
    );
  });
});

describe("api refresh retry", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    Object.defineProperty(globalThis, "fetch", {
      configurable: true,
      value: originalFetch,
      writable: true,
    });
  });

  it("retries once through POST /auth/refresh after a 401", async () => {
    let calls = 0;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      calls += 1;
      if (calls === 1) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (calls === 2) {
        expect(url).toBe("/api/v1/auth/refresh");
        expect(init?.method).toBe("POST");
        return Promise.resolve(
          new Response(JSON.stringify({ status: "refreshed" }), { status: 200 }),
        );
      }
      expect(url).toBe("/api/v1/providers");
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      );
    });

    Object.defineProperty(globalThis, "fetch", {
      configurable: true,
      value: fetchMock,
      writable: true,
    });

    const { api } = await import("@/lib/api");
    const result = await api<{ ok: boolean }>("/providers");

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("refreshes and re-fetches auth state when bootstrap is unauthenticated", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ configured: true, authenticated: false, user: null }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "refreshed" }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            configured: true,
            authenticated: true,
            user: {
              uid: "user-1",
              email: "admin@example.com",
              roles: ["admin"],
            },
          }),
          { status: 200 },
        ),
      );
    Object.defineProperty(globalThis, "fetch", {
      configurable: true,
      value: fetchMock,
      writable: true,
    });

    const { api } = await import("@/lib/api");

    const result = await api<{ authenticated: boolean }>("/auth/state");

    expect(result.authenticated).toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/auth/refresh",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/v1/auth/state",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
