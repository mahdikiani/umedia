import { describe, expect, it } from "vitest";

import type { MediaFileItem } from "@/lib/api";
import {
  groupFiles,
  modifiedGroupLabel,
  nextSortClick,
  parseFileSort,
  parseFilesView,
  parseGroupBy,
  typeGroupLabel,
} from "@/lib/files-view";

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

describe("files view helpers", () => {
  it("defaults sort to name ascending and group to none", () => {
    expect(parseFileSort(null, null)).toEqual({ sort: "name", order: "asc" });
    expect(parseGroupBy(null)).toBe("none");
    expect(parseFilesView(null)).toBe("list");
    expect(parseFilesView("cards")).toBe("cards");
  });

  it("defaults updated_at to descending when order is omitted", () => {
    expect(parseFileSort("updated_at", null)).toEqual({
      sort: "updated_at",
      order: "desc",
    });
  });

  it("toggles the active column and otherwise uses that column's default", () => {
    expect(nextSortClick("name", "asc", "name")).toEqual({
      sort: "name",
      order: "desc",
    });
    expect(nextSortClick("name", "asc", "updated_at")).toEqual({
      sort: "updated_at",
      order: "desc",
    });
    expect(nextSortClick("name", "asc", "size")).toEqual({
      sort: "size",
      order: "desc",
    });
  });

  it("groups folders and MIME types without reordering within a group", () => {
    const folder = file({
      uid: "folder-1",
      type: "folder",
      name: "Movies",
      content_type: "inode/directory",
    });
    const pdf = file({
      uid: "file-2",
      name: "doc.pdf",
      content_type: "application/pdf",
    });
    const txt = file({ uid: "file-3", name: "a.txt" });
    const groups = groupFiles([folder, pdf, txt], "type");
    expect(groups.map((group) => group.label)).toEqual([
      "Folders",
      "PDF",
      "TXT",
    ]);
    expect(typeGroupLabel(folder)).toBe("Folders");
  });

  it("buckets modified dates relative to now", () => {
    const now = new Date(2026, 7, 18, 12);
    expect(modifiedGroupLabel(new Date(2026, 7, 18, 8).toISOString(), now)).toBe(
      "Today",
    );
    expect(modifiedGroupLabel(new Date(2026, 7, 17, 8).toISOString(), now)).toBe(
      "Yesterday",
    );
    expect(modifiedGroupLabel(new Date(2026, 7, 12, 8).toISOString(), now)).toBe(
      "Previous 7 days",
    );
    expect(modifiedGroupLabel(new Date(2026, 7, 1, 8).toISOString(), now)).toBe(
      "This month",
    );
    expect(modifiedGroupLabel(new Date(2026, 0, 1, 8).toISOString(), now)).toBe(
      "Older",
    );
  });
});
