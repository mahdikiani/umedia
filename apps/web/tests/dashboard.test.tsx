import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "@/components/locale-provider";
import LoginPage from "@/app/login/page";

// A stable object, not a fresh one per call -- real Next.js `useRouter()`
// returns the same router reference across renders. Returning a new
// object here instead makes LoginPage's `useEffect(..., [router])` see a
// "changed" dependency on every render and refire indefinitely, silently
// exhausting a test's `mockResolvedValueOnce` queue before the actual
// interaction under test ever runs.
const mockRouter = { replace: vi.fn(), push: vi.fn() };
const mockSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => mockSearchParams,
}));

describe("login page", () => {
  // vitest.config.ts doesn't set `test.globals`, so @testing-library/react's
  // own automatic per-test cleanup (which hooks into a global `afterEach`)
  // never registers -- without this, a previous test's rendered DOM stays
  // in `document.body` and the next test's `getByRole` sees both trees at
  // once ("Found multiple elements").
  afterEach(cleanup);

  beforeEach(() => {
    mockSearchParams.forEach((_, key) => mockSearchParams.delete(key));
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ configured: false, authenticated: false }),
      }),
    );
  });

  it("shows first-run setup instead of a marketing landing page", async () => {
    render(
      <LocaleProvider>
        <LoginPage />
      </LocaleProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "Secure your drive" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create password/ })).toBeInTheDocument();
    expect(screen.queryByText(/همه‌جا ذخیره کن/)).not.toBeInTheDocument();
  });

  it("shows the backend's actual error text, not [object Object], for a bilingual error body", async () => {
    // apps/media's own errors send a plain string `message`, but usso/
    // fastapi_mongo_base's built-in ones (this is what a real wrong-
    // password 401 looks like) send `{en, fa}` -- reported live as the
    // login form displaying the literal text "[object Object]".
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation((url: string, init?: RequestInit) => {
          if (url.includes("/auth/state")) {
            return Promise.resolve({
              ok: true,
              status: 200,
              json: async () => ({ configured: true, authenticated: false }),
            });
          }
          if (url.includes("/auth/refresh")) {
            return Promise.resolve({
              ok: false,
              status: 401,
              json: async () => ({}),
            });
          }
          if (url.includes("/auth/sessions") && init?.method === "POST") {
            return Promise.resolve({
              ok: false,
              status: 401,
              json: async () => ({
                message: {
                  en: "Invalid credentials.",
                  fa: "اطلاعات ورود صحیح نیست.",
                },
                error_code: "invalid_credentials",
                detail: "Invalid credentials.",
              }),
            });
          }
          throw new Error(`Unexpected request: ${url}`);
        }),
    );

    render(
      <LocaleProvider>
        <LoginPage />
      </LocaleProvider>,
    );

    fireEvent.change(await screen.findByLabelText("Email address"), {
      target: { value: "admin@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "wrong-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Unlock drive/ }));

    expect(await screen.findByText("Invalid credentials.")).toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });
});
