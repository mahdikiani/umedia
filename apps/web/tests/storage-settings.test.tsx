import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StorageSettingsPage from "@/app/(dashboard)/settings/storage/page";
import { LocaleProvider } from "@/components/locale-provider";

const admin = {
  uid: "admin-1",
  email: "admin@example.com",
  roles: ["admin"],
};

const member = {
  uid: "user-1",
  email: "member@example.com",
  roles: ["user"],
};

const connection = {
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
};

const telegramConnection = {
  ...connection,
  provider_type: "telegram",
  name: "Telegram archive",
};

const placement = {
  policy: "default" as const,
  default_connection_id: "provider-1",
  fill_order: [] as string[],
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function renderStorageSettings() {
  render(
    <LocaleProvider>
      <StorageSettingsPage />
    </LocaleProvider>,
  );
}

describe("storage settings", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
    });
  });

  it("shows storage connections without account user management", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([connection]);
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    expect(
      await screen.findByRole("heading", { name: "Storage settings" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add storage" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Account" })).not.toBeInTheDocument();
  });

  it("renders the storage settings labels in Persian when that locale is selected", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue("fa"),
      setItem: vi.fn(),
    });
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([connection]);
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    expect(
      await screen.findByRole("heading", { name: "تنظیمات فضای ذخیره‌سازی" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "افزودن فضای ذخیره‌سازی" }),
    ).toBeInTheDocument();
  });

  it("starts a manual sync for the selected connection and shows its running state", async () => {
    let syncStatus: "idle" | "running" = "idle";
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers") && !init?.method) {
        return jsonResponse([connection]);
      }
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      if (url.endsWith("/providers/provider-1/sync") && init?.method === "POST") {
        syncStatus = "running";
        return jsonResponse({ status: "started", connection_id: "provider-1" }, 202);
      }
      if (url.endsWith("/providers/provider-1/sync")) {
        return jsonResponse({ status: syncStatus, connection_id: "provider-1" });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    fireEvent.click(await screen.findByRole("button", { name: "Sync" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/provider-1/sync",
        expect.objectContaining({ method: "POST" }),
      );
      expect(screen.getByRole("button", { name: "Syncing…" })).toBeDisabled();
    });
  });

  it("keeps connection options collapsed until requested", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([connection]);
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    const options = await screen.findByText("Connection settings");
    const details = options.closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(options);
    expect(details).toHaveAttribute("open");
    expect(
      screen.getByRole("checkbox", { name: /Import existing objects/i }),
    ).toBeInTheDocument();
  });

  it("keeps the add-storage dialog wide and its form scrollable within the viewport", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([connection]);
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();
    fireEvent.click(await screen.findByRole("button", { name: "Add storage" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveClass("sm:max-w-2xl");
    expect(dialog).toHaveClass("max-h-[calc(100dvh-2rem)]");
    expect(dialog).toHaveClass("overflow-hidden");
    expect(dialog.querySelector("[data-slot='storage-dialog-scroll-area']"))
      .toHaveClass("min-h-0", "flex-1", "overflow-y-auto");
  });

  it("lets an admin toggle provider import/mirror flags", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers") && !init?.method) {
        return jsonResponse([connection]);
      }
      if (url.endsWith("/settings/placement") && (!init?.method || init.method === "GET")) {
        return jsonResponse(placement);
      }
      if (url.endsWith("/providers/provider-1") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as Record<string, boolean>;
        return jsonResponse({ ...connection, ...body });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    fireEvent.click(await screen.findByText("Connection settings"));
    const importBox = await screen.findByRole("checkbox", {
      name: /Import existing objects/i,
    });
    expect(importBox).not.toBeChecked();
    fireEvent.click(importBox);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/provider-1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ import_existing: true }),
        }),
      );
    });
  });

  it("disables folder mirroring for Telegram while keeping import available", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([telegramConnection]);
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    fireEvent.click(await screen.findByText("Connection settings"));
    const mirrorBox = await screen.findByRole("checkbox", {
      name: /Mirror folder structure/i,
    });
    expect(mirrorBox).toBeDisabled();
    expect(
      screen.getByText(/Telegram channels are flat and do not support folders/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /Import existing objects/i }),
    ).toBeEnabled();
  });

  it("lets members manage their own storage connections", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: member });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([connection]);
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();

    expect(await screen.findByRole("heading", { name: "Placement" })).toBeInTheDocument();
    expect(screen.getAllByText("Local files").length).toBeGreaterThan(0);
    expect(screen.queryByRole("combobox", { name: "Policy" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add storage" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Connection settings"));
    expect(screen.getByRole("button", { name: "Remove storage" })).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /Import existing objects/i }),
    ).toBeInTheDocument();
  });

  it("requires confirmation before Remove storage DELETE", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers") && !init?.method) {
        return jsonResponse([connection]);
      }
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      if (url.endsWith("/providers/provider-1") && init?.method === "DELETE") {
        return jsonResponse(null, 204);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();
    fireEvent.click(await screen.findByText("Connection settings"));
    expect(await screen.findByRole("button", { name: "Remove storage" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove storage" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    expect(await screen.findByRole("heading", { name: "Remove storage?" })).toBeInTheDocument();

    const removeButtons = screen.getAllByRole("button", { name: "Remove storage" });
    fireEvent.click(removeButtons.at(-1)!);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/provider-1",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(screen.queryByText("Local files")).not.toBeInTheDocument();
  });

  it("lets an admin change the root placement policy", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers")) return jsonResponse([connection]);
      if (url.endsWith("/settings/placement") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        return jsonResponse({ ...placement, ...body });
      }
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();
    expect(await screen.findByRole("heading", { name: "Placement" })).toBeInTheDocument();
    expect(await screen.findByRole("combobox", { name: "Policy" })).toHaveTextContent(
      "Default storage",
    );
    const defaultStorage = screen.getByRole("combobox", { name: "Default storage" });
    expect(defaultStorage).toHaveTextContent("Local files");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/settings/placement",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("renames a connection inline and keeps the editor open on a server error", async () => {
    let patchCount = 0;
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/provider-types")) return jsonResponse([]);
      if (url.endsWith("/providers") && !init?.method) {
        return jsonResponse([{ ...connection, name: "local-files" }]);
      }
      if (url.endsWith("/settings/placement")) return jsonResponse(placement);
      if (url.endsWith("/providers/provider-1") && init?.method === "PATCH") {
        patchCount += 1;
        const body = JSON.parse(String(init.body)) as { name: string };
        if (body.name === "taken-name") {
          return jsonResponse(
            {
              error_code: "invalid_connection_name",
              detail: "You already have a connection named 'taken-name'",
            },
            422,
          );
        }
        return jsonResponse({ ...connection, name: body.name });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderStorageSettings();
    fireEvent.click(await screen.findByRole("button", { name: "Rename local-files" }));
    const input = screen.getByLabelText("Connection name") as HTMLInputElement;

    // Invalid by S3 rules: blocked client-side, nothing sent.
    fireEvent.change(input, { target: { value: "My Files" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText(/lowercase letters, numbers, and hyphens/)).toBeInTheDocument();

    // Valid locally but rejected by the server: error shown, editor stays.
    fireEvent.change(input, { target: { value: "taken-name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/already have a connection named/)).toBeInTheDocument();
    expect(screen.getByLabelText("Connection name")).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "family-photos" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByRole("heading", { name: "family-photos" }),
    ).toBeInTheDocument();
    expect(patchCount).toBe(2);
  });
});
