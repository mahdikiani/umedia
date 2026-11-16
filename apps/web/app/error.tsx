"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

import { PlainErrorPage } from "@/components/plain-error-page";

// A page or layout below the root failed. The error goes to error tracking;
// the user only sees the plain page (tests/error-page.test.tsx).
export default function AppError({
  error,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return <PlainErrorPage />;
}
