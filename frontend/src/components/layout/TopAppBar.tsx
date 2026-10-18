"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@iconify/react";
import useSWR from "swr";
import SettingsDrawer from "./SettingsDrawer";
import NotificationDropdown from "./NotificationDropdown";
import CommandPalette from "./CommandPalette";
import { getNotifications } from "@/lib/api";
import { useTheme } from "@/contexts/ThemeContext";
import { useBrandFilter } from "@/contexts/BrandFilterContext";
import { useFanpages } from "@/lib/useFanpages";

function getBrandInitials(name: string): string {
  if (!name) return "";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

function TimeDisplay() {
  const [mounted, setMounted] = useState(false);
  const [time, setTime] = useState<{ wib: string; utc: string }>({ wib: "", utc: "" });

  useEffect(() => {
    setMounted(true);
    function update() {
      const now = new Date();
      const wib = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Jakarta",
        hour: "2-digit",
        minute: "2-digit",
      }).format(now);
      const utc = new Intl.DateTimeFormat("en-GB", {
        timeZone: "UTC",
        hour: "2-digit",
        minute: "2-digit",
      }).format(now);
      setTime({ wib, utc });
    }
    update();
    const interval = setInterval(update, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!mounted || !time.wib) return null;

  return (
    <span className="hidden md:flex items-center gap-1.5 text-[13px] text-text2 font-mono">
      <span>{time.wib} WIB</span>
      <span aria-hidden="true" className="text-text2/50">·</span>
      <span>{time.utc} UTC</span>
    </span>
  );
}

export default function TopAppBar() {
  const router = useRouter();
  const { theme, toggle: toggleTheme } = useTheme();
  const { selectedBrandId, setSelectedBrandId } = useBrandFilter();
  const { data: fanpages } = useFanpages();

  const [drawerOpen, setDrawerOpen]   = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [notifOpen,   setNotifOpen]   = useState(false);
  const [brandDropdownOpen, setBrandDropdownOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const profileRef = useRef<HTMLDivElement>(null);
  const brandDropdownRef = useRef<HTMLDivElement>(null);

  const activeBrand =
    selectedBrandId !== null ? fanpages?.find((f) => f.id === selectedBrandId) : null;

  // Poll unread count every 60s
  const { data: notifData } = useSWR(
    "notifications-count",
    () => getNotifications().then((r) => r.data),
    { refreshInterval: 60000 }
  );
  const unread = notifData?.unread ?? 0;

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
      if (brandDropdownRef.current && !brandDropdownRef.current.contains(e.target as Node)) {
        setBrandDropdownOpen(false);
      }
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
      <header className="sticky top-0 z-40 min-h-[64px] bg-surface/90 backdrop-blur-md border-b border-line flex items-center justify-between px-4 lg:px-7 gap-3 flex-shrink-0">
        {/* Left controls: ⌘K search button + brand switcher */}
        <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1 max-w-xl">
          {/* Search trigger button */}
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-label="Search (⌘K)"
            title="Search (⌘K)"
            className="flex items-center gap-2 sm:gap-2.5 h-10 px-3 rounded-xl bg-surface2 border border-line text-text2 hover:text-text hover:border-line/80 transition-colors cursor-pointer w-full max-w-[180px] sm:max-w-xs min-w-0"
          >
            <Icon icon="solar:magnifer-linear" width={16} className="text-text2 flex-shrink-0" />
            <span className="truncate flex-1 text-left hidden sm:inline text-xs sm:text-sm">
              Search pages, brands...
            </span>
            <span className="truncate flex-1 text-left sm:hidden text-xs">
              Search...
            </span>
            <kbd className="hidden md:inline-flex items-center font-mono text-[11px] px-1.5 py-0.5 rounded border border-line bg-surface text-text2 flex-shrink-0">
              ⌘K
            </kbd>
          </button>

          {/* Brand switcher dropdown */}
          <div ref={brandDropdownRef} className="relative flex-shrink-0">
            <button
              type="button"
              onClick={() => {
                setBrandDropdownOpen((v) => !v);
                setProfileOpen(false);
                setNotifOpen(false);
              }}
              aria-haspopup="listbox"
              aria-expanded={brandDropdownOpen}
              aria-label={`Switch brand: ${activeBrand ? activeBrand.name : "All brands"}`}
              title="Switch brand"
              className="flex items-center gap-2 h-10 px-2.5 sm:px-3 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-xs sm:text-sm font-medium transition-colors cursor-pointer"
            >
              <span
                aria-hidden="true"
                className="w-5 h-5 rounded-md bg-surface2 text-text flex items-center justify-center text-[10px] font-bold flex-shrink-0"
              >
                {activeBrand ? getBrandInitials(activeBrand.name) : "All"}
              </span>
              <span className="max-w-[70px] sm:max-w-[120px] md:max-w-[150px] truncate">
                {activeBrand ? activeBrand.name : "All brands"}
              </span>
              <Icon
                icon="solar:alt-arrow-down-linear"
                width={14}
                className={`text-text2 transition-transform duration-200 ${
                  brandDropdownOpen ? "rotate-180" : ""
                }`}
              />
            </button>

            {brandDropdownOpen && (
              <div
                role="listbox"
                aria-label="Select brand"
                className="absolute left-0 top-12 w-60 rounded-xl border border-line shadow-dropdown bg-surface py-1 z-[400] overflow-hidden"
              >
                {/* All brands option */}
                <button
                  type="button"
                  role="option"
                  aria-selected={selectedBrandId === null}
                  onClick={() => {
                    setSelectedBrandId(null);
                    setBrandDropdownOpen(false);
                  }}
                  className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs sm:text-sm text-left transition-colors cursor-pointer ${
                    selectedBrandId === null
                      ? "bg-pri-soft text-pri-text font-semibold"
                      : "text-text hover:bg-surface2"
                  }`}
                >
                  <span className="w-5 h-5 rounded-md bg-surface2 text-text flex items-center justify-center text-[10px] font-bold flex-shrink-0">
                    All
                  </span>
                  <span className="flex-1 truncate">All brands</span>
                  {selectedBrandId === null && (
                    <Icon icon="solar:check-read-linear" width={16} className="text-pri-text flex-shrink-0" />
                  )}
                </button>

                {fanpages && fanpages.length > 0 && (
                  <>
                    <div className="my-1 border-t border-line" />
                    <div className="px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-text2">
                      Brands
                    </div>
                    <div className="max-h-60 overflow-y-auto">
                      {fanpages.map((brand) => {
                        const isSelected = selectedBrandId === brand.id;
                        return (
                          <button
                            key={brand.id}
                            type="button"
                            role="option"
                            aria-selected={isSelected}
                            onClick={() => {
                              setSelectedBrandId(brand.id);
                              setBrandDropdownOpen(false);
                            }}
                            className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs sm:text-sm text-left transition-colors cursor-pointer ${
                              isSelected
                                ? "bg-pri-soft text-pri-text font-semibold"
                                : "text-text hover:bg-surface2"
                            }`}
                          >
                            <span className="w-5 h-5 rounded-md bg-surface2 text-text flex items-center justify-center text-[10px] font-bold flex-shrink-0">
                              {getBrandInitials(brand.name)}
                            </span>
                            <span className="flex-1 truncate">{brand.name}</span>
                            {isSelected && (
                              <Icon
                                icon="solar:check-read-linear"
                                width={16}
                                className="text-pri-text flex-shrink-0"
                              />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right side controls */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Time display: WIB · UTC */}
          <TimeDisplay />

          {/* Light/Dark mode toggle */}
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            className="w-10 h-10 rounded-xl border border-line bg-surface hover:bg-surface2 text-text flex items-center justify-center transition-colors cursor-pointer"
          >
            <Icon
              icon={theme === "dark" ? "solar:sun-2-bold-duotone" : "solar:moon-bold-duotone"}
              width={18}
            />
          </button>

          {/* Notification bell */}
          <button
            type="button"
            onClick={() => { setNotifOpen((v) => !v); setProfileOpen(false); }}
            aria-label={`Notifications, ${unread} unread`}
            title="Notifications"
            className="relative w-10 h-10 rounded-xl border border-line bg-surface hover:bg-surface2 text-text flex items-center justify-center transition-colors cursor-pointer"
          >
            <Icon icon="solar:bell-bold-duotone" width={18} />
            {unread > 0 && (
              <span
                aria-hidden="true"
                className="absolute top-1.5 right-1.5 min-w-[16px] h-4 px-1 rounded-full bg-bad text-white text-[10px] font-bold flex items-center justify-center leading-none"
              >
                {unread > 9 ? "9+" : unread}
              </span>
            )}
          </button>

          {/* Settings → opens drawer */}
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="Theme settings"
            title="Theme settings"
            className="w-10 h-10 rounded-xl border border-line bg-surface hover:bg-surface2 text-text flex items-center justify-center transition-colors cursor-pointer"
          >
            <Icon icon="solar:settings-bold-duotone" width={18} />
          </button>

          {/* Avatar menu */}
          <div ref={profileRef} className="relative ml-0.5">
            <button
              type="button"
              onClick={() => { setProfileOpen((v) => !v); setNotifOpen(false); }}
              aria-label="Account menu"
              title="Account menu"
              className="w-10 h-10 rounded-full border-0 bg-pri-soft text-pri-text font-bold text-[13px] flex items-center justify-center cursor-pointer hover:opacity-90 transition-opacity"
            >
              AD
            </button>

            {profileOpen && (
              <div
                className="absolute right-0 top-12 w-52 rounded-xl border border-line shadow-dropdown overflow-hidden z-[400] bg-surface"
              >
                <div className="px-4 py-3 border-b border-line">
                  <p className="text-sm font-semibold text-text">Admin</p>
                  <p className="text-xs text-text2 truncate">Owner</p>
                </div>
                <div className="py-1">
                  <button
                    type="button"
                    onClick={() => { setProfileOpen(false); router.push("/settings"); }}
                    className="flex items-center gap-3 w-full px-4 py-2 text-sm text-text hover:bg-surface2 transition-colors cursor-pointer"
                  >
                    <Icon icon="solar:settings-bold-duotone" width={16} className="text-text2" />
                    Global Settings
                  </button>
                  <button
                    type="button"
                    onClick={() => { setProfileOpen(false); handleLogout(); }}
                    className="flex items-center gap-3 w-full px-4 py-2 text-sm text-bad hover:bg-bad-soft transition-colors cursor-pointer"
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
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onOpen={() => setPaletteOpen(true)}
      />
    </>
  );
}
