import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "@/components/app-sidebar";
import { LocaleProvider } from "@/components/locale-provider";
import { SidebarProvider } from "@/components/ui/sidebar";

const mockRouter = { replace: vi.fn(), push: vi.fn() };
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/home",
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

  it("lists Home, Files, Storages, Starred, Trash, then Storage settings, Account, Log out", async () => {
    renderSidebar();

    expect(await screen.findByRole("link", { name: /Home/ })).toHaveAttribute(
      "href",
      "/home",
    );
    expect(screen.getByRole("link", { name: "Files" })).toHaveAttribute("href", "/files");
    expect(screen.getByRole("button", { name: "Storages" })).toBeInTheDocument();
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

    const storages = screen.getByRole("button", { name: "Storages" });
    const starred = screen.getByRole("link", { name: "Starred" });
    const account = screen.getByRole("link", { name: "Account" });
    expect(storages.compareDocumentPosition(starred) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(starred.compareDocumentPosition(account) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("expands Storages to list each provider connection", async () => {
    renderSidebar();

    expect(await screen.findByRole("link", { name: "MinIO" })).toHaveAttribute(
      "href",
      "/storage?provider=prov-a",
    );
    expect(screen.getByRole("link", { name: "Google Drive" })).toHaveAttribute(
      "href",
      "/storage?provider=prov-b",
    );
    expect(
      screen.getByRole("link", { name: "MinIO" }).querySelector(
        "[data-provider-type='s3']",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Google Drive" }).querySelector(
        "[data-provider-type='google_drive']",
      ),
    ).toBeInTheDocument();
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
