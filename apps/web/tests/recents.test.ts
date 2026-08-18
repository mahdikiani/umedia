import { afterEach, describe, expect, it, vi } from "vitest";

import { readRecents, rememberRecent } from "@/lib/recents";

describe("recents", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stores files and folders separately and keeps the newest first", () => {
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => store[key] ?? null),
      setItem: vi.fn((key: string, value: string) => {
        store[key] = value;
      }),
    });

    rememberRecent({ uid: "f1", name: "old.txt", type: "file" });
    rememberRecent({ uid: "d1", name: "Docs", type: "folder" });
    rememberRecent({ uid: "f1", name: "old.txt", type: "file" });

    const recents = readRecents();
    expect(recents.files.map((item) => item.uid)).toEqual(["f1"]);
    expect(recents.folders.map((item) => item.uid)).toEqual(["d1"]);
  });
});
