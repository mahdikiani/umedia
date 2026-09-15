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
};

export function FileTypeChip({ item, active = false, onToggle }: FileTypeChipProps) {
  const label = mimeChipLabel(item);
  const key = typeFilterKey(item);
  const title = item.type === "folder" ? "Folder" : resolvedMime(item);

  return (
    <Badge
      aria-pressed={onToggle ? active : undefined}
      className={cn(onToggle ? "cursor-pointer" : "pointer-events-none")}
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
      {label}
    </Badge>
  );
}
