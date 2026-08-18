import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShareDialog } from "@/components/share-dialog";
import type { MediaFileItem } from "@/lib/api";

const baseItem: MediaFileItem = {
  uid: "file-2",
  owner_id: "admin-1",
  provider_connection_id: "provider-1",
  storage_object_id: "object-2",
  type: "file",
  name: "report.txt",
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

function jsonResponse(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => body });
}

describe("share dialog", () => {
  afterEach(cleanup);

  beforeEach(() => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
  });

  it("shows the three sharing sections", async () => {
    vi.stubGlobal("fetch", vi.fn());

    render(
      <ShareDialog item={baseItem} onItemUpdated={vi.fn()} onOpenChange={vi.fn()} />,
    );

    expect(await screen.findByText("Permanent link")).toBeInTheDocument();
    expect(screen.getByText("Temporary link")).toBeInTheDocument();
    expect(screen.getByText("People")).toBeInTheDocument();
  });

  it("toggles the permanent link on and shows a copyable URL", async () => {
    const updated = { ...baseItem, public_permission: "read" as const };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files/file-2") && init?.method === "PATCH") {
        return jsonResponse(updated);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onItemUpdated = vi.fn();

    const view = render(
      <ShareDialog item={baseItem} onItemUpdated={onItemUpdated} onOpenChange={vi.fn()} />,
    );

    expect(screen.queryByDisplayValue(/\/api\/v1\/f\/file-2/)).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Make public" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ public_permission: "read" }),
        }),
      );
    });
    expect(onItemUpdated).toHaveBeenCalledWith(updated);

    // Re-render with the updated item (the page owns the item state).
    view.rerender(
      <ShareDialog item={updated} onItemUpdated={onItemUpdated} onOpenChange={vi.fn()} />,
    );
    expect(
      screen.getByDisplayValue(/\/api\/v1\/f\/file-2\/report\.txt$/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make private" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy permanent link" }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringMatching(/\/api\/v1\/f\/file-2\/report\.txt$/),
      );
    });

    const openLink = screen.getByRole("link", { name: "Open permanent link" });
    expect(openLink).toHaveAttribute(
      "href",
      expect.stringMatching(/\/api\/v1\/f\/file-2\/report\.txt$/),
    );
    expect(openLink).toHaveAttribute("target", "_blank");
  });

  it("generates a temporary link for the selected duration preset", async () => {
    // Server returns a path-absolute SigV4-presigned S3 GET; the dialog
    // copies it verbatim (no client-side signing).
    const signedUrl =
      "/api/v1/s3/umedia/file-2/report.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=um_testkey123%2F20260815%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260815T120000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=abc123";
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files/file-2/temporary-link") && init?.method === "POST") {
        return jsonResponse({
          url: signedUrl,
          key_id: "um_testkey123",
          expires: 1786000000,
          expires_at: "2026-08-16T03:00:00Z",
        });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ShareDialog item={baseItem} onItemUpdated={vi.fn()} onOpenChange={vi.fn()} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "1 day" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate link" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2/temporary-link",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ expires_in: 86400 }),
        }),
      );
    });

    const generated = await screen.findByDisplayValue(
      /\/api\/v1\/s3\/file-2\/report\.pdf\?X-Amz-Algorithm=AWS4-HMAC-SHA256/,
    );
    expect(generated).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy temporary link" }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringMatching(
          /\/api\/v1\/s3\/file-2\/report\.pdf\?X-Amz-Algorithm=AWS4-HMAC-SHA256/,
        ),
      );
    });

    const openLink = screen.getByRole("link", { name: "Open temporary link" });
    expect(openLink).toHaveAttribute(
      "href",
      expect.stringMatching(
        /\/api\/v1\/s3\/file-2\/report\.pdf\?X-Amz-Algorithm=AWS4-HMAC-SHA256/,
      ),
    );
    expect(openLink).toHaveAttribute("target", "_blank");
  });

  it("shares with a user by uid", async () => {
    const updated = {
      ...baseItem,
      permissions: [{ user_id: "user-9", permission: 10 }],
    };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files/file-2/permissions") && init?.method === "PUT") {
        return jsonResponse(updated);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onItemUpdated = vi.fn();

    render(
      <ShareDialog item={baseItem} onItemUpdated={onItemUpdated} onOpenChange={vi.fn()} />,
    );

    // Closed select must show the human label, not the API int.
    expect(screen.getByLabelText("Permission level")).toHaveTextContent("Read");

    fireEvent.change(await screen.findByLabelText("User uid"), {
      target: { value: "user-9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Share" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2/permissions",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ user_id: "user-9", permission: 10 }),
        }),
      );
    });
    expect(onItemUpdated).toHaveBeenCalledWith(updated);
  });

  it("lists existing grants and revokes one", async () => {
    const shared = {
      ...baseItem,
      permissions: [{ user_id: "user-9", permission: 10 }],
    };
    const revoked = { ...baseItem, permissions: [] };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/files/file-2/permissions") && init?.method === "PUT") {
        return jsonResponse(revoked);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onItemUpdated = vi.fn();

    render(
      <ShareDialog item={shared} onItemUpdated={onItemUpdated} onOpenChange={vi.fn()} />,
    );

    expect(await screen.findByText("user-9")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove user-9" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/files/file-2/permissions",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ user_id: "user-9", permission: 0 }),
        }),
      );
    });
    expect(onItemUpdated).toHaveBeenCalledWith(revoked);
  });
});
