"use client";

import React, { useState, Suspense } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  getSocialDestination,
  updateSocialDestination,
  listSocialDestinations,
  listPhoneDevices,
  listDeviceAssignments,
} from "@/lib/api";
import {
  listBrands,
  listReplizAccounts,
  getEstimatedTimes,
  getRecentDeliveries,
} from "@/lib/socialBrandsApi";
import type { SocialDestinationUpdate } from "@/lib/types";
import { getErrorMessage } from "@/components/social-media/channel/channelUtils";
import { ChannelHeader } from "@/components/social-media/channel/ChannelHeader";
import { PublishTab } from "@/components/social-media/channel/PublishTab";
import { FallbackTab } from "@/components/social-media/channel/FallbackTab";
import { MediaTab } from "@/components/social-media/channel/MediaTab";
import { PacingTab } from "@/components/social-media/channel/PacingTab";
import { ContentTab } from "@/components/social-media/channel/ContentTab";
import { HistoryTab } from "@/components/social-media/channel/HistoryTab";

const TABS = [
  {
    id: "publish",
    label: "How it publishes",
    desc: "How posts reach this channel: from a phone (a Facebook Profile on your device), through Repliz, or both. Also what happens if the phone fails, and which media types are allowed.",
  },
  {
    id: "fallback",
    label: "If the phone fails",
    desc: "Safety rules and automatic recovery mechanisms when an Android device or Facebook app encounters an issue.",
  },
  {
    id: "media",
    label: "Media this channel accepts",
    desc: "Allowed post types: Single image cards, multi-image albums, and vertical reels.",
  },
  {
    id: "pacing",
    label: "Audience location & times",
    desc: "Where this channel’s audience lives and when posts go out on their local clock: active hours, posts per day, minimum gap, and estimated slots.",
  },
  {
    id: "content",
    label: "Content comes from the brand",
    desc: "What this channel receives. Content modes are managed on the brand; this channel filters posts by accepted media formats.",
  },
  {
    id: "history",
    label: "Recent posts",
    desc: "Every post sent to this channel: timestamp, AI rendering engine, delivery method, and publishing status.",
  },
] as const;

type TabKey = typeof TABS[number]["id"];

function ChannelDetailInner() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();

  const idNum = Number(params?.id);
  const activeTab: TabKey = (searchParams.get("tab") as TabKey) || "publish";

  const setTab = (t: TabKey) => {
    const p = new URLSearchParams(searchParams.toString());
    p.set("tab", t);
    router.replace(`?${p.toString()}`, { scroll: false });
  };

  // ── SWR Data Fetching ──
  const {
    data: destination,
    isLoading: loadingDest,
    error: destError,
    mutate: mutateDest,
  } = useSWR(idNum ? `social-destination-${idNum}` : null, () =>
    getSocialDestination(idNum).then((r) => r.data)
  );

  const { data: brands = [] } = useSWR("social-brands", listBrands);

  const { data: profiles = [] } = useSWR("social-profiles", () =>
    listSocialDestinations({ kind: "facebook_profile" }).then((r) => r.data)
  );

  const { data: devices = [], mutate: mutateDevices } = useSWR("phone-devices", () =>
    listPhoneDevices().then((r) => r.data)
  );

  const { data: assignments = [], mutate: mutateAssignments } = useSWR(
    idNum ? `device-assignments-${idNum}` : null,
    () => listDeviceAssignments({ destination_id: idNum }).then((r) => r.data)
  );

  const { data: replizData } = useSWR("repliz-accounts", listReplizAccounts);

  const {
    data: estimatedTimes,
    isLoading: loadingEstimates,
    error: estimatesError,
    mutate: mutateEstimates,
  } = useSWR(idNum ? `estimated-times-${idNum}` : null, () => getEstimatedTimes(idNum));

  const {
    data: recentDeliveries = [],
    isLoading: loadingDeliveries,
    error: deliveriesError,
    mutate: mutateDeliveries,
  } = useSWR(idNum ? `recent-deliveries-${idNum}` : null, () =>
    getRecentDeliveries(idNum, 30)
  );

  // ── Actions ──
  const [isUpdatingActive, setIsUpdatingActive] = useState(false);
  const [guardError, setGuardError] = useState<string | null>(null);

  const handleToggleActive = async (newActive: boolean) => {
    if (!destination) return;
    setIsUpdatingActive(true);
    setGuardError(null);
    try {
      const res = await updateSocialDestination(destination.id, { is_active: newActive });
      await mutateDest(res.data, false);
      mutateEstimates();
    } catch (err) {
      setGuardError(getErrorMessage(err));
    } finally {
      setIsUpdatingActive(false);
    }
  };

  const handleSaveSettings = async (payload: SocialDestinationUpdate) => {
    if (!destination) return;
    const res = await updateSocialDestination(destination.id, payload);
    await mutateDest(res.data, false);
    mutateEstimates();
  };

  const handleRefreshAll = () => {
    mutateDest();
    mutateDevices();
    mutateAssignments();
    mutateEstimates();
    mutateDeliveries();
  };

  if (loadingDest) {
    return (
      <div className="max-w-5xl mx-auto p-8 text-center text-sm text-text-secondary">
        Loading channel settings...
      </div>
    );
  }

  if (destError || !destination) {
    return (
      <div className="max-w-5xl mx-auto p-8">
        <div className="rounded-xl border border-error-main/40 bg-error-lighter/20 p-5 text-sm text-error-dark dark:text-rose-300">
          <p className="font-bold">Failed to load channel</p>
          <p className="mt-1">{destError ? getErrorMessage(destError) : "Channel not found."}</p>
        </div>
      </div>
    );
  }

  const currentBrand = brands.find((b) => b.id === destination.content_project_id);
  const currentTabObj = TABS.find((t) => t.id === activeTab) || TABS[0];

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-16">
      {/* ── Channel Header ── */}
      <ChannelHeader
        destination={destination}
        brand={currentBrand}
        isUpdatingActive={isUpdatingActive}
        onToggleActive={handleToggleActive}
        guardError={guardError}
        onDismissError={() => setGuardError(null)}
      />

      {/* ── Navigation Tabs ── */}
      <div className="space-y-3">
        <div
          role="tablist"
          aria-label="Channel settings"
          className="flex items-center gap-1 border-b border-divider-soft overflow-x-auto pb-px"
        >
          {TABS.map((t) => {
            const isSelected = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={isSelected}
                onClick={() => setTab(t.id)}
                className={`px-4 py-3 text-xs md:text-sm font-semibold whitespace-nowrap border-b-2 transition -mb-px ${
                  isSelected
                    ? "border-primary-main text-primary-main"
                    : "border-transparent text-text-secondary hover:text-text-primary"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Tab Description Banner */}
        <div className="p-3.5 rounded-xl bg-[#0f1d33] border border-[#1e3a5f] text-xs text-text-secondary leading-relaxed">
          <strong className="text-text-primary">{currentTabObj.label}</strong> — {currentTabObj.desc}
        </div>
      </div>

      {/* ── Tab Content ── */}
      <div>
        {activeTab === "publish" && (
          <PublishTab
            destination={destination}
            profiles={profiles}
            devices={devices}
            assignments={assignments}
            replizData={replizData}
            estimatedTimes={estimatedTimes}
            isLoadingEstimates={loadingEstimates}
            onSave={handleSaveSettings}
            onRefreshData={handleRefreshAll}
            onGoPacing={() => setTab("pacing")}
          />
        )}

        {activeTab === "fallback" && <FallbackTab />}

        {activeTab === "media" && (
          <MediaTab destination={destination} onSave={handleSaveSettings} />
        )}

        {activeTab === "pacing" && (
          <PacingTab
            destination={destination}
            brand={currentBrand}
            estimatedTimes={estimatedTimes}
            isLoadingEstimates={loadingEstimates}
            onSave={handleSaveSettings}
          />
        )}

        {activeTab === "content" && (
          <ContentTab destination={destination} brand={currentBrand} />
        )}

        {activeTab === "history" && (
          <HistoryTab
            deliveries={recentDeliveries}
            targetTz={destination.timezone}
            isLoading={loadingDeliveries}
            error={deliveriesError ? getErrorMessage(deliveriesError) : null}
            onRefresh={mutateDeliveries}
          />
        )}
      </div>
    </div>
  );
}

export default function ChannelDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-5xl mx-auto p-8 text-center text-sm text-text-secondary">
          Loading channel settings...
        </div>
      }
    >
      <ChannelDetailInner />
    </Suspense>
  );
}
