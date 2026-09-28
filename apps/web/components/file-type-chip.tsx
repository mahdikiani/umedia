"use client";

import { Badge } from "@/components/ui/badge";
import { FileTypeIcon } from "@/components/file-type-icon";
import type { FileTypeSource } from "@/lib/file-type";
import { mimeChipLabel, resolvedMime, typeFilterKey } from "@/lib/file-type";
import { cn } from "@/lib/utils";

type FileTypeChipProps = {
  item: FileTypeSource;
  active?: boolean;
  onToggle?: (key: string) => void;
  className?: string;
};

export function FileTypeChip({
  item,
  active = false,
  onToggle,
  className,
}: FileTypeChipProps) {
  const label = mimeChipLabel(item);
  const key = typeFilterKey(item);
  const title = item.type === "folder" ? "Folder" : resolvedMime(item);

  return (
    <Badge
      aria-pressed={onToggle ? active : undefined}
      className={cn(
        "min-w-0 max-w-full shrink",
        onToggle ? "cursor-pointer" : "pointer-events-none",
        className,
      )}
      onClick={
        onToggle
          ? (event) => {
              event.preventDefault();
              event.stopPropagation();
              onToggle(key);
            }
          : undefined
      }
      render={onToggle ? <button type="button" /> : undefined}
      title={title}
      variant={active ? "default" : "secondary"}
    >
      <FileTypeIcon className="size-3.5" item={item} />
      <span className="min-w-0 truncate">{label}</span>
    </Badge>
  );
}
