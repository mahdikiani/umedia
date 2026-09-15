"use client";

import { Fragment, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  api,
  createTransfer,
  LIST_PAGE_SIZE,
  type MediaFileItem,
  type Page,
  type TransferJob,
} from "@/lib/api";

type Crumb = { uid: string | null; name: string };

type TransferDestinationDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  operation: "move" | "copy";
  sourceIds: string[];
  onCreated: (job: TransferJob) => void;
};

function foldersPath(parentId: string | null): string {
  const params = new URLSearchParams();
  if (parentId) params.set("parent_id", parentId);
  params.set("sort", "name");
  params.set("order", "asc");
  params.set("limit", String(LIST_PAGE_SIZE));
  params.set("offset", "0");
  return `/files?${params.toString()}`;
}

export function TransferDestinationDialog({
  open,
  onOpenChange,
  operation,
  sourceIds,
  onCreated,
}: TransferDestinationDialogProps) {
  const [parentId, setParentId] = useState<string | null>(null);
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ uid: null, name: "Files" }]);
  const [folders, setFolders] = useState<MediaFileItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setParentId(null);
    setCrumbs([{ uid: null, name: "Files" }]);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void api<Page<MediaFileItem>>(foldersPath(parentId))
      .then((page) => {
        if (cancelled) return;
        setFolders(page.items.filter((item) => item.type === "folder"));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(
          error instanceof Error ? error.message : "Could not load folders.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, parentId]);

  useEffect(() => {
    if (!open) return;
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
          chain.unshift({ uid: item.uid, name: item.name });
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
  }, [open, parentId]);

  function navigateTo(uid: string | null) {
    setParentId(uid);
  }

  async function confirm() {
    if (sourceIds.length === 0) return;
    setSubmitting(true);
    try {
      const job = await createTransfer({
        operation,
        source_ids: sourceIds,
        dest_parent_id: parentId,
      });
      onCreated(job);
      onOpenChange(false);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not start transfer.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const title = operation === "move" ? "Move to…" : "Copy to…";
  const confirmLabel = operation === "move" ? "Move here" : "Copy here";

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
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
                        <button
                          onClick={() => navigateTo(crumb.uid)}
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
        <div className="max-h-64 overflow-y-auto rounded-lg border">
          {loading ? (
            <p className="p-4 text-sm text-muted-foreground">Loading…</p>
          ) : folders.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              No subfolders here.
            </p>
          ) : (
            <ul className="divide-y">
              {folders.map((folder) => (
                <li key={folder.uid}>
                  <button
                    className="flex w-full px-3 py-2 text-start text-sm hover:bg-muted"
                    onClick={() => navigateTo(folder.uid)}
                    type="button"
                  >
                    {folder.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            Cancel
          </Button>
          <Button disabled={submitting} onClick={() => void confirm()}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
