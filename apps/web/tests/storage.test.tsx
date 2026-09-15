import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StoragePage from "@/app/(dashboard)/storage/page";

const mockSearchParams = new URLSearchParams();
const mockRouter = { push: vi.fn(), replace: vi.fn() };

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams,
}));

const connection = {
  uid: "provider-1",
  provider_type: "local",
  name: "Local files",
  status: "configured",
  enabled: true,
  import_existing: true,
  mirror_structure: false,
  created_at: "2026-08-11T00:00:00Z",
  last_tested_at: null,
  last_error: null,
};

const object = {
  uid: "object-1",
  provider_connection_id: "provider-1",
  content_reference: "archive/report.pdf",
  provider_parent_ref: "archive",
  type: "file",
  name: "report.pdf",
  content_hash: null,
  content_type: "application/pdf",
  size: 2048,
  metadata: {},
  status: "active",
  last_seen_at: "2026-08-11T00:00:00Z",
};

const folder = {
  ...object,
  uid: "object-folder",
  content_reference: "archive",
  provider_parent_ref: null,
  type: "folder",
  name: "Archive",
  content_type: "inode/directory",
  size: 0,
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function pageOf(items: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    has_more: false,
    ...overrides,
  };
}

function mockStorageFetch(
  handlers: (url: string, init?: RequestInit) => Promise<unknown> | null,
  syncStatus: "idle" | "running" = "idle",
) {
  return vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/providers") && !init?.method) {
      return jsonResponse([connection]);
    }
    if (url.endsWith("/providers/provider-1/sync") && !init?.method) {
      return jsonResponse({ status: syncStatus, connection_id: "provider-1" });
    }
    const handled = handlers(url, init);
    if (handled) return handled;
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
  });
}

describe("storage page", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockSearchParams.delete("provider");
    mockSearchParams.delete("parent");
    mockSearchParams.delete("q");
    mockRouter.push.mockClear();
    mockRouter.replace.mockClear();
  });

  it("lists provider objects and accepts async sync", async () => {
    const fetchMock = mockStorageFetch((url, init) => {
      if (
        url.endsWith("/providers/provider-1/objects?only_parent=true&limit=50&offset=0")
      ) {
        return jsonResponse(pageOf([object]));
      }
      if (url.endsWith("/providers/provider-1/sync") && init?.method === "POST") {
        return jsonResponse({ status: "started", connection_id: "provider-1" }, 202);
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<StoragePage />);

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("PDF")).toBeInTheDocument();
    expect(screen.getByText("archive/report.pdf")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Provider connection" })).toHaveTextContent(
      "Local files",
    );
    expect(screen.queryByText("provider-1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/provider-1/sync",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("keeps Sync spinning and disabled while a background job is running", async () => {
    const fetchMock = mockStorageFetch((url) => {
      if (
        url.endsWith("/providers/provider-1/objects?only_parent=true&limit=50&offset=0")
      ) {
        return jsonResponse(pageOf([object]));
      }
      return null;
    }, "running");
    vi.stubGlobal("fetch", fetchMock);

    render(<StoragePage />);

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Syncing…" })).toBeDisabled();
    });
  });

  it("links folders to their provider parent reference", async () => {
    const fetchMock = mockStorageFetch((url) => {
      if (
        url.endsWith("/providers/provider-1/objects?only_parent=true&limit=50&offset=0")
      ) {
        return jsonResponse(pageOf([folder]));
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<StoragePage />);

    expect(await screen.findByRole("link", { name: "Archive" })).toHaveAttribute(
      "href",
      "/storage?provider=provider-1&parent=archive",
    );
    expect(screen.getByText("Folder")).toBeInTheDocument();
  });

  it("browses the parent from URL state and searches within that subtree", async () => {
    mockSearchParams.set("provider", "provider-1");
    mockSearchParams.set("parent", "archive");
    const fetchMock = mockStorageFetch((url) => {
      if (
        url.endsWith(
          "/providers/provider-1/objects?only_parent=true&parent_ref=archive&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([object]));
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<StoragePage />);

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/provider-1/objects?only_parent=true&parent_ref=archive&limit=50&offset=0",
        expect.objectContaining({ credentials: "include" }),
      );
    });
  });

  it("searches the current storage subtree when q is in the URL", async () => {
    mockSearchParams.set("provider", "provider-1");
    mockSearchParams.set("parent", "archive");
    mockSearchParams.set("q", "annual report");
    const fetchMock = mockStorageFetch((url) => {
      if (
        url.endsWith(
          "/providers/provider-1/objects?q=annual+report&parent_ref=archive&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([object]));
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<StoragePage />);

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/provider-1/objects?q=annual+report&parent_ref=archive&limit=50&offset=0",
        expect.objectContaining({ credentials: "include" }),
      );
    });
  });
});
