import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";
import { GoogleOidcLogin } from "@/components/google-oidc-login";
import { LocaleProvider } from "@/components/locale-provider";

const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "light", setTheme: vi.fn() }),
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

describe("Google OIDC login", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    mockReplace.mockReset();
  });

  it("shows Continue with Google when oidc_providers includes google", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/auth/state")) {
          return jsonResponse({
            configured: true,
            authenticated: false,
            oidc_providers: ["google"],
          });
        }
        return jsonResponse({});
      }),
    );

    render(
      <LocaleProvider>
        <LoginPage />
      </LocaleProvider>,
    );

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Continue with Google" }),
      ).toBeInTheDocument();
    });
  });

  it("does not offer OIDC during first-admin setup", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/auth/state")) {
          return jsonResponse({
            configured: false,
            authenticated: false,
            oidc_providers: ["google"],
          });
        }
        return jsonResponse({});
      }),
    );

    render(
      <LocaleProvider>
        <LoginPage />
      </LocaleProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: "Continue with Google" }),
    ).not.toBeInTheDocument();
  });

  it("starts and completes the Google OIDC paste flow for localhost redirect", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/auth/oidc/start") && init?.method === "POST") {
        return jsonResponse({
          provider: "google",
          authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1",
          state: "csrf-state",
          redirect_uri: "http://localhost",
        });
      }
      if (url.includes("/auth/oidc/complete") && init?.method === "POST") {
        return jsonResponse(
          {
            configured: true,
            authenticated: true,
            user: { uid: "u1", email: "ali@example.com", roles: ["user"] },
            access_token: "tok",
            expires_in: 3600,
          },
          201,
        );
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const onSuccess = vi.fn();
    render(<GoogleOidcLogin onSuccess={onSuccess} />);

    fireEvent.click(screen.getByRole("button", { name: "Continue with Google" }));

    await waitFor(() => {
      expect(
        screen.getByText(/accounts\.google\.com/),
      ).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Paste the redirect URL/i), {
      target: { value: "http://localhost/?code=abc&state=csrf-state" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete Google sign-in" }),
    );

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
    });

    const completeCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/auth/oidc/complete"),
    );
    expect(completeCall).toBeTruthy();
    expect(JSON.parse(String(completeCall?.[1]?.body))).toEqual({
      provider: "google",
      callback: "http://localhost/?code=abc&state=csrf-state",
      state: "csrf-state",
    });
  });

  it("uses full-page redirect when OIDC callback is on the server", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/auth/oidc/start") && init?.method === "POST") {
        return jsonResponse({
          provider: "google",
          authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1",
          state: "csrf-state",
          redirect_uri: "https://umedia.uln.me/api/v1/auth/oidc/callback",
        });
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<GoogleOidcLogin onSuccess={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Continue with Google" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/oidc/start"),
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(
      screen.queryByLabelText(/Paste the redirect URL/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/accounts\.google\.com/)).not.toBeInTheDocument();
  });
});
