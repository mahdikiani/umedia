/* eslint-disable @next/next/no-img-element */

import type { FileTypeSource } from "@/lib/file-type";
import { fileIconUrl } from "@/lib/file-type";
import { cn } from "@/lib/utils";

type FileTypeIconProps = {
  item: FileTypeSource;
  className?: string;
  decorative?: boolean;
};

export function FileTypeIcon({
  item,
  className,
  decorative = true,
}: FileTypeIconProps) {
  return (
    <img
      alt={decorative ? "" : item.name}
      className={cn("object-contain", className)}
      src={fileIconUrl(item)}
    />
  );
}
