"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  listJobs,
  publishJob,
  skipJob,
  updateJobCaption,
  regenerateCaption,
  renderJobNow,
} from "@/lib/api";
import type { PublishJob } from "@/lib/types";
import { Icon } from "@iconify/react";
import ClipPlayer from "@/components/ClipPlayer";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ModeChip } from "@/components/ui/ModeChip";
import Toast, { type ToastData } from "@/components/ui/Toast";
import UndoToastStack, { type PendingAction } from "@/components/ui/UndoToast";
import { useBrandFilter } from "@/contexts/BrandFilterContext";
import { useFanpages } from "@/lib/useFanpages";

/* Review queue = IG Repost jobs awaiting caption approval + Mode 2 news jobs
   awaiting design (pending_design) or publish approval (pending_publish). */
const fetcher = async () => {
  const [review, design, publish] = await Promise.all([
    listJobs({ status: "pending_review", limit: 100 }),
    listJobs({ status: "pending_design", limit: 100 }),
    listJobs({ status: "pending_publish", limit: 100 }),
  ]);
  const news = [
    ...(design.data as PublishJob[]),
    ...(publish.data as PublishJob[]),
  ].filter(
    (j) =>
      j.content_type === "news_content" ||
      j.content_type === "ig_recreate" ||
      j.content_type === "discussion" ||
      j.content_type === "pinterest_content" ||
      j.content_type === "facebook_recreate" ||
      j.content_type === "youtube_clip" ||
      j.content_type === "ai_video"
  );
  return [...(review.data as PublishJob[]), ...news].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );
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

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function resolveUrls(job: PublishJob): string[] {
  if (job.content_type === "youtube_clip" || job.content_type === "ai_video") {
    return job.video_thumbnail_url ? [job.video_thumbnail_url] : [];
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
  return pub;
}

function getJobTitle(job: PublishJob): string {
  const t =
    job.design_title ??
    (job.ig_username ? `@${job.ig_username}` : job.ai_generated_caption?.slice(0, 40) ?? "Post");
  return t.trim() || "Post";
}

interface LightboxState {
  job: PublishJob;
  urls: string[];
  idx: number;
}

export default function QueuePage() {
  const {
    data: jobs = [],
    isLoading,
    mutate,
  } = useSWR("queue", fetcher, {
    refreshInterval: 15000,
  });

  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"all" | "review" | "design" | "ready">("all");
  const { selectedBrandId, setSelectedBrandId } = useBrandFilter();
  const { data: fanpages = [] } = useFanpages();
  const [typeFilter, setTypeFilter] = useState("all");
  const [captionDrafts, setCaptionDrafts] = useState<Record<number, string>>({});
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);
  const [toast, setToast] = useState<ToastData | null>(null);
  const [pendingActions, setPendingActions] = useState<PendingAction[]>([]);
  const pendingActionsRef = useRef<
    Map<
      number,
      {
        action: PendingAction;
        timerId: NodeJS.Timeout;
        expiresAt: number;
      }
    >
  >(new Map());

  // Keyboard nav for lightbox
  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (!lightbox) return;
      if (e.key === "Escape") {
        setLightbox(null);
        return;
      }
      if (e.key === "ArrowRight")
        setLightbox((l) =>
          l && l.idx < l.urls.length - 1 ? { ...l, idx: l.idx + 1 } : l
        );
      if (e.key === "ArrowLeft")
        setLightbox((l) => (l && l.idx > 0 ? { ...l, idx: l.idx - 1 } : l));
    },
    [lightbox]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  useEffect(() => {
    document.body.style.overflow = lightbox ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [lightbox]);

  // Unique brands
  const brands = useMemo(() => {
    if (fanpages.length > 0) {
      return fanpages.map((f) => ({ id: f.id, name: f.name }));
    }
    const map = new Map<number, string>();
    jobs.forEach((j) => {
      if (j.fanpage_id && j.fanpage_name) {
        map.set(j.fanpage_id, j.fanpage_name);
      }
    });
    return Array.from(map.entries())
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [fanpages, jobs]);

  // Unique content types
  const contentTypes = useMemo(() => {
    const set = new Set<string>();
    jobs.forEach((j) => {
      if (j.content_type) set.add(j.content_type);
    });
    return Array.from(set).sort();
  }, [jobs]);

  const pendingJobIds = useMemo(
    () => new Set(pendingActions.map((a) => a.jobId)),
    [pendingActions]
  );

  const visibleJobs = useMemo(
    () => jobs.filter((j) => !pendingJobIds.has(j.id)),
    [jobs, pendingJobIds]
  );

  // Tab counts
  const tabCounts = useMemo(() => {
    const counts = { all: 0, review: 0, design: 0, ready: 0 };
    jobs.forEach((j) => {
      if (pendingJobIds.has(j.id)) return;
      counts.all++;
      if (j.status === "pending_review") counts.review++;
      else if (j.status === "pending_design") counts.design++;
      else if (j.status === "pending_publish") counts.ready++;
    });
    return counts;
  }, [jobs, pendingJobIds]);

  // Filtered jobs
  const filteredJobs = useMemo(() => {
    return jobs.filter((j) => {
      if (pendingJobIds.has(j.id)) return false;
      if (activeTab === "review" && j.status !== "pending_review") return false;
      if (activeTab === "design" && j.status !== "pending_design") return false;
      if (activeTab === "ready" && j.status !== "pending_publish") return false;
      if (selectedBrandId !== null && j.fanpage_id !== selectedBrandId) return false;
      if (typeFilter !== "all" && j.content_type !== typeFilter) return false;
      return true;
    });
  }, [jobs, activeTab, selectedBrandId, typeFilter, pendingJobIds]);

  // Default selection
  const selectedJob = useMemo(() => {
    if (!filteredJobs.length) return null;
    if (selectedId) {
      const found = filteredJobs.find((j) => j.id === selectedId);
      if (found) return found;
    }
    return filteredJobs[0];
  }, [filteredJobs, selectedId]);

  const currentCaption = selectedJob
    ? captionDrafts[selectedJob.id] ?? selectedJob.ai_generated_caption ?? ""
    : "";

  const executePendingAction = useCallback(
    async (jobId: number) => {
      const item = pendingActionsRef.current.get(jobId);
      if (!item) return;

      clearTimeout(item.timerId);
      pendingActionsRef.current.delete(jobId);
      setPendingActions((prev) => prev.filter((a) => a.jobId !== jobId));

      try {
        if (item.action.type === "publish") {
          await publishJob(jobId);
        } else {
          await skipJob(jobId);
        }
        mutate();
      } catch (err: unknown) {
        const errorMsg =
          err && typeof err === "object" && "message" in err
            ? String((err as { message: unknown }).message)
            : "Action failed";
        setToast({
          message: `Failed to ${item.action.type} "${item.action.title}": ${errorMsg}`,
          type: "error",
        });
        mutate();
      }
    },
    [mutate]
  );

  const handleUndo = useCallback((jobId: number) => {
    const item = pendingActionsRef.current.get(jobId);
    if (!item) return;

    clearTimeout(item.timerId);
    pendingActionsRef.current.delete(jobId);
    setPendingActions((prev) => prev.filter((a) => a.jobId !== jobId));
    setSelectedId(jobId);
  }, []);

  const scheduleAction = useCallback(
    (jobId: number, type: "publish" | "skip") => {
      if (pendingActionsRef.current.has(jobId)) return;
      const targetJob = jobs.find((j) => j.id === jobId);
      if (!targetJob) return;

      const title = getJobTitle(targetJob);
      const expiresAt = Date.now() + 10000;
      const actionId = `${jobId}-${type}-${Date.now()}`;

      const action: PendingAction = {
        id: actionId,
        jobId,
        type,
        title,
        remaining: 10,
      };

      const timerId = setTimeout(() => {
        executePendingAction(jobId);
      }, 10000);

      pendingActionsRef.current.set(jobId, { action, timerId, expiresAt });
      setPendingActions((prev) => [...prev, action]);
    },
    [jobs, executePendingAction]
  );

  function handlePublish(jobId: number) {
    scheduleAction(jobId, "publish");
  }

  function handleSkip(jobId: number) {
    scheduleAction(jobId, "skip");
  }

  // Ticker for pending countdowns
  useEffect(() => {
    if (pendingActions.length === 0) return;

    const interval = setInterval(() => {
      const now = Date.now();
      setPendingActions((prev) => {
        let changed = false;
        const next = prev.map((a) => {
          const item = pendingActionsRef.current.get(a.jobId);
          if (!item) return a;
          const rem = Math.max(0, Math.ceil((item.expiresAt - now) / 1000));
          if (rem !== a.remaining) {
            changed = true;
            return { ...a, remaining: rem };
          }
          return a;
        });
        return changed ? next : prev;
      });
    }, 250);

    return () => clearInterval(interval);
  }, [pendingActions.length]);

  // Flush pending actions immediately when leaving page
  useEffect(() => {
    const flushPending = () => {
      const items = Array.from(pendingActionsRef.current.values());
      for (const item of items) {
        clearTimeout(item.timerId);
      }
      pendingActionsRef.current.clear();

      for (const item of items) {
        if (item.action.type === "publish") {
          publishJob(item.action.jobId).catch(() => {});
        } else {
          skipJob(item.action.jobId).catch(() => {});
        }
      }
    };

    const handleBeforeUnload = () => {
      flushPending();
    };

    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      flushPending();
    };
  }, []);

  async function handleSaveCaption(jobId: number) {
    const text = captionDrafts[jobId];
    if (text === undefined) return;
    setLoadingId(jobId);
    try {
      await updateJobCaption(jobId, text);
      mutate();
    } finally {
      setLoadingId(null);
    }
  }

  async function handleRerender(jobId: number) {
    setLoadingId(jobId);
    try {
      await renderJobNow(jobId);
      mutate();
    } catch {
      alert("Couldn't start the re-render. Please try again.");
    } finally {
      setLoadingId(null);
    }
  }

  async function handleRegenerate(jobId: number) {
    setLoadingId(jobId);
    try {
      await regenerateCaption(jobId);
      setCaptionDrafts((prev) => {
        const n = { ...prev };
        delete n[jobId];
        return n;
      });
      mutate();
    } finally {
      setLoadingId(null);
    }
  }

  function openLightbox(job: PublishJob, idx = 0) {
    const urls = resolveUrls(job);
    if (!urls.length) return;
    setLightbox({ job, urls, idx });
  }

  const isClip =
    selectedJob?.content_type === "youtube_clip" ||
    selectedJob?.content_type === "ai_video";
  const isNews =
    selectedJob?.content_type === "news_content" ||
    selectedJob?.content_type === "ig_recreate" ||
    selectedJob?.content_type === "discussion" ||
    selectedJob?.content_type === "pinterest_content" ||
    selectedJob?.content_type === "facebook_recreate" ||
    selectedJob?.content_type === "youtube_clip" ||
    selectedJob?.content_type === "ai_video";
  const needsDesign =
    (selectedJob?.content_type === "news_content" ||
      selectedJob?.content_type === "discussion" ||
      selectedJob?.content_type === "pinterest_content" ||
      selectedJob?.content_type === "facebook_recreate" ||
      selectedJob?.content_type === "ig_recreate") &&
    selectedJob?.status === "pending_design";
  const clipRendering = isClip && selectedJob?.status === "pending_design";
  const selectedThumb = selectedJob ? resolveUrls(selectedJob)[0] : undefined;
  const albumCount = selectedJob ? resolveUrls(selectedJob).length : 0;
  const canPublish =
    selectedJob && !needsDesign && !clipRendering;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-bold text-text tracking-tight">
            Review queue
          </h1>
          <p className="text-sm text-text2 mt-0.5">
            Check each post, fix the caption or design, then publish or skip.
            Updates every 15 s.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          disabled={isLoading}
          className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-line bg-surface text-text hover:bg-surface2 text-xs font-semibold transition-colors"
        >
          <Icon
            icon="solar:refresh-bold-duotone"
            width={15}
            className={isLoading ? "animate-spin" : ""}
          />
          Refresh
        </button>
      </div>

      {/* Tabs & Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Status Tabs */}
        <div
          role="tablist"
          aria-label="Queue state"
          className="inline-flex flex-wrap gap-1 p-1 rounded-xl bg-surface2 border border-line"
        >
          {[
            { id: "all", label: "All", count: tabCounts.all },
            { id: "review", label: "Needs review", count: tabCounts.review },
            { id: "design", label: "Needs design", count: tabCounts.design },
            { id: "ready", label: "Ready", count: tabCounts.ready },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={activeTab === t.id}
              onClick={() => setActiveTab(t.id as typeof activeTab)}
              className={`flex items-center gap-2 h-9 px-3 rounded-lg text-xs font-semibold transition-colors ${
                activeTab === t.id
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text bg-transparent"
              }`}
            >
              <span>{t.label}</span>
              <span className="min-w-[18px] px-1.5 py-0.5 rounded-full bg-surface2 text-text2 text-[11px] font-mono">
                {t.count}
              </span>
            </button>
          ))}
        </div>

        {/* Dropdowns */}
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-text2 font-medium">
            Brand
            <select
              value={selectedBrandId ?? "all"}
              onChange={(e) =>
                setSelectedBrandId(e.target.value === "all" ? null : Number(e.target.value))
              }
              className="h-9 px-2.5 rounded-lg border border-line bg-surface text-text text-xs focus:outline-none focus:border-pri"
            >
              <option value="all">All brands</option>
              {brands.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-2 text-xs text-text2 font-medium">
            Type
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="h-9 px-2.5 rounded-lg border border-line bg-surface text-text text-xs focus:outline-none focus:border-pri"
            >
              <option value="all">All types</option>
              {contentTypes.map((ct) => (
                <option key={ct} value={ct}>
                  {ct.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {/* Empty State */}
      {!isLoading && visibleJobs.length === 0 && (
        <div className="rounded-xl border border-line bg-surface flex flex-col items-center justify-center py-20 text-center p-6">
          <Icon
            icon="solar:inbox-bold-duotone"
            width={44}
            className="text-text2 opacity-40 mb-3"
          />
          <p className="text-base font-semibold text-text">Queue is clear</p>
          <p className="text-xs text-text2 mt-1 max-w-sm">
            No posts waiting for review. New posts will show up here as crawlers run.
          </p>
        </div>
      )}

      {/* Loading Skeleton */}
      {isLoading && (
        <div className="rounded-xl border border-line bg-surface p-8 text-center text-text2 text-xs animate-pulse">
          Loading review queue…
        </div>
      )}

      {/* Master-Detail Layout */}
      {!isLoading && visibleJobs.length > 0 && (
        <div className="flex flex-col lg:flex-row gap-4 items-start">
          {/* Post list */}
          <section
            aria-label="Posts"
            className="w-full lg:w-[380px] xl:w-[420px] shrink-0 bg-surface border border-line rounded-xl overflow-hidden flex flex-col"
          >
            <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-line bg-surface">
              <span className="text-xs font-semibold text-text">
                {filteredJobs.length} post{filteredJobs.length !== 1 ? "s" : ""}
              </span>
              <span className="text-[11px] text-text2 font-medium">Newest first</span>
            </div>

            <ul className="divide-y divide-line list-none m-0 p-0 overflow-y-auto max-h-[760px]">
              {filteredJobs.map((job) => {
                const isSelected = selectedJob?.id === job.id;
                const thumb = resolveUrls(job)[0];
                const createdAt = (job as unknown as Record<string, string>).created_at;
                const title =
                  job.design_title ??
                  (job.ig_username ? `@${job.ig_username}` : job.ai_generated_caption?.slice(0, 40) ?? "Post");

                return (
                  <li key={job.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(job.id)}
                      className={`w-full text-left p-3 flex gap-3 items-center transition-colors ${
                        isSelected
                          ? "bg-pri-soft text-text"
                          : "bg-surface hover:bg-surface2 text-text"
                      }`}
                    >
                      <div className="w-[50px] h-[60px] rounded-lg bg-surface2 border border-line flex-shrink-0 flex items-center justify-center overflow-hidden text-[10px] text-text2 font-mono">
                        {thumb ? (
                          <img
                            src={thumb}
                            alt=""
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <span>
                            {job.content_type === "youtube_clip" ||
                            job.content_type === "ai_video"
                              ? "9:16"
                              : "4:5"}
                          </span>
                        )}
                      </div>

                      <div className="flex-1 min-w-0 flex flex-col gap-1">
                        <div className="flex items-center justify-between gap-2 text-[11px] text-text2">
                          <span className="truncate font-medium">{job.fanpage_name}</span>
                          <span className="shrink-0">{createdAt ? timeAgo(createdAt) : ""}</span>
                        </div>
                        <p className="text-xs font-semibold text-text truncate">
                          {title}
                        </p>
                        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                          <ModeChip mode={job.content_type || "repost"} size="sm" />
                          <StatusBadge status={job.status} />
                        </div>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          {/* Selected post detail */}
          {selectedJob && (
            <section
              aria-label="Selected post"
              className="flex-1 min-w-0 w-full bg-surface border border-line rounded-xl p-5 flex flex-col gap-4"
            >
              {/* Header */}
              <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-line">
                <div className="flex items-center gap-2.5 min-w-0">
                  {selectedJob.fanpage_picture_url ? (
                    <img
                      src={selectedJob.fanpage_picture_url}
                      alt={selectedJob.fanpage_name ?? ""}
                      className="w-8 h-8 rounded-lg object-cover flex-shrink-0"
                    />
                  ) : (
                    <div
                      className="w-8 h-8 rounded-lg flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
                      style={{
                        background: avatarColor(selectedJob.fanpage_name ?? "Fanpage"),
                      }}
                    >
                      {(selectedJob.fanpage_name ?? "?")[0]?.toUpperCase()}
                    </div>
                  )}
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-text leading-tight truncate">
                      {selectedJob.fanpage_name}
                    </p>
                    <p className="text-xs text-text2 truncate">
                      {selectedJob.ig_username
                        ? `@${selectedJob.ig_username}`
                        : selectedJob.article_source_name
                        ? `From ${selectedJob.article_source_name}`
                        : "Source content"}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <ModeChip mode={selectedJob.content_type || "repost"} />
                  <StatusBadge status={selectedJob.status} />
                </div>
              </div>

              {/* Main split */}
              <div className="flex flex-col md:flex-row gap-5 items-start">
                {/* Media Preview */}
                <div className="w-full md:w-[260px] lg:w-[300px] shrink-0 flex flex-col gap-2">
                  {isClip && selectedJob.video_url ? (
                    <ClipPlayer
                      src={selectedJob.video_url}
                      poster={selectedThumb}
                      className="aspect-[9/16] rounded-xl max-h-[440px] object-contain border border-line"
                    />
                  ) : clipRendering ? (
                    <div className="aspect-[9/16] rounded-xl border border-dashed border-line bg-surface2 flex flex-col items-center justify-center p-6 text-center gap-2 text-text2">
                      <Icon
                        icon="solar:videocamera-record-bold-duotone"
                        width={32}
                        className="text-info animate-pulse"
                      />
                      <span className="text-xs font-semibold text-text">
                        Rendering the clip
                      </span>
                      <span className="text-[11px] leading-relaxed">
                        Preview shows up once the video render completes.
                      </span>
                    </div>
                  ) : needsDesign ? (
                    <div className="aspect-[4/5] rounded-xl border border-dashed border-line bg-surface2 flex flex-col items-center justify-center p-6 text-center gap-2 text-text2">
                      <Icon
                        icon="solar:palette-bold-duotone"
                        width={32}
                        className="text-warn"
                      />
                      <span className="text-xs font-semibold text-text">
                        Not designed yet
                      </span>
                      <span className="text-[11px] leading-relaxed">
                        Open the Designer to pick photos and complete the card.
                      </span>
                      <Link
                        href={`/designer/${selectedJob.id}`}
                        className="mt-1 inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 transition-opacity"
                      >
                        <Icon icon="solar:palette-bold-duotone" width={14} />
                        Open Designer
                      </Link>
                    </div>
                  ) : selectedThumb ? (
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => openLightbox(selectedJob, 0)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          openLightbox(selectedJob, 0);
                        }
                      }}
                      className="relative group cursor-zoom-in rounded-xl overflow-hidden border border-line bg-surface2"
                    >
                      <img
                        src={selectedThumb}
                        alt="Preview"
                        className="w-full aspect-[4/5] object-cover"
                      />
                      <div className="pointer-events-none absolute inset-0 bg-black/0 group-hover:bg-black/25 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
                        <div className="bg-black/60 rounded-full p-2">
                          <Icon
                            icon="solar:maximize-bold-duotone"
                            width={18}
                            className="text-white"
                          />
                        </div>
                      </div>
                      {albumCount > 1 && (
                        <div className="absolute bottom-2.5 right-2.5 flex items-center gap-1 bg-surface/90 text-text text-xs font-bold px-2 py-0.5 rounded-full shadow-sm">
                          <Icon icon="solar:gallery-bold-duotone" width={11} />
                          {albumCount}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="aspect-[4/5] rounded-xl border border-line bg-surface2 flex flex-col items-center justify-center p-4 text-text2 text-xs">
                      <Icon
                        icon="solar:gallery-bold-duotone"
                        width={32}
                        className="opacity-40 mb-1"
                      />
                      No preview available
                    </div>
                  )}

                  <div className="flex items-center justify-between text-[11px] text-text2 px-1">
                    <span>
                      {albumCount > 1
                        ? `${albumCount} photos`
                        : isClip
                        ? "Video clip"
                        : "Card preview"}
                    </span>
                    <span>
                      {isClip ? "9:16 vertical" : "4:5 card format"}
                    </span>
                  </div>
                </div>

                {/* Caption and detail fields */}
                <div className="flex-1 min-w-0 flex flex-col gap-3">
                  <div className="flex items-center justify-between text-xs font-semibold text-text">
                    <label htmlFor="caption-editor">Caption</label>
                    <span className="font-normal text-text2 text-[11px]">
                      {currentCaption.length} / 500 characters
                    </span>
                  </div>

                  <textarea
                    id="caption-editor"
                    rows={7}
                    value={currentCaption}
                    onChange={(e) =>
                      setCaptionDrafts((prev) => ({
                        ...prev,
                        [selectedJob.id]: e.target.value,
                      }))
                    }
                    placeholder="Enter or edit caption…"
                    className="w-full p-3 rounded-lg border border-line bg-surface text-text text-sm leading-relaxed focus:outline-none focus:border-pri resize-y"
                  />

                  {/* Actions under textarea */}
                  <div className="flex flex-wrap gap-2">
                    {!isNews && (
                      <button
                        type="button"
                        disabled={loadingId === selectedJob.id}
                        onClick={() => handleRegenerate(selectedJob.id)}
                        className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors"
                      >
                        <Icon icon="solar:restart-bold-duotone" width={13} />
                        Write new caption
                      </button>
                    )}

                    {captionDrafts[selectedJob.id] !== undefined && (
                      <button
                        type="button"
                        onClick={() =>
                          setCaptionDrafts((prev) => {
                            const n = { ...prev };
                            delete n[selectedJob.id];
                            return n;
                          })
                        }
                        className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text2 hover:text-text text-xs font-medium transition-colors"
                      >
                        <Icon icon="solar:close-circle-bold-duotone" width={13} />
                        Discard changes
                      </button>
                    )}

                    {!isClip && (
                      <Link
                        href={`/designer/${selectedJob.id}`}
                        className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors"
                      >
                        <Icon icon="solar:palette-bold-duotone" width={13} />
                        Open in designer
                      </Link>
                    )}

                    {isClip && (
                      <button
                        type="button"
                        disabled={loadingId === selectedJob.id}
                        onClick={() => handleRerender(selectedJob.id)}
                        className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors"
                      >
                        <Icon icon="solar:restart-bold-duotone" width={13} />
                        Re-render clip
                      </button>
                    )}
                  </div>

                  {/* Metadata list */}
                  <dl className="mt-2 grid grid-cols-[100px_1fr] gap-x-3 gap-y-2 text-xs border-t border-line pt-3">
                    <dt className="text-text2 font-medium">Goes to</dt>
                    <dd className="text-text m-0">{selectedJob.fanpage_name}</dd>

                    <dt className="text-text2 font-medium">Planned slot</dt>
                    <dd className="text-text m-0">
                      {selectedJob.scheduled_for
                        ? `Scheduled for ${selectedJob.scheduled_for}`
                        : "First available slot upon publish"}
                    </dd>

                    <dt className="text-text2 font-medium">Image engine</dt>
                    <dd className="text-text m-0">
                      {selectedJob.design_engine ??
                        (isClip ? "Video render" : "Card template")}
                    </dd>

                    {selectedJob.last_error && (
                      <>
                        <dt className="text-bad font-medium">Last error</dt>
                        <dd className="text-bad m-0 bg-bad-soft p-2 rounded-md">
                          {selectedJob.last_error}
                        </dd>
                      </>
                    )}
                  </dl>
                </div>
              </div>

              {/* Action bar */}
              <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-line mt-2">
                <button
                  type="button"
                  disabled={loadingId === selectedJob.id}
                  onClick={() => handleSkip(selectedJob.id)}
                  className="inline-flex items-center gap-1.5 h-10 px-4 rounded-lg border border-line bg-surface text-bad hover:bg-bad-soft text-sm font-semibold transition-colors"
                >
                  <Icon icon="solar:close-bold" width={14} />
                  Skip post
                </button>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={loadingId === selectedJob.id}
                    onClick={() => handleSaveCaption(selectedJob.id)}
                    className="inline-flex items-center gap-1.5 h-10 px-4 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-sm font-semibold transition-colors"
                  >
                    Save caption
                  </button>

                  {needsDesign && (
                    <Link
                      href={`/designer/${selectedJob.id}`}
                      className="inline-flex items-center gap-1.5 h-10 px-5 rounded-lg bg-pri text-pri-fg text-sm font-semibold hover:opacity-90 transition-opacity"
                    >
                      <Icon icon="solar:palette-bold-duotone" width={15} />
                      Open in designer
                    </Link>
                  )}

                  {clipRendering && (
                    <button
                      type="button"
                      disabled
                      className="inline-flex items-center gap-1.5 h-10 px-5 rounded-lg bg-surface2 text-text2 text-sm font-semibold cursor-not-allowed border border-line"
                    >
                      <Icon
                        icon="solar:videocamera-record-bold-duotone"
                        width={15}
                      />
                      Rendering…
                    </button>
                  )}

                  {canPublish && (
                    <button
                      type="button"
                      disabled={loadingId === selectedJob.id}
                      onClick={() => handlePublish(selectedJob.id)}
                      className="inline-flex items-center gap-1.5 h-10 px-5 rounded-lg bg-pri text-pri-fg text-sm font-semibold hover:opacity-90 transition-opacity"
                    >
                      <Icon icon="solar:verified-check-bold-duotone" width={15} />
                      {loadingId === selectedJob.id ? "Publishing…" : "Publish"}
                    </button>
                  )}
                </div>
              </div>
            </section>
          )}
        </div>
      )}

      {/* Lightbox */}
      {lightbox && (
        <Lightbox
          state={lightbox}
          onClose={() => setLightbox(null)}
          onPrev={() =>
            setLightbox((l) =>
              l && l.idx > 0 ? { ...l, idx: l.idx - 1 } : l
            )
          }
          onNext={() =>
            setLightbox((l) =>
              l && l.idx < l.urls.length - 1 ? { ...l, idx: l.idx + 1 } : l
            )
          }
          onPublish={() => {
            handlePublish(lightbox.job.id);
            setLightbox(null);
          }}
          onSkip={() => {
            handleSkip(lightbox.job.id);
            setLightbox(null);
          }}
          loading={loadingId === lightbox.job.id}
        />
      )}

      {/* Undo toasts and error notifications */}
      <UndoToastStack actions={pendingActions} onUndo={handleUndo} />
      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}

/* Lightbox Modal */
function Lightbox({
  state,
  onClose,
  onPrev,
  onNext,
  onPublish,
  onSkip,
  loading,
}: {
  state: LightboxState;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onPublish: () => void;
  onSkip: () => void;
  loading: boolean;
}) {
  const { job, urls, idx } = state;
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const caption = job.ai_generated_caption ?? "";
  const isClip =
    job.content_type === "youtube_clip" || job.content_type === "ai_video";
  const total = urls.length;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 backdrop-blur-md p-4"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl rounded-2xl overflow-hidden shadow-2xl bg-surface border border-line"
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className={`relative bg-black ${isClip ? "aspect-[9/16]" : "aspect-[4/3]"}`}
        >
          {isClip ? (
            <ClipPlayer
              src={job.video_url}
              poster={urls[0]}
              className="absolute inset-0 w-full h-full object-contain"
            />
          ) : (
            <img
              src={urls[idx]}
              alt={`Image ${idx + 1}`}
              className="absolute inset-0 w-full h-full object-contain"
            />
          )}

          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10"
          >
            <Icon icon="solar:close-bold" width={16} />
          </button>

          {!isClip && total > 1 && idx > 0 && (
            <button
              type="button"
              onClick={onPrev}
              aria-label="Previous image"
              className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10"
            >
              <Icon icon="solar:alt-arrow-left-bold-duotone" width={18} />
            </button>
          )}
          {!isClip && total > 1 && idx < total - 1 && (
            <button
              type="button"
              onClick={onNext}
              aria-label="Next image"
              className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors z-10"
            >
              <Icon icon="solar:alt-arrow-right-bold-duotone" width={18} />
            </button>
          )}

          {!isClip && total > 1 && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-black/60 text-white text-xs font-semibold px-3 py-1 rounded-full z-10">
              {idx + 1} / {total}
            </div>
          )}

          {!isClip && (
            <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-14 pb-4 px-5">
              <p className="text-white text-xs font-semibold">{fanpage}</p>
              {caption && (
                <p className="text-white/80 text-xs mt-1 line-clamp-3 leading-relaxed whitespace-pre-line">
                  {caption}
                </p>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 px-5 py-3.5 bg-surface border-t border-line">
          <button
            type="button"
            onClick={onSkip}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-bad bg-bad-soft rounded-lg hover:opacity-90 transition-opacity"
          >
            <Icon icon="solar:close-bold" width={13} />
            Skip
          </button>
          <button
            type="button"
            onClick={onPublish}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-5 py-2 text-xs font-semibold bg-pri text-pri-fg rounded-lg hover:opacity-90 transition-opacity"
          >
            <Icon icon="solar:verified-check-bold-duotone" width={15} />
            {loading ? "Publishing…" : "Publish"}
          </button>
        </div>
      </div>
    </div>
  );
}
