/** Custom HTML5 drag payload for library MediaFile moves/copies.
 * OS file uploads keep using the standard `Files` type; this MIME is
 * library-only and must not be confused with upload drops. */
export const UMEDIA_FILE_IDS_MIME = "application/x-umedia-file-ids";

/** Marker MIME so dragover can detect Temporary-origin drags when
 * `getData` is empty (Chrome/Safari hide custom payloads until drop). */
export const UMEDIA_FROM_TEMPORARY_MIME =
  "application/x-umedia-from-temporary";

export type LibraryDragPayload = {
  sourceIds: string[];
  fromTemporary?: boolean;
};

export function setLibraryDragPayload(
  dataTransfer: DataTransfer,
  sourceIds: string[],
  options?: { fromTemporary?: boolean },
): void {
  const payload: LibraryDragPayload = {
    sourceIds,
    ...(options?.fromTemporary ? { fromTemporary: true } : {}),
  };
  dataTransfer.setData(UMEDIA_FILE_IDS_MIME, JSON.stringify(payload));
  if (options?.fromTemporary) {
    dataTransfer.setData(UMEDIA_FROM_TEMPORARY_MIME, "1");
  }
  dataTransfer.effectAllowed = "copyMove";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((id) => typeof id === "string");
}

export function parseLibraryDragPayload(
  dataTransfer: DataTransfer | null | undefined,
): LibraryDragPayload | null {
  if (!dataTransfer) return null;
  const raw = dataTransfer.getData(UMEDIA_FILE_IDS_MIME);
  if (!raw) {
    // During dragover/enter some browsers hide custom types via getData;
    // types still lists them.
    if (!Array.from(dataTransfer.types).includes(UMEDIA_FILE_IDS_MIME)) {
      return null;
    }
    return {
      sourceIds: [],
      ...(dataTransferFromTemporary(dataTransfer)
        ? { fromTemporary: true }
        : {}),
    };
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    // Legacy: bare string[] payload.
    if (isStringArray(parsed)) {
      return parsed.length > 0 ? { sourceIds: parsed } : null;
    }
    if (
      parsed &&
      typeof parsed === "object" &&
      isStringArray((parsed as LibraryDragPayload).sourceIds)
    ) {
      const body = parsed as LibraryDragPayload;
      if (body.sourceIds.length === 0) return null;
      return {
        sourceIds: body.sourceIds,
        ...(body.fromTemporary ? { fromTemporary: true } : {}),
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function dataTransferHasLibraryIds(
  data: DataTransfer | null | undefined,
): boolean {
  if (!data?.types) return false;
  return Array.from(data.types).includes(UMEDIA_FILE_IDS_MIME);
}

export function dataTransferFromTemporary(
  data: DataTransfer | null | undefined,
): boolean {
  if (!data?.types) return false;
  return Array.from(data.types).includes(UMEDIA_FROM_TEMPORARY_MIME);
}

/**
 * Drag from Temporary → always move.
 * Otherwise Alt/Option held → copy; default → move.
 */
export function transferOperationFromEvent(
  event: Pick<DragEvent, "altKey"> & {
    dataTransfer?: DataTransfer | null;
  },
  payload?: LibraryDragPayload | null,
): "move" | "copy" {
  if (payload?.fromTemporary) return "move";
  if (dataTransferFromTemporary(event.dataTransfer)) return "move";
  return event.altKey ? "copy" : "move";
}
