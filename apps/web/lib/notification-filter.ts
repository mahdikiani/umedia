import type { OperationNotification } from "@/lib/api";

export type NotificationFilter = "all" | "unread" | "read";

export function filterNotifications(
  notifications: OperationNotification[],
  filter: NotificationFilter,
): OperationNotification[] {
  if (filter === "unread") {
    return notifications.filter((notification) => !notification.read_at);
  }
  if (filter === "read") {
    return notifications.filter((notification) => Boolean(notification.read_at));
  }
  return notifications;
}
