"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import {
  listJobs,
  listFanpages,
  deletePublishJob,
  reeditJob,
  getJobStats,
  listSocialMediaActivity,
} from "@/lib/api";
import type { PublishJob, PublishJobStatus, DeliveryActivity } from "@/lib/types";
import { format } from "date-fns";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import Toast, { type ToastData } from "@/components/ui/Toast";
import ClipPlayer from "@/components/ClipPlayer";
import { getThumbUrl } from "@/lib/thumb";
import { Icon } from "@iconify/react";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ModeChip } from "@/components/ui/ModeChip";
import { MediaThumb } from "@/components/activity/MediaThumb";
import { ChannelChip } from "@/components/activity/ChannelChip";
import { CopyId } from "@/components/activity/CopyId";
import { isVideoUrl, getJobMediaInfo } from "@/components/activity/mediaUtils";
import {
  getDeliveriesByJobIds,
  markDeliveryPosted,
  markDeliveryNotPosted,
  retryDelivery,
  cancelDelivery,
  type DeliveryCopy,
} from "@/lib/deliveriesApi";
import { useBrandFilter } from "@/contexts/BrandFilterContext";
import TodayPlanCard from "@/components/history/TodayPlanCard";

const parseUtc = (s: string) =>
  new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");

type FanpageLite = { id: number; name: string; is_active?: boolean };

function jobTimes(job: PublishJob) {
  const queuedRaw =
    job.published_at ?? (job as unknown as Record<string, string>).updated_at;
  const queuedDate = queuedRaw ? parseUtc(queuedRaw) : null;
  const scheduledDate = job.scheduled_for ? parseUtc(job.scheduled_for) : null;
  const isPendingLive =
    !!scheduledDate && scheduledDate.getTime() > Date.now() + 60_000;
  const differs =
    !!scheduledDate &&
    !!queuedDate &&
    Math.abs(scheduledDate.getTime() - queuedDate.getTime()) > 120_000;
  return { queuedDate, scheduledDate, isPendingLive, differs };
}

/** Owner rule: "published" only when at least one copy is really live. A job Repliz merely accepted is still scheduled. */
function liveState(
  job: PublishJob,
  copies: (DeliveryCopy | DeliveryActivity)[] | undefined
): "live" | "scheduled" | "other" {
  const hasLiveCopy =
    !!copies && copies.length > 0 && copies.some((c) => c.state === "published");
  const hasNoCopies = !copies || copies.length === 0;
  const isPastOrNoScheduled =
    !job.scheduled_for || parseUtc(job.scheduled_for).getTime() <= Date.now();

  if (
    hasLiveCopy ||
    (hasNoCopies && job.status === "published" && isPastOrNoScheduled)
  ) {
    return "live";
  }

  const isFutureScheduled =
    !!job.scheduled_for && parseUtc(job.scheduled_for).getTime() > Date.now();
  const hasScheduledCopy =
    !!copies &&
    copies.some((c) => c.state === "repliz_scheduled" || c.state === "scheduled");

  if (job.status === "published" && (hasScheduledCopy || isFutureScheduled)) {
    return "scheduled";
  }

  return "other";
}

const STATUS_TABS = [
  { value: "scheduled", label: "Scheduled" },
  { value: "published", label: "Published" },
  { value: "failed", label: "Failed" },
  { value: "skipped", label: "Skipped" },
] as const;

type ActivityTab = (typeof STATUS_TABS)[number]["value"];

const ENGINE_LABELS: Record<string, string> = {
  chatgpt: "ChatGPT",
  codex: "Codex",
  codex_fallback: "Codex · fallback",
  template: "Template",
  google_flow: "Google Flow",
};

const AVATAR_COLORS = [
  "#00A76F",
  "#8E33FF",
  "#FFAB00",
  "#FF5630",
  "#00B8D9",
  "#5119B7",
  "#B76E00",
  "#B71D18",
];
function avatarColor(name: string) {
  let n = 0;
  for (let i = 0; i < name.length; i++) n += name.charCodeAt(i);
  return AVATAR_COLORS[n % AVATAR_COLORS.length];
}

function resolveUrls(job: PublishJob): string[] {
  if (job.content_type === "youtube_clip" || job.content_type === "ai_video") {
    return job.video_thumbnail_url
      ? [job.video_thumbnail_url]
      : job.video_url
      ? [job.video_url]
      : [];
  }
  if (
    job.content_type === "news_content" ||
    job.content_type === "ig_recreate" ||
    job.content_type === "discussion" ||
    job.content_type === "pinterest_content" ||
    job.content_type === "facebook_recreate"
  ) {
    return job.design_image_url ? [job.design_image_url] : [];
  }
  const pub = job.image_public_urls ?? [];
  const src = (job as unknown as Record<string, string[]>).image_source_urls ?? [];
  if (pub.length && pub[0].includes("localhost") && src.length) return src;
  if (pub.length > 0) return pub;
  if (job.video_url) return [job.video_url];
  if (job.video_thumbnail_url) return [job.video_thumbnail_url];
  return [];
}

function sourceLink(job: PublishJob): { url: string; label: string; icon: string } | null {
  if (job.content_type === "youtube_clip" && job.yt_video_id) {
    const t = Math.floor(job.clip_start_s ?? 0);
    return {
      url: `https://youtu.be/${job.yt_video_id}?t=${t}`,
      label: "Source on YouTube",
      icon: "solar:play-circle-bold-duotone",
    };
  }
  if (job.content_type === "facebook_recreate" && job.source_photo_url) {
    return {
      url: job.source_photo_url,
      label: "Source on Facebook",
      icon: "solar:gallery-bold-duotone",
    };
  }
  if (job.article_url) {
    return {
      url: job.article_url,
      label: job.article_source_name ? `Source: ${job.article_source_name}` : "Source article",
      icon: "solar:global-bold-duotone",
    };
  }
  if (job.ig_post_url) {
    return { url: job.ig_post_url, label: "Original IG post", icon: "solar:camera-bold-duotone" };
  }
  if (job.ig_username) {
    return {
      url: `https://instagram.com/${job.ig_username}`,
      label: `@${job.ig_username}`,
      icon: "solar:camera-bold-duotone",
    };
  }
  return null;
}

const PAGE_SIZE = 30;

const SCHEDULED_STATES = [
  "scheduled",
  "waiting_for_slot",
  "waiting_for_device",
  "repliz_scheduled",
] as const;

async function fetchScheduledDeliveries(): Promise<DeliveryActivity[]> {
  const responses = await Promise.all(
    SCHEDULED_STATES.map((state) =>
      listSocialMediaActivity({ state, limit: 200 })
    )
  );
  const all: DeliveryActivity[] = [];
  const seen = new Set<number>();
  for (const res of responses) {
    const list = res.data ?? [];
    for (const item of list) {
      if (!seen.has(item.id)) {
        seen.add(item.id);
        all.push(item);
      }
    }
  }
  return all.sort((a, b) => {
    if (!a.scheduled_for && !b.scheduled_for) return 0;
    if (!a.scheduled_for) return 1;
    if (!b.scheduled_for) return -1;
    return new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime();
  });
}

function formatScheduledWib(isoString: string): string {
  const d = parseUtc(isoString);
  const weekday = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Jakarta",
    weekday: "short",
  }).format(d);
  const day = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Jakarta",
    day: "numeric",
  }).format(d);
  const month = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Jakarta",
    month: "short",
  }).format(d);
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
  return `${weekday} ${day} ${month}, ${time} WIB`;
}

function formatRelativeHint(isoString: string): string {
  const d = parseUtc(isoString);
  const diffMs = d.getTime() - Date.now();
  if (diffMs <= 0) return "due now";
  const totalMin = Math.round(diffMs / 60000);
  if (totalMin < 1) return "in < 1 min";
  const days = Math.floor(totalMin / (60 * 24));
  const remMinutes = totalMin % (60 * 24);
  const hours = Math.floor(remMinutes / 60);
  const mins = remMinutes % 60;
  if (days > 0) {
    return hours > 0 ? `in ${days} d ${hours} h` : `in ${days} d`;
  }
  if (hours > 0 && mins > 0) {
    return `in ${hours} h ${mins} min`;
  }
  if (hours > 0) {
    return `in ${hours} h`;
  }
  return `in ${mins} min`;
}

export default function HistoryPage() {
  const [activeStatus, setActiveStatus] = useState<ActivityTab>("published");
  const { selectedBrandId, setSelectedBrandId } = useBrandFilter();
  const fanpageFilter = selectedBrandId !== null ? String(selectedBrandId) : "";
  const [searchQuery, setSearchQuery] = useState("");
  const [blurred, setBlurred] = useState(false);
  const [lightbox, setLightbox] = useState<{ jobIndex: number; imgIdx: number } | null>(null);
  const [scheduledPreview, setScheduledPreview] = useState<{ delivery: DeliveryActivity; imgIdx: number } | null>(null);

  const { data: fanpages = [] } = useSWR<FanpageLite[]>(
    "history-fanpages",
    () => listFanpages().then((r) => r.data as FanpageLite[])
  );

  const sortedFanpages = useMemo(() => {
    return [...fanpages].sort((a, b) => {
      const aActive = a.is_active !== false ? 1 : 0;
      const bActive = b.is_active !== false ? 1 : 0;
      if (aActive !== bActive) return bActive - aActive;
      return a.name.localeCompare(b.name);
    });
  }, [fanpages]);

  const { data: jobStats } = useSWR(
    fanpageFilter ? ["history-fanpage-stats", fanpageFilter] : null,
    () =>
      getJobStats({ fanpage_id: Number(fanpageFilter) }).then((r) => r.data),
    { refreshInterval: 60000 }
  );

  const {
    data: pages,
    size,
    setSize,
    mutate: mutateJobs,
    isValidating,
  } = useSWRInfinite<PublishJob[]>(
    (index, prev: PublishJob[] | null) => {
      if (activeStatus === "scheduled") return null;
      if (prev && prev.length < PAGE_SIZE) return null;
      return ["history-jobs", activeStatus, fanpageFilter, index] as const;
    },
    ([, status, fanpageId, index]) =>
      listJobs({
        status: status as string,
        fanpage_id: fanpageId ? Number(fanpageId) : undefined,
        limit: PAGE_SIZE,
        offset: (index as number) * PAGE_SIZE,
      }).then((r) => r.data as PublishJob[]),
    { refreshInterval: 60000, revalidateFirstPage: false }
  );

  const rawJobs = useMemo(() => (pages ? pages.flat() : []), [pages]);
  const postIds = useMemo(() => rawJobs.map((j) => j.id), [rawJobs]);
  const postIdsKey = postIds.join(",");

  const { data: deliveries = [], mutate: mutateDeliveries } = useSWR(
    postIds.length > 0 && activeStatus !== "scheduled" ? ["deliveries", postIdsKey] : null,
    () => getDeliveriesByJobIds(postIds),
    { refreshInterval: 60000 }
  );

  const {
    data: scheduledDeliveries = [],
    mutate: mutateScheduled,
    isLoading: isScheduledLoading,
  } = useSWR<DeliveryActivity[]>(
    activeStatus === "scheduled" ? "history-scheduled-deliveries" : null,
    fetchScheduledDeliveries,
    { refreshInterval: 60000 }
  );

  const filteredScheduledDeliveries = useMemo(() => {
    let list = scheduledDeliveries;
    if (selectedBrandId !== null) {
      list = list.filter((d) => d.content_project_id === selectedBrandId);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((d) => {
        const title = (d.title_snapshot ?? "").toLowerCase();
        const dest = (d.destination_name ?? "").toLowerCase();
        const cap = (d.caption_snapshot ?? "").toLowerCase();
        const idStr = String(d.publish_job_id);
        return title.includes(q) || dest.includes(q) || cap.includes(q) || idStr.includes(q);
      });
    }
    return list;
  }, [scheduledDeliveries, selectedBrandId, searchQuery]);

  const deliveriesByJob = useMemo(() => {
    const map: Record<number, DeliveryCopy[]> = {};
    for (const d of deliveries) {
      if (!map[d.publish_job_id]) {
        map[d.publish_job_id] = [];
      }
      map[d.publish_job_id].push(d);
    }
    return map;
  }, [deliveries]);
  const lastPage = pages?.[pages.length - 1];
  const reachedEnd = !!lastPage && lastPage.length < PAGE_SIZE;
  const loadingMore = isValidating;
  const isLoading = !pages;

  useEffect(() => {
    if (activeStatus !== "scheduled") {
      setSize(1);
    }
  }, [activeStatus, fanpageFilter, setSize]);

  // Client-side search filtering on loaded posts
  const jobs = useMemo(() => {
    let list = rawJobs;
    if (activeStatus === "published") {
      list = list.filter((j) => liveState(j, deliveriesByJob[j.id]) === "live");
    }
    if (!searchQuery.trim()) return list;
    const q = searchQuery.toLowerCase();
    return list.filter((j) => {
      const cap = (j.ai_generated_caption ?? "").toLowerCase();
      const title = (j.design_title ?? "").toLowerCase();
      const fp = (j.fanpage_name ?? "").toLowerCase();
      const ig = (j.ig_username ?? "").toLowerCase();
      return cap.includes(q) || title.includes(q) || fp.includes(q) || ig.includes(q);
    });
  }, [rawJobs, activeStatus, deliveriesByJob, searchQuery]);

  // Sentinel for infinite scrolling
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || reachedEnd || activeStatus === "scheduled") return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !loadingMore) setSize((s) => s + 1);
      },
      { rootMargin: "600px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [reachedEnd, loadingMore, setSize, activeStatus]);

  const lightboxJobData = lightbox ? jobs[lightbox.jobIndex] ?? null : null;
  const lightboxUrls = lightboxJobData ? resolveUrls(lightboxJobData) : [];

  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setLightbox(null);
      } else if (e.key === "ArrowLeft") {
        setLightbox((l) => (l && l.jobIndex > 0 ? { jobIndex: l.jobIndex - 1, imgIdx: 0 } : l));
      } else if (e.key === "ArrowRight") {
        setLightbox((l) => {
          if (!l) return l;
          if (l.jobIndex < jobs.length - 1) return { jobIndex: l.jobIndex + 1, imgIdx: 0 };
          if (!reachedEnd) setSize((s) => s + 1);
          return l;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox, jobs.length, reachedEnd, setSize]);

  const [confirmDeleteJob, setConfirmDeleteJob] = useState<PublishJob | null>(null);
  const [deletingJob, setDeletingJob] = useState(false);
  const [confirmReeditJob, setConfirmReeditJob] = useState<PublishJob | null>(null);
  const [reeditingJob, setReeditingJob] = useState(false);
  const [confirmDeliveryAction, setConfirmDeliveryAction] = useState<{
    delivery: DeliveryCopy;
    action: "mark-posted" | "mark-not-posted" | "retry";
  } | null>(null);
  const [deliveryActionLoading, setDeliveryActionLoading] = useState(false);
  const [confirmingCancelId, setConfirmingCancelId] = useState<number | null>(null);
  const [cancelingId, setCancelingId] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastData | null>(null);
  const notify = (message: string, type?: ToastData["type"]) => setToast({ message, type });

  async function handleCancelDelivery(id: number) {
    setCancelingId(id);
    try {
      await cancelDelivery(id);
      mutateScheduled(
        (current) => (current ? current.filter((d) => d.id !== id) : []),
        false
      );
      notify("Post cancelled", "success");
      setConfirmingCancelId(null);
    } catch (err: unknown) {
      let errorDetail = "Failed to cancel post";
      if (err && typeof err === "object" && "response" in err) {
        const res = (err as { response?: { data?: { detail?: unknown } } }).response;
        if (res?.data?.detail) {
          if (typeof res.data.detail === "string") {
            errorDetail = res.data.detail;
          } else if (Array.isArray(res.data.detail)) {
            errorDetail = res.data.detail
              .map((d: { msg?: string }) => d.msg || JSON.stringify(d))
              .join(", ");
          } else {
            errorDetail = JSON.stringify(res.data.detail);
          }
        }
      } else if (err instanceof Error) {
        errorDetail = err.message;
      }
      notify(errorDetail, "error");
    } finally {
      setCancelingId(null);
    }
  }

  async function handleDeliveryActionConfirm() {
    if (!confirmDeliveryAction) return;
    const { delivery, action } = confirmDeliveryAction;
    setDeliveryActionLoading(true);
    try {
      if (action === "mark-posted") {
        await markDeliveryPosted(delivery.id);
        notify("Copy marked as published.", "success");
      } else if (action === "mark-not-posted") {
        await markDeliveryNotPosted(delivery.id);
        notify("Copy marked as not posted (failed).", "success");
      } else if (action === "retry") {
        await retryDelivery(delivery.id);
        notify("Copy queued for retry.", "success");
      }
      await Promise.all([mutateDeliveries(), mutateJobs()]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Action failed";
      notify(msg, "error");
    } finally {
      setDeliveryActionLoading(false);
      setConfirmDeliveryAction(null);
    }
  }

  async function confirmDeleteJobAction() {
    if (!confirmDeleteJob) return;
    setDeletingJob(true);
    try {
      const res = await deletePublishJob(confirmDeleteJob.id);
      const { repliz_deleted, repliz_error } = res.data;
      if (repliz_deleted === true) {
        notify("Deleted from history and removed from Facebook via Repliz.", "success");
      } else if (repliz_deleted === false) {
        notify(
          `Deleted from history, but Repliz could not remove the live post: ${
            repliz_error ?? "unknown error"
          }`,
          "error"
        );
      } else {
        notify("Deleted from history.", "success");
      }
      if (lightboxJobData?.id === confirmDeleteJob.id) setLightbox(null);
      mutateJobs();
    } catch {
      notify("Delete failed. Please try again.", "error");
    } finally {
      setDeletingJob(false);
      setConfirmDeleteJob(null);
    }
  }

  async function confirmReeditJobAction() {
    if (!confirmReeditJob) return;
    setReeditingJob(true);
    try {
      await reeditJob(confirmReeditJob.id);
      notify("Sent back for redesign with a different image — check the Queue shortly.", "success");
      if (lightboxJobData?.id === confirmReeditJob.id) setLightbox(null);
      mutateJobs();
    } catch {
      notify("Re-edit failed. Please try again.", "error");
    } finally {
      setReeditingJob(false);
      setConfirmReeditJob(null);
    }
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-bold text-text tracking-tight">
            Activity
          </h1>
          <p className="text-sm text-text2 mt-0.5">
            Everything Studio posted, plans to post, or could not post. Every post
            goes to each active channel at its own time, so each channel copy has its
            own line and state.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-text2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={blurred}
            onChange={(e) => setBlurred(e.target.checked)}
            className="w-4 h-4 rounded border-line accent-pri cursor-pointer"
          />
          Privacy blur (for screen sharing)
        </label>
      </div>

      {/* Stat Cards */}
      {fanpageFilter && jobStats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="p-3.5 rounded-xl bg-surface border border-line">
            <div className="text-xs text-text2">Published today</div>
            <div className="text-2xl font-bold text-text mt-0.5">
              {jobStats.published_today}
            </div>
            <div className="text-[11px] text-text2 mt-0.5">channel copies today</div>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-line">
            <div className="text-xs text-text2">Scheduled / Total</div>
            <div className="text-2xl font-bold text-text mt-0.5">
              {jobStats.published_total}
            </div>
            <div className="text-[11px] text-text2 mt-0.5">total delivered</div>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-line">
            <div className="text-xs text-text2">This month</div>
            <div className="text-2xl font-bold text-text mt-0.5">
              {jobStats.published_this_month}
            </div>
            <div className="text-[11px] text-text2 mt-0.5">channel copies</div>
          </div>

          <div className="p-3.5 rounded-xl bg-surface border border-line">
            <div className="text-xs text-text2">Failed total</div>
            <div className="text-2xl font-bold text-bad mt-0.5">
              {jobStats.failed_total}
            </div>
            <div className="text-[11px] text-text2 mt-0.5">failed attempts</div>
          </div>
        </div>
      )}

      {/* Tabs and Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Activity status"
          className="inline-flex flex-wrap gap-1 p-1 rounded-xl bg-surface2 border border-line"
        >
          {STATUS_TABS.map((t) => (
            <button
              key={t.value}
              type="button"
              role="tab"
              aria-selected={activeStatus === t.value}
              onClick={() => setActiveStatus(t.value)}
              className={`h-9 px-3 rounded-lg text-xs font-semibold transition-colors ${
                activeStatus === t.value
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text bg-transparent"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search"
            aria-label="Search captions and sources"
            placeholder="Search captions, sources…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 w-52 sm:w-60 px-3 rounded-lg border border-line bg-surface text-text text-xs focus:outline-none focus:border-pri"
          />

          <select
            aria-label="Brand"
            value={fanpageFilter}
            onChange={(e) =>
              setSelectedBrandId(e.target.value ? Number(e.target.value) : null)
            }
            className="h-9 px-2.5 rounded-lg border border-line bg-surface text-text text-xs focus:outline-none focus:border-pri"
          >
            <option value="">All brands</option>
            {sortedFanpages.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}{f.is_active === false ? " (paused)" : ""}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Today's plan card (Scheduled tab only) */}
      {activeStatus === "scheduled" && (
        <TodayPlanCard brandId={selectedBrandId} />
      )}

      {/* Table Section */}
      <section
        aria-label="Posts"
        className="bg-surface border border-line rounded-xl overflow-hidden"
      >
        {/* Desktop Table View (>= 768px) */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full min-w-[800px] border-collapse text-left text-xs">
            <thead>
              {activeStatus === "scheduled" ? (
                <tr className="border-b border-line bg-surface text-text2 font-semibold text-[11px]">
                  <th scope="col" className="py-3 pl-4 pr-3">
                    Post
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Channel
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Method
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Scheduled for
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Status
                  </th>
                  <th scope="col" className="py-3 pl-3 pr-4 text-right">
                    Actions
                  </th>
                </tr>
              ) : (
                <tr className="border-b border-line bg-surface text-text2 font-semibold text-[11px]">
                  <th scope="col" className="py-3 pl-4 pr-3">
                    Post
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Type
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Made by
                  </th>
                  <th scope="col" className="py-3 px-3">
                    Channel copies
                  </th>
                  <th scope="col" className="py-3 pl-3 pr-4 text-right">
                    Actions
                  </th>
                </tr>
              )}
            </thead>
            <tbody className="divide-y divide-line">
              {activeStatus === "scheduled" ? (
                <>
                  {isScheduledLoading && (
                    <tr>
                      <td colSpan={6} className="p-8 text-center text-text2">
                        Loading scheduled deliveries…
                      </td>
                    </tr>
                  )}

                  {!isScheduledLoading && filteredScheduledDeliveries.length === 0 && (
                    <tr>
                      <td colSpan={6} className="p-12 text-center text-text2">
                        <Icon
                          icon="solar:calendar-bold-duotone"
                          width={36}
                          className="mx-auto opacity-30 mb-2"
                        />
                        Nothing scheduled. Approved posts wait here until their time slot.
                      </td>
                    </tr>
                  )}

                  {!isScheduledLoading &&
                    filteredScheduledDeliveries.map((row) => {
                      const title =
                        row.title_snapshot?.trim() || `Post #${row.publish_job_id}`;
                      const isPhone = row.intended_method === "phone";
                      const blurClass = blurred ? "blur-sm select-none" : "";
                      const canCancel =
                        row.state === "scheduled" ||
                        row.state === "waiting_for_slot" ||
                        row.state === "waiting_for_device";
                      const isReplizScheduled = row.state === "repliz_scheduled";
                      const thumb =
                        row.thumbnail_url ||
                        (row.media_urls && row.media_urls.length > 0 ? row.media_urls[0] : null);
                      const isVideo =
                        row.media_kind === "video" ||
                        isVideoUrl(thumb) ||
                        (row.media_urls ?? []).some((u) => isVideoUrl(u));
                      const isAlbum = row.media_kind === "album";
                      const mediaCount = row.media_urls?.length ?? 0;
                      const extraCount = isAlbum && mediaCount > 1 ? mediaCount - 1 : 0;
                      const hasMedia = !!thumb || mediaCount > 0;
                      const videoSrc =
                        (row.media_urls ?? []).find((u) => isVideoUrl(u)) ||
                        (isVideo ? thumb : null);

                      return (
                        <tr
                          key={row.id}
                          className="hover:bg-surface2/50 transition-colors"
                        >
                          {/* Post */}
                          <td className="py-4 pl-4 pr-3 align-top min-w-[320px] max-w-[440px]">
                            <div className="flex gap-3.5 items-start">
                              <MediaThumb
                                thumbUrl={thumb}
                                videoUrl={videoSrc}
                                isVideo={isVideo}
                                isAlbum={isAlbum}
                                extraCount={extraCount}
                                blurred={blurred}
                                onClick={
                                  hasMedia
                                    ? () => setScheduledPreview({ delivery: row, imgIdx: 0 })
                                    : undefined
                                }
                              />

                              <div className="min-w-0 flex-1 flex flex-col gap-1">
                                <h3
                                  className={`text-[15px] font-semibold text-text line-clamp-2 leading-snug ${blurClass}`}
                                >
                                  {title}
                                </h3>
                                {row.caption_snapshot && (
                                  <p
                                    className={`text-xs text-text2 line-clamp-2 leading-normal ${blurClass}`}
                                  >
                                    {row.caption_snapshot}
                                  </p>
                                )}
                                <span className="text-[11px] text-text2">
                                  Post #{row.publish_job_id}
                                </span>
                              </div>
                            </div>
                          </td>

                          {/* Destination / Channel */}
                          <td className="py-4 px-3 align-top min-w-[200px]">
                            <ChannelChip
                              destinationName={row.destination_name || "—"}
                              actualMethod={row.actual_method}
                              intendedMethod={row.intended_method}
                              replizScheduleId={row.repliz_schedule_id}
                              facebookPermalink={row.facebook_permalink}
                              facebookPostId={row.facebook_post_id}
                              lastError={row.last_error}
                              blurred={blurred}
                            />
                          </td>

                          {/* Method */}
                          <td className="py-4 px-3 align-top whitespace-nowrap">
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md border border-line bg-surface text-text text-[11px] font-medium">
                              {isPhone ? "Phone" : "Repliz"}
                            </span>
                          </td>

                          {/* Scheduled for */}
                          <td className="py-4 px-3 align-top whitespace-nowrap">
                            <div className="flex flex-col gap-0.5">
                              <span className="font-medium text-text">
                                {row.scheduled_for
                                  ? formatScheduledWib(row.scheduled_for)
                                  : "—"}
                              </span>
                              {row.scheduled_for && (
                                <span className="text-[11px] text-text2">
                                  {formatRelativeHint(row.scheduled_for)}
                                </span>
                              )}
                            </div>
                          </td>

                          {/* Status */}
                          <td className="py-4 px-3 align-top whitespace-nowrap">
                            <StatusBadge status={row.state} />
                          </td>

                          {/* Actions */}
                          <td className="py-4 pl-3 pr-4 align-top text-right whitespace-nowrap">
                            {isReplizScheduled ? (
                              <span className="text-xs text-text2 font-medium">
                                Scheduled on Repliz
                              </span>
                            ) : canCancel ? (
                              confirmingCancelId === row.id ? (
                                <div className="inline-flex items-center gap-1.5">
                                  <button
                                    type="button"
                                    disabled={cancelingId === row.id}
                                    onClick={() => handleCancelDelivery(row.id)}
                                    className="h-7 px-2.5 rounded-lg border border-bad/30 bg-bad-soft hover:bg-bad-soft/80 text-bad text-xs font-semibold transition-colors inline-flex items-center gap-1 disabled:opacity-50 cursor-pointer"
                                  >
                                    {cancelingId === row.id && (
                                      <Icon
                                        icon="svg-spinners:180-ring"
                                        width={12}
                                      />
                                    )}
                                    Cancel post
                                  </button>
                                  <button
                                    type="button"
                                    disabled={cancelingId === row.id}
                                    onClick={() => setConfirmingCancelId(null)}
                                    className="h-7 px-2 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors disabled:opacity-50 cursor-pointer"
                                  >
                                    Keep
                                  </button>
                                </div>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => setConfirmingCancelId(row.id)}
                                  className="h-7 px-2.5 rounded-lg border border-line bg-surface hover:bg-bad-soft hover:text-bad text-text text-xs font-medium transition-colors cursor-pointer"
                                >
                                  Cancel
                                </button>
                              )
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                </>
              ) : (
                <>
                  {isLoading && (
                    <tr>
                      <td colSpan={5} className="p-8 text-center text-text2">
                        Loading activity…
                      </td>
                    </tr>
                  )}

                  {!isLoading && jobs.length === 0 && (
                    <tr>
                      <td colSpan={5} className="p-12 text-center text-text2">
                        <Icon
                          icon="solar:history-bold-duotone"
                          width={36}
                          className="mx-auto opacity-30 mb-2"
                        />
                        No activity found for this filter.
                      </td>
                    </tr>
                  )}

                  {!isLoading &&
                    jobs.map((job, idx) => {
                      const urls = resolveUrls(job);
                      const media = getJobMediaInfo(job, urls);
                      const fanpage = job.fanpage_name ?? "Unknown Brand";
                      const caption = job.ai_generated_caption ?? "";
                      const title =
                        job.design_title ??
                        (caption ? caption.slice(0, 70) : `@${job.ig_username}`);
                      const { queuedDate, scheduledDate, isPendingLive, differs } =
                        jobTimes(job);
                      const canReedit =
                        job.content_type === "news_content" &&
                        job.status === "published";
                      const src = sourceLink(job);
                      const blurClass = blurred ? "blur-sm select-none" : "";
                      const copies = deliveriesByJob[job.id] || [];
                      const displayStatus =
                        liveState(job, copies) === "scheduled" ? "scheduled" : job.status;

                      const timeLabel =
                        job.scheduled_for && job.published_at && differs
                          ? `${isPendingLive ? "Goes live" : "Went live"} ${formatScheduledWib(
                              job.scheduled_for
                            )}`
                          : job.published_at
                          ? `Went live ${formatScheduledWib(job.published_at)}`
                          : job.scheduled_for
                          ? `${isPendingLive ? "Goes live" : "Scheduled"} ${formatScheduledWib(
                              job.scheduled_for
                            )}`
                          : queuedDate
                          ? formatScheduledWib(job.created_at || queuedDate.toISOString())
                          : null;

                      return (
                        <tr
                          key={job.id}
                          className="hover:bg-surface2/50 transition-colors"
                        >
                          {/* Post column */}
                          <td className="py-4 pl-4 pr-3 align-top min-w-[340px] max-w-[460px]">
                            <div className="flex gap-3.5 items-start">
                              <MediaThumb
                                thumbUrl={media.thumbUrl}
                                videoUrl={media.videoSrc}
                                isVideo={media.isVideo}
                                isAlbum={media.isAlbum}
                                extraCount={media.extraCount}
                                blurred={blurred}
                                onClick={() =>
                                  setLightbox({ jobIndex: idx, imgIdx: 0 })
                                }
                              />

                              <div className="min-w-0 flex-1 flex flex-col gap-1">
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <span
                                    className={`font-semibold text-xs text-text ${blurClass}`}
                                  >
                                    {fanpage}
                                  </span>
                                  {src && !blurred && (
                                    <a
                                      href={src.url}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="text-[11px] text-pri hover:underline inline-flex items-center gap-1"
                                    >
                                      <Icon icon={src.icon} width={11} />
                                      {src.label}
                                    </a>
                                  )}
                                </div>

                                <h3
                                  className={`text-[15px] font-semibold text-text line-clamp-2 leading-snug ${blurClass}`}
                                >
                                  {title}
                                </h3>

                                {caption && (
                                  <p
                                    className={`text-xs text-text2 line-clamp-2 leading-normal ${blurClass}`}
                                  >
                                    {caption}
                                  </p>
                                )}

                                {timeLabel && (
                                  <span className="text-[11px] text-text2 mt-0.5">
                                    {timeLabel}
                                  </span>
                                )}
                              </div>
                            </div>
                          </td>

                          {/* Type column */}
                          <td className="py-4 px-3 align-top whitespace-nowrap">
                            <ModeChip
                              mode={job.content_type || "repost"}
                              size="sm"
                            />
                          </td>

                          {/* Made by */}
                          <td className="py-4 px-3 align-top whitespace-nowrap">
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md border border-line bg-surface text-text text-[11px] font-medium">
                              {ENGINE_LABELS[job.design_engine ?? ""] ??
                                (job.content_type === "youtube_clip"
                                  ? "Video clip"
                                  : job.content_type === "ai_video"
                                  ? "Google Flow"
                                  : "Template")}
                            </span>
                          </td>

                          {/* Channel copies */}
                          <td className="py-4 px-3 align-top min-w-[240px]">
                            {copies.length > 0 ? (
                              <div className="flex flex-col gap-1.5">
                                {copies.map((c) => (
                                  <ChannelChip
                                    key={c.id}
                                    destinationName={c.destination_name || fanpage}
                                    actualMethod={c.actual_method}
                                    intendedMethod={c.intended_method}
                                    method={c.method}
                                    state={c.state}
                                    replizScheduleId={c.repliz_schedule_id}
                                    facebookPermalink={c.facebook_permalink}
                                    facebookPostId={c.facebook_post_id}
                                    lastError={c.last_error}
                                    blurred={blurred}
                                    onAction={(action) =>
                                      setConfirmDeliveryAction({
                                        delivery: c,
                                        action,
                                      })
                                    }
                                  />
                                ))}
                              </div>
                            ) : job.repliz_schedule_id ? (
                              <ChannelChip
                                destinationName={fanpage}
                                method="Repliz"
                                state={displayStatus}
                                replizScheduleId={job.repliz_schedule_id}
                                blurred={blurred}
                              />
                            ) : (
                              <ChannelChip
                                destinationName={fanpage}
                                method="Direct"
                                state={displayStatus}
                                blurred={blurred}
                              />
                            )}
                            {job.last_error && copies.length === 0 && (
                              <p className="text-[11px] text-bad bg-bad-soft p-1.5 rounded mt-1 leading-snug">
                                {job.last_error}
                              </p>
                            )}
                          </td>

                          {/* Actions */}
                          <td className="py-4 pl-3 pr-4 align-top text-right whitespace-nowrap">
                            <div className="inline-flex items-center gap-1.5">
                              {canReedit && (
                                <button
                                  type="button"
                                  onClick={() => setConfirmReeditJob(job)}
                                  className="h-8 px-2.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors cursor-pointer"
                                >
                                  Re-edit
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => setConfirmDeleteJob(job)}
                                className="h-8 px-2.5 rounded-lg border border-line bg-surface hover:bg-bad-soft text-bad text-xs font-medium transition-colors cursor-pointer"
                              >
                                Delete
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                </>
              )}
            </tbody>
          </table>
        </div>

        {/* Mobile Stacked Card View (< 768px) */}
        <div className="md:hidden divide-y divide-line">
          {activeStatus === "scheduled" ? (
            <>
              {isScheduledLoading && (
                <div className="p-8 text-center text-text2">
                  Loading scheduled deliveries…
                </div>
              )}

              {!isScheduledLoading && filteredScheduledDeliveries.length === 0 && (
                <div className="p-12 text-center text-text2">
                  <Icon
                    icon="solar:calendar-bold-duotone"
                    width={36}
                    className="mx-auto opacity-30 mb-2"
                  />
                  Nothing scheduled. Approved posts wait here until their time slot.
                </div>
              )}

              {!isScheduledLoading &&
                filteredScheduledDeliveries.map((row) => {
                  const title =
                    row.title_snapshot?.trim() || `Post #${row.publish_job_id}`;
                  const isPhone = row.intended_method === "phone";
                  const blurClass = blurred ? "blur-sm select-none" : "";
                  const canCancel =
                    row.state === "scheduled" ||
                    row.state === "waiting_for_slot" ||
                    row.state === "waiting_for_device";
                  const isReplizScheduled = row.state === "repliz_scheduled";
                  const thumb =
                    row.thumbnail_url ||
                    (row.media_urls && row.media_urls.length > 0 ? row.media_urls[0] : null);
                  const isVideo =
                    row.media_kind === "video" ||
                    isVideoUrl(thumb) ||
                    (row.media_urls ?? []).some((u) => isVideoUrl(u));
                  const isAlbum = row.media_kind === "album";
                  const mediaCount = row.media_urls?.length ?? 0;
                  const extraCount = isAlbum && mediaCount > 1 ? mediaCount - 1 : 0;
                  const hasMedia = !!thumb || mediaCount > 0;
                  const videoSrc =
                    (row.media_urls ?? []).find((u) => isVideoUrl(u)) ||
                    (isVideo ? thumb : null);

                  return (
                    <div
                      key={row.id}
                      className="p-4 flex gap-3.5 items-start hover:bg-surface2/30 transition-colors"
                    >
                      <MediaThumb
                        thumbUrl={thumb}
                        videoUrl={videoSrc}
                        isVideo={isVideo}
                        isAlbum={isAlbum}
                        extraCount={extraCount}
                        blurred={blurred}
                        onClick={
                          hasMedia
                            ? () => setScheduledPreview({ delivery: row, imgIdx: 0 })
                            : undefined
                        }
                      />

                      <div className="min-w-0 flex-1 flex flex-col gap-1.5">
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                          <span className="text-[11px] text-text2 font-medium">
                            Post #{row.publish_job_id}
                          </span>
                          <StatusBadge status={row.state} />
                        </div>

                        <h3
                          className={`text-[15px] font-semibold text-text line-clamp-2 leading-snug ${blurClass}`}
                        >
                          {title}
                        </h3>

                        {row.caption_snapshot && (
                          <p
                            className={`text-xs text-text2 line-clamp-2 leading-normal ${blurClass}`}
                          >
                            {row.caption_snapshot}
                          </p>
                        )}

                        <ChannelChip
                          destinationName={row.destination_name || "Scheduled Post"}
                          actualMethod={row.actual_method}
                          intendedMethod={row.intended_method}
                          replizScheduleId={row.repliz_schedule_id}
                          facebookPermalink={row.facebook_permalink}
                          facebookPostId={row.facebook_post_id}
                          lastError={row.last_error}
                          blurred={blurred}
                        />

                        <div className="text-[11px] text-text2 mt-0.5">
                          {row.scheduled_for ? (
                            <span>
                              {formatScheduledWib(row.scheduled_for)} ·{" "}
                              {formatRelativeHint(row.scheduled_for)}
                            </span>
                          ) : (
                            "—"
                          )}
                        </div>

                        {canCancel && (
                          <div className="pt-2 border-t border-line/60 flex justify-end">
                            {confirmingCancelId === row.id ? (
                              <div className="inline-flex items-center gap-1.5">
                                <button
                                  type="button"
                                  disabled={cancelingId === row.id}
                                  onClick={() => handleCancelDelivery(row.id)}
                                  className="h-7 px-2.5 rounded-lg border border-bad/30 bg-bad-soft hover:bg-bad-soft/80 text-bad text-xs font-semibold transition-colors inline-flex items-center gap-1 disabled:opacity-50 cursor-pointer"
                                >
                                  {cancelingId === row.id && (
                                    <Icon
                                      icon="svg-spinners:180-ring"
                                      width={12}
                                    />
                                  )}
                                  Cancel post
                                </button>
                                <button
                                  type="button"
                                  disabled={cancelingId === row.id}
                                  onClick={() => setConfirmingCancelId(null)}
                                  className="h-7 px-2 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors disabled:opacity-50 cursor-pointer"
                                >
                                  Keep
                                </button>
                              </div>
                            ) : (
                              <button
                                type="button"
                                onClick={() => setConfirmingCancelId(row.id)}
                                className="h-7 px-2.5 rounded-lg border border-line bg-surface hover:bg-bad-soft hover:text-bad text-text text-xs font-medium transition-colors cursor-pointer"
                              >
                                Cancel
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
            </>
          ) : (
            <>
              {isLoading && (
                <div className="p-8 text-center text-text2">
                  Loading activity…
                </div>
              )}

              {!isLoading && jobs.length === 0 && (
                <div className="p-12 text-center text-text2">
                  <Icon
                    icon="solar:history-bold-duotone"
                    width={36}
                    className="mx-auto opacity-30 mb-2"
                  />
                  No activity found for this filter.
                </div>
              )}

              {!isLoading &&
                jobs.map((job, idx) => {
                  const urls = resolveUrls(job);
                  const media = getJobMediaInfo(job, urls);
                  const fanpage = job.fanpage_name ?? "Unknown Brand";
                  const caption = job.ai_generated_caption ?? "";
                  const title =
                    job.design_title ??
                    (caption ? caption.slice(0, 70) : `@${job.ig_username}`);
                  const { queuedDate, scheduledDate, isPendingLive, differs } =
                    jobTimes(job);
                  const canReedit =
                    job.content_type === "news_content" &&
                    job.status === "published";
                  const src = sourceLink(job);
                  const blurClass = blurred ? "blur-sm select-none" : "";
                  const copies = deliveriesByJob[job.id] || [];
                  const displayStatus =
                    liveState(job, copies) === "scheduled" ? "scheduled" : job.status;

                  const timeLabel =
                    job.scheduled_for && job.published_at && differs
                      ? `${isPendingLive ? "Goes live" : "Went live"} ${formatScheduledWib(
                          job.scheduled_for
                        )}`
                      : job.published_at
                      ? `Went live ${formatScheduledWib(job.published_at)}`
                      : job.scheduled_for
                      ? `${isPendingLive ? "Goes live" : "Scheduled"} ${formatScheduledWib(
                          job.scheduled_for
                        )}`
                      : queuedDate
                      ? formatScheduledWib(job.created_at || queuedDate.toISOString())
                      : null;

                  return (
                    <div
                      key={job.id}
                      className="p-4 flex gap-3.5 items-start hover:bg-surface2/30 transition-colors"
                    >
                      <MediaThumb
                        thumbUrl={media.thumbUrl}
                        videoUrl={media.videoSrc}
                        isVideo={media.isVideo}
                        isAlbum={media.isAlbum}
                        extraCount={media.extraCount}
                        blurred={blurred}
                        onClick={() =>
                          setLightbox({ jobIndex: idx, imgIdx: 0 })
                        }
                      />

                      <div className="min-w-0 flex-1 flex flex-col gap-1.5">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span
                            className={`font-semibold text-xs text-text ${blurClass}`}
                          >
                            {fanpage}
                          </span>
                          <ModeChip
                            mode={job.content_type || "repost"}
                            size="sm"
                          />
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-surface2 border border-line text-text2">
                            {ENGINE_LABELS[job.design_engine ?? ""] ??
                              (job.content_type === "youtube_clip"
                                ? "Video clip"
                                : job.content_type === "ai_video"
                                ? "Google Flow"
                                : "Template")}
                          </span>
                        </div>

                        <h3
                          className={`text-[15px] font-semibold text-text line-clamp-2 leading-snug ${blurClass}`}
                        >
                          {title}
                        </h3>

                        {caption && (
                          <p
                            className={`text-xs text-text2 line-clamp-2 leading-normal ${blurClass}`}
                          >
                            {caption}
                          </p>
                        )}

                        <div className="flex flex-col gap-1.5 mt-0.5">
                          {copies.length > 0 ? (
                            copies.map((c) => (
                              <ChannelChip
                                key={c.id}
                                destinationName={c.destination_name || fanpage}
                                actualMethod={c.actual_method}
                                intendedMethod={c.intended_method}
                                method={c.method}
                                state={c.state}
                                replizScheduleId={c.repliz_schedule_id}
                                facebookPermalink={c.facebook_permalink}
                                facebookPostId={c.facebook_post_id}
                                lastError={c.last_error}
                                blurred={blurred}
                                onAction={(action) =>
                                  setConfirmDeliveryAction({
                                    delivery: c,
                                    action,
                                  })
                                }
                              />
                            ))
                          ) : job.repliz_schedule_id ? (
                            <ChannelChip
                              destinationName={fanpage}
                              method="Repliz"
                              state={displayStatus}
                              replizScheduleId={job.repliz_schedule_id}
                              blurred={blurred}
                            />
                          ) : (
                            <ChannelChip
                              destinationName={fanpage}
                              method="Direct"
                              state={displayStatus}
                              blurred={blurred}
                            />
                          )}
                          {timeLabel && (
                            <span className="text-[11px] text-text2">
                              {timeLabel}
                            </span>
                          )}
                        </div>

                        {job.last_error && copies.length === 0 && (
                          <p className="text-[11px] text-bad bg-bad-soft p-1.5 rounded leading-snug">
                            {job.last_error}
                          </p>
                        )}

                        <div className="flex items-center justify-between gap-2 pt-2 border-t border-line/60 mt-1">
                          {src && !blurred ? (
                            <a
                              href={src.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-[11px] text-pri hover:underline inline-flex items-center gap-1"
                            >
                              <Icon icon={src.icon} width={11} />
                              {src.label}
                            </a>
                          ) : (
                            <div />
                          )}
                          <div className="inline-flex items-center gap-1.5">
                            {canReedit && (
                              <button
                                type="button"
                                onClick={() => setConfirmReeditJob(job)}
                                className="h-7 px-2.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors cursor-pointer"
                              >
                                Re-edit
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteJob(job)}
                              className="h-7 px-2.5 rounded-lg border border-line bg-surface hover:bg-bad-soft text-bad text-xs font-medium transition-colors cursor-pointer"
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
            </>
          )}
        </div>

        {/* Infinite-scroll sentinel */}
        {activeStatus !== "scheduled" && <div ref={sentinelRef} className="h-4" />}

        {/* Pagination footer */}
        <div className="flex justify-between items-center p-3 px-4 border-t border-line text-xs text-text2 bg-surface">
          {activeStatus === "scheduled" ? (
            <span>
              Showing {filteredScheduledDeliveries.length} scheduled item
              {filteredScheduledDeliveries.length !== 1 ? "s" : ""}
            </span>
          ) : (
            <>
              <span>
                Showing {jobs.length} post{jobs.length !== 1 ? "s" : ""}
              </span>
              {!reachedEnd && (
                <button
                  type="button"
                  disabled={loadingMore}
                  onClick={() => setSize((s) => s + 1)}
                  className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors"
                >
                  {loadingMore && <Icon icon="svg-spinners:180-ring" width={14} />}
                  Load 30 more
                </button>
              )}
            </>
          )}
        </div>
      </section>

      {/* Lightbox */}
      {lightbox && lightboxJobData && (
        <HistoryLightbox
          state={{
            job: lightboxJobData,
            urls: lightboxUrls,
            idx: lightbox.imgIdx,
          }}
          postPosition={{
            index: lightbox.jobIndex,
            total: jobs.length,
            hasMore: !reachedEnd,
          }}
          blurred={blurred}
          copies={deliveriesByJob[lightboxJobData.id] || []}
          onDeliveryAction={(delivery, action) =>
            setConfirmDeliveryAction({ delivery, action })
          }
          onClose={() => setLightbox(null)}
          onPrev={() =>
            setLightbox((l) => (l && l.imgIdx > 0 ? { ...l, imgIdx: l.imgIdx - 1 } : l))
          }
          onNext={() =>
            setLightbox((l) =>
              l && l.imgIdx < lightboxUrls.length - 1 ? { ...l, imgIdx: l.imgIdx + 1 } : l
            )
          }
          onDelete={() => setConfirmDeleteJob(lightboxJobData)}
          onReedit={() => setConfirmReeditJob(lightboxJobData)}
        />
      )}

      {/* Scheduled Delivery Preview Modal */}
      {scheduledPreview && (
        <ScheduledPreviewModal
          delivery={scheduledPreview.delivery}
          imgIdx={scheduledPreview.imgIdx}
          blurred={blurred}
          onClose={() => setScheduledPreview(null)}
          onPrev={() =>
            setScheduledPreview((p) => (p && p.imgIdx > 0 ? { ...p, imgIdx: p.imgIdx - 1 } : p))
          }
          onNext={() =>
            setScheduledPreview((p) => {
              if (!p) return null;
              const count = p.delivery.media_urls?.length ?? 0;
              return p.imgIdx < count - 1 ? { ...p, imgIdx: p.imgIdx + 1 } : p;
            })
          }
        />
      )}

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={!!confirmDeleteJob}
        title="Delete this from history?"
        message={
          confirmDeleteJob?.repliz_schedule_id
            ? "For copies scheduled through Repliz, Studio also asks Repliz to delete the live post. This cannot be undone."
            : "Removes it from Studio history. This cannot be undone."
        }
        confirmLabel="Delete"
        danger
        loading={deletingJob}
        onConfirm={confirmDeleteJobAction}
        onCancel={() => setConfirmDeleteJob(null)}
      />

      <ConfirmDialog
        open={!!confirmReeditJob}
        title="Re-edit with a new image?"
        message="This removes the post from Repliz and sends it back for a fresh design with a different photo. It won't go out again until it's re-published."
        confirmLabel="Re-edit"
        loading={reeditingJob}
        onConfirm={confirmReeditJobAction}
        onCancel={() => setConfirmReeditJob(null)}
      />

      <ConfirmDialog
        open={!!confirmDeliveryAction}
        title={
          confirmDeliveryAction?.action === "mark-posted"
            ? "Mark copy as posted?"
            : confirmDeliveryAction?.action === "mark-not-posted"
            ? "Mark copy as not posted?"
            : "Retry this copy?"
        }
        message={
          confirmDeliveryAction?.action === "mark-posted"
            ? `Confirm that this copy has been published to ${
                confirmDeliveryAction.delivery.destination_name || "the channel"
              }. Studio will record it as live on Facebook and will never attempt to resend it.`
            : confirmDeliveryAction?.action === "mark-not-posted"
            ? `Confirm that this copy did not go live on ${
                confirmDeliveryAction.delivery.destination_name || "the channel"
              }. Studio will mark it as failed, allowing it to be safely retried.`
            : `Retry sending this copy to ${
                confirmDeliveryAction?.delivery.destination_name || "the channel"
              }. It will re-enter the publishing pipeline respecting channel pacing, sleep windows, and device locks.`
        }
        confirmLabel={
          confirmDeliveryAction?.action === "mark-posted"
            ? "Mark posted"
            : confirmDeliveryAction?.action === "mark-not-posted"
            ? "Mark not posted"
            : "Retry copy"
        }
        danger={confirmDeliveryAction?.action === "mark-not-posted"}
        loading={deliveryActionLoading}
        onConfirm={handleDeliveryActionConfirm}
        onCancel={() => {
          if (!deliveryActionLoading) setConfirmDeliveryAction(null);
        }}
      />

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}

/* History Lightbox */
function HistoryLightbox({
  state,
  postPosition,
  blurred,
  copies = [],
  onDeliveryAction,
  onClose,
  onPrev,
  onNext,
  onDelete,
  onReedit,
}: {
  state: { job: PublishJob; urls: string[]; idx: number };
  postPosition: { index: number; total: number; hasMore: boolean };
  blurred: boolean;
  copies?: DeliveryCopy[];
  onDeliveryAction?: (
    delivery: DeliveryCopy,
    action: "mark-posted" | "mark-not-posted" | "retry"
  ) => void;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onDelete: () => void;
  onReedit: () => void;
}) {
  const { job, urls, idx } = state;
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const caption = job.ai_generated_caption ?? "";
  const total = urls.length;
  const currentUrl = urls[idx];
  const { queuedDate, isPendingLive, differs } = jobTimes(job);
  const src = sourceLink(job);
  const canReedit = job.content_type === "news_content" && job.status === "published";
  const displayStatus =
    liveState(job, copies) === "scheduled" ? "scheduled" : job.status;

  const isClip =
    job.content_type === "youtube_clip" || job.content_type === "ai_video";
  const isVideo =
    isClip ||
    isVideoUrl(currentUrl) ||
    (job.video_url && isVideoUrl(job.video_url)) ||
    (urls.length > 0 && isVideoUrl(urls[0]));

  const videoSrc = (isClip && job.video_url)
    ? job.video_url
    : isVideoUrl(currentUrl)
    ? currentUrl
    : (job.video_url && isVideoUrl(job.video_url))
    ? job.video_url
    : urls.find((u) => isVideoUrl(u)) || (isVideo ? currentUrl || job.video_url : null);

  const poster =
    job.video_thumbnail_url ||
    (urls[0] && !isVideoUrl(urls[0]) ? urls[0] : undefined);

  const blurClass = blurred ? "blur-md select-none" : "";

  const timeWibStr =
    job.scheduled_for && job.published_at && differs
      ? `${isPendingLive ? "Goes live" : "Went live"} ${formatScheduledWib(
          job.scheduled_for
        )}`
      : job.published_at
      ? `Went live ${formatScheduledWib(job.published_at)}`
      : job.scheduled_for
      ? `${isPendingLive ? "Goes live" : "Scheduled"} ${formatScheduledWib(
          job.scheduled_for
        )}`
      : queuedDate
      ? formatScheduledWib(job.created_at || queuedDate.toISOString())
      : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 backdrop-blur-md p-4"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl max-h-[92vh] flex flex-col rounded-2xl overflow-hidden shadow-2xl bg-surface border border-line"
        onClick={(e) => e.stopPropagation()}
      >
        {isVideo ? (
          <div className="relative w-full bg-black flex items-center justify-center flex-shrink-0">
            <div
              className="relative h-[58vh] max-h-[58vh] sm:h-[64vh] sm:max-h-[64vh] aspect-[9/16] mx-auto bg-black flex items-center justify-center"
              onClick={(e) => e.stopPropagation()}
            >
              {videoSrc ? (
                <ClipPlayer
                  src={videoSrc}
                  poster={poster}
                  preload="metadata"
                  className={`w-full h-full object-contain ${blurClass}`}
                />
              ) : poster ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  src={poster}
                  alt=""
                  className={`w-full h-full object-contain ${blurClass}`}
                />
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-text2 opacity-40">
                  <Icon icon="solar:videocamera-bold-duotone" width={36} />
                  <p className="text-xs">No video preview</p>
                </div>
              )}
            </div>

            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 text-white text-xs font-semibold px-3 py-1 rounded-full z-10 pointer-events-none">
              {postPosition.index + 1} / {postPosition.total}
              {postPosition.hasMore ? "+" : ""}
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10 cursor-pointer"
            >
              <Icon icon="solar:close-bold" width={16} />
            </button>
          </div>
        ) : (
          <div className="relative bg-black aspect-[4/3] w-full flex-shrink-0 flex items-center justify-center">
            {total > 0 ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={urls[idx]}
                alt=""
                className={`absolute inset-0 w-full h-full object-contain ${blurClass}`}
              />
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-text2 opacity-40">
                <Icon icon="solar:gallery-bold-duotone" width={36} />
                <p className="text-xs">No media preview</p>
              </div>
            )}

            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 text-white text-xs font-semibold px-3 py-1 rounded-full z-10 pointer-events-none">
              {postPosition.index + 1} / {postPosition.total}
              {postPosition.hasMore ? "+" : ""}
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10 cursor-pointer"
            >
              <Icon icon="solar:close-bold" width={16} />
            </button>

            {!isClip && total > 1 && idx > 0 && (
              <button
                type="button"
                onClick={onPrev}
                aria-label="Previous image"
                className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10 cursor-pointer"
              >
                <Icon icon="solar:alt-arrow-left-bold-duotone" width={18} />
              </button>
            )}
            {!isClip && total > 1 && idx < total - 1 && (
              <button
                type="button"
                onClick={onNext}
                aria-label="Next image"
                className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10 cursor-pointer"
              >
                <Icon icon="solar:alt-arrow-right-bold-duotone" width={18} />
              </button>
            )}
          </div>
        )}

        {/* Details Panel */}
        <div className="p-4 flex flex-col gap-3 overflow-y-auto flex-1 min-h-0">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p
                className={`text-sm font-semibold text-text ${
                  blurred ? "blur-sm" : ""
                }`}
              >
                {fanpage}
              </p>
              <p
                className={`text-xs text-text2 flex items-center gap-1.5 flex-wrap ${
                  blurred ? "blur-sm" : ""
                }`}
              >
                <span>@{job.ig_username}</span>
                {timeWibStr && <span>· {timeWibStr}</span>}
              </p>
            </div>
            <StatusBadge status={displayStatus} />
          </div>

          {/* Channels with Repliz ID copy / Open post link */}
          <div className="flex flex-col gap-1 border-t border-line/60 pt-2">
            <span className="text-[11px] font-semibold text-text2 uppercase tracking-wider">
              Channels
            </span>
            <div className="flex flex-col gap-1.5">
              {copies.length > 0 ? (
                copies.map((c) => (
                  <ChannelChip
                    key={c.id}
                    destinationName={c.destination_name || fanpage}
                    actualMethod={c.actual_method}
                    intendedMethod={c.intended_method}
                    method={c.method}
                    state={c.state}
                    replizScheduleId={c.repliz_schedule_id}
                    facebookPermalink={c.facebook_permalink}
                    facebookPostId={c.facebook_post_id}
                    lastError={c.last_error}
                    blurred={blurred}
                    onAction={
                      onDeliveryAction
                        ? (action) => onDeliveryAction(c, action)
                        : undefined
                    }
                  />
                ))
              ) : job.repliz_schedule_id ? (
                <ChannelChip
                  destinationName={fanpage}
                  method="Repliz"
                  state={displayStatus}
                  replizScheduleId={job.repliz_schedule_id}
                  blurred={blurred}
                />
              ) : (
                <ChannelChip
                  destinationName={fanpage}
                  method="Direct"
                  state={displayStatus}
                  blurred={blurred}
                />
              )}
            </div>
          </div>

          {/* Caption in scrollable box with max-h-40 */}
          {caption && (
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold text-text2 uppercase tracking-wider">
                Caption
              </span>
              <div
                className={`text-xs text-text2 leading-relaxed whitespace-pre-line bg-surface2 p-3 rounded-lg border border-line max-h-40 overflow-y-auto ${
                  blurred ? "blur-sm select-none" : ""
                }`}
              >
                {caption}
              </div>
            </div>
          )}

          {job.last_error && (
            <p className="text-xs text-bad bg-bad-soft p-3 rounded-lg leading-relaxed">
              {job.last_error}
            </p>
          )}

          <div className="flex items-center justify-between gap-3 pt-2 border-t border-line">
            {src && !blurred ? (
              <a
                href={src.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-pri hover:underline inline-flex items-center gap-1.5"
              >
                <Icon icon={src.icon} width={13} />
                {src.label}
              </a>
            ) : (
              <div />
            )}

            <div className="flex items-center gap-2">
              {canReedit && (
                <button
                  type="button"
                  onClick={onReedit}
                  className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold cursor-pointer"
                >
                  Re-edit
                </button>
              )}
              <button
                type="button"
                onClick={onDelete}
                className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-bad-soft text-bad text-xs font-semibold cursor-pointer"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ScheduledPreviewModal({
  delivery,
  imgIdx,
  blurred,
  onClose,
  onPrev,
  onNext,
}: {
  delivery: DeliveryActivity;
  imgIdx: number;
  blurred: boolean;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
}) {
  const urls = delivery.media_urls ?? [];
  const total = urls.length;
  const blurClass = blurred ? "blur-md select-none" : "";

  const isVideo =
    delivery.media_kind === "video" ||
    isVideoUrl(delivery.thumbnail_url) ||
    urls.some((u) => isVideoUrl(u));

  const videoSrc =
    urls.find((u) => isVideoUrl(u)) ||
    (delivery.media_kind === "video" ? urls[0] : null) ||
    (isVideoUrl(delivery.thumbnail_url) ? delivery.thumbnail_url : null);

  const posterSrc =
    delivery.thumbnail_url && !isVideoUrl(delivery.thumbnail_url)
      ? delivery.thumbnail_url
      : urls[0] && !isVideoUrl(urls[0])
      ? urls[0]
      : undefined;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && !isVideo && total > 1) onPrev();
      else if (e.key === "ArrowRight" && !isVideo && total > 1) onNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext, isVideo, total]);

  const title =
    delivery.title_snapshot?.trim() || `Post #${delivery.publish_job_id}`;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm p-4 select-none"
      onClick={onClose}
    >
      <button
        type="button"
        onClick={onClose}
        title="Close (Esc)"
        className="absolute top-4 right-4 p-2 rounded-lg bg-white/10 text-white hover:bg-white/20 transition-colors z-20 cursor-pointer"
      >
        <Icon icon="solar:close-circle-bold" width={28} />
      </button>

      {!isVideo && total > 1 && (
        <div
          className="absolute top-4 left-4 px-3 py-1.5 rounded-full bg-white/10 text-white text-xs font-semibold z-20"
          onClick={(e) => e.stopPropagation()}
        >
          {imgIdx + 1} / {total}
        </div>
      )}

      {!isVideo && total > 1 && imgIdx > 0 && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onPrev();
          }}
          title="Previous (←)"
          className="absolute left-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20 transition-colors z-20 cursor-pointer"
        >
          <Icon icon="solar:alt-arrow-left-bold" width={28} />
        </button>
      )}

      {!isVideo && total > 1 && imgIdx < total - 1 && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onNext();
          }}
          title="Next (→)"
          className="absolute right-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20 transition-colors z-20 cursor-pointer"
        >
          <Icon icon="solar:alt-arrow-right-bold" width={28} />
        </button>
      )}

      <div
        className="max-w-[90vw] max-h-[90vh] flex flex-col items-center"
        onClick={(e) => e.stopPropagation()}
      >
        {isVideo ? (
          <div
            className="relative h-[65vh] max-h-[65vh] aspect-[9/16] mx-auto bg-black rounded-xl overflow-hidden flex items-center justify-center shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {videoSrc ? (
              <video
                controls
                src={videoSrc}
                poster={posterSrc}
                playsInline
                preload="metadata"
                className={`w-full h-full object-contain bg-black ${blurClass}`}
                onClick={(e) => e.stopPropagation()}
              />
            ) : posterSrc ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={posterSrc}
                alt={title}
                className={`w-full h-full object-contain ${blurClass}`}
              />
            ) : (
              <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-text2">
                <Icon icon="solar:videocamera-bold-duotone" width={40} />
                <p className="text-xs">No media preview</p>
              </div>
            )}
          </div>
        ) : total > 0 ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={urls[imgIdx] || delivery.thumbnail_url || ""}
            alt={title}
            className={`max-w-[90vw] max-h-[75vh] object-contain rounded-lg ${blurClass}`}
          />
        ) : delivery.thumbnail_url ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={delivery.thumbnail_url}
            alt={title}
            className={`max-w-[90vw] max-h-[75vh] object-contain rounded-lg ${blurClass}`}
          />
        ) : (
          <div className="w-80 h-80 rounded-lg bg-surface2 border border-line flex flex-col items-center justify-center gap-2 text-text2">
            <Icon icon="solar:gallery-bold-duotone" width={40} />
            <p className="text-xs">No media preview</p>
          </div>
        )}

        <div className="mt-3 text-center text-white/80 text-xs max-w-xl">
          <div className="flex items-center justify-center gap-2 flex-wrap">
            <span
              className={`font-semibold text-white ${
                blurred ? "blur-sm select-none" : ""
              }`}
            >
              {delivery.destination_name || "Scheduled Post"}
            </span>
            {delivery.scheduled_for && (
              <span className="text-white/60">
                · {formatScheduledWib(delivery.scheduled_for)}
              </span>
            )}
            <span className="text-white/60">
              · Post #{delivery.publish_job_id}
            </span>
            {delivery.repliz_schedule_id && (
              <CopyId
                id={delivery.repliz_schedule_id}
                className="text-white bg-white/10 border-white/20"
              />
            )}
            {delivery.facebook_permalink && (
              <a
                href={delivery.facebook_permalink}
                target="_blank"
                rel="noopener noreferrer"
                className="text-pri hover:underline inline-flex items-center gap-1 font-medium ml-1"
                onClick={(e) => e.stopPropagation()}
              >
                Open post ↗
              </a>
            )}
          </div>
          {delivery.caption_snapshot && (
            <p
              className={`mt-1 text-white/70 line-clamp-2 ${
                blurred ? "blur-sm select-none" : ""
              }`}
            >
              {delivery.caption_snapshot}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
