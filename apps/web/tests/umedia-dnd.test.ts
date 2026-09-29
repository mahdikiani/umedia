import "@testing-library/jest-dom/vitest";

import { describe, expect, it } from "vitest";

import {
  UMEDIA_FILE_IDS_MIME,
  UMEDIA_FROM_TEMPORARY_MIME,
  parseLibraryDragPayload,
  setLibraryDragPayload,
  transferOperationForStorage,
} from "@/lib/umedia-dnd";

function fakeDataTransfer(initial?: Record<string, string>): DataTransfer {
  const store = new Map<string, string>(Object.entries(initial ?? {}));
  return {
    get types() {
      return Array.from(store.keys());
    },
    getData(type: string) {
      return store.get(type) ?? "";
    },
    setData(type: string, value: string) {
      store.set(type, value);
    },
    effectAllowed: "uninitialized",
    dropEffect: "none",
  } as unknown as DataTransfer;
}

describe("umedia-dnd payload", () => {
  it("stores source storage ids and fromTemporary marker MIME", () => {
    const dt = fakeDataTransfer();
    setLibraryDragPayload(dt, ["a", "b"], {
      fromTemporary: true,
      sourceConnectionIds: ["storage-a", "storage-b"],
    });

    const raw = JSON.parse(dt.getData(UMEDIA_FILE_IDS_MIME));
    expect(raw).toEqual({
      sourceIds: ["a", "b"],
      sourceConnectionIds: ["storage-a", "storage-b"],
      fromTemporary: true,
    });
    expect(dt.getData(UMEDIA_FROM_TEMPORARY_MIME)).toBe("1");
    expect(Array.from(dt.types)).toContain(UMEDIA_FROM_TEMPORARY_MIME);
  });

  it("parses legacy bare string[] payload", () => {
    const dt = fakeDataTransfer({
      [UMEDIA_FILE_IDS_MIME]: JSON.stringify(["legacy-1"]),
    });
    expect(parseLibraryDragPayload(dt)).toEqual({ sourceIds: ["legacy-1"] });
  });

  it("moves within the same storage regardless of drag origin", () => {
    expect(transferOperationForStorage(["storage-a"], "storage-a")).toBe(
      "move",
    );
  });

  it("copies across storages, including a mixed-storage drag", () => {
    expect(transferOperationForStorage(["storage-a"], "storage-b")).toBe(
      "copy",
    );
    expect(
      transferOperationForStorage(["storage-a", "storage-b"], "storage-a"),
    ).toBe("copy");
  });

  it("copies when either storage is unknown", () => {
    expect(transferOperationForStorage([null], "storage-a")).toBe("copy");
    expect(transferOperationForStorage(["storage-a"], null)).toBe("copy");
  });
});
