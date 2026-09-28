import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileItemMenu } from "@/components/file-item-menu";
import type { MediaFileItem } from "@/lib/api";

const item: MediaFileItem = {
  uid: "file-1",
  owner_id: "admin-1",
  provider_connection_id: "provider-1",
  storage_object_id: "object-1",
  type: "file",
  name: "report.txt",
  parent_id: null,
  metadata: {},
  content_type: "text/plain",
  size: 12,
  status: "completed",
  error: null,
  public_permission: "none",
  starred: false,
  permissions: [],
  workspace_id: null,
  access_at: "2026-08-11T00:00:00Z",
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
};

describe("FileItemMenu transfers", () => {
  afterEach(cleanup);

  it("shows Move to…, Copy to…, and Add to Temporary before Share/Delete", async () => {
    const onMove = vi.fn();
    const onCopy = vi.fn();
    const onAddToTemporary = vi.fn();

    render(
      <FileItemMenu
        item={item}
        onAddToTemporary={onAddToTemporary}
        onCopy={onCopy}
        onDelete={vi.fn()}
        onMove={onMove}
        onRename={vi.fn()}
        onShare={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Actions for report/ }));

    expect(await screen.findByText("Move to…")).toBeInTheDocument();
    expect(screen.getByText("Copy to…")).toBeInTheDocument();
    expect(screen.getByText("Add to Temporary")).toBeInTheDocument();
    expect(screen.getByText("Share…")).toBeInTheDocument();
    expect(screen.getByText("Download")).toBeInTheDocument();
    expect(screen.getByText("Delete")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Move to…"));
    expect(onMove).toHaveBeenCalledWith(item);

    fireEvent.click(screen.getByRole("button", { name: /Actions for report/ }));
    fireEvent.click(await screen.findByText("Add to Temporary"));
    expect(onAddToTemporary).toHaveBeenCalledWith(item);

    fireEvent.click(screen.getByRole("button", { name: /Actions for report/ }));
    const link = document.createElement("a");
    const clickSpy = vi.spyOn(link, "click");
    const createSpy = vi
      .spyOn(document, "createElement")
      .mockReturnValue(link as HTMLAnchorElement);

    fireEvent.click(screen.getByText("Download"));

    expect(createSpy).toHaveBeenCalledWith("a");
    expect(link.href).toContain("/api/v1/files/file-1/content/report.txt?download=1");
    expect(link.download).toBe("report.txt");
    expect(clickSpy).toHaveBeenCalled();

    createSpy.mockRestore();
    clickSpy.mockRestore();
  });

  it("offers move and copy to the other pane when that pane is available", async () => {
    const onMoveToOtherSide = vi.fn();
    const onCopyToOtherSide = vi.fn();

    render(
      <FileItemMenu
        item={item}
        onAddToTemporary={vi.fn()}
        onCopy={vi.fn()}
        onCopyToOtherSide={onCopyToOtherSide}
        onDelete={vi.fn()}
        onMove={vi.fn()}
        onMoveToOtherSide={onMoveToOtherSide}
        onRename={vi.fn()}
        onShare={vi.fn()}
        otherSideParentId="folder-destination"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Actions for report/ }));
    fireEvent.click(await screen.findByText("Move to other side"));
    expect(onMoveToOtherSide).toHaveBeenCalledWith(item);

    fireEvent.click(screen.getByRole("button", { name: /Actions for report/ }));
    fireEvent.click(await screen.findByText("Copy to other side"));
    expect(onCopyToOtherSide).toHaveBeenCalledWith(item);
  });

  it("disables move when both panes show the same folder", async () => {
    render(
      <FileItemMenu
        item={item}
        onAddToTemporary={vi.fn()}
        onCopy={vi.fn()}
        onCopyToOtherSide={vi.fn()}
        onDelete={vi.fn()}
        onMove={vi.fn()}
        onMoveToOtherSide={vi.fn()}
        onRename={vi.fn()}
        onShare={vi.fn()}
        otherSideParentId={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Actions for report/ }));
    expect(await screen.findByText("Move to other side")).toHaveAttribute("data-disabled");
    expect(screen.getByText("Copy to other side")).toBeEnabled();
  });
});
