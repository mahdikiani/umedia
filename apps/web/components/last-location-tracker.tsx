"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { rememberLastLocation } from "@/lib/last-location";

/** Persist the current dashboard URL so `/` and post-login land here next. */
export function LastLocationTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    const search = searchParams.toString();
    rememberLastLocation(search ? `${pathname}?${search}` : pathname);
  }, [pathname, searchParams]);

  return null;
}
