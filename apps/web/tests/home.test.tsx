import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/(dashboard)/home/page";
import { LocaleProvider } from "@/components/locale-provider";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/home",
}));

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

const recentFile = {
  uid: "file-recent",
  owner_id: "admin-1",
  provider_connection_id: "conn-1",
  storage_object_id: "obj-1",
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
  access_at: "2026-08-17T00:00:00Z",
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

const recentFolder = {
  ...recentFile,
  uid: "folder-recent",
  type: "folder",
  name: "Projects",
  size: 0,
  content_type: "inode/directory",
  storage_object_id: null,
};

describe("home page", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(
        JSON.stringify({
          files: [{ uid: "file-recent", name: "notes.txt", type: "file", at: 1 }],
          folders: [{ uid: "folder-recent", name: "Projects", type: "folder", at: 2 }],
        }),
      ),
      setItem: vi.fn(),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/files/file-recent")) return jsonResponse(recentFile);
        if (url.endsWith("/files/folder-recent")) return jsonResponse(recentFolder);
        return jsonResponse({});
      }),
    );
  });

  it("lists last used files and last opened folders", async () => {
    render(
      <LocaleProvider>
        <HomePage />
      </LocaleProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByText("Recent files")).toBeInTheDocument();
    expect(screen.getByText("Recent folders")).toBeInTheDocument();
    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Projects/ })).toHaveAttribute(
      "href",
      "/files?folder=folder-recent",
    );
  });
});
