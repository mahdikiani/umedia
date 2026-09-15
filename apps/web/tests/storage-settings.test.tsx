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
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
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
    expect(await screen.findByRole("button", { name: "Remove" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    expect(await screen.findByRole("heading", { name: "Remove storage?" })).toBeInTheDocument();

    const removeButtons = screen.getAllByRole("button", { name: "Remove" });
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
});
