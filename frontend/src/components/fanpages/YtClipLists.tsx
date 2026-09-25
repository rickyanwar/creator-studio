"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@iconify/react";
import {
  listYtClipIdeas,
  updateYtClipIdea,
  deleteYtClipIdea,
  listYtVideos,
  retryYtVideo,
} from "@/lib/api";
import type { YtClipIdeaRef, YtVideoRef } from "@/lib/types";

type Paged<T> = { items: T[]; has_more: boolean };

function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function errorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

/** The pending clip-idea queue: preview each moment on YouTube, edit the
 * title/hook, or delete ideas before they're rendered. */
export function YtClipIdeaQueue({ fanpageId }: { fanpageId: number }) {
  const [ideas, setIdeas] = useState<YtClipIdeaRef[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editHook, setEditHook] = useState("");

  const load = useCallback(async () => {
    const res = await listYtClipIdeas(fanpageId, { status: "pending" });
    const data = res.data as Paged<YtClipIdeaRef>;
    setIdeas(data.items);
    setHasMore(data.has_more);
  }, [fanpageId]);

  useEffect(() => {
    load().catch(() => setIdeas([]));
  }, [load]);

  async function loadMore() {
    const res = await listYtClipIdeas(fanpageId, { status: "pending", offset: ideas.length });
    const data = res.data as Paged<YtClipIdeaRef>;
    setIdeas((prev) => [...prev, ...data.items]);
    setHasMore(data.has_more);
  }

  function startEdit(idea: YtClipIdeaRef) {
    setEditingId(idea.id);
    setEditTitle(idea.title);
    setEditHook(idea.hook_text || "");
  }

  async function save(ideaId: number) {
    try {
      const res = await updateYtClipIdea(fanpageId, ideaId, { title: editTitle.trim(), hook_text: editHook.trim() });
      setIdeas((prev) => prev.map((i) => (i.id === ideaId ? (res.data as YtClipIdeaRef) : i)));
      setEditingId(null);
    } catch (err) {
      alert(errorMessage(err, "Couldn't save the idea."));
    }
  }

  async function remove(ideaId: number) {
    try {
      await deleteYtClipIdea(fanpageId, ideaId);
      setIdeas((prev) => prev.filter((i) => i.id !== ideaId));
    } catch (err) {
      alert(errorMessage(err, "Couldn't delete the idea."));
    }
  }

  return (
    <div className="border-t border-hairline pt-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">Clip Ideas Queue</p>
          <p className="text-[11px] text-text-secondary mt-0.5">
            Moments the AI picked, newest video first. Nothing is rendered yet — preview each on YouTube, edit
            the title, or delete the ones you don&apos;t want.
          </p>
        </div>
        <button onClick={() => load()} className="text-text-secondary hover:text-text-primary shrink-0" title="Refresh">
          <Icon icon="solar:refresh-bold-duotone" width={16} />
        </button>
      </div>

      <div className="space-y-1.5 max-h-[460px] overflow-y-auto">
        {ideas.length === 0 ? (
          <p className="text-[11px] text-text-secondary italic">
            No clip ideas yet — they appear once a video has been analysed.
          </p>
        ) : (
          ideas.map((idea) =>
            editingId === idea.id ? (
              <div key={idea.id} className="p-2.5 rounded-lg border border-primary-main space-y-2">
                <input className="input w-full text-sm" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} placeholder="Title" />
                <input className="input w-full text-sm" value={editHook} onChange={(e) => setEditHook(e.target.value)} placeholder="Hook" />
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setEditingId(null)} className="text-xs text-text-secondary px-2 py-1">Cancel</button>
                  <button onClick={() => save(idea.id)} disabled={!editTitle.trim()} className="btn-primary text-xs px-3 py-1 disabled:opacity-50">
                    Save
                  </button>
                </div>
              </div>
            ) : (
              <div key={idea.id} className="flex items-start gap-3 p-2.5 rounded-lg border border-hairline">
                <span
                  className="shrink-0 mt-0.5 text-[11px] font-bold px-1.5 py-0.5 rounded bg-[rgba(0,167,111,0.12)] text-primary-main"
                  title="Virality score (AI)"
                >
                  {idea.virality_score}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium leading-tight text-text-primary">{idea.title}</p>
                  {idea.hook_text && <p className="text-[11px] text-text-secondary line-clamp-2 mt-0.5">{idea.hook_text}</p>}
                  <p className="text-[10px] text-text-secondary mt-0.5 truncate">
                    {clock(idea.start_s)}–{clock(idea.end_s)} · {Math.round(idea.end_s - idea.start_s)}s
                    {idea.video_title ? ` · ${idea.video_title}` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <a
                    href={idea.preview_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-text-secondary hover:text-error-main transition-colors"
                    title="Watch this moment on YouTube"
                  >
                    <Icon icon="solar:play-circle-bold-duotone" width={17} />
                  </a>
                  <button onClick={() => startEdit(idea)} className="text-text-secondary hover:text-text-primary" title="Edit">
                    <Icon icon="solar:pen-new-round-bold-duotone" width={16} />
                  </button>
                  <button onClick={() => remove(idea.id)} className="text-text-secondary hover:text-error-main" title="Delete">
                    <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                  </button>
                </div>
              </div>
            )
          )
        )}
      </div>
      {hasMore && (
        <button onClick={loadMore} className="text-xs text-primary-main font-semibold">Load more</button>
      )}
    </div>
  );
}

const STATUS_STYLE: Record<YtVideoRef["status"], string> = {
  discovered: "bg-bg-paper-hover text-text-secondary",
  analyzing: "bg-[rgba(0,184,217,0.12)] text-[#006C9C]",
  analyzed: "bg-[rgba(0,167,111,0.12)] text-primary-main",
  skipped: "bg-[rgba(255,171,0,0.14)] text-[#B76E00]",
  failed: "bg-[rgba(255,86,48,0.12)] text-error-main",
};

/** Every video discovered for this fanpage and what happened to it. */
export function YtVideoList({ fanpageId }: { fanpageId: number }) {
  const [videos, setVideos] = useState<YtVideoRef[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    const res = await listYtVideos(fanpageId);
    const data = res.data as Paged<YtVideoRef>;
    setVideos(data.items);
    setHasMore(data.has_more);
  }, [fanpageId]);

  useEffect(() => {
    if (open) load().catch(() => setVideos([]));
  }, [open, load]);

  async function loadMore() {
    const res = await listYtVideos(fanpageId, { offset: videos.length });
    const data = res.data as Paged<YtVideoRef>;
    setVideos((prev) => [...prev, ...data.items]);
    setHasMore(data.has_more);
  }

  async function retry(rowId: number) {
    try {
      const res = await retryYtVideo(fanpageId, rowId);
      setVideos((prev) => prev.map((v) => (v.id === rowId ? (res.data as YtVideoRef) : v)));
    } catch (err) {
      alert(errorMessage(err, "Couldn't retry that video."));
    }
  }

  return (
    <div className="border-t border-hairline pt-4 space-y-3">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 text-xs font-semibold text-text-secondary uppercase tracking-wide">
        <Icon icon={open ? "solar:alt-arrow-down-bold" : "solar:alt-arrow-right-bold"} width={14} />
        Videos found
      </button>
      {open && (
        <div className="space-y-1.5 max-h-[360px] overflow-y-auto">
          {videos.length === 0 ? (
            <p className="text-[11px] text-text-secondary italic">No videos discovered yet.</p>
          ) : (
            videos.map((v) => (
              <div key={v.id} className="flex items-start gap-3 p-2.5 rounded-lg border border-hairline">
                <span className={`shrink-0 text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${STATUS_STYLE[v.status]}`}>{v.status}</span>
                <div className="min-w-0 flex-1">
                  <a
                    href={`https://www.youtube.com/watch?v=${v.video_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm leading-tight text-text-primary hover:underline line-clamp-1"
                  >
                    {v.title || v.video_id}
                  </a>
                  <p className="text-[10px] text-text-secondary mt-0.5">
                    {v.channel_name ? `${v.channel_name} · ` : ""}
                    {v.duration_s ? `${clock(v.duration_s)} · ` : ""}
                    {v.status === "analyzed" ? `${v.ideas_created} clip idea(s)` : v.skip_reason || v.last_error || ""}
                  </p>
                </div>
                {(v.status === "skipped" || v.status === "failed") && (
                  <button onClick={() => retry(v.id)} className="text-text-secondary hover:text-text-primary shrink-0" title="Try this video again">
                    <Icon icon="solar:restart-bold-duotone" width={16} />
                  </button>
                )}
              </div>
            ))
          )}
          {hasMore && (
            <button onClick={loadMore} className="text-xs text-primary-main font-semibold">Load more</button>
          )}
        </div>
      )}
    </div>
  );
}
