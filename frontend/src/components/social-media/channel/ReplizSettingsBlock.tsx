"use client";

import React from "react";
import { Icon } from "@iconify/react";
import type { SocialDestination } from "@/lib/types";
import type { ReplizAccountsOut } from "@/lib/socialBrandsApi";

interface Props {
  destination: SocialDestination;
  replizData?: ReplizAccountsOut | null;
  replizAccountId: string;
  setReplizAccountId: (id: string) => void;
}

export function ReplizSettingsBlock({
  destination,
  replizData,
  replizAccountId,
  setReplizAccountId,
}: Props) {
  return (
    <section className="card p-5 space-y-4">
      <h3 className="text-sm font-bold text-text flex items-center gap-2">
        <Icon icon="solar:cloud-upload-bold" className="text-pri" width={16} />
        Repliz Settings
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="label">Repliz account for this Page</label>
          <select
            value={replizAccountId}
            onChange={(e) => setReplizAccountId(e.target.value)}
            className="input-rect"
          >
            <option value="">Select connected Repliz account...</option>
            {replizData?.accounts?.map((acc) => {
              const usedByOther =
                acc.used_by_destination_id &&
                acc.used_by_destination_id !== destination.id;
              return (
                <option
                  key={acc.id}
                  value={acc.id}
                  disabled={Boolean(usedByOther)}
                >
                  {acc.name} ({acc.type || "account"})
                  {usedByOther
                    ? ` — used by ${acc.used_by_destination_name || "another channel"}`
                    : ""}
                </option>
              );
            })}
          </select>
        </div>

        <div>
          <span className="label">Connection Status</span>
          <div className="h-[42px] px-3 rounded-md bg-surface2 border border-line flex items-center gap-2 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                replizData?.configured ? "bg-emerald-500" : "bg-amber-500"
              }`}
            />
            <span className="text-text">
              {replizData?.configured
                ? "Connected · status checked every 5 min"
                : replizData?.error || "Repliz not configured"}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
