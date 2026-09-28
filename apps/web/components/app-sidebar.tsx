"use client";

import {
  ChevronRight,
  ChevronDown,
  Database,
  File as FileIcon,
  Folder,
  FolderOpen,
  FileText,
  Home,
  LogOut,
  ShieldCheck,
  Settings,
  Star,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useLocale } from "@/components/locale-provider";
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
import { api, LIST_PAGE_SIZE, type MediaFileItem, type Page, type VolumeStats } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { formatBytes } from "@/lib/format";

function folderHref(uid: string | null): string {
  return uid ? `/files?folder=${encodeURIComponent(uid)}` : "/files";
}

export function AppSidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { locale } = useLocale();
  const text = useCopy(locale);
  const side = locale === "fa" ? "right" : "left";
  const [stats, setStats] = useState<VolumeStats | null>(null);
  const [foldersByParent, setFoldersByParent] = useState<Record<string, MediaFileItem[]>>({});
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [loadingFolders, setLoadingFolders] = useState<Set<string>>(new Set());
  const loadedParents = useRef(new Set<string>());
  const pendingLoads = useRef(new Map<string, Promise<void>>());
  const currentFolderId = pathname.startsWith("/files") ? searchParams.get("folder") : null;

  useEffect(() => {
    api<VolumeStats>("/files/stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, [pathname]);

  const loadChildren = useCallback((parentId: string | null) => {
    const key = parentId ?? "root";
    if (loadedParents.current.has(key)) return Promise.resolve();
    const existing = pendingLoads.current.get(key);
    if (existing) return existing;
    setLoadingFolders((current) => new Set(current).add(key));
    const params = new URLSearchParams({ sort: "name", order: "asc", limit: String(LIST_PAGE_SIZE), offset: "0" });
    if (parentId) params.set("parent_id", parentId);
    const pending = (async () => {
      const items: MediaFileItem[] = [];
      let offset = 0;
      let hasMore = true;
      while (hasMore) {
        params.set("offset", String(offset));
        const page = await api<Page<MediaFileItem>>(`/files?${params.toString()}`);
        items.push(...page.items);
        hasMore = page.has_more;
        offset += page.items.length;
        if (page.items.length === 0) break;
      }
      setFoldersByParent((current) => ({
        ...current,
        [key]: items.filter((item) => item.type === "folder"),
      }));
      loadedParents.current.add(key);
    })()
      .catch(() => undefined)
      .finally(() => {
        pendingLoads.current.delete(key);
        setLoadingFolders((current) => {
          const next = new Set(current);
          next.delete(key);
          return next;
        });
      });
    pendingLoads.current.set(key, pending);
    return pending;
  }, []);

  useEffect(() => {
    if (!pathname.startsWith("/files")) return;
    void loadChildren(null);
  }, [loadChildren, pathname]);

  useEffect(() => {
    if (!currentFolderId) return;
    let cancelled = false;
    async function expandCurrentPath() {
      const chain: MediaFileItem[] = [];
      let uid: string | null = currentFolderId;
      const seen = new Set<string>();
      while (uid && !seen.has(uid)) {
        seen.add(uid);
        try {
          const folder = await api<MediaFileItem>(`/files/${encodeURIComponent(uid)}`);
          if (folder.type !== "folder") break;
          chain.push(folder);
          uid = folder.parent_id;
        } catch {
          break;
        }
      }
      if (cancelled) return;
      const expanded = new Set(chain.map((folder) => folder.uid));
      setExpandedFolders((current) => new Set([...current, ...expanded]));
      await Promise.all([...chain].reverse().map((folder) => loadChildren(folder.uid)));
    }
    void expandCurrentPath();
    return () => { cancelled = true; };
  }, [currentFolderId, loadChildren]);

  function toggleFolder(uid: string) {
    const next = new Set(expandedFolders);
    if (next.has(uid)) next.delete(uid);
    else {
      next.add(uid);
      const parent = Object.values(foldersByParent).flat().find((folder) => folder.uid === uid);
      if (parent) void loadChildren(parent.uid);
    }
    setExpandedFolders(next);
  }

  function renderFolderTree(parentKey: string): React.ReactNode {
    return foldersByParent[parentKey]?.map((folder) => {
      const expanded = expandedFolders.has(folder.uid);
      const children = foldersByParent[folder.uid];
      const loading = loadingFolders.has(folder.uid);
      return (
        <SidebarMenuSubItem key={folder.uid}>
          <div className="flex min-w-0 items-center">
            <button
              aria-label={`${expanded ? text.collapseFolder : text.expandFolder} ${folder.name}`}
              aria-expanded={expanded}
              className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => toggleFolder(folder.uid)}
              type="button"
            >
              {loading ? <span className="size-3 animate-spin rounded-full border border-current border-e-transparent" /> : expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5 rtl:rotate-180" />}
            </button>
            <SidebarMenuSubButton
              className="min-w-0 flex-1"
              isActive={currentFolderId === folder.uid}
              render={<Link href={folderHref(folder.uid)} />}
              title={folder.name}
            >
              <Folder className="text-sidebar-accent-foreground" />
              <span>{folder.name}</span>
            </SidebarMenuSubButton>
          </div>
          {expanded && children && children.length > 0 && (
            <SidebarMenuSub className="ms-3.5">
              {renderFolderTree(folder.uid)}
            </SidebarMenuSub>
          )}
        </SidebarMenuSubItem>
      );
    });
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
                {pathname.startsWith("/files") && (
                  <SidebarMenuSub>
                    {renderFolderTree("root")}
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
            <SidebarMenuButton
              render={<Link href="/privacy-policy" />}
              tooltip={text.navPrivacyPolicy}
            >
              <ShieldCheck />
              <span>{text.navPrivacyPolicy}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              render={<Link href="/terms-and-conditions" />}
              tooltip={text.navTermsAndConditions}
            >
              <FileText />
              <span>{text.navTermsAndConditions}</span>
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
