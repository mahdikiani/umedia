"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { NotificationFilterTabs } from "@/components/notification-filter-tabs";
import { useLocale } from "@/components/locale-provider";
import { listNotifications, markNotificationRead, type OperationNotification } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { filterNotifications, type NotificationFilter } from "@/lib/notification-filter";

export default function NotificationsPage() {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [notifications, setNotifications] = useState<OperationNotification[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<NotificationFilter>("all");

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

  const visibleNotifications = filterNotifications(notifications, filter);
  const emptyMessage = filter === "unread"
    ? text.notificationsUnreadEmpty
    : filter === "read"
      ? text.notificationsReadEmpty
      : text.notificationsEmpty;

  return (
    <section className="mx-auto max-w-3xl space-y-5">
      <h1 className="text-2xl font-semibold">{text.notificationsTitle}</h1>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <div className="overflow-hidden rounded-xl border">
        <NotificationFilterTabs onValueChange={setFilter} value={filter}>
          {visibleNotifications.length === 0 && !error ? (
            <div className="p-8 text-center text-muted-foreground">
              <Bell className="mx-auto mb-3 size-6" />
              {emptyMessage}
            </div>
          ) : (
            <div className="space-y-3 p-3">
              {visibleNotifications.map((notification) => (
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
        </NotificationFilterTabs>
      </div>
    </section>
  );
}
