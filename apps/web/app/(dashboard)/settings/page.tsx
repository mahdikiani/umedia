"use client";

import { Database, Lock, Plus } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { AddStorageForm } from "@/components/add-storage-form";
import { AccessKeysSettings } from "@/components/access-keys-settings";
import { UsersSettings } from "@/components/users-settings";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  api,
  ApiError,
  type AuthState,
  type ProviderConnection,
  type ProviderType,
} from "@/lib/api";

export default function SettingsPage() {
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [storageDialogOpen, setStorageDialogOpen] = useState(false);
  const [submittingPassword, setSubmittingPassword] = useState(false);

  useEffect(() => {
    Promise.all([
      api<AuthState>("/auth/state"),
      api<ProviderType[]>("/provider-types"),
      api<ProviderConnection[]>("/providers"),
    ]).then(([authState, types, list]) => {
      setIsAdmin(authState.user?.roles.includes("admin") ?? false);
      setProviderTypes(types);
      setConnections(list);
    });
  }, []);

  async function removeConnection(uid: string) {
    try {
      await api(`/providers/${uid}`, { method: "DELETE" });
      setConnections((previous) => previous.filter((item) => item.uid !== uid));
    } catch (requestError) {
      toast.error(
        requestError instanceof Error ? requestError.message : "Could not remove.",
      );
    }
  }

  async function updateConnectionFlags(
    uid: string,
    flags: Partial<Pick<ProviderConnection, "import_existing" | "mirror_structure">>,
  ) {
    const previous = connections.find((item) => item.uid === uid);
    if (!previous) return;
    // Optimistic UI; roll back if the backend rejects (e.g. Telegram + mirror).
    setConnections((list) =>
      list.map((item) => (item.uid === uid ? { ...item, ...flags } : item)),
    );
    try {
      const updated = await api<ProviderConnection>(`/providers/${uid}`, {
        method: "PATCH",
        body: JSON.stringify(flags),
      });
      setConnections((list) =>
        list.map((item) => (item.uid === uid ? updated : item)),
      );
    } catch (requestError) {
      setConnections((list) =>
        list.map((item) => (item.uid === uid ? previous : item)),
      );
      toast.error(
        requestError instanceof Error
          ? requestError.message
          : "Could not update connection.",
      );
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("currentPassword"));
    const newPassword = String(form.get("newPassword"));
    const confirmPassword = String(form.get("confirmPassword"));
    if (newPassword !== confirmPassword) {
      toast.error("New passwords do not match.");
      return;
    }
    setSubmittingPassword(true);
    try {
      await api("/auth/password", {
        method: "PATCH",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      event.currentTarget.reset();
      toast.success("Password changed. You're still signed in on this device.");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Password change failed.",
      );
    } finally {
      setSubmittingPassword(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Administration</p>
      </div>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-3">
          <div className="flex gap-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
              <Database size={16} />
            </div>
            <div>
              <h2 className="font-semibold">Storage</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Connections to the providers your resources actually live on.
              </p>
            </div>
          </div>
          {isAdmin && (
            <Button onClick={() => setStorageDialogOpen(true)} size="sm">
              <Plus size={14} /> Add storage
            </Button>
          )}
        </CardHeader>
        {connections.length > 0 && (
          <CardContent className="space-y-3">
            {connections.map((connection) => (
              <div
                className="space-y-3 rounded-lg border p-3"
                key={connection.uid}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium">{connection.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {connection.provider_type}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant={
                        connection.status === "configured" ? "default" : "destructive"
                      }
                    >
                      {connection.status}
                    </Badge>
                    {isAdmin && (
                      <Button
                        onClick={() => void removeConnection(connection.uid)}
                        size="sm"
                        variant="outline"
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                </div>
                {isAdmin && (
                  <div className="space-y-2 border-t pt-3">
                    <label className="flex items-start gap-3 text-sm">
                      <input
                        checked={connection.import_existing}
                        className="mt-0.5 size-4 shrink-0 accent-primary"
                        onChange={(event) =>
                          void updateConnectionFlags(connection.uid, {
                            import_existing: event.target.checked,
                          })
                        }
                        type="checkbox"
                      />
                      <span>
                        <span className="font-medium">Import existing objects</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">
                          Pull objects already on this provider into the library
                          (and on Sync).
                        </span>
                      </span>
                    </label>
                    <label className="flex items-start gap-3 text-sm">
                      <input
                        checked={connection.mirror_structure}
                        className="mt-0.5 size-4 shrink-0 accent-primary"
                        onChange={(event) =>
                          void updateConnectionFlags(connection.uid, {
                            mirror_structure: event.target.checked,
                          })
                        }
                        type="checkbox"
                      />
                      <span>
                        <span className="font-medium">Mirror folder structure</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">
                          Reflect library renames/moves on the provider when it
                          supports folders.
                        </span>
                      </span>
                    </label>
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        )}
      </Card>

      {isAdmin && <UsersSettings />}

      <AccessKeysSettings />

      <Card>
        <CardHeader className="flex-row items-start gap-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
            <Lock size={16} />
          </div>
          <div>
            <h2 className="font-semibold">Administrator password</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Changing it signs out every other session.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <form
            className="max-w-md space-y-4"
            onSubmit={(event) => void changePassword(event)}
          >
            {[
              ["Current password", "currentPassword"],
              ["New password", "newPassword"],
              ["Confirm new password", "confirmPassword"],
            ].map(([label, name]) => (
              <div className="space-y-1.5" key={name}>
                <Label htmlFor={name}>{label}</Label>
                <Input
                  id={name}
                  minLength={name === "currentPassword" ? 1 : 12}
                  name={name}
                  required
                  type="password"
                />
              </div>
            ))}
            <Button disabled={submittingPassword} type="submit">
              Change password
            </Button>
          </form>
        </CardContent>
      </Card>

      {isAdmin && (
        <Dialog onOpenChange={setStorageDialogOpen} open={storageDialogOpen}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Add storage</DialogTitle>
            </DialogHeader>
            <AddStorageForm
              onCancel={() => setStorageDialogOpen(false)}
              onCreated={(connection) => {
                setConnections((previous) => [connection, ...previous]);
                setStorageDialogOpen(false);
                toast.success(`${connection.name} connected.`);
              }}
              providerTypes={providerTypes}
            />
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
