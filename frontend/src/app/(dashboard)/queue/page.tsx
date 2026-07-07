"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import useSWR from "swr";
import { listJobs, publishJob, skipJob, updateJobCaption, regenerateCaption } from "@/lib/api";
import type { PublishJob } from "@/lib/types";
import { Icon } from "@iconify/react";

/* Review queue = Mode 1 jobs awaiting caption approval + Mode 2 news jobs
   awaiting design (pending_design) or publish approval (pending_publish). */
const fetcher = async () => {
  const [review, design, publish] = await Promise.all([
    listJobs({ status: "pending_review", limit: 100 }),
    listJobs({ status: "pending_design", limit: 100 }),
    listJobs({ status: "pending_publish", limit: 100 }),
  ]);
  const news = [...(design.data as PublishJob[]), ...(publish.data as PublishJob[])]
    .filter((j) => j.content_type === "news_content");
  return [...(review.data as PublishJob[]), ...news].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );
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

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/* Use source URL when public URL is localhost */
function resolveUrls(job: PublishJob): string[] {
  if (job.content_type === "news_content") {
    return job.design_image_url ? [job.design_image_url] : [];
  }
  const pub = job.image_public_urls ?? [];
  const src = (job as unknown as Record<string, string[]>).image_source_urls ?? [];
  if (pub.length && pub[0].includes("localhost") && src.length) return src;
  return pub;
}

/* ── Lightbox state type ── */
interface LightboxState {
  job: PublishJob;
  urls: string[];
  idx: number;
}

export default function QueuePage() {
  const { data: jobs = [], isLoading, mutate } = useSWR("queue", fetcher, {
    refreshInterval: 15000,
  });

  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [editing, setEditing]   = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);

  /* Keyboard nav for lightbox */
  const handleKey = useCallback((e: KeyboardEvent) => {
    if (!lightbox) return;
    if (e.key === "Escape") { setLightbox(null); return; }
    if (e.key === "ArrowRight") setLightbox((l) => l && l.idx < l.urls.length - 1 ? { ...l, idx: l.idx + 1 } : l);
    if (e.key === "ArrowLeft")  setLightbox((l) => l && l.idx > 0 ? { ...l, idx: l.idx - 1 } : l);
  }, [lightbox]);

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  /* Prevent body scroll when lightbox open */
  useEffect(() => {
    document.body.style.overflow = lightbox ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [lightbox]);

  async function handlePublish(jobId: number) {
    setLoadingId(jobId);
    try { await publishJob(jobId); mutate(); }
    finally { setLoadingId(null); }
  }

  async function handleSkip(jobId: number) {
    setLoadingId(jobId);
    try { await skipJob(jobId); mutate(); }
    finally { setLoadingId(null); }
  }

  async function handleSaveCaption(jobId: number) {
    await updateJobCaption(jobId, editText);
    setEditing(null);
    mutate();
  }

  async function handleRegenerate(jobId: number) {
    setLoadingId(jobId);
    try { await regenerateCaption(jobId); mutate(); }
    finally { setLoadingId(null); }
  }

  function openLightbox(job: PublishJob, idx = 0) {
    const urls = resolveUrls(job);
    if (!urls.length) return;
    setLightbox({ job, urls, idx });
  }

  return (
    <>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-text-primary">Review Queue</h1>
            <p className="text-sm text-text-secondary mt-0.5">
              {isLoading ? "Loading…" : `${jobs.length} post${jobs.length !== 1 ? "s" : ""} awaiting approval`}
            </p>
          </div>
          <button onClick={() => mutate()} className="btn-ghost">
            <Icon icon="solar:refresh-bold-duotone" width={15} />
            Refresh
          </button>
        </div>

        {/* Empty */}
        {!isLoading && jobs.length === 0 && (
          <div className="card flex flex-col items-center justify-center py-20 text-center">
            <Icon icon="solar:inbox-bold-duotone" width={48} className="text-text-disabled mb-4" />
            <p className="text-base font-semibold text-text-primary">Queue is empty</p>
            <p className="text-sm text-text-secondary mt-1">New posts will appear here after crawling.</p>
          </div>
        )}

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
                  <div className="h-3 bg-bg-paper-hover rounded w-4/5" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Cards */}
        {!isLoading && jobs.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {jobs.map((job) => (
              <QueueCard
                key={job.id}
                job={job}
                loading={loadingId === job.id}
                isEditing={editing === job.id}
                editText={editText}
                isExpanded={expanded.has(job.id)}
                onPublish={() => handlePublish(job.id)}
                onSkip={() => handleSkip(job.id)}
                onStartEdit={() => { setEditing(job.id); setEditText(job.ai_generated_caption ?? ""); }}
                onCancelEdit={() => setEditing(null)}
                onSaveEdit={() => handleSaveCaption(job.id)}
                onEditTextChange={setEditText}
                onRegenerate={() => handleRegenerate(job.id)}
                onToggleExpand={() => setExpanded((p) => { const n = new Set(p); n.has(job.id) ? n.delete(job.id) : n.add(job.id); return n; })}
                onImageClick={(idx) => openLightbox(job, idx)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Lightbox */}
      {lightbox && (
        <Lightbox
          state={lightbox}
          onClose={() => setLightbox(null)}
          onPrev={() => setLightbox((l) => l && l.idx > 0 ? { ...l, idx: l.idx - 1 } : l)}
          onNext={() => setLightbox((l) => l && l.idx < l.urls.length - 1 ? { ...l, idx: l.idx + 1 } : l)}
          onPublish={() => { handlePublish(lightbox.job.id); setLightbox(null); }}
          onSkip={() => { handleSkip(lightbox.job.id); setLightbox(null); }}
          loading={loadingId === lightbox.job.id}
        />
      )}
    </>
  );
}

/* ── Queue card ─────────────────────────────────── */
function QueueCard({
  job, loading, isEditing, editText, isExpanded,
  onPublish, onSkip, onStartEdit, onCancelEdit, onSaveEdit,
  onEditTextChange, onRegenerate, onToggleExpand, onImageClick,
}: {
  job: PublishJob; loading: boolean; isEditing: boolean;
  editText: string; isExpanded: boolean;
  onPublish: () => void; onSkip: () => void;
  onStartEdit: () => void; onCancelEdit: () => void; onSaveEdit: () => void;
  onEditTextChange: (t: string) => void; onRegenerate: () => void;
  onToggleExpand: () => void; onImageClick: (idx: number) => void;
}) {
  const fanpage = job.fanpage_name ?? "Unknown Fanpage";
  const color = avatarColor(fanpage);
  const urls = resolveUrls(job);
  const thumb = urls[0];
  const albumCount = urls.length;
  const caption = job.ai_generated_caption ?? "";
  const createdAt = (job as unknown as Record<string, string>).created_at;
  const isNews = job.content_type === "news_content";
  const needsDesign = isNews && job.status === "pending_design";

  const mediaIcon =
    job.media_type === "album"
      ? "solar:gallery-bold-duotone"
      : "solar:gallery-minimalistic-bold-duotone";

  return (
    <div className="bg-bg-paper rounded-xl overflow-hidden flex flex-col dark:shadow-card">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 pb-2">
        {job.fanpage_picture_url ? (
          <img
            src={job.fanpage_picture_url}
            alt={fanpage}
            className="w-10 h-10 rounded-full object-cover flex-shrink-0"
          />
        ) : (
          <div
            className="w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0 select-none"
            style={{ background: color }}
          >
            {fanpage[0]?.toUpperCase() ?? "?"}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-text-primary truncate">{fanpage}</p>
          <p className="text-xs text-text-secondary truncate">
            {createdAt ? timeAgo(createdAt) : ""}{createdAt ? " · " : ""}
            {isNews ? (job.design_title ?? "News content") : `@${job.ig_username}`}
          </p>
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {!isNews && (
          <button onClick={onRegenerate} disabled={loading} title="Regenerate"
            className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-bg-paper-hover text-text-secondary hover:text-text-primary transition-colors">
            <Icon icon="solar:restart-bold-duotone" width={14} />
          </button>
          )}
          <button onClick={onStartEdit} title="Edit caption"
            className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-bg-paper-hover text-text-secondary hover:text-text-primary transition-colors">
            <Icon icon="solar:pen-new-round-bold-duotone" width={14} />
          </button>
        </div>
      </div>

      {/* Meta */}
      <div className="flex items-center gap-3 px-4 pb-3">
        {isNews ? (
          <>
            <span className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-info-main bg-[rgba(0,184,217,0.12)] px-2 py-0.5 rounded-full">
              <Icon icon="solar:document-text-bold-duotone" width={11} />
              News
            </span>
            <span className="flex items-center gap-1 text-xs text-text-secondary">
              <Icon icon={needsDesign ? "solar:pen-2-bold-duotone" : "solar:check-circle-bold-duotone"} width={12} />
              {needsDesign ? "needs design" : "design ready"}
            </span>
          </>
        ) : (
        <span className="flex items-center gap-1 text-xs text-text-secondary">
          <Icon icon={mediaIcon} width={12} />
          {job.media_type}
        </span>
        )}
        {albumCount > 1 && (
          <span className="flex items-center gap-1 text-xs text-text-secondary">
            <Icon icon="solar:gallery-bold-duotone" width={12} />
            {albumCount} photos
          </span>
        )}
      </div>

      {/* Thumbnail — click to open lightbox */}
      <div
        className="relative mx-4 rounded-lg overflow-hidden bg-bg-paper-hover cursor-zoom-in"
        onClick={() => onImageClick(0)}
      >
        {thumb ? (
          <img src={thumb} alt="Post preview" className="w-full aspect-[4/3] object-cover" />
        ) : (
          <div className="w-full aspect-[4/3] flex items-center justify-center">
            <Icon icon="solar:gallery-bold-duotone" width={40} className="text-text-disabled" />
          </div>
        )}

        {/* Album badge */}
        {albumCount > 1 && (
          <div className="absolute bottom-2.5 right-2.5 flex items-center gap-1 bg-white/90 backdrop-blur-sm text-[#1C252E] text-xs font-bold px-2.5 py-1 rounded-full">
            <Icon icon="solar:gallery-bold-duotone" width={11} />
            {albumCount}
          </div>
        )}

        {/* Expand hint overlay on hover */}
        <div className="absolute inset-0 bg-black/0 hover:bg-black/20 transition-colors flex items-center justify-center opacity-0 hover:opacity-100">
          <div className="bg-black/60 rounded-full p-2">
            <Icon icon="solar:maximize-bold-duotone" width={18} className="text-white" />
          </div>
        </div>
      </div>

      {/* Caption */}
      <div className="px-4 pt-3 pb-2 flex-1">
        {isEditing ? (
          <div className="space-y-2">
            <textarea
              className="input-rect w-full h-28 resize-none text-xs"
              value={editText}
              onChange={(e) => onEditTextChange(e.target.value)}
              autoFocus
            />
            <div className="flex gap-2">
              <button onClick={onSaveEdit} className="btn-primary text-xs py-1.5">Save</button>
              <button onClick={onCancelEdit} className="btn-ghost text-xs py-1.5">Cancel</button>
            </div>
          </div>
        ) : (
          <div>
            {caption ? (
              <>
                <p
                  className={`text-xs text-text-secondary leading-relaxed cursor-pointer ${isExpanded ? "" : "line-clamp-3"}`}
                  onClick={onToggleExpand}
                >
                  {caption}
                </p>
                {caption.length > 120 && (
                  <button onClick={onToggleExpand} className="text-[11px] text-primary-main mt-1 hover:underline">
                    {isExpanded ? "Show less" : "Show more"}
                  </button>
                )}
              </>
            ) : (
              <button onClick={onStartEdit} className="text-xs text-text-disabled italic hover:text-primary-main transition-colors">
                No caption — tap to add one
              </button>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      {!isEditing && (
        <div className="flex items-center gap-2 px-4 pb-4 pt-1">
          {needsDesign ? (
            <Link href={`/designer/${job.id}`}
              className="flex-1 btn-primary justify-center text-xs py-2">
              <Icon icon="solar:palette-bold-duotone" width={14} />
              Open in Designer
            </Link>
          ) : (
            <>
              <button onClick={onPublish} disabled={loading}
                className="flex-1 btn-primary justify-center text-xs py-2">
                <Icon icon="solar:verified-check-bold-duotone" width={14} />
                {loading ? "Publishing…" : "Publish"}
              </button>
              {isNews && (
                <Link href={`/designer/${job.id}`} title="Open in Designer"
                  className="flex items-center gap-1 px-3 py-2 text-xs font-semibold text-primary-main bg-[rgba(0,167,111,0.08)] hover:bg-[rgba(0,167,111,0.16)] rounded-md transition-colors">
                  <Icon icon="solar:palette-bold-duotone" width={13} />
                  Designer
                </Link>
              )}
            </>
          )}
          <button onClick={onSkip} disabled={loading}
            className="flex items-center gap-1 px-3 py-2 text-xs font-semibold text-error-main bg-[rgba(255,86,48,0.08)] hover:bg-[rgba(255,86,48,0.16)] rounded-md transition-colors">
            <Icon icon="solar:close-bold" width={13} />
            Skip
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Lightbox ────────────────────────────────────── */
function Lightbox({
  state, onClose, onPrev, onNext, onPublish, onSkip, loading,
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
  const color = avatarColor(fanpage);
  const caption = job.ai_generated_caption ?? "";
  const total = urls.length;
  const createdAt = (job as unknown as Record<string, string>).created_at;

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 backdrop-blur-md p-4"
      onClick={onClose}
    >
      {/* Modal — stop propagation so clicking inside doesn't close */}
      <div
        className="relative w-full max-w-2xl rounded-2xl overflow-hidden shadow-dropdown"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Image — fixed 4:3 */}
        <div className="relative bg-black aspect-[4/3]">
          <img
            src={urls[idx]}
            alt={`Image ${idx + 1}`}
            className="absolute inset-0 w-full h-full object-contain"
          />

          {/* Close button */}
          <button
            onClick={onClose}
            className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors"
          >
            <Icon icon="solar:close-bold" width={16} />
          </button>

          {/* Prev / Next arrows */}
          {total > 1 && idx > 0 && (
            <button
              onClick={onPrev}
              className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors"
            >
              <Icon icon="solar:alt-arrow-left-bold-duotone" width={18} />
            </button>
          )}
          {total > 1 && idx < total - 1 && (
            <button
              onClick={onNext}
              className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors"
            >
              <Icon icon="solar:alt-arrow-right-bold-duotone" width={18} />
            </button>
          )}

          {/* Image counter */}
          {total > 1 && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-black/60 text-white text-xs font-semibold px-3 py-1 rounded-full">
              {idx + 1} / {total}
            </div>
          )}

          {/* Bottom gradient + caption overlay */}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-16 pb-4 px-5">
            {/* Fanpage row */}
            <div className="flex items-center gap-2.5 mb-2">
              {job.fanpage_picture_url ? (
                <img
                  src={job.fanpage_picture_url}
                  alt={fanpage}
                  className="w-7 h-7 rounded-full object-cover flex-shrink-0"
                />
              ) : (
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-white font-bold text-xs flex-shrink-0"
                  style={{ background: color }}
                >
                  {fanpage[0]?.toUpperCase()}
                </div>
              )}
              <div className="min-w-0">
                <p className="text-white text-xs font-semibold leading-none">{fanpage}</p>
                <p className="text-white/60 text-[10px] mt-0.5">
                  {job.content_type === "news_content" ? (job.design_title ?? "News content") : `@${job.ig_username}`}
                  {createdAt ? ` · ${timeAgo(createdAt)}` : ""}
                </p>
              </div>
              <span className="ml-auto text-[10px] font-semibold text-white/60 uppercase tracking-wide bg-white/10 px-2 py-0.5 rounded-full">
                {job.content_type === "news_content" ? "news" : job.media_type}
              </span>
            </div>

            {/* Caption */}
            {caption ? (
              <p className="text-white/90 text-xs leading-relaxed line-clamp-4">{caption}</p>
            ) : (
              <p className="text-white/40 text-xs italic">No caption generated yet</p>
            )}
          </div>
        </div>

        {/* Dot indicators for album */}
        {total > 1 && (
          <div className="flex justify-center gap-1.5 py-2.5 bg-black">
            {urls.map((_, i) => (
              <button
                key={i}
                onClick={() => {/* handled via arrows */}}
                className={`w-1.5 h-1.5 rounded-full transition-all ${
                  i === idx ? "bg-primary-main w-4" : "bg-white/30"
                }`}
              />
            ))}
          </div>
        )}

        {/* Action bar */}
        <div className="flex items-center gap-3 px-5 py-4 bg-bg-paper">
          <button
            onClick={onPublish}
            disabled={loading}
            className="flex-1 btn-primary justify-center"
          >
            <Icon icon="solar:verified-check-bold-duotone" width={16} />
            {loading ? "Publishing…" : "Publish to Fanpage"}
          </button>
          <button
            onClick={onSkip}
            disabled={loading}
            className="flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold text-error-main bg-[rgba(255,86,48,0.08)] hover:bg-[rgba(255,86,48,0.16)] rounded-md transition-colors"
          >
            <Icon icon="solar:close-bold" width={14} />
            Skip
          </button>
        </div>
      </div>
    </div>
  );
}
