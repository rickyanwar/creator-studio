"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { useFanpages } from "@/lib/useFanpages";
import { listSocialDestinations } from "@/lib/api";
import type { SocialDestination } from "@/lib/types";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onOpen?: () => void;
}

interface PaletteItem {
  id: string;
  label: string;
  sub?: string;
  href: string;
  category: "Pages" | "Brands" | "Channels";
  icon: string;
  initials?: string;
}

const STATIC_PAGES: PaletteItem[] = [
  { id: "page-home", label: "Home", href: "/dashboard", icon: "solar:home-2-bold-duotone", category: "Pages" },
  { id: "page-analytics", label: "Analytics", href: "/analytics", icon: "solar:chart-2-bold-duotone", category: "Pages" },
  { id: "page-strategy", label: "Strategy", href: "/strategy", icon: "solar:lightbulb-bolt-bold-duotone", category: "Pages" },
  { id: "page-brands", label: "Brands", sub: "Content & channels", href: "/brands", icon: "solar:plain-bold-duotone", category: "Pages" },
  { id: "page-queue", label: "Review queue", href: "/queue", icon: "solar:inbox-line-bold-duotone", category: "Pages" },
  { id: "page-aivideo", label: "AI video", href: "/ai-video", icon: "solar:videocamera-record-bold-duotone", category: "Pages" },
  { id: "page-sources", label: "Instagram & news", href: "/sources", icon: "solar:global-bold-duotone", category: "Pages" },
  { id: "page-radar", label: "Viral radar", href: "/radar", icon: "solar:radar-2-bold-duotone", category: "Pages" },
  { id: "page-gallery", label: "Gallery", href: "/gallery", icon: "solar:gallery-wide-bold-duotone", category: "Pages" },
  { id: "page-activity", label: "Activity", href: "/history", icon: "solar:history-bold-duotone", category: "Pages" },
  { id: "page-templates", label: "Templates", href: "/templates", icon: "solar:palette-bold-duotone", category: "Pages" },
  { id: "page-logs", label: "Logs & alerts", href: "/logs", icon: "solar:document-text-bold-duotone", category: "Pages" },
  { id: "page-settings", label: "Settings", href: "/settings", icon: "solar:settings-bold-duotone", category: "Pages" },
];

function getInitials(name: string): string {
  if (!name) return "";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export default function CommandPalette({ open, onClose, onOpen }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const prevFocusedRef = useRef<HTMLElement | null>(null);

  // Brands (Fanpages)
  const { data: fanpages } = useFanpages();

  // Channels (listSocialDestinations, fetched only when palette opens)
  const { data: channels } = useSWR<SocialDestination[]>(
    open ? "palette-channels" : null,
    () => listSocialDestinations().then((r) => (Array.isArray(r.data) ? r.data : [])),
    { revalidateOnFocus: false }
  );

  // Global ⌘K / Ctrl+K listener
  useEffect(() => {
    function handleGlobalKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open) {
          onClose();
        } else {
          onOpen?.();
        }
      }
    }
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [open, onClose, onOpen]);

  // Focus management and reset on open/close
  useEffect(() => {
    if (open) {
      prevFocusedRef.current = document.activeElement as HTMLElement | null;
      setQuery("");
      setSelectedIndex(0);
      const timer = setTimeout(() => {
        inputRef.current?.focus();
      }, 30);
      return () => clearTimeout(timer);
    } else {
      if (prevFocusedRef.current) {
        prevFocusedRef.current.focus();
      }
    }
  }, [open]);

  // Build items list
  const allItems = useMemo<PaletteItem[]>(() => {
    const brandItems: PaletteItem[] = (fanpages || []).map((b) => ({
      id: `brand-${b.id}`,
      label: b.name,
      sub: b.username ? `@${b.username}` : undefined,
      href: `/brands/${b.id}`,
      category: "Brands",
      icon: "solar:plain-bold-duotone",
      initials: getInitials(b.name),
    }));

    const channelItems: PaletteItem[] = (channels || []).map((c) => ({
      id: `channel-${c.id}`,
      label: c.name,
      sub: c.kind === "facebook_page" ? "Facebook Page" : c.kind === "facebook_profile" ? "Facebook Profile" : c.kind,
      href: `/brands/channels/${c.id}`,
      category: "Channels",
      icon: c.kind === "facebook_page" ? "solar:flag-bold-duotone" : "solar:user-bold-duotone",
    }));

    return [...STATIC_PAGES, ...brandItems, ...channelItems];
  }, [fanpages, channels]);

  // Filter items based on query
  const filteredItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allItems;
    return allItems.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        (item.sub && item.sub.toLowerCase().includes(q))
    );
  }, [allItems, query]);

  // Reset selected index on query change
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // Scroll active item into view
  useEffect(() => {
    if (!listRef.current) return;
    const activeEl = listRef.current.querySelector<HTMLElement>(`[data-index="${selectedIndex}"]`);
    if (activeEl) {
      activeEl.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIndex]);

  if (!open) return null;

  function handleNavigate(href: string) {
    onClose();
    router.push(href);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (filteredItems.length > 0) {
        setSelectedIndex((prev) => (prev + 1) % filteredItems.length);
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (filteredItems.length > 0) {
        setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % filteredItems.length);
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      const current = filteredItems[selectedIndex];
      if (current) {
        handleNavigate(current.href);
      }
    } else if (e.key === "Tab") {
      // Focus trap
      e.preventDefault();
      inputRef.current?.focus();
    }
  }

  const pagesGroup = filteredItems.filter((i) => i.category === "Pages");
  const brandsGroup = filteredItems.filter((i) => i.category === "Brands");
  const channelsGroup = filteredItems.filter((i) => i.category === "Channels");

  const selectedItem = filteredItems[selectedIndex];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command search"
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-start justify-center pt-16 sm:pt-24 px-3 sm:px-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={handleKeyDown}
    >
      <div
        ref={dialogRef}
        className="w-full max-w-xl rounded-2xl border border-line bg-surface shadow-2xl overflow-hidden flex flex-col max-h-[80vh] text-text"
      >
        {/* Search Input Bar */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-line bg-surface">
          <Icon icon="solar:magnifer-linear" width={18} className="text-text2 flex-shrink-0" />
          <input
            ref={inputRef}
            type="search"
            role="combobox"
            aria-expanded={open}
            aria-controls="command-palette-results"
            aria-activedescendant={selectedItem ? `palette-item-${selectedItem.id}` : undefined}
            aria-autocomplete="list"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages, brands, channels..."
            className="flex-1 bg-transparent text-text placeholder:text-text2 outline-none text-sm sm:text-base min-w-0"
          />
          <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 text-[11px] font-mono text-text2 border border-line rounded bg-surface2">
            ESC
          </kbd>
        </div>

        {/* Results List */}
        <div
          ref={listRef}
          role="listbox"
          id="command-palette-results"
          aria-label="Search results"
          className="flex-1 overflow-y-auto p-2 min-h-[140px] max-h-[60vh] flex flex-col gap-1"
        >
          {filteredItems.length === 0 ? (
            <div className="py-12 text-center text-sm text-text2">
              No results found for &ldquo;{query}&rdquo;
            </div>
          ) : (
            <>
              {/* Pages Group */}
              {pagesGroup.length > 0 && (
                <div className="flex flex-col gap-0.5">
                  <div className="px-3 pt-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-text2">
                    Pages
                  </div>
                  {pagesGroup.map((item) => {
                    const globalIndex = filteredItems.indexOf(item);
                    const isSelected = globalIndex === selectedIndex;
                    return (
                      <div
                        key={item.id}
                        id={`palette-item-${item.id}`}
                        role="option"
                        aria-selected={isSelected}
                        data-index={globalIndex}
                        onClick={() => handleNavigate(item.href)}
                        onMouseEnter={() => setSelectedIndex(globalIndex)}
                        className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors cursor-pointer select-none ${
                          isSelected
                            ? "bg-pri-soft text-pri-text font-medium"
                            : "text-text hover:bg-surface2"
                        }`}
                      >
                        <Icon icon={item.icon} width={18} className="flex-shrink-0" />
                        <span className="flex-1 truncate">{item.label}</span>
                        {item.sub && (
                          <span className="text-xs text-text2 truncate">{item.sub}</span>
                        )}
                        <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded border border-line bg-surface2 text-text2 flex-shrink-0">
                          Page
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Brands Group */}
              {brandsGroup.length > 0 && (
                <div className="flex flex-col gap-0.5">
                  <div className="px-3 pt-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-text2">
                    Brands
                  </div>
                  {brandsGroup.map((item) => {
                    const globalIndex = filteredItems.indexOf(item);
                    const isSelected = globalIndex === selectedIndex;
                    return (
                      <div
                        key={item.id}
                        id={`palette-item-${item.id}`}
                        role="option"
                        aria-selected={isSelected}
                        data-index={globalIndex}
                        onClick={() => handleNavigate(item.href)}
                        onMouseEnter={() => setSelectedIndex(globalIndex)}
                        className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors cursor-pointer select-none ${
                          isSelected
                            ? "bg-pri-soft text-pri-text font-medium"
                            : "text-text hover:bg-surface2"
                        }`}
                      >
                        <span
                          aria-hidden="true"
                          className="w-5 h-5 rounded-md bg-surface2 text-text flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                        >
                          {item.initials}
                        </span>
                        <span className="flex-1 truncate">{item.label}</span>
                        {item.sub && (
                          <span className="text-xs text-text2 truncate">{item.sub}</span>
                        )}
                        <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded border border-line bg-surface2 text-text2 flex-shrink-0">
                          Brand
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Channels Group */}
              {channelsGroup.length > 0 && (
                <div className="flex flex-col gap-0.5">
                  <div className="px-3 pt-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-text2">
                    Channels
                  </div>
                  {channelsGroup.map((item) => {
                    const globalIndex = filteredItems.indexOf(item);
                    const isSelected = globalIndex === selectedIndex;
                    return (
                      <div
                        key={item.id}
                        id={`palette-item-${item.id}`}
                        role="option"
                        aria-selected={isSelected}
                        data-index={globalIndex}
                        onClick={() => handleNavigate(item.href)}
                        onMouseEnter={() => setSelectedIndex(globalIndex)}
                        className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors cursor-pointer select-none ${
                          isSelected
                            ? "bg-pri-soft text-pri-text font-medium"
                            : "text-text hover:bg-surface2"
                        }`}
                      >
                        <Icon icon={item.icon} width={18} className="flex-shrink-0" />
                        <span className="flex-1 truncate">{item.label}</span>
                        {item.sub && (
                          <span className="text-xs text-text2 truncate">{item.sub}</span>
                        )}
                        <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded border border-line bg-surface2 text-text2 flex-shrink-0">
                          Channel
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer Hints */}
        <div className="px-4 py-2 border-t border-line bg-surface2/40 flex items-center justify-between text-[11px] sm:text-xs text-text2">
          <div className="flex items-center gap-3">
            <span>
              <kbd className="font-mono bg-surface border border-line px-1 rounded">↑</kbd>
              <kbd className="font-mono bg-surface border border-line px-1 rounded ml-1">↓</kbd> Navigate
            </span>
            <span>
              <kbd className="font-mono bg-surface border border-line px-1.5 rounded">↵</kbd> Select
            </span>
            <span>
              <kbd className="font-mono bg-surface border border-line px-1.5 rounded">ESC</kbd> Close
            </span>
          </div>
          <span>
            {filteredItems.length} result{filteredItems.length === 1 ? "" : "s"}
          </span>
        </div>
      </div>
    </div>
  );
}
