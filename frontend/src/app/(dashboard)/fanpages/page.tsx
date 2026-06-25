"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { listFanpages, updateFanpage, triggerFanpageSync } from "@/lib/api";
import type { Fanpage } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = () => listFanpages().then((r) => r.data as Fanpage[]);

export default function FanpagesPage() {
  const { data: fanpages = [], isLoading, mutate } = useSWR("fanpages", fetcher);
  const [loadingSync, setLoadingSync]   = useState(false);
  const [togglingId,  setTogglingId]    = useState<number | null>(null);

  async function toggleActive(fp: Fanpage) {
    setTogglingId(fp.id);
    try { await updateFanpage(fp.id, { is_active: !fp.is_active }); mutate(); }
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
                  <div className="text-xs text-text-secondary mt-0.5">
                    {fp.publish_mode === "auto" ? (
                      <span className="text-primary-main">Auto-publish</span>
                    ) : (
                      <span className="text-warning-main">Manual review</span>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3 flex-shrink-0">
                <button
                  onClick={() => toggleActive(fp)}
                  disabled={togglingId === fp.id}
                  className="flex items-center gap-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
                  title={fp.is_active ? "Deactivate" : "Activate"}
                >
                  {togglingId === fp.id ? (
                    <Icon icon="solar:refresh-bold-duotone" width={20} className="animate-spin text-text-disabled" />
                  ) : (
                    <Icon
                      icon={fp.is_active ? "solar:toggle-on-bold-duotone" : "solar:toggle-off-bold-duotone"}
                      width={24}
                      className={fp.is_active ? "text-primary-main" : "text-text-disabled"}
                    />
                  )}
                  {fp.is_active ? "Active" : "Inactive"}
                </button>

                <Link href={`/fanpages/${fp.id}`} className="btn-ghost flex items-center gap-1.5">
                  <Icon icon="solar:tuning-2-bold-duotone" width={14} />
                  Configure
                </Link>
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
