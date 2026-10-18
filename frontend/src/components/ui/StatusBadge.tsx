"use client";

import React from "react";
import { clsx } from "clsx";

export type StatusTone = "ok" | "pri" | "info" | "warn" | "bad" | "off";

export interface StatusConfig {
  label: string;
  tone: StatusTone;
  bg: string;
  fg: string;
  className: string;
}

export const STATUS_MAP: Record<string, StatusConfig> = {
  // ── Post status ───────────────────────────────────
  pending_caption: {
    label: "Writing caption",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
  pending_design: {
    label: "Needs design",
    tone: "warn",
    bg: "var(--warn-soft)",
    fg: "var(--warn)",
    className: "bg-warn-soft text-warn",
  },
  rendering: {
    label: "Rendering",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
  pending_review: {
    label: "Needs review",
    tone: "warn",
    bg: "var(--warn-soft)",
    fg: "var(--warn)",
    className: "bg-warn-soft text-warn",
  },
  pending_publish: {
    label: "Ready to publish",
    tone: "ok",
    bg: "var(--pri-soft)",
    fg: "var(--pri-text)",
    className: "bg-pri-soft text-pri-text",
  },
  publishing: {
    label: "Publishing",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
  published: {
    label: "Published",
    tone: "pri",
    bg: "var(--pri)",
    fg: "var(--pri-fg)",
    className: "bg-pri text-pri-fg",
  },
  failed: {
    label: "Failed",
    tone: "bad",
    bg: "var(--bad-soft)",
    fg: "var(--bad)",
    className: "bg-bad-soft text-bad",
  },
  skipped: {
    label: "Skipped",
    tone: "off",
    bg: "var(--surface2)",
    fg: "var(--text2)",
    className: "bg-surface2 text-text2 border border-line",
  },

  // ── Channel copy status (deliveries) ──────────────
  waiting_for_slot: {
    label: "Waiting for a slot",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
  scheduled: {
    label: "Scheduled",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
  waiting_for_device: {
    label: "Waiting for a phone",
    tone: "warn",
    bg: "var(--warn-soft)",
    fg: "var(--warn)",
    className: "bg-warn-soft text-warn",
  },
  repliz_scheduled: {
    label: "Scheduled in Repliz",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
  uncertain: {
    label: "Uncertain",
    tone: "warn",
    bg: "var(--warn-soft)",
    fg: "var(--warn)",
    className: "bg-warn-soft text-warn",
  },
  needs_human: {
    label: "Needs you",
    tone: "warn",
    bg: "var(--warn-soft)",
    fg: "var(--warn)",
    className: "bg-warn-soft text-warn",
  },

  // ── Pipeline fallback ─────────────────────────────
  pending_watermark: {
    label: "Adding watermark",
    tone: "info",
    bg: "var(--info-soft)",
    fg: "var(--info)",
    className: "bg-info-soft text-info",
  },
};

export function StatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const norm = (status || "").toLowerCase().trim().replace(/[-\s]/g, "_");
  const config = STATUS_MAP[norm] || {
    label: status || "Unknown",
    tone: "off" as StatusTone,
    bg: "var(--surface2)",
    fg: "var(--text2)",
    className: "bg-surface2 text-text2 border border-line",
  };

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 px-2.5 py-[3px] rounded-full text-xs font-semibold shrink-0",
        config.className,
        className
      )}
      style={{ background: config.bg, color: config.fg }}
    >
      <span
        aria-hidden="true"
        className="w-[7px] h-[7px] rounded-full bg-current shrink-0"
      />
      <span>{config.label}</span>
    </span>
  );
}
