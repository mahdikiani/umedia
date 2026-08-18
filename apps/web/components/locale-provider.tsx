"use client";

import { DirectionProvider } from "@base-ui/react/direction-provider";
import { createContext, useContext, useEffect, useState } from "react";

export type Locale = "en" | "fa";

type LocaleContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
};

const LocaleContext = createContext<LocaleContextValue | null>(null);

const STORAGE_KEY = "umedia_locale";

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>("en");

  // Two-pass on purpose: the server (and the client's first hydration
  // pass) always render "en"/ltr so they match, then this corrects from
  // localStorage once mounted -- reading it inside useState's initializer
  // would make that first client pass diverge from the server and trip a
  // hydration mismatch.
  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    // Syncing from an external store (localStorage) on mount, not a
    // cascading self-triggered update -- the one-time exception the rule
    // itself calls out ("Subscribe for updates from some external system,
    // calling setState ... when external state changes").
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored === "fa" || stored === "en") setLocale(stored);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "fa" ? "rtl" : "ltr";
    window.localStorage.setItem(STORAGE_KEY, locale);
  }, [locale]);

  return (
    <LocaleContext.Provider value={{ locale, setLocale }}>
      {/* Drives base-ui's own RTL-aware positioning (popovers, selects,
          dropdowns) -- separate from the `dir` attribute above, which only
          covers CSS logical properties/text direction. */}
      <DirectionProvider direction={locale === "fa" ? "rtl" : "ltr"}>
        {children}
      </DirectionProvider>
    </LocaleContext.Provider>
  );
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale must be used within LocaleProvider");
  return context;
}
