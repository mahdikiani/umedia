"use client";

import {
  ChevronDown,
  ChevronUp,
  Link2,
  Star,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { toast } from "sonner";

import { FileItemMenu } from "@/components/file-item-menu";
import { FileThumbnail } from "@/components/file-thumbnail";
import { FileTypeChip } from "@/components/file-type-chip";
import {
  connectionMeta,
  StorageProviderIcon,
} from "@/components/storage-provider-icon";
import { Badge } from "@/components/ui/badge";
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
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
  createTransfer,
  removeFromTemporary,
  fileContentUrl,
  LIST_PAGE_SIZE,
  type MediaFileItem,
  type Page,
  type ProviderConnection,
  type TransferJob,
} from "@/lib/api";
import { matchesTypeFilter, typeFilterKey } from "@/lib/file-type";
import {
  SORTABLE_COLUMNS,
  groupFiles,
  nextSortClick,
  type FileSort,
  type FilesView,
  type GroupBy,
  type SortOrder,
} from "@/lib/files-view";
import { rememberRecent } from "@/lib/recents";
import {
  dataTransferHasLibraryIds,
  parseLibraryDragPayload,
  setLibraryDragPayload,
  transferOperationFromEvent,
} from "@/lib/umedia-dnd";
import { cn } from "@/lib/utils";

type Crumb = {
  uid: string | null;
  name: string;
  connectionId?: string | null;
};

export type FileBrowserPaneProps = {
  /** Stable id for dual-pane tests / aria. */
  paneId: string;
  parentId: string | null;
  onNavigate: (uid: string | null) => void;
  /** When false, navigation uses onNavigate only (no Next Link href). */
  linkNavigation?: boolean;
  folderHref?: (uid: string | null) => string;
  query?: string;
  sort: FileSort;
  order: SortOrder;
  groupBy: GroupBy;
  filesView: FilesView;
  typeFilter: string | null;
  onSortChange?: (sort: FileSort, order: SortOrder) => void;
  onTypeFilterToggle?: (key: string) => void;
  connections: ProviderConnection[];
  busyUid: string | null;
  onRename: (item: MediaFileItem) => void;
  onMove: (item: MediaFileItem) => void;
  onCopy: (item: MediaFileItem) => void;
  onAddToTemporary: (item: MediaFileItem) => void;
  onShare: (item: MediaFileItem) => void;
  onDelete: (item: MediaFileItem) => void;
  onToggleStar: (item: MediaFileItem) => void;
  onTransferCreated: (job: TransferJob) => void;
  /** After a Temporary→folder move, pointers were dropped — refresh dock. */
  onTemporaryPointersChanged?: () => void;
  refreshToken?: number;
  showBreadcrumb?: boolean;
  className?: string;
};

function StorageConnectionLabel({
  connectionId,
  connections,
  className,
}: {
  connectionId: string | null | undefined;
  connections: ProviderConnection[];
  className?: string;
}) {
  const meta = connectionMeta(connectionId, connections);
  if (!meta) return null;
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
      <StorageProviderIcon providerType={meta.providerType} size="sm" />
      <span className="truncate">{meta.name}</span>
    </span>
  );
}

function filesListPath(
  parentId: string | null,
  query: string,
  sort: FileSort,
  order: SortOrder,
  offset = 0,
): string {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (parentId) params.set("parent_id", parentId);
  params.set("sort", sort);
  params.set("order", order);
  params.set("limit", String(LIST_PAGE_SIZE));
  params.set("offset", String(offset));
  return `/files?${params.toString()}`;
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

export function FileBrowserPane({
  paneId,
  parentId,
  onNavigate,
  linkNavigation = true,
  folderHref,
  query = "",
  sort,
  order,
  groupBy,
  filesView,
  typeFilter,
  onSortChange,
  onTypeFilterToggle,
  connections,
  busyUid,
  onRename,
  onMove,
  onCopy,
  onAddToTemporary,
  onShare,
  onDelete,
  onToggleStar,
  onTransferCreated,
  onTemporaryPointersChanged,
  refreshToken = 0,
  showBreadcrumb = true,
  className,
}: FileBrowserPaneProps) {
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ uid: null, name: "Files" }]);
  const [items, setItems] = useState<MediaFileItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dropActive, setDropActive] = useState(false);
  const [folderDropUid, setFolderDropUid] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    void api<Page<MediaFileItem>>(filesListPath(parentId, query, sort, order))
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setHasMore(page.has_more);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(
          error instanceof Error ? error.message : "Could not load files.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [parentId, query, sort, order, refreshToken]);

  useEffect(() => {
    let cancelled = false;
    async function loadCrumbs() {
      if (!parentId) {
        if (!cancelled) setCrumbs([{ uid: null, name: "Files" }]);
        return;
      }
      const chain: Crumb[] = [];
      let cursor: string | null = parentId;
      for (let depth = 0; cursor && depth < 64; depth += 1) {
        try {
          const item: MediaFileItem = await api(`/files/${cursor}`);
          chain.unshift({
            uid: item.uid,
            name: item.name,
            connectionId: item.provider_connection_id,
          });
          if (item.uid === parentId) {
            rememberRecent({ uid: item.uid, name: item.name, type: "folder" });
          }
          cursor = item.parent_id;
        } catch {
          break;
        }
      }
      if (!cancelled) setCrumbs([{ uid: null, name: "Files" }, ...chain]);
    }
    void loadCrumbs();
    return () => {
      cancelled = true;
    };
  }, [parentId]);

  async function loadMore() {
    setLoadingMore(true);
    try {
      const page = await api<Page<MediaFileItem>>(
        filesListPath(parentId, query, sort, order, items.length),
      );
      setItems((previous) => [...previous, ...page.items]);
      setHasMore(page.has_more);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not load more files.",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleLibraryDrop(
    sourceIds: string[],
    destParentId: string | null,
    operation: "move" | "copy",
    fromTemporary = false,
  ) {
    if (sourceIds.includes(destParentId ?? "")) return;
    try {
      const job = await createTransfer({
        operation,
        source_ids: sourceIds,
        dest_parent_id: destParentId,
      });
      onTransferCreated(job);
      if (fromTemporary && operation === "move") {
        await Promise.all(
          sourceIds.map((id) => removeFromTemporary(id).catch(() => undefined)),
        );
        onTemporaryPointersChanged?.();
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not start transfer.",
      );
    }
  }

  function onPaneDragOver(event: React.DragEvent) {
    if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect =
      transferOperationFromEvent(event) === "copy" ? "copy" : "move";
    setDropActive(true);
  }

  function onPaneDragLeave() {
    setDropActive(false);
    setFolderDropUid(null);
  }

  function onPaneDrop(event: React.DragEvent) {
    if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    setDropActive(false);
    setFolderDropUid(null);
    const payload = parseLibraryDragPayload(event.dataTransfer);
    if (!payload || payload.sourceIds.length === 0) return;
    void handleLibraryDrop(
      payload.sourceIds,
      parentId,
      transferOperationFromEvent(event, payload),
      Boolean(payload.fromTemporary),
    );
  }

  function onFolderDrop(event: React.DragEvent, folderUid: string) {
    if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    setDropActive(false);
    setFolderDropUid(null);
    const payload = parseLibraryDragPayload(event.dataTransfer);
    if (!payload || payload.sourceIds.length === 0) return;
    void handleLibraryDrop(
      payload.sourceIds,
      folderUid,
      transferOperationFromEvent(event, payload),
      Boolean(payload.fromTemporary),
    );
  }

  function navigate(uid: string | null) {
    onNavigate(uid);
  }

  function hrefFor(uid: string | null): string {
    return folderHref?.(uid) ?? "#";
  }

  const visibleItems = items.filter((item) =>
    matchesTypeFilter(item, typeFilter),
  );
  const groups = groupFiles(visibleItems, groupBy);
  const emptyLabel = query
    ? "No matching files."
    : typeFilter
      ? "No files of this type."
      : "This folder is empty.";

  const currentStorage = parentId
    ? connectionMeta(crumbs.at(-1)?.connectionId, connections)
    : null;

  function cycleSort(column: FileSort) {
    if (!onSortChange) return;
    const next = nextSortClick(sort, order, column);
    onSortChange(next.sort, next.order);
  }

  function itemDragProps(item: MediaFileItem) {
    return {
      draggable: true as const,
      onDragStart: (event: React.DragEvent) => {
        setLibraryDragPayload(event.dataTransfer, [item.uid]);
      },
    };
  }

  /** Block navigation when a library drag lands on a folder link/button. */
  function folderLinkDragGuard() {
    return {
      onDragOver: (event: React.DragEvent) => {
        if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
        event.preventDefault();
      },
      onDrop: (event: React.DragEvent) => {
        if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
        event.preventDefault();
      },
    };
  }

  function folderDropProps(folderUid: string) {
    return {
      onDragOver: (event: React.DragEvent) => {
        if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect =
          transferOperationFromEvent(event) === "copy" ? "copy" : "move";
        setFolderDropUid(folderUid);
        setDropActive(false);
      },
      onDragLeave: (event: React.DragEvent) => {
        if (!dataTransferHasLibraryIds(event.dataTransfer)) return;
        const related = event.relatedTarget as Node | null;
        if (related && event.currentTarget.contains(related)) return;
        setFolderDropUid((current) => (current === folderUid ? null : current));
      },
      onDrop: (event: React.DragEvent) => onFolderDrop(event, folderUid),
    };
  }

  function folderDropHighlight(folderUid: string): string {
    return folderDropUid === folderUid
      ? "bg-primary/10 ring-2 ring-inset ring-primary"
      : "";
  }

  return (
    <div
      className={`relative flex min-w-0 flex-1 flex-col gap-3 ${className ?? ""}`}
      data-pane={paneId}
      data-testid={`file-pane-${paneId}`}
      onDragLeave={onPaneDragLeave}
      onDragOver={onPaneDragOver}
      onDrop={onPaneDrop}
    >
      {dropActive ? (
        <div className="pointer-events-none absolute inset-0 z-10 rounded-xl border-2 border-dashed border-primary bg-primary/5" />
      ) : null}
      {showBreadcrumb ? (
        <div className="flex items-center gap-3">
          <Breadcrumb>
            <BreadcrumbList>
              {crumbs.map((crumb, index) => (
                <Fragment key={crumb.uid ?? "root"}>
                  {index > 0 && <BreadcrumbSeparator />}
                  <BreadcrumbItem>
                    {index === crumbs.length - 1 ? (
                      <BreadcrumbPage>{crumb.name}</BreadcrumbPage>
                    ) : linkNavigation && folderHref ? (
                      <BreadcrumbLink
                        render={<Link href={hrefFor(crumb.uid)} />}
                      >
                        {crumb.name}
                      </BreadcrumbLink>
                    ) : (
                      <BreadcrumbLink
                        render={
                          <button
                            onClick={() => navigate(crumb.uid)}
                            type="button"
                          />
                        }
                      >
                        {crumb.name}
                      </BreadcrumbLink>
                    )}
                  </BreadcrumbItem>
                </Fragment>
              ))}
            </BreadcrumbList>
          </Breadcrumb>
          {currentStorage ? (
            <span className="inline-flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground">
              <StorageProviderIcon
                providerType={currentStorage.providerType}
                size="sm"
              />
              <span className="truncate">{currentStorage.name}</span>
            </span>
          ) : null}
        </div>
      ) : null}

      {filesView === "list" ? (
        <div className="overflow-hidden rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                {SORTABLE_COLUMNS.map((column) => {
                  const state =
                    sort === column.key
                      ? order === "asc"
                        ? "ascending"
                        : "descending"
                      : "none";
                  return (
                    <TableHead
                      aria-sort={state}
                      className={
                        column.key === "type"
                          ? "w-48"
                          : column.key === "size"
                            ? "w-28"
                            : column.key === "updated_at"
                              ? "w-32"
                              : undefined
                      }
                      key={column.key}
                    >
                      <Button
                        className="-ms-2.5 font-medium"
                        disabled={!onSortChange}
                        onClick={() => cycleSort(column.key)}
                        size="sm"
                        variant="ghost"
                      >
                        {column.label}
                        {state === "ascending" ? (
                          <ChevronUp data-icon="inline-end" />
                        ) : null}
                        {state === "descending" ? (
                          <ChevronDown data-icon="inline-end" />
                        ) : null}
                      </Button>
                    </TableHead>
                  );
                })}
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleItems.length === 0 && !loading && (
                <TableRow>
                  <TableCell
                    className="py-10 text-center text-sm text-muted-foreground"
                    colSpan={5}
                  >
                    {emptyLabel}
                  </TableCell>
                </TableRow>
              )}
              {groups.map((group) => (
                <Fragment key={group.id}>
                  {group.label ? (
                    <TableRow>
                      <TableCell
                        className="bg-muted/40 font-medium text-muted-foreground"
                        colSpan={5}
                      >
                        {group.label}
                      </TableCell>
                    </TableRow>
                  ) : null}
                  {group.items.map((item) => {
                    const isFolder = item.type === "folder";
                    const dropProps = isFolder
                      ? folderDropProps(item.uid)
                      : {};
                    const highlight = isFolder
                      ? folderDropHighlight(item.uid)
                      : "";
                    return (
                    <TableRow
                      className={highlight || undefined}
                      data-drop-folder={isFolder ? item.uid : undefined}
                      key={item.uid}
                    >
                      <TableCell {...dropProps}>
                        <div
                          className="flex items-center gap-2"
                          {...itemDragProps(item)}
                        >
                          {item.type === "folder" ? (
                            linkNavigation && folderHref ? (
                              <Link
                                className="flex min-w-0 items-center gap-2 font-medium hover:underline"
                                href={hrefFor(item.uid)}
                                {...folderLinkDragGuard()}
                                onClick={() =>
                                  rememberRecent({
                                    uid: item.uid,
                                    name: item.name,
                                    type: "folder",
                                  })
                                }
                              >
                                <span className="size-5 shrink-0">
                                  <FileThumbnail
                                    compact
                                    decorative
                                    item={item}
                                  />
                                </span>
                                <span className="truncate">{item.name}</span>
                              </Link>
                            ) : (
                              <button
                                className="flex min-w-0 items-center gap-2 font-medium hover:underline"
                                {...folderLinkDragGuard()}
                                onClick={() => {
                                  rememberRecent({
                                    uid: item.uid,
                                    name: item.name,
                                    type: "folder",
                                  });
                                  navigate(item.uid);
                                }}
                                type="button"
                              >
                                <span className="size-5 shrink-0">
                                  <FileThumbnail
                                    compact
                                    decorative
                                    item={item}
                                  />
                                </span>
                                <span className="truncate">{item.name}</span>
                              </button>
                            )
                          ) : (
                            <a
                              className="flex min-w-0 items-center gap-2 font-medium hover:underline"
                              href={fileContentUrl(item.uid, item.name)}
                              onClick={() =>
                                rememberRecent({
                                  uid: item.uid,
                                  name: item.name,
                                  type: "file",
                                })
                              }
                              rel="noreferrer"
                              target="_blank"
                            >
                              <span className="size-5 shrink-0">
                                <FileThumbnail compact decorative item={item} />
                              </span>
                              <span className="truncate">{item.name}</span>
                            </a>
                          )}
                          {!parentId &&
                          connectionMeta(
                            item.provider_connection_id,
                            connections,
                          ) ? (
                            <StorageConnectionLabel
                              className="text-xs text-muted-foreground"
                              connectionId={item.provider_connection_id}
                              connections={connections}
                            />
                          ) : null}
                          <Button
                            aria-label={
                              item.starred ? "Remove star" : "Add star"
                            }
                            aria-pressed={item.starred}
                            className={
                              item.starred
                                ? "text-foreground"
                                : "text-muted-foreground/50 hover:text-muted-foreground"
                            }
                            disabled={busyUid === item.uid}
                            onClick={() => void onToggleStar(item)}
                            size="icon-sm"
                            variant="ghost"
                          >
                            <Star
                              className={
                                item.starred ? "fill-foreground" : undefined
                              }
                            />
                          </Button>
                          {item.status !== "completed" && (
                            <Badge
                              variant={
                                item.status === "failed"
                                  ? "destructive"
                                  : "secondary"
                              }
                            >
                              {item.status}
                            </Badge>
                          )}
                          {item.public_permission === "read" && (
                            <Badge variant="outline">
                              <Link2 /> Public
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell {...dropProps}>
                        <FileTypeChip
                          active={typeFilter === typeFilterKey(item)}
                          item={item}
                          onToggle={onTypeFilterToggle ?? (() => undefined)}
                        />
                      </TableCell>
                      <TableCell
                        className="text-muted-foreground"
                        {...dropProps}
                      >
                        {item.type === "folder" ? "—" : formatBytes(item.size)}
                      </TableCell>
                      <TableCell
                        className="text-muted-foreground"
                        {...dropProps}
                      >
                        {new Date(item.updated_at).toLocaleDateString()}
                      </TableCell>
                      <TableCell {...dropProps}>
                        <FileItemMenu
                          item={item}
                          onAddToTemporary={onAddToTemporary}
                          onCopy={onCopy}
                          onDelete={onDelete}
                          onMove={onMove}
                          onRename={onRename}
                          onShare={onShare}
                        />
                      </TableCell>
                    </TableRow>
                    );
                  })}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {visibleItems.length === 0 && !loading && (
            <div className="rounded-xl border py-10 text-center text-sm text-muted-foreground">
              {emptyLabel}
            </div>
          )}
          {groups.map((group) => (
            <section className="flex flex-col gap-3" key={group.id}>
              {group.label ? (
                <h2 className="text-sm font-medium text-muted-foreground">
                  {group.label}
                </h2>
              ) : null}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {group.items.map((item) =>
                  item.type === "folder" ? (
                    <div
                      className={`rounded-xl ${folderDropHighlight(item.uid)}`}
                      data-drop-folder={item.uid}
                      data-kind="folder"
                      key={item.uid}
                      {...itemDragProps(item)}
                      {...folderDropProps(item.uid)}
                    >
                      <Card className="pt-0" size="sm">
                      {linkNavigation && folderHref ? (
                        <Link
                          className="flex aspect-4/3 items-center justify-center bg-muted"
                          href={hrefFor(item.uid)}
                          {...folderLinkDragGuard()}
                          onClick={() =>
                            rememberRecent({
                              uid: item.uid,
                              name: item.name,
                              type: "folder",
                            })
                          }
                        >
                          <FileThumbnail item={item} />
                        </Link>
                      ) : (
                        <button
                          className="flex aspect-4/3 items-center justify-center bg-muted"
                          {...folderLinkDragGuard()}
                          onClick={() => {
                            rememberRecent({
                              uid: item.uid,
                              name: item.name,
                              type: "folder",
                            });
                            navigate(item.uid);
                          }}
                          type="button"
                        >
                          <FileThumbnail item={item} />
                        </button>
                      )}
                      <CardHeader>
                        <CardTitle className="min-w-0 truncate">
                          {linkNavigation && folderHref ? (
                            <Link
                              className="hover:underline"
                              href={hrefFor(item.uid)}
                              {...folderLinkDragGuard()}
                            >
                              {item.name}
                            </Link>
                          ) : (
                            <button
                              className="hover:underline"
                              {...folderLinkDragGuard()}
                              onClick={() => navigate(item.uid)}
                              type="button"
                            >
                              {item.name}
                            </button>
                          )}
                        </CardTitle>
                        {!parentId &&
                        connectionMeta(
                          item.provider_connection_id,
                          connections,
                        ) ? (
                          <CardDescription>
                            <StorageConnectionLabel
                              connectionId={item.provider_connection_id}
                              connections={connections}
                            />
                          </CardDescription>
                        ) : null}
                        <CardAction>
                          <div className="flex items-center">
                            <Button
                              aria-label={
                                item.starred ? "Remove star" : "Add star"
                              }
                              aria-pressed={item.starred}
                              className={
                                item.starred
                                  ? "text-foreground"
                                  : "text-muted-foreground/50 hover:text-muted-foreground"
                              }
                              disabled={busyUid === item.uid}
                              onClick={() => void onToggleStar(item)}
                              size="icon-sm"
                              variant="ghost"
                            >
                              <Star
                                className={
                                  item.starred ? "fill-foreground" : undefined
                                }
                              />
                            </Button>
                            <FileItemMenu
                              item={item}
                              onAddToTemporary={onAddToTemporary}
                              onCopy={onCopy}
                              onDelete={onDelete}
                              onMove={onMove}
                              onRename={onRename}
                              onShare={onShare}
                            />
                          </div>
                        </CardAction>
                      </CardHeader>
                      </Card>
                    </div>
                  ) : (
                    <div key={item.uid} {...itemDragProps(item)}>
                    <Card
                      className="pt-0"
                      data-kind="file"
                      size="sm"
                    >
                      <a
                        className="flex aspect-4/3 items-center justify-center bg-muted"
                        href={fileContentUrl(item.uid, item.name)}
                        onClick={() =>
                          rememberRecent({
                            uid: item.uid,
                            name: item.name,
                            type: "file",
                          })
                        }
                        rel="noreferrer"
                        target="_blank"
                      >
                        <FileThumbnail item={item} />
                      </a>
                      <CardHeader>
                        <CardTitle className="min-w-0 truncate">
                          <a
                            className="hover:underline"
                            href={fileContentUrl(item.uid, item.name)}
                            rel="noreferrer"
                            target="_blank"
                          >
                            {item.name}
                          </a>
                        </CardTitle>
                        <CardDescription className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <span>{formatBytes(item.size)}</span>
                          {!parentId &&
                          connectionMeta(
                            item.provider_connection_id,
                            connections,
                          ) ? (
                            <>
                              <span aria-hidden>·</span>
                              <StorageConnectionLabel
                                connectionId={item.provider_connection_id}
                                connections={connections}
                              />
                            </>
                          ) : null}
                        </CardDescription>
                        <CardAction>
                          <div className="flex items-center">
                            <Button
                              aria-label={
                                item.starred ? "Remove star" : "Add star"
                              }
                              aria-pressed={item.starred}
                              className={
                                item.starred
                                  ? "text-foreground"
                                  : "text-muted-foreground/50 hover:text-muted-foreground"
                              }
                              disabled={busyUid === item.uid}
                              onClick={() => void onToggleStar(item)}
                              size="icon-sm"
                              variant="ghost"
                            >
                              <Star
                                className={
                                  item.starred ? "fill-foreground" : undefined
                                }
                              />
                            </Button>
                            <FileItemMenu
                              item={item}
                              onAddToTemporary={onAddToTemporary}
                              onCopy={onCopy}
                              onDelete={onDelete}
                              onMove={onMove}
                              onRename={onRename}
                              onShare={onShare}
                            />
                          </div>
                        </CardAction>
                      </CardHeader>
                      <CardFooter className="justify-between gap-2">
                        <FileTypeChip
                          active={typeFilter === typeFilterKey(item)}
                          item={item}
                          onToggle={onTypeFilterToggle ?? (() => undefined)}
                        />
                        {item.status !== "completed" && (
                          <Badge
                            variant={
                              item.status === "failed"
                                ? "destructive"
                                : "secondary"
                            }
                          >
                            {item.status}
                          </Badge>
                        )}
                        {item.public_permission === "read" && (
                          <Badge variant="outline">
                            <Link2 /> Public
                          </Badge>
                        )}
                      </CardFooter>
                    </Card>
                    </div>
                  ),
                )}
              </div>
            </section>
          ))}
        </div>
      )}

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
