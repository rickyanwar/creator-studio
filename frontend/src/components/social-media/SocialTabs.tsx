"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export interface SocialTabsProps {
  brands?: number;
  accounts?: number;
  devices?: number;
}

export function SocialTabs({ brands, accounts }: SocialTabsProps) {
  const pathname = usePathname();

  const tabs = [
    {
      name: "Brands",
      href: "/social-media",
      active:
        pathname === "/social-media" ||
        (pathname.startsWith("/social-media/") &&
          !pathname.startsWith("/social-media/accounts") &&
          !pathname.startsWith("/social-media/legacy")),
      count: brands,
    },
    {
      name: "Publishing accounts",
      href: "/social-media/accounts",
      active:
        pathname === "/social-media/accounts" ||
        pathname.startsWith("/social-media/accounts/"),
      count: accounts,
    },
    {
      name: "Classic view",
      href: "/social-media/legacy",
      active:
        pathname === "/social-media/legacy" ||
        pathname.startsWith("/social-media/legacy/"),
    },
    {
      name: "Activity",
      href: "/history",
      active: pathname === "/history" || pathname.startsWith("/history/"),
    },
  ];

  return (
    <div
      role="tablist"
      aria-label="Social Media sections"
      className="flex gap-2 border-b border-divider-soft overflow-x-auto"
    >
      {tabs.map((tab) => (
        <Link
          key={tab.name}
          href={tab.href}
          role="tab"
          aria-selected={tab.active}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${
            tab.active
              ? "border-primary-main text-primary-main font-semibold"
              : "border-transparent text-text-secondary hover:text-text-primary"
          }`}
        >
          <span>{tab.name}</span>
          {tab.count !== undefined && (
            <span
              className={`text-xs px-2 py-0.5 rounded-full ${
                tab.active
                  ? "bg-primary-main/15 text-primary-main font-semibold"
                  : "bg-bg-paper-hover text-text-secondary font-medium"
              }`}
            >
              {tab.count}
            </span>
          )}
        </Link>
      ))}
    </div>
  );
}
