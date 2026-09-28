import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TransferDestinationDialog } from "@/components/transfer-destination-dialog";

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

describe("TransferDestinationDialog", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("disables moving a source into its current folder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        jsonResponse({
          items: [],
          total: 0,
          limit: 50,
          offset: 0,
          has_more: false,
        }),
      ),
    );

    render(
      <TransferDestinationDialog
        onCreated={vi.fn()}
        onOpenChange={vi.fn()}
        open
        operation="move"
        sourceIds={["file-at-root"]}
        sourceParentIds={[null]}
      />,
    );

    expect(
      await screen.findByRole("button", { name: "Move here" }),
    ).toBeDisabled();
  });
});
