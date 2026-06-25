"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@iconify/react";
import useSWR from "swr";
import SettingsDrawer from "./SettingsDrawer";
import NotificationDropdown from "./NotificationDropdown";
import { getNotifications } from "@/lib/api";

export default function TopAppBar() {
  const router = useRouter();
  const [drawerOpen, setDrawerOpen]       = useState(false);
  const [profileOpen, setProfileOpen]     = useState(false);
  const [notifOpen,   setNotifOpen]       = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);

  // Poll unread count every 60s (always, not just when dropdown is open)
  const { data: notifData } = useSWR(
    "notifications-count",
    () => getNotifications().then((r) => r.data),
    { refreshInterval: 60000 }
  );
  const unread = notifData?.unread ?? 0;

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) setProfileOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function handleLogout() {
    localStorage.removeItem("token");
    router.push("/login");
  }

  return (
    <>
      <header className="sticky top-0 z-50 h-16 bg-bg-default/80 backdrop-blur-md flex items-center justify-between px-6 flex-shrink-0">
        <div />

        <div className="flex items-center gap-1">

          {/* Notification bell */}
          <button
            onClick={() => { setNotifOpen((v) => !v); setProfileOpen(false); }}
            className="relative w-9 h-9 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors text-text-secondary hover:text-text-primary"
          >
            <Icon icon="solar:bell-bold-duotone" width={20} />
            {unread > 0 && (
              <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-[#FF5630] text-white text-[10px] font-bold flex items-center justify-center leading-none">
                {unread > 9 ? "9+" : unread}
              </span>
            )}
          </button>

          {/* Settings → opens drawer */}
          <button
            onClick={() => setDrawerOpen(true)}
            className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors text-text-secondary hover:text-text-primary"
          >
            <Icon icon="solar:settings-bold-duotone" width={20} />
          </button>

          {/* Avatar with gradient ring + dropdown */}
          <div ref={profileRef} className="relative ml-1">
            <button
              onClick={() => { setProfileOpen((v) => !v); setNotifOpen(false); }}
              className="w-9 h-9 rounded-full p-[2px] flex-shrink-0"
              style={{ background: "linear-gradient(135deg, #84A98C 0%, #FFAB00 50%, #FF5630 100%)" }}
            >
              <div className="w-full h-full rounded-full bg-bg-paper-hover flex items-center justify-center overflow-hidden">
                <Icon icon="solar:user-bold-duotone" width={20} className="text-text-secondary" />
              </div>
            </button>

            {profileOpen && (
              <div
                className="absolute right-0 top-11 w-48 rounded-xl border border-divider-soft shadow-dropdown overflow-hidden z-[400]"
                style={{ background: "var(--bg-paper)" }}
              >
                <div className="px-4 py-3 border-b border-divider-soft">
                  <p className="text-sm font-semibold text-text-primary">Admin</p>
                  <p className="text-xs text-text-secondary truncate">anonymousgg77@gmail.com</p>
                </div>
                <div className="py-1">
                  <button
                    onClick={() => { setProfileOpen(false); router.push("/settings"); }}
                    className="flex items-center gap-3 w-full px-4 py-2 text-sm text-text-secondary hover:bg-bg-paper-hover hover:text-text-primary transition-colors"
                  >
                    <Icon icon="solar:settings-bold-duotone" width={16} />
                    Settings
                  </button>
                  <button
                    onClick={() => { setProfileOpen(false); handleLogout(); }}
                    className="flex items-center gap-3 w-full px-4 py-2 text-sm text-error-main hover:bg-[rgba(255,86,48,0.08)] transition-colors"
                  >
                    <Icon icon="solar:logout-3-bold-duotone" width={16} />
                    Sign out
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      <SettingsDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <NotificationDropdown open={notifOpen} onClose={() => setNotifOpen(false)} />
    </>
  );
}
