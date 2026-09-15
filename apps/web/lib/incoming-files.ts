type TransferItem = {
  kind: string;
  type: string;
  getAsFile: () => File | null;
  webkitGetAsEntry?: () => { isDirectory?: boolean; isFile?: boolean } | null;
};

type IncomingTransfer = {
  files?: FileList | File[] | null;
  items?: TransferItem[] | DataTransferItemList | null;
  types?: readonly string[];
};

const MIME_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
  "image/svg+xml": "svg",
};

export function dataTransferHasFiles(
  data: Pick<IncomingTransfer, "types"> | null | undefined,
): boolean {
  if (!data?.types) return false;
  return Array.from(data.types).includes("Files");
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return Boolean(
    target.closest("input, textarea, select, [contenteditable=true]"),
  );
}

export function ensureUploadFileName(file: File): File {
  if (file.name && file.name !== "blob") return file;
  const ext = MIME_EXT[file.type] || "bin";
  return new File([file], `pasted.${ext}`, {
    type: file.type || "application/octet-stream",
  });
}

function entryOf(
  item: TransferItem,
): { isDirectory?: boolean; isFile?: boolean } | null {
  return item.webkitGetAsEntry?.() ?? null;
}

export function filesFromDataTransfer(
  data: IncomingTransfer | DataTransfer | null | undefined,
): File[] {
  if (!data) return [];
  const items = data.items ? Array.from(data.items as TransferItem[]) : [];
  if (items.length > 0) {
    const files: File[] = [];
    for (const item of items) {
      if (item.kind !== "file") continue;
      const entry = entryOf(item);
      if (entry?.isDirectory) continue;
      const file = item.getAsFile();
      if (file) files.push(ensureUploadFileName(file));
    }
    if (files.length > 0) return files;
  }
  return Array.from(data.files ?? []).map(ensureUploadFileName);
}
