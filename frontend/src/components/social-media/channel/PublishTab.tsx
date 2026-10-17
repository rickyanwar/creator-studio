"use client";

import React, { useState, useEffect } from "react";
import { Icon } from "@iconify/react";
import type {
  SocialDestination,
  SocialDestinationUpdate,
  PhoneDevice,
  DestinationDeviceAssignment,
} from "@/lib/types";
import type {
  ReplizAccountsOut,
  EstimatedTimesOut,
} from "@/lib/socialBrandsApi";
import { getErrorMessage } from "./channelUtils";
import { EstimatedSlotsView } from "./EstimatedSlotsView";
import { PublishMethodSelector } from "./PublishMethodSelector";
import { PhoneSettingsBlock } from "./PhoneSettingsBlock";
import { ReplizSettingsBlock } from "./ReplizSettingsBlock";
import { SplitSettingsBlock } from "./SplitSettingsBlock";

interface Props {
  destination: SocialDestination;
  profiles: SocialDestination[];
  devices: PhoneDevice[];
  assignments: DestinationDeviceAssignment[];
  replizData?: ReplizAccountsOut | null;
  estimatedTimes?: EstimatedTimesOut | null;
  isLoadingEstimates?: boolean;
  onSave: (payload: SocialDestinationUpdate) => Promise<void>;
  onRefreshData: () => void;
  onGoPacing: () => void;
}

export function PublishTab({
  destination,
  profiles,
  devices,
  assignments,
  replizData,
  estimatedTimes,
  isLoadingEstimates,
  onSave,
  onRefreshData,
  onGoPacing,
}: Props) {
  const isProfile = destination.kind === "facebook_profile";

  const [mode, setMode] = useState<"phone" | "repliz" | "mixed">(
    destination.mode || (isProfile ? "phone" : "mixed")
  );
  const [parentProfileId, setParentProfileId] = useState<number | null>(
    destination.parent_profile_id
  );
  const [facebookActorId, setFacebookActorId] = useState(
    destination.facebook_actor_id || ""
  );
  const [replizAccountId, setReplizAccountId] = useState(
    destination.repliz_account_id || ""
  );
  const [dailyTarget, setDailyTarget] = useState<number>(
    destination.daily_target || 6
  );
  const [phoneDailyTarget, setPhoneDailyTarget] = useState<number>(
    destination.phone_daily_target || 3
  );
  const [replizDailyTarget, setReplizDailyTarget] = useState<number>(
    destination.repliz_daily_target || 3
  );

  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    setMode(destination.mode || (isProfile ? "phone" : "mixed"));
    setParentProfileId(destination.parent_profile_id);
    setFacebookActorId(destination.facebook_actor_id || "");
    setReplizAccountId(destination.repliz_account_id || "");
    setDailyTarget(destination.daily_target || 6);
    setPhoneDailyTarget(destination.phone_daily_target || 3);
    setReplizDailyTarget(destination.repliz_daily_target || 3);
  }, [destination, isProfile]);

  const splitSum = phoneDailyTarget + replizDailyTarget;
  const isSplitValid = mode !== "mixed" || splitSum === dailyTarget;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isSplitValid) {
      setSaveError(
        `Phone posts (${phoneDailyTarget}) + Repliz posts (${replizDailyTarget}) must equal Posts / day (${dailyTarget})`
      );
      return;
    }

    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      const payload: SocialDestinationUpdate = {
        mode,
        facebook_actor_id: facebookActorId.trim() || null,
        parent_profile_id: !isProfile && (mode === "phone" || mode === "mixed") ? parentProfileId : null,
        repliz_account_id: mode === "repliz" || mode === "mixed" ? replizAccountId.trim() || null : null,
        daily_target: dailyTarget,
        phone_daily_target: mode === "phone" ? dailyTarget : mode === "mixed" ? phoneDailyTarget : 0,
        repliz_daily_target: mode === "repliz" ? dailyTarget : mode === "mixed" ? replizDailyTarget : 0,
      };

      await onSave(payload);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
    } catch (err) {
      setSaveError(getErrorMessage(err));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      {/* 1. Upload Method Choice */}
      <PublishMethodSelector mode={mode} setMode={setMode} isProfile={isProfile} />

      {/* 2. Phone Configuration */}
      {(mode === "phone" || mode === "mixed") && (
        <PhoneSettingsBlock
          destination={destination}
          profiles={profiles}
          devices={devices}
          assignments={assignments}
          parentProfileId={parentProfileId}
          setParentProfileId={setParentProfileId}
          facebookActorId={facebookActorId}
          setFacebookActorId={setFacebookActorId}
          onRefreshData={onRefreshData}
          onError={setSaveError}
        />
      )}

      {/* 3. Repliz Configuration */}
      {(mode === "repliz" || mode === "mixed") && (
        <ReplizSettingsBlock
          destination={destination}
          replizData={replizData}
          replizAccountId={replizAccountId}
          setReplizAccountId={setReplizAccountId}
        />
      )}

      {/* 4. Split Quotas */}
      {mode === "mixed" && (
        <SplitSettingsBlock
          dailyTarget={dailyTarget}
          phoneDailyTarget={phoneDailyTarget}
          setPhoneDailyTarget={setPhoneDailyTarget}
          replizDailyTarget={replizDailyTarget}
          setReplizDailyTarget={setReplizDailyTarget}
          isSplitValid={isSplitValid}
        />
      )}

      {/* Save Button & Feedback */}
      <div className="flex flex-wrap items-center justify-between gap-4 pt-2">
        <span className="text-xs text-text-secondary">
          Changes apply to posts that have not been dispatched yet.
        </span>
        <div className="flex items-center gap-3">
          {saveSuccess && (
            <span className="text-xs text-emerald-400 font-semibold flex items-center gap-1">
              <Icon icon="solar:check-circle-bold" width={14} />
              Saved successfully
            </span>
          )}
          <button
            type="submit"
            disabled={isSaving || !isSplitValid}
            className="btn-primary text-xs px-5 py-2.5"
          >
            {isSaving ? "Saving..." : "Save upload settings"}
          </button>
        </div>
      </div>

      {saveError && (
        <div className="rounded-xl border border-error-main/40 bg-error-lighter/20 p-3 text-xs text-error-dark dark:text-rose-300">
          {saveError}
        </div>
      )}

      {/* 5. Today's Slots View */}
      <section className="card p-5">
        <EstimatedSlotsView
          estimatedTimes={estimatedTimes}
          isLoading={isLoadingEstimates}
          onGoPacing={onGoPacing}
          showPacingLink={true}
        />
      </section>
    </form>
  );
}
