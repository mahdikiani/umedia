"use client";

import {
  File as FileIcon,
  FolderOpen,
  Star,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
  fileContentUrl,
  LIST_PAGE_SIZE,
  type MediaFileItem,
  type Page,
} from "@/lib/api";

function starredListPath(offset = 0): string {
  return `/files?scope=starred&limit=${LIST_PAGE_SIZE}&offset=${offset}`;
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

export default function StarredPage() {
  const [items, setItems] = useState<MediaFileItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [busyUid, setBusyUid] = useState<string | null>(null);

  async function refresh() {
    const page = await api<Page<MediaFileItem>>(starredListPath());
    setItems(page.items);
    setHasMore(page.has_more);
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const page = await api<Page<MediaFileItem>>(starredListPath(items.length));
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
    api<Page<MediaFileItem>>(starredListPath())
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setHasMore(page.has_more);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(
          error instanceof Error ? error.message : "Could not load starred files.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function unstar(item: MediaFileItem) {
    setBusyUid(item.uid);
    try {
      await api<MediaFileItem>(`/files/${item.uid}`, {
        method: "PATCH",
        body: JSON.stringify({ starred: false }),
      });
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not unstar.");
    } finally {
      setBusyUid(null);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Starred</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Files and folders you marked with a star.
        </p>
      </div>

      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="w-28">Size</TableHead>
              <TableHead className="w-32">Modified</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!loading && items.length === 0 && (
              <TableRow>
                <TableCell
                  className="py-12 text-center text-sm text-muted-foreground"
                  colSpan={3}
                >
                  <Star className="mx-auto mb-3 text-muted-foreground" size={20} />
                  Nothing starred yet.
                </TableCell>
              </TableRow>
            )}
            {items.map((item) => (
              <TableRow key={item.uid}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    {item.type === "folder" ? (
                      <Link
                        className="flex items-center gap-2 font-medium hover:underline"
                        href={`/files?folder=${encodeURIComponent(item.uid)}`}
                      >
                        <FolderOpen className="text-muted-foreground" size={16} />
                        {item.name}
                      </Link>
                    ) : (
                      <a
                        className="flex items-center gap-2 font-medium hover:underline"
                        href={fileContentUrl(item.uid, item.name)}
                        rel="noreferrer"
                        target="_blank"
                      >
                        <FileIcon className="text-muted-foreground" size={16} />
                        {item.name}
                      </a>
                    )}
                    <Button
                      aria-label="Remove star"
                      aria-pressed
                      disabled={busyUid === item.uid}
                      onClick={() => void unstar(item)}
                      size="icon-sm"
                      variant="ghost"
                    >
                      <Star className="fill-foreground" size={14} />
                    </Button>
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {item.type === "folder" ? "—" : formatBytes(item.size)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {new Date(item.updated_at).toLocaleDateString()}
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
