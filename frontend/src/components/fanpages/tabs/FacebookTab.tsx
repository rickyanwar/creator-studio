"use client";

import { Icon } from "@iconify/react";
import type { FacebookPhotoSourceRef, FacebookPhotoIdeaRef, FanpageDetail } from "@/lib/types";

export interface FacebookTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  newFbPhotoUrl: string;
  setNewFbPhotoUrl: (v: string) => void;
  newFbPhotoLabel: string;
  setNewFbPhotoLabel: (v: string) => void;
  fbPhotoSourceSaving: boolean;
  handleAddFbPhotoSource: () => Promise<void>;
  handleToggleFbPhotoSource: (s: FacebookPhotoSourceRef) => Promise<void>;
  handleDeleteFbPhotoSource: (sourceId: number) => Promise<void>;
  fbPhotoIdeas: FacebookPhotoIdeaRef[];
  editingFbIdeaId: number | null;
  setEditingFbIdeaId: (id: number | null) => void;
  editFbIdeaTitle: string;
  setEditFbIdeaTitle: (v: string) => void;
  editFbIdeaSubtitle: string;
  setEditFbIdeaSubtitle: (v: string) => void;
  editFbIdeaCaption: string;
  setEditFbIdeaCaption: (v: string) => void;
  handleSaveFbIdea: (ideaId: number) => Promise<void>;
  startEditFbIdea: (idea: FacebookPhotoIdeaRef) => void;
  handleDeleteFbIdea: (ideaId: number) => Promise<void>;
  fbPhotoIdeasHasMore?: boolean;
  handleLoadMoreFbPhotoIdeas?: () => Promise<void>;
  fbPhotoIdeasLoadingMore?: boolean;
  hideIdeas?: boolean;
}

export function FacebookTab({
  form,
  set,
  newFbPhotoUrl,
  setNewFbPhotoUrl,
  newFbPhotoLabel,
  setNewFbPhotoLabel,
  fbPhotoSourceSaving,
  handleAddFbPhotoSource,
  handleToggleFbPhotoSource,
  handleDeleteFbPhotoSource,
  fbPhotoIdeas = [],
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
  hideIdeas,
}: FacebookTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-facebook" aria-labelledby="tab-facebook" className="space-y-8">
      {/* ── Section: Mode 6 — Facebook Photo Recreate ───────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text">FB photo recreate</h2>
            <p className="text-xs text-text2 mt-0.5">
              Recreate posts from other Facebook pages (competitor/inspiration pages, not this fanpage&apos;s
              own), like IG Recreate — each new photo is only read for its text (news / quote /
              discussion; anything else is skipped), rewritten in this fanpage&apos;s language, and rebuilt
              on this fanpage&apos;s own template with a fresh photo of the subject. The source page&apos;s
              image is never posted.
            </p>
          </div>
          <button
            onClick={() => set("facebook_photo_enabled", !form.facebook_photo_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
              form.facebook_photo_enabled ? "bg-pri" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.facebook_photo_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.facebook_photo_enabled && (
          <>
            <div>
              <label className="label">Facebook recreate AI design</label>
              <select
                className="input w-full mt-1"
                value={(form.fb_visual_engine as string) ?? "off"}
                onChange={(e) => set("fb_visual_engine", e.target.value)}
              >
                <option value="off">Off</option>
                <option value="chatgpt">ChatGPT</option>
              </select>
              <p className="text-[10px] text-text2 mt-1">
                1 ChatGPT image per post; falls back to the normal template on failure.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="label">Posts per day</label>
                <input
                  type="number"
                  min={0}
                  max={20}
                  className="input w-full"
                  value={(form.facebook_photo_daily_count as number | undefined) ?? 2}
                  onChange={(e) => set("facebook_photo_daily_count", parseInt(e.target.value || "0"))}
                />
              </div>
            </div>
            <div>
              <label className="label">Topic filter (optional)</label>
              <input
                type="text"
                maxLength={300}
                className="input w-full"
                placeholder="e.g. Formula 1 and motorsport only"
                value={(form.facebook_photo_topic_filter as string | null | undefined) ?? ""}
                onChange={(e) => set("facebook_photo_topic_filter", e.target.value)}
              />
              <p className="text-[11px] text-text2 mt-1">
                Only posts actually about this topic are taken — a source page that also posts politics or
                other sports won&apos;t leak those onto this fanpage. Leave empty to accept any topic. Applies to
                newly fetched photos; delete off-topic ideas already in the queue below.
              </p>
            </div>
            <p className="text-[11px] text-text2">
              Renders on the News/Quote/Discussion Templates set in the Global tab (Design Templates) — no
              separate template setting for FB photo recreate. Captions use the IG Repost caption settings, without the
              &quot;via @source&quot; attribution line. No like-count/growth check in this version — a photo not seen
              before on the source page&apos;s public gallery is the only filter.
            </p>

            {/* Curated source pages */}
            <div className="border-t border-line pt-4 space-y-3">
              <div>
                <p className="text-xs font-semibold text-text2 uppercase tracking-wide">Source Facebook Pages</p>
                <p className="text-[11px] text-text2 mt-0.5">
                  Paste a Facebook page URL or username (e.g. gpfansglobal). Rotated least-recently-used.
                </p>
              </div>

              <div className="space-y-1.5">
                {((form.facebook_photo_sources as FacebookPhotoSourceRef[] | undefined) ?? []).length === 0 ? (
                  <p className="text-[11px] text-text2 italic">No source pages yet.</p>
                ) : (
                  ((form.facebook_photo_sources as FacebookPhotoSourceRef[] | undefined) ?? []).map((s) => (
                    <div key={s.id} className="flex items-center gap-3 p-2.5 rounded-lg border border-line">
                      <input
                        type="checkbox"
                        className="w-4 h-4 rounded accent-pri shrink-0"
                        checked={s.is_active}
                        onChange={() => handleToggleFbPhotoSource(s)}
                        title={s.is_active ? "Active — click to pause" : "Paused — click to activate"}
                      />
                      <div className="min-w-0 flex-1">
                        <p className={`text-sm leading-tight truncate ${s.is_active ? "text-text" : "text-text2 line-through"}`}>
                          {s.label || s.page_url}
                        </p>
                        <p className="text-[11px] text-text2 truncate">
                          {s.label ? `${s.page_url} · ` : ""}used {s.times_used}×
                        </p>
                      </div>
                      <button
                        onClick={() => handleDeleteFbPhotoSource(s.id)}
                        className="text-text2 hover:text-bad transition-colors shrink-0"
                        title="Delete source"
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
                  placeholder="Facebook page URL or username"
                  value={newFbPhotoUrl}
                  onChange={(e) => setNewFbPhotoUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddFbPhotoSource()}
                />
                <input
                  type="text"
                  className="input sm:w-48"
                  placeholder="Note (optional)"
                  value={newFbPhotoLabel}
                  onChange={(e) => setNewFbPhotoLabel(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddFbPhotoSource()}
                />
                <button
                  onClick={handleAddFbPhotoSource}
                  disabled={fbPhotoSourceSaving || !newFbPhotoUrl.trim()}
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
                  Auto-populated, oldest-first — each idea is the final card text (news / quote / discussion),
                  already in this fanpage&apos;s language. Edit it before it posts, or delete ideas you
                  don&apos;t want used. The photo is picked when the card is rendered.
                </p>
              </div>

              <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
                {fbPhotoIdeas.length === 0 ? (
                  <p className="text-[11px] text-text2 italic">No pending ideas yet — they&apos;ll appear here automatically.</p>
                ) : (
                  fbPhotoIdeas.map((idea) =>
                      editingFbIdeaId === idea.id ? (
                        <div key={idea.id} className="p-2.5 rounded-lg border border-pri space-y-2">
                          <input
                            type="text"
                            className="input w-full text-sm"
                            value={editFbIdeaTitle}
                            onChange={(e) => setEditFbIdeaTitle(e.target.value)}
                            placeholder={idea.category === "discussion" ? "Question" : idea.category === "quote" ? "Quote (no quotation marks)" : "Headline"}
                          />
                          {idea.category === "quote" && (
                            <input
                              type="text"
                              className="input w-full text-sm"
                              value={editFbIdeaSubtitle}
                              onChange={(e) => setEditFbIdeaSubtitle(e.target.value)}
                              placeholder="Speaker name"
                            />
                          )}
                          {idea.category === "discussion" && (
                            <>
                              <input
                                type="text"
                                className="input w-full text-sm"
                                value={editFbIdeaSubtitle}
                                onChange={(e) => setEditFbIdeaSubtitle(e.target.value)}
                                placeholder="Label (DISCUSSION / HOT TAKE)"
                              />
                              <input
                                type="text"
                                className="input w-full text-sm"
                                value={editFbIdeaCaption}
                                onChange={(e) => setEditFbIdeaCaption(e.target.value)}
                                placeholder="Subject name"
                              />
                            </>
                          )}
                          <div className="flex gap-2 justify-end">
                            <button onClick={() => setEditingFbIdeaId(null)} className="text-xs text-text2 px-2 py-1">
                              Cancel
                            </button>
                            <button onClick={() => handleSaveFbIdea(idea.id)} className="btn-primary text-xs px-3 py-1">
                              Save
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div key={idea.id} className="flex items-start gap-3 p-2.5 rounded-lg border border-line">
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium leading-tight text-text truncate">{idea.design_title}</p>
                            {idea.design_subtitle && (
                              <p className="text-[11px] text-text2 line-clamp-2">
                                {idea.design_subtitle}{idea.design_caption ? ` · ${idea.design_caption}` : ""}
                              </p>
                            )}
                            <p className="text-[10px] text-text2 mt-0.5 uppercase tracking-wide">
                              {idea.category}
                            </p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={() => startEditFbIdea(idea)}
                              className="text-text2 hover:text-text transition-colors"
                              title="Edit"
                            >
                              <Icon icon="solar:pen-new-round-bold-duotone" width={16} />
                            </button>
                            <button
                              onClick={() => handleDeleteFbIdea(idea.id)}
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
              {fbPhotoIdeasHasMore && handleLoadMoreFbPhotoIdeas && (
                <button
                  onClick={handleLoadMoreFbPhotoIdeas}
                  disabled={fbPhotoIdeasLoadingMore}
                  className="text-xs text-pri hover:underline disabled:opacity-50"
                >
                  {fbPhotoIdeasLoadingMore ? "Loading…" : "Load more"}
                </button>
              )}
            </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
