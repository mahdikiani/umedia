"use client";

import { Check, ChevronDown, Database, Pencil, Plus, RefreshCw, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { AddStorageForm } from "@/components/add-storage-form";
import { useLocale } from "@/components/locale-provider";
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
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { connectionNameError } from "@/lib/connection-name";
import { useCopy } from "@/lib/copy";
import {
  api,
  type AuthState,
  type ProviderConnection,
  type ProviderType,
} from "@/lib/api";

const SYNC_POLL_MS = 1500;

type SyncStatus = {
  status: "idle" | "running";
  connection_id: string;
};

type ConnectionFlags = Partial<
  Pick<ProviderConnection, "import_existing" | "mirror_structure">
>;

type RenameResult = { ok: true } | { ok: false; message: string };

export default function StorageSettingsPage() {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [storageDialogOpen, setStorageDialogOpen] = useState(false);
  const [removingUid, setRemovingUid] = useState<string | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(true);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<AuthState>("/auth/state"),
      api<ProviderType[]>("/provider-types"),
      api<ProviderConnection[]>("/providers"),
    ])
      .then(([authState, types, list]) => {
        if (!active) return;
        const admin = authState.user?.roles.includes("admin") ?? false;
        setIsAdmin(admin);
        setProviderTypes(
          admin ? types : types.filter((item) => item.id !== "local"),
        );
        setConnections(list);
      })
      .catch((error: unknown) => {
        if (!active) return;
        toast.error(
          error instanceof Error ? error.message : text.storageLoadFailed,
        );
      })
      .finally(() => {
        if (active) setLoadingConnections(false);
      });
    return () => {
      active = false;
    };
  }, [text.storageLoadFailed]);

  async function removeConnection(uid: string) {
    setRemovingUid(uid);
    try {
      await api(`/providers/${uid}`, { method: "DELETE" });
      setConnections((previous) => previous.filter((item) => item.uid !== uid));
      toast.success(text.storageRemoved);
    } catch (requestError) {
      toast.error(
        requestError instanceof Error
          ? requestError.message
          : text.storageRemoveFailed,
      );
    } finally {
      setRemovingUid(null);
    }
  }

  async function updateConnectionFlags(uid: string, flags: ConnectionFlags) {
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
          : text.storageUpdateFailed,
      );
    }
  }

  /** Rename resolves with the server's reason on failure so the card can
   * keep the editor open and show it next to the field. */
  async function renameConnection(
    uid: string,
    name: string,
  ): Promise<RenameResult> {
    try {
      const updated = await api<ProviderConnection>(`/providers/${uid}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      setConnections((list) =>
        list.map((item) => (item.uid === uid ? updated : item)),
      );
      toast.success(text.connectionRenamed);
      return { ok: true };
    } catch (requestError) {
      return {
        ok: false,
        message:
          requestError instanceof Error
            ? requestError.message
            : text.storageUpdateFailed,
      };
    }
  }

  return (
    <main className="mx-auto max-w-5xl space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="text-2xl font-semibold tracking-tight">
            {text.storageSettingsTitle}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {text.storageSettingsBody}
          </p>
        </div>
        <Button onClick={() => setStorageDialogOpen(true)} size="sm">
          <Plus size={14} /> {text.addStorage}
        </Button>
      </header>

      <section aria-labelledby="storage-connections-heading" className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Database aria-hidden="true" className="size-4 text-muted-foreground" />
              <h2
                className="text-base font-semibold tracking-tight"
                id="storage-connections-heading"
              >
                {text.storageConnections}
              </h2>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {text.storageConnectionsBody}
            </p>
          </div>
          <Badge variant="secondary">
            {connections.length === 1
              ? text.storageOneConnection
              : text.storageConnectionCount.replace(
                  "{count}",
                  String(connections.length),
                )}
          </Badge>
        </div>

        {loadingConnections ? (
          <Card aria-busy="true">
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              {text.storageLoading}
            </CardContent>
          </Card>
        ) : connections.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-2">
            {connections.map((connection) => (
              <StorageConnectionCard
                connection={connection}
                key={connection.uid}
                onRemove={() => void removeConnection(connection.uid)}
                onRename={(name) => renameConnection(connection.uid, name)}
                onUpdateFlags={(flags) =>
                  void updateConnectionFlags(connection.uid, flags)
                }
                removing={removingUid === connection.uid}
                text={text}
              />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              {text.storageEmpty}
            </CardContent>
          </Card>
        )}
      </section>

      <PlacementSettingsCard connections={connections} isAdmin={isAdmin} />

      <Dialog onOpenChange={setStorageDialogOpen} open={storageDialogOpen}>
        <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{text.addStorage}</DialogTitle>
          </DialogHeader>
          <div
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
            data-slot="storage-dialog-scroll-area"
          >
            <AddStorageForm
              existingNames={connections.map((item) => item.name)}
              onCancel={() => setStorageDialogOpen(false)}
              onCreated={(connection) => {
                setConnections((previous) => [connection, ...previous]);
                setStorageDialogOpen(false);
                toast.success(`${connection.name} connected.`);
              }}
              providerTypes={providerTypes}
            />
          </div>
        </DialogContent>
      </Dialog>
    </main>
  );
}

function StorageConnectionCard({
  connection,
  onRemove,
  onRename,
  onUpdateFlags,
  removing,
  text,
}: {
  connection: ProviderConnection;
  onRemove: () => void;
  onRename: (name: string) => Promise<RenameResult>;
  onUpdateFlags: (flags: ConnectionFlags) => void;
  removing: boolean;
  text: ReturnType<typeof useCopy>;
}) {
  const [syncing, setSyncing] = useState(false);
  const startPollingRef = useRef<(() => void) | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let active = true;

    const poll = async () => {
      try {
        const status = await api<SyncStatus>(
          `/providers/${connection.uid}/sync`,
        );
        if (!active) return;
        setSyncing(status.status === "running");
        if (status.status === "running") {
          pollTimerRef.current = setTimeout(() => void poll(), SYNC_POLL_MS);
        }
      } catch {
        if (active) setSyncing(false);
      }
    };

    startPollingRef.current = () => {
      if (active) void poll();
    };
    void poll();

    return () => {
      active = false;
      if (pollTimerRef.current !== null) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
      startPollingRef.current = null;
    };
  }, [connection.uid]);

  async function sync() {
    if (syncing) return;
    setSyncing(true);
    try {
      await api(`/providers/${connection.uid}/sync`, { method: "POST" });
      toast.success(text.syncStarted);
      startPollingRef.current?.();
    } catch (error) {
      try {
        const status = await api<SyncStatus>(
          `/providers/${connection.uid}/sync`,
        );
        if (status.status === "running") {
          toast.message(text.syncAlreadyRunning);
          startPollingRef.current?.();
          return;
        }
      } catch {
        setSyncing(false);
        toast.error(error instanceof Error ? error.message : text.syncFailed);
        return;
      }
      setSyncing(false);
      toast.error(error instanceof Error ? error.message : text.syncFailed);
    }
  }

  return (
    <article className="overflow-hidden rounded-xl border bg-card">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex min-w-0 items-center gap-3">
          <StorageProviderIcon
            providerType={connection.provider_type}
            size="md"
            variant={connection.variant}
          />
          <div className="min-w-0 space-y-1">
            <ConnectionName
              name={connection.name}
              onRename={onRename}
              text={text}
            />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {connection.provider_type}
              </span>
              <Badge
                className="font-normal"
                variant={
                  connection.status === "configured" ? "secondary" : "destructive"
                }
              >
                {connection.status === "configured"
                  ? text.connectionConfigured
                  : text.connectionNeedsAttention}
              </Badge>
            </div>
          </div>
        </div>
        <Button
          disabled={syncing}
          onClick={() => void sync()}
          size="sm"
          variant="outline"
        >
          <RefreshCw
            aria-hidden="true"
            className={syncing ? "animate-spin" : undefined}
            size={14}
          />
          {syncing ? text.syncing : text.syncNow}
        </Button>
      </div>

      <details className="group border-t px-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-3 text-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card [&::-webkit-details-marker]:hidden">
          <span>{text.connectionPreferences}</span>
          <ChevronDown
            aria-hidden="true"
            className="size-4 shrink-0 transition-transform group-open:rotate-180"
          />
        </summary>
        <div className="grid gap-4 pb-4 sm:grid-cols-2">
          <label className="flex items-start gap-3 text-sm">
            <input
              checked={connection.import_existing}
              className="mt-0.5 size-4 shrink-0 accent-primary focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              onChange={(event) =>
                onUpdateFlags({ import_existing: event.target.checked })
              }
              type="checkbox"
            />
            <span>
              <span className="font-medium">{text.importExisting}</span>
              <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                {text.importExistingHelp}
              </span>
            </span>
          </label>
          <label
            className={`flex items-start gap-3 text-sm ${
              connection.provider_type === "telegram" ? "opacity-60" : ""
            }`}
          >
            <input
              checked={connection.mirror_structure}
              className="mt-0.5 size-4 shrink-0 accent-primary focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              disabled={connection.provider_type === "telegram"}
              onChange={(event) =>
                onUpdateFlags({ mirror_structure: event.target.checked })
              }
              type="checkbox"
            />
            <span>
              <span className="font-medium">{text.mirrorStructure}</span>
              <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                {connection.provider_type === "telegram"
                  ? text.mirrorTelegramHelp
                  : text.mirrorStructureHelp}
              </span>
            </span>
          </label>
          <div className="flex justify-end sm:col-span-2">
            <AlertDialog>
              <AlertDialogTrigger
                render={
                  <Button
                    disabled={removing}
                    size="sm"
                    type="button"
                    variant="outline"
                  />
                }
              >
                {text.removeStorage}
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{text.removeStorageTitle}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {text.removeStorageBody.replace("{name}", connection.name)}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{text.cancel}</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={removing}
                    onClick={onRemove}
                    variant="destructive"
                  >
                    {text.removeStorage}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </details>
    </article>
  );
}

/** The connection name with an inline editor. The name is also the
 * connection's S3 bucket, so the S3 rules are checked as the user types;
 * the server re-checks them and also rejects names already in use. */
function ConnectionName({
  name,
  onRename,
  text,
}: {
  name: string;
  onRename: (name: string) => Promise<RenameResult>;
  text: ReturnType<typeof useCopy>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const inputId = useId();
  const hintId = `${inputId}-hint`;

  const trimmed = draft.trim();
  const ruleError = trimmed === name ? null : connectionNameError(trimmed);
  const error = ruleError ?? serverError;

  function startEditing() {
    setDraft(name);
    setServerError(null);
    setEditing(true);
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (trimmed === name) {
      setEditing(false);
      return;
    }
    if (ruleError) return;
    setSaving(true);
    const result = await onRename(trimmed);
    setSaving(false);
    if (result.ok) setEditing(false);
    else setServerError(result.message);
  }

  if (!editing) {
    return (
      <div className="flex min-w-0 items-center gap-1">
        <h3 className="truncate font-mono text-sm font-medium" title={name}>
          {name}
        </h3>
        <Button
          aria-label={`${text.renameConnection} ${name}`}
          className="size-6 shrink-0"
          onClick={startEditing}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Pencil aria-hidden="true" size={12} />
        </Button>
      </div>
    );
  }

  return (
    <form className="space-y-1" onSubmit={(event) => void save(event)}>
      <div className="flex items-center gap-1">
        <label className="sr-only" htmlFor={inputId}>
          {text.renameConnectionLabel}
        </label>
        <Input
          aria-describedby={hintId}
          aria-invalid={error ? true : undefined}
          autoCapitalize="none"
          autoComplete="off"
          autoFocus
          className="h-7 w-48 font-mono text-sm"
          id={inputId}
          maxLength={63}
          onChange={(event) => {
            setDraft(event.target.value.toLowerCase());
            setServerError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") setEditing(false);
          }}
          spellCheck={false}
          value={draft}
        />
        <Button
          aria-label={text.saveName}
          className="size-7"
          disabled={saving || Boolean(ruleError)}
          size="icon"
          type="submit"
          variant="ghost"
        >
          <Check aria-hidden="true" size={14} />
        </Button>
        <Button
          aria-label={text.cancel}
          className="size-7"
          onClick={() => setEditing(false)}
          size="icon"
          type="button"
          variant="ghost"
        >
          <X aria-hidden="true" size={14} />
        </Button>
      </div>
      <p
        aria-live="polite"
        className={error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
        id={hintId}
      >
        {error ?? text.connectionNameHelp}
      </p>
    </form>
  );
}
