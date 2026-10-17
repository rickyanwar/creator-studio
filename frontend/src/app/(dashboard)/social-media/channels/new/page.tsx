"use client";

import React, { useState, useEffect, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { createSocialDestination, listSocialDestinations } from "@/lib/api";
import { listBrands, listReplizAccounts } from "@/lib/socialBrandsApi";
import type { SocialDestinationCreate } from "@/lib/types";
import { getErrorMessage } from "@/components/social-media/channel/channelUtils";
import { StepBrand } from "@/components/social-media/channel/StepBrand";
import { StepPlatform } from "@/components/social-media/channel/StepPlatform";
import { StepPublishMethod } from "@/components/social-media/channel/StepPublishMethod";
import { StepPacing } from "@/components/social-media/channel/StepPacing";

function AddChannelInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const brandQuery = searchParams.get("brand");

  // ── SWR Queries ──
  const { data: brands = [], isLoading: loadingBrands } = useSWR(
    "social-brands",
    listBrands
  );

  const { data: profiles = [] } = useSWR("social-profiles", () =>
    listSocialDestinations({ kind: "facebook_profile" }).then((r) => r.data)
  );

  const { data: replizData } = useSWR("repliz-accounts", listReplizAccounts);

  // ── Form State ──
  const [selectedBrandId, setSelectedBrandId] = useState<number | null>(null);
  const [kind, setKind] = useState<"facebook_page" | "facebook_profile">("facebook_page");
  const [channelName, setChannelName] = useState("");
  const [facebookActorId, setFacebookActorId] = useState("");
  const [parentProfileId, setParentProfileId] = useState<number | null>(null);

  const [mode, setMode] = useState<"phone" | "repliz" | "mixed">("phone");
  const [replizAccountId, setReplizAccountId] = useState("");
  const [phonePosts, setPhonePosts] = useState(3);
  const [replizPosts, setReplizPosts] = useState(3);

  const [useBrandDefaults, setUseBrandDefaults] = useState(true);
  const [customTimezone, setCustomTimezone] = useState("Europe/London");
  const [dailyPosts, setDailyPosts] = useState(6);
  const [minGapMinutes, setMinGapMinutes] = useState(10);
  const [supportedMedia, setSupportedMedia] = useState<string[]>(["image", "album", "video"]);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [noticeMessage, setNoticeMessage] = useState<string | null>(null);

  // Select initial brand from URL query or first brand
  useEffect(() => {
    if (brands.length > 0 && selectedBrandId === null) {
      if (brandQuery) {
        const found = brands.find((b) => String(b.id) === brandQuery);
        if (found) {
          setSelectedBrandId(found.id);
          return;
        }
      }
      setSelectedBrandId(brands[0].id);
    }
  }, [brands, brandQuery, selectedBrandId]);

  const selectedBrand = brands.find((b) => b.id === selectedBrandId);

  // Pre-fill parent profile default if available
  useEffect(() => {
    if (profiles.length > 0 && parentProfileId === null) {
      setParentProfileId(profiles[0].id);
    }
  }, [profiles, parentProfileId]);

  // Lock method to phone when kind is profile
  useEffect(() => {
    if (kind === "facebook_profile") {
      setMode("phone");
    }
  }, [kind]);

  const brandTz = selectedBrand?.timezone || "Europe/London";
  const sleepStart = selectedBrand?.publish_sleep_start_hour ?? 0;
  const sleepEnd = selectedBrand?.publish_sleep_end_hour ?? 7;
  const formatHour = (h: number) => (h < 10 ? `0${h}:00` : `${h}:00`);
  const quietHoursStr = `${formatHour(sleepStart)}–${formatHour(sleepEnd)}`;

  const activeTimezone = useBrandDefaults ? brandTz : customTimezone;
  const effectiveDailyTarget = mode === "mixed" ? phonePosts + replizPosts : dailyPosts;

  // ── Step Validations ──
  const isStep1Valid = Boolean(selectedBrandId);
  const isStep2Valid =
    isStep1Valid &&
    Boolean(channelName.trim()) &&
    (kind === "facebook_profile" || mode === "repliz" || Boolean(parentProfileId));

  const isStep3Valid =
    isStep2Valid &&
    (mode === "phone"
      ? kind === "facebook_profile" || Boolean(parentProfileId)
      : mode === "repliz"
      ? Boolean(replizAccountId)
      : Boolean(replizAccountId && (kind === "facebook_profile" || Boolean(parentProfileId)) && phonePosts >= 1 && replizPosts >= 1));

  const isStep4Valid =
    isStep3Valid &&
    effectiveDailyTarget >= 1 &&
    minGapMinutes >= 1 &&
    supportedMedia.length >= 1;

  const handleToggleMedia = (type: string) => {
    if (supportedMedia.includes(type)) {
      if (supportedMedia.length === 1) return;
      setSupportedMedia(supportedMedia.filter((m) => m !== type));
    } else {
      setSupportedMedia([...supportedMedia, type]);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isStep4Valid || !selectedBrandId) return;

    setIsSubmitting(true);
    setServerError(null);

    const isReplizOnly = mode === "repliz";
    const payload: SocialDestinationCreate = {
      content_project_id: selectedBrandId,
      kind,
      name: channelName.trim(),
      facebook_actor_id: facebookActorId.trim() || null,
      parent_profile_id: kind === "facebook_page" && (mode === "phone" || mode === "mixed") ? parentProfileId : null,
      repliz_account_id: mode === "repliz" || mode === "mixed" ? replizAccountId : null,
      mode,
      timezone: activeTimezone,
      daily_target: effectiveDailyTarget,
      phone_daily_target: mode === "phone" ? effectiveDailyTarget : mode === "mixed" ? phonePosts : 0,
      repliz_daily_target: mode === "repliz" ? effectiveDailyTarget : mode === "mixed" ? replizPosts : 0,
      min_gap_minutes: minGapMinutes,
      supported_media: supportedMedia,
      is_active: isReplizOnly,
    };

    try {
      const res = await createSocialDestination(payload);
      const newId = res.data.id;

      if (!isReplizOnly) {
        setNoticeMessage("Saved as paused. Activate it on the channel page after the identity check and the dry-run pass.");
      }

      router.push(`/social-media/channels/${newId}`);
    } catch (err) {
      setServerError(getErrorMessage(err));
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-20">
      {/* ── Breadcrumb ── */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-xs text-text-secondary flex-wrap">
        <Link href="/social-media" className="hover:text-primary-main transition">
          Social Media
        </Link>
        <span aria-hidden="true">/</span>
        {selectedBrand ? (
          <Link
            href={`/social-media/brands/${selectedBrand.id}`}
            className="hover:text-primary-main transition"
          >
            {selectedBrand.name}
          </Link>
        ) : (
          <span>Brand</span>
        )}
        <span aria-hidden="true">/</span>
        <span className="text-text-primary font-medium">Add channel</span>
      </nav>

      {/* ── Header ── */}
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-text-primary">Add a channel</h1>
        <p className="text-xs text-text-secondary">
          A channel is one place a brand publishes to. Pick the brand, the platform, then how posts get there.
        </p>
      </header>

      {noticeMessage && (
        <div className="rounded-xl border border-primary-main/40 bg-primary-lighter/20 p-3.5 text-xs text-primary-darker">
          {noticeMessage}
        </div>
      )}
      {serverError && (
        <div className="rounded-xl border border-error-main/40 bg-error-lighter/20 p-3.5 text-xs text-error-dark dark:text-rose-300">
          <p className="font-bold">Error creating channel</p>
          <p className="mt-0.5">{serverError}</p>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-5">
        <StepBrand
          brands={brands}
          loadingBrands={loadingBrands}
          selectedBrandId={selectedBrandId}
          setSelectedBrandId={setSelectedBrandId}
        />

        <StepPlatform
          isStep1Valid={isStep1Valid}
          kind={kind}
          setKind={setKind}
          channelName={channelName}
          setChannelName={setChannelName}
          facebookActorId={facebookActorId}
          setFacebookActorId={setFacebookActorId}
          parentProfileId={parentProfileId}
          setParentProfileId={setParentProfileId}
          profiles={profiles}
          mode={mode}
        />

        <StepPublishMethod
          isStep2Valid={isStep2Valid}
          kind={kind}
          mode={mode}
          setMode={setMode}
          replizData={replizData}
          replizAccountId={replizAccountId}
          setReplizAccountId={setReplizAccountId}
          phonePosts={phonePosts}
          setPhonePosts={setPhonePosts}
          replizPosts={replizPosts}
          setReplizPosts={setReplizPosts}
        />

        <StepPacing
          isStep3Valid={isStep3Valid}
          useBrandDefaults={useBrandDefaults}
          setUseBrandDefaults={setUseBrandDefaults}
          brandTz={brandTz}
          quietHoursStr={quietHoursStr}
          mode={mode}
          effectiveDailyTarget={effectiveDailyTarget}
          dailyPosts={dailyPosts}
          setDailyPosts={setDailyPosts}
          minGapMinutes={minGapMinutes}
          setMinGapMinutes={setMinGapMinutes}
          activeTimezone={activeTimezone}
          setCustomTimezone={setCustomTimezone}
          supportedMedia={supportedMedia}
          handleToggleMedia={handleToggleMedia}
        />

        {/* ── Footer ── */}
        <div className="flex flex-wrap items-center justify-between gap-4 pt-3">
          <span className="text-xs text-text-secondary">
            {mode === "repliz"
              ? "Starts publishing once saved."
              : "Saved as paused. Activate it on the channel page after the identity check and the dry-run pass."}
          </span>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => router.back()}
              className="btn-ghost text-xs px-4 py-2.5"
            >
              Cancel
            </button>

            <button
              type="submit"
              disabled={isSubmitting || !isStep4Valid}
              className="btn-primary text-xs px-5 py-2.5"
            >
              {isSubmitting ? "Saving..." : "Save channel"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export default function AddChannelPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-4xl mx-auto p-8 text-center text-sm text-text-secondary">
          Loading add channel...
        </div>
      }
    >
      <AddChannelInner />
    </Suspense>
  );
}
