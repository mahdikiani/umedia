"use client";

import {
  File as FileIcon,
  FolderOpen,
  RotateCcw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  connectionMeta,
  StorageProviderIcon,
} from "@/components/storage-provider-icon";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  api,
  LIST_PAGE_SIZE,
  type MediaFileItem,
  type Page,
  type ProviderConnection,
} from "@/lib/api";

/** Mirrors the backend's TRASH_RETENTION_DAYS (apps/media_files/services.py). */
const RETENTION_DAYS = 30;

function trashListPath(offset = 0): string {
  return `/files?scope=trash&limit=${LIST_PAGE_SIZE}&offset=${offset}`;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** exponent;
  return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`;
}

function daysLeft(deletedAt: string | null | undefined): number {
  if (!deletedAt) return RETENTION_DAYS;
  const elapsedDays = Math.floor(
    (Date.now() - new Date(deletedAt).getTime()) / 86_400_000,
  );
  return Math.max(RETENTION_DAYS - elapsedDays, 0);
}

export default function TrashPage() {
  const [items, setItems] = useState<MediaFileItem[]>([]);
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [busyUid, setBusyUid] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [purging, setPurging] = useState<MediaFileItem | null>(null);
  const [purgingAll, setPurgingAll] = useState(false);

  async function refresh() {
    const page = await api<Page<MediaFileItem>>(trashListPath());
    setItems(page.items);
    setHasMore(page.has_more);
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const page = await api<Page<MediaFileItem>>(trashListPath(items.length));
      setItems((current) => [...current, ...page.items]);
      setHasMore(page.has_more);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load more.");
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    Promise.all([
      api<Page<MediaFileItem>>(trashListPath()),
      api<ProviderConnection[]>("/providers"),
    ])
      .then(([page, conns]) => {
        if (cancelled) return;
        setItems(page.items);
        setHasMore(page.has_more);
        setConnections(conns);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(
          error instanceof Error ? error.message : "Could not load the trash.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function collectTrashRoots(): Promise<MediaFileItem[]> {
    const collected: MediaFileItem[] = [];
    let offset = 0;
    for (;;) {
      const page = await api<Page<MediaFileItem>>(trashListPath(offset));
      collected.push(...page.items);
      if (!page.has_more || page.items.length === 0) {
        return collected;
      }
      offset += page.items.length;
    }
  }

  async function restore(item: MediaFileItem) {
    setBusyUid(item.uid);
    try {
      await api<MediaFileItem>(`/files/${item.uid}/restore`, { method: "POST" });
      toast.success(`Restored "${item.name}".`);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not restore.");
    } finally {
      setBusyUid(null);
    }
  }

  async function restoreAll() {
    setBulkBusy(true);
    try {
      const roots = await collectTrashRoots();
      let failed = 0;
      for (const item of roots) {
        try {
          await api<MediaFileItem>(`/files/${item.uid}/restore`, { method: "POST" });
        } catch {
          failed++;
        }
      }
      if (failed === 0) {
        toast.success(
          roots.length === 1
            ? `Restored "${roots[0].name}".`
            : `Restored ${roots.length} items.`,
        );
      } else {
        toast.warning(`Restored ${roots.length - failed} items, ${failed} failed.`);
      }
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not restore.");
      await refresh();
    } finally {
      setBulkBusy(false);
    }
  }

  async function confirmPurge() {
    if (!purging) return;
    setBusyUid(purging.uid);
    try {
      await api(`/files/${purging.uid}?permanent=true`, { method: "DELETE" });
      setPurging(null);
      toast.success("Deleted forever.");
      await refresh();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not delete forever.",
      );
    } finally {
      setBusyUid(null);
    }
  }

  async function confirmPurgeAll() {
    setBulkBusy(true);
    try {
      const roots = await collectTrashRoots();
      let failed = 0;
      for (const item of roots) {
        try {
          await api(`/files/${item.uid}?permanent=true`, { method: "DELETE" });
        } catch {
          failed++;
        }
      }
      setPurgingAll(false);
      if (failed === 0) {
        toast.success(
          roots.length === 1
            ? "Deleted forever."
            : `Deleted ${roots.length} items forever.`,
        );
      } else {
        toast.warning(
          `Deleted ${roots.length - failed} items, ${failed} could not be removed.`,
        );
      }
      await refresh();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not delete forever.",
      );
      await refresh();
    } finally {
      setBulkBusy(false);
    }
  }

  const actionsLocked = loading || bulkBusy || busyUid !== null;
  const bulkDisabled = actionsLocked || items.length === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Trash</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Deleted files and folders. Restore them, or let them go for good.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            disabled={bulkDisabled}
            onClick={() => void restoreAll()}
            variant="outline"
          >
            <RotateCcw data-icon="inline-start" />
            Restore all
          </Button>
          <Button
            className="text-destructive"
            disabled={bulkDisabled}
            onClick={() => setPurgingAll(true)}
            variant="outline"
          >
            <Trash2 data-icon="inline-start" />
            Delete all
          </Button>
        </div>
      </div>

      <div className="flex items-start gap-2.5 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
        <TriangleAlert
          className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-500"
          size={16}
        />
        <p>
          Items in the trash are permanently deleted after {RETENTION_DAYS} days.
          After that they cannot be restored.
        </p>
      </div>

      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="w-40">Storage</TableHead>
              <TableHead className="w-28">Size</TableHead>
              <TableHead className="w-32">Deleted</TableHead>
              <TableHead className="w-28">Days left</TableHead>
              <TableHead className="w-56" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {!loading && items.length === 0 && (
              <TableRow>
                <TableCell
                  className="py-12 text-center text-sm text-muted-foreground"
                  colSpan={6}
                >
                  <Trash2 className="mx-auto mb-3 text-muted-foreground" size={20} />
                  The trash is empty.
                </TableCell>
              </TableRow>
            )}
            {items.map((item) => {
              const meta = connectionMeta(item.provider_connection_id, connections);
              return (
                <TableRow key={item.uid}>
                  <TableCell>
                    <div className="flex items-center gap-2 font-medium">
                      {item.type === "folder" ? (
                        <FolderOpen className="text-muted-foreground" size={16} />
                      ) : (
                        <FileIcon className="text-muted-foreground" size={16} />
                      )}
                      {item.name}
                    </div>
                  </TableCell>
                  <TableCell>
                    {meta ? (
                      <div className="flex items-center gap-1.5 text-muted-foreground">
                        <StorageProviderIcon
                          providerType={meta.providerType}
                          variant={meta.variant}
                          size="sm"
                        />
                        <span className="truncate text-xs font-mono">
                          {meta.name}
                        </span>
                      </div>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {item.type === "folder" ? "—" : formatBytes(item.size)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {item.deleted_at
                      ? new Date(item.deleted_at).toLocaleDateString()
                      : "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {daysLeft(item.deleted_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-2">
                      <Button
                        disabled={actionsLocked}
                        onClick={() => void restore(item)}
                        size="sm"
                        variant="outline"
                      >
                        <RotateCcw size={14} /> Restore
                      </Button>
                      <Button
                        className="text-destructive"
                        disabled={actionsLocked}
                        onClick={() => setPurging(item)}
                        size="sm"
                        variant="ghost"
                      >
                        <Trash2 size={14} /> Delete forever
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {hasMore && (
        <div className="flex justify-center">
          <Button
            disabled={loadingMore}
            onClick={() => void loadMore()}
            size="sm"
            variant="outline"
          >
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}

      <AlertDialog
        onOpenChange={(open) => !open && setPurging(null)}
        open={purging !== null}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{purging?.name}&rdquo; forever?</AlertDialogTitle>
            <AlertDialogDescription>
              {purging?.type === "folder"
                ? "The folder and everything inside it will be removed permanently. This cannot be undone."
                : "The file will be removed permanently. This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void confirmPurge()}
              variant="destructive"
            >
              Yes, delete forever
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        onOpenChange={(open) => !open && setPurgingAll(false)}
        open={purgingAll}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete everything in the trash forever?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Every file and folder in the trash will be removed permanently.
              This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void confirmPurgeAll()}
              variant="destructive"
            >
              Yes, delete all forever
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
