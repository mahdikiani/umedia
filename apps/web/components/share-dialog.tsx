"use client";

import { Copy, ExternalLink, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  absoluteUrl,
  api,
  createTemporaryLink,
  publicLinkUrl,
  type MediaFileItem,
  type TemporaryLink,
} from "@/lib/api";

const DURATION_PRESETS = [
  { label: "1 hour", seconds: 3600 },
  { label: "1 day", seconds: 86400 },
  { label: "7 days", seconds: 604800 },
] as const;

/** Select `value` is the human label shown in the trigger (Base UI
 * SelectValue renders the value string when closed). Map to API ints
 * on submit. */
const PERMISSION_OPTIONS = [
  { label: "Read", value: "Read", level: 10 },
  { label: "Write", value: "Write", level: 20 },
  { label: "Manage", value: "Manage", level: 30 },
] as const;

type PermissionOptionValue = (typeof PERMISSION_OPTIONS)[number]["value"];

function permissionName(level: number): string {
  return (
    PERMISSION_OPTIONS.find((candidate) => candidate.level === level)?.label
    ?? `Level ${level}`
  );
}

function permissionLevel(value: string): number {
  return (
    PERMISSION_OPTIONS.find((candidate) => candidate.value === value)?.level
    ?? PERMISSION_OPTIONS[0].level
  );
}

async function copyToClipboard(text: string, message: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(message);
  } catch {
    toast.error("Could not copy to clipboard.");
  }
}

type ShareDialogProps = {
  /** The file being shared; `null` keeps the dialog closed. */
  item: MediaFileItem | null;
  onOpenChange: (open: boolean) => void;
  /** Every mutation returns the fresh MediaFile — the page owns the item
   * state (its list row and the `item` prop above both come from it). */
  onItemUpdated: (item: MediaFileItem) => void;
};

export function ShareDialog({ item, onOpenChange, onItemUpdated }: ShareDialogProps) {
  const [busy, setBusy] = useState(false);
  const [durationSeconds, setDurationSeconds] = useState<number>(
    DURATION_PRESETS[0].seconds,
  );
  const [temporary, setTemporary] = useState<TemporaryLink | null>(null);
  const [shareUid, setShareUid] = useState("");
  const [shareLevel, setShareLevel] = useState<PermissionOptionValue>(
    PERMISSION_OPTIONS[0].value,
  );

  // Reset per-file state when the dialog is re-targeted at another file
  // (adjust-state-during-render, so no effect + no stale flash).
  const [lastUid, setLastUid] = useState(item?.uid);
  if (item?.uid !== lastUid) {
    setLastUid(item?.uid);
    setTemporary(null);
    setShareUid("");
    setShareLevel(PERMISSION_OPTIONS[0].value);
    setDurationSeconds(DURATION_PRESETS[0].seconds);
  }

  async function togglePublic() {
    if (!item) return;
    const next = item.public_permission === "read" ? "none" : "read";
    setBusy(true);
    try {
      const updated = await api<MediaFileItem>(`/files/${item.uid}`, {
        method: "PATCH",
        body: JSON.stringify({ public_permission: next }),
      });
      onItemUpdated(updated);
      toast.success(
        next === "read"
          ? "Anyone with the link can now view this file."
          : "Made private again.",
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not update sharing.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function generateTemporaryLink() {
    if (!item) return;
    setBusy(true);
    try {
      setTemporary(await createTemporaryLink(item.uid, durationSeconds));
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not create the link.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function setUserPermission(userId: string, permission: number) {
    if (!item) return;
    setBusy(true);
    try {
      const updated = await api<MediaFileItem>(`/files/${item.uid}/permissions`, {
        method: "PUT",
        body: JSON.stringify({ user_id: userId, permission }),
      });
      onItemUpdated(updated);
      if (permission === 0) {
        toast.success("Access removed.");
      } else {
        toast.success("Shared.");
        setShareUid("");
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not update access.",
      );
    } finally {
      setBusy(false);
    }
  }

  const isPublic = item?.public_permission === "read";
  const permanentUrl = item ? publicLinkUrl(item.uid, item.name) : "";
  const temporaryUrl = temporary ? absoluteUrl(temporary.url) : "";

  return (
    <Dialog onOpenChange={onOpenChange} open={item !== null}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share “{item?.name}”</DialogTitle>
          <DialogDescription>
            Links and people with access to this file.
          </DialogDescription>
        </DialogHeader>
        {item && (
          <div className="flex flex-col gap-4">
            <section className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium">Permanent link</h3>
                  <p className="text-xs text-muted-foreground">
                    Anyone with the link can view, until turned off.
                  </p>
                </div>
                <Button
                  disabled={busy}
                  onClick={() => void togglePublic()}
                  size="sm"
                  variant={isPublic ? "outline" : "default"}
                >
                  {isPublic ? "Make private" : "Make public"}
                </Button>
              </div>
              {isPublic && (
                <div className="flex items-center gap-2">
                  <Input
                    className="font-mono text-xs"
                    readOnly
                    value={permanentUrl}
                  />
                  <Button
                    aria-label="Copy permanent link"
                    onClick={() =>
                      void copyToClipboard(
                        permanentUrl,
                        "Public link copied to clipboard.",
                      )
                    }
                    size="icon-sm"
                    variant="outline"
                  >
                    <Copy size={14} />
                  </Button>
                  <a
                    aria-label="Open permanent link"
                    className={cn(buttonVariants({ size: "icon-sm", variant: "outline" }))}
                    href={permanentUrl}
                    rel="noopener noreferrer"
                    target="_blank"
                  >
                    <ExternalLink size={14} />
                  </a>
                </div>
              )}
            </section>

            <Separator />

            <section className="flex flex-col gap-2">
              <div>
                <h3 className="text-sm font-medium">Temporary link</h3>
                <p className="text-xs text-muted-foreground">
                  A signed URL that stops working after it expires — the file
                  itself stays private.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {DURATION_PRESETS.map((preset) => (
                  <Button
                    aria-pressed={durationSeconds === preset.seconds}
                    key={preset.seconds}
                    onClick={() => setDurationSeconds(preset.seconds)}
                    size="sm"
                    variant={durationSeconds === preset.seconds ? "default" : "outline"}
                  >
                    {preset.label}
                  </Button>
                ))}
                <Button
                  disabled={busy}
                  onClick={() => void generateTemporaryLink()}
                  size="sm"
                  variant="secondary"
                >
                  Generate link
                </Button>
              </div>
              {temporary && (
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <Input
                      className="font-mono text-xs"
                      readOnly
                      value={temporaryUrl}
                    />
                    <Button
                      aria-label="Copy temporary link"
                      onClick={() =>
                        void copyToClipboard(
                          temporaryUrl,
                          "Temporary link copied to clipboard.",
                        )
                      }
                      size="icon-sm"
                      variant="outline"
                    >
                      <Copy size={14} />
                    </Button>
                    <a
                      aria-label="Open temporary link"
                      className={cn(buttonVariants({ size: "icon-sm", variant: "outline" }))}
                      href={temporaryUrl}
                      rel="noopener noreferrer"
                      target="_blank"
                    >
                      <ExternalLink size={14} />
                    </a>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Expires {new Date(temporary.expires_at).toLocaleString()}
                  </p>
                </div>
              )}
            </section>

            <Separator />

            <section className="flex flex-col gap-2">
              <div>
                <h3 className="text-sm font-medium">People</h3>
                <p className="text-xs text-muted-foreground">
                  Share directly with another user by their uid.
                </p>
              </div>
              {item.permissions.length > 0 && (
                <ul className="flex flex-col gap-1.5">
                  {item.permissions.map((entry) => (
                    <li
                      className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5"
                      key={entry.user_id}
                    >
                      <span className="truncate font-mono text-xs">{entry.user_id}</span>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Badge variant="secondary">
                          {permissionName(entry.permission)}
                        </Badge>
                        <Button
                          aria-label={`Remove ${entry.user_id}`}
                          disabled={busy}
                          onClick={() => void setUserPermission(entry.user_id, 0)}
                          size="icon-xs"
                          variant="ghost"
                        >
                          <X size={12} />
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <Label className="sr-only" htmlFor="share-user-uid">
                    User uid
                  </Label>
                  <Input
                    id="share-user-uid"
                    onChange={(event) => setShareUid(event.target.value)}
                    placeholder="User uid"
                    value={shareUid}
                  />
                </div>
                <Select
                  onValueChange={(value) => {
                    if (
                      value === "Read"
                      || value === "Write"
                      || value === "Manage"
                    ) {
                      setShareLevel(value);
                    }
                  }}
                  value={shareLevel}
                >
                  <SelectTrigger aria-label="Permission level" className="w-28">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PERMISSION_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  disabled={busy || !shareUid.trim()}
                  onClick={() =>
                    void setUserPermission(
                      shareUid.trim(),
                      permissionLevel(shareLevel),
                    )
                  }
                  size="sm"
                >
                  Share
                </Button>
              </div>
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
