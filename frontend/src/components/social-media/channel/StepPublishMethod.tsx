"use client";

import React from "react";
import type { ReplizAccountsOut } from "@/lib/socialBrandsApi";

interface Props {
  isStep2Valid: boolean;
  kind: "facebook_page" | "facebook_profile";
  mode: "phone" | "repliz" | "mixed";
  setMode: (m: "phone" | "repliz" | "mixed") => void;
  replizData?: ReplizAccountsOut | null;
  replizAccountId: string;
  setReplizAccountId: (id: string) => void;
  phonePosts: number;
  setPhonePosts: (n: number) => void;
  replizPosts: number;
  setReplizPosts: (n: number) => void;
}

export function StepPublishMethod({
  isStep2Valid,
  kind,
  mode,
  setMode,
  replizData,
  replizAccountId,
  setReplizAccountId,
  phonePosts,
  setPhonePosts,
  replizPosts,
  setReplizPosts,
}: Props) {
  return (
    <section
      aria-labelledby="step3-h"
      className={`card p-5 space-y-4 transition ${
        !isStep2Valid ? "opacity-50 pointer-events-none" : ""
      }`}
    >
      <h2 id="step3-h" className="text-sm font-bold text-text-primary flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-primary-main/20 text-primary-main text-xs flex items-center justify-center font-bold">
          3
        </span>
        How it publishes
      </h2>

      <div
        role="radiogroup"
        aria-label="Publishing method"
        className="grid grid-cols-1 sm:grid-cols-3 gap-3"
      >
        <button
          type="button"
          role="radio"
          aria-checked={mode === "phone"}
          onClick={() => setMode("phone")}
          className={`text-left p-3.5 rounded-xl border transition flex flex-col justify-between gap-2 min-h-[72px] ${
            mode === "phone"
              ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
              : "bg-bg-paper border-divider-soft hover:border-text-disabled"
          }`}
        >
          <span className="text-xs font-bold text-text-primary">Phone</span>
          <span className="text-[11px] text-text-secondary leading-normal">
            A Facebook Profile on one of your phones posts it
          </span>
        </button>

        <button
          type="button"
          role="radio"
          aria-checked={mode === "repliz"}
          disabled={kind === "facebook_profile"}
          onClick={() => kind !== "facebook_profile" && setMode("repliz")}
          className={`text-left p-3.5 rounded-xl border transition flex flex-col justify-between gap-2 min-h-[72px] ${
            kind === "facebook_profile"
              ? "opacity-40 cursor-not-allowed bg-bg-default border-divider-soft"
              : mode === "repliz"
              ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
              : "bg-bg-paper border-divider-soft hover:border-text-disabled"
          }`}
        >
          <span className="text-xs font-bold text-text-primary">Repliz</span>
          <span className="text-[11px] text-text-secondary leading-normal">
            Posted through a connected Repliz account
          </span>
        </button>

        <button
          type="button"
          role="radio"
          aria-checked={mode === "mixed"}
          disabled={kind === "facebook_profile"}
          onClick={() => kind !== "facebook_profile" && setMode("mixed")}
          className={`text-left p-3.5 rounded-xl border transition flex flex-col justify-between gap-2 min-h-[72px] ${
            kind === "facebook_profile"
              ? "opacity-40 cursor-not-allowed bg-bg-default border-divider-soft"
              : mode === "mixed"
              ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
              : "bg-bg-paper border-divider-soft hover:border-text-disabled"
          }`}
        >
          <span className="text-xs font-bold text-text-primary">Phone + Repliz</span>
          <span className="text-[11px] text-text-secondary leading-normal">
            Split the daily posts between both
          </span>
        </button>
      </div>

      {(mode === "repliz" || mode === "mixed") && (
        <div className="space-y-1 max-w-md pt-2">
          <label htmlFor="repliz-account-select" className="label">
            Repliz account *
          </label>
          <select
            id="repliz-account-select"
            value={replizAccountId}
            onChange={(e) => setReplizAccountId(e.target.value)}
            className="input-rect"
          >
            <option value="">Select connected Repliz account...</option>
            {replizData?.accounts?.map((acc) => {
              const used = Boolean(acc.used_by_destination_id);
              return (
                <option key={acc.id} value={acc.id} disabled={used}>
                  {acc.name} ({acc.type}) {used ? `— used by ${acc.used_by_destination_name}` : ""}
                </option>
              );
            })}
          </select>
          {replizData?.error && (
            <p className="text-[11px] text-rose-400">{replizData.error}</p>
          )}
        </div>
      )}

      {mode === "mixed" && (
        <div className="space-y-2 pt-2">
          <span className="label">Split of the day</span>
          <div className="flex flex-wrap items-center gap-3">
            <div className="w-32">
              <label htmlFor="phone-posts" className="text-[11px] text-text-secondary block mb-1">
                Phone posts / day
              </label>
              <input
                id="phone-posts"
                type="number"
                min={1}
                value={phonePosts}
                onChange={(e) => setPhonePosts(parseInt(e.target.value, 10) || 1)}
                className="input-rect"
              />
            </div>
            <span className="text-text-secondary font-bold pt-4">+</span>
            <div className="w-32">
              <label htmlFor="repliz-posts" className="text-[11px] text-text-secondary block mb-1">
                Repliz posts / day
              </label>
              <input
                id="repliz-posts"
                type="number"
                min={1}
                value={replizPosts}
                onChange={(e) => setReplizPosts(parseInt(e.target.value, 10) || 1)}
                className="input-rect"
              />
            </div>
            <span className="text-text-secondary font-bold pt-4">=</span>
            <div className="pt-4">
              <span className="text-lg font-bold text-emerald-400 tabular-nums">
                {phonePosts + replizPosts}
              </span>
              <span className="text-xs text-text-secondary ml-1">posts / day</span>
            </div>
          </div>
        </div>
      )}

      {(mode === "phone" || mode === "mixed") && (
        <p className="text-xs text-text-disabled">
          The phone route stays off until the identity check and the App Card dry-run pass on the device.
        </p>
      )}
    </section>
  );
}
