"use client";

import { useState } from "react";
import { Icon } from "@iconify/react";
import type {
  FanpageDetail,
  DiscussionContentIdeaRef,
  PinterestContentIdeaRef,
  FacebookPhotoIdeaRef,
} from "@/lib/types";
import { draftDiscussionIdeas } from "@/lib/discussionDraftApi";
import { YtClipIdeaQueue, YtVideoList } from "@/components/fanpages/YtClipLists";

export type IdeaSubTab = "discussion" | "pinterest" | "facebook" | "youtube";

export interface IdeasTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;

  // Discussion ideas
  newDiscSeed: string;
  setNewDiscSeed: (v: string) => void;
  newDiscSubjectHint: string;
  setNewDiscSubjectHint: (v: string) => void;
  discIdeaSaving: boolean;
  handleAddDiscIdea: () => Promise<void>;
  discIdeas: DiscussionContentIdeaRef[];
  editingDiscIdeaId: number | null;
  setEditingDiscIdeaId: (id: number | null) => void;
  editDiscLabel: string;
  setEditDiscLabel: (v: string) => void;
  editDiscQuestion: string;
  setEditDiscQuestion: (v: string) => void;
  editDiscSubject: string;
  setEditDiscSubject: (v: string) => void;
  editDiscCaption: string;
  setEditDiscCaption: (v: string) => void;
  handleSaveDiscIdea: (id: number) => Promise<void>;
  startEditDiscIdea: (idea: DiscussionContentIdeaRef) => void;
  handleDeleteDiscIdea: (id: number) => Promise<void>;
  discIdeasHasMore: boolean;
  handleLoadMoreDiscIdeas: () => Promise<void>;
  discIdeasLoadingMore: boolean;

  // Pinterest ideas
  ideas: PinterestContentIdeaRef[];
  editingIdeaId: number | null;
  setEditingIdeaId: (id: number | null) => void;
  editIdeaTitle: string;
  setEditIdeaTitle: (v: string) => void;
  editIdeaDescription: string;
  setEditIdeaDescription: (v: string) => void;
  handleSaveIdea: (id: number) => Promise<void>;
  startEditIdea: (idea: PinterestContentIdeaRef) => void;
  handleDeleteIdea: (id: number) => Promise<void>;
  ideasHasMore: boolean;
  handleLoadMoreIdeas: () => Promise<void>;
  ideasLoadingMore: boolean;

  // Facebook photo ideas
  fbPhotoIdeas: FacebookPhotoIdeaRef[];
  editingFbIdeaId: number | null;
  setEditingFbIdeaId: (id: number | null) => void;
  editFbIdeaTitle: string;
  setEditFbIdeaTitle: (v: string) => void;
  editFbIdeaSubtitle: string;
  setEditFbIdeaSubtitle: (v: string) => void;
  editFbIdeaCaption: string;
  setEditFbIdeaCaption: (v: string) => void;
  handleSaveFbIdea: (id: number) => Promise<void>;
  startEditFbIdea: (idea: FacebookPhotoIdeaRef) => void;
  handleDeleteFbIdea: (id: number) => Promise<void>;
  fbPhotoIdeasHasMore: boolean;
  handleLoadMoreFbPhotoIdeas: () => Promise<void>;
  fbPhotoIdeasLoadingMore: boolean;
}

export function IdeasTab({
  fanpageId,
  form,
  newDiscSeed,
  setNewDiscSeed,
  newDiscSubjectHint,
  setNewDiscSubjectHint,
  discIdeaSaving,
  handleAddDiscIdea,
  discIdeas,
  editingDiscIdeaId,
  setEditingDiscIdeaId,
  editDiscLabel,
  setEditDiscLabel,
  editDiscQuestion,
  setEditDiscQuestion,
  editDiscSubject,
  setEditDiscSubject,
  editDiscCaption,
  setEditDiscCaption,
  handleSaveDiscIdea,
  startEditDiscIdea,
  handleDeleteDiscIdea,
  discIdeasHasMore,
  handleLoadMoreDiscIdeas,
  discIdeasLoadingMore,

  ideas,
  editingIdeaId,
  setEditingIdeaId,
  editIdeaTitle,
  setEditIdeaTitle,
  editIdeaDescription,
  setEditIdeaDescription,
  handleSaveIdea,
  startEditIdea,
  handleDeleteIdea,
  ideasHasMore,
  handleLoadMoreIdeas,
  ideasLoadingMore,

  fbPhotoIdeas,
  editingFbIdeaId,
  setEditingFbIdeaId,
  editFbIdeaTitle,
  setEditFbIdeaTitle,
  editFbIdeaSubtitle,
  setEditFbIdeaSubtitle,
  editFbIdeaCaption,
  setEditFbIdeaCaption,
  handleSaveFbIdea,
  startEditFbIdea,
  handleDeleteFbIdea,
  fbPhotoIdeasHasMore,
  handleLoadMoreFbPhotoIdeas,
  fbPhotoIdeasLoadingMore,
}: IdeasTabProps) {
  const [subTab, setSubTab] = useState<IdeaSubTab>("discussion");
  const [draftCount, setDraftCount] = useState<number>(5);
  const [draftHint, setDraftHint] = useState<string>("");
  const [draftLoading, setDraftLoading] = useState<boolean>(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftedIdeas, setDraftedIdeas] = useState<DiscussionContentIdeaRef[]>([]);

  async function handleDraftAi() {
    setDraftLoading(true);
    setDraftError(null);
    try {
      const res = await draftDiscussionIdeas(fanpageId, {
        count: draftCount,
        topic_hint: draftHint.trim() || undefined,
      });
      const newItems = Array.isArray(res.data) ? res.data : [];
      if (newItems.length > 0) {
        setDraftedIdeas((prev) => [...newItems, ...prev]);
        setDraftHint("");
      }
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Failed to draft discussion ideas with AI";
      setDraftError(String(msg));
    } finally {
      setDraftLoading(false);
    }
  }

  const allDiscIdeas = [
    ...draftedIdeas,
    ...discIdeas.filter((i) => !draftedIdeas.some((d) => d.id === i.id)),
  ];

  const onSaveDiscIdea = async (id: number) => {
    await handleSaveDiscIdea(id);
    setDraftedIdeas((prev) =>
      prev.map((i) =>
        i.id === id
          ? {
              ...i,
              label: editDiscLabel,
              question: editDiscQuestion,
              subject_name: editDiscSubject,
              caption: editDiscCaption,
            }
          : i
      )
    );
  };

  const onDeleteDiscIdea = async (id: number) => {
    await handleDeleteDiscIdea(id);
    setDraftedIdeas((prev) => prev.filter((i) => i.id !== id));
  };

  const subTabs = [
    {
      id: "discussion" as const,
      label: "Discussion",
      count: form.discussion_enabled ? String(allDiscIdeas.length) : "mode off",
      enabled: !!form.discussion_enabled,
    },
    {
      id: "pinterest" as const,
      label: "Pinterest",
      count: form.pinterest_enabled ? String(ideas.length) : "mode off",
      enabled: !!form.pinterest_enabled,
    },
    {
      id: "facebook" as const,
      label: "Facebook",
      count: form.facebook_photo_enabled ? String(fbPhotoIdeas.length) : "mode off",
      enabled: !!form.facebook_photo_enabled,
    },
    {
      id: "youtube" as const,
      label: "YouTube clips",
      count: form.yt_clip_enabled ? "Active" : "mode off",
      enabled: !!form.yt_clip_enabled,
    },
  ];

  return (
    <div role="tabpanel" id="tab-panel-ideas" aria-labelledby="tab-ideas" className="space-y-6">
      <section className="bg-surface border border-line rounded-2xl p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-text">Ideas and Content Queues</h2>
          <p className="text-xs text-text2 mt-0.5">
            Every queue of upcoming ideas in one place. Only Discussion ideas are written by hand; Pinterest, Facebook and YouTube queues fill automatically.
          </p>
        </div>

        {/* Sub-tabs header */}
        <div
          role="tablist"
          aria-label="Idea queue sub-tabs"
          className="flex flex-wrap gap-1 p-1 rounded-xl bg-surface2 border border-line"
        >
          {subTabs.map((st) => {
            const isSelected = subTab === st.id;
            return (
              <button
                key={st.id}
                type="button"
                role="tab"
                id={`it-${st.id}`}
                aria-selected={isSelected}
                onClick={() => setSubTab(st.id)}
                className={`inline-flex items-center gap-2 min-h-[38px] px-3.5 rounded-lg text-xs transition-all cursor-pointer ${
                  isSelected
                    ? "bg-surface text-pri-text font-semibold shadow-sm border border-pri"
                    : "bg-transparent text-text2 font-medium border border-transparent hover:text-text"
                }`}
              >
                <span>{st.label}</span>
                <span
                  className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-full ${
                    st.enabled
                      ? "bg-surface2 text-text2"
                      : "bg-warn-soft text-warn"
                  }`}
                >
                  {st.count}
                </span>
              </button>
            );
          })}
        </div>

        {/* ── Sub-tab: Discussion ────────────────────────── */}
        {subTab === "discussion" && (
          <div className="space-y-4">
            {!form.discussion_enabled && (
              <div role="note" className="p-3 rounded-lg bg-warn-soft border border-line text-xs text-text">
                <strong className="font-semibold text-warn">Mode off.</strong> Discussion mode is disabled for this brand. Turn it on in the Content tab to publish these ideas.
              </div>
            )}

            {/* Add discussion idea form */}
            <div className="p-4 rounded-xl border border-line bg-surface2 space-y-3">
              <span className="text-xs font-semibold text-text uppercase tracking-wide">
                Add Discussion Content Idea
              </span>
              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  type="text"
                  className="input-rect flex-2"
                  placeholder="Question / topic seed (e.g. Best race finish of all time?)"
                  value={newDiscSeed}
                  onChange={(e) => setNewDiscSeed(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddDiscIdea()}
                />
                <input
                  type="text"
                  className="input-rect flex-1"
                  placeholder="Subject hint (optional)"
                  value={newDiscSubjectHint}
                  onChange={(e) => setNewDiscSubjectHint(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddDiscIdea()}
                />
                <button
                  type="button"
                  onClick={handleAddDiscIdea}
                  disabled={discIdeaSaving || !newDiscSeed.trim()}
                  className="btn-primary text-xs px-4 py-2 shrink-0 disabled:opacity-50"
                >
                  {discIdeaSaving ? "Adding…" : "Add Idea"}
                </button>
              </div>
            </div>

            {/* Draft with AI */}
            <div className="p-4 rounded-xl border border-line bg-surface2 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-text uppercase tracking-wide flex items-center gap-1.5">
                  <Icon icon="solar:magic-stick-3-bold-duotone" width={16} className="text-pri" />
                  Draft with AI
                </span>
                <span className="text-[11px] text-text2">Generate multiple debate ideas at once</span>
              </div>
              <div className="flex flex-col sm:flex-row gap-2">
                <div className="w-full sm:w-28 shrink-0">
                  <select
                    className="input-rect w-full text-xs"
                    value={draftCount}
                    onChange={(e) => setDraftCount(Number(e.target.value))}
                    disabled={draftLoading}
                    aria-label="Number of ideas to draft"
                  >
                    {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
                      <option key={n} value={n}>
                        {n} {n === 1 ? "idea" : "ideas"}
                      </option>
                    ))}
                  </select>
                </div>
                <input
                  type="text"
                  className="input-rect flex-1 text-xs"
                  placeholder="Topic / angle hint (optional, e.g. 2026 driver lineup)"
                  maxLength={200}
                  value={draftHint}
                  onChange={(e) => setDraftHint(e.target.value)}
                  disabled={draftLoading}
                  onKeyDown={(e) => e.key === "Enter" && !draftLoading && handleDraftAi()}
                />
                <button
                  type="button"
                  onClick={handleDraftAi}
                  disabled={draftLoading}
                  className="btn-primary text-xs px-4 py-2 shrink-0 inline-flex items-center justify-center gap-1.5 disabled:opacity-50 cursor-pointer min-h-[38px]"
                >
                  {draftLoading ? (
                    <>
                      <Icon icon="solar:restart-bold" className="animate-spin" width={14} />
                      <span>Drafting…</span>
                    </>
                  ) : (
                    <>
                      <Icon icon="solar:magic-stick-3-bold" width={14} />
                      <span>Draft with AI</span>
                    </>
                  )}
                </button>
              </div>
              {draftError && (
                <p className="text-xs text-bad">{draftError}</p>
              )}
            </div>

            {/* Ideas list */}
            <div className="space-y-2">
              {allDiscIdeas.length === 0 ? (
                <p className="text-xs text-text2 italic py-4">No pending discussion ideas.</p>
              ) : (
                allDiscIdeas.map((idea) =>
                  editingDiscIdeaId === idea.id ? (
                    <div key={idea.id} className="p-4 rounded-xl border border-pri bg-surface space-y-3">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label className="label">Label</label>
                          <input
                            className="input-rect w-full"
                            value={editDiscLabel}
                            onChange={(e) => setEditDiscLabel(e.target.value)}
                          />
                        </div>
                        <div>
                          <label className="label">Subject Name</label>
                          <input
                            className="input-rect w-full"
                            value={editDiscSubject}
                            onChange={(e) => setEditDiscSubject(e.target.value)}
                          />
                        </div>
                        <div className="sm:col-span-2">
                          <label className="label">Question</label>
                          <input
                            className="input-rect w-full"
                            value={editDiscQuestion}
                            onChange={(e) => setEditDiscQuestion(e.target.value)}
                          />
                        </div>
                        <div className="sm:col-span-2">
                          <label className="label">Caption</label>
                          <textarea
                            className="input-rect w-full h-20 resize-none"
                            value={editDiscCaption}
                            onChange={(e) => setEditDiscCaption(e.target.value)}
                          />
                        </div>
                      </div>
                      <div className="flex gap-2 justify-end">
                        <button
                          type="button"
                          onClick={() => setEditingDiscIdeaId(null)}
                          className="btn-secondary text-xs px-3 py-1.5"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => onSaveDiscIdea(idea.id)}
                          className="btn-primary text-xs px-3 py-1.5"
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      key={idea.id}
                      className="p-3.5 rounded-xl border border-line bg-surface flex flex-wrap items-start justify-between gap-3"
                    >
                      <div className="space-y-1 min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-info-soft text-info border border-line">
                            {idea.label || "DISCUSSION"}
                          </span>
                          <span className="text-xs font-semibold text-text">{idea.question}</span>
                        </div>
                        {idea.subject_name && (
                          <p className="text-[11px] text-text2">Photo subject: {idea.subject_name}</p>
                        )}
                        {idea.caption && (
                          <p className="text-xs text-text2 line-clamp-2 mt-1">{idea.caption}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => startEditDiscIdea(idea)}
                          className="btn-ghost text-xs px-2.5 py-1 text-text2 hover:text-text"
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => onDeleteDiscIdea(idea.id)}
                          className="btn-ghost text-xs px-2.5 py-1 text-bad"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  )
                )
              )}
            </div>

            {discIdeasHasMore && (
              <button
                type="button"
                onClick={handleLoadMoreDiscIdeas}
                disabled={discIdeasLoadingMore}
                className="btn-secondary text-xs w-full py-2"
              >
                {discIdeasLoadingMore ? "Loading…" : "Load More Discussion Ideas"}
              </button>
            )}
          </div>
        )}

        {/* ── Sub-tab: Pinterest ─────────────────────────── */}
        {subTab === "pinterest" && (
          <div className="space-y-4">
            {!form.pinterest_enabled && (
              <div role="note" className="p-3 rounded-lg bg-warn-soft border border-line text-xs text-text">
                <strong className="font-semibold text-warn">Mode off.</strong> Pinterest mode is disabled for this brand. Turn it on in the Content tab to publish these ideas.
              </div>
            )}

            <div className="space-y-2">
              {ideas.length === 0 ? (
                <p className="text-xs text-text2 italic py-4">No pending Pinterest ideas.</p>
              ) : (
                ideas.map((idea) =>
                  editingIdeaId === idea.id ? (
                    <div key={idea.id} className="p-4 rounded-xl border border-pri bg-surface space-y-3">
                      <div>
                        <label className="label">Title</label>
                        <input
                          className="input-rect w-full"
                          value={editIdeaTitle}
                          onChange={(e) => setEditIdeaTitle(e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="label">Description</label>
                        <textarea
                          className="input-rect w-full h-20 resize-none"
                          value={editIdeaDescription}
                          onChange={(e) => setEditIdeaDescription(e.target.value)}
                        />
                      </div>
                      <div className="flex gap-2 justify-end">
                        <button
                          type="button"
                          onClick={() => setEditingIdeaId(null)}
                          className="btn-secondary text-xs px-3 py-1.5"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => handleSaveIdea(idea.id)}
                          className="btn-primary text-xs px-3 py-1.5"
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      key={idea.id}
                      className="p-3.5 rounded-xl border border-line bg-surface flex flex-wrap items-start justify-between gap-3"
                    >
                      <div className="space-y-1 min-w-0 flex-1">
                        <p className="text-sm font-semibold text-text">{idea.title}</p>
                        {idea.description && (
                          <p className="text-xs text-text2 line-clamp-2">{idea.description}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => startEditIdea(idea)}
                          className="btn-ghost text-xs px-2.5 py-1 text-text2 hover:text-text"
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteIdea(idea.id)}
                          className="btn-ghost text-xs px-2.5 py-1 text-bad"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  )
                )
              )}
            </div>

            {ideasHasMore && (
              <button
                type="button"
                onClick={handleLoadMoreIdeas}
                disabled={ideasLoadingMore}
                className="btn-secondary text-xs w-full py-2"
              >
                {ideasLoadingMore ? "Loading…" : "Load More Pinterest Ideas"}
              </button>
            )}
          </div>
        )}

        {/* ── Sub-tab: Facebook photo ────────────────────── */}
        {subTab === "facebook" && (
          <div className="space-y-4">
            {!form.facebook_photo_enabled && (
              <div role="note" className="p-3 rounded-lg bg-warn-soft border border-line text-xs text-text">
                <strong className="font-semibold text-warn">Mode off.</strong> Facebook photo recreate is disabled for this brand. Turn it on in the Content tab to publish these ideas.
              </div>
            )}

            <div className="space-y-2">
              {fbPhotoIdeas.length === 0 ? (
                <p className="text-xs text-text2 italic py-4">No pending Facebook photo ideas.</p>
              ) : (
                fbPhotoIdeas.map((idea) =>
                  editingFbIdeaId === idea.id ? (
                    <div key={idea.id} className="p-4 rounded-xl border border-pri bg-surface space-y-3">
                      <div>
                        <label className="label">Title / Quote</label>
                        <input
                          className="input-rect w-full"
                          value={editFbIdeaTitle}
                          onChange={(e) => setEditFbIdeaTitle(e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="label">Subtitle / Speaker</label>
                        <input
                          className="input-rect w-full"
                          value={editFbIdeaSubtitle}
                          onChange={(e) => setEditFbIdeaSubtitle(e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="label">Caption</label>
                        <textarea
                          className="input-rect w-full h-20 resize-none"
                          value={editFbIdeaCaption}
                          onChange={(e) => setEditFbIdeaCaption(e.target.value)}
                        />
                      </div>
                      <div className="flex gap-2 justify-end">
                        <button
                          type="button"
                          onClick={() => setEditingFbIdeaId(null)}
                          className="btn-secondary text-xs px-3 py-1.5"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => handleSaveFbIdea(idea.id)}
                          className="btn-primary text-xs px-3 py-1.5"
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      key={idea.id}
                      className="p-3.5 rounded-xl border border-line bg-surface flex flex-wrap items-start justify-between gap-3"
                    >
                      <div className="space-y-1 min-w-0 flex-1">
                        <p className="text-sm font-semibold text-text">{idea.design_title}</p>
                        {idea.design_subtitle && (
                          <p className="text-xs text-text2">{idea.design_subtitle}</p>
                        )}
                        {idea.design_caption && (
                          <p className="text-xs text-text2 line-clamp-2 mt-1">{idea.design_caption}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => startEditFbIdea(idea)}
                          className="btn-ghost text-xs px-2.5 py-1 text-text2 hover:text-text"
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteFbIdea(idea.id)}
                          className="btn-ghost text-xs px-2.5 py-1 text-bad"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  )
                )
              )}
            </div>

            {fbPhotoIdeasHasMore && (
              <button
                type="button"
                onClick={handleLoadMoreFbPhotoIdeas}
                disabled={fbPhotoIdeasLoadingMore}
                className="btn-secondary text-xs w-full py-2"
              >
                {fbPhotoIdeasLoadingMore ? "Loading…" : "Load More Facebook Ideas"}
              </button>
            )}
          </div>
        )}

        {/* ── Sub-tab: YouTube clips ─────────────────────── */}
        {subTab === "youtube" && (
          <div className="space-y-4">
            {!form.yt_clip_enabled && (
              <div role="note" className="p-3 rounded-lg bg-warn-soft border border-line text-xs text-text">
                <strong className="font-semibold text-warn">Mode off.</strong> YouTube clips are disabled for this brand. Turn them on in the Content tab to process clip moments.
              </div>
            )}
            <YtClipIdeaQueue fanpageId={fanpageId} />
            <YtVideoList fanpageId={fanpageId} />
          </div>
        )}
      </section>
    </div>
  );
}
