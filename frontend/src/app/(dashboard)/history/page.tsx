"use client";

import { useState, useRef, useEffect } from "react";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import { listJobs, listFanpages, deletePublishJob, reeditJob } from "@/lib/api";
import type { PublishJob, PublishJobStatus } from "@/lib/types";
import { format } from "date-fns";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import Toast, { type ToastData } from "@/components/ui/Toast";

const parseUtc = (s: string) =>
  new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
import { Icon } from "@iconify/react";

type FanpageLite = { id: number; name: string };

// A job is marked `published` as soon as we hand it to Repliz, but the fanpage's
// sleep-window / daily-cap pacing (see publisher._next_schedule_at) can push the
// real Facebook go-live time (scheduled_for) well after that. Surface both so
// "published" in the UI doesn't read as "already live on Facebook".
function jobTimes(job: PublishJob) {
  const queuedRaw = job.published_at ?? (job as unknown as Record<string, string>).updated_at;
  const queuedDate = queuedRaw ? parseUtc(queuedRaw) : null;
  const scheduledDate = job.scheduled_for ? parseUtc(job.scheduled_for) : null;
  const isPendingLive = !!scheduledDate && scheduledDate.getTime() > Date.now() + 60_000;
  const differs =
    !!scheduledDate && !!queuedDate && Math.abs(scheduledDate.getTime() - queuedDate.getTime()) > 120_000;
  return { queuedDate, scheduledDate, isPendingLive, differs };
}

const STATUSES: { value: PublishJobStatus; label: string; icon: string }[] = [
  { value: "published", label: "Published", icon: "solar:verified-check-bold-duotone" },
  { value: "failed",    label: "Failed",    icon: "solar:close-circle-bold-duotone" },
  { value: "skipped",   label: "Skipped",   icon: "solar:skip-next-bold-duotone" },
];

const STATUS_CONFIG: Record<string, { badge: string; icon: string; iconClass: string }> = {
  published: { badge: "badge-green", icon: "solar:verified-check-bold-duotone", iconClass: "text-primary-main" },
  failed:    { badge: "badge-red",   icon: "solar:close-circle-bold-duotone",   iconClass: "text-error-main" },
  skipped:   { badge: "badge-gray",  icon: "solar:skip-next-bold-duotone",      iconClass: "text-text-disabled" },
};

const AVATAR_COLORS = [
  "#00A76F", "#8E33FF", "#FFAB00", "#FF5630",
  "#00B8D9", "#5119B7", "#B76E00", "#B71D18",
];
function avatarColor(name: string) {
  let n = 0;
  for (let i = 0; i < name.length; i++) n += name.charCodeAt(i);
  return AVATAR_COLORS[n % AVATAR_COLORS.length];
}

function resolveUrls(job: PublishJob): string[] {
  if (job.content_type === "news_content" || job.content_type === "ig_recreate") {
    return job.design_image_url ? [job.design_image_url] : [];
  }
  const pub = job.image_public_urls ?? [];
  const src = (job as unknown as Record<string, string[]>).image_source_urls ?? [];
  if (pub.length && pub[0].includes("localhost") && src.length) return src;
  return pub;
}

// Link back to where the content originally came from — a scraped article for
// news_content jobs, or the original Instagram post/profile for ig_repost/ig_recreate.
function sourceLink(job: PublishJob): { url: string; label: string; icon: string } | null {
  if (job.article_url) {
    return {
      url: job.article_url,
      label: job.article_source_name ? `Source: ${job.article_source_name}` : "Source article",
      icon: "solar:global-bold-duotone",
    };
  }
  if (job.ig_post_url) {
    return { url: job.ig_post_url, label: "Original IG post", icon: "mdi:instagram" };
  }
  if (job.ig_username) {
    return { url: `https://instagram.com/${job.ig_username}`, label: `@${job.ig_username}`, icon: "mdi:instagram" };
  }
  return null;
}

const PAGE_SIZE = 30;

export default function HistoryPage() {
  const [activeStatus, setActiveStatus] = useState<PublishJobStatus>("published");
  const [fanpageFilter, setFanpageFilter] = useState<string>("");
  const [lightboxJob, setLightboxJob] = useState<{ job: PublishJob; urls: string[]; idx: number } | null>(null);
  const [blurred, setBlurred] = useState(false);

  const { data: fanpages = [] } = useSWR<FanpageLite[]>(
    "history-fanpages",
    () => listFanpages().then((r) => r.data as FanpageLite[])
  );

  // ── Infinite scroll (offset pagination) — mirrors the Gallery page so History
  // doesn't dump hundreds of post cards into the DOM at once ──
  const {
    data: pages,
    size,
    setSize,
    mutate: mutateJobs,
    isValidating,
  } = useSWRInfinite<PublishJob[]>(
    (index, prev: PublishJob[] | null) => {
      if (prev && prev.length < PAGE_SIZE) return null; // no more pages
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

  const jobs = pages ? pages.flat() : [];
  const lastPage = pages?.[pages.length - 1];
  const reachedEnd = !!lastPage && lastPage.length < PAGE_SIZE;
  const loadingMore = isValidating;
  const isLoading = !pages;

  // Reset to first page whenever the status/fanpage filter changes
  useEffect(() => {
    setSize(1);
  }, [activeStatus, fanpageFilter, setSize]);

  // Load next page when the sentinel scrolls into view
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || reachedEnd) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !loadingMore) setSize((s) => s + 1);
      },
      { rootMargin: "600px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [reachedEnd, loadingMore, setSize]);

  const [confirmDeleteJob, setConfirmDeleteJob] = useState<PublishJob | null>(null);
  const [deletingJob, setDeletingJob] = useState(false);
  const [confirmReeditJob, setConfirmReeditJob] = useState<PublishJob | null>(null);
  const [reeditingJob, setReeditingJob] = useState(false);
  const [toast, setToast] = useState<ToastData | null>(null);
  const notify = (message: string, type?: ToastData["type"]) => setToast({ message, type });

  async function confirmDeleteJobAction() {
    if (!confirmDeleteJob) return;
    setDeletingJob(true);
    try {
      const res = await deletePublishJob(confirmDeleteJob.id);
      const { repliz_deleted, repliz_error } = res.data;
      if (repliz_deleted === true) {
        notify("Deleted from history and removed from Facebook via Repliz.", "success");
      } else if (repliz_deleted === false) {
        notify(`Deleted from history, but Repliz could not remove the live post: ${repliz_error ?? "unknown error"}`, "error");
      } else {
        notify("Deleted from history.", "success");
      }
      if (lightboxJob?.job.id === confirmDeleteJob.id) setLightboxJob(null);
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
      if (lightboxJob?.job.id === confirmReeditJob.id) setLightboxJob(null);
      mutateJobs();
    } catch {
      notify("Re-edit failed. Please try again.", "error");
    } finally {
      setReeditingJob(false);
      setConfirmReeditJob(null);
    }
  }

  return (
    <>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-text-primary">Publish History</h1>
            <p className="text-sm text-text-secondary mt-0.5">
              {isLoading
                ? "Loading…"
                : `${jobs.length}${reachedEnd ? "" : "+"} ${activeStatus} post${jobs.length !== 1 ? "s" : ""}`}
            </p>
          </div>

          {/* Privacy toggle */}
          <button
            onClick={() => setBlurred((b) => !b)}
            title={blurred ? "Show content" : "Hide content (demo mode)"}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all flex-shrink-0 ${
              blurred
                ? "bg-warning-main/15 text-warning-dark border border-warning-main/30"
                : "bg-bg-paper-hover text-text-secondary hover:text-text-primary"
            }`}
          >
            <Icon
              icon={blurred ? "solar:eye-closed-bold-duotone" : "solar:eye-bold-duotone"}
              width={17}
            />
            <span className="hidden sm:inline">{blurred ? "Demo Mode" : "Privacy"}</span>
          </button>
        </div>

        {/* Status tabs + fanpage filter */}
        <div className="flex items-center gap-2 flex-wrap">
          {STATUSES.map(({ value, label, icon }) => (
            <button
              key={value}
              onClick={() => setActiveStatus(value)}
              className={
                activeStatus === value
                  ? "flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-md bg-primary-main text-white shadow-primary-btn"
                  : "flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-md bg-bg-paper-hover text-text-secondary hover:text-text-primary transition-colors"
              }
            >
              <Icon icon={icon} width={15} />
              {label}
            </button>
          ))}

          <select
            value={fanpageFilter}
            onChange={(e) => setFanpageFilter(e.target.value)}
            className="input-rect py-2 text-sm ml-auto w-48"
          >
            <option value="">All fanpages</option>
            {fanpages.map((f) => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))}
          </select>
        </div>

        {/* Skeleton */}
        {isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {[1, 2, 3].map((i) => (
              <div key={i} className="bg-bg-paper rounded-xl overflow-hidden animate-pulse">
                <div className="p-4 flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-bg-paper-hover" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-3.5 bg-bg-paper-hover rounded w-2/3" />
                    <div className="h-3 bg-bg-paper-hover rounded w-1/2" />
                  </div>
                </div>
                <div className="mx-4 aspect-[4/3] rounded-lg bg-bg-paper-hover" />
                <div className="p-4 space-y-2">
                  <div className="h-3 bg-bg-paper-hover rounded w-full" />
                  <div className="h-3 bg-bg-paper-hover rounded w-3/4" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Empty */}
        {!isLoading && jobs.length === 0 && (
          <div className="card flex flex-col items-center justify-center py-20 text-center">
            <Icon
              icon={STATUS_CONFIG[activeStatus]?.icon ?? "solar:history-bold-duotone"}
              width={48}
              className="text-text-disabled mb-4"
            />
            <p className="text-base font-semibold text-text-primary">No {activeStatus} posts yet</p>
            <p className="text-sm text-text-secondary mt-1">
              {activeStatus === "published"
                ? "Posts you publish will appear here."
                : activeStatus === "failed"
                ? "Failed publishes will be listed here."
                : "Posts you skip will appear here."}
            </p>
          </div>
        )}

        {/* Card grid */}
        {!isLoading && jobs.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {jobs.map((job) => (
              <HistoryCard
                key={job.id}
                job={job}
                blurred={blurred}
                onImageClick={(idx) => {
                  const urls = resolveUrls(job);
                  if (urls.length) setLightboxJob({ job, urls, idx });
                }}
                onDelete={() => setConfirmDeleteJob(job)}
                onReedit={() => setConfirmReeditJob(job)}
              />
            ))}
          </div>
        )}

        {/* Infinite-scroll sentinel + status */}
        {!isLoading && jobs.length > 0 && (
          <>
            <div ref={sentinelRef} className="h-10" />
            <div className="text-center py-2 text-xs text-text-secondary">
              {loadingMore ? (
                <span className="inline-flex items-center gap-2">
                  <Icon icon="svg-spinners:180-ring" width={16} /> Loading more…
                </span>
              ) : reachedEnd ? (
                `All ${jobs.length} post${jobs.length !== 1 ? "s" : ""} loaded`
              ) : null}
            </div>
          </>
        )}
      </div>

      {/* Lightbox */}
      {lightboxJob && (
        <HistoryLightbox
          state={lightboxJob}
          blurred={blurred}
          onClose={() => setLightboxJob(null)}
          onPrev={() => setLightboxJob((l) => l && l.idx > 0 ? { ...l, idx: l.idx - 1 } : l)}
          onNext={() => setLightboxJob((l) => l && l.idx < l.urls.length - 1 ? { ...l, idx: l.idx + 1 } : l)}
          onDelete={() => setConfirmDeleteJob(lightboxJob.job)}
          onReedit={() => setConfirmReeditJob(lightboxJob.job)}
        />
      )}

      <ConfirmDialog
        open={!!confirmDeleteJob}
        title="Delete this from history?"
        message={
          confirmDeleteJob?.repliz_schedule_id
            ? "This will also try to delete the live post from Facebook via Repliz. This cannot be undone."
            : "This cannot be undone."
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
        message="This removes the post from Repliz (whether it's live or still waiting in its scheduled slot) and sends it back for a fresh design with a different photo. It won't go out again until it's re-published."
        confirmLabel="Re-edit"
        loading={reeditingJob}
        onConfirm={confirmReeditJobAction}
        onCancel={() => setConfirmReeditJob(null)}
      />
      <Toast toast={toast} onClose={() => setToast(null)} />
    </>
  );
}

/* ── History card ─────────────────────────────────── */
function HistoryCard({
  job,
  blurred,
  onImageClick,
  onDelete,
  onReedit,
}: {
  job: PublishJob;
  blurred: boolean;
  onImageClick: (idx: number) => void;
  onDelete: () => void;
  onReedit: () => void;
}) {
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const color = avatarColor(fanpage);
  const urls = resolveUrls(job);
  const thumb = urls[0];
  const albumCount = urls.length;
  const caption = job.ai_generated_caption ?? "";
  const cfg = STATUS_CONFIG[job.status] ?? STATUS_CONFIG.skipped;
  const src = sourceLink(job);
  const canReedit = job.content_type === "news_content" && job.status === "published";

  const { queuedDate, scheduledDate, isPendingLive, differs } = jobTimes(job);

  const blur = "blur-sm select-none transition-all duration-200";
  const blurImg = "blur-md transition-all duration-200";

  return (
    <div className="bg-bg-paper rounded-xl overflow-hidden flex flex-col dark:shadow-card">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 pb-2">
        {job.fanpage_picture_url ? (
          <img
            src={job.fanpage_picture_url}
            alt={fanpage}
            className={`w-10 h-10 rounded-full object-cover flex-shrink-0 ${blurred ? blurImg : ""}`}
          />
        ) : (
          <div
            className={`w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0 select-none ${blurred ? blurImg : ""}`}
            style={{ background: color }}
          >
            {fanpage[0]?.toUpperCase() ?? "?"}
          </div>
        )}

        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold text-text-primary truncate ${blurred ? blur : ""}`}>{fanpage}</p>
          <p className={`text-xs text-text-secondary truncate ${blurred ? blur : ""}`}>
            {queuedDate
              ? format(queuedDate, "MMM d, yyyy · HH:mm")
              : `@${job.ig_username}`}
          </p>
          {differs && scheduledDate && (
            <p className={`text-[11px] truncate flex items-center gap-1 mt-0.5 ${isPendingLive ? "text-warning-dark" : "text-text-disabled"} ${blurred ? blur : ""}`}>
              <Icon icon="solar:clock-circle-bold-duotone" width={11} />
              {isPendingLive ? "Goes live" : "Went live"} {format(scheduledDate, "MMM d, HH:mm")}
            </p>
          )}
        </div>

        {/* Status badge — one at a time: "Scheduled" while still pending the
            real Facebook go-live time, else the actual job status */}
        {job.status === "published" && isPendingLive ? (
          <span className="badge-yellow flex-shrink-0 flex items-center gap-1" title="Sent to Repliz, but Facebook hasn't published it yet">
            <Icon icon="solar:clock-circle-bold-duotone" width={11} />
            Scheduled
          </span>
        ) : (
          <span className={`${cfg.badge} flex-shrink-0 flex items-center gap-1`}>
            <Icon icon={cfg.icon} width={11} />
            {job.status}
          </span>
        )}
      </div>

      {/* Meta */}
      <div className="flex items-center gap-3 px-4 pb-3">
        <span className={`flex items-center gap-1 text-xs text-text-secondary ${blurred ? blur : ""}`}>
          <Icon icon="solar:user-bold-duotone" width={12} />
          @{job.ig_username}
        </span>
        {albumCount > 1 && (
          <span className="flex items-center gap-1 text-xs text-text-secondary">
            <Icon icon="solar:gallery-bold-duotone" width={12} />
            {albumCount} photos
          </span>
        )}
        {src && !blurred && (
          <a
            href={src.url}
            target="_blank"
            rel="noopener noreferrer"
            title={src.url}
            onClick={(e) => e.stopPropagation()}
            className="flex items-center gap-1 text-xs text-primary-main hover:underline truncate max-w-[45%]"
          >
            <Icon icon={src.icon} width={12} className="flex-shrink-0" />
            <span className="truncate">{src.label}</span>
          </a>
        )}
        <div className="ml-auto flex items-center gap-2">
          {job.repliz_schedule_id && (
            <span className="flex items-center gap-1 text-xs text-text-secondary">
              <Icon icon="solar:link-bold-duotone" width={11} />
              {job.repliz_schedule_id.slice(-8)}
            </span>
          )}
          {canReedit && (
            <button
              onClick={onReedit}
              title="Re-edit with a new image"
              className="p-1 rounded-md text-text-disabled hover:text-primary-main hover:bg-primary-main/10 transition-colors"
            >
              <Icon icon="solar:gallery-edit-bold-duotone" width={14} />
            </button>
          )}
          <button
            onClick={onDelete}
            title="Delete from history"
            className="p-1 rounded-md text-text-disabled hover:text-error-main hover:bg-error-main/10 transition-colors"
          >
            <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
          </button>
        </div>
      </div>

      {/* Thumbnail */}
      <div
        className="relative mx-4 rounded-lg overflow-hidden bg-bg-paper-hover cursor-zoom-in group"
        onClick={() => onImageClick(0)}
      >
        {thumb ? (
          <img src={thumb} alt="Post" className={`w-full aspect-[4/3] object-cover ${blurred ? blurImg : ""}`} />
        ) : (
          <div className="w-full aspect-[4/3] flex items-center justify-center">
            <Icon icon="solar:gallery-bold-duotone" width={40} className="text-text-disabled" />
          </div>
        )}

        {albumCount > 1 && (
          <div className="absolute bottom-2.5 right-2.5 flex items-center gap-1 bg-white/90 backdrop-blur-sm text-[#1C252E] text-xs font-bold px-2.5 py-1 rounded-full">
            <Icon icon="solar:gallery-bold-duotone" width={11} />
            {albumCount}
          </div>
        )}

        {/* Status overlay tint for failed */}
        {job.status === "failed" && (
          <div className="absolute inset-0 bg-error-main/10 flex items-center justify-center">
            <div className="bg-black/60 rounded-full p-2">
              <Icon icon="solar:close-circle-bold-duotone" width={24} className="text-error-light" />
            </div>
          </div>
        )}

        {/* Hover zoom hint */}
        {job.status !== "failed" && thumb && (
          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
            <div className="bg-black/60 rounded-full p-2">
              <Icon icon="solar:maximize-bold-duotone" width={18} className="text-white" />
            </div>
          </div>
        )}
      </div>

      {/* Caption */}
      <div className="px-4 pt-3 pb-4 flex-1">
        {job.last_error ? (
          <p className="text-xs text-error-main bg-[rgba(255,86,48,0.08)] px-3 py-2 rounded-md leading-relaxed">
            {job.last_error}
          </p>
        ) : caption ? (
          <p className={`text-xs text-text-secondary leading-relaxed line-clamp-3 whitespace-pre-line ${blurred ? blur : ""}`}>{caption}</p>
        ) : (
          <p className="text-xs text-text-disabled italic">No caption</p>
        )}
      </div>
    </div>
  );
}

/* ── History lightbox (view-only, no actions) ─────── */
function HistoryLightbox({
  state,
  blurred,
  onClose,
  onPrev,
  onNext,
  onDelete,
  onReedit,
}: {
  state: { job: PublishJob; urls: string[]; idx: number };
  blurred: boolean;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onDelete: () => void;
  onReedit: () => void;
}) {
  const { job, urls, idx } = state;
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const color = avatarColor(fanpage);
  const caption = job.ai_generated_caption ?? "";
  const total = urls.length;
  const cfg = STATUS_CONFIG[job.status] ?? STATUS_CONFIG.skipped;
  const { queuedDate, scheduledDate, isPendingLive, differs } = jobTimes(job);
  const src = sourceLink(job);
  const showScheduled = job.status === "published" && isPendingLive;
  const canReedit = job.content_type === "news_content" && job.status === "published";

  const blur = "blur-sm select-none transition-all duration-200";
  const blurImg = "blur-xl transition-all duration-200";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 backdrop-blur-md p-4"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl rounded-2xl overflow-hidden shadow-dropdown"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Image — fixed 4:3 */}
        <div className="relative bg-black aspect-[4/3]">
          <img
            src={urls[idx]}
            alt={`Image ${idx + 1}`}
            className={`absolute inset-0 w-full h-full object-contain ${blurred ? blurImg : ""}`}
          />

          {/* Close */}
          <button
            onClick={onClose}
            className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors"
          >
            <Icon icon="solar:close-bold" width={16} />
          </button>

          {/* Prev / Next */}
          {total > 1 && idx > 0 && (
            <button onClick={onPrev}
              className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors">
              <Icon icon="solar:alt-arrow-left-bold-duotone" width={18} />
            </button>
          )}
          {total > 1 && idx < total - 1 && (
            <button onClick={onNext}
              className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors">
              <Icon icon="solar:alt-arrow-right-bold-duotone" width={18} />
            </button>
          )}

          {/* Counter */}
          {total > 1 && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-black/60 text-white text-xs font-semibold px-3 py-1 rounded-full">
              {idx + 1} / {total}
            </div>
          )}

          {/* Caption overlay */}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-16 pb-4 px-5">
            <div className="flex items-center gap-2.5 mb-2">
              {job.fanpage_picture_url ? (
                <img src={job.fanpage_picture_url} alt={fanpage}
                  className={`w-7 h-7 rounded-full object-cover flex-shrink-0 ${blurred ? blurImg : ""}`} />
              ) : (
                <div className={`w-7 h-7 rounded-full flex items-center justify-center text-white font-bold text-xs flex-shrink-0 ${blurred ? "blur-sm" : ""}`}
                  style={{ background: color }}>
                  {fanpage[0]?.toUpperCase()}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <p className={`text-white text-xs font-semibold leading-none ${blurred ? blur : ""}`}>{fanpage}</p>
                <p className={`text-white/60 text-[10px] mt-0.5 ${blurred ? blur : ""}`}>
                  @{job.ig_username}
                  {queuedDate ? ` · ${format(queuedDate, "MMM d, yyyy HH:mm")}` : ""}
                </p>
                {differs && scheduledDate && (
                  <p className={`text-[10px] mt-0.5 flex items-center gap-1 ${isPendingLive ? "text-warning-light" : "text-white/40"} ${blurred ? blur : ""}`}>
                    <Icon icon="solar:clock-circle-bold-duotone" width={10} />
                    {isPendingLive ? "Goes live" : "Went live"} {format(scheduledDate, "MMM d, yyyy HH:mm")}
                  </p>
                )}
              </div>
              {showScheduled ? (
                <span className="badge-yellow flex items-center gap-1 flex-shrink-0">
                  <Icon icon="solar:clock-circle-bold-duotone" width={11} />
                  Scheduled
                </span>
              ) : (
                <span className={`${cfg.badge} flex items-center gap-1 flex-shrink-0`}>
                  <Icon icon={cfg.icon} width={11} />
                  {job.status}
                </span>
              )}
            </div>

            {job.last_error ? (
              <p className="text-error-light text-xs leading-relaxed line-clamp-3">{job.last_error}</p>
            ) : caption ? (
              <p className={`text-white/90 text-xs leading-relaxed line-clamp-4 whitespace-pre-line ${blurred ? blur : ""}`}>{caption}</p>
            ) : (
              <p className="text-white/40 text-xs italic">No caption</p>
            )}
          </div>
        </div>

        {/* Dot indicators */}
        {total > 1 && (
          <div className="flex justify-center gap-1.5 py-2.5 bg-black">
            {urls.map((_, i) => (
              <div key={i} className={`h-1.5 rounded-full transition-all ${i === idx ? "bg-primary-main w-4" : "bg-white/30 w-1.5"}`} />
            ))}
          </div>
        )}

        {/* Info bar */}
        <div className="flex items-center gap-3 px-5 py-4 bg-bg-paper">
          {src && !blurred && (
            <a
              href={src.url}
              target="_blank"
              rel="noopener noreferrer"
              title={src.url}
              className="flex items-center gap-1.5 text-xs text-primary-main hover:underline truncate max-w-[45%]"
            >
              <Icon icon={src.icon} width={13} className="flex-shrink-0" />
              <span className="truncate">{src.label}</span>
            </a>
          )}
          {job.repliz_schedule_id && (
            <div className="flex items-center gap-1.5 text-xs text-text-secondary">
              <Icon icon="solar:link-bold-duotone" width={13} />
              Repliz ID: <span className={`font-mono text-text-primary ${blurred ? blur : ""}`}>{job.repliz_schedule_id.slice(-12)}</span>
            </div>
          )}
          {canReedit && (
            <button
              onClick={onReedit}
              title="Re-edit with a new image"
              className="p-1.5 rounded-md text-text-disabled hover:text-primary-main hover:bg-primary-main/10 transition-colors"
            >
              <Icon icon="solar:gallery-edit-bold-duotone" width={14} />
            </button>
          )}
          <button
            onClick={onDelete}
            title="Delete from history"
            className="p-1.5 rounded-md text-text-disabled hover:text-error-main hover:bg-error-main/10 transition-colors"
          >
            <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
          </button>
          <div
            className="ml-auto flex items-center gap-1.5 text-xs"
            style={{ color: showScheduled ? "#B76E00" : cfg.iconClass === "text-primary-main" ? "#00A76F" : cfg.iconClass === "text-error-main" ? "#FF5630" : "#637381" }}
          >
            <Icon icon={showScheduled ? "solar:clock-circle-bold-duotone" : cfg.icon} width={14} />
            <span className="font-semibold capitalize">{showScheduled ? "Scheduled" : job.status}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
