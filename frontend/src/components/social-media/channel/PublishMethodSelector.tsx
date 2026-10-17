"use client";

import React from "react";
import { Icon } from "@iconify/react";

interface Props {
  mode: "phone" | "repliz" | "mixed";
  setMode: (m: "phone" | "repliz" | "mixed") => void;
  isProfile: boolean;
}

export function PublishMethodSelector({ mode, setMode, isProfile }: Props) {
  return (
    <section aria-labelledby="method-heading" className="flex flex-col gap-3">
      <div>
        <h2 id="method-heading" className="text-base font-bold text-text-primary">
          How posts get uploaded
        </h2>
        <p className="text-xs text-text-secondary">
          {isProfile
            ? "Facebook Profiles publish directly via an assigned Android phone."
            : "Pick one. A Facebook Page can use a phone, Repliz, or split the day between both."}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Phone */}
        <button
          type="button"
          role="radio"
          aria-checked={mode === "phone"}
          onClick={() => setMode("phone")}
          className={`text-left p-4 rounded-xl border transition flex flex-col justify-between gap-3 min-h-[140px] ${
            mode === "phone"
              ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
              : "bg-bg-paper border-divider-soft hover:border-text-disabled"
          }`}
        >
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-bold text-text-primary flex items-center gap-2">
              <Icon icon="solar:smartphone-bold" className="text-blue-400" width={18} />
              Phone
            </span>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-800/60">
              Ready
            </span>
          </div>
          <p className="text-xs text-text-secondary leading-relaxed">
            A Facebook Profile signed in on your phone switches to this Page and posts it, like a person would.
          </p>
          <span className="text-[11px] text-text-disabled">
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
            isProfile ? "opacity-40 cursor-not-allowed bg-bg-default border-divider-soft" : ""
          } ${
            mode === "repliz"
              ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
              : "bg-bg-paper border-divider-soft hover:border-text-disabled"
          }`}
        >
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-bold text-text-primary flex items-center gap-2">
              <Icon icon="solar:cloud-upload-bold" className="text-purple-400" width={18} />
              Repliz
            </span>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-800/60">
              Ready
            </span>
          </div>
          <p className="text-xs text-text-secondary leading-relaxed">
            Repliz schedules and posts it. No phone involved.
          </p>
          <span className="text-[11px] text-text-disabled">
            Needs: A connected Repliz account for this Page.
          </span>
        </button>

        {/* Split (Mixed) */}
        <button
          type="button"
          role="radio"
          aria-checked={mode === "mixed"}
          disabled={isProfile}
          onClick={() => !isProfile && setMode("mixed")}
          className={`text-left p-4 rounded-xl border transition flex flex-col justify-between gap-3 min-h-[140px] ${
            isProfile ? "opacity-40 cursor-not-allowed bg-bg-default border-divider-soft" : ""
          } ${
            mode === "mixed"
              ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
              : "bg-bg-paper border-divider-soft hover:border-text-disabled"
          }`}
        >
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-bold text-text-primary flex items-center gap-2">
              <Icon icon="solar:shuffle-bold" className="text-amber-400" width={18} />
              Phone + Repliz
            </span>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-800/60">
              Ready
            </span>
          </div>
          <p className="text-xs text-text-secondary leading-relaxed">
            Split the day: some posts go out from the phone, the rest through Repliz.
          </p>
          <span className="text-[11px] text-text-disabled">
            Needs: Both verified phone and connected Repliz.
          </span>
        </button>
      </div>
    </section>
  );
}
