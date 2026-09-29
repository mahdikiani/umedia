"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useLocale } from "@/components/locale-provider";
import { listNotifications, markNotificationRead, type OperationNotification } from "@/lib/api";
import { useCopy } from "@/lib/copy";

export default function NotificationsPage() {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [notifications, setNotifications] = useState<OperationNotification[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setNotifications(await listNotifications());
      setError(null);
    } catch {
      setError(text.notificationsLoadFailed);
    }
  }, [text.notificationsLoadFailed]);

  useEffect(() => {
    let active = true;
    listNotifications()
      .then((items) => {
        if (active) setNotifications(items);
      })
      .catch(() => {
        if (active) setError(text.notificationsLoadFailed);
      });
    return () => { active = false; };
  }, [text.notificationsLoadFailed]);

  async function markRead(uid: string) {
    try {
      await markNotificationRead(uid);
      window.dispatchEvent(new Event("umedia-notifications-updated"));
      await refresh();
    } catch {
      setError(text.notificationsLoadFailed);
    }
  }

  return (
    <section className="mx-auto max-w-3xl space-y-5">
      <h1 className="text-2xl font-semibold">{text.notificationsTitle}</h1>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {notifications.length === 0 && !error ? (
        <div className="rounded-xl border p-8 text-center text-muted-foreground">
          <Bell className="mx-auto mb-3 size-6" />
          {text.notificationsEmpty}
        </div>
      ) : (
        <div className="space-y-3">
          {notifications.map((notification) => (
            <article className={`rounded-xl border p-4 ${notification.read_at ? "opacity-70" : "border-destructive/40"}`} key={notification.uid}>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 space-y-1">
                  <h2 className="font-medium">{notification.item_name} · {notification.operation === "upload" ? text.operationUpload : notification.operation === "copy" ? text.operationCopy : notification.operation === "move" ? text.operationMove : text.operationSync} {text.operationFailed}</h2>
                  <p className="whitespace-pre-wrap break-words text-sm text-destructive">{notification.error}</p>
                  <time className="block text-xs text-muted-foreground" dateTime={notification.created_at}>
                    {new Intl.DateTimeFormat(locale === "fa" ? "fa-IR" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(notification.created_at))}
                  </time>
                </div>
                {!notification.read_at ? (
                  <Button onClick={() => void markRead(notification.uid)} size="sm" variant="outline">
                    <Check />{text.markAsRead}
                  </Button>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
