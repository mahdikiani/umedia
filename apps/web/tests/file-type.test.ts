import { describe, expect, it } from "vitest";

import type { MediaFileItem } from "@/lib/api";
import {
  fileGlyph,
  fileIconUrl,
  filePreviewKind,
  filePreviewSrc,
  matchesTypeFilter,
  mimeChipLabel,
  typeFilterKey,
} from "@/lib/file-type";

const file = (overrides: Partial<MediaFileItem> = {}): MediaFileItem => ({
  uid: "file-1",
  owner_id: "admin-1",
  provider_connection_id: "provider-1",
  storage_object_id: "object-1",
  type: "file",
  name: "notes.txt",
  parent_id: null,
  metadata: {},
  content_type: "text/plain",
  size: 12,
  status: "completed",
  error: null,
  public_permission: "none",
  starred: false,
  permissions: [],
  workspace_id: null,
  access_at: "2026-08-11T00:00:00Z",
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  ...overrides,
});

describe("file type chips and thumbnails", () => {
  it("turns MIME types into short chip labels", () => {
    expect(mimeChipLabel(file())).toBe("TXT");
    expect(mimeChipLabel(file({ content_type: "application/pdf" }))).toBe("PDF");
    expect(mimeChipLabel(file({ content_type: "image/jpeg" }))).toBe("JPEG");
    expect(
      mimeChipLabel(
        file({ type: "folder", content_type: "inode/directory", name: "Movies" }),
      ),
    ).toBe("Folder");
  });

  it("never shows application/octet-stream; PDFs are PDF even when indexed as binary", () => {
    const pdf = file({
      name: "report.pdf",
      content_type: "application/octet-stream",
    });
    expect(mimeChipLabel(pdf)).toBe("PDF");
    expect(mimeChipLabel(pdf)).not.toMatch(/octet|application\//i);
    expect(fileGlyph(pdf)).toBe("pdf");
    expect(fileIconUrl(pdf)).toBe("/api/v1/statics/text_color_pdf.svg");
    expect(typeFilterKey(pdf)).toBe("application/pdf");

    const nameless = file({
      name: "blob",
      content_type: "application/octet-stream",
    });
    expect(mimeChipLabel(nameless)).toBe("File");
  });

  it("uses local MIME SVGs, with a content URL for image/video previews", () => {
    const txt = file();
    expect(filePreviewKind(txt)).toBe("icon");
    expect(fileIconUrl(txt)).toBe("/api/v1/statics/text_color_txt.svg");
    expect(filePreviewSrc(txt)).toBeNull();

    const jpeg = file({
      uid: "img-1",
      name: "shot.jpg",
      content_type: "image/jpeg",
    });
    expect(filePreviewKind(jpeg)).toBe("image");
    expect(filePreviewSrc(jpeg)).toBe("/api/v1/files/img-1/content/shot.jpg");
    expect(fileIconUrl(jpeg)).toBe("/api/v1/statics/text_color_jpeg.svg");

    const mp4 = file({
      uid: "vid-1",
      name: "clip.mp4",
      content_type: "video/mp4",
    });
    expect(filePreviewKind(mp4)).toBe("video");
    expect(filePreviewSrc(mp4)).toBe("/api/v1/files/vid-1/content/clip.mp4");
    expect(
      fileIconUrl(file({ type: "folder", name: "Movies" })),
    ).toBe("/api/v1/statics/folder-1485.svg");
  });

  it("filters the current folder by chip type", () => {
    const folder = file({
      uid: "folder-1",
      type: "folder",
      name: "Movies",
      content_type: "inode/directory",
    });
    const txt = file();
    expect(typeFilterKey(folder)).toBe("folder");
    expect(typeFilterKey(txt)).toBe("text/plain");
    expect(matchesTypeFilter(txt, "text/plain")).toBe(true);
    expect(matchesTypeFilter(folder, "text/plain")).toBe(false);
    expect(matchesTypeFilter(folder, "folder")).toBe(true);
    expect(matchesTypeFilter(txt, null)).toBe(true);
  });
});
