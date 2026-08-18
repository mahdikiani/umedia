import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardSearch } from "@/components/dashboard-search";
import { LocaleProvider } from "@/components/locale-provider";

const mockSearchParams = new URLSearchParams();
const mockRouter = { push: vi.fn(), replace: vi.fn() };
let mockPathname = "/files";

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => mockPathname,
  useSearchParams: () => mockSearchParams,
}));

describe("dashboard search", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockPathname = "/files";
    mockSearchParams.delete("q");
    mockSearchParams.delete("folder");
    mockRouter.replace.mockClear();
  });

  it("shows a header search field on files and writes q into the URL", async () => {
    render(
      <LocaleProvider>
        <DashboardSearch />
      </LocaleProvider>,
    );

    const input = screen.getByPlaceholderText("Search all files and folders…");
    fireEvent.change(input, { target: { value: "report" } });

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/files?q=report");
    });
  });

  it("hides on settings", () => {
    mockPathname = "/settings";
    render(
      <LocaleProvider>
        <DashboardSearch />
      </LocaleProvider>,
    );
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("does not bounce empty starred searches back to files", async () => {
    mockPathname = "/starred";
    render(
      <LocaleProvider>
        <DashboardSearch />
      </LocaleProvider>,
    );

    expect(
      screen.getByPlaceholderText("Search all files and folders…"),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(mockRouter.replace).not.toHaveBeenCalled();
    });
  });

  it("sends a starred search query to the files library", async () => {
    mockPathname = "/starred";
    render(
      <LocaleProvider>
        <DashboardSearch />
      </LocaleProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText("Search all files and folders…"), {
      target: { value: "report" },
    });

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/files?q=report");
    });
  });
});
