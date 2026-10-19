"use client";

import { Icon } from "@iconify/react";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CopyId } from "./CopyId";

export interface ChannelChipProps {
  destinationName: string;
  actualMethod?: string | null;
  intendedMethod?: string | null;
  method?: string | null;
  state?: string | null;
  replizScheduleId?: string | null;
  facebookPermalink?: string | null;
  facebookPostId?: string | null;
  lastError?: string | null;
  blurred?: boolean;
  onAction?: (action: "mark-posted" | "mark-not-posted" | "retry") => void;
  className?: string;
}

export function resolveMethodLabel(item: {
  actualMethod?: string | null;
  intendedMethod?: string | null;
  method?: string | null;
  replizScheduleId?: string | null;
}): "Phone" | "Repliz" | "Direct" {
  const raw = (
    item.actualMethod ||
    item.intendedMethod ||
    item.method ||
    ""
  ).toLowerCase();

  if (raw.includes("phone")) return "Phone";
  if (raw.includes("repliz") || !!item.replizScheduleId) return "Repliz";
  if (raw) return (raw.charAt(0).toUpperCase() + raw.slice(1)) as "Phone" | "Repliz" | "Direct";
  return "Direct";
}

export function ChannelChip({
  destinationName,
  actualMethod,
  intendedMethod,
  method,
  state,
  replizScheduleId,
  facebookPermalink,
  facebookPostId,
  lastError,
  blurred = false,
  onAction,
  className = "",
}: ChannelChipProps) {
  const methodLabel = resolveMethodLabel({
    actualMethod,
    intendedMethod,
    method,
    replizScheduleId,
  });

  const fbUrl =
    facebookPermalink ||
    (facebookPostId ? `https://facebook.com/${facebookPostId}` : null);

  const blurClass = blurred ? "blur-sm select-none" : "";

  return (
    <div
      className={`flex flex-col gap-1.5 py-1 text-xs ${className}`}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
        {/* Chip badge: destination · method */}
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-line bg-surface text-text shadow-2xs font-medium">
          <span className={`text-text ${blurClass}`}>
            {destinationName} · {methodLabel}
          </span>
          {state && <StatusBadge status={state} />}
        </span>

        {/* Repliz ID copy button */}
        {replizScheduleId && <CopyId id={replizScheduleId} />}

        {/* Facebook permalink */}
        {fbUrl && !blurred && state !== "uncertain" && (
          <a
            href={fbUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-pri hover:underline inline-flex items-center gap-1 font-medium px-1.5 py-0.5 rounded hover:bg-pri/5 transition-colors"
          >
            <Icon icon="solar:link-circle-bold-duotone" width={13} />
            Open post ↗
          </a>
        )}
      </div>

      {/* Error message */}
      {lastError && (
        <p className="text-[11px] text-bad bg-bad-soft p-1.5 rounded leading-snug">
          {lastError}
        </p>
      )}

      {/* Uncertain delivery actions */}
      {state === "uncertain" && onAction && (
        <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
          {fbUrl && !blurred && (
            <a
              href={fbUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="h-6 px-2 text-[11px] font-medium rounded border border-line bg-surface hover:bg-surface2 text-text inline-flex items-center gap-1 transition-colors"
            >
              <Icon icon="solar:link-circle-bold-duotone" width={12} />
              Open post ↗
            </a>
          )}
          <button
            type="button"
            onClick={() => onAction("mark-posted")}
            className="h-6 px-2 text-[11px] font-medium rounded border border-line bg-surface hover:bg-surface2 text-text transition-colors cursor-pointer"
          >
            Mark posted
          </button>
          <button
            type="button"
            onClick={() => onAction("mark-not-posted")}
            className="h-6 px-2 text-[11px] font-medium rounded border border-line bg-surface hover:bg-bad-soft text-bad transition-colors cursor-pointer"
          >
            Mark not posted
          </button>
        </div>
      )}

      {/* Retry failed copy action */}
      {state === "failed" && !replizScheduleId && !facebookPostId && onAction && (
        <div className="flex items-center gap-1.5 mt-0.5">
          <button
            type="button"
            onClick={() => onAction("retry")}
            className="h-6 px-2 text-[11px] font-medium rounded border border-line bg-surface hover:bg-surface2 text-pri transition-colors inline-flex items-center gap-1 cursor-pointer"
          >
            <Icon icon="solar:restart-bold" width={11} />
            Retry this copy
          </button>
        </div>
      )}
    </div>
  );
}
