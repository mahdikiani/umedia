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
});
