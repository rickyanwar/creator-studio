"use client";

import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { SocialTabs } from "@/components/social-media/SocialTabs";
import { getBrand, setBrandGptcli } from "@/lib/socialBrandsApi";
import { getFanpage, getAiEngineStatus } from "@/lib/api";
import type { BrandDetailOut } from "@/lib/socialBrandsApi";
import type { Fanpage } from "@/lib/types";
import DesignTemplatesSection from "@/components/brand/DesignTemplatesSection";
import { BrandPacingCard } from "@/components/social-media/brand/BrandPacingCard";
import { BrandContentTable } from "@/components/social-media/brand/BrandContentTable";
import { BrandAiImageCard } from "@/components/social-media/brand/BrandAiImageCard";
import { BrandChannelCard } from "@/components/social-media/brand/BrandChannelCard";
import { BrandExplainerBox } from "@/components/social-media/brand/BrandExplainerBox";

function getInitials(name: string): string {
  if (!name) return "??";
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

interface PageProps {
  params: {
    id: string;
  };
}

export default function BrandDetailPage({ params }: PageProps) {
  const brandId = parseInt(params.id, 10);
  const isValidId = !isNaN(brandId) && brandId > 0;

  const {
    data: brand,
    error: brandError,
    isLoading: brandLoading,
    mutate: mutateBrand,
  } = useSWR<BrandDetailOut>(
    isValidId ? ["social-brand", brandId] : null,
    () => getBrand(brandId)
  );

  const {
    data: fanpage,
    error: fanpageError,
    isLoading: fanpageLoading,
    mutate: mutateFanpage,
  } = useSWR<Fanpage>(
    isValidId ? ["fanpage", brandId] : null,
    () => getFanpage(brandId).then((r) => r.data as Fanpage)
  );

  const { data: aiEngineStatus } = useSWR(
    "ai-engine-status",
    () => getAiEngineStatus().then((r) => r.data),
    { revalidateOnFocus: false }
  );

  const handleRevalidate = () => {
    mutateBrand();
    mutateFanpage();
  };

  const handleToggleGptcli = async (enabled: boolean) => {
    if (!brand) return;
    await mutateBrand(
      {
        ...brand,
        gptcli_enabled_for_brand: enabled,
      },
      false
    );
    try {
      await setBrandGptcli(brandId, enabled);
      mutateBrand();
    } catch (err) {
      mutateBrand();
      throw err;
    }
  };

  const is404 =
    !isValidId ||
    brandError?.response?.status === 404 ||
    brandError?.status === 404 ||
    fanpageError?.response?.status === 404 ||
    fanpageError?.status === 404;

  if (is404) {
    return (
      <div className="space-y-6">
        <SocialTabs />
        <div className="card text-center py-16 space-y-4">
          <Icon
            icon="solar:folder-with-files-bold-duotone"
            width={48}
            className="mx-auto text-text-disabled"
          />
          <h1 className="text-xl font-bold text-text-primary">Brand not found.</h1>
          <p className="text-sm text-text-secondary">
            The brand you are looking for does not exist or has been removed.
          </p>
          <div>
            <Link href="/social-media" className="btn-primary inline-flex">
              Back to Brands
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const hasError = (brandError || fanpageError) && !is404;

  if (hasError) {
    return (
      <div className="space-y-6">
        <SocialTabs />
        <div className="card text-center py-16 space-y-4 border border-error-light/40 bg-error-lighter/10">
          <Icon
            icon="solar:danger-triangle-bold-duotone"
            width={48}
            className="mx-auto text-error-main"
          />
          <h1 className="text-xl font-bold text-text-primary">
            Failed to load brand
          </h1>
          <p className="text-sm text-text-secondary">
            {brandError?.message ||
              fanpageError?.message ||
              "Could not fetch brand details. Please check your network and try again."}
          </p>
          <div>
            <button
              type="button"
              onClick={handleRevalidate}
              className="btn-secondary inline-flex"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (brandLoading || fanpageLoading || !brand || !fanpage) {
    return (
      <div className="space-y-6 animate-pulse">
        <SocialTabs />
        {/* Breadcrumb skeleton */}
        <div className="h-4 w-48 bg-bg-paper-hover rounded" />
        {/* Header skeleton */}
        <div className="flex flex-wrap items-center gap-4">
          <div className="w-16 h-16 rounded-2xl bg-bg-paper-hover" />
          <div className="space-y-2 flex-1">
            <div className="h-7 w-64 bg-bg-paper-hover rounded" />
            <div className="h-4 w-80 bg-bg-paper-hover rounded" />
          </div>
        </div>
        {/* Card skeletons */}
        <div className="h-44 bg-bg-paper-hover rounded-xl" />
        <div className="h-64 bg-bg-paper-hover rounded-xl" />
        <div className="h-36 bg-bg-paper-hover rounded-xl" />
      </div>
    );
  }

  const channelCount = brand.channels?.length || 0;
  const publishingTodayCount = (brand.channels || []).filter(
    (c) => c.is_active && (c.daily_target > 0 || (c.today?.published || 0) > 0)
  ).length;

  const subtitleParts = [
    "Content Project",
    brand.niche || null,
    `${channelCount} ${channelCount === 1 ? "channel" : "channels"}`,
    `${publishingTodayCount} publishing today`,
  ].filter(Boolean);

  const codexQuotaPct = aiEngineStatus?.codex_usage?.window_pct;

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* 1. Breadcrumbs & SocialTabs */}
      <div className="space-y-4">
        <nav
          aria-label="Breadcrumb"
          className="text-xs text-text-secondary flex items-center gap-1.5 flex-wrap"
        >
          <Link
            href="/social-media"
            className="hover:text-text-primary transition-colors"
          >
            Social Media
          </Link>
          <span aria-hidden="true" className="opacity-50">
            /
          </span>
          <Link
            href="/social-media"
            className="hover:text-text-primary transition-colors"
          >
            Brands
          </Link>
          <span aria-hidden="true" className="opacity-50">
            /
          </span>
          <span className="text-text-primary font-medium">{brand.name}</span>
        </nav>

        <SocialTabs />
      </div>

      {/* 2. Header */}
      <header className="flex flex-wrap items-center gap-4 sm:gap-6 justify-between">
        <div className="flex items-center gap-4 min-w-0">
          <div
            aria-hidden="true"
            className="w-16 h-16 rounded-2xl bg-primary-lighter text-primary-darker flex items-center justify-center font-extrabold text-2xl shrink-0 shadow-sm"
          >
            {getInitials(brand.name)}
          </div>
          <div className="min-w-0">
            <h1 className="text-2xl sm:text-3xl font-bold text-text-primary truncate">
              {brand.name}
            </h1>
            <p className="text-xs sm:text-sm text-text-secondary mt-1">
              {subtitleParts.join(" · ")}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link
            href={`/fanpages/${brand.id}`}
            className="btn-secondary text-xs sm:text-sm px-4 py-2"
          >
            Brand settings
          </Link>
          <Link
            href={`/social-media/channels/new?brand=${brand.id}`}
            className="btn-primary text-xs sm:text-sm px-4 py-2"
          >
            <Icon icon="solar:add-circle-bold" width={18} />
            <span>Add channel</span>
          </Link>
        </div>
      </header>

      {/* 3. Brand pacing card */}
      <BrandPacingCard
        brandId={brand.id}
        initialTimezone={brand.timezone || fanpage.timezone}
        initialSleepStart={
          brand.publish_sleep_start_hour ?? fanpage.publish_sleep_start_hour
        }
        initialSleepEnd={
          brand.publish_sleep_end_hour ?? fanpage.publish_sleep_end_hour
        }
        initialDailyLimit={
          brand.publish_daily_limit || fanpage.publish_daily_limit || 14
        }
        onSaved={handleRevalidate}
      />

      {/* 4. Content this brand makes */}
      <BrandContentTable
        brandId={brand.id}
        content={brand.content}
        publishMode={brand.publish_mode}
      />

      {/* 5. AI image editing */}
      <BrandAiImageCard
        brandId={brand.id}
        gptcliEnabledForBrand={brand.gptcli_enabled_for_brand}
        gptcliGlobalEnabled={brand.gptcli_global_enabled}
        aiImagesToday={brand.ai_images_today}
        quotaPct={codexQuotaPct}
        onToggle={handleToggleGptcli}
      />

      {/* 6. Channels */}
      <section aria-labelledby="channels-heading" className="space-y-4">
        <div className="flex items-center justify-between">
          <h2
            id="channels-heading"
            className="text-lg font-bold text-text-primary"
          >
            Channels ({channelCount})
          </h2>
          <Link
            href={`/social-media/channels/new?brand=${brand.id}`}
            className="text-xs sm:text-sm font-semibold text-primary-main hover:underline"
          >
            + Add another channel
          </Link>
        </div>

        {channelCount === 0 ? (
          <div className="card text-center py-10 space-y-3">
            <p className="text-sm text-text-secondary">
              No channels connected to this brand yet.
            </p>
            <div>
              <Link
                href={`/social-media/channels/new?brand=${brand.id}`}
                className="btn-primary text-xs px-4 py-2 inline-flex"
              >
                Add channel
              </Link>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {brand.channels.map((channel, idx) => (
              <BrandChannelCard
                key={channel.id}
                channel={channel}
                defaultSlotsOpen={idx < 2}
              />
            ))}
          </div>
        )}
      </section>

      {/* 7. Explainer box */}
      <BrandExplainerBox />

      {/* 8. Design Templates */}
      <DesignTemplatesSection
        fanpage={fanpage}
        onSaved={handleRevalidate}
      />
    </div>
  );
}
