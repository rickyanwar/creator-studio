"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { SocialDestination } from "@/lib/types";
import type { BrandSummaryOut } from "@/lib/socialBrandsApi";

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
  const isPage = destination.kind === "facebook_page";
  const kindLabel = isPage ? "Facebook Page" : "Facebook Profile";
  const brandName = brand?.name || "Brand";

  return (
    <div className="flex flex-col gap-4">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-xs text-text-secondary flex-wrap">
        <Link href="/social-media" className="hover:text-primary-main transition">
          Social Media
        </Link>
        <span aria-hidden="true">/</span>
        {destination.content_project_id ? (
          <Link
            href={`/social-media/brands/${destination.content_project_id}`}
            className="hover:text-primary-main transition"
          >
            {brandName}
          </Link>
        ) : (
          <span>{brandName}</span>
        )}
        <span aria-hidden="true">/</span>
        <span className="text-text-primary font-medium">
          {kindLabel} · {destination.name}
        </span>
      </nav>

      {/* Main Title & Action Bar */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <div
            className={`w-12 h-12 rounded-xl flex items-center justify-center text-white flex-shrink-0 ${
              isPage ? "bg-[#0866FF]" : "bg-slate-700"
            }`}
          >
            <Icon icon="solar:facebook-bold" width={28} height={28} />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-text-primary">{destination.name}</h1>
            <p className="text-xs text-text-secondary mt-0.5">
              {kindLabel} · brand {brandName} · audience {destination.timezone}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Status badge */}
          <div
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
              destination.is_active
                ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/60"
                : "bg-slate-800 text-slate-300 border border-slate-700"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                destination.is_active ? "bg-emerald-500 animate-pulse" : "bg-slate-500"
              }`}
            />
            {destination.is_active ? "Live" : "Paused"}
          </div>

          {/* Activate / Pause button */}
          <button
            type="button"
            disabled={isUpdatingActive}
            onClick={() => onToggleActive(!destination.is_active)}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition flex items-center gap-1.5 disabled:opacity-50 ${
              destination.is_active
                ? "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                : "bg-primary-main hover:bg-primary-dark text-white shadow-primary-btn"
            }`}
          >
            {isUpdatingActive ? (
              <>
                <Icon icon="solar:refresh-circle-bold-duotone" className="animate-spin" width={16} />
                Updating...
              </>
            ) : destination.is_active ? (
              <>
                <Icon icon="solar:pause-bold" width={14} />
                Pause
              </>
            ) : (
              <>
                <Icon icon="solar:play-bold" width={14} />
                Activate
              </>
            )}
          </button>
        </div>
      </header>

      {/* Guard error banner */}
      {guardError && (
        <div className="rounded-xl border border-error-main/40 bg-error-lighter/20 p-3.5 flex items-start justify-between gap-3 text-xs text-error-dark dark:text-rose-300">
          <div className="flex items-start gap-2.5">
            <Icon
              icon="solar:danger-triangle-bold-duotone"
              width={18}
              className="text-error-main flex-shrink-0 mt-0.5"
            />
            <div>
              <p className="font-bold text-sm">Cannot change channel status</p>
              <p className="mt-0.5">{guardError}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onDismissError}
            className="text-error-main hover:opacity-75 transition p-1"
          >
            <Icon icon="solar:close-circle-bold-duotone" width={16} />
          </button>
        </div>
      )}
    </div>
  );
}
