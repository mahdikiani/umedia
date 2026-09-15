"use client";

import { Search } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useLocale } from "@/components/locale-provider";
import { Input } from "@/components/ui/input";
import { useCopy } from "@/lib/copy";

const SEARCH_DEBOUNCE_MS = 200;

function DashboardSearchField() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { locale } = useLocale();
  const text = useCopy(locale);

  const mode = pathname.startsWith("/files")
    || pathname.startsWith("/starred")
    || pathname.startsWith("/home")
    ? "files"
    : pathname.startsWith("/storage")
      ? "storage"
      : null;

  const urlQuery = searchParams.get("q") ?? "";
  const [value, setValue] = useState(urlQuery);

  useEffect(() => {
    // Keep the field in sync when browser history / folder links change `q`.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValue(urlQuery);
  }, [urlQuery, pathname]);

  useEffect(() => {
    if (!mode) return;
    const timeout = window.setTimeout(() => {
      const next = value.trim();
      const current = (searchParams.get("q") ?? "").trim();
      if (next === current) return;

      // Searching from Starred jumps to the library-wide Files results.
      // An empty box must stay on /starred — otherwise the debounce
      // bounces every visit straight back to /files.
      if (pathname.startsWith("/starred") || pathname.startsWith("/home")) {
        if (!next) return;
        router.replace(`/files?q=${encodeURIComponent(next)}`);
        return;
      }

      const params = new URLSearchParams(searchParams.toString());
      if (next) params.set("q", next);
      else params.delete("q");
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timeout);
  }, [value, mode, pathname, router, searchParams]);

  if (!mode) return null;

  return (
    <div className="mx-2 flex min-w-0 flex-1 justify-center">
      <label className="relative w-full max-w-xl">
        <Search
          aria-hidden
          className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          aria-label={mode === "files" ? text.searchFiles : text.searchStorage}
          className="h-9 pe-3 ps-9"
          onChange={(event) => setValue(event.target.value)}
          placeholder={
            mode === "files" ? text.searchFilesPlaceholder : text.searchStoragePlaceholder
          }
          type="search"
          value={value}
        />
      </label>
    </div>
  );
}

export function DashboardSearch() {
  return (
    <Suspense fallback={<div className="mx-2 min-h-9 flex-1" />}>
      <DashboardSearchField />
    </Suspense>
  );
}
