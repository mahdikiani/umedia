"use client";

import { FolderOpen } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AddStorageForm } from "@/components/add-storage-form";
import { useLocale } from "@/components/locale-provider";
import { LocaleToggle } from "@/components/locale-toggle";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, type AuthState, type ProviderConnection, type ProviderType } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { readLastLocation } from "@/lib/last-location";

/** The mandatory first step after setup/login: `(dashboard)/layout.tsx`
 * redirects here whenever an authenticated user has zero provider
 * connections, since every other screen (Files especially) is
 * meaningless without at least one -- rather than dropping them into an
 * empty sidebar shell with a buried "Add storage" button. This page
 * mirrors that redirect the other way: once a connection exists, it
 * sends them to their last dashboard page (fallback `/home`). */
export default function OnboardingPage() {
  const router = useRouter();
  const { locale } = useLocale();
  const text = useCopy(locale);

  const [ready, setReady] = useState(false);
  const [providerTypes, setProviderTypes] = useState<ProviderType[]>([]);

  useEffect(() => {
    api<AuthState>("/auth/state")
      .then((state) => {
        if (!state.authenticated) {
          router.replace("/login");
          return;
        }
        return Promise.all([
          api<ProviderType[]>("/provider-types"),
          api<ProviderConnection[]>("/providers"),
          Promise.resolve(state),
        ]).then(([types, connections, auth]) => {
          if (connections.length > 0) {
            router.replace(readLastLocation());
            return;
          }
          const admin = auth.user?.roles.includes("admin") ?? false;
          setProviderTypes(
            admin ? types : types.filter((item) => item.id !== "local"),
          );
          setReady(true);
        });
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  if (!ready) {
    return (
      <main className="grid min-h-screen place-items-center bg-background">
        <div className="size-5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground" />
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-background p-4 py-10">
      <div className="mx-auto max-w-2xl">
        <div className="mb-8 flex items-center justify-between">
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

        <h1 className="text-2xl font-semibold tracking-tight">
          Connect your first storage
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          UMedia needs at least one place to actually put files -- local disk, S3,
          Telegram, or another provider. You can add more later from Settings.
        </p>

        <div className="mt-8 rounded-xl border bg-card p-6">
          <AddStorageForm
            onCreated={() => router.replace(readLastLocation())}
            providerTypes={providerTypes}
          />
        </div>
      </div>
    </main>
  );
}
