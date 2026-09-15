/* eslint-disable @next/next/no-img-element */
"use client";

import { useState } from "react";

import { FileTypeIcon } from "@/components/file-type-icon";
import type { MediaFileItem } from "@/lib/api";
import { filePreviewKind, filePreviewSrc } from "@/lib/file-type";
import { cn } from "@/lib/utils";

type FileThumbnailProps = {
  item: MediaFileItem;
  className?: string;
  decorative?: boolean;
  compact?: boolean;
};

export function FileThumbnail({
  item,
  className,
  decorative = false,
  compact = false,
}: FileThumbnailProps) {
  const [failed, setFailed] = useState(false);
  const kind = failed ? "icon" : filePreviewKind(item);
  const src = kind === "icon" ? null : filePreviewSrc(item);
  const label = decorative ? "" : item.name;

  if (kind === "video" && src) {
    return (
      <video
        aria-label={label || undefined}
        className={cn("size-full object-cover", className)}
        muted
        onError={() => setFailed(true)}
        playsInline
        preload="metadata"
        src={src}
      />
    );
  }

  if (kind === "image" && src) {
    return (
      <img
        alt={label}
        className={cn("size-full object-cover", className)}
        onError={() => setFailed(true)}
        src={src}
      />
    );
  }

  return (
    <FileTypeIcon
      className={cn(compact ? "size-5" : "size-12", className)}
      decorative={decorative}
      item={item}
    />
  );
}
