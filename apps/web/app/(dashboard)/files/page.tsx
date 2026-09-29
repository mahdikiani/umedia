"use client";

import {
  Columns2,
  Database,
  FolderPlus,
  LayoutGrid,
  List,
  Pause,
  Play,
  Upload,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import * as tus from "tus-js-client";

import { FileBrowserPane } from "@/components/file-browser-pane";
import { TemporaryDock } from "@/components/temporary-dock";
import { TransferDestinationDialog } from "@/components/transfer-destination-dialog";
import { TransferProgressPanel } from "@/components/transfer-progress-panel";
import { ShareDialog } from "@/components/share-dialog";
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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useTransferTracker } from "@/hooks/use-transfer-tracker";
import {
  addToTemporary as addTemporaryPointers,
  api,
  apiForm,
  cancelTransfer,
  createTransfer,
  LIST_PAGE_SIZE,
  type MediaFileItem,
  type Page,
  type ProviderConnection,
  type TransferJob,
} from "@/lib/api";
import {
  GROUP_OPTIONS,
  parseFileSort,
  parseFilesView,
  parseGroupBy,
  type FileSort,
  type FilesView,
  type GroupBy,
  type SortOrder,
} from "@/lib/files-view";
import {
  dataTransferHasFiles,
  filesFromDataTransfer,
  isEditableTarget,
} from "@/lib/incoming-files";
import { dataTransferHasLibraryIds } from "@/lib/umedia-dnd";

const DUAL_PANE_KEY = "umedia.files.dualPane";
const SECONDARY_FOLDER_KEY = "umedia.files.secondaryFolder";

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
  groupBy: GroupBy = "none",
  view: FilesView = "list",
): string {
  const params = new URLSearchParams();
  if (uid) params.set("folder", uid);
  params.set("sort", sort);
  params.set("order", order);
  if (groupBy !== "none") params.set("group", groupBy);
  if (view !== "list") params.set("view", view);
  return `/files?${params.toString()}`;
}

const FINALIZE_POLL_ATTEMPTS = 20;
const FINALIZE_POLL_INTERVAL_MS = 1500;

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
  const searchKey = searchParams.toString();
  const currentParentId = searchParams.get("folder");
  const query = (searchParams.get("q") ?? "").trim();
  const urlSort = parseFileSort(
    searchParams.get("sort"),
    searchParams.get("order"),
  );
  const urlGroupBy = parseGroupBy(searchParams.get("group"));
  const urlView = parseFilesView(searchParams.get("view"));
  const urlTypeFilter = searchParams.get("type");
  const [sort, setSort] = useState<FileSort>(urlSort.sort);
  const [order, setOrder] = useState<SortOrder>(urlSort.order);
  const [groupBy, setGroupBy] = useState<GroupBy>(urlGroupBy);
  const [filesView, setFilesView] = useState<FilesView>(urlView);
  const [typeFilter, setTypeFilter] = useState<string | null>(urlTypeFilter);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSort(urlSort.sort);
    setOrder(urlSort.order);
    setGroupBy(urlGroupBy);
    setFilesView(urlView);
    setTypeFilter(urlTypeFilter);
  }, [
    searchKey,
    urlSort.order,
    urlSort.sort,
    urlGroupBy,
    urlView,
    urlTypeFilter,
  ]);

  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [connectionsLoaded, setConnectionsLoaded] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [renaming, setRenaming] = useState<MediaFileItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [sharing, setSharing] = useState<MediaFileItem | null>(null);
  const [deleting, setDeleting] = useState<MediaFileItem | null>(null);
  const [busyUid, setBusyUid] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadInFlight[]>([]);
  const [dropActive, setDropActive] = useState(false);
  const [dualPane, setDualPane] = useState(false);
  const [secondaryParentId, setSecondaryParentId] = useState<string | null>(
    null,
  );
  const [refreshToken, setRefreshToken] = useState(0);
  const [destDialog, setDestDialog] = useState<{
    operation: "move" | "copy";
    sourceIds: string[];
    sourceParentIds: (string | null)[];
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const uploadControllers = useRef<
    Map<string, { upload: tus.Upload; resolve: () => void }>
  >(new Map());

  const bumpRefresh = useCallback(() => {
    setRefreshToken((value) => value + 1);
  }, []);

  const {
    jobs: transferJobs,
    track: trackTransfer,
    dismiss: dismissTransfer,
  } = useTransferTracker(bumpRefresh);

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDualPane(localStorage.getItem(DUAL_PANE_KEY) === "1");
      const saved = localStorage.getItem(SECONDARY_FOLDER_KEY);
      setSecondaryParentId(saved && saved.length > 0 ? saved : null);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    api<ProviderConnection[]>("/providers")
      .then((list) => setConnections(list))
      .finally(() => setConnectionsLoaded(true));
  }, []);

  // Secondary pane remembers a folder in localStorage. After a DB wipe (or
  // delete) that uid is gone -- list_children returns an empty page with
  // no error, so the right column looks blank while the left still has
  // files. Validate and fall back to library root.
  useEffect(() => {
    if (!secondaryParentId) return;
    let cancelled = false;
    void api<MediaFileItem>(`/files/${secondaryParentId}`)
      .then((item) => {
        if (cancelled) return;
        if (item.type !== "folder") {
          setSecondaryParentId(null);
          try {
            localStorage.removeItem(SECONDARY_FOLDER_KEY);
          } catch {
            // ignore
          }
        }
      })
      .catch(() => {
        if (cancelled) return;
        setSecondaryParentId(null);
        try {
          localStorage.removeItem(SECONDARY_FOLDER_KEY);
        } catch {
          // ignore
        }
      });
    return () => {
      cancelled = true;
    };
  }, [secondaryParentId]);

  function setDualPanePersisted(next: boolean) {
    setDualPane(next);
    try {
      localStorage.setItem(DUAL_PANE_KEY, next ? "1" : "0");
    } catch {
      // ignore
    }
    // First open: mirror the left pane so both columns start with the
    // same listing instead of an empty / stale right side.
    if (next) {
      setSecondaryParentId((current) => {
        if (current !== null) return current;
        const mirror = currentParentId;
        try {
          if (mirror) localStorage.setItem(SECONDARY_FOLDER_KEY, mirror);
          else localStorage.removeItem(SECONDARY_FOLDER_KEY);
        } catch {
          // ignore
        }
        return mirror;
      });
    }
  }

  function setSecondaryParentPersisted(uid: string | null) {
    setSecondaryParentId(uid);
    try {
      if (uid) localStorage.setItem(SECONDARY_FOLDER_KEY, uid);
      else localStorage.removeItem(SECONDARY_FOLDER_KEY);
    } catch {
      // ignore
    }
  }

  async function refreshPrimaryListing() {
    await api<Page<MediaFileItem>>(
      filesListPath(currentParentId, query, sort, order),
    );
    bumpRefresh();
  }

  function replaceQuery(updates: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value === null) params.delete(key);
      else params.set(key, value);
    }
    router.replace(`/files?${params.toString()}`);
  }

  function replaceGroup(value: string | null) {
    const next = parseGroupBy(value);
    setGroupBy(next);
    replaceQuery({ group: next === "none" ? null : next });
  }

  function replaceView(next: FilesView) {
    setFilesView(next);
    replaceQuery({ view: next === "list" ? null : next });
  }

  function toggleTypeFilter(key: string) {
    const next = typeFilter === key ? null : key;
    setTypeFilter(next);
    replaceQuery({ type: next });
  }

  function onSortChange(nextSort: FileSort, nextOrder: SortOrder) {
    setSort(nextSort);
    setOrder(nextOrder);
    replaceQuery({ sort: nextSort, order: nextOrder });
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
      bumpRefresh();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not create folder.",
      );
    }
  }

  async function pollUntilSettled() {
    for (let attempt = 0; attempt < FINALIZE_POLL_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) =>
        setTimeout(resolve, FINALIZE_POLL_INTERVAL_MS),
      );
      bumpRefresh();
      const stillProcessing = await api<Page<MediaFileItem>>(
        filesListPath(currentParentId, query, sort, order),
      ).then((page) => page.items.some((item) => item.status === "processing"));
      if (!stillProcessing) return;
    }
  }

  function startSingleUpload(file: File): Promise<void> {
    const key = `${file.name}-${file.size}-${Date.now()}`;
    return new Promise<void>((resolve) => {
      const upload = new tus.Upload(file, {
        endpoint: "/api/v1/uploads/",
        chunkSize: 8 * 1024 * 1024,
        retryDelays: [0, 1000, 3000, 5000],
        metadata: {
          name: file.name,
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

  function uploadFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    void Promise.all(files.map((file) => startSingleUpload(file)))
      .then(() => refreshPrimaryListing())
      .then(() => pollUntilSettled())
      .then(() => {
        setUploads((previous) =>
          previous.filter((u) => u.stage !== "finalizing"),
        );
      });
  }

  const uploadFilesRef = useRef(uploadFiles);
  useEffect(() => {
    uploadFilesRef.current = uploadFiles;
  });

  useEffect(() => {
    function onDragEnter(event: DragEvent) {
      if (dataTransferHasLibraryIds(event.dataTransfer)) return;
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      dragDepth.current += 1;
      setDropActive(true);
    }
    function onDragOver(event: DragEvent) {
      if (dataTransferHasLibraryIds(event.dataTransfer)) return;
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    }
    function onDragLeave(event: DragEvent) {
      if (dataTransferHasLibraryIds(event.dataTransfer)) return;
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDropActive(false);
    }
    function onDrop(event: DragEvent) {
      if (dataTransferHasLibraryIds(event.dataTransfer)) {
        dragDepth.current = 0;
        setDropActive(false);
        return;
      }
      const files = filesFromDataTransfer(event.dataTransfer);
      dragDepth.current = 0;
      setDropActive(false);
      if (files.length === 0) return;
      event.preventDefault();
      uploadFilesRef.current(files);
    }
    function onPaste(event: ClipboardEvent) {
      if (isEditableTarget(event.target)) return;
      const files = filesFromDataTransfer(event.clipboardData);
      if (files.length === 0) return;
      event.preventDefault();
      uploadFilesRef.current(files);
    }

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("paste", onPaste);
    };
  }, []);

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
      await controller.upload.abort(true);
    } catch {
      // Best-effort
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
      bumpRefresh();
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
      bumpRefresh();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not update star.",
      );
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
      bumpRefresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not delete.");
    } finally {
      setBusyUid(null);
    }
  }

  function openRename(item: MediaFileItem) {
    setRenaming(item);
    setRenameValue(item.name);
  }

  function onTransferCreated(job: TransferJob) {
    trackTransfer(job);
  }

  async function onTransferCancel(uid: string) {
    try {
      trackTransfer(await cancelTransfer(uid));
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not cancel transfer.",
      );
    }
  }

  async function transferToOtherSide(
    item: MediaFileItem,
    operation: "move" | "copy",
    destinationId: string | null,
  ) {
    try {
      const job = await createTransfer({
        operation,
        source_ids: [item.uid],
        dest_parent_id: destinationId,
      });
      onTransferCreated(job);
      toast.success(operation === "move" ? "Moving to other side…" : "Copying to other side…");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start transfer.");
    }
  }

  const addToTemporary = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      try {
        await addTemporaryPointers(ids);
        toast.success("Added to Temporary");
        bumpRefresh();
      } catch (error) {
        toast.error(
          error instanceof Error
            ? error.message
            : "Could not add to Temporary.",
        );
      }
    },
    [bumpRefresh],
  );

  function primaryHref(uid: string | null) {
    return folderHref(uid, sort, order, groupBy, filesView);
  }

  const paneShared = {
    sort,
    order,
    groupBy,
    filesView,
    typeFilter,
    onSortChange,
    onTypeFilterToggle: toggleTypeFilter,
    connections,
    busyUid,
    onRename: openRename,
    onMove: (item: MediaFileItem) =>
      setDestDialog({
        operation: "move",
        sourceIds: [item.uid],
        sourceParentIds: [item.parent_id],
      }),
    onCopy: (item: MediaFileItem) =>
      setDestDialog({
        operation: "copy",
        sourceIds: [item.uid],
        sourceParentIds: [item.parent_id],
      }),
    onAddToTemporary: (item: MediaFileItem) => {
      void addToTemporary([item.uid]);
    },
    onShare: setSharing,
    onDelete: setDeleting,
    onToggleStar: toggleStar,
    onTransferCreated,
    onTemporaryPointersChanged: bumpRefresh,
    refreshToken,
  };

  if (connectionsLoaded && connections.length === 0) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-16 text-center">
        <div className="grid size-12 place-items-center rounded-2xl bg-muted">
          <Database size={20} />
        </div>
        <div>
          <h2 className="font-semibold">No storage connected yet</h2>
          <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
            Connect local storage, S3, Telegram, or another provider to start
            building your universal library.
          </p>
        </div>
        <Button render={<Link href="/settings/storage" />}>Add storage</Button>
      </div>
    );
  }

  return (
    <div className="relative flex flex-col gap-4 pb-24">
      {dropActive ? (
        <div
          aria-live="polite"
          className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-background/80"
        >
          <p className="rounded-xl border border-dashed bg-card px-6 py-4 text-sm font-medium">
            Drop files to upload
          </p>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <input
          hidden
          multiple
          onChange={(event) => {
            if (event.target.files?.length)
              void uploadFiles(event.target.files);
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
          <Upload data-icon="inline-start" />
          Upload
        </Button>
        <Button
          onClick={() => setNewFolderOpen(true)}
          size="sm"
          variant="outline"
        >
          <FolderPlus data-icon="inline-start" />
          New folder
        </Button>
        <Button
          aria-pressed={dualPane}
          onClick={() => setDualPanePersisted(!dualPane)}
          size="sm"
          title="Show two folders side by side for drag-and-drop"
          variant={dualPane ? "default" : "outline"}
        >
          <Columns2 data-icon="inline-start" />
          Two columns
        </Button>
        <Select
          items={[...GROUP_OPTIONS]}
          value={groupBy}
          onValueChange={(value) => replaceGroup(value)}
        >
          <SelectTrigger aria-label="Group files" className="w-44" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GROUP_OPTIONS.map((option) => (
              <SelectItem
                key={option.value}
                label={option.label}
                onClick={() => replaceGroup(option.value)}
                value={option.value}
              >
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <ToggleGroup
          onValueChange={(value) => {
            const next = value[0];
            if (next === "list" || next === "cards") replaceView(next);
          }}
          size="sm"
          spacing={0}
          value={[filesView]}
          variant="outline"
        >
          <ToggleGroupItem aria-label="List view" value="list">
            <List data-icon="inline-start" />
            List
          </ToggleGroupItem>
          <ToggleGroupItem aria-label="Card view" value="cards">
            <LayoutGrid data-icon="inline-start" />
            Cards
          </ToggleGroupItem>
        </ToggleGroup>
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
              <Progress
                value={upload.stage === "finalizing" ? null : upload.percent}
              />
            </div>
          ))}
        </div>
      )}

      <TransferProgressPanel
        jobs={transferJobs}
        onCancel={(uid) => void onTransferCancel(uid)}
        onDismiss={dismissTransfer}
      />

      <div
        className={
          dualPane ? "grid gap-4 lg:grid-cols-2" : "flex flex-col gap-4"
        }
      >
        <FileBrowserPane
          {...paneShared}
          folderHref={primaryHref}
          linkNavigation
          onNavigate={(uid) => router.push(primaryHref(uid))}
          paneId="primary"
          parentId={currentParentId}
          otherSideParentId={dualPane ? secondaryParentId : undefined}
          onTransferToOtherSide={dualPane ? transferToOtherSide : undefined}
          query={query}
          showBreadcrumb
        />
        {dualPane ? (
          <FileBrowserPane
            {...paneShared}
            linkNavigation={false}
            onNavigate={setSecondaryParentPersisted}
            paneId="secondary"
            parentId={secondaryParentId}
            otherSideParentId={currentParentId}
            onTransferToOtherSide={transferToOtherSide}
            query=""
            showBreadcrumb
          />
        ) : null}
      </div>

      <TemporaryDock
        addToTemporary={addToTemporary}
        currentParentId={currentParentId}
        onTransferCreated={onTransferCreated}
        refreshToken={refreshToken}
      />

      <TransferDestinationDialog
        onCreated={onTransferCreated}
        onOpenChange={(open) => {
          if (!open) setDestDialog(null);
        }}
        open={destDialog !== null}
        operation={destDialog?.operation ?? "move"}
        sourceIds={destDialog?.sourceIds ?? []}
        sourceParentIds={destDialog?.sourceParentIds ?? []}
      />

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
            <Button
              disabled={busyUid === renaming?.uid}
              onClick={() => void submitRename()}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ShareDialog
        item={sharing}
        onItemUpdated={(updated) => {
          setSharing(updated);
          bumpRefresh();
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
