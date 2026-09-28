import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "@/components/app-sidebar";
import { LocaleProvider } from "@/components/locale-provider";
import { SidebarProvider } from "@/components/ui/sidebar";

const mockRouter = { replace: vi.fn(), push: vi.fn() };
const mockSearchParams = new URLSearchParams();

let mockPathname = "/home";

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => mockPathname,
  useSearchParams: () => mockSearchParams,
}));

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

function renderSidebar() {
  return render(
    <LocaleProvider>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </LocaleProvider>,
  );
}

describe("app sidebar", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockPathname = "/home";
    window.localStorage.removeItem("umedia_locale");
    mockSearchParams.forEach((_, key) => mockSearchParams.delete(key));
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/files/stats")) {
          return jsonResponse({ used_bytes: 1536, file_count: 2, folder_count: 1 });
        }
        if (url.endsWith("/providers")) {
          return jsonResponse([
            { uid: "prov-a", name: "MinIO", provider_type: "s3", status: "connected" },
            { uid: "prov-b", name: "Google Drive", provider_type: "gdrive", status: "connected" },
          ]);
        }
        return jsonResponse({});
      }),
    );
  });

  it("links to the legal pages from the signed-in navigation", async () => {
    renderSidebar();

    expect(
      await screen.findByRole("link", { name: "Privacy Policy" }),
    ).toHaveAttribute("href", "/privacy-policy");
    expect(
      screen.getByRole("link", { name: "Terms and Conditions" }),
    ).toHaveAttribute("href", "/terms-and-conditions");
  });

  it("shows Persian legal link labels when Persian is selected", async () => {
    window.localStorage.setItem("umedia_locale", "fa");
    renderSidebar();

    expect(
      await screen.findByRole("link", { name: "سیاست حفظ حریم خصوصی" }),
    ).toHaveAttribute("href", "/privacy-policy");
    expect(
      screen.getByRole("link", { name: "شرایط و ضوابط" }),
    ).toHaveAttribute("href", "/terms-and-conditions");
  });

  it("lists Home, Files, Starred, Trash, then Storage settings, Account, Log out", async () => {
    renderSidebar();

    expect(await screen.findByRole("link", { name: /Home/ })).toHaveAttribute(
      "href",
      "/home",
    );
    expect(screen.getByRole("link", { name: "Files" })).toHaveAttribute("href", "/files");
    expect(screen.queryByRole("button", { name: "Storages" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Starred" })).toHaveAttribute(
      "href",
      "/starred",
    );
    expect(screen.getByRole("link", { name: "Trash" })).toHaveAttribute("href", "/trash");
    expect(screen.getByRole("link", { name: "Storage settings" })).toHaveAttribute(
      "href",
      "/settings/storage",
    );
    expect(screen.getByRole("link", { name: "Account" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();

    const starred = screen.getByRole("link", { name: "Starred" });
    const account = screen.getByRole("link", { name: "Account" });
    expect(starred.compareDocumentPosition(account) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows the folder tree under Files and lazily expands folders", async () => {
    const rootFolder = {
      uid: "folder-a", owner_id: "owner", type: "folder", name: "Movies",
      parent_id: null, metadata: {}, content_type: "inode/directory", size: 0,
      status: "completed", error: null, public_permission: "none", starred: false,
      permissions: [], workspace_id: null, access_at: "", created_at: "", updated_at: "",
    };
    const nestedFolder = { ...rootFolder, uid: "folder-b", name: "Sci-Fi", parent_id: "folder-a" };
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith("/files/folder-b")) return jsonResponse(nestedFolder);
      if (url.endsWith("/files/folder-a")) return jsonResponse(rootFolder);
      if (url.endsWith("/files?sort=name&order=asc&limit=50&offset=0")) {
        return jsonResponse({ items: [rootFolder], total: 1, limit: 50, offset: 0, has_more: false });
      }
      if (url.includes("/files?sort=name&order=asc&limit=50&offset=0&parent_id=folder-a")) {
        return jsonResponse({ items: [nestedFolder], total: 1, limit: 50, offset: 0, has_more: false });
      }
      return jsonResponse({ items: [], total: 0, limit: 50, offset: 0, has_more: false });
    });
    vi.stubGlobal("fetch", fetchMock);
    mockPathname = "/files";
    mockSearchParams.set("folder", "folder-b");

    renderSidebar();

    const folderLink = await screen.findByRole("link", { name: "Movies" });
    expect(folderLink).toHaveAttribute("href", "/files?folder=folder-a");
    const nestedLink = await screen.findByRole("link", { name: "Sci-Fi" });
    expect(nestedLink).toHaveAttribute(
      "href", "/files?folder=folder-b",
    );
    fireEvent.click(screen.getByRole("button", { name: "Collapse Movies" }));
    expect(screen.queryByRole("link", { name: "Sci-Fi" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand Movies" }));
    expect(screen.getByRole("link", { name: "Sci-Fi" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Storages" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/files/folder-b", expect.objectContaining({ credentials: "include" }),
    );
  });

  it("shows a used-space card", async () => {
    renderSidebar();
    const usedSpace = await screen.findByText("Used space");
    expect(usedSpace.closest("[data-slot=card]")).toHaveClass("bg-card");
    expect(usedSpace.closest("[data-slot=card]")).not.toHaveClass("bg-sidebar");
    await waitFor(() => {
      expect(screen.getByText("1.5 KB")).toBeInTheDocument();
    });
    expect(screen.getByText("2 files")).toBeInTheDocument();
  });
});
