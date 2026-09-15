"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  api,
  ApiError,
  type OidcCompleteResponse,
  type OidcStartResponse,
} from "@/lib/api";

function isLocalhostRedirect(redirectUri: string): boolean {
  try {
    const host = new URL(redirectUri).hostname;
    return host === "localhost" || host === "127.0.0.1";
  } catch {
    return true;
  }
}

/** Google identity OIDC login (`/auth/oidc/*`), not Drive storage OAuth. */
export function GoogleOidcLogin({
  onSuccess,
}: {
  onSuccess: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [oauth, setOauth] = useState<OidcStartResponse | null>(null);
  const [callback, setCallback] = useState("");

  async function startGoogleSignIn() {
    setBusy(true);
    setError("");
    try {
      const started = await api<OidcStartResponse>("/auth/oidc/start", {
        method: "POST",
        body: JSON.stringify({ provider: "google" }),
      });
      if (!isLocalhostRedirect(started.redirect_uri)) {
        window.location.assign(started.authorization_url);
        return;
      }
      setOauth(started);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not start Google sign-in.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function complete(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!oauth) return;
    setSaving(true);
    setError("");
    try {
      await api<OidcCompleteResponse>("/auth/oidc/complete", {
        method: "POST",
        body: JSON.stringify({
          provider: "google",
          callback,
          state: oauth.state,
        }),
      });
      onSuccess();
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not complete Google sign-in.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!oauth) {
    return (
      <div className="space-y-3">
        <div className="relative flex items-center gap-3">
          <div className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground">or</span>
          <div className="h-px flex-1 bg-border" />
        </div>
        <Button
          className="w-full"
          disabled={busy}
          onClick={() => void startGoogleSignIn()}
          type="button"
          variant="outline"
        >
          {busy ? "Redirecting to Google…" : "Continue with Google"}
        </Button>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
    );
  }

  return (
    <form className="space-y-3" onSubmit={(event) => void complete(event)}>
      <div className="relative flex items-center gap-3">
        <div className="h-px flex-1 bg-border" />
        <span className="text-xs text-muted-foreground">or</span>
        <div className="h-px flex-1 bg-border" />
      </div>
      <div className="space-y-3 rounded-xl border p-4">
        <div className="space-y-1">
          <p className="text-sm font-medium">Continue with Google</p>
          <p className="text-xs leading-5 text-muted-foreground">
            Open Google sign-in in a new tab, then paste whatever Google
            redirects to (full URL, query string, code, or token JSON).
          </p>
        </div>
        <div className="break-all rounded-lg bg-muted p-3 font-mono text-xs leading-5">
          {oauth.authorization_url}
        </div>
        <a
          className="inline-flex h-9 w-full items-center justify-center rounded-md bg-secondary px-4 text-sm font-medium text-secondary-foreground transition-colors hover:bg-secondary/80"
          href={oauth.authorization_url}
          rel="noopener noreferrer"
          target="_blank"
        >
          Open Google sign-in
        </a>
        <div className="space-y-1.5">
          <Label htmlFor="oidc_callback">
            Paste the redirect URL (or code / query params / token JSON)
          </Label>
          <textarea
            className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex min-h-24 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
            id="oidc_callback"
            onChange={(event) => setCallback(event.target.value)}
            required
            value={callback}
          />
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button
        className="w-full"
        disabled={saving || !callback.trim()}
        type="submit"
      >
        {saving ? "Signing in…" : "Complete Google sign-in"}
      </Button>
    </form>
  );
}
