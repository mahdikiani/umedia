"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

import { PlainErrorPage } from "@/components/plain-error-page";

// Replaces the root layout when it fails, so it must not depend on the theme,
// fonts or any provider: those may be what broke. The error goes to error
// tracking; the user only sees the plain page (tests/error-page.test.tsx).
export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
  reset?: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ margin: 0 }}>
        <PlainErrorPage />
      </body>
    </html>
  );
}
