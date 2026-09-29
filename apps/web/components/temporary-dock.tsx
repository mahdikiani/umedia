"use client";

import { ChevronDown, ChevronUp, Inbox, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useLocale } from "@/components/locale-provider";
import { useCopy } from "@/lib/copy";
import {
  clearTemporary,
  createTransfer,
  listTemporary,
  removeFromTemporary,
  type MediaFileItem,
  type TransferJob,
} from "@/lib/api";
import {
  dataTransferHasLibraryIds,
  parseLibraryDragPayload,
  setLibraryDragPayload,
} from "@/lib/umedia-dnd";

const COLLAPSED_KEY = "umedia.files.temporaryCollapsed";

type TemporaryDockProps = {
  onTransferCreated: (job: TransferJob) => void;
  refreshToken?: number;
  /** Primary-pane folder to paste into (left panel when dual-pane). */
  currentParentId: string | null;
  /** Shared stash path (menu + drag-drop). */
  addToTemporary: (ids: string[]) => Promise<void>;
};

export function TemporaryDock({
  onTransferCreated,
  refreshToken = 0,
  currentParentId,
  addToTemporary,
}: TemporaryDockProps) {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [items, setItems] = useState<MediaFileItem[]>([]);
  const [dropActive, setDropActive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listTemporary());
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not load Temporary.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload, refreshToken]);

  function toggleCollapsed() {
    setCollapsed((previous) => {
      const next = !previous;
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // ignore
      }
      return next;
    });
  }

  async function pasteHere(operation: "move" | "copy") {
    if (items.length === 0 || busy) return;
    if (
      operation === "move" &&
      items.some(
        (item) =>
          item.parent_id === currentParentId || item.uid === currentParentId,
      )
    )
      return;
    setBusy(true);
    try {
      const job = await createTransfer({
        operation,
        source_ids: items.map((item) => item.uid),
        dest_parent_id: currentParentId,
      });
      toast.success(
        operation === "move" ? "Pasting (move)…" : "Pasting (copy)…",
      );
      onTransferCreated(job);
      if (operation === "move") {
        await clearTemporary();
        await reload();
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not paste from Temporary.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function removeItem(item: MediaFileItem) {
    try {
      await removeFromTemporary(item.uid);
      toast.success("Removed from Temporary");
      await reload();
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not remove from Temporary.",
      );
    }
  }

  async function clearItems() {
    if (busy) return;
    setBusy(true);
    try {
      await clearTemporary();
      toast.success("Temporary cleared");
      await reload();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not clear Temporary.",
      );
    } finally {
      setBusy(false);
    }
  }

  function onDragOver(event: React.DragEvent) {
    if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setDropActive(true);
  }

  function onDragLeave(event: React.DragEvent) {
    if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
    setDropActive(false);
  }

  function onDrop(event: React.DragEvent) {
    if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    setDropActive(false);
    const payload = parseLibraryDragPayload(event.dataTransfer);
    if (!payload || payload.sourceIds.length === 0) return;
    void addToTemporary(payload.sourceIds);
  }

  const pasteTitle =
    "Paste into the folder you're browsing (left/primary panel)";
  const moveHasSameDestination = items.some(
    (item) =>
      item.parent_id === currentParentId || item.uid === currentParentId,
  );

  return (
    <aside
      aria-label="Temporary"
      className={`fixed end-4 bottom-4 z-50 rounded-xl border bg-card shadow-lg ${
        collapsed ? "w-auto" : "w-72"
      } ${dropActive ? "ring-2 ring-primary" : ""}`}
      data-collapsed={collapsed ? "true" : "false"}
      data-testid="temporary-dock"
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Inbox size={16} />
            Temporary
            {collapsed && items.length > 0 ? (
              <span
                className="inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[10px] font-semibold text-primary-foreground"
                data-testid="temporary-badge"
              >
                {items.length}
              </span>
            ) : null}
            {!collapsed && !loading ? (
              <span className="text-xs font-normal text-muted-foreground">
                ({items.length})
              </span>
            ) : null}
          </div>
          {!collapsed ? (
            <p className="ps-6 text-[11px] text-muted-foreground">
              {text.temporarySubtitle}
            </p>
          ) : null}
        </div>
        <Button
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand Temporary" : "Collapse Temporary"}
          onClick={toggleCollapsed}
          size="icon-xs"
          variant="ghost"
        >
          {collapsed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </Button>
      </div>
      {!collapsed ? (
        <div className="max-h-56 overflow-y-auto p-2">
          {loading ? (
            <p className="px-1 py-3 text-xs text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="px-1 py-3 text-xs text-muted-foreground">
              {text.temporaryInstructions}
            </p>
          ) : (
            <>
              <div className="mb-2 flex flex-col gap-1.5">
                <Button
                  aria-label="Clear Temporary"
                  disabled={busy}
                  onClick={() => void clearItems()}
                  size="sm"
                  variant="destructive"
                >
                  Clear
                </Button>
                <Button
                  disabled={busy || moveHasSameDestination}
                  onClick={() => void pasteHere("move")}
                  size="sm"
                  title={pasteTitle}
                  variant="default"
                >
                  Paste here (move)
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => void pasteHere("copy")}
                  size="sm"
                  title={pasteTitle}
                  variant="outline"
                >
                  Paste here (copy)
                </Button>
              </div>
              <ul className="space-y-1">
                {items.map((item) => (
                  <li
                    className="group flex items-center gap-1 rounded-md hover:bg-muted"
                    key={item.uid}
                  >
                    <span
                      className="min-w-0 flex-1 cursor-grab truncate px-2 py-1.5 text-sm active:cursor-grabbing"
                      draggable
                      onDragStart={(event) => {
                        setLibraryDragPayload(event.dataTransfer, [item.uid], {
                          fromTemporary: true,
                          sourceConnectionIds: [item.provider_connection_id],
                        });
                      }}
                      title={item.name}
                    >
                      {item.name}
                    </span>
                    <Button
                      aria-label={`Remove ${item.name} from Temporary`}
                      className="me-1 shrink-0 opacity-70 group-hover:opacity-100"
                      disabled={busy}
                      onClick={() => void removeItem(item)}
                      size="icon-xs"
                      variant="ghost"
                    >
                      <X size={12} />
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : null}
    </aside>
  );
}
