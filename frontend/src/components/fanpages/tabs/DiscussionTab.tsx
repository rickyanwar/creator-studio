"use client";

import { useState } from "react";
import { Icon } from "@iconify/react";
import type { DiscussionTopicRef, DiscussionContentIdeaRef, FanpageDetail } from "@/lib/types";
import { draftDiscussionIdeas } from "@/lib/discussionDraftApi";

export interface DiscussionTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  newTopicSeed: string;
  setNewTopicSeed: (v: string) => void;
  newTopicSubject: string;
  setNewTopicSubject: (v: string) => void;
  topicSaving: boolean;
  handleAddTopic: () => Promise<void>;
  handleToggleTopic: (t: DiscussionTopicRef) => Promise<void>;
  handleDeleteTopic: (topicId: number) => Promise<void>;
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
  handleSaveDiscIdea: (ideaId: number) => Promise<void>;
  startEditDiscIdea: (idea: DiscussionContentIdeaRef) => void;
  handleDeleteDiscIdea: (ideaId: number) => Promise<void>;
  discIdeasHasMore?: boolean;
  handleLoadMoreDiscIdeas?: () => Promise<void>;
  discIdeasLoadingMore?: boolean;
  hideIdeas?: boolean;
}

export function DiscussionTab({
  fanpageId,
  form,
  set,
  newTopicSeed,
  setNewTopicSeed,
  newTopicSubject,
  setNewTopicSubject,
  topicSaving,
  handleAddTopic,
  handleToggleTopic,
  handleDeleteTopic,
  newDiscSeed,
  setNewDiscSeed,
  newDiscSubjectHint,
  setNewDiscSubjectHint,
  discIdeaSaving,
  handleAddDiscIdea,
  discIdeas = [],
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
  hideIdeas,
}: DiscussionTabProps) {
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

  return (
    <div role="tabpanel" id="tab-panel-discussion" aria-labelledby="tab-discussion" className="space-y-8">
      {/* ── Section: Mode 4 — Discussion Content ───────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text">Discussion</h2>
            <p className="text-xs text-text2 mt-0.5">
              AI debate / hot-take cards (badge + big question + athlete photo, no yes/no buttons).
              Quota-driven — a set number per day, spread across 08:00–22:00 WIB.
            </p>
          </div>
          <button
            onClick={() => set("discussion_enabled", !form.discussion_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
              form.discussion_enabled ? "bg-pri" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.discussion_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.discussion_enabled && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div>
                <label className="label">Cards per day</label>
                <input
                  type="number"
                  min={0}
                  max={20}
                  className="input w-full"
                  value={(form.discussion_daily_count as number | undefined) ?? 2}
                  onChange={(e) => set("discussion_daily_count", parseInt(e.target.value || "0"))}
                />
                <p className="text-[11px] text-text2 mt-1">How many discussion cards to generate each day.</p>
              </div>
              <div>
                <label className="label">Topic source</label>
                <select
                  className="input w-full"
                  value={(form.discussion_topic_mode as string | undefined) ?? "both"}
                  onChange={(e) => set("discussion_topic_mode", e.target.value)}
                >
                  <option value="both">Both (news first, evergreen fallback)</option>
                  <option value="news">News only (from subscribed sources)</option>
                  <option value="evergreen">Evergreen only (your topics below)</option>
                </select>
                <p className="text-[11px] text-text2 mt-1">
                  News = fact-grounded from News sources. Evergreen = timeless opinion debates you seed below.
                </p>
              </div>
              <div>
                <label className="label">Label type</label>
                <select
                  className="input w-full"
                  value={(form.discussion_label_mode as string | undefined) ?? "both"}
                  onChange={(e) => set("discussion_label_mode", e.target.value)}
                >
                  <option value="both">Both (AI picks per card)</option>
                  <option value="discussion">Discussion only (question)</option>
                  <option value="hot_take">Hot Take only (bold claim)</option>
                </select>
                <p className="text-[11px] text-text2 mt-1">
                  Restricts which badge style the AI is allowed to use on the card.
                </p>
              </div>
            </div>

            {/* Evergreen topics */}
            <div className="border-t border-line pt-4 space-y-3">
              <div>
                <p className="text-xs font-semibold text-text2 uppercase tracking-wide">Evergreen Debate Topics</p>
                <p className="text-[11px] text-text2 mt-0.5">
                  Timeless, opinion-only debate seeds (e.g. &quot;Is Senna the GOAT?&quot;). Used when the topic
                  source is Evergreen or Both. Rotated least-recently-used. Keep them subjective — no hard
                  facts/stats (the AI can get those wrong without a fresh article).
                </p>
              </div>

              <div className="space-y-1.5">
                {((form.discussion_topics as DiscussionTopicRef[] | undefined) ?? []).length === 0 ? (
                  <p className="text-[11px] text-text2 italic">No evergreen topics yet.</p>
                ) : (
                  ((form.discussion_topics as DiscussionTopicRef[] | undefined) ?? []).map((t) => (
                    <div
                      key={t.id}
                      className="flex items-center gap-3 p-2.5 rounded-lg border border-line"
                    >
                      <input
                        type="checkbox"
                        className="w-4 h-4 rounded accent-pri shrink-0"
                        checked={t.is_active}
                        onChange={() => handleToggleTopic(t)}
                        title={t.is_active ? "Active — click to pause" : "Paused — click to activate"}
                      />
                      <div className="min-w-0 flex-1">
                        <p className={`text-sm leading-tight ${t.is_active ? "text-text" : "text-text2 line-through"}`}>
                          {t.seed_text}
                        </p>
                        <p className="text-[11px] text-text2 truncate">
                          {t.subject_hint ? `photo: ${t.subject_hint} · ` : ""}used {t.times_used}×
                        </p>
                      </div>
                      <button
                        onClick={() => handleDeleteTopic(t.id)}
                        className="text-text2 hover:text-bad transition-colors shrink-0"
                        title="Delete topic"
                      >
                        <Icon icon="solar:trash-bin-trash-bold-duotone" width={18} />
                      </button>
                    </div>
                  ))
                )}
              </div>

              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  type="text"
                  className="input flex-1"
                  placeholder="Debate seed, e.g. Is Ayrton Senna the GOAT?"
                  value={newTopicSeed}
                  onChange={(e) => setNewTopicSeed(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTopic()}
                />
                <input
                  type="text"
                  className="input sm:w-48"
                  placeholder="Photo subject (optional)"
                  value={newTopicSubject}
                  onChange={(e) => setNewTopicSubject(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTopic()}
                />
                <button
                  onClick={handleAddTopic}
                  disabled={topicSaving || !newTopicSeed.trim()}
                  className="btn-primary shrink-0 disabled:opacity-50"
                >
                  Add
                </button>
              </div>
            </div>

            {/* Content ideas queue */}
            {!hideIdeas && (
            <div className="border-t border-line pt-4 space-y-3">
              <div>
                <p className="text-xs font-semibold text-text2 uppercase tracking-wide">Content Ideas Queue</p>
                <p className="text-[11px] text-text2 mt-0.5">
                  Auto-populated, oldest-first — each idea is already fully drafted (label, question, subject,
                  caption). Edit anything before it posts, or delete ideas you don&apos;t want used. Type a title
                  below and the AI will draft the rest immediately.
                </p>
              </div>

              {/* Draft with AI */}
              <div className="p-3.5 rounded-xl border border-line bg-surface2 space-y-2.5">
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
                      className="input w-full text-xs"
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
                    className="input flex-1 text-xs"
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
                    className="btn-primary text-xs px-4 py-2 shrink-0 inline-flex items-center justify-center gap-1.5 disabled:opacity-50 cursor-pointer"
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

              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  type="text"
                  className="input flex-1"
                  placeholder="Title/topic, e.g. Is Verstappen the most complete driver ever?"
                  value={newDiscSeed}
                  onChange={(e) => setNewDiscSeed(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !discIdeaSaving && handleAddDiscIdea()}
                />
                <input
                  type="text"
                  className="input sm:w-48"
                  placeholder="Photo subject (optional)"
                  value={newDiscSubjectHint}
                  onChange={(e) => setNewDiscSubjectHint(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !discIdeaSaving && handleAddDiscIdea()}
                />
                <button
                  onClick={handleAddDiscIdea}
                  disabled={discIdeaSaving || !newDiscSeed.trim()}
                  className="btn-primary shrink-0 disabled:opacity-50"
                >
                  {discIdeaSaving ? "Drafting…" : "Generate"}
                </button>
              </div>

              <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
                {allDiscIdeas.length === 0 ? (
                  <p className="text-[11px] text-text2 italic">No pending ideas yet — they&apos;ll appear here automatically, or add a title above.</p>
                ) : (
                  allDiscIdeas.map((idea) =>
                      editingDiscIdeaId === idea.id ? (
                        <div key={idea.id} className="p-2.5 rounded-lg border border-pri space-y-2">
                          <div className="flex gap-2">
                            {(["DISCUSSION", "HOT TAKE"] as const).map((l) => (
                              <label key={l} className="flex items-center gap-1.5 cursor-pointer">
                                <input
                                  type="radio"
                                  name={`disc-idea-label-${idea.id}`}
                                  checked={editDiscLabel === l}
                                  onChange={() => setEditDiscLabel(l)}
                                  className="accent-pri"
                                />
                                <span className="text-xs text-text">{l}</span>
                              </label>
                            ))}
                          </div>
                          <input
                            type="text"
                            className="input w-full text-sm"
                            value={editDiscQuestion}
                            onChange={(e) => setEditDiscQuestion(e.target.value)}
                            placeholder="Question / claim"
                          />
                          <input
                            type="text"
                            className="input w-full text-sm"
                            value={editDiscSubject}
                            onChange={(e) => setEditDiscSubject(e.target.value)}
                            placeholder="Photo subject"
                          />
                          <textarea
                            className="input w-full text-sm"
                            rows={2}
                            value={editDiscCaption}
                            onChange={(e) => setEditDiscCaption(e.target.value)}
                            placeholder="Facebook caption"
                          />
                          <div className="flex gap-2 justify-end">
                            <button onClick={() => setEditingDiscIdeaId(null)} className="text-xs text-text2 px-2 py-1">
                              Cancel
                            </button>
                            <button onClick={() => onSaveDiscIdea(idea.id)} className="btn-primary text-xs px-3 py-1">
                              Save
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div key={idea.id} className="flex items-start gap-3 p-2.5 rounded-lg border border-line">
                          <div className="min-w-0 flex-1">
                            <p className="text-[10px] font-semibold text-pri uppercase tracking-wide">{idea.label}</p>
                            <p className="text-sm font-medium leading-tight text-text truncate">{idea.question}</p>
                            <p className="text-[11px] text-text2 line-clamp-2">{idea.caption}</p>
                            <p className="text-[10px] text-text2 mt-0.5 uppercase tracking-wide">
                              {idea.subject_name} · {idea.source_type}
                            </p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={() => startEditDiscIdea(idea)}
                              className="text-text2 hover:text-text transition-colors"
                              title="Edit"
                            >
                              <Icon icon="solar:pen-new-round-bold-duotone" width={16} />
                            </button>
                            <button
                              onClick={() => onDeleteDiscIdea(idea.id)}
                              className="text-text2 hover:text-bad transition-colors"
                              title="Delete"
                            >
                              <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                            </button>
                          </div>
                        </div>
                      )
                    )
                )}
              </div>
              {discIdeasHasMore && handleLoadMoreDiscIdeas && (
                <button
                  onClick={handleLoadMoreDiscIdeas}
                  disabled={discIdeasLoadingMore}
                  className="text-xs text-pri hover:underline disabled:opacity-50"
                >
                  {discIdeasLoadingMore ? "Loading…" : "Load more"}
                </button>
              )}
            </div>
            )}

            <p className="text-[11px] text-text2">
              Uses the Discussion Template (Global tab) and the News caption criteria for the Facebook post text.
            </p>
          </>
        )}
      </section>
    </div>
  );
}
