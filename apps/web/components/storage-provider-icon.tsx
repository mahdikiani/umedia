import {
  siBackblaze,
  siCloudflare,
  siDigitalocean,
  siDropbox,
  siGoogledrive,
  siHuggingface,
  siMinio,
  siNextcloud,
  siOwncloud,
  siTelegram,
  siWasabi,
} from "simple-icons";
import {
  Cloud,
  Database,
  FolderTree,
  Globe,
  HardDrive,
  KeyRound,
  Server,
  type LucideIcon,
} from "lucide-react";
import type { JSX } from "react";

import { cn } from "@/lib/utils";

/** Normalize aliases so UI stays consistent across callers. */
function normalizeProviderType(providerType: string): string {
  const key = providerType.trim().toLowerCase();
  if (key === "gdrive") return "google_drive";
  return key;
}

type BrandIcon = { title: string; hex: string; path: string };

/** A provider's mark: an official brand glyph (simple-icons, CC0) where
 * one exists, otherwise a generic icon for the protocol. Microsoft and
 * AWS do not allow their logos in simple-icons, so OneDrive and plain
 * S3 use generic glyphs rather than copies of trademarked artwork. */
type Mark =
  | { kind: "brand"; icon: BrandIcon; label: string }
  | { kind: "glyph"; icon: LucideIcon; label: string; tint: string };

const glyph = (icon: LucideIcon, label: string, tint: string): Mark => ({
  kind: "glyph",
  icon,
  label,
  tint,
});
const brand = (icon: BrandIcon, label = icon.title): Mark => ({
  kind: "brand",
  icon,
  label,
});

const PROVIDER_MARKS: Record<string, Mark> = {
  local: glyph(HardDrive, "Local filesystem", "local"),
  s3: glyph(Database, "S3 compatible", "s3"),
  google_drive: brand(siGoogledrive),
  onedrive: glyph(Cloud, "Microsoft OneDrive", "onedrive"),
  dropbox: brand(siDropbox),
  telegram: brand(siTelegram),
  huggingface: brand(siHuggingface),
  ftp: glyph(FolderTree, "FTP / FTPS", "ftp"),
  sftp: glyph(KeyRound, "SFTP", "sftp"),
  webdav: glyph(Globe, "WebDAV", "webdav"),
  nextcloud: brand(siNextcloud),
};

/** `variant` (from the API) names the service behind a generic protocol:
 * an S3 endpoint's vendor or a WebDAV server type. */
const VARIANT_MARKS: Record<string, Mark> = {
  cloudflare: brand(siCloudflare, "Cloudflare R2"),
  backblaze: brand(siBackblaze, "Backblaze B2"),
  wasabi: brand(siWasabi),
  digitalocean: brand(siDigitalocean, "DigitalOcean Spaces"),
  minio: brand(siMinio),
  aws: glyph(Database, "Amazon S3", "s3"),
  nextcloud: brand(siNextcloud),
  owncloud: brand(siOwncloud),
};

const UNKNOWN_MARK = glyph(Server, "Storage", "unknown");

export function providerMark(
  providerType: string,
  variant?: string | null,
): Mark {
  const normalized = normalizeProviderType(providerType);
  return (
    (variant ? VARIANT_MARKS[variant] : undefined) ??
    PROVIDER_MARKS[normalized] ??
    UNKNOWN_MARK
  );
}

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
  connections: {
    uid: string;
    name: string;
    provider_type: string;
    variant?: string | null;
  }[],
): { name: string; providerType: string; variant: string | null } | null {
  if (!connectionId) return null;
  const match = connections.find((item) => item.uid === connectionId);
  if (!match) return null;
  return {
    name: match.name,
    providerType: match.provider_type,
    variant: match.variant ?? null,
  };
}

export function StorageProviderIcon({
  providerType,
  variant,
  className,
  title,
  size = "sm",
}: {
  providerType: string;
  variant?: string | null;
  className?: string;
  title?: string;
  size?: "sm" | "md";
}): JSX.Element {
  const normalized = normalizeProviderType(providerType);
  const mark = providerMark(normalized, variant);
  const dims = SIZE[size];

  return (
    <span
      aria-hidden={title ? undefined : true}
      className={cn(
        "grid shrink-0 place-items-center",
        dims.tile,
        mark.kind === "brand" && "storage-brand-tile",
        className,
      )}
      data-provider-type={normalized}
      data-provider-variant={variant ?? undefined}
      // Inline vars beat parent Select/menu focus text overrides.
      style={
        mark.kind === "brand"
          ? { ["--brand" as string]: `#${mark.icon.hex}` }
          : {
              backgroundColor: `var(--storage-${mark.tint}-bg)`,
              color: `var(--storage-${mark.tint}-fg)`,
            }
      }
      title={title ?? mark.label}
    >
      {mark.kind === "brand" ? (
        <svg
          aria-hidden="true"
          className={dims.iconClass}
          fill="currentColor"
          viewBox="0 0 24 24"
        >
          <path d={mark.icon.path} />
        </svg>
      ) : (
        <mark.icon className={dims.iconClass} strokeWidth={2.25} />
      )}
    </span>
  );
}
