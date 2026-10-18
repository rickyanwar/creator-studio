"use client";

import Link from "next/link";
import { Icon } from "@iconify/react";
import type { FanpageDetail } from "@/lib/types";
import type { BrandDetailOut } from "@/lib/socialBrandsApi";
import { BrandChannelCard } from "@/components/social-media/brand/BrandChannelCard";
import { BrandContentTable } from "@/components/social-media/brand/BrandContentTable";
import { BrandPacingCard } from "@/components/social-media/brand/BrandPacingCard";
import { BrandExplainerBox } from "@/components/social-media/brand/BrandExplainerBox";

export interface ChannelsTabProps {
  fanpageId: number;
  brand?: BrandDetailOut;
  fanpage: FanpageDetail;
  onSaved: () => void;
}

export function ChannelsTab({
  fanpageId,
  brand,
  fanpage,
  onSaved,
}: ChannelsTabProps) {
  const channelCount = brand?.channels?.length || 0;

  return (
    <div role="tabpanel" id="tab-panel-channels" aria-labelledby="tab-channels" className="space-y-6">
      {/* Channels section */}
      <section aria-labelledby="channels-heading" className="bg-surface border border-line rounded-2xl p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 id="channels-heading" className="text-lg font-semibold text-text">
              Channels ({channelCount})
            </h2>
            <p className="text-xs text-text2 mt-0.5">
              Every post this brand makes goes to all of its active channels, each on its own pacing.
            </p>
          </div>
          <Link
            href={`/brands/channels/new?brand=${fanpageId}`}
            className="btn-primary text-xs px-4 py-2 inline-flex items-center gap-1.5"
          >
            <Icon icon="solar:add-circle-bold" width={16} />
            <span>Add a channel</span>
          </Link>
        </div>

        {channelCount === 0 ? (
          <div className="text-center py-12 space-y-3 bg-surface2 border border-line rounded-xl p-6">
            <p className="text-sm text-text2">
              No channels connected to this brand yet.
            </p>
            <Link
              href={`/brands/channels/new?brand=${fanpageId}`}
              className="btn-primary text-xs px-4 py-2 inline-flex items-center gap-1.5"
            >
              Add channel
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {brand?.channels?.map((channel, idx) => (
              <BrandChannelCard
                key={channel.id}
                channel={channel}
                defaultSlotsOpen={idx < 2}
              />
            ))}
          </div>
        )}
      </section>

      {/* Brand Content Table */}
      <BrandContentTable
        brandId={fanpageId}
        content={brand?.content || []}
        publishMode={brand?.publish_mode || null}
      />

      {/* Brand Pacing Card */}
      <BrandPacingCard
        brandId={fanpageId}
        initialTimezone={brand?.timezone || fanpage.timezone || "Europe/London"}
        initialSleepStart={brand?.publish_sleep_start_hour ?? fanpage.publish_sleep_start_hour ?? null}
        initialSleepEnd={brand?.publish_sleep_end_hour ?? fanpage.publish_sleep_end_hour ?? null}
        initialDailyLimit={brand?.publish_daily_limit || fanpage.publish_daily_limit || 14}
        onSaved={onSaved}
      />

      {/* Explainer Box */}
      <BrandExplainerBox />
    </div>
  );
}
