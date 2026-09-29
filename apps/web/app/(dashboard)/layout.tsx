"use client";

import { useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { DashboardSearch } from "@/components/dashboard-search";
import { LastLocationTracker } from "@/components/last-location-tracker";
import { NotificationToastWatcher } from "@/components/notification-toast-watcher";
import { NotificationBell } from "@/components/notification-bell";
import { useLocale } from "@/components/locale-provider";
import { LocaleToggle } from "@/components/locale-toggle";
import { ThemeToggle } from "@/components/theme-toggle";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { api, type AuthState, type ProviderConnection } from "@/lib/api";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const router = useRouter();
  const { locale } = useLocale();
  // `null` = still checking, `false` = checked and blocked (redirecting
  // away, render nothing further), `true` = clear to show the shell.
  const [ready, setReady] = useState<boolean | null>(null);

  useEffect(() => {
    api<AuthState>("/auth/state")
      .then((state) => {
        if (!state.authenticated) {
          router.replace("/login");
          setReady(false);
          return;
        }
        // Every dashboard page assumes at least one provider connection
        // exists (Files especially is meaningless without one) -- gate
        // the whole shell on that here, once, rather than each page
        // separately either duplicating this check or rendering an empty
        // "no storage" state that's really just onboarding wearing a
        // sidebar. `/onboarding` mirrors this check the other way,
        // sending a user who already has a connection on to their last page.
        return api<ProviderConnection[]>("/providers").then(
          (connections) => {
            if (connections.length === 0) {
              router.replace("/onboarding");
              setReady(false);
            } else {
              setReady(true);
            }
          },
        );
      })
      .catch(() => {
        router.replace("/login");
        setReady(false);
      });
  }, [router]);

  if (!ready) {
    return (
      <main className="grid min-h-screen place-items-center bg-background">
        <div className="size-5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground" />
      </main>
    );
  }

  return (
    <SidebarProvider>
      <Suspense fallback={null}>
        <AppSidebar />
        <LastLocationTracker />
        <NotificationToastWatcher />
      </Suspense>
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger />
          {locale === "fa" ? <NotificationBell /> : null}
          <Separator className="h-4" orientation="vertical" />
          <DashboardSearch />
          <div className="ms-auto flex items-center gap-1">
            <LocaleToggle />
            <ThemeToggle />
            {locale !== "fa" ? <NotificationBell /> : null}
          </div>
        </header>
        <div className="flex-1 p-4 md:p-6">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
