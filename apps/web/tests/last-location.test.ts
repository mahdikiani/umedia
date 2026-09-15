import { beforeEach, describe, expect, it } from "vitest";

import { readLastLocation, rememberLastLocation } from "@/lib/last-location";

describe("last location", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    const localStorage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: localStorage,
    });
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: localStorage,
    });
  });

  it("defaults to /home when nothing is stored", () => {
    expect(readLastLocation()).toBe("/home");
  });

  it("remembers a files folder URL and restores it", () => {
    rememberLastLocation("/files?folder=abc&sort=name&order=asc");
    expect(readLastLocation()).toBe("/files?folder=abc&sort=name&order=asc");
  });

  it("remembers nested settings paths", () => {
    rememberLastLocation("/settings/storage");
    expect(readLastLocation()).toBe("/settings/storage");
  });

  it("ignores non-dashboard paths", () => {
    rememberLastLocation("/login");
    expect(readLastLocation()).toBe("/home");
  });
});
