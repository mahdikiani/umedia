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

vi.mock("tus-js-client", () => ({
  Upload: class {
    start = vi.fn();
    abort = vi.fn();
  },
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
];

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

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function pageOf(items: unknown[]) {
  return {
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    has_more: false,
  };
}

describe("dual pane", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

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
    localStorage.clear();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        const url = String(input);
        if (url.endsWith("/providers")) return jsonResponse(connections);
        if (url.includes("/files/temporary")) return jsonResponse([]);
        if (url.includes("/files/transfers")) return jsonResponse([]);
        if (url.includes("/files?")) return jsonResponse(pageOf([folder]));
        if (url.match(/\/files\/[^/?]+$/)) {
          const uid = url.split("/").pop();
          if (uid === "folder-1") return jsonResponse(folder);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  it("toggles Two columns to show two panes", async () => {
    render(<FilesPage />);

    expect(await screen.findByTestId("file-pane-primary")).toBeInTheDocument();
    expect(screen.queryByTestId("file-pane-secondary")).not.toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /Two columns/i });
    expect(toggle).toHaveAttribute(
      "title",
      "Show two folders side by side for drag-and-drop",
    );
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByTestId("file-pane-secondary")).toBeInTheDocument();
    });
    expect(localStorage.getItem("umedia.files.dualPane")).toBe("1");
  });

  it("resets a missing secondary folder to root so the right pane is not blank", async () => {
    localStorage.setItem("umedia.files.dualPane", "1");
    localStorage.setItem("umedia.files.secondaryFolder", "gone-folder");

    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/providers")) return jsonResponse(connections);
      if (url.includes("/files/temporary")) return jsonResponse([]);
      if (url.includes("/files/transfers")) return jsonResponse([]);
      if (url.includes("/files/gone-folder")) {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({ message: "not found" }),
        });
      }
      if (url.includes("parent_id=gone-folder")) {
        return jsonResponse(pageOf([]));
      }
      if (url.includes("/files?")) return jsonResponse(pageOf([folder]));
      if (url.match(/\/files\/[^/?]+$/)) {
        const uid = url.split("/").pop();
        if (uid === "folder-1") return jsonResponse(folder);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesPage />);

    expect(await screen.findByTestId("file-pane-secondary")).toBeInTheDocument();

    await waitFor(() => {
      expect(localStorage.getItem("umedia.files.secondaryFolder")).toBeNull();
    });

    await waitFor(() => {
      const secondary = screen.getByTestId("file-pane-secondary");
      expect(secondary).toHaveTextContent("Movies");
    });
  });
});
