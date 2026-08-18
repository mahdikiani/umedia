"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { api, type AuthState } from "@/lib/api";

/** Sends the browser straight to `/login` or `/home` -- everything else
 * lives under those two route trees now (see `app/login/page.tsx` and
 * `app/(dashboard)/`), this page never renders real content itself. */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    api<AuthState>("/auth/state")
      .then((state) => router.replace(state.authenticated ? "/home" : "/login"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <main className="grid min-h-screen place-items-center bg-background">
      <div className="size-5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground" />
    </main>
  );
}
