"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { clsx } from "clsx";
import { getNotifications, type Notification } from "@/lib/api";

const TYPE_ICON: Record<string, string> = {
  error:   "solar:danger-circle-bold-duotone",
  warning: "solar:bell-bing-bold-duotone",
  info:    "solar:info-circle-bold-duotone",
};
const TYPE_COLOR: Record<string, string> = {
  error:   "var(--bad)",
  warning: "var(--warn)",
  info:    "var(--info)",
};
const TYPE_LABEL: Record<string, string> = {
  error:   "Error",
  warning: "Warning",
  info:    "Info",
};

function timeAgo(iso: string) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60)    return "just now";
  if (s < 3600)  return `${Math.floor(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} hours ago`;
  return `${Math.floor(s / 86400)} days ago`;
}

type Tab = "all" | "unread" | "archived";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function NotificationDropdown({ open, onClose }: Props) {
  const router = useRouter();
  const [tab, setTab]         = useState<Tab>("all");
  const [readIds, setReadIds] = useState<Set<string>>(new Set());

  const { data } = useSWR(
    "notifications",
    () => getNotifications().then((r) => r.data),
    { refreshInterval: 30000 }
  );

  const all: Notification[]      = data?.notifications ?? [];
  const unread: Notification[]   = all.filter((n) => !readIds.has(n.id));
  const archived: Notification[] = all.filter((n) =>  readIds.has(n.id));

  const displayed =
    tab === "all"     ? all :
    tab === "unread"  ? unread :
    archived;

  function markAllRead() {
    setReadIds(new Set(all.map((n) => n.id)));
  }

  function markRead(id: string) {
    setReadIds((prev) => new Set([...prev, id]));
  }

  function handleNavigate(n: Notification) {
    markRead(n.id);
    onClose();
    router.push(n.link);
  }

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-[300] bg-black/40 backdrop-blur-sm"
          onClick={onClose}
        />
      )}

      {/* Full-height right panel */}
      <div
        className={clsx(
          "fixed top-0 right-0 h-full w-[420px] max-w-[100vw] z-[301] flex flex-col bg-surface border-l border-line",
          "transition-transform duration-300 ease-in-out",
          open ? "shadow-dropdown" : "shadow-none pointer-events-none"
        )}
        style={{
          transform: open ? "translateX(0)" : "translateX(calc(100% + 80px))",
        }}
        aria-hidden={!open}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 h-16 flex-shrink-0 border-b border-line">
          <h3 className="text-lg font-bold text-text">Notifications</h3>
          <div className="flex items-center gap-1">
            <button
              onClick={markAllRead}
              title="Mark all as read"
              aria-label="Mark all as read"
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-surface2 text-pri transition-colors cursor-pointer"
            >
              <Icon icon="solar:check-read-bold-duotone" width={20} />
            </button>
            <button
              onClick={onClose}
              title="Close notifications"
              aria-label="Close notifications"
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-surface2 text-text2 hover:text-text transition-colors cursor-pointer"
            >
              <Icon icon="solar:close-circle-bold-duotone" width={20} />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1.5 px-5 py-3 flex-shrink-0 border-b border-line">
          {(["all", "unread", "archived"] as Tab[]).map((t) => {
            const count =
              t === "all" ? all.length :
              t === "unread" ? unread.length :
              archived.length;
            const active = tab === t;
            return (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors capitalize cursor-pointer",
                  active
                    ? "bg-pri text-pri-fg"
                    : "text-text2 hover:bg-surface2 hover:text-text"
                )}
              >
                {t}
                {count > 0 && (
                  <span
                    className={clsx(
                      "text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center leading-none",
                      active
                        ? "bg-white/20 text-white"
                        : "bg-surface2 text-text2"
                    )}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto divide-y divide-line">
          {displayed.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <div className="w-12 h-12 rounded-full bg-surface2 flex items-center justify-center text-text2">
                <Icon icon="solar:bell-bold-duotone" width={24} />
              </div>
              <p className="text-sm font-semibold text-text">You're all caught up</p>
              <p className="text-xs text-text2">No {tab === "all" ? "" : tab} notifications</p>
            </div>
          ) : (
            displayed.map((n) => {
              const isUnread = !readIds.has(n.id);
              const color    = TYPE_COLOR[n.type] ?? "var(--text2)";
              const icon     = TYPE_ICON[n.type]  ?? "solar:info-circle-bold-duotone";
              const label    = TYPE_LABEL[n.type] ?? n.type;

              const actionLabel =
                n.id.includes("challenged")  ? "Submit OTP" :
                n.id.includes("no_session")  ? "Import Session" :
                n.id.includes("job")         ? "View Queue" :
                n.id.includes("banned")      ? "View Burners" :
                "Fix Now";

              return (
                <div
                  key={n.id}
                  className={clsx(
                    "flex gap-3 px-5 py-4 transition-colors",
                    isUnread ? "bg-pri-soft/30" : "hover:bg-surface2"
                  )}
                >
                  {/* Icon */}
                  <div
                    className="w-9 h-9 rounded-xl flex-shrink-0 flex items-center justify-center mt-0.5"
                    style={{ background: `color-mix(in srgb, ${color} 15%, transparent)` }}
                  >
                    <Icon icon={icon} width={18} style={{ color }} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm leading-snug text-text font-semibold">
                      {n.title}
                    </p>
                    <p className="text-xs text-text2 mt-1 line-clamp-2 leading-relaxed">
                      {n.message}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1.5 text-text2 text-[11px]">
                      <Icon icon="solar:clock-circle-bold-duotone" width={12} />
                      <span>{timeAgo(n.created_at)}</span>
                      <span>·</span>
                      <span>{label}</span>
                    </div>

                    {/* Action button */}
                    <button
                      onClick={() => handleNavigate(n)}
                      className="mt-2.5 px-3 py-1 rounded-lg text-xs font-semibold bg-surface2 text-text hover:bg-pri hover:text-pri-fg transition-colors cursor-pointer"
                    >
                      {actionLabel}
                    </button>
                  </div>

                  {/* Unread indicator */}
                  {isUnread && (
                    <div className="w-2 h-2 rounded-full bg-pri flex-shrink-0 mt-2" />
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 py-3 px-5 text-center border-t border-line">
          <button
            onClick={() => { onClose(); router.push("/queue"); }}
            className="text-xs font-semibold text-pri-text hover:text-pri transition-colors cursor-pointer"
          >
            View review queue
          </button>
        </div>
      </div>
    </>
  );
}
