import "@testing-library/jest-dom/vitest";

import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TemporaryDock } from "@/components/temporary-dock";
import { renderWithLocale as render } from "@/tests/render-with-locale";
import {
  UMEDIA_FILE_IDS_MIME,
  UMEDIA_FROM_TEMPORARY_MIME,
} from "@/lib/umedia-dnd";

const stashedItem = {
  uid: "stashed-1",
  owner_id: "admin-1",
  provider_connection_id: null,
  storage_object_id: null,
  type: "file" as const,
  name: "clip.mp4",
  parent_id: null,
  metadata: {},
  content_type: "video/mp4",
  size: 10,
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

describe("TemporaryDock", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/files/temporary") && !init?.method) {
          return jsonResponse([]);
        }
        if (url.includes("/files/transfers") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return jsonResponse(
            {
              uid: "job-temp-1",
              operation: body.operation,
              status: "queued",
              source_ids: body.source_ids,
              dest_parent_id: body.dest_parent_id,
              total_items: 1,
              done_items: 0,
              failed_items: 0,
              progress_pct: 0,
              current_name: null,
              error: null,
              created_at: "2026-08-11T00:00:00Z",
              started_at: null,
              finished_at: null,
            },
            202,
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  it("adds pointers to Temporary on drop and collapses to auto width", async () => {
    const onTransferCreated = vi.fn();
    const addToTemporary = vi.fn().mockResolvedValue(undefined);
    render(
      <TemporaryDock
        addToTemporary={addToTemporary}
        currentParentId={null}
        onTransferCreated={onTransferCreated}
      />,
    );

    const dock = await screen.findByTestId("temporary-dock");
    expect(dock.className).toContain("w-72");
    expect(
      screen.getByText(/Pointer clipboard for moves and copies/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Pointer clipboard")).toBeInTheDocument();
    expect(screen.getByText(/drag and drop items into this panel/i)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /Collapse Temporary/i }),
    );
    await waitFor(() => {
      expect(dock).toHaveAttribute("data-collapsed", "true");
      expect(dock.className).toContain("w-auto");
      expect(dock.className).not.toContain("w-72");
    });

    fireEvent.click(screen.getByRole("button", { name: /Expand Temporary/i }));

    const dataTransfer = {
      types: [UMEDIA_FILE_IDS_MIME],
      getData: (type: string) =>
        type === UMEDIA_FILE_IDS_MIME
          ? JSON.stringify({ sourceIds: ["file-9"] })
          : "",
      setData: vi.fn(),
      dropEffect: "none",
      effectAllowed: "copyMove",
    };

    fireEvent.dragOver(dock, { dataTransfer });
    expect(dataTransfer.dropEffect).toBe("copy");

    fireEvent.drop(dock, { dataTransfer });

    await waitFor(() => {
      expect(addToTemporary).toHaveBeenCalledWith(["file-9"]);
    });
    expect(screen.queryByText(/Copying/i)).not.toBeInTheDocument();
  });

  it("Paste here (move) calls createTransfer with move + currentParentId", async () => {
    const onTransferCreated = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/files/temporary") && !init?.method) {
          return jsonResponse([stashedItem]);
        }
        if (url.includes("/files/transfers") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return jsonResponse(
            {
              uid: "job-paste-1",
              operation: body.operation,
              status: "queued",
              source_ids: body.source_ids,
              dest_parent_id: body.dest_parent_id,
              total_items: body.source_ids.length,
              done_items: 0,
              failed_items: 0,
              progress_pct: 0,
              current_name: null,
              error: null,
              created_at: "2026-08-11T00:00:00Z",
              started_at: null,
              finished_at: null,
            },
            202,
          );
        }
        if (url.endsWith("/files/temporary") && init?.method === "DELETE") {
          return jsonResponse(null, 204);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <TemporaryDock
        addToTemporary={vi.fn()}
        currentParentId="folder-primary"
        onTransferCreated={onTransferCreated}
      />,
    );

    expect(await screen.findByText("clip.mp4")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /Paste here \(move\)/i }),
    );

    await waitFor(() => {
      expect(onTransferCreated).toHaveBeenCalled();
    });
    const fetchMock = vi.mocked(fetch);
    const transferCall = fetchMock.mock.calls.find(([input, init]) => {
      return (
        String(input).includes("/files/transfers") &&
        (init as RequestInit | undefined)?.method === "POST"
      );
    });
    expect(transferCall).toBeTruthy();
    const body = JSON.parse(String((transferCall?.[1] as RequestInit).body));
    expect(body.operation).toBe("move");
    expect(body.dest_parent_id).toBe("folder-primary");
    expect(body.source_ids).toEqual(["stashed-1"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/files/temporary",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("disables moving items back into their current folder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        if (String(input).endsWith("/files/temporary")) {
          return jsonResponse([stashedItem]);
        }
        throw new Error(`Unexpected request: ${String(input)}`);
      }),
    );
    render(
      <TemporaryDock
        addToTemporary={vi.fn()}
        currentParentId={null}
        onTransferCreated={vi.fn()}
      />,
    );

    expect(await screen.findByText("clip.mp4")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Paste here (move)" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Paste here (copy)" }),
    ).toBeEnabled();
  });

  it("clears every Temporary pointer", async () => {
    let pointers = [stashedItem];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/files/temporary") && init?.method === "DELETE") {
          pointers = [];
          return jsonResponse(null, 204);
        }
        if (url.endsWith("/files/temporary") && !init?.method) {
          return jsonResponse(pointers);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <TemporaryDock
        addToTemporary={vi.fn()}
        currentParentId={null}
        onTransferCreated={vi.fn()}
      />,
    );

    await screen.findByText("clip.mp4");
    fireEvent.click(screen.getByRole("button", { name: "Clear Temporary" }));

    expect(
      await screen.findByText(/Pointer clipboard for moves and copies/i),
    ).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/files/temporary",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("removes one Temporary pointer without deleting the file", async () => {
    let pointers = [stashedItem];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (
          url.endsWith("/files/temporary/stashed-1") &&
          init?.method === "DELETE"
        ) {
          pointers = [];
          return jsonResponse(null, 204);
        }
        if (url.endsWith("/files/temporary") && !init?.method) {
          return jsonResponse(pointers);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <TemporaryDock
        addToTemporary={vi.fn()}
        currentParentId={null}
        onTransferCreated={vi.fn()}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Remove clip.mp4 from Temporary",
      }),
    );

    await waitFor(() => {
      expect(screen.queryByText("clip.mp4")).not.toBeInTheDocument();
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/files/temporary/stashed-1",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/v1/files/stashed-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("shows collapsed badge when Temporary has items", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        const url = String(input);
        if (url.endsWith("/files/temporary")) {
          return jsonResponse([stashedItem]);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <TemporaryDock
        addToTemporary={vi.fn()}
        currentParentId={null}
        onTransferCreated={vi.fn()}
      />,
    );

    await screen.findByText("clip.mp4");
    fireEvent.click(
      screen.getByRole("button", { name: /Collapse Temporary/i }),
    );
    expect(await screen.findByTestId("temporary-badge")).toHaveTextContent("1");
  });

  it("marks drags from Temporary with fromTemporary payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        const url = String(input);
        if (url.endsWith("/files/temporary")) {
          return jsonResponse([stashedItem]);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <TemporaryDock
        addToTemporary={vi.fn()}
        currentParentId={null}
        onTransferCreated={vi.fn()}
      />,
    );
    const row = await screen.findByTitle("clip.mp4");
    const store = new Map<string, string>();
    const dataTransfer = {
      get types() {
        return Array.from(store.keys());
      },
      getData: (type: string) => store.get(type) ?? "",
      setData: (type: string, value: string) => {
        store.set(type, value);
      },
      effectAllowed: "uninitialized",
      dropEffect: "none",
    };

    fireEvent.dragStart(row, { dataTransfer });
    expect(store.get(UMEDIA_FROM_TEMPORARY_MIME)).toBe("1");
    expect(JSON.parse(store.get(UMEDIA_FILE_IDS_MIME)!)).toEqual({
      sourceIds: ["stashed-1"],
      sourceConnectionIds: [null],
      fromTemporary: true,
    });
  });
});
