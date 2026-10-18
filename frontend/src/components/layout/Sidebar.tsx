"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clsx } from "clsx";
import { Icon } from "@iconify/react";
import { useSidebar } from "@/contexts/SidebarContext";

interface NavItem {
  id: string;
  label: string;
  sub?: string;
  href: string;
  icon: string;
  matchPrefixes?: string[];
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { id: "home", label: "Home", href: "/dashboard", icon: "solar:home-2-bold-duotone" },
      { id: "analytics", label: "Analytics", href: "/analytics", icon: "solar:chart-2-bold-duotone" },
      { id: "strategy", label: "Strategy", href: "/strategy", icon: "solar:lightbulb-bolt-bold-duotone" },
    ],
  },
  {
    label: "Create",
    items: [
      {
        id: "brands",
        label: "Brands",
        sub: "Content & channels",
        href: "/brands",
        icon: "solar:plain-bold-duotone",
        matchPrefixes: ["/brands", "/fanpages", "/social-media", "/content-projects"],
      },
      { id: "queue", label: "Review queue", href: "/queue", icon: "solar:inbox-line-bold-duotone" },
      { id: "aivideo", label: "AI video", href: "/ai-video", icon: "solar:videocamera-record-bold-duotone" },
    ],
  },
  {
    label: "Sources",
    items: [
      {
        id: "sources",
        label: "Instagram & news",
        href: "/sources",
        icon: "solar:global-bold-duotone",
        matchPrefixes: ["/sources", "/news-sources"],
      },
      { id: "radar", label: "Viral radar", href: "/radar", icon: "solar:radar-2-bold-duotone" },
      { id: "gallery", label: "Gallery", href: "/gallery", icon: "solar:gallery-wide-bold-duotone" },
    ],
  },
  {
    label: "Publish",
    items: [
      { id: "activity", label: "Activity", href: "/history", icon: "solar:history-bold-duotone" },
    ],
  },
  {
    label: "Design",
    items: [
      { id: "templates", label: "Templates", href: "/templates", icon: "solar:palette-bold-duotone" },
    ],
  },
  {
    label: "System",
    items: [
      { id: "logs", label: "Logs & alerts", href: "/logs", icon: "solar:document-text-bold-duotone" },
      { id: "settings", label: "Settings", href: "/settings", icon: "solar:settings-bold-duotone" },
    ],
  },
];

function isItemActive(item: NavItem, pathname: string): boolean {
  if (item.matchPrefixes) {
    return item.matchPrefixes.some(
      (prefix) => pathname === prefix || pathname.startsWith(prefix + "/")
    );
  }
  if (item.href === "/dashboard") {
    return pathname === "/dashboard" || pathname === "/";
  }
  return pathname === item.href || pathname.startsWith(item.href + "/");
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { collapsed, toggle } = useSidebar();

  function handleLogout() {
    localStorage.removeItem("token");
    router.push("/login");
  }

  return (
    <aside
      className={clsx(
        "fixed inset-y-0 left-0 hidden lg:flex flex-col z-[100] border-r border-line",
        "transition-[width] duration-300",
        collapsed ? "w-[80px]" : "w-[248px]"
      )}
      style={{ background: "var(--nav)" }}
    >
      {/* Toggle button on border */}
      <button
        onClick={toggle}
        className="absolute -right-3.5 top-7 z-[200] w-7 h-7 rounded-full border border-line bg-surface flex items-center justify-center text-text2 hover:text-text hover:bg-surface2 transition-colors shadow-sm"
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        <Icon icon={collapsed ? "eva:arrow-ios-forward-fill" : "eva:arrow-ios-back-fill"} width={14} />
      </button>

      {/* Brand header */}
      {collapsed ? (
        <div className="h-16 flex items-center justify-center flex-shrink-0">
          <Link
            href="/dashboard"
            aria-label="Studio Home"
            className="w-8 h-8 rounded-[9px] bg-pri flex items-center justify-center text-white shadow-sm"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M17 7.5C16 5.5 14.2 4.5 12 4.5c-2.8 0-4.6 1.5-4.6 3.6 0 4.9 9.6 2.9 9.6 7.9 0 2.2-2 3.6-4.9 3.6-2.5 0-4.3-1.1-5.1-3.1" />
            </svg>
          </Link>
        </div>
      ) : (
        <div className="h-16 flex items-center justify-between px-4 flex-shrink-0">
          <Link href="/dashboard" className="flex items-center gap-2.5 text-inherit no-underline min-w-0">
            <span
              aria-hidden="true"
              className="w-8 h-8 rounded-[9px] bg-pri flex items-center justify-center text-white shadow-sm flex-shrink-0"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 7.5C16 5.5 14.2 4.5 12 4.5c-2.8 0-4.6 1.5-4.6 3.6 0 4.9 9.6 2.9 9.6 7.9 0 2.2-2 3.6-4.9 3.6-2.5 0-4.3-1.1-5.1-3.1" />
              </svg>
            </span>
            <span className="flex flex-col leading-[1.15] min-w-0">
              <span className="text-[15px] font-bold text-text truncate">Studio</span>
              <span className="text-[12px] text-text2 truncate">autostudio.pro</span>
            </span>
          </Link>
        </div>
      )}

      {/* Nav groups */}
      <nav className={clsx("flex-1 overflow-y-auto py-2 flex flex-col gap-3.5", collapsed ? "px-2" : "px-3")}>
        {NAV_GROUPS.map((g) => (
          <div key={g.label} className="flex flex-col gap-0.5">
            {!collapsed && (
              <div className="px-2.5 pb-1 text-[11px] font-semibold tracking-[0.7px] uppercase text-text2">
                {g.label}
              </div>
            )}
            {g.items.map((it) => {
              const active = isItemActive(it, pathname);

              if (collapsed) {
                return (
                  <Link
                    key={it.id}
                    href={it.href}
                    title={it.label}
                    aria-current={active ? "page" : undefined}
                    className={clsx(
                      "flex flex-col items-center justify-center w-full min-h-[44px] py-1.5 rounded-[9px] transition-colors gap-1",
                      active
                        ? "bg-pri-soft text-pri-text font-semibold"
                        : "text-text2 hover:bg-surface2 hover:text-text"
                    )}
                  >
                    <Icon icon={it.icon} width={20} className="flex-shrink-0" />
                    <span className="text-[10px] font-semibold leading-none truncate max-w-[68px]">
                      {it.label}
                    </span>
                  </Link>
                );
              }

              return (
                <Link
                  key={it.id}
                  href={it.href}
                  title={it.sub ? `${it.label} (${it.sub})` : it.label}
                  aria-current={active ? "page" : undefined}
                  className={clsx(
                    "flex items-center gap-2.5 min-h-[38px] px-2.5 py-1.5 rounded-[9px] text-[14px] transition-colors",
                    active
                      ? "bg-pri-soft text-pri-text font-semibold"
                      : "text-text hover:bg-surface2 hover:text-text font-medium"
                  )}
                >
                  <Icon icon={it.icon} width={18} className="flex-shrink-0" />
                  <span className="flex-1 flex flex-col leading-[1.2] min-w-0">
                    <span className="truncate">{it.label}</span>
                    {it.sub && (
                      <span className="text-[11px] font-medium text-text2 truncate">
                        {it.sub}
                      </span>
                    )}
                  </span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {/* User profile / logout footer */}
      {collapsed ? (
        <div className="border-t border-line p-2 flex flex-col items-center gap-1">
          <button
            onClick={handleLogout}
            className="flex flex-col items-center justify-center w-full py-1.5 rounded-lg text-text2 hover:text-bad hover:bg-bad-soft transition-colors"
            title="Sign out"
            aria-label="Sign out"
          >
            <span
              aria-hidden="true"
              className="w-7 h-7 rounded-full bg-pri-soft text-pri-text flex items-center justify-center text-[11px] font-bold mb-0.5"
            >
              AD
            </span>
            <span className="text-[10px] font-semibold leading-none">Logout</span>
          </button>
        </div>
      ) : (
        <div className="border-t border-line p-3 flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className="w-8 h-8 rounded-full bg-pri-soft text-pri-text flex items-center justify-center text-[13px] font-bold flex-shrink-0"
          >
            AD
          </span>
          <div className="flex-1 flex flex-col leading-[1.2] min-w-0">
            <span className="text-[13px] font-semibold text-text truncate">Admin</span>
            <span className="text-[12px] text-text2 truncate">Owner</span>
          </div>
          <button
            onClick={handleLogout}
            className="text-[13px] text-text2 hover:text-bad transition-colors flex-shrink-0"
            title="Sign out"
          >
            Sign out
          </button>
        </div>
      )}
    </aside>
  );
}
