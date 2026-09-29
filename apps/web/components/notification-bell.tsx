"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bell } from "lucide-react";

import { useLocale } from "@/components/locale-provider";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { listNotifications, type OperationNotification } from "@/lib/api";
import { useCopy } from "@/lib/copy";

function operationLabel(operation: string, text: ReturnType<typeof useCopy>) {
  if (operation === "upload") return text.operationUpload;
  if (operation === "copy") return text.operationCopy;
  if (operation === "move") return text.operationMove;
  return text.operationSync;
}

export function NotificationBell() {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [notifications, setNotifications] = useState<OperationNotification[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setNotifications(await listNotifications());
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void refresh(), 0);
    window.addEventListener("umedia-notifications-updated", refresh);
    return () => {
      window.clearTimeout(initialLoad);
      window.removeEventListener("umedia-notifications-updated", refresh);
    };
  }, [refresh]);

  const unreadCount = notifications.filter((notification) => !notification.read_at).length;

  return (
    <DropdownMenu onOpenChange={(open) => { if (open) void refresh(); }}>
      <DropdownMenuTrigger
        render={<Button aria-label={text.navNotifications} className="relative" size="icon" variant="ghost" />}
      >
        <Bell />
        {unreadCount > 0 ? (
          <span className="absolute -end-0.5 -top-0.5 grid min-h-4 min-w-4 place-items-center rounded-full bg-destructive px-1 text-[10px] leading-none text-destructive-foreground">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={locale === "fa" ? "start" : "end"}
        className="w-[min(22rem,calc(100vw-2rem))] p-0"
      >
        <div className="border-b px-3 py-2.5">
          <p className="text-sm font-semibold">{text.notificationsTitle}</p>
        </div>
        {loadFailed ? (
          <p className="px-3 py-5 text-center text-sm text-destructive">{text.notificationsLoadFailed}</p>
        ) : notifications.length === 0 ? (
          <p className="px-3 py-5 text-center text-sm text-muted-foreground">{text.notificationsEmpty}</p>
        ) : (
          <div className="max-h-80 overflow-y-auto">
            {notifications.slice(0, 8).map((notification) => (
              <Link
                className="block border-b px-3 py-2.5 transition-colors last:border-0 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                href="/notifications"
                key={notification.uid}
              >
                <div className="flex items-start gap-2">
                  {!notification.read_at ? <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" /> : null}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {notification.item_name} · {operationLabel(notification.operation, text)} {text.operationFailed}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{notification.error}</p>
                    <time className="mt-1 block text-[11px] text-muted-foreground" dateTime={notification.created_at}>
                      {new Intl.DateTimeFormat(locale === "fa" ? "fa-IR" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(notification.created_at))}
                    </time>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
        <Link
          className="block border-t px-3 py-2.5 text-center text-sm font-medium text-primary hover:bg-accent"
          href="/notifications"
        >
          {text.viewNotifications}
        </Link>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
