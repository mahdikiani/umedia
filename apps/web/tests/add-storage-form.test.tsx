import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AddStorageForm } from "@/components/add-storage-form";

const providerType = {
  id: "local",
  name: "Local storage",
  description: "Files on this server.",
  adapter: "local",
  status: "available" as const,
  capabilities: ["list", "read", "write"],
  fields: [],
  connect_flow: "token" as const,
};

const googleDriveType = {
  id: "google_drive",
  name: "Google Drive",
  description: "Google Drive including Shared Drives.",
  adapter: "rclone",
  status: "beta" as const,
  capabilities: ["list", "read", "write"],
  fields: [
    {
      key: "root_folder_id",
      label: "Root folder ID",
      input_type: "text",
      required: false,
      secret: false,
      placeholder: null,
    },
  ],
  connect_flow: "oauth" as const,
};

const lockedTelegramType = {
  id: "telegram",
  name: "Telegram",
  description: "Store media in a Telegram channel using MTProto.",
  adapter: "telegram",
  status: "beta" as const,
  capabilities: ["list", "read", "write"],
  fields: [],
  connect_flow: "session" as const,
  available: false,
  unavailable_reason:
    "Set UMEDIA_TELEGRAM_API_ID and UMEDIA_TELEGRAM_API_HASH in the server environment to enable this provider.",
};

const telegramType = {
  ...lockedTelegramType,
  available: true,
  unavailable_reason: null,
  fields: [],
};

describe("add storage form", () => {
  afterEach(cleanup);

  it("locks Telegram when server credentials are missing", () => {
    render(
      <AddStorageForm
        onCreated={vi.fn()}
        providerTypes={[lockedTelegramType]}
      />,
    );

    const telegram = screen.getByRole("button", {
      name: /Telegram unavailable/,
    });
    expect(telegram).toBeDisabled();
    expect(screen.getByText(/UMEDIA_TELEGRAM_API_ID/)).toBeInTheDocument();
    expect(screen.getByText("Locked")).toBeInTheDocument();
  });

  it("includes both dual-layer flags in the create request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        uid: "provider-1",
        provider_type: "local",
        name: "Local storage",
        status: "configured",
        enabled: true,
        import_existing: true,
        mirror_structure: true,
        created_at: "2026-08-11T00:00:00Z",
        last_tested_at: null,
        last_error: null,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AddStorageForm
        onCreated={vi.fn()}
        providerTypes={[providerType]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Local storage/ }));
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Import existing objects from provider",
      }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Mirror folder structure to provider",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            provider_type: "local",
            name: "Local storage",
            config: {},
            import_existing: true,
            mirror_structure: true,
          }),
        }),
      );
    });
  });

  it("runs the oauth paste flow for google drive", async () => {
    const onCreated = vi.fn();
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/providers/oauth/start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            provider_type: "google_drive",
            authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=st",
            state: "st",
            redirect_uri: "http://localhost",
          }),
        };
      }
      if (url === "/api/v1/providers/oauth/complete") {
        return {
          ok: true,
          status: 201,
          json: async () => ({
            uid: "gdrive-1",
            provider_type: "google_drive",
            name: "Google Drive",
            status: "configured",
            enabled: true,
            import_existing: false,
            mirror_structure: false,
            created_at: "2026-08-20T00:00:00Z",
            last_tested_at: null,
            last_error: null,
          }),
        };
      }
      throw new Error(`unexpected fetch ${url} ${init?.method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AddStorageForm onCreated={onCreated} providerTypes={[googleDriveType]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Google Drive/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/oauth/start",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ provider_type: "google_drive" }),
        }),
      );
    });

    await screen.findByText(/accounts\.google\.com/);
    fireEvent.change(
      screen.getByLabelText(/Paste the redirect URL/),
      { target: { value: "http://localhost/?code=abc&state=st" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect Google Drive" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/providers/oauth/complete",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            provider_type: "google_drive",
            name: "Google Drive",
            callback: "http://localhost/?code=abc&state=st",
            state: "st",
            root_folder_id: null,
            import_existing: false,
            mirror_structure: false,
          }),
        }),
      );
    });
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ uid: "gdrive-1", provider_type: "google_drive" }),
      );
    });
  });

  it("signs into Telegram with a code and creates a connection", async () => {
    const onCreated = vi.fn();
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === "/api/v1/providers/telegram/login/start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ login_id: "pending-login", step: "code" }),
        };
      }
      if (url === "/api/v1/providers/telegram/login/pending-login/code") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            step: "complete",
            connection: {
              uid: "telegram-1",
              provider_type: "telegram",
              name: "My channel",
            },
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AddStorageForm onCreated={onCreated} providerTypes={[telegramType]} />);
    fireEvent.click(screen.getByRole("button", { name: /Telegram/ }));
    fireEvent.change(screen.getByLabelText("Connection name"), {
      target: { value: "My channel" },
    });
    fireEvent.change(screen.getByLabelText("Phone number"), {
      target: { value: "+1234567890" },
    });
    fireEvent.change(screen.getByLabelText("Channel name or @username"), {
      target: { value: "@my_channel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send login code" }));

    await screen.findByLabelText("Telegram login code");
    fireEvent.change(screen.getByLabelText("Telegram login code"), {
      target: { value: "12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ uid: "telegram-1", provider_type: "telegram" }),
      );
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/providers/telegram/login/start",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "My channel",
          phone: "+1234567890",
          channel_ref: "@my_channel",
          import_existing: false,
        }),
      }),
    );
  });

  it("asks for the two-step password when Telegram requests it", async () => {
    const onCreated = vi.fn();
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === "/api/v1/providers/telegram/login/start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ login_id: "pending-login", step: "code" }),
        };
      }
      if (url.endsWith("/code")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ step: "password" }),
        };
      }
      if (url.endsWith("/password")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            step: "complete",
            connection: { uid: "telegram-2", provider_type: "telegram" },
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AddStorageForm onCreated={onCreated} providerTypes={[telegramType]} />);
    fireEvent.click(screen.getByRole("button", { name: /Telegram/ }));
    fireEvent.change(screen.getByLabelText("Phone number"), {
      target: { value: "+1234567890" },
    });
    fireEvent.change(screen.getByLabelText("Channel name or @username"), {
      target: { value: "A Channel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send login code" }));
    await screen.findByLabelText("Telegram login code");
    fireEvent.change(screen.getByLabelText("Telegram login code"), {
      target: { value: "12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));
    await screen.findByLabelText("Two-step verification password");
    fireEvent.change(screen.getByLabelText("Two-step verification password"), {
      target: { value: "two-step" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify password" }));

    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ uid: "telegram-2", provider_type: "telegram" }),
      );
    });
  });

  it("shows Telegram's specific invalid-code response", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === "/api/v1/providers/telegram/login/start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ login_id: "pending-login", step: "code" }),
        };
      }
      if (url.endsWith("/code")) {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            error_code: "telegram_login_failed",
            message: { en: "Telegram rejected the login code or password" },
            detail: "Telegram login code is invalid or expired. Cancel and request a new code.",
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AddStorageForm onCreated={vi.fn()} providerTypes={[telegramType]} />);
    fireEvent.click(screen.getByRole("button", { name: /Telegram/ }));
    fireEvent.change(screen.getByLabelText("Phone number"), {
      target: { value: "+1234567890" },
    });
    fireEvent.change(screen.getByLabelText("Channel name or @username"), {
      target: { value: "@my_channel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send login code" }));
    await screen.findByLabelText("Telegram login code");
    fireEvent.change(screen.getByLabelText("Telegram login code"), {
      target: { value: "12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));

    expect(
      await screen.findByText(
        "Telegram login code is invalid or expired. Cancel and request a new code.",
      ),
    ).toBeInTheDocument();
  });
});
