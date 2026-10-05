"use client";

import { useState } from "react";
import { LockKeyhole, ShieldAlert, ShieldQuestion } from "lucide-react";

import { ConnectionNameField } from "@/components/connection-name-field";
import { OAuthProviderForm } from "@/components/google-drive-oauth-form";
import { TelegramLoginForm } from "@/components/telegram-login-form";
import { StorageProviderIcon } from "@/components/storage-provider-icon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, type ProviderConnection, type ProviderType } from "@/lib/api";

/** The SSH host key an SFTP server offered, returned with HTTP 409 when
 * it is not trusted yet (`host_key_unknown`) or has changed
 * (`host_key_mismatch`). See apps/media `HostKeyConfirmationRequired`. */
type OfferedHostKey = {
  host: string;
  port: number;
  algorithm: string;
  fingerprint: string;
  host_key: string;
  pinned_fingerprints?: string[];
};

type HostKeyChallenge = {
  kind: "host_key_unknown" | "host_key_mismatch";
  key: OfferedHostKey;
  /** The request to repeat, with `config.host_key` set, once trusted. */
  payload: Record<string, unknown> & { config: Record<string, unknown> };
};

function hostKeyChallenge(
  error: unknown,
  payload: HostKeyChallenge["payload"],
): HostKeyChallenge | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const code = error.body?.error_code;
  const key = error.body?.host_key as OfferedHostKey | undefined;
  if ((code !== "host_key_unknown" && code !== "host_key_mismatch") || !key?.host_key) {
    return null;
  }
  return { kind: code, key, payload };
}

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
  existingNames = [],
  onCreated,
  onCancel,
}: {
  providerTypes: ProviderType[];
  /** The user's current connection names, to suggest an unused one. */
  existingNames?: string[];
  onCreated: (connection: ProviderConnection) => void;
  onCancel?: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [challenge, setChallenge] = useState<HostKeyChallenge | null>(null);

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
    await submit({
      provider_type: selected.id,
      name: form.get("connection_name"),
      config,
      import_existing: form.get("import_existing") === "on",
      mirror_structure: form.get("mirror_structure") === "on",
    });
  }

  async function submit(payload: HostKeyChallenge["payload"]) {
    setSaving(true);
    setError("");
    try {
      const connection = await api<ProviderConnection>("/providers", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setChallenge(null);
      onCreated(connection);
    } catch (requestError) {
      const nextChallenge = hostKeyChallenge(requestError, payload);
      if (nextChallenge) {
        setChallenge(nextChallenge);
        return;
      }
      setChallenge(null);
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not add storage.",
      );
    } finally {
      setSaving(false);
    }
  }

  /** Pin the offered key and connect again -- the user's "yes" to an SSH
   * client's "Are you sure you want to continue connecting?". */
  function trustHostKey() {
    if (!challenge) return;
    void submit({
      ...challenge.payload,
      config: { ...challenge.payload.config, host_key: challenge.key.host_key },
    });
  }

  if (!selected) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {providerTypes.map((provider) => {
          const unavailable = provider.available === false;
          return (
            <button
              aria-label={
                unavailable
                  ? `${provider.name} unavailable: ${provider.unavailable_reason ?? "Not configured"}`
                  : undefined
              }
              className="rounded-xl border p-4 text-left transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
              disabled={unavailable}
              key={provider.id}
              onClick={() => setSelectedId(provider.id)}
              type="button"
            >
              <div className="flex items-start justify-between gap-3">
                <StorageProviderIcon providerType={provider.id} size="md" />
                <div className="flex items-center gap-2">
                  {unavailable ? <LockKeyhole aria-hidden="true" size={14} /> : null}
                  <Badge variant={statusVariant[provider.status] ?? "secondary"}>
                    {unavailable ? "Locked" : provider.status}
                  </Badge>
                </div>
              </div>
              <h3 className="mt-4 text-sm font-semibold">{provider.name}</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {provider.description}
              </p>
              {unavailable && provider.unavailable_reason ? (
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  {provider.unavailable_reason}
                </p>
              ) : null}
            </button>
          );
        })}
      </div>
    );
  }

  if (selected.connect_flow === "oauth") {
    return (
      <OAuthProviderForm
        existingNames={existingNames}
        onBack={() => setSelectedId(null)}
        onCancel={onCancel}
        onCreated={onCreated}
        provider={selected}
      />
    );
  }

  if (selected.connect_flow === "session") {
    return (
      <TelegramLoginForm
        existingNames={existingNames}
        onBack={() => setSelectedId(null)}
        onCancel={onCancel}
        onCreated={onCreated}
        provider={selected}
      />
    );
  }

  if (challenge) {
    return (
      <HostKeyPrompt
        challenge={challenge}
        error={error}
        onCancel={() => setChallenge(null)}
        onTrust={trustHostKey}
        saving={saving}
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
        <ConnectionNameField
          existingNames={existingNames}
          key={selected.id}
          providerName={selected.name}
        />
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
      <div className="sticky bottom-0 z-10 flex justify-end gap-3 border-t bg-popover pt-4">
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

/** First-connect / changed-key confirmation for SFTP, modeled on OpenSSH
 * and WinSCP: show the fingerprint, trust nothing until the user says so,
 * and make a *changed* key look dangerous rather than routine. */
function HostKeyPrompt({
  challenge,
  error,
  saving,
  onTrust,
  onCancel,
}: {
  challenge: HostKeyChallenge;
  error: string;
  saving: boolean;
  onTrust: () => void;
  onCancel: () => void;
}) {
  const { key, kind } = challenge;
  const changed = kind === "host_key_mismatch";
  const server = key.port === 22 ? key.host : `${key.host}:${key.port}`;
  const Icon = changed ? ShieldAlert : ShieldQuestion;

  return (
    <div className="space-y-4" role={changed ? "alert" : undefined}>
      <div className="flex items-start gap-3">
        <Icon
          aria-hidden="true"
          className={changed ? "mt-0.5 size-5 text-destructive" : "mt-0.5 size-5"}
        />
        <div className="space-y-1">
          <h3 className="text-lg font-semibold">
            {changed ? "The server's host key has changed" : "Is this the right server?"}
          </h3>
          <p className="text-sm text-muted-foreground">
            {changed
              ? `The key ${server} sent is not the one saved for it. Someone may be intercepting the connection, or the server was reinstalled. Do not continue unless the server's administrator confirms the new fingerprint.`
              : `This is the first connection to ${server}. Check that the fingerprint below matches the one the server's administrator gave you, or the output of ssh-keyscan on a trusted network.`}
          </p>
        </div>
      </div>
      <dl className="space-y-2 rounded-xl border p-4 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Key type</dt>
          <dd>{key.algorithm}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">
            {changed ? "New fingerprint" : "Fingerprint"}
          </dt>
          <dd className="break-all font-mono" data-testid="host-key-fingerprint">
            {key.fingerprint}
          </dd>
        </div>
        {changed && key.pinned_fingerprints?.length ? (
          <div>
            <dt className="text-xs text-muted-foreground">Saved fingerprint</dt>
            {key.pinned_fingerprints.map((fingerprint) => (
              <dd className="break-all font-mono" key={fingerprint}>
                {fingerprint}
              </dd>
            ))}
          </div>
        ) : null}
      </dl>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end gap-3 border-t pt-4">
        <Button autoFocus onClick={onCancel} type="button" variant="outline">
          Cancel
        </Button>
        <Button
          disabled={saving}
          onClick={onTrust}
          type="button"
          variant={changed ? "destructive" : "default"}
        >
          {saving ? "Connecting…" : changed ? "Replace key and connect" : "Trust and connect"}
        </Button>
      </div>
    </div>
  );
}
