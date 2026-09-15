import { describe, expect, it } from "vitest";

import {
  dataTransferHasFiles,
  ensureUploadFileName,
  filesFromDataTransfer,
  isEditableTarget,
} from "@/lib/incoming-files";

describe("incoming files", () => {
  it("reads files from a drop or paste DataTransfer", () => {
    const file = new File(["hello"], "note.txt", { type: "text/plain" });
    const data = {
      files: [file] as unknown as FileList,
      items: [
        {
          kind: "file" as const,
          type: "text/plain",
          getAsFile: () => file,
        },
      ],
    };
    expect(filesFromDataTransfer(data).map((item) => item.name)).toEqual([
      "note.txt",
    ]);
    expect(dataTransferHasFiles({ types: ["Files"] })).toBe(true);
    expect(dataTransferHasFiles({ types: ["text/plain"] })).toBe(false);
  });

  it("skips directory entries when the browser exposes them", () => {
    const file = new File(["x"], "inside.txt", { type: "text/plain" });
    const data = {
      files: [] as unknown as FileList,
      items: [
        {
          kind: "file" as const,
          type: "",
          getAsFile: () => new File([], "Movies"),
          webkitGetAsEntry: () => ({ isDirectory: true, isFile: false }),
        },
        {
          kind: "file" as const,
          type: "text/plain",
          getAsFile: () => file,
          webkitGetAsEntry: () => ({ isDirectory: false, isFile: true }),
        },
      ],
    };
    expect(filesFromDataTransfer(data).map((item) => item.name)).toEqual([
      "inside.txt",
    ]);
  });

  it("names a nameless clipboard image so tus metadata has a filename", () => {
    const blob = new File(["png"], "", { type: "image/png" });
    expect(ensureUploadFileName(blob).name).toBe("pasted.png");
    const named = new File(["png"], "image.png", { type: "image/png" });
    expect(ensureUploadFileName(named).name).toBe("image.png");
  });

  it("does not steal paste from text fields", () => {
    const input = document.createElement("input");
    const wrapper = document.createElement("div");
    wrapper.append(input);
    expect(isEditableTarget(input)).toBe(true);
    expect(isEditableTarget(wrapper)).toBe(false);
  });
});
