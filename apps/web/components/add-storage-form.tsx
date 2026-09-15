"use client";

import { useState } from "react";

import { GoogleDriveOauthForm } from "@/components/google-drive-oauth-form";
import { StorageProviderIcon } from "@/components/storage-provider-icon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, type ProviderConnection, type ProviderType } from "@/lib/api";

const statusVariant: Record<string, "default" | "secondary" | "destructive"> = {
  available: "default",
  beta: "secondary",
  planned: "secondary",
};

/** The provider-type picker + connect-config form, shared by the
 * onboarding flow's first step and Settings' "Add storage" dialog -- one
 * implementation of `POST /providers` (and oauth complete), not two. */
export function AddStorageForm({
  providerTypes,
  onCreated,
  onCancel,
}: {
  providerTypes: ProviderType[];
  onCreated: (connection: ProviderConnection) => void;
  onCancel?: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const selected = providerTypes.find((item) => item.id === selectedId) ?? null;

  async function createConnection(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setSaving(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const config = Object.fromEntries(
      selected.fields.map((field) => [field.key, form.get(field.key)]),
    );
    try {
      const connection = await api<ProviderConnection>("/providers", {
        method: "POST",
        body: JSON.stringify({
          provider_type: selected.id,
          name: form.get("connection_name"),
          config,
          import_existing: form.get("import_existing") === "on",
          mirror_structure: form.get("mirror_structure") === "on",
        }),
      });
      onCreated(connection);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not add storage.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!selected) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {providerTypes.map((provider) => (
          <button
            className="rounded-xl border p-4 text-left transition hover:bg-muted"
            key={provider.id}
            onClick={() => setSelectedId(provider.id)}
            type="button"
          >
            <div className="flex items-start justify-between gap-3">
              <StorageProviderIcon providerType={provider.id} size="md" />
              <Badge variant={statusVariant[provider.status] ?? "secondary"}>
                {provider.status}
              </Badge>
            </div>
            <h3 className="mt-4 text-sm font-semibold">{provider.name}</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {provider.description}
            </p>
          </button>
        ))}
      </div>
    );
  }

  if (selected.connect_flow === "oauth") {
    return (
      <GoogleDriveOauthForm
        onBack={() => setSelectedId(null)}
        onCancel={onCancel}
        onCreated={onCreated}
        provider={selected}
      />
    );
  }

  return (
    <form className="space-y-4" onSubmit={(event) => void createConnection(event)}>
      <button
        className="text-xs font-medium text-muted-foreground"
        onClick={() => setSelectedId(null)}
        type="button"
      >
        ← All providers
      </button>
      <div>
        <h3 className="text-lg font-semibold">{selected.name}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{selected.description}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="connection_name">Connection name</Label>
          <Input
            defaultValue={selected.name}
            id="connection_name"
            name="connection_name"
            required
          />
        </div>
        {selected.fields.map((field) => (
          <div className="space-y-1.5" key={field.key}>
            <Label htmlFor={field.key}>{field.label}</Label>
            <Input
              id={field.key}
              name={field.key}
              placeholder={field.placeholder ?? undefined}
              required={field.required}
              type={field.input_type === "password" ? "password" : "text"}
            />
          </div>
        ))}
      </div>
      {selected.status === "planned" && (
        <p className="rounded-xl bg-muted p-3 text-xs leading-5 text-muted-foreground">
          This native adapter is documented in the roadmap. You can save its encrypted
          configuration now; file operations will be enabled when its contract suite
          passes.
        </p>
      )}
      <div className="space-y-3 rounded-xl border p-4">
        <div className="flex items-start gap-3">
          <input
            className="mt-0.5 size-4 shrink-0 accent-primary"
            id="import_existing"
            name="import_existing"
            type="checkbox"
          />
          <div className="space-y-1">
            <Label htmlFor="import_existing">
              Import existing objects from provider
            </Label>
            <p className="text-xs leading-5 text-muted-foreground">
              Pull files and folders already in the bucket into your library
              (under a folder named after this connection). Leave off for an
              empty library that only receives new uploads.
            </p>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <input
            className="mt-0.5 size-4 shrink-0 accent-primary"
            id="mirror_structure"
            name="mirror_structure"
            type="checkbox"
          />
          <div className="space-y-1">
            <Label htmlFor="mirror_structure">
              Mirror folder structure to provider
            </Label>
            <p className="text-xs leading-5 text-muted-foreground">
              Reflect supported library renames and moves on the provider.
            </p>
          </div>
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end gap-3 border-t pt-4">
        {onCancel && (
          <Button onClick={onCancel} type="button" variant="outline">
            Cancel
          </Button>
        )}
        <Button disabled={saving} type="submit">
          {saving ? "Saving…" : "Save connection"}
        </Button>
      </div>
    </form>
  );
}
