"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clsx } from "clsx";
import { Icon } from "@iconify/react";
import { useSidebar } from "@/contexts/SidebarContext";

const nav = [
  { href: "/dashboard", label: "Dashboard",   icon: "solar:widget-bold-duotone" },
  { href: "/fanpages",  label: "Fanpages",     icon: "mingcute:facebook-fill" },
  { href: "/queue",     label: "Queue",        icon: "solar:clock-circle-bold-duotone" },
  { href: "/burners",   label: "Burners",      icon: "solar:users-group-rounded-bold-duotone" },
  { href: "/sources",   label: "IG Sources",   icon: "solar:global-bold-duotone" },
  { href: "/history",   label: "History",      icon: "solar:history-bold-duotone" },
  { href: "/logs",      label: "Logs",         icon: "solar:document-text-bold-duotone" },
  { href: "/settings",  label: "Settings",     icon: "solar:settings-bold-duotone" },
];

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
        "fixed inset-y-0 left-0 hidden lg:flex flex-col z-[100] border-r border-divider-soft",
        "transition-[width] duration-300",
        collapsed ? "w-[88px]" : "w-[280px]"
      )}
      style={{ background: "var(--sidebar-bg)" }}
    >
      {/* ── Toggle button — sits on the right border line ── */}
      <button
        onClick={toggle}
        className="absolute -right-3.5 top-8 z-[200] w-7 h-7 rounded-full border border-divider-soft bg-bg-paper flex items-center justify-center text-text-secondary hover:text-text-primary hover:bg-bg-paper-hover transition-colors shadow-sm"
        title={collapsed ? "Expand" : "Collapse"}
      >
        <Icon icon={collapsed ? "eva:arrow-ios-forward-fill" : "eva:arrow-ios-back-fill"} width={16} />
      </button>

      {/* ── Brand row ── */}
      {collapsed ? (
        /* Collapsed: logo icon centered */
        <div className="h-16 flex items-center justify-center flex-shrink-0">
          <div className="w-8 h-8 rounded-md bg-primary-main flex items-center justify-center flex-shrink-0">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5">
              <path d="M17 2H7a5 5 0 0 0-5 5v10a5 5 0 0 0 5 5h10a5 5 0 0 0 5-5V7a5 5 0 0 0-5-5Z"/>
              <circle cx="12" cy="12" r="3"/>
              <circle cx="17.5" cy="6.5" r="1" fill="white" stroke="none"/>
            </svg>
          </div>
        </div>
      ) : (
        /* Expanded: logo + name */
        <div className="h-16 flex items-center flex-shrink-0 px-4">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-md bg-primary-main flex items-center justify-center flex-shrink-0">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5">
                <path d="M17 2H7a5 5 0 0 0-5 5v10a5 5 0 0 0 5 5h10a5 5 0 0 0 5-5V7a5 5 0 0 0-5-5Z"/>
                <circle cx="12" cy="12" r="3"/>
                <circle cx="17.5" cy="6.5" r="1" fill="white" stroke="none"/>
              </svg>
            </div>
            <span className="font-bold text-text-primary text-sm tracking-tight whitespace-nowrap">
              Reposter
            </span>
          </div>
        </div>
      )}

      {/* ── Nav items ── */}
      <nav className={clsx(
        "flex-1 overflow-y-auto py-2",
        collapsed ? "px-2" : "px-3"
      )}>
        {nav.map(({ href, label, icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");

          const activeStyle = active
            ? { background: "color-mix(in srgb, var(--primary-main) 12%, transparent)" }
            : undefined;

          if (collapsed) {
            /* ── Collapsed: icon above label, centered ── */
            return (
              <Link
                key={href}
                href={href}
                style={activeStyle}
                className={clsx(
                  "flex flex-col items-center justify-center w-full min-h-[56px] py-2 rounded mb-0.5 transition-colors gap-1.5",
                  active
                    ? "text-primary-main"
                    : "text-text-secondary hover:bg-bg-paper-hover hover:text-text-primary"
                )}
              >
                <Icon icon={icon} width={28} />
                <span className="text-[11px] font-semibold leading-none">{label}</span>
              </Link>
            );
          }

          /* ── Expanded: horizontal icon + label ── */
          return (
            <Link
              key={href}
              href={href}
              style={activeStyle}
              className={clsx(
                "flex items-center gap-3 w-full px-3 min-h-[44px] rounded mb-0.5 transition-colors",
                active
                  ? "text-primary-main"
                  : "text-text-secondary hover:bg-bg-paper-hover hover:text-text-primary"
              )}
            >
              <Icon icon={icon} width={24} className="flex-shrink-0" />
              <span className="text-[14px] leading-[1.57] font-semibold">{label}</span>
            </Link>
          );
        })}
      </nav>

      {/* ── Bottom: sign out ── */}
      <div className={clsx("border-t border-divider-soft py-3", collapsed ? "px-2" : "px-3")}>
        {collapsed ? (
          <button
            onClick={handleLogout}
            className="flex flex-col items-center justify-center w-full min-h-[56px] py-2 rounded-xl gap-1.5 text-text-secondary hover:text-error-main hover:bg-[rgba(255,86,48,0.08)] transition-colors"
          >
            <Icon icon="solar:logout-3-bold-duotone" width={28} />
            <span className="text-[11px] font-semibold leading-none">Sign Out</span>
          </button>
        ) : (
          <button
            onClick={handleLogout}
            className="flex items-center gap-3 w-full px-3 min-h-[44px] rounded-xl text-text-secondary hover:text-error-main hover:bg-[rgba(255,86,48,0.08)] transition-colors"
          >
            <Icon icon="solar:logout-3-bold-duotone" width={24} className="flex-shrink-0" />
            <span className="text-[14px] leading-[1.57] font-semibold">Sign Out</span>
          </button>
        )}
      </div>
    </aside>
  );
}
