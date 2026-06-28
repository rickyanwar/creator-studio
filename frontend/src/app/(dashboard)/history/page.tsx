"use client";

import { useState } from "react";
import useSWR from "swr";
import { listJobs } from "@/lib/api";
import type { PublishJob, PublishJobStatus } from "@/lib/types";
import { format } from "date-fns";

const parseUtc = (s: string) =>
  new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
import { Icon } from "@iconify/react";

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
  const pub = job.image_public_urls ?? [];
  const src = (job as unknown as Record<string, string[]>).image_source_urls ?? [];
  if (pub.length && pub[0].includes("localhost") && src.length) return src;
  return pub;
}

const fetcher = (status: string) =>
  listJobs({ status, limit: 100 }).then((r) => r.data as PublishJob[]);

export default function HistoryPage() {
  const [activeStatus, setActiveStatus] = useState<PublishJobStatus>("published");
  const [lightboxJob, setLightboxJob] = useState<{ job: PublishJob; urls: string[]; idx: number } | null>(null);
  const [blurred, setBlurred] = useState(false);

  const { data: jobs = [], isLoading } = useSWR(
    `history-${activeStatus}`,
    () => fetcher(activeStatus),
    { refreshInterval: 60000 }
  );

  return (
    <>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-text-primary">Publish History</h1>
            <p className="text-sm text-text-secondary mt-0.5">
              {isLoading ? "Loading…" : `${jobs.length} ${activeStatus} post${jobs.length !== 1 ? "s" : ""}`}
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

        {/* Status tabs */}
        <div className="flex items-center gap-2">
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
              />
            ))}
          </div>
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
        />
      )}
    </>
  );
}

/* ── History card ─────────────────────────────────── */
function HistoryCard({
  job,
  blurred,
  onImageClick,
}: {
  job: PublishJob;
  blurred: boolean;
  onImageClick: (idx: number) => void;
}) {
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const color = avatarColor(fanpage);
  const urls = resolveUrls(job);
  const thumb = urls[0];
  const albumCount = urls.length;
  const caption = job.ai_generated_caption ?? "";
  const cfg = STATUS_CONFIG[job.status] ?? STATUS_CONFIG.skipped;

  const publishedDate = job.published_at ?? (job as unknown as Record<string, string>).updated_at;

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
            {publishedDate
              ? format(parseUtc(publishedDate), "MMM d, yyyy · HH:mm")
              : `@${job.ig_username}`}
          </p>
        </div>

        {/* Status badge */}
        <span className={`${cfg.badge} flex-shrink-0 flex items-center gap-1`}>
          <Icon icon={cfg.icon} width={11} />
          {job.status}
        </span>
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
        {job.repliz_schedule_id && (
          <span className="flex items-center gap-1 text-xs text-text-secondary ml-auto">
            <Icon icon="solar:link-bold-duotone" width={11} />
            {job.repliz_schedule_id.slice(-8)}
          </span>
        )}
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
          <p className={`text-xs text-text-secondary leading-relaxed line-clamp-3 ${blurred ? blur : ""}`}>{caption}</p>
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
}: {
  state: { job: PublishJob; urls: string[]; idx: number };
  blurred: boolean;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
}) {
  const { job, urls, idx } = state;
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const color = avatarColor(fanpage);
  const caption = job.ai_generated_caption ?? "";
  const total = urls.length;
  const cfg = STATUS_CONFIG[job.status] ?? STATUS_CONFIG.skipped;
  const publishedDate = job.published_at ?? (job as unknown as Record<string, string>).updated_at;

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
                  {publishedDate ? ` · ${format(parseUtc(publishedDate), "MMM d, yyyy HH:mm")}` : ""}
                </p>
              </div>
              <span className={`${cfg.badge} flex items-center gap-1 flex-shrink-0`}>
                <Icon icon={cfg.icon} width={11} />
                {job.status}
              </span>
            </div>

            {job.last_error ? (
              <p className="text-error-light text-xs leading-relaxed line-clamp-3">{job.last_error}</p>
            ) : caption ? (
              <p className={`text-white/90 text-xs leading-relaxed line-clamp-4 ${blurred ? blur : ""}`}>{caption}</p>
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

        {/* Info bar — no actions, read-only */}
        <div className="flex items-center gap-3 px-5 py-4 bg-bg-paper">
          {job.repliz_schedule_id && (
            <div className="flex items-center gap-1.5 text-xs text-text-secondary">
              <Icon icon="solar:link-bold-duotone" width={13} />
              Repliz ID: <span className={`font-mono text-text-primary ${blurred ? blur : ""}`}>{job.repliz_schedule_id.slice(-12)}</span>
            </div>
          )}
          <div className="ml-auto flex items-center gap-1.5 text-xs" style={{ color: cfg.iconClass === "text-primary-main" ? "#00A76F" : cfg.iconClass === "text-error-main" ? "#FF5630" : "#637381" }}>
            <Icon icon={cfg.icon} width={14} />
            <span className="font-semibold capitalize">{job.status}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
