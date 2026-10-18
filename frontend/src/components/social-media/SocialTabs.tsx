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

  const isBrands =
    pathname === "/brands" ||
    pathname === "/social-media" ||
    (pathname.startsWith("/brands/") && !pathname.startsWith("/brands/accounts")) ||
    (pathname.startsWith("/social-media/") && !pathname.startsWith("/social-media/accounts"));

  const isAccounts =
    pathname === "/brands/accounts" ||
    pathname.startsWith("/brands/accounts/") ||
    pathname === "/social-media/accounts" ||
    pathname.startsWith("/social-media/accounts/");

  const tabs = [
    {
      name: "Brands",
      href: "/brands",
      active: isBrands,
      count: brands,
    },
    {
      name: "Accounts & phones",
      href: "/brands/accounts",
      active: isAccounts,
      count: accounts,
    },
  ];

  return (
    <div
      role="tablist"
      aria-label="View"
      className="self-start flex flex-wrap gap-1 p-1 rounded-xl bg-surface2 border border-line"
    >
      {tabs.map((tab) => (
        <Link
          key={tab.name}
          href={tab.href}
          role="tab"
          aria-selected={tab.active}
          className={`h-11 px-4 rounded-[9px] text-sm font-semibold transition-all inline-flex items-center gap-2 ${
            tab.active
              ? "bg-surface text-text shadow-sm"
              : "text-text2 hover:text-text"
          }`}
        >
          <span>{tab.name}</span>
          {tab.count !== undefined && (
            <span
              className={`text-xs font-semibold tabular-nums ${
                tab.active ? "text-text" : "text-text2"
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
