import { describe, expect, it } from "vitest";

import { extractErrorMessage, fileContentUrl } from "@/lib/api";

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
