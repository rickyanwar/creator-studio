"use client";

import { useState, useId } from "react";
import { updateFanpage } from "@/lib/api";

const COMMON_TIMEZONES = [
  "Europe/London",
  "Europe/Madrid",
  "Europe/Rome",
  "Asia/Jakarta",
  "Asia/Kuala_Lumpur",
  "America/New_York",
  "America/Los_Angeles",
  "Australia/Sydney",
];

export interface BrandPacingCardProps {
  brandId: number;
  initialTimezone: string;
  initialSleepStart: number | null;
  initialSleepEnd: number | null;
  initialDailyLimit: number;
  onSaved?: () => void;
}

export function BrandPacingCard({
  brandId,
  initialTimezone,
  initialSleepStart,
  initialSleepEnd,
  initialDailyLimit,
  onSaved,
}: BrandPacingCardProps) {
  const tzSelectId = useId();
  const sleepStartId = useId();
  const sleepEndId = useId();
  const dailyLimitId = useId();

  const [timezone, setTimezone] = useState(initialTimezone || "Europe/London");
  const [startHour, setStartHour] = useState<number | null>(initialSleepStart ?? null);
  const [endHour, setEndHour] = useState<number | null>(initialSleepEnd ?? null);
  const [dailyLimit, setDailyLimit] = useState<number>(initialDailyLimit || 14);

  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error" | "invalid">("idle");
  const [validationError, setValidationError] = useState("");

  const timezones = COMMON_TIMEZONES.includes(timezone)
    ? COMMON_TIMEZONES
    : [timezone, ...COMMON_TIMEZONES];

  const handleStartChange = (val: string) => {
    if (val === "") {
      setStartHour(null);
      setEndHour(null);
    } else {
      const parsed = parseInt(val, 10);
      setStartHour(parsed);
      if (endHour === null) {
        setEndHour((parsed + 7) % 24);
      }
    }
    setSaveStatus("idle");
  };

  const handleEndChange = (val: string) => {
    if (val === "") {
      setStartHour(null);
      setEndHour(null);
    } else {
      const parsed = parseInt(val, 10);
      setEndHour(parsed);
      if (startHour === null) {
        setStartHour(0);
      }
    }
    setSaveStatus("idle");
  };

  const handleSave = async () => {
    if (!timezone.trim()) {
      setValidationError("Audience time zone is required.");
      setSaveStatus("invalid");
      return;
    }
    if (dailyLimit < 1) {
      setValidationError("Daily posts must be at least 1.");
      setSaveStatus("invalid");
      return;
    }
    if (startHour !== null && (startHour < 0 || startHour > 23)) {
      setValidationError("Quiet hour start must be between 0 and 23.");
      setSaveStatus("invalid");
      return;
    }
    if (endHour !== null && (endHour < 0 || endHour > 23)) {
      setValidationError("Quiet hour end must be between 0 and 23.");
      setSaveStatus("invalid");
      return;
    }

    setSaving(true);
    setSaveStatus("idle");
    setValidationError("");

    try {
      await updateFanpage(brandId, {
        timezone,
        publish_sleep_start_hour: startHour,
        publish_sleep_end_hour: endHour,
        publish_daily_limit: dailyLimit,
      });
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 4000);
      onSaved?.();
    } catch {
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  };

  const quietHoursSummary =
    startHour !== null && endHour !== null
      ? `${String(startHour).padStart(2, "0")}:00 – ${String(endHour).padStart(2, "0")}:00`
      : "No quiet hours";

  return (
    <section aria-label="Brand pacing" className="card space-y-5">
      <div>
        <h2 className="text-lg font-bold text-text-primary">Brand pacing</h2>
        <p className="text-xs text-text-secondary mt-1">
          Audience location and sleep window applied across this brand&apos;s channels.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* Audience time zone */}
        <div className="space-y-1.5">
          <label htmlFor={tzSelectId} className="label">
            Audience time zone
          </label>
          <select
            id={tzSelectId}
            value={timezone}
            onChange={(e) => {
              setTimezone(e.target.value);
              setSaveStatus("idle");
            }}
            className="input bg-bg-paper-hover border-divider-soft cursor-pointer text-xs"
          >
            {timezones.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
          <p className="text-[11px] text-text-secondary">
            Used for delivery scheduling and local sleep window.
          </p>
        </div>

        {/* Quiet hours (local) */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="label mb-0">Quiet hours (local)</span>
            <span className="text-[11px] font-medium text-text-secondary">
              {quietHoursSummary}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor={sleepStartId} className="sr-only">
                Start hour
              </label>
              <select
                id={sleepStartId}
                value={startHour !== null ? String(startHour) : ""}
                onChange={(e) => handleStartChange(e.target.value)}
                className="input bg-bg-paper-hover border-divider-soft cursor-pointer text-xs"
              >
                <option value="">No quiet hours</option>
                {Array.from({ length: 24 }).map((_, i) => (
                  <option key={i} value={String(i)}>
                    {String(i).padStart(2, "0")}:00
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={sleepEndId} className="sr-only">
                End hour
              </label>
              <select
                id={sleepEndId}
                value={endHour !== null ? String(endHour) : ""}
                onChange={(e) => handleEndChange(e.target.value)}
                className="input bg-bg-paper-hover border-divider-soft cursor-pointer text-xs"
              >
                <option value="">No quiet hours</option>
                {Array.from({ length: 24 }).map((_, i) => (
                  <option key={i} value={String(i)}>
                    {String(i).padStart(2, "0")}:00
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-[11px] text-text-secondary">
            Channels pause publishing during these hours in audience time.
          </p>
        </div>

        {/* Daily posts, all channels */}
        <div className="space-y-1.5">
          <label htmlFor={dailyLimitId} className="label">
            Daily posts, all channels
          </label>
          <input
            id={dailyLimitId}
            type="number"
            min={1}
            value={dailyLimit}
            onChange={(e) => {
              setDailyLimit(parseInt(e.target.value, 10) || 0);
              setSaveStatus("idle");
            }}
            className="input bg-bg-paper-hover border-divider-soft text-xs"
          />
          <p className="text-[11px] text-text-secondary">
            Anti-bot protection cap across all channels for this brand.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-divider-soft">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary text-xs px-5 py-2"
        >
          {saving ? "Saving…" : "Save pacing"}
        </button>
        {saveStatus === "saved" && (
          <span role="status" className="text-xs text-primary-main font-semibold">
            Saved.
          </span>
        )}
        {saveStatus === "error" && (
          <span role="status" className="text-xs text-error-main font-semibold">
            Save failed. Please try again.
          </span>
        )}
        {saveStatus === "invalid" && (
          <span role="status" className="text-xs text-error-main font-semibold">
            {validationError}
          </span>
        )}
      </div>
    </section>
  );
}
