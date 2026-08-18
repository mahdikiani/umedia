import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessKeysSettings, cyberduckProfileXml } from "@/components/access-keys-settings";

const endpointInfo = {
  endpoint: "https://media.example.test/s3",
  region: "us-east-1",
  bucket: "umedia",
  force_path_style: true,
};

const activeKey = {
  uid: "key-1",
  access_key_id: "um_example_access_key",
  label: "default",
  is_active: true,
  created_at: "2026-08-17T12:00:00Z",
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

describe("access key settings", () => {
  afterEach(cleanup);

  beforeEach(() => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
  });

  it("shows S3 connection details when the settings load", async () => {
    // Given
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/access-keys/s3")) return jsonResponse(endpointInfo);
      if (url.endsWith("/access-keys")) return jsonResponse([activeKey]);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    // When
    render(<AccessKeysSettings />);

    // Then
    expect(await screen.findByText(endpointInfo.endpoint)).toBeInTheDocument();
    expect(screen.getByText(endpointInfo.region)).toBeInTheDocument();
    expect(screen.getByText("media.example.test")).toBeInTheDocument();
    expect(screen.getByText("(leave empty)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Cyberduck profile" })).toBeInTheDocument();
  });

  it("builds a Cyberduck profile with context and path-style, not a bucket Path", () => {
    const xml = cyberduckProfileXml("https://media.example.test/s3");
    expect(xml).toContain("<string>media.example.test</string>");
    expect(xml).toContain("<string>/s3</string>");
    expect(xml).toContain("s3.bucket.virtualhost.disable=true");
  });

  it("shows the one-time secret when a key is created", async () => {
    // Given
    const created = {
      ...activeKey,
      uid: "key-2",
      access_key_id: "um_created_access_key",
      label: "key",
      secret_access_key: "one-time-secret-value",
    };
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/access-keys/s3")) return jsonResponse(endpointInfo);
        if (url.endsWith("/access-keys") && init?.method === "POST") {
          return jsonResponse(created, 201);
        }
        if (url.endsWith("/access-keys")) return jsonResponse([]);
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<AccessKeysSettings />);

    // When
    fireEvent.click(await screen.findByRole("button", { name: "Create key" }));

    // Then
    expect(
      await screen.findByRole("heading", { name: "Save your secret access key" }),
    ).toBeInTheDocument();
    expect(screen.getByText(created.access_key_id)).toBeInTheDocument();
    expect(screen.getByText(created.secret_access_key)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/access-keys",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ label: "key" }),
      }),
    );
  });

  it("revokes an active key with DELETE", async () => {
    // Given
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/access-keys/s3")) return jsonResponse(endpointInfo);
        if (url.endsWith("/access-keys/key-1") && init?.method === "DELETE") {
          return jsonResponse(null, 204);
        }
        if (url.endsWith("/access-keys")) return jsonResponse([activeKey]);
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<AccessKeysSettings />);

    // When
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));

    // Then
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/access-keys/key-1",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  it("refreshes the key list when the one-time secret dialog closes", async () => {
    // Given
    const created = {
      ...activeKey,
      uid: "key-2",
      access_key_id: "um_created_access_key",
      label: "key",
      secret_access_key: "one-time-secret-value",
    };
    let listRequests = 0;
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/access-keys/s3")) return jsonResponse(endpointInfo);
        if (url.endsWith("/access-keys") && init?.method === "POST") {
          return jsonResponse(created, 201);
        }
        if (url.endsWith("/access-keys")) {
          listRequests += 1;
          return jsonResponse(
            listRequests === 1
              ? []
              : [
                  {
                    ...activeKey,
                    uid: created.uid,
                    access_key_id: created.access_key_id,
                    label: created.label,
                  },
                ],
          );
        }
        throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<AccessKeysSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Create key" }));
    expect(await screen.findByText(created.secret_access_key)).toBeInTheDocument();

    // When
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // Then
    await waitFor(() => expect(listRequests).toBe(2));
    expect(await screen.findByText(created.access_key_id)).toBeInTheDocument();
    expect(screen.queryByText(created.secret_access_key)).not.toBeInTheDocument();
  });
});
