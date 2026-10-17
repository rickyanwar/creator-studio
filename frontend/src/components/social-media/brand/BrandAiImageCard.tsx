"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { AIImagesTodayOut } from "@/lib/socialBrandsApi";

export interface BrandAiImageCardProps {
  brandId: number;
  gptcliEnabledForBrand: boolean;
  gptcliGlobalEnabled: boolean;
  aiImagesToday: AIImagesTodayOut;
  quotaPct?: number | null;
  onToggle: (enabled: boolean) => Promise<void>;
}

export function BrandAiImageCard({
  gptcliEnabledForBrand,
  gptcliGlobalEnabled,
  aiImagesToday,
  quotaPct,
  onToggle,
}: BrandAiImageCardProps) {
  const [enabled, setEnabled] = useState(gptcliEnabledForBrand);
  const [isUpdating, setIsUpdating] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    setEnabled(gptcliEnabledForBrand);
  }, [gptcliEnabledForBrand]);

  const handleToggle = async () => {
    if (isUpdating) return;
    const nextVal = !enabled;
    setEnabled(nextVal);
    setErrorMsg(null);
    setIsUpdating(true);

    try {
      await onToggle(nextVal);
    } catch {
      setEnabled(!nextVal); // revert
      setErrorMsg("Failed to update AI image engine setting. Please try again.");
    } finally {
      setIsUpdating(false);
    }
  };

  const chatgptCount = aiImagesToday?.chatgpt ?? 0;
  const codexTotal = (aiImagesToday?.codex ?? 0) + (aiImagesToday?.codex_fallback ?? 0);
  const templateCount = aiImagesToday?.template ?? 0;

  return (
    <section aria-labelledby="ai-image-heading" className="card space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="ai-image-heading" className="text-lg font-bold text-text-primary">
          AI image editing
        </h2>
        <Link
          href="/settings"
          className="text-xs sm:text-sm font-semibold text-primary-main hover:underline"
        >
          Engine settings →
        </Link>
      </div>

      {!gptcliGlobalEnabled && (
        <div
          role="alert"
          className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center gap-2.5 text-xs text-amber-300"
        >
          <Icon
            icon="solar:danger-triangle-bold-duotone"
            width={18}
            className="text-amber-400 shrink-0"
          />
          <span>
            ChatGPT engine is off in{" "}
            <Link href="/settings" className="font-semibold underline">
              Settings › AI Image Engine
            </Link>
            .
          </span>
        </div>
      )}

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-xl bg-bg-paper-hover border border-divider-soft">
        <label className="flex items-center gap-3 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={enabled}
            onChange={handleToggle}
            disabled={isUpdating}
            className="w-4 h-4 rounded border-divider-soft text-primary-main focus:ring-primary-main cursor-pointer"
          />
          <span className="text-sm font-semibold text-text-primary">
            Use ChatGPT for this brand&apos;s AI images (Codex as backup)
          </span>
        </label>

        <div className="flex flex-wrap items-center gap-3 text-xs text-text-secondary">
          <span>
            Today: <b>ChatGPT {chatgptCount}</b> · <b>Codex {codexTotal}</b> · template {templateCount}
          </span>
          {quotaPct !== null && quotaPct !== undefined && (
            <span className="border-l border-divider-soft pl-3">
              Codex 5 h quota: <b>{quotaPct}%</b> <span className="opacity-70">(from 9Router)</span>
            </span>
          )}
        </div>
      </div>

      {errorMsg && (
        <p role="alert" className="text-xs text-error-main font-medium">
          {errorMsg}
        </p>
      )}

      <p className="text-xs text-text-secondary leading-relaxed">
        Turning this on adds the brand to the ChatGPT list in Settings › AI Image Engine.
        The face check stays on. Pinterest and Discussion have no AI editing yet.
      </p>
    </section>
  );
}
