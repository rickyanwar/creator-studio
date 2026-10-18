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
      <section className="bg-surface border border-line rounded-2xl p-6 flex flex-col gap-4 shadow-sm">
        <div>
          <h2 className="text-base font-bold text-text">
            Media this channel accepts
          </h2>
          <p className="text-xs text-text2 mt-1 leading-relaxed">
            The brand makes the posts; this channel only takes the kinds ticked here. Posts of an unchecked kind will not be scheduled for this channel.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {MEDIA_OPTIONS.map((opt) => {
            const kindStr = destination.kind as string;
            const isPlatformForbidden = kindStr === "youtube" && opt.key !== "video";
            const checked = !isPlatformForbidden && selectedMedia.includes(opt.key);

            return (
              <label
                key={opt.key}
                className={`p-3.5 rounded-xl border transition flex flex-col justify-between gap-2.5 min-h-[96px] ${
                  isPlatformForbidden
                    ? "opacity-40 cursor-not-allowed bg-surface2 border-line"
                    : "cursor-pointer " +
                      (checked
                        ? "bg-pri-soft border-pri ring-1 ring-pri"
                        : "bg-surface border-line hover:border-text2")
                }`}
              >
                <div className="flex items-center justify-between w-full">
                  <Icon
                    icon={opt.icon}
                    className={checked ? "text-pri" : "text-text2"}
                    width={22}
                  />
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={isPlatformForbidden}
                    onChange={() => !isPlatformForbidden && toggleMedia(opt.key)}
                    className="w-4 h-4 rounded text-pri focus:ring-pri accent-pri"
                  />
                </div>
                <div>
                  <span className="text-sm font-bold text-text block">
                    {opt.label}
                  </span>
                  <span className="text-xs text-text2 leading-snug">
                    {isPlatformForbidden
                      ? `Not accepted by ${destination.kind}`
                      : checked
                      ? "Accepted"
                      : "Not accepted"}
                  </span>
                </div>
              </label>
            );
          })}
        </div>

        <div className="p-3.5 rounded-xl bg-info-soft text-text flex flex-col gap-1 border border-info/20 text-xs">
          <span className="font-bold text-info">Platform rules</span>
          <span className="text-text2 leading-relaxed">
            Facebook accepts single images, albums and videos. Captions are preserved.
          </span>
        </div>
      </section>

      {/* Save Button & Feedback */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <span className="text-xs text-text2">
          At least one media format required.
        </span>
        <div className="flex items-center gap-3">
          {saveSuccess && (
            <span className="text-xs text-pri font-semibold flex items-center gap-1">
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
        <div className="p-3 rounded-xl border border-bad/30 bg-bad-soft text-bad text-xs">
          {saveError}
        </div>
      )}
    </form>
  );
}
