import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FilesPage from "@/app/(dashboard)/files/page";

const mockSearchParams = new URLSearchParams();
const mockRouter = { push: vi.fn(), replace: vi.fn() };

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/files",
  useSearchParams: () => mockSearchParams,
}));

const connections = [
  {
    uid: "provider-1",
    provider_type: "local",
    name: "Local files",
    status: "configured",
    enabled: true,
    import_existing: false,
    mirror_structure: false,
    created_at: "2026-08-11T00:00:00Z",
    last_tested_at: null,
    last_error: null,
  },
  {
    uid: "provider-2",
    provider_type: "s3",
    name: "Archive",
    status: "configured",
    enabled: true,
    import_existing: true,
    mirror_structure: false,
    created_at: "2026-08-11T00:00:00Z",
    last_tested_at: null,
    last_error: null,
  },
];

const mediaFile = {
  uid: "file-2",
  owner_id: "admin-1",
  provider_connection_id: "provider-2",
  storage_object_id: "object-2",
  type: "file",
  name: "from-archive.txt",
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
};

const folder = {
  uid: "folder-1",
  owner_id: "admin-1",
  provider_connection_id: "provider-1",
  storage_object_id: null,
  type: "folder",
  name: "Movies",
  parent_id: null,
  metadata: {},
  content_type: "inode/directory",
  size: 0,
  status: "completed",
  error: null,
  public_permission: "none",
  starred: false,
  permissions: [],
  workspace_id: null,
  access_at: "2026-08-11T00:00:00Z",
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
};

const nestedFile = {
  ...mediaFile,
  uid: "file-3",
  name: "inside.txt",
  parent_id: "folder-1",
};

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
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

describe("files page", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockSearchParams.delete("folder");
    mockSearchParams.delete("q");
    mockSearchParams.delete("sort");
    mockSearchParams.delete("order");
    mockRouter.push.mockClear();
    mockRouter.replace.mockClear();
  });

  it("shows a mixed-provider root from the files API", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("from-archive.txt")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Type" })).toBeInTheDocument();
    expect(screen.getByText("text/plain")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "from-archive.txt" })).toHaveAttribute(
      "href",
      "/api/v1/files/file-2/content/from-archive.txt",
    );
    expect(screen.getByRole("button", { name: "Add star" })).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files?sort=name&order=asc&limit=50&offset=0",
        expect.objectContaining({ credentials: "include" }),
      );
    });
    // A complete first page never renders a "Load more" affordance.
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("stars a file from the inline row control", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/providers") && !init?.method) {
        return jsonResponse(connections);
      }
      if (
        url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")
        && !init?.method
      ) {
        return jsonResponse(pageOf([mediaFile]));
      }
      if (url.endsWith("/files/file-2") && init?.method === "PATCH") {
        return jsonResponse({ ...mediaFile, starred: true });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Add star" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ starred: true }),
        }),
      );
    });
  });

  it("opens the Share dialog from the row menu", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    const trigger = await screen.findByRole("button", {
      name: "Actions for from-archive.txt",
    });
    fireEvent.pointerDown(trigger);
    fireEvent.click(trigger);

    fireEvent.click(await screen.findByText("Share…"));

    // The dialog with the three sharing sections replaces the old
    // direct "Share link" toggle -- nothing is mutated just by opening.
    expect(await screen.findByText("Permanent link")).toBeInTheDocument();
    expect(screen.getByText("Temporary link")).toBeInTheDocument();
    expect(screen.getByText("People")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/v1/files/file-2",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("appends the next offset page behind a Load more button", async () => {
    const secondFile = { ...mediaFile, uid: "file-9", name: "second-page.txt" };
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([mediaFile], { total: 2, has_more: true }));
      }
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=1")) {
        return jsonResponse(pageOf([secondFile], { total: 2, offset: 1 }));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("from-archive.txt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    // The new page appends -- the first page's rows stay put.
    expect(await screen.findByText("second-page.txt")).toBeInTheDocument();
    expect(screen.getByText("from-archive.txt")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Load more" }),
      ).not.toBeInTheDocument();
    });
  });

  it("links folders into ?folder= so browser history can go back", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([folder]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    const link = await screen.findByRole("link", { name: "Movies" });
    expect(link).toHaveAttribute(
      "href",
      "/files?folder=folder-1&sort=name&order=asc",
    );
    expect(screen.getByText("Folder")).toBeInTheDocument();
  });

  it("searches the library when the URL has q=", async () => {
    mockSearchParams.set("q", "archive report");
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (
        url.endsWith(
          "/files?q=archive+report&sort=name&order=asc&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("from-archive.txt")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files?q=archive+report&sort=name&order=asc&limit=50&offset=0",
        expect.objectContaining({ credentials: "include" }),
      );
    });
  });

  it("loads children when the URL already points at a folder", async () => {
    mockSearchParams.set("folder", "folder-1");
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.includes("/files/folder-1") && !url.includes("parent_id")) {
        return jsonResponse(folder);
      }
      if (
        url.includes(
          "/files?parent_id=folder-1&sort=name&order=asc&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([nestedFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("inside.txt")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Files" })).toHaveAttribute(
      "href",
      "/files?sort=name&order=asc",
    );
  });

  it("loads the explicit sort from the URL and sends it to the API", async () => {
    mockSearchParams.set("sort", "updated_at");
    mockSearchParams.set("order", "asc");
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (
        url.endsWith(
          "/files?sort=updated_at&order=asc&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("from-archive.txt")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Sort files" })).toHaveTextContent(
      "Last modified (oldest)",
    );
  });

  it("changes sort in the URL while preserving folder and search", async () => {
    mockSearchParams.set("folder", "folder-1");
    mockSearchParams.set("q", "report");
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.includes("/files/folder-1") && !url.includes("parent_id")) {
        return jsonResponse(folder);
      }
      if (
        url.includes(
          "/files?q=report&parent_id=folder-1&sort=name&order=asc&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([nestedFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);
    await screen.findByText("inside.txt");
    const trigger = screen.getByRole("combobox", { name: "Sort files" });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("option", { name: "Name (Z→A)" }));

    expect(mockRouter.replace).toHaveBeenCalledWith(
      "/files?folder=folder-1&q=report&sort=name&order=desc",
    );
  });
});
