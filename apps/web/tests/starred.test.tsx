import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StarredPage from "@/app/(dashboard)/starred/page";

const starredFolder = {
  uid: "folder-star",
  owner_id: "admin-1",
  provider_connection_id: null,
  storage_object_id: null,
  type: "folder",
  name: "Favorites",
  parent_id: null,
  metadata: {},
  content_type: "inode/directory",
  size: 0,
  status: "completed",
  error: null,
  public_permission: "none",
  starred: true,
  permissions: [],
  workspace_id: null,
  access_at: "2026-08-11T00:00:00Z",
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
};

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

function pageOf(items: unknown[]) {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

describe("starred page", () => {
  afterEach(cleanup);

  it("lists starred items and can remove a star", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files?scope=starred&limit=50&offset=0") && !init?.method) {
        return jsonResponse(pageOf([starredFolder]));
      }
      if (url.endsWith("/files/folder-star") && init?.method === "PATCH") {
        return jsonResponse({ ...starredFolder, starred: false });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<StarredPage />);

    expect(await screen.findByText("Favorites")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Favorites" })).toHaveAttribute(
      "href",
      "/files?folder=folder-star",
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove star" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/folder-star",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ starred: false }),
        }),
      );
    });
  });
});
