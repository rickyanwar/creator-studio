"use client";

import React from "react";
import { Icon } from "@iconify/react";

interface Props {
  mode: "phone" | "repliz" | "mixed";
  setMode: (m: "phone" | "repliz" | "mixed") => void;
  isProfile: boolean;
  isReplizOnly?: boolean;
}

export function PublishMethodSelector({
  mode,
  setMode,
  isProfile,
  isReplizOnly = false,
}: Props) {
  return (
    <section aria-labelledby="method-heading" className="flex flex-col gap-3">
      <div>
        <h2 id="method-heading" className="text-base font-bold text-text">
          How posts get uploaded
        </h2>
        <p className="text-xs text-text2">
          {isProfile
            ? "Facebook Profiles publish directly via an assigned Android phone."
            : isReplizOnly
            ? "This platform publishes via Repliz only."
            : "Pick one. A Facebook Page can use a phone, Repliz, or split the day between both."}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Phone */}
        <button
          type="button"
          role="radio"
          aria-checked={mode === "phone"}
          disabled={isReplizOnly}
          onClick={() => !isReplizOnly && setMode("phone")}
          className={`text-left p-4 rounded-xl border transition flex flex-col justify-between gap-3 min-h-[140px] ${
            isReplizOnly
              ? "opacity-40 cursor-not-allowed bg-surface2 border-line"
              : "cursor-pointer"
          } ${
            mode === "phone"
              ? "bg-pri-soft border-pri ring-1 ring-pri"
              : "bg-surface border-line hover:border-text2"
          }`}
        >
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-bold text-text flex items-center gap-2">
              <Icon icon="solar:smartphone-bold" className="text-info" width={18} />
              Phone
            </span>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-pri-soft text-pri-text">
              Ready
            </span>
          </div>
          <p className="text-xs text-text2 leading-relaxed">
            A Facebook Profile signed in on your phone switches to this Page and posts it, like a person would.
          </p>
          <span className="text-[11px] text-text2">
            Needs: Profile on phone, online ADB, dry-run passed.
          </span>
        </button>

        {/* Repliz */}
        <button
          type="button"
          role="radio"
          aria-checked={mode === "repliz"}
          disabled={isProfile}
          onClick={() => !isProfile && setMode("repliz")}
          className={`text-left p-4 rounded-xl border transition flex flex-col justify-between gap-3 min-h-[140px] ${
            isProfile ? "opacity-40 cursor-not-allowed bg-surface2 border-line" : "cursor-pointer"
          } ${
            mode === "repliz"
              ? "bg-pri-soft border-pri ring-1 ring-pri"
              : "bg-surface border-line hover:border-text2"
          }`}
        >
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-bold text-text flex items-center gap-2">
              <Icon icon="solar:cloud-upload-bold" className="text-pri" width={18} />
              Repliz
            </span>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-pri-soft text-pri-text">
              Ready
            </span>
          </div>
          <p className="text-xs text-text2 leading-relaxed">
            Repliz schedules and posts it. No phone involved.
          </p>
          <span className="text-[11px] text-text2">
            Needs: A connected Repliz account for this Page.
          </span>
        </button>

        {/* Split (Mixed) */}
        <button
          type="button"
          role="radio"
          aria-checked={mode === "mixed"}
          disabled={isProfile || isReplizOnly}
          onClick={() => !isProfile && !isReplizOnly && setMode("mixed")}
          className={`text-left p-4 rounded-xl border transition flex flex-col justify-between gap-3 min-h-[140px] ${
            isProfile || isReplizOnly
              ? "opacity-40 cursor-not-allowed bg-surface2 border-line"
              : "cursor-pointer"
          } ${
            mode === "mixed"
              ? "bg-pri-soft border-pri ring-1 ring-pri"
              : "bg-surface border-line hover:border-text2"
          }`}
        >
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-bold text-text flex items-center gap-2">
              <Icon icon="solar:shuffle-bold" className="text-warn" width={18} />
              Phone + Repliz
            </span>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-pri-soft text-pri-text">
              Ready
            </span>
          </div>
          <p className="text-xs text-text2 leading-relaxed">
            Split the day: some posts go out from the phone, the rest through Repliz.
          </p>
          <span className="text-[11px] text-text2">
            Needs: Both verified phone and connected Repliz.
          </span>
        </button>
      </div>
    </section>
  );
}
