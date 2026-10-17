"use client";

import React, { useState, useEffect } from "react";
import { Icon } from "@iconify/react";
import type { SocialDestination, SocialDestinationUpdate } from "@/lib/types";
import { getErrorMessage } from "./channelUtils";

interface Props {
  destination: SocialDestination;
  onSave: (payload: SocialDestinationUpdate) => Promise<void>;
}

const MEDIA_OPTIONS = [
  {
    key: "image",
    label: "Single image",
    desc: "Single photo posts with generated card designs or editorial templates.",
    icon: "solar:gallery-bold",
  },
  {
    key: "album",
    label: "Album",
    desc: "Multi-photo carousel posts recreated from Instagram or news sets.",
    icon: "solar:album-bold",
  },
  {
    key: "video",
    label: "Video / Reel",
    desc: "Vertical video reels from YouTube clips or AI video workers.",
    icon: "solar:videocamera-record-bold",
  },
];

export function MediaTab({ destination, onSave }: Props) {
  const [selectedMedia, setSelectedMedia] = useState<string[]>(
    destination.supported_media || ["image", "album", "video"]
  );
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    setSelectedMedia(destination.supported_media || ["image", "album", "video"]);
  }, [destination.supported_media]);

  const toggleMedia = (key: string) => {
    if (selectedMedia.includes(key)) {
      if (selectedMedia.length === 1) {
        setSaveError("At least one media type must be accepted.");
        return;
      }
      setSelectedMedia(selectedMedia.filter((m) => m !== key));
    } else {
      setSelectedMedia([...selectedMedia, key]);
    }
    setSaveError(null);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedMedia.length === 0) {
      setSaveError("At least one media type must be accepted.");
      return;
    }

    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      await onSave({ supported_media: selectedMedia });
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
    } catch (err) {
      setSaveError(getErrorMessage(err));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <form onSubmit={handleSave} className="flex flex-col gap-6">
      <section className="card p-6 space-y-4">
        <div>
          <h2 className="text-base font-bold text-text-primary">
            Media this channel accepts
          </h2>
          <p className="text-xs text-text-secondary mt-0.5">
            Posts made by the brand that match unchecked media formats will not be scheduled for this channel.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {MEDIA_OPTIONS.map((opt) => {
            const checked = selectedMedia.includes(opt.key);
            return (
              <label
                key={opt.key}
                className={`p-4 rounded-xl border cursor-pointer transition flex flex-col justify-between gap-3 ${
                  checked
                    ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
                    : "bg-bg-paper border-divider-soft hover:border-text-disabled"
                }`}
              >
                <div className="flex items-center justify-between w-full">
                  <div className="flex items-center gap-2.5">
                    <Icon
                      icon={opt.icon}
                      className={checked ? "text-primary-main" : "text-text-disabled"}
                      width={20}
                    />
                    <span className="text-sm font-bold text-text-primary">
                      {opt.label}
                    </span>
                  </div>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleMedia(opt.key)}
                    className="w-4 h-4 rounded text-primary-main focus:ring-primary-main"
                  />
                </div>
                <p className="text-xs text-text-secondary leading-relaxed">
                  {opt.desc}
                </p>
              </label>
            );
          })}
        </div>
      </section>

      {/* Save Button & Feedback */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <span className="text-xs text-text-secondary">
          At least one media format required.
        </span>
        <div className="flex items-center gap-3">
          {saveSuccess && (
            <span className="text-xs text-emerald-400 font-semibold flex items-center gap-1">
              <Icon icon="solar:check-circle-bold" width={14} />
              Saved media settings
            </span>
          )}
          <button
            type="submit"
            disabled={isSaving || selectedMedia.length === 0}
            className="btn-primary text-xs px-5 py-2.5"
          >
            {isSaving ? "Saving..." : "Save media settings"}
          </button>
        </div>
      </div>

      {saveError && (
        <div className="rounded-xl border border-error-main/40 bg-error-lighter/20 p-3 text-xs text-error-dark dark:text-rose-300">
          {saveError}
        </div>
      )}
    </form>
  );
}
