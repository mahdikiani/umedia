"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { api, type AuthState } from "@/lib/api";
import { readLastLocation } from "@/lib/last-location";

/** Sends the browser straight to `/login` or the last dashboard page
 * the user visited (fallback `/home`). This page never renders content. */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    api<AuthState>("/auth/state")
      .then((state) =>
        router.replace(state.authenticated ? readLastLocation() : "/login"),
      )
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <main className="grid min-h-screen place-items-center bg-background">
      <div className="size-5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground" />
    </main>
  );
}
