"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { toast } from "sonner";

import { useLocale } from "@/components/locale-provider";
import { listNotifications } from "@/lib/api";
import { useCopy } from "@/lib/copy";

const POLL_MS = 5_000;

export function NotificationToastWatcher() {
  const router = useRouter();
  const { locale } = useLocale();
  const text = useCopy(locale);

  useEffect(() => {
    let active = true;
    let initialized = false;
    const seen = new Set<string>();

    const poll = async () => {
      try {
        const notifications = await listNotifications();
        if (!active) return;
        if (!initialized) {
          notifications.forEach((notification) => seen.add(notification.uid));
          initialized = true;
          return;
        }

        for (const notification of [...notifications].reverse()) {
          if (seen.has(notification.uid)) continue;
          seen.add(notification.uid);
          const operation =
            notification.operation === "upload"
              ? text.operationUpload
              : notification.operation === "copy"
                ? text.operationCopy
                : notification.operation === "move"
                  ? text.operationMove
                  : text.operationSync;
          toast.error(`${operation} ${text.operationFailed}: ${notification.item_name}`, {
            description: notification.error,
            duration: 12_000,
            action: {
              label: text.viewNotifications,
              onClick: () => router.push("/notifications"),
            },
          });
        }
      } catch {}
    };

    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [router, text]);

  return null;
}
