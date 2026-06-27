"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@iconify/react";
import { clsx } from "clsx";

const TABS = [
  { href: "/dashboard", label: "Home",     icon: "solar:widget-bold-duotone" },
  { href: "/fanpages",  label: "Fanpages", icon: "mingcute:facebook-fill" },
  { href: "/queue",     label: "Queue",    icon: "solar:clock-circle-bold-duotone", center: true },
  { href: "/history",   label: "History",  icon: "solar:history-bold-duotone" },
  { href: "/burners",   label: "Burners",  icon: "solar:users-group-rounded-bold-duotone" },
  { href: "/settings",  label: "Settings", icon: "solar:settings-bold-duotone" },
];

export default function BottomTabBar() {
  const pathname = usePathname();

  function isActive(href: string) {
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <div className="fixed bottom-0 inset-x-0 z-50 bg-bg-paper border-t border-divider-soft lg:hidden">
      <div className="flex h-[60px]">
        {TABS.map((tab) => {
          const active = isActive(tab.href);

          if (tab.center) {
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className="flex-1 flex flex-col items-center justify-center gap-0.5 relative"
              >
                <div
                  className={clsx(
                    "w-11 h-11 rounded-xl flex items-center justify-center transition-all",
                    active ? "bg-primary-main shadow-primary-btn" : "bg-bg-paper-hover"
                  )}
                >
                  <Icon
                    icon={tab.icon}
                    width={22}
                    className={active ? "text-white" : "text-text-secondary"}
                  />
                </div>
              </Link>
            );
          }

          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={clsx(
                "flex-1 flex flex-col items-center justify-center gap-1 relative transition-colors",
                active ? "text-primary-main" : "text-text-disabled"
              )}
            >
              {active && (
                <div className="absolute top-0 left-1/2 -translate-x-1/2 w-10 h-0.5 bg-primary-main rounded-b" />
              )}
              <Icon icon={tab.icon} width={22} />
              <span className="text-[10px] font-semibold leading-none">{tab.label}</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
