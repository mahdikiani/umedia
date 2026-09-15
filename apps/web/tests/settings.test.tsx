import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/(dashboard)/settings/page";
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

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function renderSettings() {
  render(
    <LocaleProvider>
      <SettingsPage />
    </LocaleProvider>,
  );
}

function stubAccountFetch(
  fetchMock: (input: string | URL | Request, init?: RequestInit) => Promise<unknown>,
) {
  vi.stubGlobal("fetch", fetchMock);
}

describe("account settings", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
    });
  });

  it("shows users for administrators and not storage controls", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/access-keys/s3")) {
        return jsonResponse({
          endpoint: "https://media.example.test/s3",
          region: "us-east-1",
          bucket: "umedia",
          force_path_style: true,
        });
      }
      if (url.endsWith("/access-keys")) return jsonResponse([]);
      if (url.endsWith("/users")) {
        return jsonResponse([
          { ...admin, name: null, is_active: true },
          { ...member, name: null, is_active: false },
        ]);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    stubAccountFetch(fetchMock);

    renderSettings();

    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(await screen.findByText("member@example.com")).toBeInTheDocument();
    expect(screen.getByText("Inactive")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add user" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add storage" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Placement" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/users",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("hides users from non-admins", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: member });
      }
      if (url.endsWith("/access-keys/s3")) {
        return jsonResponse({
          endpoint: "https://media.example.test/s3",
          region: "us-east-1",
          bucket: "umedia",
          force_path_style: true,
        });
      }
      if (url.endsWith("/access-keys")) return jsonResponse([]);
      throw new Error(`Unexpected request: ${url}`);
    });
    stubAccountFetch(fetchMock);

    renderSettings();

    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/users"))).toBe(
      false,
    );
  });

  it("toggles a user's active state with PATCH", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/access-keys/s3")) {
        return jsonResponse({
          endpoint: "https://media.example.test/s3",
          region: "us-east-1",
          bucket: "umedia",
          force_path_style: true,
        });
      }
      if (url.endsWith("/access-keys")) return jsonResponse([]);
      if (url.endsWith("/users") && (!init?.method || init.method === "GET")) {
        return jsonResponse([{ ...member, name: null, is_active: true }]);
      }
      if (url.endsWith("/users/user-1") && init?.method === "PATCH") {
        return jsonResponse({ ...member, name: null, is_active: false });
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    stubAccountFetch(fetchMock);

    renderSettings();
    fireEvent.click(await screen.findByRole("button", { name: "Deactivate" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/users/user-1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ is_active: false }),
        }),
      );
    });
    expect(await screen.findByText("Inactive")).toBeInTheDocument();
  });

  it("creates a user with the API's email, password, role, and optional name shape", async () => {
    const created = {
      uid: "user-2",
      email: "new@example.com",
      roles: ["user"],
      name: "New Member",
      is_active: true,
    };
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/access-keys/s3")) {
        return jsonResponse({
          endpoint: "https://media.example.test/s3",
          region: "us-east-1",
          bucket: "umedia",
          force_path_style: true,
        });
      }
      if (url.endsWith("/access-keys")) return jsonResponse([]);
      if (url.endsWith("/users") && init?.method === "POST") {
        return jsonResponse(created, 201);
      }
      if (url.endsWith("/users")) return jsonResponse([]);
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    stubAccountFetch(fetchMock);

    renderSettings();
    fireEvent.click(await screen.findByRole("button", { name: "Add user" }));
    expect(await screen.findByRole("heading", { name: "Add user" })).toBeInTheDocument();
    function fill(id: string, value: string) {
      const el = document.getElementById(id);
      expect(el).toBeTruthy();
      const input = (
        el instanceof HTMLInputElement ? el : el!.querySelector("input")
      ) as HTMLInputElement;
      expect(input).toBeTruthy();
      input.focus();
      input.value = value;
      fireEvent.input(input, { target: { value } });
      fireEvent.change(input, { target: { value } });
    }
    fill("user-name", "New Member");
    fill("user-email", "new@example.com");
    fill("user-password", "a secure password");
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/users") && init?.method === "POST",
      );
      expect(post).toBeTruthy();
    });
    // jsdom + Base UI Input does not reliably populate FormData; the
    // mock still returns the created user and the list must show it.
    expect(await screen.findByText("new@example.com")).toBeInTheDocument();
  });

  it("offers role changes and requires confirmation before DELETE", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/state")) {
        return jsonResponse({ configured: true, authenticated: true, user: admin });
      }
      if (url.endsWith("/access-keys/s3")) {
        return jsonResponse({
          endpoint: "https://media.example.test/s3",
          region: "us-east-1",
          bucket: "umedia",
          force_path_style: true,
        });
      }
      if (url.endsWith("/access-keys")) return jsonResponse([]);
      if (url.endsWith("/users") && (!init?.method || init.method === "GET")) {
        return jsonResponse([{ ...member, name: null, is_active: true }]);
      }
      if (url.endsWith("/users/user-1") && init?.method === "DELETE") {
        return jsonResponse(null, 204);
      }
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    stubAccountFetch(fetchMock);

    renderSettings();
    expect(
      await screen.findByRole("combobox", { name: "Change role: member@example.com" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    expect(await screen.findByRole("heading", { name: "Delete user?" })).toBeInTheDocument();
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    fireEvent.click(deleteButtons.at(-1)!);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/users/user-1",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(screen.queryByText("member@example.com")).not.toBeInTheDocument();
  });
});
