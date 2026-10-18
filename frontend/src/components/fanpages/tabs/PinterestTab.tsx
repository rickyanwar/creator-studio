"use client";

import { Icon } from "@iconify/react";
import type { PinterestSourceRef, PinterestContentIdeaRef, FanpageDetail } from "@/lib/types";

export interface PinterestTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  newPinUrl: string;
  setNewPinUrl: (v: string) => void;
  newPinLabel: string;
  setNewPinLabel: (v: string) => void;
  pinSourceSaving: boolean;
  handleAddPinSource: () => Promise<void>;
  handleTogglePinSource: (s: PinterestSourceRef) => Promise<void>;
  handleDeletePinSource: (sourceId: number) => Promise<void>;
  ideas: PinterestContentIdeaRef[];
  editingIdeaId: number | null;
  setEditingIdeaId: (id: number | null) => void;
  editIdeaTitle: string;
  setEditIdeaTitle: (v: string) => void;
  editIdeaDescription: string;
  setEditIdeaDescription: (v: string) => void;
  handleSaveIdea: (ideaId: number) => Promise<void>;
  startEditIdea: (idea: PinterestContentIdeaRef) => void;
  handleDeleteIdea: (ideaId: number) => Promise<void>;
  ideasHasMore?: boolean;
  handleLoadMoreIdeas?: () => Promise<void>;
  ideasLoadingMore?: boolean;
  hideIdeas?: boolean;
}

export function PinterestTab({
  form,
  set,
  newPinUrl,
  setNewPinUrl,
  newPinLabel,
  setNewPinLabel,
  pinSourceSaving,
  handleAddPinSource,
  handleTogglePinSource,
  handleDeletePinSource,
  ideas = [],
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
  hideIdeas,
}: PinterestTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-pinterest" aria-labelledby="tab-pinterest" className="space-y-8">
      {/* ── Section: Mode 5 — Pinterest Content ───────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text">Pinterest</h2>
            <p className="text-xs text-text2 mt-0.5">
              A photo is the seed instead of a headline — pulled from Pinterest, staged as an editable
              idea (title + description) below, then posted on this fanpage&apos;s own pacing.
            </p>
          </div>
          <button
            onClick={() => set("pinterest_enabled", !form.pinterest_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
              form.pinterest_enabled ? "bg-pri" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.pinterest_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.pinterest_enabled && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="label">Posts per day</label>
                <input
                  type="number"
                  min={0}
                  max={20}
                  className="input w-full"
                  value={(form.pinterest_daily_count as number | undefined) ?? 2}
                  onChange={(e) => set("pinterest_daily_count", parseInt(e.target.value || "0"))}
                />
              </div>
              <div>
                <label className="label">Idea source</label>
                <select
                  className="input w-full"
                  value={(form.pinterest_source_mode as string | undefined) ?? "both"}
                  onChange={(e) => set("pinterest_source_mode", e.target.value)}
                >
                  <option value="both">Both (AI keyword first, curated fallback)</option>
                  <option value="ai_keyword">AI keyword only</option>
                  <option value="curated">Curated links only</option>
                </select>
                <p className="text-[11px] text-text2 mt-1">
                  AI keyword = the AI itself searches Pinterest for a niche topic. Curated = your links below.
                </p>
              </div>
            </div>
            <p className="text-[11px] text-text2">
              Renders on the single Pinterest Template set in the Global tab (Design Templates) — set which one to
              use, or add/tag one in Template Designer.
            </p>

            <div>
              <label className="label">Pinterest content focus (what to search for and pick)</label>
              <textarea
                className="input w-full text-sm"
                rows={2}
                placeholder='e.g. "ONLY elite-level UFC/boxing" or "focus on the golden eras 1950s-2000s"'
                value={(form.pinterest_custom_prompt as string | undefined) ?? ""}
                onChange={(e) => set("pinterest_custom_prompt", e.target.value)}
              />
              <p className="text-[11px] text-text2 mt-1">
                Controls what content is pulled from Pinterest. Caption still follows Global settings.
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-xs text-text2 italic">
                Caption follows Global settings
              </p>
            </div>

            <label className="flex items-start gap-3 rounded-lg border border-line p-3 cursor-pointer">
              <input
                type="checkbox"
                className="mt-0.5 w-4 h-4 rounded accent-pri"
                checked={Boolean(form.pinterest_allow_low_quality)}
                onChange={(e) => set("pinterest_allow_low_quality", e.target.checked)}
              />
              <span>
                <span className="block text-sm font-medium text-text">Allow low-quality photos</span>
                <span className="block text-[11px] text-text2 mt-0.5">
                  Accept photos rejected by resolution and visual-quality checks. Watermark and content checks still apply.
                </span>
              </span>
            </label>

            {/* Curated reference links */}
            <div className="border-t border-line pt-4 space-y-3">
              <div>
                <p className="text-xs font-semibold text-text2 uppercase tracking-wide">Curated Pinterest References</p>
                <p className="text-[11px] text-text2 mt-0.5">
                  Paste a Pinterest profile URL (every board on it is walked) or a board URL. Rotated
                  least-recently-used. Used when the idea source is Curated or Both.
                </p>
              </div>

              <div className="space-y-1.5">
                {((form.pinterest_sources as PinterestSourceRef[] | undefined) ?? []).length === 0 ? (
                  <p className="text-[11px] text-text2 italic">No curated references yet.</p>
                ) : (
                  ((form.pinterest_sources as PinterestSourceRef[] | undefined) ?? []).map((s) => (
                    <div key={s.id} className="flex items-center gap-3 p-2.5 rounded-lg border border-line">
                      <input
                        type="checkbox"
                        className="w-4 h-4 rounded accent-pri shrink-0"
                        checked={s.is_active}
                        onChange={() => handleTogglePinSource(s)}
                        title={s.is_active ? "Active — click to pause" : "Paused — click to activate"}
                      />
                      <div className="min-w-0 flex-1">
                        <p className={`text-sm leading-tight truncate ${s.is_active ? "text-text" : "text-text2 line-through"}`}>
                          {s.label || s.source_url}
                        </p>
                        <p className="text-[11px] text-text2 truncate">
                          {s.label ? `${s.source_url} · ` : ""}used {s.times_used}×
                        </p>
                      </div>
                      <button
                        onClick={() => handleDeletePinSource(s.id)}
                        className="text-text2 hover:text-bad transition-colors shrink-0"
                        title="Delete reference"
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
                  placeholder="Pinterest profile or board URL"
                  value={newPinUrl}
                  onChange={(e) => setNewPinUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddPinSource()}
                />
                <input
                  type="text"
                  className="input sm:w-48"
                  placeholder="Note (optional)"
                  value={newPinLabel}
                  onChange={(e) => setNewPinLabel(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddPinSource()}
                />
                <button
                  onClick={handleAddPinSource}
                  disabled={pinSourceSaving || !newPinUrl.trim()}
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
                  Auto-populated, oldest-first — each idea already has a verified photo attached. Edit the
                  title/description before it posts, or delete ideas you don&apos;t want used.
                </p>
              </div>

              <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
                {ideas.length === 0 ? (
                  <p className="text-[11px] text-text2 italic">No pending ideas yet — they&apos;ll appear here automatically.</p>
                ) : (
                  ideas.map((idea) =>
                      editingIdeaId === idea.id ? (
                        <div key={idea.id} className="p-2.5 rounded-lg border border-pri space-y-2">
                          <input
                            type="text"
                            className="input w-full text-sm"
                            value={editIdeaTitle}
                            onChange={(e) => setEditIdeaTitle(e.target.value)}
                            placeholder="Title"
                          />
                          <textarea
                            className="input w-full text-sm"
                            rows={2}
                            value={editIdeaDescription}
                            onChange={(e) => setEditIdeaDescription(e.target.value)}
                            placeholder="Description"
                          />
                          <div className="flex gap-2 justify-end">
                            <button onClick={() => setEditingIdeaId(null)} className="text-xs text-text2 px-2 py-1">
                              Cancel
                            </button>
                            <button onClick={() => handleSaveIdea(idea.id)} className="btn-primary text-xs px-3 py-1">
                              Save
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div key={idea.id} className="flex items-start gap-3 p-2.5 rounded-lg border border-line">
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium leading-tight text-text truncate">{idea.title}</p>
                            <p className="text-[11px] text-text2 line-clamp-2">{idea.description}</p>
                            <p className="text-[10px] text-text2 mt-0.5 uppercase tracking-wide">
                              {idea.source_type === "ai_keyword" ? "AI keyword" : "curated"}
                            </p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={() => startEditIdea(idea)}
                              className="text-text2 hover:text-text transition-colors"
                              title="Edit"
                            >
                              <Icon icon="solar:pen-new-round-bold-duotone" width={16} />
                            </button>
                            <button
                              onClick={() => handleDeleteIdea(idea.id)}
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
              {ideasHasMore && handleLoadMoreIdeas && (
                <button
                  onClick={handleLoadMoreIdeas}
                  disabled={ideasLoadingMore}
                  className="text-xs text-pri hover:underline disabled:opacity-50"
                >
                  {ideasLoadingMore ? "Loading…" : "Load more"}
                </button>
              )}
            </div>
            )}

            <p className="text-[11px] text-text2">
              No separate copywriting step — the idea&apos;s own title/description is what posts.
            </p>
          </>
        )}
      </section>
    </div>
  );
}
