"use client";

import { Database, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { AddStorageForm } from "@/components/add-storage-form";
import { PlacementSettingsCard } from "@/components/placement-settings";
import { StorageProviderIcon } from "@/components/storage-provider-icon";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  api,
  type AuthState,
  type ProviderConnection,
  type ProviderType,
} from "@/lib/api";

export default function StorageSettingsPage() {
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [storageDialogOpen, setStorageDialogOpen] = useState(false);
  const [removingUid, setRemovingUid] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<AuthState>("/auth/state"),
      api<ProviderType[]>("/provider-types"),
      api<ProviderConnection[]>("/providers"),
    ]).then(([authState, types, list]) => {
      const admin = authState.user?.roles.includes("admin") ?? false;
      setIsAdmin(admin);
      setProviderTypes(
        admin ? types : types.filter((item) => item.id !== "local"),
      );
      setConnections(list);
    });
  }, []);

  async function removeConnection(uid: string) {
    setRemovingUid(uid);
    try {
      await api(`/providers/${uid}`, { method: "DELETE" });
      setConnections((previous) => previous.filter((item) => item.uid !== uid));
    } catch (requestError) {
      toast.error(
        requestError instanceof Error ? requestError.message : "Could not remove.",
      );
    } finally {
      setRemovingUid(null);
    }
  }

  async function updateConnectionFlags(
    uid: string,
    flags: Partial<Pick<ProviderConnection, "import_existing" | "mirror_structure">>,
  ) {
    const previous = connections.find((item) => item.uid === uid);
    if (!previous) return;
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

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Storage settings</h1>
        <p className="text-sm text-muted-foreground">
          Providers, import, and where new library files land.
        </p>
      </div>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-3">
          <div className="flex gap-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
              <Database size={16} />
            </div>
            <div>
              <h2 className="font-semibold">Connections</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Connections to the providers your resources actually live on.
              </p>
            </div>
          </div>
          <Button onClick={() => setStorageDialogOpen(true)} size="sm">
            <Plus size={14} /> Add storage
          </Button>
        </CardHeader>
        {connections.length > 0 && (
          <CardContent className="space-y-3">
            {connections.map((connection) => (
              <div
                className="space-y-3 rounded-lg border p-3"
                key={connection.uid}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <StorageProviderIcon
                      providerType={connection.provider_type}
                      size="sm"
                    />
                    <div className="min-w-0">
                      <div className="text-sm font-medium">{connection.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {connection.provider_type}
                      </div>
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
                    <AlertDialog>
                      <AlertDialogTrigger
                        render={
                          <Button size="sm" type="button" variant="outline" />
                        }
                      >
                        Remove
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Remove storage?</AlertDialogTitle>
                          <AlertDialogDescription>
                            Library files on <strong>{connection.name}</strong>{" "}
                            go to Trash. Provider bytes are not deleted.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            disabled={removingUid === connection.uid}
                            onClick={() => void removeConnection(connection.uid)}
                            variant="destructive"
                          >
                            Remove
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                </div>
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
                        Force UMedia library folders onto the provider. When
                        off, same-storage copies share one object instead of
                        duplicating bytes.
                      </span>
                    </span>
                  </label>
                </div>
              </div>
            ))}
          </CardContent>
        )}
      </Card>

      <PlacementSettingsCard connections={connections} isAdmin={isAdmin} />

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
    </div>
  );
}
