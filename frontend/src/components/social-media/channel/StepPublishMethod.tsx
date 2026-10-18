"use client";

import React from "react";
import type { ReplizAccountsOut } from "@/lib/socialBrandsApi";

interface Props {
  isStep2Valid: boolean;
  kind: string;
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
  const isProfile = kind === "facebook_profile";
  const isReplizOnly =
    kind === "instagram" ||
    kind === "youtube" ||
    kind === "threads" ||
    kind === "x";

  return (
    <section
      aria-labelledby="step3-h"
      className={`card p-5 space-y-4 transition ${
        !isStep2Valid ? "opacity-50 pointer-events-none" : ""
      }`}
    >
      <h2 id="step3-h" className="text-sm font-bold text-text flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-pri-soft text-pri text-xs flex items-center justify-center font-bold">
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
          disabled={isReplizOnly}
          onClick={() => !isReplizOnly && setMode("phone")}
          className={`text-left p-3.5 rounded-xl border transition flex flex-col justify-between gap-2 min-h-[72px] ${
            isReplizOnly
              ? "opacity-40 cursor-not-allowed bg-surface2 border-line"
              : mode === "phone"
              ? "bg-surface border-pri ring-1 ring-pri"
              : "bg-surface border-line hover:border-text2"
          }`}
        >
          <span className="text-xs font-bold text-text">Phone</span>
          <span className="text-[11px] text-text2 leading-normal">
            A Facebook Profile on one of your phones posts it
          </span>
        </button>

        <button
          type="button"
          role="radio"
          aria-checked={mode === "repliz"}
          disabled={isProfile}
          onClick={() => !isProfile && setMode("repliz")}
          className={`text-left p-3.5 rounded-xl border transition flex flex-col justify-between gap-2 min-h-[72px] ${
            isProfile
              ? "opacity-40 cursor-not-allowed bg-surface2 border-line"
              : mode === "repliz"
              ? "bg-surface border-pri ring-1 ring-pri"
              : "bg-surface border-line hover:border-text2"
          }`}
        >
          <span className="text-xs font-bold text-text">Repliz</span>
          <span className="text-[11px] text-text2 leading-normal">
            Posted through a connected Repliz account
          </span>
        </button>

        <button
          type="button"
          role="radio"
          aria-checked={mode === "mixed"}
          disabled={isProfile || isReplizOnly}
          onClick={() => !isProfile && !isReplizOnly && setMode("mixed")}
          className={`text-left p-3.5 rounded-xl border transition flex flex-col justify-between gap-2 min-h-[72px] ${
            isProfile || isReplizOnly
              ? "opacity-40 cursor-not-allowed bg-surface2 border-line"
              : mode === "mixed"
              ? "bg-surface border-pri ring-1 ring-pri"
              : "bg-surface border-line hover:border-text2"
          }`}
        >
          <span className="text-xs font-bold text-text">Phone + Repliz</span>
          <span className="text-[11px] text-text2 leading-normal">
            Split the daily posts between both
          </span>
        </button>
      </div>

      {isReplizOnly && (
        <p className="text-xs text-text2">
          Only Repliz publishing is available for this platform.
        </p>
      )}

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
            <p className="text-[11px] text-bad">{replizData.error}</p>
          )}
        </div>
      )}

      {mode === "mixed" && (
        <div className="space-y-2 pt-2">
          <span className="label">Split of the day</span>
          <div className="flex flex-wrap items-center gap-3">
            <div className="w-32">
              <label htmlFor="phone-posts" className="text-[11px] text-text2 block mb-1">
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
            <span className="text-text2 font-bold pt-4">+</span>
            <div className="w-32">
              <label htmlFor="repliz-posts" className="text-[11px] text-text2 block mb-1">
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
            <span className="text-text2 font-bold pt-4">=</span>
            <div className="pt-4">
              <span className="text-lg font-bold text-pri tabular-nums">
                {phonePosts + replizPosts}
              </span>
              <span className="text-xs text-text2 ml-1">posts / day</span>
            </div>
          </div>
        </div>
      )}

      {(mode === "phone" || mode === "mixed") && (
        <p className="text-xs text-text2">
          The phone route stays off until the identity check and the App Card dry-run pass on the device.
        </p>
      )}
    </section>
  );
}
