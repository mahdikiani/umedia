"use client";

import { FolderOpen, KeyRound } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { GoogleOidcLogin } from "@/components/google-oidc-login";
import { useLocale } from "@/components/locale-provider";
import { LocaleToggle } from "@/components/locale-toggle";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api, type AuthState } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { readLastLocation } from "@/lib/last-location";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { locale } = useLocale();
  const text = useCopy(locale);

  const [state, setState] = useState<AuthState | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState(
    () => searchParams.get("oidc_error") ?? "",
  );
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api<AuthState>("/auth/state")
      .then((next) => {
        if (next.authenticated) {
          router.replace(readLastLocation());
        } else {
          setState(next);
        }
      })
      .catch(() => setState({ configured: true, authenticated: false }));
  }, [router]);

  if (!state) {
    return (
      <main className="grid min-h-screen place-items-center bg-background">
        <div className="size-5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground" />
      </main>
    );
  }

  const isSetup = !state.configured;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (isSetup && password !== confirmation) {
      setError("Passwords do not match");
      return;
    }
    setSubmitting(true);
    try {
      await api(isSetup ? "/auth/setup" : "/auth/sessions", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      router.replace(readLastLocation());
    } catch (requestError) {
      setError(
        requestError instanceof ApiError ? requestError.message : "Unable to continue",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-muted/40 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="grid size-9 place-items-center rounded-lg bg-foreground text-background">
              <FolderOpen size={18} strokeWidth={2.2} />
            </div>
            <div>
              <div className="text-sm font-semibold">{text.brand}</div>
              <div className="text-xs text-muted-foreground">{text.brandTagline}</div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <LocaleToggle label="long" variant="outline" />
            <ThemeToggle />
          </div>
        </div>

        <Card className="border border-border/80 shadow-md dark:shadow-lg dark:shadow-black/25">
          <CardHeader>
            <div className="mb-2 grid size-10 place-items-center rounded-full bg-muted">
              <KeyRound size={18} />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">
              {isSetup ? text.createTitle : text.loginTitle}
            </h1>
            <p className="text-sm text-muted-foreground">
              {isSetup ? text.createBody : text.loginBody}
            </p>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-1.5">
                <Label htmlFor="email">{text.email}</Label>
                <Input
                  autoComplete="email"
                  id="email"
                  onChange={(event) => setEmail(event.target.value)}
                  required
                  type="email"
                  value={email}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">{text.password}</Label>
                <Input
                  autoComplete={isSetup ? "new-password" : "current-password"}
                  autoFocus
                  id="password"
                  minLength={isSetup ? 12 : 1}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
                {isSetup && (
                  <p className="text-xs text-muted-foreground">
                    Use at least 12 characters.
                  </p>
                )}
              </div>
              {isSetup && (
                <div className="space-y-1.5">
                  <Label htmlFor="confirmation">{text.confirm}</Label>
                  <Input
                    autoComplete="new-password"
                    id="confirmation"
                    minLength={12}
                    onChange={(event) => setConfirmation(event.target.value)}
                    required
                    type="password"
                    value={confirmation}
                  />
                </div>
              )}
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button className="w-full" disabled={submitting} type="submit">
                {isSetup ? text.createAction : text.loginAction}
              </Button>
            </form>
            {!isSetup && state.oidc_providers?.includes("google") && (
              <div className="mt-4">
                <GoogleOidcLogin
                  onSuccess={() => router.replace(readLastLocation())}
                />
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
