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

describe("add storage form", () => {
  afterEach(cleanup);

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
});
