import "@testing-library/jest-dom/vitest";

import { describe, expect, it } from "vitest";

import {
  UMEDIA_FILE_IDS_MIME,
  UMEDIA_FROM_TEMPORARY_MIME,
  parseLibraryDragPayload,
  setLibraryDragPayload,
  transferOperationFromEvent,
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
  it("stores structured payload and fromTemporary marker MIME", () => {
    const dt = fakeDataTransfer();
    setLibraryDragPayload(dt, ["a", "b"], { fromTemporary: true });

    const raw = JSON.parse(dt.getData(UMEDIA_FILE_IDS_MIME));
    expect(raw).toEqual({ sourceIds: ["a", "b"], fromTemporary: true });
    expect(dt.getData(UMEDIA_FROM_TEMPORARY_MIME)).toBe("1");
    expect(Array.from(dt.types)).toContain(UMEDIA_FROM_TEMPORARY_MIME);
  });

  it("parses legacy bare string[] payload", () => {
    const dt = fakeDataTransfer({
      [UMEDIA_FILE_IDS_MIME]: JSON.stringify(["legacy-1"]),
    });
    expect(parseLibraryDragPayload(dt)).toEqual({ sourceIds: ["legacy-1"] });
  });

  it("forces move when fromTemporary even if Alt is held", () => {
    const payload = { sourceIds: ["t-1"], fromTemporary: true as const };
    expect(
      transferOperationFromEvent({ altKey: true }, payload),
    ).toBe("move");
    expect(
      transferOperationFromEvent({ altKey: false }, payload),
    ).toBe("move");
  });

  it("uses Alt for copy when not from Temporary", () => {
    expect(
      transferOperationFromEvent(
        { altKey: true },
        { sourceIds: ["f-1"] },
      ),
    ).toBe("copy");
    expect(
      transferOperationFromEvent(
        { altKey: false },
        { sourceIds: ["f-1"] },
      ),
    ).toBe("move");
  });

  it("detects fromTemporary via marker MIME during dragover", () => {
    const dt = fakeDataTransfer({
      [UMEDIA_FILE_IDS_MIME]: "",
      [UMEDIA_FROM_TEMPORARY_MIME]: "1",
    });
    // Simulate browser clearing getData during dragover but keeping types.
    Object.defineProperty(dt, "types", {
      get: () => [UMEDIA_FILE_IDS_MIME, UMEDIA_FROM_TEMPORARY_MIME],
    });
    expect(
      transferOperationFromEvent({ altKey: true, dataTransfer: dt }),
    ).toBe("move");
  });
});
