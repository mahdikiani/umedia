import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FilesPage from "@/app/(dashboard)/files/page";

const mockSearchParams = new URLSearchParams();
const mockRouter = { push: vi.fn(), replace: vi.fn() };

const tusUploadMock = vi.hoisted(() => {
  const constructed: Array<{
    file: File;
    metadata: Record<string, string>;
    start: ReturnType<typeof vi.fn>;
  }> = [];
  class Upload {
    start = vi.fn();
    abort = vi.fn();
    constructor(file: File, options: { metadata: Record<string, string> }) {
      constructed.push({ file, metadata: options.metadata, start: this.start });
    }
  }
  return { constructed, Upload };
});

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/files",
  useSearchParams: () => mockSearchParams,
}));

vi.mock("tus-js-client", () => ({
  Upload: tusUploadMock.Upload,
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

/** New transfer/Temporary endpoints always fire on mount. */
function commonExtras(url: string) {
  if (url.includes("/files/temporary")) return jsonResponse([]);
  if (url.includes("/files/transfers")) return jsonResponse([]);
  return null;
}

describe("files page", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockSearchParams.delete("folder");
    mockSearchParams.delete("q");
    mockSearchParams.delete("sort");
    mockSearchParams.delete("order");
    mockSearchParams.delete("group");
    mockSearchParams.delete("view");
    mockSearchParams.delete("type");
    mockRouter.push.mockClear();
    mockRouter.replace.mockClear();
    tusUploadMock.constructed.length = 0;
    mockRouter.replace.mockImplementation((href: string) => {
      const url = new URL(href, "http://localhost");
      for (const key of [...mockSearchParams.keys()]) {
        mockSearchParams.delete(key);
      }
      url.searchParams.forEach((value, key) => {
        mockSearchParams.set(key, value);
      });
    });
  });

  it("shows a mixed-provider root from the files API", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("from-archive.txt")).toBeInTheDocument();
    expect(screen.getByText("Archive")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Type" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "TXT" })).toBeInTheDocument();
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
      const extra = commonExtras(url);
      if (extra) return extra;
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
      const extra = commonExtras(url);
      if (extra) return extra;
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

  it("adds a file pointer to Temporary without creating a transfer", async () => {
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (
          url.endsWith("/files/temporary") &&
          init?.method === "POST"
        ) {
          return Promise.resolve({
            ok: true,
            status: 204,
            json: async () => null,
          });
        }
        const extra = commonExtras(url);
        if (extra) return extra;
        if (url.endsWith("/providers")) return jsonResponse(connections);
        if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
          return jsonResponse(pageOf([mediaFile]));
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    const trigger = await screen.findByRole("button", {
      name: "Actions for from-archive.txt",
    });
    fireEvent.pointerDown(trigger);
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByText("Add to Temporary"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/temporary",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ media_file_ids: ["file-2"] }),
        }),
      );
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/files/transfers") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("appends the next offset page behind a Load more button", async () => {
    const secondFile = { ...mediaFile, uid: "file-9", name: "second-page.txt" };
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
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
      const extra = commonExtras(url);
      if (extra) return extra;
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
      const extra = commonExtras(url);
      if (extra) return extra;
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
      const extra = commonExtras(url);
      if (extra) return extra;
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
      const extra = commonExtras(url);
      if (extra) return extra;
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
    expect(screen.getByRole("columnheader", { name: "Modified" })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("changes sort in the URL while preserving folder and search", async () => {
    mockSearchParams.set("folder", "folder-1");
    mockSearchParams.set("q", "report");
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
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
      if (
        url.includes(
          "/files?q=report&parent_id=folder-1&sort=name&order=desc&limit=50&offset=0",
        )
      ) {
        return jsonResponse(pageOf([nestedFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);
    await screen.findByText("inside.txt");
    fetchMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Name" }));

    expect(mockRouter.replace).toHaveBeenCalledWith(
      "/files?folder=folder-1&q=report&sort=name&order=desc",
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(
          "/files?q=report&parent_id=folder-1&sort=name&order=desc",
        ),
        expect.anything(),
      );
    });
    expect(screen.getByRole("columnheader", { name: "Name" })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("groups the current page by type on the client", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([folder, mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);
    await screen.findByText("from-archive.txt");

    fireEvent.click(screen.getByRole("combobox", { name: "Group files" }));
    fireEvent.click(await screen.findByRole("option", { name: "Type" }));

    expect(mockRouter.replace).toHaveBeenCalledWith("/files?group=type");
    expect(await screen.findByText("Folders")).toBeInTheDocument();
  });

  it("renders type group headings when group=type is in the URL", async () => {
    mockSearchParams.set("group", "type");
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([folder, mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByText("Folders")).toBeInTheDocument();
    expect(screen.getAllByText("TXT").length).toBeGreaterThan(1);
    expect(screen.getByText("Movies")).toBeInTheDocument();
    expect(screen.getByText("from-archive.txt")).toBeInTheDocument();
  });

  it("filters the listing when a MIME chip is clicked", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([folder, mediaFile]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);
    await screen.findByText("from-archive.txt");

    fireEvent.click(screen.getByRole("button", { name: "TXT" }));

    expect(mockRouter.replace).toHaveBeenCalledWith("/files?type=text%2Fplain");
    expect(screen.queryByText("Movies")).not.toBeInTheDocument();
    expect(screen.getByText("from-archive.txt")).toBeInTheDocument();
  });

  it("switches to a card grid with MIME thumbnails", async () => {
    const jpeg = {
      ...mediaFile,
      uid: "file-img",
      name: "shot.jpg",
      content_type: "image/jpeg",
    };
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse(pageOf([folder, mediaFile, jpeg]));
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);
    await screen.findByText("from-archive.txt");

    fireEvent.click(screen.getByRole("button", { name: "Card view" }));

    expect(mockRouter.replace).toHaveBeenCalledWith("/files?view=cards");
    expect(screen.queryByRole("columnheader", { name: "Type" })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "from-archive.txt" })).toHaveAttribute(
      "src",
      "/api/v1/statics/text_color_txt.svg",
    );
    expect(screen.getByRole("img", { name: "shot.jpg" })).toHaveAttribute(
      "src",
      "/api/v1/files/file-img/content/shot.jpg",
    );
    expect(screen.getByRole("img", { name: "Movies" })).toHaveAttribute(
      "src",
      "/api/v1/statics/folder-1485.svg",
    );
    expect(document.querySelector("[data-kind=folder]")).toBeInTheDocument();
    expect(document.querySelector("[data-kind=file]")).toBeInTheDocument();
  });

  function listingFetch() {
    return vi.fn((input: string | URL | Request) => {
      const url = String(input);
      const extra = commonExtras(url);
      if (extra) return extra;
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.includes("/files?")) return jsonResponse(pageOf([mediaFile]));
      throw new Error(`Unexpected request: ${url}`);
    });
  }


  function fileTransfer(file: File) {
    return {
      types: ["Files"],
      files: [file],
      items: [
        {
          kind: "file",
          type: file.type,
          getAsFile: () => file,
        },
      ],
    };
  }

  it("uploads a file dropped onto the page", async () => {
    vi.stubGlobal("fetch", listingFetch());
    render(<FilesPage />);
    await screen.findByText("from-archive.txt");

    const file = new File(["hello"], "dropped.txt", { type: "text/plain" });
    fireEvent.drop(window, { dataTransfer: fileTransfer(file) });

    await waitFor(() => expect(tusUploadMock.constructed).toHaveLength(1));
    expect(tusUploadMock.constructed[0].file.name).toBe("dropped.txt");
    expect(tusUploadMock.constructed[0].metadata.name).toBe("dropped.txt");
    expect(tusUploadMock.constructed[0].metadata).not.toHaveProperty(
      "provider_connection_id",
    );
    expect(tusUploadMock.constructed[0].start).toHaveBeenCalled();
  });

  it("uploads a file pasted from the clipboard", async () => {
    vi.stubGlobal("fetch", listingFetch());
    render(<FilesPage />);
    await screen.findByText("from-archive.txt");

    const file = new File(["png"], "image.png", { type: "image/png" });
    fireEvent.paste(window, { clipboardData: fileTransfer(file) });

    await waitFor(() => expect(tusUploadMock.constructed).toHaveLength(1));
    expect(tusUploadMock.constructed[0].file.name).toBe("image.png");
    expect(tusUploadMock.constructed[0].start).toHaveBeenCalled();
  });

  it("does not upload a paste while naming a folder", async () => {
    vi.stubGlobal("fetch", listingFetch());
    render(<FilesPage />);
    await screen.findByText("from-archive.txt");

    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    const name = await screen.findByLabelText("Name");
    const file = new File(["png"], "image.png", { type: "image/png" });
    fireEvent.paste(name, { clipboardData: fileTransfer(file) });

    expect(tusUploadMock.constructed).toHaveLength(0);
  });
});
