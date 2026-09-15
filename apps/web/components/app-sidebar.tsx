"use client";

import {
  ChevronDown,
  File as FileIcon,
  FolderOpen,
  Database,
  Home,
  LogOut,
  Settings,
  Star,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { useLocale } from "@/components/locale-provider";
import { StorageProviderIcon } from "@/components/storage-provider-icon";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { api, type ProviderConnection, type VolumeStats } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

function storageProviderHref(providerId: string): string {
  return `/storage?provider=${encodeURIComponent(providerId)}`;
}

export function AppSidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { locale } = useLocale();
  const text = useCopy(locale);
  const side = locale === "fa" ? "right" : "left";
  const [stats, setStats] = useState<VolumeStats | null>(null);
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [storagesOpen, setStoragesOpen] = useState(true);
  const activeProviderId = searchParams.get("provider");

  useEffect(() => {
    api<VolumeStats>("/files/stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, [pathname]);

  useEffect(() => {
    api<ProviderConnection[]>("/providers")
      .then(setConnections)
      .catch(() => setConnections([]));
  }, []);

  useEffect(() => {
    if (pathname.startsWith("/storage")) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStoragesOpen(true);
    }
  }, [pathname]);

  function isProviderActive(uid: string): boolean {
    if (!pathname.startsWith("/storage")) return false;
    if (activeProviderId) return activeProviderId === uid;
    return connections[0]?.uid === uid;
  }

  async function logout() {
    await api("/auth/sessions/current", { method: "DELETE" });
    router.replace("/login");
  }

  return (
    <Sidebar collapsible="icon" side={side}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton render={<Link href="/home" />} size="lg">
              <div className="grid size-7 place-items-center rounded-md bg-foreground text-background">
                <FolderOpen size={15} strokeWidth={2.4} />
              </div>
              <div className="flex flex-col leading-none">
                <span className="font-semibold">{text.brand}</span>
                <span className="text-xs text-muted-foreground">
                  {text.brandTagline}
                </span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith("/home")}
                  render={<Link href="/home" />}
                  tooltip={text.navHome}
                >
                  <Home />
                  <span>{text.navHome}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith("/files")}
                  render={<Link href="/files" />}
                  tooltip={text.navFiles}
                >
                  <FileIcon />
                  <span>{text.navFiles}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarSeparator />
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith("/storage")}
                  onClick={() => setStoragesOpen((open) => !open)}
                  tooltip={text.navStorage}
                >
                  <Database />
                  <span>{text.navStorage}</span>
                  <ChevronDown
                    className={cn(
                      "ms-auto size-4 shrink-0 transition-transform duration-200",
                      !storagesOpen && "-rotate-90",
                    )}
                  />
                </SidebarMenuButton>
                {storagesOpen && connections.length > 0 && (
                  <SidebarMenuSub>
                    {connections.map((connection) => (
                      <SidebarMenuSubItem key={connection.uid}>
                        <SidebarMenuSubButton
                          isActive={isProviderActive(connection.uid)}
                          render={
                            <Link href={storageProviderHref(connection.uid)} />
                          }
                        >
                          <StorageProviderIcon
                            providerType={connection.provider_type}
                            size="sm"
                          />
                          <span>{connection.name}</span>
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    ))}
                  </SidebarMenuSub>
                )}
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarSeparator />
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith("/starred")}
                  render={<Link href="/starred" />}
                  tooltip={text.navStarred}
                >
                  <Star />
                  <span>{text.navStarred}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={pathname.startsWith("/trash")}
                  render={<Link href="/trash" />}
                  tooltip={text.navTrash}
                >
                  <Trash2 />
                  <span>{text.navTrash}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {stats && (
          <Card
            className="mx-2 mt-3 group-data-[collapsible=icon]:hidden"
            size="sm"
          >
            <CardHeader>
              <CardTitle>{text.usedSpace}</CardTitle>
              <CardDescription>
                {stats.file_count} {text.usedSpaceFiles}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-lg font-medium tabular-nums">
                {formatBytes(stats.used_bytes)}
              </p>
            </CardContent>
          </Card>
        )}
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={pathname.startsWith("/settings/storage")}
              render={<Link href="/settings/storage" />}
              tooltip={text.navStorageSettings}
            >
              <Database />
              <span>{text.navStorageSettings}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={
                pathname.startsWith("/settings") &&
                !pathname.startsWith("/settings/storage")
              }
              render={<Link href="/settings" />}
              tooltip={text.navSettings}
            >
              <Settings />
              <span>{text.navSettings}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={() => void logout()} tooltip={text.logOut}>
              <LogOut />
              <span>{text.logOut}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
