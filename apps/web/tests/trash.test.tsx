import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TrashPage from "@/app/(dashboard)/trash/page";

const deletedFile = {
  uid: "file-1",
  owner_id: "admin-1",
  provider_connection_id: "provider-1",
  storage_object_id: "object-1",
  type: "file",
  name: "old-report.txt",
  parent_id: null,
  metadata: {},
  content_type: "text/plain",
  size: 2048,
  status: "completed",
  error: null,
  public_permission: "none",
  starred: false,
  permissions: [],
  workspace_id: null,
  access_at: "2026-08-01T00:00:00Z",
  deleted_at: "2026-08-10T00:00:00Z",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

function noContentResponse() {
  return Promise.resolve({
    ok: true,
    status: 204,
    json: async () => undefined,
  });
}

function pageOf(items: unknown[]) {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

describe("trash page", () => {
  afterEach(cleanup);

  it("lists trashed items with the 30-day warning and restores one", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files?scope=trash&limit=50&offset=0") && !init?.method) {
        return jsonResponse(pageOf([deletedFile]));
      }
      if (url.endsWith("/files/file-1/restore") && init?.method === "POST") {
        return jsonResponse({ ...deletedFile, deleted_at: null });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);

    expect(await screen.findByText("old-report.txt")).toBeInTheDocument();
    expect(
      screen.getByText(/permanently (deleted|removed) after 30 days/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Restore$/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-1/restore",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("deletes forever behind a confirm dialog", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files?scope=trash&limit=50&offset=0") && !init?.method) {
        return jsonResponse(pageOf([deletedFile]));
      }
      if (
        url.endsWith("/files/file-1?permanent=true")
        && init?.method === "DELETE"
      ) {
        return noContentResponse();
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Delete forever" }));

    // Nothing is deleted until the dialog is confirmed.
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/v1/files/file-1?permanent=true",
      expect.anything(),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Yes, delete forever" }),
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-1?permanent=true",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  it("restores every trash root from Restore all", async () => {
    const other = { ...deletedFile, uid: "file-2", name: "old-notes.md" };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/files?scope=trash") && !init?.method) {
        return jsonResponse(pageOf([deletedFile, other]));
      }
      if (url.endsWith("/files/file-1/restore") && init?.method === "POST") {
        return jsonResponse({ ...deletedFile, deleted_at: null });
      }
      if (url.endsWith("/files/file-2/restore") && init?.method === "POST") {
        return jsonResponse({ ...other, deleted_at: null });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Restore all" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-1/restore",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2/restore",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("deletes every trash root from Delete all after confirm", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/files?scope=trash") && !init?.method) {
        return jsonResponse(pageOf([deletedFile]));
      }
      if (
        url.endsWith("/files/file-1?permanent=true")
        && init?.method === "DELETE"
      ) {
        return noContentResponse();
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Delete all" }));
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/v1/files/file-1?permanent=true",
      expect.anything(),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Yes, delete all forever" }),
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-1?permanent=true",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  it("does not delete anything if Delete all is cancelled", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/files?scope=trash") && !init?.method) {
        return jsonResponse(pageOf([deletedFile]));
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Delete all" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("permanent=true"),
      expect.anything(),
    );
    expect(await screen.findByText("old-report.txt")).toBeInTheDocument();
  });

  it("Restore all also restores trash roots on later pages", async () => {
    const other = { ...deletedFile, uid: "file-2", name: "old-notes.md" };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/files?scope=trash") && url.includes("offset=0") && !init?.method) {
        return jsonResponse({
          items: [deletedFile],
          total: 2,
          limit: 50,
          offset: 0,
          has_more: true,
        });
      }
      if (url.includes("/files?scope=trash") && url.includes("offset=1") && !init?.method) {
        return jsonResponse({
          items: [other],
          total: 2,
          limit: 50,
          offset: 1,
          has_more: false,
        });
      }
      if (url.endsWith("/files/file-1/restore") && init?.method === "POST") {
        return jsonResponse({ ...deletedFile, deleted_at: null });
      }
      if (url.endsWith("/files/file-2/restore") && init?.method === "POST") {
        return jsonResponse({ ...other, deleted_at: null });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrashPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Restore all" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-1/restore",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2/restore",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("disables bulk actions when the trash is empty", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(pageOf([]))));
    render(<TrashPage />);
    expect(await screen.findByText("The trash is empty.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore all" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete all" })).toBeDisabled();
  });
});
