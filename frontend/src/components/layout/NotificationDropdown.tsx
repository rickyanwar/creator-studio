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
  error:   "#FF5630",
  warning: "#FFAB00",
  info:    "#00B8D9",
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
          "fixed top-0 right-0 h-full w-[420px] z-[301] flex flex-col",
          "shadow-dropdown transition-transform duration-300 ease-in-out"
        )}
        style={{
          background: "var(--bg-paper)",
          transform: open ? "translateX(0)" : "translateX(100%)",
        }}
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 h-16 flex-shrink-0">
          <h3 className="text-xl font-bold text-text-primary">Notifications</h3>
          <div className="flex items-center gap-1">
            <button
              onClick={markAllRead}
              title="Mark all as read"
              className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors"
            >
              <Icon icon="solar:check-read-bold-duotone" width={20} className="text-primary-main" />
            </button>
            <button
              className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors"
            >
              <Icon icon="solar:settings-bold-duotone" width={18} className="text-text-secondary" />
            </button>
          </div>
        </div>

        {/* ── Tabs ── */}
        <div className="flex items-center gap-0.5 px-4 pb-3 flex-shrink-0">
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
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold transition-colors capitalize",
                  active
                    ? "bg-text-primary text-bg-paper"
                    : "text-text-secondary hover:bg-bg-paper-hover hover:text-text-primary"
                )}
              >
                {t}
                {count > 0 && (
                  <span
                    className={clsx(
                      "text-[11px] font-bold px-1.5 py-0.5 rounded-full min-w-[20px] text-center leading-none",
                      active
                        ? "bg-bg-paper text-text-primary"
                        : t === "unread"
                          ? "bg-primary-lighter text-primary-darker"
                          : "bg-bg-paper-hover text-text-secondary"
                    )}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* ── List ── */}
        <div className="flex-1 overflow-y-auto divide-y divide-divider-soft">
          {displayed.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <div className="w-14 h-14 rounded-full bg-bg-paper-hover flex items-center justify-center">
                <Icon icon="solar:bell-bold-duotone" width={28} className="text-text-disabled" />
              </div>
              <p className="text-sm font-bold text-text-primary">You're all caught up!</p>
              <p className="text-xs text-text-secondary">No {tab === "all" ? "" : tab} notifications</p>
            </div>
          ) : (
            displayed.map((n) => {
              const isUnread = !readIds.has(n.id);
              const color    = TYPE_COLOR[n.type] ?? "#919EAB";
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
                    isUnread ? "bg-[rgba(0,167,111,0.04)]" : "hover:bg-bg-paper-hover"
                  )}
                >
                  {/* Avatar */}
                  <div
                    className="w-10 h-10 rounded-full flex-shrink-0 flex items-center justify-center mt-0.5"
                    style={{ background: `${color}20` }}
                  >
                    <Icon icon={icon} width={20} style={{ color }} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm leading-snug text-text-primary">
                      <span className="font-bold">{n.title}</span>
                    </p>
                    <p className="text-xs text-text-secondary mt-0.5 line-clamp-2 leading-relaxed">
                      {n.message}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1">
                      <Icon icon="solar:clock-circle-bold-duotone" width={11} className="text-text-disabled" />
                      <span className="text-[11px] text-text-disabled">{timeAgo(n.created_at)}</span>
                      <span className="text-[11px] text-text-disabled">·</span>
                      <span className="text-[11px] text-text-disabled">{label}</span>
                    </div>

                    {/* Action button */}
                    <button
                      onClick={() => handleNavigate(n)}
                      className="mt-2.5 px-3 py-1 rounded-lg text-xs font-bold transition-opacity hover:opacity-80"
                      style={{ background: "var(--text-primary)", color: "var(--bg-paper)" }}
                    >
                      {actionLabel}
                    </button>
                  </div>

                  {/* Unread dot */}
                  {isUnread && (
                    <div className="w-2 h-2 rounded-full bg-primary-main flex-shrink-0 mt-2" />
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* ── Footer ── */}
        <div className="flex-shrink-0 py-4 text-center border-t border-divider-soft">
          <button
            onClick={() => { onClose(); router.push("/burners"); }}
            className="text-sm font-bold text-text-primary hover:text-primary-main transition-colors"
          >
            View all
          </button>
        </div>
      </div>
    </>
  );
}
