"use client";

// The page a user sees when rendering fails. It never shows the error text,
// stack or digest. It uses only inline styles and no theme, provider or UI
// kit, so it still renders when the theme or a provider is what broke
// (tests/error-page.test.tsx).

import { useSyncExternalStore } from "react";

export const SUPPORT_URL = "mailto:mahdikiany@gmail.com";

const COPY = {
  en: {
    title: "Something went wrong.",
    text: "Reload the page. If it keeps happening, go to the home page or contact support.",
    reload: "Reload",
    home: "Home",
    support: "Support",
  },
  fa: {
    title: "مشکلی پیش آمد.",
    text: "صفحه را دوباره بارگیری کنید. اگر مشکل ماند، به صفحهٔ اصلی بروید یا به پشتیبانی پیام بدهید.",
    reload: "بارگیری دوباره",
    home: "صفحهٔ اصلی",
    support: "پشتیبانی",
  },
} as const;

type Lang = keyof typeof COPY;

// Same key the locale provider writes; read directly because the provider
// may be what failed. Any storage error falls back to English.
function readLang(): Lang {
  try {
    return window.localStorage.getItem("umedia_locale") === "fa" ? "fa" : "en";
  } catch {
    return "en";
  }
}

const subscribe = () => () => {};

const styles = {
  root: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    boxSizing: "border-box",
    fontFamily: "Vazirmatn Variable, system-ui, sans-serif",
    background: "#ffffff",
    color: "#1c252e",
    textAlign: "center",
  },
  box: { maxWidth: 420 },
  title: { fontSize: 22, fontWeight: 700, margin: "0 0 12px" },
  text: { fontSize: 15, lineHeight: 1.8, color: "#637381", margin: "0 0 24px" },
  actions: {
    display: "flex",
    flexWrap: "wrap",
    gap: 12,
    justifyContent: "center",
  },
  primary: {
    padding: "10px 20px",
    border: "none",
    borderRadius: 8,
    background: "#1c252e",
    color: "#ffffff",
    fontSize: 15,
    fontFamily: "inherit",
    cursor: "pointer",
  },
  link: {
    padding: "10px 20px",
    border: "1px solid #c4cdd5",
    borderRadius: 8,
    color: "#1c252e",
    fontSize: 15,
    textDecoration: "none",
  },
} satisfies Record<string, React.CSSProperties>;

function reload() {
  window.location.reload();
}

export function PlainErrorPage() {
  const lang = useSyncExternalStore<Lang>(subscribe, readLang, () => "en");
  const copy = COPY[lang];
  return (
    <main
      dir={lang === "fa" ? "rtl" : "ltr"}
      lang={lang}
      style={styles.root}
    >
      <div style={styles.box}>
        <h1 style={styles.title}>{copy.title}</h1>
        <p style={styles.text}>{copy.text}</p>
        <div style={styles.actions}>
          <button type="button" onClick={reload} style={styles.primary}>
            {copy.reload}
          </button>
          {/* A full page load, not next/link: the client router may be what broke. */}
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a href="/" style={styles.link}>
            {copy.home}
          </a>
          <a href={SUPPORT_URL} style={styles.link}>
            {copy.support}
          </a>
        </div>
      </div>
    </main>
  );
}
