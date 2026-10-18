"use client";

import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { RecentDeliveryOut } from "@/lib/socialBrandsApi";
import {
  formatDualTime,
  getDesignEngineBadge,
  getDeliveryStateBadge,
} from "./channelUtils";

interface Props {
  deliveries: RecentDeliveryOut[];
  targetTz: string;
  isLoading: boolean;
  error?: string | null;
  onRefresh: () => void;
}

export function HistoryTab({
  deliveries,
  targetTz,
  isLoading,
  error,
  onRefresh,
}: Props) {
  const [filter, setFilter] = useState<"all" | "posted" | "scheduled" | "problems">("all");

  const filteredDeliveries = deliveries.filter((item) => {
    const s = (item.state || "").toLowerCase();
    if (filter === "posted") {
      return s === "posted" || s === "published" || s.includes("fallback");
    }
    if (filter === "scheduled") {
      return s === "scheduled" || s === "planned";
    }
    if (filter === "problems") {
      return s === "failed" || s === "uncertain" || s.includes("error") || Boolean(item.last_error);
    }
    return true;
  });

  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-text">
              Recent posts on this channel
            </h2>
            <p className="text-xs text-text2 mt-0.5">
              History of recent dispatches, render engines, and live publishing results.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs text-text2 font-medium">Show:</label>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as any)}
              className="input-rect text-xs py-1.5 px-3 w-36"
            >
              <option value="all">All</option>
              <option value="posted">Posted</option>
              <option value="scheduled">Scheduled</option>
              <option value="problems">Problems</option>
            </select>
            <button
              type="button"
              onClick={onRefresh}
              className="btn-ghost text-xs p-2 rounded-md"
              title="Refresh deliveries"
            >
              <Icon icon="solar:refresh-bold" width={16} />
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-line bg-surface p-4 text-xs text-text2">
            Could not load recent deliveries: {error}
          </div>
        )}

        {isLoading ? (
          <div className="p-8 text-center text-xs text-text2">
            Loading recent posts...
          </div>
        ) : filteredDeliveries.length === 0 ? (
          <div className="p-8 text-center text-xs text-text2 border border-line rounded-xl bg-surface">
            No recent deliveries found for the selected filter.
          </div>
        ) : (
          <div className="overflow-x-auto border border-line rounded-xl">
            <table className="w-full border-collapse text-left text-xs min-w-[760px]">
              <thead>
                <tr className="bg-surface2 text-text2 border-b border-line">
                  <th scope="col" className="p-3.5 font-semibold">
                    When ({targetTz.split("/")[1]?.replace(/_/g, " ") || "Local"} · WIB)
                  </th>
                  <th scope="col" className="p-3.5 font-semibold">Post</th>
                  <th scope="col" className="p-3.5 font-semibold">Image made by</th>
                  <th scope="col" className="p-3.5 font-semibold">Sent by</th>
                  <th scope="col" className="p-3.5 font-semibold">Result</th>
                  <th scope="col" className="p-3.5 font-semibold text-right">Link</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-surface">
                {filteredDeliveries.map((del) => {
                  const timeDate = del.published_at || del.scheduled_for;
                  const { main, wib } = formatDualTime(timeDate, targetTz);
                  const eng = getDesignEngineBadge(del.design_engine);
                  const stateBadge = getDeliveryStateBadge(del.state);
                  const isPhone = (del.method || "").toLowerCase().includes("phone");

                  return (
                    <tr key={del.id} className="hover:bg-surface2 transition">
                      {/* When */}
                      <td className="p-3.5 whitespace-nowrap text-text2 tabular-nums">
                        <span className="font-medium text-text block">{main}</span>
                        <span className="text-[11px] text-text2 block">{wib}</span>
                      </td>

                      {/* Post */}
                      <td className="p-3.5 max-w-xs">
                        <span className="font-semibold text-text block line-clamp-1">
                          {del.title || `Delivery #${del.id}`}
                        </span>
                        <span className="text-[11px] text-text2 block">
                          {del.content_type || "Post"}
                        </span>
                      </td>

                      {/* Image made by */}
                      <td className="p-3.5 whitespace-nowrap">
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-semibold ${eng.bg} ${eng.text}`}
                          title={del.design_engine_note || undefined}
                        >
                          {eng.label}
                        </span>
                        {del.design_engine_note && (
                          <span className="block text-[10px] text-text2 mt-0.5 line-clamp-1 max-w-[140px]">
                            {del.design_engine_note}
                          </span>
                        )}
                      </td>

                      {/* Sent by */}
                      <td className="p-3.5 whitespace-nowrap font-medium">
                        <span className={isPhone ? "text-info" : "text-pri"}>
                          {del.method ? (del.method === "phone" ? "Phone" : del.method === "repliz" ? "Repliz" : del.method) : "—"}
                        </span>
                      </td>

                      {/* Result */}
                      <td className="p-3.5">
                        <div className="flex items-center gap-1.5">
                          <span className={`w-2 h-2 rounded-full ${stateBadge.dot}`} />
                          <span className={`font-semibold text-[11px] ${stateBadge.text}`}>
                            {stateBadge.label}
                          </span>
                        </div>
                        {del.last_error && (
                          <p className="text-[10px] text-bad mt-1 line-clamp-2 max-w-xs leading-normal">
                            {del.last_error}
                          </p>
                        )}
                      </td>

                      {/* Link */}
                      <td className="p-3.5 whitespace-nowrap text-right">
                        {del.facebook_permalink ? (
                          <a
                            href={del.facebook_permalink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-semibold text-pri hover:opacity-90 transition inline-flex items-center gap-1"
                          >
                            <span>Open post</span>
                            <Icon icon="solar:arrow-right-up-linear" width={12} />
                          </a>
                        ) : (
                          <span className="text-text2">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
