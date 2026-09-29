"use client";

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";

import { useLocale } from "@/components/locale-provider";
import { useCopy } from "@/lib/copy";
import type { NotificationFilter } from "@/lib/notification-filter";

type NotificationFilterTabsProps = {
  value: NotificationFilter;
  onValueChange: (value: NotificationFilter) => void;
  children: React.ReactNode;
};

const filters: NotificationFilter[] = ["unread", "all", "read"];

export function NotificationFilterTabs({
  value,
  onValueChange,
  children,
}: NotificationFilterTabsProps) {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const labels: Record<NotificationFilter, string> = {
    all: text.notificationsAll,
    unread: text.notificationsUnread,
    read: text.notificationsRead,
  };

  return (
    <TabsPrimitive.Root
      className="flex min-w-0 flex-col"
      onValueChange={(next) => {
        if (next === "all" || next === "unread" || next === "read") {
          onValueChange(next);
        }
      }}
      value={value}
    >
      <TabsPrimitive.List className="flex w-full items-center gap-1 border-b px-2">
        {filters.map((filter) => (
          <TabsPrimitive.Tab
            className="relative flex-1 px-2 py-2 text-xs text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring data-[active]:font-medium data-[active]:text-foreground data-[active]:after:absolute data-[active]:after:inset-x-2 data-[active]:after:-bottom-px data-[active]:after:h-0.5 data-[active]:after:rounded-full data-[active]:after:bg-primary"
            key={filter}
            value={filter}
          >
            {labels[filter]}
          </TabsPrimitive.Tab>
        ))}
      </TabsPrimitive.List>
      <TabsPrimitive.Panel className="min-w-0 outline-none" value={value}>
        {children}
      </TabsPrimitive.Panel>
    </TabsPrimitive.Root>
  );
}
