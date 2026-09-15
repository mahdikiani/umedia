import type { MediaFileItem } from "@/lib/api";
import { mimeChipLabel } from "@/lib/file-type";

export type FileSort = "name" | "updated_at" | "type" | "size";
export type SortOrder = "asc" | "desc";
export type GroupBy = "none" | "type" | "modified";
export type FilesView = "list" | "cards";

export type SortableColumn = {
  key: FileSort;
  label: string;
  defaultOrder: SortOrder;
};

export const SORTABLE_COLUMNS: readonly SortableColumn[] = [
  { key: "name", label: "Name", defaultOrder: "asc" },
  { key: "type", label: "Type", defaultOrder: "asc" },
  { key: "size", label: "Size", defaultOrder: "desc" },
  { key: "updated_at", label: "Modified", defaultOrder: "desc" },
];

export const GROUP_OPTIONS = [
  { value: "none", label: "No grouping" },
  { value: "type", label: "Type" },
  { value: "modified", label: "Date modified" },
] as const;

const SORT_KEYS = new Set<FileSort>(
  SORTABLE_COLUMNS.map((column) => column.key),
);

export function parseFileSort(
  sortParam: string | null,
  orderParam: string | null,
): { sort: FileSort; order: SortOrder } {
  const sort = SORT_KEYS.has(sortParam as FileSort)
    ? (sortParam as FileSort)
    : "name";
  const column = SORTABLE_COLUMNS.find((item) => item.key === sort)!;
  const order =
    orderParam === "asc" || orderParam === "desc"
      ? orderParam
      : column.defaultOrder;
  return { sort, order };
}

export function parseGroupBy(value: string | null): GroupBy {
  if (value === "type" || value === "modified") return value;
  return "none";
}

export function parseFilesView(value: string | null): FilesView {
  return value === "cards" ? "cards" : "list";
}

export function nextSortClick(
  currentSort: FileSort,
  currentOrder: SortOrder,
  column: FileSort,
): { sort: FileSort; order: SortOrder } {
  if (currentSort === column) {
    return { sort: column, order: currentOrder === "asc" ? "desc" : "asc" };
  }
  const next = SORTABLE_COLUMNS.find((item) => item.key === column)!;
  return { sort: column, order: next.defaultOrder };
}

export type FileGroup = {
  id: string;
  label: string;
  items: MediaFileItem[];
};

export function typeGroupLabel(item: MediaFileItem): string {
  if (item.type === "folder") return "Folders";
  return mimeChipLabel(item);
}

export function modifiedGroupLabel(
  iso: string,
  now: Date = new Date(),
): string {
  const date = new Date(iso);
  const startOfDay = (value: Date) =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const day = startOfDay(date);
  const today = startOfDay(now);
  const dayMs = 86_400_000;
  if (day === today) return "Today";
  if (day === today - dayMs) return "Yesterday";
  if (day > today - 7 * dayMs) return "Previous 7 days";
  if (
    date.getMonth() === now.getMonth()
    && date.getFullYear() === now.getFullYear()
  ) {
    return "This month";
  }
  return "Older";
}

export function groupFiles(
  items: MediaFileItem[],
  groupBy: GroupBy,
  now: Date = new Date(),
): FileGroup[] {
  if (groupBy === "none") {
    return [{ id: "all", label: "", items }];
  }
  const groups: FileGroup[] = [];
  const index = new Map<string, FileGroup>();
  for (const item of items) {
    const label =
      groupBy === "type"
        ? typeGroupLabel(item)
        : modifiedGroupLabel(item.updated_at, now);
    let group = index.get(label);
    if (!group) {
      group = { id: label, label, items: [] };
      index.set(label, group);
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}
