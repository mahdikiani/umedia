import { Database } from "lucide-react";
import type { JSX } from "react";

import { cn } from "@/lib/utils";

/** Normalize aliases so UI stays consistent across callers. */
function normalizeProviderType(providerType: string): string {
  const key = providerType.trim().toLowerCase();
  if (key === "gdrive") return "google_drive";
  return key;
}

const PROVIDER_STYLE: Record<string, { bg: string; fg: string }> = {
  local: {
    bg: "var(--storage-local-bg)",
    fg: "var(--storage-local-fg)",
  },
  s3: {
    bg: "var(--storage-s3-bg)",
    fg: "var(--storage-s3-fg)",
  },
  google_drive: {
    bg: "var(--storage-google-drive-bg)",
    fg: "var(--storage-google-drive-fg)",
  },
  telegram: {
    bg: "var(--storage-telegram-bg)",
    fg: "var(--storage-telegram-fg)",
  },
  nextcloud: {
    bg: "var(--storage-nextcloud-bg)",
    fg: "var(--storage-nextcloud-fg)",
  },
};

const FALLBACK_STYLE = {
  bg: "var(--storage-unknown-bg)",
  fg: "var(--storage-unknown-fg)",
};

const SIZE: Record<"sm" | "md", { tile: string; iconClass: string }> = {
  sm: { tile: "size-5 rounded-md", iconClass: "size-3" },
  md: { tile: "size-9 rounded-lg", iconClass: "size-4" },
};

export function connectionProviderType(
  connectionId: string | null | undefined,
  connections: { uid: string; provider_type: string }[],
): string | null {
  if (!connectionId) return null;
  return (
    connections.find((item) => item.uid === connectionId)?.provider_type ?? null
  );
}

export function connectionMeta(
  connectionId: string | null | undefined,
  connections: { uid: string; name: string; provider_type: string }[],
): { name: string; providerType: string } | null {
  if (!connectionId) return null;
  const match = connections.find((item) => item.uid === connectionId);
  if (!match) return null;
  return { name: match.name, providerType: match.provider_type };
}

export function StorageProviderIcon({
  providerType,
  className,
  title,
  size = "sm",
}: {
  providerType: string;
  className?: string;
  title?: string;
  size?: "sm" | "md";
}): JSX.Element {
  const normalized = normalizeProviderType(providerType);
  const colors = PROVIDER_STYLE[normalized] ?? FALLBACK_STYLE;
  const dims = SIZE[size];

  return (
    <span
      aria-hidden={title ? undefined : true}
      className={cn(
        "grid shrink-0 place-items-center",
        dims.tile,
        className,
      )}
      data-provider-type={normalized}
      // Inline vars beat parent Select/menu focus text overrides.
      style={{ backgroundColor: colors.bg, color: colors.fg }}
      title={title}
    >
      <Database className={dims.iconClass} strokeWidth={2.25} />
    </span>
  );
}
