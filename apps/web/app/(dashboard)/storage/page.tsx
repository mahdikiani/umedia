"use client";

import { FolderOpen, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { FileTypeChip } from "@/components/file-type-chip";
import { StorageProviderIcon } from "@/components/storage-provider-icon";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  type Page,
  type ProviderConnection,
  type StorageObjectItem,
} from "@/lib/api";

function storageHref(providerId: string, parentRef?: string): string {
  const params = new URLSearchParams({ provider: providerId });
  if (parentRef) params.set("parent", parentRef);
  return `/storage?${params.toString()}`;
}

function storageObjectsPath(
  providerId: string,
  parentRef: string | null,
  query: string,
  offset = 0,
): string {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  } else {
    params.set("only_parent", "true");
  }
  if (parentRef) params.set("parent_ref", parentRef);
  params.set("limit", String(LIST_PAGE_SIZE));
  params.set("offset", String(offset));
  return `/providers/${encodeURIComponent(providerId)}/objects?${params.toString()}`;
}

function storageParentName(parentRef: string): string {
  const segments = parentRef.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? parentRef;
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

type SyncStatus = {
  status: "idle" | "running";
  connection_id: string;
};

/** How often Storage re-checks `GET .../sync` while a background job runs. */
const SYNC_POLL_MS = 1500;

export default function StoragePage() {
  return (
    <Suspense
      fallback={
        <div className="py-16 text-center text-sm text-muted-foreground">
          Loading storage…
        </div>
      }
    >
      <StorageBrowser />
    </Suspense>
  );
}

function StorageBrowser() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedConnectionId = searchParams.get("provider");
  const currentParentRef = searchParams.get("parent");
  const query = (searchParams.get("q") ?? "").trim();

  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [objects, setObjects] = useState<StorageObjectItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const selectedConnection =
    connections.find((connection) => connection.uid === requestedConnectionId)
    ?? connections[0]
    ?? null;
  const connectionId = selectedConnection?.uid ?? null;

  const parentRef = useRef(currentParentRef);
  const queryRef = useRef(query);
  parentRef.current = currentParentRef;
  queryRef.current = query;

  const stopPollRef = useRef<(() => void) | null>(null);

  function applyFirstPage(page: Page<StorageObjectItem>) {
    setObjects(page.items);
    setHasMore(page.has_more);
  }

  async function refreshFirstPage(providerId: string) {
    const page = await api<Page<StorageObjectItem>>(
      storageObjectsPath(providerId, parentRef.current, queryRef.current),
    );
    applyFirstPage(page);
  }

  /** Poll `GET /providers/{id}/sync` until idle. Keeps the Sync button
   * spinning/disabled for the whole background job. */
  function watchSync(providerId: string) {
    stopPollRef.current?.();
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sawRunning = false;

    const poll = async () => {
      try {
        const status = await api<SyncStatus>(`/providers/${providerId}/sync`);
        if (cancelled) return;
        if (status.status === "running") {
          sawRunning = true;
          setSyncing(true);
          timer = setTimeout(() => {
            void poll();
          }, SYNC_POLL_MS);
          return;
        }
        setSyncing(false);
        if (sawRunning) {
          try {
            await refreshFirstPage(providerId);
          } catch {
            // Best-effort refresh after the job finishes.
          }
        }
      } catch {
        if (!cancelled) setSyncing(false);
      }
    };

    stopPollRef.current = () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
    void poll();
  }

  useEffect(() => {
    api<ProviderConnection[]>("/providers")
      .then((list) => {
        setConnections(list);
        if (list.length === 0) setLoading(false);
      })
      .catch((error: unknown) => {
        setLoading(false);
        toast.error(
          error instanceof Error ? error.message : "Could not load providers.",
        );
      });
  }, []);

  useEffect(() => {
    if (!connectionId) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api<Page<StorageObjectItem>>(
      storageObjectsPath(connectionId, currentParentRef, query),
    )
      .then((page) => {
        if (cancelled) return;
        applyFirstPage(page);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(
          error instanceof Error ? error.message : "Could not load storage objects.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [connectionId, currentParentRef, query]);

  useEffect(() => {
    if (!connectionId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSyncing(false);
      stopPollRef.current?.();
      return;
    }
    watchSync(connectionId);
    return () => {
      stopPollRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionId]);

  function switchConnection(uid: string) {
    setObjects([]);
    setHasMore(false);
    router.push(storageHref(uid));
  }

  async function loadMore() {
    if (!connectionId) return;
    setLoadingMore(true);
    try {
      const page = await api<Page<StorageObjectItem>>(
        storageObjectsPath(connectionId, currentParentRef, query, objects.length),
      );
      setObjects((previous) => [...previous, ...page.items]);
      setHasMore(page.has_more);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not load more objects.",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  async function sync() {
    if (!connectionId || syncing) return;
    setSyncing(true);
    try {
      await api(`/providers/${connectionId}/sync`, { method: "POST" });
      toast.success("Sync started — running in the background.");
      watchSync(connectionId);
    } catch (error) {
      try {
        const status = await api<SyncStatus>(`/providers/${connectionId}/sync`);
        if (status.status === "running") {
          toast.message("Sync is already running in the background.");
          watchSync(connectionId);
          return;
        }
      } catch {
        // fall through
      }
      setSyncing(false);
      toast.error(error instanceof Error ? error.message : "Could not sync provider.");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Storage</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Browse the physical objects indexed for each provider connection.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {selectedConnection && (
            <Select
              onValueChange={(value) => {
                if (typeof value === "string") switchConnection(value);
              }}
              value={selectedConnection.uid}
            >
              <SelectTrigger aria-label="Provider connection" className="w-52">
                <SelectValue>
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <StorageProviderIcon
                      providerType={selectedConnection.provider_type}
                      size="sm"
                    />
                    <span className="truncate">{selectedConnection.name}</span>
                  </span>
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {connections.map((connection) => (
                  <SelectItem key={connection.uid} value={connection.uid}>
                    <span className="inline-flex min-w-0 items-center gap-1.5">
                      <StorageProviderIcon
                        providerType={connection.provider_type}
                        size="sm"
                      />
                      <span className="truncate">{connection.name}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button
            disabled={!connectionId || syncing}
            onClick={() => void sync()}
            size="sm"
          >
            <RefreshCw className={syncing ? "animate-spin" : undefined} size={14} />
            {syncing ? "Syncing…" : "Sync"}
          </Button>
        </div>
      </div>

      {connectionId && (
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              {currentParentRef ? (
                <BreadcrumbLink render={<Link href={storageHref(connectionId)} />}>
                  Storage
                </BreadcrumbLink>
              ) : (
                <BreadcrumbPage>Storage</BreadcrumbPage>
              )}
            </BreadcrumbItem>
            {currentParentRef && (
              <Fragment>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  <BreadcrumbPage>
                    {storageParentName(currentParentRef)}
                  </BreadcrumbPage>
                </BreadcrumbItem>
              </Fragment>
            )}
          </BreadcrumbList>
        </Breadcrumb>
      )}

      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="w-48">Type</TableHead>
              <TableHead>Content reference</TableHead>
              <TableHead className="w-28">Size</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!loading && objects.length === 0 && (
              <TableRow>
                <TableCell
                  className="py-12 text-center text-sm text-muted-foreground"
                  colSpan={4}
                >
                  <StorageProviderIcon
                    className="mx-auto mb-3"
                    providerType={selectedConnection?.provider_type ?? "unknown"}
                    size="md"
                  />
                  {query
                    ? "No matching objects in this provider."
                    : "No indexed objects for this provider."}
                </TableCell>
              </TableRow>
            )}
            {objects.map((object) => (
              <TableRow key={object.uid}>
                <TableCell className="font-medium">
                  {object.type === "folder" && connectionId ? (
                    <Link
                      className="flex items-center gap-2 hover:underline"
                      href={storageHref(connectionId, object.content_reference)}
                    >
                      <FolderOpen className="text-muted-foreground" size={16} />
                      {object.name}
                    </Link>
                  ) : (
                    object.name
                  )}
                </TableCell>
                <TableCell>
                  <FileTypeChip item={object} />
                </TableCell>
                <TableCell className="max-w-md whitespace-normal break-all font-mono text-xs text-muted-foreground">
                  {object.content_reference}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {object.type === "folder" ? "—" : formatBytes(object.size)}
                </TableCell>
              </TableRow>
            ))}
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
    </div>
  );
}
