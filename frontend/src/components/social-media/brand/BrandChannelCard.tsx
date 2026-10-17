"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { getEstimatedTimes } from "@/lib/socialBrandsApi";
import type { ChannelSummaryOut, EstimatedTimesOut } from "@/lib/socialBrandsApi";

export interface BrandChannelCardProps {
  channel: ChannelSummaryOut;
  defaultSlotsOpen?: boolean;
}

function getKindLabel(kind: string): string {
  if (kind === "facebook_page") return "Facebook Page";
  if (kind === "facebook_profile") return "Facebook Profile";
  return kind.replace(/_/g, " ");
}

function getChannelIcon(kind: string) {
  if (kind.startsWith("facebook")) {
    return <Icon icon="mingcute:facebook-fill" width={20} className="text-[#1877F2]" />;
  }
  if (kind.startsWith("instagram") || kind === "ig") {
    return <Icon icon="skill-icons:instagram" width={20} />;
  }
  return <Icon icon="solar:flag-bold-duotone" width={20} className="text-primary-main" />;
}

export function BrandChannelCard({
  channel,
  defaultSlotsOpen = false,
}: BrandChannelCardProps) {
  const [slotsOpen, setSlotsOpen] = useState(defaultSlotsOpen);

  const {
    data: estimatedTimes,
    isLoading: slotsLoading,
    error: slotsError,
  } = useSWR<EstimatedTimesOut>(
    slotsOpen ? ["estimated-times", channel.id] : null,
    () => getEstimatedTimes(channel.id),
    { revalidateOnFocus: false }
  );

  const publishesVia =
    channel.mode === "phone"
      ? "Phone"
      : channel.mode === "repliz"
      ? "Repliz"
      : channel.mode === "mixed"
      ? `Split ${channel.phone_daily_target} phone + ${channel.repliz_daily_target} Repliz`
      : channel.mode;

  const devicesText =
    channel.devices && channel.devices.length > 0
      ? `Phone: ${channel.devices.map((d) => d.name).join(", ")}`
      : null;

  const tz = channel.timezone || "Europe/London";
  const tzCity = tz.split("/")[1]?.replace(/_/g, " ") || tz;

  const isPaused = !channel.is_active;
  const hasAttention =
    channel.attention?.some((a) => a.level === "error" || a.level === "warn") ||
    (channel.today && channel.today.needs_attention > 0);

  const statusLabel = isPaused ? "Paused" : hasAttention ? "Needs attention" : "Live";
  const statusDot = isPaused
    ? "bg-grey-500"
    : hasAttention
    ? "bg-warning-main"
    : "bg-emerald-500";
  const statusTextColor = isPaused
    ? "text-text-disabled"
    : hasAttention
    ? "text-warning-main"
    : "text-emerald-400";

  const slots = estimatedTimes?.slots || [];

  return (
    <article className="card space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-bg-paper-hover border border-divider-soft flex items-center justify-center shrink-0">
            {getChannelIcon(channel.kind)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold text-text-primary">{channel.name}</h3>
              <span className="flex items-center gap-1.5 text-xs font-semibold">
                <span className={`w-2 h-2 rounded-full ${statusDot}`} />
                <span className={statusTextColor}>{statusLabel}</span>
              </span>
            </div>
            <p className="text-xs text-text-secondary">{getKindLabel(channel.kind)}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/social-media/channels/${channel.id}`}
            className="btn-secondary text-xs px-3.5 py-1.5"
          >
            Change how it publishes
          </Link>
          <Link
            href={`/social-media/channels/${channel.id}?tab=pacing`}
            className="btn-ghost text-xs px-3.5 py-1.5"
          >
            Pacing
          </Link>
        </div>
      </div>

      {/* Grid details */}
      <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs pt-1">
        <div className="space-y-1">
          <dt className="text-text-secondary font-medium">Publishes via</dt>
          <dd className="m-0 font-semibold text-text-primary">{publishesVia}</dd>
          {devicesText && (
            <p className="text-[11px] text-text-secondary">{devicesText}</p>
          )}
        </div>

        <div className="space-y-1">
          <dt className="text-text-secondary font-medium">Pacing</dt>
          <dd className="m-0 font-semibold text-text-primary">
            {channel.daily_target}/day · {channel.min_gap_minutes} min gap
          </dd>
          <p className="text-[11px] text-text-secondary">{channel.timezone}</p>
        </div>

        <div className="space-y-1">
          <dt className="text-text-secondary font-medium">Media</dt>
          <dd className="m-0 flex flex-wrap gap-1">
            {channel.supported_media && channel.supported_media.length > 0 ? (
              channel.supported_media.map((media) => (
                <span key={media} className="badge badge-gray text-[10px] capitalize">
                  {media}
                </span>
              ))
            ) : (
              <span className="text-text-disabled">—</span>
            )}
          </dd>
        </div>

        <div className="space-y-1">
          <dt className="text-text-secondary font-medium">Today</dt>
          <dd className="m-0 font-semibold text-text-primary">
            {channel.today?.published ?? 0} of {channel.daily_target} published
          </dd>
          <p className="text-[11px] text-text-secondary">
            {channel.today?.scheduled ?? 0} scheduled · {channel.today?.failed ?? 0} failed
          </p>
        </div>
      </dl>

      {/* Attention items */}
      {channel.attention && channel.attention.length > 0 && (
        <div className="space-y-1.5 pt-1">
          {channel.attention.map((item, idx) => {
            const isErr = item.level === "error";
            return (
              <div
                key={idx}
                className={`p-2.5 rounded-lg text-xs flex items-center gap-2 ${
                  isErr
                    ? "bg-rose-500/10 border border-rose-500/30 text-rose-300"
                    : "bg-amber-500/10 border border-amber-500/30 text-amber-300"
                }`}
              >
                <Icon
                  icon="solar:danger-triangle-bold-duotone"
                  width={16}
                  className={isErr ? "text-rose-400 shrink-0" : "text-amber-400 shrink-0"}
                />
                <span>{item.message}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Collapsible Today's slots */}
      <div className="pt-2 border-t border-divider-soft">
        <button
          type="button"
          onClick={() => setSlotsOpen(!slotsOpen)}
          className="flex items-center justify-between w-full py-2 px-3 rounded-lg bg-bg-paper-hover text-xs font-semibold text-text-primary hover:bg-bg-paper-hover/80 transition"
        >
          <span className="flex items-center gap-2">
            <Icon icon="solar:clock-circle-bold-duotone" width={16} className="text-primary-main" />
            <span>Today&apos;s slots</span>
            {slots.length > 0 && (
              <span className="text-text-secondary font-normal">({slots.length})</span>
            )}
          </span>
          <Icon
            icon="solar:alt-arrow-down-bold-duotone"
            width={16}
            className={`text-text-secondary transition-transform duration-200 ${
              slotsOpen ? "rotate-180" : ""
            }`}
          />
        </button>

        {slotsOpen && (
          <div className="pt-2.5 space-y-1.5">
            {slotsLoading ? (
              <p className="text-xs text-text-secondary py-3 text-center">
                Loading today&apos;s slots…
              </p>
            ) : slotsError ? (
              <p className="text-xs text-error-main py-2">
                Could not load schedule slots.
              </p>
            ) : slots.length === 0 ? (
              <p className="text-xs text-text-secondary py-3 text-center">
                No slots scheduled for today yet. Check daily posts target and active hours.
              </p>
            ) : (
              <div className="space-y-1">
                {slots.map((s, idx) => (
                  <div
                    key={s.delivery_id ?? s.index ?? idx}
                    className="flex flex-wrap items-center justify-between py-2 px-3 rounded-lg bg-bg-paper-hover border border-divider-soft text-xs text-text-secondary gap-2"
                  >
                    <span className="font-semibold text-text-primary">
                      Slot {(s.index ?? idx) + 1}
                    </span>
                    <span className="tabular-nums">
                      ~{s.local_time} {tzCity}
                    </span>
                    <span className="tabular-nums font-mono text-[11px]">
                      {s.wib_time} WIB
                    </span>
                    <span
                      className={`capitalize font-semibold ${
                        s.method === "phone" ? "text-sky-400" : "text-purple-400"
                      }`}
                    >
                      {s.method || "auto"}
                    </span>
                    <span className="badge badge-gray text-[10px] capitalize">
                      {s.status || "Planned"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
