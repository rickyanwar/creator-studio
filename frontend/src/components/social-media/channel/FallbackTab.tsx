"use client";

import React from "react";
import { Icon } from "@iconify/react";

export function FallbackTab() {
  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-amber-950/40 border border-amber-800/50 flex items-center justify-center text-amber-400">
            <Icon icon="solar:shield-warning-bold" width={22} />
          </div>
          <div>
            <h2 className="text-base font-bold text-text-primary">If the phone fails</h2>
            <p className="text-xs text-text-secondary">
              Automatic fallback rules protecting your channel against duplicate or lost posts.
            </p>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-bg-default border border-divider-soft space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-text-primary">
            <Icon icon="solar:check-circle-bold" className="text-primary-main" width={18} />
            Send the post through Repliz instead
          </div>

          <ul className="space-y-2.5 text-xs text-text-secondary pl-6 list-disc">
            <li>
              <strong className="text-text-primary">Recovery threshold:</strong> Only after automatic phone recovery failed at least twice.
            </li>
            <li>
              <strong className="text-text-primary">Accuracy over availability:</strong> Never for a post whose result is uncertain — it may already be live.
            </li>
            <li>
              <strong className="text-text-primary">Duplicate prevention:</strong> Never twice: skipped when the same caption and media were already posted.
            </li>
            <li>
              <strong className="text-text-primary">Instant alerts:</strong> You get a Telegram message when it happens, and another with the result.
            </li>
          </ul>
        </div>

        <div className="rounded-xl border border-blue-900/40 bg-blue-950/20 p-3.5 flex items-start gap-3 text-xs text-blue-300">
          <Icon icon="solar:info-circle-bold" width={18} className="flex-shrink-0 mt-0.5" />
          <p className="leading-relaxed">
            These rules are enforced automatically by the delivery engine. There are no manual override toggles to ensure Facebook accounts remain safe from duplicate spam or ghost bans.
          </p>
        </div>
      </section>
    </div>
  );
}
