"use client";

import React from "react";
import { Icon } from "@iconify/react";

export function FallbackTab() {
  return (
    <div className="flex flex-col gap-4">
      <section className="bg-surface border border-line rounded-2xl p-6 flex flex-col gap-4 shadow-sm">
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-bold text-text">If the phone fails</h2>
          <p className="text-xs text-text2 leading-relaxed">
            What happens when the phone cannot post this Page. The rules below protect against double posts.
          </p>
        </div>

        <ul className="m-0 p-0 list-none grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
          <li className="p-4 rounded-xl bg-surface2 flex flex-col gap-2">
            <span
              aria-hidden="true"
              className="w-8 h-8 rounded-lg bg-pri-soft text-pri-text inline-flex items-center justify-center"
            >
              <Icon icon="solar:plain-bold-duotone" width={18} />
            </span>
            <strong className="text-sm font-bold text-text">Send through Repliz instead</strong>
            <span className="text-xs text-text2 leading-relaxed">
              Only after automatic phone recovery ends in “needs you” with at least 2 checks.
            </span>
          </li>

          <li className="p-4 rounded-xl bg-surface2 flex flex-col gap-2">
            <span
              aria-hidden="true"
              className="w-8 h-8 rounded-lg bg-warn-soft text-warn inline-flex items-center justify-center"
            >
              <Icon icon="solar:danger-triangle-bold" width={18} />
            </span>
            <strong className="text-sm font-bold text-text">Never for uncertain post</strong>
            <span className="text-xs text-text2 leading-relaxed">
              When the result is unclear the post may already be live, so it is never sent again.
            </span>
          </li>

          <li className="p-4 rounded-xl bg-surface2 flex flex-col gap-2">
            <span
              aria-hidden="true"
              className="w-8 h-8 rounded-lg bg-warn-soft text-warn inline-flex items-center justify-center"
            >
              <Icon icon="solar:copy-bold" width={18} />
            </span>
            <strong className="text-sm font-bold text-text">Never twice</strong>
            <span className="text-xs text-text2 leading-relaxed">
              Skipped when the same caption and media were already posted on this channel.
            </span>
          </li>

          <li className="p-4 rounded-xl bg-surface2 flex flex-col gap-2">
            <span
              aria-hidden="true"
              className="w-8 h-8 rounded-lg bg-info-soft text-info inline-flex items-center justify-center"
            >
              <Icon icon="solar:bell-bold" width={18} />
            </span>
            <strong className="text-sm font-bold text-text">You are told</strong>
            <span className="text-xs text-text2 leading-relaxed">
              A Telegram message when it happens, and another with the result.
            </span>
          </li>
        </ul>

        <p className="m-0 text-xs text-text2 leading-relaxed pt-1">
          These rules are fixed for safety. The fallback exists only for Page channels that have a Repliz account; Profile channels never fall back.
        </p>
      </section>
    </div>
  );
}

