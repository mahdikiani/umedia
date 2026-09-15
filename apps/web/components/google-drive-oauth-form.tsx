"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  api,
  ApiError,
  type OAuthStartResponse,
  type ProviderConnection,
  type ProviderType,
} from "@/lib/api";

/** Google Drive (and future oauth providers): show authorize URL, paste
 * whatever Google redirected to, complete via `/providers/oauth/complete`. */
export function GoogleDriveOauthForm({
  provider,
  onCreated,
  onCancel,
  onBack,
}: {
  provider: ProviderType;
  onCreated: (connection: ProviderConnection) => void;
  onCancel?: () => void;
  onBack: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(true);
  const [error, setError] = useState("");
  const [oauth, setOauth] = useState<OAuthStartResponse | null>(null);
  const [callback, setCallback] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setStarting(true);
      setError("");
      try {
        const started = await api<OAuthStartResponse>("/providers/oauth/start", {
          method: "POST",
          body: JSON.stringify({ provider_type: provider.id }),
        });
        if (!cancelled) setOauth(started);
      } catch (requestError) {
        if (!cancelled) {
          setError(
            requestError instanceof ApiError
              ? requestError.message
              : "Could not start Google authorization.",
          );
        }
      } finally {
        if (!cancelled) setStarting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [provider.id]);

  async function complete(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!oauth) return;
    setSaving(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const rootFolder = String(form.get("root_folder_id") ?? "").trim();
    try {
      const connection = await api<ProviderConnection>("/providers/oauth/complete", {
        method: "POST",
        body: JSON.stringify({
          provider_type: provider.id,
          name: form.get("connection_name"),
          callback,
          state: oauth.state,
          root_folder_id: rootFolder || null,
          import_existing: form.get("import_existing") === "on",
          mirror_structure: form.get("mirror_structure") === "on",
        }),
      });
      onCreated(connection);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not complete Google authorization.",
      );
    } finally {
      setSaving(false);
    }
  }

  function openAuthorize() {
    if (!oauth?.authorization_url) return;
    const width = 520;
    const height = 720;
    const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
    const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
    window.open(
      oauth.authorization_url,
      "umedia-google-oauth",
      `popup=yes,width=${width},height=${height},left=${left},top=${top}`,
    );
  }

  async function copyAuthorizeUrl() {
    if (!oauth?.authorization_url) return;
    try {
      await navigator.clipboard.writeText(oauth.authorization_url);
    } catch {
      setError("Could not copy the authorization URL.");
    }
  }

  const rootField = provider.fields.find((field) => field.key === "root_folder_id");

  return (
    <form className="space-y-4" onSubmit={(event) => void complete(event)}>
      <button
        className="text-xs font-medium text-muted-foreground"
        onClick={onBack}
        type="button"
      >
        ← All providers
      </button>
      <div>
        <h3 className="text-lg font-semibold">{provider.name}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{provider.description}</p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="connection_name">Connection name</Label>
        <Input
          defaultValue={provider.name}
          id="connection_name"
          name="connection_name"
          required
        />
      </div>

      {rootField && (
        <div className="space-y-1.5">
          <Label htmlFor="root_folder_id">{rootField.label}</Label>
          <Input
            id="root_folder_id"
            name="root_folder_id"
            placeholder={rootField.placeholder ?? undefined}
          />
        </div>
      )}

      <div className="space-y-3 rounded-xl border p-4">
        <div className="space-y-1">
          <p className="text-sm font-medium">Authorize with Google</p>
          <p className="text-xs leading-5 text-muted-foreground">
            Open the URL below, sign in, then paste whatever Google redirects to
            (full URL, query string, code, or token JSON).
          </p>
        </div>
        {starting && (
          <p className="text-xs text-muted-foreground">Preparing authorization URL…</p>
        )}
        {oauth && (
          <>
            <div className="break-all rounded-lg bg-muted p-3 font-mono text-xs leading-5">
              {oauth.authorization_url}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={openAuthorize} type="button" variant="secondary">
                Open Google login
              </Button>
              <Button onClick={() => void copyAuthorizeUrl()} type="button" variant="outline">
                Copy URL
              </Button>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="oauth_callback">
                Paste the redirect URL (or code / query params / token JSON)
              </Label>
              <textarea
                className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex min-h-24 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
                id="oauth_callback"
                onChange={(event) => setCallback(event.target.value)}
                required
                value={callback}
              />
            </div>
          </>
        )}
      </div>

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
              Add objects already stored by this provider to your UMedia library.
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
        <Button disabled={saving || starting || !oauth || !callback.trim()} type="submit">
          {saving ? "Connecting…" : "Connect Google Drive"}
        </Button>
      </div>
    </form>
  );
}
