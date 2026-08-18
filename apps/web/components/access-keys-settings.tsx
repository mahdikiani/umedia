"use client";

import { Copy, KeyRound, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  api,
  type AccessKey,
  type AccessKeyCreated,
  type S3EndpointInfo,
} from "@/lib/api";

async function copyToClipboard(value: string, label: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} copied to clipboard.`);
  } catch {
    toast.error("Could not copy to clipboard.");
  }
}

/** Split `https://host/api/v1/s3` into client fields.
 *
 * Cyberduck's bookmark Path is the *bucket*, not this URI prefix. Putting
 * `/api/v1/s3` there makes it virtual-host `s3.hostname` and TLS dies.
 */
export function connectionFields(endpoint: string): {
  server: string;
  context: string;
  port: string;
} {
  try {
    const url = new URL(endpoint);
    return {
      server: url.hostname,
      context: url.pathname.replace(/\/$/, "") || "/",
      port: url.port || (url.protocol === "http:" ? "80" : "443"),
    };
  } catch {
    return { server: endpoint, context: "/api/v1/s3", port: "443" };
  }
}

export function cyberduckProfileXml(endpoint: string): string {
  const fields = connectionFields(endpoint);
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Protocol</key>
  <string>s3</string>
  <key>Vendor</key>
  <string>umedia</string>
  <key>Scheme</key>
  <string>https</string>
  <key>Description</key>
  <string>UMedia S3</string>
  <key>Default Hostname</key>
  <string>${fields.server}</string>
  <key>Default Port</key>
  <string>${fields.port}</string>
  <key>Hostname Configurable</key>
  <true/>
  <key>Port Configurable</key>
  <true/>
  <key>Context</key>
  <string>${fields.context}</string>
  <key>Username Placeholder</key>
  <string>Access Key ID</string>
  <key>Password Placeholder</key>
  <string>Secret Access Key</string>
  <key>Properties</key>
  <array>
    <string>s3.bucket.virtualhost.disable=true</string>
  </array>
</dict>
</plist>
`;
}

function downloadCyberduckProfile(endpoint: string) {
  const blob = new Blob([cyberduckProfileXml(endpoint)], {
    type: "application/x-cyberduck-profile",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "UMedia S3.cyberduckprofile";
  link.click();
  URL.revokeObjectURL(url);
}

export function AccessKeysSettings() {
  const [endpointInfo, setEndpointInfo] = useState<S3EndpointInfo | null>(null);
  const [keys, setKeys] = useState<AccessKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [revokingUid, setRevokingUid] = useState<string | null>(null);
  const [createdKey, setCreatedKey] = useState<AccessKeyCreated | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api<S3EndpointInfo>("/access-keys/s3"),
      api<AccessKey[]>("/access-keys"),
    ])
      .then(([info, list]) => {
        if (!cancelled) {
          setEndpointInfo(info);
          setKeys(list);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(error instanceof Error ? error.message : "Could not load keys.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshKeys() {
    try {
      setKeys(await api<AccessKey[]>("/access-keys"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not refresh keys.");
    }
  }

  async function createKey() {
    setCreating(true);
    try {
      setCreatedKey(
        await api<AccessKeyCreated>("/access-keys", {
          method: "POST",
          body: JSON.stringify({ label: "key" }),
        }),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create key.");
    } finally {
      setCreating(false);
    }
  }

  async function revokeKey(uid: string) {
    setRevokingUid(uid);
    try {
      await api(`/access-keys/${uid}`, { method: "DELETE" });
      setKeys((current) =>
        current.map((key) => (key.uid === uid ? { ...key, is_active: false } : key)),
      );
      toast.success("Access key revoked.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not revoke key.");
    } finally {
      setRevokingUid(null);
    }
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-start">
          <div className="flex min-w-0 gap-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
              <KeyRound size={16} />
            </div>
            <div>
              <h2 className="font-semibold">S3 access keys</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                rclone can use the endpoint URL as-is. Cyberduck cannot: leave
                its Path field empty (that field is the bucket, not{" "}
                <code className="text-xs">/api/v1/s3</code>), and open the
                downloaded profile so requests stay path-style.
              </p>
            </div>
          </div>
          <Button
            className="self-start" disabled={creating}
            onClick={() => void createKey()}
            size="sm"
            type="button"
          >
            <Plus size={14} /> {creating ? "Creating…" : "Create key"}
          </Button>
        </CardHeader>

        <CardContent className="space-y-5">
          {loading && (
            <p className="text-sm text-muted-foreground" role="status">
              Loading S3 settings…
            </p>
          )}

          {endpointInfo && (
            <div className="space-y-2">
              <Button
                onClick={() => downloadCyberduckProfile(endpointInfo.endpoint)}
                size="sm"
                type="button"
                variant="outline"
              >
                Download Cyberduck profile
              </Button>
              {(
                () => {
                  const fields = connectionFields(endpointInfo.endpoint);
                  return [
                    ["Server", fields.server],
                    ["Port", fields.port],
                    ["Cyberduck Path", "(leave empty)"],
                    ["Region", endpointInfo.region],
                    ["rclone / AWS endpoint", endpointInfo.endpoint],
                  ] as const;
                }
              )().map(([label, value]) => (
                <div
                  className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
                  key={label}
                >
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-muted-foreground">
                      {label}
                    </div>
                    <code className="break-all text-xs">{value}</code>
                  </div>
                  {value !== "(leave empty)" && (
                    <Button
                      aria-label={`Copy ${label.toLowerCase()}`}
                      onClick={() => void copyToClipboard(value, label)}
                      size="icon-sm"
                      type="button"
                      variant="outline"
                    >
                      <Copy size={14} />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}

          {!loading && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Access keys</h3>
              {keys.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No access keys yet. Create one to connect an S3 client.
                </p>
              )}
              {keys.map((key) => (
                <div
                  className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
                  key={key.uid}
                >
                  <div className="min-w-0">
                    <code className="block break-all text-xs">{key.access_key_id}</code>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <span className="text-xs text-muted-foreground">{key.label}</span>
                      <Badge variant={key.is_active ? "secondary" : "destructive"}>
                        {key.is_active ? "Active" : "Revoked"}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {new Date(key.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  {key.is_active && (
                    <Button
                      disabled={revokingUid === key.uid}
                      onClick={() => void revokeKey(key.uid)}
                      size="sm"
                      type="button"
                      variant="destructive"
                    >
                      {revokingUid === key.uid ? "Revoking…" : "Revoke"}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog
        onOpenChange={(open) => {
          if (!open && createdKey) {
            setCreatedKey(null);
            void refreshKeys();
          }
        }}
        open={createdKey !== null}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Save your secret access key</DialogTitle>
            <DialogDescription>
              This secret is shown only once. Copy it now and store it securely.
            </DialogDescription>
          </DialogHeader>
          {createdKey && (
            <div className="space-y-3">
              {(
                [
                  ["Access key ID", createdKey.access_key_id],
                  ["Secret access key", createdKey.secret_access_key],
                ] as const
              ).map(([label, value]) => (
                <div className="space-y-1.5" key={label}>
                  <div className="text-xs font-medium text-muted-foreground">
                    {label}
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all rounded-lg border bg-muted/50 p-2 text-xs">
                      {value}
                    </code>
                    <Button
                      aria-label={`Copy ${label.toLowerCase()}`}
                      onClick={() => void copyToClipboard(value, label)}
                      size="icon-sm"
                      type="button"
                      variant="outline"
                    >
                      <Copy size={14} />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
