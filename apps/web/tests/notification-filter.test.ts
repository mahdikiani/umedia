import { describe, expect, it } from "vitest";

import type { OperationNotification } from "@/lib/api";
import { filterNotifications } from "@/lib/notification-filter";

const notifications: OperationNotification[] = [
  {
    uid: "unread-1",
    operation: "upload",
    item_name: "photo.jpg",
    error: "Upload failed",
    read_at: null,
    created_at: "2026-09-28T10:00:00Z",
  },
  {
    uid: "read-1",
    operation: "copy",
    item_name: "report.pdf",
    error: "Copy failed",
    read_at: "2026-09-28T11:00:00Z",
    created_at: "2026-09-28T09:00:00Z",
  },
];

describe("notification filters", () => {
  it("shows all notifications", () => {
    expect(filterNotifications(notifications, "all")).toEqual(notifications);
  });

  it("shows only unread notifications", () => {
    expect(filterNotifications(notifications, "unread").map(({ uid }) => uid)).toEqual(["unread-1"]);
  });

  it("shows only read notifications", () => {
    expect(filterNotifications(notifications, "read").map(({ uid }) => uid)).toEqual(["read-1"]);
  });
});
