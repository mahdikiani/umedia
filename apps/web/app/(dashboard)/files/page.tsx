"use client";

import {
  Database,
  File as FileIcon,
  FolderOpen,
  FolderPlus,
  Link2,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  Share2,
  Star,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import * as tus from "tus-js-client";

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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
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
import { ShareDialog } from "@/components/share-dialog";
import {
  api,
  apiForm,
  fileContentUrl,
  LIST_PAGE_SIZE,
  type MediaFileItem,
  type Page,
  type ProviderConnection,
} from "@/lib/api";
import { rememberRecent } from "@/lib/recents";

type Crumb = { uid: string | null; name: string };

type FileSort = "name" | "updated_at" | "type";
type SortOrder = "asc" | "desc";
type SortOption = {
  value: string;
  label: string;
  sort: FileSort;
  order: SortOrder;
};

const DEFAULT_SORT_OPTION: SortOption = {
  value: "name-asc",
  label: "Name (A→Z)",
  sort: "name",
  order: "asc",
};

const SORT_OPTIONS: readonly SortOption[] = [
  DEFAULT_SORT_OPTION,
  { value: "name-desc", label: "Name (Z→A)", sort: "name", order: "desc" },
  {
    value: "updated-desc",
    label: "Last modified (newest)",
    sort: "updated_at",
    order: "desc",
  },
  {
    value: "updated-asc",
    label: "Last modified (oldest)",
    sort: "updated_at",
    order: "asc",
  },
  { value: "type-asc", label: "Type", sort: "type", order: "asc" },
];

type FilesListOptions = {
  parentId: string | null;
  query: string;
  sort: FileSort;
  order: SortOrder;
  offset?: number;
};

type UploadInFlight = {
  key: string;
  name: string;
  percent: number;
  stage: "uploading" | "paused" | "finalizing";
};

function folderHref(
  uid: string | null,
  sort: FileSort,
  order: SortOrder,
): string {
  const params = new URLSearchParams();
  if (uid) params.set("folder", uid);
  params.set("sort", sort);
  params.set("order", order);
  return `/files?${params.toString()}`;
}

// How long the "waiting for it to finish processing" poll after a tus
// upload reports 100% keeps trying before giving up -- tus completing
// only means the bytes are fully staged; apps/media_files/uploads.py's
// completion hook then finalizes the actual MediaFile (dedup, the plugin
// write, verification) as a background task, so there's a real gap
// between "upload done" and "resource exists" to bridge here.
const FINALIZE_POLL_ATTEMPTS = 20;
const FINALIZE_POLL_INTERVAL_MS = 1500;

function filesListPath({
  parentId,
  query,
  sort,
  order,
  offset = 0,
}: FilesListOptions): string {
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

export default function FilesPage() {
  return (
    <Suspense
      fallback={
        <div className="py-16 text-center text-sm text-muted-foreground">
          Loading files…
        </div>
      }
    >
      <FilesBrowser />
    </Suspense>
  );
}

function FilesBrowser() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentParentId = searchParams.get("folder");
  const query = (searchParams.get("q") ?? "").trim();
  const sortParam = searchParams.get("sort");
  const orderParam = searchParams.get("order");
  const sortOption =
    SORT_OPTIONS.find(
      (option) => option.sort === sortParam && option.order === orderParam,
    ) ?? DEFAULT_SORT_OPTION;
  const { sort, order } = sortOption;

  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ uid: null, name: "Files" }]);
  const [items, setItems] = useState<MediaFileItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [connectionsLoaded, setConnectionsLoaded] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [renaming, setRenaming] = useState<MediaFileItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [sharing, setSharing] = useState<MediaFileItem | null>(null);
  const [deleting, setDeleting] = useState<MediaFileItem | null>(null);
  const [busyUid, setBusyUid] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadInFlight[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingSortClickRef = useRef<string | null>(null);
  // Not React state on purpose: a `tus.Upload` instance and its promise
  // `resolve` are imperative handles for pause/resume/cancel, not
  // renderable data -- `uploads` (state, above) holds the display fields
  // derived from these for the progress panel.
  const uploadControllers = useRef<
    Map<string, { upload: tus.Upload; resolve: () => void }>
  >(new Map());

  useEffect(() => {
    api<ProviderConnection[]>("/providers")
      .then((list) => {
        setConnections(list);
        if (list.length > 0) {
          setConnectionId(list[0].uid);
        }
      })
      .finally(() => setConnectionsLoaded(true));
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Flips the spinner on for this fetch (`currentParentId` changing is
    // itself the "external" trigger -- a folder navigation --
    // this effect is synchronizing to); the fetch's own `.then`/`.catch`
    // below set the actual list state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api<Page<MediaFileItem>>(
      filesListPath({ parentId: currentParentId, query, sort, order }),
    )
      .then((page) => {
        if (cancelled) return;
        // Navigation replaces the list; "Load more" below appends.
        setItems(page.items);
        setHasMore(page.has_more);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(error instanceof Error ? error.message : "Could not load files.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentParentId, query, sort, order]);

  // Rebuild the trail from the URL folder so browser back/forward and
  // deep links get the same crumbs as click-navigation.
  useEffect(() => {
    let cancelled = false;
    async function loadCrumbs() {
      if (!currentParentId) {
        if (!cancelled) setCrumbs([{ uid: null, name: "Files" }]);
        return;
      }
      const chain: Crumb[] = [];
      let cursor: string | null = currentParentId;
      for (let depth = 0; cursor && depth < 64; depth += 1) {
        try {
          const item: MediaFileItem = await api(`/files/${cursor}`);
          chain.unshift({ uid: item.uid, name: item.name });
          if (item.uid === currentParentId) {
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
  }, [currentParentId]);

  /** Re-fetch the first page and replace the list -- after a mutation
   * the safest view is the fresh top of the listing, not a stale
   * multi-page accumulation. */
  async function refresh() {
    const page = await api<Page<MediaFileItem>>(
      filesListPath({ parentId: currentParentId, query, sort, order }),
    );
    setItems(page.items);
    setHasMore(page.has_more);
  }

  async function loadMore() {
    setLoadingMore(true);
    try {
      const page = await api<Page<MediaFileItem>>(
        filesListPath({
          parentId: currentParentId,
          query,
          sort,
          order,
          offset: items.length,
        }),
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

  function switchConnection(uid: string) {
    setConnectionId(uid);
  }

  function replaceSort(value: string | null) {
    const next =
      SORT_OPTIONS.find((option) => option.value === value) ??
      DEFAULT_SORT_OPTION;
    const params = new URLSearchParams(searchParams.toString());
    params.set("sort", next.sort);
    params.set("order", next.order);
    router.replace(`/files?${params.toString()}`);
  }

  async function createFolder() {
    if (!newFolderName.trim()) return;
    const form = new FormData();
    form.set("name", newFolderName.trim());
    form.set("type", "folder");
    if (currentParentId) form.set("parent_id", currentParentId);
    try {
      await apiForm("/files", "POST", form);
      setNewFolderOpen(false);
      setNewFolderName("");
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create folder.");
    }
  }

  /** Waits for whatever just landed in `apps.media_files.uploads`'s
   * background finalize task to actually show up as a non-`processing`
   * `MediaFile` -- a tus upload reporting "success" only means its bytes
   * are fully staged, not that the file exists yet. Bounded: if it's
   * still `processing` after `FINALIZE_POLL_ATTEMPTS`, the badge already
   * rendered for that row says so; this just stops actively polling. */
  async function pollUntilSettled() {
    for (let attempt = 0; attempt < FINALIZE_POLL_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, FINALIZE_POLL_INTERVAL_MS));
      await refresh();
      const stillProcessing = await api<Page<MediaFileItem>>(
        filesListPath({ parentId: currentParentId, query, sort, order }),
      ).then((page) =>
        page.items.some(
          (item) =>
            item.provider_connection_id === connectionId
            && item.status === "processing",
        ),
      );
      if (!stillProcessing) return;
    }
  }

  function startSingleUpload(file: File): Promise<void> {
    // Only ever called from `uploadFiles`, itself only called from the
    // file input's `onChange` -- a real event handler, never render --
    // so `Date.now()` here can't produce the render-instability the rule
    // is guarding against.
    // eslint-disable-next-line react-hooks/purity
    const key = `${file.name}-${file.size}-${Date.now()}`;
    return new Promise<void>((resolve) => {
      const upload = new tus.Upload(file, {
        endpoint: "/api/v1/uploads/",
        chunkSize: 8 * 1024 * 1024,
        retryDelays: [0, 1000, 3000, 5000],
        metadata: {
          provider_connection_id: connectionId ?? "",
          name: file.name,
          // Required by tuspyserver's own HEAD route (used to resume
          // after a dropped connection), not just our own metadata --
          // see apps/media_files/uploads.py's `_validate_metadata`.
          filetype: file.type || "application/octet-stream",
          type: "file",
          ...(currentParentId ? { parent_id: currentParentId } : {}),
        },
        onError: (error) => {
          toast.error(`${file.name}: ${error.message}`);
          uploadControllers.current.delete(key);
          setUploads((previous) => previous.filter((u) => u.key !== key));
          resolve();
        },
        onProgress: (bytesUploaded, bytesTotal) => {
          const percent = Math.round((bytesUploaded / bytesTotal) * 100);
          setUploads((previous) =>
            previous.map((u) => (u.key === key ? { ...u, percent } : u)),
          );
        },
        onSuccess: () => {
          // tus reporting success only means the bytes are fully staged
          // -- apps/media_files/uploads.py's background finalize task does
          // the real work next, so this row stays visible (as
          // "finalizing") until `pollUntilSettled` below confirms it's
          // actually done, not just removed the instant the transfer ends.
          uploadControllers.current.delete(key);
          setUploads((previous) =>
            previous.map((u) =>
              u.key === key ? { ...u, percent: 100, stage: "finalizing" } : u,
            ),
          );
          resolve();
        },
      });
      uploadControllers.current.set(key, { upload, resolve });
      setUploads((previous) => [
        ...previous,
        { key, name: file.name, percent: 0, stage: "uploading" },
      ]);
      upload.start();
    });
  }

  function uploadFiles(fileList: FileList) {
    if (!connectionId) return;
    const files = Array.from(fileList);

    // Deliberately not awaited by the caller (the file-input's onChange):
    // the whole point is that kicking off an upload doesn't block the UI
    // -- progress renders from `uploads` state while this keeps running.
    void Promise.all(files.map((file) => startSingleUpload(file)))
      .then(() => refresh())
      .then(() => pollUntilSettled())
      .then(() => {
        // Sweep out any rows still marked "finalizing" once the batch has
        // settled -- see `startSingleUpload`'s onSuccess for why they
        // weren't removed the moment the transfer itself finished.
        setUploads((previous) => previous.filter((u) => u.stage !== "finalizing"));
      });
  }

  function pauseUpload(key: string) {
    const controller = uploadControllers.current.get(key);
    if (!controller) return;
    void controller.upload.abort();
    setUploads((previous) =>
      previous.map((u) => (u.key === key ? { ...u, stage: "paused" } : u)),
    );
  }

  function resumeUpload(key: string) {
    const controller = uploadControllers.current.get(key);
    if (!controller) return;
    void controller.upload.start();
    setUploads((previous) =>
      previous.map((u) => (u.key === key ? { ...u, stage: "uploading" } : u)),
    );
  }

  async function cancelUpload(key: string) {
    const controller = uploadControllers.current.get(key);
    if (!controller) return;
    uploadControllers.current.delete(key);
    setUploads((previous) => previous.filter((u) => u.key !== key));
    try {
      // `abort(true)` also tells the server to delete the staged bytes
      // (tus's Termination extension) -- a plain `abort()` (pause) would
      // leave them for the retention-window cleanup instead.
      await controller.upload.abort(true);
    } catch {
      // Best-effort server-side cleanup; local UI state is already
      // cleared, and the retention-window sweep (apps/media_files/uploads.py)
      // catches anything left behind regardless.
    }
    controller.resolve();
  }

  async function submitRename() {
    if (!renaming || !renameValue.trim()) return;
    setBusyUid(renaming.uid);
    try {
      await api<MediaFileItem>(`/files/${renaming.uid}`, {
        method: "PATCH",
        body: JSON.stringify({ name: renameValue.trim() }),
      });
      setRenaming(null);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not rename.");
    } finally {
      setBusyUid(null);
    }
  }

  async function toggleStar(item: MediaFileItem) {
    setBusyUid(item.uid);
    try {
      await api<MediaFileItem>(`/files/${item.uid}`, {
        method: "PATCH",
        body: JSON.stringify({ starred: !item.starred }),
      });
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update star.");
    } finally {
      setBusyUid(null);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setBusyUid(deleting.uid);
    try {
      await api(`/files/${deleting.uid}`, { method: "DELETE" });
      setDeleting(null);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not delete.");
    } finally {
      setBusyUid(null);
    }
  }

  // Normally unreachable -- `(dashboard)/layout.tsx` redirects to
  // `/onboarding` before this page ever mounts when there are zero
  // connections. Kept as a defensive fallback (e.g. the last connection
  // gets deleted while this page happens to already be mounted).
  if (connectionsLoaded && connections.length === 0) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-16 text-center">
        <div className="grid size-12 place-items-center rounded-2xl bg-muted">
          <Database size={20} />
        </div>
        <div>
          <h2 className="font-semibold">No storage connected yet</h2>
          <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
            Connect local storage, S3, Telegram, or another provider to start building
            your universal library.
          </p>
        </div>
        <Button render={<Link href="/settings" />}>Add storage</Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {connections.length > 1 && connectionId && (
            <Select onValueChange={(value) => switchConnection(value as string)} value={connectionId}>
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {connections.map((connection) => (
                  <SelectItem key={connection.uid} value={connection.uid}>
                    {connection.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Breadcrumb>
            <BreadcrumbList>
              {crumbs.map((crumb, index) => (
                <Fragment key={crumb.uid ?? "root"}>
                  {index > 0 && <BreadcrumbSeparator />}
                  <BreadcrumbItem>
                    {index === crumbs.length - 1 ? (
                      <BreadcrumbPage>{crumb.name}</BreadcrumbPage>
                    ) : (
                      <BreadcrumbLink
                        render={
                          <Link href={folderHref(crumb.uid, sort, order)} />
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
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <input
            hidden
            multiple
            onChange={(event) => {
              if (event.target.files?.length) void uploadFiles(event.target.files);
              event.target.value = "";
            }}
            ref={fileInputRef}
            type="file"
          />
          <Button
            onClick={() => fileInputRef.current?.click()}
            size="sm"
            variant="outline"
          >
            <Upload size={14} /> Upload
          </Button>
          <Button onClick={() => setNewFolderOpen(true)} size="sm" variant="outline">
            <FolderPlus size={14} /> New folder
          </Button>
          <Select
            items={SORT_OPTIONS}
            value={sortOption.value}
            onValueChange={(value) => {
              if (pendingSortClickRef.current === value) {
                pendingSortClickRef.current = null;
                return;
              }
              replaceSort(value);
            }}
          >
            <SelectTrigger className="w-52" aria-label="Sort files">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((option) => (
                <SelectItem
                  key={option.value}
                  onClick={() => {
                    pendingSortClickRef.current = option.value;
                    replaceSort(option.value);
                  }}
                  value={option.value}
                >
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {uploads.length > 0 && (
        <div className="space-y-3 rounded-xl border p-4">
          {uploads.map((upload) => (
            <div className="space-y-1.5" key={upload.key}>
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="truncate">{upload.name}</span>
                <div className="flex shrink-0 items-center gap-1">
                  <span className="text-muted-foreground">
                    {upload.stage === "finalizing"
                      ? "Finalizing…"
                      : upload.stage === "paused"
                        ? "Paused"
                        : `${upload.percent}%`}
                  </span>
                  {upload.stage === "uploading" && (
                    <Button
                      aria-label="Pause"
                      onClick={() => pauseUpload(upload.key)}
                      size="icon-xs"
                      variant="ghost"
                    >
                      <Pause size={12} />
                    </Button>
                  )}
                  {upload.stage === "paused" && (
                    <Button
                      aria-label="Resume"
                      onClick={() => resumeUpload(upload.key)}
                      size="icon-xs"
                      variant="ghost"
                    >
                      <Play size={12} />
                    </Button>
                  )}
                  {upload.stage !== "finalizing" && (
                    <Button
                      aria-label="Cancel"
                      onClick={() => void cancelUpload(upload.key)}
                      size="icon-xs"
                      variant="ghost"
                    >
                      <X size={12} />
                    </Button>
                  )}
                </div>
              </div>
              <Progress value={upload.stage === "finalizing" ? null : upload.percent} />
            </div>
          ))}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="w-48">Type</TableHead>
              <TableHead className="w-28">Size</TableHead>
              <TableHead className="w-32">Modified</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.length === 0 && !loading && (
              <TableRow>
                <TableCell
                  className="py-10 text-center text-sm text-muted-foreground"
                  colSpan={5}
                >
                  {query ? "No matching files." : "This folder is empty."}
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
                        href={folderHref(item.uid, sort, order)}
                        onClick={() =>
                          rememberRecent({
                            uid: item.uid,
                            name: item.name,
                            type: "folder",
                          })
                        }
                      >
                        <FolderOpen className="text-muted-foreground" size={16} />
                        {item.name}
                      </Link>
                    ) : (
                      <a
                        className="flex items-center gap-2 font-medium hover:underline"
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
                        <FileIcon className="text-muted-foreground" size={16} />
                        {item.name}
                      </a>
                    )}
                    <Button
                      aria-label={item.starred ? "Remove star" : "Add star"}
                      aria-pressed={item.starred}
                      className={
                        item.starred
                          ? "text-foreground"
                          : "text-muted-foreground/50 hover:text-muted-foreground"
                      }
                      disabled={busyUid === item.uid}
                      onClick={() => void toggleStar(item)}
                      size="icon-sm"
                      variant="ghost"
                    >
                      <Star
                        className={item.starred ? "fill-foreground" : undefined}
                        size={14}
                      />
                    </Button>
                    {item.status !== "completed" && (
                      <Badge variant={item.status === "failed" ? "destructive" : "secondary"}>
                        {item.status}
                      </Badge>
                    )}
                    {item.public_permission === "read" && (
                      <Badge variant="outline">
                        <Link2 size={11} /> Public
                      </Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">
                    {item.type === "folder"
                      ? "Folder"
                      : item.content_type || "application/octet-stream"}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {item.type === "folder" ? "—" : formatBytes(item.size)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {new Date(item.updated_at).toLocaleDateString()}
                </TableCell>
                <TableCell>
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          aria-label={`Actions for ${item.name}`}
                          size="icon-sm"
                          variant="ghost"
                        />
                      }
                    >
                      <MoreHorizontal size={15} />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onClick={() => {
                          setRenaming(item);
                          setRenameValue(item.name);
                        }}
                      >
                        <Pencil size={14} /> Rename
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setSharing(item)}>
                        <Share2 size={14} /> Share…
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-destructive"
                        onClick={() => setDeleting(item)}
                      >
                        <Trash2 size={14} /> Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
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

      <Dialog onOpenChange={setNewFolderOpen} open={newFolderOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New folder</DialogTitle>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="folder-name">Name</Label>
            <Input
              autoFocus
              id="folder-name"
              onChange={(event) => setNewFolderName(event.target.value)}
              value={newFolderName}
            />
          </div>
          <DialogFooter>
            <Button onClick={() => setNewFolderOpen(false)} variant="outline">
              Cancel
            </Button>
            <Button onClick={() => void createFolder()}>Create</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        onOpenChange={(open) => !open && setRenaming(null)}
        open={renaming !== null}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename</DialogTitle>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="rename-value">Name</Label>
            <Input
              autoFocus
              id="rename-value"
              onChange={(event) => setRenameValue(event.target.value)}
              value={renameValue}
            />
          </div>
          <DialogFooter>
            <Button onClick={() => setRenaming(null)} variant="outline">
              Cancel
            </Button>
            <Button disabled={busyUid === renaming?.uid} onClick={() => void submitRename()}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ShareDialog
        item={sharing}
        onItemUpdated={(updated) => {
          // Keep the open dialog's own view fresh, then re-sync the list
          // row (badge/permissions) behind it.
          setSharing(updated);
          void refresh();
        }}
        onOpenChange={(open) => {
          if (!open) setSharing(null);
        }}
      />

      <AlertDialog
        onOpenChange={(open) => !open && setDeleting(null)}
        open={deleting !== null}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleting?.type === "folder"
                ? "This deletes the folder and everything inside it. It's a soft delete -- recoverable until it's permanently purged."
                : "It's a soft delete -- recoverable until it's permanently purged."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDelete()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
