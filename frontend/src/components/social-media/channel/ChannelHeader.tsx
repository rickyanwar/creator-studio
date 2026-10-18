"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { SocialDestination } from "@/lib/types";
import type { BrandSummaryOut } from "@/lib/socialBrandsApi";
import { getPlatformRuleInfo } from "@/lib/platformChannelsApi";

interface Props {
  destination: SocialDestination;
  brand?: BrandSummaryOut | null;
  isUpdatingActive: boolean;
  onToggleActive: (newActive: boolean) => Promise<void>;
  guardError: string | null;
  onDismissError: () => void;
}

export function ChannelHeader({
  destination,
  brand,
  isUpdatingActive,
  onToggleActive,
  guardError,
  onDismissError,
}: Props) {
  const platformInfo = getPlatformRuleInfo(destination.kind);
  const kindLabel = platformInfo.label;
  const brandName = brand?.name || "Brand";

  return (
    <div className="flex flex-col gap-4">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-xs text-text2 flex-wrap">
        <Link href="/brands" className="hover:text-pri transition">
          Brands
        </Link>
        <span aria-hidden="true">/</span>
        {destination.content_project_id ? (
          <Link
            href={`/brands/${destination.content_project_id}`}
            className="hover:text-pri transition"
          >
            {brandName}
          </Link>
        ) : (
          <span>{brandName}</span>
        )}
        <span aria-hidden="true">/</span>
        <span className="text-text font-medium">
          {kindLabel} · {destination.name}
        </span>
      </nav>

      {/* Main Title & Action Bar */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <div
            className={`w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0 ${platformInfo.iconBg}`}
          >
            <Icon icon={platformInfo.icon} width={28} height={28} />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-text">{destination.name}</h1>
            <p className="text-xs text-text2 mt-0.5">
              {kindLabel} · brand {brandName} · audience {destination.timezone}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Status badge */}
          <div
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
              destination.is_active
                ? "bg-pri-soft text-pri-text"
                : "bg-surface2 text-text2 border border-line"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                destination.is_active ? "bg-pri animate-pulse" : "bg-text2"
              }`}
            />
            {destination.is_active ? "Live" : "Paused"}
          </div>

          {/* Activate / Pause button */}
          <button
            type="button"
            disabled={isUpdatingActive}
            onClick={() => onToggleActive(!destination.is_active)}
            className={`h-9 px-4 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer disabled:opacity-50 ${
              destination.is_active
                ? "bg-surface hover:bg-surface2 text-text border border-line"
                : "bg-pri hover:opacity-90 text-pri-fg"
            }`}
          >
            {isUpdatingActive ? (
              <span>Saving…</span>
            ) : destination.is_active ? (
              <>
                <Icon icon="solar:pause-bold" width={14} />
                <span>Pause</span>
              </>
            ) : (
              <>
                <Icon icon="solar:play-bold" width={14} />
                <span>Activate</span>
              </>
            )}
          </button>
        </div>
      </header>

      {/* Platform limits banner */}
      <div className="flex items-center gap-2 p-3 rounded-xl bg-surface border border-line text-xs text-text2">
        <Icon icon="solar:info-circle-bold" className="text-pri flex-shrink-0" width={16} />
        <div>
          <strong className="text-text font-semibold">{kindLabel} Limits: </strong>
          <span>{platformInfo.limitsSummary}</span>
        </div>
      </div>

      {/* Guard error banner */}
      {guardError && (
        <div className="rounded-xl border border-bad/30 bg-bad-soft p-3.5 flex items-start justify-between gap-3 text-xs text-bad">
          <div className="flex items-start gap-2.5">
            <Icon
              icon="solar:danger-triangle-bold-duotone"
              width={18}
              className="text-bad flex-shrink-0 mt-0.5"
            />
            <div>
              <p className="font-bold text-sm">Cannot change channel status</p>
              <p className="mt-0.5">{guardError}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onDismissError}
            className="text-bad hover:opacity-75 transition p-1"
          >
            <Icon icon="solar:close-circle-bold-duotone" width={16} />
          </button>
        </div>
      )}
    </div>
  );
}
