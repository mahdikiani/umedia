"use client";

import {
  Copy,
  Download,
  FolderInput,
  Inbox,
  MoreHorizontal,
  Pencil,
  Share2,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { fileDownloadUrl, type MediaFileItem } from "@/lib/api";

function downloadFile(item: MediaFileItem) {
  const link = document.createElement("a");
  link.href = fileDownloadUrl(item.uid, item.name);
  link.download = item.name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

type FileItemMenuProps = {
  item: MediaFileItem;
  onRename: (item: MediaFileItem) => void;
  onMove: (item: MediaFileItem) => void;
  onCopy: (item: MediaFileItem) => void;
  onAddToTemporary: (item: MediaFileItem) => void;
  onShare: (item: MediaFileItem) => void;
  onDelete: (item: MediaFileItem) => void;
};

export function FileItemMenu({
  item,
  onRename,
  onMove,
  onCopy,
  onAddToTemporary,
  onShare,
  onDelete,
}: FileItemMenuProps) {
  return (
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
        <MoreHorizontal />
      </DropdownMenuTrigger>
      {/* w-auto: default popup uses anchor width (tiny icon button) and clips labels */}
      <DropdownMenuContent align="end" className="min-w-44 w-auto">
        <DropdownMenuItem
          onClick={() => {
            onRename(item);
          }}
        >
          <Pencil /> Rename
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onMove(item)}>
          <FolderInput /> Move to…
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onCopy(item)}>
          <Copy /> Copy to…
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onAddToTemporary(item)}>
          <Inbox /> Add to Temporary
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onShare(item)}>
          <Share2 /> Share…
        </DropdownMenuItem>
        {item.type === "file" ? (
          <DropdownMenuItem onClick={() => downloadFile(item)}>
            <Download /> Download
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-destructive"
          onClick={() => onDelete(item)}
        >
          <Trash2 /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
