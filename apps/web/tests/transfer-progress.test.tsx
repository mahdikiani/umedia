import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TransferProgressPanel } from "@/components/transfer-progress-panel";
import { useTransferTracker } from "@/hooks/use-transfer-tracker";
import type { TransferJob } from "@/lib/api";

function job(overrides: Partial<TransferJob> = {}): TransferJob {
  return {
    uid: "job-1",
    operation: "move",
    status: "running",
    source_ids: ["file-1"],
    dest_parent_id: "folder-1",
    total_items: 1,
    done_items: 0,
    failed_items: 0,
    progress_pct: 42,
    current_name: "report.txt",
    error: null,
    created_at: "2026-08-11T00:00:00Z",
    started_at: "2026-08-11T00:00:01Z",
    finished_at: null,
    ...overrides,
  };
}

function TrackerHost({ onSettled }: { onSettled?: () => void }) {
  const { jobs, track } = useTransferTracker(onSettled);
  return (
    <div>
      <button
        onClick={() => track(job({ progress_pct: 10 }))}
        type="button"
      >
        Start
      </button>
      <TransferProgressPanel
        jobs={jobs}
        onCancel={vi.fn()}
        onDismiss={vi.fn()}
      />
    </div>
  );
}

describe("transfer progress", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it("lets the user cancel an active transfer", () => {
    const onCancel = vi.fn();
    render(
      <TransferProgressPanel
        jobs={[job()]}
        onCancel={onCancel}
        onDismiss={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel transfer" }));

    expect(onCancel).toHaveBeenCalledWith("job-1");
  });

  it("shows cancellation in progress and hides the cancel action", () => {
    render(
      <TransferProgressPanel
        jobs={[job({ status: "cancelling" })]}
        onCancel={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    expect(screen.getByText("Cancelling…")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel transfer" }),
    ).not.toBeInTheDocument();
  });

  it("polls and shows progress percent", async () => {
    let polls = 0;
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/files/transfers") && !url.includes("transfers/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [],
        });
      }
      if (url.includes("/files/transfers/job-1")) {
        polls += 1;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            job({
              progress_pct: polls >= 2 ? 75 : 42,
              current_name: "report.txt",
              status: "running",
            }),
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrackerHost />);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(await screen.findByTestId("transfer-progress")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("report.txt")).toBeInTheDocument();

    await act(() => {
      vi.advanceTimersByTime(1100);
    });
    await waitFor(() => {
      expect(polls).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText("42%")).toBeInTheDocument();

    await act(() => {
      vi.advanceTimersByTime(1100);
    });
    await waitFor(() => {
      expect(screen.getByText("75%")).toBeInTheDocument();
    });
  });

  it("auto-dismisses the banner five seconds after a transfer settles", async () => {
    let polls = 0;
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/files/transfers") && !url.includes("transfers/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [],
        });
      }
      if (url.includes("/files/transfers/job-1")) {
        polls += 1;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            job({
              status: polls >= 1 ? "completed" : "running",
              progress_pct: polls >= 1 ? 100 : 50,
              finished_at: polls >= 1 ? "2026-08-11T00:00:02Z" : null,
            }),
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TrackerHost />);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(await screen.findByTestId("transfer-progress")).toBeInTheDocument();

    await act(() => {
      vi.advanceTimersByTime(1100);
    });
    await waitFor(() => {
      expect(screen.getByText("Done")).toBeInTheDocument();
    });

    await act(() => {
      vi.advanceTimersByTime(5000);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("transfer-progress")).not.toBeInTheDocument();
    });
  });
});
