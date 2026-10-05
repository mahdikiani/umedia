"use client";

import { useState } from "react";

import { ConnectionNameField } from "@/components/connection-name-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  api,
  ApiError,
  type ProviderConnection,
  type ProviderType,
  type TelegramLoginResponse,
} from "@/lib/api";

type Step = "details" | "code" | "password";

export function TelegramLoginForm({
  provider,
  onBack,
  onCancel,
  onCreated,
  existingNames = [],
}: {
  provider: ProviderType;
  existingNames?: string[];
  onBack: () => void;
  onCancel?: () => void;
  onCreated: (connection: ProviderConnection) => void;
}) {
  const [step, setStep] = useState<Step>("details");
  const [loginId, setLoginId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function cancelLogin() {
    if (!loginId) return;
    await api<void>(`/providers/telegram/login/${loginId}`, { method: "DELETE" }).catch(
      () => undefined,
    );
    setLoginId("");
  }

  async function leave(onLeave: () => void) {
    setBusy(true);
    await cancelLogin();
    onLeave();
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      let response: TelegramLoginResponse;
      if (step === "details") {
        response = await api<TelegramLoginResponse>("/providers/telegram/login/start", {
          method: "POST",
          body: JSON.stringify({
            name: form.get("connection_name"),
            phone: form.get("phone"),
            channel_ref: form.get("channel_ref"),
            import_existing: form.get("import_existing") === "on",
          }),
        });
        setLoginId(response.login_id ?? "");
      } else {
        response = await api<TelegramLoginResponse>(
          `/providers/telegram/login/${loginId}/${step}`,
          {
            method: "POST",
            body: JSON.stringify({ value: form.get("value") }),
          },
        );
      }

      if (response.step === "complete" && response.connection) {
        onCreated(response.connection);
        return;
      }
      setStep(response.step === "password" ? "password" : "code");
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not sign in to Telegram.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="space-y-4" onSubmit={(event) => void submit(event)}>
      <button
        className="text-xs font-medium text-muted-foreground"
        onClick={() => void leave(onBack)}
        type="button"
      >
        ← All providers
      </button>
      <div>
        <h3 className="text-lg font-semibold">{provider.name}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Sign in with your Telegram account. Your code and two-step verification
          password are sent only to this server.
        </p>
      </div>

      {step === "details" ? (
        <>
          <ConnectionNameField
            existingNames={existingNames}
            providerName={provider.name}
          />
          <div className="space-y-1.5">
            <Label htmlFor="telegram_phone">Phone number</Label>
            <Input autoComplete="tel" id="telegram_phone" name="phone" placeholder="+1234567890" required type="tel" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="channel_ref">Channel name or @username</Label>
            <Input id="channel_ref" name="channel_ref" placeholder="@channelname or Channel name" required />
            <p className="text-xs leading-5 text-muted-foreground">
              Enter the channel name or public @username. For a channel name, your account must have joined it. Your account must be an administrator.
            </p>
          </div>
          <div className="flex items-start gap-3 rounded-xl border p-4">
            <input className="mt-0.5 size-4 accent-primary" id="import_existing" name="import_existing" type="checkbox" />
            <div className="space-y-1">
              <Label htmlFor="import_existing">Import existing files</Label>
              <p className="text-xs leading-5 text-muted-foreground">
                Add documents already in this channel to your UMedia library.
              </p>
            </div>
          </div>
        </>
      ) : (
        <div className="space-y-1.5">
          <Label htmlFor="telegram_login_value">
            {step === "code" ? "Telegram login code" : "Two-step verification password"}
          </Label>
          <Input
            autoComplete={step === "code" ? "one-time-code" : "current-password"}
            autoFocus
            id="telegram_login_value"
            inputMode={step === "code" ? "numeric" : undefined}
            name="value"
            required
            type={step === "password" ? "password" : "text"}
          />
          {step === "code" && (
            <p className="text-xs leading-5 text-muted-foreground">
              Enter the code Telegram sent in the app or by SMS.
            </p>
          )}
        </div>
      )}

      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
      <div className="sticky bottom-0 z-10 flex justify-end gap-3 border-t bg-popover pt-4">
        {onCancel && (
          <Button onClick={() => void leave(onCancel)} type="button" variant="outline">
            Cancel
          </Button>
        )}
        <Button disabled={busy} type="submit">
          {busy
            ? step === "details" ? "Sending code…" : "Verifying…"
            : step === "details" ? "Send login code" : step === "code" ? "Verify code" : "Verify password"}
        </Button>
      </div>
    </form>
  );
}
