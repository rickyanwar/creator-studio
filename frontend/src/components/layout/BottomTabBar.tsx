"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@iconify/react";
import { clsx } from "clsx";

const TABS = [
  { href: "/dashboard", label: "Home",     icon: "solar:home-2-bold-duotone" },
  { href: "/brands",    label: "Brands",   icon: "solar:plain-bold-duotone" },
  { href: "/queue",     label: "Queue",    icon: "solar:inbox-line-bold-duotone" },
  { href: "/history",   label: "Activity", icon: "solar:history-bold-duotone" },
  { href: "/gallery",   label: "Gallery",  icon: "solar:gallery-wide-bold-duotone" },
  { href: "/settings",  label: "Settings", icon: "solar:settings-bold-duotone" },
];

export default function BottomTabBar() {
  const pathname = usePathname();

  function isActive(href: string) {
    if (href === "/brands") {
      return (
        pathname.startsWith("/brands") ||
        pathname.startsWith("/fanpages") ||
        pathname.startsWith("/social-media") ||
        pathname.startsWith("/content-projects")
      );
    }
    if (href === "/dashboard") {
      return pathname === "/dashboard" || pathname === "/";
    }
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <nav
      aria-label="Mobile"
      className="fixed bottom-0 inset-x-0 z-50 bg-surface border-t border-line lg:hidden"
    >
      <div className="grid grid-cols-6 h-[56px] px-1">
        {TABS.map((tab) => {
          const active = isActive(tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "flex flex-col items-center justify-center gap-0.5 min-h-[48px] py-1 transition-colors text-center",
                active ? "text-pri-text font-semibold" : "text-text2 hover:text-text font-medium"
              )}
            >
              <Icon icon={tab.icon} width={22} height={22} />
              <span className="text-[11px] leading-none truncate max-w-[56px]">{tab.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
