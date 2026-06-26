"use client";

import { useState, useRef, useEffect } from "react";
import useSWR from "swr";
import Link from "next/link";
import { listFanpages, updateFanpage, triggerFanpageSync } from "@/lib/api";
import type { Fanpage } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = () => listFanpages().then((r) => r.data as Fanpage[]);

function FanpageMenu({ fp, onToggleActive, onTogglePublishMode, togglingId }: {
  fp: Fanpage;
  onToggleActive: (fp: Fanpage) => void;
  onTogglePublishMode: (fp: Fanpage) => void;
  togglingId: number | null;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-8 h-8 flex items-center justify-center rounded-full text-text-secondary hover:text-text-primary hover:bg-bg-paper-hover transition-colors"
        title="More options"
      >
        <Icon icon="solar:menu-dots-bold-duotone" width={18} />
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-50 w-52 rounded-xl border border-divider-soft bg-bg-paper shadow-lg py-1.5 text-sm">
          <Link
            href={`/fanpages/${fp.id}`}
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 px-4 py-2 text-text-primary hover:bg-bg-paper-hover transition-colors"
          >
            <Icon icon="solar:tuning-2-bold-duotone" width={16} className="text-text-secondary" />
            Configure
          </Link>

          <button
            onClick={() => { onTogglePublishMode(fp); setOpen(false); }}
            disabled={togglingId === fp.id}
            className="flex items-center gap-2.5 px-4 py-2 w-full text-left text-text-primary hover:bg-bg-paper-hover transition-colors disabled:opacity-50"
          >
            <Icon
              icon={fp.publish_mode === "auto"
                ? "solar:eye-bold-duotone"
                : "solar:send-square-bold-duotone"}
              width={16}
              className="text-text-secondary"
            />
            Switch to {fp.publish_mode === "auto" ? "Manual Review" : "Auto-publish"}
          </button>

          <div className="border-t border-divider-soft my-1" />

          <button
            onClick={() => { onToggleActive(fp); setOpen(false); }}
            disabled={togglingId === fp.id}
            className="flex items-center gap-2.5 px-4 py-2 w-full text-left hover:bg-bg-paper-hover transition-colors disabled:opacity-50"
          >
            <Icon
              icon={fp.is_active ? "solar:pause-circle-bold-duotone" : "solar:play-circle-bold-duotone"}
              width={16}
              className={fp.is_active ? "text-warning-main" : "text-success-main"}
            />
            <span className={fp.is_active ? "text-warning-main" : "text-success-main"}>
              {fp.is_active ? "Deactivate" : "Activate"}
            </span>
          </button>
        </div>
      )}
    </div>
  );
}

export default function FanpagesPage() {
  const { data: fanpages = [], isLoading, mutate } = useSWR("fanpages", fetcher);
  const [loadingSync, setLoadingSync] = useState(false);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  async function toggleActive(fp: Fanpage) {
    setTogglingId(fp.id);
    try { await updateFanpage(fp.id, { is_active: !fp.is_active }); mutate(); }
    finally { setTogglingId(null); }
  }

  async function togglePublishMode(fp: Fanpage) {
    setTogglingId(fp.id);
    try {
      await updateFanpage(fp.id, {
        publish_mode: fp.publish_mode === "auto" ? "manual_review" : "auto",
      });
      mutate();
    }
    finally { setTogglingId(null); }
  }

  async function handleSync() {
    setLoadingSync(true);
    try { await triggerFanpageSync(); setTimeout(() => mutate(), 3000); }
    finally { setLoadingSync(false); }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Fanpages</h1>
          <p className="text-sm text-text-secondary mt-1">
            {fanpages.length} fanpage{fanpages.length !== 1 ? "s" : ""} from Repliz
          </p>
        </div>
        <button onClick={handleSync} disabled={loadingSync} className="btn-ghost">
          <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingSync ? "animate-spin" : ""} />
          {loadingSync ? "Syncing…" : "Sync from Repliz"}
        </button>
      </div>

      {isLoading ? (
        <div className="text-sm text-text-secondary">Loading fanpages…</div>
      ) : (
        <div className="space-y-3">
          {fanpages.map((fp) => (
            <div
              key={fp.id}
              className="card flex items-center justify-between gap-4"
            >
              <div className="flex items-center gap-4 min-w-0">
                {fp.picture_url ? (
                  <img
                    src={fp.picture_url}
                    alt={fp.name}
                    className="w-10 h-10 rounded-full object-cover flex-shrink-0"
                  />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-bg-paper-hover flex items-center justify-center flex-shrink-0">
                    <Icon icon="solar:megaphone-bold-duotone" width={18} className="text-text-secondary" />
                  </div>
                )}
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-text-primary truncate">{fp.name}</span>
                    {!fp.is_connected && (
                      <span className="badge-red flex items-center gap-1">
                        <Icon icon="solar:wifi-no-connection-bold-duotone" width={10} />
                        Disconnected
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className={`text-xs ${fp.is_active ? "text-green-600" : "text-text-disabled"}`}>
                      {fp.is_active ? "Active" : "Inactive"}
                    </span>
                    <span className="text-text-disabled text-xs">·</span>
                    <span className={`text-xs ${fp.publish_mode === "auto" ? "text-primary-main" : "text-warning-main"}`}>
                      {fp.publish_mode === "auto" ? "Auto-publish" : "Manual review"}
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 flex-shrink-0">
                {togglingId === fp.id && (
                  <Icon icon="svg-spinners:ring-resize" width={16} className="text-text-secondary" />
                )}
                <FanpageMenu
                  fp={fp}
                  onToggleActive={toggleActive}
                  onTogglePublishMode={togglePublishMode}
                  togglingId={togglingId}
                />
              </div>
            </div>
          ))}

          {fanpages.length === 0 && (
            <div className="card text-center py-12">
              <p className="text-sm text-text-secondary">No fanpages found.</p>
              <p className="text-xs text-text-secondary mt-2">
                Make sure Repliz credentials are configured in{" "}
                <a href="/settings" className="text-primary-main hover:underline">Settings</a>{" "}
                and then click "Sync from Repliz".
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
